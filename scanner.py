"""
Scanner v5 — usa dicts normalizados del cliente corregido
"""
import logging, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
import numpy as np
from bingx_client import BingXClient
from strategy import EMAStrategy

logger = logging.getLogger(__name__)

WATCHLIST = [
    "BTC-USDT","ETH-USDT","BNB-USDT","SOL-USDT","XRP-USDT",
    "DOGE-USDT","ADA-USDT","AVAX-USDT","LINK-USDT","DOT-USDT",
    "MATIC-USDT","LTC-USDT","BCH-USDT","UNI-USDT","ATOM-USDT",
    "APT-USDT","OP-USDT","ARB-USDT","SUI-USDT","INJ-USDT",
    "TIA-USDT","WLD-USDT","PEPE-USDT","SHIB-USDT","TON-USDT",
    "WIF-USDT","JUP-USDT","RENDER-USDT","FET-USDT","NEAR-USDT",
]


@dataclass
class SymbolScore:
    symbol: str; score: float; signal: str; price: float
    ema1: float; ema2: float; ema3: float
    rsi: float; adx: float; atr_pct: float
    volume24h: float; vol_spike: float
    reason: str; sig_score: float


def _to_df(klines: list) -> Optional[pd.DataFrame]:
    """Convierte lista de dicts normalizados a DataFrame"""
    if not klines or len(klines) < 30:
        return None
    df = pd.DataFrame(klines)
    # klines ya vienen como dicts con keys: timestamp, open, high, low, close, volume
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("timestamp").reset_index(drop=True)


class MultiSymbolScanner:
    def __init__(self, client: BingXClient, interval="3m", htf_interval="15m",
                 top_n=1, min_volume=1_000_000, score_min=30, workers=10):
        self.client       = client
        self.interval     = interval
        self.htf_interval = htf_interval
        self.top_n        = top_n
        self.min_vol      = min_volume
        self.score_min    = score_min
        self.workers      = workers
        self.strategy     = EMAStrategy(score_min=score_min)
        self._cache: List[SymbolScore] = []
        self._cache_ts: float = 0

    def _hour_bonus(self) -> float:
        h = datetime.now(timezone.utc).hour
        if 13 <= h <= 17: return 1.3
        if 8  <= h <= 12: return 1.1
        return 1.0

    def _score_symbol(self, symbol: str) -> Optional[SymbolScore]:
        try:
            klines = self.client.get_klines(symbol, self.interval, 120)
            df     = _to_df(klines)
            if df is None:
                return None

            # Volumen 24h
            try:
                vol24h = float(self.client.get_ticker(symbol).get("quoteVolume", 0))
            except:
                vol24h = float(df["volume"].tail(480).sum() * df["close"].iloc[-1])

            if vol24h < self.min_vol:
                return None

            htf_klines = self.client.get_klines(symbol, self.htf_interval, 60)
            htf_df     = _to_df(htf_klines)

            sig = self.strategy.get_latest_signal(df, htf_df)
            if sig.action == "HOLD":
                return None

            vol_ma    = float(df["volume"].rolling(20).mean().iloc[-2])
            vol_spike = float(df["volume"].iloc[-2]) / max(vol_ma, 1)

            composite = (
                sig.score                    * 0.5 +
                min(30, sig.atr_pct * 10)   * 0.3 +
                min(20, (vol_spike-1) * 10) * 0.2
            ) * self._hour_bonus()

            return SymbolScore(
                symbol=symbol, score=round(composite, 1),
                signal=sig.action, price=sig.price,
                ema1=sig.ema1, ema2=sig.ema2, ema3=sig.ema3,
                rsi=sig.rsi, adx=sig.adx, atr_pct=sig.atr_pct,
                volume24h=vol24h, vol_spike=round(vol_spike, 2),
                reason=sig.reason, sig_score=sig.score,
            )
        except Exception as e:
            logger.debug(f"{symbol}: {e}")
            return None

    def scan(self, force=False) -> List[SymbolScore]:
        now = time.time()
        if not force and self._cache and (now - self._cache_ts) < 55:
            return self._cache

        t0 = time.time()
        logger.info(f"Escaneando {len(WATCHLIST)} pares ({self.workers} hilos)...")
        results = []

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self._score_symbol, s): s for s in WATCHLIST}
            for fut in as_completed(futs):
                r = fut.result()
                if r:
                    results.append(r)

        results.sort(key=lambda x: x.score, reverse=True)
        elapsed = time.time() - t0
        logger.info(
            f"Scan: {elapsed:.1f}s | revisados={len(WATCHLIST)} | "
            f"con_señal={len(results)} | top={[s.symbol for s in results[:self.top_n]]}"
        )
        self._cache    = results
        self._cache_ts = now
        return results

    def best_symbol(self) -> Optional[SymbolScore]:
        top = self.scan()
        return top[0] if top else None

    def format_report(self, results: List[SymbolScore]) -> str:
        if not results:
            return "🔍 Scan 30 pares — sin señales activas ahora."
        lines = [f"📊 <b>{len(results)} señal(es) encontrada(s)</b>\n"]
        for i, s in enumerate(results, 1):
            e = "🟢" if s.signal == "LONG" else "🔴"
            lines.append(
                f"{i}. {e} <b>{s.symbol}</b>  Score:<b>{s.score}</b>\n"
                f"   ${s.price:,.4f} | RSI {s.rsi:.0f} | ADX {s.adx:.0f} | Vol×{s.vol_spike}\n"
                f"   {s.reason}"
            )
        return "\n".join(lines)
