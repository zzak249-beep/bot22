"""
symbols_loader.py v2 — Filtros inteligentes de volumen, spread y blacklist.
Cuando no hay fondos, el bot sigue escaneando y enviando señales a Telegram
para que las ejecutes manualmente.
"""
import logging
import time
import ccxt
import config as cfg

log = logging.getLogger("symbols")

REFRESH_HOURS = 6
_last_refresh = 0.0

# Tokens de baja liquidez/calidad detectados en tu historial — siempre excluidos
BLACKLIST_KEYWORDS = [
    "MAXXING", "PUNCH", "NAORIS", "CRTR", "NEET", "ROBO",
    "EPIC", "ARC", "OPN", "PEPE2", "TURBO", "MEME",
]


def _is_blacklisted(symbol: str) -> bool:
    base = symbol.split("/")[0].upper()
    return any(kw in base for kw in BLACKLIST_KEYWORDS)


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

        try:
            tickers = ex.fetch_tickers()
        except Exception:
            tickers = {}

        candidates     = []
        excluded_vol   = 0
        excluded_black = 0
        excluded_spread = 0

        for symbol, market in markets.items():
            if not market.get("active"):
                continue
            if market.get("quote") != cfg.QUOTE_CURRENCY:
                continue
            if market.get("type") not in ("swap", "future"):
                continue
            if symbol in cfg.EXCLUDE_SYMBOLS:
                continue

            # ── Blacklist tokens de baja calidad ──────────────
            if _is_blacklisted(symbol):
                excluded_black += 1
                continue

            ticker   = tickers.get(symbol, {})
            vol_usdt = float(ticker.get("quoteVolume") or ticker.get("baseVolume") or 0)

            # ── Filtro volumen mínimo ──────────────────────────
            if cfg.MIN_VOLUME_USDT > 0 and vol_usdt > 0 and vol_usdt < cfg.MIN_VOLUME_USDT:
                excluded_vol += 1
                continue

            # ── Filtro spread: descarta pares ilíquidos ────────
            ask = float(ticker.get("ask") or 0)
            bid = float(ticker.get("bid") or 0)
            if ask > 0 and bid > 0:
                spread_pct = (ask - bid) / bid * 100
                if spread_pct > 1.5:   # spread > 1.5% = ilíquido
                    excluded_spread += 1
                    continue

            candidates.append((symbol, vol_usdt))

        # Ordenar por volumen descendente (más líquidos primero)
        candidates.sort(key=lambda x: x[1], reverse=True)
        symbols = [s for s, _ in candidates]

        if cfg.MAX_SYMBOLS and cfg.MAX_SYMBOLS > 0:
            symbols = symbols[:cfg.MAX_SYMBOLS]

        cfg.SYMBOLS   = symbols
        _last_refresh = now

        log.info(f"Pares cargados    : {len(symbols)}")
        log.info(f"Excl. vol<min     : {excluded_vol}")
        log.info(f"Excl. blacklist   : {excluded_black}")
        log.info(f"Excl. spread>1.5% : {excluded_spread}")
        log.info(f"Top 10            : {', '.join(symbols[:10])}")
        return symbols

    except Exception as e:
        log.error(f"Error cargando simbolos: {e}")
        if not cfg.SYMBOLS:
            fallback = [
                "BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT",
                "SOL/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
                "ADA/USDT:USDT", "AVAX/USDT:USDT", "DOT/USDT:USDT",
                "MATIC/USDT:USDT",
            ]
            cfg.SYMBOLS = fallback
            log.warning(f"Usando fallback de {len(fallback)} pares")
        return cfg.SYMBOLS


def needs_refresh() -> bool:
    return (time.time() - _last_refresh) >= REFRESH_HOURS * 3600


def get_symbol_stats() -> str:
    if cfg.MIN_VOLUME_USDT > 0:
        return (f"{len(cfg.SYMBOLS)} pares activos "
                f"(vol>${cfg.MIN_VOLUME_USDT/1e6:.1f}M | spread<1.5%)")
    return f"{len(cfg.SYMBOLS)} pares activos (TODOS los pares BingX)"