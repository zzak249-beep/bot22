"""
Bot Multi-Symbol LONG+SHORT — Zero Lag + Trend Reversal Probability
v4.2 — Máxima estabilidad y rentabilidad

FIXES v4.2:
  FIX-1  TP/SL: verifica órdenes activas tras colocarlas (has_tp_sl check)
  FIX-2  MIN_PROFIT_USDT: no abrir trade si ganancia esperada < $0.50
  FIX-3  Dedup absoluto: _opening set + verificación previa a cada apertura
  FIX-4  Defaults correctos: MAX_OPEN=2, MAX_DAY=6 (no 6 y 14 como antes)
  FIX-5  PnL diario separado del acumulado en reportes
  FIX-6  Quality score de strategy.py integrado en decisión de entrada
  FIX-7  Re-intentar TP/SL si tiene ⚠️ en ciclo siguiente
  FIX-8  SIREN/ONG bug: cooldown extendido para pares con pérdida > $0.50
"""

import os, sys, time, json, logging
import requests as req
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
log = logging.getLogger(__name__)

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

TIMEFRAME       = _env("TIMEFRAME",         "1h")
LEVERAGE        = _env("LEVERAGE",          "3",       int)
RISK_PCT        = _env("RISK_PCT",          "0.03",    float)
ZLEMA_LENGTH    = _env("ZLEMA_LENGTH",      "50",      int)
BAND_MULT       = _env("BAND_MULT",         "1.2",     float)
OSC_PERIOD      = _env("OSC_PERIOD",        "20",      int)
MIN_QUALITY     = _env("MIN_QUALITY",       "55",      int)    # score mínimo señal
ENTRY_MAX_PROB  = _env("ENTRY_MAX_PROB",    "0.50",    float)
EXIT_PROB       = _env("EXIT_PROB",         "0.80",    float)
TP_PCT          = _env("TP_PCT",            "4.5",     float)
SL_PCT          = _env("SL_PCT",            "2.0",     float)
USE_ATR_TPSL    = _env("USE_ATR_TPSL",     "true").lower()  == "true"
ATR_TP_MULT     = _env("ATR_TP_MULT",      "2.5",     float)
ATR_SL_MULT     = _env("ATR_SL_MULT",      "1.2",     float)
CHECK_INTERVAL  = _env("CHECK_INTERVAL",   "300",     int)
DEMO_MODE       = _env("DEMO_MODE",        "false").lower() == "true"
MIN_BALANCE     = _env("MIN_BALANCE",      "15",      float)
POSITION_MODE   = _env("POSITION_MODE",    "auto")
REPORT_EVERY    = _env("REPORT_EVERY",     "12",      int)
MAX_OPEN_TRADES = _env("MAX_OPEN_TRADES",  "2",       int)   # FIX-4: default 2
MIN_VOLUME_24H  = _env("MIN_VOLUME_24H",   "8000000", float)
MAX_SYMBOLS     = _env("MAX_SYMBOLS",      "12",      int)   # FIX-4: default 12
SCAN_INTERVAL   = _env("SCAN_INTERVAL",   "600",     int)
ALLOW_SHORT     = _env("ALLOW_SHORT",     "true").lower()  == "true"
COOLDOWN_MIN    = _env("COOLDOWN_MIN",    "90",      int)   # FIX-4: default 90
USE_LIMIT       = _env("USE_LIMIT_ORDERS","true").lower()  == "true"
MIN_HOLD_MIN    = _env("MIN_HOLD_MIN",    "20",      int)
MIN_ATR_PCT     = _env("MIN_ATR_PCT",     "0.6",     float)
USE_BTC_FILTER  = _env("USE_BTC_FILTER", "true").lower()  == "true"
MAX_LOSS_STREAK = _env("MAX_LOSS_STREAK", "2",       int)   # FIX-8: blacklist tras 2 (era 3)
MAX_TRADES_DAY  = _env("MAX_TRADES_DAY",  "6",       int)   # FIX-4: default 6
MIN_PROFIT_USDT = _env("MIN_PROFIT_USDT", "0.50",    float) # FIX-2: ganancia mínima esperada

