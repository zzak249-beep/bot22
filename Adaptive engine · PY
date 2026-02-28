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
║    MAX_OPEN_TRADES   max posiciones abiertas (def: 5)        ║
║    MAX_DRAWDOWN      circuit breaker %     (def: 15)         ║
║    DAILY_LOSS_LIMIT  perdida diaria %      (def: 8)          ║
║    TP1_PCT           take profit 1 %       (def: 1.0)        ║
║    TP2_PCT           take profit 2 %       (def: 1.8)        ║
║    TP3_PCT           take profit 3 %       (def: 3.0)        ║
║    SL_PCT            stop loss %           (def: 0.8)        ║
║  VARIABLES ADAPTIVE ENGINE:                                  ║
║    ADAPTIVE_LEARNING def: true                               ║
║    GRID_MODE         def: true                               ║
║    GRID_LEVELS       def: 6                                  ║
║    GRID_SPACING_PCT  def: 0.3                                ║
║    GRID_USDT         def: 5                                  ║
║    GRID_SYMBOL       def: BTC/USDT:USDT                      ║
║    REGIME_ADX_THRESH def: 25                                 ║
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
_lock    = threading.Lock()

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
@dataclass
class Trade:
    symbol:      str
    side:        str
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
    closed_history: list  = field(default_factory=list)   # para AdaptiveEngine

    def n(self):
        return len(self.trades)

    def wr(self):
        t = self.wins + self.losses
        return self.wins / t * 100 if t else 0.0

    def pf(self):
        return self.gross_profit / self.gross_loss if self.gross_loss else 0.0

    def cb(self):
        if self.peak_equity <= 0:
            return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD

    def daily_hit(self):
        if self.peak_equity <= 0:
            return False
        return self.daily_pnl < 0 and abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LOSS_PCT

    def reset_daily(self):
        if time.time() - self.daily_reset_ts > 86400:
            self.daily_pnl      = 0.0
            self.daily_reset_ts = time.time()
            log.info("Daily PnL reset")

    def record_close(self, pnl: float, side: str = "", reason: str = ""):
        if pnl >= 0:
            self.wins += 1
            self.gross_profit += pnl
        else:
            self.losses += 1
            self.gross_loss += abs(pnl)
        self.total_pnl   += pnl
        self.daily_pnl   += pnl
        self.peak_equity  = max(self.peak_equity, self.peak_equity + pnl)
        # guardar para adaptive engine
        self.closed_history.append({"pnl": pnl, "side": side, "reason": reason})
        if len(self.closed_history) > 500:
            self.closed_history = self.closed_history[-500:]

st = State()

# ─────────────────────────────────────────────
# EXCHANGE
# ─────────────────────────────────────────────
_ex: Optional[ccxt.Exchange] = None

def ex() -> ccxt.Exchange:
    global _ex
    if _ex is None:
        _ex = ccxt.bingx({
            "apiKey":  API_KEY,
            "secret":  API_SECRET,
            "options": {"defaultType": "swap"},
            "enableRateLimit": True,
        })
        _ex.load_markets()
        log.info("BingX connected")
    return _ex

def sym(raw: str) -> str:
    r = raw.upper().strip()
    if ":" in r: return r
    if "/" in r:
        b, q = r.split("/")
        return "%s/%s:%s" % (b, q, q)
    if r.endswith("USDT"):
        return "%s/USDT:USDT" % r[:-4]
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
        log.warning("[%s] leverage: %s", symbol, e)

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def tg(msg: str, silent: bool = False):
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    try:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
            data={
                "chat_id":              TG_CHAT_ID,
                "text":                 msg,
                "parse_mode":           "HTML",
                "disable_notification": "true" if silent else "false",
            },
            timeout=10
        )
    except Exception as e:
        log.warning("TG: %s", e)

