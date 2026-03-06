import time
import pandas as pd
from bingx_api import fetch_klines

# ══════════════════════════════════════════════════════
# data_feed.py — Descarga y normaliza velas de BingX
# ══════════════════════════════════════════════════════


def get_df(symbol: str, interval: str = "1h", limit: int = 300) -> pd.DataFrame:
    """
    Retorna DataFrame limpio con columnas:
    ts, open, high, low, close, volume
    Vacío si no hay datos.
    """
    raw = fetch_klines(symbol, interval, limit)
    if not raw:
        return pd.DataFrame()

    rows = []
    for c in raw:
        if isinstance(c, list):
            rows.append(c[:6])
        else:
            rows.append([
                c.get("time")   or c.get("t",   0),
                c.get("open")   or c.get("o",   0),
                c.get("high")   or c.get("h",   0),
                c.get("low")    or c.get("l",   0),
                c.get("close")  or c.get("c",   0),
                c.get("volume") or c.get("v",   0),
            ])

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["ts"] = pd.to_datetime(pd.to_numeric(df["ts"], errors="coerce"), unit="ms")
    df = (df.dropna()
            .sort_values("ts")
            .drop_duplicates("ts")
            .reset_index(drop=True))

    if df.empty or df["close"].max() == 0:
        return pd.DataFrame()

    return df
