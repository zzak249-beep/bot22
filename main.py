"""
EMA Bot v3 — Main
Mejoras:
- Scanner concurrente (30 pares en ~1s)
- Comandos Telegram: /status /balance /trades /pausa /reanudar /stop
- TP parcial: 50% en 1R, 50% en 2.5R + breakeven automático
- Trailing stop ATR-based
- PerformanceTracker en tiempo real
- Gestión de posición robusta con reintentos
"""

import logging
import os
import sys
import time
import threading
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

from bingx_client    import BingXClient
from strategy        import EMAStrategy, Signal
from risk_manager    import RiskManager, TradeParams
from telegram_client import TelegramClient
from scanner         import MultiSymbolScanner

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

# ── Config ────────────────────────────────────────────────────────────────
BINGX_API_KEY    = os.environ["BINGX_API_KEY"]
BINGX_API_SECRET = os.environ["BINGX_API_SECRET"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL          = os.getenv("SYMBOL",           "")
INTERVAL        = os.getenv("INTERVAL",         "3m")
HTF_INTERVAL    = os.getenv("HTF_INTERVAL",     "15m")
LEVERAGE        = int(os.getenv("LEVERAGE",     "5"))
DEMO_MODE       = os.getenv("DEMO_MODE",        "false").lower() == "true"

EMA1_LEN        = int(os.getenv("EMA1_LEN",     "2"))
EMA2_LEN        = int(os.getenv("EMA2_LEN",     "4"))
EMA3_LEN        = int(os.getenv("EMA3_LEN",     "20"))
SCORE_MIN       = float(os.getenv("SCORE_MIN",  "40"))

RISK_PCT        = float(os.getenv("RISK_PCT",   "1.0"))
ATR_SL_MULT     = float(os.getenv("ATR_SL_MULT","1.5"))
MAX_DD_PCT      = float(os.getenv("MAX_DD_PCT", "10.0"))
SCANNER_TOP_N   = int(os.getenv("SCANNER_TOP_N","1"))
MIN_VOLUME      = float(os.getenv("MIN_VOLUME", "2000000"))
HEARTBEAT_EVERY = int(os.getenv("HEARTBEAT_EVERY","20"))


class EMABot:
    def __init__(self):
        self.bingx    = BingXClient(BINGX_API_KEY, BINGX_API_SECRET, demo=DEMO_MODE)
        self.tg       = TelegramClient(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
        self.strategy = EMAStrategy(EMA1_LEN, EMA2_LEN, EMA3_LEN, score_min=SCORE_MIN)
        self.risk_mgr = RiskManager(
            risk_pct=RISK_PCT, atr_sl_mult=ATR_SL_MULT,
            max_dd_pct=MAX_DD_PCT, leverage=LEVERAGE,
        )
        self.scanner  = MultiSymbolScanner(
            self.bingx, INTERVAL, HTF_INTERVAL,
            top_n=SCANNER_TOP_N, min_volume=MIN_VOLUME, score_min=SCORE_MIN,
        )
        self.tg.set_bot(self)

        # Estado de posición
        self.active_symbol:  Optional[str]   = SYMBOL if SYMBOL else None
        self.position_side:  Optional[str]   = None
        self.entry_price:    Optional[float] = None
        self.position_qty:   Optional[float] = None
        self.position_qty2:  Optional[float] = None  # qty restante tras TP1
        self.current_sl:     Optional[float] = None
        self.current_tp1:    Optional[float] = None
        self.current_tp2:    Optional[float] = None
        self.r_distance:     Optional[float] = None
        self.current_atr:    Optional[float] = None
        self.tp1_hit:        bool = False

        # Control
        self.paused:         bool = False
        self.candles_seen:   int  = 0
        self.tg_offset:      int  = 0
        self._qty_step       = 0.001
        self._price_prec     = 4

    # ── Setup ──────────────────────────────────────────────────────────────
    def setup(self):
        logger.info("=== EMA Bot v3 ===")
        if not self.active_symbol:
            best = self.scanner.best_symbol()
            self.active_symbol = best.symbol if best else "BTC-USDT"
        self._init_symbol(self.active_symbol)
        self._detect_existing_position()
        self.tg.send_startup(self.active_symbol, INTERVAL, LEVERAGE, DEMO_MODE)

    def _init_symbol(self, symbol: str):
        try:
            info = self.bingx.get_symbol_info(symbol)
            self._qty_step = float(info.get("tradeMinQuantity", "0.001"))
            self._price_prec = len(str(info.get("pricePrecision", "4")))
        except Exception as e:
            logger.warning(f"Symbol info {symbol}: {e}")
        for side in ("LONG", "SHORT"):
            try:    self.bingx.set_leverage(symbol, LEVERAGE, side)
            except: pass
        try:    self.bingx.set_margin_mode(symbol, "ISOLATED")
        except: pass

    def _detect_existing_position(self):
        try:
            for pos in self.bingx.get_positions(self.active_symbol):
                amt = float(pos.get("positionAmt", 0))
                if amt != 0:
                    self.position_side = pos.get("positionSide")
                    self.entry_price   = float(pos.get("avgPrice", 0))
                    self.position_qty  = abs(amt)
                    logger.info(f"Posición existente: {self.position_side} qty={self.position_qty}")
        except Exception as e:
            logger.warning(f"Detect position: {e}")

    # ── Datos ──────────────────────────────────────────────────────────────
    def _df(self, symbol: str, interval: str = None, limit: int = 150) -> pd.DataFrame:
        raw = self.bingx.get_klines(symbol, interval or INTERVAL, limit=limit)
        if not raw:
            raise ValueError(f"Sin datos {symbol}")
        df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
        df = df.astype({"timestamp":"int64","open":"float64","high":"float64",
                        "low":"float64","close":"float64","volume":"float64"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.sort_values("timestamp").reset_index(drop=True)

    def _balance(self) -> float:
        try:
            b = self.bingx.get_balance()
            return float(b.get("availableMargin", b.get("balance", 0)))
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return 0.0

    # ── Trading ────────────────────────────────────────────────────────────
    def _open(self, symbol: str, sig: Signal, balance: float):
        params: Optional[TradeParams] = self.risk_mgr.compute(
            balance=balance, price=sig.price, side=sig.action,
            atr=sig.atr, qty_step=self._qty_step, price_precision=self._price_prec,
        )
        if not params:
            return

        buy_sell = "BUY" if sig.action == "LONG" else "SELL"

        self.tg.send_signal(
            symbol=symbol, action=sig.action, price=sig.price,
            ema1=sig.ema1, ema2=sig.ema2, ema3=sig.ema3,
            rsi=sig.rsi, adx=sig.adx, atr_pct=sig.atr_pct,
            reason=sig.reason, qty=params.quantity, leverage=LEVERAGE,
            score=sig.score, sl_price=params.sl_price,
            tp1=params.tp1_price, tp2=params.tp2_price,
        )

        try:
            self.bingx.cancel_all_orders(symbol)
            order = self.bingx.place_market_order(
                symbol, buy_sell, sig.action, params.quantity
            )

            # SL
            sl_side = "SELL" if sig.action == "LONG" else "BUY"
            self.bingx.place_stop_loss(symbol, sl_side, sig.action,
                                       params.sl_price, params.quantity)
            # TP1 (50%)
            self.bingx.place_take_profit(symbol, sl_side, sig.action,
                                         params.tp1_price, params.qty_tp1)
            # TP2 (50%)
            self.bingx.place_take_profit(symbol, sl_side, sig.action,
                                         params.tp2_price, params.qty_tp2)

            self.position_side = sig.action
            self.entry_price   = sig.price
            self.position_qty  = params.quantity
            self.position_qty2 = params.qty_tp2
            self.current_sl    = params.sl_price
            self.current_tp1   = params.tp1_price
            self.current_tp2   = params.tp2_price
            self.r_distance    = params.r_distance
            self.current_atr   = sig.atr
            self.active_symbol = symbol
            self.tp1_hit       = False

            self.tg.send_order_filled(symbol, sig.action, sig.price,
                                      str(order.get("orderId","")), params.quantity)
            logger.info(f"✅ Abierto {sig.action} {symbol} @ {sig.price}")

        except Exception as e:
            logger.error(f"Error abriendo: {e}")
            self.tg.send_error("open", str(e))

    def _close(self, symbol: str, price: float, reason: str = ""):
        if not self.position_side or not self.position_qty:
            return
        qty = self.position_qty2 if self.tp1_hit else self.position_qty
        try:
            self.bingx.cancel_all_orders(symbol)
            self.bingx.close_position(symbol, self.position_side, qty)

            pnl = 0.0
            if self.entry_price:
                pnl = (price - self.entry_price) if self.position_side == "LONG" \
                      else (self.entry_price - price)
                pnl *= qty * LEVERAGE

            self.tg.send_close(symbol, self.position_side,
                               self.entry_price or 0, price, pnl, qty, reason)
            self.risk_mgr.tracker.record(pnl, symbol, self.position_side)
            logger.info(f"🔒 Cerrado {self.position_side} {symbol} pnl={pnl:+.2f}")

            self.position_side = self.entry_price = self.position_qty = None
            self.position_qty2 = self.current_sl  = self.current_tp1  = None
            self.current_tp2   = self.r_distance  = self.current_atr  = None
            self.tp1_hit = False

        except Exception as e:
            logger.error(f"Error cerrando: {e}")
            self.tg.send_error("close", str(e))

    def _manage_open_position(self, price: float):
        """Breakeven + trailing stop en posición abierta"""
        if not self.position_side or not self.entry_price:
            return

        # Breakeven cuando toca TP1
        if not self.tp1_hit and self.current_tp1:
            hit = (self.position_side == "LONG"  and price >= self.current_tp1) or \
                  (self.position_side == "SHORT" and price <= self.current_tp1)
            if hit:
                self.tp1_hit = True
                # Mover SL a breakeven
                self.current_sl = self.risk_mgr.breakeven_sl(
                    self.position_side, self.entry_price, price,
                    self.current_sl or self.entry_price, self.r_distance or 0
                )
                pnl_tp1 = abs(self.current_tp1 - self.entry_price) * \
                          (self.position_qty or 0) * 0.5 * LEVERAGE
                self.tg.send_tp_hit(
                    self.active_symbol, self.position_side, 1,
                    price, pnl_tp1, self.position_qty2 or 0
                )
                logger.info(f"TP1 hit | SL → breakeven {self.current_sl}")

        # Trailing stop ATR-based (activo desde 1.5R)
        if self.current_atr and self.r_distance:
            self.current_sl = self.risk_mgr.trailing_sl(
                self.position_side, price, self.current_sl or 0,
                self.current_atr, activate_r=1.5,
                entry=self.entry_price or 0, r=self.r_distance,
            )

    # ── Comandos Telegram ──────────────────────────────────────────────────
    def _handle_commands(self):
        try:
            updates = self.tg.get_updates(self.tg_offset)
            for upd in updates:
                self.tg_offset = upd["update_id"] + 1
                msg = upd.get("message", {}).get("text", "").lower().strip()
                cid = str(upd.get("message", {}).get("chat", {}).get("id", ""))

                if cid != TELEGRAM_CHAT_ID:
                    continue

                if msg == "/status":
                    pos = self.position_side or "Sin posición"
                    bal = self._balance()
                    self.tg._send(
                        f"ℹ️ <b>Estado del Bot</b>\n\n"
                        f"Par activo: <code>{self.active_symbol}</code>\n"
                        f"Posición:   <code>{pos}</code>\n"
                        f"Pausado:    <code>{'Sí' if self.paused else 'No'}</code>\n"
                        f"Balance:    <code>${bal:,.2f}</code>\n"
                        f"Velas:      <code>{self.candles_seen}</code>"
                    )
                elif msg == "/balance":
                    self.tg.send_balance(self._balance())
                elif msg == "/trades":
                    self.tg.send_stats(self.risk_mgr.tracker.summary())
                elif msg == "/pausa":
                    self.paused = True
                    self.tg.send_paused()
                elif msg == "/reanudar":
                    self.paused = False
                    self.tg.send_resumed()
                elif msg == "/stop":
                    self.tg._send("⛔ Deteniendo bot...")
                    if self.active_symbol and self.position_side:
                        price = float(self.bingx.get_ticker(self.active_symbol).get("lastPrice", 0))
                        self._close(self.active_symbol, price, "Stop manual")
                    raise SystemExit("Stop por Telegram")
                elif msg == "/scan":
                    results = self.scanner.scan(force=True)
                    self.tg._send(self.scanner.format_report(results[:5]))
        except SystemExit:
            raise
        except Exception as e:
            logger.debug(f"Command error: {e}")

    # ── Tick ───────────────────────────────────────────────────────────────
    def tick(self):
        self.candles_seen += 1
        self._handle_commands()

        if self.paused:
            logger.info("Bot pausado — skip tick")
            return

        # ── Sin posición: escanear ──────────────────────────────────────
        if not self.position_side:
            results = self.scanner.scan()

            if results:
                best = results[0]
                logger.info(f"Mejor señal: {best.symbol} {best.signal} score={best.score}")
                self.tg.send_scan_result(len(results), best.symbol, best.score)
                self._init_symbol(best.symbol)
                balance = self._balance()

                from strategy import Signal as Sig
                sig = Sig(
                    action=best.signal, price=best.price,
                    ema1=best.ema1, ema2=best.ema2, ema3=best.ema3,
                    rsi=best.rsi, adx=best.adx,
                    atr=best.atr_pct * best.price / 100,
                    atr_pct=best.atr_pct, volume_ok=True,
                    reason=best.reason, timestamp=best.symbol,
                    score=best.sig_score,
                )
                self._open(best.symbol, sig, balance)
            else:
                logger.info("Sin señales activas")
                if self.candles_seen % 5 == 0:
                    self.tg.send_scan_result(0, "", 0)
            return

        # ── Con posición: gestionar ─────────────────────────────────────
        symbol = self.active_symbol
        try:
            df     = self._df(symbol)
            htf_df = self._df(symbol, HTF_INTERVAL, 60)
            sig    = self.strategy.get_latest_signal(df, htf_df)
            price  = float(df["close"].iloc[-1])

            # Gestión interna (breakeven, trailing)
            self._manage_open_position(price)

            # Señal contraria → cerrar
            flip = (sig.action == "LONG"  and self.position_side == "SHORT") or \
                   (sig.action == "SHORT" and self.position_side == "LONG")
            if flip:
                self._close(symbol, price, f"Señal invertida a {sig.action}")
                time.sleep(0.3)
                # Abrir nueva posición inmediatamente
                balance = self._balance()
                self._open(symbol, sig, balance)

        except Exception as e:
            logger.error(f"Tick error: {e}", exc_info=True)
            self.tg.send_error(f"tick:{symbol}", str(e))

        # Heartbeat
        if self.candles_seen % HEARTBEAT_EVERY == 0:
            try:
                df2 = self._df(symbol)
                c   = self.strategy.compute(df2)
                self.tg.send_heartbeat(
                    symbol, float(df2["close"].iloc[-1]),
                    self.position_side or "FLAT",
                    float(c["ema3"].iloc[-1]),
                    self._balance(), self.candles_seen,
                    self.risk_mgr.tracker.total_pnl,
                )
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")

    # ── Loop ───────────────────────────────────────────────────────────────
    def run(self):
        self.setup()
        secs = _interval_secs(INTERVAL)
        logger.info(f"🚀 Bot activo | {INTERVAL} ({secs}s)")

        while True:
            try:
                now  = time.time()
                wait = max(0, (int(now / secs) + 1) * secs + 2 - now)
                logger.info(f"⏳ Esperando {wait:.1f}s al cierre de vela...")
                time.sleep(wait)
                self.tick()
            except (KeyboardInterrupt, SystemExit):
                logger.info("Bot detenido")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
                self.tg.send_error("loop", str(e))
                time.sleep(20)


def _interval_secs(iv: str) -> int:
    return int(iv[:-1]) * {"m":60,"h":3600,"d":86400}.get(iv[-1], 60)


if __name__ == "__main__":
    EMABot().run()