def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def msg_start(bal: float):
    tg(
        "<b>SAIYAN AURA FUSION — ONLINE</b>\n"
        "Balance: <b>$%.2f USDT</b>\n"
        "$%.0f/trade · %dx · max %d pos\n"
        "TP1 +%.1f%% · TP2 +%.1f%% · TP3 +%.1f%%\n"
        "SL -%.1f%% · CB -%.1f%% · Diario -%.1f%%\n"
        "Adaptive Engine: ACTIVO\n"
        "Webhook listo en /webhook\n"
        "%s" % (
            bal, FIXED_USDT, LEVERAGE, MAX_OPEN_TRADES,
            TP1_PCT, TP2_PCT, TP3_PCT, SL_PCT, CB_DD, DAILY_LOSS_PCT,
            now()
        )
    )

def msg_open(t: Trade, bal: float):
    d = "LONG" if t.side == "long" else "SHORT"
    tg(
        "<b>%s</b> — <code>%s</code>\n"
        "Entrada:  <code>%.6g</code>\n"
        "TP1 50%%: <code>%.6g</code>  (+%.1f%%)\n"
        "TP2 30%%: <code>%.6g</code>  (+%.1f%%)\n"
        "TP3 20%%: <code>%.6g</code>  (+%.1f%%)\n"
        "SL:       <code>%.6g</code>   (-%.1f%%)\n"
        "%.4g contratos · $%.0fx%dx\n"
        "Balance: $%.2f · %d/%d pos\n"
        "%s" % (
            d, t.symbol,
            t.entry_price,
            t.tp1, TP1_PCT,
            t.tp2, TP2_PCT,
            t.tp3, TP3_PCT,
            t.sl, SL_PCT,
            t.contracts, FIXED_USDT, LEVERAGE,
            bal, st.n(), MAX_OPEN_TRADES,
            now()
        )
    )

def msg_tp(t: Trade, label: str, pnl_est: float, remaining: str):
    be_txt = "SL → Break-Even activado" if label == "TP1" else ""
    tg(
        "<b>%s HIT</b> — <code>%s</code>\n"
        "~$%+.2f parcial\n"
        "Restante: %s\n"
        "%s\n"
        "%s" % (label, t.symbol, pnl_est, remaining, be_txt, now())
    )

def msg_close(t: Trade, exit_p: float, pnl: float, reason: str):
    e  = "OK" if pnl >= 0 else "XX"
    pct = (exit_p - t.entry_price) / t.entry_price * 100 * (1 if t.side == "long" else -1)
    tg(
        "<b>%s CERRADO · %s</b>\n"
        "<code>%s</code> %s\n"
        "<code>%.6g</code> → <code>%.6g</code> (%+.2f%%)\n"
        "PnL: <b>$%+.2f</b>\n"
        "%dW/%dL · WR:%.1f%% · PF:%.2f\n"
        "Hoy: $%+.2f · Total: $%+.2f\n"
        "%s" % (
            e, reason,
            t.symbol, t.side.upper(),
            t.entry_price, exit_p, pct,
            pnl,
            st.wins, st.losses, st.wr(), st.pf(),
            st.daily_pnl, st.total_pnl,
            now()
        )
    )

def msg_blocked(reason: str, action: str, symbol: str):
    tg(
        "<b>BLOQUEADO</b> — %s\n"
        "Accion: %s %s\n"
        "%s" % (reason, action, symbol, now())
    )

def msg_error(txt: str):
    tg("<b>ERROR:</b> <code>%s</code>\n%s" % (txt[:300], now()))

# ─────────────────────────────────────────────
# CSV LOG
# ─────────────────────────────────────────────
def csv_log(action: str, t: Trade, exit_p: float = 0.0, pnl: float = 0.0):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts", "action", "symbol", "side", "entry", "exit", "pnl", "qty"])
            w.writerow([now(), action, t.symbol, t.side,
                        t.entry_price, exit_p or t.entry_price, round(pnl, 4), t.contracts])
    except Exception as e:
        log.warning("CSV: %s", e)

