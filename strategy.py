"""
EMA Slope + EMA Cross Strategy
Ported from Pine Script v3 by ChartArt
3-minute timeframe implementation
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    action: str          # "LONG", "SHORT", "HOLD"
    price: float
    ema1: float
    ema2: float
    ema3: float
    reason: str
    timestamp: str


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average"""
    return series.ewm(span=period, adjust=False).mean()


def crossover(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """True when series_a crosses ABOVE series_b"""
    prev_a = series_a.shift(1)
    prev_b = series_b.shift(1)
    return (prev_a <= prev_b) & (series_a > series_b)


def crossunder(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """True when series_a crosses BELOW series_b"""
    prev_a = series_a.shift(1)
    prev_b = series_b.shift(1)
    return (prev_a >= prev_b) & (series_a < series_b)


def change(series: pd.Series) -> pd.Series:
    """Difference from previous bar"""
    return series.diff(1)


class EMAStrategy:
    def __init__(self, ma1_length: int = 2, ma2_length: int = 4, ma3_length: int = 20):
        self.ma1_length = ma1_length
        self.ma2_length = ma2_length
        self.ma3_length = ma3_length
        logger.info(
            f"Strategy initialized | EMA1={ma1_length} EMA2={ma2_length} EMA3={ma3_length}"
        )

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute all indicators and signals on OHLCV DataFrame.
        Requires columns: open, high, low, close, volume, timestamp
        """
        if len(df) < self.ma3_length + 5:
            raise ValueError(f"Not enough candles. Need at least {self.ma3_length + 5}, got {len(df)}")

        price = df["close"]

        df["ema1"] = ema(price, self.ma1_length)
        df["ema2"] = ema(price, self.ma2_length)
        df["ema3"] = ema(price, self.ma3_length)

        df["d_price"] = change(price)
        df["d_ema1"]  = change(df["ema1"])
        df["d_ema2"]  = change(df["ema2"])
        df["d_ema3"]  = change(df["ema3"])

        # ── Entry conditions (mirrors Pine Script exactly) ──────────────────
        # LONG: price crosses under EMA3  OR
        #       (price falling & EMA1 falling & price crossunder EMA1 & EMA2 rising)
        co_price_ma3  = crossunder(price, df["ema3"])
        cu_price_ma1  = crossunder(price, df["ema1"])

        cond_long_a = co_price_ma3
        cond_long_b = (
            (df["d_price"] < 0) &
            (df["d_ema1"]  < 0) &
            cu_price_ma1 &
            (df["d_ema2"]  > 0)
        )
        df["signal_long"]  = cond_long_a | cond_long_b

        # SHORT: price crosses over EMA3  OR
        #        (price rising & EMA1 rising & price crossover EMA1 & EMA2 falling)
        co_price_ma3_up = crossover(price, df["ema3"])
        co_price_ma1    = crossover(price, df["ema1"])

        cond_short_a = co_price_ma3_up
        cond_short_b = (
            (df["d_price"] > 0) &
            (df["d_ema1"]  > 0) &
            co_price_ma1 &
            (df["d_ema2"]  < 0)
        )
        df["signal_short"] = cond_short_a | cond_short_b

        # ── Bar color trend (informational) ────────────────────────────────
        df["trend_up"] = (df["d_ema2"] > 0) & (df["d_ema3"] > 0)
        df["trend_dn"] = (df["d_ema2"] < 0) & (df["d_ema3"] < 0)

        return df

    def get_latest_signal(self, df: pd.DataFrame) -> Signal:
        """
        Return the signal for the most recently CLOSED candle (index -2).
        We never act on the live / unclosed candle.
        """
        df = self.compute(df.copy())

        # Use the last CLOSED candle (-2) to avoid repainting
        row = df.iloc[-2]
        price_val = float(row["close"])
        ts = str(row["timestamp"]) if "timestamp" in row else "N/A"

        if row["signal_long"]:
            reason = "Price crossunder EMA3" if (
                float(df["close"].iloc[-3]) >= float(df["ema3"].iloc[-3])
            ) else "Price/EMA1 crossunder with EMA2 slope up"
            return Signal("LONG",  price_val,
                          float(row["ema1"]), float(row["ema2"]), float(row["ema3"]),
                          reason, ts)

        if row["signal_short"]:
            reason = "Price crossover EMA3" if (
                float(df["close"].iloc[-3]) <= float(df["ema3"].iloc[-3])
            ) else "Price/EMA1 crossover with EMA2 slope down"
            return Signal("SHORT", price_val,
                          float(row["ema1"]), float(row["ema2"]), float(row["ema3"]),
                          reason, ts)

        return Signal("HOLD", price_val,
                      float(row["ema1"]), float(row["ema2"]), float(row["ema3"]),
                      "No signal", ts)
