"""
╔══════════════════════════════════════════════════════════════════╗
║           BINGX SUPERBOT — v7.1  (ARCHIVO ÚNICO)                ║
║                                                                  ║
║  CAMBIOS v7.1 vs v7.0:                                          ║
║  ✅ MIN_ADX bajado 28→25 — más señales, sigue filtrando lateral ║
║  ✅ MIN_SCORE bajado 6→5 — menos restrictivo sin sacrificar R:R ║
║  ✅ HTF trend: ya no es filtro DURO, suma puntos al score        ║
║  ✅ LEVERAGE bajado 3x→2x — proteger balance en drawdown        ║
║  ✅ MAX_TRADES_DAY 2→3 — más oportunidades por día              ║
║  ✅ SCAN_INTERVAL 600→300 — detectar señales antes               ║
║  ✅ Partial TP al 50% del camino → SL a breakeven               ║
║  ✅ Trailing stop activado tras TP parcial                       ║
║  ✅ PnL neto corregido (fee calculada sobre notional real)       ║
║  ✅ Cooldown por par reducido 3h→1.5h                           ║
╚══════════════════════════════════════════════════════════════════╝

ENV VARS necesarias en Railway:
  BINGX_API_KEY       = tu_api_key
  BINGX_SECRET_KEY    = tu_secret_key
  TELEGRAM_TOKEN      = (opcional)
  TELEGRAM_CHAT_ID    = (opcional)
  DRY_RUN             = true   ← empieza siempre en dry run
"""

import os, sys, time, json, hmac, hashlib, logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor, as_completed

# ═══════════════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("BOT7")

# ═══════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY")    or os.environ.get("BINGX_KEY",    "")
SECRET_KEY = os.environ.get("BINGX_SECRET_KEY") or os.environ.get("BINGX_SECRET", "")
TG_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TG_CHAT    = os.environ.get("TELEGRAM_CHAT_ID", "")
DRY_RUN    = os.environ.get("DRY_RUN", "true").lower() == "true"

if not API_KEY or not SECRET_KEY:
    log.critical("❌ Faltan BINGX_API_KEY y BINGX_SECRET_KEY en Railway → Variables")
    sys.exit(1)

BASE_URL        = "https://open-api.bingx.com"
TIMEFRAME       = "1h"
HTF             = "4h"

# FIXED v7.1: leverage reducido para proteger balance en drawdown
LEVERAGE        = int(os.environ.get("LEVERAGE", "2"))          # era 3

# FIXED v7.1: score mínimo más alcanzable sin perder calidad
MIN_SCORE       = int(os.environ.get("MIN_SCORE", "5"))         # era 6

# FIXED v7.1: ADX más permisivo, igual filtra laterales
MIN_ADX         = int(os.environ.get("MIN_ADX", "25"))          # era 28

RISK_PCT        = float(os.environ.get("RISK_PCT", "0.015"))    # 1.5% del balance
TP_RATIO        = float(os.environ.get("TP_RATIO", "2.5"))      # TP = 2.5 × SL
ATR_SL_MULT     = float(os.environ.get("ATR_SL_MULT", "1.8"))   # SL = 1.8 × ATR
MAX_POSITIONS   = 1

# FIXED v7.1: más trades al día para aprovechar señales válidas
MAX_TRADES_DAY  = int(os.environ.get("MAX_TRADES_DAY", "3"))    # era 2

DAILY_STOP_USD  = float(os.environ.get("DAILY_STOP_USD", "4.0"))
MIN_BALANCE     = float(os.environ.get("MIN_BALANCE", "20.0"))
MIN_VOLUME_24H  = float(os.environ.get("MIN_VOLUME_24H", "8000000"))

# FIXED v7.1: scan más frecuente para no perder entradas
SCAN_INTERVAL   = int(os.environ.get("SCAN_INTERVAL", "300"))   # era 600

MONITOR_INTERVAL = 30

# FIXED v7.1: cooldown reducido 3h→1.5h
COOLDOWN_HOURS  = float(os.environ.get("COOLDOWN_HOURS", "1.5"))  # era 3h

MAX_SCAN_SYMBOLS = int(os.environ.get("MAX_SCAN_SYMBOLS", "80"))
SCAN_THREADS    = 6
FEE_RATE        = 0.0005        # 0.05% taker BingX
STATE_FILE      = "/tmp/bot7_state.json"