# ─────────────────────────────────────────────
# CORE LOGIC
# ─────────────────────────────────────────────
def open_trade(raw_symbol: str, side: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)

        if st.n() >= MAX_OPEN_TRADES:
            msg_blocked("Max posiciones abiertas", side, symbol)
            return {"result": "blocked_max_trades"}
        if symbol in st.trades:
            return {"result": "already_open"}
        if st.cb():
            msg_blocked("Circuit Breaker >=%.1f%%" % CB_DD, side, symbol)
            return {"result": "blocked_circuit_breaker"}
        if st.daily_hit():
            msg_blocked("Limite diario >=%.1f%%" % DAILY_LOSS_PCT, side, symbol)
            return {"result": "blocked_daily_limit"}

        try:
            e = ex()
            if symbol not in e.markets:
                e.load_markets()
            if symbol not in e.markets:
                raise ValueError("Symbol not found: %s" % symbol)

            set_lev(symbol)
            px    = price(symbol)
            bal   = balance()
            notl  = FIXED_USDT * LEVERAGE
            raw_q = notl / px
            qty   = float(e.amount_to_precision(symbol, raw_q))

            if qty * px < 5:
                raise ValueError("Notional too small: %.2f" % (qty * px))

            order_side = "buy" if side == "long" else "sell"
            log.info("[OPEN] %s %s qty=%s @~%.6g", symbol, side.upper(), qty, px)

            order   = e.create_order(symbol, "market", order_side, qty,
                                     params={"reduceOnly": False})
            entry_p = float(order.get("average") or px)

            mult = 1 if side == "long" else -1
            tp1  = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP1_PCT / 100)))
            tp2  = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP2_PCT / 100)))
            tp3  = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP3_PCT / 100)))
            sl   = float(e.price_to_precision(symbol, entry_p * (1 - mult * SL_PCT  / 100)))

            close_side = "sell" if side == "long" else "buy"
            ep         = {"reduceOnly": True}

            splits = [(0.50, tp1, "TP1"), (0.30, tp2, "TP2"), (0.20, tp3, "TP3")]
            for frac, px_tp, lbl in splits:
                q = float(e.amount_to_precision(symbol, qty * frac))
                try:
                    e.create_order(symbol, "limit", close_side, q, px_tp, ep)
                    log.info("  %s @ %.6g qty=%s", lbl, px_tp, q)
                except Exception as err:
                    log.warning("  %s: %s", lbl, err)

            try:
                e.create_order(symbol, "stop_market", close_side, qty, None,
                               {**ep, "stopPrice": sl})
                log.info("  SL @ %.6g", sl)
            except Exception as err:
                log.warning("  SL: %s", err)

            t = Trade(symbol=symbol, side=side, entry_price=entry_p,
                      contracts=qty, tp1=tp1, tp2=tp2, tp3=tp3, sl=sl,
                      entry_time=now())
            st.trades[symbol] = t
            csv_log("OPEN", t)
            msg_open(t, bal)
            return {"result": "opened", "symbol": symbol, "side": side,
                    "entry": entry_p, "qty": qty}

        except Exception as e:
            log.error("open_trade %s: %s", symbol, e)
            msg_error("open_trade %s: %s" % (symbol, e))
            return {"result": "error", "detail": str(e)}


def close_trade(raw_symbol: str, reason: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)
        if symbol not in st.trades:
            log.warning("close_trade: %s not in state", symbol)
            return {"result": "not_found"}

        t = st.trades[symbol]
        try:
            e = ex()
            try:
                e.cancel_all_orders(symbol)
            except Exception as err:
                log.warning("cancel_all: %s", err)

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

            st.record_close(pnl, side=t.side, reason=reason)
            csv_log("CLOSE", t, exit_p, pnl)
            msg_close(t, exit_p, pnl, reason)
            del st.trades[symbol]
            return {"result": "closed", "pnl": round(pnl, 4)}

        except Exception as e:
            log.error("close_trade %s: %s", symbol, e)
            msg_error("close_trade %s: %s" % (symbol, e))
            return {"result": "error", "detail": str(e)}


