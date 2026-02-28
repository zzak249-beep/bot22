"""
╔══════════════════════════════════════════════════════════════╗
║  SAIYAN AURA FUSION BOT                                      ║
║  TradingView Webhook → BingX Perpetual Futures + Telegram    ║
║  Railway 24/7                                                ║
╠══════════════════════════════════════════════════════════════╣
║  VARIABLES OBLIGATORIAS:                                     ║
║    BINGX_API_KEY                                             ║
║    BINGX_API_SECRET                                          ║
║    TELEGRAM_BOT_TOKEN                                        ║
║    TELEGRAM_CHAT_ID                                          ║
║    WEBHOOK_SECRET    (clave que pones en TradingView)        ║
║                                                              ║
║  VARIABLES OPCIONALES:                                       ║
║    FIXED_USDT        USDT por trade        (def: 20)         ║
║    LEVERAGE          apalancamiento        (def: 5)          ║
║    MAX_OPEN_TRADES   máx posiciones abiertas (def: 5)        ║
║    MAX_DRAWDOWN      circuit breaker %     (def: 15)         ║
║    DAILY_LOSS_LIMIT  pérdida diaria %      (def: 8)          ║
║    TP1_PCT           take profit 1 %       (def: 1.0)        ║
║    TP2_PCT           take profit 2 %       (def: 1.8)        ║
║    TP3_PCT           take profit 3 %       (def: 3.0)        ║
║    SL_PCT            stop loss %           (def: 0.8)        ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, time, logging, csv, threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict

import requests
import ccxt
from flask import Flask, request, jsonify

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("saiyan")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
API_KEY          = os.environ.get("BINGX_API_KEY",       "")
API_SECRET       = os.environ.get("BINGX_API_SECRET",    "")
TG_TOKEN         = os.environ.get("TELEGRAM_BOT_TOKEN",  "")
TG_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID",    "")
WEBHOOK_SECRET   = os.environ.get("WEBHOOK_SECRET",      "saiyan2024")

FIXED_USDT       = float(os.environ.get("FIXED_USDT",        "20.0"))
LEVERAGE         = int  (os.environ.get("LEVERAGE",           "5"))
MAX_OPEN_TRADES  = int  (os.environ.get("MAX_OPEN_TRADES",    "5"))
CB_DD            = float(os.environ.get("MAX_DRAWDOWN",       "15.0"))
DAILY_LOSS_PCT   = float(os.environ.get("DAILY_LOSS_LIMIT",   "8.0"))
TP1_PCT          = float(os.environ.get("TP1_PCT",            "1.0"))
TP2_PCT          = float(os.environ.get("TP2_PCT",            "1.8"))
TP3_PCT          = float(os.environ.get("TP3_PCT",            "3.0"))
SL_PCT           = float(os.environ.get("SL_PCT",             "0.8"))
PORT             = int  (os.environ.get("PORT",               "8080"))

CSV_PATH = "/tmp/saiyan_trades.csv"
_lock    = threading.Lock()   # thread-safe state access

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
@dataclass
class Trade:
    symbol:      str
    side:        str          # "long" | "short"
    entry_price: float
    contracts:   float
    tp1:         float
    tp2:         float
    tp3:         float
    sl:          float
    entry_time:  str
    tp1_hit:     bool  = False
    tp2_hit:     bool  = False
    sl_at_be:    bool  = False

@dataclass
class State:
    trades:         Dict[str, Trade] = field(default_factory=dict)
    wins:           int   = 0
    losses:         int   = 0
    gross_profit:   float = 0.0
    gross_loss:     float = 0.0
    peak_equity:    float = 0.0
    total_pnl:      float = 0.0
    daily_pnl:      float = 0.0
    daily_reset_ts: float = field(default_factory=time.time)

    # ── helpers
    def n(self):          return len(self.trades)
    def wr(self):
        t = self.wins + self.losses
        return self.wins / t * 100 if t else 0.0
    def pf(self):
        return self.gross_profit / self.gross_loss if self.gross_loss else 0.0

    def cb(self):
        if self.peak_equity <= 0: return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD

    def daily_hit(self):
        if self.peak_equity <= 0: return False
        return self.daily_pnl < 0 and abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LOSS_PCT

    def reset_daily(self):
        if time.time() - self.daily_reset_ts > 86400:
            self.daily_pnl      = 0.0
            self.daily_reset_ts = time.time()
            log.info("Daily PnL reset")

    def record_close(self, pnl: float):
        if pnl >= 0:
            self.wins += 1; self.gross_profit += pnl
        else:
            self.losses += 1; self.gross_loss += abs(pnl)
        self.total_pnl   += pnl
        self.daily_pnl   += pnl
        self.peak_equity  = max(self.peak_equity, self.peak_equity + pnl)

st = State()

# ─────────────────────────────────────────────
# EXCHANGE
# ─────────────────────────────────────────────
_ex: Optional[ccxt.Exchange] = None

def ex() -> ccxt.Exchange:
    global _ex
    if _ex is None:
        _ex = ccxt.bingx({
            "apiKey": API_KEY,
            "secret": API_SECRET,
            "options": {"defaultType": "swap"},
            "enableRateLimit": True,
        })
        _ex.load_markets()
        log.info("BingX connected ✓")
    return _ex

def sym(raw: str) -> str:
    """Normalize 'BTCUSDT' → 'BTC/USDT:USDT'"""
    r = raw.upper().strip()
    if ":" in r: return r
    if "/" in r:
        b, q = r.split("/"); return f"{b}/{q}:{q}"
    if r.endswith("USDT"): return f"{r[:-4]}/USDT:USDT"
    return r

def price(symbol: str) -> float:
    return float(ex().fetch_ticker(symbol)["last"])

def balance() -> float:
    return float(ex().fetch_balance()["USDT"]["free"])

def position(symbol: str) -> Optional[dict]:
    try:
        for p in ex().fetch_positions([symbol]):
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                return p
    except Exception:
        pass
    return None

def set_lev(symbol: str):
    try:
        ex().set_leverage(LEVERAGE, symbol)
    except Exception as e:
        log.warning(f"[{symbol}] leverage: {e}")

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def tg(msg: str):
    if not (TG_TOKEN and TG_CHAT_ID): return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.warning(f"TG: {e}")

def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

# ── Messages
def msg_start(bal: float):
    tg(
        f"<b>🚀 SAIYAN AURA FUSION — ONLINE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${bal:.2f} USDT</b>\n"
        f"⚙️ ${FIXED_USDT:.0f}/trade · {LEVERAGE}x · max {MAX_OPEN_TRADES} pos\n"
        f"🎯 TP1 +{TP1_PCT}% · TP2 +{TP2_PCT}% · TP3 +{TP3_PCT}%\n"
        f"🛑 SL -{SL_PCT}% · CB -{CB_DD}% · Diario -{DAILY_LOSS_PCT}%\n"
        f"🔗 Webhook listo en /webhook\n"
        f"⏰ {now()}"
    )

def msg_open(t: Trade, bal: float):
    e = "🟢" if t.side == "long" else "🔴"
    d = "▲ LONG" if t.side == "long" else "▼ SHORT"
    tg(
        f"{e} <b>{d}</b> — <code>{t.symbol}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Entrada:  <code>{t.entry_price:.6g}</code>\n"
        f"🟡 TP1 50%: <code>{t.tp1:.6g}</code>  (+{TP1_PCT}%)\n"
        f"🟠 TP2 30%: <code>{t.tp2:.6g}</code>  (+{TP2_PCT}%)\n"
        f"🟢 TP3 20%: <code>{t.tp3:.6g}</code>  (+{TP3_PCT}%)\n"
        f"🛑 SL:      <code>{t.sl:.6g}</code>   (-{SL_PCT}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 {t.contracts} contratos · ${FIXED_USDT}×{LEVERAGE}x\n"
        f"💰 Balance: ${bal:.2f} · {st.n()}/{MAX_OPEN_TRADES} pos\n"
        f"⏰ {now()}"
    )

def msg_tp(t: Trade, label: str, pnl_est: float, remaining: str):
    tg(
        f"🏆 <b>{label} HIT</b> — <code>{t.symbol}</code>\n"
        f"💵 ~${pnl_est:+.2f} parcial\n"
        f"📦 Restante: {remaining}\n"
        f"{'🛡 SL → Break-Even activado' if label=='TP1' else ''}\n"
        f"⏰ {now()}"
    )

def msg_close(t: Trade, exit_p: float, pnl: float, reason: str):
    e = "✅" if pnl >= 0 else "❌"
    pct = (exit_p - t.entry_price) / t.entry_price * 100 * (1 if t.side == "long" else -1)
    tg(
        f"{e} <b>CERRADO · {reason}</b>\n"
        f"<code>{t.symbol}</code> {t.side.upper()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <code>{t.entry_price:.6g}</code> → <code>{exit_p:.6g}</code> ({pct:+.2f}%)\n"
        f"{'💰' if pnl>=0 else '💸'} PnL: <b>${pnl:+.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {st.wins}W/{st.losses}L · WR:{st.wr():.1f}% · PF:{st.pf():.2f}\n"
        f"💹 Hoy: ${st.daily_pnl:+.2f} · Total: ${st.total_pnl:+.2f}\n"
        f"⏰ {now()}"
    )

def msg_blocked(reason: str, action: str, symbol: str):
    tg(
        f"⛔ <b>BLOQUEADO</b> — {reason}\n"
        f"Acción: {action} {symbol}\n"
        f"⏰ {now()}"
    )

def msg_error(txt: str):
    tg(f"🔥 <b>ERROR:</b> <code>{txt[:300]}</code>\n⏰ {now()}")

# ─────────────────────────────────────────────
# CSV LOG
# ─────────────────────────────────────────────
def csv_log(action: str, t: Trade, exit_p: float = 0.0, pnl: float = 0.0):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts","action","symbol","side","entry","exit","pnl","qty"])
            w.writerow([now(), action, t.symbol, t.side,
                        t.entry_price, exit_p or t.entry_price, round(pnl,4), t.contracts])
    except Exception as e:
        log.warning(f"CSV: {e}")

# ─────────────────────────────────────────────
# CORE LOGIC
# ─────────────────────────────────────────────
def open_trade(raw_symbol: str, side: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)

        # guards
        if st.n() >= MAX_OPEN_TRADES:
            msg_blocked("Máx posiciones abiertas", side, symbol)
            return {"result": "blocked_max_trades"}
        if symbol in st.trades:
            return {"result": "already_open"}
        if st.cb():
            msg_blocked(f"Circuit Breaker ≥{CB_DD}%", side, symbol)
            return {"result": "blocked_circuit_breaker"}
        if st.daily_hit():
            msg_blocked(f"Límite diario ≥{DAILY_LOSS_PCT}%", side, symbol)
            return {"result": "blocked_daily_limit"}

        try:
            e = ex()
            if symbol not in e.markets:
                e.load_markets()
            if symbol not in e.markets:
                raise ValueError(f"Symbol not found: {symbol}")

            set_lev(symbol)
            px     = price(symbol)
            bal    = balance()
            notl   = FIXED_USDT * LEVERAGE
            raw_q  = notl / px
            qty    = float(e.amount_to_precision(symbol, raw_q))

            if qty * px < 5:
                raise ValueError(f"Notional too small: {qty*px:.2f}")

            order_side = "buy" if side == "long" else "sell"
            log.info(f"[OPEN] {symbol} {side.upper()} qty={qty} @~{px:.6g}")

            order      = e.create_order(symbol, "market", order_side, qty,
                                        params={"reduceOnly": False})
            entry_p    = float(order.get("average") or px)

            # levels
            mult = 1 if side == "long" else -1
            tp1  = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP1_PCT/100)))
            tp2  = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP2_PCT/100)))
            tp3  = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP3_PCT/100)))
            sl   = float(e.price_to_precision(symbol, entry_p * (1 - mult * SL_PCT/100)))

            close_side = "sell" if side == "long" else "buy"
            ep         = {"reduceOnly": True}

            # TP limit orders (50/30/20)
            splits = [(0.50, tp1, "TP1"), (0.30, tp2, "TP2"), (0.20, tp3, "TP3")]
            for frac, px_tp, lbl in splits:
                q = float(e.amount_to_precision(symbol, qty * frac))
                try:
                    e.create_order(symbol, "limit", close_side, q, px_tp, ep)
                    log.info(f"  {lbl} @ {px_tp:.6g} qty={q}")
                except Exception as err:
                    log.warning(f"  {lbl}: {err}")

            # SL stop-market
            try:
                e.create_order(symbol, "stop_market", close_side, qty, None,
                               {**ep, "stopPrice": sl})
                log.info(f"  SL @ {sl:.6g}")
            except Exception as err:
                log.warning(f"  SL: {err}")

            t = Trade(symbol=symbol, side=side, entry_price=entry_p,
                      contracts=qty, tp1=tp1, tp2=tp2, tp3=tp3, sl=sl,
                      entry_time=now())
            st.trades[symbol] = t
            csv_log("OPEN", t)
            msg_open(t, bal)
            return {"result": "opened", "symbol": symbol, "side": side,
                    "entry": entry_p, "qty": qty}

        except Exception as e:
            log.error(f"open_trade {symbol}: {e}")
            msg_error(f"open_trade {symbol}: {e}")
            return {"result": "error", "detail": str(e)}


def close_trade(raw_symbol: str, reason: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)
        if symbol not in st.trades:
            log.warning(f"close_trade: {symbol} not in state")
            return {"result": "not_found"}

        t = st.trades[symbol]
        try:
            e = ex()
            try: e.cancel_all_orders(symbol)
            except Exception as err: log.warning(f"cancel_all: {err}")

            pos    = position(symbol)
            exit_p = price(symbol)
            pnl    = 0.0

            if pos:
                qty        = abs(float(pos.get("contracts", 0)))
                close_side = "sell" if t.side == "long" else "buy"
                ord_       = e.create_order(symbol, "market", close_side, qty,
                                            params={"reduceOnly": True})
                exit_p     = float(ord_.get("average") or exit_p)
                pnl        = ((exit_p - t.entry_price) if t.side == "long"
                              else (t.entry_price - exit_p)) * qty

            st.record_close(pnl)
            csv_log("CLOSE", t, exit_p, pnl)
            msg_close(t, exit_p, pnl, reason)
            del st.trades[symbol]
            return {"result": "closed", "pnl": round(pnl, 4)}

        except Exception as e:
            log.error(f"close_trade {symbol}: {e}")
            msg_error(f"close_trade {symbol}: {e}")
            return {"result": "error", "detail": str(e)}


def handle_tp(raw_symbol: str, tp_label: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)
        if symbol not in st.trades:
            return {"result": "not_found"}

        t = st.trades[symbol]
        try:
            e      = ex()
            px     = price(symbol)
            pos    = position(e, symbol) if False else None   # just for display
            try:    pos = None; [p for p in e.fetch_positions([symbol]) if abs(float(p.get("contracts",0)))>0 and (pos := p)]
            except: pass
            rem = str(round(float(pos.get("contracts",0)),4)) if pos else "~50% restante"

            if tp_label == "TP1" and not t.tp1_hit:
                t.tp1_hit  = True
                pnl_est    = abs(t.tp1 - t.entry_price) * t.contracts * 0.50
                # move SL to break-even
                try:
                    be = float(e.price_to_precision(symbol, t.entry_price))
                    e.create_order(symbol, "stop_market",
                                   "sell" if t.side=="long" else "buy",
                                   t.contracts, None,
                                   {"reduceOnly": True, "stopPrice": be})
                    t.sl = be; t.sl_at_be = True
                    log.info(f"[{symbol}] SL → BE @ {be:.6g}")
                except Exception as err:
                    log.warning(f"BE: {err}")
                msg_tp(t, "TP1", pnl_est, rem)

            elif tp_label == "TP2" and not t.tp2_hit:
                t.tp2_hit = True
                pnl_est   = abs(t.tp2 - t.entry_price) * t.contracts * 0.30
                msg_tp(t, "TP2", pnl_est, rem)

            elif tp_label == "TP3":
                pnl_est = abs(t.tp3 - t.entry_price) * t.contracts * 0.20
                msg_tp(t, "TP3", pnl_est, "0")

            return {"result": f"{tp_label}_handled"}

        except Exception as e:
            log.error(f"handle_tp {symbol} {tp_label}: {e}")
            return {"result": "error", "detail": str(e)}


# ─────────────────────────────────────────────
# FLASK APP
# ─────────────────────────────────────────────
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "alive",
        "bot": "SAIYAN AURA FUSION",
        "open_trades": st.n(),
        "wins": st.wins, "losses": st.losses,
        "win_rate": round(st.wr(), 1),
        "profit_factor": round(st.pf(), 2),
        "total_pnl": round(st.total_pnl, 2),
        "daily_pnl": round(st.daily_pnl, 2),
        "circuit_breaker": st.cb(),
        "daily_limit": st.daily_hit(),
        "time": now()
    })

@app.route("/positions", methods=["GET"])
def positions():
    return jsonify({
        sym_: {
            "side": t.side, "entry": t.entry_price,
            "tp1": t.tp1, "tp2": t.tp2, "tp3": t.tp3,
            "sl": t.sl, "sl_at_be": t.sl_at_be,
            "tp1_hit": t.tp1_hit, "tp2_hit": t.tp2_hit,
            "since": t.entry_time
        }
        for sym_, t in st.trades.items()
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Payload JSON desde TradingView:
    {
      "secret":  "TU_CLAVE",
      "action":  "Long Entry" | "Short Entry" | "Long Exit" | "Short Exit"
                 | "TP1 Hit" | "TP2 Hit" | "TP3 Hit" | "Stop Loss Hit",
      "symbol":  "{{ticker}}"
    }
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        log.info(f"Webhook: {data}")

        if data.get("secret","") != WEBHOOK_SECRET:
            log.warning("Unauthorized webhook")
            return jsonify({"error": "unauthorized"}), 401

        action = str(data.get("action","")).strip().lower()
        symbol = str(data.get("symbol","")).strip()

        if not action or not symbol:
            return jsonify({"error": "missing action or symbol"}), 400

        st.reset_daily()

        # ── Route
        if   "long entry"  in action: res = open_trade(symbol, "long")
        elif "short entry" in action: res = open_trade(symbol, "short")
        elif "long exit"   in action: res = close_trade(symbol, "LONG EXIT")
        elif "short exit"  in action: res = close_trade(symbol, "SHORT EXIT")
        elif "stop loss"   in action: res = close_trade(symbol, "STOP LOSS")
        elif "tp1"         in action: res = handle_tp(symbol, "TP1")
        elif "tp2"         in action: res = handle_tp(symbol, "TP2")
        elif "tp3"         in action: res = handle_tp(symbol, "TP3")
        else:
            log.warning(f"Unknown action: {action}")
            return jsonify({"error": f"unknown action: {action}"}), 400

        return jsonify(res), 200

    except Exception as e:
        log.exception(f"Webhook crash: {e}")
        msg_error(f"Webhook: {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────
def startup():
    log.info("━" * 55)
    log.info("  SAIYAN AURA FUSION BOT — Starting...")
    log.info("━" * 55)
    log.info(f"  USDT/trade: ${FIXED_USDT} | Leverage: {LEVERAGE}x")
    log.info(f"  TP1:{TP1_PCT}% TP2:{TP2_PCT}% TP3:{TP3_PCT}% SL:{SL_PCT}%")
    log.info(f"  Max trades: {MAX_OPEN_TRADES} | CB:{CB_DD}% | Daily:{DAILY_LOSS_PCT}%")
    log.info("━" * 55)

    if not (API_KEY and API_SECRET):
        log.warning("⚠ No API keys — orders will FAIL (DRY MODE)")

    for attempt in range(10):
        try:
            bal = balance()
            st.peak_equity    = bal
            st.daily_reset_ts = time.time()
            log.info(f"Balance: ${bal:.2f} USDT")
            msg_start(bal)
            break
        except Exception as e:
            wait = min(2 ** attempt, 60)
            log.warning(f"Startup {attempt+1}/10: {e} — retry {wait}s")
            time.sleep(wait)


# ── Run startup in background thread so gunicorn workers don't block
threading.Thread(target=startup, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
