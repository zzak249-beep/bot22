#!/usr/bin/env python3
"""
indicators.py v3.0 - Indicadores tecnicos para estrategia BB+RSI
"""

import numpy as np
import pandas as pd
from config import (BB_PERIOD, BB_STD, SMA_PERIOD, RSI_PERIOD, ATR_PERIOD,
                    MACD_FAST, MACD_SLOW, MACD_SIGNAL, STOCH_K, STOCH_D,
                    TREND_LOOKBACK, TREND_THRESH, VOLUME_FILTER)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["close"]
    h = df["high"]
    lo = df["low"]

    # Bollinger Bands
    roll = c.rolling(BB_PERIOD)
    df["basis"] = roll.mean()
    std = roll.std()
    df["upper"] = df["basis"] + BB_STD * std
    df["lower"] = df["basis"] - BB_STD * std

    # SMA50
    df["sma50"] = c.rolling(SMA_PERIOD).mean()

    # RSI
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR
    tr = pd.concat([
        h - lo,
        (h - c.shift()).abs(),
        (lo - c.shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()

    # MACD
    ema_fast = c.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = c.ewm(span=MACD_SLOW, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["macd"] = macd_line - signal_line  # histogram

    # Stochastic
    low_k  = lo.rolling(STOCH_K).min()
    high_k = h.rolling(STOCH_K).max()
    pct_k  = 100 * (c - low_k) / (high_k - low_k).replace(0, np.nan)
    df["stoch"] = pct_k.rolling(STOCH_D).mean()

    # Volume MA
    if "volume" in df.columns:
        df["vol_ma"] = df["volume"].rolling(20).mean()

    return df


def get_trend(basis: pd.Series, idx: int) -> str:
    """
    Calcula la tendencia del precio usando la BB basis (SMA).
    Retorna: "up" | "down" | "flat"
    """
    if idx < TREND_LOOKBACK:
        return "flat"
    try:
        old = float(basis.iloc[idx - TREND_LOOKBACK])
        cur = float(basis.iloc[idx])
        if old == 0:
            return "flat"
        change = (cur - old) / old
        if change > TREND_THRESH:
            return "up"
        if change < -TREND_THRESH:
            return "down"
        return "flat"
    except Exception:
        return "flat"


def divergence(closes: pd.Series, rsis: pd.Series) -> str:
    """
    Detecta divergencias entre precio y RSI.
    Retorna: "bull" | "bear" | "none"
    """
    try:
        if len(closes) < 5 or len(rsis) < 5:
            return "none"
        closes = closes.dropna()
        rsis   = rsis.dropna()
        if len(closes) < 4 or len(rsis) < 4:
            return "none"

        c_old, c_new = float(closes.iloc[-4]), float(closes.iloc[-1])
        r_old, r_new = float(rsis.iloc[-4]),   float(rsis.iloc[-1])

        # Divergencia alcista: precio baja pero RSI sube
        if c_new < c_old and r_new > r_old + 2:
            return "bull"
        # Divergencia bajista: precio sube pero RSI baja
        if c_new > c_old and r_new < r_old - 2:
            return "bear"
    except Exception:
        pass
    return "none"


def calc_score_long(rsi: float, div: str, macd_bull: bool,
                    stoch: float, bear_bars: int) -> int:
    """Puntuacion para señal LONG (0-100)."""
    score = 0
    # RSI oversold
    if rsi < 30:    score += 30
    elif rsi < 35:  score += 25
    elif rsi < 40:  score += 20
    elif rsi < 45:  score += 15
    else:           score += 5
    # Divergencia
    if div == "bull":   score += 25
    elif div == "none": score += 10
    # MACD
    if macd_bull:   score += 20
    # Stoch
    if stoch < 25:  score += 15
    elif stoch < 35: score += 10
    elif stoch < 50: score += 5
    # Momentum (velas bajistas = mas probable rebote)
    if bear_bars >= 4:   score += 10
    elif bear_bars >= 3: score += 5
    return score


def calc_score_short(rsi: float, div: str, macd_bull: bool,
                     stoch: float, bull_bars: int) -> int:
    """Puntuacion para señal SHORT (0-100)."""
    score = 0
    # RSI overbought
    if rsi > 70:    score += 30
    elif rsi > 65:  score += 25
    elif rsi > 60:  score += 20
    elif rsi > 55:  score += 15
    else:           score += 5
    # Divergencia
    if div == "bear":   score += 25
    elif div == "none": score += 10
    # MACD
    if not macd_bull:   score += 20
    # Stoch
    if stoch > 75:   score += 15
    elif stoch > 65: score += 10
    elif stoch > 50: score += 5
    # Momentum
    if bull_bars >= 4:   score += 10
    elif bull_bars >= 3: score += 5
    return score


def volume_ok(df: pd.DataFrame, idx: int, mult: float = 1.2) -> bool:
    """True si el volumen actual es mayor que la media."""
    if not VOLUME_FILTER:
        return True
    try:
        if "vol_ma" not in df.columns or "volume" not in df.columns:
            return True
        vol = float(df["volume"].iloc[idx])
        vma = float(df["vol_ma"].iloc[idx])
        return vol > vma * mult
    except Exception:
        return True


def momentum_bars(closes: pd.Series, idx: int, lookback: int = 5) -> int:
    """Cuenta velas bajistas en los ultimos N periodos."""
    count = 0
    try:
        start = max(0, idx - lookback + 1)
        for i in range(start, idx + 1):
            if i > 0 and float(closes.iloc[i]) < float(closes.iloc[i - 1]):
                count += 1
    except Exception:
        pass
    return count