def handle_tp(raw_symbol: str, tp_label: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)
        if symbol not in st.trades:
            return {"result": "not_found"}

        t = st.trades[symbol]
        try:
            e   = ex()
            pos = None
            try:
                for p in e.fetch_positions([symbol]):
                    if abs(float(p.get("contracts", 0))) > 0:
                        pos = p
                        break
            except Exception:
                pass

            rem = "%.4g" % abs(float(pos.get("contracts", 0))) if pos else "~restante"

            if tp_label == "TP1" and not t.tp1_hit:
                t.tp1_hit = True
                pnl_est   = abs(t.tp1 - t.entry_price) * t.contracts * 0.50
                try:
                    be = float(e.price_to_precision(symbol, t.entry_price))
                    e.create_order(symbol, "stop_market",
                                   "sell" if t.side == "long" else "buy",
                                   t.contracts, None,
                                   {"reduceOnly": True, "stopPrice": be})
                    t.sl = be
                    t.sl_at_be = True
                    log.info("[%s] SL → BE @ %.6g", symbol, be)
                except Exception as err:
                    log.warning("BE: %s", err)
                msg_tp(t, "TP1", pnl_est, rem)

            elif tp_label == "TP2" and not t.tp2_hit:
                t.tp2_hit = True
                pnl_est   = abs(t.tp2 - t.entry_price) * t.contracts * 0.30
                msg_tp(t, "TP2", pnl_est, rem)

            elif tp_label == "TP3":
                pnl_est = abs(t.tp3 - t.entry_price) * t.contracts * 0.20
                msg_tp(t, "TP3", pnl_est, "0")

            return {"result": "%s_handled" % tp_label}

        except Exception as e:
            log.error("handle_tp %s %s: %s", symbol, tp_label, e)
            return {"result": "error", "detail": str(e)}


# ─────────────────────────────────────────────
# ADAPTIVE ENGINE — importacion segura
# ─────────────────────────────────────────────
adaptive     = None
grid_engine  = None

try:
    from adaptive_engine import (
        AdaptiveEngine, GridEngine,
        start_adaptive_worker,
        start_grid_worker,
        start_regime_worker,
        GRID_SYMBOL,
    )
    adaptive    = AdaptiveEngine(st, log)
    grid_engine = GridEngine(ex, st, log, tg)
    log.info("Adaptive Engine cargado OK")
except Exception as _ae_err:
    log.warning("adaptive_engine no disponible: %s", _ae_err)


# ─────────────────────────────────────────────
# FLASK APP
# ─────────────────────────────────────────────
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":          "alive",
        "bot":             "SAIYAN AURA FUSION",
        "open_trades":     st.n(),
        "wins":            st.wins,
        "losses":          st.losses,
        "win_rate":        round(st.wr(), 1),
        "profit_factor":   round(st.pf(), 2),
        "total_pnl":       round(st.total_pnl, 2),
        "daily_pnl":       round(st.daily_pnl, 2),
        "circuit_breaker": st.cb(),
        "daily_limit":     st.daily_hit(),
        "time":            now(),
    })

@app.route("/positions", methods=["GET"])
def positions():
    return jsonify({
        s: {
            "side":     t.side,
            "entry":    t.entry_price,
            "tp1":      t.tp1,
            "tp2":      t.tp2,
            "tp3":      t.tp3,
            "sl":       t.sl,
            "sl_at_be": t.sl_at_be,
            "tp1_hit":  t.tp1_hit,
            "tp2_hit":  t.tp2_hit,
            "since":    t.entry_time,
        }
        for s, t in st.trades.items()
    })

@app.route("/adaptive", methods=["GET"])
def adaptive_endpoint():
    if adaptive:
        return jsonify(adaptive.get_status())
    return jsonify({"error": "adaptive engine no disponible"}), 503

