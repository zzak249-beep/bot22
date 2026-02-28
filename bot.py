"""
╔══════════════════════════════════════════════════════════════════╗
║  SAIYAN AURA FUSION BOT  v3.0  ── ULTIMATE EDITION              ║
║  TradingView Webhook → BingX Perpetual Futures + Telegram        ║
║  Railway 24/7                                                    ║
╠══════════════════════════════════════════════════════════════════╣
║  NUEVAS FEATURES v3.0:                                           ║
║  ✦ Comandos Telegram (/status /positions /close /pause /resume)  ║
║  ✦ Modo PAUSA sin reiniciar el bot                               ║
║  ✦ Resumen diario automático a medianoche UTC                    ║
║  ✦ Anti-spike: filtra precios anómalos antes de operar           ║
║  ✦ Cooldown configurable entre trades del mismo símbolo          ║
║  ✦ Dashboard HTML en /dashboard                                  ║
║  ✦ Métricas: max_drawdown_real, avg_win, avg_loss, best/worst    ║
║  ✦ Persistencia de estado en /tmp (sobrevive reinicios)          ║
║  ✦ Telegram bot polling en hilo separado                         ║
╠══════════════════════════════════════════════════════════════════╣
║  VARIABLES OBLIGATORIAS:                                         ║
║    BINGX_API_KEY  · BINGX_API_SECRET                             ║
║    TELEGRAM_BOT_TOKEN  · TELEGRAM_CHAT_ID                        ║
║    WEBHOOK_SECRET                                                ║
║                                                                  ║
║  VARIABLES OPCIONALES:                                           ║
║    FIXED_USDT          def: 20                                   ║
║    LEVERAGE            def: 5                                    ║
║    MAX_OPEN_TRADES     def: 5                                    ║
║    MAX_DRAWDOWN        def: 15   (circuit breaker %)             ║
║    DAILY_LOSS_LIMIT    def: 8                                    ║
║    TP1_PCT             def: 1.0                                  ║
║    TP2_PCT             def: 1.8                                  ║
║    TP3_PCT             def: 3.0                                  ║
║    SL_PCT              def: 0.8                                  ║
║    TRAILING_PCT        def: 0.5  (trailing step %)               ║
║    TRAILING_ACTIVATE   def: 1.0  (activa trailing tras %)        ║
║    HEARTBEAT_MIN       def: 60   (resumen periódico min)         ║
║    COOLDOWN_MIN        def: 5    (min entre trades mismo símbolo)║
║    ANTI_SPIKE_PCT      def: 3.0  (rechaza precio >X% del medio)  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, time, logging, csv, threading, json, hmac, hashlib
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List
from collections import deque

import requests
import ccxt
from flask import Flask, request, jsonify, Response

# ─────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("saiyan")

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────
API_KEY           = os.environ.get("BINGX_API_KEY",       "")
API_SECRET        = os.environ.get("BINGX_API_SECRET",    "")
TG_TOKEN          = os.environ.get("TELEGRAM_BOT_TOKEN",  "")
TG_CHAT_ID        = os.environ.get("TELEGRAM_CHAT_ID",    "")
WEBHOOK_SECRET    = os.environ.get("WEBHOOK_SECRET",      "saiyan2024")

FIXED_USDT        = float(os.environ.get("FIXED_USDT",        "20.0"))
LEVERAGE          = int  (os.environ.get("LEVERAGE",           "5"))
MAX_OPEN_TRADES   = int  (os.environ.get("MAX_OPEN_TRADES",    "5"))
CB_DD             = float(os.environ.get("MAX_DRAWDOWN",       "15.0"))
DAILY_LOSS_PCT    = float(os.environ.get("DAILY_LOSS_LIMIT",   "8.0"))
TP1_PCT           = float(os.environ.get("TP1_PCT",            "1.0"))
TP2_PCT           = float(os.environ.get("TP2_PCT",            "1.8"))
TP3_PCT           = float(os.environ.get("TP3_PCT",            "3.0"))
SL_PCT            = float(os.environ.get("SL_PCT",             "0.8"))
TRAILING_PCT      = float(os.environ.get("TRAILING_PCT",       "0.5"))
TRAILING_ACTIVATE = float(os.environ.get("TRAILING_ACTIVATE",  "1.0"))
HEARTBEAT_MIN     = int  (os.environ.get("HEARTBEAT_MIN",      "60"))
COOLDOWN_MIN      = int  (os.environ.get("COOLDOWN_MIN",       "5"))
ANTI_SPIKE_PCT    = float(os.environ.get("ANTI_SPIKE_PCT",     "3.0"))
PORT              = int  (os.environ.get("PORT",               "8080"))

STATE_PATH = "/tmp/saiyan_state.json"
CSV_PATH   = "/tmp/saiyan_trades.csv"
_lock      = threading.Lock()

# ─────────────────────────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    symbol:        str
    side:          str
    entry_price:   float
    contracts:     float
    tp1:           float
    tp2:           float
    tp3:           float
    sl:            float
    entry_time:    str
    tp1_hit:       bool  = False
    tp2_hit:       bool  = False
    sl_at_be:      bool  = False
    trailing_on:   bool  = False
    trailing_high: float = 0.0

@dataclass
class ClosedTrade:
    symbol:    str
    side:      str
    entry:     float
    exit:      float
    pnl:       float
    reason:    str
    duration:  str
    ts:        str

@dataclass
class State:
    trades:           Dict[str, Trade]       = field(default_factory=dict)
    closed_history:   List[dict]             = field(default_factory=list)  # últimas 50
    cooldowns:        Dict[str, float]       = field(default_factory=dict)  # symbol → ts_last_close
    wins:             int   = 0
    losses:           int   = 0
    gross_profit:     float = 0.0
    gross_loss:       float = 0.0
    peak_equity:      float = 0.0
    total_pnl:        float = 0.0
    daily_pnl:        float = 0.0
    daily_reset_ts:   float = field(default_factory=time.time)
    start_time:       float = field(default_factory=time.time)
    total_trades:     int   = 0
    paused:           bool  = False
    max_dd_real:      float = 0.0   # drawdown máximo histórico real
    best_trade:       float = 0.0
    worst_trade:      float = 0.0
    tg_update_offset: int   = 0     # para polling de comandos Telegram

    def n(self):  return len(self.trades)

    def wr(self):
        t = self.wins + self.losses
        return self.wins / t * 100 if t else 0.0

    def pf(self):
        return self.gross_profit / self.gross_loss if self.gross_loss else 0.0

    def avg_win(self):
        return self.gross_profit / self.wins if self.wins else 0.0

    def avg_loss(self):
        return self.gross_loss / self.losses if self.losses else 0.0

    def expectancy(self):
        """Expectativa matemática por trade en USDT."""
        wr = self.wr() / 100
        return wr * self.avg_win() - (1 - wr) * self.avg_loss()

    def cb(self):
        if self.peak_equity <= 0: return False
        dd = abs(self.total_pnl) / self.peak_equity * 100
        return self.total_pnl < 0 and dd >= CB_DD

    def daily_hit(self):
        if self.peak_equity <= 0: return False
        return self.daily_pnl < 0 and abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LOSS_PCT

    def in_cooldown(self, symbol: str) -> bool:
        last = self.cooldowns.get(symbol, 0)
        return (time.time() - last) < COOLDOWN_MIN * 60

    def reset_daily(self):
        now_ts = time.time()
        if now_ts - self.daily_reset_ts > 86400:
            self.daily_pnl      = 0.0
            self.daily_reset_ts = now_ts
            log.info("Daily PnL reset")

    def record_close(self, pnl: float, symbol: str):
        self.total_trades += 1
        if pnl >= 0:
            self.wins        += 1
            self.gross_profit += pnl
            self.best_trade   = max(self.best_trade, pnl)
        else:
            self.losses      += 1
            self.gross_loss  += abs(pnl)
            self.worst_trade  = min(self.worst_trade, pnl)
        self.total_pnl   += pnl
        self.daily_pnl   += pnl
        self.cooldowns[symbol] = time.time()
        # Actualizar drawdown máximo real
        if self.peak_equity > 0:
            dd = abs(self.total_pnl) / self.peak_equity * 100
            if self.total_pnl < 0 and dd > self.max_dd_real:
                self.max_dd_real = dd
        # Actualizar equity pico
        cur_equity = self.peak_equity + self.total_pnl
        if cur_equity > self.peak_equity:
            self.peak_equity = cur_equity

    def uptime(self) -> str:
        secs = int(time.time() - self.start_time)
        h, m = divmod(secs // 60, 60)
        d, h = divmod(h, 24)
        if d > 0: return f"{d}d {h}h {m}m"
        return f"{h}h {m}m"

    def to_persist(self) -> dict:
        """Serializa solo lo necesario para persistir entre reinicios."""
        return {
            "wins": self.wins, "losses": self.losses,
            "gross_profit": self.gross_profit, "gross_loss": self.gross_loss,
            "total_pnl": self.total_pnl, "peak_equity": self.peak_equity,
            "total_trades": self.total_trades, "best_trade": self.best_trade,
            "worst_trade": self.worst_trade, "max_dd_real": self.max_dd_real,
            "closed_history": self.closed_history[-50:],
            "cooldowns": self.cooldowns,
        }

    def load_persist(self, d: dict):
        self.wins           = d.get("wins", 0)
        self.losses         = d.get("losses", 0)
        self.gross_profit   = d.get("gross_profit", 0.0)
        self.gross_loss     = d.get("gross_loss", 0.0)
        self.total_pnl      = d.get("total_pnl", 0.0)
        self.peak_equity    = d.get("peak_equity", 0.0)
        self.total_trades   = d.get("total_trades", 0)
        self.best_trade     = d.get("best_trade", 0.0)
        self.worst_trade    = d.get("worst_trade", 0.0)
        self.max_dd_real    = d.get("max_dd_real", 0.0)
        self.closed_history = d.get("closed_history", [])
        self.cooldowns      = d.get("cooldowns", {})

st = State()

# ─────────────────────────────────────────────────────────────────
# PERSISTENCIA
# ─────────────────────────────────────────────────────────────────
def save_state():
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(st.to_persist(), f)
    except Exception as e:
        log.warning(f"save_state: {e}")

def load_state():
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f:
                st.load_persist(json.load(f))
            log.info(f"Estado restaurado: {st.total_trades} trades históricos")
    except Exception as e:
        log.warning(f"load_state: {e}")

# ─────────────────────────────────────────────────────────────────
# EXCHANGE
# ─────────────────────────────────────────────────────────────────
_ex: Optional[ccxt.Exchange] = None
_ex_lock = threading.Lock()

def ex() -> ccxt.Exchange:
    global _ex
    with _ex_lock:
        if _ex is None:
            _ex = ccxt.bingx({
                "apiKey":  API_KEY,
                "secret":  API_SECRET,
                "options": {"defaultType": "swap"},
                "enableRateLimit": True,
            })
            _ex.load_markets()
            log.info("BingX connected ✓")
        return _ex

def ex_call(fn, *args, retries=3, **kwargs):
    """Wrapper con reintentos exponenciales."""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except ccxt.NetworkError as e:
            wait = 2 ** attempt
            log.warning(f"NetworkError ({attempt+1}/{retries}): {e} — retry {wait}s")
            time.sleep(wait)
        except ccxt.RateLimitExceeded:
            log.warning("RateLimit — waiting 10s")
            time.sleep(10)
        except ccxt.AuthenticationError as e:
            log.error(f"AuthError: {e}")
            raise
        except Exception as e:
            if attempt == retries - 1:
                raise
            log.warning(f"Error ({attempt+1}/{retries}): {e}")
            time.sleep(2)
    raise RuntimeError(f"Failed after {retries} attempts")

def sym(raw: str) -> str:
    r = raw.upper().strip()
    if ":" in r: return r
    if "/" in r:
        b, q = r.split("/"); return f"{b}/{q}:{q}"
    if r.endswith("USDT"): return f"{r[:-4]}/USDT:USDT"
    return r

def price(symbol: str) -> float:
    return float(ex_call(ex().fetch_ticker, symbol)["last"])

def price_validated(symbol: str) -> float:
    """Precio con protección anti-spike: compara bid/ask con last."""
    ticker = ex_call(ex().fetch_ticker, symbol)
    last   = float(ticker["last"])
    bid    = float(ticker.get("bid") or last)
    ask    = float(ticker.get("ask") or last)
    mid    = (bid + ask) / 2
    if mid > 0 and abs(last - mid) / mid * 100 > ANTI_SPIKE_PCT:
        raise ValueError(f"Anti-spike: last={last:.6g} vs mid={mid:.6g} ({abs(last-mid)/mid*100:.1f}%)")
    return last

def balance() -> float:
    b = ex_call(ex().fetch_balance)
    return float(b.get("USDT", {}).get("free", 0))

def position(symbol: str) -> Optional[dict]:
    try:
        for p in ex_call(ex().fetch_positions, [symbol]):
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                return p
    except Exception:
        pass
    return None

def set_lev(symbol: str):
    try:
        ex_call(ex().set_leverage, LEVERAGE, symbol)
    except Exception as e:
        log.warning(f"[{symbol}] leverage: {e}")

def cancel_stop_orders(symbol: str):
    """Cancela todas las órdenes stop/reduceOnly del símbolo."""
    try:
        orders = ex_call(ex().fetch_open_orders, symbol)
        for o in orders:
            if o.get("reduceOnly") or o.get("type","").lower() in ("stop_market","stop","stop_limit"):
                try: ex_call(ex().cancel_order, o["id"], symbol)
                except: pass
    except Exception as e:
        log.warning(f"cancel_stop_orders {symbol}: {e}")

# ─────────────────────────────────────────────────────────────────
# TELEGRAM — envío + cola de reintentos
# ─────────────────────────────────────────────────────────────────
_tg_queue: deque = deque(maxlen=50)
_tg_q_lock = threading.Lock()

def tg(msg: str, silent: bool = False):
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    payload = {
        "chat_id":              TG_CHAT_ID,
        "text":                 msg[:4096],
        "parse_mode":           "HTML",
        "disable_notification": silent,
    }
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload, timeout=10
        )
        if not r.ok:
            log.warning(f"TG {r.status_code}: {r.text[:150]}")
            with _tg_q_lock: _tg_queue.append(msg)
    except Exception as e:
        log.warning(f"TG error: {e}")
        with _tg_q_lock: _tg_queue.append(msg)

def _tg_retry_worker():
    while True:
        time.sleep(30)
        with _tg_q_lock:
            pending = list(_tg_queue)
            _tg_queue.clear()
        for msg in pending:
            tg(msg)

def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def _bar(value: float, max_val: float, width: int = 10) -> str:
    filled = int(min(value / max_val, 1.0) * width) if max_val > 0 else 0
    return "█" * filled + "░" * (width - filled)

def _dur(entry_time_str: str) -> str:
    try:
        entry_dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        secs     = int((datetime.now(timezone.utc) - entry_dt).total_seconds())
        h, rem   = divmod(secs, 3600)
        m        = rem // 60
        return f"{h}h {m}m" if h > 0 else f"{m}m"
    except Exception:
        return "?"

# ─────────────────────────────────────────────────────────────────
# MENSAJES TELEGRAM
# ─────────────────────────────────────────────────────────────────
def msg_start(bal: float):
    tg(
        f"<b>🚀 SAIYAN AURA FUSION v3.0 — ONLINE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${bal:.2f} USDT</b>\n"
        f"⚙️ ${FIXED_USDT:.0f}/trade · {LEVERAGE}x · max {MAX_OPEN_TRADES} pos\n"
        f"🎯 TP1 +{TP1_PCT}% · TP2 +{TP2_PCT}% · TP3 +{TP3_PCT}%\n"
        f"🛑 SL -{SL_PCT}% · Trailing {TRAILING_PCT}% (activa +{TRAILING_ACTIVATE}%)\n"
        f"🛡 CB -{CB_DD}% · Diario -{DAILY_LOSS_PCT}%\n"
        f"⏱ Cooldown: {COOLDOWN_MIN}min · Anti-spike: {ANTI_SPIKE_PCT}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 Comandos: /status /pos /pause /resume\n"
        f"⏰ {now()}"
    )

def msg_open(t: Trade, bal: float):
    e  = "🟢" if t.side == "long" else "🔴"
    d  = "▲ LONG" if t.side == "long" else "▼ SHORT"
    notional = FIXED_USDT * LEVERAGE
    riesgo   = FIXED_USDT * SL_PCT / 100
    tg(
        f"{e} <b>{d}</b> — <code>{t.symbol}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Entrada:  <code>{t.entry_price:.6g}</code>\n"
        f"🟡 TP1 50%: <code>{t.tp1:.6g}</code>  (+{TP1_PCT}%)\n"
        f"🟠 TP2 30%: <code>{t.tp2:.6g}</code>  (+{TP2_PCT}%)\n"
        f"🟢 TP3 20%: <code>{t.tp3:.6g}</code>  (+{TP3_PCT}%)\n"
        f"🛑 SL:      <code>{t.sl:.6g}</code>   (-{SL_PCT}%)\n"
        f"〽️ Trailing: activa en +{TRAILING_ACTIVATE}% · paso {TRAILING_PCT}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 {t.contracts} contratos · ${notional:.0f} notional\n"
        f"⚠️ Riesgo: ~${riesgo:.2f} · Balance: ${bal:.2f}\n"
        f"📊 {st.wins}W/{st.losses}L · WR:{st.wr():.1f}% · E:${st.expectancy():.2f}/trade\n"
        f"🔢 Pos abiertas: {st.n()}/{MAX_OPEN_TRADES}\n"
        f"⏰ {now()}"
    )

def msg_tp(t: Trade, label: str, pnl_est: float, remaining: str):
    extras = ""
    if label == "TP1":
        extras = "🛡 SL → Break-Even ✓\n〽️ Trailing activado ✓\n"
    tg(
        f"🏆 <b>{label} HIT</b> — <code>{t.symbol}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 ~${pnl_est:+.2f} parcial\n"
        f"📦 Restante: {remaining}\n"
        f"{extras}"
        f"💹 Hoy: ${st.daily_pnl:+.2f} · Total: ${st.total_pnl:+.2f}\n"
        f"⏰ {now()}"
    )

def msg_trailing_update(t: Trade, new_sl: float, gain_pct: float):
    tg(
        f"〽️ <b>TRAILING SL</b> — <code>{t.symbol}</code>\n"
        f"🛑 SL → <code>{new_sl:.6g}</code> · Ganancia: +{gain_pct:.2f}%\n"
        f"⏰ {now()}",
        silent=True
    )

def msg_close(t: Trade, exit_p: float, pnl: float, reason: str):
    e   = "✅" if pnl >= 0 else "❌"
    pct = (exit_p - t.entry_price) / t.entry_price * 100 * (1 if t.side == "long" else -1)
    dur = _dur(t.entry_time)
    daily_bar = _bar(abs(st.daily_pnl), st.peak_equity * DAILY_LOSS_PCT / 100 if st.peak_equity > 0 else 1)
    tg(
        f"{e} <b>CERRADO · {reason}</b>\n"
        f"<code>{t.symbol}</code> {t.side.upper()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <code>{t.entry_price:.6g}</code> → <code>{exit_p:.6g}</code> ({pct:+.2f}%)\n"
        f"{'💰' if pnl>=0 else '💸'} PnL: <b>${pnl:+.2f}</b>  ⏱ {dur}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {st.wins}W/{st.losses}L · WR:{st.wr():.1f}% · PF:{st.pf():.2f}\n"
        f"💡 Avg: +${st.avg_win():.2f} / -${st.avg_loss():.2f} · E:${st.expectancy():.2f}\n"
        f"🏆 Mejor: ${st.best_trade:+.2f} · Peor: ${st.worst_trade:+.2f}\n"
        f"💹 Hoy: ${st.daily_pnl:+.2f} [{daily_bar}]\n"
        f"💼 Total: ${st.total_pnl:+.2f} · MaxDD: {st.max_dd_real:.1f}%\n"
        f"⏰ {now()}"
    )

def msg_blocked(reason: str, action: str, symbol: str):
    tg(
        f"⛔ <b>BLOQUEADO</b> — {reason}\n"
        f"Acción ignorada: {action} {symbol}\n"
        f"💹 Hoy: ${st.daily_pnl:+.2f} · Total: ${st.total_pnl:+.2f}\n"
        f"⏰ {now()}"
    )

def msg_error(txt: str):
    tg(f"🔥 <b>ERROR:</b> <code>{txt[:400]}</code>\n⏰ {now()}")

def msg_status():
    """Respuesta al comando /status."""
    try:
        bal = balance()
    except Exception:
        bal = 0.0
    paused_line = "⏸ <b>BOT EN PAUSA</b>\n" if st.paused else ""
    cb_line     = "🚨 <b>CIRCUIT BREAKER ACTIVO</b>\n" if st.cb() else ""
    dl_line     = "🚨 <b>LÍMITE DIARIO ALCANZADO</b>\n" if st.daily_hit() else ""
    daily_bar   = _bar(abs(st.daily_pnl), st.peak_equity * DAILY_LOSS_PCT / 100 if st.peak_equity > 0 else 1)
    cb_bar      = _bar(abs(st.total_pnl) if st.total_pnl < 0 else 0, st.peak_equity * CB_DD / 100 if st.peak_equity > 0 else 1)
    tg(
        f"📊 <b>STATUS — SAIYAN v3.0</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{paused_line}{cb_line}{dl_line}"
        f"💰 Balance: <b>${bal:.2f} USDT</b>\n"
        f"📦 Posiciones: {st.n()}/{MAX_OPEN_TRADES}\n"
        f"⏱ Uptime: {st.uptime()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 {st.wins}W / {st.losses}L · WR:{st.wr():.1f}%\n"
        f"🏦 PF:{st.pf():.2f} · E:${st.expectancy():.2f}/trade\n"
        f"🏆 Mejor: ${st.best_trade:+.2f} · Peor: ${st.worst_trade:+.2f}\n"
        f"💹 Hoy: ${st.daily_pnl:+.2f} [{daily_bar}] -{DAILY_LOSS_PCT}%\n"
        f"🛡 Drawdown: [{cb_bar}] -{CB_DD}% · Max: {st.max_dd_real:.1f}%\n"
        f"💼 Total PnL: ${st.total_pnl:+.2f}\n"
        f"⏰ {now()}"
    )

def msg_positions():
    """Respuesta al comando /pos."""
    if not st.trades:
        tg("📦 <b>Sin posiciones abiertas</b>")
        return
    lines = [f"📦 <b>POSICIONES ABIERTAS ({st.n()})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for sym_, t in st.trades.items():
        try:
            px      = price(sym_)
            gain    = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
            unreal  = gain
            e_icon  = "🟢" if gain >= 0 else "🔴"
            trail_s = " 〽️" if t.trailing_on else ""
            be_s    = " 🛡BE" if t.sl_at_be else ""
            lines.append(
                f"{e_icon} <code>{sym_}</code> {t.side.upper()}{trail_s}{be_s}\n"
                f"   💵 {t.entry_price:.6g} → {px:.6g} ({unreal:+.2f}%)\n"
                f"   🛑 SL:{t.sl:.6g}  ⏱{_dur(t.entry_time)}"
            )
        except Exception:
            lines.append(f"<code>{sym_}</code> {t.side.upper()} (sin precio)")
    tg("\n".join(lines))

def msg_daily_summary():
    """Resumen diario automático a medianoche."""
    try:
        bal = balance()
    except Exception:
        bal = 0.0
    emoji = "📈" if st.daily_pnl >= 0 else "📉"
    tg(
        f"{emoji} <b>RESUMEN DIARIO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance cierre: ${bal:.2f} USDT\n"
        f"💹 PnL del día: <b>${st.daily_pnl:+.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Histórico total:\n"
        f"  {st.wins}W / {st.losses}L · WR:{st.wr():.1f}%\n"
        f"  PF:{st.pf():.2f} · E:${st.expectancy():.2f}/trade\n"
        f"  🏆 Mejor: ${st.best_trade:+.2f}\n"
        f"  💸 Peor:  ${st.worst_trade:+.2f}\n"
        f"  MaxDD: {st.max_dd_real:.1f}%\n"
        f"  Total: ${st.total_pnl:+.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {now()}"
    )

def msg_heartbeat():
    try:
        bal        = balance()
        open_lines = ""
        for sym_, t in st.trades.items():
            try:
                px     = price(sym_)
                gain   = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
                icon   = "🟢" if gain >= 0 else "🔴"
                trail  = "〽️" if t.trailing_on else "  "
                open_lines += f"  {icon}{trail} <code>{sym_}</code> {t.side.upper()} {gain:+.2f}% ⏱{_dur(t.entry_time)}\n"
            except Exception:
                open_lines += f"  <code>{sym_}</code> {t.side.upper()}\n"
        if not open_lines:
            open_lines = "  (sin posiciones)\n"
        daily_bar = _bar(abs(st.daily_pnl), st.peak_equity * DAILY_LOSS_PCT / 100 if st.peak_equity > 0 else 1)
        cb_bar    = _bar(abs(st.total_pnl) if st.total_pnl < 0 else 0, st.peak_equity * CB_DD / 100 if st.peak_equity > 0 else 1)
        paused    = " ⏸PAUSA" if st.paused else ""
        tg(
            f"💓 <b>HEARTBEAT{paused}</b> · {now()}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: ${bal:.2f} USDT\n"
            f"📦 Posiciones ({st.n()}/{MAX_OPEN_TRADES}):\n{open_lines}"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {st.wins}W/{st.losses}L · WR:{st.wr():.1f}% · PF:{st.pf():.2f}\n"
            f"💹 Hoy: ${st.daily_pnl:+.2f} [{daily_bar}]\n"
            f"🛡 DD: [{cb_bar}] Max:{st.max_dd_real:.1f}%\n"
            f"⏱ Uptime: {st.uptime()}",
            silent=True
        )
    except Exception as e:
        log.warning(f"Heartbeat error: {e}")

# ─────────────────────────────────────────────────────────────────
# TELEGRAM BOT COMMANDS (polling)
# ─────────────────────────────────────────────────────────────────
def _tg_commands_worker():
    """Polling de comandos Telegram: /status /pos /pause /resume /close <sym>"""
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    log.info("Telegram commands polling iniciado")
    while True:
        try:
            url    = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
            params = {"timeout": 30, "offset": st.tg_update_offset, "allowed_updates": ["message"]}
            r      = requests.get(url, params=params, timeout=40)
            if not r.ok:
                time.sleep(5); continue
            updates = r.json().get("result", [])
            for upd in updates:
                st.tg_update_offset = upd["update_id"] + 1
                msg_obj  = upd.get("message", {})
                chat_id  = str(msg_obj.get("chat", {}).get("id", ""))
                text     = msg_obj.get("text", "").strip().lower()
                # Solo aceptar comandos del chat autorizado
                if chat_id != str(TG_CHAT_ID):
                    continue
                if text in ("/status", "/s"):
                    msg_status()
                elif text in ("/pos", "/positions", "/p"):
                    msg_positions()
                elif text == "/pause":
                    with _lock: st.paused = True
                    tg("⏸ <b>Bot en PAUSA.</b> No se abrirán nuevas posiciones.\nUsa /resume para reanudar.")
                elif text == "/resume":
                    with _lock: st.paused = False
                    tg("▶️ <b>Bot REANUDADO.</b> Aceptando señales.")
                elif text == "/help":
                    tg(
                        "📲 <b>Comandos disponibles:</b>\n"
                        "/status — resumen general\n"
                        "/pos — posiciones abiertas\n"
                        "/pause — pausar nuevas entradas\n"
                        "/resume — reanudar\n"
                        "/help — esta ayuda"
                    )
                elif text.startswith("/close "):
                    raw_sym = text.split("/close ")[1].strip().upper()
                    tg(f"🔄 Cerrando <code>{raw_sym}</code>...")
                    res = close_trade(raw_sym, "MANUAL /close")
                    if res.get("result") == "closed":
                        tg(f"✅ <code>{raw_sym}</code> cerrado. PnL: ${res.get('pnl', 0):+.2f}")
                    else:
                        tg(f"⚠️ No se pudo cerrar: {res}")
        except requests.exceptions.Timeout:
            pass  # timeout largo es normal en long-polling
        except Exception as e:
            log.warning(f"TG commands worker: {e}")
            time.sleep(10)

# ─────────────────────────────────────────────────────────────────
# CSV LOG
# ─────────────────────────────────────────────────────────────────
def csv_log(action: str, t: Trade, exit_p: float = 0.0, pnl: float = 0.0):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts","action","symbol","side","entry","exit","pnl","qty","trailing","sl_at_be"])
            w.writerow([now(), action, t.symbol, t.side,
                        t.entry_price, exit_p or t.entry_price,
                        round(pnl, 4), t.contracts, t.trailing_on, t.sl_at_be])
    except Exception as e:
        log.warning(f"CSV: {e}")

# ─────────────────────────────────────────────────────────────────
# TRAILING STOP WORKER
# ─────────────────────────────────────────────────────────────────
def _update_sl_order(t: Trade, new_sl: float):
    try:
        close_side = "sell" if t.side == "long" else "buy"
        cancel_stop_orders(t.symbol)
        ex_call(ex().create_order, t.symbol, "stop_market", close_side, t.contracts, None,
                {"reduceOnly": True, "stopPrice": new_sl})
        log.info(f"  [{t.symbol}] SL → {new_sl:.6g}")
    except Exception as e:
        log.warning(f"_update_sl_order: {e}")

def _trailing_worker():
    log.info("Trailing Stop worker iniciado")
    while True:
        time.sleep(15)
        with _lock:
            symbols = list(st.trades.keys())
        for symbol in symbols:
            try:
                with _lock:
                    if symbol not in st.trades: continue
                    t = st.trades[symbol]

                px = price(symbol)
                gain_pct = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)

                with _lock:
                    if symbol not in st.trades: continue
                    t = st.trades[symbol]

                    if not t.trailing_on and gain_pct >= TRAILING_ACTIVATE:
                        t.trailing_on   = True
                        t.trailing_high = px
                        log.info(f"[{symbol}] Trailing ON @ {px:.6g} (+{gain_pct:.2f}%)")

                    if not t.trailing_on: continue

                    if t.side == "long":
                        if px > t.trailing_high:
                            t.trailing_high = px
                        new_sl = t.trailing_high * (1 - TRAILING_PCT / 100)
                        if new_sl > t.sl:
                            t.sl = new_sl
                            msg_trailing_update(t, new_sl, gain_pct)
                            _update_sl_order(t, new_sl)
                    else:
                        if t.trailing_high == 0 or px < t.trailing_high:
                            t.trailing_high = px
                        new_sl = t.trailing_high * (1 + TRAILING_PCT / 100)
                        if new_sl < t.sl:
                            t.sl = new_sl
                            msg_trailing_update(t, new_sl, gain_pct)
                            _update_sl_order(t, new_sl)
            except Exception as e:
                log.warning(f"Trailing [{symbol}]: {e}")

# ─────────────────────────────────────────────────────────────────
# CORE LOGIC
# ─────────────────────────────────────────────────────────────────
def open_trade(raw_symbol: str, side: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)

        if st.paused:
            return {"result": "paused"}
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
        if st.in_cooldown(symbol):
            remaining = int(COOLDOWN_MIN * 60 - (time.time() - st.cooldowns.get(symbol, 0)))
            log.info(f"[{symbol}] Cooldown activo ({remaining}s restantes)")
            return {"result": "cooldown", "remaining_sec": remaining}

        try:
            e = ex()
            if symbol not in e.markets:
                ex_call(e.load_markets)
            if symbol not in e.markets:
                raise ValueError(f"Symbol not found: {symbol}")

            set_lev(symbol)
            px    = price_validated(symbol)   # con anti-spike
            bal   = balance()
            notl  = FIXED_USDT * LEVERAGE
            raw_q = notl / px
            qty   = float(e.amount_to_precision(symbol, raw_q))

            if qty * px < 5:
                raise ValueError(f"Notional demasiado pequeño: {qty*px:.2f}")

            order_side = "buy" if side == "long" else "sell"
            log.info(f"[OPEN] {symbol} {side.upper()} qty={qty} @~{px:.6g}")

            order   = ex_call(e.create_order, symbol, "market", order_side, qty,
                              params={"reduceOnly": False})
            entry_p = float(order.get("average") or px)

            mult = 1 if side == "long" else -1
            tp1  = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP1_PCT / 100)))
            tp2  = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP2_PCT / 100)))
            tp3  = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP3_PCT / 100)))
            sl   = float(e.price_to_precision(symbol, entry_p * (1 - mult * SL_PCT / 100)))
            close_side = "sell" if side == "long" else "buy"
            ep         = {"reduceOnly": True}

            for frac, px_tp, lbl in [(0.50, tp1, "TP1"), (0.30, tp2, "TP2"), (0.20, tp3, "TP3")]:
                q = float(e.amount_to_precision(symbol, qty * frac))
                try:
                    ex_call(e.create_order, symbol, "limit", close_side, q, px_tp, ep)
                    log.info(f"  {lbl} @ {px_tp:.6g} qty={q}")
                except Exception as err:
                    log.warning(f"  {lbl}: {err}")

            try:
                ex_call(e.create_order, symbol, "stop_market", close_side, qty, None,
                        {**ep, "stopPrice": sl})
                log.info(f"  SL @ {sl:.6g}")
            except Exception as err:
                log.warning(f"  SL: {err}")

            t = Trade(symbol=symbol, side=side, entry_price=entry_p,
                      contracts=qty, tp1=tp1, tp2=tp2, tp3=tp3, sl=sl,
                      entry_time=now(), trailing_high=entry_p)
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
            return {"result": "not_found"}
        t = st.trades[symbol]
        try:
            e = ex()
            try: ex_call(e.cancel_all_orders, symbol)
            except Exception as err: log.warning(f"cancel_all: {err}")

            pos    = position(symbol)
            exit_p = price(symbol)
            pnl    = 0.0

            if pos:
                qty        = abs(float(pos.get("contracts", 0)))
                close_side = "sell" if t.side == "long" else "buy"
                ord_       = ex_call(e.create_order, symbol, "market", close_side, qty,
                                     params={"reduceOnly": True})
                exit_p     = float(ord_.get("average") or exit_p)
                pnl        = ((exit_p - t.entry_price) if t.side == "long"
                              else (t.entry_price - exit_p)) * qty

            dur = _dur(t.entry_time)
            st.closed_history.append({
                "symbol": t.symbol, "side": t.side,
                "entry": t.entry_price, "exit": exit_p,
                "pnl": round(pnl, 4), "reason": reason,
                "duration": dur, "ts": now()
            })
            if len(st.closed_history) > 50:
                st.closed_history = st.closed_history[-50:]

            st.record_close(pnl, symbol)
            csv_log("CLOSE", t, exit_p, pnl)
            msg_close(t, exit_p, pnl, reason)
            del st.trades[symbol]
            save_state()
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
            e   = ex()
            pos = position(symbol)
            rem = str(round(float(pos.get("contracts", 0)), 4)) if pos else "~restante"

            if tp_label == "TP1" and not t.tp1_hit:
                t.tp1_hit = True
                pnl_est   = abs(t.tp1 - t.entry_price) * t.contracts * 0.50
                try:
                    be         = float(e.price_to_precision(symbol, t.entry_price))
                    close_side = "sell" if t.side == "long" else "buy"
                    cancel_stop_orders(symbol)
                    ex_call(e.create_order, symbol, "stop_market", close_side,
                            t.contracts, None, {"reduceOnly": True, "stopPrice": be})
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


# ─────────────────────────────────────────────────────────────────
# FLASK APP
# ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":          "alive",
        "bot":             "SAIYAN AURA FUSION v3.0",
        "paused":          st.paused,
        "uptime":          st.uptime(),
        "open_trades":     st.n(),
        "total_trades":    st.total_trades,
        "wins":            st.wins,
        "losses":          st.losses,
        "win_rate":        round(st.wr(), 1),
        "profit_factor":   round(st.pf(), 2),
        "expectancy":      round(st.expectancy(), 2),
        "avg_win":         round(st.avg_win(), 2),
        "avg_loss":        round(st.avg_loss(), 2),
        "best_trade":      round(st.best_trade, 2),
        "worst_trade":     round(st.worst_trade, 2),
        "max_drawdown":    round(st.max_dd_real, 2),
        "total_pnl":       round(st.total_pnl, 2),
        "daily_pnl":       round(st.daily_pnl, 2),
        "circuit_breaker": st.cb(),
        "daily_limit":     st.daily_hit(),
        "time":            now()
    })

@app.route("/positions", methods=["GET"])
def positions_endpoint():
    result = {}
    for sym_, t in st.trades.items():
        try:
            px     = price(sym_)
            unreal = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
        except Exception:
            px, unreal = 0.0, 0.0
        result[sym_] = {
            "side": t.side, "entry": t.entry_price,
            "current_price": px, "unrealized_pct": round(unreal, 2),
            "tp1": t.tp1, "tp2": t.tp2, "tp3": t.tp3, "sl": t.sl,
            "sl_at_be": t.sl_at_be, "trailing_on": t.trailing_on,
            "trailing_high": t.trailing_high,
            "tp1_hit": t.tp1_hit, "tp2_hit": t.tp2_hit,
            "duration": _dur(t.entry_time), "since": t.entry_time,
        }
    return jsonify(result)

@app.route("/history", methods=["GET"])
def history_endpoint():
    return jsonify({"trades": st.closed_history[-20:]})

@app.route("/dashboard", methods=["GET"])
def dashboard():
    """Dashboard HTML visual."""
    try: bal = balance()
    except Exception: bal = 0.0
    wr    = st.wr()
    color = "#00ff88" if st.total_pnl >= 0 else "#ff4444"
    rows  = ""
    for t in reversed(st.closed_history[-10:]):
        pnl_c = "#00ff88" if t["pnl"] >= 0 else "#ff4444"
        rows += (f"<tr><td>{t['ts']}</td><td>{t['symbol']}</td>"
                 f"<td>{t['side'].upper()}</td>"
                 f"<td style='color:{pnl_c}'>${t['pnl']:+.2f}</td>"
                 f"<td>{t['reason']}</td><td>{t['duration']}</td></tr>")
    open_rows = ""
    for sym_, t in st.trades.items():
        try:
            px = price(sym_)
            g  = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
        except Exception:
            px, g = 0.0, 0.0
        gc = "#00ff88" if g >= 0 else "#ff4444"
        trail = "〽️" if t.trailing_on else ""
        open_rows += (f"<tr><td>{sym_}</td><td>{t.side.upper()}</td>"
                      f"<td>{t.entry_price:.6g}</td><td>{px:.6g}</td>"
                      f"<td style='color:{gc}'>{g:+.2f}%</td>"
                      f"<td>{trail}</td><td>{_dur(t.entry_time)}</td></tr>")
    paused_banner = '<div style="background:#ff8800;color:#000;padding:10px;text-align:center;font-weight:bold">⏸ BOT EN PAUSA</div>' if st.paused else ""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<title>SAIYAN v3.0</title>
<style>
  body{{background:#0d0d0d;color:#e0e0e0;font-family:monospace;margin:20px}}
  h1{{color:#ff6600}}  h2{{color:#ff9900;margin-top:30px}}
  .card{{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:15px;display:inline-block;margin:8px;min-width:150px;text-align:center}}
  .big{{font-size:2em;font-weight:bold}}
  table{{width:100%;border-collapse:collapse;margin-top:10px}}
  th{{background:#222;padding:8px;text-align:left;color:#ff9900}}
  td{{padding:6px 8px;border-bottom:1px solid #222}}
  tr:hover{{background:#1a1a1a}}
  .green{{color:#00ff88}} .red{{color:#ff4444}} .orange{{color:#ff9900}}
</style></head><body>
{paused_banner}
<h1>🔥 SAIYAN AURA FUSION v3.0</h1>
<div style="color:#888">Actualización automática cada 30s · {now()}</div>
<div>
  <div class="card"><div style="color:#888">Balance</div><div class="big">${bal:.2f}</div><div style="color:#888">USDT</div></div>
  <div class="card"><div style="color:#888">PnL Total</div><div class="big" style="color:{color}">${st.total_pnl:+.2f}</div></div>
  <div class="card"><div style="color:#888">Hoy</div><div class="big" style="color:{'#00ff88' if st.daily_pnl>=0 else '#ff4444'}">${st.daily_pnl:+.2f}</div></div>
  <div class="card"><div style="color:#888">Win Rate</div><div class="big">{wr:.1f}%</div><div style="color:#888">{st.wins}W/{st.losses}L</div></div>
  <div class="card"><div style="color:#888">Profit Factor</div><div class="big">{st.pf():.2f}</div></div>
  <div class="card"><div style="color:#888">Expectancy</div><div class="big" style="color:{'#00ff88' if st.expectancy()>=0 else '#ff4444'}">${st.expectancy():.2f}</div></div>
  <div class="card"><div style="color:#888">Max DD</div><div class="big" style="color:#ff4444">{st.max_dd_real:.1f}%</div></div>
  <div class="card"><div style="color:#888">Uptime</div><div class="big" style="font-size:1.2em">{st.uptime()}</div></div>
</div>
<h2>📦 Posiciones abiertas ({st.n()}/{MAX_OPEN_TRADES})</h2>
<table><tr><th>Símbolo</th><th>Lado</th><th>Entrada</th><th>Precio</th><th>PnL%</th><th>Trail</th><th>Tiempo</th></tr>
{open_rows if open_rows else "<tr><td colspan='7' style='color:#666;text-align:center'>Sin posiciones abiertas</td></tr>"}
</table>
<h2>📜 Últimas 10 operaciones</h2>
<table><tr><th>Fecha</th><th>Símbolo</th><th>Lado</th><th>PnL</th><th>Razón</th><th>Duración</th></tr>
{rows if rows else "<tr><td colspan='6' style='color:#666;text-align:center'>Sin historial</td></tr>"}
</table>
</body></html>"""
    return Response(html, mimetype="text/html")

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        log.info(f"Webhook: {data}")

        if data.get("secret", "") != WEBHOOK_SECRET:
            log.warning("Webhook no autorizado")
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
            return jsonify({"error": f"unknown action: {action}"}), 400

        return jsonify(res), 200

    except Exception as e:
        log.exception(f"Webhook crash: {e}")
        msg_error(f"Webhook: {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────
# WORKERS
# ─────────────────────────────────────────────────────────────────
def _heartbeat_worker():
    time.sleep(90)
    while True:
        msg_heartbeat()
        time.sleep(HEARTBEAT_MIN * 60)

def _daily_summary_worker():
    """Envía resumen diario a medianoche UTC y resetea daily_pnl."""
    while True:
        now_utc  = datetime.now(timezone.utc)
        tomorrow = (now_utc + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        wait     = (tomorrow - now_utc).total_seconds()
        time.sleep(wait)
        msg_daily_summary()
        with _lock:
            st.daily_pnl      = 0.0
            st.daily_reset_ts = time.time()
        save_state()

def _autosave_worker():
    """Guarda estado cada 5 minutos."""
    while True:
        time.sleep(300)
        save_state()

# ─────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────
def startup():
    load_state()
    log.info("━" * 58)
    log.info("  SAIYAN AURA FUSION BOT v3.0 — Starting...")
    log.info("━" * 58)
    log.info(f"  USDT/trade: ${FIXED_USDT} | Leverage: {LEVERAGE}x | Max: {MAX_OPEN_TRADES}")
    log.info(f"  TP1:{TP1_PCT}% TP2:{TP2_PCT}% TP3:{TP3_PCT}% SL:{SL_PCT}%")
    log.info(f"  Trailing: activa +{TRAILING_ACTIVATE}% | step {TRAILING_PCT}%")
    log.info(f"  CB:{CB_DD}% | Daily:{DAILY_LOSS_PCT}% | Cooldown:{COOLDOWN_MIN}min")
    log.info(f"  Anti-spike: {ANTI_SPIKE_PCT}% | Heartbeat: {HEARTBEAT_MIN}min")
    log.info("━" * 58)

    if not (API_KEY and API_SECRET):
        log.warning("⚠ Sin API keys")
    if not (TG_TOKEN and TG_CHAT_ID):
        log.warning("⚠ Sin Telegram")

    for attempt in range(10):
        try:
            bal = balance()
            if st.peak_equity == 0:
                st.peak_equity = bal
            st.daily_reset_ts = time.time()
            log.info(f"✓ Balance: ${bal:.2f} USDT")
            log.info(f"✓ Historial restaurado: {st.total_trades} trades")
            msg_start(bal)
            break
        except Exception as e:
            wait = min(2 ** attempt, 60)
            log.warning(f"Startup {attempt+1}/10: {e} — retry {wait}s")
            time.sleep(wait)


# ─────────────────────────────────────────────────────────────────
# LAUNCH (compatible con gunicorn)
# ─────────────────────────────────────────────────────────────────
threading.Thread(target=startup,               daemon=True).start()
threading.Thread(target=_trailing_worker,      daemon=True).start()
threading.Thread(target=_heartbeat_worker,     daemon=True).start()
threading.Thread(target=_daily_summary_worker, daemon=True).start()
threading.Thread(target=_autosave_worker,      daemon=True).start()
threading.Thread(target=_tg_retry_worker,      daemon=True).start()
threading.Thread(target=_tg_commands_worker,   daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
