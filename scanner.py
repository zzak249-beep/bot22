"""
Scanner de 30 símbolos — selecciona los mejores pares para operar
Criterios: volumen 24h, volatilidad ATR, fuerza de señal EMA
"""

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from bingx_client import BingXClient
from strategy import EMAStrategy

logger = logging.getLogger(__name__)

# Pares a escanear (los más líquidos de BingX)
WATCHLIST = [
    "BTC-USDT","ETH-USDT","BNB-USDT","SOL-USDT","XRP-USDT",
    "DOGE-USDT","ADA-USDT","AVAX-USDT","LINK-USDT","DOT-USDT",
    "MATIC-USDT","LTC-USDT","BCH-USDT","UNI-USDT","ATOM-USDT",
    "FIL-USDT","APT-USDT","OP-USDT","ARB-USDT","SUI-USDT",
    "INJ-USDT","TIA-USDT","SEI-USDT","WLD-USDT","PEPE-USDT",
    "SHIB-USDT","FLOKI-USDT","TON-USDT","NOT-USDT","WIF-USDT",
]


@dataclass
class SymbolScore:
    symbol:    str
    score:     float
    signal:    str       # LONG / SHORT / HOLD
    volume24h: float     # USD
    atr_pct:   float     # volatilidad % ATR(14)
    spread_pct: float    # distancia precio-EMA3 %
    price:     float
    ema1:      float
    ema2:      float
    ema3:      float
    reason:    str


class MultiSymbolScanner:
    def __init__(
        self,
        client: BingXClient,
        interval: str = "3m",
        top_n: int = 3,       # cuántos pares elegir para operar
        min_volume: float = 5_000_000,  # volumen mínimo 24h en USD
    ):
        self.client   = client
        self.interval = interval
        self.top_n    = top_n
        self.min_vol  = min_volume
        self.strategy = EMAStrategy()

    # ──────────────────────────────────────────────────────────────────────
    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def _score_symbol(self, symbol: str) -> Optional[SymbolScore]:
        try:
            # Candles
            raw = self.client.get_klines(symbol, self.interval, limit=100)
            if not raw or len(raw) < 30:
                return None

            df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
            df = df.astype({
                "timestamp":"int64","open":"float64","high":"float64",
                "low":"float64","close":"float64","volume":"float64",
            })
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.sort_values("timestamp").reset_index(drop=True)

            # Ticker para volumen 24h
            ticker = self.client.get_ticker(symbol)
            vol24h = float(ticker.get("quoteVolume", 0))

            if vol24h < self.min_vol:
                return None

            # Señal
            sig = self.strategy.get_latest_signal(df)
            if sig.action == "HOLD":
                return None

            # ATR como % del precio
            atr     = self._atr(df)
            atr_pct = (atr / sig.price) * 100

            # Distancia precio ↔ EMA3 (nos dice si el cruce es limpio)
            spread_pct = abs(sig.price - sig.ema3) / sig.price * 100

            # Score = volumen * volatilidad (más acción = mejor oportunidad)
            score = vol24h * atr_pct

            return SymbolScore(
                symbol=symbol, score=score,
                signal=sig.action, volume24h=vol24h,
                atr_pct=atr_pct, spread_pct=spread_pct,
                price=sig.price, ema1=sig.ema1, ema2=sig.ema2, ema3=sig.ema3,
                reason=sig.reason,
            )

        except Exception as e:
            logger.debug(f"Score error {symbol}: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────
    def scan(self) -> List[SymbolScore]:
        """
        Escanea WATCHLIST y devuelve los top_n símbolos con señal activa,
        ordenados por score (volumen × volatilidad).
        """
        logger.info(f"Escaneando {len(WATCHLIST)} pares...")
        results = []

        for symbol in WATCHLIST:
            score = self._score_symbol(symbol)
            if score:
                results.append(score)
            time.sleep(0.08)  # evitar rate-limit

        # Ordenar: primero los de mayor score
        results.sort(key=lambda x: x.score, reverse=True)
        top = results[: self.top_n]

        logger.info(
            f"Scan completo | señales={len(results)} | "
            f"top={[s.symbol for s in top]}"
        )
        return top

    def best_symbol(self) -> Optional[SymbolScore]:
        """Devuelve el mejor par del momento (score más alto con señal activa)"""
        top = self.scan()
        return top[0] if top else None

    def format_scan_report(self, results: List[SymbolScore]) -> str:
        if not results:
            return "🔍 Sin señales activas en los 30 pares."

        lines = ["📊 <b>Scan de 30 pares — Top oportunidades</b>\n"]
        for i, s in enumerate(results, 1):
            emoji = "🟢" if s.signal == "LONG" else "🔴"
            lines.append(
                f"{i}. {emoji} <b>{s.symbol}</b>\n"
                f"   Precio: ${s.price:,.4f} | Vol: ${s.volume24h/1e6:.1f}M\n"
                f"   ATR: {s.atr_pct:.2f}% | Score: {s.score/1e9:.1f}B\n"
                f"   Razón: {s.reason}"
            )
        return "\n".join(lines)
