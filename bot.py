"""
Bot Multi-Symbol LONG+SHORT — Zero Lag + Trend Reversal Probability
v4.0 — Mejoras de rentabilidad

FIXES vs v3.0:
  FIX-1  LIMIT orders en entradas: fee 0.02% vs 0.05% taker (ahorra 60%)
  FIX-2  Tiempo mínimo de holding (MIN_HOLD_MIN): evita trades de 9 segundos
  FIX-3  Filtro ATR: solo entra si el movimiento potencial cubre fees + margen
  FIX-4  Parámetros conservadores por defecto: menos trades, más calidad
  FIX-5  TP/SL dinámicos basados en ATR (no porcentaje fijo)
  FIX-6  Blacklist de pares que pierden repetidamente
  FIX-7  Filtro de tendencia BTC/ETH: no abrir longs si BTC cae, ni shorts si sube
  FIX-8  Anti-overtrading: máximo 1 entrada nueva por ciclo
  FIX-9  Detección de spike de volumen antes de entrar
  FIX-10 Resumen de comisiones pagadas en el reporte
"""

import os
import sys
import time
import logging
import requests as req
import pandas as pd
from datetime import datetime, timezone, timedelta
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

API_KEY        = _env("BINGX_API_KEY")
SECRET_KEY     = _env("BINGX_SECRET_KEY")
TELEGRAM_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = _env("TELEGRAM_CHAT_ID",   "")

# ── Estrategia ────────────────────────────────────────────────
TIMEFRAME      = _env("TIMEFRAME",      "1h")        # FIX-4: 1h en vez de 15m
LEVERAGE       = _env("LEVERAGE",       "3",    int) # FIX-4: 3x en vez de 5x
RISK_PCT       = _env("RISK_PCT",       "0.03", float) # FIX-4: 3% en vez de 5%
ZLEMA_LENGTH   = _env("ZLEMA_LENGTH",   "50",   int) # FIX-4: más reactivo
BAND_MULT      = _env("BAND_MULT",      "1.2",  float)
OSC_PERIOD     = _env("OSC_PERIOD",     "20",   int)
ENTRY_MAX_PROB = _env("ENTRY_MAX_PROB", "0.55", float) # FIX-4: más estricto
EXIT_PROB      = _env("EXIT_PROB",      "0.80", float)
TP_PCT         = _env("TP_PCT",         "4.5",  float) # FIX-4: cubre fees
SL_PCT         = _env("SL_PCT",         "2.0",  float)
USE_ATR_TPSL   = _env("USE_ATR_TPSL",  "true").lower() == "true" # FIX-5
ATR_TP_MULT    = _env("ATR_TP_MULT",    "2.5",  float) # TP = 2.5 × ATR
ATR_SL_MULT    = _env("ATR_SL_MULT",    "1.2",  float) # SL = 1.2 × ATR

# ── Operación ─────────────────────────────────────────────────
CHECK_INTERVAL  = _env("CHECK_INTERVAL",  "300",     int)  # FIX-4: 5min
DEMO_MODE       = _env("DEMO_MODE",       "false").lower() == "true"
MIN_BALANCE     = _env("MIN_BALANCE",     "10",      float)
POSITION_MODE   = _env("POSITION_MODE",   "auto")
REPORT_EVERY    = _env("REPORT_EVERY",    "12",      int)   # cada 12 ciclos × 5min = 1h
MAX_OPEN_TRADES = _env("MAX_OPEN_TRADES", "2",       int)   # FIX-4: 2 en vez de 3
MIN_VOLUME_24H  = _env("MIN_VOLUME_24H",  "5000000", float) # FIX-4: 5M USDT
MAX_SYMBOLS     = _env("MAX_SYMBOLS",     "15",      int)   # FIX-4: solo top 15
SCAN_INTERVAL   = _env("SCAN_INTERVAL",   "600",     int)
ALLOW_SHORT     = _env("ALLOW_SHORT",     "true").lower() == "true"
COOLDOWN_MIN    = _env("COOLDOWN_MIN",    "60",      int)   # FIX-4: 1h
USE_LIMIT       = _env("USE_LIMIT_ORDERS","true").lower() == "true"  # FIX-1
MIN_HOLD_MIN    = _env("MIN_HOLD_MIN",    "15",      int)   # FIX-2: mínimo 15min
MIN_ATR_PCT     = _env("MIN_ATR_PCT",     "0.5",     float) # FIX-3: ATR > 0.5% del precio
USE_BTC_FILTER  = _env("USE_BTC_FILTER",  "true").lower() == "true"  # FIX-7
MAX_LOSS_STREAK = _env("MAX_LOSS_STREAK", "3",       int)   # blacklist tras N pérdidas seguidas
FEE_RATE        = 0.0002 if USE_LIMIT else 0.0005  # FIX-1

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

