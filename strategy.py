"""
Sniper Entry/Exit Strategy - KhanSaab V.02
Translated from Pine Script to Python
"""
import pandas as pd
import numpy as np


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    tr = atr(high, low, close, period)
    plus_dm_s = pd.Series(plus_dm, index=high.index).ewm(alpha=1/period, adjust=False).mean()
    minus_dm_s = pd.Series(minus_dm, index=high.index).ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * plus_dm_s / tr
    minus_di = 100 * minus_dm_s / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False).mean()


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    hlc3 = (high + low + close) / 3
    cumulative_vol = volume.cumsum()
    cumulative_tp_vol = (hlc3 * volume).cumsum()
    return cumulative_tp_vol / cumulative_vol


def compute_scores(df: pd.DataFrame, rsi_5m: pd.Series = None) -> dict:
    """
    Compute bull/bear scores exactly as in Pine Script.
    df must have: open, high, low, close, volume
    """
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']

    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    vwap_val = vwap(high, low, close, volume)
    atr_val = atr(high, low, close, 14)
    rsi_val = rsi(close, 14)
    macd_m, macd_s, _ = macd(close)
    adx_val = adx(high, low, close, 14)
    vol_avg = volume.rolling(20).mean()

    rsi5m = rsi_5m if rsi_5m is not None else rsi_val  # fallback

    # Last bar values
    i = -1
    c = close.iloc[i]
    o = df['open'].iloc[i]
    v = volume.iloc[i]

    bull = 0
    bull += 1 if c > vwap_val.iloc[i] else 0
    bull += 1 if rsi_val.iloc[i] > 50 else 0
    bull += 1 if macd_m.iloc[i] > macd_s.iloc[i] else 0
    bull += 1 if ema9.iloc[i] > ema21.iloc[i] else 0
    bull += 1 if adx_val.iloc[i] > 25 and c > ema9.iloc[i] else 0
    bull += 1 if v > vol_avg.iloc[i] and c > o else 0
    bull += 1 if rsi5m.iloc[i] > 50 else 0

    bear = 0
    bear += 1 if c < vwap_val.iloc[i] else 0
    bear += 1 if rsi_val.iloc[i] < 50 else 0
    bear += 1 if macd_m.iloc[i] < macd_s.iloc[i] else 0
    bear += 1 if ema9.iloc[i] < ema21.iloc[i] else 0
    bear += 1 if adx_val.iloc[i] > 25 and c < ema9.iloc[i] else 0
    bear += 1 if v > vol_avg.iloc[i] and c < o else 0
    bear += 1 if rsi5m.iloc[i] < 50 else 0

    bull_pct = (bull / 7) * 100
    bear_pct = (bear / 7) * 100

    diff = bull_pct - bear_pct
    if diff >= 40:
        bias = "STRONG BULL"
    elif -diff >= 40:
        bias = "STRONG BEAR"
    elif bull_pct > bear_pct:
        bias = "MILD BULL"
    else:
        bias = "MILD BEAR"

    # EMA cross signal
    ema9_prev = ema9.iloc[-2]
    ema21_prev = ema21.iloc[-2]
    ema9_curr = ema9.iloc[-1]
    ema21_curr = ema21.iloc[-1]

    buy_signal = (ema9_prev <= ema21_prev) and (ema9_curr > ema21_curr)
    sell_signal = (ema9_prev >= ema21_prev) and (ema9_curr < ema21_curr)

    atr_now = atr_val.iloc[-1]

    return {
        "close": c,
        "ema9": ema9_curr,
        "ema21": ema21_curr,
        "vwap": vwap_val.iloc[i],
        "rsi": rsi_val.iloc[i],
        "macd_main": macd_m.iloc[i],
        "macd_signal": macd_s.iloc[i],
        "adx": adx_val.iloc[i],
        "atr": atr_now,
        "volume": v,
        "vol_avg": vol_avg.iloc[i],
        "bull_pct": bull_pct,
        "bear_pct": bear_pct,
        "bias": bias,
        "buy_signal": buy_signal,
        "sell_signal": sell_signal,
        "rsi_5m": rsi5m.iloc[i],
    }


def compute_trade_levels(entry: float, atr_val: float, direction: str, multiplier: float = 1.5) -> dict:
    risk = atr_val * multiplier
    is_long = direction == "BUY"
    sl = entry - risk if is_long else entry + risk
    targets = {}
    for i in range(1, 6):
        targets[f"tp{i}"] = entry + (risk * i) if is_long else entry - (risk * i)
    return {"entry": entry, "sl": sl, "atr": atr_val, "risk": risk, **targets}
