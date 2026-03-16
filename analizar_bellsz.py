"""
analizar_bellsz.py — FVG + Zero Lag Liquidity + Supertrend
===========================================================
Basado en los indicadores TradingView que SÍ dan señales.

SEÑAL LONG:
  1. FVG ALCISTA activo (gap entre velas: low > high[2])
     + precio toca el CE (midpoint) y cierra por encima  ← AlphaX FVG Tracker
  2. Wick inferior grande con volumen alto               ← Zero Lag Liq
     OR Supertrend alcista                               ← SMC Sniper
  3. Volumen actual ≥ media × RVOL_MIN

SEÑAL SHORT:
  1. FVG BAJISTA activo (gap: high < low[2])
     + precio toca el CE y cierra por debajo
  2. Wick superior grande con volumen alto OR ST bajista
  3. Volumen ≥ media × RVOL_MIN

FVG = Fair Value Gap (desequilibrio entre 3 velas).
CE  = Consequent Encroachment = midpoint del FVG.
"""

import logging
import os
import time
from datetime import datetime, timezone
import concurrent.futures

import config
import exchange

log = logging.getLogger("analizar")

_cooldown_ts:   dict = {}
_niveles_cache: dict = {}
_NIVELES_TTL          = 600


# ══════════════════════════════════════════════════════
# INDICADORES BASE
# ══════════════════════════════════════════════════════

def _ema(prices, period):
    if len(prices) < period:
        return None
    k   = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def _atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return 0.0
    trs = [max(highs[i]-lows[i],
               abs(highs[i]-closes[i-1]),
               abs(lows[i]-closes[i-1]))
           for i in range(1, len(highs))]
    return sum(trs[-period:]) / period


def _rsi_vol(volumes, period=14):
    """RSI del volumen — replica vol=ta.rsi(volume,14) del Zero Lag Liq"""
    if len(volumes) < period + 1:
        return 50.0
    d  = [volumes[i] - volumes[i-1] for i in range(1, len(volumes))]
    ag = sum(max(x, 0)      for x in d[:period]) / period
    al = sum(abs(min(x, 0)) for x in d[:period]) / period
    for x in d[period:]:
        ag = (ag * (period - 1) + max(x, 0))      / period
        al = (al * (period - 1) + abs(min(x, 0))) / period
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 2)


# ══════════════════════════════════════════════════════
# CONDICIÓN 1 — FVG  [AlphaX FVG Tracker]
#
# FVG alcista: low[0] > high[2]  → gap entre vela 0 y vela 2
# FVG bajista: high[0] < low[2]  → gap entre vela 0 y vela 2
# Señal:  precio toca CE (midpoint) y cierra al lado correcto
# ══════════════════════════════════════════════════════

def detectar_fvg(candles: list) -> dict:
    """
    Escanea las últimas N velas buscando FVGs activos.
    Un FVG está activo si el precio no lo ha llenado completamente.
    Señal de entrada cuando el precio retoca el CE y cierra confirmado.

    Replica: entryMode="CE Touch" + confirmCandle=true del AlphaX FVG Tracker
    """
    max_fvgs  = int(os.getenv("FVG_LOOKBACK", "20"))   # velas atrás a escanear
    min_size  = float(os.getenv("FVG_MIN_SIZE", "0"))  # tamaño mínimo en precio

    resultado = {
        "bull_entry": False, "bear_entry": False,
        "bull_fvg":   False, "bear_fvg":   False,
        "ce_bull": 0.0, "ce_bear": 0.0,
        "fvg_top": 0.0, "fvg_bot": 0.0,
    }

    if len(candles) < 5:
        return resultado

    c_cur  = candles[-1]   # vela actual (confirmación)
    c_prev = candles[-2]   # vela anterior (interacción)

    # Escanear FVGs recientes
    lb = min(max_fvgs, len(candles) - 3)
    for i in range(lb):
        idx = len(candles) - 1 - i   # índice de la vela más reciente del triplete

        if idx < 2:
            break

        c0 = candles[idx]       # vela actual del triplete (más reciente)
        c2 = candles[idx - 2]   # vela de hace 2

        # ── FVG ALCISTA: low[0] > high[2] ──────────────────
        if c0["low"] > c2["high"]:
            fvg_bot = c2["high"]
            fvg_top = c0["low"]
            size    = fvg_top - fvg_bot
            if size < min_size:
                continue

            ce = (fvg_top + fvg_bot) / 2

            # Verificar que el FVG no esté llenado
            # (precio actual no ha cerrado por debajo de fvg_bot)
            filled = any(candles[j]["close"] < fvg_bot
                         for j in range(idx + 1, len(candles)))
            if filled:
                continue

            resultado["bull_fvg"] = True
            resultado["ce_bull"]  = ce
            resultado["fvg_bot"]  = fvg_bot
            resultado["fvg_top"]  = fvg_top

            # Señal de entrada: CE touch + cierre confirmado sobre CE
            # Replica: low <= mid AND close > mid AND close > open
            if c_prev["low"] <= ce and c_cur["close"] > ce and c_cur["close"] > c_cur["open"]:
                resultado["bull_entry"] = True
                break

        # ── FVG BAJISTA: high[0] < low[2] ──────────────────
        if c0["high"] < c2["low"]:
            fvg_bot = c0["high"]
            fvg_top = c2["low"]
            size    = fvg_top - fvg_bot
            if size < min_size:
                continue

            ce = (fvg_top + fvg_bot) / 2

            # Verificar que no esté llenado
            filled = any(candles[j]["close"] > fvg_top
                         for j in range(idx + 1, len(candles)))
            if filled:
                continue

            resultado["bear_fvg"] = True
            resultado["ce_bear"]  = ce
            resultado["fvg_top"]  = fvg_top
            resultado["fvg_bot"]  = fvg_bot

            # Señal: high >= CE + cierre confirmado bajo CE
            if c_prev["high"] >= ce and c_cur["close"] < ce and c_cur["close"] < c_cur["open"]:
                resultado["bear_entry"] = True
                break

    return resultado


