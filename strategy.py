"""
Estrategia Zero Lag EMA + Trend Reversal Probability — v2.1
Más conservador: reduce entradas falsas en mercados laterales
"""

import pandas as pd
from typing import Dict


def zlema(series: pd.Series, length: int) -> pd.Series:
    lag      = (length - 1) // 2
    ema_data = series + series.diff(lag)
    return ema_data.ewm(span=length, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> float:
    if len(series) < period + 1:
        return 50.0
    delta  = series.diff()
    gains  = delta.clip(lower=0).rolling(window=period).mean()
    losses = (-delta).clip(lower=0).rolling(window=period).mean()
    ag, al = gains.iloc[-1], losses.iloc[-1]
    if al == 0:
        return 100.0
    return float(100 - (100 / (1 + ag / al)))


def calculate_bands(z, mult, period):
    std   = z.rolling(window=period).std()
    return z + std * mult, z - std * mult


def stochastic_oscillator(high, low, close, period):
    lo = low.rolling(window=period).min()
    hi = high.rolling(window=period).max()
    k  = 100 * (close - lo) / (hi - lo)
    return k.fillna(50)


def calculate_reversal_probability(close, z, upper, lower, osc) -> float:
    price, zv = close.iloc[-1], z.iloc[-1]
    up, lo    = upper.iloc[-1], lower.iloc[-1]
    osc_v     = osc.iloc[-1]
    rng       = up - lo

    band_factor = 0.0
    if rng > 0:
        dist = ((up - price) / rng if price > zv else (price - lo) / rng)
        band_factor = max(0.0, min(0.4, 0.4 * (1 - dist)))

    osc_factor = (0.35 if osc_v > 80 else
                  0.25 if osc_v > 70 else
                  0.35 if osc_v < 20 else
                  0.25 if osc_v < 30 else 0.0)

    mom = 0.0
    if len(close) >= 5:
        mom = min(0.25, abs((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) * 100)

    return min(1.0, max(0.0, band_factor + osc_factor + mom))


def is_ranging(close: pd.Series, period=12, threshold=1.8) -> bool:
    """Detecta consolidación: rango < threshold%."""
    if len(close) < period:
        return False
    r = close.iloc[-period:]
    return (r.max() - r.min()) / r.min() * 100 < threshold


def trend_gap_pct(close: pd.Series, z: pd.Series) -> float:
    """% de distancia precio-ZLEMA. > 0.5% = tendencia real."""
    zv = z.iloc[-1]
    return abs(close.iloc[-1] - zv) / zv * 100 if zv != 0 else 0.0


def quality_score(close, z, upper, lower, osc, rsi_val, direction) -> int:
    score = 0
    gap = trend_gap_pct(close, z)

    # Fuerza de tendencia
    if gap > 1.5:   score += 35
    elif gap > 0.8: score += 25
    elif gap > 0.5: score += 15
    else:           score += 0

    # RSI en zona correcta
    if direction == "bull":
        if 35 <= rsi_val <= 58:  score += 30
        elif 25 <= rsi_val < 35: score += 20  # rebote sobreventa
        elif 58 < rsi_val <= 68: score += 10
        elif rsi_val > 68:       score -= 20  # sobrecomprado: peligro
    else:
        if 42 <= rsi_val <= 65:  score += 30
        elif 65 < rsi_val <= 75: score += 20  # rebote sobrecompra
        elif 35 <= rsi_val < 42: score += 10
        elif rsi_val < 35:       score -= 20  # sobrevendido: peligro

    # Oscilador estocástico
    osc_v = osc.iloc[-1]
    if direction == "bull":
        if osc_v < 35:  score += 25
        elif osc_v > 75: score -= 20
    else:
        if osc_v > 65:  score += 25
        elif osc_v < 25: score -= 20

    # Sin consolidación
    if not is_ranging(close, 10, 1.8):
        score += 10

    return max(0, min(100, score))


def detect_entry_signals(close, z, upper, lower, osc, trend) -> Dict:
    p     = close.iloc[-1]
    pp    = close.iloc[-2] if len(close) >= 2 else p
    zv    = z.iloc[-1]
    zprev = z.iloc[-2] if len(z) >= 2 else zv
    osc_v = osc.iloc[-1]

    # LONG: cruce alcista reciente (precio cruzó ZLEMA hacia arriba)
    bull_cross  = (p > zv and pp <= zprev)
    bull_bounce = (p <= lower.iloc[-1] and osc_v < 25 and p > close.iloc[-2])
    bull_entry  = trend == 1 and p > zv and osc_v < 75 and (bull_cross or bull_bounce)

    # SHORT: cruce bajista reciente
    bear_cross  = (p < zv and pp >= zprev)
    bear_reject = (p >= upper.iloc[-1] and osc_v > 75 and p < close.iloc[-2])
    bear_entry  = trend == -1 and p < zv and osc_v > 25 and (bear_cross or bear_reject)

    return {"bullish_entry": bull_entry, "bearish_entry": bear_entry}


def calculate_signals(df: pd.DataFrame,
                      zlema_length=50, band_mult=1.2, osc_period=20,
                      min_quality=60) -> Dict:
    if df is None or len(df) < max(zlema_length, osc_period) + 10:
        raise ValueError("Datos insuficientes")

    z     = zlema(df["close"], zlema_length)
    up, lo = calculate_bands(z, band_mult, zlema_length)
    osc   = stochastic_oscillator(df["high"], df["low"], df["close"], osc_period)
    trend = 1 if df["close"].iloc[-1] > z.iloc[-1] else -1
    prob  = calculate_reversal_probability(df["close"], z, up, lo, osc)
    rsi_v = calculate_rsi(df["close"], 14)
    rang  = is_ranging(df["close"], 12, 1.8)
    gap   = trend_gap_pct(df["close"], z)

    sigs  = detect_entry_signals(df["close"], z, up, lo, osc, trend)

    # Filtros duros — apagan señal independientemente del score
    if rang:
        sigs["bullish_entry"] = False
        sigs["bearish_entry"] = False

    if rsi_v > 72:  # sobrecomprado → no long
        sigs["bullish_entry"] = False
    if rsi_v < 28:  # sobrevendido → no short
        sigs["bearish_entry"] = False

    if gap < 0.5:  # tendencia demasiado débil
        sigs["bullish_entry"] = False
        sigs["bearish_entry"] = False

    q_bull = quality_score(df["close"], z, up, lo, osc, rsi_v, "bull") if sigs["bullish_entry"] else 0
    q_bear = quality_score(df["close"], z, up, lo, osc, rsi_v, "bear") if sigs["bearish_entry"] else 0

    if sigs["bullish_entry"] and q_bull < min_quality:
        sigs["bullish_entry"] = False
    if sigs["bearish_entry"] and q_bear < min_quality:
        sigs["bearish_entry"] = False

    return {
        "close":          float(df["close"].iloc[-1]),
        "zlema":          float(z.iloc[-1]),
        "upper_band":     float(up.iloc[-1]),
        "lower_band":     float(lo.iloc[-1]),
        "oscillator":     float(osc.iloc[-1]),
        "trend":          trend,
        "probability":    prob,
        "rsi":            round(rsi_v, 1),
        "gap_pct":        round(gap, 3),
        "bullish_entry":  sigs["bullish_entry"],
        "bearish_entry":  sigs["bearish_entry"],
        "quality_bull":   q_bull,
        "quality_bear":   q_bear,
        "ranging":        rang,
    }
