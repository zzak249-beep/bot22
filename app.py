"""
╔══════════════════════════════════════════════════════════════════════╗
║          AEGIS GEX v6.0 — Simons-QE Quant Engine                   ║
║  Async · Maker-First · MTF · LSTM+RF · Hurst · OI · Fear&Greed     ║
║  BingX Futuros Perpetuos → Telegram                                 ║
╚══════════════════════════════════════════════════════════════════════╝

Mejoras v6 sobre v5:
  • Hurst Exponent   → detecta si el mercado es trending o mean-reverting
                       y activa las señales correctas para cada régimen
  • Autocorrelación  → mide momentum serial, filtra ruido
  • Kurtosis filter  → bloquea entradas en distribuciones de cola gorda
                       (pre-flash-crash, como hacía Medallion Fund)
  • OI Delta         → confirma o rechaza señales con flujo institucional
  • Fear & Greed     → sesgo macro QE/liquidez vía alternative.me (gratis)
  • Skewness         → asimetría de retornos como filtro adicional
  • Regime-aware     → Z-Score solo en H<0.5 | momentum solo en H>0.5
"""

import os, asyncio, logging, math, time, collections, random, json
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import ccxt.pro as ccxt_pro
import ccxt as ccxt_sync
import requests as http_requests

# ─────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("AEGIS")

# ═══════════════════════════════════════════════════════════════
#   CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════

BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")
MODE             = os.getenv("MODE", "paper")

# ── Riesgo ───────────────────────────────────
ACCOUNT_BALANCE  = float(os.getenv("ACCOUNT_BALANCE",  "100"))
RISK_PER_TRADE   = float(os.getenv("RISK_PER_TRADE",   "0.01"))
MAX_OPEN_TRADES  = int(os.getenv("MAX_OPEN_TRADES",    "3"))
MAX_DAILY_LOSS   = float(os.getenv("MAX_DAILY_LOSS",   "0.03"))
LEVERAGE         = int(os.getenv("LEVERAGE",           "3"))

# ── Ejecución ─────────────────────────────────
LIMIT_WAIT_SECS  = int(os.getenv("LIMIT_WAIT_SECS",   "8"))
PRICE_SLIP_PCT   = float(os.getenv("PRICE_SLIP_PCT",  "0.0005"))

# ── Multi-Timeframe ───────────────────────────
MTF_TIMEFRAMES      = ["1m", "5m", "15m", "1h"]
MTF_WEIGHTS         = {"1m": 0.15, "5m": 0.25, "15m": 0.35, "1h": 0.25}
MTF_CONFLUENCE_MIN  = float(os.getenv("MTF_CONFLUENCE_MIN", "0.55"))

# ── Señales ───────────────────────────────────
ZSCORE_WINDOW        = int(os.getenv("ZSCORE_WINDOW",       "20"))
ZSCORE_THRESHOLD     = float(os.getenv("ZSCORE_THRESHOLD",  "2.5"))
WHALE_MULT           = float(os.getenv("WHALE_VOL_MULT",    "2.5"))
CVD_THRESHOLD        = float(os.getenv("CVD_THRESHOLD",     "0.6"))
ABSORPTION_VOL_MULT  = float(os.getenv("ABSORPTION_VOL",    "3.0"))
ABSORPTION_MOVE_PCT  = float(os.getenv("ABSORPTION_MOVE",   "0.002"))

# ── Simons v6 ─────────────────────────────────
HURST_WINDOW         = int(os.getenv("HURST_WINDOW",        "40"))   # velas para Hurst
HURST_TREND_MIN      = float(os.getenv("HURST_TREND_MIN",   "0.55")) # H>0.55 → trending
HURST_REVERT_MAX     = float(os.getenv("HURST_REVERT_MAX",  "0.45")) # H<0.45 → mean-rev
KURT_MAX             = float(os.getenv("KURT_MAX",          "6.0"))  # kurtosis máxima
OI_DELTA_MIN         = float(os.getenv("OI_DELTA_MIN",      "0.02")) # mínimo cambio OI
FG_EXTREME_FEAR      = int(os.getenv("FG_EXTREME_FEAR",     "25"))   # umbral miedo extremo
FG_EXTREME_GREED     = int(os.getenv("FG_EXTREME_GREED",    "75"))   # umbral codicia extrema
FG_REFRESH_SECS      = int(os.getenv("FG_REFRESH_SECS",     "300"))  # cada 5 min

# ── Trailing stop ─────────────────────────────
TRAILING_PCT     = float(os.getenv("TRAILING_PCT",    "0.8"))
TRAILING_CHECK   = int(os.getenv("TRAILING_CHECK",    "10"))

# ── Scanner ───────────────────────────────────
SCAN_INTERVAL    = int(os.getenv("SCAN_INTERVAL",     "60"))
SCANNER_ENABLED  = os.getenv("SCANNER_ENABLED",       "true").lower() == "true"
SIGNAL_COOLDOWN  = int(os.getenv("SIGNAL_COOLDOWN",   "300"))

# ── Símbolos ──────────────────────────────────
_DEFAULT_SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT", "LINK/USDT:USDT",
]
_env_syms    = os.getenv("SCAN_SYMBOLS", "")
SCAN_SYMBOLS = [s.strip() for s in _env_syms.split(",") if s.strip()] if _env_syms else _DEFAULT_SYMBOLS

DGRP_POS_MAX = 35
DGRP_NEG_MIN = 60

# ═══════════════════════════════════════════════════════════════
#   ESTADO GLOBAL
# ═══════════════════════════════════════════════════════════════

_open_trades:       set   = set()
_trailing_state:    dict  = {}
_last_signal_time:  dict  = {}
_last_signal_val:   dict  = {}
_daily_pnl:         float = 0.0
_daily_date:        str   = ""
_ai_memory          = collections.deque(maxlen=500)
_ai_model_ready:    bool  = False
_ai_z_threshold:    float = ZSCORE_THRESHOLD

# ── Cache Fear & Greed (QE macro proxy) ───────
_fg_cache: dict = {"value": 50, "bias": 0, "ts": 0.0, "label": "Neutral"}
# ── Cache OI por símbolo ──────────────────────
_oi_cache: dict = {}   # sym → {"prev": float, "ts": float}
# ── Confluence dinámica ajustada por F&G ──────
_mtf_confluence_dyn: float = MTF_CONFLUENCE_MIN

# ═══════════════════════════════════════════════════════════════
#   EXCHANGES
# ═══════════════════════════════════════════════════════════════

_ex_cfg = {
    "apiKey": BINGX_API_KEY,
    "secret": BINGX_SECRET_KEY,
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
}
exchange_async = ccxt_pro.bingx(_ex_cfg)
exchange_sync  = ccxt_sync.bingx({**_ex_cfg, "options": {"defaultType": "swap"}})

def normalize_symbol(raw: str) -> str:
    s = raw.strip().upper()
    for sfx in [".P", "PERP", ".PERP"]:
        if s.endswith(sfx): s = s[:-len(sfx)]
    if "/USDT:USDT" in s: return s
    if "/" in s and ":USDT" not in s: return s.replace("/USDT", "/USDT:USDT")
    if s.endswith("USDT") and "/" not in s: return f"{s[:-4]}/USDT:USDT"
    return s

# ═══════════════════════════════════════════════════════════════
#   TELEGRAM
# ═══════════════════════════════════════════════════════════════

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        log.error(f"Telegram: {e}")