open_trades: dict  = {}   # {symbol: {position, entry_price, entry_qty, ...}}
cooldowns:   dict  = {}   # {symbol: expiry_timestamp}
blacklist:   dict  = {}   # {symbol: loss_streak}
symbols:     list  = []
last_scan_ts: float = 0
btc_trend_1h: float = 0.0

stats = {
    "cycle":          0,
    "wins":           0,
    "losses":         0,
    "total_pnl":      0.0,
    "total_fees":     0.0,
    "start_balance":  None,
    "account_mode":   None,
    "entries_today":  0,
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
    """Top pares USDT por volumen, excluye blacklist y derivados."""
    global symbols, last_scan_ts
    now = time.time()
    if symbols and (now - last_scan_ts) < SCAN_INTERVAL:
        return symbols
    try:
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
        # Excluir blacklist consolidada (pérdidas > MAX_LOSS_STREAK)
        bl = {s for s, n in blacklist.items() if n >= MAX_LOSS_STREAK}
        symbols = [x["symbol"] for x in items if x["symbol"] not in bl][:MAX_SYMBOLS]
        last_scan_ts = now
        logger.info(f"Símbolos: {len(symbols)} (blacklist: {len(bl)})")
        return symbols
    except Exception as e:
        logger.warning(f"get_symbols error: {e}")
        return symbols or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]


def get_ohlcv(symbol: str) -> pd.DataFrame:
    klines = client.get_klines(symbol, TIMEFRAME, limit=200)
    if not klines:
        raise RuntimeError(f"Sin velas {symbol}")
    df = pd.DataFrame(klines)
    if isinstance(klines[0], dict):
        df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                                 "c": "close", "v": "volume"})
    else:
        df.columns = (["open_time", "open", "high", "low", "close", "volume"]
                      + list(range(len(df.columns) - 6)))
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """ATR simple para filtrar entradas y calcular TP/SL dinámicos."""
    if len(df) < period + 1:
        return 0.0
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i - 1]),
               abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    return float(sum(trs[-period:]) / period)


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


def update_blacklist(symbol: str, win: bool):
    """FIX-6: acumula pérdidas consecutivas; reset en winner."""
    if win:
        blacklist[symbol] = 0
    else:
        blacklist[symbol] = blacklist.get(symbol, 0) + 1
    if blacklist[symbol] >= MAX_LOSS_STREAK:
        logger.warning(f"  [{symbol}] en blacklist ({MAX_LOSS_STREAK} pérdidas seguidas)")
        client.send_telegram(
            f"<b>⛔ Blacklist: {symbol}</b>\n"
            f"{MAX_LOSS_STREAK} pérdidas consecutivas — omitido por {COOLDOWN_MIN*4}min"
        )
        cooldowns[symbol] = time.time() + COOLDOWN_MIN * 4 * 60


def update_btc_trend():
    """FIX-7: tendencia BTC 1h para filtrar entradas."""
    global btc_trend_1h
    try:
        d = client.get_klines("BTC-USDT", "1h", limit=3)
        if d and len(d) >= 2:
            closes = [float(x["close"]) for x in d] if isinstance(d[0], dict) \
                     else [float(x[4]) for x in d]
            btc_trend_1h = (closes[-1] - closes[-2]) / closes[-2] * 100
    except Exception:
        pass


def btc_filter_ok(direction: str) -> bool:
    """FIX-7: bloquea longs si BTC cae > 1.5% y shorts si sube > 1.5%."""
    if not USE_BTC_FILTER:
        return True
    if direction == "long" and btc_trend_1h < -1.5:
        return False
    if direction == "short" and btc_trend_1h > 1.5:
        return False
    return True


