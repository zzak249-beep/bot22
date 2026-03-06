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
import liquidity as liq_mod

# ══════════════════════════════════════════════════════
# strategy.py v13.1 — FIXES + LIQUIDITY INTEGRATION
#
# Bugs corregidos vs v12.4:
#   ① LONG: eliminada condición `price >= sma * 0.97`
#      → cuando precio toca BB inferior está BAJO la SMA,
#        el filtro anterior bloqueaba casi todas las señales LONG
#   ② SHORT: cambiado de `trend_1h == "flat"` a `trend_1h != "up"`
#      → antes sólo disparaba con tendencia neutral, ahora también
#        con tendencia bajista (que es cuando más aplica un SHORT)
#   ③ Integración completa de liquidity.py:
#      → bias institucional (order book, funding, CVD, LSR)
#        puede confirmar señal (+bonus), penalizarla (-pts) o bloquearla
# ══════════════════════════════════════════════════════

MIN_BARS = max(BB_PERIOD, SMA_PERIOD) + 30


def _get_4h_bias(symbol: str) -> str:
    """Tendencia 4h. Solo bloquea si la señal 4h ES MUY CLARA."""
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

        # Solo bloquear si señal 4h es muy clara (no en zona gris)
        if price_4h < sma_4h * 0.97 and rsi_4h < 45:
            return "down"
        if price_4h > sma_4h * 1.03 and rsi_4h > 55:
            return "up"
        return "neutral"
    except Exception as e:
        print(f"  [MTF] error {symbol}: {e}")
        return "neutral"


def _apply_liquidity(signal: dict, symbol: str, price: float, atr: float) -> dict | None:
    """
    Integra el análisis institucional de liquidity.py.
    Retorna señal ajustada, o None si debe bloquearse.
    """
    try:
        lbias = liq_mod.analyze(symbol, price, atr)
        # Mapear side → action que entiende apply_liquidity_filter
        action = "buy" if signal["side"] == "long" else "sell_short"
        liq_sig = liq_mod.apply_liquidity_filter(
            {"action": action, "score": signal["score"], "reason": ""},
            lbias
        )
        if liq_sig.get("action") == "none":
            print(f"  [{symbol}] ❌ BLOQUEADO por liquidez institucional "
                  f"({lbias.bias} {lbias.score}/100)")
            return None
        # Aplicar score ajustado y añadir resumen de liquidez
        signal = signal.copy()
        signal["score"]     = liq_sig.get("score", signal["score"])
        signal["liq_bias"]  = lbias.bias
        signal["liq_score"] = lbias.score
        signal["liq_info"]  = lbias.summary
        reason_prefix = liq_sig.get("reason", "")
        if reason_prefix:
            print(f"  [{symbol}] {reason_prefix.strip()}")
        return signal
    except Exception as e:
        # Si liquidity falla (API no disponible, etc.) no bloquear la señal
        print(f"  [{symbol}] [LIQ] aviso: {e} — continuando sin filtro inst.")
        return signal


