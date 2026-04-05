"""
Bot SHORT-Only Multi-Symbol — v3.0
Mismos fixes CRÍTICOS que bot.py v5.0.
MARKET ONLY, cleanup al arrancar, circuit breaker, sin memes.
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
MIN_QUALITY     = _senv("MIN_QUALITY",      "65",      int)
ENTRY_MAX_PROB  = _senv("ENTRY_MAX_PROB",   "0.40",    float)
EXIT_PROB       = _senv("EXIT_PROB",        "0.78",    float)
TP_PCT          = _senv("TP_PCT",           "5.0",     float)
SL_PCT          = _senv("SL_PCT",           "2.5",     float)
USE_ATR_TPSL    = _senv("USE_ATR_TPSL",    "true").lower() == "true"
ATR_TP_MULT     = _senv("ATR_TP_MULT",     "3.0",     float)
ATR_SL_MULT     = _senv("ATR_SL_MULT",     "1.5",     float)
CHECK_INTERVAL  = _senv("CHECK_INTERVAL",  "300",     int)
DEMO_MODE       = _senv("DEMO_MODE",       "false").lower() == "true"
MIN_BALANCE     = _senv("MIN_BALANCE",     "20",      float)
POSITION_MODE   = _senv("POSITION_MODE",   "auto")
REPORT_EVERY    = _senv("REPORT_EVERY",    "12",      int)
MIN_VOLUME_24H  = _senv("MIN_VOLUME_24H",  "10000000",float)
MAX_SYMBOLS     = _senv("MAX_SYMBOLS",     "12",      int)
SCAN_INTERVAL   = _senv("SCAN_INTERVAL",  "900",     int)
COOLDOWN_MIN    = _senv("COOLDOWN_MIN",   "180",     int)  # 3h para shorts
MIN_HOLD_MIN    = _senv("MIN_HOLD_MIN",   "30",      int)
MIN_ATR_PCT     = _senv("MIN_ATR_PCT",    "1.0",     float)
USE_BTC_FILTER  = _senv("USE_BTC_FILTER","true").lower() == "true"
MAX_LOSS_STREAK = _senv("MAX_LOSS_STREAK","2",       int)
MIN_PROFIT_USDT = _senv("MIN_PROFIT_USDT","0.60",   float)
DAILY_LOSS_LIM  = _senv("DAILY_LOSS_LIMIT","4.0",   float)
MAX_PUMP_PCT    = _senv("SH_MAX_PUMP_PCT","5.0",     float)
MIN_DROP_PCT    = _senv("SH_MIN_DROP_PCT","0.8",     float)
MIN_RED_CANDLES = _senv("SH_MIN_RED_CANDLES","3",   int)

MAX_OPEN_TRADES = min(_senv("MAX_OPEN_TRADES", "2", int), 2)
MAX_TRADES_DAY  = min(_senv("MAX_TRADES_DAY",  "3", int), 3)

FEE_RATE = 0.0005
PNL_FILE = "/tmp/bot_shorts_v3_pnl.json"

EXCLUDED = {
    'DOW','SP500','GOLD','SILVER','XAU','OIL','BRENT',
    'EUR','GBP','JPY','TSLA','AAPL','MSFT','NVDA',
    'COIN','MSTR','WHEAT','CORN','BOME','PEPE','SHIB',
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
_opening:         set   = set()
_recently_closed: dict  = {}
symbols:          list  = []
last_scan_ts:     float = 0
btc_trend_1h:     float = 0.0
trades_today:     int   = 0
pnl_today:        float = 0.0
today_date:       str   = ""
circuit_until:    float = 0.0

def _load_pnl():
    try:
        with open(PNL_FILE) as f:
            d    = json.load(f)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if d.get("date", "") == today:
                return (d.get("total_pnl",0.0), d.get("total_fees",0.0),
                        d.get("wins",0), d.get("losses",0))
    except Exception:
        pass
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
            json.dump({"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                       "total_pnl": stats["total_pnl"], "total_fees": stats["total_fees"],
                       "wins": stats["wins"], "losses": stats["losses"]}, f)
    except Exception:
        pass

def now_utc():
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

def reset_daily_if_needed():
    global trades_today, pnl_today, today_date, circuit_until
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != today_date:
        trades_today  = 0
        pnl_today     = 0.0
        today_date    = today
        circuit_until = 0.0

def detect_account_mode():
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

def check_circuit_breaker() -> bool:
    if circuit_until and time.time() < circuit_until:
        mins = int((circuit_until - time.time()) / 60)
        log.warning(f"  [CIRCUIT SHORT] Pausado {mins}min")
        return True
    return False

def maybe_trigger_circuit():
    global circuit_until
    if pnl_today < -abs(DAILY_LOSS_LIM):
        circuit_until = time.time() + 2 * 3600
        client.send_telegram(
            f"<b>🔴 Circuit Breaker SHORT</b>\n"
            f"Pérdida: ${pnl_today:.4f} > ${DAILY_LOSS_LIM}\nPausado 2h"
        )

def get_symbols():
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
            sym = t.get("symbol","")
            if not sym.endswith("-USDT"):
                continue
            base = sym.replace("-USDT","").upper()
            if any(ex in base for ex in EXCLUDED) or sym in bl:
                continue
            try:
                price  = float(t.get("lastPrice",0))
                vol    = float(t.get("volume",0)) * price
                change = float(t.get("priceChangePercent",0))
                if vol < MIN_VOLUME_24H or price < 0.0001:
                    continue
                if change > MAX_PUMP_PCT:
                    continue
                items.append({"symbol":sym,"vol":vol,"change":change})
            except Exception:
                continue
        items.sort(key=lambda x: x["change"])
        symbols      = [x["symbol"] for x in items[:MAX_SYMBOLS]]
        last_scan_ts = now
        log.info(f"Símbolos SHORT: {len(symbols)}")
        return symbols
    except Exception as e:
        log.warning(f"get_symbols: {e}")
        return symbols or ["BTC-USDT","ETH-USDT"]

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
    trs = [max(H[i]-L[i], abs(H[i]-C[i-1]), abs(L[i]-C[i-1])) for i in range(1, len(C))]
    return float(sum(trs[-period:]) / period)

def calc_expected_profit(price, qty, tp_pct):
    notional = qty * price * LEVERAGE
    return notional * (tp_pct / 100) - notional * FEE_RATE * 2

def calculate_qty(balance, price):
    return max(round((balance * RISK_PCT * LEVERAGE) / price, 3), 0.001)

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
    blacklist[symbol] = 0 if win else blacklist.get(symbol, 0) + 1
    if not win and pnl_net < -0.80:
        set_cooldown(symbol, 360)
    if blacklist[symbol] >= MAX_LOSS_STREAK:
        client.send_telegram(f"<b>⛔ Blacklist SHORT: {symbol}</b>")
        set_cooldown(symbol, COOLDOWN_MIN * 10)

def update_btc_trend():
    global btc_trend_1h
    try:
        d = client.get_klines("BTC-USDT","1h",limit=3)
        if d and len(d) >= 2:
            closes = ([float(x["close"]) for x in d] if isinstance(d[0],dict)
                      else [float(x[4]) for x in d])
            btc_trend_1h = (closes[-1] - closes[-2]) / closes[-2] * 100
    except Exception:
        pass

def has_bearish_momentum(df) -> bool:
    if len(df) < 8:
        return False
    closes = df["close"].values
    opens  = df["open"].values
    if (closes[-5] - closes[-1]) / closes[-5] * 100 < MIN_DROP_PCT:
        return False
    red = sum(1 for i in range(-MIN_RED_CANDLES-1, 0) if closes[i] < opens[i])
    if red < MIN_RED_CANDLES:
        return False
    if len(closes) >= 20 and closes[-1] >= sum(closes[-20:]) / 20:
        return False
    return closes[-1] < opens[-1]

def sync_all_positions():
    try:
        all_pos = client.get_all_open_positions()
        real = {}
        for p in all_pos:
            sym  = p.get("symbol","")
            amt  = float(p.get("positionAmt",0))
            ps   = str(p.get("positionSide","BOTH")).upper()
            side = "long" if (ps == "LONG" or (ps == "BOTH" and amt > 0)) else "short"
            real[sym] = {
                "position":      side,
                "entry_price":   float(p.get("avgPrice",0) or p.get("entryPrice",0)),
                "entry_qty":     abs(amt),
                "position_side": ps,
                "tp_price":      open_trades.get(sym,{}).get("tp_price",0),
                "sl_price":      open_trades.get(sym,{}).get("sl_price",0),
                "has_tp_sl":     open_trades.get(sym,{}).get("has_tp_sl",False),
                "opened_at":     open_trades.get(sym,{}).get("opened_at",
                                 datetime.now(timezone.utc)),
            }
        for sym in list(open_trades.keys()):
            if sym not in real:
                del open_trades[sym]
        now = time.time()
        for sym, info in real.items():
            if sym in open_trades:
                continue
            if now - _recently_closed.get(sym,0) < 300:
                continue
            if info["position"] == "short":
                open_trades[sym] = info
    except Exception as e:
        log.warning(f"sync: {e}")

# ─────────────────────── CORE LOGIC ──────────────────────────

def retry_tp_sl(symbol):
    t = open_trades.get(symbol)
    if not t or t.get("has_tp_sl") or not t.get("tp_price"):
        return
    tp_sl = client.place_tp_sl(symbol,"short",t["entry_qty"],
                               t["tp_price"],t["sl_price"],t["position_side"])
    if tp_sl["tp"] and tp_sl["sl"]:
        open_trades[symbol]["has_tp_sl"] = True
        client.send_telegram(f"<b>✅ TP/SL SHORT recolocados: {symbol}</b>")


def handle_exit(symbol, signals, reason):
    if symbol not in open_trades or symbol in _closing:
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

        is_signal = any(w in reason.lower() for w in ("prob","tendencia"))
        if is_signal and hold_mins < MIN_HOLD_MIN:
            _closing.discard(symbol)
            return

        pnl_usd   = qty * (entry - current)
        notional  = qty * entry * LEVERAGE
        fee_total = notional * FEE_RATE * 2
        pnl_net   = pnl_usd - fee_total
        win       = pnl_net > 0

        stats["total_pnl"]  += pnl_net
        stats["total_fees"] += fee_total
        global pnl_today
        pnl_today += pnl_net
        if win:  stats["wins"]   += 1
        else:    stats["losses"] += 1

        _save_pnl()
        update_blacklist(symbol, win, pnl_net)

        client.cleanup_symbol_orders(symbol)
        time.sleep(0.5)
        client.close_all_positions(symbol)
        _recently_closed[symbol] = time.time()
        del open_trades[symbol]

        total = stats["wins"] + stats["losses"]
        wr    = stats["wins"] / total * 100 if total > 0 else 0
        emoji = "✅" if win else "❌"
        client.send_telegram(
            f"<b>{emoji} SHORT CERRADO — {reason}</b>\n"
            f"{symbol} | ${entry:.4f} → ${current:.4f} | {int(hold_mins)}min\n"
            f"PnL neto: <b>${pnl_net:+.4f}</b> (fee: ${fee_total:.4f})\n"
            f"Hoy: ${pnl_today:+.4f} | WR: {wr:.1f}%"
        )
        maybe_trigger_circuit()
        set_cooldown(symbol)
        time.sleep(1)
    finally:
        _closing.discard(symbol)


def handle_short_entry(symbol, signals, balance, df):
    global trades_today
    if symbol in open_trades or symbol in _opening or symbol in _closing:
        return

    pending = client.get_open_orders(symbol)
    if pending:
        client.cleanup_symbol_orders(symbol)
        time.sleep(1)

    _opening.add(symbol)
    try:
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

        min_tp = (FEE_RATE * 2 * LEVERAGE * 100) + 3.0
        tp_pct = max(tp_pct, min_tp)

        if calc_expected_profit(price, qty, tp_pct) < MIN_PROFIT_USDT:
            return

        tp_price = price * (1 - tp_pct / 100)
        sl_price = price * (1 + sl_pct / 100)
        acc_mode = stats.get("account_mode","hedge")
        pos_side = "SHORT" if acc_mode == "hedge" else "BOTH"

        client.set_leverage(symbol, LEVERAGE)
        ok = client.place_entry(symbol, "short", qty, pos_side)
        if not ok:
            return

        # Verificar posición real
        confirmed = False
        for _ in range(15):
            time.sleep(1)
            for p in client.get_positions(symbol):
                amt = float(p.get("positionAmt",0))
                ps  = str(p.get("positionSide","BOTH")).upper()
                if ps == "SHORT" or (ps == "BOTH" and amt < 0):
                    confirmed = True
                    break
            if confirmed:
                break

        if not confirmed:
            client.close_all_positions(symbol)
            return

        if symbol in open_trades:
            return

        open_trades[symbol] = {
            "position":      "short",
            "entry_price":   price,
            "entry_qty":     qty,
            "position_side": pos_side,
            "tp_price":      tp_price,
            "sl_price":      sl_price,
            "has_tp_sl":     False,
            "opened_at":     datetime.now(timezone.utc),
        }
        _recently_closed.pop(symbol, None)

        tp_sl = client.place_tp_sl(symbol,"short",qty,tp_price,sl_price,pos_side)
        open_trades[symbol]["has_tp_sl"] = tp_sl["tp"] and tp_sl["sl"]
        trades_today += 1

        client.send_telegram(
            f"<b>🔴 SHORT ABIERTO [v3.0 MARKET]</b>\n"
            f"{symbol} | ${price:.4f} | Qty:{qty}\n"
            f"TP {'✅' if tp_sl['tp'] else '❌'}: ${tp_price:.4f} (-{tp_pct:.2f}%)\n"
            f"SL {'✅' if tp_sl['sl'] else '❌'}: ${sl_price:.4f} (+{sl_pct:.2f}%)\n"
            f"Score: {signals.get('quality_bear',0)}/100 | RSI: {signals.get('rsi','?')}\n"
            f"Hoy: {trades_today}/{MAX_TRADES_DAY}"
        )
    except BingXError as e:
        log.error(f"  Error SHORT {symbol}: {e}")
        open_trades.pop(symbol, None)
    finally:
        _opening.discard(symbol)


def analyze_symbol(symbol, balance):
    if symbol in open_trades:
        if symbol in _closing:
            return
        t = open_trades[symbol]
        if not t.get("has_tp_sl"):
            retry_tp_sl(symbol)
        try:
            df      = get_ohlcv(symbol)
            signals = calculate_signals(df, ZLEMA_LENGTH, BAND_MULT, OSC_PERIOD, MIN_QUALITY)
        except Exception as e:
            log.debug(f"  {symbol}: {e}")
            return

        cur = signals["close"]
        tp  = t.get("tp_price",0)
        sl  = t.get("sl_price",0)

        if tp and sl:
            if cur <= tp:
                handle_exit(symbol, signals, f"TP ${cur:.4f}")
                return
            if cur >= sl:
                handle_exit(symbol, signals, f"SL ${cur:.4f}")
                return

        if signals["probability"] >= EXIT_PROB:
            handle_exit(symbol, signals, f"Prob {signals['probability']:.1%}")
            return
        if signals["trend"] == 1:
            handle_exit(symbol, signals, "Tendencia viró alcista")
            return
        return

    if len(open_trades) >= MAX_OPEN_TRADES or trades_today >= MAX_TRADES_DAY:
        return
    if is_on_cooldown(symbol):
        return
    if time.time() - _recently_closed.get(symbol,0) < 300:
        return
    if USE_BTC_FILTER and btc_trend_1h > 2.0:
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
    if not signals["bearish_entry"]:
        return
    if signals.get("ranging") or signals.get("gap_pct",0) < 0.5:
        return
    if signals.get("quality_bear",0) < MIN_QUALITY:
        return
    if calc_atr(df,14) / signals["close"] * 100 < MIN_ATR_PCT:
        return
    if not has_bearish_momentum(df):
        return

    handle_short_entry(symbol, signals, balance, df)


def send_report(balance):
    total = stats["wins"] + stats["losses"]
    wr    = stats["wins"] / total * 100 if total > 0 else 0
    bl    = sum(1 for n in blacklist.values() if n >= MAX_LOSS_STREAK)
    pos   = ""
    for sym, t in open_trades.items():
        try:
            tk  = req.get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
                          params={"symbol":sym},timeout=5).json()
            cur = float(tk["data"]["lastPrice"]) if tk.get("code")==0 else t["entry_price"]
        except Exception:
            cur = t["entry_price"]
        pct = (t["entry_price"]-cur)/t["entry_price"]*100
        pos += f"  SHORT {sym}: {pct:+.2f}% {'✅' if t.get('has_tp_sl') else '⚠️'}\n"
    client.send_telegram(
        f"<b>📊 Short Bot v3.0 #{stats['cycle']}</b>\n"
        f"Balance: ${balance:.2f} | BTC: {btc_trend_1h:+.2f}%\n"
        f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}\n"
        f"{pos or '  sin posiciones\n'}"
        f"Trades: {total} | WR: {wr:.1f}%\n"
        f"Hoy: {trades_today}/{MAX_TRADES_DAY} | PnL hoy: ${pnl_today:+.4f}\n"
        f"Acum: ${stats['total_pnl']:+.4f} | Bl: {bl}"
    )


def main():
    stats["account_mode"] = detect_account_mode()
    reset_daily_if_needed()

    log.info("=" * 70)
    log.info(f"  Short Bot v3.0 | MARKET ONLY | TF:{TIMEFRAME} LEV:{LEVERAGE}x")
    log.info(f"  Max:{MAX_OPEN_TRADES} pos | MaxDay:{MAX_TRADES_DAY} | DailyLimit:${DAILY_LOSS_LIM}")
    log.info("=" * 70)

    if DEMO_MODE:
        log.warning("MODO DEMO")
    try:
        stats["start_balance"] = client.get_balance()
        log.info(f"Balance: ${stats['start_balance']:.2f}")
    except BingXError as e:
        log.error(f"No conecta: {e}")
        sys.exit(1)

    client.cleanup_all_orders()
    time.sleep(2)
    sync_all_positions()
    get_symbols()
    update_btc_trend()

    client.send_telegram(
        f"<b>🔴 Short Bot v3.0 iniciado — MARKET ONLY</b>\n"
        f"Max:{MAX_OPEN_TRADES} | MaxDay:{MAX_TRADES_DAY} | DailyLimit:${DAILY_LOSS_LIM}\n"
        f"Balance: ${stats['start_balance']:.2f}"
    )

    while True:
        try:
            stats["cycle"] += 1
            reset_daily_if_needed()
            if check_circuit_breaker():
                time.sleep(CHECK_INTERVAL)
                continue
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
                f"Shorts:{len(open_trades)}/{MAX_OPEN_TRADES} | WR:{wr:.1f}% | "
                f"Hoy:${pnl_today:+.4f} ({trades_today}/{MAX_TRADES_DAY})\n{'='*70}"
            )

            for sym in list(open_trades.keys()):
                analyze_symbol(sym, balance)
                time.sleep(0.3)

            if len(open_trades) < MAX_OPEN_TRADES and trades_today < MAX_TRADES_DAY:
                new_entries = 0
                for sym in syms:
                    if len(open_trades) >= MAX_OPEN_TRADES or new_entries >= 1:
                        break
                    if sym in open_trades:
                        continue
                    prev = len(open_trades)
                    analyze_symbol(sym, balance)
                    if len(open_trades) > prev:
                        new_entries += 1
                    time.sleep(0.25)
                log.info(f"  Scan: {new_entries} shorts")

            if REPORT_EVERY > 0 and stats["cycle"] % REPORT_EVERY == 0:
                send_report(balance)

            log.info(f"  Próximo ciclo en {CHECK_INTERVAL}s\n")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            try:
                final = client.get_balance()
                client.send_telegram(
                    f"<b>Short Bot v3.0 detenido</b>\n"
                    f"WR: {stats['wins']/(stats['wins']+stats['losses'])*100:.1f}% "
                    f"| PnL: ${stats['total_pnl']:+.4f}\nBalance: ${final:.2f}"
                )
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            log.error(f"Error #{stats['cycle']}: {e}", exc_info=True)
            client.send_telegram(f"<b>⚠️ Error Short Bot v3.0</b>\n{e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
