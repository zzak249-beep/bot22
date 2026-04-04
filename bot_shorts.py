"""
Bot SHORT-Only Multi-Symbol — Zero Lag + Trend Reversal Probability
v2.1 — Fixes críticos

FIXES v2.1 (mismos que bot.py v4.1):
  FIX-A  Doble cierre eliminado: _closing set + _recently_closed
  FIX-B  sync_all_positions no restaura posiciones cerradas < 5min
  FIX-C  handle_exit idempotente con lock
  FIX-D  PnL persistente en JSON
  FIX-E  Confirmación ZLEMA antes de entrar
  FIX-F  MAX_TRADES_DAY: límite diario
  FIX-G  Deduplicación estricta de símbolo
  FIX-H  Momentum bajista más estricto (3 velas rojas + precio bajo MA)
"""

import os, sys, time, json, logging
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
log = logging.getLogger(__name__)

# ──────────────────────── CONFIG ─────────────────────────────

def _senv(key, default=None, cast=str):
    val = os.getenv(f"SH_{key}") or os.getenv(key, default)
    if val is None:
        raise EnvironmentError(f"Variable requerida: {key}")
    return cast(val)

API_KEY        = _senv("BINGX_API_KEY")
SECRET_KEY     = _senv("BINGX_SECRET_KEY")
TELEGRAM_TOKEN = _senv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = _senv("TELEGRAM_CHAT_ID",   "")

TIMEFRAME       = _senv("TIMEFRAME",        "1h")
LEVERAGE        = _senv("LEVERAGE",         "3",       int)
RISK_PCT        = _senv("RISK_PCT",         "0.02",    float)
ZLEMA_LENGTH    = _senv("ZLEMA_LENGTH",     "50",      int)
BAND_MULT       = _senv("BAND_MULT",        "1.2",     float)
OSC_PERIOD      = _senv("OSC_PERIOD",       "20",      int)
ENTRY_MAX_PROB  = _senv("ENTRY_MAX_PROB",   "0.45",    float)  # aún más estricto en shorts
EXIT_PROB       = _senv("EXIT_PROB",        "0.78",    float)
TP_PCT          = _senv("TP_PCT",           "4.0",     float)
SL_PCT          = _senv("SL_PCT",           "2.0",     float)
USE_ATR_TPSL    = _senv("USE_ATR_TPSL",    "true").lower() == "true"
ATR_TP_MULT     = _senv("ATR_TP_MULT",     "2.2",     float)
ATR_SL_MULT     = _senv("ATR_SL_MULT",     "1.1",     float)
CHECK_INTERVAL  = _senv("CHECK_INTERVAL",  "300",     int)
DEMO_MODE       = _senv("DEMO_MODE",       "false").lower() == "true"
MIN_BALANCE     = _senv("MIN_BALANCE",     "10",      float)
POSITION_MODE   = _senv("POSITION_MODE",   "auto")
REPORT_EVERY    = _senv("REPORT_EVERY",    "12",      int)
MAX_OPEN_TRADES = _senv("MAX_OPEN_TRADES", "2",       int)
MIN_VOLUME_24H  = _senv("MIN_VOLUME_24H",  "5000000", float)
MAX_SYMBOLS     = _senv("MAX_SYMBOLS",     "20",      int)
SCAN_INTERVAL   = _senv("SCAN_INTERVAL",  "600",     int)
COOLDOWN_MIN    = _senv("COOLDOWN_MIN",   "90",      int)
USE_LIMIT       = _senv("USE_LIMIT_ORDERS","true").lower() == "true"
MIN_HOLD_MIN    = _senv("MIN_HOLD_MIN",   "20",      int)
MIN_ATR_PCT     = _senv("MIN_ATR_PCT",    "0.6",     float)
USE_BTC_FILTER  = _senv("USE_BTC_FILTER","true").lower() == "true"
MAX_LOSS_STREAK = _senv("MAX_LOSS_STREAK","2",       int)
MAX_TRADES_DAY  = _senv("MAX_TRADES_DAY","5",        int)   # shorts: límite más bajo
MAX_PUMP_PCT    = _senv("SH_MAX_PUMP_PCT","4.0",     float)
MIN_DROP_PCT    = _senv("SH_MIN_DROP_PCT","0.5",     float)  # más estricto
MIN_RED_CANDLES = _senv("SH_MIN_RED_CANDLES","3",    int)    # 3 en vez de 2