def vol_spike_ok(df: pd.DataFrame) -> bool:
    """FIX-9: comprueba que hay volumen real, no ruido."""
    if len(df) < 6:
        return True
    vols = df["volume"].values
    avg_vol = sum(vols[-6:-1]) / 5
    current = vols[-1]
    return current > avg_vol * 0.8  # al menos 80% del promedio


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
                    "tp_price":      open_trades.get(sym, {}).get("tp_price", 0),
                    "sl_price":      open_trades.get(sym, {}).get("sl_price", 0),
                    "opened_at":     open_trades.get(sym, {}).get("opened_at",
                                     datetime.now(timezone.utc)),
                }
        for sym in list(open_trades.keys()):
            if sym not in real:
                logger.info(f"  [SYNC] {sym} cerrado externamente")
                del open_trades[sym]
        for sym, info in real.items():
            if sym not in open_trades:
                logger.info(f"  [SYNC] Recuperando {info['position'].upper()} {sym}")
                open_trades[sym] = info
    except Exception as e:
        logger.warning(f"sync error: {e}")


# ─────────────────────── CORE LOGIC ──────────────────────────

def handle_exit(symbol: str, signals: dict, reason: str):
    if symbol not in open_trades:
        return

    t         = open_trades[symbol]
    entry     = t["entry_price"]
    current   = signals["close"]
    direction = t["position"]
    qty       = t["entry_qty"]
    opened_at = t["opened_at"]

    # FIX-2: no cerrar antes de MIN_HOLD_MIN (solo salidas por señal, no por TP/SL)
    hold_mins = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60
    is_signal_exit = "prob" in reason.lower() or "tendencia" in reason.lower()
    if is_signal_exit and hold_mins < MIN_HOLD_MIN:
        logger.info(f"  {symbol}: holding mínimo no alcanzado ({hold_mins:.1f}/{MIN_HOLD_MIN}min) — ignorando señal de salida")
        return

    pnl_pct = ((current - entry) / entry * 100) if direction == "long" \
               else ((entry - current) / entry * 100)

    # Estimar PnL en USDT incluyendo fees
    notional  = qty * entry * LEVERAGE
    fee_total = notional * FEE_RATE * 2  # entrada + salida
    pnl_usd   = qty * (current - entry) * (1 if direction == "long" else -1)
    pnl_net   = pnl_usd - fee_total

    stats["total_pnl"]  += pnl_net
    stats["total_fees"] += fee_total
    win = pnl_net > 0

    if win:
        stats["wins"] += 1
    else:
        stats["losses"] += 1

    update_blacklist(symbol, win)

    mins  = int(hold_mins)
    emoji = "✅" if win else "❌"
    logger.info(f"  CERRANDO {direction.upper()} {symbol} | {reason} | PnL neto: ${pnl_net:+.4f} | {mins}min")

    client.close_all_positions(symbol)

    client.send_telegram(
        f"<b>{emoji} CERRADO {direction.upper()} — {reason}</b>\n"
        f"Par: {symbol} | TF: {TIMEFRAME}\n"
        f"Entrada: ${entry:.4f} → Salida: ${current:.4f} | {mins}min\n"
        f"PnL bruto: ${pnl_usd:+.4f} | Fee: ${fee_total:.4f}\n"
        f"<b>PnL neto: ${pnl_net:+.4f} USDT</b>\n"
        f"PnL sesión: ${stats['total_pnl']:+.4f} | Fees pagadas: ${stats['total_fees']:.4f}\n"
        f"Posiciones: {len(open_trades)-1}/{MAX_OPEN_TRADES}"
    )

    set_cooldown(symbol)
    del open_trades[symbol]
    time.sleep(1)


