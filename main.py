#!/usr/bin/env python3
"""
BOT LONGS v5.6 — MLP Tactical Bridge (Aurolo) — OPTIMIZADO PARA RENTABILIDAD
════════════════════════════════════════════════════════════════════════════════

MEJORAS v5.6 vs v5.5:
  1. Learning Score — techo dinámico por nº de trades (evita parálisis)
  2. Aurolo P1 — zona ampliada a 6 velas, condición rebote suavizada
  3. Aurolo P2 — WaveTrend acepta cruces desde zona neutral-baja (no solo OS)
  4. VWAP — filtro suavizado: penaliza score en lugar de bloquear
  5. Cooldowns — reducidos y configurables desde .env
  6. Circuit breaker — umbral relativo al equity (no absoluto)
  7. SL inteligente — capped correctamente, nunca < 0.5% ni > SL_MAX_PCT
  8. Blacklist — requiere mínimo 8 trades antes de aplicar
  9. SKIP_HOURS — configurable desde .env
 10. Score logging — muestra por qué se rechazan señales (debug)
 11. Trailing SL post-TP1 — sube el SL al mínimo reciente, no solo break-even
 12. Monitor frecuencia — reduce latencia de salidas a EMA25
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
from datetime import datetime, timedelta
from urllib.parse import urlencode
from collections import defaultdict

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
MAX_TRADES     = clean('MAX_OPEN_TRADES',      '3',     'int')
RISK_PCT       = clean('RISK_PCT',             '1.0',   'float')
ACCOUNT_EQUITY = clean('ACCOUNT_EQUITY',       '100',   'float')

# ── TPs Escalonados ────────────────────────────────────────────────────────
TP1_PCT   = clean('TP1_PCT',   '40',  'float')
TP2_PCT   = clean('TP2_PCT',   '35',  'float')
TP1_RATIO = clean('TP1_RATIO', '1.0', 'float')
TP2_RATIO = clean('TP2_RATIO', '2.0', 'float')

# ── TP/SL ──────────────────────────────────────────────────────────────────
TP_MIN    = clean('TAKE_PROFIT_PCT', '2.0',  'float')
ATR_TP_M  = clean('ATR_TP_MULT',    '2.5',  'float')
MIN_RR    = clean('MIN_RR',         '1.5',  'float')
SL_ATR_M  = clean('SL_ATR_MULT',   '1.0',  'float')
SL_MAX_PCT = clean('SL_MAX_PCT',   '3.0',  'float')
SL_MIN_PCT = clean('SL_MIN_PCT',   '0.5',  'float')

# ── Filtros de contexto ────────────────────────────────────────────────────
MIN_VOL   = clean('MIN_VOLUME_24H',     '500000', 'float')
MAX_SYMS  = clean('MAX_SYMBOLS',        '60',     'int')
MIN_SCORE = clean('MIN_SCORE',          '50',     'float')
BTC_BLOCK = clean('BTC_BEAR_BLOCK_PCT', '2.0',    'float')

# ── VWAP ─────────────────────────────────────────────────────────────────
VWAP_CANDLES   = clean('VWAP_CANDLES',  '50',   'int')
VWAP_AS_FILTER = clean('VWAP_FILTER',  'false', 'bool')

# ── Motor Principal: Aurolo MLP Tactical Bridge ────────────────────────────
AUROLO_EMA_LEN   = clean('AUROLO_EMA_LEN',   '55',   'int')
AUROLO_ZONA_AUTO = clean('AUROLO_ZONA_AUTO',  'true', 'bool')
AUROLO_ZONA_PCT  = clean('AUROLO_ZONA_PCT',   '0.8',  'float')
AUROLO_ZONA_VELAS = clean('AUROLO_ZONA_VELAS', '6',   'int')

# Punto 2 — WaveTrend
WT_CH_LEN  = clean('WT_CH_LEN',  '10',  'int')
WT_AVG_LEN = clean('WT_AVG_LEN', '21',  'int')
WT_OB1     = clean('WT_OB1',     '60',  'float')
WT_OB2     = clean('WT_OB2',     '42',  'float')
WT_OS1     = clean('WT_OS1',     '-60', 'float')
WT_OS2     = clean('WT_OS2',     '-42', 'float')
WT_OS_ENTRY = clean('WT_OS_ENTRY', '-20', 'float')

# Punto 3 — ADX
ADX_LEN    = clean('ADX_LEN',    '14',  'int')
ADX_DI_LEN = clean('ADX_DI_LEN', '14', 'int')
ADX_KEY    = clean('ADX_KEY',    '20',  'float')

# Configuración señal
AUROLO_MIN_PTS = clean('AUROLO_MIN_PTS', '2',     'int')
AUROLO_ENTRY   = clean('AUROLO_ENTRY',   'close', 'str')

# ── Circuit breaker ────────────────────────────────────────────────────────
CB_PCT     = clean('CIRCUIT_BREAKER_PCT', '8.0',  'float')
CB_HOURS   = clean('CB_PAUSE_HOURS',      '1',    'int')
MAX_STREAK = clean('MAX_LOSING_STREAK',   '5',    'int')

# ── Cooldowns ─────────────────────────────────────────────────────────────
CD_TP  = clean('COOLDOWN_TP_MIN', '5',  'int')
CD_SL  = clean('COOLDOWN_SL_MIN', '30', 'int')

# ── Aprendizaje ───────────────────────────────────────────────────────────
LEARN_MIN_TRADES_SCORE = clean('LEARN_MIN_TRADES', '15', 'int')
LEARN_MIN_TRADES_BL    = clean('LEARN_MIN_TRADES_BL', '8', 'int')
SCORE_CAP_LOW          = clean('SCORE_CAP_LOW', '60',  'float')
SCORE_CAP_HIGH         = clean('SCORE_CAP_HIGH', '80', 'float')

# ── Misc ───────────────────────────────────────────────────────────────────
INTERVAL   = clean('CHECK_INTERVAL', '90', 'int')
LTV_WARN   = clean('LTV_WARNING_PCT', '80', 'float')

# SKIP_HOURS configurable
_skip_raw  = os.getenv('SKIP_HOURS', '2,3')
SKIP_HOURS = set(int(x.strip()) for x in _skip_raw.split(',') if x.strip().isdigit())

BASE_URL   = "https://open-api.bingx.com"
FEE        = 0.0002
TP_MIN_FEE = round((2 * FEE * LEVERAGE + 0.003) * 100, 3)

EXCLUDE = {
    'DOW','SP500','GOLD','SILVER','XAU','OIL','BRENT','EUR','GBP','JPY',
    'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
    'WHEAT','CORN','SUGAR','PAXG','XAUT',
}

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
# MOTOR AUROLO v5.6
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
    if n < di_len + adx_smooth + 2:
        return [0.0]*n, [0.0]*n, [0.0]*n
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
    """Motor Aurolo v5.6"""
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

    price    = closes[-1]
    ema55    = ema(closes, AUROLO_EMA_LEN)
    result['ema55'] = ema55

    # CAMBIO DE TENDENCIA
    ema55_prev       = ema(closes[:-1], AUROLO_EMA_LEN)
    tendencia_ahora  = price > ema55
    tendencia_antes  = closes[-2] > ema55_prev if len(closes) >= 2 else tendencia_ahora
    result['cambio_tend'] = (tendencia_ahora != tendencia_antes)

    if not tendencia_ahora:
        result['señal']       = 'NO'
        p_round = round(price, 4)
        ema_round = round(ema55, 4)
        result['descripcion'] = f'Bajista (p={p_round} < EMA55={ema_round})'
        return result

    # ZONA DINÁMICA
    if AUROLO_ZONA_AUTO and atr_v and atr_v > 0:
        zona_pct = (atr_v / price * 100) * 1.0
        zona_pct = max(min(zona_pct, 2.0), 0.3)
    else:
        zona_pct = AUROLO_ZONA_PCT
    zona_inf = ema55 * (1 - zona_pct / 100)
    zona_sup = ema55 * (1 + zona_pct / 100)
    result['zona_inf'] = zona_inf
    result['zona_sup'] = zona_sup

    # PUNTO 1: TENDENCIAL EMA55
    toco_zona = False
    n_velas = min(AUROLO_ZONA_VELAS, len(closes) - 1)
    for i in range(-n_velas, 0):
        c_i = closes[i]; l_i = lows[i]
        if AUROLO_ENTRY == 'close':
            if zona_inf <= c_i <= zona_sup:
                toco_zona = True; break
        else:
            if l_i <= zona_sup and c_i >= zona_inf * 0.993:
                toco_zona = True; break

    rebota = closes[-1] > ema55 * 0.999
    result['p1'] = toco_zona and rebota

    # PUNTO 2: WAVETREND
    wt1      = _wavetrend_series(closes, highs, lows, WT_CH_LEN, WT_AVG_LEN)
    wt_now   = wt1[-1]
    wt_prev  = wt1[-2] if len(wt1) >= 2 else wt_now
    wt_prev2 = wt1[-3] if len(wt1) >= 3 else wt_prev
    result['wt_now'] = wt_now; result['wt_prev'] = wt_prev

    cruce_alc = (wt_now > wt_prev) and (wt_prev <= WT_OS_ENTRY or wt_prev2 <= WT_OS2)
    en_os     = wt_now <= WT_OS2
    result['p2'] = cruce_alc or (en_os and wt_now > wt_prev)

    # PUNTO 3: ADX + DIRECCIONALIDAD
    adx_vals, dip_vals, din_vals = _adx_di_series(highs, lows, closes, ADX_DI_LEN, ADX_LEN)
    adx_now  = adx_vals[-1]; adx_prev = adx_vals[-2] if len(adx_vals)>=2 else adx_now
    dip_now  = dip_vals[-1]; din_now  = din_vals[-1]
    result['adx_now'] = adx_now; result['dip'] = dip_now; result['din'] = din_now

    adx_fuerte  = adx_now >= ADX_KEY
    di_alcista  = dip_now > din_now
    adx_cayendo = adx_now < adx_prev
    result['p3'] = adx_fuerte and di_alcista

    pts = int(result['p1']) + int(result['p2']) + int(result['p3'])
    result['puntos'] = pts

    # SL INTELIGENTE v5.6
    atr_actual   = atr_v or atr_calc(highs, lows, closes, 14)
    min_reciente = min(lows[-8:-1]) if len(lows) >= 8 else lows[-1]
    sl_vulner    = min_reciente - atr_actual * SL_ATR_M
    sl_bajo_ema  = ema55 * (1 - 0.15/100)
    sl_price     = min(sl_vulner, sl_bajo_ema)
    sl_max       = price * (1 - SL_MAX_PCT / 100)
    sl_min       = price * (1 - SL_MIN_PCT / 100)
    sl_price     = max(sl_price, sl_max)
    sl_price     = min(sl_price, sl_min)
    sl_pct       = (price - sl_price) / price * 100
    result['sl_price'] = round(sl_price, 8)
    result['sl_pct']   = round(sl_pct, 3)

    # SEÑAL DE DEBILIDAD
    wt_ob_baj = wt_now < wt_prev and wt_prev >= WT_OB2
    di_gira   = din_now > dip_now * 0.80
    result['debilidad'] = bool(adx_cayendo and (wt_ob_baj or wt_now >= WT_OB1) and di_gira)

    # VOLUMEN
    vol_avg = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    result['vol_ratio'] = volumes[-1] / vol_avg if vol_avg > 0 else 1

    # CLASIFICAR
    p1_icon = '✅' if result['p1'] else '❌'
    p2_icon = '✅' if result['p2'] else '❌'
    p3_icon = '✅' if result['p3'] else '❌'
    wt_r = round(wt_now, 1)
    adx_r = round(adx_now, 1)
    dip_r = round(dip_now, 1)
    result['descripcion'] = (
        f"P1({p1_icon})EMA{AUROLO_EMA_LEN} | "
        f"P2({p2_icon})WT={wt_r} | "
        f"P3({p3_icon})ADX={adx_r} DI+={dip_r}"
    )

    if pts >= 3:   result['señal'] = 'LONG_3/3'
    elif pts == 2: result['señal'] = 'LONG_2/3'
    elif pts == 1: result['señal'] = 'ESPERAR'
    else:          result['señal'] = 'NO'

    return result


def vwap_contexto(closes, highs, lows, volumes, n=50):
    if len(closes) < n: return closes[-1], True
    vwap = calc_vwap(closes, highs, lows, volumes, n)
    return vwap, closes[-1] > vwap


# ============================================================================
# APRENDIZAJE v5.6
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
        self.by_btc        = defaultdict(lambda: {'w':0,'l':0,'pnl':0.0})
        self.by_reason     = defaultdict(lambda: {'n':0,'pnl':0.0})
        self.factor_wins   = defaultdict(int)
        self.factor_losses = defaultdict(int)
        self.score_boost   = {}

    def _score_cap(self):
        n = len(self.history)
        if n < LEARN_MIN_TRADES_SCORE:
            return SCORE_CAP_LOW
        return SCORE_CAP_HIGH

    def record(self, symbol, score, pnl, win, hora_utc=None,
               pts_aurolo=0, btc_dir='flat', reason='?', factors=None):
        rec = {
            'ts': datetime.now().isoformat(), 'sym': symbol,
            'score': score, 'pnl': pnl, 'win': win,
            'hora': hora_utc or datetime.utcnow().hour,
            'pts': pts_aurolo, 'btc': btc_dir,
            'reason': reason, 'factors': factors or [],
        }
        self.history.append(rec); self.last10.append(rec)
        if len(self.last10) > 10: self.last10.pop(0)
        s = self.sym_stats[symbol]; s['n'] += 1; s['pnl'] += pnl
        k = 'w' if win else 'l'
        if win: s['w'] += 1; self.streak = 0
        else:   s['l'] += 1; self.streak += 1
        self.by_hour[rec['hora']][k] += 1; self.by_hour[rec['hora']]['pnl'] += pnl
        self.by_pts[pts_aurolo][k]   += 1; self.by_pts[pts_aurolo]['pnl']   += pnl
        self.by_btc[btc_dir][k]      += 1; self.by_btc[btc_dir]['pnl']      += pnl
        self.by_reason[reason]['n']  += 1; self.by_reason[reason]['pnl']    += pnl
        for f in (factors or []):
            if win: self.factor_wins[f]   += 1
            else:   self.factor_losses[f] += 1
        self._adjust()
        if len(self.history) % 10 == 0: self._reporte()

    def _adjust(self):
        n = len(self.history)
        cap = self._score_cap()

        if n >= LEARN_MIN_TRADES_SCORE:
            wr = sum(1 for t in self.last10 if t['win']) / len(self.last10)
            if wr < 0.35:
                self.opt_score = min(self.opt_score + 3, cap)
            elif wr < 0.45:
                self.opt_score = min(self.opt_score + 1, cap)
            elif wr > 0.65:
                self.opt_score = max(self.opt_score - 2, MIN_SCORE)
            elif wr > 0.75:
                self.opt_score = max(self.opt_score - 4, MIN_SCORE)
        else:
            if self.opt_score > cap:
                self.opt_score = cap
                log.info(f"  [LEARN] Score cappado a {cap} (solo {n} trades)")

        for sym, s in self.sym_stats.items():
            tot = s['w'] + s['l']
            if (tot >= LEARN_MIN_TRADES_BL and
                    s['pnl'] < -2.0 and
                    s['w'] / tot < 0.20):
                if sym not in self.blacklist:
                    self.blacklist.add(sym)
                    wr_pct = int(s['w']/tot*100)
                    log.warning(f"  [LEARN] 🚫 {sym} → blacklist ({tot} trades, WR={wr_pct}%)")

        if n >= 20:
            for f in set(list(self.factor_wins) + list(self.factor_losses)):
                w = self.factor_wins.get(f, 0); l = self.factor_losses.get(f, 0)
                if w+l < 5: continue
                wr_f = w/(w+l)
                if wr_f < 0.30:   self.score_boost[f] = -8
                elif wr_f > 0.70: self.score_boost[f] = +5
                else:             self.score_boost.pop(f, None)

    def hora_ok(self, h):
        d = self.by_hour.get(h)
        if not d: return True, "ok"
        tot = d['w']+d['l']
        if tot < 8: return True, "ok"
        wr_hora = d['w'] / tot
        if wr_hora < 0.25:
            wr_pct = int(wr_hora*100)
            return False, f"hora {h}h WR={wr_pct}%"
        return True, "ok"

    def bonus_pts(self, pts) -> int:
        d = self.by_pts.get(pts)
        if not d: return 0
        tot = d['w']+d['l']
        if tot < 5: return 0
        wr = d['w']/tot
        if wr > 0.65: return +10
        if wr < 0.35: return -10
        return 0

    def ok(self, sym, score):
        if sym in self.blacklist:
            return False, "blacklist"
        if score < self.opt_score:
            score_i = int(score)
            opt_i = int(self.opt_score)
            return False, f"score {score_i}<{opt_i}"
        if self.streak >= MAX_STREAK:
            return False, f"streak -{self.streak}"
        return True, "ok"

    def adj(self, factors):
        return sum(self.score_boost.get(f, 0) for f in factors)

    def _reporte(self):
        n = len(self.history)
        wr  = sum(1 for t in self.history if t['win'])/n*100 if n else 0
        pnl = sum(t['pnl'] for t in self.history)
        pts_txt = ""
        for p in sorted(self.by_pts):
            d = self.by_pts[p]; tot = d['w']+d['l']
            if tot > 0:
                wr_pts = int(d['w']/tot*100)
                pnl_pts = d['pnl']
                pts_txt += f"  {p}/3 pts: WR={wr_pts}% PnL=${pnl_pts:.2f} ({tot}t)\n"
        reas_txt = ""
        for r, d in sorted(self.by_reason.items(), key=lambda x: x[1]['pnl'], reverse=True):
            reas_pnl = d['pnl']
            reas_n = d['n']
            reas_txt += f"  {r}: ${reas_pnl:+.2f} ({reas_n}x)\n"

        wr_i = int(wr)
        score_i = int(self.opt_score)
        cap_i = int(self._score_cap())
        bl_len = len(self.blacklist)
        
        msg = (
            f"<b>🧠 APRENDIZAJE — {n} trades</b>\n"
            f"WR: {wr_i}% | PnL: ${pnl:+.4f} | Score mín: {score_i}\n"
            f"Blacklist: {bl_len} | Cap: {cap_i}\n\n"
            f"<b>📊 Por puntos Aurolo:</b>\n{pts_txt or '  Sin datos\n'}"
            f"<b>🚪 Cierres:</b>\n{reas_txt or '  Sin datos\n'}"
        )
        report_num = n // 10
        log.info(f"[LEARN] #{report_num}: WR={wr_i}% PnL=${pnl:+.4f} Score={score_i}")
        try:
            if TG_TOKEN and TG_CHAT:
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=6
                )
        except: pass

    def save(self, fp='/tmp/bot_learn_v56.json'):
        try:
            json.dump({
                'history': self.history[-200:], 'sym_stats': dict(self.sym_stats),
                'opt_score': self.opt_score, 'blacklist': list(self.blacklist),
                'by_hour': dict(self.by_hour), 'by_pts': dict(self.by_pts),
                'by_btc': dict(self.by_btc), 'by_reason': dict(self.by_reason),
                'factor_wins': dict(self.factor_wins),
                'factor_losses': dict(self.factor_losses),
                'score_boost': self.score_boost,
            }, open(fp,'w'), indent=2)
        except: pass

    def load(self, fp='/tmp/bot_learn_v56.json'):
        for path in [fp, '/tmp/bot_learn_v55.json', '/tmp/bot_learn_v53.json', '/tmp/bot_learn.json']:
            try:
                if not os.path.exists(path): continue
                d = json.load(open(path))
                self.history    = d.get('history',[])
                self.sym_stats  = defaultdict(lambda:{'w':0,'l':0,'pnl':0.0,'n':0}, d.get('sym_stats',{}))
                raw_score       = d.get('opt_score', MIN_SCORE)
                self.blacklist  = set(d.get('blacklist',[]))
                self.by_hour    = defaultdict(lambda:{'w':0,'l':0,'pnl':0.0}, d.get('by_hour',{}))
                self.by_pts     = defaultdict(lambda:{'w':0,'l':0,'pnl':0.0}, d.get('by_pts',{}))
                self.by_btc     = defaultdict(lambda:{'w':0,'l':0,'pnl':0.0}, d.get('by_btc',{}))
                self.by_reason  = defaultdict(lambda:{'n':0,'pnl':0.0}, d.get('by_reason',{}))
                self.factor_wins   = defaultdict(int, d.get('factor_wins',{}))
                self.factor_losses = defaultdict(int, d.get('factor_losses',{}))
                self.score_boost   = d.get('score_boost',{})
                cap = self._score_cap()
                self.opt_score = min(raw_score, cap)
                hist_len = len(self.history)
                score_i = int(self.opt_score)
                cap_i = int(cap)
                bl_len = len(self.blacklist)
                log.info(f"  [LEARN] {hist_len} trades | Score: {score_i} (cap={cap_i}) | BL: {bl_len}")
                return
            except: continue


# ============================================================================
# BOT PRINCIPAL v5.6
# ============================================================================

class LongBot:
    _opening = False

    def __init__(self):
        log.info("=" * 72)
        log.info("  BOT LONGS v5.6 — Optimizado para rentabilidad")
        log.info(f"  Capital: ${POS_SIZE} | Riesgo: {RISK_PCT}%/trade | {LEVERAGE}x")
        log.info(f"  Motor: EMA{AUROLO_EMA_LEN} + WaveTrend(ch={WT_CH_LEN},avg={WT_AVG_LEN}) + ADX>{ADX_KEY}")
        zona_txt = 'auto(ATR)' if AUROLO_ZONA_AUTO else f'{AUROLO_ZONA_PCT}%'
        log.info(f"  Mín {AUROLO_MIN_PTS}/3 pts | Zona: {zona_txt} | {AUROLO_ZONA_VELAS} velas")
        tp1_pct_i = int(TP1_PCT)
        tp2_pct_i = int(TP2_PCT)
        runner_pct_i = int(100-TP1_PCT-TP2_PCT)
        log.info(f"  TPs: {tp1_pct_i}%@{TP1_RATIO}×SL | {tp2_pct_i}%@{TP2_RATIO}×SL | {runner_pct_i}%→EMA25")
        vwap_txt = 'filtro duro' if VWAP_AS_FILTER else 'solo score'
        log.info(f"  VWAP: {vwap_txt} | CD: TP={CD_TP}m SL={CD_SL}m")
        cap_low_i = int(SCORE_CAP_LOW)
        cap_high_i = int(SCORE_CAP_HIGH)
        log.info(f"  Score: min={MIN_SCORE} | Cap<{LEARN_MIN_TRADES_SCORE}t={cap_low_i} Cap≥{LEARN_MIN_TRADES_SCORE}t={cap_high_i}")
        skip_sorted = sorted(SKIP_HOURS)
        log.info(f"  SKIP_HOURS: {skip_sorted} | CB: {CB_PCT}% equity → {CB_HOURS}h pausa")
        log.info("=" * 72)

        self.symbols      = []
        self.trades       = {}
        self._contracts   = {}
        self._cooldowns   = {}
        self._last_report = datetime.now() - timedelta(hours=3)
        self._btc_1h      = 0.0
        self._btc_ok      = True
        self._mode        = 'hedge'
        self._daily_pnl   = 0.0
        self._daily_date  = datetime.utcnow().date()
        self._cb_active   = False
        self._cb_until    = None
        self.learn        = Learning()
        self.learn.load()
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0,'fees':0.0}

        if not self._connect(): log.error("❌ Sin conexión BingX"); sys.exit(1)
        self._detect_mode()
        self._load_contracts()
        self._refresh_symbols()
        self._recover()

        score_i = int(self.learn.opt_score)
        cap_i = int(self.learn._score_cap())
        trades_len = len(self.trades)
        
        self._tg(
            f"<b>🤖 Bot LONGS v5.6 — Optimizado</b>\n"
            f"Motor: EMA{AUROLO_EMA_LEN}+WT(OS_entry={WT_OS_ENTRY})+ADX>{ADX_KEY} | Mín {AUROLO_MIN_PTS}/3\n"
            f"TPs: {tp1_pct_i}%→TP1 | {tp2_pct_i}%→TP2 | {runner_pct_i}%→EMA25\n"
            f"Score: {score_i} (cap {cap_i}) | CD: TP={CD_TP}m SL={CD_SL}m\n"
            f"Posiciones recuperadas: {trades_len}"
        )

    def _connect(self) -> bool:
        global AUTO, ACCOUNT_EQUITY
        if not AUTO: return True
        if not API_KEY or not API_SECRET:
            log.error("❌ API keys no configuradas"); AUTO = False; return False
        d = api('GET', '/openApi/swap/v2/user/balance')
        if d.get('code') == 0:
            b = d.get('data',{}); eq = _safe_float(b.get('equity', b.get('balance', 0)))
            if eq > 0: ACCOUNT_EQUITY = eq
            eq_f = f"{ACCOUNT_EQUITY:.2f}"
            log.info(f"✅ BingX conectado | ${eq_f} USDT")
            return True
        code_v = d.get('code')
        msg_v = d.get('msg')
        log.error(f"❌ [{code_v}]: {msg_v}")
        AUTO = False; return False

    def _detect_mode(self):
        try:
            d = api('GET', '/openApi/swap/v2/user/positions', {'symbol':'BTC-USDT'})
            for p in (d.get('data') or []):
                s = str(p.get('positionSide','')).upper()
                if s in ('LONG','SHORT'): self._mode='hedge'; log.info("  Modo: HEDGE"); return
                if s == 'BOTH': self._mode='oneway'; log.info("  Modo: ONE-WAY"); return
        except: pass
        log.info("  Modo: HEDGE (default)")

    def _load_contracts(self):
        d = pub('/openApi/swap/v2/quote/contracts')
        if d.get('code') == 0:
            for c in d.get('data',[]):
                s = c.get('symbol','')
                if s: self._contracts[s] = {
                    'step': float(c.get('tradeMinQuantity',1)),
                    'prec': int(c.get('quantityPrecision',2)),
                    'ctval': float(c.get('contractSize',1)),
                }
            contracts_len = len(self._contracts)
            log.info(f"  Contratos: {contracts_len}")

    def _refresh_symbols(self):
        d = pub('/openApi/swap/v2/quote/ticker')
        if d.get('code') != 0:
            self.symbols = self.symbols or ['BTC-USDT','ETH-USDT','SOL-USDT']; return
        items = []
        for t in d.get('data',[]):
            sym = t.get('symbol','')
            if not sym.endswith('-USDT'): continue
            base = sym.replace('-USDT','').upper()
            if any(ex in base for ex in EXCLUDE): continue
            try:
                price = float(t.get('lastPrice',0)); vol = float(t.get('volume',0))*price
                if vol >= MIN_VOL and price > 0: items.append({'sym':sym,'vol':vol})
            except: continue
        items.sort(key=lambda x: x['vol'], reverse=True)
        self.symbols = [x['sym'] for x in items[:MAX_SYMS]]
        syms_len = len(self.symbols)
        log.info(f"  Símbolos: {syms_len}")

    def _get_exchange_positions(self, symbol=None):
        params = {}
        if symbol: params['symbol'] = symbol
        d = api('GET', '/openApi/swap/v2/user/positions', params)
        result = defaultdict(lambda: {'long':0.0,'short':0.0})
        for p in (d.get('data') or []):
            try:
                amt=float(p.get('positionAmt',0) or 0); sym=p.get('symbol','')
                side=str(p.get('positionSide','')).upper()
                if not sym or abs(amt)==0: continue
                if side=='LONG' or (side=='BOTH' and amt>0): result[sym]['long']=abs(amt)
                elif side=='SHORT' or (side=='BOTH' and amt<0): result[sym]['short']=abs(amt)
            except: continue
        return result

    def _has_any_position(self, symbol) -> bool:
        pos = self._get_exchange_positions(symbol)
        return pos[symbol]['long']>0 or pos[symbol]['short']>0

    def _order_close_short(self, sym, qty):
        params = {'symbol':sym,'side':'BUY','type':'MARKET','quantity':str(qty)}
        if self._mode == 'hedge': params['positionSide'] = 'SHORT'
        else: params['reduceOnly'] = 'true'
        return api('POST', '/openApi/swap/v2/trade/order', params)

    def _recover(self):
        if not AUTO: return
        all_pos = self._get_exchange_positions(); n_rec=0; n_sh=0
        for sym, sides in all_pos.items():
            if sides['short'] > 0:
                log.warning(f"  ⚠️ SHORT huérfano: {sym} → cerrando")
                if self._order_close_short(sym, sides['short']).get('code')==0:
                    n_sh+=1; self._tg(f"<b>🔧 SHORT cerrado</b>\n{sym}")
                time.sleep(0.5)
            if sides['long'] > 0 and sym not in self.trades:
                d2 = api('GET','/openApi/swap/v2/user/positions',{'symbol':sym})
                entry = 0.0
                for p in (d2.get('data') or []):
                    s2=str(p.get('positionSide','')).upper(); a2=float(p.get('positionAmt',0) or 0)
                    if (s2=='LONG' and abs(a2)>0) or (s2=='BOTH' and a2>0):
                        entry=float(p.get('avgPrice') or p.get('entryPrice') or 0); break
                if entry<=0: continue
                qty = sides['long']
                sl_rec = entry * (1 - SL_MAX_PCT / 100)
                self.trades[sym] = self._mk_trade(
                    entry, qty, sl_rec,
                    entry * (1 + TP1_RATIO * SL_MAX_PCT / 100),
                    entry * (1 + TP2_RATIO * SL_MAX_PCT / 100),
                    SL_MAX_PCT, 0, '?', entry, entry, 0)
                n_rec+=1
                entry_f = f"{entry:.6f}"
                log.info(f"  ♻️ LONG recuperado: {sym} @ ${entry_f}")
        log.info(f"  Recuperadas: {n_rec} | SHORTs cerrados: {n_sh}")

    def _scan_orphan_shorts(self):
        if not AUTO: return
        for sym, sides in self._get_exchange_positions().items():
            if sides['short'] > 0:
                self._order_close_short(sym, sides['short']); time.sleep(0.3)

    def _mk_trade(self, entry, qty, sl, tp1_p, tp2_p, sl_pct, score,
                  pts_label, ema25_v, ema55_v, aurolo_pts):
        qty_tp1 = round(qty * TP1_PCT/100, 6)
        qty_tp2 = round(qty * TP2_PCT/100, 6)
        return {
            'entry': entry, 'qty_total': qty, 'qty_runner': qty,
            'qty_tp1': qty_tp1, 'qty_tp2': qty_tp2,
            'tp1_hit': False, 'tp2_hit': False,
            'tp1_price': tp1_p, 'tp2_price': tp2_p,
            'sl': sl, 'sl_orig': sl,
            'sl_pct': sl_pct, 'highest': entry,
            'opened': datetime.now(), 'score': score,
            'ema25': ema25_v, 'ema55': ema55_v,
            'aurolo_pts': aurolo_pts,
            'entrada_label': pts_label,
            'usdt': POS_SIZE, 'pnl_parcial': 0.0,
            'factors': [], 'hora_utc': datetime.utcnow().hour,
            'btc_dir': self._btc_dir(),
            'debilidad_alertada': False,
        }

    def _klines(self, symbol, interval='5m', limit=130):
        d = pub('/openApi/swap/v3/quote/klines',
                {'symbol':symbol,'interval':interval,'limit':limit})
        if d.get('code')==0 and d.get('data'):
            kl = d['data']
            return ([float(k['close']) for k in kl],[float(k['high']) for k in kl],
                    [float(k['low']) for k in kl],[float(k['volume']) for k in kl],
                    [float(k['open']) for k in kl])
        return None,None,None,None,None

    def _ticker(self, sym):
        d = pub('/openApi/swap/v2/quote/ticker',{'symbol':sym})
        if d.get('code')==0 and d.get('data'):
            t=d['data']
            return {'price':float(t.get('lastPrice',0)),'change':float(t.get('priceChangePercent',0))}
        return None

    def _update_btc(self):
        c,*_ = self._klines('BTC-USDT','1h',4)
        if c and len(c)>=2:
            self._btc_1h=(c[-1]-c[-2])/c[-2]*100; self._btc_ok=self._btc_1h>=-BTC_BLOCK
        else: self._btc_ok=True

    def _btc_dir(self):
        if self._btc_1h > 0.5: return 'up'
        if self._btc_1h < -0.5: return 'down'
        return 'flat'

    def _update_equity(self):
        global ACCOUNT_EQUITY
        d=api('GET','/openApi/swap/v2/user/balance')
        if d.get('code')==0:
            b=d.get('data',{}); eq=_safe_float(b.get('equity', b.get('balance', 0)))
            if eq>0: ACCOUNT_EQUITY=eq

    def _check_ltv(self):
        if not AUTO: return
        d=api('GET','/openApi/swap/v2/user/balance')
        if d.get('code')!=0: return
        try:
            b=d.get('data',{}); eq=_safe_float(b.get('equity', b.get('balance', 0)))
            mg=_safe_float(b.get('usedMargin', b.get('initialMargin', 0)))
            ltv_pct = mg / eq * 100 if eq > 0 else 0
            if eq>0 and ltv_pct >= LTV_WARN:
                self._tg("<b>⚠️ LTV ALTO — cerrando posiciones</b>")
                for sym in list(self.trades):
                    tk=self._ticker(sym)
                    if tk: self._close_all(sym,tk['price'],"LTV EMERGENCIA")
        except: pass

    def analyze(self, symbol):
        if symbol in self.trades: return None
        if not self._cd_ok(symbol): return None
        hora = datetime.utcnow().hour
        if hora in SKIP_HOURS: return None
        if not self._btc_ok: return None
        if self._cb_active: return None

        hora_ok, hora_reason = self.learn.hora_ok(hora)
        if not hora_ok:
            log.debug(f"  {symbol}: bloq hora ({hora_reason})")
            return None

        c5, h5, l5, v5, o5 = self._klines(symbol, '5m', 130)
        if not c5 or len(c5) < AUROLO_EMA_LEN + 50: return None

        c1h, h1h, l1h, v1h, _ = self._klines(symbol, '1h', 50)
        tk = self._ticker(symbol)
        if not tk or tk['price'] <= 0: return None
        price = tk['price']; change_24 = tk['change']

        trend_1h = 0; rsi_1h = 50.0
        if c1h and len(c1h) >= 25:
            e9_1h = ema(c1h, 9); e21_1h = ema(c1h, 21)
            rsi_1h = rsi(c1h, 14)
            if e9_1h > e21_1h: trend_1h = 1
            elif e9_1h < e21_1h: trend_1h = -1
        if trend_1h == -1: return None

        atr_v   = atr_calc(h5, l5, c5, 14)
        atr_pct = atr_v / price * 100 if price > 0 else 0
        if atr_pct < 0.10:    return None
        if change_24 > 25.0:  return None
        if change_24 < -15.0: return None

        sig_aurolo = aurolo_signal(c5, h5, l5, v5, o5, atr_v)

        if sig_aurolo['puntos'] < AUROLO_MIN_PTS:
            pts = sig_aurolo['puntos']
            log.debug(f"  {symbol}: Aurolo {pts}/3 pts — insuf")
            return None

        if sig_aurolo['cambio_tend']:
            log.debug(f"  {symbol}: cambio tendencia — esperar")
            return None

        vwap_val, precio_sobre_vwap = vwap_contexto(c5, h5, l5, v5, VWAP_CANDLES)
        if VWAP_AS_FILTER and not precio_sobre_vwap:
            log.debug(f"  {symbol}: bajo VWAP (filtro duro)")
            return None

        sl_price = sig_aurolo['sl_price']
        sl_pct   = sig_aurolo['sl_pct']

        if sl_pct <= 0 or sl_pct > SL_MAX_PCT * 1.1:
            sl_f = f"{sl_pct:.2f}"
            log.debug(f"  {symbol}: SL inválido ({sl_f}%)")
            return None

        tp1_price = price * (1 + sl_pct * TP1_RATIO / 100)
        tp2_price = price * (1 + sl_pct * TP2_RATIO / 100)
        tp_ref    = max(sl_pct * MIN_RR, TP_MIN, TP_MIN_FEE, atr_pct * ATR_TP_M)
        rr        = tp_ref / sl_pct if sl_pct > 0 else 0

        if rr < MIN_RR * 0.75:
            rr_f = f"{rr:.2f}"
            log.debug(f"  {symbol}: RR bajo ({rr_f})")
            return None

        score = 0; reasons = []; factors = []
        pts   = sig_aurolo['puntos']

        if pts == 3:   score += 50; reasons.append("Aurolo3/3(50)"); factors.append("aurolo_3")
        elif pts == 2: score += 30; reasons.append("Aurolo2/3(30)"); factors.append("aurolo_2")

        if sig_aurolo['p1']: score += 10; reasons.append("P1_Tend(10)"); factors.append("p1_tend")
        if sig_aurolo['p2']: score += 10; reasons.append("P2_WT(10)");   factors.append("p2_wt")
        if sig_aurolo['p3']: score += 10; reasons.append("P3_ADX(10)");  factors.append("p3_adx")

        wt_val = sig_aurolo['wt_now']
        if wt_val <= WT_OS1:
            wt_i = int(wt_val)
            score += 8; reasons.append(f"WT_deep({wt_i})(8)"); factors.append("wt_deep")
        elif wt_val <= WT_OS2:
            wt_i = int(wt_val)
            score += 4; reasons.append(f"WT_os({wt_i})(4)"); factors.append("wt_os")

        adx_val = sig_aurolo['adx_now']
        if adx_val > ADX_KEY * 1.4:
            score += 6; reasons.append("ADX_strong(6)"); factors.append("adx_strong")

        vr = sig_aurolo['vol_ratio']
        if vr >= 2.0:
            vr_f = f"{vr:.1f}"
            score += 10; reasons.append(f"Vol{vr_f}x(10)"); factors.append("vol_fuerte")
        elif vr >= 1.4:
            vr_f = f"{vr:.1f}"
            score += 5;  reasons.append(f"Vol{vr_f}x(5)");  factors.append("vol_medio")

        if precio_sobre_vwap:
            score += 8; reasons.append("VWAP↑(8)"); factors.append("vwap_arriba")
        else:
            score -= 5; reasons.append("VWAP↓(-5)"); factors.append("vwap_abajo")

        if trend_1h == 1:
            score += 12; reasons.append("1H↑(12)"); factors.append("trend_1h_up")

        if self._btc_1h > 1.0:
            score += 8; reasons.append("BTC↑(8)"); factors.append("btc_up")
        elif self._btc_1h > 0.3:
            score += 4; reasons.append("BTC~(4)"); factors.append("btc_ok")

        if rsi_1h < 40:
            rsi_i = int(rsi_1h)
            score += 8; reasons.append(f"RSI1H{rsi_i}(8)"); factors.append("rsi_1h_os")
        elif rsi_1h < 55:
            rsi_i = int(rsi_1h)
            score += 4; reasons.append(f"RSI1H{rsi_i}(4)"); factors.append("rsi_1h_ok")

        if sl_pct < SL_MAX_PCT * 0.6:
            score += 5; reasons.append("SL_tight(5)"); factors.append("sl_tight")

        bonus_p = self.learn.bonus_pts(pts)
        if bonus_p != 0:
            bonus_s = f"{bonus_p:+d}"
            score += bonus_p; reasons.append(f"LearnPts({bonus_s})"); factors.append(f"learn_pts_{pts}")

        adj = self.learn.adj(factors)
        if adj != 0:
            adj_s = f"{adj:+d}"
            score += adj; reasons.append(f"FactAdj({adj_s})")

        ok, reason = self.learn.ok(symbol, score)
        if not ok:
            score_i = int(score)
            log.debug(f"  {symbol}: rechazado ({reason}) score={score_i}")
            return None

        e25 = ema(c5, 25)
        e55 = sig_aurolo['ema55']

        reason_txt = ' | '.join(reasons)
        
        return {
            'price': price, 'change': change_24, 'score': score,
            'score_min': self.learn.opt_score,
            'aurolo_pts': pts,
            'aurolo_p1': sig_aurolo['p1'], 'aurolo_p2': sig_aurolo['p2'], 'aurolo_p3': sig_aurolo['p3'],
            'aurolo_wt': sig_aurolo['wt_now'], 'aurolo_adx': sig_aurolo['adx_now'],
            'aurolo_dip': sig_aurolo['dip'],   'aurolo_din': sig_aurolo['din'],
            'aurolo_desc': sig_aurolo['descripcion'], 'aurolo_señal': sig_aurolo['señal'],
            'sl_price': round(sl_price, 8), 'sl_pct': round(sl_pct, 3),
            'tp1_price': round(tp1_price, 8), 'tp2_price': round(tp2_price, 8),
            'tp_pct': round(tp_ref, 2), 'rr': round(rr, 2),
            'vwap': vwap_val, 'ema25': e25, 'ema55': e55,
            'zona_inf': sig_aurolo['zona_inf'], 'zona_sup': sig_aurolo['zona_sup'],
            'trend_1h': trend_1h, 'rsi_1h': rsi_1h,
            'vol_ratio': vr, 'atr_pct': atr_pct,
            'reasons': reason_txt, 'factors': factors,
            'hora_utc': hora, 'btc_dir': self._btc_dir(),
            'precio_sobre_vwap': precio_sobre_vwap,
        }

    def _set_lev(self, sym):
        for side in ('LONG','SHORT'):
            try: api('POST','/openApi/swap/v2/trade/leverage',
                     {'symbol':sym,'side':side,'leverage':str(LEVERAGE)})
            except: pass

    def _calc_qty(self, sym, price, sl_price):
        info  = self._contracts.get(sym, {'step':1,'prec':2,'ctval':1})
        step  = max(float(info.get('step',1)), 1e-6)
        prec  = int(info.get('prec',2))
        ctval = max(float(info.get('ctval',1)), 1e-9)
        ppc   = price * ctval
        if ppc <= 0: return None, 0
        if sl_price < price:
            dist_pct = (price-sl_price)/price*100
            riesgo   = ACCOUNT_EQUITY * (RISK_PCT/100)
            notional = min(riesgo / (dist_pct/100), POS_SIZE*LEVERAGE)
            notional = max(notional, MIN_TRADE)
        else:
            notional = max(POS_SIZE*LEVERAGE, MIN_TRADE)
        qty = math.ceil((notional/ppc)/step)*step; qty=round(qty,prec); val=qty*ppc
        for _ in range(200):
            if val>=MIN_TRADE: break
            qty+=step; qty=round(qty,prec); val=qty*ppc
        return (qty, round(val,4)) if val>=MIN_TRADE else (None,0)

    def _order(self, sym, side, qty, otype='MARKET', price=None, stop_price=None):
        params = {'symbol':sym,'side':side.upper(),'type':otype,'quantity':str(qty)}
        if self._mode=='hedge': params['positionSide']='LONG'
        else:
            if side.upper()=='SELL': params['reduceOnly']='true'
        if price:       params['price']=str(round(price,8)); params['timeInForce']='GTC'
        if stop_price:  params['stopPrice']=str(round(stop_price,8))
        return api('POST','/openApi/swap/v2/trade/order',params)

    def _wait_fill(self, sym, oid, timeout=35):
        for _ in range(timeout):
            d=api('GET','/openApi/swap/v2/trade/order',{'symbol':sym,'orderId':str(oid)})
            if d.get('code')==0:
                o=d.get('data',{}).get('order',{}); st=o.get('status','')
                if st=='FILLED': return float(o.get('executedQty',0)),float(o.get('avgPrice',0))
                if st in ('CANCELED','EXPIRED','REJECTED'): return None,None
            time.sleep(1)
        return None,None

    def _confirm_pos(self, sym, timeout=15):
        for _ in range(timeout):
            d=api('GET','/openApi/swap/v2/user/positions',{'symbol':sym})
            for p in (d.get('data') or []):
                amt=float(p.get('positionAmt',0) or 0); side=str(p.get('positionSide','')).upper()
                if (side=='LONG' and abs(amt)>0) or (side=='BOTH' and amt>0):
                    return abs(amt),float(p.get('avgPrice') or p.get('entryPrice') or 0)
            time.sleep(1)
        return None,None

    def _cancel_open(self, sym):
        d=api('GET','/openApi/swap/v2/trade/openOrders',{'symbol':sym})
        for o in (d.get('data',{}).get('orders') or []):
            oid=o.get('orderId')
            if oid: api('DELETE','/openApi/swap/v2/trade/order',{'symbol':sym,'orderId':str(oid)})

    def _place_sl(self, sym, qty, sl_price):
        d=self._order(sym,'SELL',qty,'STOP_MARKET',stop_price=sl_price)
        ok=d.get('code')==0
        if not ok:
            sl_limit = sl_price * 0.999
            d=self._order(sym,'SELL',qty,'STOP',price=sl_limit,stop_price=sl_price)
            ok=d.get('code')==0
        sl_f = f"{sl_price:.6f}"
        icon = '✅' if ok else '❌'
        log.info(f"  {icon} SL @ ${sl_f}")
        return ok

    def open_trade(self, sym, sig):
        if not AUTO or sym in self.trades: return False
        if LongBot._opening or len(self.trades)>=MAX_TRADES: return False
        if self._has_any_position(sym):
            log.warning(f"  ⛔ {sym} ya tiene posición → omitiendo"); return False
        LongBot._opening = True
        try: return self._open(sym, sig)
        finally: LongBot._opening = False

    def _open(self, sym, sig):
        price    = sig['price']
        sl_price = sig['sl_price']
        pts      = sig['aurolo_pts']
        label    = sig['aurolo_señal']

        score_i = int(sig['score'])
        rr_f = f"{sig['rr']:.2f}"
        log.info(f"\n  🎯 LONG {sym} [{label}] | Score:{score_i} | RR:{rr_f}:1")
        log.info(f"  {sig['aurolo_desc']}")
        zi_f = f"{sig['zona_inf']:.4f}"
        zs_f = f"{sig['zona_sup']:.4f}"
        log.info(f"  Zona EMA{AUROLO_EMA_LEN}: ${zi_f}–${zs_f}")

        self._set_lev(sym); time.sleep(0.2)
        qty, notional = self._calc_qty(sym, price, sl_price)
        if not qty: return False

        limit_p = round(price * (1-0.05/100), 8)
        d = self._order(sym, 'BUY', qty, 'LIMIT', price=limit_p)
        if d.get('code') != 0:
            msg = d.get('msg')
            log.error(f"  ❌ LIMIT: {msg}"); return False

        oid = d.get('data',{}).get('orderId')
        filled_qty, fill_price = self._wait_fill(sym, oid, 30)

        if not filled_qty:
            log.warning("  ⚠️ LIMIT sin fill → MARKET")
            self._cancel_open(sym); time.sleep(0.5)
            d = self._order(sym, 'BUY', qty, 'MARKET')
            if d.get('code')!=0:
                msg = d.get('msg')
                log.error(f"  ❌ MARKET: {msg}"); return False
            filled_qty, fill_price = self._confirm_pos(sym, 12)
            if not filled_qty: return False

        sl_pct_real = (fill_price - sl_price) / fill_price * 100
        tp1_price   = fill_price * (1 + sl_pct_real * TP1_RATIO / 100)
        tp2_price   = fill_price * (1 + sl_pct_real * TP2_RATIO / 100)

        sl_ok = self._place_sl(sym, filled_qty, sl_price)
        if not sl_ok:
            time.sleep(2); sl_ok = self._place_sl(sym, filled_qty, sl_price)
        if not sl_ok:
            log.error("  ❌ SL crítico — cerrando")
            self._order(sym,'SELL',filled_qty,'MARKET'); return False

        trade = {
            'entry': fill_price, 'qty_total': filled_qty, 'qty_runner': filled_qty,
            'qty_tp1': round(filled_qty * TP1_PCT/100, 6),
            'qty_tp2': round(filled_qty * TP2_PCT/100, 6),
            'qty_runner_f': filled_qty - round(filled_qty*TP1_PCT/100,6) - round(filled_qty*TP2_PCT/100,6),
            'tp1_hit': False, 'tp2_hit': False,
            'tp1_price': tp1_price, 'tp2_price': tp2_price,
            'sl': sl_price, 'sl_orig': sl_price, 'sl_pct': sl_pct_real,
            'highest': fill_price, 'opened': datetime.now(),
            'score': sig['score'], 'ema25': sig['ema25'], 'ema55': sig['ema55'],
            'aurolo_pts': pts, 'entrada_label': label,
            'vwap': sig['vwap'], 'usdt': POS_SIZE, 'pnl_parcial': 0.0,
            'factors': sig['factors'], 'hora_utc': sig['hora_utc'],
            'btc_dir': sig['btc_dir'], 'debilidad_alertada': False,
        }
        self.trades[sym] = trade
        self.stats['exec']  += 1
        self.stats['fees']  += notional * FEE

        p1 = "✅" if sig['aurolo_p1'] else "❌"
        p2 = "✅" if sig['aurolo_p2'] else "❌"
        p3 = "✅" if sig['aurolo_p3'] else "❌"
        vwap_icon = "✅" if sig.get('precio_sobre_vwap') else "⚠️"

        wt_f = f"{sig['aurolo_wt']:.1f}"
        wt_extra = '(OS✅)' if sig['aurolo_wt'] <= WT_OS2 else ''
        adx_f = f"{sig['aurolo_adx']:.1f}"
        dip_f = f"{sig['aurolo_dip']:.1f}"
        din_f = f"{sig['aurolo_din']:.1f}"
        fill_f = f"{fill_price:.6f}"
        vwap_f = f"{sig['vwap']:.6f}"
        tp1_f = f"{tp1_price:.6f}"
        tp1_pct_f = f"{sl_pct_real*TP1_RATIO:.2f}"
        tp2_f = f"{tp2_price:.6f}"
        tp2_pct_f = f"{sl_pct_real*TP2_RATIO:.2f}"
        sl_f = f"{sl_price:.6f}"
        sl_real_f = f"{sl_pct_real:.2f}"
        btc_f = f"{self._btc_1h:+.2f}"
        
        tp1_pct_i = int(TP1_PCT)
        tp2_pct_i = int(TP2_PCT)
        runner_pct_i = int(100-TP1_PCT-TP2_PCT)
        trend_icon = '🟢' if sig['trend_1h']==1 else '⚪'
        
        self._tg(
            f"<b>🟢 LONG [{label}]</b> — <b>{sym}</b>\n"
            f"Score: {score_i} | RR: {rr_f}:1\n\n"
            f"<b>🔍 Aurolo {pts}/3:</b>\n"
            f"{p1} P1 EMA{AUROLO_EMA_LEN}: ${sig['ema55']:.4f}\n"
            f"{p2} P2 WT: {wt_f}{wt_extra}\n"
            f"{p3} P3 ADX: {adx_f} | DI+:{dip_f} DI-:{din_f}\n\n"
            f"📍 Entrada:  ${fill_f}\n"
            f"{vwap_icon} VWAP:    ${vwap_f}\n"
            f"🎯 TP1 ({tp1_pct_i}%): ${tp1_f} (+{tp1_pct_f}%)\n"
            f"🎯 TP2 ({tp2_pct_i}%): ${tp2_f} (+{tp2_pct_f}%)\n"
            f"🏃 Runner ({runner_pct_i}%): EMA25\n"
            f"🛑 SL: ${sl_f} (-{sl_real_f}%)\n"
            f"1H: {trend_icon} | BTC: {btc_f}%"
        )
        return True

    def _close_partial(self, sym, qty, exit_price, label):
        if qty <= 0: return 0
        d = self._order(sym, 'SELL', qty, 'MARKET')
        if d.get('code') != 0:
            msg = d.get('msg')
            log.error(f"  ❌ Parcial {label} {sym}: {msg}"); return 0
        t = self.trades[sym]
        chg  = (exit_price - t['entry']) / t['entry']
        frac = qty / t['qty_total']
        net  = POS_SIZE*LEVERAGE*chg*frac - POS_SIZE*LEVERAGE*FEE*2*frac
        t['pnl_parcial'] += net; t['qty_runner'] -= qty
        self.stats['fees'] += POS_SIZE*LEVERAGE*FEE*2*frac
        self._daily_pnl   += net; self.stats['pnl'] += net
        net_f = f"{net:+.4f}"
        runner_f = f"{t['qty_runner']:.4f}"
        log.info(f"  💰 {label} {sym}: ${net_f} | Resta:{runner_f}")
        exit_f = f"{exit_price:.6f}"
        self._tg(f"<b>💰 {label}</b> — {sym}\n${exit_f}\nPnL: ${net_f}")
        return net

    def _close_all(self, sym, exit_price, reason):
        if sym not in self.trades: return False
        t = self.trades[sym]
        qty_rem = t['qty_runner']
        if qty_rem > 0: self._order(sym,'SELL',qty_rem,'MARKET')
        frac_r = qty_rem / t['qty_total'] if t['qty_total'] > 0 else 0
        chg_r  = (exit_price - t['entry']) / t['entry']
        net_r  = POS_SIZE*LEVERAGE*chg_r*frac_r - POS_SIZE*LEVERAGE*FEE*2*frac_r
        net_total = t['pnl_parcial'] + net_r
        win = net_total > 0

        self.stats['closed'] += 1; self.stats['pnl'] += net_r
        self.stats['fees']   += POS_SIZE*LEVERAGE*FEE*2*frac_r
        self._daily_pnl      += net_r
        if win: self.stats['wins'] += 1
        else:   self.stats['losses'] += 1

        total = self.stats['wins']+self.stats['losses']
        wr    = self.stats['wins']/total*100 if total else 0
        mins  = int((datetime.now()-t['opened']).total_seconds()/60)
        emoji = "✅" if win else "❌"
        pct   = net_total/POS_SIZE*100

        net_f = f"{net_total:+.4f}"
        pct_f = f"{pct:+.1f}"
        wr_f = f"{wr:.0f}"
        log.info(f"  {emoji} {reason} | ${net_f} ({pct_f}%) | {mins}min | WR:{wr_f}%")

        self.learn.record(
            symbol=sym, score=t['score'], pnl=net_total, win=win,
            hora_utc=t.get('hora_utc',datetime.utcnow().hour),
            pts_aurolo=t.get('aurolo_pts',0),
            btc_dir=t.get('btc_dir','flat'),
            reason=reason, factors=t.get('factors',[]),
        )
        self._set_cd(sym, 'TP' if any(k in reason for k in ['TP','EMA','PROFIT']) else 'SL')

        entry_f = f"{t['entry']:.6f}"
        exit_f = f"{exit_price:.6f}"
        parcial_f = f"{t['pnl_parcial']:+.4f}"
        netr_f = f"{net_r:+.4f}"
        label = t.get('entrada_label','?')
        
        self._tg(
            f"<b>{'✅' if win else '❌'} CERRADO — {reason}</b>\n"
            f"<b>{sym}</b> | {label} | {mins}min\n"
            f"${entry_f} → ${exit_f}\n"
            f"Parcial: ${parcial_f} | Runner: ${netr_f}\n"
            f"<b>PnL: ${net_f} ({pct_f}%) | WR: {wr_f}%</b>"
        )
        if self.stats['closed'] % 3 == 0: self.learn.save()
        del self.trades[sym]
        return True

    async def monitor(self):
        for sym in list(self.trades.keys()):
            try:
                t  = self.trades[sym]
                tk = self._ticker(sym)
                if not tk: continue
                cur = tk['price']
                pct = (cur - t['entry']) / t['entry'] * 100

                c5, h5, l5, v5, _ = self._klines(sym, '5m', 80)
                if c5:
                    t['ema25'] = ema(c5, 25)
                    t['ema55'] = ema(c5, AUROLO_EMA_LEN)

                if c5 and h5 and l5 and not t.get('debilidad_alertada', False):
                    atr_live = atr_calc(h5, l5, c5, 14)
                    sig_live = aurolo_signal(c5, h5, l5, v5 or [1]*len(c5), c5, atr_live)
                    if sig_live['debilidad']:
                        t['debilidad_alertada'] = True
                        wt_live = f"{sig_live['wt_now']:.0f}"
                        adx_live = f"{sig_live['adx_now']:.0f}"
                        pct_f = f"{pct:+.2f}"
                        log.warning(f"  ⚠️ DEBILIDAD {sym}: WT={wt_live} ADX={adx_live} | {pct_f}%")
                        wt_live2 = f"{sig_live['wt_now']:.1f}"
                        adx_live2 = f"{sig_live['adx_now']:.1f}"
                        self._tg(
                            f"<b>⚠️ DEBILIDAD — {sym}</b>\n"
                            f"Momentum agotándose | {pct_f}% desde entrada\n"
                            f"WT={wt_live2} | ADX={adx_live2} cayendo\n"
                            f"Considera salida manual"
                        )
                    if sig_live['cambio_tend'] and pct > 0:
                        self._close_all(sym, cur, "CAMBIO TENDENCIA"); continue

                if cur > t['highest']: t['highest'] = cur

                if not t['tp1_hit'] and cur >= t['tp1_price']:
                    tp1_pct_i = int(TP1_PCT)
                    self._close_partial(sym, t['qty_tp1'], cur, f"TP1({tp1_pct_i}%)")
                    t['tp1_hit'] = True
                    be = t['entry'] * 1.0008
                    if be > t['sl']: t['sl'] = be
                    be_f = f"{be:.6f}"
                    log.info(f"  🔒 {sym} SL → break-even ${be_f}")
                    continue

                if t['tp1_hit'] and not t['tp2_hit'] and cur >= t['tp2_price']:
                    tp2_pct_i = int(TP2_PCT)
                    self._close_partial(sym, t['qty_tp2'], cur, f"TP2({tp2_pct_i}%)")
                    t['tp2_hit'] = True
                    locked = t['entry'] + (t['highest'] - t['entry']) * 0.5
                    if locked > t['sl']: t['sl'] = locked
                    locked_f = f"{locked:.6f}"
                    log.info(f"  🔒 {sym} SL → ${locked_f}")
                    continue

                if t['tp2_hit']:
                    if c5 and l5:
                        min_rec = min(l5[-4:]) if len(l5) >= 4 else l5[-1]
                        trailing = max(t['ema25'], min_rec * 0.999)
                        if trailing > t['sl']: t['sl'] = trailing
                    if cur < t['ema25'] and c5 and c5[-1] < t['ema25'] and c5[-2] < t['ema25']:
                        self._close_all(sym, cur, "EMA25 RUNNER"); continue

                elif t['tp1_hit']:
                    if cur < t['ema25'] and c5 and c5[-1] < t['ema25'] and c5[-2] < t['ema25']:
                        self._close_all(sym, cur, "EMA25 PRE-TP2"); continue

                elif pct > 0.5 and cur < t['ema25']:
                    if c5 and c5[-1] < t['ema25'] and c5[-2] < t['ema25']:
                        self._close_all(sym, cur, "EMA25 EARLY"); continue

                if cur <= t['sl']:
                    self._close_all(sym, cur, "STOP LOSS")

            except Exception as e:
                log.debug(f"monitor {sym}: {e}")

    def _cd_ok(self, sym):
        ts = self._cooldowns.get(sym)
        if not ts: return True
        resume, _ = ts if isinstance(ts, tuple) else (ts, 'TP')
        if time.time() >= resume: del self._cooldowns[sym]; return True
        return False

    def _set_cd(self, sym, reason='TP'):
        mins = CD_TP if reason == 'TP' else CD_SL
        self._cooldowns[sym] = (time.time() + mins*60, reason)

    def _daily_reset(self):
        today = datetime.utcnow().date()
        if today != self._daily_date:
            self._daily_pnl=0.0; self._daily_date=today
            self._cb_active=False; self._cb_until=None
            self.learn.streak=0; self._update_equity()
            log.info("📅 Nuevo día — reset diario")

    def _circuit_check(self):
        self._daily_reset()
        if self._cb_active:
            if self._cb_until and datetime.utcnow() > self._cb_until:
                self._cb_active=False; self._daily_pnl=0.0
                log.info("  🔓 Circuit breaker OFF")
                self._tg("<b>🔓 Circuit breaker OFF</b>")
            return self._cb_active
        cb_threshold = ACCOUNT_EQUITY * (CB_PCT / 100)
        if self._daily_pnl < -cb_threshold:
            self._cb_active=True
            self._cb_until=datetime.utcnow()+timedelta(hours=CB_HOURS)
            pnl_f = f"{self._daily_pnl:.3f}"
            log.warning(f"  🔒 CIRCUIT BREAKER | ${pnl_f} (>{CB_PCT}% equity)")
            self._tg(f"<b>🔒 CIRCUIT BREAKER</b>\nPérdida: ${pnl_f} ({CB_PCT}% equity) | Pausa {CB_HOURS}h")
        return self._cb_active

    def _report(self):
        if datetime.now()-self._last_report < timedelta(hours=2): return
        self._last_report = datetime.now()
        total = self.stats['wins']+self.stats['losses']
        wr    = self.stats['wins']/total*100 if total else 0
        pos   = ""
        for sym,t in self.trades.items():
            tk=self._ticker(sym); cur=tk['price'] if tk else t['entry']
            pct=(cur-t['entry'])/t['entry']*100
            tp_st = "TP1✅TP2✅" if t['tp2_hit'] else "TP1✅" if t['tp1_hit'] else "→TP1"
            pct_f = f"{pct:+.2f}"
            pts = t['aurolo_pts']
            pos += f"  📌 {sym}[{pts}/3]: {pct_f}% {tp_st}\n"

        pnl_f = f"{self.stats['pnl']:+.4f}"
        wr_i = int(wr)
        daily_f = f"{self._daily_pnl:+.4f}"
        eq_f = f"{ACCOUNT_EQUITY:.2f}"
        score_i = int(self.learn.opt_score)
        cap_i = int(self.learn._score_cap())
        btc_f = f"{self._btc_1h:+.2f}"
        
        self._tg(
            f"<b>📊 Reporte v5.6</b>\n"
            f"PnL: ${pnl_f} | WR: {wr_i}% | {total}t\n"
            f"Día: ${daily_f} | Equity: ${eq_f}\n"
            f"Score: {score_i} (cap {cap_i}) | BTC: {btc_f}%\n"
            + (pos if pos else "  Sin posiciones\n")
        )

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
        log.info("\n🚀 Bot LONGS v5.6 — Optimizado para rentabilidad\n")
        iteration=0; last_sym=last_ltv=last_hedge=last_eq=0

        while True:
            try:
                iteration += 1; self._daily_reset()
                if time.time()-last_sym   > 600:  self._refresh_symbols();       last_sym=time.time()
                if time.time()-last_ltv   > 300:  self._check_ltv();             last_ltv=time.time()
                if time.time()-last_hedge > 600:  self._scan_orphan_shorts();    last_hedge=time.time()
                if time.time()-last_eq    > 1800: self._update_equity();         last_eq=time.time()

                self._update_btc()
                if self._circuit_check():
                    await asyncio.sleep(INTERVAL); continue

                total=self.stats['wins']+self.stats['losses']
                wr=self.stats['wins']/total*100 if total else 0

                now_time = datetime.now().strftime('%H:%M:%S')
                trades_len = len(self.trades)
                pnl_f = f"{self.stats['pnl']:+.4f}"
                wr_f = f"{wr:.0f}"
                
                sep = "=" * 72
                log.info(f"\n{sep}")
                log.info(
                    f"  #{iteration} {now_time} | "
                    f"Abiertos:{trades_len}/{MAX_TRADES} | "
                    f"PnL:${pnl_f} | WR:{wr_f}%"
                )
                btc_f = f"{self._btc_1h:+.2f}"
                eq_f = f"{ACCOUNT_EQUITY:.2f}"
                score_i = int(self.learn.opt_score)
                cap_i = int(self.learn._score_cap())
                log.info(
                    f"  BTC:{btc_f}% | Equity:${eq_f} | "
                    f"Score:{score_i}/{cap_i}"
                )
                log.info(f"{sep}\n")

                await self.monitor()
                self._report()

                if len(self.trades) < MAX_TRADES:
                    syms_len = len(self.symbols)
                    log.info(f"  Escaneando {syms_len} símbolos...")
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.trades) >= MAX_TRADES: break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            sig_i = int(sig['score'])
                            opt_i = int(self.learn.opt_score)
                            rr_f = f"{sig['rr']:.2f}"
                            wt_i = int(sig['aurolo_wt'])
                            adx_i = int(sig['aurolo_adx'])
                            log.info(
                                f"  💡 {sym} [{sig['aurolo_señal']}] | "
                                f"Score:{sig_i}/{opt_i} | "
                                f"RR:{rr_f}:1 | "
                                f"WT={wt_i} ADX={adx_i}"
                            )
                            if self.open_trade(sym, sig):
                                await asyncio.sleep(3)
                        if (i+1) % 15 == 0:
                            log.info(f"  ...{i+1}/{syms_len}")
                        await asyncio.sleep(0.12)
                    log.info(f"  ✅ Scan: {found} señales")
                else:
                    log.info("  ⏸️ Max trades — monitoreando")

                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt: log.info("⏹️ Detenido"); break
            except Exception as e:
                log.error(f"❌ Error #{iteration}: {e}", exc_info=True)
                await asyncio.sleep(20)

        self.learn.save()


# ============================================================================
# ENTRY POINT
# ============================================================================

async def main():
    bot = LongBot()
    await bot.run()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("👋 Bot terminado")
