"""
EMA Bot v5 — Diagnóstico completo + ejecución robusta
"""
import logging, os, sys, time
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
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("bot.log", encoding="utf-8")],
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
SCORE_MIN       = float(os.getenv("SCORE_MIN",  "30"))

RISK_PCT        = float(os.getenv("RISK_PCT",   "1.0"))
ATR_SL_MULT     = float(os.getenv("ATR_SL_MULT","1.5"))
MAX_DD_PCT      = float(os.getenv("MAX_DD_PCT", "10.0"))
SCANNER_TOP_N   = int(os.getenv("SCANNER_TOP_N","1"))
MIN_VOLUME      = float(os.getenv("MIN_VOLUME", "1000000"))
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
        self.scanner = MultiSymbolScanner(
            self.bingx, INTERVAL, HTF_INTERVAL,
            top_n=SCANNER_TOP_N, min_volume=MIN_VOLUME, score_min=SCORE_MIN,
        )

        self.active_symbol: Optional[str]   = SYMBOL if SYMBOL else None
        self.position_side: Optional[str]   = None
        self.entry_price:   Optional[float] = None
        self.position_qty:  Optional[float] = None
        self.position_qty2: Optional[float] = None
        self.current_sl:    Optional[float] = None
        self.current_tp1:   Optional[float] = None
        self.current_tp2:   Optional[float] = None
        self.r_distance:    Optional[float] = None
        self.current_atr:   Optional[float] = None
        self.tp1_hit:       bool = False
        self.paused:        bool = False
        self.candles_seen:  int  = 0
        self.tg_offset:     int  = 0
        self._qty_step      = 0.001
        self._price_prec    = 4

    # ── Setup ──────────────────────────────────────────────────────────────
    def setup(self):
        logger.info("=== EMA Bot v5 ===")
        mode = "PAPER" if DEMO_MODE else "LIVE"
        logger.info(f"Modo: {mode} | Leverage: {LEVERAGE}x | Score min: {SCORE_MIN}")
        self._test_connection()
        if not self.active_symbol:
            best = self.scanner.best_symbol()
            self.active_symbol = best.symbol if best else "BTC-USDT"
        self._init_symbol(self.active_symbol)
        self._detect_position()
        bal = self._balance()
        self.tg.send_startup(self.active_symbol, INTERVAL, LEVERAGE, DEMO_MODE)
        self.tg._send(
            f"⚙️ <b>Config cargada</b>\n\n"
            f"Balance: <code>${bal:,.2f} USDT</code>\n"
            f"Score mínimo: <code>{SCORE_MIN}</code>\n"
            f"Volumen mínimo: <code>${MIN_VOLUME/1e6:.1f}M</code>\n"
            f"SL: <code>ATR × {ATR_SL_MULT}</code>\n"
            f"Riesgo por trade: <code>{RISK_PCT}%</code>\n"
            f"Modo: <code>{'PAPER 🟡' if DEMO_MODE else 'LIVE 🟢'}</code>"
        )

    def _test_connection(self):
        try:
            bal = self.bingx.get_balance()
            logger.info(f"✅ BingX conectado | balance={bal}")
        except Exception as e:
            self.tg.send_error("conexión BingX", str(e))
            logger.error(f"❌ BingX connection failed: {e}")

    def _init_symbol(self, symbol: str):
        try:
            info = self.bingx.get_symbol_info(symbol)
            self._qty_step = float(info.get("tradeMinQuantity", "0.001"))
            logger.info(f"Symbol {symbol} | qty_step={self._qty_step}")
        except Exception as e:
            logger.warning(f"Symbol info {symbol}: {e}")
        for side in ("LONG", "SHORT"):
            try:    self.bingx.set_leverage(symbol, LEVERAGE, side)
            except Exception as e: logger.debug(f"Leverage {side}: {e}")
        try:    self.bingx.set_margin_mode(symbol, "ISOLATED")
        except Exception as e: logger.debug(f"MarginMode: {e}")

    def _detect_position(self):
        try:
            for pos in self.bingx.get_positions(self.active_symbol or ""):
                amt = float(pos.get("positionAmt", 0))
                if abs(amt) > 0:
                    self.position_side = pos.get("positionSide")
                    self.entry_price   = float(pos.get("avgPrice", 0))
                    self.position_qty  = abs(amt)
                    logger.info(f"Posición existente: {self.position_side} {self.position_qty}")
        except Exception as e:
            logger.debug(f"Detect position: {e}")

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
            v = float(b.get("availableMargin", b.get("balance", b.get("equity", 0))))
            logger.info(f"Balance disponible: ${v:.2f}")
            return v
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return 0.0

    # ── Open ───────────────────────────────────────────────────────────────
    def _open(self, symbol: str, sig: Signal, balance: float):
        logger.info(f"--- INTENTANDO ABRIR {sig.action} {symbol} ---")
        if balance <= 0:
            msg = f"Balance cero o negativo: ${balance}"
            logger.error(msg); self.tg.send_error("balance", msg); return

        logger.info(f"Risk params: balance={balance:.2f} price={sig.price} atr={sig.atr:.6f}")
        params: Optional[TradeParams] = self.risk_mgr.compute(
            balance=balance, price=sig.price, side=sig.action,
            atr=sig.atr, qty_step=self._qty_step, price_precision=self._price_prec,
        )
        if params is None:
            sl_dist  = sig.atr * ATR_SL_MULT
            sl_pct   = sl_dist / sig.price if sig.price > 0 else 0
            notional = (balance * RISK_PCT/100 * LEVERAGE) / max(sl_pct, 0.0001)
            msg = (
                f"Risk manager rechazó el trade\n"
                f"Balance: ${balance:.2f}\n"
                f"Riesgo: ${balance*RISK_PCT/100:.2f}\n"
                f"Notional calculado: ${notional:.2f}\n"
                f"Mínimo requerido: $5\n"
                f"ATR: {sig.atr:.6f} ({sig.atr_pct:.2f}%)\n"
                f"SL dist: {sl_pct*100:.3f}%"
            )
            logger.error(f"Risk manager None: {msg}")
            self.tg.send_error("risk_manager", msg)
            return

        logger.info(f"Params OK: qty={params.quantity} sl={params.sl_price} tp1={params.tp1_price} tp2={params.tp2_price}")
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
            logger.info(f"Cancelando órdenes abiertas en {symbol}...")
            try:    self.bingx.cancel_all_orders(symbol)
            except: pass

            logger.info(f"Colocando orden MARKET {buy_sell} {sig.action} {params.quantity} {symbol}...")
            order = self.bingx.place_market_order(symbol, buy_sell, sig.action, params.quantity)
            logger.info(f"Orden colocada: {order}")
            order_id = str(order.get("orderId",
                order.get("data", {}).get("order", {}).get("orderId", "OK")))

            try:
                sl_side = "SELL" if sig.action == "LONG" else "BUY"
                self.bingx.place_stop_loss(symbol, sl_side, sig.action,
                                           params.sl_price, params.quantity)
                logger.info(f"SL colocado @ {params.sl_price}")
            except Exception as e:
                logger.warning(f"SL error: {e}")
                self.tg.send_error("SL", str(e))

            try:
                tp_side = "SELL" if sig.action == "LONG" else "BUY"
                self.bingx.place_take_profit(symbol, tp_side, sig.action,
                                             params.tp1_price, params.qty_tp1)
                self.bingx.place_take_profit(symbol, tp_side, sig.action,
                                             params.tp2_price, params.qty_tp2)
                logger.info(f"TP1={params.tp1_price} TP2={params.tp2_price}")
            except Exception as e:
                logger.warning(f"TP error: {e}")

            self.position_side  = sig.action
            self.entry_price    = sig.price
            self.position_qty   = params.quantity
            self.position_qty2  = params.qty_tp2
            self.current_sl     = params.sl_price
            self.current_tp1    = params.tp1_price
            self.current_tp2    = params.tp2_price
            self.r_distance     = params.r_distance
            self.current_atr    = sig.atr
            self.active_symbol  = symbol
            self.tp1_hit        = False

            self.tg.send_order_filled(symbol, sig.action, sig.price, order_id, params.quantity)
            logger.info(f"✅ POSICIÓN ABIERTA: {sig.action} {symbol} @ {sig.price} qty={params.quantity}")

        except Exception as e:
            logger.error(f"❌ Error ejecutando orden: {e}", exc_info=True)
            self.tg.send_error(f"order_execution {symbol}", str(e))

    # ── Close ──────────────────────────────────────────────────────────────
    def _close(self, symbol: str, price: float, reason: str = ""):
        if not self.position_side or not self.position_qty:
            return
        qty = self.position_qty2 if (self.tp1_hit and self.position_qty2) else self.position_qty
        try:
            try:    self.bingx.cancel_all_orders(symbol)
            except: pass
            self.bingx.close_position(symbol, self.position_side, qty)
            pnl = 0.0
            if self.entry_price:
                pnl = ((price - self.entry_price) if self.position_side == "LONG"
                       else (self.entry_price - price)) * qty * LEVERAGE
            self.tg.send_close(symbol, self.position_side,
                               self.entry_price or 0, price, pnl, qty, reason)
            self.risk_mgr.tracker.record(pnl, symbol, self.position_side)
            logger.info(f"Cerrado {self.position_side} {symbol} pnl={pnl:+.2f}")
            self.position_side = self.entry_price = self.position_qty = None
            self.position_qty2 = self.current_sl  = self.current_tp1  = None
            self.current_tp2   = self.r_distance  = self.current_atr  = None
            self.tp1_hit = False
        except Exception as e:
            logger.error(f"Close error: {e}")
            self.tg.send_error("close", str(e))

    # ── Gestión posición abierta ────────────────────────────────────────────
    def _manage_position(self, price: float):
        if not self.position_side or not self.entry_price:
            return
        if not self.tp1_hit and self.current_tp1:
            hit = ((self.position_side == "LONG"  and price >= self.current_tp1) or
                   (self.position_side == "SHORT" and price <= self.current_tp1))
            if hit:
                self.tp1_hit = True
                # ✅ FIX: nombre correcto del método
                self.current_sl = self.risk_mgr.breakeven(
                    self.position_side, self.entry_price, price,
                    self.current_sl or self.entry_price, self.r_distance or 0
                )
                pnl_tp1 = abs(self.current_tp1 - self.entry_price) * \
                          (self.position_qty or 0) * 0.5 * LEVERAGE
                self.tg.send_tp_hit(self.active_symbol, self.position_side,
                                    1, price, pnl_tp1, self.position_qty2 or 0)
        if self.current_atr and self.r_distance:
            # ✅ FIX: nombre correcto del método
            self.current_sl = self.risk_mgr.trailing(
                self.position_side, price, self.current_sl or 0,
                self.current_atr, entry=self.entry_price or 0,
                r=self.r_distance, mult=1.5,
            )

    # ── Comandos Telegram ──────────────────────────────────────────────────
    def _commands(self):
        try:
            for upd in self.tg.get_updates(self.tg_offset):
                self.tg_offset = upd["update_id"] + 1
                msg = upd.get("message", {}).get("text", "").lower().strip()
                cid = str(upd.get("message", {}).get("chat", {}).get("id", ""))
                if cid != TELEGRAM_CHAT_ID:
                    continue
                if msg == "/status":
                    bal = self._balance()
                    self.tg._send(
                        f"ℹ️ <b>Estado</b>\n\n"
                        f"Par: <code>{self.active_symbol}</code>\n"
                        f"Posición: <code>{self.position_side or 'Sin posición'}</code>\n"
                        f"Entrada: <code>{self.entry_price or '—'}</code>\n"
                        f"Pausado: <code>{'Sí' if self.paused else 'No'}</code>\n"
                        f"Balance: <code>${bal:,.2f}</code>\n"
                        f"Velas: <code>{self.candles_seen}</code>"
                    )
                elif msg == "/balance":
                    self.tg.send_balance(self._balance())
                elif msg == "/trades":
                    self.tg.send_stats(self.risk_mgr.tracker.summary())
                elif msg == "/pausa":
                    self.paused = True; self.tg.send_paused()
                elif msg == "/reanudar":
                    self.paused = False; self.tg.send_resumed()
                elif msg == "/scan":
                    r = self.scanner.scan(force=True)
                    self.tg._send(self.scanner.format_report(r[:5]))
                elif msg == "/stop":
                    self.tg._send("⛔ Deteniendo...")
                    if self.active_symbol and self.position_side:
                        try:
                            p = float(self.bingx.get_ticker(self.active_symbol).get("lastPrice", 0))
                            self._close(self.active_symbol, p, "Stop manual")
                        except: pass
                    raise SystemExit("Stop Telegram")
        except SystemExit:
            raise
        except Exception as e:
            logger.debug(f"Commands: {e}")

    # ── Tick ───────────────────────────────────────────────────────────────
    def tick(self):
        self.candles_seen += 1
        self._commands()
        if self.paused:
            logger.info("Pausado"); return

        if not self.position_side:
            logger.info("Sin posición — escaneando...")
            results = self.scanner.scan()
            if not results:
                logger.info("Sin señales activas")
                if self.candles_seen % 3 == 0:
                    self.tg._send("🔍 Scan: sin señales activas ahora. Esperando próxima vela...")
                return

            best = results[0]
            logger.info(f"SEÑAL: {best.symbol} {best.signal} score={best.score}")
            self.tg._send(self.scanner.format_report(results[:3]))
            self._init_symbol(best.symbol)
            balance = self._balance()
            if balance <= 1:
                self.tg.send_error("balance_insuficiente",
                    f"Balance muy bajo: ${balance:.2f}\nMínimo recomendado: $10 USDT")
                return

            sig = Signal(
                action    = best.signal,
                price     = best.price,
                ema1      = best.ema1,
                ema2      = best.ema2,
                ema3      = best.ema3,
                rsi       = best.rsi,
                adx       = best.adx,
                atr       = best.atr_pct * best.price / 100,
                atr_pct   = best.atr_pct,
                volume_ok = True,
                reason    = best.reason,
                timestamp = best.symbol,
                score     = best.sig_score,
            )
            self._open(best.symbol, sig, balance)
            return

        symbol = self.active_symbol
        try:
            df     = self._df(symbol)
            htf_df = self._df(symbol, HTF_INTERVAL, 60)
            sig    = self.strategy.get_latest_signal(df, htf_df)
            price  = float(df["close"].iloc[-1])
            logger.info(f"[{symbol}] precio={price:.4f} posición={self.position_side} señal={sig.action}")
            self._manage_position(price)
            flip = ((sig.action == "LONG"  and self.position_side == "SHORT") or
                    (sig.action == "SHORT" and self.position_side == "LONG"))
            if flip:
                logger.info(f"Señal invertida: cerrando {self.position_side} → abriendo {sig.action}")
                self._close(symbol, price, f"Flip a {sig.action}")
                time.sleep(0.5)
                balance = self._balance()
                self._open(symbol, sig, balance)
        except Exception as e:
            logger.error(f"Tick error {symbol}: {e}", exc_info=True)
            self.tg.send_error(f"tick:{symbol}", str(e))

        if self.candles_seen % HEARTBEAT_EVERY == 0:
            try:
                df2 = self._df(symbol)
                c   = self.strategy.compute(df2)
                self.tg.send_heartbeat(
                    symbol, float(df2["close"].iloc[-1]),
                    self.position_side or "FLAT",
                    float(c["ema3"].iloc[-1]),
                    self._balance(), self.candles_seen,
                    self.risk_mgr.tracker.pnl,   # ✅ FIX: era .total_pnl
                )
            except: pass

    # ── Loop ───────────────────────────────────────────────────────────────
    def run(self):
        self.setup()
        secs = _secs(INTERVAL)
        logger.info(f"🚀 Loop activo | {INTERVAL}")
        while True:
            try:
                now  = time.time()
                wait = max(0, (int(now / secs) + 1) * secs + 2 - now)
                logger.info(f"⏳ Próxima vela en {wait:.0f}s")
                time.sleep(wait)
                self.tick()
            except (KeyboardInterrupt, SystemExit):
                logger.info("Bot detenido"); break
            except Exception as e:
                logger.error(f"Loop: {e}", exc_info=True)
                self.tg.send_error("loop", str(e))
                time.sleep(20)


def _secs(iv: str) -> int:
    return int(iv[:-1]) * {"m":60,"h":3600,"d":86400}.get(iv[-1], 60)


if __name__ == "__main__":
    EMABot().run()