def handle_entry(symbol: str, signals: dict, direction: str, balance: float, df: pd.DataFrame):
    price = signals["close"]
    qty   = calculate_qty(balance, price)
    if qty <= 0:
        return

    # FIX-5: TP/SL dinámicos basados en ATR
    atr_val = calc_atr(df, 14)
    if USE_ATR_TPSL and atr_val > 0:
        tp_dist = atr_val * ATR_TP_MULT
        sl_dist = atr_val * ATR_SL_MULT
        tp_pct  = tp_dist / price * 100
        sl_pct  = sl_dist / price * 100
    else:
        tp_pct = TP_PCT
        sl_pct = SL_PCT

    # Asegurar TP mínimo que cubra fees
    min_tp = (FEE_RATE * 2 * LEVERAGE * 100) + 1.5  # fees + 1.5% margen
    tp_pct = max(tp_pct, min_tp, TP_PCT)
    sl_pct = max(sl_pct, SL_PCT)

    tp_price = price * (1 + tp_pct / 100) if direction == "long" else price * (1 - tp_pct / 100)
    sl_price = price * (1 - sl_pct / 100) if direction == "long" else price * (1 + sl_pct / 100)

    side     = "BUY" if direction == "long" else "SELL"
    acc_mode = stats.get("account_mode", "hedge")
    pos_side = ("LONG" if direction == "long" else "SHORT") if acc_mode == "hedge" else "BOTH"

    logger.info(
        f"  ABRIENDO {direction.upper()} {symbol} | ${price:.4f} "
        f"qty={qty} TP:{tp_pct:.2f}% SL:{sl_pct:.2f}% "
        f"({'LIMIT' if USE_LIMIT else 'MARKET'})"
    )

    try:
        client.set_leverage(symbol, LEVERAGE)

        # FIX-1: usar LIMIT para reducir fees
        if USE_LIMIT:
            offset = 1 - 0.0003 if direction == "long" else 1 + 0.0003
            limit_price = round(price * offset, 8)
            params = {
                "symbol":       symbol,
                "side":         side,
                "type":         "LIMIT",
                "price":        f"{limit_price:.8g}",
                "quantity":     f"{qty:.6g}",
                "timeInForce":  "GTC",
            }
            if pos_side != "BOTH":
                params["positionSide"] = pos_side
            try:
                client._request("POST", "/openApi/swap/v2/trade/order", params)
                logger.info(f"  LIMIT {direction.upper()} {symbol} @ ${limit_price:.6g} ✅")
            except BingXError:
                logger.warning(f"  LIMIT falló, usando MARKET")
                client.place_market_order(symbol, side, qty, position_side=pos_side)
        else:
            client.place_market_order(symbol, side, qty, position_side=pos_side)

        # Esperar confirmación de posición
        time.sleep(2)

        open_trades[symbol] = {
            "position":      direction,
            "entry_price":   price,
            "entry_qty":     qty,
            "position_side": pos_side,
            "tp_price":      tp_price,
            "sl_price":      sl_price,
            "tp_pct":        round(tp_pct, 2),
            "sl_pct":        round(sl_pct, 2),
            "opened_at":     datetime.now(timezone.utc),
        }

        # Colocar TP y SL
        tp_sl = client.place_tp_sl(symbol, direction, qty, tp_price, sl_price, position_side=pos_side)
        stats["entries_today"] += 1

        emoji     = "🟢" if direction == "long" else "🔴"
        trend_str = "ALCISTA" if signals["trend"] == 1 else "BAJISTA"
        tp_icon   = "✅" if tp_sl["tp"] else "❌"
        sl_icon   = "✅" if tp_sl["sl"] else "❌"
        ord_type  = "LIMIT" if USE_LIMIT else "MARKET"

        client.send_telegram(
            f"<b>{emoji} {direction.upper()} ABIERTO [Multi-Symbol v4]</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME} | {LEVERAGE}x | {ord_type}\n"
            f"Precio: ${price:.4f} | Qty: {qty}\n"
            f"TP {tp_icon}: ${tp_price:.4f} (+{tp_pct:.2f}%)\n"
            f"SL {sl_icon}: ${sl_price:.4f} (-{sl_pct:.2f}%)\n"
            f"Tendencia: {trend_str} | Prob: {signals['probability']:.1%}\n"
            f"Balance: ${balance:.2f} USDT | BTC 1h: {btc_trend_1h:+.2f}%\n"
            f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )

    except BingXError as e:
        logger.error(f"  Error abriendo {symbol}: {e}")
        if symbol in open_trades:
            del open_trades[symbol]
        client.send_telegram(f"<b>⚠️ Error {direction.upper()} {symbol}</b>\n{e}")


