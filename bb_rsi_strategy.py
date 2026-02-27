"""
Estrategia #2: Bollinger Bands + RSI (Mean Reversion)
- LONG:  BB inferior rota + RSI < 30 + vela alcista cierra dentro de BB
- SHORT: BB superior rota + RSI > 70 + vela bajista cierra dentro de BB
- TP: BB media (SMA20)
- Ratio riesgo/beneficio: 0.75 para maximizar winrate
"""

import numpy as np
import pandas as pd
import logging
from strategies.vwap_strategy import Signal, TradeSignal, detect_reversal_candle

logger = logging.getLogger(__name__)


def compute_bollinger_bands(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0) -> pd.DataFrame:
    df = df.copy()
    df["bb_mid"] = df["close"].rolling(period).mean()
    bb_std = df["close"].rolling(period).std()
    df["bb_upper"] = df["bb_mid"] + (std_mult * bb_std)
    df["bb_lower"] = df["bb_mid"] - (std_mult * bb_std)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    return df


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    df = df.copy()
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"] - df["close"].shift(1))
        )
    )
    df["atr"] = df["tr"].ewm(span=period, adjust=False).mean()
    return df


class BBRSIMeanReversionStrategy:
    """
    Bollinger Bands (20, 2) + RSI (14) Mean Reversion
    Mejor rendimiento en mercados laterales y oscilantes.
    """

    def __init__(self, config: dict):
        self.bb_period = config.get("bb_period", 20)
        self.bb_std = config.get("bb_std", 2.0)
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_oversold = config.get("rsi_oversold", 30)
        self.rsi_overbought = config.get("rsi_overbought", 70)
        self.rr_ratio = config.get("rr_ratio", 0.75)  # Bulkowski style: 0.75 R:R = mayor winrate
        self.min_bb_width = config.get("min_bb_width", 0.005)  # Filtro de squeeze

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        if len(df) < self.bb_period + self.rsi_period:
            return TradeSignal(Signal.NONE, 0, 0, 0, 0, 0, "LOW", "Datos insuficientes")

        df = compute_bollinger_bands(df, self.bb_period, self.bb_std)
        df = compute_rsi(df, self.rsi_period)
        df = compute_atr(df)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = last["close"]
        rsi = last["rsi"]
        bb_upper = last["bb_upper"]
        bb_lower = last["bb_lower"]
        bb_mid = last["bb_mid"]    # TP objetivo
        atr = last["atr"]
        bb_width = last["bb_width"]

        if pd.isna(rsi) or pd.isna(bb_mid):
            return TradeSignal(Signal.NONE, 0, 0, 0, 0, 0, "LOW", "Indicadores sin calcular")

        # Filtro: BB Squeeze (mercado comprimido, esperar expansión)
        if bb_width < self.min_bb_width:
            return TradeSignal(Signal.NONE, 0, 0, 0, bb_mid, 0, "LOW", "BB Squeeze - sin señal")

        # ── CHECK LONG ──
        # Condición: precio cerró dentro de la banda inferior + RSI oversold
        if rsi < self.rsi_oversold and close < bb_lower:
            if detect_reversal_candle(last, "bullish"):
                potential_gain = bb_mid - close
                sl_distance = potential_gain / self.rr_ratio  # rr=0.75 → SL más ajustado
                sl = close - sl_distance
                # Validar que SL no sea absurdo
                sl = max(sl, last["low"] - atr * 0.3)

                confidence = "HIGH" if rsi < 20 else "MEDIUM"
                rr_real = potential_gain / (close - sl)

                return TradeSignal(
                    signal=Signal.LONG,
                    entry_price=close,
                    tp_price=round(bb_mid, 4),
                    sl_price=round(sl, 4),
                    vwap=round(bb_mid, 4),
                    deviation_band=2,
                    confidence=confidence,
                    reason=f"BB Lower Touch + RSI={round(rsi, 1)} (oversold). TP=BB Mid. R:R={round(rr_real, 2)}"
                )

        # ── CHECK SHORT ──
        # Condición: precio cerró dentro de la banda superior + RSI overbought
        if rsi > self.rsi_overbought and close > bb_upper:
            if detect_reversal_candle(last, "bearish"):
                potential_gain = close - bb_mid
                sl_distance = potential_gain / self.rr_ratio
                sl = close + sl_distance
                sl = min(sl, last["high"] + atr * 0.3)

                confidence = "HIGH" if rsi > 80 else "MEDIUM"
                rr_real = potential_gain / (sl - close)

                return TradeSignal(
                    signal=Signal.SHORT,
                    entry_price=close,
                    tp_price=round(bb_mid, 4),
                    sl_price=round(sl, 4),
                    vwap=round(bb_mid, 4),
                    deviation_band=2,
                    confidence=confidence,
                    reason=f"BB Upper Touch + RSI={round(rsi, 1)} (overbought). TP=BB Mid. R:R={round(rr_real, 2)}"
                )

        return TradeSignal(Signal.NONE, 0, 0, 0, round(bb_mid, 4), 0, "LOW", "Sin señal")


