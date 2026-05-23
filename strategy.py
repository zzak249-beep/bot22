"""
EMA Strategy — cruces EMA1/EMA2 con filtro HTF EMA3
Indicadores: EMA, RSI, ADX, ATR
"""
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    action:     str       # LONG | SHORT | HOLD
    price:      float
    ema1:       float
    ema2:       float
    ema3:       float
    rsi:        float
    adx:        float
    atr:        float
    atr_pct:    float
    volume_ok:  bool
    reason:     str
    timestamp:  str
    score:      float


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l = loss.ewm(com=period - 1, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_high = high.shift(1)
    prev_low  = low.shift(1)
    prev_close = close.shift(1)

    up_move   = high - prev_high
    down_move = prev_low - low
    plus_dm   = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_s  = tr.ewm(com=period - 1, adjust=False).mean()
    plus_s = pd.Series(plus_dm,  index=df.index).ewm(com=period - 1, adjust=False).mean()
    minus_s= pd.Series(minus_dm, index=df.index).ewm(com=period - 1, adjust=False).mean()

    dip  = 100 * plus_s  / atr_s.replace(0, np.nan)
    dim  = 100 * minus_s / atr_s.replace(0, np.nan)
    dx   = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dx.ewm(com=period - 1, adjust=False).mean()


class EMAStrategy:
    def __init__(self, ema1_len=2, ema2_len=4, ema3_len=20, score_min=30.0,
                 rsi_period=14, adx_period=14, atr_period=14):
        self.ema1_len   = ema1_len
        self.ema2_len   = ema2_len
        self.ema3_len   = ema3_len
        self.score_min  = score_min
        self.rsi_period = rsi_period
        self.adx_period = adx_period
        self.atr_period = atr_period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Añade columnas de indicadores al DataFrame."""
        df = df.copy()
        df["ema1"] = _ema(df["close"], self.ema1_len)
        df["ema2"] = _ema(df["close"], self.ema2_len)
        df["ema3"] = _ema(df["close"], self.ema3_len)
        df["rsi"]  = _rsi(df["close"], self.rsi_period)
        df["adx"]  = _adx(df, self.adx_period)
        df["atr"]  = _atr(df, self.atr_period)
        return df

    def get_latest_signal(self, df: pd.DataFrame,
                          htf_df: Optional[pd.DataFrame] = None) -> Signal:
        df = self.compute(df)
        i  = df.iloc[-1]
        prev = df.iloc[-2]

        price   = float(i["close"])
        ema1    = float(i["ema1"])
        ema2    = float(i["ema2"])
        ema3    = float(i["ema3"])
        rsi     = float(i["rsi"])
        adx     = float(i["adx"])
        atr     = float(i["atr"])
        atr_pct = (atr / price * 100) if price > 0 else 0

        # Cruce EMA1/EMA2
        cross_up   = (prev["ema1"] <= prev["ema2"]) and (ema1 > ema2)
        cross_down = (prev["ema1"] >= prev["ema2"]) and (ema1 < ema2)

        # HTF bias
        htf_bull = htf_bear = False
        if htf_df is not None and len(htf_df) >= self.ema3_len:
            htf_ema1 = float(_ema(htf_df["close"], self.ema1_len).iloc[-1])
            htf_ema2 = float(_ema(htf_df["close"], self.ema2_len).iloc[-1])
            htf_ema3 = float(_ema(htf_df["close"], self.ema3_len).iloc[-1])
            htf_price = float(htf_df["close"].iloc[-1])
            htf_bull = htf_price > htf_ema3 and htf_ema1 > htf_ema2
            htf_bear = htf_price < htf_ema3 and htf_ema1 < htf_ema2

        # Scoring
        score   = 0.0
        reasons = []

        if cross_up:
            score += 35; reasons.append("EMA cruce↑")
        elif ema1 > ema2:
            score += 15; reasons.append("EMA alcista")

        if cross_down:
            score += 35; reasons.append("EMA cruce↓")
        elif ema1 < ema2:
            score += 15; reasons.append("EMA bajista")

        if price > ema3:
            score += 10; reasons.append("P>EMA3")
        elif price < ema3:
            score += 10; reasons.append("P<EMA3")

        if 40 < rsi < 65:
            score += 15; reasons.append(f"RSI{rsi:.0f}")
        if adx > 20:
            score += 10; reasons.append(f"ADX{adx:.0f}")

        if htf_bull and ema1 > ema2:
            score += 15; reasons.append("HTF🟢")
        elif htf_bear and ema1 < ema2:
            score += 15; reasons.append("HTF🔴")

        # Dirección
        if ema1 > ema2 and price > ema3:
            action = "LONG"
        elif ema1 < ema2 and price < ema3:
            action = "SHORT"
        else:
            action = "HOLD"

        if score < self.score_min:
            action = "HOLD"

        return Signal(
            action    = action,
            price     = price,
            ema1      = ema1,
            ema2      = ema2,
            ema3      = ema3,
            rsi       = rsi,
            adx       = adx,
            atr       = atr,
            atr_pct   = atr_pct,
            volume_ok = True,
            reason    = " | ".join(reasons) if reasons else "Sin señal",
            timestamp = str(i["timestamp"]),
            score     = round(score, 1),
        )
