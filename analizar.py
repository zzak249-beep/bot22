"""
analizar.py — Motor de señales SMC v5.0 [MetaClaw Edition]

NUEVA ESTRATEGIA v5.0:
  ✅ Premium/Discount Zones — solo entrar en zona favorable del swing
  ✅ Displacement Detection — velas de impulso institucional (3+ consecutivas)
  ✅ Inducement (IDM) — trampa antes del movimiento real
  ✅ HTF 4h añadida como filtro adicional (4h + 1h + 5m = triple confirmación)
  ✅ Liquidity pools — detección de stops acumulados en EQH/EQL
  ✅ Imbalances — FVG solo si no está ya rellenado
  ✅ ATR dinámico — SL basado en estructura, no ATR fijo
  ✅ Score v5.0 max 16 puntos (Premium/Discount +1, IDM +1, Displacement +1)
  ✅ MetaClaw compatible — output enriquecido para el agente IA
"""

import logging
from datetime import datetime, timezone
import concurrent.futures
import time

import config
import exchange

log = logging.getLogger("analizar")

_last_signal_ts: dict = {}


# ══════════════════════════════════════════════════════════════
# INDICADORES TÉCNICOS
# ══════════════════════════════════════════════════════════════

def calc_ema(prices: list, period: int):
    if len(prices) < period:
        return None
    k   = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def calc_rsi(prices: list, period: int = 14):
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains  = [max(d, 0)      for d in deltas[:period]]
    losses = [abs(min(d, 0)) for d in deltas[:period]]
    avg_g  = sum(gains)  / period
    avg_l  = sum(losses) / period
    for d in deltas[period:]:
        avg_g = (avg_g * (period - 1) + max(d, 0))      / period
        avg_l = (avg_l * (period - 1) + abs(min(d, 0))) / period
    if avg_l == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_g / avg_l)), 2)


def calc_atr(highs, lows, closes, period: int = 14) -> float:
    if len(highs) < period + 1:
        return 0.0
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1]))
        for i in range(1, len(highs))
    ]
    return sum(trs[-period:]) / period if len(trs) >= period else (sum(trs) / len(trs) if trs else 0.0)


def calc_vwap(candles: list) -> float:
    today = datetime.now(timezone.utc).date()
    hoy   = [c for c in candles
             if datetime.fromtimestamp(c["ts"]/1000, tz=timezone.utc).date() == today]
    if not hoy:
        hoy = candles[-50:]
    vol_total = sum(c["volume"] for c in hoy)
    if vol_total <= 0:
        return candles[-1]["close"]
    hlc3_total = sum(((c["high"]+c["low"]+c["close"])/3) * c["volume"] for c in hoy)
    return hlc3_total / vol_total


def calc_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return None, None, None
    macd_s = []
    for i in range(slow - 1, len(prices)):
        ef = calc_ema(prices[:i+1], fast)
        es = calc_ema(prices[:i+1], slow)
        if ef and es:
            macd_s.append(ef - es)
    if len(macd_s) < signal:
        return None, None, None
    ml = macd_s[-1]
    sl = calc_ema(macd_s, signal)
    if sl is None:
        return None, None, None
    return round(ml, 8), round(sl, 8), round(ml - sl, 8)


def calc_volumen_ratio(candles, periodo=20) -> float:
    if len(candles) < periodo + 1:
        return 1.0
    vols = [c["volume"] for c in candles[-(periodo+1):-1]]
    avg  = sum(vols) / len(vols) if vols else 0
    return (candles[-1]["volume"] / avg) if avg > 0 else 1.0


def calc_pivotes(ph, pl, pc) -> dict:
    pp = (ph + pl + pc) / 3
    return {
        "PP": pp,
        "R1": 2*pp - pl, "R2": pp + (ph - pl),
        "S1": 2*pp - ph, "S2": pp - (ph - pl),
    }


# ══════════════════════════════════════════════════════════════
# NUEVO v5.0: PREMIUM / DISCOUNT ZONES
# ══════════════════════════════════════════════════════════════

def calc_premium_discount(candles: list, lookback: int = 50) -> dict:
    """
    ICT Premium/Discount:
    - Tomar el swing high y swing low de las últimas N velas
    - Mitad del rango = equilibrio
    - Por ENCIMA del 50% = zona premium (cara → SHORT ideal)
    - Por DEBAJO del 50% = zona de descuento (barata → LONG ideal)
    """
    lb = candles[-lookback:] if len(candles) >= lookback else candles
    swing_high = max(c["high"] for c in lb)
    swing_low  = min(c["low"]  for c in lb)
    rango      = swing_high - swing_low
    if rango <= 0:
        return {"premium": False, "discount": False, "equilibrium": 0, "zona_pct": 50.0}
    
    precio      = candles[-1]["close"]
    equilibrium = swing_low + rango * 0.5
    zona_pct    = (precio - swing_low) / rango * 100  # 0% = low, 100% = high
    
    # Descuento: 0-45% del rango → LONG favorable
    # Premium:  55-100% del rango → SHORT favorable
    return {
        "premium":     zona_pct >= 55.0,
        "discount":    zona_pct <= 45.0,
        "equilibrium": round(equilibrium, 8),
        "zona_pct":    round(zona_pct, 1),
        "swing_high":  swing_high,
        "swing_low":   swing_low,
    }


# ══════════════════════════════════════════════════════════════
# NUEVO v5.0: DISPLACEMENT (impulso institucional)
# ══════════════════════════════════════════════════════════════