@app.route("/grid", methods=["GET"])
def grid_status_endpoint():
    if grid_engine:
        return jsonify(grid_engine.get_status())
    return jsonify({"error": "grid engine no disponible"}), 503

@app.route("/grid/start/<raw_sym>", methods=["POST"])
def grid_start_endpoint(raw_sym):
    if grid_engine:
        return jsonify(grid_engine.create_grid(raw_sym.upper()))
    return jsonify({"error": "grid engine no disponible"}), 503

@app.route("/grid/stop/<raw_sym>", methods=["POST"])
def grid_stop_endpoint(raw_sym):
    if grid_engine:
        return jsonify(grid_engine.cancel_grid(raw_sym.upper()))
    return jsonify({"error": "grid engine no disponible"}), 503

@app.route("/regime/<raw_sym>", methods=["GET"])
def regime_endpoint(raw_sym):
    if grid_engine:
        regime = grid_engine.regime.detect(raw_sym.upper(), ex())
        return jsonify({"symbol": raw_sym.upper(), "regime": regime})
    return jsonify({"error": "grid engine no disponible"}), 503

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
        log.info("Webhook: %s", data)

        if data.get("secret", "") != WEBHOOK_SECRET:
            log.warning("Unauthorized webhook")
            return jsonify({"error": "unauthorized"}), 401

        action = str(data.get("action", "")).strip().lower()
        symbol = str(data.get("symbol", "")).strip()

        if not action or not symbol:
            return jsonify({"error": "missing action or symbol"}), 400

        st.reset_daily()

        if   "long entry"  in action: res = open_trade(symbol, "long")
        elif "short entry" in action: res = open_trade(symbol, "short")
        elif "long exit"   in action: res = close_trade(symbol, "LONG EXIT")
        elif "short exit"  in action: res = close_trade(symbol, "SHORT EXIT")
        elif "stop loss"   in action: res = close_trade(symbol, "STOP LOSS")
        elif "tp1"         in action: res = handle_tp(symbol, "TP1")
        elif "tp2"         in action: res = handle_tp(symbol, "TP2")
        elif "tp3"         in action: res = handle_tp(symbol, "TP3")
        else:
            log.warning("Unknown action: %s", action)
            return jsonify({"error": "unknown action: %s" % action}), 400

        return jsonify(res), 200

    except Exception as e:
        log.exception("Webhook crash: %s", e)
        msg_error("Webhook: %s" % e)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# TELEGRAM COMMANDS WORKER
