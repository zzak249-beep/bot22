"""
AEGIS GEX Bot — Dealer Flow Engine v2.0
Webhook TradingView + Scanner autónomo de señales
BingX Futuros Perpetuos → Telegram
"""

import os
import logging
import time
import threading
import numpy as np
from flask import Flask, request, jsonify
import ccxt
import requests
from datetime import datetime

# ──────────────────────────────────────────────
# Logging
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
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")
ORDER_SIZE       = float(os.getenv("ORDER_SIZE", "0.01"))
LEVERAGE         = int(os.getenv("LEVERAGE", "5"))
MODE             = os.getenv("MODE", "paper")

# ── Scanner config ──
SCAN_INTERVAL    = int(os.getenv("SCAN_INTERVAL", "60"))      # segundos entre escaneos
SCAN_SYMBOLS     = os.getenv("SCAN_SYMBOLS", "BTC/USDT:USDT,ETH/USDT:USDT").split(",")
SCAN_TIMEFRAME   = os.getenv("SCAN_TIMEFRAME", "15m")
SCANNER_ENABLED  = os.getenv("SCANNER_ENABLED", "true").lower() == "true"

# ──────────────────────────────────────────────
# Conexión BingX
# ──────────────────────────────────────────────
exchange = ccxt.bingx({
    "apiKey": BINGX_API_KEY,
    "secret": BINGX_SECRET_KEY,
    "options": {"defaultType": "swap"},
})

# ──────────────────────────────────────────────
# Telegram
# ──────────────────────────────────────────────
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Error Telegram: {e}")


def fmt_signal_msg(data: dict, order: dict | None, error: str | None, source: str = "TradingView") -> str:
    ts       = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    symbol   = data.get("ticker", "?")
    signal   = data.get("signal", "?").upper()
    regime   = data.get("regime", "?")
    dgrp     = data.get("dgrp_score", "?")
    price    = data.get("price", "?")
    gex_flip = data.get("gex_flip", "?")
    regime_emoji = {"POSITIVE GAMMA": "🟢", "NEGATIVE GAMMA": "🔴", "FLIP ZONE": "🟡"}.get(regime, "⚪")
    src_icon = "📡" if source == "Scanner" else "📊"

    if error:
        return (
            f"⚠️ <b>AEGIS GEX — ERROR</b>\n"
            f"────────────────────\n"
            f"🕒 {ts} | {src_icon} {source}\n"
            f"📈 {symbol} | Señal: <b>{signal}</b>\n"
            f"❌ <code>{error}</code>"
        )

    status   = "✅ EJECUTADA" if order and not order.get("paper") else "📋 PAPER"
    order_id = order.get("id", "—") if order else "—"
    bull     = "LONG" in signal or "BUY" in signal

    return (
        f"{'🟢' if bull else '🔴'} <b>AEGIS GEX — {signal}</b>\n"
        f"────────────────────\n"
        f"🕒 {ts} | {src_icon} {source}\n"
        f"📈 <b>{symbol}</b> @ <b>{price}</b>\n"
        f"────────────────────\n"
        f"{regime_emoji} Régimen: <b>{regime}</b>\n"
        f"📊 DGRP Score: <b>{dgrp}/100</b>\n"
        f"🔄 GEX Flip: <b>{gex_flip}</b>\n"
        f"────────────────────\n"
        f"📦 {status} | 🆔 <code>{order_id}</code>\n"
        f"⚙️ Size: {ORDER_SIZE} | Lev: {LEVERAGE}x"
    )


# ──────────────────────────────────────────────
# Trading
# ──────────────────────────────────────────────
def set_leverage(symbol: str):
    try:
        exchange.set_leverage(LEVERAGE, symbol)
    except Exception as e:
        log.warning(f"Leverage: {e}")


def close_opposite_position(symbol: str, side: str):
    try:
        positions = exchange.fetch_positions([symbol])
        for pos in positions:
            if pos["symbol"] == symbol and float(pos.get("contracts", 0)) != 0:
                ps = pos.get("side", "")
                if side == "long" and ps == "short":
                    exchange.create_market_buy_order(symbol, abs(float(pos["contracts"])), {"reduceOnly": True})
                elif side == "short" and ps == "long":
                    exchange.create_market_sell_order(symbol, abs(float(pos["contracts"])), {"reduceOnly": True})
    except Exception as e:
        log.warning(f"Close opposite: {e}")


def execute_order(symbol: str, side: str) -> dict:
    set_leverage(symbol)
    close_opposite_position(symbol, side)
    if side == "long":
        return exchange.create_market_buy_order(symbol, ORDER_SIZE)
    elif side == "short":
        return exchange.create_market_sell_order(symbol, ORDER_SIZE)
    raise ValueError(f"Side inválido: {side}")


