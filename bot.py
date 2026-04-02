"""
Bot de Scalping — Zero Lag + Trend Reversal Probability
Estrategia: https://youtube.com/@WhaleAnalytics
Deploy: Railway | Exchange: BingX Perpetual Futures

FIXES v2:
  FIX-1  sync_position() usa detected_side de bingx_client (Hedge mode correcto)
         Antes: amt>0 → "long", incorrecto si BingX tiene SHORT con amt>0
  FIX-2  ENTRY_MAX_PROB default cambiado de 0.30 → 0.65
         Con 0.30 el bot nunca entraba (prob siempre ~55%)
         0.65 significa "entrar si prob de reversión < 65%" — más razonable
  FIX-3  Telegram notificaciones añadidas en entradas, salidas y errores
         Requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en variables Railway
  FIX-4  handle_entry usa positionSide correcto (LONG/SHORT en Hedge mode)
         Detecta automáticamente si la cuenta es Hedge o One-Way
  FIX-5  Reporte de estado cada N ciclos vía Telegram
"""

import os
import sys
import time
import logging
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv

from bingx_client import BingXClient, BingXError
from strategy import calculate_signals

# ─────────────────────────── SETUP ───────────────────────────

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ──────────────────────── CONFIG ─────────────────────────────

def _env(key: str, default=None, cast=str):
    val = os.getenv(key, default)
    if val is None:
        raise EnvironmentError(f"Variable de entorno requerida no encontrada: {key}")
    return cast(val)


API_KEY        = _env("BINGX_API_KEY")
SECRET_KEY     = _env("BINGX_SECRET_KEY")
TELEGRAM_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = _env("TELEGRAM_CHAT_ID",   "")
SYMBOL         = _env("SYMBOL",         "BTC-USDT")
TIMEFRAME      = _env("TIMEFRAME",      "15m")
LEVERAGE       = _env("LEVERAGE",       "5",    int)
RISK_PCT       = _env("RISK_PCT",       "0.05", float)
ZLEMA_LENGTH   = _env("ZLEMA_LENGTH",   "70",   int)
BAND_MULT      = _env("BAND_MULT",      "1.2",  float)
OSC_PERIOD     = _env("OSC_PERIOD",     "20",   int)
# FIX-2: cambiado de 0.30 a 0.65 — con 0.30 nunca entraba (prob ~55% siempre)
ENTRY_MAX_PROB = _env("ENTRY_MAX_PROB", "0.65", float)
EXIT_PROB      = _env("EXIT_PROB",      "0.84", float)
CHECK_INTERVAL = _env("CHECK_INTERVAL", "60",   int)
DEMO_MODE      = _env("DEMO_MODE",      "false").lower() == "true"
MIN_BALANCE    = _env("MIN_BALANCE",    "10",   float)
# Modo de cuenta: "hedge" o "oneway" (detectado automáticamente si no se configura)
POSITION_MODE  = _env("POSITION_MODE",  "auto")    # auto | hedge | oneway
# Reporte Telegram cada N ciclos (0 = desactivado)
REPORT_EVERY   = _env("REPORT_EVERY",   "60",   int)

# ──────────────────────── STATE ──────────────────────────────

client = BingXClient(
    API_KEY, SECRET_KEY,
    demo=DEMO_MODE,
    telegram_token=TELEGRAM_TOKEN,
    telegram_chat=TELEGRAM_CHAT,
)

state = {
    "position":      None,   # "long" | "short" | None
    "entry_price":   None,
    "entry_qty":     None,
    "position_side": None,   # "LONG" | "SHORT" | "BOTH" — para cerrar correctamente
    "cycle":         0,
    "wins":          0,
    "losses":        0,
    "start_balance": None,
    "account_mode":  None,   # "hedge" | "oneway" — detectado al arrancar
    "total_pnl":     0.0,
}

# ─────────────────────── HELPERS ─────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def get_ohlcv() -> pd.DataFrame:
    klines = client.get_klines(SYMBOL, TIMEFRAME, limit=500)
    if not klines:
        raise RuntimeError("Sin datos de velas")
    df = pd.DataFrame(klines)
    if isinstance(klines[0], dict):
        df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                                 "c": "close", "v": "volume"})
    else:
        df.columns = ["open_time", "open", "high", "low", "close", "volume"] + list(
            range(len(df.columns) - 6)
        )
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df.reset_index(drop=True)


