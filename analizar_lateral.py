"""
analizar_lateral.py — Módulo de detección y trading en mercado lateral
SMC Bot v5.5 [Range Trading Edition]

Detecta cuando el mercado está en rango (lateral) y genera señales
apropiadas para esa condición usando EQH/EQL, rebotes en extremos,
y patrones de vela en los bordes del rango.

Integrar en analizar.py llamando a:
  - es_mercado_lateral(candles) → bool + info del rango
  - señal_lateral(par, candles, ...) → señal o None
"""

import logging
from typing import Optional

log = logging.getLogger("lateral")


def calc_adx(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """
    Average Directional Index — mide la FUERZA de la tendencia.
    ADX < 25 → mercado lateral/sin tendencia clara
    ADX > 25 → tendencia definida
    ADX > 40 → tendencia fuerte
    """
    if len(highs) < period + 2:
        return 25.0  # default neutral

    try:
        # True Range
        trs = []
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)

        # Directional Movement
        plus_dm  = []
        minus_dm = []
        for i in range(1, len(highs)):
            up   = highs[i] - highs[i-1]
            down = lows[i-1] - lows[i]
            plus_dm.append(max(up, 0)   if up > down   else 0)
            minus_dm.append(max(down, 0) if down > up   else 0)

        # Smooth over period
        def smooth(data, p):
            if len(data) < p:
                return 0
            s = sum(data[:p])
            for i in range(p, len(data)):
                s = s - s/p + data[i]
            return s

        atr14    = smooth(trs[-period*2:],     period)
        pdi14    = smooth(plus_dm[-period*2:],  period)
        mdi14    = smooth(minus_dm[-period*2:], period)

        if atr14 <= 0:
            return 25.0

        plus_di  = 100 * pdi14 / atr14
        minus_di = 100 * mdi14 / atr14
        dx_sum   = abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9) * 100

        # Simple ADX aproximado (promedio de DX últimas N velas)
        dx_list = []
        for i in range(max(0, len(trs) - period*2), len(trs)):
            if i == 0:
                continue
            tr_i  = trs[i]
            pdi_i = plus_dm[i]
            mdi_i = minus_dm[i]
            if tr_i > 0:
                pdi_pct = 100 * pdi_i / tr_i
                mdi_pct = 100 * mdi_i / tr_i
                deno    = pdi_pct + mdi_pct
                if deno > 0:
                    dx_list.append(abs(pdi_pct - mdi_pct) / deno * 100)

        return sum(dx_list[-period:]) / len(dx_list[-period:]) if dx_list else 25.0

    except Exception:
        return 25.0


def detectar_rango(candles: list, lookback: int = 30) -> dict:
    """
    Detecta si el mercado está en rango (lateral).
    
    Returns:
        {
          "es_rango": bool,
          "high": float,       # techo del rango
          "low": float,        # suelo del rango
          "mid": float,        # punto medio
          "amplitud_pct": float,  # amplitud como % del precio
          "adx": float,
          "cerca_high": bool,  # precio cerca del techo
          "cerca_low": bool,   # precio cerca del suelo
          "en_mitad": bool,    # precio en la zona media (evitar trades)
        }
    """
    if len(candles) < lookback:
        return {"es_rango": False}

    recientes = candles[-lookback:]
    highs  = [c["high"]  for c in recientes]
    lows   = [c["low"]   for c in recientes]
    closes = [c["close"] for c in recientes]
    precio = closes[-1]

    rango_high = max(highs)
    rango_low  = min(lows)
    rango_mid  = (rango_high + rango_low) / 2
    amplitud   = (rango_high - rango_low) / rango_mid * 100 if rango_mid > 0 else 0

    # ADX para confirmar falta de tendencia
    all_highs  = [c["high"]  for c in candles]
    all_lows   = [c["low"]   for c in candles]
    all_closes = [c["close"] for c in candles]
    adx = calc_adx(all_highs, all_lows, all_closes, 14)

    # Rango válido: amplitud entre 2% y 15%, ADX < 25
    es_rango = (
        2.0 <= amplitud <= 15.0
        and adx < 25.0
    )

    # Tolerancia del 15% del rango para "cerca del extremo"
    tolerancia = (rango_high - rango_low) * 0.15

    cerca_high = precio >= rango_high - tolerancia
    cerca_low  = precio <= rango_low + tolerancia
    en_mitad   = not cerca_high and not cerca_low

    return {
        "es_rango":     es_rango,
        "high":         rango_high,
        "low":          rango_low,
        "mid":          rango_mid,
        "amplitud_pct": round(amplitud, 2),
        "adx":          round(adx, 1),
        "cerca_high":   cerca_high,
        "cerca_low":    cerca_low,
        "en_mitad":     en_mitad,
    }


