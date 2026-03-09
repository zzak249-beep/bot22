"""
indicators.py — Cálculo de EMAs, RSI, ATR, VWAP semanal
"""
import numpy as np
import pandas as pd


# ─── EMAs ────────────────────────────────────────────────────
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ─── RSI ─────────────────────────────────────────────────────
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta  = series.diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)
    avg_g  = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l  = loss.ewm(com=period - 1, adjust=False).mean()
    rs     = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ─── ATR ─────────────────────────────────────────────────────
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c  = c.shift(1)
    tr      = pd.concat([
        h - l,
        (h - prev_c).abs(),
        (l - prev_c).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


# ─── VWAP SEMANAL (POC INSTITUCIONAL) ────────────────────────
def vwap_weekly(df: pd.DataFrame) -> pd.Series:
    """
    Calcula VWAP acumulado por semana ISO.
    Equivalente al VWAP semanal de TradingView.
    """
    df   = df.copy()
    week = df.index.isocalendar().week.astype(int)
    year = df.index.year

    tp   = (df["high"] + df["low"] + df["close"]) / 3
    vol  = df["volume"]

    key  = year * 100 + week
    cumtp  = (tp * vol).groupby(key).cumsum()
    cumvol = vol.groupby(key).cumsum()

    return cumtp / cumvol


# ─── BUILDER ─────────────────────────────────────────────────
def build(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega todas las columnas de indicadores al DataFrame.
    df debe tener índice datetime y columnas: open, high, low, close, volume
    """
    from config import (
        EMA_FAST, EMA_MID, EMA_TREND, EMA_STRUCT, EMA_MACRO,
        RSI_PERIOD, VOL_MA_PERIOD,
    )

    df = df.copy()

    # EMAs
    df["ema9"]   = ema(df["close"], EMA_FAST)
    df["ema21"]  = ema(df["close"], EMA_MID)
    df["ema50"]  = ema(df["close"], EMA_TREND)
    df["ema120"] = ema(df["close"], EMA_STRUCT)
    df["ema200"] = ema(df["close"], EMA_MACRO)

    # RSI
    df["rsi"] = rsi(df["close"], RSI_PERIOD)

    # ATR
    df["atr"] = atr(df)

    # Volumen medio
    df["vol_ma"] = df["volume"].rolling(VOL_MA_PERIOD).mean()

    # VWAP semanal
    df["vwap_w"] = vwap_weekly(df)

    return df