# ═══════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════════════════
def telegram(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════
#  BINGX API CLIENT
# ═══════════════════════════════════════════════════════════════════════
_session = requests.Session()
_session.headers.update({"X-BX-APIKEY": API_KEY})

def _sign(params: dict) -> str:
    payload = urlencode(sorted(params.items()))
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()

def _get(path: str, params: dict = None, signed: bool = True) -> dict:
    p = dict(params or {})
    if signed:
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = _sign(p)
    r = _session.get(BASE_URL + path, params=p, timeout=12)
    r.raise_for_status()
    return r.json()

def _post(path: str, params: dict = None) -> dict:
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = _sign(p)
    r = _session.post(BASE_URL + path, params=p, timeout=12)
    r.raise_for_status()
    return r.json()

def get_balance() -> float:
    try:
        d = _get("/openApi/swap/v2/user/balance")
        for a in d.get("data", {}).get("balance", []):
            if a.get("asset") == "USDT":
                return float(a.get("availableMargin", 0))
    except Exception as e:
        log.error(f"get_balance: {e}")
    return 0.0

def get_all_symbols() -> list[str]:
    try:
        d = _get("/openApi/swap/v2/quote/contracts", signed=False)
        syms = []
        for c in d.get("data", []):
            if (str(c.get("currency", "")).upper() == "USDT"
                    and int(c.get("status", 0)) == 1
                    and str(c.get("symbol", "")).endswith("-USDT")):
                syms.append(c["symbol"])
        return syms
    except Exception as e:
        log.error(f"get_all_symbols: {e}")
        return []

def get_klines(symbol: str, interval: str, limit: int = 200) -> list[dict]:
    try:
        d = _get("/openApi/swap/v3/quote/klines",
                 {"symbol": symbol, "interval": interval, "limit": limit},
                 signed=False)
        out = []
        for k in d.get("data", []):
            out.append({
                "open":   float(k[1]), "high":   float(k[2]),
                "low":    float(k[3]), "close":  float(k[4]),
                "volume": float(k[5]),
            })
        return out
    except Exception:
        return []

def get_ticker_volume(symbol: str) -> float:
    try:
        d = _get("/openApi/swap/v2/quote/ticker",
                 {"symbol": symbol}, signed=False)
        return float(d.get("data", {}).get("quoteVolume", 0))
    except Exception:
        return 0.0

def get_mark_price(symbol: str) -> float:
    try:
        d = _get("/openApi/swap/v2/quote/premiumIndex",
                 {"symbol": symbol}, signed=False)
        return float(d.get("data", {}).get("markPrice", 0))
    except Exception:
        return 0.0

def get_positions() -> list[dict]:
    try:
        d = _get("/openApi/swap/v2/user/positions")
        return [p for p in d.get("data", [])
                if abs(float(p.get("positionAmt", 0))) > 0]
    except Exception as e:
        log.error(f"get_positions: {e}")
        return []

def cancel_all_orders(symbol: str) -> bool:
    try:
        _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
        return True
    except Exception as e:
        log.warning(f"cancel_all_orders {symbol}: {e}")
        return False

def cancel_all_orders_global():
    try:
        d = _get("/openApi/swap/v2/trade/allOpenOrders")
        orders = d.get("data", {}).get("orders", [])
        symbols_with_orders = set(o.get("symbol", "") for o in orders)
        cancelled = 0
        for sym in symbols_with_orders:
            if cancel_all_orders(sym):
                cancelled += 1
                time.sleep(0.1)
        log.info(f"🗑️  Canceladas órdenes en {cancelled} pares")
        return cancelled
    except Exception as e:
        log.error(f"cancel_all_orders_global: {e}")
        return 0

def set_leverage(symbol: str, lev: int, side: str = "LONG"):
    try:
        _post("/openApi/swap/v2/trade/leverage",
              {"symbol": symbol, "side": side, "leverage": lev})
    except Exception:
        pass

def set_margin_isolated(symbol: str):
    try:
        _post("/openApi/swap/v2/trade/marginType",
              {"symbol": symbol, "marginType": "ISOLATED"})
    except Exception:
        pass

def get_symbol_info(symbol: str) -> dict:
    try:
        d = _get("/openApi/swap/v2/quote/contracts", signed=False)
        for c in d.get("data", []):
            if c.get("symbol") == symbol:
                return c
    except Exception:
        pass
    return {}

def place_market_order(symbol: str, side: str, pos_side: str, qty: float) -> dict:
    params = {
        "symbol":       symbol,
        "side":         side,
        "positionSide": pos_side,
        "type":         "MARKET",
        "quantity":     qty,
    }
    return _post("/openApi/swap/v2/trade/order", params)

def close_position_market(symbol: str, pos_side: str, qty: float) -> bool:
    side = "SELL" if pos_side == "LONG" else "BUY"
    try:
        r = place_market_order(symbol, side, pos_side, qty)
        return r.get("code", -1) == 0
    except Exception as e:
        log.error(f"close_position_market {symbol}: {e}")
        return False

# ═══════════════════════════════════════════════════════════════════════
#  INDICATORS
# ═══════════════════════════════════════════════════════════════════════
def _ema_np(arr: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    k = 2.0 / (n + 1)
    for i in range(len(arr)):
        v = arr[i]
        if np.isnan(v):
            continue
        prev = out[i-1] if i > 0 and not np.isnan(out[i-1]) else v
        out[i] = v * k + prev * (1 - k)
    return out

def _rsi_np(closes: np.ndarray, n: int = 14) -> float:
    if len(closes) < n + 2:
        return 50.0
    d  = np.diff(closes[-n*3:])
    g  = np.where(d > 0, d, 0.0)
    l  = np.where(d < 0, -d, 0.0)
    ag = np.mean(g[-n:])
    al = np.mean(l[-n:])
    if al == 0:
        return 100.0
    return float(100 - 100 / (1 + ag / al))

def _atr_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float:
    if len(close) < n + 1:
        return 0.0
    pc = np.roll(close, 1)
    pc[0] = close[0]
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - pc), np.abs(low - pc)))
    return float(np.mean(tr[-n:]))