# ══════════════════════════════════════════════════════
# CONDICIÓN 2A — ZERO LAG LIQUIDITY [AlgoAlpha]
# Wick grande con RSI volumen > 60
# ══════════════════════════════════════════════════════

def wick_liquidity(candles: list) -> dict:
    """
    Replica highlight del Zero Lag Liq:
    vol_rsi > 60  AND  avg_wick > sma(avg_wick,21) * wickMult
    lower_wick > upper_wick → contexto alcista (rebote desde abajo)
    upper_wick > lower_wick → contexto bajista (rechazo desde arriba)
    """
    wick_mult = float(os.getenv("WICK_MULT", "1.5"))

    lower_wicks = [min(c["close"], c["open"]) - c["low"]  for c in candles]
    upper_wicks = [c["high"] - max(c["close"], c["open"]) for c in candles]
    avg_wicks   = [(l + u) / 2 for l, u in zip(lower_wicks, upper_wicks)]
    volumes     = [c["volume"] for c in candles]

    if len(candles) < 25:
        return {"bull": False, "bear": False}

    a_w     = _sma(avg_wicks[-22:-1], 21)
    vol_rsi = _rsi_vol(volumes[-20:])

    if not a_w or a_w <= 0:
        return {"bull": False, "bear": False}

    lw = lower_wicks[-1]
    uw = upper_wicks[-1]
    aw = avg_wicks[-1]

    highlight = vol_rsi > 60 and aw > a_w * wick_mult

    return {
        "bull": highlight and lw > uw,   # wick inferior = zona de compra
        "bear": highlight and uw > lw,   # wick superior = zona de venta
        "vol_rsi": vol_rsi,
    }


# ══════════════════════════════════════════════════════
# CONDICIÓN 2B — SUPERTREND  [SMC Sniper v6]
# factor=2.5, period=10
# ══════════════════════════════════════════════════════

def supertrend(highs, lows, closes, factor=2.5, period=10):
    if len(closes) < period + 2:
        return False, False

    atrs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i],
                 abs(highs[i]-closes[i-1]),
                 abs(lows[i]-closes[i-1]))
        atrs.append(tr)

    av = sum(atrs[:period]) / period
    atr_s = [av]
    for a in atrs[period:]:
        av = (av * (period - 1) + a) / period
        atr_s.append(av)

    n      = len(atr_s)
    offset = len(closes) - n
    ub     = [0.0] * n
    lb     = [0.0] * n
    dr     = [1]   * n
    st     = [0.0] * n

    for i in range(n):
        ci  = i + offset
        hl2 = (highs[ci] + lows[ci]) / 2
        u   = hl2 + factor * atr_s[i]
        l   = hl2 - factor * atr_s[i]
        ub[i] = min(u, ub[i-1]) if i > 0 and closes[ci-1] < ub[i-1] else u
        lb[i] = max(l, lb[i-1]) if i > 0 and closes[ci-1] > lb[i-1] else l
        if i == 0:
            dr[i] = 1
        elif st[i-1] == ub[i-1]:
            dr[i] = 1 if closes[ci] < ub[i] else -1
        else:
            dr[i] = -1 if closes[ci] > lb[i] else 1
        st[i] = ub[i] if dr[i] == 1 else lb[i]

    return dr[-1] < 0, dr[-1] > 0   # (st_bull, st_bear)