def detectar_displacement(candles: list) -> dict:
    """
    Displacement = 2+ velas consecutivas de gran cuerpo (>60% del rango)
    en la misma dirección con volumen creciente.
    Señal de que el smart money entró con fuerza.
    """
    result = {"bull_disp": False, "bear_disp": False, "fuerza": 0}
    if len(candles) < 5:
        return result

    # Mirar las últimas 4 velas (excluir la actual, puede estar formando)
    ventana = candles[-5:-1]
    
    # Contar consecutivas alcistas/bajistas de cuerpo grande
    consec_bull = consec_bear = 0
    for c in ventana:
        rng  = c["high"] - c["low"]
        if rng <= 0:
            continue
        body = abs(c["close"] - c["open"])
        if c["close"] > c["open"] and body / rng > 0.60:
            consec_bull += 1
            consec_bear  = 0
        elif c["close"] < c["open"] and body / rng > 0.60:
            consec_bear += 1
            consec_bull  = 0
        else:
            consec_bull = consec_bear = 0

    if consec_bull >= 2:
        result["bull_disp"] = True
        result["fuerza"]    = consec_bull
    elif consec_bear >= 2:
        result["bear_disp"] = True
        result["fuerza"]    = consec_bear

    return result


# ══════════════════════════════════════════════════════════════
# NUEVO v5.0: INDUCEMENT (trampa antes del movimiento real)
# ══════════════════════════════════════════════════════════════

def detectar_inducement(candles: list, lado: str) -> bool:
    """
    Inducement (IDM): breakout falso justo antes del movimiento real.
    
    Para LONG: precio rompió brevemente por DEBAJO de un mínimo reciente
               pero recuperó → stops cazados → smart money LONG ahora.
    Para SHORT: precio rompió brevemente por ENCIMA de un máximo reciente
                pero cayó → stops cazados → smart money SHORT ahora.
    """
    if len(candles) < 10:
        return False

    look = candles[-10:-1]
    current = candles[-1]

    if lado == "LONG":
        # Buscar si alguna vela reciente hizo low por debajo del mínimo anterior y recuperó
        lows = [c["low"] for c in look]
        for i in range(1, len(look)):
            if look[i]["low"] < min(lows[:i]) and look[i]["close"] > look[i]["open"]:
                # Breakout bajo + recuperación = IDM bull
                return True
    else:
        # Buscar si alguna vela reciente hizo high por encima del máximo anterior y cayó
        highs = [c["high"] for c in look]
        for i in range(1, len(look)):
            if look[i]["high"] > max(highs[:i]) and look[i]["close"] < look[i]["open"]:
                # Breakout alto + caída = IDM bear
                return True
    return False


# ══════════════════════════════════════════════════════════════
# PATRONES DE VELA
# ══════════════════════════════════════════════════════════════

def detectar_patron_vela(candles: list, lado: str) -> dict:
    result = {"patron": None, "confianza": 0}
    if len(candles) < 3:
        return result
    c    = candles[-1]
    prev = candles[-2]
    p2   = candles[-3]
    rng  = c["high"] - c["low"]
    if rng <= 0:
        return result
    body       = abs(c["close"] - c["open"])
    upper_wick = c["high"]  - max(c["close"], c["open"])
    lower_wick = min(c["close"], c["open"]) - c["low"]
    body_pct   = body / rng

    if lado == "LONG":
        # Pin Bar alcista
        if (lower_wick / rng >= config.PINBAR_RATIO and
                body_pct < 0.40 and
                c["close"] > (c["high"] + c["low"]) / 2):
            result = {"patron": "PIN_BAR", "confianza": 2}
        # Engulfing alcista
        elif (config.ENGULF_ACTIVO and
              c["close"] > c["open"] and
              prev["close"] < prev["open"] and
              c["close"] > prev["open"] and
              c["open"]  < prev["close"]):
            result = {"patron": "ENGULFING", "confianza": 2}
        # Morning Star (3 velas)
        elif (p2["close"] < p2["open"] and
              abs(prev["close"] - prev["open"]) / (prev["high"] - prev["low"] + 1e-10) < 0.3 and
              c["close"] > c["open"] and
              c["close"] > (p2["open"] + p2["close"]) / 2):
            result = {"patron": "MORNING_STAR", "confianza": 3}
        elif c["close"] > c["open"] and body_pct > 0.50:
            result = {"patron": "BULL_STRONG", "confianza": 1}
    else:
        # Pin Bar bajista
        if (upper_wick / rng >= config.PINBAR_RATIO and
                body_pct < 0.40 and
                c["close"] < (c["high"] + c["low"]) / 2):
            result = {"patron": "PIN_BAR", "confianza": 2}
        # Engulfing bajista
        elif (config.ENGULF_ACTIVO and
              c["close"] < c["open"] and
              prev["close"] > prev["open"] and
              c["close"] < prev["open"] and
              c["open"]  > prev["close"]):
            result = {"patron": "ENGULFING", "confianza": 2}
        # Evening Star (3 velas)
        elif (p2["close"] > p2["open"] and
              abs(prev["close"] - prev["open"]) / (prev["high"] - prev["low"] + 1e-10) < 0.3 and
              c["close"] < c["open"] and
              c["close"] < (p2["open"] + p2["close"]) / 2):
            result = {"patron": "EVENING_STAR", "confianza": 3}
        elif c["close"] < c["open"] and body_pct > 0.50:
            result = {"patron": "BEAR_STRONG", "confianza": 1}

    return result


def momentum_ok(candles: list, lado: str) -> bool:
    if not config.MOMENTUM_ACTIVO or len(candles) < 3:
        return True
    if lado == "LONG":
        return candles[-2]["close"] > candles[-2]["open"] or candles[-3]["close"] > candles[-3]["open"]
    else:
        return candles[-2]["close"] < candles[-2]["open"] or candles[-3]["close"] < candles[-3]["open"]


# ══════════════════════════════════════════════════════════════
# LIQUIDITY SWEEPS
# ══════════════════════════════════════════════════════════════

def detectar_sweep(candles: list) -> dict:
    result = {"sweep_bull": False, "sweep_bear": False}
    if not config.SWEEP_ACTIVO or len(candles) < config.SWEEP_LOOKBACK + 2:
        return result
    c   = candles[-1]
    rng = c["high"] - c["low"]
    if rng <= 0:
        return result
    lb        = candles[-(config.SWEEP_LOOKBACK+1):-1]
    highest_h = max(x["high"] for x in lb)
    lowest_l  = min(x["low"]  for x in lb)
    if (c["low"] < lowest_l and c["close"] > lowest_l and
            (c["close"] - c["low"]) / rng > 0.60):
        result["sweep_bull"] = True
    if (c["high"] > highest_h and c["close"] < highest_h and
            (c["high"] - c["close"]) / rng > 0.60):
        result["sweep_bear"] = True
    return result


