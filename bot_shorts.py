"""
Bot SHORT-Only Multi-Symbol — Zero Lag + Trend Reversal Probability
v2.1 — Short Specialist

FIXES vs v2.0:
  FIX-1  LIMIT orders en entradas: fee 0.02% vs 0.05% (ahorra 60%)
  FIX-2  MIN_HOLD_MIN: evita cierres en segundos que solo pagan fees
  FIX-3  Filtro ATR: solo entra si hay volatilidad suficiente
  FIX-4  Parámetros conservadores: menos trades, más calidad
  FIX-5  TP/SL dinámicos basados en ATR
  FIX-6  Blacklist de pares con pérdidas consecutivas
  FIX-7  Filtro BTC: no abrir shorts si BTC sube > 1.5%
  FIX-8  Confirmación de momentum bajista reforzada
  FIX-9  PnL neto con deducción de fees en reporte
  FIX-10 Anti-overtrading: max 1 entrada por ciclo
  FIX-11 TP/SL solo se colocan cuando la posición existe (fix error 109420)
         Si la orden es LIMIT y aún no se ejecutó, se reintenta en el próximo ciclo
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

def _senv(key, default=None, cast=str):
    """Lee SH_{key} primero, luego {key}, luego default."""
    val = os.getenv(f"SH_{key}") or os.getenv(key, default)
    if val is None:
        raise EnvironmentError(f"Variable requerida: {key}")
    return cast(val)

API_KEY        = _senv("BINGX_API_KEY")
SECRET_KEY     = _senv("BINGX_SECRET_KEY")
TELEGRAM_TOKEN = _senv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = _senv("TELEGRAM_CHAT_ID",   "")

# ── Estrategia ─────────────────────────────────────────────────
TIMEFRAME      = _senv("TIMEFRAME",      "1h")
LEVERAGE       = _senv("LEVERAGE",       "3",    int)
RISK_PCT       = _senv("RISK_PCT",       "0.02", float)
ZLEMA_LENGTH   = _senv("ZLEMA_LENGTH",   "50",   int)
BAND_MULT      = _senv("BAND_MULT",      "1.2",  float)
OSC_PERIOD     = _senv("OSC_PERIOD",     "20",   int)
ENTRY_MAX_PROB = _senv("ENTRY_MAX_PROB", "0.50", float)
EXIT_PROB      = _senv("EXIT_PROB",      "0.78", float)
TP_PCT         = _senv("TP_PCT",         "4.0",  float)
SL_PCT         = _senv("SL_PCT",         "2.0",  float)
USE_ATR_TPSL   = _senv("USE_ATR_TPSL",  "true").lower() == "true"
ATR_TP_MULT    = _senv("ATR_TP_MULT",    "2.2",  float)
ATR_SL_MULT    = _senv("ATR_SL_MULT",    "1.1",  float)

# ── Operación ──────────────────────────────────────────────────
CHECK_INTERVAL  = _senv("CHECK_INTERVAL",  "300",     int)
DEMO_MODE       = _senv("DEMO_MODE",       "false").lower() == "true"
MIN_BALANCE     = _senv("MIN_BALANCE",     "10",      float)
POSITION_MODE   = _senv("POSITION_MODE",   "auto")
REPORT_EVERY    = _senv("REPORT_EVERY",    "12",      int)
MAX_OPEN_TRADES = _senv("MAX_OPEN_TRADES", "2",       int)
MIN_VOLUME_24H  = _senv("MIN_VOLUME_24H",  "5000000", float)
MAX_SYMBOLS     = _senv("MAX_SYMBOLS",     "20",      int)
SCAN_INTERVAL   = _senv("SCAN_INTERVAL",   "600",     int)
COOLDOWN_MIN    = _senv("COOLDOWN_MIN",    "90",      int)
USE_LIMIT       = _senv("USE_LIMIT_ORDERS","true").lower() == "true"
MIN_HOLD_MIN    = _senv("MIN_HOLD_MIN",    "20",      int)
MIN_ATR_PCT     = _senv("MIN_ATR_PCT",     "0.6",     float)
USE_BTC_FILTER  = _senv("USE_BTC_FILTER",  "true").lower() == "true"
MAX_LOSS_STREAK = _senv("MAX_LOSS_STREAK", "2",       int)
MAX_PUMP_PCT    = _senv("SH_MAX_PUMP_PCT", "4.0",     float)
MIN_DROP_PCT    = _senv("SH_MIN_DROP_PCT", "0.4",     float)
MIN_RED_CANDLES = _senv("SH_MIN_RED_CANDLES","2",     int)
FEE_RATE        = 0.0002 if USE_LIMIT else 0.0005

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

open_trades:  dict  = {}
cooldowns:    dict  = {}
blacklist:    dict  = {}
symbols:      list  = []
last_scan_ts: float = 0
btc_trend_1h: float = 0.0

stats = {
    "cycle":         0,
    "wins":          0,
    "losses":        0,
    "total_pnl":     0.0,
    "total_fees":    0.0,
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
                return "hedge"
            elif side == "BOTH":
                return "oneway"
        return "hedge"
    except Exception:
        return "hedge"


def get_symbols() -> list:
    """Pares bajistas: ordenados por % de caída, excluyendo pumps."""
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
            return symbols or ["BTC-USDT", "ETH-USDT"]

        bl = {s for s, n in blacklist.items() if n >= MAX_LOSS_STREAK}
        items = []
        for t in d.get("data", []):
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            base = sym.replace("-USDT", "").upper()
            if any(ex in base for ex in EXCLUDED):
                continue
            if sym in bl:
                continue
            try:
                price  = float(t.get("lastPrice", 0))
                vol    = float(t.get("volume", 0)) * price
                change = float(t.get("priceChangePercent", 0))
                if vol < MIN_VOLUME_24H or price < 0.000001:
                    continue
                if change > MAX_PUMP_PCT:
                    continue
                items.append({"symbol": sym, "vol": vol, "change": change})
            except Exception:
                continue

        items.sort(key=lambda x: x["change"])
        symbols = [x["symbol"] for x in items[:MAX_SYMBOLS]]
        last_scan_ts = now
        logger.info(f"Símbolos SHORT: {len(symbols)} (blacklist:{len(bl)})")
        return symbols
    except Exception as e:
        logger.warning(f"get_symbols error: {e}")
        return symbols or ["BTC-USDT", "ETH-USDT"]


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
    if win:
        blacklist[symbol] = 0
    else:
        blacklist[symbol] = blacklist.get(symbol, 0) + 1
    streak = blacklist[symbol]
    if streak >= MAX_LOSS_STREAK:
        logger.warning(f"  [{symbol}] blacklist ({streak} pérdidas seguidas)")
        client.send_telegram(
            f"<b>⛔ Blacklist SHORT: {symbol}</b>\n"
            f"{streak} pérdidas consecutivas"
        )
        cooldowns[symbol] = time.time() + COOLDOWN_MIN * 6 * 60


def update_btc_trend():
    global btc_trend_1h
    try:
        d = client.get_klines("BTC-USDT", "1h", limit=3)
        if d and len(d) >= 2:
            closes = [float(x["close"]) for x in d] if isinstance(d[0], dict) \
                     else [float(x[4]) for x in d]
            btc_trend_1h = (closes[-1] - closes[-2]) / closes[-2] * 100
    except Exception:
        pass


def has_bearish_momentum(df: pd.DataFrame) -> bool:
    """FIX-8: confirmación reforzada de momentum bajista."""
    if len(df) < 6:
        return False
    closes = df["close"].values
    opens  = df["open"].values

    drop = (closes[-5] - closes[-1]) / closes[-5] * 100
    if drop < MIN_DROP_PCT:
        return False

    red = sum(1 for i in range(-MIN_RED_CANDLES - 1, 0)
              if closes[i] < opens[i])
    if red < MIN_RED_CANDLES:
        return False

    avg_close = sum(closes[-6:-1]) / 5
    if closes[-1] >= avg_close:
        return False

    return True


def vol_spike_ok(df: pd.DataFrame) -> bool:
    if len(df) < 6:
        return True
    vols = df["volume"].values
    avg_vol = sum(vols[-6:-1]) / 5
    return vols[-1] > avg_vol * 0.8


def sync_all_positions():
    try:
        d = client._request("GET", "/openApi/swap/v2/user/positions", {})
        real = {}
        for p in (d if isinstance(d, list) else []):
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                sym  = p.get("symbol", "")
                ps   = str(p.get("positionSide", "BOTH")).upper()
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
                    "tp_sl_placed":  open_trades.get(sym, {}).get("tp_sl_placed", False),
                }
        for sym in list(open_trades.keys()):
            if sym not in real:
                logger.info(f"  [SYNC] {sym} cerrado externamente")
                del open_trades[sym]
        for sym, info in real.items():
            if sym not in open_trades and info["position"] == "short":
                logger.info(f"  [SYNC] Recuperando SHORT {sym}")
                open_trades[sym] = info
    except Exception as e:
        logger.warning(f"sync error: {e}")


def _try_place_tp_sl(symbol: str):
    """
    FIX-11: Intenta colocar TP/SL solo si la posición ya existe en BingX.
    Se llama tanto al abrir como en cada ciclo de monitoreo hasta que se confirme.
    """
    t = open_trades.get(symbol)
    if not t or t.get("tp_sl_placed", False):
        return

    positions = client.get_positions(symbol)
    pos_exists = any(abs(float(p.get("positionAmt", 0))) > 0 for p in positions)

    if not pos_exists:
        logger.info(f"  {symbol}: posición aún no confirmada — TP/SL pendientes")
        return

    tp_sl = client.place_tp_sl(
        symbol, "short",
        t["entry_qty"], t["tp_price"], t["sl_price"],
        position_side=t["position_side"],
    )
    open_trades[symbol]["tp_sl_placed"] = True
    tp_icon = "✅" if tp_sl["tp"] else "❌"
    sl_icon = "✅" if tp_sl["sl"] else "❌"
    logger.info(f"  {symbol}: TP {tp_icon} SL {sl_icon} colocados (posición confirmada)")


# ─────────────────────── CORE LOGIC ──────────────────────────

def handle_exit(symbol: str, signals: dict, reason: str):
    if symbol not in open_trades:
        return

    t         = open_trades[symbol]
    entry     = t["entry_price"]
    current   = signals["close"]
    qty       = t["entry_qty"]
    opened_at = t["opened_at"]

    hold_mins    = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60
    is_signal    = "prob" in reason.lower() or "tendencia" in reason.lower()
    is_emergency = "sl" in reason.lower() or "tp" in reason.lower()

    if is_signal and not is_emergency and hold_mins < MIN_HOLD_MIN:
        logger.info(f"  {symbol}: holding mínimo {hold_mins:.1f}/{MIN_HOLD_MIN}min — ignorando señal")
        return

    pnl_usd     = qty * (entry - current)
    notional    = qty * entry * LEVERAGE
    fee_total   = notional * FEE_RATE * 2
    pnl_net     = pnl_usd - fee_total
    pnl_pct     = (entry - current) / entry * 100
    win         = pnl_net > 0

    stats["total_pnl"]  += pnl_net
    stats["total_fees"] += fee_total

    if win:
        stats["wins"] += 1
    else:
        stats["losses"] += 1

    update_blacklist(symbol, win)

    mins  = int(hold_mins)
    emoji = "✅" if win else "❌"
    logger.info(f"  CERRANDO SHORT {symbol} | {reason} | PnL neto: ${pnl_net:+.4f} | {mins}min")

    client.close_all_positions(symbol)

    client.send_telegram(
        f"<b>{emoji} SHORT CERRADO — {reason}</b>\n"
        f"Par: {symbol} | TF: {TIMEFRAME}\n"
        f"Entrada: ${entry:.4f} → Salida: ${current:.4f} | {mins}min\n"
        f"PnL bruto: ${pnl_usd:+.4f} | Fee: ${fee_total:.4f}\n"
        f"<b>PnL neto: ${pnl_net:+.4f} USDT</b>\n"
        f"Sesión: ${stats['total_pnl']:+.4f} | Fees: ${stats['total_fees']:.4f}\n"
        f"Posiciones: {len(open_trades)-1}/{MAX_OPEN_TRADES}"
    )

    set_cooldown(symbol)
    del open_trades[symbol]
    time.sleep(1)


def handle_short_entry(symbol: str, signals: dict, balance: float, df: pd.DataFrame):
    price = signals["close"]
    qty   = calculate_qty(balance, price)
    if qty <= 0:
        return

    atr_val = calc_atr(df, 14)
    if USE_ATR_TPSL and atr_val > 0:
        tp_dist = atr_val * ATR_TP_MULT
        sl_dist = atr_val * ATR_SL_MULT
        tp_pct  = tp_dist / price * 100
        sl_pct  = sl_dist / price * 100
    else:
        tp_pct = TP_PCT
        sl_pct = SL_PCT

    min_tp = (FEE_RATE * 2 * LEVERAGE * 100) + 1.5
    tp_pct = max(tp_pct, min_tp, TP_PCT)
    sl_pct = max(sl_pct, SL_PCT)

    tp_price = price * (1 - tp_pct / 100)
    sl_price = price * (1 + sl_pct / 100)

    acc_mode = stats.get("account_mode", "hedge")
    pos_side = "SHORT" if acc_mode == "hedge" else "BOTH"

    logger.info(
        f"  SHORT {symbol} | ${price:.4f} qty={qty} "
        f"TP:{tp_pct:.2f}% SL:{sl_pct:.2f}% "
        f"({'LIMIT' if USE_LIMIT else 'MARKET'})"
    )

    try:
        client.set_leverage(symbol, LEVERAGE)

        if USE_LIMIT:
            offset = 1 + 0.0003
            limit_price = round(price * offset, 8)
            params = {
                "symbol":       symbol,
                "side":         "SELL",
                "type":         "LIMIT",
                "price":        f"{limit_price:.8g}",
                "quantity":     f"{qty:.6g}",
                "timeInForce":  "GTC",
            }
            if pos_side != "BOTH":
                params["positionSide"] = pos_side
            try:
                client._request("POST", "/openApi/swap/v2/trade/order", params)
                logger.info(f"  LIMIT SHORT {symbol} @ ${limit_price:.6g} ✅")
            except BingXError:
                logger.warning(f"  LIMIT falló → MARKET")
                client.place_market_order(symbol, "SELL", qty, position_side=pos_side)
        else:
            client.place_market_order(symbol, "SELL", qty, position_side=pos_side)

        # Registrar trade ANTES de intentar TP/SL
        open_trades[symbol] = {
            "position":      "short",
            "entry_price":   price,
            "entry_qty":     qty,
            "position_side": pos_side,
            "tp_price":      tp_price,
            "sl_price":      sl_price,
            "tp_pct":        round(tp_pct, 2),
            "sl_pct":        round(sl_pct, 2),
            "opened_at":     datetime.now(timezone.utc),
            "tp_sl_placed":  False,   # FIX-11: flag para saber si TP/SL ya se puso
        }

        # FIX-11: esperar y verificar que la posición existe antes de poner TP/SL
        time.sleep(2)
        _try_place_tp_sl(symbol)

        t = open_trades.get(symbol, {})
        tp_sl_ok = t.get("tp_sl_placed", False)
        tp_icon  = "✅" if tp_sl_ok else "⏳"
        sl_icon  = "✅" if tp_sl_ok else "⏳"
        ord_type = "LIMIT" if USE_LIMIT else "MARKET"

        client.send_telegram(
            f"<b>🔴 SHORT ABIERTO [Short Specialist v2.1]</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME} | {LEVERAGE}x | {ord_type}\n"
            f"Precio: ${price:.4f} | Qty: {qty}\n"
            f"TP {tp_icon}: ${tp_price:.4f} (-{tp_pct:.2f}%)\n"
            f"SL {sl_icon}: ${sl_price:.4f} (+{sl_pct:.2f}%)\n"
            f"{'TP/SL pendientes confirmación posición' if not tp_sl_ok else ''}\n"
            f"Prob: {signals['probability']:.1%} | BTC 1h: {btc_trend_1h:+.2f}%\n"
            f"Balance: ${balance:.2f} USDT\n"
            f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )

    except BingXError as e:
        logger.error(f"  Error SHORT {symbol}: {e}")
        if symbol in open_trades:
            del open_trades[symbol]
        client.send_telegram(f"<b>⚠️ Error SHORT {symbol}</b>\n{e}")


def analyze_symbol(symbol: str, balance: float):
    """Gestiona posición abierta o busca nuevo short."""

    # ── Gestión de posición abierta ───────────────────────────
    if symbol in open_trades:
        t = open_trades[symbol]
        try:
            df      = get_ohlcv(symbol)
            signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
        except Exception as e:
            logger.debug(f"  {symbol} error: {e}")
            return

        # FIX-11: si el TP/SL no se pudo colocar antes, reintentar ahora
        if not t.get("tp_sl_placed", False):
            _try_place_tp_sl(symbol)

        cur  = signals["close"]
        prob = signals["probability"]
        tp   = t.get("tp_price", 0)
        sl   = t.get("sl_price", 0)

        # Verificar TP/SL manualmente (por si las órdenes no se ejecutaron en BingX)
        if tp and sl:
            if cur <= tp:
                handle_exit(symbol, signals, f"TP alcanzado ${cur:.4f}")
                return
            if cur >= sl:
                handle_exit(symbol, signals, f"SL alcanzado ${cur:.4f}")
                return

        if prob >= EXIT_PROB:
            handle_exit(symbol, signals, f"Prob reversión {prob:.1%}")
            return
        if signals["trend"] == 1:
            handle_exit(symbol, signals, "Tendencia viró alcista")
            return
        return

    # ── Búsqueda de nuevo short ───────────────────────────────
    if len(open_trades) >= MAX_OPEN_TRADES:
        return
    if is_on_cooldown(symbol):
        return

    if USE_BTC_FILTER and btc_trend_1h > 1.5:
        return

    try:
        df      = get_ohlcv(symbol)
        signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
    except Exception as e:
        logger.debug(f"  {symbol} error: {e}")
        return

    if balance < MIN_BALANCE:
        return
    if signals["probability"] >= ENTRY_MAX_PROB:
        return
    if not signals["bearish_entry"]:
        return

    atr_val = calc_atr(df, 14)
    atr_pct = atr_val / signals["close"] * 100
    if atr_pct < MIN_ATR_PCT:
        logger.debug(f"  {symbol}: ATR {atr_pct:.3f}% insuficiente")
        return

    if not has_bearish_momentum(df):
        logger.debug(f"  {symbol}: sin momentum bajista suficiente")
        return

    if not vol_spike_ok(df):
        logger.debug(f"  {symbol}: volumen insuficiente")
        return

    handle_short_entry(symbol, signals, balance, df)


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
        pct = (t["entry_price"] - cur) / t["entry_price"] * 100
        tpsl_status = "✅" if t.get("tp_sl_placed") else "⏳"
        pos_lines += f"  SHORT {sym}: {pct:+.2f}% TP/SL:{tpsl_status}\n"

    no_pos = "  sin posiciones\n"
    client.send_telegram(
        f"<b>📊 Reporte Short Bot v2.1 #{stats['cycle']}</b>\n"
        f"Balance: ${balance:.2f} USDT | BTC 1h: {btc_trend_1h:+.2f}%\n"
        f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}\n"
        f"{pos_lines if pos_lines else no_pos}"
        f"Trades: {total} | WR: {wr:.1f}% ({stats['wins']}W/{stats['losses']}L)\n"
        f"PnL neto: ${stats['total_pnl']:+.4f} | Fees: ${stats['total_fees']:.4f}\n"
        f"Blacklist: {bl_count} pares"
    )


# ─────────────────────── MAIN LOOP ───────────────────────────

def main():
    stats["account_mode"] = detect_account_mode()

    logger.info("=" * 70)
    logger.info("  SHORT-Only Bot v2.1  |  Solo Shorts")
    logger.info(f"  TF:{TIMEFRAME} | LEV:{LEVERAGE}x | MaxTrades:{MAX_OPEN_TRADES}")
    logger.info(f"  Riesgo:{RISK_PCT:.0%}/op | MaxSymbols:{MAX_SYMBOLS}")
    logger.info(f"  TP:{TP_PCT}% SL:{SL_PCT}% | ATR TP/SL:{'ON' if USE_ATR_TPSL else 'OFF'}")
    logger.info(f"  LIMIT:{'ON' if USE_LIMIT else 'OFF'} | Fee:{FEE_RATE*100:.3f}%")
    logger.info(f"  MinHold:{MIN_HOLD_MIN}min | Cooldown:{COOLDOWN_MIN}min")
    logger.info(f"  Momentum: caída>{MIN_DROP_PCT}% + {MIN_RED_CANDLES} velas rojas")
    logger.info(f"  BTC filter:{'ON' if USE_BTC_FILTER else 'OFF'}")
    logger.info(f"  FIX-11: TP/SL diferido hasta confirmar posición ✅")
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
        f"<b>🔴 Short-Only Bot v2.1 iniciado</b>\n"
        f"TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} shorts\n"
        f"Símbolos: {len(symbols)} (vol>{MIN_VOLUME_24H/1e6:.0f}M, no pumps>{MAX_PUMP_PCT}%)\n"
        f"LIMIT: {'ON ✅ (fee 0.02%)' if USE_LIMIT else 'OFF ⚠️'}\n"
        f"TP:{TP_PCT}% | SL:{SL_PCT}% | Cooldown:{COOLDOWN_MIN}min\n"
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
                f"Shorts:{len(open_trades)}/{MAX_OPEN_TRADES} | "
                f"WR:{wr:.1f}% | PnL:${stats['total_pnl']:+.4f} | "
                f"Fees:${stats['total_fees']:.4f} | BTC:{btc_trend_1h:+.2f}%\n"
                f"{'='*70}"
            )

            # 1. Gestionar posiciones abiertas
            for sym in list(open_trades.keys()):
                analyze_symbol(sym, balance)
                time.sleep(0.3)

            # 2. FIX-10: max 1 entrada nueva por ciclo
            if len(open_trades) < MAX_OPEN_TRADES:
                logger.info(f"  Escaneando {len(syms)} para shorts…")
                new_entries = 0
                for i, sym in enumerate(syms):
                    if len(open_trades) >= MAX_OPEN_TRADES or new_entries >= 1:
                        break
                    if sym in open_trades:
                        continue
                    prev = len(open_trades)
                    analyze_symbol(sym, balance)
                    if len(open_trades) > prev:
                        new_entries += 1
                    time.sleep(0.2)
                    if (i + 1) % 5 == 0:
                        logger.info(f"  …{i+1}/{len(syms)}")
                logger.info(f"  Scan: {new_entries} nuevos shorts")
            else:
                logger.info(f"  Max shorts — monitoreando")

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
                    f"<b>🔴 Short Bot v2.1 detenido</b>\n"
                    f"Trades: {total} | WR: {wr:.1f}%\n"
                    f"PnL neto: ${stats['total_pnl']:+.4f} USDT\n"
                    f"Fees pagadas: ${stats['total_fees']:.4f} USDT\n"
                    f"Balance final: ${final:.2f} (${pnl:+.2f})"
                )
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            logger.error(f"Error ciclo #{cycle}: {e}", exc_info=True)
            client.send_telegram(f"<b>⚠️ Error Short Bot</b>\n{e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
