"""
EMA Slope + EMA Cross Bot — v2
Scanner de 30 pares + BingX Perpetuos + Telegram
Temporalidad: 3 minutos
"""

import logging
import os
import sys
import time
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

from bingx_client    import BingXClient
from strategy        import EMAStrategy, Signal
from risk_manager    import RiskManager, TradeParams
from telegram_client import TelegramClient
from scanner         import MultiSymbolScanner, SymbolScore

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

BINGX_API_KEY    = os.environ["BINGX_API_KEY"]
BINGX_API_SECRET = os.environ["BINGX_API_SECRET"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL           = os.getenv("SYMBOL", "")
INTERVAL         = os.getenv("INTERVAL",    "3m")
LEVERAGE         = int(os.getenv("LEVERAGE", "5"))
DEMO_MODE        = os.getenv("DEMO_MODE",   "false").lower() == "true"

EMA1_LEN         = int(os.getenv("EMA1_LEN",  "2"))
EMA2_LEN         = int(os.getenv("EMA2_LEN",  "4"))
EMA3_LEN         = int(os.getenv("EMA3_LEN",  "20"))

RISK_PCT         = float(os.getenv("RISK_PCT",    "1.0"))
SL_PCT           = float(os.getenv("SL_PCT",      "1.5"))
TP_RATIO         = float(os.getenv("TP_RATIO",    "2.0"))
MAX_DD_PCT       = float(os.getenv("MAX_DD_PCT",  "10.0"))
SCANNER_TOP_N    = int(os.getenv("SCANNER_TOP_N", "1"))
MIN_VOLUME       = float(os.getenv("MIN_VOLUME",  "5000000"))
HEARTBEAT_EVERY  = int(os.getenv("HEARTBEAT_EVERY", "20"))


class EMABot:
    def __init__(self):
        self.bingx    = BingXClient(BINGX_API_KEY, BINGX_API_SECRET, demo=DEMO_MODE)
        self.tg       = TelegramClient(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
        self.strategy = EMAStrategy(EMA1_LEN, EMA2_LEN, EMA3_LEN)
        self.risk_mgr = RiskManager(
            risk_pct=RISK_PCT, sl_pct=SL_PCT, tp_ratio=TP_RATIO,
            max_dd_pct=MAX_DD_PCT, leverage=LEVERAGE,
        )
        self.scanner = MultiSymbolScanner(
            self.bingx, INTERVAL, top_n=SCANNER_TOP_N, min_volume=MIN_VOLUME
        )
        self.active_symbol:   Optional[str]   = SYMBOL if SYMBOL else None
        self.position_side:   Optional[str]   = None
        self.entry_price:     Optional[float] = None
        self.position_qty:    Optional[float] = None
        self.current_sl:      Optional[float] = None
        self.candles_seen:    int = 0
        self._qty_step        = 0.001
        self._price_precision = 4

    def setup(self):
        logger.info("=== EMA Bot v2 Iniciando ===")
        if not self.active_symbol:
            logger.info("Modo SCANNER — buscando el mejor par...")
            best = self.scanner.best_symbol()
            self.active_symbol = best.symbol if best else "BTC-USDT"
            logger.info(f"Par inicial: {self.active_symbol}")
        self._init_symbol(self.active_symbol)
        self.tg.send_startup(self.active_symbol, INTERVAL, LEVERAGE, DEMO_MODE)

    def _init_symbol(self, symbol: str):
        try:
            info = self.bingx.get_symbol_info(symbol)
            self._qty_step = float(info.get("tradeMinQuantity", "0.001"))
        except Exception as e:
            logger.warning(f"Symbol info {symbol}: {e}")
        for side in ("LONG", "SHORT"):
            try:
                self.bingx.set_leverage(symbol, LEVERAGE, side)
            except Exception as e:
                logger.debug(f"Leverage {symbol} {side}: {e}")
        try:
            self.bingx.set_margin_mode(symbol, "ISOLATED")
        except Exception as e:
            logger.debug(f"MarginMode: {e}")

    def _fetch_df(self, symbol: str) -> pd.DataFrame:
        raw = self.bingx.get_klines(symbol, INTERVAL, limit=150)
        if not raw:
            raise ValueError(f"Sin datos {symbol}")
        df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
        df = df.astype({"timestamp":"int64","open":"float64","high":"float64",
                        "low":"float64","close":"float64","volume":"float64"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.sort_values("timestamp").reset_index(drop=True)

    def _balance(self) -> float:
        try:
            bal = self.bingx.get_balance()
            return float(bal.get("availableMargin", bal.get("balance", 0)))
        except Exception as e:
            logger.error(f"Balance: {e}")
            return 0.0

    def _open(self, symbol: str, signal: Signal, balance: float):
        params = self.risk_mgr.compute(
            balance=balance, price=signal.price, side=signal.action,
            qty_step=self._qty_step, price_precision=self._price_precision,
        )
        if not params:
            return
        buy_sell = "BUY" if signal.action == "LONG" else "SELL"
        self.tg.send_signal(
            symbol=symbol, action=signal.action, price=signal.price,
            ema1=signal.ema1, ema2=signal.ema2, ema3=signal.ema3,
            reason=signal.reason, qty=params.quantity, leverage=LEVERAGE,
            sl_price=params.sl_price, tp_price=params.tp_price,
        )
        try:
            self.bingx.cancel_all_orders(symbol)
            order    = self.bingx.place_market_order(symbol, buy_sell, signal.action, params.quantity)
            order_id = order.get("orderId","N/A")
            if params.sl_price:
                sl_side = "SELL" if signal.action == "LONG" else "BUY"
                self.bingx.place_stop_loss(symbol, sl_side, signal.action, params.sl_price, params.quantity)
            if params.tp_price:
                tp_side = "SELL" if signal.action == "LONG" else "BUY"
                self.bingx.place_take_profit(symbol, tp_side, signal.action, params.tp_price, params.quantity)
            self.position_side   = signal.action
            self.entry_price     = signal.price
            self.position_qty    = params.quantity
            self.current_sl      = params.sl_price
            self.active_symbol   = symbol
            self.tg.send_order_filled(symbol, signal.action, signal.price, str(order_id), params.quantity)
            logger.info(f"Posicion abierta {signal.action} {symbol} qty={params.quantity}")
        except Exception as e:
            logger.error(f"Error abriendo: {e}")
            self.tg.send_error("open", str(e))

    def _close(self, symbol: str, price: float, reason: str = ""):
        if not self.position_side or not self.position_qty:
            return
        try:
            self.bingx.cancel_all_orders(symbol)
            self.bingx.close_position(symbol, self.position_side, self.position_qty)
            pnl = 0.0
            if self.entry_price:
                if self.position_side == "LONG":
                    pnl = (price - self.entry_price) * self.position_qty * LEVERAGE
                else:
                    pnl = (self.entry_price - price) * self.position_qty * LEVERAGE
            self.tg.send_close(symbol, self.position_side, self.entry_price or 0, price, pnl, self.position_qty)
            logger.info(f"Posicion cerrada {self.position_side} {symbol} pnl={pnl:.2f}")
            self.position_side = self.entry_price = self.position_qty = self.current_sl = None
        except Exception as e:
            logger.error(f"Error cerrando: {e}")
            self.tg.send_error("close", str(e))

    def tick(self):
        self.candles_seen += 1

        if not self.position_side:
            # Sin posicion: escanear 30 pares
            logger.info("Escaneando 30 pares...")
            results = self.scanner.scan()
            if results:
                self.tg._send(self.scanner.format_scan_report(results))
                best = results[0]
                self._init_symbol(best.symbol)
                sig = Signal(
                    action=best.signal, price=best.price,
                    ema1=best.ema1, ema2=best.ema2, ema3=best.ema3,
                    reason=best.reason, timestamp=best.symbol,
                )
                self._open(best.symbol, sig, self._balance())
            else:
                logger.info("Sin senales activas — esperando...")
            return

        # Con posicion: monitorear
        symbol = self.active_symbol
        try:
            df     = self._fetch_df(symbol)
            signal = self.strategy.get_latest_signal(df)
            price  = float(df["close"].iloc[-1])
            logger.info(f"[{symbol}] senal={signal.action} precio={price:.4f}")

            if self.current_sl:
                new_sl = self.risk_mgr.trailing_stop(
                    self.position_side, self.entry_price or 0, price, self.current_sl
                )
                if new_sl != self.current_sl:
                    self.current_sl = new_sl

            if signal.action == "LONG"  and self.position_side == "SHORT":
                self._close(symbol, price, "Flip a LONG")
            elif signal.action == "SHORT" and self.position_side == "LONG":
                self._close(symbol, price, "Flip a SHORT")
        except Exception as e:
            logger.error(f"Tick error: {e}", exc_info=True)
            self.tg.send_error(f"tick:{symbol}", str(e))

        if self.candles_seen % HEARTBEAT_EVERY == 0:
            try:
                df  = self._fetch_df(symbol)
                df2 = self.strategy.compute(df)
                self.tg.send_heartbeat(
                    symbol, float(df["close"].iloc[-1]),
                    self.position_side or "FLAT",
                    float(df2["ema3"].iloc[-1]),
                    self._balance(), self.candles_seen,
                )
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

    def run(self):
        self.setup()
        secs = self._to_seconds(INTERVAL)
        logger.info(f"Bot activo | {INTERVAL} ({secs}s)")
        while True:
            try:
                now  = time.time()
                wait = max(0, (int(now / secs) + 1) * secs + 3 - now)
                logger.info(f"Esperando {wait:.1f}s...")
                time.sleep(wait)
                self.tick()
            except KeyboardInterrupt:
                logger.info("Detenido por usuario")
                self.tg._send("Bot detenido")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
                time.sleep(15)

    @staticmethod
    def _to_seconds(interval: str) -> int:
        unit  = interval[-1]
        value = int(interval[:-1])
        return value * {"m": 60, "h": 3600, "d": 86400}.get(unit, 60)


if __name__ == "__main__":
    EMABot().run()
