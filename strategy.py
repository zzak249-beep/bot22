import numpy as np
import pandas as pd
from indicators import (add_indicators, get_trend, divergence,
                        calc_score_long, calc_score_short,
                        volume_ok, momentum_bars)
from data_feed import get_df
from config import (BB_PERIOD, SMA_PERIOD, RSI_LONG, RSI_SHORT,
                    SCORE_MIN, MIN_RR, SL_BUFFER, PARTIAL_TP_ATR,
                    MTF_ENABLED, MTF_INTERVAL, MTF_BLOCK_COUNTER,
                    REENTRY_ENABLED, REENTRY_SCORE_MIN)

# ══════════════════════════════════════════════════════
# strategy.py v12.4 — FIX: filtros calibrados
# Problema anterior: MTF+volumen bloqueaban todo
# Solución: MTF solo bloquea si tendencia MUY clara,
#           volumen más permisivo, score sin penalización doble
# ══════════════════════════════════════════════════════

MIN_BARS = max(BB_PERIOD, SMA_PERIOD) + 30


def _get_4h_bias(symbol: str) -> str:
    """Tendencia 4h. Más permisivo: solo bloquea si bajista fuerte."""
    if not MTF_ENABLED:
        return "neutral"
    try:
        df4 = get_df(symbol, interval=MTF_INTERVAL, limit=60)
        if df4.empty or len(df4) < 30:
            return "neutral"
        df4 = add_indicators(df4)
        i = len(df4) - 1

        # Usar precio vs SMA50 4h como bias (más robusto que BB basis)
        price_4h = float(df4["close"].iloc[-1])
        sma_4h   = float(df4["sma50"].iloc[-1]) if not np.isnan(df4["sma50"].iloc[-1]) else price_4h
        rsi_4h   = float(df4["rsi"].iloc[-1])   if not np.isnan(df4["rsi"].iloc[-1])   else 50.0

        # Solo bloquear si señal 4h ES MUY CLARA (no en zona gris)
        if price_4h < sma_4h * 0.97 and rsi_4h < 45:
            return "down"
        if price_4h > sma_4h * 1.03 and rsi_4h > 55:
            return "up"
        return "neutral"
    except Exception as e:
        print(f"  [MTF] error {symbol}: {e}")
        return "neutral"


def get_signal(df: pd.DataFrame, symbol: str = "",
               reentry_info: dict = None) -> dict | None:
    """
    Retorna señal dict o None.
    Imprime motivo de rechazo para debug.
    """
    if len(df) < MIN_BARS:
        return None

    df = add_indicators(df)
    i     = len(df) - 1
    cur   = df.iloc[i]

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

    # ── Filtro de volumen ──────────────────────────────
    vol_pass = volume_ok(df, i)
    if not vol_pass:
        print(f"  [{symbol}] ❌ volumen bajo — descartado")
        return None

    # ── Bias 4h ────────────────────────────────────────
    bias_4h = _get_4h_bias(symbol) if symbol else "neutral"

    # ── Divergencia y momentum ─────────────────────────
    dv = divergence(
        df["close"].iloc[max(0, i-8):i+1],
        df["rsi"].iloc[max(0, i-8):i+1],
    )

    score_min = REENTRY_SCORE_MIN if (reentry_info and REENTRY_ENABLED) else SCORE_MIN
    is_reentry = bool(reentry_info and REENTRY_ENABLED)

    # ══ LONG ══════════════════════════════════════════
    if trend_1h != "down" and price >= sma * 0.97:

        # MTF: solo bloquear LONG si 4h claramente bajista
        if MTF_BLOCK_COUNTER and bias_4h == "down":
            print(f"  [{symbol}] ❌ LONG bloqueado por 4h bajista")
        else:
            bear_bars = momentum_bars(df["close"], i, lookback=5)
            touch_long = (price <= blo * 1.003) or \
                         (dv == "bull" and r < RSI_LONG and price <= blo * 1.015)

            if not touch_long:
                pass  # sin debug para no saturar logs
            elif r >= RSI_LONG:
                print(f"  [{symbol}] RSI={r:.1f} muy alto para LONG (>={RSI_LONG})")
            else:
                sc = calc_score_long(r, dv, mb, stv, bear_bars)
                if sc < score_min:
                    print(f"  [{symbol}] LONG score={sc} < {score_min} — descartado")
                else:
                    sl  = float(cur["low"]) * (1 - SL_BUFFER)
                    tp  = bhi
                    tp_p = price + PARTIAL_TP_ATR * a
                    if sl > 0 and tp > price and (price - sl) > 0:
                        rr = (tp - price) / (price - sl)
                        if rr < MIN_RR:
                            print(f"  [{symbol}] LONG RR={rr:.2f} < {MIN_RR} — descartado")
                        else:
                            print(f"  [{symbol}] ✅ SEÑAL LONG score={sc} rsi={r:.1f} rr={rr:.2f} 4h={bias_4h}")
                            return dict(
                                side="long", price=price, sl=sl, tp=tp,
                                tp_p=tp_p, score=sc, rsi=round(r, 1),
                                trend=trend_1h, atr=a,
                                bias_4h=bias_4h, reentry=is_reentry
                            )

    # ══ SHORT ═════════════════════════════════════════
    if trend_1h == "flat" and price <= sma * 1.03:

        if MTF_BLOCK_COUNTER and bias_4h == "up":
            print(f"  [{symbol}] ❌ SHORT bloqueado por 4h alcista")
        else:
            bull_bars = momentum_bars(df["close"], i, lookback=5)
            touch_short = (price >= bhi * 0.997) or \
                          (dv == "bear" and r > RSI_SHORT and price >= bhi * 0.985)

            if touch_short and r > RSI_SHORT:
                sc = calc_score_short(r, dv, mb, stv, bull_bars)
                if sc < score_min:
                    print(f"  [{symbol}] SHORT score={sc} < {score_min} — descartado")
                else:
                    sl  = float(cur["high"]) * (1 + SL_BUFFER)
                    tp  = blo
                    tp_p = price - PARTIAL_TP_ATR * a
                    if sl > price and tp < price and (sl - price) > 0:
                        rr = (price - tp) / (sl - price)
                        if rr < MIN_RR:
                            print(f"  [{symbol}] SHORT RR={rr:.2f} < {MIN_RR} — descartado")
                        else:
                            print(f"  [{symbol}] ✅ SEÑAL SHORT score={sc} rsi={r:.1f} rr={rr:.2f} 4h={bias_4h}")
                            return dict(
                                side="short", price=price, sl=sl, tp=tp,
                                tp_p=tp_p, score=sc, rsi=round(r, 1),
                                trend=trend_1h, atr=a,
                                bias_4h=bias_4h, reentry=is_reentry
                            )
    return None