def analyze_symbol(symbol: str, balance: float):
    """Gestiona posición abierta O busca nueva entrada."""

    # ── Gestión de posición abierta ───────────────────────────
    if symbol in open_trades:
        t = open_trades[symbol]
        try:
            df      = get_ohlcv(symbol)
            signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
        except Exception as e:
            logger.debug(f"  {symbol} señales error: {e}")
            return

        cur       = signals["close"]
        direction = t["position"]
        prob      = signals["probability"]

        # Verificar TP/SL manualmente (por si BingX no los ejecutó)
        tp = t.get("tp_price", 0)
        sl = t.get("sl_price", 0)
        if tp and sl:
            if direction == "long":
                if cur >= tp:
                    handle_exit(symbol, signals, f"TP alcanzado ${cur:.4f}")
                    return
                if cur <= sl:
                    handle_exit(symbol, signals, f"SL alcanzado ${cur:.4f}")
                    return
            else:
                if cur <= tp:
                    handle_exit(symbol, signals, f"TP alcanzado ${cur:.4f}")
                    return
                if cur >= sl:
                    handle_exit(symbol, signals, f"SL alcanzado ${cur:.4f}")
                    return

        # Salida por señal de estrategia
        if prob >= EXIT_PROB:
            handle_exit(symbol, signals, f"Prob reversión {prob:.1%}")
            return
        if direction == "long" and signals["trend"] == -1:
            handle_exit(symbol, signals, "Tendencia viró bajista")
            return
        if direction == "short" and signals["trend"] == 1:
            handle_exit(symbol, signals, "Tendencia viró alcista")
            return
        return

    # ── Búsqueda de nueva entrada ─────────────────────────────
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

    # FIX-3: Filtro ATR — movimiento mínimo para cubrir fees
    atr_val = calc_atr(df, 14)
    atr_pct = atr_val / signals["close"] * 100
    if atr_pct < MIN_ATR_PCT:
        logger.debug(f"  {symbol}: ATR {atr_pct:.3f}% < {MIN_ATR_PCT}% — omitido")
        return

    # FIX-9: Filtro de volumen
    if not vol_spike_ok(df):
        logger.debug(f"  {symbol}: volumen insuficiente — omitido")
        return

    if signals["bullish_entry"]:
        # FIX-7: filtro BTC
        if not btc_filter_ok("long"):
            logger.info(f"  {symbol}: LONG bloqueado por BTC tendencia ({btc_trend_1h:+.2f}%)")
            return
        handle_entry(symbol, signals, "long", balance, df)

    elif ALLOW_SHORT and signals["bearish_entry"]:
        if not btc_filter_ok("short"):
            logger.info(f"  {symbol}: SHORT bloqueado por BTC tendencia ({btc_trend_1h:+.2f}%)")
            return
        handle_entry(symbol, signals, "short", balance, df)


def send_report(balance: float):
    total = stats["wins"] + stats["losses"]
    wr    = stats["wins"] / total * 100 if total > 0 else 0
    bl_count = sum(1 for n in blacklist.values() if n >= MAX_LOSS_STREAK)

    pos_lines = ""
    for sym, t in open_trades.items():
        try:
            tk  = req.get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
                          params={"symbol": sym}, timeout=5).json()
            cur = float(tk["data"]["lastPrice"]) if tk.get("code") == 0 else t["entry_price"]
        except Exception:
            cur = t["entry_price"]
        d   = t["position"]
        pct = ((cur - t["entry_price"]) / t["entry_price"] * 100) if d == "long" \
              else ((t["entry_price"] - cur) / t["entry_price"] * 100)
        pos_lines += f"  {d.upper()} {sym}: {pct:+.2f}%\n"

    no_pos = "  sin posiciones\n"
    client.send_telegram(
        f"<b>📊 Reporte Multi-Symbol v4 #{stats['cycle']}</b>\n"
        f"Balance: ${balance:.2f} USDT\n"
        f"Abiertos: {len(open_trades)}/{MAX_OPEN_TRADES}\n"
        f"{pos_lines if pos_lines else no_pos}"
        f"Trades: {total} | WR: {wr:.1f}% ({stats['wins']}W/{stats['losses']}L)\n"
        f"PnL neto: ${stats['total_pnl']:+.4f} | Fees: ${stats['total_fees']:.4f}\n"
        f"BTC 1h: {btc_trend_1h:+.2f}% | Blacklist: {bl_count} pares"
    )


# ─────────────────────── MAIN LOOP ───────────────────────────