FEE_RATE = 0.0002 if USE_LIMIT else 0.0005
PNL_FILE = "/tmp/bot22_shorts_pnl.json"

EXCLUDED = {
    'DOW','SP500','GOLD','SILVER','XAU','OIL','BRENT',
    'EUR','GBP','JPY','TSLA','AAPL','MSFT','NVDA',
    'COIN','MSTR','WHEAT','CORN',
}

# ──────────────────────── STATE ──────────────────────────────

client = BingXClient(
    API_KEY, SECRET_KEY, demo=DEMO_MODE,
    telegram_token=TELEGRAM_TOKEN, telegram_chat=TELEGRAM_CHAT,
)

open_trades:      dict  = {}
cooldowns:        dict  = {}
blacklist:        dict  = {}
_closing:         set   = set()
_recently_closed: dict  = {}
symbols:          list  = []
last_scan_ts:     float = 0
btc_trend_1h:     float = 0.0
trades_today:     int   = 0
today_date:       str   = ""

def _load_pnl():
    try:
        with open(PNL_FILE) as f:
            d = json.load(f)
            return d.get("total_pnl",0.0), d.get("total_fees",0.0), \
                   d.get("wins",0), d.get("losses",0)
    except Exception:
        return 0.0, 0.0, 0, 0

_pnl0, _fees0, _wins0, _losses0 = _load_pnl()

stats = {
    "cycle":         0,
    "wins":          _wins0,
    "losses":        _losses0,
    "total_pnl":     _pnl0,
    "total_fees":    _fees0,
    "start_balance": None,
    "account_mode":  None,
}

def _save_pnl():
    try:
        with open(PNL_FILE, "w") as f:
            json.dump({"total_pnl": stats["total_pnl"], "total_fees": stats["total_fees"],
                       "wins": stats["wins"], "losses": stats["losses"]}, f)
    except Exception:
        pass

# ─────────────────────── HELPERS ─────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

def reset_daily_if_needed():
    global trades_today, today_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != today_date:
        trades_today = 0
        today_date   = today

def detect_account_mode() -> str:
    if POSITION_MODE.lower() in ("hedge", "oneway"):
        return POSITION_MODE.lower()
    try:
        for pos in client.get_positions("BTC-USDT"):
            side = str(pos.get("positionSide","")).upper()
            if side in ("LONG","SHORT"):
                return "hedge"
            elif side == "BOTH":
                return "oneway"
        return "hedge"
    except Exception:
        return "hedge"

def get_symbols() -> list:
    global symbols, last_scan_ts
    now = time.time()
    if symbols and (now - last_scan_ts) < SCAN_INTERVAL:
        return symbols
    try:
        d = req.get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker", timeout=15).json()
        if d.get("code") != 0:
            return symbols or ["BTC-USDT","ETH-USDT"]
        bl = {s for s, n in blacklist.items() if n >= MAX_LOSS_STREAK}
        items = []
        for t in d.get("data",[]):
            sym  = t.get("symbol","")
            if not sym.endswith("-USDT"):
                continue
            base = sym.replace("-USDT","").upper()
            if any(ex in base for ex in EXCLUDED) or sym in bl:
                continue
            try:
                price  = float(t.get("lastPrice",0))
                vol    = float(t.get("volume",0)) * price
                change = float(t.get("priceChangePercent",0))
                if vol < MIN_VOLUME_24H or price < 0.000001:
                    continue
                if change > MAX_PUMP_PCT:  # excluir pumps
                    continue
                items.append({"symbol":sym,"vol":vol,"change":change})
            except Exception:
                continue
        items.sort(key=lambda x: x["change"])  # primero los que más bajaron
        symbols      = [x["symbol"] for x in items[:MAX_SYMBOLS]]
        last_scan_ts = now
        log.info(f"Símbolos SHORT: {len(symbols)} (bl:{len(bl)})")
        return symbols
    except Exception as e:
        log.warning(f"get_symbols error: {e}")
        return symbols or ["BTC-USDT","ETH-USDT"]