FEE_RATE = 0.0002 if USE_LIMIT else 0.0005
PNL_FILE = "/tmp/bot22_pnl.json"

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
_closing:         set   = set()   # lock anti-doble cierre
_opening:         set   = set()   # FIX-3: lock anti-doble apertura
_recently_closed: dict  = {}      # {symbol: timestamp}
symbols:          list  = []
last_scan_ts:     float = 0
btc_trend_1h:     float = 0.0
trades_today:     int   = 0
pnl_today:        float = 0.0
today_date:       str   = ""

def _load_pnl():
    try:
        with open(PNL_FILE) as f:
            d = json.load(f)
            return (d.get("total_pnl", 0.0), d.get("total_fees", 0.0),
                    d.get("wins", 0), d.get("losses", 0))
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

def now_utc():
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

def reset_daily_if_needed():
    global trades_today, pnl_today, today_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != today_date:
        trades_today = 0
        pnl_today    = 0.0
        today_date   = today
        log.info(f"  Reset diario | trades: 0/{MAX_TRADES_DAY}")

def detect_account_mode():
    if POSITION_MODE.lower() in ("hedge", "oneway"):
        return POSITION_MODE.lower()
    try:
        for pos in client.get_positions("BTC-USDT"):
            side = str(pos.get("positionSide", "")).upper()
            if side in ("LONG", "SHORT"):
                return "hedge"
            elif side == "BOTH":
                return "oneway"
        return "hedge"
    except Exception:
        return "hedge"

def get_symbols():
    global symbols, last_scan_ts
    now = time.time()
    if symbols and (now - last_scan_ts) < SCAN_INTERVAL:
        return symbols
    try:
        d = req.get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker", timeout=15).json()
        if d.get("code") != 0:
            return symbols or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
        bl = {s for s, n in blacklist.items() if n >= MAX_LOSS_STREAK}
        items = []
        for t in d.get("data", []):
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            base = sym.replace("-USDT", "").upper()
            if any(ex in base for ex in EXCLUDED) or sym in bl:
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
        symbols      = [x["symbol"] for x in items[:MAX_SYMBOLS]]
        last_scan_ts = now
        log.info(f"Símbolos: {len(symbols)} (bl:{len(bl)})")
        return symbols
    except Exception as e:
        log.warning(f"get_symbols: {e}")
        return symbols or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

def get_ohlcv(symbol):
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

def calc_atr(df, period=14):
    if len(df) < period + 1:
        return 0.0
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    trs = [max(H[i]-L[i], abs(H[i]-C[i-1]), abs(L[i]-C[i-1]))
           for i in range(1, len(C))]
    return float(sum(trs[-period:]) / period)

def calc_expected_profit(price, qty, tp_pct):
    """FIX-2: calcula ganancia neta esperada en USDT."""
    notional    = qty * price * LEVERAGE
    gross_profit= notional * (tp_pct / 100)
    fees        = notional * FEE_RATE * 2
    return gross_profit - fees

def calculate_qty(balance, price):
    qty = round((balance * RISK_PCT * LEVERAGE) / price, 3)
    return max(qty, 0.001)

def is_on_cooldown(symbol):
    ts = cooldowns.get(symbol)
    if not ts:
        return False
    if time.time() > ts:
        del cooldowns[symbol]
        return False
    return True

def set_cooldown(symbol, minutes=None):
    cooldowns[symbol] = time.time() + (minutes or COOLDOWN_MIN) * 60

def update_blacklist(symbol, win, pnl_net=0.0):
    if win:
        blacklist[symbol] = 0
    else:
        blacklist[symbol] = blacklist.get(symbol, 0) + 1
    streak = blacklist[symbol]

    # FIX-8: cooldown extendido si pérdida grande
    if not win and pnl_net < -0.50:
        extra = COOLDOWN_MIN * 3
        log.warning(f"  [{symbol}] pérdida ${pnl_net:.3f} → cooldown {extra}min")
        set_cooldown(symbol, extra)

    if streak >= MAX_LOSS_STREAK:
        log.warning(f"  [{symbol}] blacklist ({streak} pérdidas)")
        client.send_telegram(f"<b>⛔ Blacklist: {symbol}</b> — {streak} pérdidas seguidas")
        set_cooldown(symbol, COOLDOWN_MIN * 6)

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