# ══════════════════════════════════════════════════════
# CONDICIÓN 3 — VOLUMEN
# ══════════════════════════════════════════════════════

def check_vol(candles: list) -> dict:
    rvol_min = float(os.getenv("RVOL_MIN", "0.8"))
    vols     = [c["volume"] for c in candles]
    if len(vols) < 22:
        return {"ok": True, "ratio": 1.0}
    avg   = _sma(vols[-21:-1], 20) or 1
    ratio = vols[-1] / avg
    return {"ok": ratio >= rvol_min, "ratio": round(ratio, 2)}


# ══════════════════════════════════════════════════════
# KILL ZONES y COOLDOWN
# ══════════════════════════════════════════════════════

def en_killzone() -> dict:
    m    = datetime.now(timezone.utc)
    mins = m.hour * 60 + m.minute
    asia   = config.KZ_ASIA_START   <= mins < config.KZ_ASIA_END
    london = config.KZ_LONDON_START <= mins < config.KZ_LONDON_END
    ny     = config.KZ_NY_START     <= mins < config.KZ_NY_END
    return {
        "in_kz":  asia or london or ny,
        "nombre": "ASIA" if asia else ("LONDON" if london else ("NY" if ny else "FUERA")),
    }


def _cooldown_ok(par: str) -> bool:
    return (time.time() - _cooldown_ts.get(par, 0)) >= config.COOLDOWN_VELAS * 300


def registrar_senal_ts(par: str):
    _cooldown_ts[par] = time.time()


def registrar_trade_kz(kz: str, ganado: bool):
    pass


def actualizar_macro_btc():
    pass


def invalidar_niveles(par):
    _niveles_cache.pop(par, None)


# ══════════════════════════════════════════════════════
# SL Y TP
# ══════════════════════════════════════════════════════

def _calcular_sl(candles, lado, atr, precio, fvg_bot=0, fvg_top=0):
    """
    SL inteligente:
    - LONG:  justo bajo el fvg_bot (o swing low si es mejor)
    - SHORT: justo sobre el fvg_top (o swing high si es mejor)
    """
    rec = candles[-16:-1]
    buf = atr * 0.2

    if lado == "LONG":
        sl_fvg  = fvg_bot - buf if fvg_bot > 0 else 0
        sl_sw   = min(c["low"] for c in rec) - buf if rec else 0
        sl_atr  = precio - atr * config.SL_ATR_MULT
        opciones = [x for x in [sl_fvg, sl_sw] if 0 < x < precio]
        sl = max(opciones) if opciones else sl_atr
        if precio - sl > 3 * atr:
            sl = sl_atr
    else:
        sl_fvg  = fvg_top + buf if fvg_top > 0 else 0
        sl_sw   = max(c["high"] for c in rec) + buf if rec else 0
        sl_atr  = precio + atr * config.SL_ATR_MULT
        opciones = [x for x in [sl_fvg, sl_sw] if x > precio]
        sl = min(opciones) if opciones else sl_atr
        if sl - precio > 3 * atr:
            sl = sl_atr

    return sl


# ══════════════════════════════════════════════════════
# ANÁLISIS PRINCIPAL
# ══════════════════════════════════════════════════════

