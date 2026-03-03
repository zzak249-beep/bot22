"""
strategy.py — BB+RSI con 5 mejoras:
  1. Trailing Stop  2. Multi-timeframe  3. TP Parcial  4. SHORT  5. Filtro Volumen
"""
import pandas as pd
import numpy as np
import config as cfg


def calc_bb(close, period=None, sigma=None):
    period = period or cfg.BB_PERIOD
    sigma  = sigma  or cfg.BB_SIGMA
    basis  = close.rolling(period).mean()
    std    = close.rolling(period).std()
    return basis + sigma * std, basis, basis - sigma * std


def calc_rsi(close):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(cfg.RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(cfg.RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def calc_atr(df, p=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()


def calc_volume_spike(volume, period=20):
    avg = volume.rolling(period).mean()
    return volume / avg.replace(0, float("nan"))


def get_trend_htf(df_4h):
    if df_4h is None or len(df_4h) < cfg.BB_PERIOD + 5:
        return "neutral"
    _, basis_4h, _ = calc_bb(df_4h["close"])
    rsi_4h     = calc_rsi(df_4h["close"])
    cur_price  = float(df_4h["close"].iloc[-1])
    cur_basis  = float(basis_4h.iloc[-1])
    cur_rsi    = float(rsi_4h.iloc[-1])
    if cur_price > cur_basis and cur_rsi < 70:
        return "bull"
    elif cur_price < cur_basis and cur_rsi > 30:
        return "bear"
    return "neutral"


def get_signal(df, df_4h=None):
    if len(df) < cfg.BB_PERIOD + 15:
        return {"action": "hold", "reason": "datos insuficientes"}

    df = df.copy()
    upper, basis, lower = calc_bb(df["close"])
    df["basis"] = basis
    df["lower"] = lower
    df["upper"] = upper
    df["rsi"]   = calc_rsi(df["close"])
    df["atr"]   = calc_atr(df)
    df["vol_spike"] = calc_volume_spike(df["volume"]) if "volume" in df.columns else 1.0

    cur  = df.iloc[-1]
    prev = df.iloc[-2]
    price     = float(cur["close"])
    rsi       = float(cur["rsi"])
    atr       = float(cur["atr"])
    vol_spike = float(cur["vol_spike"]) if not pd.isna(cur["vol_spike"]) else 1.0

    # 5. Filtro volumen anomalo
    if cfg.VOLUME_SPIKE_ENABLED and vol_spike > cfg.VOLUME_SPIKE_MULT:
        return {"action": "hold", "entry": round(price,4), "rsi": round(rsi,1),
                "reason": f"Volumen anomalo x{vol_spike:.1f}"}

    # 2. Tendencia 4h
    trend = get_trend_htf(df_4h)

    # SEÑAL LONG
    bb_cross_long = float(prev["close"]) >= float(prev["lower"]) and price < float(cur["lower"])
    if bb_cross_long and rsi < cfg.RSI_OB and atr > 0:
        if trend == "bear":
            return {"action": "hold", "entry": round(price,4), "rsi": round(rsi,1),
                    "reason": "LONG bloqueado: tendencia 4h bajista"}
        sl      = price - cfg.SL_ATR * atr
        tp_full = float(cur["basis"])
        tp_part = price + cfg.PARTIAL_TP_ATR * atr
        return {"action": "buy", "entry": round(price,4), "sl": round(sl,4),
                "tp": round(tp_full,4), "tp_partial": round(tp_part,4),
                "rsi": round(rsi,1), "atr": round(atr,4),
                "bb_lower": round(float(cur["lower"]),4),
                "bb_basis": round(float(cur["basis"]),4),
                "trend_4h": trend,
                "reason": f"LONG BB inferior | RSI={round(rsi,1)} | 4h={trend}"}

    # SEÑAL SHORT
    if cfg.SHORT_ENABLED:
        bb_cross_short = float(prev["close"]) <= float(prev["upper"]) and price > float(cur["upper"])
        if bb_cross_short and rsi > cfg.RSI_OS and atr > 0:
            if trend == "bull":
                return {"action": "hold", "entry": round(price,4), "rsi": round(rsi,1),
                        "reason": "SHORT bloqueado: tendencia 4h alcista"}
            sl      = price + cfg.SL_ATR * atr
            tp_full = float(cur["basis"])
            tp_part = price - cfg.PARTIAL_TP_ATR * atr
            return {"action": "sell_short", "entry": round(price,4), "sl": round(sl,4),
                    "tp": round(tp_full,4), "tp_partial": round(tp_part,4),
                    "rsi": round(rsi,1), "atr": round(atr,4),
                    "bb_upper": round(float(cur["upper"]),4),
                    "bb_basis": round(float(cur["basis"]),4),
                    "trend_4h": trend,
                    "reason": f"SHORT BB superior | RSI={round(rsi,1)} | 4h={trend}"}

    # SALIDA LONG
    if float(prev["close"]) <= float(prev["basis"]) and price > float(cur["basis"]):
        return {"action": "exit_long", "entry": round(price,4), "rsi": round(rsi,1),
                "reason": "LONG exit: cruzo media BB arriba"}

    # SALIDA SHORT
    if float(prev["close"]) >= float(prev["basis"]) and price < float(cur["basis"]):
        return {"action": "exit_short", "entry": round(price,4), "rsi": round(rsi,1),
                "reason": "SHORT exit: cruzo media BB abajo"}

    return {"action": "hold", "entry": round(price,4), "rsi": round(rsi,1), "reason": "Sin señal"}


def calc_trailing_stop(pos, cur_price, atr):
    """Mueve el SL en favor de la operacion. Nunca en contra."""
    side       = pos.get("side", "long")
    old_sl     = pos["sl"]
    entry      = pos["entry"]
    trail_dist = cfg.TRAILING_STOP_ATR * atr
    if side == "long":
        if cur_price < entry * (1 + cfg.TRAILING_ACTIVATE_PCT / 100):
            return old_sl
        return max(cur_price - trail_dist, old_sl)
    else:
        if cur_price > entry * (1 - cfg.TRAILING_ACTIVATE_PCT / 100):
            return old_sl
        return min(cur_price + trail_dist, old_sl)
