"""
╔══════════════════════════════════════════════════════════════════╗
║           BINGX SUPERBOT — v7.0  (ARCHIVO ÚNICO)                ║
║                                                                  ║
║  PROBLEMAS ANTERIORES RESUELTOS:                                 ║
║  ✅ Sin imports externos → sin ModuleNotFoundError               ║
║  ✅ MARKET entry → siempre se ejecuta (no pending orders)        ║
║  ✅ SL/TP monitoreado en código → no depende de órdenes BingX    ║
║  ✅ Cancela TODAS las órdenes pendientes al arrancar              ║
║  ✅ Escanea TODOS los pares USDT de BingX                        ║
║  ✅ Filtros duros: ADX > 25, volumen, tendencia multi-TF         ║
║  ✅ Máximo 2 trades/día, 1 posición a la vez                     ║
║  ✅ Ratio mínimo TP:SL = 2.5:1                                   ║
╚══════════════════════════════════════════════════════════════════╝

ENV VARS necesarias en Railway:
  BINGX_API_KEY       = tu_api_key
  BINGX_SECRET_KEY    = tu_secret_key
  TELEGRAM_TOKEN      = (opcional)
  TELEGRAM_CHAT_ID    = (opcional)
  DRY_RUN             = true   ← empieza siempre en dry run
"""

# ── Standard library only + requests + pandas ────────────────────────
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
#  CONFIG — desde variables de entorno
# ═══════════════════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY")    or os.environ.get("BINGX_KEY",    "")
SECRET_KEY = os.environ.get("BINGX_SECRET_KEY") or os.environ.get("BINGX_SECRET", "")
TG_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TG_CHAT    = os.environ.get("TELEGRAM_CHAT_ID", "")
DRY_RUN    = os.environ.get("DRY_RUN", "true").lower() == "true"

if not API_KEY or not SECRET_KEY:
    log.critical("❌ Faltan BINGX_API_KEY y BINGX_SECRET_KEY en Railway → Variables")
    sys.exit(1)

# ── Parámetros de trading ─────────────────────────────────────────────
BASE_URL        = "https://open-api.bingx.com"
TIMEFRAME       = "1h"          # 1h: buen balance señal/ruido
HTF             = "4h"          # higher timeframe para confirmación
LEVERAGE        = 3             # 3x: conservador
RISK_PCT        = 0.015         # 1.5% del balance por trade
TP_RATIO        = 2.5           # TP = 2.5 × SL (ratio mínimo)
ATR_SL_MULT     = 1.8           # SL = 1.8 × ATR
MAX_POSITIONS   = 1             # UNA sola posición a la vez
MAX_TRADES_DAY  = 2             # máximo 2 trades por día
DAILY_STOP_USD  = 4.0           # stop si pierde $4 en el día
MIN_BALANCE     = 20.0          # balance mínimo para operar
MIN_VOLUME_24H  = 8_000_000     # $8M volumen diario mínimo
MIN_ADX         = 28            # ADX mínimo — filtra mercados laterales
SCAN_INTERVAL   = 600           # escanear cada 10 minutos
MONITOR_INTERVAL = 30           # revisar posición cada 30 segundos
COOLDOWN_BARS   = 3             # horas de cooldown tras cerrar un par
MAX_SCAN_SYMBOLS = 80           # máximo pares a escanear
SCAN_THREADS    = 6             # hilos paralelos para escaneo
FEE_RATE        = 0.0005        # 0.05% taker fee BingX
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
            if (str(c.get("currency","")).upper() == "USDT"
                    and int(c.get("status", 0)) == 1
                    and str(c.get("symbol","")).endswith("-USDT")):
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

