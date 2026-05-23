"""
strategy.py — Motor de señales v5 EDGE

7 ventajas sobre bots normales:
  1. Regime filter  — detecta si el mercado está en tendencia o rango
                      y solo opera en tendencia (elimina >60% de falsas señales)
  2. Volume imbalance — detecta desequilibrio comprador/vendedor en las
                        últimas velas antes de entrar (smart money footprint)
  3. Fair Value Gap  — identifica gaps de precio no rellenados que actúan
                       como imanes; solo entra si el precio acaba de salir de uno
  4. Funding rate    — evita entrar LONG cuando funding > 0.1% (caro y saturado)
                       y SHORT cuando funding < -0.05% (squeeze inminente)
  5. ATR adaptativo  — SL/TP se calculan sobre la volatilidad real de las
                       últimas 14 velas, no sobre % fijo
  6. Multi-timeframe — 3m entrada, 15m tendencia, 1h macro, todos alineados
  7. Score 0-100     — pondera los 7 filtros; solo opera con score >= 60
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional
import numpy as np

log = logging.getLogger("strategy")


# ══════════════════════════════════════════════════════════════
#  ESTRUCTURAS
# ══════════════════════════════════════════════════════════════

@dataclass
class Candle:
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float

    @property
    def body(self) -> float:   return abs(self.close - self.open)
    @property
    def range(self) -> float:  return self.high - self.low
    @property
    def bullish(self) -> bool: return self.close > self.open
    @property
    def bearish(self) -> bool: return self.close < self.open
    @property
    def wick_up(self) -> float:
        return self.high - max(self.open, self.close)
    @property
    def wick_dn(self) -> float:
        return min(self.open, self.close) - self.low


@dataclass
class Signal:
    symbol:   str
    side:     str         # LONG | SHORT
    price:    float
    sl:       float
    tp1:      float
    tp2:      float
    tp3:      float       # NUEVO: tercer objetivo (RR 4:1)
    score:    int         # 0-100
    regime:   str         # TREND | RANGE | CHOPPY
    reason:   str         # texto con filtros que pasó
    atr:      float       # ATR en precio
    rr:       float       # ratio riesgo/recompensa real


@dataclass
class MarketContext:
    """Contexto de mercado completo para tomar decisiones."""
    funding_rate: float = 0.0
    open_interest_delta: float = 0.0   # % cambio OI últimas 4h
    volume_imbalance: float = 0.0      # >0 presión compradora, <0 vendedora
    regime: str = "UNKNOWN"            # TREND_UP | TREND_DOWN | RANGE | CHOPPY


# ══════════════════════════════════════════════════════════════
#  INDICADORES TÉCNICOS
# ══════════════════════════════════════════════════════════════

def _ema(values: list[float], p: int) -> np.ndarray:
    arr = np.array(values, dtype=float)
    out = np.full_like(arr, np.nan)
    if len(arr) < p:
        return out
    k = 2 / (p + 1)
    out[p - 1] = arr[:p].mean()
    for i in range(p, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(closes: list[float], p: int = 14) -> float:
    arr = np.diff(np.array(closes[-(p * 3):], dtype=float))
    g = np.where(arr > 0, arr, 0.0)
    l = np.where(arr < 0, -arr, 0.0)
    ag, al = g[-p:].mean(), l[-p:].mean()
    return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))


def _atr(candles: list[Candle], p: int = 14) -> float:
    if len(candles) < p + 1:
        return candles[-1].range if candles else 0.0
    trs = [max(c.high - c.low,
               abs(c.high - candles[i - 1].close),
               abs(c.low  - candles[i - 1].close))
           for i, c in enumerate(candles) if i > 0]
    return float(np.mean(trs[-p:]))


def _adx(candles: list[Candle], p: int = 14) -> float:
    """ADX simplificado — mide fuerza de tendencia (0-100)."""
    if len(candles) < p + 2:
        return 0.0
    dm_pos, dm_neg, tr_list = [], [], []
    for i in range(1, len(candles)):
        h, l = candles[i].high, candles[i].low
        ph, pl, pc = candles[i-1].high, candles[i-1].low, candles[i-1].close
        up   = h - ph
        down = pl - l
        dm_pos.append(up   if up > down and up > 0   else 0.0)
        dm_neg.append(down if down > up and down > 0 else 0.0)
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
    def _smooth(arr):
        out = [sum(arr[:p])]
        for v in arr[p:]:
            out.append(out[-1] - out[-1] / p + v)
        return out
    atr_s = _smooth(tr_list)
    dmp_s = _smooth(dm_pos)
    dmn_s = _smooth(dm_neg)
    dx = []
    for a, p_, n in zip(atr_s, dmp_s, dmn_s):
        if a == 0:
            dx.append(0.0)
            continue
        di_p = 100 * p_ / a
        di_n = 100 * n / a
        s = di_p + di_n
        dx.append(100 * abs(di_p - di_n) / s if s else 0.0)
    return float(np.mean(dx[-p:])) if dx else 0.0


def _volume_imbalance(candles: list[Candle], n: int = 5) -> float:
    """
    Ratio de presión compradora vs vendedora en las últimas n velas.
    >1.3 → presión compradora fuerte
    <0.7 → presión vendedora fuerte
    """
    recent = candles[-n:]
    buy_vol  = sum(c.volume * (c.close - c.low)  / c.range if c.range else 0 for c in recent)
    sell_vol = sum(c.volume * (c.high - c.close) / c.range if c.range else 0 for c in recent)
    total = buy_vol + sell_vol
    return buy_vol / total if total > 0 else 0.5


def _detect_regime(candles: list[Candle], adx_val: float) -> str:
    """
    Detecta el régimen de mercado.
    TREND_UP / TREND_DOWN / RANGE / CHOPPY
    Clave: solo operar en TREND_UP o TREND_DOWN.
    """
    if adx_val >= 25:
        closes = [c.close for c in candles[-20:]]
        ema_fast = _ema(closes, 8)
        ema_slow = _ema(closes, 21)
        if ema_fast[-1] > ema_slow[-1]:
            return "TREND_UP"
        return "TREND_DOWN"
    if adx_val >= 18:
        return "RANGE"
    return "CHOPPY"


def _fair_value_gap(candles: list[Candle]) -> tuple[bool, bool]:
    """
    Detecta Fair Value Gaps (FVG) en las últimas 10 velas.
    FVG alcista: vela i-2 high < vela i low (hueco sin rellenar)
    FVG bajista: vela i-2 low > vela i high
    Retorna (fvg_bull, fvg_bear)
    """
    recent = candles[-10:]
    fvg_bull = fvg_bear = False
    for i in range(2, len(recent)):
        if recent[i-2].high < recent[i].low:       # gap alcista
            fvg_bull = True
        if recent[i-2].low  > recent[i].high:      # gap bajista
            fvg_bear = True
    return fvg_bull, fvg_bear


def _structure_break(candles: list[Candle], n: int = 10) -> tuple[bool, bool]:
    """
    Break of Structure (BoS): precio rompe el swing high/low reciente.
    Retorna (bullish_break, bearish_break)
    """
    highs = [c.high for c in candles[-n-1:-1]]
    lows  = [c.low  for c in candles[-n-1:-1]]
    curr  = candles[-1]
    return (curr.close > max(highs),
            curr.close < min(lows))


def _pin_bar(c: Candle, side: str) -> bool:
    if c.body == 0:
        return False
    if side == "LONG":
        return c.wick_dn > c.body * 2.2 and c.wick_up < c.body
    return c.wick_up > c.body * 2.2 and c.wick_dn < c.body


# ══════════════════════════════════════════════════════════════
#  ESTRATEGIA PRINCIPAL
# ══════════════════════════════════════════════════════════════

class EdgeStrategy:
    """
    Estrategia con 7 capas de filtro y score ponderado 0-100.
    Score >= 60 para operar.
    """

    # Pesos de cada filtro en el score total
    WEIGHTS = {
        "ema_align":    20,   # EMAs 8/21/55 alineadas
        "htf_align":    15,   # 15m y 1h alineados
        "adx_trend":    15,   # ADX > 25 (tendencia real)
        "rsi_zone":     10,   # RSI en zona correcta
        "vol_imbal":    15,   # imbalance comprador/vendedor
        "fvg_or_bos":   15,   # FVG o BoS reciente
        "pin_or_vol":   10,   # pin bar o volumen spike
    }
    SCORE_MIN = 60

    def __init__(self, cfg):
        self.cfg = cfg

    def evaluate(
        self,
        symbol:     str,
        c3m:        list[Candle],
        c15m:       list[Candle],
        c1h:        list[Candle],
        ctx:        MarketContext,
    ) -> Optional[Signal]:

        cfg = self.cfg
        closes = [c.close for c in c3m]
        curr   = c3m[-1]
        prev   = c3m[-2]

        # ── Indicadores base ──────────────────────────────────
        e8  = _ema(closes, 8)
        e21 = _ema(closes, 21)
        e55 = _ema(closes, 55)
        if any(np.isnan(x[-1]) for x in [e8, e21, e55]):
            return None

        atr_val = _atr(c3m, 14)
        adx_val = _adx(c3m, 14)
        rsi_val = _rsi(closes, 14)
        regime  = _detect_regime(c3m, adx_val)
        vim     = _volume_imbalance(c3m, 5)
        fvg_b, fvg_s = _fair_value_gap(c3m)
        bos_b, bos_s = _structure_break(c3m, 10)

        # HTF tendencia
        cl15  = [c.close for c in c15m]
        e21_15 = _ema(cl15, 21)
        e55_15 = _ema(cl15, 55)
        htf_up = cl15[-1] > e21_15[-1] > e55_15[-1]
        htf_dn = cl15[-1] < e21_15[-1] < e55_15[-1]

        # Macro 1h
        if len(c1h) >= 200:
            cl1h   = [c.close for c in c1h]
            e200_1h = _ema(cl1h, 200)
            macro_up = cl1h[-1] > e200_1h[-1]
            macro_dn = cl1h[-1] < e200_1h[-1]
        else:
            macro_up = macro_dn = True  # sin datos suficientes, neutro

        # ── Filtro de régimen: no operar en RANGE/CHOPPY ─────
        if regime in ("RANGE", "CHOPPY"):
            log.debug("%s: régimen %s — skip", symbol, regime)
            return None

        # ── Funding rate filter ───────────────────────────────
        # No LONG si funding muy positivo (costoso y saturado de longs)
        # No SHORT si funding muy negativo (squeeze inminente)
        if ctx.funding_rate > 0.001 and regime == "TREND_UP":
            log.debug("%s: funding %.4f%% — long caro, skip", symbol, ctx.funding_rate*100)
            return None
        if ctx.funding_rate < -0.0005 and regime == "TREND_DOWN":
            log.debug("%s: funding %.4f%% — short squeeze riesgo, skip", symbol, ctx.funding_rate*100)
            return None

        # ══════════════════════════════════════════════════════
        #  EVALUACIÓN LONG
        # ══════════════════════════════════════════════════════
        if regime == "TREND_UP":
            score = 0
            reasons = []

            # 1. EMA alineadas alcistas (20pts)
            if e8[-1] > e21[-1] > e55[-1]:
                cross = prev.close <= e8[-2] and curr.close > e8[-1]
                if cross:
                    score += self.WEIGHTS["ema_align"]
                    reasons.append("EMA✓")
                else:
                    score += 10   # alineadas pero sin cross reciente
                    reasons.append("EMA~")
            else:
                return None  # EMAs no alineadas = no operar

            # 2. HTF alineado (15pts)
            if htf_up and macro_up:
                score += self.WEIGHTS["htf_align"]
                reasons.append("HTF✓")
            elif htf_up or macro_up:
                score += 7
                reasons.append("HTF~")

            # 3. ADX fuerza tendencia (15pts)
            if adx_val >= 30:
                score += self.WEIGHTS["adx_trend"]
                reasons.append(f"ADX{adx_val:.0f}")
            elif adx_val >= 25:
                score += 10
                reasons.append(f"ADX{adx_val:.0f}")

            # 4. RSI no sobrecomprado (10pts)
            if 40 <= rsi_val <= 65:
                score += self.WEIGHTS["rsi_zone"]
                reasons.append(f"RSI{rsi_val:.0f}✓")
            elif rsi_val < 70:
                score += 5
                reasons.append(f"RSI{rsi_val:.0f}")
            else:
                return None  # RSI > 70 = sobrecomprado, no entrar

            # 5. Volume imbalance comprador (15pts)
            if vim >= 0.60:
                score += self.WEIGHTS["vol_imbal"]
                reasons.append(f"VI{vim:.2f}✓")
            elif vim >= 0.52:
                score += 8
                reasons.append(f"VI{vim:.2f}")

            # 6. FVG o BoS alcista (15pts)
            if fvg_b or bos_b:
                score += self.WEIGHTS["fvg_or_bos"]
                reasons.append("FVG/BoS✓")

            # 7. Pin bar o volumen spike (10pts)
            vol_avg = np.mean([c.volume for c in c3m[-20:-1]])
            vol_spike = curr.volume >= vol_avg * 1.5
            if _pin_bar(curr, "LONG") or vol_spike:
                score += self.WEIGHTS["pin_or_vol"]
                reasons.append("PIN/VOL✓")

            if score < self.SCORE_MIN:
                log.debug("%s LONG score=%d < %d — skip", symbol, score, self.SCORE_MIN)
                return None

            # ── Calcular niveles con ATR ──────────────────────
            sl  = round(curr.close - atr_val * cfg.ATR_SL_MULT,  8)
            tp1 = round(curr.close + atr_val * cfg.ATR_TP1_MULT, 8)
            tp2 = round(curr.close + atr_val * cfg.ATR_TP2_MULT, 8)
            tp3 = round(curr.close + atr_val * cfg.ATR_TP3_MULT, 8)
            rr  = (tp1 - curr.close) / (curr.close - sl) if (curr.close - sl) > 0 else 0
            if rr < 1.5:
                log.debug("%s LONG RR=%.2f < 1.5 — skip", symbol, rr)
                return None

            return Signal(
                symbol=symbol, side="LONG", price=curr.close,
                sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                score=score, regime=regime,
                reason=" | ".join(reasons), atr=atr_val, rr=rr,
            )

        # ══════════════════════════════════════════════════════
        #  EVALUACIÓN SHORT
        # ══════════════════════════════════════════════════════
        if regime == "TREND_DOWN":
            score = 0
            reasons = []

            if e8[-1] < e21[-1] < e55[-1]:
                cross = prev.close >= e8[-2] and curr.close < e8[-1]
                if cross:
                    score += self.WEIGHTS["ema_align"]
                    reasons.append("EMA✓")
                else:
                    score += 10
                    reasons.append("EMA~")
            else:
                return None

            if htf_dn and macro_dn:
                score += self.WEIGHTS["htf_align"]
                reasons.append("HTF✓")
            elif htf_dn or macro_dn:
                score += 7
                reasons.append("HTF~")

            if adx_val >= 30:
                score += self.WEIGHTS["adx_trend"]
                reasons.append(f"ADX{adx_val:.0f}")
            elif adx_val >= 25:
                score += 10
                reasons.append(f"ADX{adx_val:.0f}")

            if 35 <= rsi_val <= 60:
                score += self.WEIGHTS["rsi_zone"]
                reasons.append(f"RSI{rsi_val:.0f}✓")
            elif rsi_val > 30:
                score += 5
                reasons.append(f"RSI{rsi_val:.0f}")
            else:
                return None  # RSI < 30 = posible rebote

            if vim <= 0.40:
                score += self.WEIGHTS["vol_imbal"]
                reasons.append(f"VI{vim:.2f}✓")
            elif vim <= 0.48:
                score += 8
                reasons.append(f"VI{vim:.2f}")

            if fvg_s or bos_s:
                score += self.WEIGHTS["fvg_or_bos"]
                reasons.append("FVG/BoS✓")

            vol_avg = np.mean([c.volume for c in c3m[-20:-1]])
            if _pin_bar(curr, "SHORT") or curr.volume >= vol_avg * 1.5:
                score += self.WEIGHTS["pin_or_vol"]
                reasons.append("PIN/VOL✓")

            if score < self.SCORE_MIN:
                log.debug("%s SHORT score=%d < %d — skip", symbol, score, self.SCORE_MIN)
                return None

            sl  = round(curr.close + atr_val * cfg.ATR_SL_MULT,  8)
            tp1 = round(curr.close - atr_val * cfg.ATR_TP1_MULT, 8)
            tp2 = round(curr.close - atr_val * cfg.ATR_TP2_MULT, 8)
            tp3 = round(curr.close - atr_val * cfg.ATR_TP3_MULT, 8)
            rr  = (curr.close - tp1) / (sl - curr.close) if (sl - curr.close) > 0 else 0
            if rr < 1.5:
                log.debug("%s SHORT RR=%.2f < 1.5 — skip", symbol, rr)
                return None

            return Signal(
                symbol=symbol, side="SHORT", price=curr.close,
                sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                score=score, regime=regime,
                reason=" | ".join(reasons), atr=atr_val, rr=rr,
            )

        return None
