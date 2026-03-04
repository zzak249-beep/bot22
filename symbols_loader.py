# symbols_loader.py
import time
import logging
import requests

log = logging.getLogger("symbols_loader")

try:
    import config as cfg
except Exception:
    cfg = None

_last_load     = 0
_REFRESH_HOURS = 24
_symbol_stats  = {}


def needs_refresh() -> bool:
    return (time.time() - _last_load) > _REFRESH_HOURS * 3600


def load_symbols(force=False) -> list:
    global _last_load, _symbol_stats

    if not force and not needs_refresh():
        return getattr(cfg, "SYMBOLS", [])

    # Si config tiene SYMBOLS definidos, usarlos siempre
    if cfg and hasattr(cfg, "SYMBOLS") and cfg.SYMBOLS:
        log.info(f"Usando {len(cfg.SYMBOLS)} pares de config.py")
        _last_load = time.time()
        return cfg.SYMBOLS

    log.info("Cargando pares desde BingX...")
    symbols = []
    try:
        urls = [
            "https://open-api.bingx.com/openApi/swap/v2/quote/contracts",
            "https://open-api.bingx.com/openApi/contract/v1/allContracts",
        ]
        for url in urls:
            r = requests.get(url, timeout=10).json()
            data = r if isinstance(r, list) else r.get("data", [])
            if not data:
                continue
            for item in data:
                sym = item.get("symbol") or item.get("contractId", "")
                if sym and sym.endswith("-USDT"):
                    vol = float(item.get("volume24h") or item.get("quoteVolume24h") or 0)
                    _symbol_stats[sym] = {"volume24h": vol}
                    symbols.append(sym)
            if symbols:
                break
    except Exception as e:
        log.warning(f"Error cargando pares: {e}")

    if not symbols:
        symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "LINK-USDT"]

    symbols = sorted(list(set(symbols)))
    if cfg:
        cfg.SYMBOLS = symbols

    _last_load = time.time()
    log.info(f"Cargados {len(symbols)} pares")
    return symbols


def get_symbol_stats() -> dict:
    return _symbol_stats