def main():
    stats["account_mode"] = detect_account_mode()

    logger.info("=" * 70)
    logger.info("  Multi-Symbol Bot v4.0  |  LONG + SHORT")
    logger.info(f"  TF:{TIMEFRAME} | LEV:{LEVERAGE}x | MaxTrades:{MAX_OPEN_TRADES}")
    logger.info(f"  Riesgo:{RISK_PCT:.0%}/op | MaxSymbols:{MAX_SYMBOLS} | Vol>:{MIN_VOLUME_24H/1e6:.0f}M")
    logger.info(f"  TP:{TP_PCT}% SL:{SL_PCT}% | ATR TP/SL:{'ON' if USE_ATR_TPSL else 'OFF'}")
    logger.info(f"  LIMIT orders:{'ON' if USE_LIMIT else 'OFF'} | Fee:{FEE_RATE*100:.3f}%")
    logger.info(f"  Min hold:{MIN_HOLD_MIN}min | Cooldown:{COOLDOWN_MIN}min")
    logger.info(f"  BTC filter:{'ON' if USE_BTC_FILTER else 'OFF'} | Shorts:{'ON' if ALLOW_SHORT else 'OFF'}")
    logger.info(f"  Modo cuenta: {stats['account_mode'].upper()}")
    logger.info("=" * 70)

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
    update_btc_trend()

    client.send_telegram(
        f"<b>🚀 Multi-Symbol Bot v4.0 iniciado</b>\n"
        f"TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} trades\n"
        f"Símbolos: {len(symbols)} (vol>{MIN_VOLUME_24H/1e6:.0f}M)\n"
        f"LIMIT orders: {'ON ✅ (fee 0.02%)' if USE_LIMIT else 'OFF ⚠️ (fee 0.05%)'}\n"
        f"TP:{TP_PCT}% | SL:{SL_PCT}% | ATR TP/SL:{'ON' if USE_ATR_TPSL else 'OFF'}\n"
        f"Modo: {'DEMO' if DEMO_MODE else 'REAL'} | {stats['account_mode'].upper()}\n"
        f"Balance: ${stats['start_balance']:.2f} USDT"
    )

    while True:
        try:
            stats["cycle"] += 1
            cycle = stats["cycle"]

            syms = get_symbols()
            sync_all_positions()
            update_btc_trend()

            try:
                balance = client.get_balance()
            except BingXError as e:
                logger.error(f"Error balance: {e}")
                time.sleep(CHECK_INTERVAL)
                continue

            total = stats["wins"] + stats["losses"]
            wr    = stats["wins"] / total * 100 if total > 0 else 0
            logger.info(
                f"\n{'='*70}\n"
                f"  #{cycle} {now_utc()} | ${balance:.2f} | "
                f"Pos:{len(open_trades)}/{MAX_OPEN_TRADES} | "
                f"WR:{wr:.1f}% | PnL:${stats['total_pnl']:+.4f} | "
                f"Fees:${stats['total_fees']:.4f} | BTC:{btc_trend_1h:+.2f}%\n"
                f"{'='*70}"
            )

            # 1. Gestionar posiciones abiertas
            for sym in list(open_trades.keys()):
                analyze_symbol(sym, balance)
                time.sleep(0.3)

            # 2. FIX-8: máximo 1 nueva entrada por ciclo
            if len(open_trades) < MAX_OPEN_TRADES:
                logger.info(f"  Escaneando {len(syms)} símbolos…")
                new_entries = 0
                for i, sym in enumerate(syms):
                    if len(open_trades) >= MAX_OPEN_TRADES or new_entries >= 1:
                        break
                    if sym in open_trades:
                        continue
                    prev_count = len(open_trades)
                    analyze_symbol(sym, balance)
                    if len(open_trades) > prev_count:
                        new_entries += 1
                    time.sleep(0.2)
                    if (i + 1) % 5 == 0:
                        logger.info(f"  …{i+1}/{len(syms)}")
                logger.info(f"  Scan: {new_entries} nuevas entradas")
            else:
                logger.info(f"  Max trades — solo monitoreando")

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
                    f"<b>Bot Multi-Symbol v4 detenido</b>\n"
                    f"Trades: {total} | WR: {wr:.1f}%\n"
                    f"PnL neto: ${stats['total_pnl']:+.4f} USDT\n"
                    f"Fees pagadas: ${stats['total_fees']:.4f} USDT\n"
                    f"Balance final: ${final:.2f} USDT (${pnl:+.2f})"
                )
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            logger.error(f"Error ciclo #{cycle}: {e}", exc_info=True)
            client.send_telegram(f"<b>⚠️ Error Multi-Symbol Bot</b>\n{e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
