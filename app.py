#!/usr/bin/env python3
"""
BOT LONGS v6.0 — PRECISION EDITION
════════════════════════════════════════════════════════════════════════════════

DIAGNÓSTICO de los bots anteriores:
  ❌ AEGIS + SuperBot corriendo en paralelo → SHORTs descontrolados en ALTUSDT,
     BANANAUSDT (5 SHORTs en 1 hora → pérdida pura de comisiones)
  ❌ 72 páginas de historial = ~500 trades → comisiones ~$30 con cuenta de $67
  ❌ SKY-USDT y REZ-USDT abiertos con 10x (bug en _recover)
  ❌ Score mínimo 60 demasiado bajo → señales de baja calidad
  ❌ AUROLO_MIN_PTS=1 en v5.9 → ruido enorme
  ❌ Sin filtro de sesión en v5.9/v5.9-FULLSCAN → opera 24/7
  ❌ CAUTION_BLOCK=false → opera en mercados laterales
  ❌ Múltiples bots compitiendo por el mismo capital

CAMBIOS v6.0 PRECISION:
  ✅ FIX CRÍTICO: PARAR AEGIS Y SUPERBOT — solo un bot activo
  ✅ Score mínimo subido a 75 (bull) / 85 (neutral)
  ✅ AUROLO_MIN_PTS=2 por defecto, con gate extra de calidad
  ✅ Sesión ESTRICTA: solo London (7-12h) + NY open (13-17h) UTC
  ✅ trend_4h == 1 requerido SIEMPRE (no solo en neutral)
  ✅ BTC 1h debe ser > -0.3% (era -1.0%)
  ✅ Breadth mínimo 50% (era 35%)
  ✅ VOL_RATIO_MIN=1.5 (era 1.2)
  ✅ MAX_DAILY_TRADES=3 (nuevo: máximo 3 trades por día)
  ✅ MAX_TRADES=2 (era 3)
  ✅ RSI 1h < 65 (evitar sobrecompra)
  ✅ Require OFI alcista O MTF15m alcista (al menos uno)
  ✅ Fix bug leverage en _recover() — ignora posiciones con leverage > LEVERAGE+1
  ✅ Cooldown SL: 6h (era 4h)
  ✅ TP1_RATIO subido a 2.5 (era 2.0) para cubrir comisiones con margen
  ✅ Awareness de comisiones: TP neto mínimo 0.6%
  ✅ Filtro change_24h: solo -5% a +12% (evita pumps/dumps extremos)
  ✅ Trailing stop manual robusto cuando BingX lo rechaza
  ✅ Reporte diario de calidad de señales
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json, random
from datetime import datetime, timedelta
from urllib.parse import urlencode
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================================
# CONFIG
# ============================================================================

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default)).strip()
    if v.startswith('"') and v.endswith('"'): v = v[1:-1]
    elif v.startswith("'") and v.endswith("'"): v = v[1:-1]
    if typ in ('int', 'float'):
        v = v.replace(',', '.')
        m = re.match(r'^-?\d+\.?\d*', v)
        v = m.group(0) if m else str(default)
    if typ == 'int':   return int(float(v))
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

def _strip_quotes(s):
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or \
       (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s

API_KEY    = _strip_quotes(os.getenv('BINGX_API_KEY',    ''))
API_SECRET = _strip_quotes(os.getenv('BINGX_API_SECRET', ''))
TG_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

# ── Capital ────────────────────────────────────────────────────────────────
AUTO           = clean('AUTO_TRADING_ENABLED', 'true',  'bool')
POS_SIZE       = clean('MAX_POSITION_SIZE',    '10',    'float')
MIN_TRADE      = clean('MIN_TRADE_USDT',       '10',    'float')
_lev           = clean('LEVERAGE',             '2',     'int')
LEVERAGE       = min(_lev, 3)
MAX_TRADES     = clean('MAX_OPEN_TRADES',      '2',     'int')   # v6.0: era 3
MAX_DAILY_TRADES = clean('MAX_DAILY_TRADES',   '3',     'int')   # v6.0: NUEVO
RISK_PCT       = clean('RISK_PCT',             '1.0',   'float')
ACCOUNT_EQUITY = clean('ACCOUNT_EQUITY',       '100',   'float')

# ── TPs Escalonados ────────────────────────────────────────────────────────
TP1_PCT   = clean('TP1_PCT',   '40',  'float')
TP2_PCT   = clean('TP2_PCT',   '35',  'float')
TP1_RATIO = clean('TP1_RATIO', '2.5', 'float')  # v6.0: era 2.0 — más margen sobre comisiones
TP2_RATIO = clean('TP2_RATIO', '4.0', 'float')  # v6.0: era 3.5

# ── TP/SL ──────────────────────────────────────────────────────────────────
TP_MIN    = clean('TAKE_PROFIT_PCT', '1.5',  'float')
ATR_TP_M  = clean('ATR_TP_MULT',    '3.0',  'float')
MIN_RR    = clean('MIN_RR',         '2.5',  'float')   # v6.0: era 2.2
SL_ATR_M  = clean('SL_ATR_MULT',   '1.5',  'float')
SL_MAX_PCT = clean('SL_MAX_PCT',   '2.5',  'float')    # v6.0: era 3.5 — más tight
SL_MIN_PCT = clean('SL_MIN_PCT',   '0.7',  'float')

# ── Trailing Stop ──────────────────────────────────────────────────────────
USE_TRAILING_EXIT = clean('USE_TRAILING_EXIT', 'true', 'bool')
TRAIL_RATE_PCT    = clean('TRAIL_RATE_PCT',    '1.2',  'float')  # v6.0: era 1.5
TRAIL_ACTIVATION  = clean('TRAIL_ACTIVATION',  '1.0',  'float')  # v6.0: era 0.8

# ── Zombie cleanup ────────────────────────────────────────────────────────
ZOMBIE_CLEANUP_MIN = clean('ZOMBIE_CLEANUP_MIN', '10', 'int')
ZOMBIE_MAX_AGE_MIN = clean('ZOMBIE_MAX_AGE_MIN', '20', 'int')

# ── Símbolos y volumen ────────────────────────────────────────────────────
MIN_VOL   = clean('MIN_VOLUME_24H',  '1000000', 'float')  # v6.0: era 300K — más liquidez
MAX_SYMS  = clean('MAX_SYMBOLS',     '100',     'int')    # v6.0: limitar a 100 mejores
MIN_SCORE = clean('MIN_SCORE',       '75',      'float')  # v6.0: era 60
BTC_BLOCK = clean('BTC_BEAR_BLOCK_PCT', '0.3', 'float')  # v6.0: era 1.0 — más estricto

# ── Régimen de mercado ────────────────────────────────────────────────────
REGIME_CHECK      = clean('REGIME_CHECK',      'true',  'bool')
BREADTH_MIN       = clean('BREADTH_MIN',        '0.50',  'float')  # v6.0: era 0.35
BREADTH_BEAR_HARD = clean('BREADTH_BEAR_HARD',  '0.30',  'float')  # v6.0: era 0.20
BTC_4H_CRASH_PCT  = clean('BTC_4H_CRASH_PCT',  '2.5',   'float')
BTC_4H_CRASH_PAUSE= clean('BTC_4H_CRASH_HOURS','3',      'int')    # v6.0: era 2
DAILY_LOSS_CAP_PCT= clean('DAILY_LOSS_CAP_PCT','8.0',   'float')
CAUTION_BLOCK     = clean('CAUTION_BLOCK',      'true',  'bool')
SCORE_BULL        = clean('SCORE_BULL',         '75',    'float')  # v6.0: era 60
SCORE_NEUTRAL     = clean('SCORE_NEUTRAL',      '85',    'float')  # v6.0: era 68

# ── VWAP ──────────────────────────────────────────────────────────────────
VWAP_CANDLES   = clean('VWAP_CANDLES',  '50',   'int')
VWAP_AS_FILTER = clean('VWAP_FILTER',  'true',  'bool')

# ── Motor Aurolo ──────────────────────────────────────────────────────────
AUROLO_EMA_LEN    = clean('AUROLO_EMA_LEN',   '55',   'int')
AUROLO_ZONA_AUTO  = clean('AUROLO_ZONA_AUTO',  'true', 'bool')
AUROLO_ZONA_PCT   = clean('AUROLO_ZONA_PCT',   '0.8',  'float')
AUROLO_ZONA_VELAS = clean('AUROLO_ZONA_VELAS', '6',    'int')
AUROLO_MIN_PTS    = clean('AUROLO_MIN_PTS',    '2',    'int')   # v6.0: mínimo 2/3
AUROLO_ENTRY      = clean('AUROLO_ENTRY',      'close','str')
VOL_RATIO_MIN     = clean('VOL_RATIO_MIN',     '1.5',  'float') # v6.0: era 1.2

# WaveTrend
WT_CH_LEN   = clean('WT_CH_LEN',   '10',  'int')
WT_AVG_LEN  = clean('WT_AVG_LEN',  '21',  'int')
WT_OB1      = clean('WT_OB1',      '60',  'float')
WT_OB2      = clean('WT_OB2',      '42',  'float')
WT_OS1      = clean('WT_OS1',      '-60', 'float')
WT_OS2      = clean('WT_OS2',      '-42', 'float')
WT_OS_ENTRY = clean('WT_OS_ENTRY', '-20', 'float')

# ADX
ADX_LEN    = clean('ADX_LEN',    '14',  'int')
ADX_DI_LEN = clean('ADX_DI_LEN', '14',  'int')
ADX_KEY    = clean('ADX_KEY',    '20',  'float')

# ── Circuit breaker ───────────────────────────────────────────────────────
CB_PCT     = clean('CIRCUIT_BREAKER_PCT', '5.0',  'float')  # v6.0: era 6.0
CB_HOURS   = clean('CB_PAUSE_HOURS',      '4',    'int')    # v6.0: era 2
MAX_STREAK = clean('MAX_LOSING_STREAK',   '3',    'int')    # v6.0: era 4

# ── Cooldowns ─────────────────────────────────────────────────────────────
CD_TP            = clean('COOLDOWN_TP_MIN',       '15',  'int')   # v6.0: era 10
CD_SL            = clean('COOLDOWN_SL_MIN',       '360', 'int')   # v6.0: era 240 → 6h
CD_SL_TODAY      = clean('COOLDOWN_SL_TODAY',     'true','bool')
CD_SL_FAST_MIN   = clean('COOLDOWN_SL_FAST_MIN',  '10',  'int')
CD_SL_FAST_HOURS = clean('COOLDOWN_SL_FAST_HOURS','12',  'int')   # v6.0: era 8 → 12h

# ── Aprendizaje ───────────────────────────────────────────────────────────
LEARN_MIN_TRADES_SCORE = clean('LEARN_MIN_TRADES',    '10', 'int')
LEARN_MIN_TRADES_BL    = clean('LEARN_MIN_TRADES_BL', '5',  'int')
SCORE_CAP_LOW          = clean('SCORE_CAP_LOW',       '80', 'float')
SCORE_CAP_HIGH         = clean('SCORE_CAP_HIGH',      '92', 'float')

# ── Sesión (v6.0: ESTRICTA) ───────────────────────────────────────────────
SESSION_FILTER   = clean('SESSION_FILTER',   'true', 'bool')
SESSION_LONDON_S = clean('SESSION_LONDON_S', '7',    'int')   # 07:00 UTC
SESSION_LONDON_E = clean('SESSION_LONDON_E', '12',   'int')   # 12:00 UTC
SESSION_NY_S     = clean('SESSION_NY_S',     '13',   'int')   # 13:00 UTC
SESSION_NY_E     = clean('SESSION_NY_E',     '17',   'int')   # 17:00 UTC

# ── Smart Money (v6.0) ────────────────────────────────────────────────────
STOP_HUNT_DETECT  = clean('STOP_HUNT_DETECT', 'true', 'bool')
STOP_HUNT_WICK    = clean('STOP_HUNT_WICK',   '0.6',  'float')
STOP_HUNT_VOL_M   = clean('STOP_HUNT_VOL_M',  '2.5',  'float')
OFI_BULL_THRESH   = clean('OFI_BULL_THRESH',  '0.58', 'float')
OFI_SCORE_BONUS   = clean('OFI_SCORE_BONUS',  '15',   'float')
MTF_SCORE_BONUS   = clean('MTF_SCORE_BONUS',  '20',   'float')
SL_HUNT_OFFSET    = clean('SL_HUNT_OFFSET',   '0.05', 'float')
ATR_HIGH_PCT      = clean('ATR_HIGH_PCT',     '3.5',  'float')
ATR_HIGH_SIZE_M   = clean('ATR_HIGH_SIZE_M',  '0.5',  'float')

# ── Misc ──────────────────────────────────────────────────────────────────
INTERVAL     = clean('CHECK_INTERVAL', '90', 'int')
LTV_WARN     = clean('LTV_WARNING_PCT','75',  'float')
SCAN_WORKERS = clean('SCAN_WORKERS',   '6',   'int')  # v6.0: era 8, reducir para más cuidado

BASE_URL = "https://open-api.bingx.com"
FEE      = 0.0002  # maker
FEE_TAKER = 0.001  # taker
# v6.0: asumir worst-case taker para calcular rentabilidad real
FEE_COST_PCT  = FEE_TAKER * LEVERAGE * 2 * 100  # % round-trip al tamaño
TP_MIN_NET_PCT = FEE_COST_PCT + 0.2  # TP neto mínimo tras comisiones

# Excluir stablecoins y forex
EXCLUDE = {
    'USDC', 'BUSD', 'TUSD', 'FRAX', 'DAI', 'USDP', 'FDUSD',
    'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD',
}

BREADTH_COINS = [
    'BTC-USDT','ETH-USDT','BNB-USDT','SOL-USDT','XRP-USDT',
    'ADA-USDT','AVAX-USDT','DOGE-USDT','DOT-USDT','MATIC-USDT',
    'LINK-USDT','UNI-USDT','ATOM-USDT','LTC-USDT','BCH-USDT',
    'NEAR-USDT','APT-USDT','OP-USDT','ARB-USDT','SUI-USDT',
]

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ============================================================================
# API
# ============================================================================

def api(method, endpoint, params=None, retries=3):
    params = params or {}
    for attempt in range(retries + 1):
        try:
            p   = {**{k: str(v) for k, v in params.items()},
                   'timestamp': str(int(time.time() * 1000))}
            qs  = urlencode(sorted(p.items()))
            sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY': API_KEY,
                   'Content-Type': 'application/x-www-form-urlencoded'}
            r   = getattr(requests, method.lower())(url, headers=hdr, timeout=15)
            return r.json()
        except Exception as e:
            if attempt < retries: time.sleep(2 ** attempt)
            else: log.error(f"API {endpoint}: {e}"); return {}

def pub(path, params=None):
    try:
        return requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10).json()
    except:
        return {}

def _safe_float(val, default=0.0):
    if val is None: return default
    if isinstance(val, dict):
        for k in ('equity', 'balance', 'availableMargin', 'amount'):
            if k in val: return _safe_float(val[k], default)
        return default
    try: return float(val)
    except: return default

# ============================================================================
# INDICADORES
# ============================================================================

def ema(prices, n):
    if not prices: return 0
    if len(prices) < n: return sum(prices) / len(prices)
    k, e = 2 / (n + 1), prices[0]
    for p in prices[1:]: e = p * k + e * (1 - k)
    return e

def rsi(prices, n=14):
    if len(prices) < n + 1: return 50.0
    g = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
    l = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]
    ag, al = sum(g[-n:]) / n, sum(l[-n:]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

def atr_calc(highs, lows, closes, n=14):
    if len(closes) < 2: return 0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, min(len(closes), n+1))]
    return sum(trs) / len(trs) if trs else 0

def calc_vwap(closes, highs, lows, volumes, n=None):
    n = n or len(closes)
    c = closes[-n:]; h = highs[-n:]; l = lows[-n:]; v = volumes[-n:]
    tp_vol  = sum(((h[i]+l[i]+c[i])/3) * v[i] for i in range(len(c)))
    vol_sum = sum(v)
    return tp_vol / vol_sum if vol_sum > 0 else c[-1]

# ============================================================================
# SMART MONEY FILTERS (v6.0)
# ============================================================================

def session_ok(hora_utc):
    """Filtra SOLO London open y NY open — las ventanas de mayor calidad institucional."""
    if not SESSION_FILTER: return True, "sin filtro"
    h = hora_utc
    if SESSION_LONDON_S <= h < SESSION_LONDON_E:
        return True, f"London ({h}h UTC)"
    if SESSION_NY_S <= h < SESSION_NY_E:
        return True, f"NY ({h}h UTC)"
    return False, f"fuera de sesión ({h}h UTC)"


def detect_stop_hunt(closes, highs, lows, volumes, n_lookback=3):
    """Detecta barrido de liquidez antes de entrar."""
    if len(closes) < n_lookback + 6: return False, "datos insuf."
    vol_avg = sum(volumes[-8:-2]) / 6 if len(volumes) >= 8 else volumes[-1]
    if vol_avg <= 0: return False, "vol avg cero"
    for i in range(-n_lookback, 0):
        c = closes[i]; o = closes[i-1]
        mecha_inf = min(o, c) - lows[i]
        cuerpo = abs(c - o) + 1e-10
        ratio_wick = mecha_inf / cuerpo
        vol_r = volumes[i] / (vol_avg + 1e-10)
        if ratio_wick >= STOP_HUNT_WICK and vol_r >= STOP_HUNT_VOL_M and c > o:
            return True, f"hunt wick={ratio_wick:.1f}x vol={vol_r:.1f}x"
    return False, "limpio"


def order_flow_imbalance(closes, opens, volumes, n=10):
    """Presión compradora vs vendedora (proxy con velas)."""
    if len(closes) < n + 1: return 0.5, "insuf."
    bull_vol = bear_vol = 0.0
    for i in range(-n, 0):
        c = closes[i]; o = opens[i] if opens else closes[i-1]
        v = volumes[i]
        if c > o:   bull_vol += v
        elif c < o: bear_vol += v
        else:       bull_vol += v * 0.5; bear_vol += v * 0.5
    total = bull_vol + bear_vol
    if total <= 0: return 0.5, "sin vol"
    ratio = bull_vol / total
    desc = f"OFI {int(ratio*100)}% bull" if ratio >= OFI_BULL_THRESH else f"OFI {int(ratio*100)}% bear"
    return ratio, desc


def sl_anti_hunt(sl_price, price):
    """Offset aleatorio al SL para romper niveles redondos cazados por MMs."""
    offset_pct = random.uniform(-SL_HUNT_OFFSET * 2, SL_HUNT_OFFSET * 0.5)
    sl_adj = sl_price * (1 + offset_pct / 100)
    sl_max = price * (1 - SL_MIN_PCT / 100)
    sl_min = price * (1 - SL_MAX_PCT * 1.1 / 100)
    return round(max(sl_min, min(sl_max, sl_adj)), 8)

# ============================================================================
# MOTOR AUROLO
# ============================================================================

def _wavetrend_series(closes, highs, lows, ch_len=10, avg_len=21):
    n = len(closes)
    if n < ch_len + avg_len + 2: return [0.0] * n
    hlc3 = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(n)]
    k  = 2 / (ch_len + 1)
    esa = [hlc3[0]] * n
    for i in range(1, n): esa[i] = hlc3[i] * k + esa[i-1] * (1 - k)
    d  = [abs(hlc3[i] - esa[i]) for i in range(n)]
    de = [d[0]] * n
    for i in range(1, n): de[i] = d[i] * k + de[i-1] * (1 - k)
    ci = [(hlc3[i] - esa[i]) / (0.015 * de[i]) if de[i] != 0 else 0 for i in range(n)]
    k2  = 2 / (avg_len + 1)
    wt1 = [ci[0]] * n
    for i in range(1, n): wt1[i] = ci[i] * k2 + wt1[i-1] * (1 - k2)
    return wt1


def _adx_di_series(highs, lows, closes, di_len=14, adx_smooth=14):
    n = len(closes)
    if n < di_len + adx_smooth + 2: return [0.0]*n, [0.0]*n, [0.0]*n
    tr = [0.0]*n; pdm = [0.0]*n; ndm = [0.0]*n
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr[i]  = max(h-l, abs(h-pc), abs(l-pc))
        up, dn = highs[i]-highs[i-1], lows[i-1]-lows[i]
        pdm[i] = max(up, 0) if up > dn else 0
        ndm[i] = max(dn, 0) if dn > up else 0
    def wilder(data, n):
        s = [0.0] * len(data)
        if n < len(data):
            s[n] = sum(data[1:n+1])
            for i in range(n+1, len(data)):
                s[i] = s[i-1] - s[i-1]/n + data[i]
        return s
    atr_s = wilder(tr, di_len); pdm_s = wilder(pdm, di_len); ndm_s = wilder(ndm, di_len)
    dip = [100*pdm_s[i]/atr_s[i] if atr_s[i]>0 else 0 for i in range(n)]
    din = [100*ndm_s[i]/atr_s[i] if atr_s[i]>0 else 0 for i in range(n)]
    dx  = [abs(dip[i]-din[i])/(dip[i]+din[i])*100 if (dip[i]+din[i])>0 else 0 for i in range(n)]
    adx_v = [0.0] * n
    start = di_len + adx_smooth
    if start < n:
        adx_v[start] = sum(dx[di_len:start+1]) / adx_smooth
        for i in range(start+1, n):
            adx_v[i] = (adx_v[i-1]*(adx_smooth-1) + dx[i]) / adx_smooth
    return adx_v, dip, din


def aurolo_signal(closes, highs, lows, volumes, opens, atr_v=None):
    result = {
        'puntos': 0, 'señal': 'NO', 'p1': False, 'p2': False, 'p3': False,
        'ema55': 0, 'zona_inf': 0, 'zona_sup': 0,
        'wt_now': 0, 'wt_prev': 0, 'adx_now': 0, 'dip': 0, 'din': 0,
        'sl_price': 0, 'sl_pct': 0, 'debilidad': False,
        'cambio_tend': False, 'descripcion': '', 'vol_ratio': 1,
    }
    min_len = AUROLO_EMA_LEN + WT_CH_LEN + WT_AVG_LEN + 5
    if len(closes) < min_len:
        result['descripcion'] = 'Datos insuficientes'
        return result

    price  = closes[-1]
    ema55  = ema(closes, AUROLO_EMA_LEN)
    result['ema55'] = ema55

    ema55_prev      = ema(closes[:-1], AUROLO_EMA_LEN)
    tendencia_ahora = price > ema55
    tendencia_antes = closes[-2] > ema55_prev if len(closes) >= 2 else tendencia_ahora
    result['cambio_tend'] = (tendencia_ahora != tendencia_antes)

    if not tendencia_ahora:
        result['señal'] = 'NO'
        result['descripcion'] = f'Bajista (p={round(price,4)} < EMA55={round(ema55,4)})'
        return result

    if AUROLO_ZONA_AUTO and atr_v and atr_v > 0:
        zona_pct = (atr_v / price * 100) * 1.0
        zona_pct = max(min(zona_pct, 2.0), 0.3)
    else:
        zona_pct = AUROLO_ZONA_PCT
    zona_inf = ema55 * (1 - zona_pct / 100)
    zona_sup = ema55 * (1 + zona_pct / 100)
    result['zona_inf'] = zona_inf; result['zona_sup'] = zona_sup

    toco_zona = False
    n_velas = min(AUROLO_ZONA_VELAS, len(closes) - 1)
    for i in range(-n_velas, 0):
        c_i = closes[i]; l_i = lows[i]
        if AUROLO_ENTRY == 'close':
            if zona_inf <= c_i <= zona_sup: toco_zona = True; break
        else:
            if l_i <= zona_sup and c_i >= zona_inf * 0.993: toco_zona = True; break

    rebota = closes[-1] > ema55 * 0.999
    result['p1'] = toco_zona and rebota

    wt1      = _wavetrend_series(closes, highs, lows, WT_CH_LEN, WT_AVG_LEN)
    wt_now   = wt1[-1]
    wt_prev  = wt1[-2] if len(wt1) >= 2 else wt_now
    wt_prev2 = wt1[-3] if len(wt1) >= 3 else wt_prev
    result['wt_now'] = wt_now; result['wt_prev'] = wt_prev

    cruce_alc = (wt_now > wt_prev) and (wt_prev <= WT_OS_ENTRY or wt_prev2 <= WT_OS2)
    en_os     = wt_now <= WT_OS2
    result['p2'] = cruce_alc or (en_os and wt_now > wt_prev)

    adx_vals, dip_vals, din_vals = _adx_di_series(highs, lows, closes, ADX_DI_LEN, ADX_LEN)
    adx_now  = adx_vals[-1]; adx_prev = adx_vals[-2] if len(adx_vals) >= 2 else adx_now
    dip_now  = dip_vals[-1]; din_now  = din_vals[-1]
    result['adx_now'] = adx_now; result['dip'] = dip_now; result['din'] = din_now

    result['p3'] = (adx_now >= ADX_KEY) and (dip_now > din_now)

    pts = int(result['p1']) + int(result['p2']) + int(result['p3'])
    result['puntos'] = pts

    atr_actual   = atr_v or atr_calc(highs, lows, closes, 14)
    min_reciente = min(lows[-8:-1]) if len(lows) >= 8 else lows[-1]
    sl_vulner    = min_reciente - atr_actual * SL_ATR_M
    sl_bajo_ema  = ema55 * (1 - 0.20/100)
    sl_calculado = min(sl_vulner, sl_bajo_ema)

    sl_max_price = price * (1 - SL_MAX_PCT / 100)
    sl_min_price = price * (1 - SL_MIN_PCT / 100)
    sl_price = max(sl_calculado, sl_max_price)
    sl_price = min(sl_price, sl_min_price)
    if sl_price >= price: sl_price = price * (1 - SL_MIN_PCT / 100)

    sl_pct = (price - sl_price) / price * 100
    if sl_pct < SL_MIN_PCT: sl_price = price * (1 - SL_MIN_PCT / 100); sl_pct = SL_MIN_PCT

    result['sl_price'] = round(sl_price, 8); result['sl_pct'] = round(sl_pct, 3)

    wt_ob_baj   = wt_now < wt_prev and wt_prev >= WT_OB2
    di_gira     = din_now > dip_now * 0.80
    adx_cayendo = adx_now < adx_prev
    result['debilidad'] = bool(adx_cayendo and (wt_ob_baj or wt_now >= WT_OB1) and di_gira)

    vol_avg = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    result['vol_ratio'] = volumes[-1] / vol_avg if vol_avg > 0 else 1

    p1i = '✅' if result['p1'] else '❌'
    p2i = '✅' if result['p2'] else '❌'
    p3i = '✅' if result['p3'] else '❌'
    result['descripcion'] = (f"P1({p1i})EMA55 | P2({p2i})WT={round(wt_now,1)} | "
                             f"P3({p3i})ADX={round(adx_now,1)} DI+={round(dip_now,1)}")

    if pts >= 3:   result['señal'] = 'LONG_3/3'
    elif pts == 2: result['señal'] = 'LONG_2/3'
    elif pts == 1: result['señal'] = 'LONG_1/3'
    else:          result['señal'] = 'NO'
    return result


def vwap_contexto(closes, highs, lows, volumes, n=50):
    if len(closes) < n: return closes[-1], True
    vwap = calc_vwap(closes, highs, lows, volumes, n)
    return vwap, closes[-1] > vwap

# ============================================================================
# APRENDIZAJE
# ============================================================================

class Learning:
    def __init__(self):
        self.history       = []
        self.sym_stats     = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0,'n':0})
        self.opt_score     = MIN_SCORE
        self.blacklist     = set()
        self.streak        = 0
        self.last10        = []
        self.by_hour       = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})
        self.by_pts        = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})
        self.factor_wins   = defaultdict(int)
        self.factor_losses = defaultdict(int)
        self.score_boost   = {}
        self.daily_losers  = set()
        self._daily_date   = datetime.utcnow().date()

    def _check_daily_reset(self):
        today = datetime.utcnow().date()
        if today != self._daily_date: self.daily_losers = set(); self._daily_date = today

    def _score_cap(self):
        return SCORE_CAP_LOW if len(self.history) < LEARN_MIN_TRADES_SCORE else SCORE_CAP_HIGH

    def record(self, symbol, score, pnl, win, hora_utc=None,
               pts_aurolo=0, btc_dir='flat', reason='?', factors=None):
        self._check_daily_reset()
        rec = {'ts': datetime.now().isoformat(), 'sym': symbol, 'score': score,
               'pnl': pnl, 'win': win, 'hora': hora_utc or datetime.utcnow().hour,
               'pts': pts_aurolo, 'btc': btc_dir, 'reason': reason, 'factors': factors or []}
        self.history.append(rec); self.last10.append(rec)
        if len(self.last10) > 10: self.last10.pop(0)
        s = self.sym_stats[symbol]; s['n'] += 1; s['pnl'] += pnl
        if win: s['w'] += 1; self.streak = 0
        else:   s['l'] += 1; self.streak += 1
        if CD_SL_TODAY and not win and 'SL' in reason.upper():
            self.daily_losers.add(symbol)
        self.by_hour[rec['hora']]['w' if win else 'l'] += 1
        self.by_hour[rec['hora']]['pnl'] += pnl
        self.by_pts[pts_aurolo]['w' if win else 'l'] += 1
        for f in (factors or []):
            if win: self.factor_wins[f] += 1
            else:   self.factor_losses[f] += 1
        self._adjust()
        if len(self.history) % 5 == 0: self._reporte()

    def _adjust(self):
        cap = self._score_cap()
        if len(self.history) >= LEARN_MIN_TRADES_SCORE:
            wr = sum(1 for t in self.last10 if t['win']) / len(self.last10)
            if   wr < 0.30: self.opt_score = min(self.opt_score + 5, cap)
            elif wr < 0.40: self.opt_score = min(self.opt_score + 2, cap)
            elif wr > 0.65: self.opt_score = max(self.opt_score - 2, MIN_SCORE)
            elif wr > 0.75: self.opt_score = max(self.opt_score - 4, MIN_SCORE)
        self.opt_score = max(min(self.opt_score, cap), MIN_SCORE)

        for sym, s in self.sym_stats.items():
            tot = s['w'] + s['l']
            if tot >= LEARN_MIN_TRADES_BL and s['pnl'] < -1.5 and s['w']/tot < 0.25:
                if sym not in self.blacklist:
                    self.blacklist.add(sym); log.warning(f"  [LEARN] 🚫 {sym} → blacklist")

        if len(self.history) >= 15:
            for f in set(list(self.factor_wins) + list(self.factor_losses)):
                w = self.factor_wins.get(f, 0); l = self.factor_losses.get(f, 0)
                if w+l < 5: continue
                wr_f = w/(w+l)
                if   wr_f < 0.30: self.score_boost[f] = -10
                elif wr_f > 0.70: self.score_boost[f] = +6
                else:             self.score_boost.pop(f, None)

    def hora_ok(self, h):
        d = self.by_hour.get(h)
        if not d: return True, "ok"
        tot = d['w']+d['l']
        if tot < 6: return True, "ok"
        wr_hora = d['w'] / tot
        if wr_hora < 0.25: return False, f"hora {h}h WR={int(wr_hora*100)}%"
        return True, "ok"

    def bonus_pts(self, pts):
        d = self.by_pts.get(pts)
        if not d: return 0
        tot = d['w']+d['l']
        if tot < 5: return 0
        wr = d['w']/tot
        if wr > 0.65: return +10
        if wr < 0.35: return -15
        return 0

    def ok(self, sym, score):
        self._check_daily_reset()
        if sym in self.blacklist:    return False, "blacklist"
        if sym in self.daily_losers: return False, "SL hoy"
        threshold = max(self.opt_score, MIN_SCORE)
        if score < threshold:        return False, f"score {int(score)}<{int(threshold)}"
        if self.streak >= MAX_STREAK:return False, f"streak -{self.streak}"
        return True, "ok"

    def adj(self, factors):
        return sum(self.score_boost.get(f, 0) for f in factors)

    def _reporte(self):
        n = len(self.history)
        wr  = sum(1 for t in self.history if t['win'])/n*100 if n else 0
        pnl = sum(t['pnl'] for t in self.history)
        log.info(f"[LEARN] #{n}: WR={int(wr)}% PnL=${pnl:+.4f} Score≥{int(self.opt_score)}")

    def save(self, fp='/tmp/bot_v60.json'):
        try:
            json.dump({
                'history': self.history[-200:], 'sym_stats': dict(self.sym_stats),
                'opt_score': self.opt_score, 'blacklist': list(self.blacklist),
                'by_hour': dict(self.by_hour), 'by_pts': dict(self.by_pts),
                'factor_wins': dict(self.factor_wins), 'factor_losses': dict(self.factor_losses),
                'score_boost': self.score_boost, 'daily_losers': list(self.daily_losers),
            }, open(fp,'w'), indent=2)
        except: pass

    def load(self, fp='/tmp/bot_v60.json'):
        for path in [fp, '/tmp/bot_learn_v511.json', '/tmp/bot_learn_v59.json',
                     '/tmp/bot_learn.json']:
            try:
                if not os.path.exists(path): continue
                d = json.load(open(path))
                self.history       = d.get('history', [])
                self.sym_stats     = defaultdict(lambda:{'w':0,'l':0,'pnl':0.0,'n':0},
                                                 d.get('sym_stats', {}))
                self.blacklist     = set(d.get('blacklist', []))
                self.by_hour       = defaultdict(lambda:{'w':0,'l':0,'pnl':0.0},
                                                 d.get('by_hour', {}))
                self.by_pts        = defaultdict(lambda:{'w':0,'l':0,'pnl':0.0},
                                                 d.get('by_pts', {}))
                self.factor_wins   = defaultdict(int, d.get('factor_wins', {}))
                self.factor_losses = defaultdict(int, d.get('factor_losses', {}))
                self.score_boost   = d.get('score_boost', {})
                self.daily_losers  = set(d.get('daily_losers', []))
                raw_score          = d.get('opt_score', MIN_SCORE)
                cap = self._score_cap()
                self.opt_score = max(min(raw_score, cap), MIN_SCORE)
                log.info(f"  [LEARN] {len(self.history)} trades | Score:{int(self.opt_score)} | BL:{len(self.blacklist)}")
                return
            except: continue

# ============================================================================
# BOT PRINCIPAL v6.0 PRECISION
# ============================================================================

class LongBot:
    _opening = False

    def __init__(self):
        log.info("=" * 72)
        log.info("  BOT LONGS v6.0 — PRECISION EDITION")
        log.info(f"  Capital: ${POS_SIZE} | {LEVERAGE}x | Max:{MAX_TRADES} trades | Diario:{MAX_DAILY_TRADES}")
        log.info(f"  Score: bull≥{SCORE_BULL} neutral≥{SCORE_NEUTRAL} | Aurolo≥{AUROLO_MIN_PTS}/3")
        log.info(f"  Sesión: London {SESSION_LONDON_S}-{SESSION_LONDON_E}h + NY {SESSION_NY_S}-{SESSION_NY_E}h UTC")
        log.info(f"  Vol min: ${MIN_VOL:,.0f} | Breadth min: {int(BREADTH_MIN*100)}%")
        log.info("=" * 72)

        self.symbols         = []
        self.trades          = {}
        self._contracts      = {}
        self._cooldowns      = {}
        self._pending_orders = {}
        self._last_report    = datetime.now() - timedelta(hours=3)
        self._last_zombie_clean = 0
        self._btc_1h         = 0.0
        self._btc_4h         = 0.0
        self._btc_ok         = True
        self._regime         = 'neutral'
        self._regime_until   = None
        self._breadth        = 0.5
        self._mode           = 'hedge'
        self._daily_pnl      = 0.0
        self._daily_trades   = 0          # v6.0: contador diario
        self._daily_date     = datetime.utcnow().date()
        self._equity_start   = ACCOUNT_EQUITY
        self._cb_active      = False
        self._cb_until       = None
        self.learn           = Learning()
        self.learn.load()
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0,'fees':0.0,
                      'filtered_session':0,'filtered_regime':0,'filtered_score':0}

        if not self._connect(): log.error("❌ Sin conexión BingX"); sys.exit(1)
        self._detect_mode()
        self._load_contracts()
        self._refresh_symbols()
        n_killed = self._nuke_zombie_orders()
        self._recover()

        self._tg(
            f"<b>🎯 Bot LONGS v6.0 PRECISION</b>\n"
            f"Max {MAX_TRADES} trades | Máx {MAX_DAILY_TRADES}/día\n"
            f"Score: bull≥{SCORE_BULL} neutral≥{SCORE_NEUTRAL}\n"
            f"Sesión: London+NY | Breadth≥{int(BREADTH_MIN*100)}%\n"
            f"SL max {SL_MAX_PCT}% | TP1 {TP1_RATIO}R | TP2 {TP2_RATIO}R\n"
            f"🧟 Zombies: {n_killed} | ♻️ Recuperadas: {len(self.trades)}\n"
            f"<b>⚠️ PARAR AEGIS Y SUPERBOT — solo un bot activo</b>"
        )

    # ── Conexión ──────────────────────────────────────────────────────────
    def _connect(self) -> bool:
        global AUTO, ACCOUNT_EQUITY
        if not AUTO: return True
        if not API_KEY or not API_SECRET:
            log.error("❌ API keys no configuradas"); AUTO = False; return False
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') == 0:
            b = d.get('data', {})
            if isinstance(b, list):
                eq = 0.0
                for item in b:
                    v = _safe_float(item)
                    if v > 0: eq = v; break
            else:
                eq = _safe_float(b.get('equity', b.get('balance', 0)))
                if eq <= 0:
                    for _, val in b.items():
                        v = _safe_float(val)
                        if v > 0: eq = v; break
            if eq > 0: ACCOUNT_EQUITY = eq; self._equity_start = eq
            log.info(f"✅ BingX conectado | ${ACCOUNT_EQUITY:.2f} USDT")
            return True
        log.error(f"❌ [{d.get('code')}]: {d.get('msg')}")
        AUTO = False; return False

    def _detect_mode(self):
        try:
            d = api('GET', '/openApi/swap/v2/user/positions', {'symbol': 'BTC-USDT'})
            for p in (d.get('data') or []):
                s = str(p.get('positionSide', '')).upper()
                if s in ('LONG', 'SHORT'): self._mode = 'hedge'; log.info("  Modo: HEDGE"); return
                if s == 'BOTH': self._mode = 'oneway'; log.info("  Modo: ONE-WAY"); return
        except: pass
        log.info("  Modo: HEDGE (default)")

    def _load_contracts(self):
        d = pub('/openApi/swap/v2/quote/contracts')
        if d.get('code') == 0:
            for c in d.get('data', []):
                s = c.get('symbol', '')
                if s: self._contracts[s] = {
                    'step': float(c.get('tradeMinQuantity', 1)),
                    'prec': int(c.get('quantityPrecision', 2)),
                    'ctval': float(c.get('contractSize', 1)),
                }
            log.info(f"  Contratos: {len(self._contracts)}")

    def _refresh_symbols(self):
        """v6.0: Mínimo $1M volumen, top 100 por liquidez."""
        d = pub('/openApi/swap/v2/quote/ticker')
        if d.get('code') != 0:
            self.symbols = self.symbols or ['BTC-USDT', 'ETH-USDT', 'SOL-USDT']; return
        items = []
        for t in d.get('data', []):
            sym = t.get('symbol', '')
            if not sym.endswith('-USDT'): continue
            base = sym.replace('-USDT', '').upper()
            if any(base == ex for ex in EXCLUDE): continue
            if any(base.startswith(ex) for ex in EXCLUDE): continue
            try:
                price = float(t.get('lastPrice', 0))
                vol   = float(t.get('volume', 0)) * price
                if vol >= MIN_VOL and price > 0:
                    items.append({'sym': sym, 'vol': vol})
            except: continue
        items.sort(key=lambda x: x['vol'], reverse=True)
        items = items[:MAX_SYMS]  # Top 100 más líquidos
        self.symbols = [x['sym'] for x in items]
        log.info(f"  Símbolos: {len(self.symbols)} (vol>${MIN_VOL/1e6:.1f}M)")

    def _analyze_parallel(self, symbols_batch):
        results = []
        with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
            futures = {ex.submit(self.analyze, sym): sym for sym in symbols_batch}
            for fut in as_completed(futures):
                try:
                    sig = fut.result()
                    if sig: results.append((futures[fut], sig))
                except Exception as e:
                    log.debug(f"analyze error: {e}")
        results.sort(key=lambda x: x[1]['score'], reverse=True)
        return results

    def _nuke_zombie_orders(self) -> int:
        if not AUTO: return 0
        protected_ids = set()
        for sym in list(self.trades.keys()):
            d = api('GET', '/openApi/swap/v2/trade/openOrders', {'symbol': sym})
            for o in (d.get('data', {}).get('orders') or []):
                otype = str(o.get('type', '')).upper()
                if 'STOP' in otype or 'TRAILING' in otype:
                    oid = o.get('orderId')
                    if oid: protected_ids.add(str(oid))
        killed = 0; now_ms = int(time.time() * 1000)
        all_syms = set(self.symbols or [])
        try:
            d_pos = api('GET', '/openApi/swap/v2/user/positions', {})
            for p in (d_pos.get('data') or []):
                s = p.get('symbol', '')
                if s: all_syms.add(s)
        except: pass
        for sym in list(all_syms)[:80]:
            try:
                d = api('GET', '/openApi/swap/v2/trade/openOrders', {'symbol': sym})
                for o in (d.get('data', {}).get('orders') or []):
                    oid   = str(o.get('orderId', ''))
                    otype = str(o.get('type', '')).upper()
                    otime = int(o.get('time', now_ms) or now_ms)
                    age_min = (now_ms - otime) / 60000
                    if oid in protected_ids: continue
                    if (otype in ('LIMIT', 'TRIGGER', 'STOP', 'TAKE_PROFIT') and
                            (sym not in self.trades or age_min > ZOMBIE_MAX_AGE_MIN)):
                        r = api('DELETE', '/openApi/swap/v2/trade/order',
                                {'symbol': sym, 'orderId': oid})
                        if r.get('code') == 0: killed += 1
                        time.sleep(0.12)
            except: pass
        if killed: log.info(f"  🧟 Zombies eliminados: {killed}")
        self._last_zombie_clean = time.time()
        return killed

    def _update_market_regime(self):
        if not REGIME_CHECK: return
        c4h, *_ = self._klines('BTC-USDT', '4h', 10)
        if c4h and len(c4h) >= 4:
            self._btc_4h = (c4h[-1] - c4h[-4]) / c4h[-4] * 100
            if self._btc_4h < -BTC_4H_CRASH_PCT:
                if not self._regime_until or datetime.utcnow() > self._regime_until:
                    self._regime_until = datetime.utcnow() + timedelta(hours=BTC_4H_CRASH_PAUSE)
                    self._tg(f"<b>🚨 CRASH GUARD</b>\nBTC {self._btc_4h:.1f}% en 4h → Pausa {BTC_4H_CRASH_PAUSE}h")

        bulls = 0; total = 0
        for coin in BREADTH_COINS[:10]:
            try:
                c, *_ = self._klines(coin, '1h', 25)
                if c and len(c) >= 21:
                    e21 = ema(c, 21)
                    if c[-1] > e21: bulls += 1
                    total += 1
            except: pass
        if total > 0: self._breadth = bulls / total

        # v6.0: bear duro si breadth < 30%
        if self._breadth < BREADTH_BEAR_HARD:
            if self._regime != 'bear':
                log.warning(f"  🛑 BEAR FORZADO — Breadth {int(self._breadth*100)}%")
                self._tg(f"<b>🛑 BEAR FORZADO</b>\nBreadth {int(self._breadth*100)}% < {int(BREADTH_BEAR_HARD*100)}%")
            self._regime = 'bear'; return

        btc_bear    = (self._btc_4h < -1.5) or (self._btc_1h < -BTC_BLOCK)
        low_breadth = self._breadth < BREADTH_MIN  # v6.0: 50%

        if btc_bear and low_breadth:    nuevo = 'bear'
        elif btc_bear or low_breadth:   nuevo = 'caution'
        elif self._btc_4h > 1.0 and self._breadth > 0.65: nuevo = 'bull'
        else:                           nuevo = 'neutral'

        if nuevo != self._regime: log.info(f"  📊 RÉGIMEN: {self._regime} → {nuevo}")
        self._regime = nuevo

    def _regime_ok(self):
        if self._regime_until and datetime.utcnow() < self._regime_until:
            remaining = int((self._regime_until - datetime.utcnow()).total_seconds() / 60)
            return False, f"crash guard {remaining}min"
        if self._regime == 'bear':    return False, "régimen bajista"
        if CAUTION_BLOCK and self._regime == 'caution': return False, "régimen caution"
        return True, "ok"

    def _score_min_for_regime(self):
        if self._regime == 'bull': return max(self.learn.opt_score, SCORE_BULL)
        return max(self.learn.opt_score, SCORE_NEUTRAL)

    def _get_exchange_positions(self, symbol=None):
        params = {}
        if symbol: params['symbol'] = symbol
        d = api('GET', '/openApi/swap/v2/user/positions', params)
        result = defaultdict(lambda: {'long': 0.0, 'short': 0.0})
        for p in (d.get('data') or []):
            try:
                amt  = float(p.get('positionAmt', 0) or 0)
                sym  = p.get('symbol', '')
                side = str(p.get('positionSide', '')).upper()
                if not sym or abs(amt) == 0: continue
                if side == 'LONG' or (side == 'BOTH' and amt > 0): result[sym]['long'] = abs(amt)
                elif side == 'SHORT' or (side == 'BOTH' and amt < 0): result[sym]['short'] = abs(amt)
            except: continue
        return result

    def _has_any_position(self, symbol):
        pos = self._get_exchange_positions(symbol)
        return pos[symbol]['long'] > 0 or pos[symbol]['short'] > 0

    def _order_close_short(self, sym, qty):
        params = {'symbol': sym, 'side': 'BUY', 'type': 'MARKET', 'quantity': str(qty)}
        if self._mode == 'hedge': params['positionSide'] = 'SHORT'
        else: params['reduceOnly'] = 'true'
        return api('POST', '/openApi/swap/v2/trade/order', params)

    def _recover(self):
        """v6.0: FIX — ignora posiciones con leverage > LEVERAGE+1 (manuales)."""
        if not AUTO: return
        all_pos = self._get_exchange_positions(); n_rec = 0; n_sh = 0
        for sym, sides in all_pos.items():
            if sides['short'] > 0:
                log.warning(f"  ⚠️ SHORT huérfano: {sym} → cerrando")
                if self._order_close_short(sym, sides['short']).get('code') == 0: n_sh += 1
                time.sleep(0.5)
            if sides['long'] > 0 and sym not in self.trades:
                d2 = api('GET', '/openApi/swap/v2/user/positions', {'symbol': sym})
                entry = 0.0; lev_pos = 1.0
                for p in (d2.get('data') or []):
                    s2  = str(p.get('positionSide', '')).upper()
                    a2  = float(p.get('positionAmt', 0) or 0)
                    if (s2 == 'LONG' and abs(a2) > 0) or (s2 == 'BOTH' and a2 > 0):
                        entry   = float(p.get('avgPrice') or p.get('entryPrice') or 0)
                        lev_pos = float(p.get('leverage', LEVERAGE) or LEVERAGE)
                        break
                # v6.0 FIX: ignorar posiciones manuales con leverage diferente
                if lev_pos > LEVERAGE + 1:
                    log.info(f"  ⚠️ {sym} lev={lev_pos}x > {LEVERAGE+1}x → posición manual, ignorada")
                    continue
                if entry <= 0: continue
                qty    = sides['long']
                sl_rec = entry * (1 - SL_MAX_PCT / 100)
                self.trades[sym] = {
                    'entry': entry, 'qty_total': qty, 'qty_runner': qty,
                    'qty_tp1': round(qty * TP1_PCT/100, 6),
                    'qty_tp2': round(qty * TP2_PCT/100, 6),
                    'tp1_hit': False, 'tp2_hit': False,
                    'tp1_price': entry * (1 + TP1_RATIO * SL_MAX_PCT / 100),
                    'tp2_price': entry * (1 + TP2_RATIO * SL_MAX_PCT / 100),
                    'sl': sl_rec, 'sl_orig': sl_rec, 'sl_pct': SL_MAX_PCT,
                    'highest': entry, 'opened': datetime.now(),
                    'score': 0, 'ema25': entry, 'ema55': entry,
                    'aurolo_pts': 0, 'entrada_label': 'recovered',
                    'usdt': POS_SIZE, 'pnl_parcial': 0.0,
                    'factors': [], 'hora_utc': datetime.utcnow().hour,
                    'btc_dir': self._btc_dir(), 'debilidad_alertada': False,
                    'trailing_placed': False, 'trailing_sl': sl_rec,
                }
                n_rec += 1
                log.info(f"  ♻️ LONG recuperado: {sym} @ ${entry:.6f} ({lev_pos}x)")
        log.info(f"  Recuperadas: {n_rec} | SHORTs cerrados: {n_sh}")

    def _klines(self, symbol, interval='5m', limit=130):
        d = pub('/openApi/swap/v3/quote/klines',
                {'symbol': symbol, 'interval': interval, 'limit': limit})
        if d.get('code') == 0 and d.get('data'):
            kl = d['data']
            return ([float(k['close'])  for k in kl],
                    [float(k['high'])   for k in kl],
                    [float(k['low'])    for k in kl],
                    [float(k['volume']) for k in kl],
                    [float(k['open'])   for k in kl])
        return None, None, None, None, None

    def _ticker(self, sym):
        d = pub('/openApi/swap/v2/quote/ticker', {'symbol': sym})
        if d.get('code') == 0 and d.get('data'):
            t = d['data']
            return {'price': float(t.get('lastPrice', 0)),
                    'change': float(t.get('priceChangePercent', 0))}
        return None

    def _update_btc(self):
        c, *_ = self._klines('BTC-USDT', '1h', 4)
        if c and len(c) >= 2:
            self._btc_1h = (c[-1] - c[-2]) / c[-2] * 100
            self._btc_ok = self._btc_1h >= -BTC_BLOCK  # v6.0: más estricto
        else:
            self._btc_ok = True

    def _btc_dir(self):
        if self._btc_1h > 0.5:  return 'up'
        if self._btc_1h < -0.5: return 'down'
        return 'flat'

    def _update_equity(self):
        global ACCOUNT_EQUITY
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') == 0:
            b  = d.get('data', {})
            if isinstance(b, list):
                for item in b:
                    v = _safe_float(item)
                    if v > 0: ACCOUNT_EQUITY = v; break
            else:
                eq = _safe_float(b.get('equity', b.get('balance', 0)))
                if eq <= 0:
                    for _, val in b.items():
                        v = _safe_float(val)
                        if v > 0: eq = v; break
                if eq > 0: ACCOUNT_EQUITY = eq

    def _check_ltv(self):
        if not AUTO: return
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') != 0: return
        try:
            b   = d.get('data', {}); eq = _safe_float(b.get('equity', b.get('balance', 0)))
            mg  = _safe_float(b.get('usedMargin', b.get('initialMargin', 0)))
            ltv = mg / eq * 100 if eq > 0 else 0
            if eq > 0 and ltv >= LTV_WARN:
                self._tg("<b>⚠️ LTV ALTO — cerrando posiciones</b>")
                for sym in list(self.trades):
                    tk = self._ticker(sym)
                    if tk: self._close_all(sym, tk['price'], "LTV EMERGENCIA")
        except: pass

    # ── ANALYZE v6.0 — PRECISION GATE ────────────────────────────────────
    def analyze(self, symbol):
        """
        v6.0: Gate de calidad estricto.
        Requiere: Sesión + Régimen + BTC positivo + trend_4h=1 SIEMPRE
                  + Aurolo ≥2/3 + Vol ratio ≥1.5 + (OFI bull OR MTF15m bull)
                  + Score ≥75 (bull) / ≥85 (neutral)
        """
        if symbol in self.trades: return None
        if not self._cd_ok(symbol): return None
        if symbol in self._pending_orders: return None

        hora = datetime.utcnow().hour

        # v6.0: GATE 1 — Sesión estricta
        ses_ok, ses_reason = session_ok(hora)
        if not ses_ok:
            self.stats['filtered_session'] += 1
            return None

        # v6.0: GATE 2 — Régimen de mercado
        regime_ok, _ = self._regime_ok()
        if not regime_ok:
            self.stats['filtered_regime'] += 1
            return None

        # v6.0: GATE 3 — BTC positivo (no solo "no muy negativo")
        if not self._btc_ok: return None
        if self._cb_active: return None

        # v6.0: GATE 4 — Breadth suficiente
        if self._breadth < BREADTH_MIN: return None

        hora_ok, _ = self.learn.hora_ok(hora)
        if not hora_ok: return None

        # v6.0: GATE 5 — Máximo trades diarios
        if self._daily_trades >= MAX_DAILY_TRADES: return None

        c5, h5, l5, v5, o5 = self._klines(symbol, '5m', 130)
        if not c5 or len(c5) < AUROLO_EMA_LEN + 50: return None

        tk = self._ticker(symbol)
        if not tk or tk['price'] <= 0: return None
        price    = tk['price']
        change_24 = tk['change']

        # v6.0: Filtro change_24h estricto
        if change_24 > 12.0 or change_24 < -5.0: return None

        # v6.0: GATE 6 — Stop hunt detection
        if STOP_HUNT_DETECT:
            hunt, _ = detect_stop_hunt(c5, h5, l5, v5)
            if hunt: return None

        # v6.0: GATE 7 — Trend 1h y 4h (AMBOS requeridos)
        c1h, h1h, l1h, v1h, _ = self._klines(symbol, '1h', 50)
        c4h, h4h, l4h, v4h, _ = self._klines(symbol, '4h', 30)

        trend_1h = 0; rsi_1h = 50.0
        if c1h and len(c1h) >= 25:
            e9_1h = ema(c1h, 9); e21_1h = ema(c1h, 21)
            rsi_1h = rsi(c1h, 14)
            if e9_1h > e21_1h: trend_1h = 1
            elif e9_1h < e21_1h: trend_1h = -1

        # v6.0: 1h debe ser alcista
        if trend_1h != 1: return None

        # v6.0: RSI 1h no sobrecomprado
        if rsi_1h > 65: return None

        trend_4h = 0
        if c4h and len(c4h) >= 21:
            e9_4h = ema(c4h, 9); e21_4h = ema(c4h, 21)
            if e9_4h > e21_4h: trend_4h = 1
            elif e9_4h < e21_4h: trend_4h = -1

        # v6.0: 4h SIEMPRE debe ser alcista (no solo en neutral)
        if trend_4h != 1: return None

        atr_v   = atr_calc(h5, l5, c5, 14)
        atr_pct = atr_v / price * 100 if price > 0 else 0
        if atr_pct < 0.10 or atr_pct > ATR_HIGH_PCT * 1.5: return None

        sig_aurolo = aurolo_signal(c5, h5, l5, v5, o5, atr_v)

        # v6.0: Aurolo mínimo + vol ratio
        if sig_aurolo['puntos'] < AUROLO_MIN_PTS: return None
        if sig_aurolo['cambio_tend']: return None
        if sig_aurolo['vol_ratio'] < VOL_RATIO_MIN: return None

        vwap_val, precio_sobre_vwap = vwap_contexto(c5, h5, l5, v5, VWAP_CANDLES)
        if VWAP_AS_FILTER and not precio_sobre_vwap: return None

        sl_price = sig_aurolo['sl_price']
        sl_pct   = sig_aurolo['sl_pct']
        if sl_pct < SL_MIN_PCT * 0.9 or sl_pct > SL_MAX_PCT * 1.1: return None

        tp1_price = price * (1 + sl_pct * TP1_RATIO / 100)
        tp2_price = price * (1 + sl_pct * TP2_RATIO / 100)
        tp_ref    = max(sl_pct * MIN_RR, TP_MIN, atr_pct * ATR_TP_M)
        rr        = tp_ref / sl_pct if sl_pct > 0 else 0
        if rr < MIN_RR * 0.80: return None

        # v6.0: TP1 neto debe cubrir comisiones taker con margen
        tp1_neto = sl_pct * TP1_RATIO - FEE_COST_PCT
        if tp1_neto < 0.6: return None  # v6.0: era 0.3 — margen real

        # Smart Money
        ofi_ratio, ofi_desc = order_flow_imbalance(c5, o5, v5, n=10)

        trend_15m = 0
        try:
            c15, h15, l15, v15, o15 = self._klines(symbol, '15m', 40)
            if c15 and len(c15) >= 25:
                e9_15 = ema(c15, 9); e21_15 = ema(c15, 21)
                trend_15m = 1 if e9_15 > e21_15 else -1
        except: pass

        # v6.0: GATE 8 — Requiere al menos OFI alcista O MTF15m alcista
        ofi_bull = ofi_ratio >= OFI_BULL_THRESH
        mtf_bull = trend_15m == 1
        if not ofi_bull and not mtf_bull: return None

        # ── SCORING ──────────────────────────────────────────────────────
        score = 0; reasons = []; factors = []
        pts   = sig_aurolo['puntos']

        # Base Aurolo
        if pts == 3:   score += 55; reasons.append("Aurolo3/3(55)"); factors.append("aurolo_3")
        elif pts == 2: score += 35; reasons.append("Aurolo2/3(35)"); factors.append("aurolo_2")

        if sig_aurolo['p1']: score += 10; factors.append("p1_tend")
        if sig_aurolo['p2']: score += 10; factors.append("p2_wt")
        if sig_aurolo['p3']: score += 10; factors.append("p3_adx")

        wt_val = sig_aurolo['wt_now']
        if wt_val <= WT_OS1:   score += 8; factors.append("wt_deep")
        elif wt_val <= WT_OS2: score += 4; factors.append("wt_os")

        adx_val = sig_aurolo['adx_now']
        if adx_val > ADX_KEY * 1.4: score += 6; factors.append("adx_strong")

        vr = sig_aurolo['vol_ratio']
        if vr >= 2.5:   score += 12; factors.append("vol_fuerte")
        elif vr >= 1.8: score += 7;  factors.append("vol_medio")
        elif vr >= 1.5: score += 3;  factors.append("vol_ok")

        if precio_sobre_vwap: score += 8; factors.append("vwap_arriba")

        # Trend (1h y 4h ya validados como +1)
        score += 12; factors.append("trend_1h_up")
        score += 10; factors.append("trend_4h_up")

        # MTF 15m
        if mtf_bull:  score += MTF_SCORE_BONUS; factors.append("mtf_15m_bull"); reasons.append(f"MTF15m(+{int(MTF_SCORE_BONUS)})")
        else:         score -= 5;               factors.append("mtf_15m_miss")

        # OFI
        if ofi_bull:  score += OFI_SCORE_BONUS; factors.append("ofi_bull"); reasons.append(f"OFI({int(ofi_ratio*100)}%)")
        else:         score -= 5;               factors.append("ofi_miss")

        # Régimen
        if self._regime == 'bull':    score += 12; factors.append("regime_bull")
        elif self._regime == 'neutral': score += 5; factors.append("regime_neutral")

        # BTC
        if self._btc_1h > 1.0:    score += 8; factors.append("btc_up")
        elif self._btc_1h > 0.3:  score += 4; factors.append("btc_ok")

        if self._btc_4h > 1.5:   score += 8; factors.append("btc4h_up")

        # RSI
        if rsi_1h < 40:   score += 8; factors.append("rsi_1h_os")
        elif rsi_1h < 55: score += 4; factors.append("rsi_1h_ok")

        # Breadth
        if self._breadth > 0.70:   score += 10; factors.append("breadth_great")
        elif self._breadth > 0.55: score += 5;  factors.append("breadth_good")

        # SL ajustado
        if sl_pct < SL_MAX_PCT * 0.5: score += 6; factors.append("sl_tight")

        # Aprendizaje
        bonus_p = self.learn.bonus_pts(pts)
        if bonus_p != 0: score += bonus_p
        adj = self.learn.adj(factors)
        if adj != 0: score += adj

        score_min = self._score_min_for_regime()
        if score < score_min:
            self.stats['filtered_score'] += 1
            log.debug(f"  {symbol}: score {int(score)}<{int(score_min)}")
            return None

        ok, reason = self.learn.ok(symbol, score)
        if not ok: return None

        # Anti-hunt SL
        sl_price = sl_anti_hunt(sl_price, price)
        sl_pct   = (price - sl_price) / price * 100

        # Size multiplier si ATR alto
        size_mult = 0.5 if atr_pct > ATR_HIGH_PCT else 1.0

        return {
            'price': price, 'change': change_24, 'score': score, 'score_min': score_min,
            'aurolo_pts': pts, 'aurolo_p1': sig_aurolo['p1'],
            'aurolo_p2': sig_aurolo['p2'], 'aurolo_p3': sig_aurolo['p3'],
            'aurolo_wt': sig_aurolo['wt_now'], 'aurolo_adx': sig_aurolo['adx_now'],
            'aurolo_desc': sig_aurolo['descripcion'], 'aurolo_señal': sig_aurolo['señal'],
            'sl_price': round(sl_price, 8), 'sl_pct': round(sl_pct, 3),
            'tp1_price': round(tp1_price, 8), 'tp2_price': round(tp2_price, 8),
            'tp_pct': round(tp_ref, 2), 'rr': round(rr, 2), 'tp1_neto': round(tp1_neto, 3),
            'vwap': vwap_val, 'ema25': ema(c5, 25), 'ema55': sig_aurolo['ema55'],
            'trend_1h': trend_1h, 'trend_4h': trend_4h, 'trend_15m': trend_15m,
            'rsi_1h': rsi_1h, 'ofi_ratio': ofi_ratio, 'ofi_desc': ofi_desc,
            'vol_ratio': vr, 'atr_pct': atr_pct, 'size_mult': size_mult,
            'reasons': ' | '.join(reasons), 'factors': factors,
            'hora_utc': hora, 'btc_dir': self._btc_dir(),
            'precio_sobre_vwap': precio_sobre_vwap,
            'regime': self._regime, 'breadth': self._breadth,
        }

    def _set_lev(self, sym):
        for side in ('LONG', 'SHORT'):
            try: api('POST', '/openApi/swap/v2/trade/leverage',
                     {'symbol': sym, 'side': side, 'leverage': str(LEVERAGE)})
            except: pass

    def _calc_qty(self, sym, price, sl_price, size_mult=1.0):
        info  = self._contracts.get(sym, {'step': 1, 'prec': 2, 'ctval': 1})
        step  = max(float(info.get('step', 1)), 1e-6)
        prec  = int(info.get('prec', 2))
        ctval = max(float(info.get('ctval', 1)), 1e-9)
        ppc   = price * ctval
        if ppc <= 0: return None, 0
        dist_pct = (price - sl_price) / price * 100 if sl_price < price else SL_MIN_PCT
        riesgo   = ACCOUNT_EQUITY * (RISK_PCT / 100)
        notional = min(riesgo / (dist_pct / 100), POS_SIZE * LEVERAGE) * size_mult
        notional = max(notional, MIN_TRADE)
        qty = math.ceil((notional / ppc) / step) * step
        qty = round(qty, prec); val = qty * ppc
        for _ in range(200):
            if val >= MIN_TRADE: break
            qty += step; qty = round(qty, prec); val = qty * ppc
        return (qty, round(val, 4)) if val >= MIN_TRADE else (None, 0)

    def _order(self, sym, side, qty, otype='MARKET', price=None, stop_price=None,
               reduce_only=False, activation_price=None, price_rate=None):
        params = {'symbol': sym, 'side': side.upper(), 'type': otype, 'quantity': str(qty)}
        if self._mode == 'hedge': params['positionSide'] = 'LONG'
        else:
            if side.upper() == 'SELL' or reduce_only: params['reduceOnly'] = 'true'
        if price:            params['price'] = str(round(price, 8)); params['timeInForce'] = 'GTC'
        if stop_price:       params['stopPrice'] = str(round(stop_price, 8))
        if activation_price: params['activationPrice'] = str(round(activation_price, 8))
        if price_rate:       params['priceRate'] = str(price_rate)
        return api('POST', '/openApi/swap/v2/trade/order', params)

    def _confirm_pos(self, sym, timeout=15):
        for _ in range(timeout):
            d = api('GET', '/openApi/swap/v2/user/positions', {'symbol': sym})
            for p in (d.get('data') or []):
                amt  = float(p.get('positionAmt', 0) or 0)
                side = str(p.get('positionSide', '')).upper()
                if (side == 'LONG' and abs(amt) > 0) or (side == 'BOTH' and amt > 0):
                    return abs(amt), float(p.get('avgPrice') or p.get('entryPrice') or 0)
            time.sleep(1)
        return None, None

    def _cancel_open(self, sym):
        d = api('GET', '/openApi/swap/v2/trade/openOrders', {'symbol': sym})
        for o in (d.get('data', {}).get('orders') or []):
            oid = o.get('orderId')
            if oid:
                api('DELETE', '/openApi/swap/v2/trade/order', {'symbol': sym, 'orderId': str(oid)})
                time.sleep(0.1)

    def _place_sl(self, sym, qty, sl_price):
        d = self._order(sym, 'SELL', qty, 'STOP_MARKET', stop_price=sl_price)
        if d.get('code') == 0: return True
        d = self._order(sym, 'SELL', qty, 'STOP',
                        price=sl_price * 0.999, stop_price=sl_price)
        if d.get('code') == 0: return True
        d = self._order(sym, 'SELL', qty, 'STOP_MARKET', stop_price=sl_price * 0.998)
        return d.get('code') == 0

    def _place_trailing_stop(self, sym, qty, activation_price, trail_rate_pct):
        params = {'symbol': sym, 'side': 'SELL', 'type': 'TRAILING_STOP_MARKET',
                  'quantity': str(qty), 'activationPrice': str(round(activation_price, 8)),
                  'priceRate': str(trail_rate_pct)}
        if self._mode == 'hedge': params['positionSide'] = 'LONG'
        else: params['reduceOnly'] = 'true'
        d = api('POST', '/openApi/swap/v2/trade/order', params)
        return d.get('code') == 0

    def _chase_limit_entry(self, sym, qty):
        d = pub('/openApi/swap/v2/quote/bookTicker', {'symbol': sym})
        ask_price = None
        if d.get('code') == 0 and d.get('data'):
            ask_price = float(d['data'].get('askPrice', 0) or 0)
        if not ask_price or ask_price <= 0:
            tk = self._ticker(sym)
            if tk: ask_price = tk['price'] * 1.0002
        if not ask_price: return None, None
        limit_price = round(ask_price * 1.0005, 8)
        d = self._order(sym, 'BUY', qty, 'LIMIT', price=limit_price)
        if d.get('code') != 0:
            d = self._order(sym, 'BUY', qty, 'MARKET')
            if d.get('code') != 0: return None, None
        for _ in range(12):
            time.sleep(1)
            fq, fp = self._confirm_pos(sym, 1)
            if fq and fp: return fq, fp
        self._cancel_open(sym)
        time.sleep(0.5)
        fq, fp = self._confirm_pos(sym, 2)
        if fq: return fq, fp
        dm = self._order(sym, 'BUY', qty, 'MARKET')
        if dm.get('code') == 0: return self._confirm_pos(sym, 10)
        return None, None

    def open_trade(self, sym, sig):
        if not AUTO or sym in self.trades: return False
        if LongBot._opening or len(self.trades) >= MAX_TRADES: return False
        if self._daily_trades >= MAX_DAILY_TRADES: return False
        if sym in self._pending_orders: return False
        if self._has_any_position(sym):
            log.warning(f"  ⛔ {sym} ya tiene posición"); return False
        LongBot._opening = True
        try: return self._open(sym, sig)
        finally: LongBot._opening = False

    def _open(self, sym, sig):
        price    = sig['price']
        sl_price = sig['sl_price']
        pts      = sig['aurolo_pts']
        label    = sig['aurolo_señal']
        size_mult= sig.get('size_mult', 1.0)

        log.info(f"\n  🎯 LONG {sym} [{label}] | Score:{int(sig['score'])}/{int(sig['score_min'])} | RR:{sig['rr']:.2f}:1")
        self._set_lev(sym); time.sleep(0.2)
        qty, notional = self._calc_qty(sym, price, sl_price, size_mult)
        if not qty: return False

        if size_mult < 1.0:
            log.info(f"  ⚠️ Volatilidad alta ATR={sig.get('atr_pct',0):.1f}% → size x{size_mult}")

        self._pending_orders[sym] = 'pending'
        filled_qty, fill_price = self._chase_limit_entry(sym, qty)
        if not filled_qty or not fill_price:
            log.error(f"  ❌ No se pudo abrir {sym}")
            self._pending_orders.pop(sym, None)
            return False

        sl_pct_real = sig['sl_pct']
        sl_real     = fill_price * (1 - sl_pct_real / 100)
        sl_real     = sl_anti_hunt(sl_real, fill_price)
        sl_real     = max(sl_real, fill_price * (1 - SL_MAX_PCT / 100))
        sl_real     = min(sl_real, fill_price * (1 - SL_MIN_PCT / 100))

        tp1_price = fill_price * (1 + sl_pct_real * TP1_RATIO / 100)
        tp2_price = fill_price * (1 + sl_pct_real * TP2_RATIO / 100)

        sl_ok = self._place_sl(sym, filled_qty, sl_real)
        if not sl_ok:
            time.sleep(2); sl_ok = self._place_sl(sym, filled_qty, sl_real)
        if not sl_ok:
            log.error("  ❌ SL crítico — cerrando posición")
            self._order(sym, 'SELL', filled_qty, 'MARKET')
            self._pending_orders.pop(sym, None)
            return False

        trailing_placed = False
        if USE_TRAILING_EXIT:
            activation = fill_price * (1 + TRAIL_ACTIVATION / 100)
            trailing_placed = self._place_trailing_stop(sym, filled_qty, activation, TRAIL_RATE_PCT)
            if not trailing_placed:
                log.info(f"  🔧 Trailing Exchange rechazado — gestión manual activada")

        trade = {
            'entry': fill_price, 'qty_total': filled_qty, 'qty_runner': filled_qty,
            'qty_tp1': round(filled_qty * TP1_PCT/100, 6),
            'qty_tp2': round(filled_qty * TP2_PCT/100, 6),
            'tp1_hit': False, 'tp2_hit': False,
            'tp1_price': tp1_price, 'tp2_price': tp2_price,
            'sl': sl_real, 'sl_orig': sl_real, 'sl_pct': sl_pct_real,
            'trailing_sl': sl_real,   # v6.0: tracking del trailing manual
            'highest': fill_price, 'opened': datetime.now(),
            'score': sig['score'], 'ema25': sig['ema25'], 'ema55': sig['ema55'],
            'aurolo_pts': pts, 'entrada_label': label,
            'vwap': sig['vwap'], 'usdt': POS_SIZE, 'pnl_parcial': 0.0,
            'factors': sig['factors'], 'hora_utc': sig['hora_utc'],
            'btc_dir': sig['btc_dir'], 'debilidad_alertada': False,
            'trailing_placed': trailing_placed, 'size_mult': size_mult,
        }
        self.trades[sym] = trade
        self._pending_orders.pop(sym, None)
        self.stats['exec'] += 1
        self.stats['fees'] += notional * FEE_TAKER
        self._daily_trades += 1

        p1 = "✅" if sig['aurolo_p1'] else "❌"
        p2 = "✅" if sig['aurolo_p2'] else "❌"
        p3 = "✅" if sig['aurolo_p3'] else "❌"
        size_tag = f" [x{size_mult}]" if size_mult < 1.0 else ""

        self._tg(
            f"<b>🟢 LONG [{label}]</b> — <b>{sym}</b>{size_tag}\n"
            f"Score: {int(sig['score'])}/{int(sig['score_min'])} | RR: {sig['rr']:.2f}:1 | {sig['regime']}\n"
            f"{p1} P1 EMA55  {p2} P2 WT:{sig['aurolo_wt']:.1f}  {p3} P3 ADX:{sig['aurolo_adx']:.1f}\n"
            f"OFI: {sig.get('ofi_desc','?')} | MTF15m: {'✅' if sig.get('trend_15m')==1 else '❌'}\n"
            f"📍 ${fill_price:.6f} | SL: ${sl_real:.6f} (-{sl_pct_real:.2f}%)\n"
            f"TP1: ${tp1_price:.6f} (+{sl_pct_real*TP1_RATIO:.2f}%) | TP2: ${tp2_price:.6f}\n"
            f"Trailing: {'✅ Exchange' if trailing_placed else '🔧 Manual'} | BTC 1h: {self._btc_1h:+.2f}%"
        )
        return True

    def _close_partial(self, sym, qty, exit_price, label):
        if qty <= 0: return 0
        d = self._order(sym, 'SELL', qty, 'MARKET')
        if d.get('code') != 0: return 0
        t = self.trades[sym]
        chg  = (exit_price - t['entry']) / t['entry']
        frac = qty / t['qty_total']
        net  = POS_SIZE * LEVERAGE * chg * frac - POS_SIZE * LEVERAGE * FEE_TAKER * 2 * frac
        t['pnl_parcial'] += net; t['qty_runner'] -= qty
        self.stats['fees']  += POS_SIZE * LEVERAGE * FEE_TAKER * 2 * frac
        self._daily_pnl     += net; self.stats['pnl'] += net
        log.info(f"  💰 {label} {sym}: ${net:+.4f}")
        self._tg(f"<b>💰 {label}</b> — {sym}\n${exit_price:.6f}\nPnL parcial: ${net:+.4f}")
        return net

    def _close_all(self, sym, exit_price, reason):
        if sym not in self.trades: return False
        t = self.trades[sym]
        qty_rem = t['qty_runner']
        if qty_rem > 0: self._order(sym, 'SELL', qty_rem, 'MARKET')
        frac_r = qty_rem / t['qty_total'] if t['qty_total'] > 0 else 0
        chg_r  = (exit_price - t['entry']) / t['entry']
        net_r  = POS_SIZE * LEVERAGE * chg_r * frac_r - POS_SIZE * LEVERAGE * FEE_TAKER * 2 * frac_r
        net_total = t['pnl_parcial'] + net_r
        win = net_total > 0

        self.stats['closed'] += 1; self.stats['pnl'] += net_r
        self.stats['fees']   += POS_SIZE * LEVERAGE * FEE_TAKER * 2 * frac_r
        self._daily_pnl      += net_r
        if win: self.stats['wins'] += 1
        else:   self.stats['losses'] += 1

        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now() - t['opened']).total_seconds() / 60)

        log.info(f"  {'✅' if win else '❌'} {reason} | ${net_total:+.4f} | {mins}min | WR:{wr:.0f}%")

        self.learn.record(
            symbol=sym, score=t['score'], pnl=net_total, win=win,
            hora_utc=t.get('hora_utc', datetime.utcnow().hour),
            pts_aurolo=t.get('aurolo_pts', 0), btc_dir=t.get('btc_dir', 'flat'),
            reason=reason, factors=t.get('factors', []),
        )

        if 'STOP LOSS' in reason or 'SL' in reason:
            hours = CD_SL_FAST_HOURS if mins < CD_SL_FAST_MIN else CD_SL // 60
            self._cooldowns[sym] = (time.time() + hours * 3600, 'SL')
        else:
            self._cooldowns[sym] = (time.time() + CD_TP * 60, 'TP')

        self._tg(
            f"<b>{'✅' if win else '❌'} CERRADO — {reason}</b>\n"
            f"<b>{sym}</b> | {mins}min\n"
            f"${t['entry']:.6f} → ${exit_price:.6f}\n"
            f"<b>PnL: ${net_total:+.4f} | WR: {wr:.0f}%</b>\n"
            f"Trades hoy: {self._daily_trades}/{MAX_DAILY_TRADES}"
        )
        if self.stats['closed'] % 3 == 0: self.learn.save()
        del self.trades[sym]
        self._cancel_open(sym)
        return True

    async def monitor(self):
        for sym in list(self.trades.keys()):
            try:
                t  = self.trades[sym]
                tk = self._ticker(sym)
                if not tk: continue
                cur = tk['price']

                c5, h5, l5, v5, _ = self._klines(sym, '5m', 80)
                if c5:
                    t['ema25'] = ema(c5, 25)
                    t['ema55'] = ema(c5, AUROLO_EMA_LEN)

                if c5 and h5 and l5 and not t.get('debilidad_alertada', False):
                    atr_live = atr_calc(h5, l5, c5, 14)
                    sig_live = aurolo_signal(c5, h5, l5, v5 or [1]*len(c5), c5, atr_live)
                    if sig_live['debilidad']:
                        t['debilidad_alertada'] = True
                        self._tg(f"<b>⚠️ DEBILIDAD — {sym}</b>\nConsiderar cierre manual")
                    # Cambio de tendencia con beneficio → cerrar
                    if sig_live['cambio_tend'] and (cur - t['entry']) / t['entry'] * 100 > 0.2:
                        self._close_all(sym, cur, "CAMBIO TENDENCIA"); continue

                if cur > t['highest']: t['highest'] = cur

                # Trailing stop exchange (si se colocó)
                if t.get('trailing_placed') and USE_TRAILING_EXIT:
                    if cur <= t['sl']:
                        self._close_all(sym, cur, "STOP LOSS"); continue
                    continue  # El exchange gestiona el trailing

                # v6.0: Trailing manual robusto
                profit_pct = (cur - t['entry']) / t['entry'] * 100
                if USE_TRAILING_EXIT and profit_pct >= TRAIL_ACTIVATION:
                    new_trail_sl = cur * (1 - TRAIL_RATE_PCT / 100)
                    if new_trail_sl > t['trailing_sl']:
                        old_sl = t['trailing_sl']
                        t['trailing_sl'] = new_trail_sl
                        t['sl'] = new_trail_sl
                        # Actualizar SL en exchange
                        self._cancel_open(sym)
                        placed = self._place_sl(sym, t['qty_runner'], new_trail_sl)
                        log.info(f"  🔧 Trail manual {sym}: ${old_sl:.6f}→${new_trail_sl:.6f} ({'✅' if placed else '❌'})")

                # TP1
                if not t['tp1_hit'] and cur >= t['tp1_price']:
                    self._close_partial(sym, t['qty_tp1'], cur, f"TP1({int(TP1_PCT)}%)")
                    t['tp1_hit'] = True
                    be = t['entry'] * 1.001  # breakeven +0.1%
                    if be > t['sl']:
                        t['sl'] = be; t['trailing_sl'] = be
                        self._cancel_open(sym)
                        self._place_sl(sym, t['qty_runner'], be)
                    continue

                # TP2
                if t['tp1_hit'] and not t['tp2_hit'] and cur >= t['tp2_price']:
                    self._close_partial(sym, t['qty_tp2'], cur, f"TP2({int(TP2_PCT)}%)")
                    t['tp2_hit'] = True; continue

                # SL
                if cur <= t['sl']:
                    self._close_all(sym, cur, "STOP LOSS")

            except Exception as e:
                log.debug(f"monitor {sym}: {e}")

    def _cd_ok(self, sym):
        ts = self._cooldowns.get(sym)
        if not ts: return True
        resume = ts[0] if isinstance(ts, tuple) else ts
        if time.time() >= resume: del self._cooldowns[sym]; return True
        return False

    def _daily_reset(self):
        today = datetime.utcnow().date()
        if today != self._daily_date:
            old_trades = self._daily_trades
            self._daily_pnl    = 0.0
            self._daily_date   = today
            self._daily_trades = 0
            self._cb_active    = False
            self._cb_until     = None
            self.learn.streak  = 0
            self._update_equity()
            self._equity_start = ACCOUNT_EQUITY
            log.info(f"📅 Nuevo día — reset diario | Trades ayer: {old_trades}")

    def _circuit_check(self):
        self._daily_reset()
        if self._cb_active:
            if self._cb_until and datetime.utcnow() > self._cb_until:
                self._cb_active = False; self._daily_pnl = 0.0
                log.info("  🔓 Circuit breaker OFF")
            return self._cb_active
        if self._equity_start > 0:
            eq_loss_pct = abs(self._daily_pnl) / self._equity_start * 100
            if self._daily_pnl < 0 and eq_loss_pct > DAILY_LOSS_CAP_PCT:
                self._cb_active = True
                self._cb_until  = datetime.utcnow() + timedelta(hours=CB_HOURS)
                self._tg(f"<b>🔒 DAILY LOSS CAP</b>\n{eq_loss_pct:.1f}% | Pausa {CB_HOURS}h")
                return True
        cb_threshold = ACCOUNT_EQUITY * (CB_PCT / 100)
        if self._daily_pnl < -cb_threshold:
            self._cb_active = True
            self._cb_until  = datetime.utcnow() + timedelta(hours=CB_HOURS)
            self._tg(f"<b>🔒 CIRCUIT BREAKER</b>\n${self._daily_pnl:.3f} | Pausa {CB_HOURS}h")
        return self._cb_active

    def _report(self):
        if datetime.now() - self._last_report < timedelta(hours=2): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        pos   = ""
        for sym, t in self.trades.items():
            tk  = self._ticker(sym); cur = tk['price'] if tk else t['entry']
            pct = (cur - t['entry']) / t['entry'] * 100
            pos += f"  {'✅' if pct > 0 else '📌'} {sym}[{t['aurolo_pts']}/3]: {pct:+.2f}%\n"
        fees_total = self.stats['fees']
        self._tg(
            f"<b>📊 Reporte v6.0 PRECISION</b>\n"
            f"PnL: ${self.stats['pnl']:+.4f} | Fees: ${fees_total:.4f}\n"
            f"WR: {wr:.0f}% ({total}t) | Hoy: {self._daily_trades}/{MAX_DAILY_TRADES}\n"
            f"Régimen: {self._regime} | Breadth: {int(self._breadth*100)}%\n"
            f"Filtrados — sesión:{self.stats['filtered_session']} "
            f"régimen:{self.stats['filtered_regime']} score:{self.stats['filtered_score']}\n"
            + (pos if pos else "  Sin posiciones\n")
        )
        # Reset contadores de filtros
        self.stats['filtered_session'] = 0
        self.stats['filtered_regime']  = 0
        self.stats['filtered_score']   = 0

    def _tg(self, msg):
        try:
            if TG_TOKEN and TG_CHAT:
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=6
                )
        except: pass

    async def run(self):
        log.info(f"\n🚀 Bot LONGS v6.0 PRECISION | {len(self.symbols)} símbolos | {SCAN_WORKERS} workers\n")
        iteration = 0
        last_sym = last_ltv = last_hedge = last_eq = last_regime = 0

        while True:
            try:
                iteration += 1; self._daily_reset()
                if time.time() - last_sym    > 600:  self._refresh_symbols();    last_sym    = time.time()
                if time.time() - last_ltv    > 300:  self._check_ltv();          last_ltv    = time.time()
                if time.time() - last_eq     > 1800: self._update_equity();      last_eq     = time.time()
                if time.time() - last_regime > 300:  self._update_market_regime(); last_regime = time.time()
                if time.time() - last_hedge  > 600:
                    for sym, sides in self._get_exchange_positions().items():
                        if sides['short'] > 0:
                            self._order_close_short(sym, sides['short']); time.sleep(0.3)
                    last_hedge = time.time()
                if time.time() - self._last_zombie_clean > ZOMBIE_CLEANUP_MIN * 60:
                    self._nuke_zombie_orders()

                self._update_btc()
                if self._circuit_check():
                    await asyncio.sleep(INTERVAL); continue

                # v6.0: chequeo de sesión activa para el ciclo
                hora_actual = datetime.utcnow().hour
                en_sesion, ses_name = session_ok(hora_actual)

                total     = self.stats['wins'] + self.stats['losses']
                wr        = self.stats['wins'] / total * 100 if total else 0
                score_min = self._score_min_for_regime()

                log.info(f"\n{'='*72}")
                log.info(
                    f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                    f"Abiertos:{len(self.trades)}/{MAX_TRADES} | "
                    f"Hoy:{self._daily_trades}/{MAX_DAILY_TRADES} | "
                    f"PnL:${self.stats['pnl']:+.4f} | WR:{wr:.0f}%"
                )
                log.info(
                    f"  BTC1h:{self._btc_1h:+.2f}% BTC4h:{self._btc_4h:+.2f}% | "
                    f"Régimen:{self._regime} | Breadth:{int(self._breadth*100)}% | "
                    f"Score≥{int(score_min)} | Sesión:{ses_name if en_sesion else '⏸️ fuera'}"
                )
                log.info(f"{'='*72}\n")

                await self.monitor()
                self._report()

                if (len(self.trades) < MAX_TRADES and
                        self._daily_trades < MAX_DAILY_TRADES and
                        en_sesion):

                    regime_ok, regime_reason = self._regime_ok()
                    if not regime_ok:
                        log.info(f"  ⏸️ {regime_reason}")
                        await asyncio.sleep(INTERVAL); continue

                    log.info(f"  🔍 Scan: {len(self.symbols)} símbolos | {SCAN_WORKERS} workers...")
                    signals = self._analyze_parallel(self.symbols)
                    log.info(f"  ✅ {len(signals)} señales de alta calidad")

                    for sym, sig in signals:
                        if len(self.trades) >= MAX_TRADES: break
                        if self._daily_trades >= MAX_DAILY_TRADES: break
                        log.info(
                            f"  💡 {sym} [{sig['aurolo_señal']}] | "
                            f"Score:{int(sig['score'])}/{int(sig['score_min'])} | "
                            f"RR:{sig['rr']:.2f}:1 | SL:{sig['sl_pct']:.2f}% | "
                            f"OFI:{int(sig.get('ofi_ratio',0.5)*100)}% | "
                            f"MTF:{'✅' if sig.get('trend_15m')==1 else '❌'}"
                        )
                        if self.open_trade(sym, sig):
                            await asyncio.sleep(3)
                elif not en_sesion:
                    log.info(f"  ⏸️ Fuera de sesión ({hora_actual}h UTC) — solo monitoreando")
                else:
                    log.info("  ⏸️ Max trades o max diario — monitoreando")

                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("⏹️ Detenido"); break
            except Exception as e:
                log.error(f"❌ Error #{iteration}: {e}", exc_info=True)
                await asyncio.sleep(20)

        self.learn.save()


async def main():
    bot = LongBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Bot terminado")