def btc_filter_ok(direction):
    if not USE_BTC_FILTER:
        return True
    if direction == "long"  and btc_trend_1h < -1.5:
        return False
    if direction == "short" and btc_trend_1h >  1.5:
        return False
    return True

def vol_spike_ok(df):
    if len(df) < 6:
        return True
    vols = df["volume"].values
    return vols[-1] > sum(vols[-6:-1]) / 5 * 0.7

def sync_all_positions():
    try:
        d    = client._request("GET", "/openApi/swap/v2/user/positions", {})
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
                    "has_tp_sl":     open_trades.get(sym, {}).get("has_tp_sl", False),
                    "opened_at":     open_trades.get(sym, {}).get("opened_at",
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
            if now - _recently_closed.get(sym, 0) < 300:  # 5min de protección
                continue
            log.info(f"  [SYNC] Recuperando {info['position'].upper()} {sym}")
            open_trades[sym] = info
    except Exception as e:
        log.warning(f"sync: {e}")


# ─────────────────────── CORE LOGIC ──────────────────────────

def retry_tp_sl_if_missing(symbol):
    """FIX-7: reintenta colocar TP/SL si la posición los tiene en ⚠️."""
    if symbol not in open_trades:
        return
    t = open_trades[symbol]
    if t.get("has_tp_sl"):
        return
    if not t.get("tp_price") or not t.get("sl_price"):
        return

    log.info(f"  [TP/SL RETRY] {symbol} — reintentando TP/SL…")
    tp_sl = client.place_tp_sl(
        symbol, t["position"], t["entry_qty"],
        t["tp_price"], t["sl_price"],
        position_side=t["position_side"]
    )
    if tp_sl["tp"] and tp_sl["sl"]:
        open_trades[symbol]["has_tp_sl"] = True
        log.info(f"  [TP/SL RETRY] {symbol} OK ✅")
        client.send_telegram(f"<b>✅ TP/SL colocados en reintento: {symbol}</b>")


def handle_exit(symbol, signals, reason):
    if symbol not in open_trades or symbol in _closing:
        if symbol in _closing:
            log.warning(f"  {symbol}: cierre en progreso — ignorado")
        return

    _closing.add(symbol)
    try:
        if symbol not in open_trades:
            return

        t         = open_trades[symbol]
        entry     = t["entry_price"]
        current   = signals["close"]
        direction = t["position"]
        qty       = t["entry_qty"]
        opened_at = t["opened_at"]
        hold_mins = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60

        # Respetar tiempo mínimo para salidas por señal (no para TP/SL)
        is_signal = any(w in reason.lower() for w in ("prob", "tendencia", "ranging"))
        if is_signal and hold_mins < MIN_HOLD_MIN:
            log.info(f"  {symbol}: hold {hold_mins:.1f}/{MIN_HOLD_MIN}min — ignorando")
            _closing.discard(symbol)
            return

        pnl_usd   = qty * (current - entry) * (1 if direction == "long" else -1)
        notional  = qty * entry * LEVERAGE
        fee_total = notional * FEE_RATE * 2
        pnl_net   = pnl_usd - fee_total
        win       = pnl_net > 0

        stats["total_pnl"]  += pnl_net
        stats["total_fees"] += fee_total
        global pnl_today
        pnl_today += pnl_net

        if win:
            stats["wins"]   += 1
        else:
            stats["losses"] += 1

        _save_pnl()
        update_blacklist(symbol, win, pnl_net)

        log.info(f"  CERRANDO {direction.upper()} {symbol} | {reason} | "
                 f"${pnl_net:+.4f} | {int(hold_mins)}min")

        client.cancel_symbol_orders(symbol)
        time.sleep(0.3)
        client.close_all_positions(symbol)

        _recently_closed[symbol] = time.time()
        del open_trades[symbol]

        total = stats["wins"] + stats["losses"]
        wr    = stats["wins"] / total * 100 if total > 0 else 0
        emoji = "✅" if win else "❌"

        client.send_telegram(
            f"<b>{emoji} CERRADO {direction.upper()} — {reason}</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME}\n"
            f"Entrada: ${entry:.4f} → Salida: ${current:.4f} | {int(hold_mins)}min\n"
            f"PnL bruto: ${pnl_usd:+.4f} | Fee: ${fee_total:.4f}\n"
            f"<b>PnL neto: ${pnl_net:+.4f} USDT</b>\n"
            f"Hoy: ${pnl_today:+.4f} | Acumulado: ${stats['total_pnl']:+.4f}\n"
            f"WR: {wr:.1f}% | Pos: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )

        set_cooldown(symbol)
        time.sleep(1)

    finally:
        _closing.discard(symbol)


def handle_entry(symbol, signals, direction, balance, df):
    global trades_today

    # FIX-3: lock absoluto de doble apertura
    if symbol in open_trades or symbol in _opening or symbol in _closing:
        return

    _opening.add(symbol)
    try:
        price = signals["close"]
        qty   = calculate_qty(balance, price)
        if qty <= 0:
            return

        # TP/SL dinámicos
        atr_val = calc_atr(df, 14)
        if USE_ATR_TPSL and atr_val > 0:
            tp_pct = max(atr_val / price * 100 * ATR_TP_MULT, TP_PCT)
            sl_pct = max(atr_val / price * 100 * ATR_SL_MULT, SL_PCT)
        else:
            tp_pct, sl_pct = TP_PCT, SL_PCT

        min_tp = (FEE_RATE * 2 * LEVERAGE * 100) + 2.0
        tp_pct = max(tp_pct, min_tp)

        # FIX-2: verificar ganancia esperada mínima
        expected_profit = calc_expected_profit(price, qty, tp_pct)
        if expected_profit < MIN_PROFIT_USDT:
            log.info(f"  {symbol}: ganancia esperada ${expected_profit:.3f} < ${MIN_PROFIT_USDT} — omitido")
            return

        tp_price = price * (1 + tp_pct/100) if direction == "long" else price * (1 - tp_pct/100)
        sl_price = price * (1 - sl_pct/100) if direction == "long" else price * (1 + sl_pct/100)

        side     = "BUY" if direction == "long" else "SELL"
        acc_mode = stats.get("account_mode", "hedge")
        pos_side = ("LONG" if direction == "long" else "SHORT") if acc_mode == "hedge" else "BOTH"

        log.info(f"  ABRIENDO {direction.upper()} {symbol} | ${price:.4f} "
                 f"qty={qty} TP:{tp_pct:.2f}% SL:{sl_pct:.2f}% profit_esp:${expected_profit:.3f}")

        client.set_leverage(symbol, LEVERAGE)

        if USE_LIMIT:
            offset = 1 - 0.0003 if direction == "long" else 1 + 0.0003
            lp     = round(price * offset, 8)
            params = {"symbol": symbol, "side": side, "type": "LIMIT",
                      "price": f"{lp:.8g}", "quantity": f"{qty:.6g}", "timeInForce": "GTC"}
            if pos_side != "BOTH":
                params["positionSide"] = pos_side
            try:
                client._request("POST", "/openApi/swap/v2/trade/order", params)
            except BingXError:
                client.place_market_order(symbol, side, qty, position_side=pos_side)
        else:
            client.place_market_order(symbol, side, qty, position_side=pos_side)

        time.sleep(2)

        # FIX-3: re-check tras espera
        if symbol in open_trades:
            log.warning(f"  {symbol}: ya en open_trades — no sobreescribir")
            return

        open_trades[symbol] = {
            "position":      direction,
            "entry_price":   price,
            "entry_qty":     qty,
            "position_side": pos_side,
            "tp_price":      tp_price,
            "sl_price":      sl_price,
            "tp_pct":        round(tp_pct, 2),
            "sl_pct":        round(sl_pct, 2),
            "has_tp_sl":     False,
            "opened_at":     datetime.now(timezone.utc),
        }
        _recently_closed.pop(symbol, None)

        # Colocar TP/SL
        tp_sl = client.place_tp_sl(symbol, direction, qty, tp_price, sl_price,
                                   position_side=pos_side)
        open_trades[symbol]["has_tp_sl"] = tp_sl["tp"] and tp_sl["sl"]

        trades_today += 1
        q_score = signals.get("quality_bull" if direction == "long" else "quality_bear", 0)

        emoji    = "🟢" if direction == "long" else "🔴"
        tp_icon  = "✅" if tp_sl["tp"] else "❌"
        sl_icon  = "✅" if tp_sl["sl"] else "❌"
        ord_type = "LIMIT" if USE_LIMIT else "MARKET"

        client.send_telegram(
            f"<b>{emoji} {direction.upper()} ABIERTO [v4.2]</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME} | {LEVERAGE}x | {ord_type}\n"
            f"Precio: ${price:.4f} | Qty: {qty}\n"
            f"TP {tp_icon}: ${tp_price:.4f} (+{tp_pct:.2f}%)\n"
            f"SL {sl_icon}: ${sl_price:.4f} (-{sl_pct:.2f}%)\n"
            f"Profit esp: ${expected_profit:.3f} | Score: {q_score}/100\n"
            f"RSI: {signals.get('rsi','?')} | Prob: {signals['probability']:.1%}\n"
            f"BTC 1h: {btc_trend_1h:+.2f}% | Hoy: {trades_today}/{MAX_TRADES_DAY}\n"
            f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )

    except BingXError as e:
        log.error(f"  Error abriendo {symbol}: {e}")
        open_trades.pop(symbol, None)
        client.send_telegram(f"<b>⚠️ Error {direction.upper()} {symbol}</b>\n{e}")
    finally:
        _opening.discard(symbol)


def analyze_symbol(symbol, balance):
    # ── Posición abierta ──────────────────────────────────────
    if symbol in open_trades:
        if symbol in _closing:
            return
        t = open_trades[symbol]

        # FIX-7: reintentar TP/SL si faltan
        if not t.get("has_tp_sl"):
            retry_tp_sl_if_missing(symbol)

        try:
            df      = get_ohlcv(symbol)
            signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD, MIN_QUALITY)
        except Exception as e:
            log.debug(f"  {symbol} señales: {e}")
            return

        cur  = signals["close"]
        d    = t["position"]
        tp   = t.get("tp_price", 0)
        sl   = t.get("sl_price", 0)

        if tp and sl:
            if d == "long":
                if cur >= tp:
                    handle_exit(symbol, signals, f"TP ${cur:.4f}")
                    return
                if cur <= sl:
                    handle_exit(symbol, signals, f"SL ${cur:.4f}")
                    return
            else:
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
        if d == "long"  and signals["trend"] == -1:
            handle_exit(symbol, signals, "Tendencia viró bajista")
            return
        if d == "short" and signals["trend"] ==  1:
            handle_exit(symbol, signals, "Tendencia viró alcista")
            return
        return

    # ── Nueva entrada ─────────────────────────────────────────
    if len(open_trades) >= MAX_OPEN_TRADES:
        return
    if trades_today >= MAX_TRADES_DAY:
        return
    if is_on_cooldown(symbol):
        return
    if time.time() - _recently_closed.get(symbol, 0) < 300:
        return

    try:
        df      = get_ohlcv(symbol)
        signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD, MIN_QUALITY)
    except Exception as e:
        log.debug(f"  {symbol}: {e}")
        return

    if balance < MIN_BALANCE:
        return
    if signals["probability"] >= ENTRY_MAX_PROB:
        return
    if signals.get("ranging"):
        return

    atr_pct = calc_atr(df, 14) / signals["close"] * 100
    if atr_pct < MIN_ATR_PCT:
        return
    if not vol_spike_ok(df):
        return

    if signals["bullish_entry"] and signals.get("quality_bull", 0) >= MIN_QUALITY:
        if not btc_filter_ok("long"):
            return
        handle_entry(symbol, signals, "long", balance, df)

    elif ALLOW_SHORT and signals["bearish_entry"] and signals.get("quality_bear", 0) >= MIN_QUALITY:
        if not btc_filter_ok("short"):
            return
        handle_entry(symbol, signals, "short", balance, df)


