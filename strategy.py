"""
strategy.py — Indicadores y logica de señales BB+RSI
"""
import pandas as pd
import numpy as np
import config as cfg


# ═══════════════════════════════════════════════════════════
#  INDICADORES
# ═══════════════════════════════════════════════════════════

def calc_bb(close: pd.Series):
    basis = close.rolling(cfg.BB_PERIOD).mean()
    std   = close.rolling(cfg.BB_PERIOD).std()
    return basis + cfg.BB_SIGMA * std, basis, basis - cfg.BB_SIGMA * std


def calc_rsi(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(cfg.RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(cfg.RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def calc_atr(df: pd.DataFrame, p: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()


# ═══════════════════════════════════════════════════════════
#  SEÑAL PRINCIPAL
# ═══════════════════════════════════════════════════════════

def get_signal(df: pd.DataFrame) -> dict:
    """
    Analiza el DataFrame de velas y devuelve la señal.

    Retorna dict con keys:
      action : "buy" | "exit" | "hold"
      entry  : precio actual
      sl     : stop loss
      tp     : take profit (media BB)
      rsi    : valor RSI actual
      reason : descripcion de la señal
    """
    min_bars = cfg.BB_PERIOD + 15
    if len(df) < min_bars:
        return {"action": "hold", "reason": "datos insuficientes"}

    df = df.copy()
    upper, basis, lower = calc_bb(df["close"])
    df["basis"] = basis
    df["lower"] = lower
    df["upper"] = upper
    df["rsi"]   = calc_rsi(df["close"])
    df["atr"]   = calc_atr(df)

    cur  = df.iloc[-1]
    prev = df.iloc[-2]

    price = float(cur["close"])
    rsi   = float(cur["rsi"])
    atr   = float(cur["atr"])

    # ── ENTRADA: precio cruza por debajo de banda inferior ──
    bb_cross = float(prev["close"]) >= float(prev["lower"]) and price < float(cur["lower"])
    rsi_ok   = rsi < cfg.RSI_OB
    atr_ok   = atr > 0

    if bb_cross and rsi_ok and atr_ok:
        sl = price - cfg.SL_ATR * atr
        tp = float(cur["basis"])
        return {
            "action": "buy",
            "entry":  round(price, 4),
            "sl":     round(sl, 4),
            "tp":     round(tp, 4),
            "rsi":    round(rsi, 1),
            "atr":    round(atr, 4),
            "bb_lower": round(float(cur["lower"]), 4),
            "bb_basis": round(float(cur["basis"]), 4),
            "reason": f"Precio cruzo banda inferior BB | RSI={round(rsi,1)}"
        }

    # ── SALIDA: precio cruza la media hacia arriba ──
    basis_cross = float(prev["close"]) <= float(prev["basis"]) and price > float(cur["basis"])
    if basis_cross:
        return {
            "action": "exit",
            "entry":  round(price, 4),
            "rsi":    round(rsi, 1),
            "reason": "Precio cruzo la media BB hacia arriba"
        }

    return {
        "action": "hold",
        "entry":  round(price, 4),
        "rsi":    round(rsi, 1),
        "reason": "Sin señal"
    }