def get_open_orders(symbol: str) -> list[dict]:
    try:
        d = _get("/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
        return d.get("data", {}).get("orders", [])
    except Exception:
        return []

def cancel_all_orders(symbol: str) -> bool:
    try:
        _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
        return True
    except Exception as e:
        log.warning(f"cancel_all_orders {symbol}: {e}")
        return False

def cancel_all_orders_global():
    """Cancela TODAS las órdenes pendientes en todos los pares."""
    try:
        # Obtener todos los pares con órdenes abiertas
        d = _get("/openApi/swap/v2/trade/allOpenOrders")
        orders = d.get("data", {}).get("orders", [])
        symbols_with_orders = set(o.get("symbol","") for o in orders)
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
    """
    MARKET order — siempre se ejecuta.
    side: BUY | SELL
    pos_side: LONG | SHORT
    """
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
#  INDICATORS (puro numpy — sin dependencias externas)
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
    d   = np.diff(closes[-n*3:])
    g   = np.where(d > 0, d, 0.0)
    l   = np.where(d < 0, -d, 0.0)
    ag  = np.mean(g[-n:])
    al  = np.mean(l[-n:])
    if al == 0:
        return 100.0
    return float(100 - 100 / (1 + ag / al))

def _atr_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float:
    if len(close) < n + 1:
        return 0.0
    pc  = np.roll(close, 1)
    pc[0] = close[0]
    tr  = np.maximum(high - low,
          np.maximum(np.abs(high - pc), np.abs(low - pc)))
    return float(np.mean(tr[-n:]))

def _adx_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float:
    if len(close) < n * 2:
        return 0.0
    ph  = np.roll(high, 1);  ph[0]  = high[0]
    pl  = np.roll(low,  1);  pl[0]  = low[0]
    pc  = np.roll(close, 1); pc[0]  = close[0]
    tr  = np.maximum(high - low,
          np.maximum(np.abs(high - pc), np.abs(low - pc)))
    dm_p = np.where((high - ph) > (pl - low), np.maximum(high - ph, 0), 0.0)
    dm_m = np.where((pl - low) > (high - ph), np.maximum(pl - low, 0), 0.0)
    atr14  = np.mean(tr[-n:])
    if atr14 == 0:
        return 0.0
    di_p = 100 * np.mean(dm_p[-n:]) / atr14
    di_m = 100 * np.mean(dm_m[-n:]) / atr14
    denom = di_p + di_m
    if denom == 0:
        return 0.0
    dx   = 100 * abs(di_p - di_m) / denom
    return float(dx)

def _macd_np(closes: np.ndarray):
    e12 = _ema_np(closes, 12)
    e26 = _ema_np(closes, 26)
    m   = e12 - e26
    sig = _ema_np(m, 9)
    return float(m[-1]), float(sig[-1])

def _vwap_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, vol: np.ndarray) -> float:
    hlc3  = (high + low + close) / 3.0
    cv    = np.cumsum(vol)
    cvp   = np.cumsum(hlc3 * vol)
    return float(cvp[-1] / cv[-1]) if cv[-1] > 0 else float(close[-1])

def _ema_val(closes: np.ndarray, n: int) -> float:
    return float(_ema_np(closes, n)[-1])

def _vol_ratio(vol: np.ndarray, n: int = 20) -> float:
    if len(vol) < n + 1:
        return 1.0
    avg = np.mean(vol[-n-1:-1])
    return float(vol[-1] / avg) if avg > 0 else 1.0

