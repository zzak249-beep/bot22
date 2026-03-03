"""
symbols_loader.py — Carga todos los pares de futuros USDT de BingX.
Usa BingX directamente para evitar bloqueos de IP (Railway).
"""

import logging
import time
import ccxt
import config as cfg

log = logging.getLogger("symbols")

REFRESH_HOURS = 6
_last_refresh = 0.0


def load_symbols(force: bool = False) -> list:
    global _last_refresh

    # Override manual desde .env
    if cfg.SYMBOLS_OVERRIDE:
        symbols = [s.strip() for s in cfg.SYMBOLS_OVERRIDE.split(",") if s.strip()]
        cfg.SYMBOLS = symbols
        log.info(f"Simbolos manuales desde .env: {len(symbols)} pares")
        return symbols

    now = time.time()
    if not force and cfg.SYMBOLS and (now - _last_refresh) < REFRESH_HOURS * 3600:
        return cfg.SYMBOLS

    log.info("Cargando pares de futuros USDT desde BingX...")

    try:
        ex = ccxt.bingx({
            "apiKey":  cfg.BINGX_API_KEY,
            "secret":  cfg.BINGX_SECRET,
            "options": {"defaultType": "swap"},
        })

        markets = ex.load_markets()

        # Obtener tickers para volumen (en lotes para no saturar)
        try:
            tickers = ex.fetch_tickers()
        except Exception:
            tickers = {}

        candidates = []
        for symbol, market in markets.items():
            if not market.get("active"):
                continue
            if market.get("quote") != cfg.QUOTE_CURRENCY:
                continue
            if market.get("type") not in ("swap", "future"):
                continue
            if symbol in cfg.EXCLUDE_SYMBOLS:
                continue

            ticker   = tickers.get(symbol, {})
            vol_usdt = float(ticker.get("quoteVolume") or ticker.get("baseVolume") or 0)

            # Solo filtrar por volumen si MIN_VOLUME_USDT > 0 (0 = TODOS los pares)
            if cfg.MIN_VOLUME_USDT > 0 and vol_usdt > 0 and vol_usdt < cfg.MIN_VOLUME_USDT:
                continue

            candidates.append((symbol, vol_usdt))

        # Ordenar: con volumen primero (descendente), sin volumen al final
        candidates.sort(key=lambda x: x[1], reverse=True)
        symbols = [s for s, _ in candidates]

        if cfg.MAX_SYMBOLS and cfg.MAX_SYMBOLS > 0:
            symbols = symbols[:cfg.MAX_SYMBOLS]

        cfg.SYMBOLS = symbols
        _last_refresh = now

        log.info(f"Pares encontrados: {len(symbols)}")
        log.info(f"Top 10: {', '.join(symbols[:10])}")
        return symbols

    except Exception as e:
        log.error(f"Error cargando simbolos: {e}")
        if not cfg.SYMBOLS:
            fallback = [
                "BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT","XRP/USDT",
                "DOGE/USDT","ADA/USDT","AVAX/USDT","DOT/USDT","MATIC/USDT",
            ]
            cfg.SYMBOLS = fallback
            log.warning(f"Usando fallback de {len(fallback)} pares")
        return cfg.SYMBOLS


def needs_refresh() -> bool:
    return (time.time() - _last_refresh) >= REFRESH_HOURS * 3600


def get_symbol_stats() -> str:
    if cfg.MIN_VOLUME_USDT > 0:
        return f"{len(cfg.SYMBOLS)} pares activos (vol > ${cfg.MIN_VOLUME_USDT/1e6:.0f}M USDT)"
    return f"{len(cfg.SYMBOLS)} pares activos (TODOS los pares BingX)"
