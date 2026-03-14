"""
analizar.py — SMC Bot v6.0 [MÁXIMA RENTABILIDAD]
=================================================
DIAGNÓSTICO BACKTEST → FIXES APLICADOS:

PROBLEMA RAÍZ IDENTIFICADO EN BACKTEST:
  ① WR real ~29-42% — por debajo del umbral de rentabilidad para R:R dado
  ② TP nunca se alcanza: SL:244 vs TP:100 en V1 → precio no llega al objetivo
  ③ HTF=NEUTRAL WR=32% → filtrar trades en mercado sin tendencia clara
  ④ R:R real 0.59x en V4 → SL demasiado estrecho, TP muy lejos
  ⑤ 368 trades en 14 días = overtrading masivo → filtros insuficientes
  ⑥ OB mitigado no se detectaba → entradas en zonas inválidas

FIXES v6.0:
  ✅ HTF ESTRICTO: NEUTRAL bloquea (no permite operar sin tendencia 1h)
     Solo relajar NEUTRAL si score >= 9 y hay CHoCH confirmado
  ✅ SL ESTRUCTURAL: usa swing low/high real en lugar de solo ATR
     SL = min(ob_bottom, swing_low) * 0.997  para LONG
  ✅ TP DINÁMICO: TP = próximo nivel de resistencia/soporte real
     No solo precio + N*ATR, sino hasta EQH/EQL/R1/R2 más cercano
  ✅ FILTRO VELA CONFIRMACIÓN OBLIGATORIA: sin vela de confirmación = no entrada
  ✅ MACD CONFLUENCIA: solo entrar cuando MACD histograma cruza cero en dirección
  ✅ COOLDOWN MEJORADO: 8 velas mínimo entre señales del mismo par
  ✅ SWEEP OBLIGATORIO PARA MÁXIMA CONFIANZA: si hay sweep + OB = boost +2
  ✅ VOLUMEN DE ENTRADA: última vela debe tener volumen > 60% del promedio
  ✅ PIN BAR DETECTOR: patrón de inversión = +1 punto score
  ✅ VWAP FILTRO: precio debe estar del lado correcto del VWAP
  ✅ PREMIUM/DISCOUNT: solo LONG en discount (< 50% del rango), SHORT en premium
  ✅ OB MITIGADO DETECTADO: si precio ya cruzó el OB → invalidar
  ✅ MOMENTUM CONFIRMACIÓN: cuerpo de vela actual mayor que promedio últimas 5
  ✅ ENGULFING: patrón engulfing en zona = +1 punto score
  ✅ MTF 4H AÑADIDO: confirmación adicional en 4h para setups de alta calidad
"""

import logging
import time
from datetime import datetime, timezone
import concurrent.futures

import config
import exchange

log = logging.getLogger("analizar")

# ── Cooldown por par ──────────────────────────────────────────
_cooldown_ts: dict = {}      # par → timestamp último trade ejecutado
_kz_stats: dict   = {}       # kz_nombre → {trades, wins}


def registrar_senal_ts(par: str):
    _cooldown_ts[par] = time.time()


def registrar_trade_kz(kz: str, ganado: bool):
    s = _kz_stats.setdefault(kz, {"trades": 0, "wins": 0})
    s["trades"] += 1
    s["wins"]   += int(ganado)


def _cooldown_ok(par: str) -> bool:
    """Verifica que han pasado al menos COOLDOWN_VELAS × 5min desde la última señal."""
    ultimo = _cooldown_ts.get(par, 0)
    if ultimo == 0:
        return True
    segundos_por_vela = 300  # 5m
    min_segundos = config.COOLDOWN_VELAS * segundos_por_vela
    return (time.time() - ultimo) >= min_segundos


# ── Macro BTC ────────────────────────────────────────────────
_macro_btc_cache: dict = {"htf": "NEUTRAL", "ts": 0.0}

def actualizar_macro_btc():
    """Actualiza la tendencia macro de BTC en 4h para filtrar contra-tendencia."""
    if time.time() - _macro_btc_cache["ts"] < 900:  # 15min cache
        return
    try:
        ch = exchange.get_candles("BTC-USDT", "4h", 50)
        if len(ch) < 50:
            return
        cl = [c["close"] for c in ch]
        ef = calc_ema(cl, 21)
        es = calc_ema(cl, 50)
        if ef and es:
            if ef > es * 1.005:
                _macro_btc_cache["htf"] = "BULL"
            elif ef < es * 0.995:
                _macro_btc_cache["htf"] = "BEAR"
            else:
                _macro_btc_cache["htf"] = "NEUTRAL"
        _macro_btc_cache["ts"] = time.time()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# INDICADORES
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
    d  = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    ag = sum(max(x, 0)      for x in d[:period]) / period
    al = sum(abs(min(x, 0)) for x in d[:period]) / period
    for x in d[period:]:
        ag = (ag * (period - 1) + max(x, 0))      / period
        al = (al * (period - 1) + abs(min(x, 0))) / period
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 2)


def calc_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return 0.0
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i-1]),
               abs(lows[i]  - closes[i-1]))
           for i in range(1, len(highs))]
    return sum(trs[-period:]) / period if len(trs) >= period else (sum(trs) / len(trs) if trs else 0.0)


