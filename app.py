"""
AEGIS GEX Bot - Dealer Flow Engine
Webhook receiver para señales de TradingView → Ejecuta órdenes en BingX → Notifica en Telegram
"""

import os
import logging
import hmac
import hashlib
from flask import Flask, request, jsonify
import ccxt
import requests
from datetime import datetime

# ──────────────────────────────────────────────
# Configuración de logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ──────────────────────────────────────────────
# Variables de entorno
# ──────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")       # Clave secreta para validar TradingView
ORDER_SIZE       = float(os.getenv("ORDER_SIZE", "0.01"))  # Tamaño por defecto en USDT equivalente
LEVERAGE         = int(os.getenv("LEVERAGE", "5"))
MODE             = os.getenv("MODE", "live")               # "live" o "paper"

# ──────────────────────────────────────────────
# Conexión a BingX (Futuros Perpetuos)
# ──────────────────────────────────────────────
exchange = ccxt.bingx({
    "apiKey": BINGX_API_KEY,
    "secret": BINGX_SECRET_KEY,
    "options": {"defaultType": "swap"},
})

# ──────────────────────────────────────────────
# Telegram helper
# ──────────────────────────────────────────────
def send_telegram(message: str):
    """Envía un mensaje al canal de Telegram configurado."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Error enviando Telegram: {e}")


def fmt_signal_msg(data: dict, order: dict | None, error: str | None) -> str:
    """Formatea el mensaje de Telegram para una señal recibida."""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    symbol   = data.get("ticker", "?")
    signal   = data.get("signal", "?").upper()
    regime   = data.get("regime", "?")
    dgrp     = data.get("dgrp_score", "?")
    price    = data.get("price", "?")
    gex_flip = data.get("gex_flip", "?")

    regime_emoji = {"POSITIVE GAMMA": "🟢", "NEGATIVE GAMMA": "🔴", "FLIP ZONE": "🟡"}.get(regime, "⚪")

    if error:
        return (
            f"⚠️ <b>AEGIS GEX — ERROR</b>\n"
            f"────────────────────\n"
            f"🕒 {ts}\n"
            f"📈 {symbol} | Señal: <b>{signal}</b>\n"
            f"❌ Error: <code>{error}</code>"
        )

    status = "✅ EJECUTADA" if order else "📋 PAPER TRADE"
    order_id = order.get("id", "—") if order else "—"

    return (
        f"{'🟢' if 'LONG' in signal or 'BUY' in signal else '🔴'} <b>AEGIS GEX — SEÑAL {signal}</b>\n"
        f"────────────────────\n"
        f"🕒 {ts}\n"
        f"📈 Par: <b>{symbol}</b>\n"
        f"💲 Precio entrada: <b>{price}</b>\n"
        f"────────────────────\n"
        f"{regime_emoji} Régimen: <b>{regime}</b>\n"
        f"📊 DGRP Score: <b>{dgrp}/100</b>\n"
        f"🔄 GEX Flip: <b>{gex_flip}</b>\n"
        f"────────────────────\n"
        f"📦 Estado: <b>{status}</b>\n"
        f"🆔 Order ID: <code>{order_id}</code>\n"
        f"⚙️ Tamaño: {ORDER_SIZE} | Apalancamiento: {LEVERAGE}x"
    )


# ──────────────────────────────────────────────
# Lógica de trading
# ──────────────────────────────────────────────
def close_opposite_position(symbol: str, side: str):
    """Cierra la posición contraria si existe (modo Always-In-Market)."""
    try:
        positions = exchange.fetch_positions([symbol])
        for pos in positions:
            if pos["symbol"] == symbol and float(pos.get("contracts", 0)) != 0:
                pos_side = pos.get("side", "")
                # Si voy a abrir LONG y tengo SHORT abierto, cierro el SHORT
                if side == "long" and pos_side == "short":
                    exchange.create_market_buy_order(symbol, abs(float(pos["contracts"])), {"reduceOnly": True})
                    log.info(f"Posición SHORT cerrada para {symbol}")
                elif side == "short" and pos_side == "long":
                    exchange.create_market_sell_order(symbol, abs(float(pos["contracts"])), {"reduceOnly": True})
                    log.info(f"Posición LONG cerrada para {symbol}")
    except Exception as e:
        log.warning(f"No se pudo cerrar posición contraria: {e}")


def set_leverage(symbol: str):
    """Configura el apalancamiento para el par."""
    try:
        exchange.set_leverage(LEVERAGE, symbol)
    except Exception as e:
        log.warning(f"No se pudo configurar leverage: {e}")


def execute_order(symbol: str, side: str) -> dict:
    """Ejecuta una orden de mercado en BingX."""
    set_leverage(symbol)
    close_opposite_position(symbol, side)

    if side == "long":
        order = exchange.create_market_buy_order(symbol, ORDER_SIZE)
    elif side == "short":
        order = exchange.create_market_sell_order(symbol, ORDER_SIZE)
    else:
        raise ValueError(f"Señal inválida: {side}")

    return order


# ──────────────────────────────────────────────
# Validación de la señal AEGIS GEX
# ──────────────────────────────────────────────
VALID_SIGNALS = {
    # Señales LONG
    "long", "buy", "wall_break_long", "gex_flip_cross_long",
    "vanna_unwind_long", "compression_break_long",
    # Señales SHORT
    "short", "sell", "wall_break_short", "gex_flip_cross_short",
    "vanna_unwind_short", "compression_break_short",
    # Señal de cierre
    "close",
}

LONG_SIGNALS  = {"long", "buy", "wall_break_long", "gex_flip_cross_long", "vanna_unwind_long", "compression_break_long"}
SHORT_SIGNALS = {"short", "sell", "wall_break_short", "gex_flip_cross_short", "vanna_unwind_short", "compression_break_short"}


def interpret_signal(raw: str) -> str:
    """Normaliza la señal a 'long', 'short' o 'close'."""
    raw = raw.lower().strip()
    if raw in LONG_SIGNALS:
        return "long"
    if raw in SHORT_SIGNALS:
        return "short"
    if raw == "close":
        return "close"
    raise ValueError(f"Señal desconocida: {raw}")


# ──────────────────────────────────────────────
# Rutas Flask
# ──────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "online", "bot": "AEGIS GEX", "mode": MODE}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    # Validar secreto si está configurado
    if WEBHOOK_SECRET:
        auth = request.headers.get("X-Webhook-Secret", "")
        if auth != WEBHOOK_SECRET:
            log.warning("Webhook secret inválido.")
            return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON recibido"}), 400

    log.info(f"Señal recibida: {data}")

    raw_signal = data.get("signal", "")
    symbol     = data.get("ticker", "BTC/USDT:USDT")

    try:
        side = interpret_signal(raw_signal)
    except ValueError as e:
        msg = str(e)
        log.error(msg)
        send_telegram(fmt_signal_msg(data, None, msg))
        return jsonify({"error": msg}), 400

    order = None
    error = None

    try:
        if MODE == "live":
            if side == "close":
                # Cierre de todas las posiciones del símbolo
                positions = exchange.fetch_positions([symbol])
                for pos in positions:
                    if pos["symbol"] == symbol and float(pos.get("contracts", 0)) != 0:
                        pos_side = pos.get("side")
                        if pos_side == "long":
                            order = exchange.create_market_sell_order(symbol, abs(float(pos["contracts"])), {"reduceOnly": True})
                        elif pos_side == "short":
                            order = exchange.create_market_buy_order(symbol, abs(float(pos["contracts"])), {"reduceOnly": True})
            else:
                order = execute_order(symbol, side)
        else:
            # Paper trade: solo simulamos
            log.info(f"[PAPER] {side.upper()} {ORDER_SIZE} {symbol}")
            order = {"id": "PAPER-" + datetime.utcnow().strftime("%H%M%S"), "paper": True}

    except Exception as e:
        error = str(e)
        log.error(f"Error ejecutando orden: {error}")

    send_telegram(fmt_signal_msg(data, order, error))

    if error:
        return jsonify({"status": "error", "message": error}), 500

    return jsonify({"status": "success", "order": order}), 200


@app.route("/status", methods=["GET"])
def status():
    """Endpoint para ver posiciones abiertas."""
    try:
        if MODE != "live":
            return jsonify({"mode": "paper", "positions": []}), 200
        positions = exchange.fetch_positions()
        open_pos = [p for p in positions if float(p.get("contracts", 0)) != 0]
        return jsonify({"mode": MODE, "positions": open_pos}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────
# Inicio
# ──────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    send_telegram(
        f"🚀 <b>AEGIS GEX Bot iniciado</b>\n"
        f"Modo: <b>{MODE.upper()}</b> | Leverage: <b>{LEVERAGE}x</b> | Size: <b>{ORDER_SIZE}</b>"
    )
    app.run(host="0.0.0.0", port=port)
