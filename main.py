"""
SuperBot Main Orchestrator
FIXED: env var naming (BINGX_API_KEY / BINGX_API_SECRET),
       order tracking, limit order fill detection, Telegram alerts,
       TP1 PnL recording bug, R:R protection.

Loop: scan → filter → open trades → manage positions → repeat
Commission strategy:
  - LIMIT entry orders  (maker 0.02% vs taker 0.05% = 60% cheaper)
  - Partial close at TP1 to lock profit
  - Full close at TP2
  - SL set via order (not manual monitoring)
"""
import logging, os, time, json, uuid
from datetime import datetime, timezone
from typing import Optional

from bingx_client import BingXClient
from scanner import Scanner
from risk_manager import RiskManager, TradeParams
from strategy import Signal
import notifier

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("BOT")

# ── Config from ENV ───────────────────────────────────────────────────
def _get_env(keys: list, required: bool = True) -> str:
    for key in keys:
        val = os.environ.get(key, "")
        if val:
            return val
    if required:
        raise EnvironmentError(
            f"Required environment variable not found. "
            f"Set one of: {keys}"
        )
    return ""

API_KEY     = _get_env(["BINGX_API_KEY"])
SECRET_KEY  = _get_env(["BINGX_API_SECRET", "BINGX_SECRET_KEY"])

SCAN_PERIOD     = int(os.environ.get("SCAN_PERIOD_SECONDS", "900"))
DRY_RUN         = os.environ.get("DRY_RUN", "true").lower() == "true"
LIMIT_ENTRY     = os.environ.get("LIMIT_ENTRY", "true").lower() == "true"
SLIPPAGE_OFFSET = float(os.environ.get("SLIPPAGE_OFFSET", "0.0003"))

# Risk parameters
# FIXED: reduced defaults — leverage 2x, risk 1% to protect small balance
RISK_PER_TRADE   = float(os.environ.get("RISK_PER_TRADE",   "0.01"))   # was 0.02
MAX_POSITIONS    = int(os.environ.get("MAX_OPEN_TRADES",     "5"))
LEVERAGE         = int(os.environ.get("LEVERAGE",            "2"))      # was 5
DAILY_LOSS_LIMIT = float(os.environ.get("DAILY_LOSS_LIMIT",  "0.04"))  # was 0.05 — tighter

# Minimum R:R ratio — skip trade if reward doesn't justify risk
# FIXED: enforce R:R >= 1.5 before opening any trade
MIN_RR_RATIO     = float(os.environ.get("MIN_RR_RATIO", "1.5"))

# Limit order: cancel and re-place if unfilled after N seconds
LIMIT_ORDER_TIMEOUT = int(os.environ.get("LIMIT_ORDER_TIMEOUT", "120"))

# ── State persistence ─────────────────────────────────────────────────
STATE_FILE = "/tmp/superbot_state.json"

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"open_trades": {}, "daily_date": "", "stats": {}}

def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"State save error: {e}")