def send_report(balance):
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
        d     = t["position"]
        pct   = ((cur - t["entry_price"]) / t["entry_price"] * 100) if d == "long" \
                else ((t["entry_price"] - cur) / t["entry_price"] * 100)
        tp_sl = "✅" if t.get("has_tp_sl") else "⚠️"
        pos_lines += f"  {d.upper()} {sym}: {pct:+.2f}% TP/SL:{tp_sl}\n"

    no_pos = "  sin posiciones\n"
    client.send_telegram(
        f"<b>📊 Reporte Multi-Symbol v4.2 #{stats['cycle']}</b>\n"
        f"Balance: ${balance:.2f} | BTC 1h: {btc_trend_1h:+.2f}%\n"
        f"Abiertos: {len(open_trades)}/{MAX_OPEN_TRADES}\n"
        f"{pos_lines if pos_lines else no_pos}"
        f"Trades: {total} | WR: {wr:.1f}% ({stats['wins']}W/{stats['losses']}L)\n"
        f"Hoy: {trades_today}/{MAX_TRADES_DAY} trades | PnL hoy: ${pnl_today:+.4f}\n"
        f"Acumulado: ${stats['total_pnl']:+.4f} | Fees: ${stats['total_fees']:.4f}\n"
        f"Blacklist: {bl} pares"
    )