# ──────────────────────────────────────────────
# Señales
# ──────────────────────────────────────────────
LONG_SIGNALS  = {"long","buy","wall_break_long","gex_flip_cross_long","vanna_unwind_long","compression_break_long"}
SHORT_SIGNALS = {"short","sell","wall_break_short","gex_flip_cross_short","vanna_unwind_short","compression_break_short"}

def interpret_signal(raw: str) -> str:
    raw = raw.lower().strip()
    if raw in LONG_SIGNALS:  return "long"
    if raw in SHORT_SIGNALS: return "short"
    if raw == "close":       return "close"
    raise ValueError(f"Señal desconocida: {raw}")


def process_signal(data: dict, source: str = "TradingView"):
    """Procesa y ejecuta una señal (de webhook o del scanner)."""
    raw_signal = data.get("signal", "")
    symbol     = data.get("ticker", "BTC/USDT:USDT")

    try:
        side = interpret_signal(raw_signal)
    except ValueError as e:
        send_telegram(fmt_signal_msg(data, None, str(e), source))
        return {"error": str(e)}

    order = None
    error = None

    try:
        if MODE == "live":
            if side == "close":
                positions = exchange.fetch_positions([symbol])
                for pos in positions:
                    if pos["symbol"] == symbol and float(pos.get("contracts", 0)) != 0:
                        ps = pos.get("side")
                        if ps == "long":
                            order = exchange.create_market_sell_order(symbol, abs(float(pos["contracts"])), {"reduceOnly": True})
                        elif ps == "short":
                            order = exchange.create_market_buy_order(symbol, abs(float(pos["contracts"])), {"reduceOnly": True})
            else:
                order = execute_order(symbol, side)
        else:
            log.info(f"[PAPER] {side.upper()} {ORDER_SIZE} {symbol}")
            order = {"id": f"PAPER-{datetime.utcnow().strftime('%H%M%S')}", "paper": True}

    except Exception as e:
        error = str(e)
        log.error(f"Error orden: {error}")

    send_telegram(fmt_signal_msg(data, order, error, source))
    return {"order": order, "error": error}


# ──────────────────────────────────────────────
# ══════════════════════════════════════════════
#   SCANNER AUTÓNOMO DE SEÑALES
# ══════════════════════════════════════════════
# ──────────────────────────────────────────────

# Estado: evita señales duplicadas consecutivas por símbolo
_last_signal: dict[str, str] = {}


def compute_ema(data: list[float], period: int) -> list[float]:
    ema = [None] * len(data)
    k   = 2 / (period + 1)
    for i in range(len(data)):
        if i < period - 1:
            continue
        if i == period - 1:
            ema[i] = sum(data[i - period + 1 : i + 1]) / period
        else:
            ema[i] = data[i] * k + ema[i - 1] * (1 - k)
    return ema


def compute_atr(highs, lows, closes, period=14) -> list[float]:
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)
    # Wilder smoothing
    atr = [None] * (len(closes) - 1)
    if len(trs) >= period:
        atr[period - 1] = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period
    return atr


def compute_bb(closes: list[float], period=20, mult=2.0):
    """Retorna (upper, mid, lower) para la última vela."""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid    = sum(window) / period
    std    = (sum((x - mid) ** 2 for x in window) / period) ** 0.5
    return mid + mult * std, mid, mid - mult * std


def compute_vwap(highs, lows, closes, volumes) -> float:
    """VWAP de toda la sesión cargada."""
    typical = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    tp_vol  = sum(t * v for t, v in zip(typical, volumes))
    tot_vol = sum(volumes)
    return tp_vol / tot_vol if tot_vol > 0 else closes[-1]


def detect_regime(atr_values: list, period: int = 14) -> tuple[str, float]:
    """
    Detecta el régimen de mercado basado en ATR relativo.
    Retorna (régimen, dgrp_score).
    """
    valid = [x for x in atr_values if x is not None]
    if len(valid) < period * 2:
        return "FLIP ZONE", 50

    recent_atr = valid[-1]
    avg_atr    = sum(valid[-period * 2 :]) / (period * 2)
    ratio      = recent_atr / avg_atr if avg_atr > 0 else 1.0

    # DGRP Score: 0–100, cuanto mayor más "NEGATIVE GAMMA" (tendencial)
    dgrp = min(100, max(0, int((ratio - 0.5) / 1.5 * 100)))

    if ratio < 0.75:
        return "POSITIVE GAMMA", dgrp    # Rango, bajo ATR
    elif ratio > 1.25:
        return "NEGATIVE GAMMA", dgrp    # Tendencial, alto ATR
    else:
        return "FLIP ZONE", dgrp