def _adx_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float:
    if len(close) < n * 2:
        return 0.0
    ph = np.roll(high, 1);  ph[0] = high[0]
    pl = np.roll(low,  1);  pl[0] = low[0]
    pc = np.roll(close, 1); pc[0] = close[0]
    tr   = np.maximum(high - low,
           np.maximum(np.abs(high - pc), np.abs(low - pc)))
    dm_p = np.where((high - ph) > (pl - low), np.maximum(high - ph, 0), 0.0)
    dm_m = np.where((pl - low) > (high - ph), np.maximum(pl - low, 0), 0.0)
    atr14 = np.mean(tr[-n:])
    if atr14 == 0:
        return 0.0
    di_p  = 100 * np.mean(dm_p[-n:]) / atr14
    di_m  = 100 * np.mean(dm_m[-n:]) / atr14
    denom = di_p + di_m
    if denom == 0:
        return 0.0
    dx = 100 * abs(di_p - di_m) / denom
    return float(dx)

def _macd_np(closes: np.ndarray):
    e12 = _ema_np(closes, 12)
    e26 = _ema_np(closes, 26)
    m   = e12 - e26
    sig = _ema_np(m, 9)
    return float(m[-1]), float(sig[-1])

def _vwap_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, vol: np.ndarray) -> float:
    hlc3 = (high + low + close) / 3.0
    cv   = np.cumsum(vol)
    cvp  = np.cumsum(hlc3 * vol)
    return float(cvp[-1] / cv[-1]) if cv[-1] > 0 else float(close[-1])

def _ema_val(closes: np.ndarray, n: int) -> float:
    return float(_ema_np(closes, n)[-1])

def _vol_ratio(vol: np.ndarray, n: int = 20) -> float:
    if len(vol) < n + 1:
        return 1.0
    avg = np.mean(vol[-n-1:-1])
    return float(vol[-1] / avg) if avg > 0 else 1.0

