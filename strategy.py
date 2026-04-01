"""
Strategy: Zero Lag Trend Signals + Trend Reversal Probability
Replica exacta de los indicadores Pine Script de AlgoAlpha
"""

import numpy as np
import pandas as pd
from scipy import stats
import logging

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's Smoothed MA (usado por Pine Script ta.rma)"""
    return series.ewm(alpha=1 / period, adjust=False).mean()


# ─────────────────────────────────────────────
# INDICADOR 1: Zero Lag Trend Signals
# ─────────────────────────────────────────────

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    return _rma(tr, length)


def _zlema(close: pd.Series, length: int) -> pd.Series:
    lag = int((length - 1) / 2)
    adjusted = close + (close - close.shift(lag))
    return _ema(adjusted, length)


def _compute_trend_series(close: pd.Series, zlema_vals: pd.Series,
                           volatility: pd.Series) -> pd.Series:
    """Replica el 'var trend = 0' de Pine Script (stateful, barra por barra)."""
    trend = np.zeros(len(close), dtype=int)
    for i in range(1, len(close)):
        c, pc = close.iloc[i], close.iloc[i - 1]
        z, pz = zlema_vals.iloc[i], zlema_vals.iloc[i - 1]
        v, pv = volatility.iloc[i], volatility.iloc[i - 1]

        # ta.crossover(close, zlema + volatility)
        if pc <= pz + pv and c > z + v:
            trend[i] = 1
        # ta.crossunder(close, zlema - volatility)
        elif pc >= pz - pv and c < z - v:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]

    return pd.Series(trend, index=close.index)


# ─────────────────────────────────────────────
# INDICADOR 2: Trend Reversal Probability
# ─────────────────────────────────────────────

def _amazing_oscillator(high: pd.Series, low: pd.Series) -> pd.Series:
    hl2 = (high + low) / 2
    return hl2.rolling(5).mean() - hl2.rolling(34).mean()


def _custom_rsi(ao: pd.Series, period: int) -> pd.Series:
    change = ao.diff()
    rise = _rma(change.clip(lower=0), period)
    fall = _rma((-change).clip(lower=0), period)

    rsi = pd.Series(np.nan, index=ao.index)
    for i in range(len(rise)):
        r, f = rise.iloc[i], fall.iloc[i]
        if pd.isna(r) or pd.isna(f):
            continue
        if f == 0:
            rsi.iloc[i] = 50.0
        elif r == 0:
            rsi.iloc[i] = -50.0
        else:
            rsi.iloc[i] = (100 - (100 / (1 + r / f))) - 50
    return rsi


def _bars_since_cross(rsi: pd.Series):
    """
    Replica exacta de Pine Script:
        cut = ta.barssince(ta.cross(customRSI, 0))
        if cut == 0 and cut != cut[1]
            durations.unshift(cut[1])

    ta.cross(a, b) → True cuando (a > b y a[1] <= b) o (a < b y a[1] >= b).
    ta.barssince → 0 en la barra del cruce, +1 en barras siguientes.

    Cuando cut pasa a 0, cut[1] en Pine = cut[i-1] aquí = duración del segmento
    que acaba de terminar. La condición (cut != cut[1]) equivale a (cut_prev > 0).
    """
    cut = np.zeros(len(rsi), dtype=int)
    durations = []
    for i in range(1, len(rsi)):
        prev, curr = rsi.iloc[i - 1], rsi.iloc[i]
        if pd.isna(prev) or pd.isna(curr):
            cut[i] = 0
            continue
        # ta.cross(customRSI, 0) — ambas direcciones
        crossed = (curr > 0 and prev <= 0) or (curr < 0 and prev >= 0)
        if crossed:
            cut_prev = int(cut[i - 1])          # = cut[1] en Pine cuando cut==0
            if cut_prev > 0:                     # condición: cut != cut[1]
                durations.append(cut_prev)
            cut[i] = 0
        else:
            cut[i] = cut[i - 1] + 1
    return pd.Series(cut, index=rsi.index), durations


