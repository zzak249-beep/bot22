"""
telegram_notifier.py — Notificaciones Telegram v14.0
Wrapper unificado que elimina la confusión entre versiones.
"""
import logging
import threading
import requests

log = logging.getLogger("telegram_notifier")

try:
    import config as cfg
    TOKEN   = cfg.TELEGRAM_TOKEN
    CHAT_ID = cfg.TELEGRAM_CHAT_ID
    VERSION = cfg.VERSION
    TRADE_MODE = cfg.TRADE_MODE
except Exception:
    TOKEN = ""; CHAT_ID = ""; VERSION = "v14"; TRADE_MODE = "paper"


def _send(text: str, parse_mode: str = "Markdown"):
    """Envía mensaje a Telegram. No crashea si falla."""
    tok = TOKEN or ""
    cid = CHAT_ID or ""
    if not tok or not cid:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": cid, "text": text[:4000], "parse_mode": parse_mode},
            timeout=10
        )
    except Exception as e:
        log.debug(f"Telegram: {e}")


# ── Arranque ──────────────────────────────────────────

def notify_start(version: str, symbols: list, mode: str, balance: float):
    modo = "🧪 PAPER" if mode != "live" else "💰 LIVE"
    _send(
        f"🚀 *Bot {version} iniciado — {modo}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance : `${balance:.2f} USDT`\n"
        f"📋 Pares   : `{len(symbols)}`\n"
        f"⚙️ Estrategias: BB_RSI + RSI_DIVERGE + EMA_PULL\n"
        f"🔧 Leverage: `{cfg.LEVERAGE}x` | Riesgo/trade: `{cfg.RISK_PCT*100:.1f}%`\n"
        f"🎯 Score mín: `{cfg.SCORE_MIN}` | R:R mín: `{cfg.MIN_RR}`"
    )


# ── Señales ───────────────────────────────────────────

def notify_signal(symbol: str, side: str, score: int, rsi: float,
                  entry: float, sl: float, tp: float, trend: str,
                  executed: bool, balance: float, **kwargs):
    lado  = "📈 LONG" if side == "long" else "📉 SHORT"
    modo  = "✅ Ejecutado" if executed else "⚠️ No ejecutado"
    risk  = abs(entry - sl) / entry * 100 if entry > 0 else 0
    rew   = abs(tp - entry) / entry * 100 if entry > 0 else 0
    rr    = round(rew / risk, 2) if risk > 0 else 0
    bias  = kwargs.get("bias_4h", "?")
    strat = kwargs.get("strategy", "BB_RSI")
    _send(
        f"{lado} *{strat}* — `{symbol}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Entrada : `{entry}`\n"
        f"🔴 SL      : `{sl}` _(-{risk:.2f}%)_\n"
        f"🟢 TP      : `{tp}` _(+{rew:.2f}%)_\n"
        f"📐 R:R     : `{rr}x` | Score: `{score}/100`\n"
        f"📊 RSI: `{rsi}` | Trend: `{trend}` | 4h: `{bias}`\n"
        f"💼 Balance: `${balance:.2f}`\n"
        f"{modo}"
    )


def notify_close(symbol: str, side: str, entry: float, exit_p: float,
                 pnl: float, reason: str, balance: float):
    emoji = "✅" if pnl >= 0 else "❌"
    chg   = (exit_p - entry) / entry * 100 if entry > 0 else 0
    if side == "short":
        chg = -chg
    _send(
        f"{emoji} *CIERRE* — `{symbol}` [{reason}]\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entrada  : `{entry}`\n"
        f"Salida   : `{exit_p}` _({chg:+.2f}%)_\n"
        f"PnL      : `${pnl:+.4f}`\n"
        f"Balance  : `${balance:.2f}`"
    )


def notify_partial_tp(symbol: str, side: str, price: float, balance: float):
    _send(
        f"⚡ *CIERRE PARCIAL* — `{symbol}`\n"
        f"Precio: `{price}` | Balance: `${balance:.2f}`\n"
        f"🔒 SL movido a _breakeven_"
    )


def notify_circuit_breaker(reason: str):
    _send(f"🛑 *CIRCUIT BREAKER* — `{reason}`\n⏸ Bot pausado temporalmente")


def notify_heartbeat(version: str, cycle: int, balance: float,
                     positions: int, mode: str, stats: dict):
    wr  = stats.get("wr", 0)
    dd  = stats.get("drawdown_pct", 0)
    con = stats.get("consecutive", 0)
    pnl = stats.get("pnl_today", 0)
    w   = stats.get("wins", 0)
    l   = stats.get("losses", 0)
    _send(
        f"🟢 *HEARTBEAT {version} #{cycle}*\n"
        f"Balance: `${balance:.2f}`  Posiciones: `{positions}`\n"
        f"Drawdown: `{dd:.1f}%`  Pérd.consec: `{con}`\n"
        f"W/L: `{w}/{l}` WR:`{wr:.1f}%`  PnL hoy: `${pnl:+.2f}`"
    )


def notify_error(msg: str):
    _send(f"🚨 *ERROR*\n`{msg[:300]}`")


def notify_reentry(symbol: str, side: str, score: int):
    _send(f"🔄 *RE-ENTRY* — `{symbol}` {side.upper()} score=`{score}`")


# ── Listener de comandos Telegram ─────────────────────

_last_update_id = 0

def _poll_commands():
    global _last_update_id
    tok = TOKEN or ""
    cid = CHAT_ID or ""
    if not tok or not cid:
        return
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{tok}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 5},
            timeout=10
        ).json()
        for upd in r.get("result", []):
            _last_update_id = upd["update_id"]
            text = upd.get("message", {}).get("text", "").strip().lower()
            if text == "/status":
                _send("ℹ️ Bot activo y escaneando señales.")
            elif text == "/balance":
                try:
                    import trader
                    b = trader.get_balance()
                    _send(f"💰 Balance: `${b:.2f} USDT`")
                except Exception:
                    pass
    except Exception:
        pass


def start_command_listener():
    """Arranca el listener de comandos en un hilo de fondo."""
    import time
    def loop():
        while True:
            try:
                _poll_commands()
            except Exception:
                pass
            time.sleep(15)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
