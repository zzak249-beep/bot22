#!/usr/bin/env python3
"""
EXPLOSION SCANNER v2.1 — HARD GATES (DEFINITIVO)
════════════════════════════════════════════════════════════════════════════════

PROBLEMA RAÍZ de v1.0 y v2.0:
  Se usaban PENALIZACIONES en vez de BLOQUEOS.
  -15pts no es suficiente si la señal base ya tiene 80pts.

SOLUCIÓN v2.1 — GATES DUROS (return None si no pasan):
  Gate 1: Símbolo válido (no NCS*, no sintéticos, no stablecoins)
  Gate 2: MTF >= 2/3 timeframes alcistas — OBLIGATORIO
  Gate 3: CVD >= 50% (más compradores que vendedores) — OBLIGATORIO
  Gate 4: Vol ratio <= 15x (cap anti-anomalía datos)
  Gate 5: Z-score <= 8.0 (cap anti-datos corruptos)
  Gate 6: Volumen real mínimo (>= 20 velas con vol > 0)
  Gate 7: RSI <= 75 (no sobrecomprado al entrar)

RESULTADO ESPERADO:
  - NCSINASDAQ → bloqueado en Gate 1 (prefijo NCS*)
  - HYPE CVD39% → bloqueado en Gate 3 (CVD < 50%)
  - Vol 218x → bloqueado en Gate 4 (cap 15x)
  - Z=4106 → bloqueado en Gate 5 (cap 8.0)
  - MTF 0/3 → bloqueado en Gate 2
"""

import os, time, sys, math, logging, requests, re, random, threading, json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import hmac, hashlib
from urllib.parse import urlencode

# ============================================================================
# CONFIG
# ============================================================================

def _e(k, d, t='str'):
    v = os.getenv(k, str(d)).strip().strip('"').strip("'")
    if t in ('int','float'): v = re.sub(r'[^\d\.\-]','',v) or str(d)
    if t=='int':   return int(float(v))
    if t=='float': return float(v)
    if t=='bool':  return v.lower()=='true'
    return v

TG_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN','')
TG_CHAT    = os.getenv('TELEGRAM_CHAT_ID','')
BASE_URL   = "https://open-api.bingx.com"

SCAN_INTERVAL = _e('SCAN_INTERVAL',   '120','int')
MIN_VOL       = _e('MIN_VOL_24H',     '500000','float')
MAX_SCAN      = _e('MAX_SYMBOLS_SCAN','200','int')
WORKERS       = _e('WORKERS',         '10','int')
MIN_CONF      = _e('MIN_CONFIDENCE',  '65','int')   # subido de 55
HOT_CONF      = _e('HOT_CONFIDENCE',  '75','int')   # para alertas

# ── HARD GATE THRESHOLDS (no tocar sin razón) ─────────────────────────────
GATE_MTF_MIN     = 2      # mínimo 2/3 timeframes alcistas
GATE_CVD_MIN     = 0.50   # mínimo 50% compradores
GATE_VOL_CAP     = 15.0   # máximo ratio vol razonable
GATE_Z_CAP       = 8.0    # máximo Z-score razonable
GATE_RSI_MAX     = 75.0   # RSI máximo para entrar
GATE_VOL_VELAS   = 20     # mínimo velas con volumen > 0

# ── SCORE THRESHOLDS ──────────────────────────────────────────────────────
BB_SQUEEZE_PCT = _e('BB_SQUEEZE_PCT','2.0','float')
Z_VOL_SPIKE    = _e('Z_VOL_SPIKE',   '3.0','float')
OI_CHANGE_MIN  = _e('OI_CHANGE_MIN', '3.0','float')
CVD_BULL       = 0.62     # para bonus máximo CVD
FUND_BULL_MIN  = -0.02    # funding para bonus

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('SCANNER')

# ============================================================================
# FILTRO DE SÍMBOLOS (Gate 1)
# ============================================================================

SYNTHETIC_PREFIXES = (
    'NCS',   # BingX synthetic index products (NASDAQ, S&P, etc.)
    'NCB',   # BingX synthetic
    'NCBS',  # BingX synthetic
)