def calculate_qty(balance: float, price: float) -> float:
    usdt_exposure = balance * RISK_PCT * LEVERAGE
    qty = usdt_exposure / price
    qty = round(qty, 3)
    return max(qty, 0.001)


def detect_account_mode() -> str:
    """
    FIX-4: Detecta automáticamente si la cuenta está en Hedge o One-Way mode.
    En Hedge mode, BingX devuelve posiciones con positionSide=LONG o SHORT.
    En One-Way mode, devuelve positionSide=BOTH.
    """
    if POSITION_MODE.lower() in ("hedge", "oneway"):
        logger.info(f"Modo de cuenta configurado manualmente: {POSITION_MODE}")
        return POSITION_MODE.lower()
    # Intentar detectar mirando una posición de prueba o el campo de las posiciones
    try:
        positions = client.get_positions(SYMBOL)
        for pos in positions:
            side = str(pos.get("positionSide", "")).upper()
            if side in ("LONG", "SHORT"):
                logger.info("Cuenta en modo HEDGE detectado")
                return "hedge"
            elif side == "BOTH":
                logger.info("Cuenta en modo ONE-WAY detectado")
                return "oneway"
        # Sin posiciones abiertas — asumir hedge (BingX default en nuevas cuentas)
        logger.info("Sin posiciones para detectar modo — asumiendo HEDGE")
        return "hedge"
    except Exception as e:
        logger.warning(f"No se pudo detectar modo: {e} — asumiendo HEDGE")
        return "hedge"


def sync_position():
    """
    FIX-1: Usa detected_side de get_open_position() para detectar LONG/SHORT
    correctamente en Hedge mode.
    Antes: state["position"] = "long" if amt > 0 else "short"
    → incorrecto en Hedge mode donde un SHORT puede tener amt > 0
    """
    pos = client.get_open_position(SYMBOL)
    if pos is None:
        state["position"]      = None
        state["entry_price"]   = None
        state["entry_qty"]     = None
        state["position_side"] = None
    else:
        # FIX-1: usar detected_side calculado en bingx_client.py
        state["position"]      = pos.get("detected_side", "long")
        state["entry_price"]   = float(pos.get("avgPrice", 0))
        state["entry_qty"]     = abs(float(pos.get("positionAmt", 0)))
        # Guardar positionSide real para el cierre correcto
        state["position_side"] = str(pos.get("positionSide", "BOTH")).upper()


def log_banner():
    mode = "DEMO" if DEMO_MODE else "REAL MONEY"
    logger.info("=" * 60)
    logger.info(f"  Zero Lag Scalping Bot v2  |  {mode}")
    logger.info(f"  {SYMBOL} | {TIMEFRAME} | {LEVERAGE}x leverage")
    logger.info(f"  Riesgo: {RISK_PCT:.0%}/op | Entrada: prob<{ENTRY_MAX_PROB:.0%} | Salida: prob>={EXIT_PROB:.0%}")
    logger.info(f"  ZLEMA:{ZLEMA_LENGTH} | BandMult:{BAND_MULT} | OscPeriod:{OSC_PERIOD}")
    logger.info(f"  Telegram: {'ON' if TELEGRAM_TOKEN and TELEGRAM_CHAT else 'OFF'}")
    logger.info(f"  Modo cuenta: {state.get('account_mode', 'detectando...')}")
    logger.info("=" * 60)


def log_state(signals: dict, balance: float):
    trend_str = "ALCISTA" if signals["trend"] == 1 else "BAJISTA"
    pos_str = state["position"] or "Sin posicion"
    prob = signals["probability"]

    logger.info(
        f"[{now_utc()}] #{state['cycle']} | "
        f"${signals['close']:.4f} | "
        f"Tendencia: {trend_str} | "
        f"Prob: {prob:.1%} | "
        f"Pos: {pos_str} | "
        f"Balance: {balance:.2f} USDT"
    )


# ─────────────────────── CORE LOGIC ──────────────────────────

