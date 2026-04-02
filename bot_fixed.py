"""
Bot de Scalping Multi-Moneda — Zero Lag + Trend Reversal Probability
VERSIÓN CORREGIDA v3 - Fixes de API + Multi-Símbolo

CORRECCIONES v3:
  FIX-6  Corrección de parámetros de orden BingX (error 109400)
         - Eliminado positionSide de place_market_order (no es necesario en algunas configs)
         - Usar close_position individual en lugar de close_all_positions
  FIX-7  Soporte para múltiples pares de trading
         - Scanner que evalúa múltiples símbolos en paralelo
         - Gestión independiente de posiciones por símbolo
  FIX-8  Validación de cantidad mínima por símbolo
         - Consulta info del símbolo antes de operar
  FIX-9  Mejores mensajes de error con detalles completos
"""

import os
import sys
import time
import logging
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
from typing import Dict, List, Optional

from bingx_client_fixed import BingXClient, BingXError
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

# FIX-7: Soporte para múltiples símbolos (separados por coma)
SYMBOLS_STR    = _env("SYMBOLS", "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT")
SYMBOLS        = [s.strip() for s in SYMBOLS_STR.split(",")]

TIMEFRAME      = _env("TIMEFRAME",      "15m")
LEVERAGE       = _env("LEVERAGE",       "5",    int)
RISK_PCT       = _env("RISK_PCT",       "0.05", float)
ZLEMA_LENGTH   = _env("ZLEMA_LENGTH",   "70",   int)
BAND_MULT      = _env("BAND_MULT",      "1.2",  float)
OSC_PERIOD     = _env("OSC_PERIOD",     "20",   int)
ENTRY_MAX_PROB = _env("ENTRY_MAX_PROB", "0.65", float)
EXIT_PROB      = _env("EXIT_PROB",      "0.84", float)
CHECK_INTERVAL = _env("CHECK_INTERVAL", "60",   int)
DEMO_MODE      = _env("DEMO_MODE",      "false").lower() == "true"
MIN_BALANCE    = _env("MIN_BALANCE",    "10",   float)
POSITION_MODE  = _env("POSITION_MODE",  "auto")
REPORT_EVERY   = _env("REPORT_EVERY",   "60",   int)
MAX_POSITIONS  = _env("MAX_POSITIONS",  "3",    int)  # Máximo de posiciones simultáneas

# ──────────────────────── STATE ──────────────────────────────

client = BingXClient(
    API_KEY, SECRET_KEY,
    demo=DEMO_MODE,
    telegram_token=TELEGRAM_TOKEN,
    telegram_chat=TELEGRAM_CHAT,
)

# FIX-7: State ahora es por símbolo
positions_state: Dict[str, dict] = {}  # symbol -> state dict
global_state = {
    "cycle": 0,
    "total_wins": 0,
    "total_losses": 0,
    "start_balance": None,
    "account_mode": None,
    "total_pnl": 0.0,
}

# ─────────────────────── HELPERS ─────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def get_ohlcv(symbol: str) -> pd.DataFrame:
    """Obtener velas para un símbolo"""
    try:
        klines = client.get_klines(symbol, TIMEFRAME, limit=500)
        if not klines:
            logger.warning(f"{symbol}: Sin datos de velas")
            return None
        
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
    
    except Exception as e:
        logger.error(f"{symbol}: Error obteniendo velas: {e}")
        return None


def calculate_qty(symbol: str, balance: float, price: float) -> float:
    """FIX-8: Calcular cantidad respetando límites del símbolo"""
    try:
        # Obtener info del símbolo
        symbol_info = client.get_symbol_info(symbol)
        if not symbol_info:
            logger.warning(f"{symbol}: No se pudo obtener info del símbolo")
            return 0
        
        min_qty = symbol_info.get("minQty", 0.001)
        qty_step = symbol_info.get("qtyStep", 0.001)
        
        # Calcular cantidad base
        usdt_exposure = balance * RISK_PCT * LEVERAGE
        qty = usdt_exposure / price
        
        # Redondear al step más cercano
        qty = round(qty / qty_step) * qty_step
        
        # Asegurar mínimo
        if qty < min_qty:
            logger.warning(f"{symbol}: Cantidad {qty} menor que mínimo {min_qty}")
            return 0
        
        return qty
    
    except Exception as e:
        logger.error(f"{symbol}: Error calculando cantidad: {e}")
        return 0


