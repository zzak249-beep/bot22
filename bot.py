"""
🤖 Bot de Scalping — Zero Lag + Trend Reversal Probability
Estrategia: https://youtube.com/@WhaleAnalytics
Deploy: Railway | Exchange: BingX Perpetual Futures
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
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ──────────────────────── CONFIG ─────────────────────────────

def _env(key: str, default=None, cast=str):
    val = os.getenv(key, default)
    if val is None:
        raise EnvironmentError(f"Variable de entorno requerida no encontrada: {key}")
    return cast(val)


API_KEY       = _env("BINGX_API_KEY")
SECRET_KEY    = _env("BINGX_SECRET_KEY")
SYMBOL        = _env("SYMBOL",        "BTC-USDT")
TIMEFRAME     = _env("TIMEFRAME",     "15m")
LEVERAGE      = _env("LEVERAGE",      "5",    int)
RISK_PCT      = _env("RISK_PCT",      "0.05", float)   # % del balance por operación
ZLEMA_LENGTH  = _env("ZLEMA_LENGTH",  "70",   int)
BAND_MULT     = _env("BAND_MULT",     "1.2",  float)
OSC_PERIOD    = _env("OSC_PERIOD",    "20",   int)      # Pine Script default: 20 | Vídeo recomienda: 50
ENTRY_MAX_PROB= _env("ENTRY_MAX_PROB","0.30", float)   # Entrar solo si prob < 30%
EXIT_PROB     = _env("EXIT_PROB",     "0.84", float)   # Salir cuando prob ≥ 84%
CHECK_INTERVAL= _env("CHECK_INTERVAL","60",   int)      # Segundos entre ciclos
DEMO_MODE     = _env("DEMO_MODE",     "false").lower() == "true"
MIN_BALANCE   = _env("MIN_BALANCE",   "10",   float)   # USDT mínimos para operar

# ──────────────────────── STATE ──────────────────────────────

client = BingXClient(API_KEY, SECRET_KEY, demo=DEMO_MODE)

# Estado del bot (en memoria; se sincroniza con la API al inicio de cada ciclo)
state = {
    "position":     None,   # "long" | "short" | None
    "entry_price":  None,
    "entry_qty":    None,
    "cycle":        0,
    "wins":         0,
    "losses":       0,
    "start_balance": None,
}

# ─────────────────────── HELPERS ─────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def get_ohlcv() -> pd.DataFrame:
    klines = client.get_klines(SYMBOL, TIMEFRAME, limit=500)
    if not klines:
        raise RuntimeError("Sin datos de velas")
    # BingX devuelve: [openTime, open, high, low, close, volume, ...]
    df = pd.DataFrame(klines)
    # Renombrar columnas según respuesta de BingX
    if isinstance(klines[0], dict):
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    else:
        df.columns = ["open_time", "open", "high", "low", "close", "volume"] + list(
            range(len(df.columns) - 6)
        )
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df.reset_index(drop=True)


def calculate_qty(balance: float, price: float) -> float:
    """Calcula cantidad a comprar según riesgo y apalancamiento."""
    usdt_exposure = balance * RISK_PCT * LEVERAGE
    qty = usdt_exposure / price
    # Redondear a 3 decimales (la mayoría de pares BTC lo soportan)
    qty = round(qty, 3)
    return max(qty, 0.001)


def sync_position():
    """Sincroniza el estado local con la posición real en BingX."""
    pos = client.get_open_position(SYMBOL)
    if pos is None:
        state["position"] = None
        state["entry_price"] = None
        state["entry_qty"] = None
    else:
        amt = float(pos.get("positionAmt", 0))
        state["position"] = "long" if amt > 0 else "short"
        state["entry_price"] = float(pos.get("avgPrice", 0))
        state["entry_qty"] = abs(amt)


def log_banner():
    mode = "🟡 DEMO" if DEMO_MODE else "🔴 REAL MONEY"
    logger.info("=" * 60)
    logger.info(f"  🤖 Zero Lag Scalping Bot  |  {mode}")
    logger.info(f"  📊 {SYMBOL} | {TIMEFRAME} | {LEVERAGE}x leverage")
    logger.info(f"  🎯 Riesgo: {RISK_PCT:.0%}/op | Entrada: prob<{ENTRY_MAX_PROB:.0%} | Salida: prob≥{EXIT_PROB:.0%}")
    logger.info(f"  ⚙️  ZLEMA:{ZLEMA_LENGTH} | BandMult:{BAND_MULT} | OscPeriod:{OSC_PERIOD}")
    logger.info("=" * 60)


def log_state(signals: dict, balance: float):
    trend_str = "🟢 ALCISTA" if signals["trend"] == 1 else "🔴 BAJISTA"
    pos_str = state["position"] or "Sin posición"
    prob = signals["probability"]
    prob_emoji = "🔥" if prob >= EXIT_PROB else ("⚠️" if prob >= 0.6 else "✅")

    logger.info(
        f"[{now_utc()}] #{state['cycle']} | "
        f"💵 {signals['close']:.4f} | "
        f"Tendencia: {trend_str} | "
        f"{prob_emoji} Prob: {prob:.1%} | "
        f"Pos: {pos_str} | "
        f"Balance: {balance:.2f} USDT"
    )


# ─────────────────────── CORE LOGIC ──────────────────────────

def handle_exit(signals: dict, reason: str):
    """Cierra la posición activa."""
    if state["position"] is None:
        return

    entry = state["entry_price"] or 0
    current = signals["close"]
    pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
    if state["position"] == "short":
        pnl_pct = -pnl_pct

    logger.info(f"🚪 CERRANDO {state['position'].upper()} | Razón: {reason} | PnL: {pnl_pct:+.2f}%")
    client.close_all_positions(SYMBOL)

    if pnl_pct > 0:
        state["wins"] += 1
    else:
        state["losses"] += 1

    state["position"] = None
    state["entry_price"] = None
    state["entry_qty"] = None
    time.sleep(2)  # Pequeña pausa para que la orden se ejecute


def handle_entry(signals: dict, direction: str, balance: float):
    """Abre una nueva posición."""
    price = signals["close"]
    qty = calculate_qty(balance, price)

    if qty <= 0:
        logger.warning("⚠️ Cantidad calculada es 0, operación omitida")
        return

    side = "BUY" if direction == "long" else "SELL"
    emoji = "🟢" if direction == "long" else "🔴"
    logger.info(
        f"{emoji} ABRIENDO {direction.upper()} | "
        f"Precio: {price:.4f} | Qty: {qty} | "
        f"Prob: {signals['probability']:.1%}"
    )

    try:
        client.set_leverage(SYMBOL, LEVERAGE)
        client.place_market_order(SYMBOL, side, qty)
        state["position"] = direction
        state["entry_price"] = price
        state["entry_qty"] = qty
    except BingXError as e:
        logger.error(f"❌ Error al abrir orden: {e}")


def run_cycle():
    """Un ciclo completo del bot."""
    state["cycle"] += 1

    # 1. Sincronizar posición con el exchange
    try:
        sync_position()
    except BingXError as e:
        logger.error(f"No se pudo sincronizar posición: {e}")
        return

    # 2. Obtener velas y calcular señales
    try:
        df = get_ohlcv()
        signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
    except Exception as e:
        logger.error(f"Error calculando señales: {e}")
        return

    # 3. Balance
    try:
        balance = client.get_balance()
    except BingXError as e:
        logger.error(f"Error obteniendo balance: {e}")
        return

    log_state(signals, balance)

    trend     = signals["trend"]
    prob      = signals["probability"]
    bull_in   = signals["bullish_entry"]
    bear_in   = signals["bearish_entry"]

    # 4. LÓGICA DE SALIDA ─────────────────────────────────────
    if state["position"] is not None:

        # Salida por alta probabilidad de reversión
        if prob >= EXIT_PROB:
            handle_exit(signals, f"Prob reversión {prob:.1%} ≥ {EXIT_PROB:.1%}")
            return

        # Salida por cambio de tendencia en contra
        if state["position"] == "long" and trend == -1:
            handle_exit(signals, "Tendencia viró a bajista")
            return

        if state["position"] == "short" and trend == 1:
            handle_exit(signals, "Tendencia viró a alcista")
            return

    # 5. LÓGICA DE ENTRADA ────────────────────────────────────
    if state["position"] is None:

        if balance < MIN_BALANCE:
            logger.warning(f"⚠️ Balance insuficiente: {balance:.2f} USDT < {MIN_BALANCE} USDT mínimo")
            return

        if prob >= ENTRY_MAX_PROB:
            logger.info(f"⏳ Esperando: prob {prob:.1%} demasiado alta para entrar (máx {ENTRY_MAX_PROB:.1%})")
            return

        if bull_in:
            handle_entry(signals, "long", balance)

        elif bear_in:
            handle_entry(signals, "short", balance)

        else:
            logger.info("⏳ Sin señal de entrada en esta barra")


# ─────────────────────── MAIN LOOP ───────────────────────────

def main():
    log_banner()

    if DEMO_MODE:
        logger.warning("🟡 MODO DEMO ACTIVADO — No se usa dinero real")
    else:
        logger.warning("🔴 MODO REAL — Se utilizará dinero real en BingX")
        logger.warning("   Asegúrate de haber configurado correctamente RISK_PCT y LEVERAGE")
        time.sleep(5)  # Pausa para que el usuario pueda cancelar si hay un error

    # Balance inicial
    try:
        state["start_balance"] = client.get_balance()
        logger.info(f"💰 Balance inicial: {state['start_balance']:.2f} USDT")
    except BingXError as e:
        logger.error(f"No se pudo conectar con BingX: {e}")
        logger.error("Verifica BINGX_API_KEY y BINGX_SECRET_KEY en tu .env")
        sys.exit(1)

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            logger.info("\n🛑 Bot detenido por el usuario")
            # Mostrar resumen final
            total = state["wins"] + state["losses"]
            wr = state["wins"] / total * 100 if total > 0 else 0
            try:
                final_balance = client.get_balance()
                pnl = final_balance - state["start_balance"]
                logger.info(f"📊 Operaciones: {total} | Ganadoras: {state['wins']} | Win rate: {wr:.1f}%")
                logger.info(f"💰 Balance final: {final_balance:.2f} USDT (PnL: {pnl:+.2f} USDT)")
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            logger.error(f"❌ Error inesperado: {e}", exc_info=True)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
