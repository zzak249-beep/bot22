"""
symbols_loader.py — Carga TODOS los pares de BingX Futuros
════════════════════════════════════════════════════════════
Al arrancar el bot, obtiene la lista completa de contratos
perpetuos USDT de BingX (~300+ pares) y actualiza config.PARES.

Filtros aplicados:
  - Solo pares USDT perpetuos activos
  - Volumen 24h > VOLUMEN_MIN_USD (evita pares muertos)
  - Se refresca cada 24h automáticamente
"""

import time
import logging
import requests

log = logging.getLogger("symbols_loader")

try:
    import config as cfg
except Exception:
    cfg = None

BASE_URL      = "https://open-api.bingx.com"
_last_load    = 0
_REFRESH_H    = 24
_symbol_stats = {}

# Pares bloqueados (muy baja liquidez o problemáticos)
_BLACKLIST = {"LUNA-USDT", "LUNC-USDT", "BUSD-USDT"}


def needs_refresh() -> bool:
    return (time.time() - _last_load) > _REFRESH_H * 3600


def load_symbols(force: bool = False) -> list:
    """
    Carga todos los pares perpetuos USDT de BingX.
    Retorna lista en formato "BTC-USDT" y actualiza config.PARES.
    """
    global _last_load, _symbol_stats

    if not force and not needs_refresh():
        return cfg.PARES if cfg else []

    log.info("Cargando todos los pares de BingX Futuros...")
    symbols = []

    # ── Endpoint 1: contratos perpetuos swap ──────────────
    try:
        r = requests.get(
            f"{BASE_URL}/openApi/swap/v2/quote/contracts",
            timeout=12
        ).json()
        items = r if isinstance(r, list) else r.get("data", [])
        for item in items:
            sym = item.get("symbol", item.get("contractId", ""))
            if not sym:
                continue
            # Solo USDT perpetuos
            if not sym.endswith("-USDT"):
                sym_try = item.get("symbol", "")
                if "USDT" in sym_try:
                    sym = sym_try.replace("USDT", "-USDT") if "-" not in sym_try else sym_try
                else:
                    continue
            if sym in _BLACKLIST:
                continue
            vol = float(item.get("volume24h", item.get("quoteVolume24h", 0)) or 0)
            _symbol_stats[sym] = {"volume24h": vol}
            symbols.append(sym)
        log.info(f"Endpoint 1: {len(symbols)} contratos")
    except Exception as e:
        log.warning(f"Endpoint 1 falló: {e}")

    # ── Endpoint 2: fallback ───────────────────────────────
    if not symbols:
        try:
            r = requests.get(
                f"{BASE_URL}/openApi/contract/v1/allContracts",
                timeout=12
            ).json()
            items = r if isinstance(r, list) else r.get("data", [])
            for item in items:
                sym = item.get("symbol", "")
                if sym and "USDT" in sym:
                    if "-" not in sym:
                        base = sym.replace("USDT", "")
                        sym  = f"{base}-USDT"
                    if sym not in _BLACKLIST:
                        vol = float(item.get("volume24h", 0) or 0)
                        _symbol_stats[sym] = {"volume24h": vol}
                        symbols.append(sym)
            log.info(f"Endpoint 2: {len(symbols)} contratos")
        except Exception as e:
            log.warning(f"Endpoint 2 falló: {e}")

    # ── Endpoint 3: ticker 24h (más confiable para volumen) ─
    if not symbols:
        try:
            r = requests.get(
                f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                timeout=12
            ).json()
            items = r.get("data", r if isinstance(r, list) else [])
            for item in items:
                sym = item.get("symbol", "")
                if sym and sym.endswith("-USDT") and sym not in _BLACKLIST:
                    vol = float(item.get("quoteVolume", 0) or 0)
                    _symbol_stats[sym] = {"volume24h": vol}
                    symbols.append(sym)
            log.info(f"Endpoint 3: {len(symbols)} contratos")
        except Exception as e:
            log.warning(f"Endpoint 3 falló: {e}")

    # ── Deduplicar y filtrar por volumen ───────────────────
    symbols = list(dict.fromkeys(symbols))   # mantiene orden, elimina duplicados

    vol_min = getattr(cfg, "VOLUMEN_MIN_USD", 500_000) if cfg else 500_000
    if _symbol_stats:
        con_vol = [s for s in symbols
                   if _symbol_stats.get(s, {}).get("volume24h", 0) >= vol_min]
        if len(con_vol) >= 20:
            symbols = con_vol
            log.info(f"Después de filtro volumen >{vol_min:,.0f}$: {len(symbols)} pares")

    # ── Ordenar: mayor volumen primero ─────────────────────
    symbols.sort(
        key=lambda s: _symbol_stats.get(s, {}).get("volume24h", 0),
        reverse=True
    )

    # ── Fallback si no se cargó nada ───────────────────────
    if len(symbols) < 10:
        log.warning("Fallback a lista de pares predefinida")
        symbols = [
            "BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
            "DOGE-USDT","AVAX-USDT","LINK-USDT","ADA-USDT","DOT-USDT",
            "MATIC-USDT","LTC-USDT","UNI-USDT","ATOM-USDT","NEAR-USDT",
            "ARB-USDT","OP-USDT","APT-USDT","SUI-USDT","INJ-USDT",
        ]

    # ── Actualizar config.PARES y config.SYMBOLS en runtime ─
    if cfg:
        cfg.PARES   = symbols
        cfg.SYMBOLS = [s.replace("-", "/") for s in symbols]
        log.info(f"✅ config.PARES actualizado: {len(symbols)} pares")

    _last_load = time.time()
    return symbols


def get_symbol_stats() -> dict:
    return _symbol_stats


def get_top_n(n: int = 50) -> list:
    """Retorna los N pares con mayor volumen."""
    stats = _symbol_stats
    if not stats:
        return (cfg.PARES[:n] if cfg else [])
    sorted_syms = sorted(stats, key=lambda s: stats[s].get("volume24h", 0), reverse=True)
    return sorted_syms[:n]