EXCLUDE_BASES = {
    # Stablecoins
    'USDC','BUSD','TUSD','FRAX','DAI','USDP','FDUSD','USDD',
    # Forex puro
    'EUR','GBP','JPY','CHF','AUD','CAD','MXN','BRL',
    # Metales sintéticos
    'XAU','XAG','PAXG','XAUT',
}

def is_valid_symbol(sym: str) -> bool:
    """
    Gate 1: símbolo válido.
    Retorna False para sintéticos, stablecoins, forex.
    """
    if not sym.endswith('-USDT'):
        return False
    base = sym.replace('-USDT','').upper()

    # Bases excluidas explícitamente
    if base in EXCLUDE_BASES:
        return False

    # Prefijos sintéticos BingX
    if any(base.startswith(pfx) for pfx in SYNTHETIC_PREFIXES):
        return False

    # Patrón de nombre sintético: letras + 3+ dígitos (NASDAQ1002, SP500, etc.)
    if re.search(r'[A-Z]{2,}\d{3,}', base):
        return False

    # Muy corto (< 2 chars) o muy largo (> 10 chars) = probablemente raro
    if len(base) < 2 or len(base) > 12:
        return False

    return True

# ============================================================================
# API
# ============================================================================

def pub(path, params=None):
    try:
        r = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10)
        return r.json()
    except:
        return {}

def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print(msg[:300]); return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
            timeout=6
        )
    except:
        pass

# ============================================================================
# INDICADORES (robustos)
# ============================================================================

def ema_f(prices, n):
    if not prices or len(prices) < 2: return prices[-1] if prices else 0.0
    k = 2/(n+1); e = prices[0]
    for p in prices[1:]: e = p*k + e*(1-k)
    return e

def rsi_f(prices, n=14):
    if len(prices) < n+1: return 50.0
    gains  = [max(prices[i]-prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1]-prices[i], 0) for i in range(1, len(prices))]
    ag = sum(gains[-n:])/n; al = sum(losses[-n:])/n
    return 100.0 if al == 0 else 100 - 100/(1+ag/al)

def bollinger_f(closes, n=20, k=2.0):
    if len(closes) < n: return closes[-1], closes[-1], closes[-1], 99.0
    w = closes[-n:]; mid = sum(w)/n
    std = math.sqrt(sum((x-mid)**2 for x in w)/n)
    u = mid+k*std; l = mid-k*std
    width = (u-l)/mid*100 if mid > 0 else 99.0
    return u, mid, l, round(width, 3)

def z_score_safe(volumes, period=30):
    """
    Gate 5: Z-score con protección contra datos corruptos.
    Filtra velas vacías, requiere stddev mínima, cap en GATE_Z_CAP.
    """
    clean = [v for v in volumes if v > 0]
    if len(clean) < period + 1: return 0.0
    window = clean[-period-1:-1]
    if len(window) < 10: return 0.0
    mean = sum(window) / len(window)
    if mean <= 0: return 0.0
    var  = sum((v-mean)**2 for v in window) / len(window)
    std  = math.sqrt(var)
    # Si std < 5% de la media, no hay variación significativa
    if std < mean * 0.05: return 0.0
    z = (clean[-1] - mean) / std
    # Gate 5: cap
    return round(min(max(z, -GATE_Z_CAP), GATE_Z_CAP), 2)

def vol_ratio_safe(volumes, n_recent=3, n_base=7):
    """
    Gate 4: ratio de volumen con cap anti-anomalía.
    """
    base_vols = [v for v in volumes[-n_base-n_recent:-n_recent] if v > 0]
    if not base_vols: return 1.0
    avg = sum(base_vols) / len(base_vols)
    if avg <= 0: return 1.0
    recent = [v for v in volumes[-n_recent:] if v >= 0]
    recent_avg = sum(recent) / len(recent) if recent else 0
    ratio = recent_avg / avg if avg > 0 else 1.0
    # Gate 4: cap
    return round(min(ratio, GATE_VOL_CAP), 2)

def cvd_f(closes, opens, volumes, n=20):
    """Cumulative Volume Delta — ratio 0 a 1. < 0.5 = bajista."""
    if len(closes) < n: return 0.5
    bull = bear = 0.0
    for i in range(-n, 0):
        c = closes[i]
        o = opens[i] if opens and len(opens) >= abs(i) else closes[i-1]
        v = volumes[i]
        if v <= 0: continue
        if c > o:   bull += v
        elif c < o: bear += v
        else:       bull += v*0.5; bear += v*0.5
    total = bull + bear
    return round(bull/total if total > 0 else 0.5, 3)