def handle_exit(signals: dict, reason: str):
    if state["position"] is None:
        return

    entry   = state["entry_price"] or 0
    current = signals["close"]
    pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
    if state["position"] == "short":
        pnl_pct = -pnl_pct

    # Estimar PnL en USDT
    qty     = state["entry_qty"] or 0
    pnl_usd = qty * (current - entry) * (1 if state["position"] == "long" else -1)
    state["total_pnl"] += pnl_usd

    logger.info(f"CERRANDO {state['position'].upper()} | {reason} | PnL: {pnl_pct:+.2f}%")

    # FIX-3: Notificación Telegram al cerrar
    emoji = "✅" if pnl_pct > 0 else "❌"
    client.send_telegram(
        f"<b>{emoji} CERRADO {state['position'].upper()} — {reason}</b>\n"
        f"Par: {SYMBOL} | TF: {TIMEFRAME}\n"
        f"Entrada: ${entry:.4f} | Salida: ${current:.4f}\n"
        f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+.4f} USDT)\n"
        f"PnL total sesión: ${state['total_pnl']:+.4f} USDT"
    )

    client.close_all_positions(SYMBOL)

    if pnl_pct > 0:
        state["wins"] += 1
    else:
        state["losses"] += 1

    state["position"]      = None
    state["entry_price"]   = None
    state["entry_qty"]     = None
    state["position_side"] = None
    time.sleep(2)


def handle_entry(signals: dict, direction: str, balance: float):
    price = signals["close"]
    qty   = calculate_qty(balance, price)

    if qty <= 0:
        logger.warning("Cantidad calculada es 0, operacion omitida")
        return

    side = "BUY" if direction == "long" else "SELL"

    # FIX-4: usar positionSide correcto según modo de cuenta
    account_mode = state.get("account_mode", "hedge")
    if account_mode == "hedge":
        pos_side = "LONG" if direction == "long" else "SHORT"
    else:
        pos_side = "BOTH"

    logger.info(
        f"ABRIENDO {direction.upper()} | "
        f"Precio: ${price:.4f} | Qty: {qty} | "
        f"Prob: {signals['probability']:.1%} | positionSide: {pos_side}"
    )

    try:
        client.set_leverage(SYMBOL, LEVERAGE)
        client.place_market_order(SYMBOL, side, qty, position_side=pos_side)
        state["position"]      = direction
        state["entry_price"]   = price
        state["entry_qty"]     = qty
        state["position_side"] = pos_side

        # FIX-3: Notificación Telegram al abrir
        emoji = "🟢" if direction == "long" else "🔴"
        trend_str = "ALCISTA" if signals["trend"] == 1 else "BAJISTA"
        client.send_telegram(
            f"<b>{emoji} ABIERTO {direction.upper()}</b>\n"
            f"Par: {SYMBOL} | TF: {TIMEFRAME} | {LEVERAGE}x\n"
            f"Precio entrada: ${price:.4f}\n"
            f"Cantidad: {qty} | positionSide: {pos_side}\n"
            f"Tendencia: {trend_str} | Prob reversión: {signals['probability']:.1%}\n"
            f"Balance: ${balance:.2f} USDT"
        )

    except BingXError as e:
        logger.error(f"Error al abrir orden: {e}")
        # FIX-3: Notificar error por Telegram
        client.send_telegram(f"<b>Error abriendo {direction.upper()} {SYMBOL}</b>\n{e}")


def send_status_report(signals: dict, balance: float):
    """FIX-3: Reporte periódico de estado vía Telegram."""
    total = state["wins"] + state["losses"]
    wr = state["wins"] / total * 100 if total > 0 else 0
    pos_str = f"{state['position'].upper()} @ ${state['entry_price']:.4f}" \
              if state["position"] else "Sin posicion"
    trend_str = "ALCISTA" if signals["trend"] == 1 else "BAJISTA"

    client.send_telegram(
        f"<b>Reporte #{state['cycle']} — {SYMBOL}</b>\n"
        f"Balance: ${balance:.2f} USDT\n"
        f"Posicion: {pos_str}\n"
        f"Tendencia: {trend_str} | Prob: {signals['probability']:.1%}\n"
        f"Trades: {total} | WR: {wr:.1f}% ({state['wins']}W/{state['losses']}L)\n"
        f"PnL sesion: ${state['total_pnl']:+.4f} USDT"
    )