class SuperBot:
    def __init__(self):
        self.client  = BingXClient(API_KEY, SECRET_KEY)
        self.scanner = Scanner(self.client)
        self.risk    = RiskManager(
            risk_pct=RISK_PER_TRADE,
            max_pos=MAX_POSITIONS,
            leverage=LEVERAGE,
            daily_loss_limit=DAILY_LOSS_LIMIT,
        )
        self.state   = load_state()
        self._symbol_info_cache: dict = {}

        log.info(
            f"🤖 SuperBot initialized | DRY_RUN={DRY_RUN} | "
            f"Risk={RISK_PER_TRADE*100:.1f}% | Leverage={LEVERAGE}x | "
            f"Max positions={MAX_POSITIONS} | MinRR={MIN_RR_RATIO}"
        )
        self._init_daily()

    def _init_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.get("daily_date") != today:
            balance = self.client.get_balance()
            self.risk.reset_daily(balance)
            self.state["daily_date"] = today
            save_state(self.state)
            log.info(f"📅 New trading day: {today} | Balance: {balance:.2f} USDT")
            if today != "":
                notifier.notify_startup(balance, DRY_RUN)

    # ── Symbol precision ──────────────────────────────────────────────
    def _get_precision(self, symbol: str) -> tuple:
        if symbol not in self._symbol_info_cache:
            info = self.client.get_symbol_info(symbol)
            self._symbol_info_cache[symbol] = (
                int(info.get("quantityPrecision", 3)),
                int(info.get("pricePrecision", 4)),
            )
        return self._symbol_info_cache[symbol]

    # ── R:R validation ────────────────────────────────────────────────
    # FIXED: new method — skip trades with poor reward-to-risk ratio
    def _check_rr(self, signal: Signal) -> bool:
        try:
            risk   = abs(signal.entry - signal.sl)
            reward = abs(signal.tp2  - signal.entry)
            if risk <= 0:
                log.warning("R:R check failed — zero risk distance")
                return False
            rr = reward / risk
            if rr < MIN_RR_RATIO:
                log.info(f"⛔ R:R={rr:.2f} < {MIN_RR_RATIO} — skipping trade")
                return False
            log.info(f"✅ R:R={rr:.2f} — acceptable")
            return True
        except Exception as e:
            log.warning(f"R:R check error: {e}")
            return False

    # ── Open new trade ────────────────────────────────────────────────
    def _open_trade(self, symbol: str, signal: Signal):
        if symbol in self.state["open_trades"]:
            log.info(f"Already in trade for {symbol}, skip.")
            return

        # FIXED: validate R:R before sizing position
        if not self._check_rr(signal):
            return

        balance = self.client.get_balance()
        open_count = len(self.state["open_trades"])
        if not self.risk.can_open_trade(open_count, balance):
            return

        qty_p, price_p = self._get_precision(symbol)
        params = self.risk.size_position(
            symbol, signal.direction,
            signal.entry, signal.sl, signal.tp1, signal.tp2, signal.tp3,
            balance, qty_p, price_p,
        )
        if not params:
            return

        trade_id = str(uuid.uuid4())[:8]

        if DRY_RUN:
            log.info(
                f"🔵 [DRY RUN] {symbol} {params.direction} x{params.quantity} "
                f"@ {params.entry_price} SL={params.sl_price} TP1={params.tp1_price}"
            )
            self.state["open_trades"][symbol] = {
                "trade_id":  trade_id,
                "direction": params.direction,
                "entry":     params.entry_price,
                "sl":        params.sl_price,
                "tp1":       params.tp1_price,
                "tp2":       params.tp2_price,
                "tp3":       params.tp3_price,
                "qty":       params.quantity,
                "qty_p":     qty_p,
                "tp1_hit":   False,
                "tp2_hit":   False,
                "order_id":  "DRY-" + trade_id,
                "opened_at": datetime.utcnow().isoformat(),
                "status":    "FILLED",
            }
            save_state(self.state)
            notifier.notify_trade_opened(
                symbol, params.direction, params.quantity,
                params.entry_price, params.sl_price,
                params.tp1_price, params.tp2_price,
                params.notional, dry_run=True
            )
            return

        try:
            # Set leverage and margin type
            try:
                self.client.set_margin_type(symbol, "ISOLATED")
            except Exception:
                pass
            self.client.set_leverage(symbol, params.leverage)

            side     = "BUY"  if params.direction == "LONG"  else "SELL"
            pos_side = params.direction

            if LIMIT_ENTRY:
                offset    = SLIPPAGE_OFFSET * params.entry_price
                limit_px  = (params.entry_price - offset if side == "BUY"
                             else params.entry_price + offset)
                limit_px  = round(limit_px, price_p)
                order_type = "LIMIT"
                price_arg  = limit_px
            else:
                order_type = "MARKET"
                price_arg  = None

            result   = self.client.place_order(
                symbol, side, pos_side, order_type, params.quantity,
                price=price_arg,
                stop_loss=params.sl_price,
                client_order_id=f"sb_{trade_id}",
            )

            order_id = result.get("data", {}).get("orderId", "")
            if not order_id:
                log.error(f"❌ No orderId returned for {symbol}: {result}")
                return

            log.info(
                f"✅ Order placed {symbol} {params.direction} qty={params.quantity} "
                f"@ {price_arg or 'MARKET'} SL={params.sl_price} | orderId={order_id}"
            )

            self.state["open_trades"][symbol] = {
                "trade_id":  trade_id,
                "direction": params.direction,
                "entry":     params.entry_price,
                "sl":        params.sl_price,
                "tp1":       params.tp1_price,
                "tp2":       params.tp2_price,
                "tp3":       params.tp3_price,
                "qty":       params.quantity,
                "qty_p":     qty_p,
                "tp1_hit":   False,
                "tp2_hit":   False,
                "order_id":  str(order_id),
                "opened_at": datetime.utcnow().isoformat(),
                "status":    "PENDING" if LIMIT_ENTRY else "FILLED",
                "limit_px":  price_arg,
            }
            save_state(self.state)

            notifier.notify_trade_opened(
                symbol, params.direction, params.quantity,
                price_arg or params.entry_price, params.sl_price,
                params.tp1_price, params.tp2_price,
                params.notional, dry_run=False
            )

        except Exception as e:
            log.error(f"❌ Failed to open {symbol}: {e}", exc_info=True)

    # ── Check pending limit orders ─────────────────────────────────────
    def _check_pending_orders(self):
        """Check if limit entry orders got filled; cancel stale ones."""
        for symbol, trade in list(self.state["open_trades"].items()):
            if trade.get("status") != "PENDING":
                continue

            order_id  = trade.get("order_id", "")
            opened_at = trade.get("opened_at", "")

            if not order_id:
                continue

            try:
                order = self.client.get_order_status(symbol, order_id)
                status = order.get("status", "")

                if status in ("FILLED", "PARTIALLY_FILLED"):
                    actual_entry = float(order.get("avgPrice", trade["entry"]) or trade["entry"])
                    self.state["open_trades"][symbol]["status"] = "FILLED"
                    self.state["open_trades"][symbol]["entry"]  = actual_entry
                    log.info(f"✅ Limit order FILLED {symbol} @ {actual_entry}")
                    save_state(self.state)

                elif status in ("CANCELED", "EXPIRED", "REJECTED"):
                    log.info(f"⚠️ Order {status} for {symbol}, removing trade.")
                    del self.state["open_trades"][symbol]
                    save_state(self.state)

                else:
                    try:
                        dt = datetime.fromisoformat(opened_at)
                        age = (datetime.utcnow() - dt).total_seconds()
                        if age > LIMIT_ORDER_TIMEOUT:
                            log.info(
                                f"⏰ Limit order timeout ({age:.0f}s) for {symbol}, "
                                f"canceling..."
                            )
                            self.client.cancel_all_orders(symbol)
                            del self.state["open_trades"][symbol]
                            save_state(self.state)
                    except Exception:
                        pass

            except Exception as e:
                log.debug(f"Order check error {symbol}: {e}")

    # ── Manage open positions ──────────────────────────────────────────
    def _manage_positions(self):
        """Check TP1/TP2 conditions and close accordingly."""

        active_trades = {
            sym: t for sym, t in self.state["open_trades"].items()
            if t.get("status") == "FILLED"
        }

        if not active_trades:
            return

        exchange_positions = {}
        if not DRY_RUN:
            try:
                for p in self.client.get_positions():
                    exchange_positions[p["symbol"]] = p
            except Exception as e:
                log.error(f"Failed to fetch positions: {e}")
                return

        for symbol, trade in list(active_trades.items()):
            direction = trade["direction"]
            qty       = trade["qty"]
            tp1       = trade["tp1"]
            tp2       = trade["tp2"]
            tp1_hit   = trade.get("tp1_hit", False)
            qty_p     = trade.get("qty_p", 3)

            if not DRY_RUN and symbol not in exchange_positions:
                log.info(f"📤 Position closed externally: {symbol} (SL or TP hit by exchange)")
                entry = trade.get("entry", 0)
                sl    = trade.get("sl", 0)
                loss  = abs(entry - sl) * qty if entry and sl else 0
                self.risk.record_pnl(-loss, trade.get("est_fee", 0))
                notifier.notify_sl_hit(symbol, sl, loss)
                del self.state["open_trades"][symbol]
                save_state(self.state)
                continue

            try:
                ticker = self.client.get_ticker(symbol)
                price  = float(ticker.get("lastPrice", trade["entry"]) or trade["entry"])
            except Exception as e:
                log.debug(f"Ticker error {symbol}: {e}")
                continue

            if price <= 0:
                continue

            # ── TP1: Partial close (50%) ─────────────────────────────
            if not tp1_hit:
                tp1_reached = (
                    (direction == "LONG"  and price >= tp1) or
                    (direction == "SHORT" and price <= tp1)
                )
                if tp1_reached:
                    partial_qty = self.risk.partial_close_qty(qty, qty_p)
                    log.info(f"💰 TP1 hit {symbol} @ {price} | Closing {partial_qty}")
                    if not DRY_RUN:
                        try:
                            self.client.close_position(symbol, direction, partial_qty)
                        except Exception as e:
                            log.error(f"Partial close error {symbol}: {e}")

                    # FIXED: record actual PnL for the closed qty, not halved estimate
                    est_pnl = abs(price - trade["entry"]) * partial_qty
                    self.risk.record_pnl(est_pnl)
                    notifier.notify_tp_hit(symbol, 1, price, partial_qty, est_pnl)

                    remaining = round(qty - partial_qty, qty_p)
                    self.state["open_trades"][symbol]["tp1_hit"] = True
                    self.state["open_trades"][symbol]["qty"]     = remaining
                    save_state(self.state)

            # ── TP2: Full close ───────────────────────────────────────
            if tp1_hit:
                tp2_reached = (
                    (direction == "LONG"  and price >= tp2) or
                    (direction == "SHORT" and price <= tp2)
                )
                if tp2_reached:
                    remaining = self.state["open_trades"][symbol]["qty"]
                    log.info(f"🎯 TP2 hit {symbol} @ {price} | Closing {remaining}")
                    if not DRY_RUN:
                        try:
                            self.client.close_position(symbol, direction, remaining)
                        except Exception as e:
                            log.error(f"TP2 close error {symbol}: {e}")
                    est_pnl = abs(price - trade["entry"]) * remaining
                    self.risk.record_pnl(est_pnl)
                    notifier.notify_tp_hit(symbol, 2, price, remaining, est_pnl)
                    del self.state["open_trades"][symbol]
                    save_state(self.state)

    # ── Status log ─────────────────────────────────────────────────────
    def _log_status(self):
        balance   = self.client.get_balance() if not DRY_RUN else 0
        equity    = self.client.get_total_equity() if not DRY_RUN else 0
        open_syms = list(self.state["open_trades"].keys())
        stats     = self.risk.get_stats()
        log.info(
            f"📊 Status | Balance: ${balance:.2f} Equity: ${equity:.2f} | "
            f"Open: {open_syms} | "
            f"DailyPnL: {stats['daily_pnl']:+.2f} | "
            f"Fees: ${stats['total_fees']:.3f}"
        )

    # ── Main loop ──────────────────────────────────────────────────────
    def run(self):
        log.info(
            f"🚀 SuperBot STARTED | DRY_RUN={DRY_RUN} | "
            f"SCAN_PERIOD={SCAN_PERIOD}s | LIMIT_ENTRY={LIMIT_ENTRY}"
        )

        cycle = 0
        while True:
            try:
                cycle += 1
                log.info(f"{'='*50} CYCLE {cycle} {'='*50}")

                self._init_daily()

                # 1. Check pending limit orders
                self._check_pending_orders()

                # 2. Manage active positions
                self._manage_positions()

                # 3. Scan for new entries if capacity available
                balance    = self.client.get_balance() if not DRY_RUN else 1000.0
                open_count = len(self.state["open_trades"])

                if self.risk.can_open_trade(open_count, balance):
                    results = self.scanner.scan()
                    slots   = MAX_POSITIONS - open_count

                    for result in results[:slots]:
                        sym = result.symbol
                        if sym not in self.state["open_trades"]:
                            self._open_trade(sym, result.signal)
                            time.sleep(1.0)
                else:
                    log.info(f"📊 Capacity full ({open_count}/{MAX_POSITIONS}), skipping scan.")

                # 4. Status log every cycle
                self._log_status()

                if cycle % max(1, (86400 // SCAN_PERIOD)) == 0:
                    stats = self.risk.get_stats()
                    bal   = self.client.get_balance() if not DRY_RUN else 0
                    notifier.notify_daily_summary(
                        bal, stats["daily_pnl"], stats["daily_trades"], stats["total_fees"]
                    )

                log.info(f"😴 Sleeping {SCAN_PERIOD}s...")

            except KeyboardInterrupt:
                log.info("Bot stopped by user.")
                break
            except Exception as e:
                log.error(f"💥 Loop error: {e}", exc_info=True)

            time.sleep(SCAN_PERIOD)