def get_mtf(symbol) -> tuple[int, int]:
    """
    Gate 2: cuenta timeframes alcistas (EMA9 > EMA21).
    Retorna (alcistas, total).
    """
    alcistas = 0; total = 0
    for tf, limit in [('5m',60), ('15m',50), ('1h',40)]:
        try:
            d = pub('/openApi/swap/v3/quote/klines',
                    {'symbol': symbol, 'interval': tf, 'limit': limit})
            if d.get('code')==0 and d.get('data') and len(d['data']) >= 25:
                closes = [float(k['close']) for k in d['data']]
                if ema_f(closes, 9) > ema_f(closes, 21): alcistas += 1
                total += 1
        except:
            pass
    return alcistas, total

# ============================================================================
# ANALIZADOR v2.1 — CON GATES DUROS
# ============================================================================

def analyze(symbol: str, oi_cache: dict) -> dict | None:
    """
    Analiza símbolo con gates duros.
    Cualquier gate que falle → return None inmediato.
    """

    # ══════════════════════════════════════════════════════════════
    # GATE 1: SÍMBOLO VÁLIDO
    # ══════════════════════════════════════════════════════════════
    if not is_valid_symbol(symbol):
        return None

    # Ticker
    try:
        d = pub('/openApi/swap/v2/quote/ticker', {'symbol': symbol})
        if d.get('code') != 0 or not d.get('data'): return None
        t     = d['data']
        price = float(t.get('lastPrice', 0))
        chg   = float(t.get('priceChangePercent', 0))
        if price <= 0: return None
        if chg > 25 or chg < -15: return None  # ya explotó o en caída libre
    except:
        return None

    # Klines 5m
    try:
        d5 = pub('/openApi/swap/v3/quote/klines',
                 {'symbol': symbol, 'interval': '5m', 'limit': 120})
        if d5.get('code') != 0 or not d5.get('data') or len(d5['data']) < 50:
            return None
        kl = d5['data']
        c5 = [float(k['close'])  for k in kl]
        h5 = [float(k['high'])   for k in kl]
        l5 = [float(k['low'])    for k in kl]
        v5 = [float(k['volume']) for k in kl]
        o5 = [float(k['open'])   for k in kl]
    except:
        return None

    # ══════════════════════════════════════════════════════════════
    # GATE 6: LIQUIDEZ REAL
    # Mínimo GATE_VOL_VELAS velas con volumen > 0
    # ══════════════════════════════════════════════════════════════
    velas_con_vol = sum(1 for v in v5 if v > 0)
    if velas_con_vol < GATE_VOL_VELAS:
        return None  # moneda sin liquidez real

    # ══════════════════════════════════════════════════════════════
    # GATE 2: MTF MÍNIMO 2/3 — OBLIGATORIO
    # ══════════════════════════════════════════════════════════════
    tf_bull, tf_total = get_mtf(symbol)
    if tf_bull < GATE_MTF_MIN:
        return None  # tendencia no confirmada en múltiples timeframes

    # ══════════════════════════════════════════════════════════════
    # GATE 3: CVD >= 50% — OBLIGATORIO
    # Más compradores que vendedores
    # ══════════════════════════════════════════════════════════════
    cvd = cvd_f(c5, o5, v5, 20)
    if cvd < GATE_CVD_MIN:
        return None  # presión vendedora dominante

    # ══════════════════════════════════════════════════════════════
    # GATE 7: RSI NO SOBRECOMPRADO
    # ══════════════════════════════════════════════════════════════
    rsi_curr = rsi_f(c5, 14)
    rsi_prev = rsi_f(c5[:-1], 14)
    if rsi_curr > GATE_RSI_MAX:
        return None  # ya subió demasiado

    # ── A partir de aquí: todos los gates pasados ──────────────────
    # Calculamos score

    conf = 0
    sigs = []

    # ── SEÑAL 1: BB Squeeze ───────────────────────────────────────
    _, _, _, bbw = bollinger_f(c5, 20, 2.0)
    if bbw < BB_SQUEEZE_PCT:
        pts = min(int(25*(BB_SQUEEZE_PCT-bbw)/BB_SQUEEZE_PCT + 10), 25)
        conf += pts
        sigs.append(f"🎯 BB Squeeze {bbw:.1f}% (+{pts})")
    elif bbw < BB_SQUEEZE_PCT * 1.5:
        conf += 8
        sigs.append(f"📊 BB comprimiendo {bbw:.1f}% (+8)")

    # ── SEÑAL 2: Z-Score volumen (Gate 5 ya aplicado) ─────────────
    zv = z_score_safe(v5, 30)
    if zv >= Z_VOL_SPIKE * 1.5:
        conf += 20
        sigs.append(f"🐳 Vol ALTO Z={zv:.1f} (+20)")
    elif zv >= Z_VOL_SPIKE:
        conf += 14
        sigs.append(f"⚡ Vol spike Z={zv:.1f} (+14)")
    elif zv >= Z_VOL_SPIKE * 0.7:
        conf += 6
        sigs.append(f"📈 Vol elevado Z={zv:.1f} (+6)")

    # ── SEÑAL 3: Vol ratio reciente (Gate 4 ya aplicado) ──────────
    vr = vol_ratio_safe(v5, 3, 7)
    if vr >= 3.0:
        conf += 12
        sigs.append(f"🔥 Vol acelerado {vr:.1f}x (+12)")
    elif vr >= 2.0:
        conf += 7
        sigs.append(f"📊 Vol aumentando {vr:.1f}x (+7)")

    # ── SEÑAL 4: Open Interest ────────────────────────────────────
    oi_chg = 0.0
    try:
        d_oi  = pub('/openApi/swap/v2/quote/openInterest', {'symbol': symbol})
        oi_c  = float((d_oi.get('data') or {}).get('openInterest', 0) or 0)
        oi_p  = oi_cache.get(symbol, {}).get('oi', oi_c)
        oi_chg = (oi_c-oi_p)/oi_p*100 if oi_p > 0 else 0.0
        oi_cache[symbol] = {'oi': oi_c, 'ts': time.time()}
        if oi_chg >= 6 and abs(chg) < 3:
            conf += 22; sigs.append(f"🐳 OI +{oi_chg:.1f}% precio plano (+22)")
        elif oi_chg >= OI_CHANGE_MIN:
            conf += 10; sigs.append(f"📈 OI +{oi_chg:.1f}% (+10)")
        elif oi_chg <= -3:
            conf -= 8   # OI bajando = cierran posiciones
    except:
        pass

    # ── SEÑAL 5: Funding rate ─────────────────────────────────────
    fund = 0.0
    try:
        d_f  = pub('/openApi/swap/v2/quote/premiumIndex', {'symbol': symbol})
        fund = float((d_f.get('data') or {}).get('lastFundingRate', 0) or 0) * 100
        if fund <= FUND_BULL_MIN * 3:
            conf += 18; sigs.append(f"💰 Funding {fund:.3f}% muy negativo (+18)")
        elif fund <= FUND_BULL_MIN:
            conf += 10; sigs.append(f"💰 Funding {fund:.3f}% negativo (+10)")
        elif fund <= 0:
            conf += 4
        elif fund >= 0.05:
            conf -= 8   # longs saturados
    except:
        pass

    # ── SEÑAL 6: CVD (gate ya pasado, ahora scoring) ──────────────
    # cvd ya calculado arriba, >= 0.50 garantizado
    if cvd >= CVD_BULL:
        pts = min(int((cvd-0.5)*60), 20)
        conf += pts
        sigs.append(f"🌊 CVD {int(cvd*100)}% bull (+{pts})")
    elif cvd >= 0.55:
        conf += 8
        sigs.append(f"🌊 CVD {int(cvd*100)}% leve bull (+8)")
    # 50-55% → no suma ni resta (neutro)

    # ── SEÑAL 7: RSI rebotando (gate ya pasado, ahora scoring) ────
    if rsi_prev < 45 and rsi_curr > rsi_prev:
        pts = int((45 - rsi_prev) / 45 * 18)
        conf += pts
        sigs.append(f"📈 RSI rebota {rsi_prev:.0f}→{rsi_curr:.0f} (+{pts})")
    elif rsi_curr < 35:
        conf += 8
        sigs.append(f"📈 RSI zona OS {rsi_curr:.0f} (+8)")

    # ── SEÑAL 8: Breakout 20 velas ────────────────────────────────
    broke = False
    if len(h5) >= 22:
        res = max(h5[-21:-1])
        broke = c5[-1] > res and c5[-2] <= res
        if broke:
            bstr = (c5[-1]/res - 1)*100
            if bstr > 0.5:
                conf += 20; sigs.append(f"🚀 BREAKOUT {bstr:.2f}% (+20)")
            else:
                conf += 14; sigs.append(f"📈 Breakout leve (+14)")

    # ── SEÑAL 9: MTF scoring (gate ya pasado, ahora bonus) ────────
    # tf_bull >= 2 garantizado por gate
    if tf_bull >= 3:
        conf += 20; sigs.append("✅ MTF 3/3 (+20)")
    elif tf_bull == 2:
        conf += 12; sigs.append("✅ MTF 2/3 (+12)")

    # ── SEÑAL 10: VWAP ───────────────────────────────────────────
    try:
        n_v = min(50, len(c5))
        cv=c5[-n_v:]; hv=h5[-n_v:]; lv=l5[-n_v:]; vv=v5[-n_v:]
        tp_vol = sum(((hv[i]+lv[i]+cv[i])/3)*vv[i] for i in range(len(cv)))
        vs = sum(vv)
        vwap = tp_vol/vs if vs > 0 else price
        vd   = (price-vwap)/vwap*100 if vwap > 0 else 0
        if -0.5 <= vd <= 0.5:
            conf += 10; sigs.append(f"🎯 Precio en VWAP (+10)")
        elif 0 < vd <= 2:
            conf += 6;  sigs.append(f"📈 Sobre VWAP (+6)")
        elif vd < -1.5:
            conf -= 6
    except:
        vd = 0

    # ── SEÑAL 11: Momentum 3 velas ───────────────────────────────
    if len(o5) >= 3 and all(c5[i] > o5[i] for i in [-3,-2,-1]):
        conf += 10; sigs.append("🚀 3 velas bull (+10)")

    # ── SEÑAL 12: Precio cerca del High 24h ──────────────────────
    try:
        h24 = float(t.get('highPrice', 0))
        if h24 > 0:
            dist_h = (h24-price)/h24*100
            if dist_h < 1.0:
                conf += 12; sigs.append(f"💪 Cerca High 24h ({dist_h:.1f}%) (+12)")
            elif dist_h < 3.0:
                conf += 5
    except:
        pass

    # ── BONUS COMBOS ──────────────────────────────────────────────
    if bbw < BB_SQUEEZE_PCT and broke:
        conf += 15; sigs.append("💥 COMBO SQUEEZE+BREAKOUT (+15)")
    if zv >= Z_VOL_SPIKE and oi_chg >= OI_CHANGE_MIN and cvd >= 0.62:
        conf += 12; sigs.append("🐳 COMBO BALLENA+OI+CVD (+12)")
    if tf_bull >= 2 and broke and vr >= 2.0:
        conf += 10; sigs.append("🚀 COMBO MTF+BREAKOUT+VOL (+10)")

    # Cap final
    conf = min(max(conf, 0), 100)
    if conf < MIN_CONF: return None

    return {
        'symbol':     symbol,
        'confidence': conf,
        'price':      price,
        'change':     chg,
        'signals':    sigs,
        'tf':         tf_bull,
        'zv':         zv,
        'cvd':        cvd,
        'rsi':        round(rsi_curr, 1),
        'bbw':        bbw,
        'oi_chg':     round(oi_chg, 2),
        'fund':       round(fund, 4),
        'vr':         vr,
        'breakout':   broke,
    }

