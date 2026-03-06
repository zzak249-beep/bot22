import pandas as pd
import numpy as np
from config import (RSI_PERIOD, BB_PERIOD, BB_SIGMA, SMA_PERIOD,
                    TREND_LOOKBACK, TREND_THRESH,
                    VOLUME_MA_PERIOD, VOLUME_MIN_RATIO, VOLUME_FILTER)

# ══════════════════════════════════════════════════════
# indicators.py — Indicadores técnicos v12.3
# Mejoras: filtro de volumen, scoring con momentum
# ══════════════════════════════════════════════════════

def rsi_calc(close: pd.Series) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).rolling(RSI_PERIOD).mean()
    l = (-d.clip(upper=0)).rolling(RSI_PERIOD).mean()
    return 100 - 100 / (1 + g / l.replace(0, float("nan")))

def atr_calc(df: pd.DataFrame, p: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def macd_calc(close: pd.Series) -> pd.Series:
    e12 = close.ewm(span=12, adjust=False).mean()
    e26 = close.ewm(span=26, adjust=False).mean()
    line = e12 - e26
    return line - line.ewm(span=9, adjust=False).mean()

def stoch_rsi_calc(close: pd.Series, period: int = 14, k: int = 3) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    rs = g / l.replace(0, float("nan"))
    rsi_s = 100 - 100 / (1 + rs)
    lo = rsi_s.rolling(period).min()
    hi = rsi_s.rolling(period).max()
    stoch = (rsi_s - lo) / (hi - lo).replace(0, float("nan")) * 100
    return stoch.rolling(k).mean()

def divergence(cls: pd.Series, rsi_s: pd.Series, lb: int = 6):
    if len(rsi_s) < lb + 2:
        return None
    r = rsi_s.iloc[-lb-1:]; p = cls.iloc[-lb-1:]
    rn = float(r.iloc[-1]); pn = float(p.iloc[-1])
    if pn < float(p.iloc[:-1].min()) and rn > float(r.iloc[:-1].min()) + 3:
        return "bull"
    if pn > float(p.iloc[:-1].max()) and rn < float(r.iloc[:-1].max()) - 3:
        return "bear"
    return None

def get_trend(basis_series: pd.Series, i: int) -> str:
    if i < TREND_LOOKBACK: return "flat"
    now  = float(basis_series.iloc[i])
    prev = float(basis_series.iloc[i - TREND_LOOKBACK])
    if now == 0 or prev == 0: return "flat"
    change_pct = (now - prev) / prev * 100
    if   change_pct >  TREND_THRESH: return "up"
    elif change_pct < -TREND_THRESH: return "down"
    else:                             return "flat"

def volume_ok(df: pd.DataFrame, i: int) -> bool:
    """True si el volumen actual es suficiente para operar."""
    if not VOLUME_FILTER:
        return True
    if i < VOLUME_MA_PERIOD:
        return True
    vol_now = float(df["volume"].iloc[i])
    vol_ma  = float(df["volume"].iloc[i-VOLUME_MA_PERIOD:i].mean())
    if vol_ma == 0:
        return True
    return (vol_now / vol_ma) >= VOLUME_MIN_RATIO

def momentum_bars(close: pd.Series, i: int, lookback: int = 5) -> int:
    """
    Cuenta cuántas de las últimas N velas son bajistas (negativas).
    Retorna número 0..lookback. Más alto = más presión bajista.
    """
    if i < lookback:
        return 0
    segment = close.iloc[i-lookback:i+1]
    return sum(1 for j in range(1, len(segment)) if float(segment.iloc[j]) < float(segment.iloc[j-1]))

def calc_score_long(r: float, dv, mb: bool, stv, bear_bars: int = 0) -> int:
    s = 40
    if   r < 20: s += 30
    elif r < 25: s += 22
    elif r < 28: s += 15
    elif r < 30: s += 12
    elif r < 32: s += 8
    if dv == "bull": s += 18
    if mb: s += 5
    if stv is not None and not np.isnan(stv):
        if   stv < 10: s += 15
        elif stv < 20: s += 10
        elif stv < 30: s += 5
    # Penalizar si demasiadas velas seguidas bajistas (momentum bajista)
    if bear_bars >= 4: s -= 10
    elif bear_bars == 3: s -= 5
    return min(max(s, 0), 100)

def calc_score_short(r: float, dv, mb: bool, stv, bull_bars: int = 0) -> int:
    s = 40
    if   r > 80: s += 30
    elif r > 75: s += 22
    elif r > 72: s += 15
    elif r > 70: s += 12
    elif r > 68: s += 8
    if dv == "bear": s += 18
    if not mb: s += 5
    if stv is not None and not np.isnan(stv):
        if   stv > 90: s += 15
        elif stv > 80: s += 10
        elif stv > 70: s += 5
    # Penalizar si demasiadas velas alcistas seguidas (podría continuar)
    if bull_bars >= 4: s -= 10
    elif bull_bars == 3: s -= 5
    return min(max(s, 0), 100)

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    basis        = df["close"].rolling(BB_PERIOD).mean()
    std          = df["close"].rolling(BB_PERIOD).std()
    df["upper"]  = basis + BB_SIGMA * std
    df["basis"]  = basis
    df["lower"]  = basis - BB_SIGMA * std
    df["rsi"]    = rsi_calc(df["close"])
    df["atr"]    = atr_calc(df)
    df["macd"]   = macd_calc(df["close"])
    df["stoch"]  = stoch_rsi_calc(df["close"])
    df["sma50"]  = df["close"].rolling(SMA_PERIOD).mean()
    df["vol_ma"] = df["volume"].rolling(VOLUME_MA_PERIOD).mean()
    return df
