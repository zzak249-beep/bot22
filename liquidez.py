"""
liquidez.py — Motor de Liquidez v2.0 [Bellsz / hyper-scanner]
═══════════════════════════════════════════════════════════════
Basado en los mejores indicadores de TradingView:
  • ICT Algo: Sweep + MSS + FVG (DivergentTrades)
  • Liquidity Raids [UAlgo] — RVOL confirmation
  • ICT Session Zones & Sweep Signals [WillyAlgoTrader] — ATR depth scoring
  • ICT Liquidity Pools SSL BSL (DropkingICT) — MSS post-sweep required
  • HTF Sweeps & PO3 — PDH/PDL/PWH/PWL/PMH/PML

Técnicas implementadas:
  1. BSL/SSL multi-pivot — niveles de swing reales (no rolling max simple)
  2. RVOL — volumen relativo exige spike en el sweep (institucional)
  3. ATR-depth scoring — profundidad del sweep normalizada por ATR
  4. MSS post-sweep — Market Structure Shift confirma la reversión
  5. MTF levels — PDH/PDL (diario), PWH/PWL (semanal) como zonas clave
  6. FVG post-sweep — imbalance tras el sweep = entry institucional
  7. Premium/Discount — solo LONG en discount (<50% rango), SHORT en premium
  8. Sweep strength score — 1-10 puntos según calidad del evento
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 1. BSL / SSL — MULTI-PIVOT (mejor que rolling max/min)
# ══════════════════════════════════════════════════════════════

def _pivot_highs(highs: list, length: int = 5) -> list:
    """Swing highs confirmados: máximo local en ventana 2*length+1."""
    result = []
    n = len(highs)
    for i in range(length, n - length):
        if highs[i] == max(highs[i - length:i + length + 1]):
            result.append((i, highs[i]))
    return result


def _pivot_lows(lows: list, length: int = 5) -> list:
    """Swing lows confirmados: mínimo local en ventana 2*length+1."""
    result = []
    n = len(lows)
    for i in range(length, n - length):
        if lows[i] == min(lows[i - length:i + length + 1]):
            result.append((i, lows[i]))
    return result


def get_bsl_ssl_levels(candles: list, pivot_length: int = 5, max_levels: int = 5) -> dict:
    """
    Retorna los niveles BSL y SSL más recientes basados en pivots reales.
    BSL = Buy Side Liquidity = por ENCIMA de swing highs (stops de shorts)
    SSL = Sell Side Liquidity = por DEBAJO de swing lows (stops de longs)
    """
    if len(candles) < pivot_length * 2 + 5:
        return {"bsl_levels": [], "ssl_levels": [], "nearest_bsl": None, "nearest_ssl": None}

    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    precio = candles[-1]["close"]

    ph = _pivot_highs(highs, pivot_length)
    pl = _pivot_lows(lows,   pivot_length)

    # BSL: swing highs POR ENCIMA del precio actual (no sweepados aún)
    bsl = sorted(
        [(i, v) for i, v in ph if v > precio * 1.001],  # mínimo 0.1% encima
        key=lambda x: x[1]  # ordenar por precio
    )[-max_levels:]  # los más cercanos hacia arriba

    # SSL: swing lows POR DEBAJO del precio actual
    ssl = sorted(
        [(i, v) for i, v in pl if v < precio * 0.999],
        key=lambda x: x[1], reverse=True
    )[-max_levels:]  # los más cercanos hacia abajo

    return {
        "bsl_levels":  bsl,                              # lista de (idx, precio)
        "ssl_levels":  ssl,
        "nearest_bsl": bsl[0][1]  if bsl else None,     # el más cercano encima
        "nearest_ssl": ssl[0][1]  if ssl else None,      # el más cercano abajo
        "dist_bsl_pct": abs(bsl[0][1] - precio) / precio * 100 if bsl else 999.0,
        "dist_ssl_pct": abs(ssl[0][1] - precio) / precio * 100 if ssl else 999.0,
    }


# ══════════════════════════════════════════════════════════════
# 2. RVOL — Relative Volume (volumen institucional)
# ══════════════════════════════════════════════════════════════

def calc_rvol(candles: list, period: int = 20) -> float:
    """
    RVOL = volumen_actual / media_volumen_periodo
    >1.5 = volumen significativo (posible institucional)
    >2.0 = volumen alto (confirmación sweep fuerte)
    >3.0 = volumen extremo (evento institucional)
    """
    if len(candles) < period + 1:
        return 1.0
    vols = [c["volume"] for c in candles[-(period + 1):-1]]
    avg  = sum(vols) / len(vols) if vols else 0
    if avg <= 0:
        return 1.0
    return round(candles[-1]["volume"] / avg, 2)


# ══════════════════════════════════════════════════════════════
# 3. ATR-NORMALIZED SWEEP DEPTH
# ══════════════════════════════════════════════════════════════

def _calc_atr_simple(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period if len(trs) >= period else sum(trs) / max(len(trs), 1)


def sweep_depth_atr(wick_size: float, atr: float) -> float:
    """
    Profundidad del sweep en múltiplos de ATR.
    >0.3 ATR = válido | >0.5 ATR = fuerte | >1.0 ATR = muy fuerte
    """
    if atr <= 0:
        return 0.0
    return round(wick_size / atr, 2)


# ══════════════════════════════════════════════════════════════
# 4. SWEEP DETECTION — mejorado con RVOL + ATR depth
# ══════════════════════════════════════════════════════════════

def detectar_sweep_v2(candles: list, pivot_length: int = 5,
                      rvol_min: float = 1.2, atr_depth_min: float = 0.25) -> dict:
    """
    Sweep de alta calidad basado en TradingView best practices:
    - SSL sweep: low < swing_low Y close > swing_low (rechazo confirmado)
    - BSL sweep: high > swing_high Y close < swing_high (rechazo confirmado)
    - RVOL >= rvol_min (volumen institucional)
    - Profundidad mecha >= atr_depth_min * ATR
    - Opcional: MSS en las últimas N velas
    """
    result = {
        "sweep_bull": False, "sweep_bear": False,
        "sweep_bull_score": 0, "sweep_bear_score": 0,
        "swept_ssl": None, "swept_bsl": None,
        "rvol": 1.0, "atr_depth_bull": 0.0, "atr_depth_bear": 0.0,
        "mss_bull": False, "mss_bear": False,
    }

    if len(candles) < pivot_length * 2 + 10:
        return result

    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    c      = candles[-1]
    precio = c["close"]
    rng    = max(c["high"] - c["low"], 1e-10)
    atr    = _calc_atr_simple(candles, 14)
    rvol   = calc_rvol(candles, 20)

    result["rvol"] = rvol

    # Pivots históricos (excluyendo la vela actual)
    ph = _pivot_highs(highs[:-1], pivot_length)
    pl = _pivot_lows(lows[:-1],   pivot_length)

    # ── SSL SWEEP (bullish) ──
    # Precio baja por debajo de un swing low Y cierra por encima
    if pl:
        # El swing low más cercano debajo del actual low
        ssl_candidates = [(i, v) for i, v in pl if v >= c["low"] * 0.998 and v <= c["low"] * 1.005]
        if not ssl_candidates:
            # Cualquier swing low que fue penetrado por el low actual
            ssl_candidates = [(i, v) for i, v in pl if c["low"] < v and c["close"] > v]

        for idx, ssl_level in ssl_candidates[:3]:
            if c["low"] < ssl_level and c["close"] > ssl_level:
                wick_size = ssl_level - c["low"]
                depth     = sweep_depth_atr(wick_size, atr)
                wick_ratio = (c["close"] - c["low"]) / rng

                if depth >= atr_depth_min and wick_ratio >= 0.45:
                    score = 0
                    score += 1  # base: sweep confirmado
                    if rvol >= rvol_min:      score += 1
                    if rvol >= 2.0:           score += 1
                    if depth >= 0.5:          score += 1
                    if depth >= 1.0:          score += 1
                    if wick_ratio >= 0.65:    score += 1

                    result["sweep_bull"]       = True
                    result["swept_ssl"]        = ssl_level
                    result["atr_depth_bull"]   = depth
                    result["sweep_bull_score"] = score
                    break

    # ── BSL SWEEP (bearish) ──
    if ph:
        bsl_candidates = [(i, v) for i, v in ph if v >= c["high"] * 0.995 and v <= c["high"] * 1.002]
        if not bsl_candidates:
            bsl_candidates = [(i, v) for i, v in ph if c["high"] > v and c["close"] < v]

        for idx, bsl_level in bsl_candidates[:3]:
            if c["high"] > bsl_level and c["close"] < bsl_level:
                wick_size  = c["high"] - bsl_level
                depth      = sweep_depth_atr(wick_size, atr)
                wick_ratio = (c["high"] - c["close"]) / rng

                if depth >= atr_depth_min and wick_ratio >= 0.45:
                    score = 0
                    score += 1
                    if rvol >= rvol_min:      score += 1
                    if rvol >= 2.0:           score += 1
                    if depth >= 0.5:          score += 1
                    if depth >= 1.0:          score += 1
                    if wick_ratio >= 0.65:    score += 1

                    result["sweep_bear"]       = True
                    result["swept_bsl"]        = bsl_level
                    result["atr_depth_bear"]   = depth
                    result["sweep_bear_score"] = score
                    break

    # ── MSS — Market Structure Shift (últimas 3 velas post-sweep) ──
    # Confirma que el sweep produjo un cambio de estructura
    if len(candles) >= 5:
        recent = candles[-4:-1]  # 3 velas antes de la actual
        if result["sweep_bull"]:
            # MSS bull: alguna vela reciente cerró por encima de un swing high local
            local_highs = [x["high"] for x in candles[-10:-4]]
            if local_highs and any(x["close"] > max(local_highs) * 0.998 for x in recent):
                result["mss_bull"] = True
        if result["sweep_bear"]:
            local_lows = [x["low"] for x in candles[-10:-4]]
            if local_lows and any(x["close"] < min(local_lows) * 1.002 for x in recent):
                result["mss_bear"] = True

    return result


# ══════════════════════════════════════════════════════════════
# 5. MTF LEVELS — PDH/PDL, PWH/PWL (targets de liquidez clave)
# ══════════════════════════════════════════════════════════════

def get_htf_levels(candles_1h: list, candles_4h: list, candles_1d: list) -> dict:
    """
    Niveles HTF donde se acumula liquidez institucional:
    - PDH/PDL: Previous Day High/Low
    - PWH/PWL: Previous Week High/Low (aproximado con 5d)
    - H4 high/low reciente
    """
    res = {
        "pdh": None, "pdl": None,
        "pwh": None, "pwl": None,
        "h4h": None, "h4l": None,
        "near_pdh": False, "near_pdl": False,
        "near_pwh": False, "near_pwl": False,
        "above_pdh": False, "below_pdl": False,
    }
    precio = candles_1d[-1]["close"] if candles_1d else 0
    if precio <= 0:
        return res

    # PDH/PDL — vela diaria anterior
    if len(candles_1d) >= 2:
        prev_d = candles_1d[-2]
        res["pdh"] = prev_d["high"]
        res["pdl"] = prev_d["low"]
        pct = 0.005  # 0.5%
        res["near_pdh"]   = abs(precio - res["pdh"]) / precio < pct
        res["near_pdl"]   = abs(precio - res["pdl"]) / precio < pct
        res["above_pdh"]  = precio > res["pdh"] * 1.001  # rompió PDH
        res["below_pdl"]  = precio < res["pdl"] * 0.999  # rompió PDL

    # PWH/PWL — últimas 5 velas diarias = semana aprox
    if len(candles_1d) >= 6:
        week = candles_1d[-6:-1]
        res["pwh"] = max(c["high"] for c in week)
        res["pwl"] = min(c["low"]  for c in week)
        pct = 0.008
        if precio > 0:
            res["near_pwh"] = abs(precio - res["pwh"]) / precio < pct
            res["near_pwl"] = abs(precio - res["pwl"]) / precio < pct

    # H4 reciente — últimas 3 velas de 4h
    if len(candles_4h) >= 4:
        h4_recent = candles_4h[-4:-1]
        res["h4h"] = max(c["high"] for c in h4_recent)
        res["h4l"] = min(c["low"]  for c in h4_recent)

    return res


# ══════════════════════════════════════════════════════════════
# 6. FVG POST-SWEEP (imbalance institucional)
# ══════════════════════════════════════════════════════════════

def detectar_fvg_post_sweep(candles: list, lookback: int = 5) -> dict:
    """
    FVG formado en las últimas N velas — indica imbalance tras el sweep.
    Bullish FVG: low[i] > high[i-2] → gap alcista
    Bearish FVG: high[i] < low[i-2] → gap bajista
    """
    res = {"bull_fvg": False, "bear_fvg": False,
           "bull_fvg_top": 0, "bull_fvg_bot": 0,
           "bear_fvg_top": 0, "bear_fvg_bot": 0}

    if len(candles) < lookback + 3:
        return res

    recent = candles[-(lookback + 2):]
    for i in range(2, len(recent)):
        # Bullish FVG: gap entre vela i-2 high y vela i low
        if recent[i]["low"] > recent[i-2]["high"]:
            res["bull_fvg"]     = True
            res["bull_fvg_top"] = recent[i]["low"]
            res["bull_fvg_bot"] = recent[i-2]["high"]
        # Bearish FVG: gap entre vela i-2 low y vela i high
        if recent[i]["high"] < recent[i-2]["low"]:
            res["bear_fvg"]     = True
            res["bear_fvg_top"] = recent[i-2]["low"]
            res["bear_fvg_bot"] = recent[i]["high"]

    return res


# ══════════════════════════════════════════════════════════════
# 7. PREMIUM / DISCOUNT (solo operar en zona correcta)
# ══════════════════════════════════════════════════════════════

def get_premium_discount(candles: list, lookback: int = 50) -> dict:
    """
    Equilibrio = 50% del rango (lookback).
    Discount (<50%) = zona de COMPRA (LONG).
    Premium (>50%) = zona de VENTA (SHORT).
    """
    if len(candles) < lookback:
        return {"equilibrio": 0, "en_discount": True, "en_premium": False, "pct_rango": 50.0}

    recent = candles[-lookback:]
    high   = max(c["high"] for c in recent)
    low    = min(c["low"]  for c in recent)
    rango  = high - low
    precio = candles[-1]["close"]

    if rango <= 0:
        return {"equilibrio": precio, "en_discount": True, "en_premium": False, "pct_rango": 50.0}

    pct_rango  = (precio - low) / rango * 100
    equilibrio = low + rango * 0.5

    return {
        "equilibrio":  round(equilibrio, 8),
        "pct_rango":   round(pct_rango, 1),
        "en_discount": pct_rango <= 50.0,  # bueno para LONG
        "en_premium":  pct_rango >= 50.0,  # bueno para SHORT
        "en_deep_discount": pct_rango <= 30.0,  # óptimo para LONG
        "en_deep_premium":  pct_rango >= 70.0,  # óptimo para SHORT
    }


# ══════════════════════════════════════════════════════════════
# 8. SWEEP STRENGTH SCORE TOTAL (0-10)
#    Combina todos los factores en un score unificado
# ══════════════════════════════════════════════════════════════

def calcular_score_liquidez(
    sweep:      dict,
    liq_levels: dict,
    htf:        dict,
    fvg:        dict,
    pd_zone:    dict,
    lado:       str,   # "LONG" o "SHORT"
    rvol:       float,
    mss:        bool,
) -> tuple[int, list]:
    """
    Score de calidad del setup de liquidez (0-12).
    Retorna (score, motivos).
    """
    score   = 0
    motivos = []

    if lado == "LONG":
        if sweep.get("sweep_bull"):
            score += 2; motivos.append("SSL_SWEEP")
            # Bonus por profundidad ATR
            d = sweep.get("atr_depth_bull", 0)
            if d >= 0.5:  score += 1; motivos.append(f"DEPTH{d:.1f}ATR")
            if d >= 1.0:  score += 1; motivos.append("DEEP_SWEEP")

        # RVOL
        if rvol >= 1.5:  score += 1; motivos.append(f"RVOL{rvol:.1f}x")
        if rvol >= 2.5:  score += 1; motivos.append("RVOL_HIGH")

        # MSS post-sweep
        if mss or sweep.get("mss_bull"):
            score += 2; motivos.append("MSS")

        # FVG post-sweep
        if fvg.get("bull_fvg"):
            score += 1; motivos.append("FVG_BULL")

        # HTF levels sweepados o cerca
        if htf.get("near_pdl") or htf.get("below_pdl"):
            score += 1; motivos.append("PDL")
        if htf.get("near_pwl"):
            score += 1; motivos.append("PWL")

        # Premium/Discount
        if pd_zone.get("en_deep_discount"):
            score += 1; motivos.append("DEEP_DISC")
        elif pd_zone.get("en_discount"):
            score += 0  # neutral

    else:  # SHORT
        if sweep.get("sweep_bear"):
            score += 2; motivos.append("BSL_SWEEP")
            d = sweep.get("atr_depth_bear", 0)
            if d >= 0.5:  score += 1; motivos.append(f"DEPTH{d:.1f}ATR")
            if d >= 1.0:  score += 1; motivos.append("DEEP_SWEEP")

        if rvol >= 1.5:  score += 1; motivos.append(f"RVOL{rvol:.1f}x")
        if rvol >= 2.5:  score += 1; motivos.append("RVOL_HIGH")

        if mss or sweep.get("mss_bear"):
            score += 2; motivos.append("MSS")

        if fvg.get("bear_fvg"):
            score += 1; motivos.append("FVG_BEAR")

        if htf.get("near_pdh") or htf.get("above_pdh"):
            score += 1; motivos.append("PDH")
        if htf.get("near_pwh"):
            score += 1; motivos.append("PWH")

        if pd_zone.get("en_deep_premium"):
            score += 1; motivos.append("DEEP_PREM")

    return score, motivos


# ══════════════════════════════════════════════════════════════
# 9. ANÁLISIS COMPLETO DE LIQUIDEZ PARA UN PAR
# ══════════════════════════════════════════════════════════════

def analizar_liquidez(par: str, candles_5m: list,
                      candles_1h: list, candles_4h: list, candles_1d: list,
                      score_min: int = 3,
                      rvol_min: float = 1.2,
                      atr_depth_min: float = 0.25) -> Optional[dict]:
    """
    Análisis completo de liquidez institucional.
    Retorna señal si hay setup válido, None si no.
    """
    if len(candles_5m) < 30:
        return None

    precio = candles_5m[-1]["close"]
    if precio <= 0:
        return None

    atr  = _calc_atr_simple(candles_5m, 14)
    rvol = calc_rvol(candles_5m, 20)

    # Sweep detection mejorado
    sweep = detectar_sweep_v2(candles_5m, pivot_length=5,
                               rvol_min=rvol_min, atr_depth_min=atr_depth_min)

    # BSL/SSL levels para log/info
    liq = get_bsl_ssl_levels(candles_5m, pivot_length=5)

    # HTF levels
    htf = get_htf_levels(candles_1h, candles_4h, candles_1d)

    # FVG post-sweep (últimas 5 velas)
    fvg = detectar_fvg_post_sweep(candles_5m, lookback=5)

    # Premium/Discount (rango 50 velas)
    pd_zone = get_premium_discount(candles_5m, lookback=50)

    # Elegir lado
    best_lado   = None
    best_score  = 0
    best_motivos = []

    for lado in (["LONG"] if not sweep["sweep_bear"] else ["SHORT", "LONG"]):
        if lado == "LONG" and not sweep["sweep_bull"]:
            continue
        if lado == "SHORT" and not sweep["sweep_bear"]:
            continue

        mss = sweep.get("mss_bull") if lado == "LONG" else sweep.get("mss_bear")
        s, m = calcular_score_liquidez(sweep, liq, htf, fvg, pd_zone,
                                       lado, rvol, mss)
        if s > best_score:
            best_score  = s
            best_lado   = lado
            best_motivos = m

    if best_lado is None or best_score < score_min:
        return None

    # SL/TP usando ATR
    atr_sl = atr if atr > 0 else precio * 0.005
    if best_lado == "LONG":
        sl  = precio - atr_sl * 1.5
        tp  = precio + atr_sl * 3.0
        tp1 = precio + atr_sl * 1.5
        # SL justo debajo del nivel sweepado si existe
        if sweep.get("swept_ssl"):
            sl = min(sl, sweep["swept_ssl"] * 0.998)
    else:
        sl  = precio + atr_sl * 1.5
        tp  = precio - atr_sl * 3.0
        tp1 = precio - atr_sl * 1.5
        if sweep.get("swept_bsl"):
            sl = max(sl, sweep["swept_bsl"] * 1.002)

    rr = abs(tp - precio) / abs(precio - sl) if abs(precio - sl) > 0 else 0
    if rr < 1.5:
        return None

    return {
        "par":          par,
        "lado":         best_lado,
        "precio":       precio,
        "sl":           round(sl, 8),
        "tp":           round(tp, 8),
        "tp1":          round(tp1, 8),
        "atr":          round(atr_sl, 8),
        "score":        best_score,
        "rr":           round(rr, 2),
        "motivos":      best_motivos,
        "rvol":         rvol,
        # Sweep info
        "sweep_bull":   sweep["sweep_bull"],
        "sweep_bear":   sweep["sweep_bear"],
        "swept_ssl":    sweep.get("swept_ssl"),
        "swept_bsl":    sweep.get("swept_bsl"),
        "atr_depth":    sweep.get("atr_depth_bull") or sweep.get("atr_depth_bear"),
        "mss":          sweep.get("mss_bull") or sweep.get("mss_bear"),
        # HTF
        "pdh":          htf.get("pdh"),
        "pdl":          htf.get("pdl"),
        "near_pdh":     htf.get("near_pdh"),
        "near_pdl":     htf.get("near_pdl"),
        # Contexto
        "pct_rango":    pd_zone.get("pct_rango"),
        "en_discount":  pd_zone.get("en_discount"),
        "fvg_bull":     fvg.get("bull_fvg"),
        "fvg_bear":     fvg.get("bear_fvg"),
        # Dist a niveles
        "dist_bsl_pct": liq.get("dist_bsl_pct"),
        "dist_ssl_pct": liq.get("dist_ssl_pct"),
        "nearest_bsl":  liq.get("nearest_bsl"),
        "nearest_ssl":  liq.get("nearest_ssl"),
    }
