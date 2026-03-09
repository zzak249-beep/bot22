#!/usr/bin/env python3
"""
telegram_notifier.py v4.0 — Notificaciones Telegram + comandos
"""

import os
import threading
import time
import requests
from datetime import datetime, timezone

_TOKEN   = lambda: os.getenv("TELEGRAM_TOKEN", "")
_CHAT_ID = lambda: os.getenv("TELEGRAM_CHAT_ID", "")
_OFFSET  = {"value": 0}

EMOJI = {"long": "🟢", "short": "🔴", "win": "✅", "loss": "❌",
         "info": "ℹ️", "warn": "⚠️", "rocket": "🚀"}


def _send(text: str, parse_mode: str = "HTML"):
    token = _TOKEN()
    chat  = _CHAT_ID()
    if not token or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": parse_mode},
            timeout=8,
        )
    except Exception as e:
        print(f"[TG] send error: {e}")


# ── Notificaciones principales ──────────────────────

def notify_start(version: str, symbols: list, mode: str, balance: float):
    _send(
        f"🤖 <b>BOT {version} INICIADO</b>\n"
        f"Modo: <code>{mode.upper()}</code>\n"
        f"Balance: <code>${balance:.2f}</code>\n"
        f"Pares activos: <code>{len(symbols)}</code>\n"
        f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
    )


def notify_signal(sym: str, side: str, score: int, rsi: float,
                  price: float, sl: float, tp: float,
                  trend: str, executed: bool, balance: float):
    e = EMOJI["long"] if side == "long" else EMOJI["short"]
    status = "ABIERTA" if executed else "SIN FONDOS"
    _send(
        f"{e} <b>{sym} {side.upper()} — {status}</b>\n"
        f"Score: <code>{score}</code> | RSI: <code>{rsi:.1f}</code>\n"
        f"Precio: <code>{price:.4f}</code>\n"
        f"SL: <code>{sl:.4f}</code> | TP: <code>{tp:.4f}</code>\n"
        f"Tendencia 1h: <code>{trend}</code>\n"
        f"Balance: <code>${balance:.2f}</code>"
    )


def notify_close(sym: str, side: str, entry: float, exit_p: float,
                 pnl: float, reason: str, balance: float):
    e = EMOJI["win"] if pnl > 0 else EMOJI["loss"]
    _send(
        f"{e} <b>{sym} CERRADO — {reason}</b>\n"
        f"Side: <code>{side.upper()}</code>\n"
        f"Entry: <code>{entry:.4f}</code> → Exit: <code>{exit_p:.4f}</code>\n"
        f"PnL: <code>${pnl:+.4f}</code>\n"
        f"Balance: <code>${balance:.2f}</code>"
    )


def notify_partial_tp(sym: str, side: str, price: float, balance: float):
    _send(
        f"📊 <b>{sym} PARTIAL TP</b>\n"
        f"50% cerrado a <code>{price:.4f}</code>\n"
        f"SL movido a breakeven\n"
        f"Balance: <code>${balance:.2f}</code>"
    )


def notify_heartbeat(version: str, cycle: int, balance: float,
                     open_pos: int, mode: str, stats: dict):
    dd  = stats.get("drawdown_pct", 0)
    wr  = stats.get("overall_wr", 0)
    pnl = stats.get("daily_pnl", 0)
    _send(
        f"💓 <b>Heartbeat #{cycle}</b> — {version}\n"
        f"Modo: <code>{mode.upper()}</code>\n"
        f"Balance: <code>${balance:.2f}</code>\n"
        f"Posiciones abiertas: <code>{open_pos}</code>\n"
        f"WR total: <code>{wr:.1f}%</code>\n"
        f"PnL hoy: <code>${pnl:+.4f}</code>\n"
        f"Drawdown: <code>{dd:.1f}%</code>"
    )


def notify_circuit_breaker(reason: str):
    _send(f"🚨 <b>CIRCUIT BREAKER</b>\nRazón: {reason}")


def notify_no_funds(sym: str, side: str, score: int, rsi: float,
                    price: float, sl: float, tp: float):
    _send(
        f"⚠️ <b>SEÑAL SIN FONDOS</b>\n"
        f"{sym} {side.upper()} score={score} rsi={rsi:.1f}\n"
        f"Entry={price:.4f} SL={sl:.4f} TP={tp:.4f}"
    )


def notify_error(msg: str):
    _send(f"🔴 <b>ERROR</b>\n<code>{msg}</code>")


# ── Listener de comandos ─────────────────────────────

def _handle_command(text: str):
    """Procesa comandos /pause /resume /status /help"""
    import risk_manager as rm
    import trader

    cmd = text.strip().lower().split()[0]

    if cmd == "/pause":
        rm.pause()
        _send("⏸️ Bot pausado. Usa /resume para reactivar.")

    elif cmd == "/resume":
        rm.resume()
        _send("▶️ Bot reanudado.")

    elif cmd == "/status":
        bal = trader.get_balance()
        pos = trader.get_positions()
        stats = rm.get_stats(bal)
        summary = trader.get_summary()
        _send(
            f"📊 <b>STATUS</b>\n"
            f"Balance: <code>${bal:.2f}</code>\n"
            f"Posiciones: <code>{len(pos)}</code> {list(pos.keys())}\n"
            f"WR: <code>{summary.get('wr',0)}%</code> "
            f"({summary.get('wins',0)}W / {summary.get('losses',0)}L)\n"
            f"PnL total: <code>${summary.get('pnl',0):+.4f}</code>\n"
            f"Drawdown: <code>{stats.get('drawdown_pct',0):.1f}%</code>\n"
            f"Pausa: <code>{'SÍ' if rm.is_manually_paused() else 'NO'}</code>"
        )

    elif cmd == "/help":
        _send(
            "🤖 <b>Comandos disponibles</b>\n"
            "/status — Ver estado actual\n"
            "/pause  — Pausar el bot\n"
            "/resume — Reanudar el bot\n"
            "/help   — Esta ayuda"
        )


def _poll_commands():
    """Polling de comandos Telegram en hilo separado."""
    while True:
        try:
            token = _TOKEN()
            if not token:
                time.sleep(30)
                continue
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": _OFFSET["value"], "timeout": 20},
                timeout=25,
            )
            updates = r.json().get("result", [])
            for u in updates:
                _OFFSET["value"] = u["update_id"] + 1
                msg = u.get("message", {})
                text = msg.get("text", "")
                if text.startswith("/"):
                    _handle_command(text)
        except Exception:
            pass
        time.sleep(2)


def start_command_listener():
    if not _TOKEN():
        return
    t = threading.Thread(target=_poll_commands, daemon=True)
    t.start()
