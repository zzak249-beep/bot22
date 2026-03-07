"""
strategy.py — Motor de señales BB+RSI v14.0
════════════════════════════════════════════
Estrategias implementadas:
  1. BB_RSI       — precio toca banda + RSI extremo
  2. RSI_DIVERGE  — divergencia precio/RSI en extremos
  3. STOCH_BB     — stoch oversold/overbought + toque BB
  4. TREND_PULL   — retroceso a EMA50 en tendencia

Genera señales LONG y SHORT con SL/TP dinámicos por ATR.
"""

import numpy as np
import pandas as pd
import logging

from indicators import (
    add_indicators, get_trend, divergence,
    calc_score_long, calc_score_short,
    volume_ok, momentum_bars
)
from data_feed import get_df

try:
    import config as cfg
    RSI_LONG       = cfg.RSI_LONG
    RSI_SHORT      = cfg.RSI_SHORT
    SCORE_MIN      = cfg.SCORE_MIN
    MIN_RR         = cfg.MIN_RR
    SL_BUFFER      = cfg.SL_BUFFER
    PARTIAL_TP_ATR = cfg.PARTIAL_TP_ATR
    MTF_ENABLED    = cfg.MTF_ENABLED
    MTF_BLOCK      = cfg.MTF_BLOCK_COUNTER
    MTF_INTERVAL   = cfg.MTF_INTERVAL
    REENTRY_ENABLED     = cfg.REENTRY_ENABLED
    REENTRY_SCORE_MIN   = cfg.REENTRY_SCORE_MIN
except Exception:
    RSI_LONG = 35; RSI_SHORT = 65; SCORE_MIN = 35; MIN_RR = 1.3
    SL_BUFFER = 0.002; PARTIAL_TP_ATR = 2.0
    MTF_ENABLED = True; MTF_BLOCK = True; MTF_INTERVAL = "1h"
    REENTRY_ENABLED = True; REENTRY_SCORE_MIN = 50

log = logging.getLogger("strategy")
MIN_BARS = 55   # mínimo de velas para calcular indicadores


def _get_4h_bias(symbol: str) -> str:
    """
    Tendencia en 1h para confirmar dirección.
    Solo bloquea si la señal es MUY clara (no zona gris).
    """
    if not MTF_ENABLED or not symbol:
        return "neutral"
    try:
        df4 = get_df(symbol, interval=MTF_INTERVAL, limit=80)
        if df4.empty or len(df4) < 30:
            return "neutral"
        df4   = add_indicators(df4)
        price = float(df4["close"].iloc[-1])
        ema50 = float(df4["ema50"].iloc[-1]) if not np.isnan(df4["ema50"].iloc[-1]) else price
        rsi4  = float(df4["rsi"].iloc[-1])   if not np.isnan(df4["rsi"].iloc[-1])   else 50.0
        # Solo bloquear si la tendencia contraria es muy fuerte
        if price < ema50 * 0.96 and rsi4 < 42:
            return "down"
        if price > ema50 * 1.04 and rsi4 > 58:
            return "up"
        return "neutral"
    except Exception as e:
        log.debug(f"MTF {symbol}: {e}")
        return "neutral"