class EMARibbonScalpingStrategy:
    """
    Estrategia Scalping 5 Min: EMA 9/15 + VWAP + RSI
    - EMA9 > EMA15 = tendencia alcista → buscar LONG en pullbacks
    - EMA9 < EMA15 = tendencia bajista → buscar SHORT en pullbacks
    - MA200 como filtro macro de tendencia
    - RSI para confirmar momentum
    """

    def __init__(self, config: dict):
        self.ema_fast = config.get("ema_fast", 9)
        self.ema_slow = config.get("ema_slow", 15)
        self.ma_macro = config.get("ma_macro", 200)
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_bull_min = config.get("rsi_bull_min", 50)  # RSI > 50 para LONG
        self.rsi_bear_max = config.get("rsi_bear_max", 50)  # RSI < 50 para SHORT

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        if len(df) < self.ma_macro + 10:
            return TradeSignal(Signal.NONE, 0, 0, 0, 0, 0, "LOW", "Datos insuficientes para MA200")

        df = df.copy()
        df["ema_fast"] = df["close"].ewm(span=self.ema_fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.ema_slow, adjust=False).mean()
        df["ma200"] = df["close"].rolling(self.ma_macro).mean()
        df = compute_rsi(df, self.rsi_period)
        df = compute_atr(df)

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = last["close"]
        ema_fast = last["ema_fast"]
        ema_slow = last["ema_slow"]
        ma200 = last["ma200"]
        rsi = last["rsi"]
        atr = last["atr"]

        if pd.isna(ma200) or pd.isna(rsi):
            return TradeSignal(Signal.NONE, 0, 0, 0, 0, 0, "LOW", "Indicadores sin calcular")

        bullish_trend = ema_fast > ema_slow
        above_ma200 = close > ma200
        bearish_trend = ema_fast < ema_slow
        below_ma200 = close < ma200

        # ── SCALP LONG ──
        # EMA9 > EMA15 + precio > MA200 + precio tocó EMA9/EMA15 + RSI momentum
        if bullish_trend and above_ma200 and rsi > self.rsi_bull_min:
            # Pullback hacia EMA: precio toca/cruza EMA rápida
            touched_ema = prev["low"] <= ema_fast or (prev["close"] <= ema_fast and close > ema_fast)
            if touched_ema and detect_reversal_candle(last, "bullish"):
                sl = last["low"] - atr * 0.3
                tp = close + (atr * 2.0)  # 1:2 R:R en scalping
                rr = (tp - close) / (close - sl)

                confidence = "HIGH" if rsi > 60 and above_ma200 else "MEDIUM"
                return TradeSignal(
                    signal=Signal.LONG,
                    entry_price=close,
                    tp_price=round(tp, 4),
                    sl_price=round(sl, 4),
                    vwap=round(ema_slow, 4),
                    deviation_band=0,
                    confidence=confidence,
                    reason=f"EMA Scalp LONG | EMA9>{self.ema_fast} EMA15>{self.ema_slow} | RSI={round(rsi,1)} | sobre MA200 | R:R={round(rr,2)}"
                )

        # ── SCALP SHORT ──
        elif bearish_trend and below_ma200 and rsi < self.rsi_bear_max:
            touched_ema = prev["high"] >= ema_fast or (prev["close"] >= ema_fast and close < ema_fast)
            if touched_ema and detect_reversal_candle(last, "bearish"):
                sl = last["high"] + atr * 0.3
                tp = close - (atr * 2.0)
                rr = (close - tp) / (sl - close)

                confidence = "HIGH" if rsi < 40 and below_ma200 else "MEDIUM"
                return TradeSignal(
                    signal=Signal.SHORT,
                    entry_price=close,
                    tp_price=round(tp, 4),
                    sl_price=round(sl, 4),
                    vwap=round(ema_slow, 4),
                    deviation_band=0,
                    confidence=confidence,
                    reason=f"EMA Scalp SHORT | EMA9<EMA15 | RSI={round(rsi,1)} | bajo MA200 | R:R={round(rr,2)}"
                )

        return TradeSignal(Signal.NONE, 0, 0, 0, 0, 0, "LOW", "Sin señal de scalping")
