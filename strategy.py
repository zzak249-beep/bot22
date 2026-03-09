import numpy as np
import pandas as pd
from indicators import (add_indicators, get_trend, divergence,
                        calc_score_long, calc_score_short,
                        volume_ok, momentum_bars)
from data_feed import get_df
from config import (BB_PERIOD, SMA_PERIOD, RSI_LONG, RSI_SHORT,
                    SCORE_MIN, MIN_RR, SL_BUFFER, PARTIAL_TP_ATR,
                    MTF_ENABLED, MTF_INTERVAL, MTF_BLOCK_COUNTER,
                    REENTRY_ENABLED, REENTRY_SCORE_MIN,
                    TP_ATR_MULT, SL_ATR_MULT)

# ══════════════════════════════════════════════════════
# strategy.py v4.0 — AGRESIVO MÁXIMA RENTABILIDAD
#
# Cambios vs v3.0:
#   1. TP = 4.0x ATR, SL = 1.0x ATR → ratio 4:1
#   2. MTF_BLOCK_COUNTER OFF → menos bloqueos
#   3. TREND_THRESH más bajo → más señales SHORT
#   4. touch_long/touch_short más permisivo (1.005 en lugar de 1.003)
#   5. Score dinámico por learner
# ══════════════════════════════════════════════════════

MIN_BARS = max(BB_PERIOD, SMA_PERIOD) + 30


def _get_4h_bias(symbol: str) -> str:
    """MTF agresivo: solo bloquea si señal 4h es EXTREMA."""
    if not MTF_ENABLED:
        return "neutral"
    try:
        df4 = get_df(symbol, interval=MTF_INTERVAL, limit=60)
        if df4.empty or len(df4) < 30:
            return "neutral"
        df4 = add_indicators(df4)

        price_4h = float(df4["close"].iloc[-1])
        sma_4h   = float(df4["sma50"].iloc[-1]) if not np.isnan(df4["sma50"].iloc[-1]) else price_4h
        rsi_4h   = float(df4["rsi"].iloc[-1])   if not np.isnan(df4["rsi"].iloc[-1])   else 50.0

        # Solo bloquear si señal 4h es MUY EXTREMA
        if price_4h < sma_4h * 0.95 and rsi_4h < 38:
            return "down"
        if price_4h > sma_4h * 1.05 and rsi_4h > 62:
            return "up"
        return "neutral"
    except Exception:
        return "neutral"


def _get_score_min(symbol: str, reentry_info: dict = None) -> int:
    if reentry_info and REENTRY_ENABLED:
        return REENTRY_SCORE_MIN
    try:
        from learner import Learner
        learner = Learner()
        cfg = learner.get_config_for_pair(symbol)
        if not cfg.get("skip"):
            return cfg.get("score_min", SCORE_MIN)
    except Exception:
        pass
    return SCORE_MIN