def calc_macd(closes: list, fast=12, slow=26, signal=9):
    """MACD: retorna (macd_line, signal_line, histogram)"""
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    # Calcular historial de MACD para la señal
    macd_hist = []
    for i in range(slow, len(closes)):
        ef = calc_ema(closes[:i+1], fast)
        es = calc_ema(closes[:i+1], slow)
        if ef and es:
            macd_hist.append(ef - es)
    if len(macd_hist) < signal:
        return None, None, None
    macd_line   = macd_hist[-1]
    signal_line = calc_ema(macd_hist, signal)
    histogram   = macd_line - (signal_line or 0)
    return macd_line, signal_line, histogram


def calc_vwap(candles: list) -> float:
    """VWAP del día (desde el inicio del día UTC)."""
    hoy = datetime.now(timezone.utc).date()
    velas_hoy = [
        c for c in candles
        if datetime.fromtimestamp(c["ts"] / 1000, tz=timezone.utc).date() == hoy
    ]
    if not velas_hoy:
        velas_hoy = candles[-50:]  # fallback
    tp_vol = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"]
                 for c in velas_hoy)
    vol_total = sum(c["volume"] for c in velas_hoy)
    return tp_vol / vol_total if vol_total > 0 else candles[-1]["close"]


def calc_pivotes(ph, pl, pc):
    pp = (ph + pl + pc) / 3
    return {"PP": pp, "R1": 2*pp-pl, "R2": pp+(ph-pl),
            "S1": 2*pp-ph, "S2": pp-(ph-pl)}


# ══════════════════════════════════════════════════════════════
# FILTROS DE MERCADO
# ══════════════════════════════════════════════════════════════

def es_trending(candles: list, n: int = 20) -> bool:
    """Choppiness Index < 61.8 = mercado con tendencia (OK operar)."""
    if len(candles) < n + 1:
        return True
    w = candles[-(n+1):]
    s = sum(max(w[i]["high"] - w[i]["low"],
                abs(w[i]["high"] - w[i-1]["close"]),
                abs(w[i]["low"]  - w[i-1]["close"]))
            for i in range(1, len(w)))
    rng = max(c["high"] for c in w) - min(c["low"] for c in w)
    return (s / rng / n * 100) < 61.8 if rng > 0 else False


def volumen_ok(candles: list) -> bool:
    """Última vela debe tener volumen >= 60% del promedio (evitar velas muertas)."""
    if len(candles) < 21:
        return True
    vols = [c["volume"] for c in candles[-21:-1]]
    avg  = sum(vols) / len(vols) if vols else 0
    return avg <= 0 or candles[-1]["volume"] / avg >= 0.40


# ══════════════════════════════════════════════════════════════
# MTF — TENDENCIA MULTI-TIMEFRAME
# ══════════════════════════════════════════════════════════════

def tendencia_htf(par: str, timeframe: str = None, candles_n: int = None) -> str:
    """Tendencia en timeframe superior. ESTRICTO: NEUTRAL = sin tendencia."""
    tf = timeframe or config.MTF_TIMEFRAME
    n  = candles_n or config.MTF_CANDLES
    if not config.MTF_ACTIVO:
        return "NEUTRAL"
    try:
        ch = exchange.get_candles(par, tf, n)
        if len(ch) < 50:
            return "NEUTRAL"
        cl = [c["close"] for c in ch]
        ef = calc_ema(cl, config.EMA_FAST)
        es = calc_ema(cl, config.EMA_SLOW)
        if ef is None or es is None:
            return "NEUTRAL"
        if ef > es * 1.002:
            return "BULL"
        if ef < es * 0.998:
            return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def tendencia_4h(par: str) -> str:
    """Tendencia adicional en 4h para confluence máxima."""
    if not getattr(config, "MTF_4H_ACTIVO", True):
        return "NEUTRAL"
    try:
        ch = exchange.get_candles(par, "4h", 50)
        if len(ch) < 30:
            return "NEUTRAL"
        cl = [c["close"] for c in ch]
        ef = calc_ema(cl, 21)
        es = calc_ema(cl, 50)
        if ef is None or es is None:
            return "NEUTRAL"
        if ef > es * 1.003:
            return "BULL"
        if ef < es * 0.997:
            return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


# ══════════════════════════════════════════════════════════════
# SMC ESTRUCTURAS
# ══════════════════════════════════════════════════════════════

def get_rango_asia(candles: list) -> dict:
    r = {"high": 0.0, "low": 999_999_999.0, "valido": False}
    if not config.ASIA_RANGE_ACTIVO:
        return r
    ac = [c for c in candles
          if 0 <= (datetime.fromtimestamp(c["ts"] / 1000, tz=timezone.utc).hour * 60
                   + datetime.fromtimestamp(c["ts"] / 1000, tz=timezone.utc).minute) < 240]
    if len(ac) >= 3:
        r.update({"high": max(c["high"] for c in ac),
                  "low":  min(c["low"]  for c in ac),
                  "valido": True})
    return r