def run_cycle():
    state["cycle"] += 1

    # 1. Sincronizar posición
    try:
        sync_position()
    except BingXError as e:
        logger.error(f"No se pudo sincronizar posicion: {e}")
        return

    # 2. Velas y señales
    try:
        df = get_ohlcv()
        signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
    except Exception as e:
        logger.error(f"Error calculando senales: {e}")
        return

    # 3. Balance
    try:
        balance = client.get_balance()
    except BingXError as e:
        logger.error(f"Error obteniendo balance: {e}")
        return

    log_state(signals, balance)

    # FIX-3: Reporte periódico
    if REPORT_EVERY > 0 and state["cycle"] % REPORT_EVERY == 0:
        send_status_report(signals, balance)

    trend   = signals["trend"]
    prob    = signals["probability"]
    bull_in = signals["bullish_entry"]
    bear_in = signals["bearish_entry"]

    # 4. LÓGICA DE SALIDA
    if state["position"] is not None:
        if prob >= EXIT_PROB:
            handle_exit(signals, f"Prob reversion {prob:.1%} >= {EXIT_PROB:.1%}")
            return
        if state["position"] == "long" and trend == -1:
            handle_exit(signals, "Tendencia viro a bajista")
            return
        if state["position"] == "short" and trend == 1:
            handle_exit(signals, "Tendencia viro a alcista")
            return

    # 5. LÓGICA DE ENTRADA
    if state["position"] is None:
        if balance < MIN_BALANCE:
            logger.warning(f"Balance insuficiente: {balance:.2f} < {MIN_BALANCE} USDT")
            return

        if prob >= ENTRY_MAX_PROB:
            logger.info(
                f"Esperando: prob {prob:.1%} >= {ENTRY_MAX_PROB:.1%} "
                f"(necesita ser MENOR para entrar)"
            )
            return

        if bull_in:
            handle_entry(signals, "long", balance)
        elif bear_in:
            handle_entry(signals, "short", balance)
        else:
            logger.info("Sin senal de entrada en esta barra")


# ─────────────────────── MAIN LOOP ───────────────────────────

def main():
    # Detectar modo de cuenta antes de arrancar
    state["account_mode"] = detect_account_mode()

    log_banner()

    if DEMO_MODE:
        logger.warning("MODO DEMO ACTIVADO — No se usa dinero real")
    else:
        logger.warning("MODO REAL — Se utilizara dinero real en BingX")
        time.sleep(3)

    try:
        state["start_balance"] = client.get_balance()
        logger.info(f"Balance inicial: ${state['start_balance']:.2f} USDT")
    except BingXError as e:
        logger.error(f"No se pudo conectar con BingX: {e}")
        sys.exit(1)

    # FIX-3: Notificación de arranque
    client.send_telegram(
        f"<b>Bot Zero Lag iniciado</b>\n"
        f"Par: {SYMBOL} | TF: {TIMEFRAME} | {LEVERAGE}x\n"
        f"Modo: {'DEMO' if DEMO_MODE else 'REAL'} | Cuenta: {state['account_mode'].upper()}\n"
        f"Entrada: prob<{ENTRY_MAX_PROB:.0%} | Salida: prob>={EXIT_PROB:.0%}\n"
        f"Balance: ${state['start_balance']:.2f} USDT\n"
        f"Telegram: OK"
    )

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            logger.info("Bot detenido por el usuario")
            total = state["wins"] + state["losses"]
            wr = state["wins"] / total * 100 if total > 0 else 0
            try:
                final_balance = client.get_balance()
                pnl = final_balance - state["start_balance"]
                logger.info(f"Trades: {total} | WR: {wr:.1f}%")
                logger.info(f"Balance final: ${final_balance:.2f} (PnL: ${pnl:+.2f})")
                client.send_telegram(
                    f"<b>Bot detenido</b>\n"
                    f"Trades: {total} | WR: {wr:.1f}%\n"
                    f"Balance final: ${final_balance:.2f} USDT\n"
                    f"PnL sesion: ${pnl:+.2f} USDT"
                )
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            logger.error(f"Error inesperado: {e}", exc_info=True)
            client.send_telegram(f"<b>Error inesperado en bot {SYMBOL}</b>\n{e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