def detect_account_mode() -> str:
    """Detectar modo de cuenta (Hedge/One-Way)"""
    if POSITION_MODE.lower() in ("hedge", "oneway"):
        logger.info(f"Modo de cuenta configurado manualmente: {POSITION_MODE}")
        return POSITION_MODE.lower()
    
    try:
        # Intentar con primer símbolo
        positions = client.get_positions(SYMBOLS[0])
        for pos in positions:
            side = str(pos.get("positionSide", "")).upper()
            if side in ("LONG", "SHORT"):
                logger.info("Cuenta en modo HEDGE detectado")
                return "hedge"
            elif side == "BOTH":
                logger.info("Cuenta en modo ONE-WAY detectado")
                return "oneway"
        
        logger.info("Sin posiciones para detectar modo — asumiendo HEDGE")
        return "hedge"
    
    except Exception as e:
        logger.warning(f"No se pudo detectar modo: {e} — asumiendo HEDGE")
        return "hedge"


def init_symbol_state(symbol: str):
    """Inicializar state para un símbolo"""
    if symbol not in positions_state:
        positions_state[symbol] = {
            "position": None,
            "entry_price": None,
            "entry_qty": None,
            "position_side": None,
            "wins": 0,
            "losses": 0,
            "pnl": 0.0,
        }


def sync_position(symbol: str):
    """Sincronizar posición de un símbolo"""
    init_symbol_state(symbol)
    state = positions_state[symbol]
    
    try:
        pos = client.get_open_position(symbol)
        if pos is None:
            state["position"] = None
            state["entry_price"] = None
            state["entry_qty"] = None
            state["position_side"] = None
        else:
            state["position"] = pos.get("detected_side", "long")
            state["entry_price"] = float(pos.get("avgPrice", 0))
            state["entry_qty"] = abs(float(pos.get("positionAmt", 0)))
            state["position_side"] = str(pos.get("positionSide", "BOTH")).upper()
    
    except Exception as e:
        logger.error(f"{symbol}: Error sincronizando posición: {e}")


def count_open_positions() -> int:
    """Contar cuántas posiciones están abiertas"""
    count = 0
    for state in positions_state.values():
        if state["position"] is not None:
            count += 1
    return count


# ─────────────────────── CORE LOGIC ──────────────────────────

def handle_exit(symbol: str, signals: dict, reason: str):
    """Cerrar posición de un símbolo"""
    state = positions_state[symbol]
    
    if state["position"] is None:
        return
    
    entry = state["entry_price"] or 0
    current = signals["close"]
    pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
    
    if state["position"] == "short":
        pnl_pct = -pnl_pct
    
    qty = state["entry_qty"] or 0
    pnl_usd = qty * (current - entry) * (1 if state["position"] == "long" else -1)
    
    state["pnl"] += pnl_usd
    global_state["total_pnl"] += pnl_usd
    
    logger.info(f"{symbol}: CERRANDO {state['position'].upper()} | {reason} | PnL: {pnl_pct:+.2f}%")
    
    emoji = "✅" if pnl_pct > 0 else "❌"
    client.send_telegram(
        f"<b>{emoji} CERRADO {state['position'].upper()} — {symbol}</b>\n"
        f"Razón: {reason}\n"
        f"TF: {TIMEFRAME} | Leverage: {LEVERAGE}x\n"
        f"Entrada: ${entry:.4f} | Salida: ${current:.4f}\n"
        f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+.4f} USDT)\n"
        f"PnL símbolo: ${state['pnl']:+.4f} | PnL total: ${global_state['total_pnl']:+.4f}"
    )
    
    # FIX-6: Cerrar posición específica en lugar de close_all_positions
    try:
        side = "SELL" if state["position"] == "long" else "BUY"
        client.close_position(symbol, side, qty)
    except BingXError as e:
        logger.error(f"{symbol}: Error cerrando posición: {e}")
        client.send_telegram(f"<b>⚠️ Error cerrando {symbol}</b>\n{e}")
    
    if pnl_pct > 0:
        state["wins"] += 1
        global_state["total_wins"] += 1
    else:
        state["losses"] += 1
        global_state["total_losses"] += 1
    
    state["position"] = None
    state["entry_price"] = None
    state["entry_qty"] = None
    state["position_side"] = None
    
    time.sleep(1)