# ═══════════════════════════════════════════════════════════════════════
#  STRATEGY
# ═══════════════════════════════════════════════════════════════════════
def analyze_symbol(symbol: str) -> dict:
    EMPTY = {"direction": None, "entry": 0, "sl": 0, "tp": 0,
             "atr": 0, "score": 0, "reason": "no data"}

    klines = get_klines(symbol, TIMEFRAME, 150)
    if len(klines) < 80:
        return EMPTY

    df  = pd.DataFrame(klines)
    o   = df["open"].values.astype(float)
    h   = df["high"].values.astype(float)
    l   = df["low"].values.astype(float)
    c   = df["close"].values.astype(float)
    v   = df["volume"].values.astype(float)

    price = c[-1]
    if price <= 0:
        return EMPTY

    # HTF (4h)
    klines_4h = get_klines(symbol, HTF, 60)
    htf_trend = 0
    htf_rsi   = 50.0
    if len(klines_4h) >= 30:
        c4     = np.array([k["close"] for k in klines_4h], dtype=float)
        h4     = np.array([k["high"]  for k in klines_4h], dtype=float)
        l4     = np.array([k["low"]   for k in klines_4h], dtype=float)
        e9_4h  = _ema_val(c4, 9)
        e21_4h = _ema_val(c4, 21)
        htf_rsi = _rsi_np(c4, 14)
        htf_trend = 1 if e9_4h > e21_4h else -1

    # LTF indicators
    e9   = _ema_val(c, 9)
    e21  = _ema_val(c, 21)
    e50  = _ema_val(c, 50)
    e9p  = float(_ema_np(c, 9)[-2])
    e21p = float(_ema_np(c, 21)[-2])
    rsi  = _rsi_np(c, 14)
    atr  = _atr_np(h, l, c, 14)
    adx  = _adx_np(h, l, c, 14)
    vwap = _vwap_np(h, l, c, v)
    macd, macd_sig = _macd_np(c)
    vol_r = _vol_ratio(v, 20)

    if atr <= 0:
        return {**EMPTY, "reason": "ATR=0"}

    atr_pct = atr / price * 100
    if atr_pct < 0.3:
        return {**EMPTY, "reason": f"ATR bajo {atr_pct:.2f}%"}

    cross_up   = e9 > e21 and e9p <= e21p
    cross_down = e9 < e21 and e9p >= e21p

    # FIXED v7.1: también detectar tendencia establecida (no solo cruce)
    # Permite entrar en pullbacks dentro de una tendencia clara
    trend_up   = e9 > e21 and e21 > e50 and price > e9
    trend_down = e9 < e21 and e21 < e50 and price < e9

    recent_high    = float(np.max(h[-4:-1]))
    recent_low     = float(np.min(l[-4:-1]))
    breakout_up    = price > recent_high and c[-1] > o[-1]
    breakout_down  = price < recent_low  and c[-1] < o[-1]

    sl_dist = max(atr * ATR_SL_MULT, price * 0.015)
    tp_dist = sl_dist * TP_RATIO

    # Profit real tras fees
    notional   = (price * 1.0) * LEVERAGE
    fee_cost   = notional * FEE_RATE * 2
    exp_profit = (tp_dist / price) * notional - fee_cost

    def bull_score():
        s = 0
        s += 2 if cross_up              else 0   # trigger principal
        s += 1 if trend_up              else 0   # NUEVO: tendencia alineada
        s += 1 if htf_trend == 1        else 0   # HTF alcista (score, no filtro)
        s += 1 if htf_rsi > 50          else 0   # HTF RSI
        s += 1 if price > vwap          else 0
        s += 1 if price > e50           else 0
        s += 1 if macd > macd_sig       else 0
        s += 1 if 45 < rsi < 72         else 0   # FIXED: rango más amplio
        s += 1 if vol_r > 1.15          else 0   # FIXED: umbral más bajo (era 1.2)
        s += 1 if breakout_up           else 0
        return s

    def bear_score():
        s = 0
        s += 2 if cross_down            else 0
        s += 1 if trend_down            else 0   # NUEVO
        s += 1 if htf_trend == -1       else 0
        s += 1 if htf_rsi < 50          else 0
        s += 1 if price < vwap          else 0
        s += 1 if price < e50           else 0
        s += 1 if macd < macd_sig       else 0
        s += 1 if 28 < rsi < 55         else 0   # FIXED
        s += 1 if vol_r > 1.15          else 0
        s += 1 if breakout_down         else 0
        return s

    bs = bull_score()
    ss = bear_score()

    # FIXED v7.1: HTF trend ya NO es filtro duro — está en el score
    # Solo bloquear si HTF está completamente en contra (score compensa)
    htf_blocks_long  = htf_trend == -1 and htf_rsi < 40   # HTF bajista fuerte
    htf_blocks_short = htf_trend ==  1 and htf_rsi > 60   # HTF alcista fuerte

    trigger_long  = (cross_up  or trend_up)   and bs >= MIN_SCORE
    trigger_short = (cross_down or trend_down) and ss >= MIN_SCORE

    # LONG
    if (trigger_long and adx >= MIN_ADX
            and not htf_blocks_long and exp_profit > 0.2):
        sl = round(price - sl_dist, 6)
        tp = round(price + tp_dist, 6)
        # Partial TP al 50% del camino hacia TP
        tp_partial = round(price + tp_dist * 0.5, 6)
        return {
            "direction":  "long",
            "entry":      price,
            "sl":         sl,
            "tp":         tp,
            "tp_partial": tp_partial,
            "atr":        atr,
            "score":      bs,
            "reason":     (f"LONG score={bs}/11 ADX={adx:.0f} "
                           f"RSI={rsi:.0f} HTF={htf_trend:+d} "
                           f"vol={vol_r:.1f}x cross={'Y' if cross_up else 'N'}"),
        }

    # SHORT
    if (trigger_short and adx >= MIN_ADX
            and not htf_blocks_short and exp_profit > 0.2):
        sl = round(price + sl_dist, 6)
        tp = round(price - tp_dist, 6)
        tp_partial = round(price - tp_dist * 0.5, 6)
        return {
            "direction":  "short",
            "entry":      price,
            "sl":         sl,
            "tp":         tp,
            "tp_partial": tp_partial,
            "atr":        atr,
            "score":      ss,
            "reason":     (f"SHORT score={ss}/11 ADX={adx:.0f} "
                           f"RSI={rsi:.0f} HTF={htf_trend:+d} "
                           f"vol={vol_r:.1f}x cross={'Y' if cross_down else 'N'}"),
        }

    return {
        **EMPTY,
        "reason": (f"Sin señal | cross={'UP' if cross_up else 'DWN' if cross_down else 'NO'} "
                   f"trend={'UP' if trend_up else 'DWN' if trend_down else 'NO'} "
                   f"score=B{bs}/S{ss} ADX={adx:.0f} HTF={htf_trend:+d}"),
    }

