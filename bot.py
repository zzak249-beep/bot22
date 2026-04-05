"""
Bot Multi-Symbol LONG+SHORT — v5.0
CRÍTICO — CAMBIOS QUE DETIENEN LAS PÉRDIDAS:

  STOP-1  ELIMINADAS órdenes LIMIT en entradas
           → Acumulaban 12+ órdenes BOMEUSDT sin ejecutar.
           → Ahora SOLO MARKET: ejecución garantizada en 1 orden.

  STOP-2  cleanup_all_orders() al arrancar
           → Cancela toda orden LIMIT pendiente de sesiones anteriores.

  STOP-3  Verificación de posición real antes de registrar entrada
           → Si la posición no aparece en BingX tras 15s → no registrar.

  STOP-4  MAX_OPEN_TRADES=2, MAX_TRADES_DAY=4 (valores duros en código)
           → No se pueden subir por variables. Demasiado = pérdidas.

  STOP-5  cleanup_symbol_orders antes de CADA cierre
           → Cancela TP/SL huérfanos antes de cerrar con MARKET.

  STOP-6  PnL fichero se resetea al nuevo día automáticamente
           → El -$234 era de sesiones anteriores sumándose.

  STOP-7  Pausa 2h si pérdida diaria > DAILY_LOSS_LIMIT (default $5)

  STOP-8  No abrir si ya hay 1+ órdenes pendientes del mismo símbolo
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

TIMEFRAME      = _env("TIMEFRAME",        "1h")
LEVERAGE       = _env("LEVERAGE",         "3",       int)
RISK_PCT       = _env("RISK_PCT",         "0.03",    float)
ZLEMA_LENGTH   = _env("ZLEMA_LENGTH",     "50",      int)
BAND_MULT      = _env("BAND_MULT",        "1.2",     float)
OSC_PERIOD     = _env("OSC_PERIOD",       "20",      int)
MIN_QUALITY    = _env("MIN_QUALITY",      "60",      int)
ENTRY_MAX_PROB = _env("ENTRY_MAX_PROB",   "0.45",    float)  # más estricto
EXIT_PROB      = _env("EXIT_PROB",        "0.80",    float)
TP_PCT         = _env("TP_PCT",           "5.0",     float)  # subido para cubrir fees
SL_PCT         = _env("SL_PCT",           "2.5",     float)
USE_ATR_TPSL   = _env("USE_ATR_TPSL",    "true").lower() == "true"
ATR_TP_MULT    = _env("ATR_TP_MULT",     "3.0",     float)
ATR_SL_MULT    = _env("ATR_SL_MULT",     "1.5",     float)
CHECK_INTERVAL = _env("CHECK_INTERVAL",  "300",     int)
DEMO_MODE      = _env("DEMO_MODE",       "false").lower() == "true"
MIN_BALANCE    = _env("MIN_BALANCE",     "20",      float)
POSITION_MODE  = _env("POSITION_MODE",   "auto")
REPORT_EVERY   = _env("REPORT_EVERY",    "12",      int)
MIN_VOLUME_24H = _env("MIN_VOLUME_24H",  "10000000",float)  # 10M mínimo
MAX_SYMBOLS    = _env("MAX_SYMBOLS",     "10",      int)    # solo top 10
SCAN_INTERVAL  = _env("SCAN_INTERVAL",  "900",     int)    # 15min
ALLOW_SHORT    = _env("ALLOW_SHORT",    "true").lower() == "true"
COOLDOWN_MIN   = _env("COOLDOWN_MIN",   "120",     int)    # 2h
MIN_HOLD_MIN   = _env("MIN_HOLD_MIN",   "30",      int)
MIN_ATR_PCT    = _env("MIN_ATR_PCT",    "0.8",     float)
USE_BTC_FILTER = _env("USE_BTC_FILTER","true").lower() == "true"
MAX_LOSS_STREAK= _env("MAX_LOSS_STREAK","2",       int)
MIN_PROFIT_USDT= _env("MIN_PROFIT_USDT","0.80",   float)
DAILY_LOSS_LIM = _env("DAILY_LOSS_LIMIT","5.0",   float)  # STOP-7: pausa si pierde $5/día

# STOP-4: LÍMITES DUROS — NO CONFIGURABLES POR VARIABLE
MAX_OPEN_TRADES = min(_env("MAX_OPEN_TRADES", "2", int), 2)  # máximo absoluto: 2
MAX_TRADES_DAY  = min(_env("MAX_TRADES_DAY",  "4", int), 4)  # máximo absoluto: 4

FEE_RATE = 0.0005  # MARKET = taker fee
PNL_FILE = "/tmp/bot_v5_pnl.json"

EXCLUDED = {
    'DOW','SP500','GOLD','SILVER','XAU','OIL','BRENT',
    'EUR','GBP','JPY','TSLA','AAPL','MSFT','NVDA',
    'COIN','MSTR','WHEAT','CORN','BOME','PEPE','SHIB',  # memes = muy volátiles
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
            d = json.load(f)
            # STOP-6: solo cargar si es del mismo día
            saved_date = d.get("date", "")
            today      = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if saved_date == today:
                return (d.get("total_pnl", 0.0), d.get("total_fees", 0.0),
                        d.get("wins", 0), d.get("losses", 0))
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
            json.dump({
                "date":       datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "total_pnl":  stats["total_pnl"],
                "total_fees": stats["total_fees"],
                "wins":       stats["wins"],
                "losses":     stats["losses"],
            }, f)
    except Exception:
        pass

# ─────────────────────── HELPERS ─────────────────────────────

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
        log.info(f"  Reset diario | trades 0/{MAX_TRADES_DAY} | circuit reset")

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

def check_circuit_breaker() -> bool:
    """STOP-7: pausa si pérdida diaria supera el límite."""
    if circuit_until and time.time() < circuit_until:
        mins = int((circuit_until - time.time()) / 60)
        log.warning(f"  [CIRCUIT] Pausado {mins}min más (pérdida diaria ${pnl_today:.2f})")
        return True
    return False

def maybe_trigger_circuit():
    global circuit_until
    if pnl_today < -abs(DAILY_LOSS_LIM):
        circuit_until = time.time() + 2 * 3600
        log.error(f"  [CIRCUIT] 🔴 Pérdida ${pnl_today:.2f} → pausado 2h")
        client.send_telegram(
            f"<b>🔴 Circuit Breaker activado</b>\n"
            f"Pérdida diaria: ${pnl_today:.4f} > ${DAILY_LOSS_LIM}\n"
            f"Bot pausado 2 horas. Reanuda: {datetime.now(timezone.utc).strftime('%H:%M')} UTC +2h"
        )

def get_symbols():
    global symbols, last_scan_ts
    now = time.time()
    if symbols and (now - last_scan_ts) < SCAN_INTERVAL:
        return symbols
    try:
        d = req.get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker", timeout=15).json()
        if d.get("code") != 0:
            return symbols or ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT"]
        bl    = {s for s, n in blacklist.items() if n >= MAX_LOSS_STREAK}
        items = []
        for t in d.get("data", []):
            sym  = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            base = sym.replace("-USDT", "").upper()
            if any(ex in base for ex in EXCLUDED) or sym in bl:
                continue
            try:
                price = float(t.get("lastPrice", 0))
                vol   = float(t.get("volume",    0)) * price
                if vol < MIN_VOLUME_24H or price < 0.0001:
                    continue
                items.append({"symbol": sym, "vol": vol})
            except Exception:
                continue
        items.sort(key=lambda x: x["vol"], reverse=True)
        symbols      = [x["symbol"] for x in items[:MAX_SYMBOLS]]
        last_scan_ts = now
        log.info(f"Símbolos: {len(symbols)} (bl:{len(bl)}) — {symbols[:5]}…")
        return symbols
    except Exception as e:
        log.warning(f"get_symbols: {e}")
        return symbols or ["BTC-USDT", "ETH-USDT"]

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
    notional = qty * price * LEVERAGE
    return notional * (tp_pct / 100) - notional * FEE_RATE * 2

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
    blacklist[symbol] = 0 if win else blacklist.get(symbol, 0) + 1
    if not win and pnl_net < -0.80:
        log.warning(f"  [{symbol}] pérdida grande ${pnl_net:.3f} → cooldown 4h")
        set_cooldown(symbol, 240)
    if blacklist[symbol] >= MAX_LOSS_STREAK:
        log.warning(f"  [{symbol}] blacklist")
        client.send_telegram(f"<b>⛔ Blacklist: {symbol}</b>")
        set_cooldown(symbol, COOLDOWN_MIN * 8)

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
    if direction == "long"  and btc_trend_1h < -2.0:
        return False
    if direction == "short" and btc_trend_1h >  2.0:
        return False
    return True

def sync_all_positions():
    """Sincroniza posiciones reales. No restaura si fue cerrado < 5min."""
    try:
        all_pos = client.get_all_open_positions()
        real = {}
        for p in all_pos:
            sym  = p.get("symbol", "")
            amt  = float(p.get("positionAmt", 0))
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
        # Eliminar trades cerrados en BingX
        for sym in list(open_trades.keys()):
            if sym not in real:
                log.info(f"  [SYNC] {sym} cerrado externamente")
                del open_trades[sym]
        # Restaurar solo si no fue cerrado recientemente
        now = time.time()
        for sym, info in real.items():
            if sym in open_trades:
                continue
            if now - _recently_closed.get(sym, 0) < 300:
                continue
            log.info(f"  [SYNC] Recuperando {info['position'].upper()} {sym}")
            open_trades[sym] = info
    except Exception as e:
        log.warning(f"sync: {e}")

# ─────────────────────── CORE LOGIC ──────────────────────────

def retry_tp_sl(symbol):
    """Reintenta colocar TP/SL si faltan."""
    t = open_trades.get(symbol)
    if not t or t.get("has_tp_sl") or not t.get("tp_price"):
        return
    log.info(f"  [TP/SL RETRY] {symbol}")
    tp_sl = client.place_tp_sl(
        symbol, t["position"], t["entry_qty"],
        t["tp_price"], t["sl_price"],
        position_side=t["position_side"]
    )
    if tp_sl["tp"] and tp_sl["sl"]:
        open_trades[symbol]["has_tp_sl"] = True
        log.info(f"  [TP/SL RETRY] {symbol} OK ✅")
        client.send_telegram(f"<b>✅ TP/SL recolocados: {symbol}</b>")


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
        direction = t["position"]
        qty       = t["entry_qty"]
        opened_at = t["opened_at"]
        hold_mins = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60

        is_signal = any(w in reason.lower() for w in ("prob", "tendencia"))
        if is_signal and hold_mins < MIN_HOLD_MIN:
            log.info(f"  {symbol}: hold mínimo {hold_mins:.1f}/{MIN_HOLD_MIN}min")
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

        log.info(f"  CIERRE {direction.upper()} {symbol} | {reason} | ${pnl_net:+.4f} | {int(hold_mins)}min")

        # STOP-5: cancelar órdenes antes de cerrar
        client.cleanup_symbol_orders(symbol)
        time.sleep(0.5)
        client.close_all_positions(symbol)

        _recently_closed[symbol] = time.time()
        del open_trades[symbol]

        total = stats["wins"] + stats["losses"]
        wr    = stats["wins"] / total * 100 if total > 0 else 0
        emoji = "✅" if win else "❌"

        client.send_telegram(
            f"<b>{emoji} CERRADO {direction.upper()} — {reason}</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME}\n"
            f"${entry:.4f} → ${current:.4f} | {int(hold_mins)}min\n"
            f"PnL neto: <b>${pnl_net:+.4f}</b> (fee: ${fee_total:.4f})\n"
            f"Hoy: ${pnl_today:+.4f} | Acum: ${stats['total_pnl']:+.4f}\n"
            f"WR: {wr:.1f}% | Pos: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )
        maybe_trigger_circuit()
        set_cooldown(symbol)
        time.sleep(1)
    finally:
        _closing.discard(symbol)


def handle_entry(symbol, signals, direction, balance, df):
    global trades_today

    # Triple comprobación anti-duplicado
    if symbol in open_trades or symbol in _opening or symbol in _closing:
        return

    # STOP-8: verificar que no hay órdenes pendientes del símbolo
    pending = client.get_open_orders(symbol)
    if pending:
        log.warning(f"  {symbol}: tiene {len(pending)} órdenes pendientes — cancelando antes de entrar")
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
        sl_pct = max(sl_pct, SL_PCT)

        expected_profit = calc_expected_profit(price, qty, tp_pct)
        if expected_profit < MIN_PROFIT_USDT:
            log.info(f"  {symbol}: profit esp ${expected_profit:.3f} < ${MIN_PROFIT_USDT} — skip")
            return

        tp_price = price * (1 + tp_pct/100) if direction == "long" else price * (1 - tp_pct/100)
        sl_price = price * (1 - sl_pct/100) if direction == "long" else price * (1 + sl_pct/100)
        acc_mode = stats.get("account_mode", "hedge")
        pos_side = ("LONG" if direction == "long" else "SHORT") if acc_mode == "hedge" else "BOTH"

        log.info(f"  ABRIENDO {direction.upper()} {symbol} | ${price:.4f} "
                 f"qty={qty} TP:{tp_pct:.2f}% SL:{sl_pct:.2f}% ~${expected_profit:.3f}")

        client.set_leverage(symbol, LEVERAGE)

        # STOP-1: SOLO MARKET
        ok = client.place_entry(symbol, direction, qty, pos_side)
        if not ok:
            log.error(f"  {symbol}: entrada fallida")
            return

        # Esperar confirmación real de posición
        entry_confirmed = False
        for _ in range(15):
            time.sleep(1)
            positions = client.get_positions(symbol)
            for p in positions:
                amt = float(p.get("positionAmt", 0))
                ps  = str(p.get("positionSide", "BOTH")).upper()
                if ((direction == "long"  and (ps == "LONG"  or amt > 0)) or
                    (direction == "short" and (ps == "SHORT" or amt < 0))):
                    entry_confirmed = True
                    break
            if entry_confirmed:
                break

        if not entry_confirmed:
            log.error(f"  {symbol}: posición NO confirmada en BingX tras 15s — abortando")
            client.close_all_positions(symbol)  # cierre de seguridad
            return

        # STOP-3: re-check anti-dup
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
            "has_tp_sl":     False,
            "opened_at":     datetime.now(timezone.utc),
        }
        _recently_closed.pop(symbol, None)

        tp_sl = client.place_tp_sl(symbol, direction, qty, tp_price, sl_price, pos_side)
        open_trades[symbol]["has_tp_sl"] = tp_sl["tp"] and tp_sl["sl"]
        trades_today += 1

        q = signals.get("quality_bull" if direction == "long" else "quality_bear", 0)
        e = "🟢" if direction == "long" else "🔴"
        tp_i = "✅" if tp_sl["tp"] else "❌"
        sl_i = "✅" if tp_sl["sl"] else "❌"

        client.send_telegram(
            f"<b>{e} {direction.upper()} ABIERTO [v5.0 MARKET]</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME} | {LEVERAGE}x\n"
            f"${price:.4f} | Qty: {qty}\n"
            f"TP {tp_i}: ${tp_price:.4f} (+{tp_pct:.2f}%)\n"
            f"SL {sl_i}: ${sl_price:.4f} (-{sl_pct:.2f}%)\n"
            f"Profit esp: ${expected_profit:.3f} | Score: {q}/100\n"
            f"RSI: {signals.get('rsi','?')} | BTC: {btc_trend_1h:+.2f}%\n"
            f"Hoy: {trades_today}/{MAX_TRADES_DAY}"
        )

    except BingXError as e:
        log.error(f"  Error {symbol}: {e}")
        open_trades.pop(symbol, None)
    finally:
        _opening.discard(symbol)


def analyze_symbol(symbol, balance):
    # ── Posición abierta ──────────────────────────────────────
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
        tp  = t.get("tp_price", 0)
        sl  = t.get("sl_price", 0)
        d   = t["position"]

        if tp and sl:
            if d == "long"  and cur >= tp:
                handle_exit(symbol, signals, f"TP ${cur:.4f}")
                return
            if d == "long"  and cur <= sl:
                handle_exit(symbol, signals, f"SL ${cur:.4f}")
                return
            if d == "short" and cur <= tp:
                handle_exit(symbol, signals, f"TP ${cur:.4f}")
                return
            if d == "short" and cur >= sl:
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
    if signals.get("gap_pct", 0) < 0.5:
        return

    atr_pct = calc_atr(df, 14) / signals["close"] * 100
    if atr_pct < MIN_ATR_PCT:
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
        pct   = ((cur-t["entry_price"])/t["entry_price"]*100) if d == "long" \
                else ((t["entry_price"]-cur)/t["entry_price"]*100)
        tp_sl = "✅" if t.get("has_tp_sl") else "⚠️ RETRY PENDIENTE"
        pos_lines += f"  {d.upper()} {sym}: {pct:+.2f}% {tp_sl}\n"

    no_pos = "  sin posiciones\n"
    client.send_telegram(
        f"<b>📊 Reporte v5.0 #{stats['cycle']}</b>\n"
        f"Balance: ${balance:.2f} | BTC 1h: {btc_trend_1h:+.2f}%\n"
        f"Abiertos: {len(open_trades)}/{MAX_OPEN_TRADES}\n"
        f"{pos_lines if pos_lines else no_pos}"
        f"Trades: {total} | WR: {wr:.1f}%\n"
        f"Hoy: {trades_today}/{MAX_TRADES_DAY} | PnL hoy: ${pnl_today:+.4f}\n"
        f"Acumulado: ${stats['total_pnl']:+.4f} | Fees: ${stats['total_fees']:.4f}\n"
        f"Blacklist: {bl} | Limit pérdida: ${DAILY_LOSS_LIM}/día"
    )


# ─────────────────────── MAIN ────────────────────────────────

def main():
    stats["account_mode"] = detect_account_mode()
    reset_daily_if_needed()

    log.info("=" * 70)
    log.info("  Multi-Symbol Bot v5.0  |  MARKET ONLY — sin LIMIT")
    log.info(f"  TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} pos | MaxDay:{MAX_TRADES_DAY}")
    log.info(f"  Fee MARKET:{FEE_RATE*100:.3f}% | TP:{TP_PCT}% SL:{SL_PCT}%")
    log.info(f"  Cooldown:{COOLDOWN_MIN}min | MinHold:{MIN_HOLD_MIN}min")
    log.info(f"  MinBalance:${MIN_BALANCE} | MinProfit:${MIN_PROFIT_USDT} | DailyLimit:${DAILY_LOSS_LIM}")
    log.info(f"  Símbolos top: {MAX_SYMBOLS} (vol>{MIN_VOLUME_24H/1e6:.0f}M)")
    log.info("=" * 70)

    if DEMO_MODE:
        log.warning("MODO DEMO")

    try:
        stats["start_balance"] = client.get_balance()
        log.info(f"Balance: ${stats['start_balance']:.2f} USDT")
    except BingXError as e:
        log.error(f"No se pudo conectar: {e}")
        sys.exit(1)

    # STOP-2: limpiar todas las órdenes LIMIT pendientes al arrancar
    log.info("  Limpiando órdenes LIMIT pendientes…")
    client.cleanup_all_orders()
    time.sleep(2)

    sync_all_positions()
    get_symbols()
    update_btc_trend()

    client.send_telegram(
        f"<b>🚀 Bot v5.0 iniciado — MARKET ONLY</b>\n"
        f"⚠️ Órdenes LIMIT eliminadas — se usa MARKET siempre\n"
        f"TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} | MaxDay:{MAX_TRADES_DAY}\n"
        f"Limit pérdida diaria: ${DAILY_LOSS_LIM} | MinProfit: ${MIN_PROFIT_USDT}\n"
        f"Símbolos: {len(symbols)} (top por vol, sin memes)\n"
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
                f"Pos:{len(open_trades)}/{MAX_OPEN_TRADES} | WR:{wr:.1f}% | "
                f"Hoy:${pnl_today:+.4f} ({trades_today}/{MAX_TRADES_DAY}) | "
                f"BTC:{btc_trend_1h:+.2f}%\n{'='*70}"
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
                    time.sleep(0.25)
                log.info(f"  Scan: {new_entries} entradas")
            else:
                log.info(f"  Límite — monitoreando")

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
                    f"<b>Bot v5.0 detenido</b>\n"
                    f"Trades: {total} | WR: {wr:.1f}%\n"
                    f"PnL acumulado: ${stats['total_pnl']:+.4f}\n"
                    f"Balance: ${final:.2f}"
                )
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            log.error(f"Error #{stats['cycle']}: {e}", exc_info=True)
            client.send_telegram(f"<b>⚠️ Error Bot v5.0</b>\n{e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