def get_signal(df: pd.DataFrame, symbol: str = "",
               reentry_info: dict = None) -> dict | None:
    """
    Retorna señal dict o None.
    Imprime motivo de rechazo para debug.
    """
    if len(df) < MIN_BARS:
        return None

    df = add_indicators(df)
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

    # ── Filtro de volumen ──────────────────────────────
    if not volume_ok(df, i):
        print(f"  [{symbol}] ❌ volumen bajo — descartado")
        return None

    # ── Bias 4h ────────────────────────────────────────
    bias_4h = _get_4h_bias(symbol) if symbol else "neutral"

    # ── Divergencia y momentum ─────────────────────────
    dv = divergence(
        df["close"].iloc[max(0, i - 8):i + 1],
        df["rsi"].iloc[max(0, i - 8):i + 1],
    )

    score_min  = REENTRY_SCORE_MIN if (reentry_info and REENTRY_ENABLED) else SCORE_MIN
    is_reentry = bool(reentry_info and REENTRY_ENABLED)

    # ══ LONG ══════════════════════════════════════════
    # FIX ①: eliminado `price >= sma * 0.97`
    # Cuando precio está en la BB inferior está BAJO la SMA — eso es justo
    # donde queremos entrar LONG. El filtro anterior bloqueaba casi todo.
    if trend_1h != "down":

        if MTF_BLOCK_COUNTER and bias_4h == "down":
            print(f"  [{symbol}] ❌ LONG bloqueado por 4h bajista")
        else:
            bear_bars  = momentum_bars(df["close"], i, lookback=5)
            touch_long = (price <= blo * 1.003) or \
                         (dv == "bull" and r < RSI_LONG and price <= blo * 1.015)

            if touch_long:
                if r >= RSI_LONG:
                    print(f"  [{symbol}] RSI={r:.1f} muy alto para LONG (>={RSI_LONG})")
                else:
                    sc = calc_score_long(r, dv, mb, stv, bear_bars)
                    if sc < score_min:
                        print(f"  [{symbol}] LONG score={sc} < {score_min} — descartado")
                    else:
                        sl   = float(cur["low"]) * (1 - SL_BUFFER)
                        tp   = bhi
                        tp_p = price + PARTIAL_TP_ATR * a
                        if sl > 0 and tp > price and (price - sl) > 0:
                            rr = (tp - price) / (price - sl)
                            if rr < MIN_RR:
                                print(f"  [{symbol}] LONG RR={rr:.2f} < {MIN_RR} — descartado")
                            else:
                                sig = dict(
                                    side="long", price=price, sl=sl, tp=tp,
                                    tp_p=tp_p, score=sc, rsi=round(r, 1),
                                    trend=trend_1h, atr=a,
                                    bias_4h=bias_4h, reentry=is_reentry
                                )
                                # ── Filtro institucional ──────────────
                                sig = _apply_liquidity(sig, symbol, price, a)
                                if sig is None:
                                    return None
                                print(f"  [{symbol}] ✅ SEÑAL LONG "
                                      f"score={sig['score']} rsi={r:.1f} "
                                      f"rr={rr:.2f} 4h={bias_4h} "
                                      f"liq={sig.get('liq_bias','?')}")
                                return sig

    # ══ SHORT ═════════════════════════════════════════
    # FIX ②: cambiado de `trend_1h == "flat"` a `trend_1h != "up"`
    # Antes sólo disparaba con tendencia neutral; ahora también con bajista,
    # que es exactamente cuando un SHORT tiene más sentido.
    if trend_1h != "up":

        if MTF_BLOCK_COUNTER and bias_4h == "up":
            print(f"  [{symbol}] ❌ SHORT bloqueado por 4h alcista")
        else:
            bull_bars   = momentum_bars(df["close"], i, lookback=5)
            touch_short = (price >= bhi * 0.997) or \
                          (dv == "bear" and r > RSI_SHORT and price >= bhi * 0.985)

            if touch_short and r > RSI_SHORT:
                sc = calc_score_short(r, dv, mb, stv, bull_bars)
                if sc < score_min:
                    print(f"  [{symbol}] SHORT score={sc} < {score_min} — descartado")
                else:
                    sl   = float(cur["high"]) * (1 + SL_BUFFER)
                    tp   = blo
                    tp_p = price - PARTIAL_TP_ATR * a
                    if sl > price and tp < price and (sl - price) > 0:
                        rr = (price - tp) / (sl - price)
                        if rr < MIN_RR:
                            print(f"  [{symbol}] SHORT RR={rr:.2f} < {MIN_RR} — descartado")
                        else:
                            sig = dict(
                                side="short", price=price, sl=sl, tp=tp,
                                tp_p=tp_p, score=sc, rsi=round(r, 1),
                                trend=trend_1h, atr=a,
                                bias_4h=bias_4h, reentry=is_reentry
                            )
                            # ── Filtro institucional ──────────────
                            sig = _apply_liquidity(sig, symbol, price, a)
                            if sig is None:
                                return None
                            print(f"  [{symbol}] ✅ SEÑAL SHORT "
                                  f"score={sig['score']} rsi={r:.1f} "
                                  f"rr={rr:.2f} 4h={bias_4h} "
                                  f"liq={sig.get('liq_bias','?')}")
                            return sig
    return None