def get_ohlcv(symbol: str) -> pd.DataFrame:
    klines = client.get_klines(symbol, TIMEFRAME, limit=200)
    if not klines:
        raise RuntimeError(f"Sin velas {symbol}")
    df = pd.DataFrame(klines)
    if isinstance(klines[0], dict):
        df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    else:
        df.columns = (["open_time","open","high","low","close","volume"]
                      + list(range(len(df.columns)-6)))
    for col in ("open","high","low","close","volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).reset_index(drop=True)

def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 0.0
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    trs = [max(H[i]-L[i], abs(H[i]-C[i-1]), abs(L[i]-C[i-1]))
           for i in range(1, len(C))]
    return float(sum(trs[-period:]) / period)

def calculate_qty(balance: float, price: float) -> float:
    qty = round((balance * RISK_PCT * LEVERAGE) / price, 3)
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
    if blacklist[symbol] >= MAX_LOSS_STREAK:
        log.warning(f"  [{symbol}] blacklist SHORT")
        client.send_telegram(f"<b>⛔ Blacklist SHORT: {symbol}</b>")
        cooldowns[symbol] = time.time() + COOLDOWN_MIN * 6 * 60

def update_btc_trend():
    global btc_trend_1h
    try:
        d = client.get_klines("BTC-USDT", "1h", limit=3)
        if d and len(d) >= 2:
            closes = ([float(x["close"]) for x in d] if isinstance(d[0], dict)
                      else [float(x[4]) for x in d])
            btc_trend_1h = (closes[-1] - closes[-2]) / closes[-2] * 100
    except Exception:
        pass

def has_bearish_momentum(df: pd.DataFrame) -> bool:
    """FIX-H: confirmación reforzada — 3 velas rojas + precio bajo MA."""
    if len(df) < 8:
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

    if len(closes) >= 20:
        ma = sum(closes[-20:]) / 20
        if closes[-1] >= ma:
            return False

    # La última vela debe ser bajista (cierre < apertura)
    if closes[-1] >= opens[-1]:
        return False

    return True

def vol_spike_ok(df: pd.DataFrame) -> bool:
    if len(df) < 6:
        return True
    vols = df["volume"].values
    avg  = sum(vols[-6:-1]) / 5
    return vols[-1] > avg * 0.8

def sync_all_positions():
    try:
        d    = client._request("GET", "/openApi/swap/v2/user/positions", {})
        real = {}
        for p in (d if isinstance(d, list) else []):
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                sym  = p.get("symbol","")
                ps   = str(p.get("positionSide","BOTH")).upper()
                side = "long" if (ps == "LONG" or (ps == "BOTH" and amt > 0)) else "short"
                real[sym] = {
                    "position":      side,
                    "entry_price":   float(p.get("avgPrice",0) or p.get("entryPrice",0)),
                    "entry_qty":     abs(amt),
                    "position_side": ps,
                    "tp_price":      open_trades.get(sym,{}).get("tp_price",0),
                    "sl_price":      open_trades.get(sym,{}).get("sl_price",0),
                    "opened_at":     open_trades.get(sym,{}).get("opened_at",
                                     datetime.now(timezone.utc)),
                }
        for sym in list(open_trades.keys()):
            if sym not in real:
                log.info(f"  [SYNC] {sym} cerrado externamente")
                del open_trades[sym]
        now = time.time()
        for sym, info in real.items():
            if sym in open_trades:
                continue
            if now - _recently_closed.get(sym, 0) < 300:
                continue
            # Solo recuperar shorts
            if info["position"] == "short":
                log.info(f"  [SYNC] Recuperando SHORT {sym}")
                open_trades[sym] = info
    except Exception as e:
        log.warning(f"sync error: {e}")

# ─────────────────────── CORE LOGIC ──────────────────────────

def handle_exit(symbol: str, signals: dict, reason: str):
    if symbol not in open_trades:
        return
    if symbol in _closing:
        log.warning(f"  {symbol}: cierre en progreso — ignorando duplicado")
        return

    _closing.add(symbol)
    try:
        if symbol not in open_trades:
            return

        t         = open_trades[symbol]
        entry     = t["entry_price"]
        current   = signals["close"]
        qty       = t["entry_qty"]
        opened_at = t["opened_at"]

        hold_mins = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60
        is_signal = "prob" in reason.lower() or "tendencia" in reason.lower()
        if is_signal and hold_mins < MIN_HOLD_MIN:
            log.info(f"  {symbol}: hold mínimo {hold_mins:.1f}/{MIN_HOLD_MIN}min — ignorando")
            _closing.discard(symbol)
            return

        pnl_usd   = qty * (entry - current)
        notional  = qty * entry * LEVERAGE
        fee_total = notional * FEE_RATE * 2
        pnl_net   = pnl_usd - fee_total
        win       = pnl_net > 0

        stats["total_pnl"]  += pnl_net
        stats["total_fees"] += fee_total
        if win:
            stats["wins"]   += 1
        else:
            stats["losses"] += 1

        _save_pnl()
        update_blacklist(symbol, win)

        log.info(f"  CERRANDO SHORT {symbol} | {reason} | ${pnl_net:+.4f} | {int(hold_mins)}min")

        client.close_all_positions(symbol)
        _recently_closed[symbol] = time.time()
        del open_trades[symbol]

        total = stats["wins"] + stats["losses"]
        wr    = stats["wins"] / total * 100 if total > 0 else 0
        emoji = "✅" if win else "❌"

        client.send_telegram(
            f"<b>{emoji} SHORT CERRADO — {reason}</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME}\n"
            f"Entrada: ${entry:.4f} → Salida: ${current:.4f} | {int(hold_mins)}min\n"
            f"PnL bruto: ${pnl_usd:+.4f} | Fee: ${fee_total:.4f}\n"
            f"<b>PnL neto: ${pnl_net:+.4f} USDT</b>\n"
            f"Acumulado: ${stats['total_pnl']:+.4f} | Fees: ${stats['total_fees']:.4f}\n"
            f"WR: {wr:.1f}% ({stats['wins']}W/{stats['losses']}L)\n"
            f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )

        set_cooldown(symbol)
        time.sleep(1)

    finally:
        _closing.discard(symbol)


def handle_short_entry(symbol: str, signals: dict, balance: float, df: pd.DataFrame):
    global trades_today

    if symbol in open_trades:
        log.warning(f"  {symbol}: ya tiene posición — entrada ignorada")
        return
    if symbol in _closing:
        return

    price = signals["close"]
    qty   = calculate_qty(balance, price)
    if qty <= 0:
        return

    atr_val = calc_atr(df, 14)
    if USE_ATR_TPSL and atr_val > 0:
        tp_pct = max(atr_val / price * 100 * ATR_TP_MULT, TP_PCT)
        sl_pct = max(atr_val / price * 100 * ATR_SL_MULT, SL_PCT)
    else:
        tp_pct, sl_pct = TP_PCT, SL_PCT

    min_tp = (FEE_RATE * 2 * LEVERAGE * 100) + 1.5
    tp_pct = max(tp_pct, min_tp)

    tp_price = price * (1 - tp_pct / 100)
    sl_price = price * (1 + sl_pct / 100)

    acc_mode = stats.get("account_mode", "hedge")
    pos_side = "SHORT" if acc_mode == "hedge" else "BOTH"

    log.info(f"  SHORT {symbol} | ${price:.4f} qty={qty} TP:{tp_pct:.2f}% SL:{sl_pct:.2f}%")

    try:
        client.set_leverage(symbol, LEVERAGE)

        if USE_LIMIT:
            offset = 1 + 0.0003
            lp = round(price * offset, 8)
            params = {
                "symbol": symbol, "side": "SELL", "type": "LIMIT",
                "price": f"{lp:.8g}", "quantity": f"{qty:.6g}",
                "timeInForce": "GTC",
            }
            if pos_side != "BOTH":
                params["positionSide"] = pos_side
            try:
                client._request("POST", "/openApi/swap/v2/trade/order", params)
            except BingXError:
                client.place_market_order(symbol, "SELL", qty, position_side=pos_side)
        else:
            client.place_market_order(symbol, "SELL", qty, position_side=pos_side)

        time.sleep(2)

        if symbol in open_trades:
            log.warning(f"  {symbol}: ya en open_trades — no sobreescribir")
            return

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
        }
        _recently_closed.pop(symbol, None)

        tp_sl   = client.place_tp_sl(symbol, "short", qty, tp_price, sl_price, position_side=pos_side)
        trades_today += 1

        tp_icon  = "✅" if tp_sl["tp"] else "❌"
        sl_icon  = "✅" if tp_sl["sl"] else "❌"
        ord_type = "LIMIT" if USE_LIMIT else "MARKET"

        client.send_telegram(
            f"<b>🔴 SHORT ABIERTO [v2.1]</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME} | {LEVERAGE}x | {ord_type}\n"
            f"Precio: ${price:.4f} | Qty: {qty}\n"
            f"TP {tp_icon}: ${tp_price:.4f} (-{tp_pct:.2f}%)\n"
            f"SL {sl_icon}: ${sl_price:.4f} (+{sl_pct:.2f}%)\n"
            f"Prob: {signals['probability']:.1%} | BTC 1h: {btc_trend_1h:+.2f}%\n"
            f"Trades hoy: {trades_today}/{MAX_TRADES_DAY}\n"
            f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )

    except BingXError as e:
        log.error(f"  Error SHORT {symbol}: {e}")
        open_trades.pop(symbol, None)
        client.send_telegram(f"<b>⚠️ Error SHORT {symbol}</b>\n{e}")