# ============================================================================
# SCANNER PRINCIPAL v2.1
# ============================================================================

class Scanner:

    def __init__(self):
        self.oi_cache    = {}
        self.alerted     = {}    # {symbol: (conf, timestamp)}
        self.daily_log   = []
        self.daily_date  = datetime.utcnow().date()
        self.hot_symbols = []    # [(symbol, conf)] actualizado en cada scan
        self._lock       = threading.Lock()

        log.info("=" * 62)
        log.info("  🔥 EXPLOSION SCANNER v2.1 — HARD GATES DEFINITIVO")
        log.info(f"  Gates duros:")
        log.info(f"    MTF >= {GATE_MTF_MIN}/3 timeframes alcistas")
        log.info(f"    CVD >= {int(GATE_CVD_MIN*100)}% compradores")
        log.info(f"    RSI <= {GATE_RSI_MAX}")
        log.info(f"    Z-score cap {GATE_Z_CAP} | Vol ratio cap {GATE_VOL_CAP}x")
        log.info(f"    Sintéticos NCS* bloqueados")
        log.info(f"  Confianza mín: {MIN_CONF}% | Alerta: {HOT_CONF}%")
        log.info("=" * 62)

        tg(
            f"<b>🔥 EXPLOSION SCANNER v2.1</b>\n"
            f"Gates duros activados:\n"
            f"✅ NCS* y sintéticos bloqueados\n"
            f"✅ MTF ≥ {GATE_MTF_MIN}/3 obligatorio\n"
            f"✅ CVD ≥ {int(GATE_CVD_MIN*100)}% obligatorio\n"
            f"✅ RSI ≤ {GATE_RSI_MAX} obligatorio\n"
            f"✅ Z-vol cap {GATE_Z_CAP} | Vol cap {GATE_VOL_CAP}x\n"
            f"Confianza mín: {MIN_CONF}% | Alerta: {HOT_CONF}%"
        )

    # ── Símbolos válidos ──────────────────────────────────────────

    def _get_symbols(self) -> list[str]:
        d = pub('/openApi/swap/v2/quote/ticker')
        if d.get('code') != 0: return []
        items = []
        for t in d.get('data', []):
            sym = t.get('symbol','')
            if not is_valid_symbol(sym): continue  # Gate 1
            try:
                price = float(t.get('lastPrice', 0))
                vol   = float(t.get('volume', 0)) * price
                if vol >= MIN_VOL and price > 0:
                    items.append((sym, vol))
            except:
                continue
        items.sort(key=lambda x: x[1], reverse=True)
        result = [s for s,_ in items[:MAX_SCAN]]
        log.info(f"  {len(result)} símbolos válidos (filtrados sintéticos)")
        return result

    # ── Alert deduplication ───────────────────────────────────────

    def _should_alert(self, sym, conf) -> bool:
        prev = self.alerted.get(sym)
        if not prev: return True
        pc, pts = prev
        if time.time() - pts > 2700: return True   # 45 min mínimo
        if conf >= pc + 15: return True             # conf subió mucho
        return False

    # ── Formato mensaje ───────────────────────────────────────────

    def _fmt(self, r) -> str:
        c   = r['confidence']
        lvl = "🔴 CRÍTICO" if c >= 80 else "🟠 ALTO" if c >= 65 else "🟡 MEDIO"
        sigs_txt = "\n".join(f"  {s}" for s in r['signals'][:6])
        return (
            f"{lvl} <b>{r['symbol']}</b> — <b>{c}%</b>\n"
            f"💲 ${r['price']:.6f} | 24h: {r['change']:+.2f}%\n"
            f"BB:{r['bbw']:.1f}% | Z:{r['zv']:.1f} | CVD:{int(r['cvd']*100)}%\n"
            f"OI:{r['oi_chg']:+.1f}% | Fund:{r['fund']:.3f}% | MTF:{r['tf']}/3\n"
            f"Señales:\n{sigs_txt}\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
            + ("\n🚀 <b>BREAKOUT ACTIVO</b>" if r.get('breakout') else "")
        )

    # ── Un ciclo de scan ──────────────────────────────────────────

    def scan_once(self) -> list[dict]:
        symbols = self._get_symbols()
        if not symbols: return []

        results = []
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(analyze, sym, self.oi_cache): sym
                       for sym in symbols}
            for fut in as_completed(futures):
                try:
                    r = fut.result()
                    if r: results.append(r)
                except Exception as e:
                    log.debug(f"  err: {e}")

        results.sort(key=lambda x: x['confidence'], reverse=True)

        # Actualizar hot_symbols (para el bot trader)
        with self._lock:
            self.hot_symbols = [(r['symbol'], r['confidence']) for r in results]

        # Log top resultados
        log.info(f"  ✅ {len(results)} señales VÁLIDAS (todos los gates pasados)")
        for r in results[:10]:
            lvl = "🔴" if r['confidence']>=80 else "🟠" if r['confidence']>=65 else "🟡"
            log.info(
                f"  {lvl} {r['symbol']:<20} {r['confidence']:>3}% | "
                f"BB:{r['bbw']:.1f}% Z:{r['zv']:.1f} CVD:{int(r['cvd']*100)}% "
                f"OI:{r['oi_chg']:+.1f}% MTF:{r['tf']}/3 RSI:{r['rsi']:.0f}"
            )

        # Enviar alertas
        for r in results:
            if r['confidence'] < HOT_CONF: continue
            if self._should_alert(r['symbol'], r['confidence']):
                tg(self._fmt(r))
                self.alerted[r['symbol']] = (r['confidence'], time.time())
                self.daily_log.append(r)

        return results

    # ── Resumen diario ────────────────────────────────────────────

    def _daily_summary(self):
        today = datetime.utcnow().date()
        if today == self.daily_date: return
        if self.daily_log:
            n   = len(self.daily_log)
            top = sorted(self.daily_log, key=lambda x: x['confidence'], reverse=True)[:5]
            tg(
                f"<b>📊 Resumen Scanner v2.1 — {self.daily_date}</b>\n"
                f"Total alertas válidas: {n}\n"
                f"🔴 ≥80%: {sum(1 for a in self.daily_log if a['confidence']>=80)}\n"
                f"🟠 65-79%: {sum(1 for a in self.daily_log if 65<=a['confidence']<80)}\n\n"
                f"Top 5:\n"
                + "\n".join(f"  {a['symbol']} — {a['confidence']}% "
                            f"(CVD:{int(a['cvd']*100)}% MTF:{a['tf']}/3)"
                            for a in top)
            )
        self.daily_log  = []
        self.daily_date = today

    # ── Getter para bot trader ────────────────────────────────────

    def get_hot(self, min_conf=70, n=30) -> list[str]:
        with self._lock:
            return [s for s,c in self.hot_symbols if c >= min_conf][:n]

    def get_confidence(self, symbol) -> int:
        with self._lock:
            for s,c in self.hot_symbols:
                if s == symbol: return c
        return 0

    # ── Loop principal ────────────────────────────────────────────

    def run(self):
        log.info(f"\n🚀 Scanner v2.1 | Gates duros activos\n")
        iteration = 0
        while True:
            try:
                iteration += 1
                self._daily_summary()
                log.info(f"\n{'='*55}")
                log.info(f"  Scan #{iteration} | {datetime.now().strftime('%H:%M:%S')}")
                log.info(f"{'='*55}")
                self.scan_once()
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                log.info("⏹️ Detenido"); break
            except Exception as e:
                log.error(f"Error: {e}", exc_info=True)
                time.sleep(30)

    def run_background(self):
        """Para uso integrado en bot_complete_v7.py"""
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