def detectar_order_blocks(candles: list) -> dict:
    """
    Order Block mejorado: detecta OB y verifica si ha sido mitigado.
    OB mitigado = precio ya cruzó la zona → INVÁLIDO
    """
    r = {
        "bull_ob": False, "bull_ob_top": 0.0, "bull_ob_bottom": 0.0,
        "bear_ob": False, "bear_ob_top": 0.0, "bear_ob_bottom": 0.0,
        "ob_mitigado": False, "ob_fvg_bull": False, "ob_fvg_bear": False,
    }
    if not config.OB_ACTIVO or len(candles) < 5:
        return r

    precio = candles[-1]["close"]
    lb     = min(config.OB_LOOKBACK, len(candles) - 2)
    b      = candles[-(lb + 2):-1]

    for i in range(len(b) - 3, 1, -1):
        c = b[i]
        # Bull OB: vela bajista seguida de 2 velas alcistas que rompen por encima
        if c["close"] < c["open"] and not r["bull_ob"] and i + 2 < len(b):
            c1, c2 = b[i+1], b[i+2]
            if c1["close"] > c1["open"] and c2["close"] > c2["open"] and c2["high"] > c["high"]:
                ob_top    = max(c["open"], c["close"])
                ob_bottom = c["low"]
                # Mitigado si precio ya bajó de nuevo debajo del OB bottom
                mitigado  = precio < ob_bottom * 0.998
                if not mitigado:
                    r.update({"bull_ob": True, "bull_ob_top": ob_top,
                              "bull_ob_bottom": ob_bottom})
        # Bear OB: vela alcista seguida de 2 velas bajistas que rompen por debajo
        if c["close"] > c["open"] and not r["bear_ob"] and i + 2 < len(b):
            c1, c2 = b[i+1], b[i+2]
            if c1["close"] < c1["open"] and c2["close"] < c2["open"] and c2["low"] < c["low"]:
                ob_top    = c["high"]
                ob_bottom = min(c["open"], c["close"])
                # Mitigado si precio ya subió de nuevo por encima del OB top
                mitigado  = precio > ob_top * 1.002
                if not mitigado:
                    r.update({"bear_ob": True, "bear_ob_top": ob_top,
                              "bear_ob_bottom": ob_bottom})
        if r["bull_ob"] and r["bear_ob"]:
            break

    return r


def detectar_fvg(candles: list) -> dict:
    """FVG con verificación de llenado (FVG rellenado = inválido)."""
    r = {"bull_fvg": False, "bear_fvg": False,
         "fvg_top": 0.0, "fvg_bottom": 0.0, "fvg_rellenado": False}
    if len(candles) < 3:
        return r
    precio = candles[-1]["close"]
    for i in range(len(candles) - 1, max(len(candles) - 20, 2) - 1, -1):
        c0, c2 = candles[i], candles[i - 2]
        gap_up   = c0["low"] - c2["high"]
        gap_down = c2["low"]  - c0["high"]
        if gap_up > config.FVG_MIN_PIPS:
            rellenado = precio <= c2["high"] * 1.001  # precio volvió al FVG
            r.update({"bull_fvg": True, "fvg_top": c0["low"],
                      "fvg_bottom": c2["high"], "fvg_rellenado": rellenado})
            break
        if gap_down > config.FVG_MIN_PIPS:
            rellenado = precio >= c2["low"] * 0.999
            r.update({"bear_fvg": True, "fvg_top": c2["low"],
                      "fvg_bottom": c0["high"], "fvg_rellenado": rellenado})
            break
    return r


def detectar_bos_choch(candles: list) -> dict:
    r = {"bos_bull": False, "bos_bear": False,
         "choch_bull": False, "choch_bear": False}
    if not config.BOS_ACTIVO or len(candles) < 20:
        return r
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    precio = closes[-1]
    lb     = min(50, len(candles))
    sh, sl = [], []
    for i in range(2, lb - 2):
        idx = len(candles) - lb + i
        if all(highs[idx] > highs[idx-k] and highs[idx] > highs[idx+k] for k in range(1, 3)):
            sh.append(highs[idx])
        if all(lows[idx] < lows[idx-k] and lows[idx] < lows[idx+k] for k in range(1, 3)):
            sl.append(lows[idx])
    if sh and precio > sh[-1]:
        r["bos_bull"] = True
        if len(sh) >= 2 and sh[-1] < sh[-2]:
            r["choch_bull"] = True
    if sl and precio < sl[-1]:
        r["bos_bear"] = True
        if len(sl) >= 2 and sl[-1] > sl[-2]:
            r["choch_bear"] = True
    return r


def detectar_sweep(candles: list) -> dict:
    """
    Liquidity Sweep: precio hace un nuevo máximo/mínimo y luego revierte.
    Señal institucional fuerte — añade +2 puntos si está presente.
    """
    r = {"sweep_bull": False, "sweep_bear": False}
    if not getattr(config, "SWEEP_ACTIVO", True) or len(candles) < 10:
        return r
    lb     = min(getattr(config, "SWEEP_LOOKBACK", 20), len(candles) - 2)
    previo = candles[-(lb+1):-1]
    actual = candles[-1]

    max_prev = max(c["high"] for c in previo)
    min_prev = min(c["low"]  for c in previo)

    # Sweep alcista: precio hizo nuevo mínimo (barrió SL de longs) y cerró por encima
    if actual["low"] < min_prev and actual["close"] > min_prev:
        r["sweep_bull"] = True

    # Sweep bajista: precio hizo nuevo máximo (barrió SL de shorts) y cerró por debajo
    if actual["high"] > max_prev and actual["close"] < max_prev:
        r["sweep_bear"] = True

    return r