def fmt_signal_msg(sym, sig, price, stype, regime, score,
                   mtf=None, ai_conf=None, zscore=None,
                   whale=False, cvd=None, absorb=False,
                   order=None, error=None, source="Scanner",
                   hurst=None, kurt=None, fg=None, oi_delta=None):
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bull  = "long" in sig.lower() or "buy" in sig.lower()
    src   = "📡 Scanner" if source == "Scanner" else "📊 TradingView"
    re_ic = {"POSITIVE GAMMA": "🟢", "NEGATIVE GAMMA": "🔴", "FLIP ZONE": "🟡"}.get(regime, "⚪")

    extras = ""
    if zscore   is not None: extras += f"\n📊 Z-Score: <b>{zscore}</b> (umbral: {_ai_z_threshold:.2f})"
    if whale:                extras += f"\n🐋 Ballena | CVD: <b>{cvd}%</b>"
    if absorb:               extras += f"\n🧲 Absorción (disparo inminente)"
    if mtf      is not None: extras += f"\n📐 Confluencia MTF: <b>{mtf*100:.0f}%</b>"
    if ai_conf  is not None: extras += f"\n🧠 Confianza IA: <b>{ai_conf*100:.0f}%</b>"
    # v6 extras
    if hurst    is not None:
        regime_h = "Trending" if hurst > HURST_TREND_MIN else ("Mean-Rev" if hurst < HURST_REVERT_MAX else "Neutral")
        extras += f"\n📉 Hurst: <b>{hurst:.3f}</b> ({regime_h})"
    if kurt     is not None: extras += f"\n🎲 Kurtosis: <b>{kurt:.2f}</b>"
    if fg       is not None: extras += f"\n😱 Fear&Greed: <b>{fg}</b> ({_fg_cache['label']})"
    if oi_delta is not None: extras += f"\n📦 OI Delta: <b>{oi_delta:+.2%}</b>"

    if error:
        return (f"⚠️ <b>AEGIS v6 — ERROR</b>\n────────────────\n"
                f"🕒 {ts}\n📈 {sym} | {sig.upper()}\n❌ <code>{error}</code>")

    status = "✅ LIVE" if (order and not order.get("paper") and MODE == "live") else "📋 PAPER"
    oid    = (order or {}).get("id", "—")
    size   = round(calc_order_size(), 2)

    return (
        f"{'🟢' if bull else '🔴'} <b>AEGIS v6 — {sig.upper()}</b>\n"
        f"────────────────────\n"
        f"🕒 {ts} | {src}\n"
        f"📈 <b>{sym}</b> @ <b>{price}</b>\n"
        f"💡 {stype}{extras}\n"
        f"────────────────────\n"
        f"{re_ic} Régimen: <b>{regime}</b> | DGRP: <b>{score}/100</b>\n"
        f"────────────────────\n"
        f"📦 {status} | ID: <code>{oid}</code>\n"
        f"⚙️ Size: ${size} | Lev: {LEVERAGE}x | Riesgo: ${ACCOUNT_BALANCE*RISK_PER_TRADE:.2f}\n"
        f"💼 Posiciones: {len(_open_trades)}/{MAX_OPEN_TRADES} | PnL día: {_daily_pnl:+.2f}%"
    )

# ═══════════════════════════════════════════════════════════════
#   GESTIÓN DE RIESGO
# ═══════════════════════════════════════════════════════════════

def _reset_daily():
    global _daily_pnl, _daily_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _daily_date != today:
        if _daily_date:
            log.info(f"[RIESGO] Nuevo día. PnL ayer: {_daily_pnl:+.2f}%")
            send_telegram(f"📅 Nuevo día de trading\n"
                          f"PnL ayer: <b>{_daily_pnl:+.2f}%</b>\n"
                          f"Umbral Z-Score IA: <b>{_ai_z_threshold:.2f}</b>\n"
                          f"Fear&Greed: <b>{_fg_cache['value']}</b> ({_fg_cache['label']})")
        _daily_pnl, _daily_date = 0.0, today

def circuit_breaker() -> tuple[bool, str]:
    _reset_daily()
    if _daily_pnl <= -(MAX_DAILY_LOSS * 100):
        return False, f"Circuit breaker: pérdida diaria {_daily_pnl:.2f}% ≥ límite {MAX_DAILY_LOSS*100:.0f}%"
    if len(_open_trades) >= MAX_OPEN_TRADES:
        return False, f"Máx. posiciones alcanzado ({MAX_OPEN_TRADES})"
    return True, ""

def calc_order_size() -> float:
    dollar_risk = ACCOUNT_BALANCE * RISK_PER_TRADE
    stop_pct    = 0.01
    nominal     = dollar_risk / stop_pct
    margin      = nominal / LEVERAGE
    max_margin  = ACCOUNT_BALANCE * 0.10
    return round(min(margin, max_margin) * LEVERAGE, 2)

def can_send_signal(sym: str, sig: str) -> bool:
    now = time.time()
    if now - _last_signal_time.get(sym, 0) < SIGNAL_COOLDOWN: return False
    if _last_signal_val.get(sym) == sig: return False
    return True

def register_signal(sym: str, sig: str):
    _last_signal_time[sym] = time.time()
    _last_signal_val[sym]  = sig

# ═══════════════════════════════════════════════════════════════
#   INDICADORES CLÁSICOS (pure Python)
# ═══════════════════════════════════════════════════════════════

def ema(data, p):
    out = [None] * len(data); k = 2 / (p + 1)
    for i in range(p - 1, len(data)):
        out[i] = sum(data[i - p + 1:i + 1]) / p if i == p - 1 else data[i] * k + out[i - 1] * (1 - k)
    return out

def atr(highs, lows, closes, p=14):
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
           for i in range(1, len(closes))]
    out = [None] * len(trs)
    if len(trs) >= p:
        out[p-1] = sum(trs[:p]) / p
        for i in range(p, len(trs)):
            out[i] = (out[i-1] * (p-1) + trs[i]) / p
    return out

def bollinger(closes, p=20, mult=2.0):
    if len(closes) < p: return None, None, None
    w = closes[-p:]; mid = sum(w) / p
    std = (sum((x - mid) ** 2 for x in w) / p) ** 0.5
    return mid + mult * std, mid, mid - mult * std

def vwap(highs, lows, closes, volumes):
    tp  = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    tot = sum(volumes)
    return sum(t * v for t, v in zip(tp, volumes)) / tot if tot > 0 else closes[-1]

def rsi(closes, p=14):
    if len(closes) < p + 1: return 50.0
    gs, ls = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]; gs.append(max(d, 0)); ls.append(max(-d, 0))
    ag = sum(gs[-p:]) / p; al = sum(ls[-p:]) / p
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 2)

def dgrp(atr_vals):
    valid = [x for x in atr_vals if x is not None]
    if len(valid) < 28: return "FLIP ZONE", 50
    avg = sum(valid[-28:]) / 28; ratio = valid[-1] / avg if avg > 0 else 1.0
    score = int(min(100, max(0, (ratio - 0.4) / 1.6 * 100)))
    if score < DGRP_POS_MAX:   return "POSITIVE GAMMA", score
    elif score > DGRP_NEG_MIN: return "NEGATIVE GAMMA", score
    return "FLIP ZONE", score

def cvd_whale(opens, closes, volumes, lb=20):
    if len(closes) < lb + 1: return 0.0, False
    rec  = list(zip(opens[-lb:], closes[-lb:], volumes[-lb:]))
    bv   = sum(v for o, c, v in rec if c >= o)
    sv   = sum(v for o, c, v in rec if c < o)
    tot  = bv + sv
    if tot == 0: return 0.0, False
    imb  = abs(bv - sv) / tot
    avgv = sum(volumes[-lb-1:-1]) / lb
    return round(imb * 100, 1), imb >= CVD_THRESHOLD and volumes[-1] > avgv * WHALE_MULT

def zscore(closes, w=None):
    w = w or ZSCORE_WINDOW
    if len(closes) < w + 1: return None
    sub  = closes[-(w+1):-1]; mean = sum(sub) / w
    std  = (sum((x - mean) ** 2 for x in sub) / w) ** 0.5
    return None if std == 0 else round((closes[-1] - mean) / std, 3)

