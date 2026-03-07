#!/usr/bin/env python3
"""
notifier.py — Notificaciones Telegram
"""

import requests
import time
import config

TELEGRAM_API = "https://api.telegram.org/bot"


def enviar(mensaje: str, parse_mode: str = "HTML") -> bool:
    """Envía mensaje a Telegram"""
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM] Sin credenciales: {mensaje[:80]}")
        return False
    try:
        url = f"{TELEGRAM_API}{config.TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": parse_mode
        }
        r = requests.post(url, json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False


def notify_inicio(balance: float):
    modo = "🔥 DINERO REAL" if not config.MODO_DEMO else "🧪 MODO DEMO"
    msg = (
        f"🤖 <b>Bot BingX Arrancado</b>\n"
        f"💰 Balance: <b>${balance:.2f} USDT</b>\n"
        f"{modo}\n"
        f"⚙️ Leverage: {config.LEVERAGE}x | Score min: {config.SCORE_MIN}\n"
        f"📊 Max posiciones: {config.MAX_POSICIONES}\n"
        f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    enviar(msg)


def notify_señal(symbol: str, lado: str, precio: float, sl: float, tp: float,
                 score: int, cantidad: float, balance: float):
    emoji = "🟢" if lado == "LONG" else "🔴"
    rr = abs(tp - precio) / abs(precio - sl) if abs(precio - sl) > 0 else 0
    msg = (
        f"{emoji} <b>{lado} {symbol}</b>\n"
        f"💵 Entrada: <b>${precio:.4f}</b>\n"
        f"🛡️ SL: ${sl:.4f} | 🎯 TP: ${tp:.4f}\n"
        f"📊 Score: {score}/100 | R:R {rr:.1f}x\n"
        f"📦 Cantidad: {cantidad} | Margen: ${balance * config.RIESGO_POR_TRADE:.2f}\n"
        f"⏰ {time.strftime('%H:%M:%S')}"
    )
    enviar(msg)


def notify_cierre(symbol: str, lado: str, precio_entrada: float, precio_salida: float,
                  pnl: float, resultado: str, duracion_min: int = 0):
    emoji = "✅" if resultado == "WIN" else "❌"
    pct = (pnl / (precio_entrada * 0.03)) * 100 if precio_entrada > 0 else 0
    msg = (
        f"{emoji} <b>CERRADO {lado} {symbol}</b>\n"
        f"📥 Entrada: ${precio_entrada:.4f} → 📤 Salida: ${precio_salida:.4f}\n"
        f"💰 PnL: <b>${pnl:+.2f}</b>\n"
        f"⏱️ Duración: {duracion_min} min\n"
        f"⏰ {time.strftime('%H:%M:%S')}"
    )
    enviar(msg)


def notify_reporte(stats: dict, balance: float, posiciones: int):
    wr = 0
    if stats.get("trades_total", 0) > 0:
        wr = stats["trades_win"] / stats["trades_total"] * 100
    msg = (
        f"📊 <b>Reporte del día</b>\n"
        f"💰 Balance: <b>${balance:.2f} USDT</b>\n"
        f"📈 PnL hoy: <b>${stats.get('pnl_total', 0):+.2f}</b>\n"
        f"🎯 Trades: {stats.get('trades_total', 0)} "
        f"(✅{stats.get('trades_win', 0)} ❌{stats.get('trades_loss', 0)})\n"
        f"📉 Win Rate: {wr:.0f}%\n"
        f"🔄 Posiciones abiertas: {posiciones}\n"
        f"⏰ {time.strftime('%H:%M:%S')}"
    )
    enviar(msg)


def notify_circuit_breaker(razon: str, perdida_pct: float):
    msg = (
        f"⛔ <b>CIRCUIT BREAKER ACTIVADO</b>\n"
        f"🚨 Razón: {razon}\n"
        f"📉 Pérdida: {perdida_pct:.1f}%\n"
        f"⏸️ Bot pausado temporalmente\n"
        f"⏰ {time.strftime('%H:%M:%S')}"
    )
    enviar(msg)


def notify_error(mensaje: str):
    msg = f"⚠️ <b>Error Bot</b>\n{mensaje}\n⏰ {time.strftime('%H:%M:%S')}"
    enviar(msg)