def analyze_symbol(symbol: str, timeframe: str) -> dict | None:
    """
    Analiza un símbolo y retorna un dict con la señal o None si no hay señal.

    Lógica GEX aproximada:
    ─────────────────────
    • compression_break  : BB squeeze + ruptura
    • gex_flip_cross     : EMA9 cruza EMA21 cuando ATR sale de FLIP ZONE
    • wall_break         : Precio cruza VWAP con momentum
    • vanna_unwind       : Rayo de volatilidad inverso al régimen previo
    """
    try:
        ohlcv  = exchange.fetch_ohlcv(symbol, timeframe, limit=100)
    except Exception as e:
        log.warning(f"fetch_ohlcv {symbol}: {e}")
        return None

    if len(ohlcv) < 50:
        return None

    opens   = [x[1] for x in ohlcv]
    highs   = [x[2] for x in ohlcv]
    lows    = [x[3] for x in ohlcv]
    closes  = [x[4] for x in ohlcv]
    volumes = [x[5] for x in ohlcv]
    price   = closes[-1]

    ema9  = compute_ema(closes, 9)
    ema21 = compute_ema(closes, 21)
    atr   = compute_atr(highs, lows, closes, 14)
    bb_upper, bb_mid, bb_lower = compute_bb(closes, 20, 2.0)
    vwap  = compute_vwap(highs, lows, closes, volumes)

    regime, dgrp = detect_regime(atr)

    # ── Valores anteriores y actuales ──
    e9_prev  = ema9[-2]  if ema9[-2]  is not None else None
    e9_curr  = ema9[-1]  if ema9[-1]  is not None else None
    e21_prev = ema21[-2] if ema21[-2] is not None else None
    e21_curr = ema21[-1] if ema21[-1] is not None else None

    close_prev = closes[-2]
    close_curr = closes[-1]

    signal = None
    signal_type = None

    # ── 1. Compression Break (BB squeeze + breakout) ──
    if bb_upper and bb_lower and bb_mid:
        bb_width     = (bb_upper - bb_lower) / bb_mid
        bb_upper2, bb_mid2, bb_lower2 = compute_bb(closes[:-1], 20, 2.0)
        bb_width_prev = ((bb_upper2 - bb_lower2) / bb_mid2) if bb_upper2 else bb_width

        squeeze = bb_width < 0.025  # Banda muy estrecha
        if squeeze:
            if close_curr > bb_upper:
                signal = "compression_break_long"
                signal_type = "Compresión BB → ruptura alcista"
            elif close_curr < bb_lower:
                signal = "compression_break_short"
                signal_type = "Compresión BB → ruptura bajista"

    # ── 2. GEX Flip Cross (EMA9 cruza EMA21, fuera de FLIP ZONE) ──
    if signal is None and all(v is not None for v in [e9_prev, e9_curr, e21_prev, e21_curr]):
        cross_up   = e9_prev <= e21_prev and e9_curr > e21_curr
        cross_down = e9_prev >= e21_prev and e9_curr < e21_curr

        if cross_up and regime != "FLIP ZONE":
            signal = "gex_flip_cross_long"
            signal_type = "GEX Flip Cross alcista (EMA9 > EMA21)"
        elif cross_down and regime != "FLIP ZONE":
            signal = "gex_flip_cross_short"
            signal_type = "GEX Flip Cross bajista (EMA9 < EMA21)"

    # ── 3. Wall Break (cruza VWAP con cierre limpio) ──
    if signal is None:
        vwap_cross_up   = close_prev < vwap and close_curr > vwap * 1.001
        vwap_cross_down = close_prev > vwap and close_curr < vwap * 0.999

        if vwap_cross_up and regime in ("POSITIVE GAMMA", "FLIP ZONE"):
            signal = "wall_break_long"
            signal_type = f"Wall Break alcista VWAP @ {vwap:.4f}"
        elif vwap_cross_down and regime in ("POSITIVE GAMMA", "FLIP ZONE"):
            signal = "wall_break_short"
            signal_type = f"Wall Break bajista VWAP @ {vwap:.4f}"

    # ── 4. Vanna Unwind (reversión rápida en NEGATIVE GAMMA) ──
    if signal is None and regime == "NEGATIVE GAMMA":
        # Vela envolvente inversa al trend reciente
        trend_up = closes[-3] < closes[-2]  # vela previa era alcista

        big_candle = abs(close_curr - opens[-1]) > (atr[-1] * 1.5 if atr[-1] else 0)
        if big_candle:
            if trend_up and close_curr < opens[-1]:
                signal = "vanna_unwind_short"
                signal_type = "Vanna Unwind bajista (Neg.Gamma reversal)"
            elif not trend_up and close_curr > opens[-1]:
                signal = "vanna_unwind_long"
                signal_type = "Vanna Unwind alcista (Neg.Gamma reversal)"

    if signal is None:
        return None

    return {
        "signal":     signal,
        "signal_type": signal_type,
        "ticker":     symbol,
        "price":      f"{price:.4f}",
        "regime":     regime,
        "dgrp_score": dgrp,
        "gex_flip":   f"{vwap:.4f}",
    }


