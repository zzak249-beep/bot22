"""
indicators.py — Indicadores técnicos v14.0
Usado por strategy.py
"""
import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Añade BB, RSI, ATR, EMA/SMA, MACD, Stoch al DataFrame."""
    df = df.copy()
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    v = df["volume"].astype(float) if "volume" in df.columns else pd.Series(1.0, index=df.index)

    # ── Bollinger Bands (20, 2σ) ──────────────────────
    sma20  = c.rolling(20).mean()
    std20  = c.rolling(20).std()
    df["basis"] = sma20
    df["upper"] = sma20 + 2.0 * std20
    df["lower"] = sma20 - 2.0 * std20

    # ── SMA50 ─────────────────────────────────────────
    df["sma50"] = c.rolling(50).mean()

    # ── EMA50 ─────────────────────────────────────────
    df["ema50"] = c.ewm(span=50, adjust=False).mean()

    # ── RSI 14 ────────────────────────────────────────
    delta = c.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    ag    = gain.ewm(com=13, adjust=False).mean()
    al    = loss.ewm(com=13, adjust=False).mean()
    rs    = ag / al.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)

    # ── ATR 14 ────────────────────────────────────────
    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(com=13, adjust=False).mean()

    # ── MACD (12,26,9) ────────────────────────────────
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"]   = ema12 - ema26
    df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    # ── Stochastic RSI (14,3) ─────────────────────────
    rsi_ser = df["rsi"]
    min_rsi = rsi_ser.rolling(14).min()
    max_rsi = rsi_ser.rolling(14).max()
    rng = (max_rsi - min_rsi).replace(0, np.nan)
    stoch_raw = (rsi_ser - min_rsi) / rng * 100
    df["stoch"] = stoch_raw.rolling(3).mean()

    # ── Volume ratio ──────────────────────────────────
    vol_ma = v.rolling(20).mean()
    df["vol_ratio"] = v / vol_ma.replace(0, np.nan)

    return df


def get_trend(series: pd.Series, idx: int, lookback: int = 8, thresh: float = 0.03) -> str:
    """'up' | 'down' | 'flat' basado en pendiente de los últimos N cierres."""
    start = max(0, idx - lookback)
    chunk = series.iloc[start:idx + 1].dropna()
    if len(chunk) < 3:
        return "flat"
    first = float(chunk.iloc[0])
    last  = float(chunk.iloc[-1])
    if first <= 0:
        return "flat"
    pct = (last - first) / first
    if pct > thresh:
        return "up"
    if pct < -thresh:
        return "down"
    return "flat"


def divergence(closes: pd.Series, rsi: pd.Series) -> str:
    """
    'bull' = precio hace mínimo más bajo pero RSI hace mínimo más alto → señal alcista
    'bear' = precio hace máximo más alto pero RSI hace máximo más bajo → señal bajista
    'none' = sin divergencia
    """
    closes = closes.dropna()
    rsi    = rsi.dropna()
    n = min(len(closes), len(rsi))
    if n < 4:
        return "none"
    c = closes.iloc[-n:].values
    r = rsi.iloc[-n:].values
    # buscar mínimos (para divergencia alcista)
    if c[-1] < c[0] and r[-1] > r[0] + 2:
        return "bull"
    # buscar máximos (para divergencia bajista)
    if c[-1] > c[0] and r[-1] < r[0] - 2:
        return "bear"
    return "none"


def volume_ok(df: pd.DataFrame, idx: int) -> bool:
    """Volumen actual >= 60% de la media de 20 velas."""
    if "vol_ratio" not in df.columns:
        return True
    vr = df["vol_ratio"].iloc[idx]
    if pd.isna(vr):
        return True
    return float(vr) >= 0.6


def momentum_bars(closes: pd.Series, idx: int, lookback: int = 5) -> int:
    """Número de velas bajistas en las últimas N (para confirmar entrada LONG)."""
    start = max(0, idx - lookback)
    chunk = closes.iloc[start:idx + 1]
    if len(chunk) < 2:
        return 0
    diffs = chunk.diff().dropna()
    return int((diffs < 0).sum())


def calc_score_long(rsi: float, div: str, macd_pos: bool,
                    stoch: float, bear_bars: int) -> int:
    score = 0
    # RSI cuanto más bajo mejor
    if rsi < 20:   score += 35
    elif rsi < 25: score += 28
    elif rsi < 30: score += 20
    elif rsi < 35: score += 12
    else:          score += 5

    # Divergencia alcista
    if div == "bull": score += 20

    # MACD positivo o recuperándose
    if macd_pos: score += 10

    # Stoch oversold
    if not pd.isna(stoch):
        if stoch < 20:   score += 15
        elif stoch < 35: score += 8

    # Momentum bajista (señal de reversión inminente)
    if bear_bars >= 4:   score += 15
    elif bear_bars >= 3: score += 8
    elif bear_bars >= 2: score += 3

    return min(score, 100)


def calc_score_short(rsi: float, div: str, macd_pos: bool,
                     stoch: float, bull_bars: int) -> int:
    score = 0
    if rsi > 80:   score += 35
    elif rsi > 75: score += 28
    elif rsi > 70: score += 20
    elif rsi > 65: score += 12
    else:          score += 5

    if div == "bear": score += 20
    if not macd_pos:  score += 10

    if not pd.isna(stoch):
        if stoch > 80:   score += 15
        elif stoch > 65: score += 8

    if bull_bars >= 4:   score += 15
    elif bull_bars >= 3: score += 8
    elif bull_bars >= 2: score += 3

    return min(score, 100)