# ─────────────────────────────────────────────
def _tg_commands_worker():
    offset = 0
    while True:
        try:
            url = "https://api.telegram.org/bot%s/getUpdates" % TG_TOKEN
            r   = requests.get(url, params={"offset": offset, "timeout": 30}, timeout=35)
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                text   = upd.get("message", {}).get("text", "").strip()
                chat   = str(upd.get("message", {}).get("chat", {}).get("id", ""))
                if chat != TG_CHAT_ID:
                    continue

                if text == "/status":
                    tg(
                        "<b>STATUS</b>\n"
                        "Pos: %d/%d\n"
                        "%dW/%dL · WR:%.1f%% · PF:%.2f\n"
                        "Hoy: $%+.2f · Total: $%+.2f\n"
                        "CB: %s · Daily: %s\n"
                        "%s" % (
                            st.n(), MAX_OPEN_TRADES,
                            st.wins, st.losses, st.wr(), st.pf(),
                            st.daily_pnl, st.total_pnl,
                            st.cb(), st.daily_hit(),
                            now()
                        )
                    )

                elif text == "/positions":
                    if not st.trades:
                        tg("Sin posiciones abiertas.")
                    else:
                        for s, t in st.trades.items():
                            tg(
                                "<code>%s</code> %s\n"
                                "Entrada: %.6g\n"
                                "TP1: %.6g · TP2: %.6g · TP3: %.6g\n"
                                "SL: %.6g %s\n"
                                "Desde: %s" % (
                                    s, t.side.upper(),
                                    t.entry_price,
                                    t.tp1, t.tp2, t.tp3,
                                    t.sl, "(BE)" if t.sl_at_be else "",
                                    t.entry_time
                                )
                            )

                elif text == "/adaptive":
                    if adaptive:
                        adaptive.msg_adaptive_report(tg)
                    else:
                        tg("Adaptive engine no disponible.")

                elif text == "/grid":
                    if grid_engine:
                        grid_engine.msg_grid_status(tg)
                    else:
                        tg("Grid engine no disponible.")

                elif text.startswith("/grid_start"):
                    if grid_engine:
                        parts = text.split()
                        s     = parts[1].upper() if len(parts) > 1 else GRID_SYMBOL
                        tg("Creando grid para %s..." % s)
                        res = grid_engine.create_grid(s)
                        tg("Grid: %s" % str(res))
                    else:
                        tg("Grid engine no disponible.")

                elif text.startswith("/grid_stop"):
                    if grid_engine:
                        parts = text.split()
                        s     = parts[1].upper() if len(parts) > 1 else GRID_SYMBOL
                        res   = grid_engine.cancel_grid(s)
                        tg("Grid cancelado: PnL=%+.4f" % res.get("pnl", 0))
                    else:
                        tg("Grid engine no disponible.")

                elif text == "/regime":
                    if grid_engine:
                        for pair in ["BTC/USDT:USDT", "ETH/USDT:USDT"]:
                            r2 = grid_engine.regime.detect(pair, ex())
                            tg("%s: %s" % (pair, r2.upper()))
                    else:
                        tg("Grid engine no disponible.")

                elif text == "/help":
                    tg(
                        "<b>COMANDOS</b>\n"
                        "/status — estado del bot\n"
                        "/positions — posiciones abiertas\n"
                        "/adaptive — reporte auto-aprendizaje\n"
                        "/grid — grids activos\n"
                        "/grid_start SYMBOL — iniciar grid\n"
                        "/grid_stop SYMBOL — detener grid\n"
                        "/regime — regimen de mercado\n"
                        "/help — esta ayuda"
                    )

        except Exception as e:
            log.warning("tg_commands: %s", e)
        time.sleep(1)


# ─────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────
def startup():
    log.info("=" * 55)
    log.info("  SAIYAN AURA FUSION BOT — Starting...")
    log.info("=" * 55)
    log.info("  USDT/trade: $%s | Leverage: %sx", FIXED_USDT, LEVERAGE)
    log.info("  TP1:%.1f%% TP2:%.1f%% TP3:%.1f%% SL:%.1f%%",
             TP1_PCT, TP2_PCT, TP3_PCT, SL_PCT)
    log.info("  Max trades: %s | CB:%.1f%% | Daily:%.1f%%",
             MAX_OPEN_TRADES, CB_DD, DAILY_LOSS_PCT)
    log.info("=" * 55)

    if not (API_KEY and API_SECRET):
        log.warning("No API keys — orders will FAIL")

    for attempt in range(10):
        try:
            bal = balance()
            st.peak_equity    = bal
            st.daily_reset_ts = time.time()
            log.info("Balance: $%.2f USDT", bal)
            msg_start(bal)
            break
        except Exception as e:
            wait = min(2 ** attempt, 60)
            log.warning("Startup %d/10: %s — retry %ds", attempt + 1, e, wait)
            time.sleep(wait)

    # Iniciar workers
    if TG_TOKEN and TG_CHAT_ID:
        threading.Thread(target=_tg_commands_worker, daemon=True,
                         name="tg_cmd").start()
        log.info("Telegram commands worker iniciado")

    if adaptive:
        start_adaptive_worker(adaptive, tg, log)

    if grid_engine:
        start_grid_worker(grid_engine, log)
        try:
            gs = os.environ.get("GRID_SYMBOL", "BTC/USDT:USDT")
            start_regime_worker(grid_engine, [gs], log)
        except Exception as e:
            log.warning("regime_worker: %s", e)


startup()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