def analyze_symbol(symbol: str, balance: float):
    # ── Gestión de posición abierta ───────────────────────────
    if symbol in open_trades:
        if symbol in _closing:
            return
        t = open_trades[symbol]
        try:
            df      = get_ohlcv(symbol)
            signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
        except Exception as e:
            log.debug(f"  {symbol} error: {e}")
            return

        cur  = signals["close"]
        tp   = t.get("tp_price", 0)
        sl   = t.get("sl_price", 0)

        if tp and sl:
            if cur <= tp:
                handle_exit(symbol, signals, f"TP ${cur:.4f}")
                return
            if cur >= sl:
                handle_exit(symbol, signals, f"SL ${cur:.4f}")
                return

        prob = signals["probability"]
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
    if trades_today >= MAX_TRADES_DAY:
        return
    if is_on_cooldown(symbol):
        return
    if time.time() - _recently_closed.get(symbol, 0) < 300:
        return
    if USE_BTC_FILTER and btc_trend_1h > 1.5:
        return

    try:
        df      = get_ohlcv(symbol)
        signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD)
    except Exception as e:
        log.debug(f"  {symbol} error: {e}")
        return

    if balance < MIN_BALANCE:
        return
    if signals["probability"] >= ENTRY_MAX_PROB:
        return
    if not signals["bearish_entry"]:
        return

    atr_val = calc_atr(df, 14)
    if atr_val / signals["close"] * 100 < MIN_ATR_PCT:
        return
    if not has_bearish_momentum(df):
        return
    if not vol_spike_ok(df):
        return

    handle_short_entry(symbol, signals, balance, df)