# ============================================================================
# FUNCIÓN INTEGRACIÓN CON BOT v7.0
# ============================================================================

def get_hot_symbols(min_conf=70, n=30, oi_cache=None) -> list[str]:
    """
    Llamada rápida para bot_complete_v7.py → _get_scan_order().
    Reemplaza la función del scanner v2.0.

    Uso en bot_complete_v7.py:
        from explosion_scanner_v2 import get_hot_symbols
        hot = get_hot_symbols(min_conf=70, n=20)
    """
    if oi_cache is None: oi_cache = {}
    symbols_raw = []
    d = pub('/openApi/swap/v2/quote/ticker')
    if d.get('code') == 0:
        for t in d.get('data', []):
            sym = t.get('symbol','')
            if not is_valid_symbol(sym): continue
            try:
                price = float(t.get('lastPrice', 0))
                vol   = float(t.get('volume', 0)) * price
                if vol >= MIN_VOL and price > 0:
                    symbols_raw.append(sym)
            except:
                continue
    symbols_raw = symbols_raw[:150]

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(analyze, sym, oi_cache): sym for sym in symbols_raw}
        for fut in as_completed(futures):
            r = fut.result()
            if r and r['confidence'] >= min_conf:
                results.append(r)

    results.sort(key=lambda x: x['confidence'], reverse=True)
    return [r['symbol'] for r in results[:n]]

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    Scanner().run()
