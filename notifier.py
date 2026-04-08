"""
Telegram Notifier
Sends trade alerts and daily summaries to Telegram.
Optional: only activates if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.
"""
import logging, requests, time, os
from typing import Optional

log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED   = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send(message: str, parse_mode: str = "HTML") -> bool:
    """Send message to Telegram. Returns True if successful."""
    if not TELEGRAM_ENABLED:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": parse_mode,
        }, timeout=10)
        if not r.ok:
            log.warning(f"Telegram error: {r.text[:100]}")
        return r.ok
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")
        return False


def notify_trade_opened(symbol: str, direction: str, qty: float,
                        entry: float, sl: float, tp1: float, tp2: float,
                        notional: float, dry_run: bool = False) -> None:
    emoji = "🟢" if direction == "LONG" else "🔴"
    mode  = "📋 [DRY RUN] " if dry_run else ""
    msg = (
        f"{mode}{emoji} <b>TRADE OPENED</b>\n"
        f"Symbol: <code>{symbol}</code>\n"
        f"Dir: <b>{direction}</b> | Qty: {qty}\n"
        f"Entry: {entry} | SL: {sl}\n"
        f"TP1: {tp1} | TP2: {tp2}\n"
        f"Notional: ${notional:.1f}"
    )
    send(msg)


def notify_tp_hit(symbol: str, tp_level: int, price: float,
                  partial_qty: float, pnl: float = 0) -> None:
    msg = (
        f"💰 <b>TP{tp_level} HIT</b>\n"
        f"Symbol: <code>{symbol}</code>\n"
        f"Price: {price} | Qty closed: {partial_qty}\n"
        f"Est PnL: {pnl:+.2f} USDT"
    )
    send(msg)


def notify_sl_hit(symbol: str, price: float, loss: float = 0) -> None:
    msg = (
        f"🛑 <b>STOP LOSS HIT</b>\n"
        f"Symbol: <code>{symbol}</code>\n"
        f"Price: {price} | Loss: {loss:.2f} USDT"
    )
    send(msg)


def notify_daily_summary(balance: float, daily_pnl: float,
                          trades: int, fees: float) -> None:
    emoji = "📈" if daily_pnl >= 0 else "📉"
    msg = (
        f"{emoji} <b>DAILY SUMMARY</b>\n"
        f"Balance: ${balance:.2f}\n"
        f"Daily P&L: {daily_pnl:+.2f} USDT\n"
        f"Trades: {trades} | Fees paid: ${fees:.3f}"
    )
    send(msg)


def notify_kill_switch(daily_loss_pct: float) -> None:
    msg = (
        f"⛔ <b>KILL SWITCH ACTIVATED</b>\n"
        f"Daily loss: {daily_loss_pct:.1f}% exceeded limit.\n"
        f"Bot paused for today."
    )
    send(msg)


def notify_startup(balance: float, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "🔴 LIVE TRADING"
    msg = (
        f"🤖 <b>SuperBot Started</b>\n"
        f"Mode: {mode}\n"
        f"Balance: ${balance:.2f} USDT"
    )
    send(msg)
