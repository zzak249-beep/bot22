"""
Estrategia #1: VWAP con Bandas de Desviación Estándar
Mean Reversion - señales SHORT en +2/+3 SD, LONG en -2/-3 SD
Objetivo: VWAP (fair value)
"""

import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Signal(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


@dataclass
class TradeSignal:
    signal: Signal
    entry_price: float
    tp_price: float
    sl_price: float
    vwap: float
    deviation_band: int  # 2 o 3
    confidence: str      # "HIGH" | "MEDIUM"
    reason: str


def compute_vwap_bands(df: pd.DataFrame, num_std: list = [1, 2, 3]) -> pd.DataFrame:
    """
    Calcula VWAP y sus bandas de desviación estándar.
    df debe tener: open, high, low, close, volume
    Reinicia por sesión (cada día).
    """
    df = df.copy()
    df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_volume"] = df["typical_price"] * df["volume"]

    # Acumulado por sesión (reinicia cada día)
    df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.date
    df["cum_tp_vol"] = df.groupby("date")["tp_volume"].cumsum()
    df["cum_vol"] = df.groupby("date")["volume"].cumsum()
    df["vwap"] = df["cum_tp_vol"] / df["cum_vol"]

    # Varianza acumulada para SD
    df["tp_vwap_diff_sq"] = (df["typical_price"] - df["vwap"]) ** 2
    df["cum_var"] = df.groupby("date")["tp_vwap_diff_sq"].cumsum()
    df["vwap_std"] = np.sqrt(df["cum_var"] / df.groupby("date").cumcount().add(1))

    for sd in num_std:
        df[f"vwap_upper_{sd}"] = df["vwap"] + (sd * df["vwap_std"])
        df[f"vwap_lower_{sd}"] = df["vwap"] - (sd * df["vwap_std"])

    return df


def detect_reversal_candle(row: pd.Series, direction: str) -> bool:
    """
    Detecta velas de reversión: pin bar, envolvente, shooting star.
    direction: 'bullish' | 'bearish'
    """
    body = abs(row["close"] - row["open"])
    total_range = row["high"] - row["low"]
    if total_range == 0:
        return False

    body_ratio = body / total_range
    upper_wick = row["high"] - max(row["open"], row["close"])
    lower_wick = min(row["open"], row["close"]) - row["low"]

    if direction == "bullish":
        # Mecha inferior larga = pin bar alcista
        wick_ratio = lower_wick / total_range if total_range > 0 else 0
        return wick_ratio > 0.55 or (row["close"] > row["open"] and body_ratio > 0.5)

    elif direction == "bearish":
        # Mecha superior larga = shooting star
        wick_ratio = upper_wick / total_range if total_range > 0 else 0
        return wick_ratio > 0.55 or (row["close"] < row["open"] and body_ratio > 0.5)

    return False


class VWAPMeanReversionStrategy:
    """
    Estrategia de reversión a la media basada en VWAP + Bandas SD
    - SHORT: precio toca/rompe +2SD o +3SD con vela bajista
    - LONG:  precio toca/rompe -2SD o -3SD con vela alcista
    - TP: VWAP (fair value)
    - SL: por encima/debajo de la vela de entrada
    """

    def __init__(self, config: dict):
        self.min_band = config.get("min_band", 2)          # Mínima banda SD para señal
        self.sl_multiplier = config.get("sl_multiplier", 1.5)  # ATR multiplicador para SL
        self.min_vwap_std = config.get("min_vwap_std", 0.001)  # Filtro: mínima volatilidad

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        """
        Analiza las últimas velas y retorna señal de trading.
        df: DataFrame con OHLCV + timestamps de BingX
        """
        if len(df) < 50:
            return TradeSignal(Signal.NONE, 0, 0, 0, 0, 0, "LOW", "Datos insuficientes")

        df = compute_vwap_bands(df)

        # Últimas 2 velas para confirmación
        last = df.iloc[-1]
        prev = df.iloc[-2]

        vwap = last["vwap"]
        close = last["close"]
        vwap_std = last["vwap_std"]

        # Filtro: bandas demasiado ajustadas (mercado sin movimiento)
        if vwap_std / vwap < self.min_vwap_std:
            return TradeSignal(Signal.NONE, 0, 0, 0, vwap, 0, "LOW", "Volatilidad insuficiente")

        # ATR aproximado para SL
        atr = df["high"].tail(14).values - df["low"].tail(14).values
        atr_val = np.mean(atr)

        # ── CHECK SHORT (precio en banda superior) ──
        for band in [3, 2]:
            upper = last[f"vwap_upper_{band}"]
            if close >= upper and band >= self.min_band:
                if detect_reversal_candle(last, "bearish"):
                    sl = last["high"] + (atr_val * 0.5)
                    tp = vwap  # Target: VWAP
                    rr = (close - tp) / (sl - close)
                    confidence = "HIGH" if band == 3 else "MEDIUM"
                    return TradeSignal(
                        signal=Signal.SHORT,
                        entry_price=close,
                        tp_price=round(tp, 4),
                        sl_price=round(sl, 4),
                        vwap=round(vwap, 4),
                        deviation_band=band,
                        confidence=confidence,
                        reason=f"Precio en +{band}SD VWAP ({round(upper, 4)}) con vela bajista. R:R={round(rr, 2)}"
                    )

        # ── CHECK LONG (precio en banda inferior) ──
        for band in [3, 2]:
            lower = last[f"vwap_lower_{band}"]
            if close <= lower and band >= self.min_band:
                if detect_reversal_candle(last, "bullish"):
                    sl = last["low"] - (atr_val * 0.5)
                    tp = vwap  # Target: VWAP
                    rr = (tp - close) / (close - sl)
                    confidence = "HIGH" if band == 3 else "MEDIUM"
                    return TradeSignal(
                        signal=Signal.LONG,
                        entry_price=close,
                        tp_price=round(tp, 4),
                        sl_price=round(sl, 4),
                        vwap=round(vwap, 4),
                        deviation_band=band,
                        confidence=confidence,
                        reason=f"Precio en -{band}SD VWAP ({round(lower, 4)}) con vela alcista. R:R={round(rr, 2)}"
                    )

        return TradeSignal(Signal.NONE, 0, 0, 0, round(vwap, 4), 0, "LOW", "Sin señal")