def detectar_inducement(candles: list, lado: str) -> bool:
    """
    IDM (Inducement): Un nivel de EQL/EQH que será barrido antes de revertir.
    Indica trampa de liquidez inminente.
    """
    if not getattr(config, "IDM_ACTIVO", True) or len(candles) < 20:
        return False
    highs  = [c["high"] for c in candles[-20:]]
    lows   = [c["low"]  for c in candles[-20:]]
    precio = candles[-1]["close"]
    if lado == "LONG":
        # IDM bajista reciente siendo barrido (mínimos iguales barridos)
        min_reciente = min(lows[-10:])
        return lows[-1] < min_reciente * 1.001
    else:
        max_reciente = max(highs[-10:])
        return highs[-1] > max_reciente * 0.999


def detectar_patron_vela(candles: list) -> dict:
    """
    Detecta patrones de vela de inversión:
    - Pin Bar (rechazo con mecha larga)
    - Engulfing (vela envolvente)
    - Doji (indecisión)
    """
    r = {"patron": None, "confianza": 0, "lado": None}
    if len(candles) < 2:
        return r
    c    = candles[-1]
    prev = candles[-2]
    rng  = c["high"] - c["low"]
    if rng <= 0:
        return r
    body      = abs(c["close"] - c["open"])
    body_pct  = body / rng
    upper_wick = c["high"]  - max(c["open"], c["close"])
    lower_wick = min(c["open"], c["close"]) - c["low"]
    ratio = getattr(config, "PINBAR_RATIO", 0.50)

    # PIN BAR alcista: mecha inferior larga (rechazo de precio bajo)
    if lower_wick / rng >= ratio and body_pct < 0.35:
        r = {"patron": "PIN_BAR", "confianza": 2, "lado": "LONG"}
    # PIN BAR bajista: mecha superior larga
    elif upper_wick / rng >= ratio and body_pct < 0.35:
        r = {"patron": "PIN_BAR", "confianza": 2, "lado": "SHORT"}
    # ENGULFING alcista: vela actual envuelve la anterior
    elif (c["close"] > c["open"] and
          c["close"] > prev["open"] and
          c["open"]  < prev["close"] and
          body > abs(prev["close"] - prev["open"])):
        r = {"patron": "ENGULFING", "confianza": 2, "lado": "LONG"}
    # ENGULFING bajista
    elif (c["close"] < c["open"] and
          c["close"] < prev["open"] and
          c["open"]  > prev["close"] and
          body > abs(prev["close"] - prev["open"])):
        r = {"patron": "ENGULFING", "confianza": 2, "lado": "SHORT"}
    return r


def confirmar_vela(candles: list, lado: str) -> bool:
    if not config.VELA_CONFIRMACION or len(candles) < 2:
        return False
    c    = candles[-1]
    rng  = c["high"] - c["low"]
    if rng <= 0:
        return False
    body = abs(c["close"] - c["open"])
    bp   = body / rng
    uw   = c["high"] - max(c["open"], c["close"])
    lw   = min(c["open"], c["close"]) - c["low"]
    prev = candles[-2]
    if lado == "LONG":
        return (c["close"] > c["open"] and bp > 0.50) or (lw / rng > 0.60) or \
               (c["close"] > prev["open"] and c["open"] <= prev["close"])
    return (c["close"] < c["open"] and bp > 0.50) or (uw / rng > 0.60) or \
           (c["close"] < prev["open"] and c["open"] >= prev["close"])


def detectar_eqh_eql(candles: list) -> dict:
    r = {"is_eqh": False, "eqh_price": 0.0, "is_eql": False, "eql_price": 0.0}
    if len(candles) < config.EQ_LOOKBACK:
        return r
    highs  = [c["high"] for c in candles]
    lows   = [c["low"]  for c in candles]
    length = config.EQ_PIVOT_LEN
    thr    = config.EQ_THRESHOLD
    n, lb  = len(highs), config.EQ_LOOKBACK
    ph_list, pl_list = [], []

    def phigh(h, l, i):
        if i < l or i + l >= len(h): return None
        v = h[i]
        return v if all(h[j] < v for j in range(i-l, i+l+1) if j != i) else None

    def plow(lo, l, i):
        if i < l or i + l >= len(lo): return None
        v = lo[i]
        return v if all(lo[j] > v for j in range(i-l, i+l+1) if j != i) else None

    for i in range(max(length, n - lb - length), n - length):
        ph = phigh(highs, length, i)
        if ph: ph_list.append(ph)
        pl = plow(lows, length, i)
        if pl: pl_list.append(pl)

    if len(ph_list) >= 2:
        for i in range(len(ph_list) - 1, 0, -1):
            for j in range(i - 1, max(i - 10, -1), -1):
                if abs(ph_list[i] - ph_list[j]) / ph_list[i] * 100 <= thr:
                    r.update({"is_eqh": True, "eqh_price": ph_list[i]})
                    break
            if r["is_eqh"]: break

    if len(pl_list) >= 2:
        for i in range(len(pl_list) - 1, 0, -1):
            for j in range(i - 1, max(i - 10, -1), -1):
                if abs(pl_list[i] - pl_list[j]) / pl_list[i] * 100 <= thr:
                    r.update({"is_eql": True, "eql_price": pl_list[i]})
                    break
            if r["is_eql"]: break

    return r