def scanner_loop():
    """Hilo de escaneo autónomo."""
    log.info(f"🔍 Scanner iniciado | Símbolos: {SCAN_SYMBOLS} | TF: {SCAN_TIMEFRAME} | Intervalo: {SCAN_INTERVAL}s")
    send_telegram(
        f"🔍 <b>Scanner autónomo iniciado</b>\n"
        f"📋 Símbolos: <code>{', '.join(SCAN_SYMBOLS)}</code>\n"
        f"⏱ Timeframe: <b>{SCAN_TIMEFRAME}</b> | Intervalo: <b>{SCAN_INTERVAL}s</b>"
    )

    scan_count = 0
    while True:
        scan_count += 1
        log.info(f"─── SCAN #{scan_count} ───")
        signals_found = 0

        for symbol in SCAN_SYMBOLS:
            symbol = symbol.strip()
            try:
                result = analyze_symbol(symbol, SCAN_TIMEFRAME)
            except Exception as e:
                log.error(f"Error analizando {symbol}: {e}")
                continue

            if result is None:
                log.info(f"  {symbol}: sin señal")
                continue

            signal = result["signal"]

            # Evitar señal duplicada consecutiva en el mismo símbolo
            if _last_signal.get(symbol) == signal:
                log.info(f"  {symbol}: señal repetida ({signal}), ignorando")
                continue

            _last_signal[symbol] = signal
            signals_found += 1

            log.info(f"  ✅ SEÑAL: {symbol} → {signal} | {result.get('signal_type')}")
            process_signal(result, source="Scanner")

            time.sleep(0.5)  # pausa entre órdenes si hay múltiples símbolos

        log.info(f"─── FIN SCAN #{scan_count} | Señales: {signals_found} ───")
        time.sleep(SCAN_INTERVAL)


# ──────────────────────────────────────────────
# Rutas Flask
# ──────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":   "online",
        "bot":      "AEGIS GEX v2.0",
        "mode":     MODE,
        "scanner":  SCANNER_ENABLED,
        "symbols":  SCAN_SYMBOLS,
        "timeframe": SCAN_TIMEFRAME,
    }), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        if request.headers.get("X-Webhook-Secret", "") != WEBHOOK_SECRET:
            log.warning("Webhook secret inválido.")
            return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON recibido"}), 400

    log.info(f"Webhook recibido: {data}")
    result = process_signal(data, source="TradingView")

    if result.get("error"):
        return jsonify({"status": "error", "message": result["error"]}), 500
    return jsonify({"status": "success", "order": result.get("order")}), 200


@app.route("/status", methods=["GET"])
def status():
    try:
        if MODE != "live":
            return jsonify({"mode": "paper", "positions": [], "last_signals": _last_signal}), 200
        positions = exchange.fetch_positions()
        open_pos  = [p for p in positions if float(p.get("contracts", 0)) != 0]
        return jsonify({"mode": MODE, "positions": open_pos, "last_signals": _last_signal}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/scan", methods=["GET"])
def scan_now():
    """Ejecuta un escaneo inmediato y devuelve las señales encontradas."""
    found = []
    for symbol in SCAN_SYMBOLS:
        symbol = symbol.strip()
        try:
            result = analyze_symbol(symbol, SCAN_TIMEFRAME)
            if result:
                found.append(result)
        except Exception as e:
            found.append({"symbol": symbol, "error": str(e)})
    return jsonify({"scanned": len(SCAN_SYMBOLS), "signals": found}), 200


# ──────────────────────────────────────────────
# Arranque
# ──────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    send_telegram(
        f"🚀 <b>AEGIS GEX Bot v2.0 iniciado</b>\n"
        f"Modo: <b>{MODE.upper()}</b> | Leverage: <b>{LEVERAGE}x</b> | Size: <b>{ORDER_SIZE}</b>\n"
        f"Scanner: <b>{'ON' if SCANNER_ENABLED else 'OFF'}</b>"
    )

    if SCANNER_ENABLED:
        t = threading.Thread(target=scanner_loop, daemon=True)
        t.start()

    app.run(host="0.0.0.0", port=port)