# ─────────────────────── MAIN LOOP ───────────────────────────

def main():
    stats["account_mode"] = detect_account_mode()
    reset_daily_if_needed()

    log.info("=" * 70)
    log.info("  Multi-Symbol Bot v4.2  |  LONG + SHORT")
    log.info(f"  TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} pos | MaxDay:{MAX_TRADES_DAY}")
    log.info(f"  LIMIT:{'ON' if USE_LIMIT else 'OFF'} ({FEE_RATE*100:.3f}%) | TP:{TP_PCT}% SL:{SL_PCT}%")
    log.info(f"  Cooldown:{COOLDOWN_MIN}min | MinHold:{MIN_HOLD_MIN}min | MinProfit:${MIN_PROFIT_USDT}")
    log.info(f"  Entry: prob<{ENTRY_MAX_PROB:.0%} score>={MIN_QUALITY} | Exit: prob>={EXIT_PROB:.0%}")
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
        f"<b>🚀 Multi-Symbol Bot v4.2 iniciado</b>\n"
        f"FIXES: TP/SL workingType ✅ | MinProfit ✅ | Score ✅ | Blacklist×2 ✅\n"
        f"TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} | MaxDay:{MAX_TRADES_DAY}\n"
        f"LIMIT:{'✅ 0.02%' if USE_LIMIT else '⚠️'} | Símbolos:{len(symbols)}\n"
        f"TP:{TP_PCT}% SL:{SL_PCT}% MinProfit:${MIN_PROFIT_USDT}\n"
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
            except BingXError:
                time.sleep(CHECK_INTERVAL)
                continue

            total = stats["wins"] + stats["losses"]
            wr    = stats["wins"] / total * 100 if total > 0 else 0
            log.info(
                f"\n{'='*70}\n"
                f"  #{stats['cycle']} {now_utc()} | ${balance:.2f} | "
                f"Pos:{len(open_trades)}/{MAX_OPEN_TRADES} | WR:{wr:.1f}% | "
                f"PnL hoy:${pnl_today:+.4f} | BTC:{btc_trend_1h:+.2f}% | "
                f"Hoy:{trades_today}/{MAX_TRADES_DAY}\n"
                f"{'='*70}"
            )

            for sym in list(open_trades.keys()):
                analyze_symbol(sym, balance)
                time.sleep(0.3)

            if len(open_trades) < MAX_OPEN_TRADES and trades_today < MAX_TRADES_DAY:
                log.info(f"  Escaneando {len(syms)} símbolos…")
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
                log.info(f"  Scan: {new_entries} entradas | {len(syms)} analizados")
            else:
                log.info(f"  Max trades o límite diario — solo monitoreando")

            if REPORT_EVERY > 0 and stats["cycle"] % REPORT_EVERY == 0:
                send_report(balance)

            log.info(f"  Próximo ciclo en {CHECK_INTERVAL}s\n")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            try:
                final = client.get_balance()
                total = stats["wins"] + stats["losses"]
                wr    = stats["wins"] / total * 100 if total > 0 else 0
                client.send_telegram(
                    f"<b>Bot v4.2 detenido</b>\n"
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
            client.send_telegram(f"<b>⚠️ Error Bot v4.2</b>\n{e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
