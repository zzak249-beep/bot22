"""
Trader Principal — BingX Futuros Perpetuos
────────────────────────────────────────────────────────
• Entrada fija: 8 USDT × 7x apalancamiento = 56 USDT por trade
• Si GANA  → trailing stop dinámico (deja correr la ganancia)
• Si PIERDE → cierre inmediato al tocar el SL inicial (corta rápido)
────────────────────────────────────────────────────────
Fases del trailing stop:
  Fase 0 (SL Fijo)    : SL en precio inicial de la estrategia
  Fase 1 (Breakeven)  : al ganar ≥ BE_TRIGGER_PCT  → SL sube a precio de entrada
  Fase 2 (Trailing)   : al ganar ≥ TRAIL_START_PCT → SL sigue al precio a TRAIL_DISTANCE_PCT detrás
  Cierre por TP       : precio alcanza el objetivo → cierre inmediato
"""

import asyncio
import logging
import os
import pandas as pd
import numpy as np
from datetime import datetime
from dataclasses import dataclass

from exchange.bingx_client import BingXClient
from strategies.vwap_strategy import VWAPMeanReversionStrategy, Signal, TradeSignal
from strategies.bb_rsi_strategy import BBRSIMeanReversionStrategy, EMARibbonScalpingStrategy
from utils.risk_manager import RiskManager
from utils.logger import setup_logger

logger = setup_logger(__name__)

# ─── Parámetros de trailing stop ────────────────────
BE_TRIGGER_PCT     = 0.40   # Ganancia % para mover SL a breakeven
TRAIL_START_PCT    = 0.80   # Ganancia % para empezar trailing
TRAIL_DISTANCE_PCT = 0.40   # Distancia % del trailing al máximo
LOSS_CUT_PCT       = 0.60   # Pérdida % máxima para cierre forzado
MONITOR_INTERVAL   = 15     # Segundos entre chequeos de posiciones


@dataclass
class ActiveTrade:
    symbol:      str
    strategy:    str
    direction:   str    # "LONG" | "SHORT"
    entry:       float
    qty:         float
    initial_sl:  float
    current_sl:  float
    tp:          float
    usdt_margin: float
    leverage:    int
    best_price:  float
    phase:       int    # 0=fijo, 1=breakeven, 2=trailing
    opened_at:   str
    order_id:    str   = ""
    pnl_usdt:    float = 0.0
    closed:      bool  = False
    close_reason: str  = ""

    def update_pnl(self, price: float) -> float:
        if self.direction == "LONG":
            pct = (price - self.entry) / self.entry
        else:
            pct = (self.entry - price) / self.entry
        self.pnl_usdt = round(pct * self.usdt_margin * self.leverage, 4)
        return self.pnl_usdt

    def pnl_pct(self) -> float:
        return (self.pnl_usdt / self.usdt_margin) * 100 if self.usdt_margin else 0

    def update_trailing(self, price: float) -> bool:
        """
        Actualiza SL dinámico.
        Retorna True si la posición debe cerrarse.
        """
        if self.direction == "LONG":
            gain_pct = ((price - self.entry) / self.entry) * 100
            loss_pct = ((self.entry - price) / self.entry) * 100

            if price > self.best_price:
                self.best_price = price

            # TP
            if price >= self.tp:
                self.close_reason = f"✅ TP alcanzado | precio {price:.6f}"
                return True

            # SL fijo en pérdida
            if self.phase == 0 and loss_pct >= LOSS_CUT_PCT:
                self.close_reason = f"🛑 SL tocado | pérdida -{loss_pct:.2f}% | precio {price:.6f}"
                return True

            # Fase 1: breakeven
            if self.phase == 0 and gain_pct >= BE_TRIGGER_PCT:
                self.current_sl = self.entry
                self.phase = 1
                logger.info(f"[{self.symbol}] 🔄 Fase 1 → SL a breakeven {self.entry:.6f}")

            # Fase 2: trailing
            if gain_pct >= TRAIL_START_PCT:
                new_sl = round(self.best_price * (1 - TRAIL_DISTANCE_PCT / 100), 6)
                if new_sl > self.current_sl:
                    self.current_sl = new_sl
                    self.phase = 2

            # Trailing tocado
            if self.phase > 0 and price <= self.current_sl:
                self.close_reason = (
                    f"📈 Trailing SL tocado | precio {price:.6f} | "
                    f"SL {self.current_sl:.6f} | pico {self.best_price:.6f}"
                )
                return True

        else:  # SHORT
            gain_pct = ((self.entry - price) / self.entry) * 100
            loss_pct = ((price - self.entry) / self.entry) * 100

            if price < self.best_price:
                self.best_price = price

            if price <= self.tp:
                self.close_reason = f"✅ TP alcanzado | precio {price:.6f}"
                return True

            if self.phase == 0 and loss_pct >= LOSS_CUT_PCT:
                self.close_reason = f"🛑 SL tocado | pérdida -{loss_pct:.2f}% | precio {price:.6f}"
                return True

            if self.phase == 0 and gain_pct >= BE_TRIGGER_PCT:
                self.current_sl = self.entry
                self.phase = 1
                logger.info(f"[{self.symbol}] 🔄 Fase 1 → SL a breakeven {self.entry:.6f}")

            if gain_pct >= TRAIL_START_PCT:
                new_sl = round(self.best_price * (1 + TRAIL_DISTANCE_PCT / 100), 6)
                if new_sl < self.current_sl:
                    self.current_sl = new_sl
                    self.phase = 2

            if self.phase > 0 and price >= self.current_sl:
                self.close_reason = (
                    f"📉 Trailing SL tocado | precio {price:.6f} | "
                    f"SL {self.current_sl:.6f} | pico {self.best_price:.6f}"
                )
                return True

        return False


