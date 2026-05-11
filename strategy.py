"""
Strategy v4 — Fix crítico: HTF es solo bonus/penalización, nunca bloquea
Diagnóstico: el filtro HTF bloqueaba el 100% de señales válidas
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    action:    str
    price:     float
    ema1:      float
    ema2:      float
    ema3:      float
    rsi:       float
    adx:       float
    atr:       float
    atr_pct:   float
    volume_ok: bool
    reason:    str
    timestamp: str
    score:     float = 0.0


def _ema(s, p):   return s.ewm(span=p, adjust=False).mean()
def _co(a, b):    return (a.shift(1) <= b.shift(1)) & (a > b)
def _cu(a, b):    return (a.shift(1) >= b.shift(1)) & (a < b)

def _rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(com=p-1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=p-1, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _atr(df, p=14):
    h, lo, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h-lo, (h-pc).abs(), (lo-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, adjust=False).mean()

def _adx(df, p=14):
    h, lo, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr   = pd.concat([h-lo, (h-pc).abs(), (lo-pc).abs()], axis=1).max(axis=1)
    dmp  = (h - h.shift()).clip(lower=0)
    dmm  = (lo.shift() - lo).clip(lower=0)
    dmp  = dmp.where(dmp > dmm, 0)
    dmm  = dmm.where(dmm > dmp, 0)
    a14  = tr.ewm(com=p-1, adjust=False).mean().replace(0, np.nan)
    dip  = 100 * dmp.ewm(com=p-1, adjust=False).mean() / a14
    dim  = 100 * dmm.ewm(com=p-1, adjust=False).mean() / a14
    dx   = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dx.ewm(com=p-1, adjust=False).mean()


class EMAStrategy:
    def __init__(self, ma1=2, ma2=4, ma3=20, score_min=30):
        self.ma1 = ma1; self.ma2 = ma2; self.ma3 = ma3
        self.score_min = score_min
        logger.info(f"Strategy v4 | EMA={ma1}/{ma2}/{ma3} score_min={score_min}")

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = df["close"]
        df = df.copy()
        df["ema1"]   = _ema(p, self.ma1)
        df["ema2"]   = _ema(p, self.ma2)
        df["ema3"]   = _ema(p, self.ma3)
        df["rsi"]    = _rsi(p)
        df["adx"]    = _adx(df)
        df["atr"]    = _atr(df)
        df["vol_ma"] = df["volume"].rolling(20).mean()
        dp = p.diff(); d1 = df["ema1"].diff(); d2 = df["ema2"].diff()
        # Señales originales Pine Script — sin ningún filtro adicional
        df["raw_long"]  = _cu(p, df["ema3"]) | ((dp<0) & (d1<0) & _cu(p, df["ema1"]) & (d2>0))
        df["raw_short"] = _co(p, df["ema3"]) | ((dp>0) & (d1>0) & _co(p, df["ema1"]) & (d2<0))
        return df

    def _score(self, row, action: str, htf: str = "NEUTRAL") -> float:
        s = 40.0  # base: señal EMA disparada

        # ADX — confirmación de tendencia (nunca penaliza, solo suma)
        adx = float(row.get("adx", 0) or 0)
        if adx >= 25:   s += 25
        elif adx >= 18: s += 15
        elif adx >= 12: s += 7

        # RSI — momentum (penaliza extremos, nunca bloquea)
        rsi = float(row.get("rsi", 50) or 50)
        if action == "LONG":
            if 25 <= rsi <= 60: s += 20
            elif rsi < 25:      s += 10   # sobrevendido = ok para largo
            elif rsi > 70:      s -= 10   # sobrecomprado = cuidado
        else:
            if 40 <= rsi <= 75: s += 20
            elif rsi > 75:      s += 10
            elif rsi < 30:      s -= 10

        # Volumen — confirmación (nunca bloquea)
        vol    = float(row.get("volume", 0) or 0)
        vol_ma = float(row.get("vol_ma", 1) or 1)
        ratio  = vol / max(vol_ma, 1)
        if ratio >= 1.5:   s += 15
        elif ratio >= 1.0: s += 8
        elif ratio >= 0.5: s += 3

        # HTF — sesgo de tendencia (SOLO bonus/penalización, NUNCA bloquea)
        if action == "LONG"  and htf == "BULL": s += 20
        if action == "SHORT" and htf == "BEAR": s += 20
        if action == "LONG"  and htf == "BEAR": s -= 10   # penalización suave
        if action == "SHORT" and htf == "BULL": s -= 10

        return round(min(100, max(0, s)), 1)

    def _htf_bias(self, htf_df: Optional[pd.DataFrame]) -> str:
        if htf_df is None or len(htf_df) < self.ma3 + 5:
            return "NEUTRAL"
        try:
            htf = htf_df.copy()
            htf["ema1"] = _ema(htf["close"], self.ma1)
            htf["ema3"] = _ema(htf["close"], self.ma3)
            last = htf.iloc[-2]
            e1, e3 = float(last["ema1"]), float(last["ema3"])
            if e1 > e3 * 1.0003: return "BULL"
            if e1 < e3 * 0.9997: return "BEAR"
        except:
            pass
        return "NEUTRAL"

    def get_latest_signal(self, df: pd.DataFrame,
                          htf_df: Optional[pd.DataFrame] = None) -> Signal:
        df  = self.compute(df)
        row = df.iloc[-2]   # vela cerrada, no la viva
        htf = self._htf_bias(htf_df)
        price = float(row["close"])
        atr   = float(row["atr"]) if not pd.isna(row["atr"]) else price * 0.005

        base = dict(
            price=price,
            ema1=float(row["ema1"]), ema2=float(row["ema2"]), ema3=float(row["ema3"]),
            rsi=float(row["rsi"]) if not pd.isna(row["rsi"]) else 50,
            adx=float(row["adx"]) if not pd.isna(row["adx"]) else 0,
            atr=atr, atr_pct=atr / price * 100,
            volume_ok=float(row["volume"]) >= float(row.get("vol_ma", 0) or 0),
            timestamp=str(row.get("timestamp", "")),
        )

        if row["raw_long"]:
            sc = self._score(row, "LONG", htf)
            htf_tag = f" [15m:{htf}]" if htf != "NEUTRAL" else ""
            if sc >= self.score_min:
                return Signal(action="LONG",
                              reason=f"EMA cross↑ RSI={base['rsi']:.0f} ADX={base['adx']:.0f}{htf_tag}",
                              score=sc, **base)
            else:
                logger.debug(f"LONG raw pero score={sc} < {self.score_min} — descartada")

        if row["raw_short"]:
            sc = self._score(row, "SHORT", htf)
            htf_tag = f" [15m:{htf}]" if htf != "NEUTRAL" else ""
            if sc >= self.score_min:
                return Signal(action="SHORT",
                              reason=f"EMA cross↓ RSI={base['rsi']:.0f} ADX={base['adx']:.0f}{htf_tag}",
                              score=sc, **base)
            else:
                logger.debug(f"SHORT raw pero score={sc} < {self.score_min} — descartada")

        return Signal(action="HOLD", reason="Sin señal", score=0, **base)
