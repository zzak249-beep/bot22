#!/usr/bin/env python3
"""
data_feed.py v4.0 — Descarga y cachea velas de BingX
"""

import pandas as pd
import time
from bingx_api import fetch_klines

_cache: dict = {}
_CACHE_TTL = 60  # segundos


def get_df(symbol: str, interval: str = "30m", limit: int = 300) -> pd.DataFrame:
    """
    Descarga klines de BingX y devuelve un DataFrame OHLCV.
    Usa caché de 60s para evitar llamadas excesivas.
    """
    key = f"{symbol}_{interval}"
    now = time.time()

    if key in _cache:
        cached_time, cached_df = _cache[key]
        if now - cached_time < _CACHE_TTL:
            return cached_df

    raw = fetch_klines(symbol, interval=interval, limit=limit)
    if not raw:
        return pd.DataFrame()

    df = _parse_klines(raw)
    if not df.empty:
        _cache[key] = (now, df)

    return df


def _parse_klines(raw: list) -> pd.DataFrame:
    """
    Convierte lista de klines (BingX formato v2 o v3) a DataFrame.
    Formatos soportados:
      - Lista de listas: [timestamp, open, high, low, close, volume]
      - Lista de dicts:  {"time": .., "open": .., "high": .., "low": .., "close": .., "volume": ..}
    """
    if not raw:
        return pd.DataFrame()

    try:
        rows = []
        for k in raw:
            if isinstance(k, (list, tuple)) and len(k) >= 6:
                rows.append({
                    "timestamp": int(k[0]),
                    "open":      float(k[1]),
                    "high":      float(k[2]),
                    "low":       float(k[3]),
                    "close":     float(k[4]),
                    "volume":    float(k[5]),
                })
            elif isinstance(k, dict):
                rows.append({
                    "timestamp": int(k.get("time",   k.get("t", 0))),
                    "open":      float(k.get("open",  k.get("o", 0))),
                    "high":      float(k.get("high",  k.get("h", 0))),
                    "low":       float(k.get("low",   k.get("l", 0))),
                    "close":     float(k.get("close", k.get("c", 0))),
                    "volume":    float(k.get("volume",k.get("v", 0))),
                })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    except Exception as e:
        print(f"[data_feed] parse error: {e}")
        return pd.DataFrame()


def clear_cache(symbol: str = None, interval: str = None):
    if symbol and interval:
        _cache.pop(f"{symbol}_{interval}", None)
    else:
        _cache.clear()
