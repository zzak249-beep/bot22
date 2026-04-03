"""
Bot SHORT-Only Multi-Symbol — Zero Lag + Trend Reversal Probability
Solo opera en SHORT | Escanea todos los pares disponibles en BingX
v1.0 — Short Specialist

Señales de entrada SHORT:
  • Tendencia bajista (precio < ZLEMA)
  • Cruce bajista reciente del precio bajo ZLEMA  OR  rechazo en banda superior
  • Oscilador > 20 (no sobrevendido)
  • Probabilidad de reversión < ENTRY_MAX_PROB

Señales de salida SHORT:
  • Probabilidad de reversión >= EXIT_PROB  (agotamiento bajista)
  • Tendencia giró a alcista
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

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ──────────────────────── CONFIG ─────────────────────────────

def _env(key, default=None, cast=str):
    val = os.getenv(key, default)
    if val is None:
        raise EnvironmentError(f"Variable requerida: {key}")
    return cast(val)

# Las variables usan prefijo SH_ para que puedas tener ambos bots
# en el mismo Railway project con variables diferentes.
# Si el prefijo no existe, cae al valor sin prefijo.
def _senv(key, default=None, cast=str):
    """Intenta SH_{key}, luego {key}, luego default."""
    val = os.getenv(f"SH_{key}") or os.getenv(key, default)
    if val is None:
        raise EnvironmentError(f"Variable requerida: {key}")
    return cast(val)

API_KEY        = _senv("BINGX_API_KEY")
SECRET_KEY     = _senv("BINGX_SECRET_KEY")
TELEGRAM_TOKEN = _senv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = _senv("TELEGRAM_CHAT_ID",   "")
TIMEFRAME      = _senv("TIMEFRAME",      "15m")
LEVERAGE       = _senv("LEVERAGE",       "5",    int)
RISK_PCT       = _senv("RISK_PCT",       "0.03", float)  # más conservador en shorts
ZLEMA_LENGTH   = _senv("ZLEMA_LENGTH",   "70",   int)
BAND_MULT      = _senv("BAND_MULT",      "1.2",  float)
OSC_PERIOD     = _senv("OSC_PERIOD",     "20",   int)
# Para shorts: entrar cuando prob < 0.60 (mercado no agotado aún hacia abajo)
ENTRY_MAX_PROB = _senv("ENTRY_MAX_PROB", "0.60", float)
# Salir cuando hay 80%+ de prob de reversión (precio agotado bajista)
EXIT_PROB      = _senv("EXIT_PROB",      "0.80", float)
CHECK_INTERVAL = _senv("CHECK_INTERVAL", "60",   int)
DEMO_MODE      = _senv("DEMO_MODE",      "false").lower() == "true"
MIN_BALANCE    = _senv("MIN_BALANCE",    "10",   float)
POSITION_MODE  = _senv("POSITION_MODE",  "auto")
REPORT_EVERY   = _senv("REPORT_EVERY",   "60",   int)
MAX_OPEN_TRADES = _senv("MAX_OPEN_TRADES", "3",       int)
MIN_VOLUME_24H  = _senv("MIN_VOLUME_24H",  "1000000", float)
MAX_SYMBOLS     = _senv("MAX_SYMBOLS",     "60",      int)
SCAN_INTERVAL   = _senv("SCAN_INTERVAL",   "300",     int)
COOLDOWN_MIN    = _senv("COOLDOWN_MIN",    "20",      int)
TP_PCT          = _senv("TP_PCT",          "2.0",     float)  # % take profit shorts
SL_PCT          = _senv("SL_PCT",          "1.0",     float)  # % stop loss shorts

# Filtros adicionales específicos para shorts
# Solo entrar short si el precio cayó al menos X% en las últimas N velas
MIN_DROP_PCT    = _senv("SH_MIN_DROP_PCT",  "0.3",  float)  # 0.3% de caída mínima confirmada
# Excluir pares que subieron mucho (momentum alcista fuerte = peligro para shorts)
MAX_PUMP_PCT    = _senv("SH_MAX_PUMP_PCT",  "5.0",  float)  # excluir si subió > 5% en 1h

EXCLUDED = {
    'DOW','SP500','GOLD','SILVER','XAU','OIL','BRENT',
    'EUR','GBP','JPY','TSLA','AAPL','MSFT','NVDA',
    'COIN','MSTR','WHEAT','CORN',
}

# ──────────────────────── STATE ──────────────────────────────

client = BingXClient(
    API_KEY, SECRET_KEY,
    demo=DEMO_MODE,
    telegram_token=TELEGRAM_TOKEN,
    telegram_chat=TELEGRAM_CHAT,
)

open_trades: dict = {}
cooldowns:   dict = {}
symbols:     list = []
last_scan_ts: float = 0

stats = {
    "cycle":         0,
    "wins":          0,
    "losses":        0,
    "total_pnl":     0.0,
    "start_balance": None,
    "account_mode":  None,
}

# ─────────────────────── HELPERS ─────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def detect_account_mode() -> str:
    if POSITION_MODE.lower() in ("hedge", "oneway"):
        return POSITION_MODE.lower()
    try:
        positions = client.get_positions("BTC-USDT")
        for pos in positions:
            side = str(pos.get("positionSide", "")).upper()
            if side in ("LONG", "SHORT"):
                logger.info("Modo HEDGE detectado")
                return "hedge"
            elif side == "BOTH":
                logger.info("Modo ONE-WAY detectado")
                return "oneway"
        return "hedge"
    except Exception:
        return "hedge"


def get_symbols() -> list:
    """Obtiene pares USDT ordenados por volumen."""
    global symbols, last_scan_ts
    now = time.time()
    if symbols and (now - last_scan_ts) < SCAN_INTERVAL:
        return symbols

    try:
        import requests as req
        d = req.get(
            "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
            timeout=15,
        ).json()
        if d.get("code") != 0:
            return symbols or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

        items = []
        for t in d.get("data", []):
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            base = sym.replace("-USDT", "").upper()
            if any(ex in base for ex in EXCLUDED):
                continue
            try:
                price  = float(t.get("lastPrice", 0))
                vol    = float(t.get("volume", 0)) * price
                change = float(t.get("priceChangePercent", 0))

                if vol < MIN_VOLUME_24H or price < 0.000001:
                    continue
                # Para shorts: excluir pares con pump masivo reciente
                if change > MAX_PUMP_PCT:
                    continue
                items.append({"symbol": sym, "vol": vol, "change": change})
            except Exception:
                continue

        # Ordenar: primero los que más bajaron (mejores candidatos a short)
        items.sort(key=lambda x: x["change"])
        # Tomar los MAX_SYMBOLS pero priorizando bajadas
        symbols = [x["symbol"] for x in items[:MAX_SYMBOLS]]
        last_scan_ts = now
        logger.info(f"Símbolos SHORT actualizados: {len(symbols)}")
        return symbols

    except Exception as e:
        logger.warning(f"Error símbolos: {e}")
        return symbols or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]


def get_ohlcv(symbol: str) -> pd.DataFrame:
    klines = client.get_klines(symbol, TIMEFRAME, limit=500)
    if not klines:
        raise RuntimeError(f"Sin velas para {symbol}")
    df = pd.DataFrame(klines)
    if isinstance(klines[0], dict):
        df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                                 "c": "close", "v": "volume"})
    else:
        df.columns = ["open_time", "open", "high", "low", "close", "volume"] + \
                     list(range(len(df.columns) - 6))
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def calculate_qty(balance: float, price: float) -> float:
    usdt_exposure = balance * RISK_PCT * LEVERAGE
    qty = round(usdt_exposure / price, 3)
    return max(qty, 0.001)


def is_on_cooldown(symbol: str) -> bool:
    ts = cooldowns.get(symbol)
    if not ts:
        return False
    if time.time() > ts:
        del cooldowns[symbol]
        return False
    return True


def set_cooldown(symbol: str):
    cooldowns[symbol] = time.time() + COOLDOWN_MIN * 60


def has_bearish_momentum(df: pd.DataFrame) -> bool:
    """
    Confirma que hay momentum bajista real antes de entrar short.
    Evita entrar en correcciones pequeñas dentro de tendencias alcistas.
    """
    if len(df) < 5:
        return False
    closes = df["close"].tolist()
    # Caída mínima en las últimas 3 velas
    drop = (closes[-4] - closes[-1]) / closes[-4] * 100
    # Al menos 2 de las últimas 3 velas son rojas
    red_candles = sum(
        1 for i in range(-3, 0)
        if df["close"].iloc[i] < df["open"].iloc[i]
    )
    return drop >= MIN_DROP_PCT and red_candles >= 2


def sync_all_positions():
    try:
        d = client._request("GET", "/openApi/swap/v2/user/positions", {})
        real = {}
        for p in (d if isinstance(d, list) else []):
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                sym = p.get("symbol", "")
                ps  = str(p.get("positionSide", "BOTH")).upper()
                side = "long" if (ps == "LONG" or (ps == "BOTH" and amt > 0)) else "short"
                real[sym] = {
                    "position":      side,
                    "entry_price":   float(p.get("avgPrice", 0) or p.get("entryPrice", 0)),
                    "entry_qty":     abs(amt),
                    "position_side": ps,
                    "opened_at":     open_trades.get(sym, {}).get("opened_at", datetime.now(timezone.utc)),
                }
        for sym in list(open_trades.keys()):
            if sym not in real:
                logger.info(f"  [SYNC] {sym} cerrado externamente")
                del open_trades[sym]
        for sym, info in real.items():
            # Solo recuperar shorts (este bot no gestiona longs)
            if sym not in open_trades and info["position"] == "short":
                logger.info(f"  [SYNC] Recuperando SHORT {sym}")
                open_trades[sym] = info
    except Exception as e:
        logger.warning(f"sync error: {e}")


# ─────────────────────── CORE LOGIC ──────────────────────────

def handle_exit(symbol: str, signals: dict, reason: str):
    if symbol not in open_trades:
        return

    t       = open_trades[symbol]
    entry   = t["entry_price"]
    current = signals["close"]
    qty     = t["entry_qty"]

    # SHORT: ganamos cuando el precio baja
    pnl_pct = (entry - current) / entry * 100
    pnl_usd = qty * (entry - current)
    stats["total_pnl"] += pnl_usd

    mins  = int((datetime.now(timezone.utc) - t["opened_at"]).total_seconds() / 60)
    emoji = "✅" if pnl_pct > 0 else "❌"

    logger.info(f"  CERRANDO SHORT {symbol} | {reason} | PnL: {pnl_pct:+.2f}%")

    client.send_telegram(
        f"<b>{emoji} SHORT CERRADO — {reason}</b>\n"
        f"Par: {symbol} | TF: {TIMEFRAME}\n"
        f"Entrada: ${entry:.4f} → Salida: ${current:.4f} | {mins}min\n"
        f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+.4f} USDT)\n"
        f"PnL total sesión: ${stats['total_pnl']:+.4f} USDT\n"
        f"Posiciones abiertas: {len(open_trades)-1}/{MAX_OPEN_TRADES}"
    )

    client.close_all_positions(symbol)

    if pnl_pct > 0:
        stats["wins"] += 1
    else:
        stats["losses"] += 1

    set_cooldown(symbol)
    del open_trades[symbol]
    time.sleep(1)


def handle_short_entry(symbol: str, signals: dict, balance: float):
    price = signals["close"]
    qty   = calculate_qty(balance, price)
    if qty <= 0:
        return

    acc_mode = stats.get("account_mode", "hedge")
    pos_side = "SHORT" if acc_mode == "hedge" else "BOTH"

    logger.info(f"  ABRIENDO SHORT {symbol} | ${price:.4f} qty={qty} prob={signals['probability']:.1%}")

    try:
        client.set_leverage(symbol, LEVERAGE)
        client.place_market_order(symbol, "SELL", qty, position_side=pos_side)

        tp_price = price * (1 - TP_PCT / 100)
        sl_price = price * (1 + SL_PCT / 100)

        open_trades[symbol] = {
            "position":      "short",
            "entry_price":   price,
            "entry_qty":     qty,
            "position_side": pos_side,
            "tp_price":      tp_price,
            "sl_price":      sl_price,
            "opened_at":     datetime.now(timezone.utc),
        }

        # Colocar TP y SL en BingX
        tp_sl = client.place_tp_sl(symbol, "short", qty, tp_price, sl_price, position_side=pos_side)

        tp_icon = "✅" if tp_sl["tp"] else "❌"
        sl_icon = "✅" if tp_sl["sl"] else "❌"
        client.send_telegram(
            f"<b>🔴 SHORT ABIERTO [Short Specialist]</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME} | {LEVERAGE}x\n"
            f"Precio: ${price:.4f} | Qty: {qty}\n"
            f"TP {tp_icon}: ${tp_price:.4f} (-{TP_PCT}%)\n"
            f"SL {sl_icon}: ${sl_price:.4f} (+{SL_PCT}%)\n"
            f"Prob reversión: {signals['probability']:.1%} (< {ENTRY_MAX_PROB:.0%})\n"
            f"Balance: ${balance:.2f} USDT\n"
            f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )

    except BingXError as e:
        logger.error(f"  Error abriendo SHORT {symbol}: {e}")
        client.send_telegram(f"<b>⚠️ Error SHORT {symbol}</b>\n{e}")


def analyze_symbol(symbol: str, balance: float):
    # Si ya tiene posición → gestionar salida
    if symbol in open_trades:
        try:
            df      = get_ohlcv(symbol)
            signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
        except Exception as e:
            logger.debug(f"  {symbol} señales error: {e}")
            return

        prob = signals["probability"]

        # Salir si: alta prob de reversión (rebote alcista inminente)
        if prob >= EXIT_PROB:
            handle_exit(symbol, signals, f"Prob reversión {prob:.1%} >= {EXIT_PROB:.0%}")
            return
        # Salir si: tendencia giró alcista
        if signals["trend"] == 1:
            handle_exit(symbol, signals, "Tendencia viró a alcista")
            return
        return

    # Buscar nuevos shorts
    if len(open_trades) >= MAX_OPEN_TRADES:
        return
    if is_on_cooldown(symbol):
        return

    try:
        df      = get_ohlcv(symbol)
        signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
    except Exception as e:
        logger.debug(f"  {symbol} señales error: {e}")
        return

    if balance < MIN_BALANCE:
        return
    if signals["probability"] >= ENTRY_MAX_PROB:
        return

    # Solo entrar en señales bajistas
    if not signals["bearish_entry"]:
        return

    # Confirmar momentum bajista real
    if not has_bearish_momentum(df):
        logger.debug(f"  {symbol}: señal SHORT sin momentum suficiente — omitida")
        return

    # Todo OK → abrir short
    handle_short_entry(symbol, signals, balance)


def send_report(balance: float):
    total = stats["wins"] + stats["losses"]
    wr    = stats["wins"] / total * 100 if total > 0 else 0
    pos_lines = ""
    for sym, t in open_trades.items():
        try:
            import requests as req
            tk = req.get(
                "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
                params={"symbol": sym}, timeout=5,
            ).json()
            cur = float(tk["data"]["lastPrice"]) if tk.get("code") == 0 else t["entry_price"]
        except Exception:
            cur = t["entry_price"]
        pct = (t["entry_price"] - cur) / t["entry_price"] * 100
        pos_lines += f"  SHORT {sym}: {pct:+.2f}%\n"

    no_pos = "  sin posiciones\n"
    client.send_telegram(
        f"<b>📊 Reporte Short Bot #{stats['cycle']}</b>\n"
        f"Balance: ${balance:.2f} USDT\n"
        f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}\n"
        f"{pos_lines if pos_lines else no_pos}"
        f"Trades: {total} | WR: {wr:.1f}% ({stats['wins']}W/{stats['losses']}L)\n"
        f"PnL sesión: ${stats['total_pnl']:+.4f} USDT"
    )


# ─────────────────────── MAIN LOOP ───────────────────────────

def main():
    stats["account_mode"] = detect_account_mode()

    logger.info("=" * 65)
    logger.info("  🔴 Zero Lag SHORT-Only Bot v1.0  |  Solo Shorts")
    logger.info(f"  TF:{TIMEFRAME} | LEV:{LEVERAGE}x | MaxTrades:{MAX_OPEN_TRADES}")
    logger.info(f"  Riesgo:{RISK_PCT:.0%}/op | MaxSymbols:{MAX_SYMBOLS}")
    logger.info(f"  Entrada:prob<{ENTRY_MAX_PROB:.0%} | Salida:prob>={EXIT_PROB:.0%}")
    logger.info(f"  Modo cuenta: {stats['account_mode'].upper()}")
    logger.info(f"  Filtro momentum: caída>{MIN_DROP_PCT}% + 2 velas rojas")
    logger.info("=" * 65)

    if DEMO_MODE:
        logger.warning("MODO DEMO — sin dinero real")

    try:
        stats["start_balance"] = client.get_balance()
        logger.info(f"Balance inicial: ${stats['start_balance']:.2f} USDT")
    except BingXError as e:
        logger.error(f"No se pudo conectar: {e}")
        sys.exit(1)

    sync_all_positions()
    get_symbols()

    client.send_telegram(
        f"<b>🔴 Short-Only Bot v1.0 iniciado</b>\n"
        f"TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} shorts\n"
        f"Símbolos: {len(symbols)} (priorizados por bajada)\n"
        f"Modo: {'DEMO' if DEMO_MODE else 'REAL'} | Cuenta: {stats['account_mode'].upper()}\n"
        f"Balance: ${stats['start_balance']:.2f} USDT"
    )

    while True:
        try:
            stats["cycle"] += 1
            cycle = stats["cycle"]

            syms = get_symbols()
            sync_all_positions()

            try:
                balance = client.get_balance()
            except BingXError as e:
                logger.error(f"Error balance: {e}")
                time.sleep(CHECK_INTERVAL)
                continue

            total = stats["wins"] + stats["losses"]
            wr    = stats["wins"] / total * 100 if total > 0 else 0
            logger.info(
                f"\n{'='*65}\n"
                f"  #{cycle} {now_utc()} | Balance:${balance:.2f} | "
                f"Shorts:{len(open_trades)}/{MAX_OPEN_TRADES} | "
                f"WR:{wr:.1f}% | PnL:${stats['total_pnl']:+.4f}\n"
                f"{'='*65}"
            )

            # 1. Gestionar posiciones abiertas
            for sym in list(open_trades.keys()):
                analyze_symbol(sym, balance)
                time.sleep(0.2)

            # 2. Buscar nuevos shorts
            if len(open_trades) < MAX_OPEN_TRADES:
                logger.info(f"  Escaneando {len(syms)} símbolos para shorts…")
                found = 0
                for i, sym in enumerate(syms):
                    if len(open_trades) >= MAX_OPEN_TRADES:
                        break
                    if sym in open_trades:
                        continue
                    analyze_symbol(sym, balance)
                    if sym in open_trades:
                        found += 1
                    time.sleep(0.15)
                    if (i + 1) % 20 == 0:
                        logger.info(f"  …{i+1}/{len(syms)} | {found} shorts abiertos")
                logger.info(f"  Scan: {found} nuevos shorts")
            else:
                logger.info(f"  Max shorts ({MAX_OPEN_TRADES}) — solo monitoreando")

            if REPORT_EVERY > 0 and cycle % REPORT_EVERY == 0:
                send_report(balance)

            logger.info(f"  Próximo ciclo en {CHECK_INTERVAL}s\n")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Short Bot detenido")
            try:
                final = client.get_balance()
                pnl   = final - stats["start_balance"]
                total = stats["wins"] + stats["losses"]
                wr    = stats["wins"] / total * 100 if total > 0 else 0
                client.send_telegram(
                    f"<b>🔴 Short Bot detenido</b>\n"
                    f"Trades: {total} | WR: {wr:.1f}%\n"
                    f"Balance final: ${final:.2f} USDT (PnL: ${pnl:+.2f})"
                )
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            logger.error(f"Error inesperado ciclo #{cycle}: {e}", exc_info=True)
            client.send_telegram(f"<b>⚠️ Error en Short Bot</b>\n{e}")
            time.sleep(20)


if __name__ == "__main__":
    main()
