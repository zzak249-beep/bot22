"""
strategy.py — Señales EMA Institutional Hunter 2H
              Fiel al Pine Script + mejoras: RSI, volumen, ATR mínimo

LÓGICA:
  LONG:
    1. Tendencia alcista:  EMA50 > EMA120 > EMA200
    2. Precio sobre estructura: close > EMA120 AND close > EMA200
    3. Pullback:  low <= EMA21 OR low <= EMA50
    4. Cruce micro:  EMA9 cruza arriba EMA21
    5. [MEJORA] RSI > RSI_BULL_MIN (no comprar en zona débil)
    6. [MEJORA] Volumen > media
    7. [MEJORA] SL mínimo de 0.5×ATR (evitar ruido)

  SHORT: espejo exacto

  SL / TP:
    LONG SL  = min(EMA50, low de la vela señal)
    SHORT SL = max(EMA50, high de la vela señal)
    TP1 = entry + risk × TP1_RR   (50% de la posición)
    TP2 = entry + risk × TP2_RR   (restante)
"""

from dataclasses import dataclass
from typing import Literal
import pandas as pd

import config as cfg


@dataclass
class Signal:
    direction : Literal["LONG", "SHORT"]
    entry     : float
    sl        : float
    tp1       : float
    tp2       : float
    risk_pct  : float          # riesgo % respecto al entry
    atr       : float
    bar_time  : pd.Timestamp

    def __str__(self):
        return (
            f"{self.direction}  entry={self.entry:.4f}  "
            f"SL={self.sl:.4f}  TP1={self.tp1:.4f}  TP2={self.tp2:.4f}  "
            f"riesgo={self.risk_pct:.2f}%"
        )


def _crossover(s1: pd.Series, s2: pd.Series) -> pd.Series:
    """True en la vela donde s1 cruza por encima de s2."""
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))


def _crossunder(s1: pd.Series, s2: pd.Series) -> pd.Series:
    return (s1 < s2) & (s1.shift(1) >= s2.shift(1))


def detect(df: pd.DataFrame) -> Signal | None:
    """
    Evalúa la última vela CERRADA (índice -2) para evitar
    señales en vela aún abierta.
    Retorna Signal o None.
    """
    if len(df) < cfg.EMA_MACRO + 10:
        return None

    # Trabajamos sobre penúltima vela (la última cerrada)
    i  = -2
    c  = df.iloc[i]
    cp = df.iloc[i - 1]   # vela anterior (para cruce)

    # ─── Alias ──────────────────────────────────────────────
    close   = c["close"]
    low     = c["low"]
    high    = c["high"]
    e9      = c["ema9"];   e9p  = cp["ema9"]
    e21     = c["ema21"];  e21p = cp["ema21"]
    e50     = c["ema50"]
    e120    = c["ema120"]
    e200    = c["ema200"]
    rsi_val = c["rsi"]
    vol     = c["volume"]
    vol_ma  = c["vol_ma"]
    atr_val = c["atr"]
    bar_ts  = df.index[i]

    # ─── Tendencia ───────────────────────────────────────────
    bull_trend = e50 > e120 > e200
    bear_trend = e50 < e120 < e200

    # ─── Estructura ──────────────────────────────────────────
    above_struct = close > e120 and close > e200
    below_struct = close < e120 and close < e200

    # ─── Pullback ────────────────────────────────────────────
    pb_long  = low  <= e21 or low  <= e50
    pb_short = high >= e21 or high >= e50

    # ─── Cruce micro EMA9/21 ─────────────────────────────────
    cross_long  = (e9 > e21) and (e9p <= e21p)
    cross_short = (e9 < e21) and (e9p >= e21p)

    # ─── Filtros extra ───────────────────────────────────────
    vol_ok      = vol > vol_ma if vol_ma > 0 else True
    rsi_bull_ok = rsi_val > cfg.RSI_BULL_MIN
    rsi_bear_ok = rsi_val < cfg.RSI_BEAR_MAX

    # ─── LONG ────────────────────────────────────────────────
    if (bull_trend and above_struct and pb_long and
            cross_long and rsi_bull_ok and vol_ok):

        sl    = min(e50, low) * (1 - 0.0005)   # pequeño buffer anti-wick
        risk  = close - sl
        min_r = atr_val * cfg.MIN_ATR_MULT

        if risk < min_r:                        # SL demasiado estrecho
            sl   = close - min_r
            risk = min_r

        if risk <= 0:
            return None

        tp1 = close + risk * cfg.TP1_RR
        tp2 = close + risk * cfg.TP2_RR

        return Signal(
            direction="LONG", entry=close,
            sl=sl, tp1=tp1, tp2=tp2,
            risk_pct=(risk / close) * 100,
            atr=atr_val, bar_time=bar_ts,
        )

    # ─── SHORT ───────────────────────────────────────────────
    if (bear_trend and below_struct and pb_short and
            cross_short and rsi_bear_ok and vol_ok):

        sl    = max(e50, high) * (1 + 0.0005)
        risk  = sl - close
        min_r = atr_val * cfg.MIN_ATR_MULT

        if risk < min_r:
            sl   = close + min_r
            risk = min_r

        if risk <= 0:
            return None

        tp1 = close - risk * cfg.TP1_RR
        tp2 = close - risk * cfg.TP2_RR

        return Signal(
            direction="SHORT", entry=close,
            sl=sl, tp1=tp1, tp2=tp2,
            risk_pct=(risk / close) * 100,
            atr=atr_val, bar_time=bar_ts,
        )

    return None
