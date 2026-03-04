"""
symbols_loader.py — Carga y refresco dinámico de pares de trading
Compatible con BB+RSI BOT ELITE v6
"""
import logging
from datetime import datetime, timedelta

import config as cfg
import exchange as ex

log = logging.getLogger("symbols_loader")

_last_load:   datetime | None = None
_symbol_stats: dict = {}

REFRESH_HOURS = 24   # Refrescar lista de pares cada 24h


def needs_refresh() -> bool:
    """True si hay que recargar la lista de símbolos."""
    if _last_load is None:
        return True
    return datetime.now() - _last_load > timedelta(hours=REFRESH_HOURS)


def load_symbols(force: bool = False) -> list:
    """
    Carga los pares activos desde el exchange y actualiza cfg.SYMBOLS.
    Si force=False y la lista ya está cargada, devuelve la lista actual.
    Retorna la lista de símbolos cargados.
    """
    global _last_load, _symbol_stats

    if not force and cfg.SYMBOLS and not needs_refresh():
        return cfg.SYMBOLS

    log.info("Cargando lista de pares desde el exchange...")

    try:
        exchange   = ex.get_exchange()
        markets    = exchange.load_markets()

        # Filtrar: futuros perpetuos USDT activos con volumen razonable
        candidates = []
        for sym, mkt in markets.items():
            if not sym.endswith("/USDT"):
                continue
            if not mkt.get("active", False):
                continue
            # Algunos exchanges marcan futuros con 'swap' o 'future'
            mkt_type = mkt.get("type", "")
            if mkt_type not in ("swap", "future", "spot"):
                continue
            candidates.append(sym)

        if candidates:
            cfg.SYMBOLS = sorted(candidates)
            log.info(f"Pares cargados: {len(cfg.SYMBOLS)}")
        else:
            # Fallback a lista estática si el exchange no devuelve nada útil
            log.warning("Exchange no devolvió pares válidos — usando lista estática")
            if not cfg.SYMBOLS:
                cfg.SYMBOLS = _default_symbols()

        # Estadísticas básicas
        _symbol_stats = {
            "total":    len(cfg.SYMBOLS),
            "source":   "exchange" if candidates else "static",
            "loaded_at": datetime.now().strftime("%d/%m %H:%M"),
        }

    except Exception as e:
        log.error(f"Error cargando símbolos: {e}")
        if not cfg.SYMBOLS:
            cfg.SYMBOLS = _default_symbols()
            log.info(f"Usando {len(cfg.SYMBOLS)} pares por defecto")
        _symbol_stats = {
            "total":    len(cfg.SYMBOLS),
            "source":   "fallback",
            "loaded_at": datetime.now().strftime("%d/%m %H:%M"),
        }

    _last_load = datetime.now()
    return cfg.SYMBOLS


def get_symbol_stats() -> dict:
    """Retorna estadísticas de la última carga (para Telegram startup)."""
    return _symbol_stats


def _default_symbols() -> list:
    """Lista de pares por defecto si el exchange falla."""
    return [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "ARB/USDT", "OP/USDT",
        "MATIC/USDT", "NEAR/USDT", "APT/USDT", "SUI/USDT", "PEPE/USDT",
        "TON/USDT", "WIF/USDT", "FLOKI/USDT", "SHIB/USDT", "UNI/USDT",
    ]