# ═══════════════════════════════════════════════════════════════════════
#  SCANNER
# ═══════════════════════════════════════════════════════════════════════
def scan_market(exclude_symbols: set = None) -> list[dict]:
    exclude = exclude_symbols or set()

    all_syms = get_all_symbols()
    if not all_syms:
        log.warning("Sin símbolos de BingX")
        return []

    priority = [
        "BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
        "DOGE-USDT","ADA-USDT","AVAX-USDT","DOT-USDT","MATIC-USDT",
        "LINK-USDT","UNI-USDT","ATOM-USDT","LTC-USDT","FIL-USDT",
        "NEAR-USDT","APT-USDT","ARB-USDT","OP-USDT","SUI-USDT",
        "INJ-USDT","TIA-USDT","SEI-USDT","WIF-USDT","JTO-USDT",
    ]
    others  = [s for s in all_syms if s not in priority and s not in exclude]
    symbols = [s for s in priority if s not in exclude] + others
    symbols = symbols[:MAX_SCAN_SYMBOLS]

    log.info(f"🔍 Escaneando {len(symbols)} símbolos...")

    def scan_one(sym):
        try:
            vol = get_ticker_volume(sym)
            if vol < MIN_VOLUME_24H:
                return None
            sig = analyze_symbol(sym)
            if sig["direction"]:
                return {"symbol": sym, "volume": vol, **sig}
        except Exception as e:
            log.debug(f"scan_one {sym}: {e}")
        return None

    results = []
    with ThreadPoolExecutor(max_workers=SCAN_THREADS) as ex:
        futs = {ex.submit(scan_one, sym): sym for sym in symbols}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.append(r)
        time.sleep(0.05)

    results.sort(key=lambda x: x["score"], reverse=True)
    log.info(f"✅ {len(results)} señales encontradas")
    for r in results[:5]:
        log.info(f"  {r['symbol']:<16} {r['direction'].upper():<5} "
                 f"score={r['score']} | {r['reason']}")
    return results

# ═══════════════════════════════════════════════════════════════════════
#  STATE
# ═══════════════════════════════════════════════════════════════════════
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        pass
    return {
        "position":     None,
        "cooldowns":    {},
        "trades_today": 0,
        "pnl_today":    0.0,
        "today_date":   "",
        "total_trades": 0,
        "total_pnl":    0.0,
    }