# ══════════════════════════════════════════════════════════════
# MTF — TENDENCIA 4H + 1H
# ══════════════════════════════════════════════════════════════

def tendencia_htf(par: str) -> str:
    """Tendencia 1H (principal)."""
    if not config.MTF_ACTIVO:
        return "NEUTRAL"
    try:
        candles_htf = exchange.get_candles(par, config.MTF_TIMEFRAME, config.MTF_CANDLES)
        if len(candles_htf) < 50:
            return "NEUTRAL"
        closes = [c["close"] for c in candles_htf]
        ema_f  = calc_ema(closes, config.EMA_FAST)
        ema_s  = calc_ema(closes, config.EMA_SLOW)
        if ema_f is None or ema_s is None:
            return "NEUTRAL"
        if ema_f > ema_s * 1.001:
            return "BULL"
        if ema_f < ema_s * 0.999:
            return "BEAR"
        return "NEUTRAL"
    except Exception as e:
        log.debug(f"tendencia_htf {par}: {e}")
        return "NEUTRAL"


def tendencia_4h(par: str) -> str:
    """Tendencia 4H — filtro de estructura mayor."""
    try:
        candles_4h = exchange.get_candles(par, "4h", 30)
        if len(candles_4h) < 20:
            return "NEUTRAL"
        closes = [c["close"] for c in candles_4h]
        ema21  = calc_ema(closes, 21)
        ema50  = calc_ema(closes, 50) if len(closes) >= 50 else calc_ema(closes, len(closes)//2)
        if ema21 is None or ema50 is None:
            return "NEUTRAL"
        if ema21 > ema50 * 1.002:
            return "BULL"
        if ema21 < ema50 * 0.998:
            return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


# ══════════════════════════════════════════════════════════════
# RANGO ASIA
# ══════════════════════════════════════════════════════════════

def get_rango_asia(candles: list) -> dict:
    result = {"high": 0.0, "low": 999_999_999.0, "valido": False}
    if not config.ASIA_RANGE_ACTIVO:
        return result
    asia_c = []
    for c in candles:
        dt    = datetime.fromtimestamp(c["ts"]/1000, tz=timezone.utc)
        m_utc = dt.hour * 60 + dt.minute
        if 0 <= m_utc < 240:
            asia_c.append(c)
    if len(asia_c) >= 3:
        result["high"]   = max(c["high"] for c in asia_c)
        result["low"]    = min(c["low"]  for c in asia_c)
        result["valido"] = True
    return result


# ══════════════════════════════════════════════════════════════
# FAIR VALUE GAP (con validación de relleno)
# ══════════════════════════════════════════════════════════════

def detectar_fvg(candles: list) -> dict:
    result = {
        "bull_fvg": False, "bear_fvg": False,
        "fvg_top": 0.0, "fvg_bottom": 0.0,
        "fvg_size": 0.0, "fvg_rellenado": True,
    }
    if len(candles) < 3:
        return result
    precio = candles[-1]["close"]
    desde  = len(candles) - 1
    hasta  = max(len(candles) - 25, 2)
    for i in range(desde, hasta-1, -1):
        c0, c2   = candles[i], candles[i-2]
        gap_bull = c0["low"]  - c2["high"]
        gap_bear = c2["low"]  - c0["high"]
        if gap_bull > config.FVG_MIN_PIPS:
            top    = c0["low"]
            bottom = c2["high"]
            # Verificar si ya fue rellenado (precio ya pasó por el FVG)
            rellenado = precio < bottom or any(
                candles[j]["low"] < bottom
                for j in range(i, len(candles)-1)
            )
            result.update({
                "bull_fvg": True, "fvg_top": top,
                "fvg_bottom": bottom, "fvg_size": gap_bull,
                "fvg_rellenado": rellenado,
            })
            break
        if gap_bear > config.FVG_MIN_PIPS:
            top    = c2["low"]
            bottom = c0["high"]
            rellenado = precio > top or any(
                candles[j]["high"] > top
                for j in range(i, len(candles)-1)
            )
            result.update({
                "bear_fvg": True, "fvg_top": top,
                "fvg_bottom": bottom, "fvg_size": gap_bear,
                "fvg_rellenado": rellenado,
            })
            break
    return result


def fvg_grande(fvg: dict, atr: float) -> bool:
    gap = fvg.get("fvg_size", abs(fvg.get("fvg_top", 0) - fvg.get("fvg_bottom", 0)))
    return atr > 0 and gap >= atr * 0.3


# ══════════════════════════════════════════════════════════════
# ORDER BLOCKS
# ══════════════════════════════════════════════════════════════

def detectar_order_blocks(candles: list) -> dict:
    result = {
        "bull_ob": False, "bull_ob_top": 0.0, "bull_ob_bottom": 0.0,
        "bear_ob": False, "bear_ob_top": 0.0, "bear_ob_bottom": 0.0,
        "bull_ob_mitigado": True, "bear_ob_mitigado": True,
    }
    if not config.OB_ACTIVO or len(candles) < 5:
        return result

    lb    = min(config.OB_LOOKBACK, len(candles) - 2)
    buscar = candles[-(lb+2):-1]

    for i in range(len(buscar)-3, 1, -1):
        c   = buscar[i]
        rng = c["high"] - c["low"]
        if rng <= 0:
            continue

        if c["close"] < c["open"] and not result["bull_ob"]:
            if i + 2 < len(buscar):
                c1, c2 = buscar[i+1], buscar[i+2]
                if (c1["close"] > c1["open"] and c2["close"] > c2["open"] and c2["high"] > c["high"]):
                    ob_top    = max(c["open"], c["close"])
                    ob_bottom = c["low"]
                    ob_50pct  = (ob_top + ob_bottom) / 2
                    mitigado  = any(buscar[j]["close"] < ob_50pct for j in range(i+1, len(buscar)))
                    result["bull_ob"]          = True
                    result["bull_ob_top"]      = ob_top
                    result["bull_ob_bottom"]   = ob_bottom
                    result["bull_ob_mitigado"] = mitigado

        if c["close"] > c["open"] and not result["bear_ob"]:
            if i + 2 < len(buscar):
                c1, c2 = buscar[i+1], buscar[i+2]
                if (c1["close"] < c1["open"] and c2["close"] < c2["open"] and c2["low"] < c["low"]):
                    ob_top    = c["high"]
                    ob_bottom = min(c["open"], c["close"])
                    ob_50pct  = (ob_top + ob_bottom) / 2
                    mitigado  = any(buscar[j]["close"] > ob_50pct for j in range(i+1, len(buscar)))
                    result["bear_ob"]          = True
                    result["bear_ob_top"]      = ob_top
                    result["bear_ob_bottom"]   = ob_bottom
                    result["bear_ob_mitigado"] = mitigado

        if result["bull_ob"] and result["bear_ob"]:
            break

    return result


def ob_valido_bull(ob: dict, precio: float) -> bool:
    return (ob["bull_ob"] and not ob["bull_ob_mitigado"] and
            ob["bull_ob_bottom"] <= precio <= ob["bull_ob_top"] * 1.005)

def ob_valido_bear(ob: dict, precio: float) -> bool:
    return (ob["bear_ob"] and not ob["bear_ob_mitigado"] and
            ob["bear_ob_bottom"] * 0.995 <= precio <= ob["bear_ob_top"])


def ob_fvg_confluencia_bull(ob: dict, fvg: dict, precio: float) -> bool:
    if not ob["bull_ob"] or ob["bull_ob_mitigado"]:
        return False
    if not fvg["bull_fvg"] or fvg.get("fvg_rellenado", True):
        return False
    ob_top    = ob["bull_ob_top"]
    ob_bottom = ob["bull_ob_bottom"]
    fvg_bot   = fvg["fvg_bottom"]
    fvg_top   = fvg["fvg_top"]
    overlap        = (fvg_bot <= ob_top and fvg_top >= ob_bottom)
    precio_en_zona = (ob_bottom <= precio <= ob_top * 1.01 or fvg_bot <= precio <= fvg_top)
    return overlap or precio_en_zona

def ob_fvg_confluencia_bear(ob: dict, fvg: dict, precio: float) -> bool:
    if not ob["bear_ob"] or ob["bear_ob_mitigado"]:
        return False
    if not fvg["bear_fvg"] or fvg.get("fvg_rellenado", True):
        return False
    ob_top    = ob["bear_ob_top"]
    ob_bottom = ob["bear_ob_bottom"]
    fvg_bot   = fvg["fvg_bottom"]
    fvg_top   = fvg["fvg_top"]
    overlap        = (fvg_bot <= ob_top and fvg_top >= ob_bottom)
    precio_en_zona = (ob_bottom * 0.99 <= precio <= ob_top or fvg_bot <= precio <= fvg_top)
    return overlap or precio_en_zona


# ══════════════════════════════════════════════════════════════
# BOS + CHoCH
# ══════════════════════════════════════════════════════════════

def detectar_bos_choch(candles: list) -> dict:
    result = {"bos_bull": False, "bos_bear": False, "choch_bull": False, "choch_bear": False}
    if not config.BOS_ACTIVO or len(candles) < 20:
        return result
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    precio = closes[-1]
    lb     = min(50, len(candles))
    swing_highs, swing_lows = [], []
    for i in range(2, lb-2):
        idx = len(candles) - lb + i
        if (highs[idx] > highs[idx-1] and highs[idx] > highs[idx-2] and
                highs[idx] > highs[idx+1] and highs[idx] > highs[idx+2]):
            swing_highs.append(highs[idx])
        if (lows[idx] < lows[idx-1] and lows[idx] < lows[idx-2] and
                lows[idx] < lows[idx+1] and lows[idx] < lows[idx+2]):
            swing_lows.append(lows[idx])
    if swing_highs and precio > swing_highs[-1]:
        result["bos_bull"]  = True
        if len(swing_highs) >= 2 and swing_highs[-1] < swing_highs[-2]:
            result["choch_bull"] = True
    if swing_lows and precio < swing_lows[-1]:
        result["bos_bear"]  = True
        if len(swing_lows) >= 2 and swing_lows[-1] > swing_lows[-2]:
            result["choch_bear"] = True
    return result


# ══════════════════════════════════════════════════════════════
# EQUAL HIGHS / LOWS (Liquidity Pools)
# ══════════════════════════════════════════════════════════════

def _pivot_high(highs, length, idx):
    if idx < length or idx + length >= len(highs):
        return None
    val = highs[idx]
    for i in range(idx-length, idx+length+1):
        if i != idx and highs[i] >= val:
            return None
    return val

def _pivot_low(lows, length, idx):
    if idx < length or idx + length >= len(lows):
        return None
    val = lows[idx]
    for i in range(idx-length, idx+length+1):
        if i != idx and lows[i] <= val:
            return None
    return val

def detectar_eqh_eql(candles: list) -> dict:
    result = {"is_eqh": False, "eqh_price": 0.0, "is_eql": False, "eql_price": 0.0}
    if len(candles) < config.EQ_LOOKBACK:
        return result
    highs  = [c["high"] for c in candles]
    lows   = [c["low"]  for c in candles]
    length = config.EQ_PIVOT_LEN
    thr    = config.EQ_THRESHOLD
    n, lb  = len(highs), config.EQ_LOOKBACK
    ph_list, pl_list = [], []
    for i in range(max(length, n-lb-length), n-length):
        ph = _pivot_high(highs, length, i)
        if ph:
            ph_list.append(ph)
        pl = _pivot_low(lows, length, i)
        if pl:
            pl_list.append(pl)
    if len(ph_list) >= 2:
        for i in range(len(ph_list)-1, 0, -1):
            for j in range(i-1, max(i-10, -1), -1):
                if abs(ph_list[i]-ph_list[j])/ph_list[i]*100 <= thr:
                    result["is_eqh"]    = True
                    result["eqh_price"] = ph_list[i]
                    break
            if result["is_eqh"]:
                break
    if len(pl_list) >= 2:
        for i in range(len(pl_list)-1, 0, -1):
            for j in range(i-1, max(i-10, -1), -1):
                if abs(pl_list[i]-pl_list[j])/pl_list[i]*100 <= thr:
                    result["is_eql"]    = True
                    result["eql_price"] = pl_list[i]
                    break
            if result["is_eql"]:
                break
    return result


# ══════════════════════════════════════════════════════════════
# ICT KILLZONES
# ══════════════════════════════════════════════════════════════

def en_killzone() -> dict:
    ahora  = datetime.now(timezone.utc)
    tim    = ahora.hour * 60 + ahora.minute
    asia   = config.KZ_ASIA_START   <= tim < config.KZ_ASIA_END
    london = config.KZ_LONDON_START <= tim < config.KZ_LONDON_END
    ny     = config.KZ_NY_START     <= tim < config.KZ_NY_END
    return {
        "in_asia":   asia,
        "in_london": london,
        "in_ny":     ny,
        "in_kz":     asia or london or ny,
        "nombre":    "ASIA" if asia else ("LONDON" if london else ("NY" if ny else "FUERA")),
    }


# ══════════════════════════════════════════════════════════════
# VOLUMEN
# ══════════════════════════════════════════════════════════════

def volumen_ok(candles: list) -> bool:
    if len(candles) < 21:
        return True
    vols = [c["volume"] for c in candles[-21:-1]]
    avg  = sum(vols) / len(vols) if vols else 0
    return avg <= 0 or candles[-1]["volume"] / avg >= 0.20


# ══════════════════════════════════════════════════════════════
# COOLDOWN
# ══════════════════════════════════════════════════════════════

def _tf_to_seconds(tf: str) -> int:
    tf = tf.strip().lower()
    try:
        if tf.endswith("h"):   return int(tf[:-1]) * 3600
        elif tf.endswith("m"): return int(tf[:-1]) * 60
        elif tf.endswith("d"): return int(tf[:-1]) * 86400
        else:                  return int(tf) * 60
    except Exception:
        return 300

def cooldown_ok(par: str) -> bool:
    last = _last_signal_ts.get(par, 0)
    secs = config.COOLDOWN_VELAS * _tf_to_seconds(config.TIMEFRAME)
    return (time.time() - last) >= secs

def registrar_senal_ts(par: str):
    _last_signal_ts[par] = time.time()


# ══════════════════════════════════════════════════════════════
# SEÑAL PRINCIPAL v5.0 — Score máximo 16 puntos
# ══════════════════════════════════════════════════════════════

_NO_CRIPTO_PREFIJOS = ("NCC", "NCFX", "FOREX", "STOCK", "NCX", "INDEX")
_NO_CRIPTO_SUFIJOS  = ("2USD", "2USDT", "2GBP", "2EUR", "2BTC")

def _par_es_cripto(par: str) -> bool:
    base = par.replace("-USDT", "").upper()
    for p in _NO_CRIPTO_PREFIJOS:
        if base.startswith(p):
            return False
    for s in _NO_CRIPTO_SUFIJOS:
        if base.endswith(s):
            return False
    if "2USD" in base or "FOREX" in base:
        return False
    return True


def analizar_par(par: str):
    try:
        # Filtro: solo criptomonedas reales
        if not _par_es_cripto(par):
            return None

        candles = exchange.get_candles(par, config.TIMEFRAME, config.CANDLES_LIMIT)
        if len(candles) < 60 or not volumen_ok(candles):
            return None

        closes = [c["close"] for c in candles]
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        precio = closes[-1]
        if precio <= 0:
            return None

        atr  = calc_atr(highs, lows, closes, config.ATR_PERIOD)
        atr7 = calc_atr(highs, lows, closes, config.ATR_FAST)
        if atr <= 0:
            return None

        ema_f         = calc_ema(closes, config.EMA_FAST)
        ema_s         = calc_ema(closes, config.EMA_SLOW)
        bull_trend_5m = ema_f is not None and ema_s is not None and ema_f > ema_s
        bear_trend_5m = ema_f is not None and ema_s is not None and ema_f < ema_s

        ema_loc_f = calc_ema(closes, config.EMA_LOCAL_FAST)
        ema_loc_s = calc_ema(closes, config.EMA_LOCAL_SLOW)
        ema9_bull = ema_loc_f is not None and ema_loc_s is not None and ema_loc_f > ema_loc_s
        ema9_bear = ema_loc_f is not None and ema_loc_s is not None and ema_loc_f < ema_loc_s

        rsi = calc_rsi(closes, config.RSI_PERIOD) or 50.0

        vwap       = calc_vwap(candles)
        sobre_vwap = precio > vwap
        bajo_vwap  = precio < vwap

        macd_l, macd_sig, macd_hist = calc_macd(closes)
        macd_bull = macd_hist is not None and macd_hist > 0
        macd_bear = macd_hist is not None and macd_hist < 0
        vol_ratio = calc_volumen_ratio(candles)
        vol_spike = vol_ratio >= 1.3

        # HTF multi-timeframe
        htf            = tendencia_htf(par)    # 1H
        htf_4h         = tendencia_4h(par)     # 4H (nueva)
        # FIX: HTF 1H es filtro DURO — no entrar contra tendencia mayor
        # Si ambos HTF (1H y 4H) contradicen la dirección → bloquear
        htf_contra_long  = (htf == "BEAR" and htf_4h == "BEAR")
        htf_contra_short = (htf == "BULL" and htf_4h == "BULL")
        trend_ok_long  = not htf_contra_long
        trend_ok_short = not htf_contra_short

        # SMC core
        fvg   = detectar_fvg(candles)
        eq    = detectar_eqh_eql(candles)
        ob    = detectar_order_blocks(candles)
        bos   = detectar_bos_choch(candles)
        asia  = get_rango_asia(candles)
        sweep = detectar_sweep(candles)

        # NUEVO v5.0
        pd_zone = calc_premium_discount(candles, lookback=50)
        disp    = detectar_displacement(candles)
        idm_l   = detectar_inducement(candles, "LONG")
        idm_s   = detectar_inducement(candles, "SHORT")

        pat_long  = detectar_patron_vela(candles, "LONG")
        pat_short = detectar_patron_vela(candles, "SHORT")

        # Pivotes diarios
        candles_d = exchange.get_candles(par, "1d", 5)
        pivotes   = None
        near_s1 = near_s2 = near_r1 = near_r2 = near_pp = False
        if len(candles_d) >= 2:
            prev_d  = candles_d[-2]
            pivotes = calc_pivotes(prev_d["high"], prev_d["low"], prev_d["close"])
            pct     = config.PIVOT_NEAR_PCT / 100
            pct_w   = pct * 1.5
            near_s1 = abs(precio - pivotes["S1"]) / precio < pct
            near_s2 = abs(precio - pivotes["S2"]) / precio < pct
            near_r1 = abs(precio - pivotes["R1"]) / precio < pct
            near_r2 = abs(precio - pivotes["R2"]) / precio < pct
            near_pp = abs(precio - pivotes["PP"]) / precio < pct_w

        near_asia_low = near_asia_high = False
        asia_brk_low  = asia_brk_high  = False
        if asia["valido"]:
            pct            = config.PIVOT_NEAR_PCT / 100
            near_asia_low  = abs(precio - asia["low"])  / precio < pct
            near_asia_high = abs(precio - asia["high"]) / precio < pct
            asia_brk_low   = precio < asia["low"]  * 1.005
            asia_brk_high  = precio > asia["high"] * 0.995

        if not cooldown_ok(par):
            return None

        kz = en_killzone()
        if config.KZ_REQUERIDA and not kz["in_kz"]:
            return None

        ob_fvg_bull = ob_fvg_confluencia_bull(ob, fvg, precio)
        ob_fvg_bear = ob_fvg_confluencia_bear(ob, fvg, precio)

        # ──────────────────────────────────────────────────────
        # SCORING LONG v5.0 (máx 16)
        # ──────────────────────────────────────────────────────
        sl_long = 0
        ml_long = []

        # Estructura SMC (base)
        if ob_fvg_bull:
            sl_long += 3; ml_long.append("OB+FVG")
        elif fvg["bull_fvg"] and not fvg.get("fvg_rellenado", True):
            sl_long += 2; ml_long.append("FVG")
            if fvg_grande(fvg, atr):
                sl_long += 1; ml_long.append("FVG+")
        elif ob_valido_bull(ob, precio):
            sl_long += 1; ml_long.append("OB")

        if ob_fvg_bull and fvg_grande(fvg, atr):
            sl_long += 1; ml_long.append("FVG+")

        if sweep["sweep_bull"]:
            sl_long += 2; ml_long.append("SWEEP")
            if kz["in_kz"]:
                sl_long += 1; ml_long.append("SWEEP_KZ")  # Sweep en killzone = setup institucional

        if bos["bos_bull"]:
            sl_long += 1
            ml_long.append("CHoCH" if bos["choch_bull"] else "BOS")

        # Patrón de vela
        if pat_long["patron"]:
            sl_long += pat_long["confianza"]
            ml_long.append(pat_long["patron"])

        # NUEVO v5.0: Premium/Discount zone
        if pd_zone["discount"]:
            sl_long += 1; ml_long.append(f"DISCOUNT{pd_zone['zona_pct']:.0f}%")

        # NUEVO v5.0: Displacement alcista previo
        if disp["bull_disp"]:
            sl_long += 1; ml_long.append(f"DISP{disp['fuerza']}")

        # NUEVO v5.0: Inducement (trampa bajista previa)
        if idm_l:
            sl_long += 1; ml_long.append("IDM")

        # HTF multi-timeframe
        if htf == "BULL":
            sl_long += 1; ml_long.append("MTF1H")
        if htf_4h == "BULL":
            sl_long += 1; ml_long.append("MTF4H")

        if ema9_bull and sobre_vwap:
            sl_long += 1; ml_long.append("EMA9+VWAP")

        if bull_trend_5m:
            sl_long += 1; ml_long.append("EMA21")

        if rsi <= 40:
            sl_long += 2; ml_long.append(f"RSI{rsi:.0f}")   # RSI oversold = mejor entrada
        elif rsi <= config.RSI_BUY_MAX:
            sl_long += 1; ml_long.append(f"RSI{rsi:.0f}")
        elif rsi > 65:
            sl_long -= 1  # RSI alto = penalizar LONG (posible techo)

        if macd_bull and config.MACD_ACTIVO:
            sl_long += 1; ml_long.append("MACD")

        if vol_spike:
            sl_long += 1; ml_long.append(f"VOL{vol_ratio:.1f}x")

        if kz["in_kz"]:
            sl_long += 1; ml_long.append(f"KZ_{kz['nombre']}")

        if near_s1:        sl_long += 1; ml_long.append("S1")
        if near_s2:        sl_long += 1; ml_long.append("S2")
        if eq["is_eql"]:   sl_long += 1; ml_long.append("EQL")
        if near_asia_low:  sl_long += 1; ml_long.append("ASIA_LOW")
        if asia_brk_low:   sl_long += 1; ml_long.append("ASIA_BRK")
        if near_pp:        sl_long += 1; ml_long.append("PP")

        # ──────────────────────────────────────────────────────
        # SCORING SHORT v5.0 (máx 16)
        # ──────────────────────────────────────────────────────
        sl_short = 0
        ml_short = []

        if ob_fvg_bear:
            sl_short += 3; ml_short.append("OB+FVG")
        elif fvg["bear_fvg"] and not fvg.get("fvg_rellenado", True):
            sl_short += 2; ml_short.append("FVG")
            if fvg_grande(fvg, atr):
                sl_short += 1; ml_short.append("FVG+")
        elif ob_valido_bear(ob, precio):
            sl_short += 1; ml_short.append("OB")

        if ob_fvg_bear and fvg_grande(fvg, atr):
            sl_short += 1; ml_short.append("FVG+")

        if sweep["sweep_bear"]:
            sl_short += 2; ml_short.append("SWEEP")
            if kz["in_kz"]:
                sl_short += 1; ml_short.append("SWEEP_KZ")

        if bos["bos_bear"]:
            sl_short += 1
            ml_short.append("CHoCH" if bos["choch_bear"] else "BOS")

        if pat_short["patron"]:
            sl_short += pat_short["confianza"]
            ml_short.append(pat_short["patron"])

        # NUEVO v5.0: Premium zone
        if pd_zone["premium"]:
            sl_short += 1; ml_short.append(f"PREMIUM{pd_zone['zona_pct']:.0f}%")

        # NUEVO v5.0: Displacement bajista
        if disp["bear_disp"]:
            sl_short += 1; ml_short.append(f"DISP{disp['fuerza']}")

        # NUEVO v5.0: Inducement (trampa alcista previa)
        if idm_s:
            sl_short += 1; ml_short.append("IDM")

        # HTF
        if htf == "BEAR":
            sl_short += 1; ml_short.append("MTF1H")
        if htf_4h == "BEAR":
            sl_short += 1; ml_short.append("MTF4H")

        if ema9_bear and bajo_vwap:
            sl_short += 1; ml_short.append("EMA9+VWAP")

        if bear_trend_5m:
            sl_short += 1; ml_short.append("EMA21")

        if rsi >= 65:
            sl_short += 2; ml_short.append(f"RSI{rsi:.0f}")  # RSI overbought = mejor short
        elif rsi >= config.RSI_SELL_MIN:
            sl_short += 1; ml_short.append(f"RSI{rsi:.0f}")
        elif rsi < 35:
            sl_short -= 1  # RSI bajo = penalizar SHORT (posible suelo)

        if macd_bear and config.MACD_ACTIVO:
            sl_short += 1; ml_short.append("MACD")

        if vol_spike:
            sl_short += 1; ml_short.append(f"VOL{vol_ratio:.1f}x")

        if kz["in_kz"]:
            sl_short += 1; ml_short.append(f"KZ_{kz['nombre']}")

        if near_r1:         sl_short += 1; ml_short.append("R1")
        if near_r2:         sl_short += 1; ml_short.append("R2")
        if eq["is_eqh"]:    sl_short += 1; ml_short.append("EQH")
        if near_asia_high:  sl_short += 1; ml_short.append("ASIA_HIGH")
        if asia_brk_high:   sl_short += 1; ml_short.append("ASIA_BRK")
        if near_pp:         sl_short += 1; ml_short.append("PP")

        # ──────────────────────────────────────────────────────
        # CONDICIONES BASE
        # ──────────────────────────────────────────────────────
        zona_long  = (near_s1 or near_s2 or near_pp or eq["is_eql"] or
                      near_asia_low or asia_brk_low or ob["bull_ob"] or pd_zone["discount"])
        zona_short = (near_r1 or near_r2 or near_pp or eq["is_eqh"] or
                      near_asia_high or asia_brk_high or ob["bear_ob"] or pd_zone["premium"])

        # FVG sin rellenar es requisito en v5.0 (FVG ya rellenado no cuenta)
        fvg_bull_valido = fvg["bull_fvg"] and not fvg.get("fvg_rellenado", True)
        fvg_bear_valido = fvg["bear_fvg"] and not fvg.get("fvg_rellenado", True)

        # FIX v5.1: base también válida si hay OB válido en zona, o BOS con zona
        base_long  = (
            (fvg_bull_valido and zona_long)          # FVG no rellenado + zona
            or ob_fvg_bull                            # OB+FVG confluencia
            or sweep["sweep_bull"]                    # Liquidity sweep
            or idm_l                                  # Inducement
            or (ob_valido_bull(ob, precio) and zona_long)   # OB válido en zona
            or (bos["bos_bull"] and zona_long)        # BOS + zona
        )
        base_short = (
            (fvg_bear_valido and zona_short)
            or ob_fvg_bear
            or sweep["sweep_bear"]
            or idm_s
            or (ob_valido_bear(ob, precio) and zona_short)
            or (bos["bos_bear"] and zona_short)
        )

        # Filtros de confirmación — respeta VELA_CONFIRMACION
        rng_vela   = max(candles[-1]["high"] - candles[-1]["low"], 1e-10)
        if config.VELA_CONFIRMACION:
            conf_long  = (candles[-1]["close"] > candles[-1]["open"] or
                          (candles[-1]["close"] - candles[-1]["low"]) / rng_vela > 0.45)
            conf_short = (candles[-1]["close"] < candles[-1]["open"] or
                          (candles[-1]["high"] - candles[-1]["close"]) / rng_vela > 0.45)
        else:
            conf_long = conf_short = True

        mom_long  = momentum_ok(candles, "LONG")
        mom_short = momentum_ok(candles, "SHORT")

        rsi_ok_long  = rsi < config.RSI_BUY_MAX  or sl_long  >= 8
        rsi_ok_short = rsi > config.RSI_SELL_MIN or sl_short >= 8

        score_min = config.SCORE_MIN

        # ── Elegir dirección ──
        lado = score = None
        motivos = []

        if (not config.SOLO_LONG and base_short and
                sl_short >= score_min and trend_ok_short and
                conf_short and mom_short and rsi_ok_short):
            if sl_short > sl_long:
                lado, score, motivos = "SHORT", sl_short, ml_short

        if (base_long and sl_long >= score_min and trend_ok_long and
                conf_long and mom_long and rsi_ok_long):
            if lado is None or sl_long >= sl_short:
                lado, score, motivos = "LONG", sl_long, ml_long

        # Log diagnóstico
        if lado is None:
            if sl_long >= 3 or sl_short >= 3:
                razon_l, razon_s = [], []
                if not base_long:
                    razon_l.append(f"noBase(fvg={fvg_bull_valido},zona={zona_long},sweep={sweep['sweep_bull']},ob_fvg={ob_fvg_bull})")
                if sl_long < score_min:
                    razon_l.append(f"score={sl_long}<{score_min}")
                if not conf_long:
                    razon_l.append("noConf")
                if not base_short:
                    razon_s.append(f"noBase(fvg={fvg_bear_valido},zona={zona_short},ob_fvg={ob_fvg_bear})")
                if sl_short < score_min:
                    razon_s.append(f"score={sl_short}<{score_min}")
                log.info(
                    f"[NO-SE] {par} L:{sl_long}pts({','.join(razon_l)}) "
                    f"S:{sl_short}pts({','.join(razon_s)}) "
                    f"rsi={rsi:.1f} htf={htf} kz={kz['nombre']}"
                )
            return None

        # ── SL/TP dinámico basado en estructura ──
        atr_sl = atr7 if atr7 > 0 else atr
        # SL mínimo garantizado: 1.2x ATR para evitar hits por ruido
        sl_min_dist = atr_sl * max(config.SL_ATR_MULT, 1.2)

        if lado == "LONG":
            # SL: usar estructura si está cerca, sino ATR con margen
            sl_ob   = ob["bull_ob_bottom"] * 0.997 if (ob["bull_ob"] and not ob["bull_ob_mitigado"]) else 0
            sl_asia = asia["low"] * 0.997 if (asia["valido"] and asia["low"] < precio) else 0
            sl_low  = min(c["low"] for c in candles[-5:]) * 0.997  # Mínimo reciente de 5 velas
            sl_atr  = precio - sl_min_dist
            # Preferir estructura sobre ATR, pero respetar distancia mínima
            sl_struct = max([x for x in [sl_ob, sl_asia, sl_low] if 0 < x < precio - atr_sl * 0.5], default=0)
            sl = sl_struct if sl_struct > sl_atr else sl_atr
            # TP: basado en R:R mínimo 2.5 sobre el SL real
            dist_sl = precio - sl
            tp   = precio + dist_sl * max(config.TP_ATR_MULT, 2.5)
            tp1  = precio + dist_sl * 1.2
            tp2  = precio + dist_sl * 4.0
        else:
            sl_ob   = ob["bear_ob_top"] * 1.003 if (ob["bear_ob"] and not ob["bear_ob_mitigado"]) else 0
            sl_asia = asia["high"] * 1.003 if (asia["valido"] and asia["high"] > precio) else 0
            sl_high = max(c["high"] for c in candles[-5:]) * 1.003  # Máximo reciente de 5 velas
            sl_atr  = precio + sl_min_dist
            sl_struct = min([x for x in [sl_ob, sl_asia, sl_high] if x > precio + atr_sl * 0.5], default=float("inf"))
            sl = sl_struct if sl_struct < sl_atr else sl_atr
            dist_sl = sl - precio
            tp   = precio - dist_sl * max(config.TP_ATR_MULT, 2.5)
            tp1  = precio - dist_sl * 1.2
            tp2  = precio - dist_sl * 4.0

        rr = abs(tp - precio) / abs(precio - sl) if abs(precio - sl) > 0 else 0
        if rr < config.MIN_RR:
            log.debug(f"[R:R] {par} {rr:.2f} < mín {config.MIN_RR}")
            return None

        registrar_senal_ts(par)

        return {
            # Core
            "par":          par,
            "lado":         lado,
            "precio":       precio,
            "sl":           round(sl, 8),
            "tp":           round(tp, 8),
            "tp1":          round(tp1, 8),
            "tp2":          round(tp2, 8),
            "atr":          round(atr_sl, 8),
            "score":        score,
            "rsi":          rsi,
            "rr":           round(rr, 2),
            "motivos":      motivos,
            "kz":           kz["nombre"],
            # HTF
            "htf":          htf,
            "htf_4h":       htf_4h,
            # VWAP
            "vwap":         round(vwap, 8),
            "sobre_vwap":   sobre_vwap,
            # FVG
            "fvg_top":      fvg.get("fvg_top", 0),
            "fvg_bottom":   fvg.get("fvg_bottom", 0),
            "fvg_rellenado": fvg.get("fvg_rellenado", True),
            # OB
            "ob_bull":      ob["bull_ob"],
            "ob_bear":      ob["bear_ob"],
            "ob_fvg_bull":  ob_fvg_bull,
            "ob_fvg_bear":  ob_fvg_bear,
            "ob_mitigado":  ob["bull_ob_mitigado"] if lado == "LONG" else ob["bear_ob_mitigado"],
            # Estructura
            "bos_bull":     bos["bos_bull"],
            "bos_bear":     bos["bos_bear"],
            "choch_bull":   bos["choch_bull"],
            "choch_bear":   bos["choch_bear"],
            "sweep_bull":   sweep["sweep_bull"],
            "sweep_bear":   sweep["sweep_bear"],
            # Patrón
            "patron":       pat_long["patron"] if lado == "LONG" else pat_short["patron"],
            "vela_conf":    pat_long["patron"] is not None if lado == "LONG" else pat_short["patron"] is not None,
            # NUEVO v5.0
            "premium":      pd_zone["premium"],
            "discount":     pd_zone["discount"],
            "zona_pct":     pd_zone["zona_pct"],
            "displacement": disp["bull_disp"] if lado == "LONG" else disp["bear_disp"],
            "inducement":   idm_l if lado == "LONG" else idm_s,
            # Misc
            "pivotes":      pivotes,
            "macd_hist":    macd_hist,
            "vol_ratio":    round(vol_ratio, 2),
            "asia_valido":  asia["valido"],
        }

    except Exception as e:
        log.info(f"[ERR-PAR] {par}: {type(e).__name__}: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# ANÁLISIS MASIVO CON THREADS
# ══════════════════════════════════════════════════════════════

def analizar_todos(pares: list, workers: int = 4) -> list:
    senales = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futuros = {ex.submit(analizar_par, p): p for p in pares}
        for fut in concurrent.futures.as_completed(futuros):
            try:
                res = fut.result()
                if res:
                    senales.append(res)
            except Exception as e:
                log.error(f"thread analizar: {e}")
    senales.sort(key=lambda x: x["score"], reverse=True)
    return senales
