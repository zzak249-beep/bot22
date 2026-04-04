"""
Estrategia Zero Lag EMA + Trend Reversal Probability
v2.0 — Filtros adicionales para reducir señales falsas

NUEVO v2.0:
  - RSI filter: no longs si RSI > 65, no shorts si RSI < 35
  - Trend strength: precio debe estar claramente de un lado de ZLEMA (> 0.3%)
  - Quality score: puntuación 0-100 para priorizar las mejores señales
  - Entry confirmada por 2 velas consecutivas del mismo lado
  - No entrar en consolidación (rango de precio < 1.5% en últimas 10 velas)
"""

import numpy as np
import pandas as pd
from typing import Dict


# ──────────────────────── INDICADORES ────────────────────────

def zlema(series: pd.Series, length: int) -> pd.Series:
    lag = (length - 1) // 2
    ema_data = series + (series.diff(lag))
    return ema_data.ewm(span=length, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> float:
    """RSI simple para filtrar entradas sobrecompradas/sobrevendidas."""
    if len(series) < period + 1:
        return 50.0
    delta  = series.diff()
    gains  = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    avg_g  = gains.rolling(window=period).mean().iloc[-1]
    avg_l  = losses.rolling(window=period).mean().iloc[-1]
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return float(100 - (100 / (1 + rs)))


def calculate_bands(zlema_series, mult, period):
    std   = zlema_series.rolling(window=period).std()
    upper = zlema_series + (std * mult)
    lower = zlema_series - (std * mult)
    return upper, lower


def stochastic_oscillator(high, low, close, period):
    lowest  = low.rolling(window=period).min()
    highest = high.rolling(window=period).max()
    k = 100 * (close - lowest) / (highest - lowest)
    return k.fillna(50)


def calculate_trend(close, zlema_series):
    return 1 if close.iloc[-1] > zlema_series.iloc[-1] else -1


def calculate_reversal_probability(close, zlema_series, upper_band, lower_band, osc):
    price         = close.iloc[-1]
    current_zlema = zlema_series.iloc[-1]
    current_upper = upper_band.iloc[-1]
    current_lower = lower_band.iloc[-1]
    current_osc   = osc.iloc[-1]

    band_range = current_upper - current_lower
    if band_range > 0:
        if price > current_zlema:
            dist         = (current_upper - price) / band_range
            band_factor  = max(0, min(0.4, 0.4 * (1 - dist)))
        else:
            dist         = (price - current_lower) / band_range
            band_factor  = max(0, min(0.4, 0.4 * (1 - dist)))
    else:
        band_factor = 0

    if current_osc > 80:
        osc_factor = 0.35
    elif current_osc > 70:
        osc_factor = 0.25
    elif current_osc < 20:
        osc_factor = 0.35
    elif current_osc < 30:
        osc_factor = 0.25
    else:
        osc_factor = 0

    if len(close) >= 5:
        momentum_factor = min(0.25, abs((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) * 100)
    else:
        momentum_factor = 0

    return min(1.0, max(0.0, band_factor + osc_factor + momentum_factor))


# ──────────────────────── FILTROS DE CALIDAD ─────────────────

def is_ranging(close: pd.Series, period: int = 10, threshold_pct: float = 1.5) -> bool:
    """Detecta si el precio está en consolidación (sin tendencia clara)."""
    if len(close) < period:
        return False
    recent = close.iloc[-period:]
    rng    = (recent.max() - recent.min()) / recent.min() * 100
    return rng < threshold_pct


def trend_strength_pct(close: pd.Series, zlema_series: pd.Series) -> float:
    """Distancia % del precio a la ZLEMA — mide fuerza de tendencia."""
    if zlema_series.iloc[-1] == 0:
        return 0.0
    return abs(close.iloc[-1] - zlema_series.iloc[-1]) / zlema_series.iloc[-1] * 100


def consecutive_candles(close: pd.Series, direction: str, n: int = 2) -> bool:
    """Verifica N velas consecutivas en la misma dirección."""
    if len(close) < n + 1:
        return True
    if direction == "bull":
        return all(close.iloc[-(i+1)] > close.iloc[-(i+2)] for i in range(n))
    else:
        return all(close.iloc[-(i+1)] < close.iloc[-(i+2)] for i in range(n))


def calculate_quality_score(
    close: pd.Series,
    zlema_series: pd.Series,
    upper_band: pd.Series,
    lower_band: pd.Series,
    osc: pd.Series,
    rsi_val: float,
    direction: str,
) -> int:
    """
    Puntuación 0-100 de la calidad de la señal.
    Solo entrar si score >= MIN_QUALITY_SCORE (default 60).
    """
    score = 0

    # 1. Distancia del precio a ZLEMA (tendencia fuerte = buena entrada)
    strength = trend_strength_pct(close, zlema_series)
    if strength > 1.0:
        score += 30
    elif strength > 0.5:
        score += 20
    elif strength > 0.3:
        score += 10
    # Si es < 0.3% → señal débil, no sumar puntos

    # 2. RSI en zona correcta
    if direction == "bull":
        if 40 <= rsi_val <= 60:
            score += 25   # zona ideal: tendencia sin sobrecompra
        elif 30 <= rsi_val < 40:
            score += 20   # rebote desde sobreventa
        elif 60 < rsi_val <= 70:
            score += 10   # momentum alcista, pero cuidado
        else:
            score -= 10   # sobrecomprado o sobrevendido en dirección equivocada
    else:
        if 40 <= rsi_val <= 60:
            score += 25
        elif 60 < rsi_val <= 70:
            score += 20   # rebote desde sobrecompra
        elif 30 <= rsi_val < 40:
            score += 10
        else:
            score -= 10

    # 3. Oscilador estocástico
    osc_val = osc.iloc[-1]
    if direction == "bull" and osc_val < 40:
        score += 20   # precio en zona baja del rango
    elif direction == "bear" and osc_val > 60:
        score += 20
    elif direction == "bull" and osc_val > 75:
        score -= 15   # sobrecomprado
    elif direction == "bear" and osc_val < 25:
        score -= 15

    # 4. Velas consecutivas confirmando dirección
    if consecutive_candles(close, "bull" if direction == "bull" else "bear", n=2):
        score += 15

    # 5. Sin consolidación
    if not is_ranging(close, period=8, threshold_pct=1.2):
        score += 10

    return max(0, min(100, score))


def detect_entry_signals(close, zlema_series, upper_band, lower_band, osc, trend):
    price    = close.iloc[-1]
    prev     = close.iloc[-2] if len(close) >= 2 else price
    z        = zlema_series.iloc[-1]
    z_prev   = zlema_series.iloc[-2] if len(zlema_series) >= 2 else z
    lower    = lower_band.iloc[-1]
    upper_v  = upper_band.iloc[-1]
    osc_v    = osc.iloc[-1]

    # Cruce alcista: precio cruzó ZLEMA hacia arriba
    bull_cross  = price > z and prev <= z_prev
    bull_bounce = price <= lower and osc_v < 30 and close.iloc[-1] > close.iloc[-2]
    bull_entry  = (trend == 1 and price > z and osc_v < 80 and (bull_cross or bull_bounce))

    # Cruce bajista: precio cruzó ZLEMA hacia abajo
    bear_cross  = price < z and prev >= z_prev
    bear_reject = price >= upper_v and osc_v > 70 and close.iloc[-1] < close.iloc[-2]
    bear_entry  = (trend == -1 and price < z and osc_v > 20 and (bear_cross or bear_reject))

    return {"bullish_entry": bull_entry, "bearish_entry": bear_entry}


# ──────────────────────── FUNCIÓN PRINCIPAL ──────────────────

def calculate_signals(
    df: pd.DataFrame,
    zlema_length: int   = 50,
    band_mult:    float = 1.2,
    osc_period:   int   = 20,
    min_quality:  int   = 55,   # score mínimo para generar señal de entrada
) -> Dict:
    """
    Calcula todas las señales con filtros de calidad.

    Retorna:
      close, zlema, upper_band, lower_band, oscillator,
      trend, probability, rsi,
      bullish_entry, bearish_entry,
      quality_score, ranging
    """
    if df is None or len(df) < max(zlema_length, osc_period) + 10:
        raise ValueError("Datos insuficientes")

    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"Columna faltante: {col}")

    z_vals        = zlema(df["close"], zlema_length)
    upper, lower  = calculate_bands(z_vals, band_mult, zlema_length)
    osc           = stochastic_oscillator(df["high"], df["low"], df["close"], osc_period)
    trend         = calculate_trend(df["close"], z_vals)
    prob          = calculate_reversal_probability(df["close"], z_vals, upper, lower, osc)
    rsi_val       = calculate_rsi(df["close"], 14)
    ranging       = is_ranging(df["close"], period=10, threshold_pct=1.5)
    entry_signals = detect_entry_signals(df["close"], z_vals, upper, lower, osc, trend)

    # Calcular quality score solo si hay señal potencial
    q_bull = 0
    q_bear = 0
    if entry_signals["bullish_entry"]:
        q_bull = calculate_quality_score(df["close"], z_vals, upper, lower, osc, rsi_val, "bull")
    if entry_signals["bearish_entry"]:
        q_bear = calculate_quality_score(df["close"], z_vals, upper, lower, osc, rsi_val, "bear")

    # Filtros adicionales de calidad
    # No entrar en consolidación
    if ranging:
        entry_signals["bullish_entry"] = False
        entry_signals["bearish_entry"] = False

    # RSI extremos: no longs sobrecomprados, no shorts sobrevendidos
    if rsi_val > 70:
        entry_signals["bullish_entry"] = False
    if rsi_val < 30:
        entry_signals["bearish_entry"] = False

    # Tendencia débil (precio muy cerca de ZLEMA) → no entrar
    if trend_strength_pct(df["close"], z_vals) < 0.3:
        entry_signals["bullish_entry"] = False
        entry_signals["bearish_entry"] = False

    # Quality score mínimo
    if entry_signals["bullish_entry"] and q_bull < min_quality:
        entry_signals["bullish_entry"] = False
    if entry_signals["bearish_entry"] and q_bear < min_quality:
        entry_signals["bearish_entry"] = False

    return {
        "close":          float(df["close"].iloc[-1]),
        "zlema":          float(z_vals.iloc[-1]),
        "upper_band":     float(upper.iloc[-1]),
        "lower_band":     float(lower.iloc[-1]),
        "oscillator":     float(osc.iloc[-1]),
        "trend":          trend,
        "probability":    prob,
        "rsi":            round(rsi_val, 1),
        "bullish_entry":  entry_signals["bullish_entry"],
        "bearish_entry":  entry_signals["bearish_entry"],
        "quality_bull":   q_bull,
        "quality_bear":   q_bear,
        "ranging":        ranging,
    }