def handle_entry(symbol: str, signals: dict, direction: str, balance: float):
    """Abrir posición en un símbolo"""
    state = positions_state[symbol]
    
    # Verificar límite de posiciones simultáneas
    open_count = count_open_positions()
    if open_count >= MAX_POSITIONS:
        logger.info(f"{symbol}: Máximo de posiciones alcanzado ({open_count}/{MAX_POSITIONS})")
        return
    
    price = signals["close"]
    qty = calculate_qty(symbol, balance, price)
    
    if qty <= 0:
        logger.warning(f"{symbol}: Cantidad calculada inválida: {qty}")
        return
    
    side = "BUY" if direction == "long" else "SELL"
    
    # FIX-6: No pasar positionSide si causa problemas
    logger.info(
        f"{symbol}: ABRIENDO {direction.upper()} | "
        f"Precio: ${price:.4f} | Qty: {qty} | "
        f"Prob: {signals['probability']:.1%}"
    )
    
    try:
        client.set_leverage(symbol, LEVERAGE)
        
        # FIX-6: Simplificar orden sin positionSide
        client.place_market_order(symbol, side, qty)
        
        state["position"] = direction
        state["entry_price"] = price
        state["entry_qty"] = qty
        
        emoji = "🟢" if direction == "long" else "🔴"
        trend_str = "ALCISTA" if signals["trend"] == 1 else "BAJISTA"
        
        client.send_telegram(
            f"<b>{emoji} ABIERTO {direction.upper()} — {symbol}</b>\n"
            f"TF: {TIMEFRAME} | Leverage: {LEVERAGE}x\n"
            f"Precio: ${price:.4f} | Cantidad: {qty}\n"
            f"Tendencia: {trend_str} | Prob: {signals['probability']:.1%}\n"
            f"Balance: ${balance:.2f} USDT\n"
            f"Posiciones abiertas: {count_open_positions()}/{MAX_POSITIONS}"
        )
        
    except BingXError as e:
        logger.error(f"{symbol}: Error abriendo orden: {e}")
        client.send_telegram(f"<b>⚠️ Error abriendo {direction.upper()} {symbol}</b>\n{e}")


def process_symbol(symbol: str, balance: float):
    """Procesar un símbolo individual"""
    init_symbol_state(symbol)
    state = positions_state[symbol]
    
    # 1. Sincronizar posición
    try:
        sync_position(symbol)
    except Exception as e:
        logger.error(f"{symbol}: Error sincronizando: {e}")
        return
    
    # 2. Obtener velas y señales
    df = get_ohlcv(symbol)
    if df is None or len(df) < ZLEMA_LENGTH + OSC_PERIOD:
        return
    
    try:
        signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
    except Exception as e:
        logger.error(f"{symbol}: Error calculando señales: {e}")
        return
    
    trend = signals["trend"]
    prob = signals["probability"]
    bull_in = signals["bullish_entry"]
    bear_in = signals["bearish_entry"]
    
    # Log state
    trend_str = "ALCISTA" if trend == 1 else "BAJISTA"
    pos_str = state["position"] or "—"
    logger.info(
        f"{symbol}: ${signals['close']:.4f} | {trend_str} | "
        f"Prob: {prob:.1%} | Pos: {pos_str}"
    )
    
    # 3. LÓGICA DE SALIDA
    if state["position"] is not None:
        if prob >= EXIT_PROB:
            handle_exit(symbol, signals, f"Prob {prob:.1%} >= {EXIT_PROB:.1%}")
            return
        
        if state["position"] == "long" and trend == -1:
            handle_exit(symbol, signals, "Tendencia bajista")
            return
        
        if state["position"] == "short" and trend == 1:
            handle_exit(symbol, signals, "Tendencia alcista")
            return
    
    # 4. LÓGICA DE ENTRADA
    if state["position"] is None:
        if balance < MIN_BALANCE:
            return
        
        if prob >= ENTRY_MAX_PROB:
            return
        
        if bull_in:
            handle_entry(symbol, signals, "long", balance)
        elif bear_in:
            handle_entry(symbol, signals, "short", balance)


def send_status_report(balance: float):
    """Reporte de estado global"""
    total = global_state["total_wins"] + global_state["total_losses"]
    wr = global_state["total_wins"] / total * 100 if total > 0 else 0
    
    # Resumen de posiciones
    positions_summary = []
    for symbol, state in positions_state.items():
        if state["position"]:
            positions_summary.append(
                f"{symbol}: {state['position'].upper()} @ ${state['entry_price']:.4f}"
            )
    
    pos_text = "\n".join(positions_summary) if positions_summary else "Sin posiciones"
    
    client.send_telegram(
        f"<b>📊 Reporte Global #{global_state['cycle']}</b>\n"
        f"Balance: ${balance:.2f} USDT\n"
        f"Símbolos: {len(SYMBOLS)} | Activos: {count_open_positions()}/{MAX_POSITIONS}\n\n"
        f"<b>Posiciones:</b>\n{pos_text}\n\n"
        f"Trades: {total} | WR: {wr:.1f}% ({global_state['total_wins']}W/{global_state['total_losses']}L)\n"
        f"PnL total: ${global_state['total_pnl']:+.4f} USDT"
    )


