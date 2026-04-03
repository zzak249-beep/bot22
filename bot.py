"""
Bot de Scalping Multi-Symbol — Zero Lag + Trend Reversal Probability
LONG + SHORT | Escanea todos los pares disponibles en BingX
v3.0 — Multi-Symbol Scanner
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
        raise EnvironmentError(f"Variable requerida no encontrada: {key}")
    return cast(val)

API_KEY        = _env("BINGX_API_KEY")
SECRET_KEY     = _env("BINGX_SECRET_KEY")
TELEGRAM_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = _env("TELEGRAM_CHAT_ID",   "")
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
# Multi-symbol config
MAX_OPEN_TRADES = _env("MAX_OPEN_TRADES", "3",       int)
MIN_VOLUME_24H  = _env("MIN_VOLUME_24H",  "1000000", float)  # USDT
MAX_SYMBOLS     = _env("MAX_SYMBOLS",     "50",      int)
SCAN_INTERVAL   = _env("SCAN_INTERVAL",   "300",     int)    # re-scan de símbolos cada 5min
ALLOW_SHORT     = _env("ALLOW_SHORT",     "true").lower() == "true"
COOLDOWN_MIN    = _env("COOLDOWN_MIN",    "15",      int)    # minutos entre trades mismo par

# Símbolos excluidos (derivados, índices, materias primas)
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

# open_trades: {symbol: {position, entry_price, entry_qty, position_side, opened_at}}
open_trades: dict = {}
cooldowns:   dict = {}   # {symbol: timestamp_fin_cooldown}
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
        logger.info("Sin posiciones — asumiendo HEDGE")
        return "hedge"
    except Exception as e:
        logger.warning(f"No se pudo detectar modo: {e} — asumiendo HEDGE")
        return "hedge"


def get_symbols() -> list:
    """Obtiene todos los pares USDT con volumen suficiente, ordenados por volumen."""
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
                price = float(t.get("lastPrice", 0))
                vol   = float(t.get("volume", 0)) * price
                if vol < MIN_VOLUME_24H or price < 0.000001:
                    continue
                items.append({"symbol": sym, "vol": vol})
            except Exception:
                continue

        items.sort(key=lambda x: x["vol"], reverse=True)
        symbols = [x["symbol"] for x in items[:MAX_SYMBOLS]]
        last_scan_ts = now
        logger.info(f"Símbolos actualizados: {len(symbols)}")
        return symbols

    except Exception as e:
        logger.warning(f"Error obteniendo símbolos: {e}")
        return symbols or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]


def get_ohlcv(symbol: str) -> pd.DataFrame:
    klines = client.get_klines(symbol, TIMEFRAME, limit=500)
    if not klines:
        raise RuntimeError(f"Sin datos de velas para {symbol}")
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


def sync_all_positions():
    """Sincroniza open_trades con las posiciones reales en BingX."""
    try:
        import requests as req
        d = client._request("GET", "/openApi/swap/v2/user/positions", {})
        real = {}
        for p in (d if isinstance(d, list) else []):
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                sym = p.get("symbol", "")
                ps  = str(p.get("positionSide", "BOTH")).upper()
                real[sym] = {
                    "position":      "long" if (ps == "LONG" or (ps == "BOTH" and amt > 0)) else "short",
                    "entry_price":   float(p.get("avgPrice", 0) or p.get("entryPrice", 0)),
                    "entry_qty":     abs(amt),
                    "position_side": ps,
                    "opened_at":     open_trades.get(sym, {}).get("opened_at", datetime.now(timezone.utc)),
                }
        # Eliminar trades que ya no están en BingX
        for sym in list(open_trades.keys()):
            if sym not in real:
                logger.info(f"  [SYNC] {sym} cerrado externamente — eliminando del estado")
                del open_trades[sym]
        # Añadir posiciones reales que no teníamos registradas
        for sym, info in real.items():
            if sym not in open_trades:
                logger.info(f"  [SYNC] Recuperando {info['position'].upper()} {sym}")
                open_trades[sym] = info
    except Exception as e:
        logger.warning(f"sync_all_positions error: {e}")


# ─────────────────────── CORE LOGIC ──────────────────────────

def handle_exit(symbol: str, signals: dict, reason: str):
    if symbol not in open_trades:
        return

    t         = open_trades[symbol]
    entry     = t["entry_price"]
    current   = signals["close"]
    direction = t["position"]
    qty       = t["entry_qty"]

    pnl_pct = ((current - entry) / entry * 100) if direction == "long" \
               else ((entry - current) / entry * 100)
    pnl_usd = qty * (current - entry) * (1 if direction == "long" else -1)
    stats["total_pnl"] += pnl_usd

    logger.info(f"  CERRANDO {direction.upper()} {symbol} | {reason} | PnL: {pnl_pct:+.2f}%")

    emoji = "✅" if pnl_pct > 0 else "❌"
    mins  = int((datetime.now(timezone.utc) - t["opened_at"]).total_seconds() / 60)
    client.send_telegram(
        f"<b>{emoji} CERRADO {direction.upper()} — {reason}</b>\n"
        f"Par: {symbol} | TF: {TIMEFRAME}\n"
        f"Entrada: ${entry:.4f} | Salida: ${current:.4f} | {mins}min\n"
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


def handle_entry(symbol: str, signals: dict, direction: str, balance: float):
    price = signals["close"]
    qty   = calculate_qty(balance, price)
    if qty <= 0:
        return

    side     = "BUY" if direction == "long" else "SELL"
    acc_mode = stats.get("account_mode", "hedge")
    pos_side = ("LONG" if direction == "long" else "SHORT") if acc_mode == "hedge" else "BOTH"

    logger.info(f"  ABRIENDO {direction.upper()} {symbol} | ${price:.4f} qty={qty} prob={signals['probability']:.1%}")

    try:
        client.set_leverage(symbol, LEVERAGE)
        client.place_market_order(symbol, side, qty, position_side=pos_side)

        open_trades[symbol] = {
            "position":      direction,
            "entry_price":   price,
            "entry_qty":     qty,
            "position_side": pos_side,
            "opened_at":     datetime.now(timezone.utc),
        }

        emoji = "🟢" if direction == "long" else "🔴"
        trend_str = "ALCISTA" if signals["trend"] == 1 else "BAJISTA"
        client.send_telegram(
            f"<b>{emoji} ABIERTO {direction.upper()} [Multi-Symbol]</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME} | {LEVERAGE}x\n"
            f"Precio: ${price:.4f} | Qty: {qty}\n"
            f"Tendencia: {trend_str} | Prob rev: {signals['probability']:.1%}\n"
            f"Balance: ${balance:.2f} USDT\n"
            f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )

    except BingXError as e:
        logger.error(f"  Error abriendo {symbol}: {e}")
        client.send_telegram(f"<b>⚠️ Error abriendo {direction.upper()} {symbol}</b>\n{e}")


def analyze_symbol(symbol: str, balance: float):
    """Analiza un símbolo y actúa según señales."""
    # Si ya tiene posición abierta → gestionar salida
    if symbol in open_trades:
        t = open_trades[symbol]
        try:
            df      = get_ohlcv(symbol)
            signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
        except Exception as e:
            logger.debug(f"  {symbol} error señales: {e}")
            return

        direction = t["position"]
        prob      = signals["probability"]

        if prob >= EXIT_PROB:
            handle_exit(symbol, signals, f"Prob reversión {prob:.1%}")
            return
        if direction == "long" and signals["trend"] == -1:
            handle_exit(symbol, signals, "Tendencia viró a bajista")
            return
        if direction == "short" and signals["trend"] == 1:
            handle_exit(symbol, signals, "Tendencia viró a alcista")
            return
        return

    # Sin posición → buscar entrada si hay slots libres
    if len(open_trades) >= MAX_OPEN_TRADES:
        return
    if is_on_cooldown(symbol):
        return

    try:
        df      = get_ohlcv(symbol)
        signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
    except Exception as e:
        logger.debug(f"  {symbol} error señales: {e}")
        return

    if balance < MIN_BALANCE:
        return
    if signals["probability"] >= ENTRY_MAX_PROB:
        return

    if signals["bullish_entry"]:
        handle_entry(symbol, signals, "long", balance)
    elif ALLOW_SHORT and signals["bearish_entry"]:
        handle_entry(symbol, signals, "short", balance)


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
        d   = t["position"]
        pct = ((cur - t["entry_price"]) / t["entry_price"] * 100) if d == "long" \
              else ((t["entry_price"] - cur) / t["entry_price"] * 100)
        pos_lines += f"  {d.upper()} {sym}: {pct:+.2f}%\n"

    client.send_telegram(
        f"<b>📊 Reporte Multi-Symbol Bot #{stats['cycle']}</b>\n"
        f"Balance: ${balance:.2f} USDT\n"
        f"Abiertos: {len(open_trades)}/{MAX_OPEN_TRADES}\n"
        f"{pos_lines or '  sin posiciones\n'}"
        f"Trades: {total} | WR: {wr:.1f}% ({stats['wins']}W/{stats['losses']}L)\n"
        f"PnL sesión: ${stats['total_pnl']:+.4f} USDT"
    )


# ─────────────────────── MAIN LOOP ───────────────────────────

def main():
    stats["account_mode"] = detect_account_mode()

    logger.info("=" * 65)
    logger.info("  Zero Lag Multi-Symbol Bot v3.0  |  LONG + SHORT")
    logger.info(f"  TF:{TIMEFRAME} | LEV:{LEVERAGE}x | MaxTrades:{MAX_OPEN_TRADES}")
    logger.info(f"  Riesgo:{RISK_PCT:.0%}/op | MaxSymbols:{MAX_SYMBOLS}")
    logger.info(f"  Entrada:prob<{ENTRY_MAX_PROB:.0%} | Salida:prob>={EXIT_PROB:.0%}")
    logger.info(f"  Modo cuenta: {stats['account_mode'].upper()}")
    logger.info(f"  Shorts: {'ON' if ALLOW_SHORT else 'OFF'}")
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
        f"<b>🚀 Multi-Symbol Bot v3.0 iniciado</b>\n"
        f"TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} trades\n"
        f"Símbolos: {len(symbols)} | Shorts: {'ON' if ALLOW_SHORT else 'OFF'}\n"
        f"Modo: {'DEMO' if DEMO_MODE else 'REAL'} | Cuenta: {stats['account_mode'].upper()}\n"
        f"Balance: ${stats['start_balance']:.2f} USDT"
    )

    while True:
        try:
            stats["cycle"] += 1
            cycle = stats["cycle"]

            # Re-escanear símbolos periódicamente
            syms = get_symbols()

            # Sincronizar posiciones con BingX
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
                f"Pos:{len(open_trades)}/{MAX_OPEN_TRADES} | "
                f"WR:{wr:.1f}% | PnL:${stats['total_pnl']:+.4f}\n"
                f"{'='*65}"
            )

            # 1. Gestionar posiciones abiertas primero
            for sym in list(open_trades.keys()):
                analyze_symbol(sym, balance)
                time.sleep(0.2)

            # 2. Buscar nuevas entradas si hay slots
            if len(open_trades) < MAX_OPEN_TRADES:
                logger.info(f"  Escaneando {len(syms)} símbolos…")
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
                        logger.info(f"  …{i+1}/{len(syms)} analizados | {found} entradas")
                logger.info(f"  Scan completo: {found} nuevas entradas")
            else:
                logger.info(f"  Max trades alcanzado ({MAX_OPEN_TRADES}) — solo monitoreando")

            # Reporte periódico
            if REPORT_EVERY > 0 and cycle % REPORT_EVERY == 0:
                send_report(balance)

            logger.info(f"  Próximo ciclo en {CHECK_INTERVAL}s\n")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Bot detenido")
            try:
                final = client.get_balance()
                pnl   = final - stats["start_balance"]
                total = stats["wins"] + stats["losses"]
                wr    = stats["wins"] / total * 100 if total > 0 else 0
                client.send_telegram(
                    f"<b>Bot Multi-Symbol detenido</b>\n"
                    f"Trades: {total} | WR: {wr:.1f}%\n"
                    f"Balance final: ${final:.2f} USDT (PnL: ${pnl:+.2f})"
                )
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            logger.error(f"Error inesperado ciclo #{cycle}: {e}", exc_info=True)
            client.send_telegram(f"<b>⚠️ Error en Multi-Symbol Bot</b>\n{e}")
            time.sleep(20)


if __name__ == "__main__":
    main()
