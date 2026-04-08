"""
Market Scanner
Scans all USDT perpetual pairs on BingX in parallel.
Filters by volume, ADX, trend quality. Returns ranked candidates.
"""
import logging, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import numpy as np

from bingx_client import BingXClient
from strategy import compute_signal, Signal, _rsi, _adx, _atr

log = logging.getLogger(__name__)

# ── Filter thresholds ──────────────────────────────────────────────────
MIN_24H_VOLUME_USDT  = 3_000_000   # $3M daily (lowered to find more signals)
MAX_SYMBOLS_TO_SCAN  = 150          # cap API calls
TOP_N_RESULTS        = 10           # return best N
SCAN_THREADS         = 10           # parallel workers
HTF_INTERVAL         = "1h"         # higher timeframe for RSI filter
LTF_INTERVAL         = "15m"        # entry timeframe
MIN_KLINES           = 200          # minimum candles required

# Priority symbols to always scan first (most liquid)
PRIORITY_SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "DOT-USDT", "MATIC-USDT",
    "LINK-USDT", "UNI-USDT", "LTC-USDT", "ATOM-USDT", "FIL-USDT",
    "NEAR-USDT", "APT-USDT", "OP-USDT", "ARB-USDT", "INJ-USDT",
]


@dataclass
class ScanResult:
    symbol:     str
    signal:     Signal
    htf_rsi:    float
    volume_24h: float
    score_adj:  float
    adx:        float


class Scanner:
    def __init__(self, client: BingXClient):
        self.client = client
        self._volume_cache: dict = {}
        self._cache_ts: float    = 0

    def _get_htf_rsi(self, symbol: str) -> float:
        try:
            candles = self.client.get_klines(symbol, HTF_INTERVAL, 60)
            if len(candles) < 20:
                return 50.0
            closes = np.array([c["close"] for c in candles], dtype=float)
            rsi    = _rsi(closes, 14)
            val    = float(rsi[-1])
            return val if not np.isnan(val) else 50.0
        except Exception:
            return 50.0

    def _quick_adx(self, candles: list) -> float:
        """Fast ADX from candle list."""
        try:
            if len(candles) < 30:
                return 0.0
            h = np.array([c["high"]  for c in candles], dtype=float)
            l = np.array([c["low"]   for c in candles], dtype=float)
            c = np.array([c["close"] for c in candles], dtype=float)
            adx_arr = _adx(h, l, c, 14)
            val = float(adx_arr[-1])
            return val if not np.isnan(val) else 0.0
        except Exception:
            return 0.0

    def _scan_symbol(self, symbol: str) -> Optional[ScanResult]:
        try:
            # Volume filter (cheap call)
            vol = self.client.get_24h_volume(symbol)
            if vol < MIN_24H_VOLUME_USDT:
                return None

            # Candle fetch for LTF
            candles = self.client.get_klines(symbol, LTF_INTERVAL, 280)
            if len(candles) < MIN_KLINES:
                return None

            # Quick ADX pre-filter (avoid full compute on flat markets)
            adx_val = self._quick_adx(candles[-50:])
            if adx_val < 18:
                return None

            # Higher timeframe RSI
            htf_rsi = self._get_htf_rsi(symbol)

            # Full signal computation
            signal = compute_signal(candles, htf_rsi)
            if signal.direction == "NONE":
                return None

            # Reject weak R:R
            if signal.rr_ratio < 1.5:
                return None

            # HTF alignment bonus
            htf_bonus = 0
            if signal.direction == "LONG"  and htf_rsi > 55: htf_bonus = 15
            if signal.direction == "SHORT" and htf_rsi < 45: htf_bonus = 15
            # Extreme HTF alignment (strong trend)
            if signal.direction == "LONG"  and htf_rsi > 65: htf_bonus = 25
            if signal.direction == "SHORT" and htf_rsi < 35: htf_bonus = 25

            # Volume bonus: higher volume = stronger signal
            vol_bonus = min(10, (vol / MIN_24H_VOLUME_USDT - 1) * 2)

            # ADX bonus: stronger trend
            adx_bonus = min(10, (adx_val - 22) / 3)

            score_adj = signal.score + htf_bonus + vol_bonus + adx_bonus

            return ScanResult(symbol, signal, htf_rsi, vol, score_adj, adx_val)

        except Exception as e:
            log.debug(f"Scan error {symbol}: {e}")
            return None

    def _get_symbols(self) -> list:
        """Get symbols with priority ordering."""
        try:
            all_syms = self.client.get_all_symbols()
        except Exception as e:
            log.error(f"Failed to get symbols: {e}")
            return PRIORITY_SYMBOLS

        priority = [s for s in PRIORITY_SYMBOLS if s in all_syms]
        other    = [s for s in all_syms if s not in PRIORITY_SYMBOLS]
        combined = priority + other
        return combined[:MAX_SYMBOLS_TO_SCAN]

    def scan(self) -> list:
        """Full market scan. Returns ranked ScanResult list."""
        log.info("🔍 Starting market scan...")
        t0 = time.time()

        symbols = self._get_symbols()
        log.info(f"Scanning {len(symbols)} symbols with {SCAN_THREADS} threads...")

        results = []
        with ThreadPoolExecutor(max_workers=SCAN_THREADS) as ex:
            futs = {ex.submit(self._scan_symbol, sym): sym for sym in symbols}
            for fut in as_completed(futs):
                try:
                    res = fut.result(timeout=30)
                    if res:
                        results.append(res)
                except Exception as e:
                    log.debug(f"Future error: {e}")
                time.sleep(0.01)

        # Rank by adjusted score
        results.sort(key=lambda r: r.score_adj, reverse=True)
        top = results[:TOP_N_RESULTS]

        elapsed = time.time() - t0
        log.info(
            f"✅ Scan done in {elapsed:.1f}s | "
            f"{len(results)} signals from {len(symbols)} symbols | "
            f"Top {len(top)} selected"
        )

        for r in top:
            log.info(
                f"  {r.symbol:<16} {r.signal.direction:<5} "
                f"score={r.score_adj:.0f} vol=${r.volume_24h/1e6:.1f}M "
                f"ADX={r.adx:.0f} RR={r.signal.rr_ratio:.1f}x | {r.signal.reason}"
            )

        return top
