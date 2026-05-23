"""
scanner.py — Scanner paralelo con contexto de mercado completo

Ventaja: obtiene funding rate + order book imbalance de los mejores
candidatos antes de decidir, eliminando entradas en mercado caro/saturado.
"""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone

import numpy as np

from bingx_client import BingXClient
from strategy import EdgeStrategy, Candle, Signal, MarketContext
from config import cfg

log = logging.getLogger("scanner")


def _to_candles(raw: list[dict]) -> list[Candle]:
    return [Candle(**c) for c in raw]


# Whitelist de alta liquidez — los únicos donde el edge funciona
WHITELIST_HQ = {
    "BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
    "ADA-USDT","DOGE-USDT","AVAX-USDT","LINK-USDT","DOT-USDT",
    "UNI-USDT","ATOM-USDT","NEAR-USDT","APT-USDT","ARB-USDT",
    "OP-USDT","SUI-USDT","INJ-USDT","TIA-USDT","JUP-USDT",
    "WIF-USDT","PEPE-USDT","BONK-USDT","LTC-USDT","BCH-USDT",
    "FIL-USDT","AAVE-USDT","CRV-USDT","ONDO-USDT","ENA-USDT",
}


class Scanner:
    def __init__(self, client: BingXClient):
        self.client   = client
        self.strategy = EdgeStrategy(cfg)
        self._cooldown: dict[str, int]   = {}
        self._daily:    dict[str, int]   = {}
        self._scan_n:   int              = 0

    # ── Símbolos ──────────────────────────────────────────────

    async def get_symbols(self) -> list[str]:
        if cfg.SYMBOLS:
            return cfg.SYMBOLS
        try:
            all_syms = await self.client.get_symbols()
            if cfg.LIQUIDITY_MODE == "high_only":
                return [s for s in all_syms if s in WHITELIST_HQ]
            # Modo ampliado: filtrar stablecoins y micro-caps
            exclude = {"USDC","BUSD","TUSD","DAI","FDUSD","USDP",
                       "USDT","USTC","LUNA","LUNC","UST"}
            return [s for s in all_syms
                    if not any(s.startswith(e) for e in exclude)]
        except Exception as e:
            log.error("get_symbols: %s", e)
            return list(WHITELIST_HQ)

    # ── Filtros de acceso ─────────────────────────────────────

    def _cooldown_ok(self, symbol: str, bar_idx: int) -> bool:
        return (bar_idx - self._cooldown.get(symbol, -999)) >= cfg.COOLDOWN_BARS

    def _daily_ok(self, symbol: str) -> bool:
        today = datetime.now(timezone.utc).date().isoformat()
        return self._daily.get(f"{symbol}:{today}", 0) < cfg.MAX_SIGNALS_DAY

    def _record(self, symbol: str, bar_idx: int):
        today = datetime.now(timezone.utc).date().isoformat()
        key   = f"{symbol}:{today}"
        self._daily[key]    = self._daily.get(key, 0) + 1
        self._cooldown[symbol] = bar_idx

    # ── Liquidez mínima ───────────────────────────────────────

    def _liquid(self, candles: list[Candle]) -> bool:
        if len(candles) < 20:
            return False
        vols = [c.volume * c.close for c in candles[-30:]]
        return float(np.mean(vols)) >= cfg.MIN_VOL_USDT

    # ── Contexto de mercado (funding + OB imbalance) ──────────

    async def _market_context(self, symbol: str) -> MarketContext:
        try:
            funding, ob_imb = await asyncio.gather(
                self.client.get_funding_rate(symbol),
                self.client.get_orderbook_imbalance(symbol, limit=10),
            )
            # OB imbalance → volume imbalance proxy
            vim = ob_imb / (ob_imb + 1) if ob_imb > 0 else 0.5
            return MarketContext(
                funding_rate      = funding,
                volume_imbalance  = vim,
            )
        except Exception:
            return MarketContext()

    # ── Scan de un símbolo ────────────────────────────────────

    async def _scan_one(self, symbol: str) -> tuple[str, Signal | None]:
        try:
            # Descargar 3 timeframes en paralelo
            raw3m, raw15m, raw1h = await asyncio.gather(
                self.client.get_klines(symbol, "3m",  200),
                self.client.get_klines(symbol, "15m", 60),
                self.client.get_klines(symbol, "1h",  220),
            )

            if len(raw3m) < 60 or len(raw15m) < 15:
                return symbol, None

            c3m  = _to_candles(raw3m)
            c15m = _to_candles(raw15m)
            c1h  = _to_candles(raw1h) if raw1h else []

            if not self._liquid(c3m):
                return symbol, None
            if not self._cooldown_ok(symbol, len(c3m)):
                return symbol, None
            if not self._daily_ok(symbol):
                return symbol, None

            # Contexto de mercado
            ctx = await self._market_context(symbol)

            sig = self.strategy.evaluate(symbol, c3m, c15m, c1h, ctx)
            if sig:
                self._record(symbol, len(c3m))
            return symbol, sig

        except Exception as e:
            log.debug("scan_one %s: %s", symbol, e)
            return symbol, None

    # ── Scan completo ─────────────────────────────────────────

    async def run(self, symbols: list[str] | None = None) -> list[Signal]:
        self._scan_n += 1
        t0  = time.time()
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log.info("▶ Scan #%d — %s UTC", self._scan_n, now)

        if symbols is None:
            symbols = await self.get_symbols()

        if not symbols:
            log.warning("Sin símbolos.")
            return []

        signals: list[Signal] = []
        batch_size = 8

        # Diagnóstico por categoría
        diag = {"fetch_err":0, "liquidity":0, "no_sig":0, "signals":0}

        for i in range(0, len(symbols), batch_size):
            batch   = symbols[i:i + batch_size]
            results = await asyncio.gather(
                *[self._scan_one(s) for s in batch],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    diag["fetch_err"] += 1
                    continue
                _, sig = r
                if sig:
                    signals.append(sig)
                    diag["signals"] += 1
                else:
                    diag["no_sig"] += 1
            await asyncio.sleep(0.4)

        elapsed = time.time() - t0
        log.info(
            "✔ Scan #%d — %.1fs | %d símbolos | %d señal(es) | "
            "fetch_err=%d no_sig=%d",
            self._scan_n, elapsed, len(symbols),
            diag["signals"], diag["fetch_err"], diag["no_sig"],
        )

        # Ordenar por score descendente
        return sorted(signals, key=lambda s: s.score, reverse=True)