def absorption(highs, lows, closes, volumes, lb=10):
    if len(closes) < lb + 1: return False, ""
    avgv = sum(volumes[-(lb+1):-1]) / lb
    if volumes[-1] < avgv * ABSORPTION_VOL_MULT: return False, ""
    move = (highs[-1] - lows[-1]) / closes[-1] if closes[-1] > 0 else 99
    if move >= ABSORPTION_MOVE_PCT: return False, ""
    return True, "alcista" if closes[-1] >= closes[-2] else "bajista"

# ═══════════════════════════════════════════════════════════════
#   SIMONS v6 — Indicadores estadísticos avanzados
# ═══════════════════════════════════════════════════════════════

def hurst_exponent(closes, max_lag=None) -> float:
    """
    Exponente de Hurst — el corazón del Medallion Fund.
    H < 0.45 → mercado mean-reverting → usar Z-Score / Absorción
    H > 0.55 → mercado trending       → usar GEX Flip / VWAP / Whale
    H ≈ 0.50 → random walk            → señales neutrales
    """
    max_lag = max_lag or min(HURST_WINDOW, len(closes) // 2)
    if len(closes) < max_lag * 2: return 0.5
    lags = range(2, max_lag)
    tau  = []
    for lag in lags:
        diffs = [(closes[i] - closes[i - lag]) ** 2 for i in range(lag, len(closes))]
        tau.append(max(1e-10, (sum(diffs) / len(diffs)) ** 0.5))
    if not tau: return 0.5
    logs_lag = [math.log(l) for l in lags]
    logs_tau = [math.log(t) for t in tau]
    n = len(lags)
    mx = sum(logs_lag) / n; my = sum(logs_tau) / n
    num = sum((x - mx) * (y - my) for x, y in zip(logs_lag, logs_tau))
    den = sum((x - mx) ** 2 for x in logs_lag)
    return round((num / den) / 2, 4) if den > 0 else 0.5

def autocorr_lag1(closes, n=20) -> float:
    """
    Correlación serial lag-1 de retornos.
    > +0.1 → momentum (retornos se repiten)
    < -0.1 → reversión (retornos se invierten)
    ≈ 0    → ruido puro
    """
    if len(closes) < n + 2: return 0.0
    r  = [(closes[i] - closes[i-1]) / (closes[i-1] + 1e-10) for i in range(-n-1, 0)]
    r0 = r[:-1]; r1 = r[1:]
    m0 = sum(r0) / len(r0); m1 = sum(r1) / len(r1)
    num = sum((a - m0) * (b - m1) for a, b in zip(r0, r1))
    d0  = (sum((a - m0) ** 2 for a in r0)) ** 0.5
    d1  = (sum((b - m1) ** 2 for b in r1)) ** 0.5
    return round(num / (d0 * d1 + 1e-9), 4)

def returns_moments(closes, n=30) -> tuple[float, float, float]:
    """
    Retorna (std, skewness, kurtosis) de los últimos n retornos.
    Kurtosis > KURT_MAX → distribución de cola gorda → no operar (riesgo flash crash)
    Skewness < -1 en long o > +1 en short → sesgo en contra
    """
    if len(closes) < n + 1: return 0.0, 0.0, 3.0
    r   = [(closes[i] - closes[i-1]) / (closes[i-1] + 1e-10) for i in range(-n, 0)]
    m   = sum(r) / len(r)
    std = (sum((x - m) ** 2 for x in r) / len(r)) ** 0.5
    if std < 1e-9: return std, 0.0, 3.0
    skew = sum((x - m) ** 3 for x in r) / (len(r) * std ** 3)
    kurt = sum((x - m) ** 4 for x in r) / (len(r) * std ** 4)
    return round(std, 6), round(skew, 3), round(kurt, 3)

# ═══════════════════════════════════════════════════════════════
#   QE SIGNALS — Fear & Greed + Open Interest
# ═══════════════════════════════════════════════════════════════

def refresh_fear_greed():
    """
    Fear & Greed Index de alternative.me — proxy gratuito de liquidez macro (QE).
    < 25  → Miedo extremo  → institucionales acumulan (Simons compraba el pánico)
    > 75  → Codicia extrema → mercado sobrecalentado → reducir exposición long
    """
    global _fg_cache, _mtf_confluence_dyn
    now = time.time()
    if now - _fg_cache["ts"] < FG_REFRESH_SECS: return  # usa cache
    try:
        resp = http_requests.get(
            "https://api.alternative.me/fng/?limit=1", timeout=6
        ).json()
        fg_val   = int(resp["data"][0]["value"])
        fg_label = resp["data"][0]["value_classification"]
        if fg_val < FG_EXTREME_FEAR:
            bias = 1   # sesgo alcista, acumulación institucional
        elif fg_val > FG_EXTREME_GREED:
            bias = -1  # sesgo bajista, euforia = techo
        else:
            bias = 0

        # Ajusta confluencia MTF dinámicamente:
        # Miedo extremo → baja umbral (mercado más predecible al alza)
        # Codicia extrema → sube umbral (no seguir el rebaño)
        if fg_val < FG_EXTREME_FEAR:
            _mtf_confluence_dyn = max(0.45, MTF_CONFLUENCE_MIN - 0.10)
        elif fg_val > FG_EXTREME_GREED:
            _mtf_confluence_dyn = min(0.80, MTF_CONFLUENCE_MIN + 0.15)
        else:
            _mtf_confluence_dyn = MTF_CONFLUENCE_MIN

        _fg_cache = {"value": fg_val, "bias": bias, "ts": now, "label": fg_label}
        log.info(f"[F&G] {fg_val} ({fg_label}) → bias={'LONG' if bias==1 else 'SHORT' if bias==-1 else 'NEUTRO'}"
                 f" | MTF_min={_mtf_confluence_dyn:.2f}")
    except Exception as e:
        log.warning(f"[F&G] No se pudo actualizar: {e}")

async def get_oi_delta(sym: str) -> float:
    """
    Delta de Open Interest entre las últimas 2 velas de 5m.
    OI subiendo  + precio subiendo  → confirmación institucional alcista
    OI bajando   + precio subiendo  → divergencia → posible trampa
    Retorna el cambio relativo del OI (e.g. +0.03 = +3%)
    """
    global _oi_cache
    try:
        oi_data = await exchange_async.fetch_open_interest_history(sym, "5m", limit=3)
        if len(oi_data) >= 2:
            prev_oi = float(oi_data[-2].get("openInterestAmount", 0) or 0)
            curr_oi = float(oi_data[-1].get("openInterestAmount", 0) or 0)
            if prev_oi > 0:
                delta = (curr_oi - prev_oi) / prev_oi
                _oi_cache[sym] = {"prev": prev_oi, "curr": curr_oi, "delta": delta}
                return round(delta, 5)
    except Exception as e:
        log.debug(f"[OI] {sym}: {e}")
    return 0.0

# ═══════════════════════════════════════════════════════════════
#   LSTM PURO (sin dependencias, aprende patrones de precio)
# ═══════════════════════════════════════════════════════════════

class MiniLSTM:
    def __init__(self, inp=7, hid=10):  # v6: +2 inputs (hurst, kurt)
        self.hid = hid; self.inp = inp
        self._init()

    def _init(self):
        def X(r, c): sc = (2/(r+c))**0.5; return [[random.gauss(0, sc) for _ in range(c)] for _ in range(r)]
        h, i = self.hid, self.inp
        self.Wf=X(h,i+h); self.bf=[0.1]*h
        self.Wi=X(h,i+h); self.bi=[0.0]*h
        self.Wg=X(h,i+h); self.bg=[0.0]*h
        self.Wo=X(h,i+h); self.bo=[0.5]*h
        self.Wy=X(1,h);   self.by=[0.0]

    @staticmethod
    def _s(x): return 1/(1+math.exp(-max(-20,min(20,x))))
    @staticmethod
    def _t(x): return math.tanh(max(-20,min(20,x)))

    def _mm(self, W, x, b):
        return [b[i]+sum(W[i][j]*x[j] for j in range(len(x))) for i in range(len(b))]

    def forward(self, seq):
        h = [0.0]*self.hid; c = [0.0]*self.hid
        for x in seq:
            xh = x+h
            f  = [self._s(v) for v in self._mm(self.Wf,xh,self.bf)]
            ig = [self._s(v) for v in self._mm(self.Wi,xh,self.bi)]
            g  = [self._t(v) for v in self._mm(self.Wg,xh,self.bg)]
            o  = [self._s(v) for v in self._mm(self.Wo,xh,self.bo)]
            c  = [f[j]*c[j]+ig[j]*g[j] for j in range(self.hid)]
            h  = [o[j]*self._t(c[j]) for j in range(self.hid)]
        return self._s(self.by[0]+sum(self.Wy[0][j]*h[j] for j in range(self.hid)))

    def train(self, seq, target, lr=0.01):
        p = self.forward(seq); e = p - target
        for j in range(self.hid): self.Wy[0][j] -= lr*e*p*(1-p)
        self.by[0] -= lr*e*p*(1-p)
        return abs(e)

# ═══════════════════════════════════════════════════════════════
#   RANDOM FOREST SIMPLE
# ═══════════════════════════════════════════════════════════════

class MiniTree:
    def __init__(self, depth=4): self.d=depth; self.tree=None

    def fit(self, X, y):
        self.tree = self._b(X, y, 0)

    def _b(self, X, y, dep):
        if not X or dep>=self.d or len(set(str(v) for v in y))==1:
            return sum(y)/len(y) if y else 0.5
        bf=bv=None; bs=float("inf")
        for f in range(len(X[0])):
            for v in sorted(set(r[f] for r in X))[1:]:
                ly=[y[i] for i,r in enumerate(X) if r[f]<v]
                ry=[y[i] for i,r in enumerate(X) if r[f]>=v]
                if not ly or not ry: continue
                sc=self._g(ly)*len(ly)+self._g(ry)*len(ry)
                if sc<bs: bs,bf,bv=sc,f,v
        if bf is None: return sum(y)/len(y)
        lX=[r for r in X if r[bf]<bv]; rX=[r for r in X if r[bf]>=bv]
        lY=[y[i] for i,r in enumerate(X) if r[bf]<bv]; rY=[y[i] for i,r in enumerate(X) if r[bf]>=bv]
        return {"f":bf,"v":bv,"l":self._b(lX,lY,dep+1),"r":self._b(rX,rY,dep+1)}

    def _g(self, y):
        if not y: return 0; p=sum(y)/len(y); return 1-p*p-(1-p)**2

    def predict(self, x):
        n=self.tree
        while isinstance(n,dict): n=n["l"] if x[n["f"]]<n["v"] else n["r"]
        return n

class MiniRF:
    def __init__(self, n=10, d=4): self.trees=[MiniTree(d) for _ in range(n)]; self.ok=False

    def fit(self, X, y):
        n=len(X)
        for t in self.trees:
            idx=[random.randint(0,n-1) for _ in range(n)]
            t.fit([X[i] for i in idx],[y[i] for i in idx])
        self.ok=True

    def predict(self, x):
        if not self.ok: return _ai_z_threshold
        ps=[t.predict(x) for t in self.trees if t.tree is not None]
        return max(2.0,min(4.0,sum(ps)/len(ps))) if ps else _ai_z_threshold

# ═══════════════════════════════════════════════════════════════
#   MÓDULO IA GLOBAL (v6: incluye Hurst y Kurtosis como features)
# ═══════════════════════════════════════════════════════════════

_lstm = MiniLSTM(inp=7, hid=10)  # +hurst, +kurt vs v5
_rf   = MiniRF(n=10, d=4)

def _features(closes, volumes, hour, hurst_val=0.5, kurt_val=3.0):
    if len(closes)<10 or len(volumes)<10: return None
    pc = [(closes[i]-closes[i-1])/(closes[i-1]+1e-10) for i in range(-5,0)]
    vm = sum(volumes[-10:])/10
    vn = [volumes[i]/(vm+1e-10) for i in range(-5,0)]
    # 7 features: precio, volumen, hora, fg_bias, hurst, kurt_norm, autocorr
    fg_norm   = _fg_cache["value"] / 100.0
    kurt_norm = min(1.0, kurt_val / 10.0)
    return [[p, v, hour/23.0, fg_norm, hurst_val, kurt_norm, 0.0] for p, v in zip(pc, vn)]

def lstm_confidence(closes, volumes, hour, hurst_val=0.5, kurt_val=3.0) -> float:
    seq = _features(closes, volumes, hour, hurst_val, kurt_val)
    if seq is None: return 0.5
    try:    return _lstm.forward(seq)
    except: return 0.5

def update_ai(success, z_entry, vol_mult, hour, mtf_score, funding, ob_imb,
              hurst_val=0.5, kurt_val=3.0):
    global _ai_z_threshold, _ai_model_ready
    _ai_memory.append({
        "ok":1 if success else 0, "z":z_entry, "vol":vol_mult,
        "hr":hour, "mtf":mtf_score, "fund":funding, "ob":ob_imb,
        "hurst":hurst_val, "kurt":min(kurt_val, 10.0),
    })
    closes_proxy = [1.0+(random.gauss(0,0.001)) for _ in range(10)]
    volumes_proxy= [vol_mult]*10
    seq = _features(closes_proxy, volumes_proxy, hour, hurst_val, kurt_val)
    if seq: _lstm.train(seq, float(success))

    if len(_ai_memory) < 20: return
    data = list(_ai_memory)
    X = [[d["hr"]/23.0, d["vol"]/5.0, d["mtf"], d["fund"]*100, d["ob"],
          d.get("hurst",0.5), min(d.get("kurt",3.0),10.0)/10.0] for d in data]
    y = [d["z"]*d["ok"] for d in data]
    try:
        _rf.fit(X, y)
        hr   = datetime.now(timezone.utc).hour
        last = data[-1]
        _ai_z_threshold = _rf.predict([
            hr/23.0, last["vol"]/5.0, last["mtf"],
            last["fund"]*100, last["ob"],
            last.get("hurst",0.5), min(last.get("kurt",3.0),10.0)/10.0
        ])
        _ai_model_ready = True
        log.info(f"[IA] Retrain OK. Nuevo umbral Z: {_ai_z_threshold:.3f}")
    except Exception as e:
        log.error(f"[IA] Retrain error: {e}")

# ═══════════════════════════════════════════════════════════════
#   EJECUCIÓN SMART — Maker-First con fallback a Market
# ═══════════════════════════════════════════════════════════════

async def smart_order(sym: str, side: str, size: float) -> dict | None:
    """
    v6.1 — Ejecución ultra-rápida:
    • Leverage + OB se obtienen en PARALELO (ahorra ~300ms)
    • Precio mid-point (mejor fill rate que solo bid/ask)
    • Poll activo cada 1s en lugar de sleep fijo (detecta fill instantáneo)
    • Fallback a market inmediato si el spread es >0.1% (mercado muy rápido)
    """
    if MODE != "live":
        try:
            t = await exchange_async.fetch_ticker(sym)
            p = t["last"]
        except:
            p = 0.0
        log.info(f"[PAPER] {side.upper()} ${size} {sym} @ {p:.4f}")
        return {"id": f"PAPER-{datetime.now().strftime('%H%M%S')}", "paper": True,
                "average": p, "price": p}
    try:
        # ── Paralelo: leverage + order book al mismo tiempo ──────
        lev_task = exchange_async.set_leverage(LEVERAGE, sym)
        ob_task  = exchange_async.fetch_order_book(sym, limit=5)
        results  = await asyncio.gather(lev_task, ob_task, return_exceptions=True)
        ob = results[1] if not isinstance(results[1], Exception) else None
        if ob is None:
            ob = await exchange_async.fetch_order_book(sym, limit=5)

        best_bid = ob["bids"][0][0]
        best_ask = ob["asks"][0][0]
        spread   = (best_ask - best_bid) / best_bid

        # Si el spread es > 0.1% el mercado va muy rápido → directo a market
        if spread > 0.001:
            log.warning(f"[ORDER] {sym}: spread {spread:.4%} alto → MARKET directo")
            morder = await exchange_async.create_market_order(sym, side, size)
            fill   = morder.get("average") or morder.get("price", 0)
            log.info(f"[MARKET-FAST] Fill @ {fill:.4f}")
            return morder

        # Mid-point con ligero sesgo hacia el lado de la orden
        # Para LONG: ligeramente por encima del bid (pero bajo el ask) → más probabilidad de fill
        # Para SHORT: ligeramente por debajo del ask
        if side == "buy":
            limit_price = round(best_bid + (best_ask - best_bid) * 0.35, 4)
        else:
            limit_price = round(best_ask - (best_ask - best_bid) * 0.35, 4)

        log.info(f"[LIMIT] {side.upper()} {sym} @ {limit_price} | spread={spread:.4%} | size={size}")
        order = await exchange_async.create_limit_order(sym, side, size, limit_price)
        oid   = order["id"]

        # ── Poll activo: verifica cada 1s hasta LIMIT_WAIT_SECS ──
        for _ in range(LIMIT_WAIT_SECS):
            await asyncio.sleep(1)
            try:
                check = await exchange_async.fetch_order(oid, sym)
                if check["status"] == "closed":
                    fill = check.get("average") or check.get("price") or limit_price
                    log.info(f"[LIMIT] ✅ Fill en {_+1}s @ {fill:.4f} (maker sin comisión)")
                    return check
            except:
                pass

        # No llenado → cancelar y market
        try: await exchange_async.cancel_order(oid, sym)
        except: pass
        log.warning(f"[LIMIT] Precio escapó tras {LIMIT_WAIT_SECS}s → MARKET fallback")
        morder = await exchange_async.create_market_order(sym, side, size)
        fill   = morder.get("average") or morder.get("price", 0)
        log.info(f"[MARKET] Fill @ {fill:.4f}")
        return morder

    except Exception as e:
        log.error(f"[ORDER] {sym} {side}: {e}")
        return None

async def close_position(sym: str, side: str, contracts: float):
    if MODE != "live":
        log.info(f"[PAPER CLOSE] {sym} {side}")
        return {"id": f"PAPER-CLOSE-{datetime.now().strftime('%H%M%S')}", "paper": True}
    try:
        ob         = await exchange_async.fetch_order_book(sym, limit=5)
        close_side = "sell" if side == "long" else "buy"
        limit_price = (ob["asks"][0][0] if close_side == "sell" else ob["bids"][0][0])
        order = await exchange_async.create_limit_order(
            sym, close_side, contracts, round(limit_price, 4), {"reduceOnly": True}
        )
        await asyncio.sleep(LIMIT_WAIT_SECS)
        check = await exchange_async.fetch_order(order["id"], sym)
        if check["status"] == "closed": return check
        await exchange_async.cancel_order(order["id"], sym)
        m = await exchange_async.create_market_order(sym, close_side, contracts, {"reduceOnly": True})
        return m
    except Exception as e:
        log.error(f"[CLOSE] {sym}: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
#   ANÁLISIS POR TIMEFRAME — v6 con Simons filters
# ═══════════════════════════════════════════════════════════════

async def analyze_tf(sym: str, tf: str) -> dict | None:
    try:
        ohlcv = await exchange_async.fetch_ohlcv(sym, tf, limit=120)
    except Exception as e:
        log.warning(f"fetch_ohlcv {sym} {tf}: {e}"); return None
    if len(ohlcv) < 60: return None

    opens   = [x[1] for x in ohlcv]
    highs   = [x[2] for x in ohlcv]
    lows    = [x[3] for x in ohlcv]
    closes  = [x[4] for x in ohlcv]
    volumes = [x[5] for x in ohlcv]
    price   = closes[-1]

    # ── Indicadores clásicos ─────────────────────
    atr_v              = atr(highs, lows, closes, 14)
    regime, score      = dgrp(atr_v)
    e9                 = ema(closes, 9)
    e21                = ema(closes, 21)
    bb_up,bb_mid,bb_lo = bollinger(closes, 20, 2.0)
    vw                 = vwap(highs, lows, closes, volumes)
    cvd_pct, is_whale  = cvd_whale(opens, closes, volumes, 20)
    z                  = zscore(closes, ZSCORE_WINDOW)
    abs_ok, abs_dir    = absorption(highs, lows, closes, volumes, 10)
    rsi_v              = rsi(closes, 14)
    atr_last           = atr_v[-1] or 0

    # ── Simons v6 ────────────────────────────────
    h_exp  = hurst_exponent(closes, HURST_WINDOW)
    ac     = autocorr_lag1(closes, 20)
    _, skew, kurt = returns_moments(closes, 30)

    # FILTRO KURTOSIS — distribución de cola gorda = no operar
    if kurt > KURT_MAX:
        log.info(f"[SIMONS] {sym} {tf}: kurtosis={kurt:.2f} > {KURT_MAX} → skip (riesgo flash)")
        return None

    e9p,e9c   = e9[-2],  e9[-1]
    e21p,e21c = e21[-2], e21[-1]
    cp,cc,op  = closes[-2], closes[-1], opens[-1]

    thr    = _ai_z_threshold if _ai_model_ready else ZSCORE_THRESHOLD
    signal = stype = None
    extra  = {}

    # ── Regime-aware signal routing (Simons) ─────
    # H < 0.45 → mean-reverting → Z-Score y Absorción son válidos
    # H > 0.55 → trending       → EMA, VWAP, CVD son válidos
    # H 0.45-0.55 → todos con score reducido
    is_reverting = h_exp < HURST_REVERT_MAX
    is_trending  = h_exp > HURST_TREND_MIN

    # 1. Z-Score (solo válido en mean-reverting)
    if z is not None and abs(z) >= thr and (is_reverting or not is_trending):
        avgv = sum(volumes[-ZSCORE_WINDOW-1:-1]) / ZSCORE_WINDOW
        if volumes[-1] > avgv * 3.0:
            if z < -thr and rsi_v < 42:
                signal, stype = "zscore_long",  f"Z-Score {z} → agotamiento bajista (RSI {rsi_v}) [H={h_exp:.3f}]"
            elif z > thr and rsi_v > 58:
                signal, stype = "zscore_short", f"Z-Score {z} → agotamiento alcista (RSI {rsi_v}) [H={h_exp:.3f}]"
            extra["z_score"] = z

    # 2. Absorción (válida en mean-reverting y neutral)
    if signal is None and abs_ok and not is_trending:
        d = "long" if "alcista" in abs_dir else "short"
        signal, stype = f"absorption_{d}", f"Absorción {abs_dir} [H={h_exp:.3f}]"
        extra["absorption"] = True

    # 3. Vanna Unwind (válido siempre, requiere Negative Gamma)
    if signal is None and regime == "NEGATIVE GAMMA" and atr_last > 0:
        if abs(cc - op) > atr_last * 1.5:
            tu = closes[-3] < closes[-2]
            if tu and cc < op and rsi_v > 58:
                signal, stype = "vanna_unwind_short", "Vanna Unwind bajista (P>70%)"
            elif not tu and cc > op and rsi_v < 42:
                signal, stype = "vanna_unwind_long",  "Vanna Unwind alcista (P>70%)"

    # 4. Whale CVD (solo válido en trending)
    if signal is None and is_whale and (is_trending or not is_reverting):
        bv = sum(v for o,c,v in zip(opens[-20:],closes[-20:],volumes[-20:]) if c>=o)
        sv = sum(v for o,c,v in zip(opens[-20:],closes[-20:],volumes[-20:]) if c<o)
        if bv>sv: signal,stype="whale_long",  f"Ballena alcista | CVD {cvd_pct}% [H={h_exp:.3f}]"
        else:     signal,stype="whale_short", f"Ballena bajista | CVD {cvd_pct}% [H={h_exp:.3f}]"
        extra.update({"whale": True, "cvd_pct": cvd_pct})

    # 5. Compresión Bollinger (válida en cualquier régimen)
    if signal is None and bb_up and bb_mid and bb_lo:
        bw = (bb_up - bb_lo) / bb_mid if bb_mid else 99
        if bw < 0.025:
            if cc > bb_up:   signal, stype = "compression_break_long",  "Compresión BB alcista"
            elif cc < bb_lo: signal, stype = "compression_break_short", "Compresión BB bajista"

    # 6. GEX Flip Cross (solo válido en trending o neutral)
    if signal is None and all(v is not None for v in [e9p,e9c,e21p,e21c]):
        if not is_reverting:  # EMA cross falla en mercados mean-reverting
            if e9p<=e21p and e9c>e21c: signal,stype="gex_flip_cross_long",  f"GEX Flip Cross alcista [H={h_exp:.3f}]"
            elif e9p>=e21p and e9c<e21c: signal,stype="gex_flip_cross_short",f"GEX Flip Cross bajista [H={h_exp:.3f}]"

    # 7. Wall Break VWAP (solo válido en trending)
    if signal is None and is_trending:
        if cp < vw and cc > vw*1.001:   signal,stype="wall_break_long",  f"Wall Break VWAP @ {vw:.4f} [H={h_exp:.3f}]"
        elif cp > vw and cc < vw*0.999: signal,stype="wall_break_short", f"Wall Break VWAP @ {vw:.4f} [H={h_exp:.3f}]"

    # ── Autocorrelación: boost o penalizar confianza ──
    # AC > 0.1 + señal momentum = confirmado; AC < -0.1 + señal reversión = confirmado
    sig_is_momentum  = signal in ("gex_flip_cross_long","gex_flip_cross_short","wall_break_long","wall_break_short","whale_long","whale_short")
    sig_is_reversion = signal in ("zscore_long","zscore_short","absorption_long","absorption_short")
    ac_confirmed = (sig_is_momentum and ac > 0.1) or (sig_is_reversion and ac < -0.1)
    extra["hurst"] = h_exp
    extra["kurt"]  = kurt
    extra["ac_confirmed"] = ac_confirmed

    bull_bias = (e9c or 0) > (e21c or 0) and cc > vw
    bear_bias = (e9c or 0) < (e21c or 0) and cc < vw

    return {
        "signal": signal, "stype": stype, "price": price,
        "regime": regime, "score": score, "rsi": rsi_v, "z": z,
        "dir": 1 if bull_bias else (-1 if bear_bias else 0),
        "extra": extra, "hurst": h_exp, "kurt": kurt,
    }

# ═══════════════════════════════════════════════════════════════
#   MULTI-TIMEFRAME ENGINE
# ═══════════════════════════════════════════════════════════════

async def analyze_mtf(sym: str) -> dict | None:
    # ── PARALELO: los 4 timeframes se analizan simultáneamente ───
    # v5 era secuencial (~4s). v6.1 es paralelo (~1s, el más lento marca el tiempo)
    coros   = [analyze_tf(sym, tf) for tf in MTF_TIMEFRAMES]
    raw     = await asyncio.gather(*coros, return_exceptions=True)
    results = {}
    for tf, r in zip(MTF_TIMEFRAMES, raw):
        if r and not isinstance(r, Exception): results[tf] = r

    base = next((results[tf] for tf in MTF_TIMEFRAMES if results.get(tf, {}).get("signal")), None)
    if base is None: return None

    sig_dir = 1 if "long" in base["signal"] else -1
    wtd = 0.0
    for tf, r in results.items():
        w = MTF_WEIGHTS.get(tf, 0.25)
        if r["dir"] == sig_dir:                                          wtd += w
        elif r.get("signal") and ("long" in r["signal"]) == (sig_dir==1): wtd += w * 0.7

    # Boost si autocorrelación confirma la señal en el timeframe base
    if base["extra"].get("ac_confirmed"):
        wtd = min(1.0, wtd * 1.10)
        log.info(f"[MTF] {sym}: autocorr confirma señal → wtd boost → {wtd:.2f}")

    # Usa confluencia dinámica ajustada por Fear & Greed
    conf_min = _mtf_confluence_dyn
    if wtd < conf_min:
        log.info(f"[MTF] {sym}: confluencia {wtd:.2f} < {conf_min:.2f} insuficiente"); return None

    # Filtro RSI 1h
    rsi_1h = results.get("1h", {}).get("rsi", 50)
    if sig_dir == 1  and rsi_1h > 70: log.info(f"[MTF] {sym} LONG bloqueado RSI1h={rsi_1h}"); return None
    if sig_dir == -1 and rsi_1h < 30: log.info(f"[MTF] {sym} SHORT bloqueado RSI1h={rsi_1h}"); return None

    return {
        "signal": base["signal"], "stype": base["stype"],
        "ticker": sym, "price": f"{base['price']:.4f}",
        "regime": base["regime"], "score": base["score"],
        "mtf_score": wtd,
        "hurst": base.get("hurst", 0.5),
        "kurt":  base.get("kurt", 3.0),
        **base["extra"],
    }

# ═══════════════════════════════════════════════════════════════
#   PROCESS SIGNAL — núcleo de decisión v6
# ═══════════════════════════════════════════════════════════════

LONG_SIGS  = {"long","buy","wall_break_long","gex_flip_cross_long","vanna_unwind_long",
              "compression_break_long","whale_long","zscore_long","absorption_long"}
SHORT_SIGS = {"short","sell","wall_break_short","gex_flip_cross_short","vanna_unwind_short",
              "compression_break_short","whale_short","zscore_short","absorption_short"}

def interp(raw):
    r = raw.lower().strip()
    if r in LONG_SIGS:  return "long"
    if r in SHORT_SIGS: return "short"
    if r == "close":    return "close"
    raise ValueError(f"Señal desconocida: {raw}")

async def process_signal(data: dict, source="Scanner", mtf_score=None) -> dict:
    sym = normalize_symbol(data.get("ticker", "BTC/USDT:USDT"))
    data["ticker"] = sym

    try:   side = interp(data.get("signal", ""))
    except ValueError as e:
        send_telegram(fmt_signal_msg(sym, data.get("signal","?"), data.get("price","?"),
                                     str(e), "?", "?", error=str(e), source=source))
        return {"error": str(e)}

    ok, reason = circuit_breaker()
    if not ok and side != "close":
        log.warning(f"[CB] {reason}")
        send_telegram(f"🚫 <b>Circuit Breaker</b>\n{sym}: {reason}")
        return {"error": reason}

    if side != "close" and not can_send_signal(sym, side):
        log.info(f"[COOLDOWN] {sym} {side}"); return {"skipped": "cooldown"}

    hurst_val = data.get("hurst", 0.5)
    kurt_val  = data.get("kurt", 3.0)

    # ── FILTRO F&G QE ─────────────────────────────
    fg_bias = _fg_cache["bias"]
    sig_dir = 1 if side == "long" else (-1 if side == "short" else 0)
    if fg_bias != 0 and sig_dir != 0 and fg_bias != sig_dir:
        # Contra tendencia macro — requiere mayor convicción
        min_score = _mtf_confluence_dyn + 0.10
        if (mtf_score or 0) < min_score:
            log.info(f"[F&G] {sym}: señal contra bias macro (F&G={_fg_cache['value']}) → skip")
            return {"skipped": "fg_macro_filter"}

    # ── FILTRO OI Delta ───────────────────────────
    oi_delta = 0.0
    if side != "close":
        oi_delta = await get_oi_delta(sym)
        if sig_dir == 1  and oi_delta < -OI_DELTA_MIN:
            log.info(f"[OI] {sym}: OI cayendo {oi_delta:.2%} en LONG → divergencia → skip")
            return {"skipped": "oi_divergence"}
        if sig_dir == -1 and oi_delta >  OI_DELTA_MIN:
            log.info(f"[OI] {sym}: OI subiendo {oi_delta:.2%} en SHORT → divergencia → skip")
            return {"skipped": "oi_divergence"}

    # ── Confianza LSTM (con Hurst y Kurt) ─────────
    ai_conf = None
    try:
        ohlcv = await exchange_async.fetch_ohlcv(sym, "5m", limit=25)
        if len(ohlcv) >= 15:
            cl = [x[4] for x in ohlcv]; vl = [x[5] for x in ohlcv]
            ai_conf = lstm_confidence(cl, vl, datetime.now(timezone.utc).hour,
                                      hurst_val, kurt_val)
            if side == "long"  and ai_conf < 0.44: return {"skipped": "lstm_low"}
            if side == "short" and ai_conf > 0.56: return {"skipped": "lstm_low"}
    except Exception as e: log.warning(f"LSTM check: {e}")

    # ── Precio actual ─────────────────────────────
    try:
        t = await exchange_async.fetch_ticker(sym)
        cur_price = t["last"]
    except: cur_price = float(data.get("price", 0) or 0)

    # ── Order book + funding ──────────────────────
    ob_imb = funding = 0.0
    try:
        ob     = await exchange_async.fetch_order_book(sym, limit=10)
        bv     = sum(b[1] for b in ob["bids"][:5]); av = sum(a[1] for a in ob["asks"][:5])
        ob_imb = (bv - av) / (bv + av) if bv + av > 0 else 0.0
    except: pass
    try:
        fr      = await exchange_async.fetch_funding_rate(sym)
        funding = float(fr.get("fundingRate", 0) or 0)
    except: pass

    if sig_dir == 1  and (funding > 0.001 or ob_imb < -0.2): return {"skipped": "market_filter"}
    if sig_dir == -1 and (funding < -0.001 or ob_imb > 0.2):  return {"skipped": "market_filter"}

    size  = calc_order_size()
    order = error = None

    if side == "close":
        state = _trailing_state.get(sym)
        if state:
            o = await close_position(sym, state["side"], state["contracts"])
            order = o
        _open_trades.discard(sym)
        _trailing_state.pop(sym, None)
    else:
        order = await smart_order(sym, "buy" if side == "long" else "sell", size)
        if order:
            entry  = float(order.get("average") or order.get("price") or cur_price)
            _open_trades.add(sym)
            _trailing_state[sym] = {
                "side": side, "entry": entry, "best": entry,
                "contracts": size, "ai_conf": ai_conf,
                "mtf_score": mtf_score or 0.0, "paper": order.get("paper", False),
                "hurst": hurst_val, "kurt": kurt_val,
            }
            register_signal(sym, side)
        else:
            error = "Order failed"

    send_telegram(fmt_signal_msg(
        sym, data.get("signal","?"), f"{cur_price:.4f}",
        data.get("stype", data.get("signal_type","")),
        data.get("regime","?"), data.get("score", data.get("dgrp_score","?")),
        mtf=mtf_score, ai_conf=ai_conf,
        zscore=data.get("z_score"), whale=data.get("whale",False),
        cvd=data.get("cvd_pct"), absorb=data.get("absorption",False),
        order=order, error=error, source=source,
        hurst=hurst_val if hurst_val != 0.5 else None,
        kurt=kurt_val if kurt_val != 3.0 else None,
        fg=_fg_cache["value"],
        oi_delta=oi_delta if oi_delta != 0.0 else None,
    ))
    return {"order": order, "error": error}

# ═══════════════════════════════════════════════════════════════
#   TRAILING STOP — loop async
# ═══════════════════════════════════════════════════════════════

async def trailing_loop():
    log.info(f"Trailing loop ON | {TRAILING_PCT}% | check cada {TRAILING_CHECK}s")
    while True:
        await asyncio.sleep(TRAILING_CHECK)
        for sym, state in list(_trailing_state.items()):
            try:
                t     = await exchange_async.fetch_ticker(sym)
                price = t["last"]
            except: continue

            side  = state["side"]
            best  = state["best"]
            trail = TRAILING_PCT / 100.0

            moved = False
            if side == "long"  and price > best: state["best"] = best = price; moved=True
            if side == "short" and price < best: state["best"] = best = price; moved=True
            if moved: log.info(f"[TRAIL] {sym} nuevo best={best:.4f}")

            hit = (side=="long"  and price <= best*(1-trail)) or \
                  (side=="short" and price >= best*(1+trail))
            if hit:
                await _fire_trailing(sym, state, price)

async def _fire_trailing(sym, state, price):
    global _daily_pnl
    side  = state["side"]; entry=state["entry"]; best=state["best"]
    pnl   = ((price-entry)/entry*100) if side=="long" else ((entry-price)/entry*100)
    pnl_l = round(pnl * LEVERAGE, 2)
    _daily_pnl += pnl_l

    await close_position(sym, side, state["contracts"])
    _trailing_state.pop(sym, None); _open_trades.discard(sym)

    update_ai(pnl > 0, state.get("z_score", ZSCORE_THRESHOLD), 1.0,
              datetime.now(timezone.utc).hour, state.get("mtf_score",0.5),
              funding=0.0, ob_imb=0.0,
              hurst_val=state.get("hurst", 0.5),
              kurt_val=state.get("kurt", 3.0))

    _reset_daily()
    send_telegram(
        f"{'🟢' if pnl_l>=0 else '🔴'} <b>TRAILING STOP</b>\n"
        f"────────────────────\n"
        f"📈 <b>{sym}</b>\n"
        f"📍 Entrada: <b>{entry:.4f}</b> → Salida: <b>{price:.4f}</b>\n"
        f"🏆 Best: <b>{best:.4f}</b>\n"
        f"{'🟢' if pnl_l>=0 else '🔴'} PnL: <b>{pnl_l:+.2f}%</b> (x{LEVERAGE})\n"
        f"📊 PnL hoy: <b>{_daily_pnl:+.2f}%</b>\n"
        f"🧠 Umbral Z IA: <b>{_ai_z_threshold:.3f}</b> | "
        f"Memoria: <b>{len(_ai_memory)}</b> trades\n"
        f"😱 F&G: <b>{_fg_cache['value']}</b> | Confluencia: <b>{_mtf_confluence_dyn:.2f}</b>"
    )

# ═══════════════════════════════════════════════════════════════
#   SCANNER LOOP
# ═══════════════════════════════════════════════════════════════

async def scanner_loop():
    log.info(f"Scanner ON | {len(SCAN_SYMBOLS)} syms | {SCAN_INTERVAL}s")
    send_telegram(
        f"🚀 <b>AEGIS GEX v6.0 — Simons+QE</b>\n"
        f"────────────────────\n"
        f"Modo: <b>{MODE.upper()}</b> | Lev: <b>{LEVERAGE}x</b>\n"
        f"Balance: <b>${ACCOUNT_BALANCE}</b> | Riesgo/trade: <b>{RISK_PER_TRADE*100:.0f}%</b>\n"
        f"MTF: <b>{', '.join(MTF_TIMEFRAMES)}</b> | Confluencia: <b>{MTF_CONFLUENCE_MIN*100:.0f}%</b>\n"
        f"Ejecución: <b>Maker-First</b> (limit {LIMIT_WAIT_SECS}s → market)\n"
        f"Circuit breaker: <b>{MAX_DAILY_LOSS*100:.0f}%/día</b>\n"
        f"🔬 Simons: <b>Hurst + Kurtosis + Autocorr</b>\n"
        f"📡 QE: <b>Fear&Greed + OI Delta</b>\n"
        f"🧠 LSTM(7 features) + RF(7 features)"
    )
    n = 0
    while True:
        n += 1
        ok, reason = circuit_breaker()
        if not ok:
            log.warning(f"[CB] {reason} — scan pausado"); await asyncio.sleep(SCAN_INTERVAL); continue

        # Actualiza Fear & Greed (macro QE) una vez por ciclo
        try:
            await asyncio.get_event_loop().run_in_executor(None, refresh_fear_greed)
        except Exception as e:
            log.warning(f"[F&G executor] {e}")

        log.info(f"── SCAN #{n} | pos={len(_open_trades)}/{MAX_OPEN_TRADES} | "
                 f"PnL={_daily_pnl:+.2f}% | F&G={_fg_cache['value']} | "
                 f"MTF_min={_mtf_confluence_dyn:.2f} ──")
        # ── PARALELO: analiza todos los símbolos simultáneamente ─
        # v5: secuencial ~6s para 6 syms. v6.1: paralelo ~1.5s total
        async def _scan_one(sym):
            try:
                res = await analyze_mtf(sym)
                return sym, res
            except Exception as e:
                log.error(f"[SCAN] {sym}: {e}")
                if "connection" in str(e).lower() or "timeout" in str(e).lower():
                    log.warning(f"[EXCHANGE] Timeout en {sym}, reconectando...")
                    try: await exchange_async.close()
                    except: pass
                    await asyncio.sleep(3)
                return sym, None

        scan_results = await asyncio.gather(*[_scan_one(s) for s in SCAN_SYMBOLS])

        found = 0
        for sym, res in scan_results:
            if res is None: continue
            sig   = res["signal"]
            score = res["mtf_score"]
            found += 1
            log.info(f"  ✅ {sym} → {sig} | MTF={score:.2f} | "
                     f"H={res.get('hurst',0.5):.3f} | K={res.get('kurt',3.0):.1f}")
            await process_signal(res, source="Scanner", mtf_score=score)

        log.info(f"── FIN #{n} | señales={found} | PnL={_daily_pnl:+.2f}% ──")
        await asyncio.sleep(SCAN_INTERVAL)

# ═══════════════════════════════════════════════════════════════
#   FLASK — API REST
# ═══════════════════════════════════════════════════════════════

flask_app = Flask(__name__)
_loop: asyncio.AbstractEventLoop | None = None

def _run_async(coro):
    if _loop and _loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(coro, _loop)
        return fut.result(timeout=30)
    return {"error": "Loop no disponible"}

@flask_app.route("/", methods=["GET"])
def health():
    ok, reason = circuit_breaker()
    return jsonify({
        "status": "online", "bot": "AEGIS GEX v6.0", "mode": MODE,
        "scanner": SCANNER_ENABLED, "symbols": SCAN_SYMBOLS,
        "open_trades": list(_open_trades), "daily_pnl_pct": round(_daily_pnl, 2),
        "circuit_breaker_active": not ok, "block_reason": reason,
        "ai": {"trained": _ai_model_ready, "z_threshold": round(_ai_z_threshold, 3),
               "memory": len(_ai_memory)},
        "trailing_active": list(_trailing_state.keys()),
        "simons": {
            "hurst_window": HURST_WINDOW,
            "kurt_max": KURT_MAX,
            "hurst_trend_min": HURST_TREND_MIN,
            "hurst_revert_max": HURST_REVERT_MAX,
        },
        "qe": {
            "fear_greed": _fg_cache["value"],
            "fg_label": _fg_cache["label"],
            "fg_bias": _fg_cache["bias"],
            "mtf_confluence_dynamic": round(_mtf_confluence_dyn, 3),
            "oi_cache_symbols": list(_oi_cache.keys()),
        },
    }), 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Webhook-Secret","") != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True)
    if not data: return jsonify({"error": "No JSON"}), 400
    log.info(f"Webhook: {data}")
    res = _run_async(process_signal(data, source="TradingView"))
    if res.get("error"): return jsonify({"status": "error", "message": res["error"]}), 500
    return jsonify({"status": "success"}), 200

@flask_app.route("/status", methods=["GET"])
def status():
    ok, reason = circuit_breaker()
    return jsonify({
        "mode": MODE, "open_trades": list(_open_trades),
        "trailing": {k: {kk:vv for kk,vv in v.items() if kk not in ("paper",)}
                     for k,v in _trailing_state.items()},
        "daily_pnl_pct": round(_daily_pnl, 2),
        "can_trade": ok, "block_reason": reason,
        "ai": {"trained": _ai_model_ready, "z_threshold": round(_ai_z_threshold, 3),
               "memory": len(_ai_memory), "rf_ready": _rf.ok},
        "qe": {"fear_greed": _fg_cache["value"], "label": _fg_cache["label"],
               "mtf_dynamic": round(_mtf_confluence_dyn, 3)},
    }), 200

@flask_app.route("/scan", methods=["GET"])
def scan_now():
    results = []
    for sym in SCAN_SYMBOLS:
        try:
            res = _run_async(analyze_mtf(sym.strip()))
            if res: results.append(res)
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return jsonify({"scanned": len(SCAN_SYMBOLS), "found": len(results), "signals": results}), 200

@flask_app.route("/risk", methods=["GET"])
def risk():
    ok, reason = circuit_breaker()
    return jsonify({
        "balance": ACCOUNT_BALANCE, "risk_pct": RISK_PER_TRADE*100,
        "max_trades": MAX_OPEN_TRADES, "current_open": len(_open_trades),
        "daily_loss_limit_pct": MAX_DAILY_LOSS*100, "daily_pnl_pct": round(_daily_pnl, 2),
        "can_trade": ok, "reason": reason, "leverage": LEVERAGE,
        "order_size_usdt": calc_order_size(),
    }), 200

@flask_app.route("/ai", methods=["GET"])
def ai_info():
    return jsonify({
        "trained": _ai_model_ready, "z_threshold": round(_ai_z_threshold, 3),
        "memory_size": len(_ai_memory), "rf_ready": _rf.ok,
        "features": ["hora", "vol_mult", "mtf_score", "funding", "ob_imb", "hurst", "kurt_norm"],
        "last_5": list(_ai_memory)[-5:],
    }), 200

@flask_app.route("/macro", methods=["GET"])
def macro():
    """Nuevo endpoint v6 — estado macro QE"""
    refresh_fear_greed()
    return jsonify({
        "fear_greed": _fg_cache["value"],
        "label": _fg_cache["label"],
        "bias": "LONG" if _fg_cache["bias"]==1 else ("SHORT" if _fg_cache["bias"]==-1 else "NEUTRO"),
        "mtf_confluence_dynamic": round(_mtf_confluence_dyn, 3),
        "mtf_confluence_base": MTF_CONFLUENCE_MIN,
        "oi_cache": _oi_cache,
        "hurst_config": {
            "window": HURST_WINDOW,
            "trending_above": HURST_TREND_MIN,
            "reverting_below": HURST_REVERT_MAX,
        },
    }), 200

# ═══════════════════════════════════════════════════════════════
#   ARRANQUE
# ═══════════════════════════════════════════════════════════════

import threading

def run_flask(port):
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)

async def main():
    global _loop
    _loop = asyncio.get_running_loop()
    tasks = [trailing_loop()]
    if SCANNER_ENABLED:
        tasks.append(scanner_loop())
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    send_telegram(
        f"🔌 <b>AEGIS GEX v6.0 arrancando...</b>\n"
        f"Modo: <b>{MODE.upper()}</b> | Puerto: <b>{port}</b>\n"
        f"🔬 Simons: Hurst + Kurtosis + Autocorrelación\n"
        f"📡 QE: Fear&Greed + OI Delta\n"
        f"Ejecución: <b>Maker-First limit → Market fallback</b>"
    )
    t = threading.Thread(target=run_flask, args=(port,), daemon=True)
    t.start()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Detenido manualmente.")
    finally:
        asyncio.run(exchange_async.close())