def send_report(balance: float):
    total = stats["wins"] + stats["losses"]
    wr    = stats["wins"] / total * 100 if total > 0 else 0
    bl    = sum(1 for n in blacklist.values() if n >= MAX_LOSS_STREAK)

    pos_lines = ""
    for sym, t in open_trades.items():
        try:
            tk  = req.get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
                          params={"symbol": sym}, timeout=5).json()
            cur = float(tk["data"]["lastPrice"]) if tk.get("code") == 0 else t["entry_price"]
        except Exception:
            cur = t["entry_price"]
        pct    = (t["entry_price"] - cur) / t["entry_price"] * 100
        tp_ok  = "✅" if t.get("tp_price") else "⚠️"
        pos_lines += f"  SHORT {sym}: {pct:+.2f}% TP/SL:{tp_ok}\n"

    no_pos = "  sin posiciones\n"
    client.send_telegram(
        f"<b>📊 Reporte Short Bot v2.1 #{stats['cycle']}</b>\n"
        f"Balance: ${balance:.2f} | BTC 1h: {btc_trend_1h:+.2f}%\n"
        f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}\n"
        f"{pos_lines if pos_lines else no_pos}"
        f"Trades: {total} | WR: {wr:.1f}% ({stats['wins']}W/{stats['losses']}L)\n"
        f"PnL acumulado: ${stats['total_pnl']:+.4f} | Fees: ${stats['total_fees']:.4f}\n"
        f"Hoy: {trades_today}/{MAX_TRADES_DAY} | Blacklist: {bl}"
    )


# ─────────────────────── MAIN LOOP ───────────────────────────