def _cdf(z: float) -> float:
    """CDF normal exacta igual que el Pine Script f_cdf()."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = -1 if z < 0 else 1
    x = abs(z) / (2 ** 0.5)
    t = 1 / (1 + p * x)
    erf = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-x * x)
    return 0.5 * (1 + sign * erf)


# ─────────────────────────────────────────────
# FUNCIÓN PRINCIPAL
# ─────────────────────────────────────────────

def calculate_signals(df: pd.DataFrame,
                       length: int = 70,
                       mult: float = 1.2,
                       osc_period: int = 20) -> dict:
    """
    Calcula todas las señales necesarias para el bot.

    Parámetros
    ----------
    df          : DataFrame con columnas open, high, low, close (al menos 500 velas)
    length      : Longitud ZLEMA (Pine Script default: 70)
    mult        : Multiplicador de bandas (Pine Script default: 1.2)
    osc_period  : Período oscilador reversión (Pine Script default: 20; vídeo recomienda 50)

    Retorna dict con:
        trend           : 1 (alcista) | -1 (bajista)
        probability     : float [0-1] probabilidad de reversión
        bullish_entry   : bool  — señal de entrada larga
        bearish_entry   : bool  — señal de entrada corta
        trend_bullish   : bool  — cambio de tendencia a alcista
        trend_bearish   : bool  — cambio de tendencia a bajista
        zlema           : float último valor ZLEMA
        volatility      : float última banda
        close           : float precio actual
        bars_since_cross: int   barras desde último cruce RSI
    """
    if len(df) < max(length * 3 + 10, osc_period * 3):
        raise ValueError(f"Necesitas al menos {max(length*3+10, osc_period*3)} velas")

    close = df["close"].astype(float).reset_index(drop=True)
    high  = df["high"].astype(float).reset_index(drop=True)
    low   = df["low"].astype(float).reset_index(drop=True)

    # ── Indicador 1 ──
    z = _zlema(close, length)
    atr_vals = _atr(high, low, close, length)
    vol = atr_vals.rolling(length * 3).max() * mult

    trend_series = _compute_trend_series(close, z, vol)

    # Señales de entrada (pequeñas flechas)
    def crossover(a, b):
        return (a.shift(1) <= b.shift(1)) & (a > b)

    def crossunder(a, b):
        return (a.shift(1) >= b.shift(1)) & (a < b)

    bull_entry_series = crossover(close, z) & (trend_series == 1) & (trend_series.shift(1) == 1)
    bear_entry_series = crossunder(close, z) & (trend_series == -1) & (trend_series.shift(1) == -1)

    # Cambio de tendencia (flechas grandes)
    trend_bull_series = crossover(trend_series, pd.Series(0, index=trend_series.index))
    trend_bear_series = crossunder(trend_series, pd.Series(0, index=trend_series.index))

    # ── Indicador 2 ──
    ao = _amazing_oscillator(high, low)
    rsi = _custom_rsi(ao, osc_period)
    cut, durations = _bars_since_cross(rsi)

    current_cut = int(cut.iloc[-1])
    probability = 0.5
    if len(durations) >= 5:
        avg = float(np.mean(durations))
        std = float(np.std(durations))
        if std > 0:
            z_score = (current_cut - avg) / std
            probability = _cdf(z_score)

    # Tomar valores de la última barra completa (penúltima para señales, última para estado)
    last = -1
    return {
        "trend":            int(trend_series.iloc[last]),
        "probability":      round(probability, 4),
        "bullish_entry":    bool(bull_entry_series.iloc[last]),
        "bearish_entry":    bool(bear_entry_series.iloc[last]),
        "trend_bullish":    bool(trend_bull_series.iloc[last]),
        "trend_bearish":    bool(trend_bear_series.iloc[last]),
        "zlema":            float(z.iloc[last]),
        "volatility":       float(vol.iloc[last]),
        "close":            float(close.iloc[last]),
        "bars_since_cross": current_cut,
        "durations_count":  len(durations),
    }
