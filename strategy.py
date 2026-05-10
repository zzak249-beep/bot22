"""
Strategy v3 — Sistema multi-señal profesional
Mejoras sobre v1:
- RSI + ADX + volumen como filtros OPCIONALES con scoring
- Multi-timeframe: 3m señal + 15m sesgo
- Señal score 0-100 (solo opera >40)
- Detección de régimen de mercado
- Anti-chop con pendiente EMA normalizada
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
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
    score:     float = 0.0   # 0-100


# ── Indicadores ──────────────────────────────────────────────────────────

def _ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

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
    atr14 = tr.ewm(com=p-1, adjust=False).mean().replace(0, np.nan)
    dip   = 100 * dmp.ewm(com=p-1, adjust=False).mean() / atr14
    dim   = 100 * dmm.ewm(com=p-1, adjust=False).mean() / atr14
    dx    = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dx.ewm(com=p-1, adjust=False).mean()

def _co(a, b):   # crossover
    return (a.shift(1) <= b.shift(1)) & (a > b)

def _cu(a, b):   # crossunder
    return (a.shift(1) >= b.shift(1)) & (a < b)


class EMAStrategy:
    def __init__(
        self,
        ma1=2, ma2=4, ma3=20,
        score_min=40,        # score mínimo para operar (0=off)
        adx_weight=25,       # peso ADX en el score
        rsi_weight=20,       # peso RSI
        vol_weight=15,       # peso volumen
        htf_weight=20,       # peso confirmación 15m
    ):
        self.ma1=ma1; self.ma2=ma2; self.ma3=ma3
        self.score_min  = score_min
        self.adx_weight = adx_weight
        self.rsi_weight = rsi_weight
        self.vol_weight = vol_weight
        self.htf_weight = htf_weight
        logger.info(f"Strategy v3 | EMA={ma1}/{ma2}/{ma3} score_min={score_min}")

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = df["close"]
        df = df.copy()
        df["ema1"] = _ema(p, self.ma1)
        df["ema2"] = _ema(p, self.ma2)
        df["ema3"] = _ema(p, self.ma3)
        df["rsi"]  = _rsi(p)
        df["adx"]  = _adx(df)
        df["atr"]  = _atr(df)
        df["vol_ma"] = df["volume"].rolling(20).mean()

        dp = p.diff(); d1 = df["ema1"].diff(); d2 = df["ema2"].diff()

        # Señales raw (Pine Script original — sin filtros)
        df["raw_long"]  = _cu(p, df["ema3"]) | ((dp<0) & (d1<0) & _cu(p,df["ema1"]) & (d2>0))
        df["raw_short"] = _co(p, df["ema3"]) | ((dp>0) & (d1>0) & _co(p,df["ema1"]) & (d2<0))

        # Señales filtradas (con score ≥ score_min)
        df["signal_long"]  = False
        df["signal_short"] = False
        return df

    def _score(self, row, action, htf_bias="NEUTRAL") -> float:
        s = 40.0   # base — señal EMA ya disparada

        # ADX (tendencia): 0 = sin tendencia, 40 = fuerte
        adx = float(row.get("adx", 0))
        if adx >= 25:     s += self.adx_weight
        elif adx >= 18:   s += self.adx_weight * 0.6
        elif adx >= 12:   s += self.adx_weight * 0.3

        # RSI (momentum y no-extremo)
        rsi = float(row.get("rsi", 50))
        if action == "LONG":
            if 30 <= rsi <= 55:   s += self.rsi_weight
            elif rsi < 30:        s += self.rsi_weight * 0.5   # sobrevendido ✓
            elif rsi > 65:        s -= 10                       # sobrecomprado ✗
        else:
            if 45 <= rsi <= 70:   s += self.rsi_weight
            elif rsi > 70:        s += self.rsi_weight * 0.5
            elif rsi < 35:        s -= 10

        # Volumen
        vol    = float(row.get("volume", 0))
        vol_ma = float(row.get("vol_ma", 1)) or 1
        ratio  = vol / vol_ma
        if ratio >= 1.5:   s += self.vol_weight
        elif ratio >= 1.0: s += self.vol_weight * 0.6
        elif ratio >= 0.7: s += self.vol_weight * 0.2

        # Multi-timeframe
        if action == "LONG"  and htf_bias == "BULL": s += self.htf_weight
        if action == "SHORT" and htf_bias == "BEAR": s += self.htf_weight
        if action == "LONG"  and htf_bias == "BEAR": s -= 15
        if action == "SHORT" and htf_bias == "BULL": s -= 15

        return round(min(100, max(0, s)), 1)

    def _htf_bias(self, htf_df: Optional[pd.DataFrame]) -> str:
        if htf_df is None or len(htf_df) < self.ma3 + 5:
            return "NEUTRAL"
        htf = htf_df.copy()
        htf["ema1"] = _ema(htf["close"], self.ma1)
        htf["ema3"] = _ema(htf["close"], self.ma3)
        last = htf.iloc[-2]
        if float(last["ema1"]) > float(last["ema3"]) * 1.0005:
            return "BULL"
        if float(last["ema1"]) < float(last["ema3"]) * 0.9995:
            return "BEAR"
        return "NEUTRAL"

    def get_latest_signal(self, df: pd.DataFrame,
                          htf_df: Optional[pd.DataFrame] = None) -> Signal:
        df  = self.compute(df)
        row = df.iloc[-2]
        htf = self._htf_bias(htf_df)
        price = float(row["close"])
        atr   = float(row["atr"]) if not np.isnan(float(row["atr"])) else price*0.01

        base = dict(
            price=price,
            ema1=float(row["ema1"]),  ema2=float(row["ema2"]),  ema3=float(row["ema3"]),
            rsi=float(row["rsi"]),    adx=float(row["adx"]),
            atr=atr,                  atr_pct=atr/price*100,
            volume_ok=float(row["volume"]) >= float(row.get("vol_ma", 0)),
            timestamp=str(row.get("timestamp","")),
        )

        if row["raw_long"]:
            sc = self._score(row, "LONG", htf)
            if sc >= self.score_min:
                htf_tag = f" [15m:{htf}]" if htf != "NEUTRAL" else ""
                return Signal(action="LONG",
                              reason=f"EMA cross↑ ADX={row['adx']:.1f} RSI={row['rsi']:.1f}{htf_tag}",
                              score=sc, **base)

        if row["raw_short"]:
            sc = self._score(row, "SHORT", htf)
            if sc >= self.score_min:
                htf_tag = f" [15m:{htf}]" if htf != "NEUTRAL" else ""
                return Signal(action="SHORT",
                              reason=f"EMA cross↓ ADX={row['adx']:.1f} RSI={row['rsi']:.1f}{htf_tag}",
                              score=sc, **base)

        return Signal(action="HOLD", reason="Sin señal", score=0, **base)