def main():
    stats["account_mode"] = detect_account_mode()
    reset_daily_if_needed()

    log.info("=" * 70)
    log.info("  Short-Only Bot v2.1  |  Solo Shorts")
    log.info(f"  TF:{TIMEFRAME} | LEV:{LEVERAGE}x | MaxTrades:{MAX_OPEN_TRADES} | MaxDay:{MAX_TRADES_DAY}")
    log.info(f"  LIMIT:{'ON' if USE_LIMIT else 'OFF'} ({FEE_RATE*100:.3f}%) | TP:{TP_PCT}% SL:{SL_PCT}%")
    log.info(f"  Entry prob<{ENTRY_MAX_PROB:.0%} | Exit>={EXIT_PROB:.0%} | MinHold:{MIN_HOLD_MIN}min")
    log.info(f"  Momentum: caída>{MIN_DROP_PCT}% + {MIN_RED_CANDLES} velas rojas")
    log.info(f"  PnL acumulado: ${stats['total_pnl']:+.4f}")
    log.info("=" * 70)

    if DEMO_MODE:
        log.warning("MODO DEMO")

    try:
        stats["start_balance"] = client.get_balance()
        log.info(f"Balance: ${stats['start_balance']:.2f} USDT")
    except BingXError as e:
        log.error(f"No se pudo conectar: {e}")
        sys.exit(1)

    sync_all_positions()
    get_symbols()
    update_btc_trend()

    client.send_telegram(
        f"<b>🔴 Short Bot v2.1 iniciado</b>\n"
        f"FIXES: doble-cierre ✅ | PnL persistente ✅ | Momentum ×3 velas ✅\n"
        f"TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} | MaxDay:{MAX_TRADES_DAY}\n"
        f"LIMIT: {'ON ✅ (0.02%)' if USE_LIMIT else 'OFF ⚠️'}\n"
        f"Balance: ${stats['start_balance']:.2f} | PnL previo: ${stats['total_pnl']:+.4f}"
    )

    while True:
        try:
            stats["cycle"] += 1
            reset_daily_if_needed()
            syms = get_symbols()
            sync_all_positions()
            update_btc_trend()

            try:
                balance = client.get_balance()
            except BingXError as e:
                log.error(f"Error balance: {e}")
                time.sleep(CHECK_INTERVAL)
                continue

            total = stats["wins"] + stats["losses"]
            wr    = stats["wins"] / total * 100 if total > 0 else 0
            log.info(
                f"\n{'='*70}\n"
                f"  #{stats['cycle']} {now_utc()} | ${balance:.2f} | "
                f"Shorts:{len(open_trades)}/{MAX_OPEN_TRADES} | "
                f"WR:{wr:.1f}% | PnL:${stats['total_pnl']:+.4f} | "
                f"BTC:{btc_trend_1h:+.2f}% | Hoy:{trades_today}/{MAX_TRADES_DAY}\n"
                f"{'='*70}"
            )

            for sym in list(open_trades.keys()):
                analyze_symbol(sym, balance)
                time.sleep(0.3)

            if len(open_trades) < MAX_OPEN_TRADES and trades_today < MAX_TRADES_DAY:
                log.info(f"  Escaneando {len(syms)} para shorts…")
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
                log.info(f"  Scan: {new_entries} nuevos shorts")
            else:
                log.info(f"  {'Max trades' if len(open_trades)>=MAX_OPEN_TRADES else 'Límite diario'} — monitoreando")

            if REPORT_EVERY > 0 and stats["cycle"] % REPORT_EVERY == 0:
                send_report(balance)

            log.info(f"  Próximo ciclo en {CHECK_INTERVAL}s\n")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("Short Bot detenido")
            try:
                final = client.get_balance()
                total = stats["wins"] + stats["losses"]
                wr    = stats["wins"] / total * 100 if total > 0 else 0
                client.send_telegram(
                    f"<b>Short Bot v2.1 detenido</b>\n"
                    f"Trades: {total} | WR: {wr:.1f}%\n"
                    f"PnL acumulado: ${stats['total_pnl']:+.4f}\n"
                    f"Fees: ${stats['total_fees']:.4f}\n"
                    f"Balance: ${final:.2f}"
                )
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            log.error(f"Error ciclo #{stats['cycle']}: {e}", exc_info=True)
            client.send_telegram(f"<b>⚠️ Error Short Bot v2.1</b>\n{e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