def analizar_par(par: str):
    try:
        if not _cooldown_ok(par):
            return None

        candles = exchange.get_candles(par, config.TIMEFRAME, config.CANDLES_LIMIT)
        if len(candles) < 50:
            return None

        cl     = [c["close"] for c in candles]
        hi     = [c["high"]  for c in candles]
        lo     = [c["low"]   for c in candles]
        precio = cl[-1]
        if precio <= 0:
            return None

        atr = _atr(hi, lo, cl)
        if atr <= 0:
            return None

        # ── CONDICIÓN 1: FVG con entrada CE ─────────────────
        fvg = detectar_fvg(candles)

        if not fvg["bull_entry"] and not fvg["bear_entry"]:
            return None

        lado = "LONG" if fvg["bull_entry"] else "SHORT"

        if lado == "SHORT" and config.SOLO_LONG:
            return None

        # ── CONDICIÓN 2: Wick Liq OR Supertrend ─────────────
        wl       = wick_liquidity(candles)
        st_bull, st_bear = supertrend(hi, lo, cl)

        conf2_ok = False
        conf2_str = ""

        if lado == "LONG":
            if wl["bull"]:
                conf2_ok  = True
                conf2_str = f"WICK_LIQ(rsi_vol={wl['vol_rsi']:.0f})"
            elif st_bull:
                conf2_ok  = True
                conf2_str = "ST_BULL"
        else:
            if wl["bear"]:
                conf2_ok  = True
                conf2_str = f"WICK_LIQ(rsi_vol={wl['vol_rsi']:.0f})"
            elif st_bear:
                conf2_ok  = True
                conf2_str = "ST_BEAR"

        if not conf2_ok:
            log.info(
                f"[SKIP-C2] {par} {lado} FVG ok pero sin "
                f"wick_liq({wl['bull']}/{wl['bear']}) ni ST({st_bull}/{st_bear})"
            )
            return None

        # ── CONDICIÓN 3: VOLUMEN ─────────────────────────────
        vol = check_vol(candles)
        if not vol["ok"]:
            log.info(f"[SKIP-VOL] {par} {lado} RVOL={vol['ratio']:.2f}")
            return None

        # ── SL / TP ──────────────────────────────────────────
        sl_p = _calcular_sl(
            candles, lado, atr, precio,
            fvg_bot=fvg["fvg_bot"], fvg_top=fvg["fvg_top"]
        )
        dist = abs(precio - sl_p)
        if dist <= 0:
            return None

        tp_p  = (precio + dist * config.TP_DIST_MULT)  if lado == "LONG" else (precio - dist * config.TP_DIST_MULT)
        tp1_p = (precio + dist * config.TP1_DIST_MULT) if lado == "LONG" else (precio - dist * config.TP1_DIST_MULT)
        rr    = abs(tp_p - precio) / dist

        if rr < config.MIN_RR:
            return None

        kz    = en_killzone()
        score = 3   # base: las 3 condiciones
        if vol["ratio"] >= 2.0: score += 1
        if wl["bull"] or wl["bear"]: score += 1   # wick_liq es más fuerte que solo ST
        if kz["in_kz"]: score += 1

        ce_nivel = fvg["ce_bull"] if lado == "LONG" else fvg["ce_bear"]
        motivos  = [
            f"FVG_CE({'BULL' if lado=='LONG' else 'BEAR'})",
            conf2_str,
            f"RVOL×{vol['ratio']:.1f}",
        ]
        if kz["in_kz"]:
            motivos.append(f"KZ_{kz['nombre']}")

        registrar_senal_ts(par)

        log.info(
            f"[SEÑAL] {lado:5s} {par:15s} FVG_CE={ce_nivel:.6f} "
            f"{conf2_str} RVOL×{vol['ratio']:.1f} "
            f"score={score} SL={sl_p:.6f} TP={tp_p:.6f} RR={rr:.2f}"
        )

        return {
            "par": par, "lado": lado, "precio": precio,
            "sl":  round(sl_p,  8),
            "tp":  round(tp_p,  8),
            "tp1": round(tp1_p, 8),
            "tp2": round(tp_p,  8),
            "atr": round(atr,   8),
            "dist_sl": round(dist, 8),
            "score":   score,
            "rsi":     50.0,
            "rr":      round(rr, 2),
            "motivos": motivos,
            "kz":      kz["nombre"],
            "htf": "NEUTRAL", "htf_4h": "NEUTRAL",
            "purga_nivel": f"FVG_{'BULL' if lado=='LONG' else 'BEAR'}",
            "purga_peso":  score,
            "vol_ratio":   vol["ratio"],
            "bsl_h1": 0.0, "ssl_h1": 0.0,
            "bsl_h4": 0.0, "ssl_h4": 0.0,
            "bsl_d":  0.0, "ssl_d":  0.0,
            "ema_r": 0.0, "ema_l": 0.0,
            "vwap": 0.0, "sobre_vwap": False,
            "fvg_top": fvg["fvg_top"], "fvg_bottom": fvg["fvg_bot"],
            "fvg_rellenado": False,
            "ob_bull": False, "ob_bear": False,
            "ob_fvg_bull": lado == "LONG", "ob_fvg_bear": lado == "SHORT",
            "ob_mitigado": False,
            "bos_bull": False, "bos_bear": False,
            "choch_bull": False, "choch_bear": False,
            "sweep_bull": wl["bull"], "sweep_bear": wl["bear"],
            "patron": None, "vela_conf": True,
            "premium": False, "discount": False,
            "displacement": False, "macd_hist": 0,
            "asia_valido": True, "adx": 25.0, "inducement": False,
            "liq_bull": wl["bull"], "liq_bear": wl["bear"],
            "liq_z_up": 0.0, "liq_z_dn": 0.0, "liq_plot_trnd": 0,
        }

    except Exception as e:
        log.error(f"analizar_par {par}: {e}")
        return None


def analizar_todos(pares: list, workers: int = 6) -> list:
    senales = []
    w = min(workers, len(pares), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=w) as ex:
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