# ═══════════════════════════════════════════════════════════════════════
#  STRATEGY — señal de entrada
# ═══════════════════════════════════════════════════════════════════════
def analyze_symbol(symbol: str) -> dict:
    """
    Retorna dict con:
      direction: 'long' | 'short' | None
      entry, sl, tp, atr, score, reason
    """
    EMPTY = {"direction": None, "entry": 0, "sl": 0, "tp": 0,
             "atr": 0, "score": 0, "reason": "no data"}

    # ── Cargar velas LTF (1h) ────────────────────────────────────
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

    # ── Cargar velas HTF (4h) para confirmación ──────────────────
    klines_4h = get_klines(symbol, HTF, 60)
    htf_trend = 0  # 1=bull, -1=bear
    htf_rsi   = 50.0
    if len(klines_4h) >= 30:
        c4 = np.array([k["close"] for k in klines_4h], dtype=float)
        h4 = np.array([k["high"]  for k in klines_4h], dtype=float)
        l4 = np.array([k["low"]   for k in klines_4h], dtype=float)
        e9_4h  = _ema_val(c4, 9)
        e21_4h = _ema_val(c4, 21)
        htf_rsi = _rsi_np(c4, 14)
        htf_trend = 1 if e9_4h > e21_4h else -1

    # ── Indicadores LTF ──────────────────────────────────────────
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

    # Filtro: sin movimiento = no operar
    if atr_pct < 0.3:
        return {**EMPTY, "reason": f"ATR bajo {atr_pct:.2f}%"}

    # ── Cruce EMA ────────────────────────────────────────────────
    cross_up   = e9  > e21  and e9p <= e21p
    cross_down = e9  < e21  and e9p >= e21p

    # ── Contexto reciente ────────────────────────────────────────
    recent_high = float(np.max(h[-4:-1]))   # máximo de 3 velas anteriores
    recent_low  = float(np.min(l[-4:-1]))   # mínimo de 3 velas anteriores
    breakout_up   = price > recent_high and c[-1] > o[-1]
    breakout_down = price < recent_low  and c[-1] < o[-1]

    # ── Scoring (0-10) ───────────────────────────────────────────
    def bull_score():
        s = 0
        s += 2 if cross_up   else 0          # trigger principal
        s += 1 if htf_trend == 1  else 0     # HTF alcista
        s += 1 if htf_rsi > 52   else 0      # HTF RSI
        s += 1 if price > vwap   else 0      # precio > VWAP
        s += 1 if price > e50    else 0      # precio > EMA50
        s += 1 if macd > macd_sig else 0     # MACD alcista
        s += 1 if rsi > 50 and rsi < 70 else 0  # RSI zona correcta
        s += 1 if vol_r > 1.2    else 0      # volumen elevado
        s += 1 if breakout_up    else 0      # ruptura de máximo
        return s

    def bear_score():
        s = 0
        s += 2 if cross_down  else 0
        s += 1 if htf_trend == -1  else 0
        s += 1 if htf_rsi < 48    else 0
        s += 1 if price < vwap    else 0
        s += 1 if price < e50     else 0
        s += 1 if macd < macd_sig else 0
        s += 1 if rsi < 50 and rsi > 30 else 0
        s += 1 if vol_r > 1.2     else 0
        s += 1 if breakout_down   else 0
        return s

    bs = bull_score()
    ss = bear_score()

    # ── Filtros duros ────────────────────────────────────────────
    MIN_SCORE = 6    # necesita 6/10

    sl_dist = max(atr * ATR_SL_MULT, price * 0.015)
    tp_dist = sl_dist * TP_RATIO

    # Verificar profit real después de fees
    notional    = (price * 1.0) * LEVERAGE  # 1 coin aprox
    fee_cost    = notional * FEE_RATE * 2
    exp_profit  = (tp_dist / price) * notional - fee_cost

    # ── LONG ─────────────────────────────────────────────────────
    if (cross_up and bs >= MIN_SCORE and adx >= MIN_ADX
            and htf_trend == 1 and exp_profit > 0.3):
        sl = round(price - sl_dist, 6)
        tp = round(price + tp_dist, 6)
        return {
            "direction": "long",
            "entry":     price,
            "sl":        sl,
            "tp":        tp,
            "atr":       atr,
            "score":     bs,
            "reason":    (f"LONG score={bs}/10 ADX={adx:.0f} "
                          f"RSI={rsi:.0f} HTF={htf_trend:+d} "
                          f"vol_ratio={vol_r:.1f}x"),
        }

    # ── SHORT ────────────────────────────────────────────────────
    if (cross_down and ss >= MIN_SCORE and adx >= MIN_ADX
            and htf_trend == -1 and exp_profit > 0.3):
        sl = round(price + sl_dist, 6)
        tp = round(price - tp_dist, 6)
        return {
            "direction": "short",
            "entry":     price,
            "sl":        sl,
            "tp":        tp,
            "atr":       atr,
            "score":     ss,
            "reason":    (f"SHORT score={ss}/10 ADX={adx:.0f} "
                          f"RSI={rsi:.0f} HTF={htf_trend:+d} "
                          f"vol_ratio={vol_r:.1f}x"),
        }

    return {
        **EMPTY,
        "reason": (f"Sin señal | cross={'UP' if cross_up else 'DWN' if cross_down else 'NO'} "
                   f"score=B{bs}/S{ss} ADX={adx:.0f}"),
    }