def get_signal(df: pd.DataFrame, symbol: str = "",
               reentry_info: dict = None) -> dict | None:
    """
    Analiza el DataFrame y retorna un dict de señal o None.

    Formato de retorno:
    {
        side:    "long" | "short"
        price:   float
        sl:      float
        tp:      float
        tp_p:    float  (TP parcial)
        score:   int    (0-100)
        rsi:     float
        atr:     float
        trend:   str
        bias_4h: str
        strategy: str
        reentry: bool
        rr:      float
    }
    """
    if df is None or len(df) < MIN_BARS:
        return None

    df = add_indicators(df)
    i  = len(df) - 1
    cur = df.iloc[i]

    price = float(cur["close"])
    rsi   = float(cur["rsi"])   if not np.isnan(cur["rsi"])   else 50.0
    atr   = float(cur["atr"])   if not np.isnan(cur["atr"])   else 0.0
    macd  = float(cur["macd"])  if not np.isnan(cur["macd"])  else 0.0
    stoch = float(cur["stoch"]) if not np.isnan(cur["stoch"]) else 50.0
    ema50 = float(cur["ema50"]) if not np.isnan(cur["ema50"]) else price
    blo   = float(cur["lower"]) if not np.isnan(cur["lower"]) else price * 0.97
    bhi   = float(cur["upper"]) if not np.isnan(cur["upper"]) else price * 1.03
    basis = float(cur["basis"]) if not np.isnan(cur["basis"]) else price

    if atr <= 0:
        return None

    macd_pos  = macd > 0
    trend_1h  = get_trend(df["basis"], i)
    vol_ok    = volume_ok(df, i)
    bear_bars = momentum_bars(df["close"], i, lookback=5)
    bull_bars = momentum_bars(df["close"], i, lookback=5)

    dv = divergence(
        df["close"].iloc[max(0, i - 10):i + 1],
        df["rsi"].iloc[max(0, i - 10):i + 1],
    )

    bias_4h  = _get_4h_bias(symbol)
    min_score = REENTRY_SCORE_MIN if (reentry_info and REENTRY_ENABLED) else SCORE_MIN

    # ═══════════════════════════════════════════════════
    # SEÑALES LONG
    # ═══════════════════════════════════════════════════
    # Nota: cuando RSI está muy oversold + toca BB inferior,
    # la tendencia SERÁ "down" — eso es exactamente lo que buscamos.
    # Solo bloqueamos si la tendencia bajista es muy fuerte Y RSI no tan extremo.
    long_trend_ok = (trend_1h != "down") or (rsi <= RSI_LONG - 5)  # muy oversold = override

    if long_trend_ok:
        if MTF_BLOCK and bias_4h == "down" and rsi > RSI_LONG - 8:
            pass  # bloqueado por tendencia bajista fuerte en 1h + no tan oversold
        else:
            # ── Condiciones de entrada LONG ──────────────
            # 1) Precio cerca o por debajo de la banda inferior BB
            bb_touch   = price <= blo * 1.012   # hasta 1.2% sobre la banda
            # 2) RSI en zona oversold
            rsi_ok     = rsi <= RSI_LONG
            # 3) Divergencia alcista aunque no toque exactamente la BB
            div_entry  = (dv == "bull" and rsi <= RSI_LONG + 5 and price <= basis)
            # 4) Retroceso a EMA50 con RSI oversold
            ema_entry  = (abs(price - ema50) / ema50 < 0.012 and rsi <= RSI_LONG + 3)

            can_enter = (bb_touch and rsi_ok) or div_entry or (bb_touch and ema_entry)

            if can_enter and vol_ok:
                sc = calc_score_long(rsi, dv, macd_pos, stoch, bear_bars)
                if sc >= min_score:
                    sl  = price - atr * 1.5
                    sl  = min(sl, float(cur["low"]) * (1 - SL_BUFFER))
                    tp  = bhi if bhi > price * 1.01 else price + atr * 2.5
                    tp_p = price + atr * PARTIAL_TP_ATR

                    if sl > 0 and tp > price:
                        rr = (tp - price) / (price - sl)
                        if rr >= MIN_RR:
                            strategy = "BB_RSI"
                            if div_entry:    strategy = "RSI_DIVERGE"
                            elif ema_entry:  strategy = "EMA_PULL"
                            log.info(f"[{symbol}] ✅ LONG {strategy} "
                                     f"score={sc} rsi={rsi:.1f} rr={rr:.2f} 4h={bias_4h}")
                            return dict(
                                side="long", price=price, sl=sl, tp=tp, tp_p=tp_p,
                                score=sc, rsi=round(rsi, 1), atr=atr,
                                trend=trend_1h, bias_4h=bias_4h,
                                strategy=strategy, rr=round(rr, 2),
                                reentry=bool(reentry_info and REENTRY_ENABLED)
                            )

    # ═══════════════════════════════════════════════════
    # SEÑALES SHORT
    # ═══════════════════════════════════════════════════
    short_trend_ok = (trend_1h != "up") or (rsi >= RSI_SHORT + 5)

    if short_trend_ok:
        if MTF_BLOCK and bias_4h == "up" and rsi < RSI_SHORT + 8:
            pass
        else:
            bb_touch_s  = price >= bhi * 0.988
            rsi_ok_s    = rsi >= RSI_SHORT
            div_entry_s = (dv == "bear" and rsi >= RSI_SHORT - 5 and price >= basis)
            ema_entry_s = (abs(price - ema50) / ema50 < 0.012 and rsi >= RSI_SHORT - 3)

            can_enter_s = (bb_touch_s and rsi_ok_s) or div_entry_s or (bb_touch_s and ema_entry_s)

            if can_enter_s and vol_ok:
                sc = calc_score_short(rsi, dv, macd_pos, stoch, bull_bars)
                if sc >= min_score:
                    sl  = price + atr * 1.5
                    sl  = max(sl, float(cur["high"]) * (1 + SL_BUFFER))
                    tp  = blo if blo < price * 0.99 else price - atr * 2.5
                    tp_p = price - atr * PARTIAL_TP_ATR

                    if sl > price and tp < price:
                        rr = (price - tp) / (sl - price)
                        if rr >= MIN_RR:
                            strategy = "BB_RSI_S"
                            if div_entry_s:   strategy = "RSI_DIVERGE_S"
                            elif ema_entry_s: strategy = "EMA_PULL_S"
                            log.info(f"[{symbol}] ✅ SHORT {strategy} "
                                     f"score={sc} rsi={rsi:.1f} rr={rr:.2f} 4h={bias_4h}")
                            return dict(
                                side="short", price=price, sl=sl, tp=tp, tp_p=tp_p,
                                score=sc, rsi=round(rsi, 1), atr=atr,
                                trend=trend_1h, bias_4h=bias_4h,
                                strategy=strategy, rr=round(rr, 2),
                                reentry=bool(reentry_info and REENTRY_ENABLED)
                            )
    return None