def señal_en_rango(
    par: str,
    candles: list,
    rango: dict,
    rsi: float,
    patron_long: dict,
    patron_short: dict,
    score_base_long: int,
    score_base_short: int,
) -> Optional[dict]:
    """
    Genera señal específica para mercado lateral.
    Solo opera en los extremos del rango, nunca en la mitad.
    
    En rango:
    - LONG cerca del suelo (EQL/soporte) con RSI < 50 y patrón alcista
    - SHORT cerca del techo (EQH/resistencia) con RSI > 50 y patrón bajista
    - TP conservador: 50-70% del rango (no buscar breakout)
    - SL fuera del rango
    """
    if not rango.get("es_rango") or rango.get("en_mitad"):
        return None

    precio     = candles[-1]["close"]
    rng_high   = rango["high"]
    rng_low    = rango["low"]
    rng_amplitud = rng_high - rng_low

    lado = score = None
    motivos = []

    # LONG en suelo del rango
    if rango["cerca_low"] and rsi < 55:
        score = score_base_long + 2  # bonus por estar en zona de rango
        motivos = ["RANGO_SUELO", "EQL"]
        if patron_long.get("patron"):
            score += patron_long["confianza"]
            motivos.append(patron_long["patron"])
        if rsi < 40:
            score += 1
            motivos.append(f"RSI{rsi:.0f}")
        lado = "LONG"

    # SHORT en techo del rango
    elif rango["cerca_high"] and rsi > 45:
        score = score_base_short + 2
        motivos = ["RANGO_TECHO", "EQH"]
        if patron_short.get("patron"):
            score += patron_short["confianza"]
            motivos.append(patron_short["patron"])
        if rsi > 60:
            score += 1
            motivos.append(f"RSI{rsi:.0f}")
        lado = "SHORT"

    if lado is None or score < 6:
        return None

    # SL/TP específicos para rango
    # SL: fuera del rango (más allá del extremo)
    sl_margin = rng_amplitud * 0.08  # 8% del rango más allá del extremo

    if lado == "LONG":
        sl   = rng_low - sl_margin
        # TP: 60% del rango hacia el techo
        tp   = precio + rng_amplitud * 0.60
        tp1  = precio + rng_amplitud * 0.30
        dist = precio - sl
    else:
        sl   = rng_high + sl_margin
        tp   = precio - rng_amplitud * 0.60
        tp1  = precio - rng_amplitud * 0.30
        dist = sl - precio

    if dist <= 0:
        return None

    rr = abs(tp - precio) / dist
    if rr < 1.5:  # R:R mínimo más bajo para rango (1.5 vs 2.0 tendencia)
        return None

    return {
        "par":              par,
        "lado":             lado,
        "precio":           precio,
        "sl":               round(sl, 8),
        "tp":               round(tp, 8),
        "tp1":              round(tp1, 8),
        "tp2":              round(tp, 8),  # En rango no hay TP2 mayor
        "score":            score,
        "rr":               round(rr, 2),
        "motivos":          motivos,
        "mercado_lateral":  True,
        "rango_high":       round(rng_high, 8),
        "rango_low":        round(rng_low, 8),
        "rango_adx":        rango["adx"],
        "rango_amplitud":   rango["amplitud_pct"],
    }