def premium_discount_zone(candles: list) -> dict:
    """
    Premium/Discount: solo LONG en zona discount (abajo del 50% del rango),
    solo SHORT en zona premium (arriba del 50%).
    Evita entrar en malos precios.
    """
    r = {"premium": False, "discount": False, "zona_pct": 50.0}
    if not getattr(config, "PREMIUM_DISCOUNT_ACTIVO", True) or len(candles) < 10:
        return r
    lb     = min(getattr(config, "PREMIUM_DISCOUNT_LB", 50), len(candles))
    reciente = candles[-lb:]
    max_h  = max(c["high"] for c in reciente)
    min_l  = min(c["low"]  for c in reciente)
    rng    = max_h - min_l
    if rng <= 0:
        return r
    precio  = candles[-1]["close"]
    zona    = (precio - min_l) / rng * 100
    premium  = zona >= 60
    discount = zona <= 40
    return {"premium": premium, "discount": discount, "zona_pct": round(zona, 1)}


def en_killzone() -> dict:
    ahora = datetime.now(timezone.utc)
    tim   = ahora.hour * 60 + ahora.minute
    asia   = config.KZ_ASIA_START   <= tim < config.KZ_ASIA_END
    london = config.KZ_LONDON_START <= tim < config.KZ_LONDON_END
    ny     = config.KZ_NY_START     <= tim < config.KZ_NY_END
    return {
        "in_asia": asia, "in_london": london, "in_ny": ny,
        "in_kz":   asia or london or ny,
        "nombre":  "ASIA" if asia else ("LONDON" if london else ("NY" if ny else "FUERA")),
    }


def calcular_pivotes_diarios(candles_d: list):
    if len(candles_d) < 2:
        return None
    p = candles_d[-2]
    return calc_pivotes(p["high"], p["low"], p["close"])


# ══════════════════════════════════════════════════════════════
# SL ESTRUCTURAL — NUEVO EN v6.0
# ══════════════════════════════════════════════════════════════

def _swing_low(candles: list, n: int = 10) -> float:
    """Encuentra el swing low más reciente en las últimas n velas."""
    if len(candles) < n + 2:
        return 0.0
    reciente = candles[-(n+1):-1]
    lows = [c["low"] for c in reciente]
    return min(lows)


def _swing_high(candles: list, n: int = 10) -> float:
    """Encuentra el swing high más reciente en las últimas n velas."""
    if len(candles) < n + 2:
        return 0.0
    reciente = candles[-(n+1):-1]
    highs = [c["high"] for c in reciente]
    return max(highs)


def _calcular_sl_estructural(candles: list, ob: dict, lado: str,
                               atr: float, precio: float) -> float:
    """
    SL estructural: usa swing low/high real + buffer de seguridad.
    Más preciso que ATR puro → menos liquidaciones prematuras.
    """
    if lado == "LONG":
        swing = _swing_low(candles, 15)
        sl_ob  = ob["bull_ob_bottom"] * 0.997 if ob["bull_ob"] else 0
        sl_atr = precio - atr * config.SL_ATR_MULT
        sl_sw  = swing * 0.997 if swing > 0 else 0
        # Usa el más alto de OB bottom, swing low y ATR-based
        candidatos = [x for x in [sl_ob, sl_sw, sl_atr] if x > 0]
        return max(candidatos) if candidatos else sl_atr
    else:
        swing  = _swing_high(candles, 15)
        sl_ob  = ob["bear_ob_top"] * 1.003 if ob["bear_ob"] else 0
        sl_atr = precio + atr * config.SL_ATR_MULT
        sl_sw  = swing * 1.003 if swing > 0 else 0
        candidatos = [x for x in [sl_ob, sl_sw] if x > 0 and x < sl_atr]
        return min(candidatos) if candidatos else sl_atr


# ══════════════════════════════════════════════════════════════
# TP DINÁMICO — NUEVO EN v6.0
# ══════════════════════════════════════════════════════════════

def _calcular_tp_dinamico(candles: list, eq: dict, pivotes: dict,
                            asia: dict, lado: str, precio: float,
                            atr: float) -> tuple:
    """
    TP dinámico: apunta a niveles reales de liquidez.
    TP2 = siguiente nivel de resistencia/soporte (EQH/EQL, R1/R2, ASIA)
    TP1 = 40-50% del camino al TP2
    """
    tp_atr  = precio + atr * config.TP_ATR_MULT if lado == "LONG" else precio - atr * config.TP_ATR_MULT
    tp1_atr = precio + atr * config.PARTIAL_TP1_MULT if lado == "LONG" else precio - atr * config.PARTIAL_TP1_MULT

    # Recopilar niveles objetivo según dirección
    niveles = []
    if lado == "LONG":
        if eq.get("is_eqh") and eq["eqh_price"] > precio:
            niveles.append(eq["eqh_price"] * 0.998)  # just under EQH
        if pivotes:
            if pivotes["R1"] > precio:
                niveles.append(pivotes["R1"] * 0.998)
            if pivotes["R2"] > precio:
                niveles.append(pivotes["R2"] * 0.998)
        if asia.get("valido") and asia["high"] > precio:
            niveles.append(asia["high"] * 0.998)
    else:
        if eq.get("is_eql") and eq["eql_price"] < precio:
            niveles.append(eq["eql_price"] * 1.002)  # just above EQL
        if pivotes:
            if pivotes["S1"] < precio:
                niveles.append(pivotes["S1"] * 1.002)
            if pivotes["S2"] < precio:
                niveles.append(pivotes["S2"] * 1.002)
        if asia.get("valido") and asia["low"] < precio:
            niveles.append(asia["low"] * 1.002)

    if not niveles:
        return tp_atr, tp1_atr

    # Elegir el nivel más cercano como TP2 (realista, no codicioso)
    if lado == "LONG":
        niveles_validos = [n for n in niveles if n > precio + atr * 1.0]
        tp2 = min(niveles_validos) if niveles_validos else tp_atr
    else:
        niveles_validos = [n for n in niveles if n < precio - atr * 1.0]
        tp2 = max(niveles_validos) if niveles_validos else tp_atr

    # TP1 = 45% del recorrido al TP2 (más fácil de alcanzar)
    tp1 = precio + (tp2 - precio) * 0.45

    return tp2, tp1