def get_signal(df: pd.DataFrame, symbol: str = "",
               reentry_info: dict = None) -> dict | None:
    """
    Genera señal con ratio TP:SL 4:1 AGRESIVO.
    Más señales, más rentabilidad por trade.
    """
    if len(df) < MIN_BARS:
        return None

    df  = add_indicators(df)
    i   = len(df) - 1
    cur = df.iloc[i]

    price = float(cur["close"])
    r     = float(cur["rsi"])   if not np.isnan(cur["rsi"])   else 50.0
    a     = float(cur["atr"])   if not np.isnan(cur["atr"])   else 0.0
    mb    = float(cur["macd"]) > 0 if not np.isnan(cur["macd"]) else True
    stv   = float(cur["stoch"]) if not np.isnan(cur["stoch"]) else 50.0
    sma   = float(cur["sma50"]) if not np.isnan(cur["sma50"]) else price
    if a <= 0:
        return None

    blo      = float(cur["lower"])
    bhi      = float(cur["upper"])
    trend_1h = get_trend(df["basis"], i)
    bias_4h  = _get_4h_bias(symbol) if symbol else "neutral"

    dv = divergence(
        df["close"].iloc[max(0, i-8):i+1],
        df["rsi"].iloc[max(0, i-8):i+1],
    )

    score_min  = _get_score_min(symbol, reentry_info)
    is_reentry = bool(reentry_info and REENTRY_ENABLED)

    # ══ LONG ══════════════════════════════════════════
    if trend_1h != "down" and price >= sma * 0.96:   # más permisivo

        if MTF_BLOCK_COUNTER and bias_4h == "down":
            print(f"  [{symbol}] ❌ LONG bloqueado 4h bajista")
        else:
            bear_bars = momentum_bars(df["close"], i, lookback=5)

            # touch más permisivo: 1.005 en vez de 1.003
            touch_long = (price <= blo * 1.005) or \
                         (dv == "bull" and r < RSI_LONG and price <= blo * 1.02)

            if touch_long and r < RSI_LONG:
                sc = calc_score_long(r, dv, mb, stv, bear_bars)
                if sc < score_min:
                    print(f"  [{symbol}] LONG score={sc} < {score_min}")
                else:
                    sl   = price - a * SL_ATR_MULT
                    tp   = price + a * TP_ATR_MULT
                    tp_p = price + a * PARTIAL_TP_ATR

                    sl = min(sl, float(cur["low"]) * (1 - SL_BUFFER))

                    if sl > 0 and tp > price and (price - sl) > 0:
                        rr = (tp - price) / (price - sl)
                        if rr < MIN_RR:
                            print(f"  [{symbol}] LONG RR={rr:.2f} < {MIN_RR}")
                        else:
                            print(f"  [{symbol}] ✅ LONG score={sc} rsi={r:.1f} rr={rr:.2f} 4h={bias_4h}")
                            return dict(
                                side="long", price=price, sl=sl, tp=tp,
                                tp_p=tp_p, score=sc, rsi=round(r, 1),
                                trend=trend_1h, atr=a,
                                bias_4h=bias_4h, reentry=is_reentry
                            )

    # ══ SHORT ═════════════════════════════════════════
    if trend_1h != "up" and price <= sma * 1.04:   # más permisivo

        if MTF_BLOCK_COUNTER and bias_4h == "up":
            print(f"  [{symbol}] ❌ SHORT bloqueado 4h alcista")
        else:
            bear_count = momentum_bars(df["close"], i, lookback=5)
            bull_bars  = 5 - bear_count

            # touch más permisivo: 0.995 en vez de 0.997
            touch_short = (price >= bhi * 0.995) or \
                          (dv == "bear" and r > RSI_SHORT and price >= bhi * 0.98)

            if touch_short and r > RSI_SHORT:
                sc = calc_score_short(r, dv, mb, stv, bull_bars)
                if sc < score_min:
                    print(f"  [{symbol}] SHORT score={sc} < {score_min}")
                else:
                    sl   = price + a * SL_ATR_MULT
                    tp   = price - a * TP_ATR_MULT
                    tp_p = price - a * PARTIAL_TP_ATR

                    sl = max(sl, float(cur["high"]) * (1 + SL_BUFFER))

                    if sl > price and tp < price and (sl - price) > 0:
                        rr = (price - tp) / (sl - price)
                        if rr < MIN_RR:
                            print(f"  [{symbol}] SHORT RR={rr:.2f} < {MIN_RR}")
                        else:
                            print(f"  [{symbol}] ✅ SHORT score={sc} rsi={r:.1f} rr={rr:.2f} 4h={bias_4h}")
                            return dict(
                                side="short", price=price, sl=sl, tp=tp,
                                tp_p=tp_p, score=sc, rsi=round(r, 1),
                                trend=trend_1h, atr=a,
                                bias_4h=bias_4h, reentry=is_reentry
                            )
    return None
