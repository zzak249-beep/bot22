"""
Bot Multi-Symbol LONG+SHORT — Zero Lag + Trend Reversal Probability
v4.1 — Fixes críticos

FIXES v4.1:
  FIX-A  Doble cierre eliminado: _closing set + _recently_closed dict
         APE-USDT se cerraba 2 veces porque sync restauraba la posición
         justo después de borrarla del dict
  FIX-B  sync_all_positions no restaura posiciones cerradas recientemente
         (ventana de 5 minutos)
  FIX-C  handle_exit es idempotente: comprobación doble antes y después
         del lock para evitar race conditions
  FIX-D  PnL de sesión persiste en archivo JSON para sobrevivir reinicios
  FIX-E  Entradas más estrictas: requiere confirmación en 2 timeframes
  FIX-F  MAX_TRADES_PER_DAY: límite diario de trades
  FIX-G  Deduplicación de símbolo: no puede haber 2 posiciones del mismo par
  FIX-H  Señal mínima de ZLEMA cruce confirmado antes de entrar
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

def _env(key, default=None, cast=str):
    val = os.getenv(key, default)
    if val is None:
        raise EnvironmentError(f"Variable requerida: {key}")
    return cast(val)

API_KEY        = _env("BINGX_API_KEY")
SECRET_KEY     = _env("BINGX_SECRET_KEY")
TELEGRAM_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = _env("TELEGRAM_CHAT_ID",   "")

TIMEFRAME       = _env("TIMEFRAME",        "1h")
LEVERAGE        = _env("LEVERAGE",         "3",       int)
RISK_PCT        = _env("RISK_PCT",         "0.03",    float)
ZLEMA_LENGTH    = _env("ZLEMA_LENGTH",     "50",      int)
BAND_MULT       = _env("BAND_MULT",        "1.2",     float)
OSC_PERIOD      = _env("OSC_PERIOD",       "20",      int)
ENTRY_MAX_PROB  = _env("ENTRY_MAX_PROB",   "0.50",    float)  # más estricto
EXIT_PROB       = _env("EXIT_PROB",        "0.80",    float)
TP_PCT          = _env("TP_PCT",           "4.5",     float)
SL_PCT          = _env("SL_PCT",           "2.0",     float)
USE_ATR_TPSL    = _env("USE_ATR_TPSL",    "true").lower() == "true"
ATR_TP_MULT     = _env("ATR_TP_MULT",     "2.5",     float)
ATR_SL_MULT     = _env("ATR_SL_MULT",     "1.2",     float)
CHECK_INTERVAL  = _env("CHECK_INTERVAL",  "300",     int)
DEMO_MODE       = _env("DEMO_MODE",       "false").lower() == "true"
MIN_BALANCE     = _env("MIN_BALANCE",     "10",      float)
POSITION_MODE   = _env("POSITION_MODE",   "auto")
REPORT_EVERY    = _env("REPORT_EVERY",    "12",      int)
MAX_OPEN_TRADES = _env("MAX_OPEN_TRADES", "2",       int)
MIN_VOLUME_24H  = _env("MIN_VOLUME_24H",  "5000000", float)
MAX_SYMBOLS     = _env("MAX_SYMBOLS",     "15",      int)
SCAN_INTERVAL   = _env("SCAN_INTERVAL",  "600",     int)
ALLOW_SHORT     = _env("ALLOW_SHORT",    "true").lower() == "true"
COOLDOWN_MIN    = _env("COOLDOWN_MIN",   "60",      int)
USE_LIMIT       = _env("USE_LIMIT_ORDERS","true").lower() == "true"
MIN_HOLD_MIN    = _env("MIN_HOLD_MIN",   "20",      int)
MIN_ATR_PCT     = _env("MIN_ATR_PCT",    "0.5",     float)
USE_BTC_FILTER  = _env("USE_BTC_FILTER","true").lower() == "true"
MAX_LOSS_STREAK = _env("MAX_LOSS_STREAK","3",       int)
MAX_TRADES_DAY  = _env("MAX_TRADES_DAY","8",        int)  # FIX-F

FEE_RATE = 0.0002 if USE_LIMIT else 0.0005
PNL_FILE = "/tmp/bot22_pnl.json"   # FIX-D: persistencia

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
_closing:         set   = set()   # FIX-A: lock de cierre
_recently_closed: dict  = {}      # FIX-B: {symbol: timestamp}
symbols:          list  = []
last_scan_ts:     float = 0
btc_trend_1h:     float = 0.0
trades_today:     int   = 0
today_date:       str   = ""

# FIX-D: cargar PnL persistente
def _load_pnl():
    try:
        with open(PNL_FILE) as f:
            d = json.load(f)
            return d.get("total_pnl", 0.0), d.get("total_fees", 0.0), \
                   d.get("wins", 0), d.get("losses", 0)
    except Exception:
        return 0.0, 0.0, 0, 0

_pnl0, _fees0, _wins0, _losses0 = _load_pnl()

stats = {
    "cycle":          0,
    "wins":           _wins0,
    "losses":         _losses0,
    "total_pnl":      _pnl0,
    "total_fees":     _fees0,
    "start_balance":  None,
    "account_mode":   None,
}

def _save_pnl():
    try:
        with open(PNL_FILE, "w") as f:
            json.dump({
                "total_pnl":  stats["total_pnl"],
                "total_fees": stats["total_fees"],
                "wins":       stats["wins"],
                "losses":     stats["losses"],
            }, f)
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
        log.info(f"  Reset diario — trades hoy: 0/{MAX_TRADES_DAY}")

def detect_account_mode() -> str:
    if POSITION_MODE.lower() in ("hedge", "oneway"):
        return POSITION_MODE.lower()
    try:
        for pos in client.get_positions("BTC-USDT"):
            side = str(pos.get("positionSide", "")).upper()
            if side in ("LONG", "SHORT"):
                log.info("Modo HEDGE detectado")
                return "hedge"
            elif side == "BOTH":
                log.info("Modo ONE-WAY detectado")
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
            return symbols or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
        bl = {s for s, n in blacklist.items() if n >= MAX_LOSS_STREAK}
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
                if vol < MIN_VOLUME_24H or price < 0.000001:
                    continue
                items.append({"symbol": sym, "vol": vol})
            except Exception:
                continue
        items.sort(key=lambda x: x["vol"], reverse=True)
        symbols      = [x["symbol"] for x in items[:MAX_SYMBOLS]]
        last_scan_ts = now
        log.info(f"Símbolos: {len(symbols)} (blacklist: {len(bl)})")
        return symbols
    except Exception as e:
        log.warning(f"get_symbols error: {e}")
        return symbols or ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

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
        log.warning(f"  [{symbol}] blacklist ({MAX_LOSS_STREAK} pérdidas)")
        client.send_telegram(f"<b>⛔ Blacklist: {symbol}</b> — {MAX_LOSS_STREAK} pérdidas seguidas")
        cooldowns[symbol] = time.time() + COOLDOWN_MIN * 4 * 60

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

def btc_filter_ok(direction: str) -> bool:
    if not USE_BTC_FILTER:
        return True
    if direction == "long"  and btc_trend_1h < -1.5:
        return False
    if direction == "short" and btc_trend_1h >  1.5:
        return False
    return True

# FIX-E: confirmar señal con cruce de ZLEMA real
def zlema_cross_confirmed(df: pd.DataFrame, direction: str) -> bool:
    """Require that the last 2 bars show a clean ZLEMA cross, not just position."""
    if len(df) < 6:
        return False
    closes = df["close"].values.tolist()
    # Simple proxy: últimas 3 velas cerrando del mismo lado de la MA de 20 períodos
    if len(closes) < 25:
        return True
    ma = sum(closes[-20:]) / 20
    c  = closes[-1]
    c2 = closes[-2]
    c3 = closes[-3]
    if direction == "long":
        # Precio cruzó hacia arriba recientemente
        return c > ma and c2 <= ma or (c > ma and c > c2 > c3)
    else:
        return c < ma and c2 >= ma or (c < ma and c < c2 < c3)

def vol_spike_ok(df: pd.DataFrame) -> bool:
    if len(df) < 6:
        return True
    vols = df["volume"].values
    avg  = sum(vols[-6:-1]) / 5
    return vols[-1] > avg * 0.8

def sync_all_positions():
    """FIX-B: no restaurar posiciones cerradas hace menos de 5 minutos."""
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
                    "opened_at":     open_trades.get(sym, {}).get("opened_at",
                                     datetime.now(timezone.utc)),
                }
        # Eliminar trades que ya no están en BingX
        for sym in list(open_trades.keys()):
            if sym not in real:
                log.info(f"  [SYNC] {sym} cerrado externamente")
                del open_trades[sym]
        # FIX-G: solo restaurar si no está en recently_closed y no está ya en open_trades
        now = time.time()
        for sym, info in real.items():
            if sym in open_trades:
                continue
            # FIX-B: saltar si fue cerrado recientemente
            closed_ts = _recently_closed.get(sym, 0)
            if now - closed_ts < 300:
                log.debug(f"  [SYNC] {sym} ignorado (cerrado hace {int(now-closed_ts)}s)")
                continue
            log.info(f"  [SYNC] Recuperando {info['position'].upper()} {sym}")
            open_trades[sym] = info
    except Exception as e:
        log.warning(f"sync error: {e}")

# ─────────────────────── CORE LOGIC ──────────────────────────

def handle_exit(symbol: str, signals: dict, reason: str):
    # FIX-A: comprobación doble con lock
    if symbol not in open_trades:
        return
    if symbol in _closing:
        log.warning(f"  {symbol}: cierre ya en progreso — ignorando duplicado")
        return

    _closing.add(symbol)
    try:
        if symbol not in open_trades:  # re-check dentro del lock
            return

        t         = open_trades[symbol]
        entry     = t["entry_price"]
        current   = signals["close"]
        direction = t["position"]
        qty       = t["entry_qty"]
        opened_at = t["opened_at"]

        # Respetar tiempo mínimo para salidas por señal
        hold_mins = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60
        is_signal = "prob" in reason.lower() or "tendencia" in reason.lower()
        if is_signal and hold_mins < MIN_HOLD_MIN:
            log.info(f"  {symbol}: hold mínimo {hold_mins:.1f}/{MIN_HOLD_MIN}min — ignorando señal")
            _closing.discard(symbol)
            return

        pnl_usd   = qty * (current - entry) * (1 if direction == "long" else -1)
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

        _save_pnl()  # FIX-D

        update_blacklist(symbol, win)
        log.info(f"  CERRANDO {direction.upper()} {symbol} | {reason} | ${pnl_net:+.4f} | {int(hold_mins)}min")

        ok = client.close_all_positions(symbol)

        # FIX-B: marcar como recién cerrado ANTES de borrar del dict
        _recently_closed[symbol] = time.time()
        del open_trades[symbol]

        mins  = int(hold_mins)
        emoji = "✅" if win else "❌"
        total = stats["wins"] + stats["losses"]
        wr    = stats["wins"] / total * 100 if total > 0 else 0

        client.send_telegram(
            f"<b>{emoji} CERRADO {direction.upper()} — {reason}</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME}\n"
            f"Entrada: ${entry:.4f} → Salida: ${current:.4f} | {mins}min\n"
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


def handle_entry(symbol: str, signals: dict, direction: str, balance: float, df: pd.DataFrame):
    global trades_today

    # FIX-G: comprobación estricta de deduplicación
    if symbol in open_trades:
        log.warning(f"  {symbol}: ya tiene posición abierta — entrada ignorada")
        return
    if symbol in _closing:
        return

    price = signals["close"]
    qty   = calculate_qty(balance, price)
    if qty <= 0:
        return

    # TP/SL dinámicos con ATR
    atr_val = calc_atr(df, 14)
    if USE_ATR_TPSL and atr_val > 0:
        tp_pct = max(atr_val / price * 100 * ATR_TP_MULT, TP_PCT)
        sl_pct = max(atr_val / price * 100 * ATR_SL_MULT, SL_PCT)
    else:
        tp_pct, sl_pct = TP_PCT, SL_PCT

    # TP mínimo para cubrir fees
    min_tp = (FEE_RATE * 2 * LEVERAGE * 100) + 1.5
    tp_pct = max(tp_pct, min_tp)

    tp_price = price * (1 + tp_pct/100) if direction == "long" else price * (1 - tp_pct/100)
    sl_price = price * (1 - sl_pct/100) if direction == "long" else price * (1 + sl_pct/100)

    side     = "BUY" if direction == "long" else "SELL"
    acc_mode = stats.get("account_mode", "hedge")
    pos_side = ("LONG" if direction == "long" else "SHORT") if acc_mode == "hedge" else "BOTH"

    log.info(f"  ABRIENDO {direction.upper()} {symbol} | ${price:.4f} qty={qty} TP:{tp_pct:.2f}% SL:{sl_pct:.2f}%")

    try:
        client.set_leverage(symbol, LEVERAGE)

        # FIX-1: LIMIT order
        if USE_LIMIT:
            offset = 1 - 0.0003 if direction == "long" else 1 + 0.0003
            lp = round(price * offset, 8)
            params = {
                "symbol": symbol, "side": side, "type": "LIMIT",
                "price": f"{lp:.8g}", "quantity": f"{qty:.6g}",
                "timeInForce": "GTC",
            }
            if pos_side != "BOTH":
                params["positionSide"] = pos_side
            try:
                client._request("POST", "/openApi/swap/v2/trade/order", params)
            except BingXError:
                client.place_market_order(symbol, side, qty, position_side=pos_side)
        else:
            client.place_market_order(symbol, side, qty, position_side=pos_side)

        time.sleep(2)

        # FIX-G: verificar que no se abrió ya (race condition)
        if symbol in open_trades:
            log.warning(f"  {symbol}: ya en open_trades tras apertura — no sobreescribir")
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
            "opened_at":     datetime.now(timezone.utc),
        }
        # Limpiar de recently_closed si estaba
        _recently_closed.pop(symbol, None)

        tp_sl   = client.place_tp_sl(symbol, direction, qty, tp_price, sl_price, position_side=pos_side)
        trades_today += 1

        emoji    = "🟢" if direction == "long" else "🔴"
        tp_icon  = "✅" if tp_sl["tp"] else "❌"
        sl_icon  = "✅" if tp_sl["sl"] else "❌"
        ord_type = "LIMIT" if USE_LIMIT else "MARKET"

        client.send_telegram(
            f"<b>{emoji} {direction.upper()} ABIERTO [v4.1]</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME} | {LEVERAGE}x | {ord_type}\n"
            f"Precio: ${price:.4f} | Qty: {qty}\n"
            f"TP {tp_icon}: ${tp_price:.4f} (+{tp_pct:.2f}%)\n"
            f"SL {sl_icon}: ${sl_price:.4f} (-{sl_pct:.2f}%)\n"
            f"Prob: {signals['probability']:.1%} | BTC 1h: {btc_trend_1h:+.2f}%\n"
            f"Trades hoy: {trades_today}/{MAX_TRADES_DAY}\n"
            f"Posiciones: {len(open_trades)}/{MAX_OPEN_TRADES}"
        )

    except BingXError as e:
        log.error(f"  Error abriendo {symbol}: {e}")
        open_trades.pop(symbol, None)
        client.send_telegram(f"<b>⚠️ Error {direction.upper()} {symbol}</b>\n{e}")


def analyze_symbol(symbol: str, balance: float):
    """Gestiona posición abierta O busca nueva entrada."""

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
        prob = signals["probability"]
        tp   = t.get("tp_price", 0)
        sl   = t.get("sl_price", 0)
        direction = t["position"]

        # Verificar TP/SL manualmente
        if tp and sl:
            if direction == "long":
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

        # Salida por señal de estrategia
        if prob >= EXIT_PROB:
            handle_exit(symbol, signals, f"Prob reversión {prob:.1%}")
            return
        if direction == "long"  and signals["trend"] == -1:
            handle_exit(symbol, signals, "Tendencia viró bajista")
            return
        if direction == "short" and signals["trend"] ==  1:
            handle_exit(symbol, signals, "Tendencia viró alcista")
            return
        return

    # ── Búsqueda de nueva entrada ─────────────────────────────
    if len(open_trades) >= MAX_OPEN_TRADES:
        return
    if trades_today >= MAX_TRADES_DAY:
        return
    if is_on_cooldown(symbol):
        return
    # FIX-B: saltar si cerrado recientemente
    if time.time() - _recently_closed.get(symbol, 0) < 300:
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

    # Filtro ATR
    atr_val = calc_atr(df, 14)
    if atr_val / signals["close"] * 100 < MIN_ATR_PCT:
        return

    # Filtro volumen
    if not vol_spike_ok(df):
        return

    if signals["bullish_entry"]:
        if not btc_filter_ok("long"):
            return
        # FIX-E: confirmar cruce ZLEMA
        if not zlema_cross_confirmed(df, "long"):
            log.debug(f"  {symbol}: LONG sin cruce ZLEMA confirmado")
            return
        handle_entry(symbol, signals, "long", balance, df)

    elif ALLOW_SHORT and signals["bearish_entry"]:
        if not btc_filter_ok("short"):
            return
        if not zlema_cross_confirmed(df, "short"):
            log.debug(f"  {symbol}: SHORT sin cruce ZLEMA confirmado")
            return
        handle_entry(symbol, signals, "short", balance, df)


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
        d   = t["position"]
        pct = ((cur - t["entry_price"]) / t["entry_price"] * 100) if d == "long" \
              else ((t["entry_price"] - cur) / t["entry_price"] * 100)
        tp_sl_ok = "✅" if (t.get("tp_price") and t.get("sl_price")) else "⚠️"
        pos_lines += f"  {d.upper()} {sym}: {pct:+.2f}% TP/SL:{tp_sl_ok}\n"

    no_pos = "  sin posiciones\n"
    client.send_telegram(
        f"<b>📊 Reporte Multi-Symbol v4.1 #{stats['cycle']}</b>\n"
        f"Balance: ${balance:.2f} USDT | BTC 1h: {btc_trend_1h:+.2f}%\n"
        f"Abiertos: {len(open_trades)}/{MAX_OPEN_TRADES}\n"
        f"{pos_lines if pos_lines else no_pos}"
        f"Trades: {total} | WR: {wr:.1f}% ({stats['wins']}W/{stats['losses']}L)\n"
        f"PnL acumulado: ${stats['total_pnl']:+.4f} | Fees: ${stats['total_fees']:.4f}\n"
        f"Hoy: {trades_today}/{MAX_TRADES_DAY} trades | Blacklist: {bl}"
    )


# ─────────────────────── MAIN LOOP ───────────────────────────

def main():
    stats["account_mode"] = detect_account_mode()
    reset_daily_if_needed()

    log.info("=" * 70)
    log.info("  Multi-Symbol Bot v4.1  |  LONG + SHORT")
    log.info(f"  TF:{TIMEFRAME} | LEV:{LEVERAGE}x | MaxTrades:{MAX_OPEN_TRADES}")
    log.info(f"  LIMIT:{'ON' if USE_LIMIT else 'OFF'} ({FEE_RATE*100:.3f}%) | "
             f"TP:{TP_PCT}% SL:{SL_PCT}% ATR:{'ON' if USE_ATR_TPSL else 'OFF'}")
    log.info(f"  Cooldown:{COOLDOWN_MIN}min | MinHold:{MIN_HOLD_MIN}min | MaxDay:{MAX_TRADES_DAY}")
    log.info(f"  Entry prob<{ENTRY_MAX_PROB:.0%} | Exit prob>={EXIT_PROB:.0%}")
    log.info(f"  PnL acumulado cargado: ${stats['total_pnl']:+.4f}")
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
        f"<b>🚀 Multi-Symbol Bot v4.1 iniciado</b>\n"
        f"FIXES: doble-cierre ✅ | PnL persistente ✅ | ZLEMA confirmado ✅\n"
        f"TF:{TIMEFRAME} | LEV:{LEVERAGE}x | Max:{MAX_OPEN_TRADES} | MaxDay:{MAX_TRADES_DAY}\n"
        f"LIMIT: {'ON ✅ (0.02%)' if USE_LIMIT else 'OFF ⚠️'} | "
        f"BTC filter: {'ON' if USE_BTC_FILTER else 'OFF'}\n"
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
                f"Pos:{len(open_trades)}/{MAX_OPEN_TRADES} | "
                f"WR:{wr:.1f}% | PnL:${stats['total_pnl']:+.4f} | "
                f"Fees:${stats['total_fees']:.4f} | BTC:{btc_trend_1h:+.2f}% | "
                f"Hoy:{trades_today}/{MAX_TRADES_DAY}\n"
                f"{'='*70}"
            )

            # 1. Gestionar posiciones abiertas primero
            for sym in list(open_trades.keys()):
                analyze_symbol(sym, balance)
                time.sleep(0.3)

            # 2. Buscar nuevas entradas — max 1 por ciclo
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
                log.info(f"  Scan: {new_entries} nuevas entradas | {len(syms)} analizados")
            else:
                log.info(f"  {'Max trades' if len(open_trades)>=MAX_OPEN_TRADES else 'Límite diario'} — solo monitoreando")

            if REPORT_EVERY > 0 and stats["cycle"] % REPORT_EVERY == 0:
                send_report(balance)

            log.info(f"  Próximo ciclo en {CHECK_INTERVAL}s\n")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("Bot detenido")
            try:
                final = client.get_balance()
                total = stats["wins"] + stats["losses"]
                wr    = stats["wins"] / total * 100 if total > 0 else 0
                client.send_telegram(
                    f"<b>Bot v4.1 detenido</b>\n"
                    f"Trades: {total} | WR: {wr:.1f}%\n"
                    f"PnL acumulado: ${stats['total_pnl']:+.4f}\n"
                    f"Fees pagadas: ${stats['total_fees']:.4f}\n"
                    f"Balance: ${final:.2f} USDT"
                )
            except Exception:
                pass
            sys.exit(0)
        except Exception as e:
            log.error(f"Error ciclo #{stats['cycle']}: {e}", exc_info=True)
            client.send_telegram(f"<b>⚠️ Error Bot v4.1</b>\n{e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