def save_state(s: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(s, f, indent=2, default=str)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════
#  BOT
# ═══════════════════════════════════════════════════════════════════════
class Bot:
    def __init__(self):
        self.state = load_state()

    def _daily_reset(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state["today_date"] != today:
            self.state["trades_today"] = 0
            self.state["pnl_today"]    = 0.0
            self.state["today_date"]   = today
            save_state(self.state)
            bal = get_balance()
            log.info(f"📅 Nuevo día: {today} | Balance: ${bal:.2f}")

    def _on_cooldown(self, symbol: str) -> bool:
        exp = self.state["cooldowns"].get(symbol, 0)
        return time.time() < exp

    def _set_cooldown(self, symbol: str):
        # FIXED v7.1: cooldown 1.5h (era 3h)
        self.state["cooldowns"][symbol] = time.time() + COOLDOWN_HOURS * 3600
        save_state(self.state)

    def _sync_position(self):
        if not self.state["position"]:
            return
        sym = self.state["position"]["symbol"]
        pos = get_positions()
        alive = any(
            p.get("symbol") == sym and abs(float(p.get("positionAmt", 0))) > 0
            for p in pos
        )
        if not alive:
            log.info(f"📤 Posición {sym} cerrada externamente (SL/TP de BingX)")
            entry     = self.state["position"]["entry"]
            mark      = get_mark_price(sym) or entry
            direction = self.state["position"]["direction"]
            qty       = self.state["position"]["qty"]
            pnl = qty * (mark - entry) * (1 if direction == "long" else -1)
            # FIXED v7.1: fee sobre notional real de apertura
            pnl -= self.state["position"]["notional"] * FEE_RATE * 2
            self._record_close(pnl, "Cerrado externamente")
            self.state["position"] = None
            save_state(self.state)

    def _record_close(self, pnl: float, reason: str):
        self.state["pnl_today"]    += pnl
        self.state["total_pnl"]    += pnl
        self.state["total_trades"] += 1
        save_state(self.state)
        emoji = "✅" if pnl > 0 else "❌"
        log.info(f"  {emoji} Cierre: {reason} | PnL ${pnl:+.4f}")

    # ── Open position ─────────────────────────────────────────────
    def open_position(self, sig: dict) -> bool:
        symbol    = sig["symbol"]
        direction = sig["direction"]
        entry     = sig["entry"]
        sl        = sig["sl"]
        tp        = sig["tp"]
        tp_partial = sig.get("tp_partial", (entry + tp) / 2)

        balance = get_balance()
        if balance < MIN_BALANCE:
            log.warning(f"Balance ${balance:.2f} < mínimo ${MIN_BALANCE}")
            return False

        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return False

        risk_usd   = balance * RISK_PCT
        qty_raw    = (risk_usd * LEVERAGE) / sl_dist
        info       = get_symbol_info(symbol)
        qty_prec   = int(info.get("quantityPrecision", 3))
        price_prec = int(info.get("pricePrecision", 4))
        qty        = round(qty_raw, qty_prec)
        if qty <= 0:
            qty = round(5.0 / entry, qty_prec)

        notional   = qty * entry
        # FIXED v7.1: fee calculada sobre notional real (entrada + salida)
        fee_entry  = notional * FEE_RATE
        fee_exit   = notional * FEE_RATE         # aprox igual al cierre
        fee_total  = fee_entry + fee_exit
        tp_pct     = abs(tp - entry) / entry * 100
        sl_pct     = abs(sl - entry) / entry * 100
        exp_profit = notional * (tp_pct / 100) - fee_total

        log.info(f"  → {direction.upper()} {symbol} qty={qty} @ ${entry:.4f}")
        log.info(f"     TP: ${tp:.4f} (+{tp_pct:.2f}%) | SL: ${sl:.4f} (-{sl_pct:.2f}%)")
        log.info(f"     TP parcial: ${tp_partial:.4f} | Profit esp: ${exp_profit:.3f}")

        if DRY_RUN:
            log.info(f"  🔵 [DRY RUN] No se ejecuta orden real")
        else:
            try:
                set_margin_isolated(symbol)
                set_leverage(symbol, LEVERAGE, "LONG")
                set_leverage(symbol, LEVERAGE, "SHORT")
                side     = "BUY"  if direction == "long" else "SELL"
                pos_side = "LONG" if direction == "long" else "SHORT"
                r = place_market_order(symbol, side, pos_side, qty)
                if r.get("code", -1) != 0:
                    log.error(f"  BingX error: {r}")
                    return False
                log.info(f"  ✅ Orden MARKET ejecutada: {r.get('data',{}).get('orderId','?')}")
            except Exception as e:
                log.error(f"  ❌ open_position error: {e}")
                return False

        self.state["position"] = {
            "symbol":       symbol,
            "direction":    direction,
            "entry":        entry,
            "sl":           sl,
            "sl_original":  sl,          # NUEVO: para trailing
            "tp":           tp,
            "tp_partial":   tp_partial,  # NUEVO: cierre parcial
            "qty":          qty,
            "qty_original": qty,         # NUEVO: qty inicial
            "notional":     notional,
            "tp_pct":       tp_pct,
            "sl_pct":       sl_pct,
            "opened_at":    datetime.now(timezone.utc).isoformat(),
            "partial_done": False,       # flag TP parcial ejecutado
            "breakeven_set": False,      # flag SL movido a breakeven
        }
        self.state["trades_today"] += 1
        save_state(self.state)

        e = "🟢" if direction == "long" else "🔴"
        telegram(
            f"<b>{e} {direction.upper()} ABIERTO [v7.1]</b>\n"
            f"Par: {symbol} | {TIMEFRAME} | {LEVERAGE}x\n"
            f"Precio: ${entry:.4f} | Qty: {qty}\n"
            f"TP: ${tp:.4f} (+{tp_pct:.2f}%) | TP50%: ${tp_partial:.4f}\n"
            f"SL: ${sl:.4f} (-{sl_pct:.2f}%)\n"
            f"Ratio: {TP_RATIO}:1 | Profit esp: ${exp_profit:.3f}\n"
            f"Score: {sig['score']}/11 | {sig['reason']}\n"
            f"Balance: ${balance:.2f} | Hoy: {self.state['trades_today']}/{MAX_TRADES_DAY}\n"
            f"{'⚠️ DRY RUN' if DRY_RUN else '💰 REAL'}"
        )
        return True

    # ── Close position (full or partial) ─────────────────────────
    def close_position(self, reason: str, price: float = None, partial: bool = False) -> bool:
        t = self.state["position"]
        if not t:
            return True

        sym       = t["symbol"]
        d         = t["direction"]
        qty_close = round(t["qty"] * 0.5, 6) if partial else t["qty"]
        cur       = price or get_mark_price(sym) or t["entry"]

        # FIXED v7.1: PnL proporcional a qty cerrada
        pnl_raw = qty_close * (cur - t["entry"]) * (1 if d == "long" else -1)
        # fee proporcional
        fee     = (qty_close * cur) * FEE_RATE * 2
        pnl_net = pnl_raw - fee

        opened  = datetime.fromisoformat(t["opened_at"])
        hold_m  = int((datetime.now(timezone.utc) - opened).total_seconds() / 60)

        log.info(f"  {'PARCIAL' if partial else 'TOTAL'} CERRANDO {d.upper()} {sym} "
                 f"| {reason} | qty={qty_close} | ${pnl_net:+.4f} | {hold_m}min")

        if not DRY_RUN:
            pos_side = "LONG" if d == "long" else "SHORT"
            ok = close_position_market(sym, pos_side, qty_close)
            if not ok:
                log.error(f"  ❌ No se pudo cerrar {sym}")
                return False

        self._record_close(pnl_net, f"{reason} ({'parcial' if partial else 'total'})")

        if partial:
            # Actualizar qty restante y mover SL a breakeven
            remaining = round(t["qty"] - qty_close, 6)
            self.state["position"]["qty"]          = remaining
            self.state["position"]["partial_done"] = True
            # FIXED: mover SL a breakeven tras TP parcial
            entry = t["entry"]
            self.state["position"]["sl"]           = entry
            self.state["position"]["breakeven_set"] = True
            save_state(self.state)
            log.info(f"  🔒 SL movido a breakeven ${entry:.4f} | Restante: {remaining}")
            telegram(
                f"<b>💰 TP PARCIAL {d.upper()} {sym}</b>\n"
                f"50% cerrado @ ${cur:.4f} | PnL: ${pnl_net:+.4f}\n"
                f"SL movido a breakeven ${entry:.4f}"
            )
        else:
            self._set_cooldown(sym)
            emoji = "✅" if pnl_net > 0 else "❌"
            telegram(
                f"<b>{emoji} CERRADO {d.upper()} — {reason}</b>\n"
                f"Par: {sym} | {hold_m}min\n"
                f"${t['entry']:.4f} → ${cur:.4f}\n"
                f"PnL neto: <b>${pnl_net:+.4f}</b>\n"
                f"PnL hoy: ${self.state['pnl_today']:+.4f} | "
                f"Total: ${self.state['total_pnl']:+.4f}"
            )
            self.state["position"] = None
            save_state(self.state)
            time.sleep(1)

        return True

    # ── Monitor open position ─────────────────────────────────────
    def monitor(self):
        t = self.state["position"]
        if not t:
            return

        self._sync_position()
        if not self.state["position"]:
            return

        sym        = t["symbol"]
        d          = t["direction"]
        cur        = get_mark_price(sym)
        if cur <= 0:
            return

        pnl_pct  = (cur - t["entry"]) / t["entry"] * 100 * (1 if d == "long" else -1)
        hold_m   = int((datetime.now(timezone.utc) -
                        datetime.fromisoformat(t["opened_at"])).total_seconds() / 60)

        log.info(f"  📊 {d.upper()} {sym}: ${cur:.4f} | "
                 f"PnL: {pnl_pct:+.2f}% | {hold_m}min | "
                 f"partial={'✓' if t.get('partial_done') else '○'} "
                 f"BE={'✓' if t.get('breakeven_set') else '○'}")

        # SL (con o sin trailing)
        sl = t["sl"]
        if d == "long" and cur <= sl:
            self.close_position(f"SL @ ${cur:.4f}", cur)
            return
        if d == "short" and cur >= sl:
            self.close_position(f"SL @ ${cur:.4f}", cur)
            return

        # NUEVO: TP parcial al 50% del camino
        if not t.get("partial_done"):
            tp_partial = t.get("tp_partial", t["tp"])
            if (d == "long"  and cur >= tp_partial) or \
               (d == "short" and cur <= tp_partial):
                self.close_position(f"TP parcial @ ${cur:.4f}", cur, partial=True)
                return

        # TP completo
        if (d == "long"  and cur >= t["tp"]) or \
           (d == "short" and cur <= t["tp"]):
            self.close_position(f"TP @ ${cur:.4f}", cur)
            return

        # NUEVO: trailing stop tras TP parcial
        # Mueve el SL por encima del breakeven a medida que sube el precio
        if t.get("partial_done") and t.get("breakeven_set"):
            entry   = t["entry"]
            tp_full = t["tp"]
            # Trailing: SL = max(breakeven, precio - 0.5 * sl_dist_original)
            sl_orig_dist = abs(entry - t["sl_original"])
            if d == "long":
                trailing_sl = round(cur - sl_orig_dist * 0.5, 6)
                new_sl = max(entry, trailing_sl)
                if new_sl > self.state["position"]["sl"]:
                    self.state["position"]["sl"] = new_sl
                    save_state(self.state)
                    log.info(f"  📈 Trailing SL → ${new_sl:.4f}")
            else:
                trailing_sl = round(cur + sl_orig_dist * 0.5, 6)
                new_sl = min(entry, trailing_sl)
                if new_sl < self.state["position"]["sl"]:
                    self.state["position"]["sl"] = new_sl
                    save_state(self.state)
                    log.info(f"  📉 Trailing SL → ${new_sl:.4f}")

    # ── Main loop ─────────────────────────────────────────────────
    def run(self):
        log.info("=" * 65)
        log.info("  🤖 BingX SuperBot v7.1")
        log.info(f"  TF:{TIMEFRAME} HTF:{HTF} | LEV:{LEVERAGE}x | "
                 f"RISK:{RISK_PCT:.1%} | TP_RATIO:{TP_RATIO} | MIN_SCORE:{MIN_SCORE}")
        log.info(f"  MaxDay:{MAX_TRADES_DAY} | DailyStop:${DAILY_STOP_USD} | "
                 f"Cooldown:{COOLDOWN_HOURS}h | Scan:{SCAN_INTERVAL}s")
        log.info(f"  {'⚠️  DRY RUN ACTIVO — sin trades reales' if DRY_RUN else '💰 MODO REAL'}")
        log.info("=" * 65)

        bal = get_balance()
        if bal == 0.0 and not DRY_RUN:
            log.error("Balance = 0 o error de conexión. Revisa credenciales.")
            sys.exit(1)
        log.info(f"💼 Balance: ${bal:.2f} USDT")

        log.info("🗑️  Cancelando todas las órdenes pendientes...")
        n = cancel_all_orders_global()
        log.info(f"   {n} pares limpiados")
        time.sleep(2)

        telegram(
            f"<b>🤖 SuperBot v7.1 iniciado</b>\n"
            f"TF: {TIMEFRAME} | HTF: {HTF} | {LEVERAGE}x\n"
            f"Min score: {MIN_SCORE}/11 | Min ADX: {MIN_ADX}\n"
            f"Max {MAX_TRADES_DAY} trades/día | Stop -${DAILY_STOP_USD}\n"
            f"Scan cada {SCAN_INTERVAL}s | TP parcial + trailing\n"
            f"Balance: ${bal:.2f} USDT\n"
            f"{'⚠️ DRY RUN' if DRY_RUN else '💰 MODO REAL'}"
        )

        cycle       = 0
        last_scan   = 0
        last_report = 0

        while True:
            try:
                cycle += 1
                self._daily_reset()

                bal = get_balance()
                pos = self.state["position"]
                log.info(
                    f"\n{'='*65}\n"
                    f"  #{cycle} {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} | "
                    f"${bal:.2f} | Pos:{'Sí' if pos else 'No'} | "
                    f"Hoy: ${self.state['pnl_today']:+.3f} "
                    f"({self.state['trades_today']}/{MAX_TRADES_DAY})\n"
                    f"{'='*65}"
                )

                # Stop diario
                if self.state["pnl_today"] < -DAILY_STOP_USD:
                    if self.state["position"]:
                        self.close_position("Stop diario")
                    log.warning(f"  🔴 STOP DIARIO: ${self.state['pnl_today']:.3f}")
                    telegram(f"<b>🔴 Stop diario activado</b>\n"
                             f"Pérdida: ${self.state['pnl_today']:.4f}")
                    while datetime.now(timezone.utc).strftime("%Y-%m-%d") == self.state["today_date"]:
                        time.sleep(300)
                    continue

                # Monitorear posición activa
                if self.state["position"]:
                    self.monitor()
                    time.sleep(MONITOR_INTERVAL)
                    continue

                # Límite diario
                if self.state["trades_today"] >= MAX_TRADES_DAY:
                    log.info(f"  Límite diario {self.state['trades_today']}/{MAX_TRADES_DAY}")
                    time.sleep(300)
                    continue

                # Balance mínimo
                if bal < MIN_BALANCE:
                    log.warning(f"  Balance ${bal:.2f} < ${MIN_BALANCE}")
                    time.sleep(300)
                    continue

                # Escanear mercado
                now = time.time()
                if now - last_scan >= SCAN_INTERVAL:
                    last_scan = now
                    exclude = set()
                    if self.state["position"]:
                        exclude.add(self.state["position"]["symbol"])
                    exclude.update(k for k, v in self.state["cooldowns"].items()
                                   if time.time() < v)

                    results = scan_market(exclude)
                    for sig in results:
                        if self.state["position"]:
                            break
                        if self._on_cooldown(sig["symbol"]):
                            continue
                        ok = self.open_position(sig)
                        if ok:
                            break
                        time.sleep(0.5)

                # Reporte horario
                if now - last_report > 3600:
                    last_report = now
                    telegram(
                        f"<b>📊 Reporte horario v7.1</b>\n"
                        f"Balance: ${bal:.2f}\n"
                        f"Trades hoy: {self.state['trades_today']}/{MAX_TRADES_DAY}\n"
                        f"PnL hoy: ${self.state['pnl_today']:+.4f}\n"
                        f"PnL total: ${self.state['total_pnl']:+.4f}\n"
                        f"Trades total: {self.state['total_trades']}"
                    )

                time.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                if self.state["position"]:
                    self.close_position("Bot detenido")
                telegram("<b>Bot v7.1 detenido manualmente</b>")
                sys.exit(0)
            except Exception as e:
                log.error(f"Loop #{cycle}: {e}", exc_info=True)
                telegram(f"<b>⚠️ Error v7.1</b>\n{e}")
                time.sleep(30)


if __name__ == "__main__":
    Bot().run()