# ═══════════════════════════════════════════════════════════════════════
#  MARKET SCANNER
# ═══════════════════════════════════════════════════════════════════════
def scan_market(exclude_symbols: set = None) -> list[dict]:
    """Escanea todos los pares USDT, devuelve lista ordenada por score."""
    exclude = exclude_symbols or set()

    all_syms = get_all_symbols()
    if not all_syms:
        log.warning("Sin símbolos de BingX")
        return []

    # Prioridad a los más líquidos
    priority = [
        "BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
        "DOGE-USDT","ADA-USDT","AVAX-USDT","DOT-USDT","MATIC-USDT",
        "LINK-USDT","UNI-USDT","ATOM-USDT","LTC-USDT","FIL-USDT",
        "NEAR-USDT","APT-USDT","ARB-USDT","OP-USDT","SUI-USDT",
    ]
    others = [s for s in all_syms if s not in priority and s not in exclude]
    symbols = [s for s in priority if s not in exclude] + others
    symbols = symbols[:MAX_SCAN_SYMBOLS]

    log.info(f"🔍 Escaneando {len(symbols)} pares...")

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

    # ── Daily reset ───────────────────────────────────────────────
    def _daily_reset(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state["today_date"] != today:
            self.state["trades_today"] = 0
            self.state["pnl_today"]    = 0.0
            self.state["today_date"]   = today
            save_state(self.state)
            bal = get_balance()
            log.info(f"📅 Nuevo día: {today} | Balance: ${bal:.2f}")

    # ── Cooldown ──────────────────────────────────────────────────
    def _on_cooldown(self, symbol: str) -> bool:
        exp = self.state["cooldowns"].get(symbol, 0)
        return time.time() < exp

    def _set_cooldown(self, symbol: str):
        self.state["cooldowns"][symbol] = time.time() + COOLDOWN_BARS * 3600
        save_state(self.state)

    # ── Sync position with BingX ──────────────────────────────────
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
            entry = self.state["position"]["entry"]
            mark  = get_mark_price(sym) or entry
            direction = self.state["position"]["direction"]
            qty       = self.state["position"]["qty"]
            pnl = qty * (mark - entry) * (1 if direction == "long" else -1)
            pnl -= qty * entry * FEE_RATE * 2
            self._record_close(pnl, "Cerrado externamente")
            self.state["position"] = None
            save_state(self.state)

    def _record_close(self, pnl: float, reason: str):
        self.state["pnl_today"]  += pnl
        self.state["total_pnl"]  += pnl
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

        balance = get_balance()
        if balance < MIN_BALANCE:
            log.warning(f"Balance ${balance:.2f} < mínimo ${MIN_BALANCE}")
            return False

        # Calcular cantidad
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return False
        risk_usd = balance * RISK_PCT
        qty_raw  = (risk_usd * LEVERAGE) / sl_dist
        # Obtener precisión del par
        info     = get_symbol_info(symbol)
        qty_prec = int(info.get("quantityPrecision", 3))
        price_prec = int(info.get("pricePrecision", 4))
        qty      = round(qty_raw, qty_prec)
        if qty <= 0:
            qty = round(5.0 / entry, qty_prec)

        notional   = qty * entry
        fee_total  = notional * FEE_RATE * 2
        tp_pct     = abs(tp - entry) / entry * 100
        sl_pct     = abs(sl - entry) / entry * 100
        exp_profit = notional * (tp_pct / 100) - fee_total

        log.info(f"  → {direction.upper()} {symbol} qty={qty} @ ${entry:.4f}")
        log.info(f"     TP: ${tp:.4f} (+{tp_pct:.2f}%) | SL: ${sl:.4f} (-{sl_pct:.2f}%)")
        log.info(f"     Profit esperado: ${exp_profit:.3f} | {sig['reason']}")

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
            "symbol":    symbol,
            "direction": direction,
            "entry":     entry,
            "sl":        sl,
            "tp":        tp,
            "qty":       qty,
            "notional":  notional,
            "tp_pct":    tp_pct,
            "sl_pct":    sl_pct,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "partial_done": False,
        }
        self.state["trades_today"] += 1
        save_state(self.state)

        e = "🟢" if direction == "long" else "🔴"
        telegram(
            f"<b>{e} {direction.upper()} ABIERTO [v7.0]</b>\n"
            f"Par: {symbol} | {TIMEFRAME} | {LEVERAGE}x\n"
            f"Precio: ${entry:.4f} | Qty: {qty}\n"
            f"TP: ${tp:.4f} (+{tp_pct:.2f}%)\n"
            f"SL: ${sl:.4f} (-{sl_pct:.2f}%)\n"
            f"Ratio: {TP_RATIO}:1 | Profit esp: ${exp_profit:.3f}\n"
            f"Score: {sig['score']}/10 | {sig['reason']}\n"
            f"Balance: ${balance:.2f} | Hoy: {self.state['trades_today']}/{MAX_TRADES_DAY}\n"
            f"{'⚠️ DRY RUN' if DRY_RUN else '💰 REAL'}"
        )
        return True

    # ── Close position ────────────────────────────────────────────
    def close_position(self, reason: str, price: float = None) -> bool:
        t = self.state["position"]
        if not t:
            return True
        sym   = t["symbol"]
        d     = t["direction"]
        qty   = t["qty"]
        cur   = price or get_mark_price(sym) or t["entry"]

        pnl_raw = qty * (cur - t["entry"]) * (1 if d == "long" else -1)
        fee     = t["notional"] * FEE_RATE * 2
        pnl_net = pnl_raw - fee

        opened  = datetime.fromisoformat(t["opened_at"])
        hold_m  = int((datetime.now(timezone.utc) - opened).total_seconds() / 60)

        log.info(f"  CERRANDO {d.upper()} {sym} | {reason} | ${pnl_net:+.4f} | {hold_m}min")

        if not DRY_RUN:
            pos_side = "LONG" if d == "long" else "SHORT"
            ok = close_position_market(sym, pos_side, qty)
            if not ok:
                log.error(f"  ❌ No se pudo cerrar {sym} — intentar manualmente en BingX")
                return False

        self._record_close(pnl_net, reason)
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

        sym = t["symbol"]
        d   = t["direction"]
        cur = get_mark_price(sym)
        if cur <= 0:
            return

        pnl_pct = (cur - t["entry"]) / t["entry"] * 100 * (1 if d == "long" else -1)
        hold_m  = int((datetime.now(timezone.utc) -
                       datetime.fromisoformat(t["opened_at"])).total_seconds() / 60)

        log.info(f"  📊 {d.upper()} {sym}: ${cur:.4f} | "
                 f"PnL: {pnl_pct:+.2f}% | {hold_m}min")

        # SL en código (protección adicional)
        if d == "long"  and cur <= t["sl"]:
            self.close_position(f"SL @ ${cur:.4f}", cur); return
        if d == "short" and cur >= t["sl"]:
            self.close_position(f"SL @ ${cur:.4f}", cur); return

        # TP
        if d == "long"  and cur >= t["tp"]:
            self.close_position(f"TP @ ${cur:.4f}", cur); return
        if d == "short" and cur <= t["tp"]:
            self.close_position(f"TP @ ${cur:.4f}", cur); return

    # ── Main loop ─────────────────────────────────────────────────
    def run(self):
        log.info("=" * 65)
        log.info("  🤖 BingX SuperBot v7.0 — Archivo único autocontenido")
        log.info(f"  TF:{TIMEFRAME} HTF:{HTF} | LEV:{LEVERAGE}x | "
                 f"RISK:{RISK_PCT:.1%} | TP_RATIO:{TP_RATIO}")
        log.info(f"  MaxDay:{MAX_TRADES_DAY} | DailyStop:${DAILY_STOP_USD} | "
                 f"MinVol:${MIN_VOLUME_24H/1e6:.0f}M")
        log.info(f"  {'⚠️  DRY RUN ACTIVO — sin trades reales' if DRY_RUN else '💰 MODO REAL'}")
        log.info("=" * 65)

        # Test de conexión
        bal = get_balance()
        if bal == 0.0 and not DRY_RUN:
            log.error("Balance = 0 o error de conexión. Revisa credenciales.")
            sys.exit(1)
        log.info(f"💼 Balance: ${bal:.2f} USDT")

        # ─── STARTUP: Cancelar TODAS las órdenes pendientes ───────
        log.info("🗑️  Cancelando todas las órdenes pendientes...")
        n = cancel_all_orders_global()
        log.info(f"   {n} pares limpiados")
        time.sleep(2)

        telegram(
            f"<b>🤖 SuperBot v7.0 iniciado</b>\n"
            f"TF: {TIMEFRAME} | HTF: {HTF} | {LEVERAGE}x\n"
            f"Pares: hasta {MAX_SCAN_SYMBOLS} USDT perps\n"
            f"Max {MAX_TRADES_DAY} trades/día | Stop -${DAILY_STOP_USD}\n"
            f"Balance: ${bal:.2f} USDT\n"
            f"{'⚠️ DRY RUN' if DRY_RUN else '💰 MODO REAL'}"
        )

        cycle        = 0
        last_scan    = 0
        last_report  = 0

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
                    # Esperar al día siguiente
                    while datetime.now(timezone.utc).strftime("%Y-%m-%d") == self.state["today_date"]:
                        time.sleep(300)
                    continue

                # Monitorear posición activa cada MONITOR_INTERVAL
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

                # Escanear mercado cada SCAN_INTERVAL
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
                        f"<b>📊 Reporte horario v7.0</b>\n"
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
                telegram("<b>Bot v7.0 detenido manualmente</b>")
                sys.exit(0)
            except Exception as e:
                log.error(f"Loop #{cycle}: {e}", exc_info=True)
                telegram(f"<b>⚠️ Error v7.0</b>\n{e}")
                time.sleep(30)


if __name__ == "__main__":
    Bot().run()
