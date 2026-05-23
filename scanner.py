"""
Scanner — busca las mejores señales entre múltiples pares
"""
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from strategy import EMAStrategy

logger = logging.getLogger(__name__)


@dataclass
class SymbolScore:
    symbol:    str
    signal:    str      # LONG | SHORT
    price:     float
    ema1:      float
    ema2:      float
    ema3:      float
    rsi:       float
    adx:       float
    atr_pct:   float
    atr:       float
    reason:    str
    score:     float    # puntuación del scanner (volumen, ADX…)
    sig_score: float    # puntuación de la señal strategy


# Universo por defecto si USE_WHITELIST está desactivado
DEFAULT_SYMBOLS = [
    "BTC-USDT","ETH-USDT","BNB-USDT","SOL-USDT","XRP-USDT",
    "DOGE-USDT","ADA-USDT","AVAX-USDT","DOT-USDT","LINK-USDT",
    "MATIC-USDT","UNI-USDT","ATOM-USDT","LTC-USDT","ARB-USDT",
    "OP-USDT","INJ-USDT","SUI-USDT","AAVE-USDT","PEPE-USDT",
]


class MultiSymbolScanner:
    def __init__(self, bingx_client, interval: str, htf_interval: str,
                 top_n: int = 3, min_volume: float = 1_000_000,
                 score_min: float = 30.0):
        self.bingx        = bingx_client
        self.interval     = interval
        self.htf_interval = htf_interval
        self.top_n        = top_n
        self.min_volume   = min_volume
        self.score_min    = score_min
        self.strategy     = EMAStrategy()
        self._cache: List[SymbolScore] = []
        self._cache_ts: float = 0
        self._cache_ttl: float = 60.0   # segundos

    def _get_symbols(self) -> List[str]:
        try:
            return self.bingx.get_all_symbols()
        except Exception as e:
            logger.warning(f"get_all_symbols: {e} — usando DEFAULT_SYMBOLS")
            return DEFAULT_SYMBOLS

    def _get_df(self, symbol: str, interval: str, limit: int = 150) -> Optional[pd.DataFrame]:
        try:
            raw = self.bingx.get_klines(symbol, interval, limit=limit)
            if not raw or len(raw) < 30:
                return None
            df = pd.DataFrame(raw)
            df = df.rename(columns={"timestamp": "timestamp"})
            df = df.astype({c: "float64" for c in ["open","high","low","close","volume"]})
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
            return df.sort_values("timestamp").reset_index(drop=True)
        except Exception as e:
            logger.debug(f"klines {symbol}: {e}")
            return None

    def _score_symbol(self, symbol: str) -> Optional[SymbolScore]:
        df = self._get_df(symbol, self.interval)
        if df is None:
            return None

        # Filtro de volumen
        vol = float((df["close"] * df["volume"]).tail(96).sum())
        if vol < self.min_volume:
            return None

        htf_df = self._get_df(symbol, self.htf_interval, limit=60)
        sig    = self.strategy.get_latest_signal(df, htf_df)

        if sig.action == "HOLD":
            return None
        if sig.score < self.score_min:
            return None

        # Puntuación scanner (adicional al score de la señal)
        scanner_score = sig.score
        if sig.adx > 25:
            scanner_score += 5
        if vol > self.min_volume * 3:
            scanner_score += 5

        return SymbolScore(
            symbol    = symbol,
            signal    = sig.action,
            price     = sig.price,
            ema1      = sig.ema1,
            ema2      = sig.ema2,
            ema3      = sig.ema3,
            rsi       = sig.rsi,
            adx       = sig.adx,
            atr_pct   = sig.atr_pct,
            atr       = sig.atr,
            reason    = sig.reason,
            score     = round(scanner_score, 1),
            sig_score = sig.score,
        )

    def scan(self, force: bool = False) -> List[SymbolScore]:
        now = time.time()
        if not force and self._cache and (now - self._cache_ts) < self._cache_ttl:
            return self._cache

        symbols = self._get_symbols()
        results: List[SymbolScore] = []

        for sym in symbols:
            try:
                r = self._score_symbol(sym)
                if r:
                    results.append(r)
            except Exception as e:
                logger.debug(f"scan {sym}: {e}")

        results.sort(key=lambda x: x.score, reverse=True)
        self._cache    = results
        self._cache_ts = now
        logger.info(f"Scan completo: {len(results)} señales de {len(symbols)} pares")
        return results

    def best_symbol(self) -> Optional[SymbolScore]:
        results = self.scan()
        return results[0] if results else None

    def format_report(self, results: List[SymbolScore]) -> str:
        if not results:
            return "🔍 Sin señales activas"
        lines = [f"🔍 <b>Top {len(results)} señales</b>\n"]
        for i, r in enumerate(results, 1):
            emoji = "🟢" if r.signal == "LONG" else "🔴"
            lines.append(
                f"<b>#{i}</b> {emoji} <code>{r.symbol}</code> — Score: <code>{r.score}</code>\n"
                f"  Precio: <code>{r.price:.6f}</code> | RSI: <code>{r.rsi:.0f}</code> | ADX: <code>{r.adx:.0f}</code>\n"
                f"  ATR: <code>{r.atr_pct:.2f}%</code> | {r.reason}"
            )
        return "\n".join(lines)
