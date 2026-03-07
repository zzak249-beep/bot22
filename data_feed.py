"""
data_feed.py — Obtención de datos OHLCV de BingX v14.0
"""
import logging
import requests
import pandas as pd
import numpy as np

log = logging.getLogger("data_feed")

BASE_URL = "https://open-api.bingx.com"

# Mapa de intervalos legibles → formato BingX
_TF_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
    "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h",
    "6h": "6h", "12h": "12h", "1d": "1d", "1w": "1w",
}


def _fetch_klines(symbol: str, interval: str, limit: int) -> list:
    """Llama a BingX y devuelve la lista raw de klines."""
    par = symbol.replace("/", "-")
    tf  = _TF_MAP.get(interval, interval)
    try:
        r = requests.get(
            f"{BASE_URL}/openApi/swap/v3/quote/klines",
            params={"symbol": par, "interval": tf, "limit": limit},
            timeout=12
        )
        data = r.json()
        return data.get("data", []) if isinstance(data, dict) else data
    except Exception as e:
        log.warning(f"data_feed {symbol} {interval}: {e}")
        return []


def _klines_to_df(klines: list) -> pd.DataFrame:
    rows = []
    for k in klines:
        try:
            if isinstance(k, dict):
                ts = int(k.get("time", k.get("t", 0)))
                o  = float(k.get("open",   k.get("o", 0)))
                h  = float(k.get("high",   k.get("h", 0)))
                lo = float(k.get("low",    k.get("l", 0)))
                c  = float(k.get("close",  k.get("c", 0)))
                v  = float(k.get("volume", k.get("v", 0)))
            elif isinstance(k, (list, tuple)) and len(k) >= 6:
                ts, o, h, lo, c, v = int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
            else:
                continue
            if c > 0:
                rows.append({"time": ts, "open": o, "high": h, "low": lo, "close": c, "volume": v})
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    return df


def get_df(symbol: str, interval: str = "15m", limit: int = 250) -> pd.DataFrame:
    """Devuelve DataFrame OHLCV listo para indicadores. Vacío si falla."""
    klines = _fetch_klines(symbol, interval, limit)
    if not klines:
        return pd.DataFrame()
    df = _klines_to_df(klines)
    if len(df) < 30:
        return pd.DataFrame()
    return df