# ─────────────────────── MAIN LOOP ───────────────────────────

def run_cycle():
    """Ejecutar un ciclo completo de todos los símbolos"""
    global_state["cycle"] += 1
    
    # Obtener balance una vez
    try:
        balance = client.get_balance()
    except BingXError as e:
        logger.error(f"Error obteniendo balance: {e}")
        return
    
    logger.info(f"\n{'='*80}")
    logger.info(f"CICLO #{global_state['cycle']} | Balance: ${balance:.2f} USDT | Posiciones: {count_open_positions()}/{MAX_POSITIONS}")
    logger.info(f"{'='*80}")
    
    # Procesar cada símbolo
    for symbol in SYMBOLS:
        process_symbol(symbol, balance)
    
    # Reporte periódico
    if REPORT_EVERY > 0 and global_state["cycle"] % REPORT_EVERY == 0:
        send_status_report(balance)


def main():
    """Función principal"""
    # Detectar modo de cuenta
    global_state["account_mode"] = detect_account_mode()
    
    # Banner
    mode = "DEMO" if DEMO_MODE else "REAL MONEY"
    logger.info("=" * 80)
    logger.info(f"  Bot Multi-Moneda Zero Lag v3  |  {mode}")
    logger.info(f"  Símbolos: {', '.join(SYMBOLS)}")
    logger.info(f"  TF: {TIMEFRAME} | Leverage: {LEVERAGE}x | Max Posiciones: {MAX_POSITIONS}")
    logger.info(f"  Riesgo: {RISK_PCT:.0%}/op | Entrada: prob<{ENTRY_MAX_PROB:.0%} | Salida: prob>={EXIT_PROB:.0%}")
    logger.info(f"  Telegram: {'ON' if TELEGRAM_TOKEN and TELEGRAM_CHAT else 'OFF'}")
    logger.info(f"  Cuenta: {global_state['account_mode'].upper()}")
    logger.info("=" * 80)
    
    if not DEMO_MODE:
        logger.warning("⚠️  MODO REAL — Se usará dinero real en BingX")
        time.sleep(3)
    
    # Balance inicial
    try:
        global_state["start_balance"] = client.get_balance()
        logger.info(f"Balance inicial: ${global_state['start_balance']:.2f} USDT\n")
    except BingXError as e:
        logger.error(f"Error conectando con BingX: {e}")
        sys.exit(1)
    
    # Notificación inicial
    client.send_telegram(
        f"<b>🚀 Bot Multi-Moneda iniciado</b>\n"
        f"Símbolos: {', '.join(SYMBOLS)}\n"
        f"TF: {TIMEFRAME} | Leverage: {LEVERAGE}x\n"
        f"Modo: {'DEMO' if DEMO_MODE else 'REAL'} | Cuenta: {global_state['account_mode'].upper()}\n"
        f"Max posiciones: {MAX_POSITIONS}\n"
        f"Balance: ${global_state['start_balance']:.2f} USDT"
    )
    
    # Loop principal
    while True:
        try:
            run_cycle()
        
        except KeyboardInterrupt:
            logger.info("\n🛑 Bot detenido por el usuario")
            
            try:
                final_balance = client.get_balance()
                pnl = final_balance - global_state["start_balance"]
                total = global_state["total_wins"] + global_state["total_losses"]
                wr = global_state["total_wins"] / total * 100 if total > 0 else 0
                
                logger.info(f"Trades: {total} | WR: {wr:.1f}%")
                logger.info(f"Balance final: ${final_balance:.2f} (PnL: ${pnl:+.2f})")
                
                client.send_telegram(
                    f"<b>🛑 Bot detenido</b>\n"
                    f"Trades: {total} | WR: {wr:.1f}%\n"
                    f"Balance final: ${final_balance:.2f} USDT\n"
                    f"PnL total: ${pnl:+.2f} USDT"
                )
            except Exception:
                pass
            
            sys.exit(0)
        
        except Exception as e:
            logger.error(f"❌ Error inesperado: {e}", exc_info=True)
            client.send_telegram(f"<b>❌ Error inesperado</b>\n{e}")
        
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
