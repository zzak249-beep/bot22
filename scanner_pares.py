"""
scanner_pares.py — Escáner de pares BingX con filtro de volumen 24h
SMC Bot v5.0 [MetaClaw Edition]
"""
import logging
import time

import requests

import config

log = logging.getLogger("scanner")

_cache_pares: list = []
_cache_ts:    float = 0.0
_CACHE_TTL:   int   = 3600  # 1 hora


def get_pares_cached(vol_min: float = 200_000.0) -> list:
    global _cache_pares, _cache_ts
    if _cache_pares and (time.time() - _cache_ts) < _CACHE_TTL:
        return _cache_pares
    pares = _fetch_pares_bingx(vol_min)
    if pares:
        _cache_pares = pares
        _cache_ts    = time.time()
        log.info(f"[SCAN] {len(pares)} pares con vol>${vol_min/1e6:.1f}M")
    else:
        log.warning("[SCAN] Fallo fetch BingX — usando lista fija")
        try:
            from config_pares import PARES
            _cache_pares = PARES
        except Exception:
            _cache_pares = _PARES_FALLBACK
    return _cache_pares


def _fetch_pares_bingx(vol_min: float) -> list:
    try:
        resp = requests.get(
            "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
            timeout=12,
        )
        data = resp.json()
        tickers = data.get("data", []) or []
        if not tickers:
            # Intentar endpoint alternativo
            resp2 = requests.get(
                "https://open-api.bingx.com/openApi/swap/v2/quote/contracts",
                timeout=10,
            )
            tickers = resp2.json().get("data", []) or []

        pares = []
        prios = set(config.PARES_PRIORITARIOS)
        bloq  = set(config.PARES_BLOQUEADOS)

        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            if sym in bloq:
                continue
            # Filtro de volumen
            vol = float(
                t.get("quoteVolume",
                t.get("volume",
                t.get("turnover", 0))) or 0
            )
            if vol >= vol_min or sym in prios:
                pares.append((sym, vol))

        # Ordenar: prioritarios primero, luego por volumen desc
        pares.sort(key=lambda x: (0 if x[0] in prios else 1, -x[1]))
        return [p for p, _ in pares]
    except Exception as e:
        log.warning(f"[SCAN] _fetch_pares_bingx: {e}")
        return []


# Lista de fallback si la API falla
_PARES_FALLBACK = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT",
    "XRP-USDT", "ADA-USDT", "AVAX-USDT", "DOGE-USDT",
    "DOT-USDT", "LINK-USDT", "UNI-USDT", "ATOM-USDT",
    "NEAR-USDT", "APT-USDT", "SUI-USDT", "ARB-USDT",
    "OP-USDT", "PEPE-USDT", "WIF-USDT", "TRX-USDT",
    "TON-USDT", "INJ-USDT", "IMX-USDT", "STX-USDT",
    "LTC-USDT", "BCH-USDT", "FIL-USDT", "AAVE-USDT",
]