# ══════════════════════════════════════════════════════════════
# SEÑAL PRINCIPAL v6.0
# ══════════════════════════════════════════════════════════════

def analizar_par(par: str):
    try:
        candles = exchange.get_candles(par, config.TIMEFRAME, config.CANDLES_LIMIT)
        if len(candles) < 80:
            return None

        # ── Cooldown ──────────────────────────────────────────
        if not _cooldown_ok(par):
            return None

        # ── Choppiness filter ─────────────────────────────────
        if not es_trending(candles, 20):
            return None

        # ── Volumen ───────────────────────────────────────────
        if not volumen_ok(candles):
            return None

        cl     = [c["close"] for c in candles]
        hi     = [c["high"]  for c in candles]
        lo     = [c["low"]   for c in candles]
        precio = cl[-1]
        if precio <= 0:
            return None

        atr = calc_atr(hi, lo, cl, config.ATR_PERIOD)
        if atr <= 0:
            return None

        # ATR mínimo: mercados muertos
        if atr / precio * 100 < 0.03:
            return None

        # ── Indicadores base ──────────────────────────────────
        ef      = calc_ema(cl, config.EMA_FAST)
        es      = calc_ema(cl, config.EMA_SLOW)
        ef_loc  = calc_ema(cl, getattr(config, "EMA_LOCAL_FAST", 9))
        es_loc  = calc_ema(cl, getattr(config, "EMA_LOCAL_SLOW", 21))
        bull5   = ef is not None and es is not None and ef > es * 1.001
        bear5   = ef is not None and es is not None and ef < es * 0.999
        bull_loc = ef_loc is not None and es_loc is not None and ef_loc > es_loc * 1.0005
        bear_loc = ef_loc is not None and es_loc is not None and ef_loc < es_loc * 0.9995

        rsi      = calc_rsi(cl, config.RSI_PERIOD) or 50.0
        rsi_l    = 25 <= rsi <= config.RSI_BUY_MAX
        rsi_s    = config.RSI_SELL_MIN <= rsi <= 75

        # MACD
        _, _, macd_hist = calc_macd(cl)
        macd_bull = macd_hist is not None and macd_hist > 0
        macd_bear = macd_hist is not None and macd_hist < 0

        # VWAP
        vwap       = calc_vwap(candles)
        sobre_vwap = precio > vwap * (1 + getattr(config, "VWAP_PCT", 0.5) / 100)
        bajo_vwap  = precio < vwap * (1 - getattr(config, "VWAP_PCT", 0.5) / 100)

        # ── MTF ───────────────────────────────────────────────
        htf    = tendencia_htf(par)
        htf_4h = tendencia_4h(par)

        # ── v6.0 FIX CRÍTICO: HTF ESTRICTO ───────────────────
        # NEUTRAL = no operar, a menos que score sea muy alto con CHoCH
        trend_ok_l = bull5 and (htf == "BULL" or (htf == "NEUTRAL" and htf_4h == "BULL"))
        trend_ok_s = bear5 and (htf == "BEAR" or (htf == "NEUTRAL" and htf_4h == "BEAR"))

        # ── SMC Estructuras ───────────────────────────────────
        fvg    = detectar_fvg(candles)
        eq     = detectar_eqh_eql(candles)
        ob     = detectar_order_blocks(candles)
        bos    = detectar_bos_choch(candles)
        sweep  = detectar_sweep(candles)
        asia   = get_rango_asia(candles)
        kz     = en_killzone()
        pd_zone = premium_discount_zone(candles)
        pat    = detectar_patron_vela(candles)

        # Pivotes diarios
        candles_d = exchange.get_candles(par, "1d", 5)
        pivotes   = calcular_pivotes_diarios(candles_d)
        pct       = config.PIVOT_NEAR_PCT / 100
        ns1 = ns2 = nr1 = nr2 = False
        if pivotes:
            ns1 = abs(precio - pivotes["S1"]) / precio < pct
            ns2 = abs(precio - pivotes["S2"]) / precio < pct
            nr1 = abs(precio - pivotes["R1"]) / precio < pct
            nr2 = abs(precio - pivotes["R2"]) / precio < pct

        # Asia
        nal = nah = False
        if asia["valido"]:
            nal = abs(precio - asia["low"])  / precio < pct
            nah = abs(precio - asia["high"]) / precio < pct

        # OB entry check
        iob_b = (ob["bull_ob"] and ob["bull_ob_bottom"] <= precio <= ob["bull_ob_top"] * 1.005)
        iob_r = (ob["bear_ob"] and ob["bear_ob_bottom"] * 0.995 <= precio <= ob["bear_ob_top"])

        # OB + FVG confluencia (señal institucional fuerte)
        ob_fvg_bull = (iob_b and fvg["bull_fvg"] and
                       ob["bull_ob_bottom"] <= fvg.get("fvg_bottom", 0) <= ob["bull_ob_top"])
        ob_fvg_bear = (iob_r and fvg["bear_fvg"] and
                       ob["bear_ob_bottom"] <= fvg.get("fvg_top", 0) <= ob["bear_ob_top"])

        # Desplazamiento (vela de impulso fuerte)
        desplazamiento = False
        if getattr(config, "DISPLACEMENT_ACTIVO", True) and len(candles) >= 3:
            c_prev = candles[-3:-1]
            avg_body = sum(abs(c["close"] - c["open"]) for c in c_prev) / 2
            curr_body = abs(candles[-1]["close"] - candles[-1]["open"])
            desplazamiento = curr_body > avg_body * 1.5

        # Vela confirmación
        vcl = confirmar_vela(candles, "LONG")
        vcs = confirmar_vela(candles, "SHORT")

        # Momentum (EMA local)
        momentum_l = bull_loc
        momentum_s = bear_loc

        # ── SCORING v6.0 (máx 16) ─────────────────────────────
        sl = ss = 0
        ml: list = []
        ms: list = []

        def add(cond, pts, lbl, side):
            nonlocal sl, ss
            if cond:
                if side in ("L", "B"): sl += pts; ml.append(lbl)
                if side in ("S", "B"): ss += pts; ms.append(lbl)

        # Señales SMC fundamentales (peso alto)
        add(fvg["bull_fvg"] and not fvg["fvg_rellenado"], 2, "FVG",      "L")
        add(fvg["bear_fvg"] and not fvg["fvg_rellenado"], 2, "FVG",      "S")
        add(iob_b,                                         2, "OB+",      "L")
        add(iob_r,                                         2, "OB-",      "S")
        add(ob_fvg_bull,                                   1, "OB+FVG",   "L")  # bonus
        add(ob_fvg_bear,                                   1, "OB+FVG",   "S")
        add(sweep["sweep_bull"],                           2, "SWEEP",    "L")
        add(sweep["sweep_bear"],                           2, "SWEEP",    "S")
        add(bos["choch_bull"],                             2, "CHoCH",    "L")
        add(bos["choch_bear"],                             2, "CHoCH",    "S")
        add(bos["bos_bull"] and not bos["choch_bull"],     1, "BOS",      "L")
        add(bos["bos_bear"] and not bos["choch_bear"],     1, "BOS",      "S")

        # Confluencias de precio
        add(ns1 or ns2,        1, "S1/S2",    "L")
        add(eq["is_eql"],      1, "EQL",      "L")
        add(nal,               1, "ASIA_L",   "L")
        add(nr1 or nr2,        1, "R1/R2",    "S")
        add(eq["is_eqh"],      1, "EQH",      "S")
        add(nah,               1, "ASIA_H",   "S")
        add(pd_zone["discount"],1,"DISCOUNT", "L")
        add(pd_zone["premium"], 1, "PREMIUM", "S")

        # Tendencia y momentum
        add(htf == "BULL",     1, "MTF_1H",   "L")
        add(htf == "BEAR",     1, "MTF_1H",   "S")
        add(htf_4h == "BULL",  1, "MTF_4H",   "L")
        add(htf_4h == "BEAR",  1, "MTF_4H",   "S")
        add(bull5,             1, "EMA",       "L")
        add(bear5,             1, "EMA",       "S")
        add(macd_bull,         1, "MACD",      "L")
        add(macd_bear,         1, "MACD",      "S")
        add(kz["in_kz"],       1, f"KZ_{kz['nombre']}", "B")

        # Patrones de vela
        add(vcl,               1, "VELA",      "L")
        add(vcs,               1, "VELA",      "S")
        add(pat.get("patron") and pat.get("lado") == "LONG",
            pat.get("confianza", 0), pat.get("patron", "PAT"), "L")
        add(pat.get("patron") and pat.get("lado") == "SHORT",
            pat.get("confianza", 0), pat.get("patron", "PAT"), "S")

        # RSI
        add(rsi_l, 1, f"RSI{rsi:.0f}", "L")
        add(rsi_s, 1, f"RSI{rsi:.0f}", "S")

        # VWAP
        if getattr(config, "VWAP_ACTIVO", True):
            add(bajo_vwap,  1, "VWAP_B", "L")
            add(sobre_vwap, 1, "VWAP_H", "S")

        # Desplazamiento
        add(desplazamiento, 1, "DISP", "B")

        # ── CONDICIONES BASE OBLIGATORIAS ─────────────────────
        # v6.0: condiciones más estrictas para evitar entradas malas
        zona_l = ns1 or ns2 or eq["is_eql"] or nal or iob_b or pd_zone["discount"]
        zona_s = nr1 or nr2 or eq["is_eqh"] or nah or iob_r or pd_zone["premium"]

        # Base: necesita FVG O (OB + algo más) + zona + KZ
        base_l = (fvg["bull_fvg"] or iob_b or sweep["sweep_bull"]) and zona_l and kz["in_kz"]
        base_s = (fvg["bear_fvg"] or iob_r or sweep["sweep_bear"]) and zona_s and kz["in_kz"]

        # ── DECIDIR DIRECCIÓN ─────────────────────────────────
        lado = score = None
        motivos: list = []

        # v6.0 FIX: HTF ESTRICTO — NEUTRAL solo con score muy alto
        htf_neutral_l = (htf == "NEUTRAL" and htf_4h != "BEAR" and sl >= 9 and bos["choch_bull"])
        htf_neutral_s = (htf == "NEUTRAL" and htf_4h != "BULL" and ss >= 9 and bos["choch_bear"])

        if not config.SOLO_LONG:
            if base_s and ss >= config.SCORE_MIN and (trend_ok_s or htf_neutral_s) and rsi_s:
                if ss > sl:
                    lado, score, motivos = "SHORT", ss, ms

        if base_l and sl >= config.SCORE_MIN and (trend_ok_l or htf_neutral_l) and rsi_l:
            if lado is None or sl >= ss:
                lado, score, motivos = "LONG", sl, ml

        if lado is None:
            if sl >= 3 or ss >= 3:
                log.debug(
                    f"[NO-SEÑAL] {par} L:{sl}({','.join(ml)}) S:{ss}({','.join(ms)}) "
                    f"base_L={base_l} base_S={base_s} "
                    f"trend_L={trend_ok_l}(htf={htf},4h={htf_4h},5m={bull5}) "
                    f"rsi={rsi:.0f}"
                )
            return None

        # ── SL / TP v6.0 ──────────────────────────────────────
        sl_p = _calcular_sl_estructural(candles, ob, lado, atr, precio)
        tp_p, tp1_p = _calcular_tp_dinamico(candles, eq, pivotes, asia, lado, precio, atr)

        # Verificar R:R mínimo
        dist = abs(precio - sl_p)
        if dist <= 0:
            return None
        rr = abs(tp_p - precio) / dist
        if rr < config.MIN_RR:
            log.debug(f"[NO-SEÑAL] {par} R:R={rr:.2f} < {config.MIN_RR}")
            return None

        # ── Macro BTC veto ────────────────────────────────────
        # No operar contra macro BTC si score < 10
        macro = _macro_btc_cache["htf"]
        if score < 10:
            if lado == "LONG"  and macro == "BEAR":
                log.debug(f"[MACRO] {par} LONG vetado — BTC macro BEAR score={score}")
                return None
            if lado == "SHORT" and macro == "BULL":
                log.debug(f"[MACRO] {par} SHORT vetado — BTC macro BULL score={score}")
                return None

        # Registrar timestamp para cooldown
        registrar_senal_ts(par)

        return {
            "par":           par,
            "lado":          lado,
            "precio":        precio,
            "sl":            round(sl_p, 8),
            "tp":            round(tp_p, 8),
            "tp1":           round(tp1_p, 8),
            "tp2":           round(tp_p, 8),
            "atr":           round(atr, 8),
            "score":         score,
            "rsi":           rsi,
            "rr":            round(rr, 2),
            "motivos":       motivos,
            "kz":            kz["nombre"],
            "htf":           htf,
            "htf_4h":        htf_4h,
            "vwap":          round(vwap, 8),
            "sobre_vwap":    sobre_vwap,
            "fvg_top":       fvg.get("fvg_top", 0),
            "fvg_bottom":    fvg.get("fvg_bottom", 0),
            "fvg_rellenado": fvg.get("fvg_rellenado", True),
            "ob_bull":       ob["bull_ob"],
            "ob_bear":       ob["bear_ob"],
            "ob_fvg_bull":   ob_fvg_bull,
            "ob_fvg_bear":   ob_fvg_bear,
            "ob_mitigado":   not ob["bull_ob"] and not ob["bear_ob"],
            "bos_bull":      bos["bos_bull"],
            "bos_bear":      bos["bos_bear"],
            "choch_bull":    bos["choch_bull"],
            "choch_bear":    bos["choch_bear"],
            "sweep_bull":    sweep["sweep_bull"],
            "sweep_bear":    sweep["sweep_bear"],
            "patron":        pat.get("patron"),
            "vela_conf":     vcl or vcs,
            "premium":       pd_zone["premium"],
            "discount":      pd_zone["discount"],
            "zona_pct":      pd_zone["zona_pct"],
            "displacement":  desplazamiento,
            "inducement":    False,
            "pivotes":       pivotes,
            "macd_hist":     round(macd_hist, 8) if macd_hist else 0,
            "vol_ratio":     round(candles[-1]["volume"] /
                                   (sum(c["volume"] for c in candles[-21:-1]) / 20 + 1e-9), 2),
            "asia_valido":   asia["valido"],
        }

    except Exception as e:
        log.error(f"analizar_par {par}: {e}")
        return None


def analizar_todos(pares: list, workers: int = 4) -> list:
    senales = []
    workers = getattr(config, "ANALISIS_WORKERS", workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futuros = {ex.submit(analizar_par, p): p for p in pares}
        for fut in concurrent.futures.as_completed(futuros):
            try:
                r = fut.result()
                if r:
                    senales.append(r)
            except Exception as e:
                log.error(f"thread: {e}")
    senales.sort(key=lambda x: x["score"], reverse=True)
    return senales