class Trader:
    def __init__(self):
        self.api_key    = os.environ["BINGX_API_KEY"]
        self.api_secret = os.environ["BINGX_API_SECRET"]
        self.testnet    = os.environ.get("TESTNET", "false").lower() == "true"

        # ── Configuración fija ──────────────────────────
        self.usdt_per_trade = float(os.environ.get("USDT_PER_TRADE", "8"))   # 8 USDT
        self.leverage       = int(os.environ.get("LEVERAGE", "7"))            # 7x

        # Pares y timeframes
        self.pairs       = os.environ.get("TRADING_PAIRS", "BTC-USDT,ETH-USDT,SOL-USDT").split(",")
        self.mean_rev_tf = os.environ.get("MEAN_REV_TIMEFRAME", "15m")
        self.scalping_tf = os.environ.get("SCALPING_TIMEFRAME", "5m")
        self.analysis_interval = int(os.environ.get("ANALYSIS_INTERVAL", "60"))

        # Estrategias
        self.vwap_strategy   = VWAPMeanReversionStrategy({"min_band": int(os.environ.get("VWAP_MIN_BAND", "2"))})
        self.bb_rsi_strategy = BBRSIMeanReversionStrategy({"rsi_oversold": int(os.environ.get("RSI_OVERSOLD", "30")), "rsi_overbought": int(os.environ.get("RSI_OVERBOUGHT", "70"))})
        self.ema_scalp_strategy = EMARibbonScalpingStrategy({})

        self.risk_manager = RiskManager(
            max_risk_pct=1.0,
            max_open_positions=int(os.environ.get("MAX_POSITIONS", "3")),
            max_daily_loss_pct=float(os.environ.get("MAX_DAILY_LOSS", "5.0"))
        )

        # Callbacks
        self.on_signal_callbacks = []
        self.on_trade_callbacks  = []
        self.on_close_callbacks  = []

        # Estado
        self.active_trades: dict[str, ActiveTrade] = {}
        self.daily_pnl      = 0.0
        self.total_trades   = 0
        self.winning_trades = 0
        self.client: BingXClient | None = None

    def register_signal_callback(self, cb): self.on_signal_callbacks.append(cb)
    def register_trade_callback(self, cb):  self.on_trade_callbacks.append(cb)
    def register_close_callback(self, cb):  self.on_close_callbacks.append(cb)

    async def _notify(self, callbacks, *args):
        for cb in callbacks:
            try:
                await cb(*args)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _parse_klines(self, raw: list) -> pd.DataFrame:
        cols = ["timestamp", "open", "high", "low", "close", "volume"]
        if len(raw[0]) > 6:
            cols += ["close_time", "quote_vol", "trades", "taker_base", "taker_quote", "ignore"]
        df = pd.DataFrame(raw, columns=cols[:len(raw[0])])
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["timestamp"] = pd.to_numeric(df["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)

    async def get_balance(self) -> float:
        try:
            data = await self.client.get_balance()
            if isinstance(data, list):
                for a in data:
                    if a.get("asset") == "USDT":
                        return float(a.get("balance", 0))
            elif isinstance(data, dict):
                return float(data.get("balance", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Error balance: {e}")
            return 0.0

    async def get_current_price(self, symbol: str) -> float:
        try:
            ticker = await self.client.get_ticker(symbol)
            if isinstance(ticker, dict):
                for key in ("lastPrice", "last", "price", "c"):
                    if key in ticker:
                        return float(ticker[key])
            elif isinstance(ticker, list) and ticker:
                return float(ticker[0].get("lastPrice", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Error precio {symbol}: {e}")
            return 0.0

    def calculate_qty(self, entry_price: float) -> float:
        """Contratos = (8 USDT × 7x) / precio_entrada"""
        qty = (self.usdt_per_trade * self.leverage) / entry_price
        return round(qty, 4)

    # ──────────────────────────────────────────────────
    # APERTURA
    # ──────────────────────────────────────────────────
    async def execute_trade(self, symbol: str, signal: TradeSignal, strategy_name: str):
        if not self.risk_manager.can_trade(symbol, self.active_trades, self.daily_pnl):
            logger.info(f"[{symbol}] Bloqueado por risk manager")
            return
        try:
            balance = await self.get_balance()
            if balance < self.usdt_per_trade:
                logger.warning(f"Balance insuficiente ${balance:.2f}")
                return

            qty = self.calculate_qty(signal.entry_price)
            if qty <= 0:
                return

            pos_side   = signal.signal.value
            order_side = "BUY" if signal.signal == Signal.LONG else "SELL"

            try:
                await self.client.set_leverage(symbol, self.leverage, pos_side)
            except Exception as e:
                logger.warning(f"set_leverage: {e}")

            order = await self.client.place_order(
                symbol=symbol,
                side=order_side,
                position_side=pos_side,
                order_type="MARKET",
                quantity=qty,
                client_order_id=f"bot_{symbol}_{int(asyncio.get_event_loop().time())}"
            )
            order_id = str(order.get("orderId", ""))

            trade = ActiveTrade(
                symbol       = symbol,
                strategy     = strategy_name,
                direction    = pos_side,
                entry        = signal.entry_price,
                qty          = qty,
                initial_sl   = signal.sl_price,
                current_sl   = signal.sl_price,
                tp           = signal.tp_price,
                usdt_margin  = self.usdt_per_trade,
                leverage     = self.leverage,
                best_price   = signal.entry_price,
                phase        = 0,
                opened_at    = datetime.utcnow().isoformat(),
                order_id     = order_id,
            )
            self.active_trades[symbol] = trade
            self.risk_manager.register_trade(symbol)
            self.total_trades += 1

            pos_val = round(self.usdt_per_trade * self.leverage, 2)
            logger.info(
                f"✅ ABIERTO {pos_side} {symbol} | entrada {signal.entry_price} | "
                f"qty {qty} | {self.usdt_per_trade}×{self.leverage}x = {pos_val} USDT | "
                f"SL {signal.sl_price} | TP {signal.tp_price}"
            )

            await self._notify(self.on_trade_callbacks, {
                "type":      "OPEN",
                "symbol":    symbol,
                "strategy":  strategy_name,
                "signal":    pos_side,
                "entry":     signal.entry_price,
                "tp":        signal.tp_price,
                "sl":        signal.sl_price,
                "qty":       qty,
                "leverage":  self.leverage,
                "margin":    self.usdt_per_trade,
                "pos_value": pos_val,
                "order_id":  order_id,
                "confidence": signal.confidence,
                "reason":    signal.reason,
                "balance":   balance,
                "timestamp": trade.opened_at,
            })

        except Exception as e:
            logger.error(f"Error abriendo {symbol}: {e}", exc_info=True)

    # ──────────────────────────────────────────────────
    # CIERRE
    # ──────────────────────────────────────────────────
    async def close_trade(self, trade: ActiveTrade, current_price: float):
        try:
            trade.update_pnl(current_price)
            close_side = "SELL" if trade.direction == "LONG" else "BUY"

            await self.client.place_order(
                symbol=trade.symbol,
                side=close_side,
                position_side=trade.direction,
                order_type="MARKET",
                quantity=trade.qty,
            )

            trade.closed = True
            won = trade.pnl_usdt >= 0
            if won:
                self.winning_trades += 1
            self.daily_pnl = round(self.daily_pnl + trade.pnl_usdt, 4)
            self.risk_manager.close_trade(trade.symbol, trade.pnl_pct())
            self.active_trades.pop(trade.symbol, None)

            phase_name = {0: "SL Fijo", 1: "Breakeven", 2: "Trailing"}.get(trade.phase, "?")
            logger.info(
                f"{'✅' if won else '❌'} CERRADO {trade.direction} {trade.symbol} | "
                f"PnL: {'+' if won else ''}{trade.pnl_usdt} USDT ({trade.pnl_pct():.2f}%) | "
                f"Fase: {phase_name} | {trade.close_reason}"
            )

            await self._notify(self.on_close_callbacks, {
                "symbol":     trade.symbol,
                "direction":  trade.direction,
                "strategy":   trade.strategy,
                "entry":      trade.entry,
                "exit":       current_price,
                "pnl_usdt":   trade.pnl_usdt,
                "pnl_pct":    round(trade.pnl_pct(), 2),
                "won":        won,
                "phase":      phase_name,
                "best_price": trade.best_price,
                "trailing_sl":trade.current_sl,
                "reason":     trade.close_reason,
                "daily_pnl":  self.daily_pnl,
                "winrate":    round((self.winning_trades / max(self.total_trades, 1)) * 100, 1),
                "timestamp":  datetime.utcnow().isoformat(),
            })

        except Exception as e:
            logger.error(f"Error cerrando {trade.symbol}: {e}", exc_info=True)

    # ──────────────────────────────────────────────────
    # MONITOR (corre cada MONITOR_INTERVAL segundos)
    # ──────────────────────────────────────────────────
    async def monitor_positions(self):
        while True:
            await asyncio.sleep(MONITOR_INTERVAL)
            if not self.active_trades:
                continue

            for trade in list(self.active_trades.values()):
                if trade.closed:
                    continue
                try:
                    price = await self.get_current_price(trade.symbol)
                    if price <= 0:
                        continue

                    trade.update_pnl(price)
                    should_close = trade.update_trailing(price)

                    phase_name = {0: "SL Fijo 🔒", 1: "Breakeven 🔄", 2: f"Trailing 🎯 SL={trade.current_sl:.4f}"}.get(trade.phase, "?")
                    pnl_icon = "📈" if trade.pnl_usdt >= 0 else "📉"
                    logger.info(
                        f"[MONITOR] {trade.direction} {trade.symbol} | "
                        f"Precio: {price:.4f} | {pnl_icon} PnL: {trade.pnl_usdt:+.4f} USDT "
                        f"({trade.pnl_pct():+.2f}%) | {phase_name}"
                    )

                    if should_close:
                        await self.close_trade(trade, price)

                except Exception as e:
                    logger.error(f"Monitor error {trade.symbol}: {e}")

    # ──────────────────────────────────────────────────
    # ANÁLISIS DE SEÑALES
    # ──────────────────────────────────────────────────
    async def analyze_pair(self, symbol: str):
        if symbol in self.active_trades:
            return  # Ya hay posición abierta en este par

        try:
            klines_15m = await self.client.get_klines(symbol, self.mean_rev_tf, limit=300)
            klines_5m  = await self.client.get_klines(symbol, self.scalping_tf,  limit=300)
            if not klines_15m or not klines_5m:
                return

            df_15m = self._parse_klines(klines_15m)
            df_5m  = self._parse_klines(klines_5m)

            # VWAP
            sig = self.vwap_strategy.analyze(df_15m)
            if sig.signal != Signal.NONE:
                await self._notify(self.on_signal_callbacks, symbol, "VWAP+SD", sig)
                if sig.confidence in ["HIGH", "MEDIUM"]:
                    await self.execute_trade(symbol, sig, "VWAP Mean Reversion 15m")
                    return

            # BB + RSI
            sig = self.bb_rsi_strategy.analyze(df_15m)
            if sig.signal != Signal.NONE:
                await self._notify(self.on_signal_callbacks, symbol, "BB+RSI", sig)
                if sig.confidence == "HIGH":
                    await self.execute_trade(symbol, sig, "BB+RSI Mean Reversion 15m")
                    return

            # EMA Scalping
            sig = self.ema_scalp_strategy.analyze(df_5m)
            if sig.signal != Signal.NONE:
                await self._notify(self.on_signal_callbacks, symbol, "EMA Ribbon 5m", sig)
                if sig.confidence == "HIGH":
                    await self.execute_trade(symbol, sig, "EMA Ribbon Scalping 5m")

        except Exception as e:
            logger.error(f"Error analizando {symbol}: {e}", exc_info=True)

    # ──────────────────────────────────────────────────
    # LOOP PRINCIPAL
    # ──────────────────────────────────────────────────
    async def run_loop(self):
        async with BingXClient(self.api_key, self.api_secret, self.testnet) as client:
            self.client = client
            pos_val = self.usdt_per_trade * self.leverage
            modo = "⚪ DEMO" if self.testnet else "🔴 DINERO REAL"

            logger.info("=" * 60)
            logger.info(f"  BingX Trading Bot — {modo}")
            logger.info(f"  Entrada: {self.usdt_per_trade} USDT × {self.leverage}x = {pos_val} USDT/trade")
            logger.info(f"  Pares:   {', '.join(self.pairs)}")
            logger.info(f"  TF:      {self.mean_rev_tf} (mean rev) / {self.scalping_tf} (scalp)")
            logger.info(f"  Trailing: BE@+{BE_TRIGGER_PCT}% → Trail@+{TRAIL_START_PCT}% (dist {TRAIL_DISTANCE_PCT}%)")
            logger.info(f"  Corte pérdida: -{LOSS_CUT_PCT}%")
            logger.info("=" * 60)

            asyncio.create_task(self.monitor_positions())

            while True:
                try:
                    for symbol in self.pairs:
                        await self.analyze_pair(symbol)
                        await asyncio.sleep(2)

                    wr = round((self.winning_trades / max(self.total_trades, 1)) * 100, 1)
                    logger.info(
                        f"⏱ Ciclo | Posiciones: {len(self.active_trades)}/{len(self.pairs)} | "
                        f"P&L hoy: ${self.daily_pnl:+.4f} | Trades: {self.total_trades} | WR: {wr}%"
                    )
                    await asyncio.sleep(self.analysis_interval)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error loop: {e}", exc_info=True)
                    await asyncio.sleep(30)
