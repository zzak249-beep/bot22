#!/usr/bin/env python3
"""
EXPLOSION SCANNER v1.0 — BINGX PUMP DETECTOR
════════════════════════════════════════════════════════════════════════════════

Las monedas que "explotan" (+10-40% en horas) dejan SIEMPRE estas huellas
30-90 minutos ANTES del movimiento:

1. 📦 COMPRESIÓN VOLUMEN → Rango estrecho + volumen cayendo = muelle tensado
2. 🐳 BALLENA SILENCIOSA → OI sube pero precio estable = acumulación institucional
3. ⚡ Z-SCORE VOLUMEN SPIKE → Volumen 3-5x de golpe sin pump = manos fuertes entrando
4. 🔥 FUNDING NEGATIVO → Shorts pagando longs = presión compradora inminente
5. 📊 BREAKOUT ESTRUCTURA → Precio rompe resistencia clave con volumen
6. 🌊 CVD DIVERGENCIA → Precio lateral pero compradores acumulando
7. 🎯 SQUEEZE BANDAS BOLLINGER → BB muy estrecho = breakout inminente
8. 📈 RSI HIDDEN BULLISH DIVERGENCE → Precio baja pero RSI sube = suelo

CÓMO USARLO:
  - Corre en PARALELO al bot principal (Railway deployment separado)
  - Envía alertas Telegram con nivel de confianza 1-100
  - No abre trades por sí solo — te avisa TÚ decides
  - O integrado al bot para auto-entrada en las mejores señales
"""

import os, time, sys, math, logging, requests, json, re, random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict, deque
from urllib.parse import urlencode
import hmac, hashlib

# ============================================================================
# CONFIG
# ============================================================================

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default)).strip().strip('"').strip("'")
    if typ in ('int', 'float'): v = re.sub(r'[^\d\.-]', '', v) or str(default)
    if typ == 'int':   return int(float(v))
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

TG_TOKEN  = os.getenv('TELEGRAM_BOT_TOKEN', '')
TG_CHAT   = os.getenv('TELEGRAM_CHAT_ID', '')
API_KEY   = os.getenv('BINGX_API_KEY', '').strip().strip('"')
API_SECRET= os.getenv('BINGX_API_SECRET', '').strip().strip('"')
BASE_URL  = "https://open-api.bingx.com"

# Parámetros del scanner
SCAN_INTERVAL    = clean('SCAN_INTERVAL',    '120', 'int')   # cada 2 min
MIN_VOL_24H      = clean('MIN_VOL_24H',      '500000', 'float')  # $500K mínimo
MAX_SYMBOLS_SCAN = clean('MAX_SYMBOLS_SCAN', '200',  'int')   # top 200 por volumen
WORKERS          = clean('WORKERS',          '10',   'int')
MIN_CONFIDENCE   = clean('MIN_CONFIDENCE',   '55',   'int')   # % mínimo para alertar
AUTO_TRADE       = clean('AUTO_TRADE',       'false','bool')  # auto-entrada en señales ≥80

# Umbrales de detección
Z_VOL_SPIKE      = clean('Z_VOL_SPIKE',      '3.0',  'float')  # Z-score volumen
OI_CHANGE_MIN    = clean('OI_CHANGE_MIN',    '3.0',  'float')  # % cambio OI
BB_SQUEEZE_PCT   = clean('BB_SQUEEZE_PCT',   '2.0',  'float')  # ancho BB < 2%
CVD_IMBAL        = clean('CVD_IMBAL',        '0.65', 'float')  # 65%+ compradores
FUNDING_BULL_MIN = clean('FUNDING_BULL_MIN', '-0.02','float')  # funding negativo = bull
RSI_HIDDEN_MAX   = clean('RSI_HIDDEN_MAX',   '45',   'float')  # RSI zona oversold

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('EXPLOSION')

# ============================================================================
# API
# ============================================================================

def pub(path, params=None):
    try:
        r = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10)
        return r.json()
    except: return {}

def priv(method, endpoint, params=None):
    params = params or {}
    try:
        p   = {**{k: str(v) for k, v in params.items()},
               'timestamp': str(int(time.time() * 1000))}
        qs  = urlencode(sorted(p.items()))
        sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
        url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
        hdr = {'X-BX-APIKEY': API_KEY}
        r   = getattr(requests, method.lower())(url, headers=hdr, timeout=10)
        return r.json()
    except: return {}

def tg(msg):
    if not TG_TOKEN or not TG_CHAT: print(msg); return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
            timeout=6
        )
    except: pass

# ============================================================================
# INDICADORES TÉCNICOS
# ============================================================================

def ema_v(prices, n):
    if len(prices) < 2: return prices[-1] if prices else 0
    k, e = 2/(n+1), prices[0]
    for p in prices[1:]: e = p*k + e*(1-k)
    return e

def sma(prices, n):
    w = prices[-n:] if len(prices) >= n else prices
    return sum(w) / len(w)

def rsi_v(prices, n=14):
    if len(prices) < n+1: return 50.0
    gains  = [max(prices[i]-prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1]-prices[i], 0) for i in range(1, len(prices))]
    ag = sum(gains[-n:])/n; al = sum(losses[-n:])/n
    return 100.0 if al == 0 else 100 - 100/(1+ag/al)

def atr_v(highs, lows, closes, n=14):
    if len(closes) < 2: return 0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, min(len(closes), n+1))]
    return sum(trs)/len(trs) if trs else 0

def bollinger_bands(closes, n=20, k=2.0):
    if len(closes) < n: return closes[-1], closes[-1], closes[-1], 99.9
    w   = closes[-n:]
    mid = sum(w)/n
    std = math.sqrt(sum((x-mid)**2 for x in w)/n)
    upper = mid + k*std; lower = mid - k*std
    width_pct = (upper-lower)/mid*100 if mid > 0 else 99
    return upper, mid, lower, width_pct

def z_score_volume(volumes, period=30):
    if len(volumes) < period+1: return 0.0
    window = volumes[-period-1:-1]
    mean   = sum(window)/len(window)
    var    = sum((v-mean)**2 for v in window)/len(window)
    std    = math.sqrt(var) if var > 0 else 1e-10
    return (volumes[-1]-mean)/std

def cvd_ratio(closes, opens, volumes, n=20):
    """Ratio de volumen comprador vs total."""
    if len(closes) < n: return 0.5
    bull = bear = 0.0
    for i in range(-n, 0):
        c = closes[i]; o = opens[i] if opens else closes[i-1]; v = volumes[i]
        if c > o:   bull += v
        elif c < o: bear += v
        else:       bull += v*0.5; bear += v*0.5
    total = bull+bear
    return bull/total if total > 0 else 0.5

def hidden_bull_divergence(closes, lows, rsi_period=14, lookback=30):
    """
    Divergencia alcista oculta: precio hace mínimo más alto,
    RSI hace mínimo más bajo → señal de continuación alcista.
    """
    if len(closes) < lookback+rsi_period: return False, 0.0
    rsi_series = []
    for i in range(len(closes)-lookback, len(closes)):
        rsi_series.append(rsi_v(closes[max(0,i-rsi_period-1):i+1]))
    if len(rsi_series) < lookback//2: return False, 0.0
    # Buscar dos mínimos
    mid = len(rsi_series)//2
    price_low1 = min(lows[len(lows)-lookback:len(lows)-mid])
    price_low2 = min(lows[len(lows)-mid:])
    rsi_low1   = min(rsi_series[:mid])
    rsi_low2   = min(rsi_series[mid:])
    # Hidden bullish: precio hace low2 > low1 PERO rsi hace low2 < low1
    divergence = price_low2 > price_low1 * 1.001 and rsi_low2 < rsi_low1 * 0.98
    strength   = (price_low2/price_low1 - 1) * 100 if divergence else 0.0
    return divergence, round(strength, 2)

def vwap_distance(closes, highs, lows, volumes, n=50):
    """Distancia del precio al VWAP. Negativo = por debajo (soportado)."""
    if len(closes) < n: return 0.0
    c = closes[-n:]; h = highs[-n:]; l = lows[-n:]; v = volumes[-n:]
    tp_vol  = sum(((h[i]+l[i]+c[i])/3)*v[i] for i in range(len(c)))
    vol_sum = sum(v)
    vwap = tp_vol/vol_sum if vol_sum > 0 else closes[-1]
    return (closes[-1]-vwap)/vwap*100

def detect_breakout(closes, highs, n=20):
    """
    Detecta rotura de resistencia: precio cierra por encima del máximo de n velas.
    """
    if len(closes) < n+2: return False, 0.0
    resistance = max(highs[-n-1:-1])  # máximo de las últimas n velas (sin la actual)
    current    = closes[-1]
    prev       = closes[-2]
    broke_out  = current > resistance and prev <= resistance
    strength   = (current/resistance - 1)*100 if broke_out else 0.0
    return broke_out, round(strength, 2)

def volume_profile_breakout(closes, highs, lows, volumes, n=50):
    """
    Rotura del Point of Control (precio con más volumen).
    Si precio sube por encima del POC con volumen = señal fuerte.
    """
    if len(closes) < n: return False, 0.0
    c = closes[-n:]; h = highs[-n:]; l = lows[-n:]; v = volumes[-n:]
    price_levels = {}
    for i in range(len(c)):
        key = round(c[i], 4)
        price_levels[key] = price_levels.get(key, 0) + v[i]
    poc = max(price_levels, key=price_levels.get)
    current = closes[-1]
    above_poc = current > poc
    strength  = (current/poc - 1)*100 if above_poc and poc > 0 else 0.0
    return above_poc and strength > 0.1, round(strength, 2)

# ============================================================================
# FETCHERS DE DATOS
# ============================================================================

def get_klines(symbol, interval='5m', limit=100):
    d = pub('/openApi/swap/v3/quote/klines',
            {'symbol': symbol, 'interval': interval, 'limit': limit})
    if d.get('code') == 0 and d.get('data'):
        kl = d['data']
        return {
            'closes':  [float(k['close'])  for k in kl],
            'highs':   [float(k['high'])   for k in kl],
            'lows':    [float(k['low'])    for k in kl],
            'volumes': [float(k['volume']) for k in kl],
            'opens':   [float(k['open'])   for k in kl],
        }
    return None

def get_oi(symbol):
    """Open Interest en USDT."""
    sym_rest = symbol  # BingX ya usa el formato correcto
    d = pub('/openApi/swap/v2/quote/openInterest', {'symbol': sym_rest})
    if d.get('code') == 0 and d.get('data'):
        return float(d['data'].get('openInterest', 0) or 0)
    return 0.0

def get_funding(symbol):
    d = pub('/openApi/swap/v2/quote/premiumIndex', {'symbol': symbol})
    if d.get('code') == 0 and d.get('data'):
        return float(d['data'].get('lastFundingRate', 0) or 0) * 100
    return 0.0

def get_ticker(symbol):
    d = pub('/openApi/swap/v2/quote/ticker', {'symbol': symbol})
    if d.get('code') == 0 and d.get('data'):
        t = d['data']
        return {
            'price':  float(t.get('lastPrice', 0)),
            'change': float(t.get('priceChangePercent', 0)),
            'volume': float(t.get('volume', 0)),
            'high24': float(t.get('highPrice', 0)),
            'low24':  float(t.get('lowPrice', 0)),
        }
    return None

# ============================================================================
# MOTOR DE DETECCIÓN DE EXPLOSIONES
# ============================================================================

class ExplosionDetector:
    """
    Analiza una moneda y calcula su probabilidad de explosión (0-100).
    
    Cada señal suma puntos. El total determina el nivel de alerta:
    ≥80 → 🔴 CRÍTICO (alta probabilidad de pump inminente)
    ≥65 → 🟠 ALTO
    ≥50 → 🟡 MEDIO
    <50 → ignorado
    """

    def __init__(self):
        self.oi_cache = {}  # {symbol: (oi, timestamp)}

    def analyze(self, symbol: str) -> dict | None:
        """Retorna dict con análisis o None si no hay señal."""
        ticker = get_ticker(symbol)
        if not ticker or ticker['price'] <= 0: return None

        price     = ticker['price']
        change_24 = ticker['change']

        # Filtros básicos rápidos
        if change_24 > 25.0: return None   # ya explotó
        if change_24 < -15.0: return None  # en caída libre

        # Datos 5m (señales de entrada)
        k5 = get_klines(symbol, '5m', 120)
        if not k5 or len(k5['closes']) < 50: return None

        closes5 = k5['closes']; highs5 = k5['highs']; lows5 = k5['lows']
        vols5   = k5['volumes']; opens5 = k5['opens']

        # Datos 1h (contexto)
        k1h = get_klines(symbol, '1h', 50)

        # Datos 15m (confirmación intermedia)
        k15 = get_klines(symbol, '15m', 60)

        confidence = 0
        signals    = []
        details    = {}

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 1: COMPRESIÓN DE BOLLINGER BANDS (squeeze)
        # El precio se comprime antes de un movimiento explosivo
        # ─────────────────────────────────────────────────────────────────
        bb_upper, bb_mid, bb_lower, bb_width = bollinger_bands(closes5, 20, 2.0)
        details['bb_width'] = round(bb_width, 2)

        if bb_width < BB_SQUEEZE_PCT:
            pts = int(25 * (BB_SQUEEZE_PCT - bb_width) / BB_SQUEEZE_PCT + 10)
            pts = min(pts, 25)
            confidence += pts
            signals.append(f"🎯 BB Squeeze {bb_width:.1f}% (+{pts})")
        elif bb_width < BB_SQUEEZE_PCT * 1.5:
            confidence += 8
            signals.append(f"📊 BB Comprimiendo {bb_width:.1f}% (+8)")

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 2: Z-SCORE VOLUMEN (ballena silenciosa)
        # Volumen 3-5x sin movimiento de precio = manos fuertes acumulando
        # ─────────────────────────────────────────────────────────────────
        z_vol = z_score_volume(vols5, 30)
        details['z_vol'] = round(z_vol, 2)

        if z_vol >= Z_VOL_SPIKE * 1.5:
            confidence += 20
            signals.append(f"🐳 Volumen Z={z_vol:.1f}x EXTREMO (+20)")
        elif z_vol >= Z_VOL_SPIKE:
            confidence += 14
            signals.append(f"⚡ Volumen Z={z_vol:.1f}x spike (+14)")
        elif z_vol >= Z_VOL_SPIKE * 0.7:
            confidence += 6
            signals.append(f"📈 Volumen elevado Z={z_vol:.1f} (+6)")

        # Vol ratio últimas 3 velas vs promedio
        vol_avg = sum(vols5[-10:-3])/7 if len(vols5) >= 10 else vols5[-1]
        vol_reciente = sum(vols5[-3:])/3
        vol_ratio = vol_reciente/vol_avg if vol_avg > 0 else 1.0
        details['vol_ratio'] = round(vol_ratio, 2)

        if vol_ratio >= 3.0:
            confidence += 12
            signals.append(f"🔥 Aceleración vol {vol_ratio:.1f}x reciente (+12)")
        elif vol_ratio >= 2.0:
            confidence += 7
            signals.append(f"📊 Incremento vol {vol_ratio:.1f}x (+7)")

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 3: OPEN INTEREST DIVERGENCIA
        # OI sube pero precio no → acumulación institucional
        # ─────────────────────────────────────────────────────────────────
        oi_current = get_oi(symbol)
        oi_prev    = self.oi_cache.get(symbol, {}).get('oi', oi_current)
        oi_change  = (oi_current - oi_prev) / oi_prev * 100 if oi_prev > 0 else 0
        self.oi_cache[symbol] = {'oi': oi_current, 'ts': time.time()}
        details['oi_change'] = round(oi_change, 2)

        if oi_change >= OI_CHANGE_MIN * 2:
            # OI sube rápido + precio plano = bomba inminente
            if abs(change_24) < 3.0:
                confidence += 22
                signals.append(f"🐳 OI +{oi_change:.1f}% precio plano (+22)")
            else:
                confidence += 14
                signals.append(f"📈 OI +{oi_change:.1f}% creciendo (+14)")
        elif oi_change >= OI_CHANGE_MIN:
            confidence += 10
            signals.append(f"📊 OI +{oi_change:.1f}% (+10)")

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 4: FUNDING RATE
        # Funding muy negativo = shorts pagando longs → presión compradora
        # ─────────────────────────────────────────────────────────────────
        funding = get_funding(symbol)
        details['funding'] = round(funding, 4)

        if funding <= FUNDING_BULL_MIN * 3:  # muy negativo
            confidence += 18
            signals.append(f"💰 Funding {funding:.3f}% muy negativo (+18)")
        elif funding <= FUNDING_BULL_MIN:
            confidence += 10
            signals.append(f"💰 Funding {funding:.3f}% negativo (+10)")
        elif funding <= 0:
            confidence += 4
            signals.append(f"💰 Funding {funding:.3f}% neutro-bull (+4)")
        elif funding >= 0.05:  # funding muy positivo = cuidado
            confidence -= 8

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 5: CVD DIVERGENCIA
        # Precio lateral pero compradores dominan = acumulación
        # ─────────────────────────────────────────────────────────────────
        cvd = cvd_ratio(closes5, opens5, vols5, n=20)
        details['cvd'] = round(cvd, 3)

        if cvd >= CVD_IMBAL:
            pts = int((cvd - 0.5) * 60)  # 0.65 → 9pts, 0.80 → 18pts
            pts = min(pts, 20)
            confidence += pts
            signals.append(f"🌊 CVD {int(cvd*100)}% compradores (+{pts})")
        elif cvd <= 1 - CVD_IMBAL:
            confidence -= 10

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 6: BREAKOUT DE RESISTENCIA
        # Cierra por encima del máximo de las últimas 20 velas
        # ─────────────────────────────────────────────────────────────────
        broke, break_strength = detect_breakout(closes5, highs5, n=20)
        details['breakout'] = broke
        if broke:
            if break_strength > 0.5:
                confidence += 20
                signals.append(f"🚀 BREAKOUT {break_strength:.2f}% vol={vol_ratio:.1f}x (+20)")
            else:
                confidence += 14
                signals.append(f"📈 Breakout {break_strength:.2f}% (+14)")

        # Breakout en 1h también
        if k1h and len(k1h['closes']) >= 20:
            broke_1h, bstr_1h = detect_breakout(k1h['closes'], k1h['highs'], n=20)
            if broke_1h:
                confidence += 15
                signals.append(f"🚀 BREAKOUT 1h {bstr_1h:.2f}% (+15)")

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 7: DIVERGENCIA ALCISTA OCULTA (Hidden Bullish Divergence)
        # Precio hace mínimos más altos pero RSI aún bajo → suelo + reversión
        # ─────────────────────────────────────────────────────────────────
        div_bull, div_str = hidden_bull_divergence(closes5, lows5, lookback=30)
        details['hidden_div'] = div_bull
        if div_bull:
            confidence += 16
            signals.append(f"🎯 Div. Alcista Oculta {div_str:.2f}% (+16)")

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 8: RSI OVERSOLD CON RECUPERACIÓN
        # RSI rebotando desde zona <35 con volumen
        # ─────────────────────────────────────────────────────────────────
        rsi_current = rsi_v(closes5, 14)
        rsi_prev    = rsi_v(closes5[:-1], 14)
        details['rsi_5m'] = round(rsi_current, 1)

        if rsi_prev < RSI_HIDDEN_MAX and rsi_current > rsi_prev:
            pts = int((RSI_HIDDEN_MAX - rsi_prev) / RSI_HIDDEN_MAX * 18)
            confidence += pts
            signals.append(f"📈 RSI rebota desde {rsi_prev:.0f}→{rsi_current:.0f} (+{pts})")
        elif rsi_current > 70:
            confidence -= 5  # sobrecomprado → no entrar

        # RSI en 1h
        if k1h and len(k1h['closes']) >= 15:
            rsi_1h = rsi_v(k1h['closes'], 14)
            details['rsi_1h'] = round(rsi_1h, 1)
            if 40 <= rsi_1h <= 60:
                confidence += 6
                signals.append(f"📊 RSI 1h={rsi_1h:.0f} zona ideal (+6)")
            elif rsi_1h > 70:
                confidence -= 8

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 9: ALINEACIÓN MULTI-TIMEFRAME
        # EMA9 > EMA21 en 5m, 15m y 1h = tendencia confirmada
        # ─────────────────────────────────────────────────────────────────
        tf_aligned = 0
        e9_5m  = ema_v(closes5, 9); e21_5m = ema_v(closes5, 21)
        if e9_5m > e21_5m: tf_aligned += 1

        if k15 and len(k15['closes']) >= 25:
            e9_15  = ema_v(k15['closes'], 9); e21_15 = ema_v(k15['closes'], 21)
            if e9_15 > e21_15: tf_aligned += 1

        if k1h and len(k1h['closes']) >= 25:
            e9_1h  = ema_v(k1h['closes'], 9); e21_1h = ema_v(k1h['closes'], 21)
            if e9_1h > e21_1h: tf_aligned += 1

        details['tf_aligned'] = tf_aligned
        if tf_aligned == 3:
            confidence += 18
            signals.append(f"✅ MTF 3/3 alineados (+18)")
        elif tf_aligned == 2:
            confidence += 10
            signals.append(f"✅ MTF 2/3 alineados (+10)")

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 10: DISTANCIA AL VWAP
        # Precio tocando VWAP desde abajo = soporte institucional
        # ─────────────────────────────────────────────────────────────────
        vwap_dist = vwap_distance(closes5, highs5, lows5, vols5, 50)
        details['vwap_dist'] = round(vwap_dist, 2)

        if -0.5 <= vwap_dist <= 0.5:
            confidence += 10
            signals.append(f"🎯 Precio en VWAP ({vwap_dist:+.2f}%) (+10)")
        elif 0.5 < vwap_dist <= 2.0:
            confidence += 6
            signals.append(f"📈 Precio sobre VWAP (+6)")
        elif vwap_dist < -1.0:
            confidence -= 5

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 11: MOMENTUM ACELERADO (últimas 3 velas todas alcistas con vol)
        # ─────────────────────────────────────────────────────────────────
        last3_bull = all(closes5[i] > opens5[i] for i in [-3, -2, -1]) if opens5 else False
        last3_vol  = all(vols5[i] > vols5[i-1] for i in [-2, -1]) if len(vols5) >= 4 else False
        if last3_bull and last3_vol:
            confidence += 14
            signals.append(f"🚀 Momentum 3 velas bull + vol creciente (+14)")
        elif last3_bull:
            confidence += 6
            signals.append(f"📈 3 velas alcistas seguidas (+6)")

        # ─────────────────────────────────────────────────────────────────
        # SEÑAL 12: PRECIO CERCA DEL HIGH 24H (momentum)
        # Si el precio está a menos del 2% del high 24h = fuerza relativa
        # ─────────────────────────────────────────────────────────────────
        if ticker['high24'] > 0:
            dist_high = (ticker['high24'] - price) / ticker['high24'] * 100
            details['dist_high24'] = round(dist_high, 2)
            if dist_high < 1.0:
                confidence += 12
                signals.append(f"💪 Cerca del High 24h ({dist_high:.1f}% abajo) (+12)")
            elif dist_high < 3.0:
                confidence += 5

        # ─────────────────────────────────────────────────────────────────
        # BONUS: Combinaciones explosivas
        # ─────────────────────────────────────────────────────────────────
        # Squeeze + Breakout = combo más poderoso
        if details.get('bb_width', 99) < BB_SQUEEZE_PCT and broke:
            confidence += 15
            signals.append("💥 COMBO SQUEEZE+BREAKOUT (+15)")

        # Ballena + OI = acumulación institucional confirmada
        if z_vol >= Z_VOL_SPIKE and oi_change >= OI_CHANGE_MIN and cvd >= CVD_IMBAL:
            confidence += 12
            signals.append("🐳 COMBO BALLENA+OI+CVD (+12)")

        # MTF + Breakout + Volumen
        if tf_aligned >= 2 and broke and vol_ratio >= 2.0:
            confidence += 10
            signals.append("🚀 COMBO MTF+BREAKOUT+VOL (+10)")

        # Cap al 100
        confidence = min(confidence, 100)

        if confidence < MIN_CONFIDENCE: return None

        return {
            'symbol':     symbol,
            'confidence': confidence,
            'price':      price,
            'change_24':  change_24,
            'signals':    signals,
            'details':    details,
            'rsi':        details.get('rsi_5m', 50),
            'tf_aligned': tf_aligned,
            'z_vol':      z_vol,
            'bb_width':   details.get('bb_width', 99),
            'oi_change':  oi_change,
            'funding':    funding,
            'cvd':        cvd,
            'breakout':   broke,
        }

# ============================================================================
# SCANNER PRINCIPAL
# ============================================================================

class ExplosionScanner:

    def __init__(self):
        self.detector      = ExplosionDetector()
        self.symbols       = []
        self.alerted       = {}   # {symbol: (confidence, timestamp)}
        self.daily_alerts  = []   # historial del día
        self.daily_date    = datetime.utcnow().date()

        log.info("=" * 65)
        log.info("  🔥 EXPLOSION SCANNER v1.0 — PUMP DETECTOR")
        log.info(f"  Confianza mínima: {MIN_CONFIDENCE}% | Auto-trade: {AUTO_TRADE}")
        log.info(f"  Umbral BB squeeze: <{BB_SQUEEZE_PCT}% | Z-Vol: >{Z_VOL_SPIKE}")
        log.info("=" * 65)
        tg(
            f"<b>🔥 EXPLOSION SCANNER v1.0 activo</b>\n"
            f"Buscando pumps inminentes en BingX\n"
            f"Confianza mínima: {MIN_CONFIDENCE}%\n"
            f"Señales: Squeeze · OI · Ballena · CVD · Breakout · MTF\n"
            f"📡 Actualizando cada {SCAN_INTERVAL}s"
        )
        self._refresh_symbols()

    def _refresh_symbols(self):
        """Top símbolos por volumen — excluye stablecoins y forex."""
        exclude = {'USDC','BUSD','TUSD','FRAX','DAI','USDP','FDUSD',
                   'EUR','GBP','JPY','CHF','AUD','CAD'}
        d = pub('/openApi/swap/v2/quote/ticker')
        if d.get('code') != 0: return
        items = []
        for t in d.get('data', []):
            sym = t.get('symbol', '')
            if not sym.endswith('-USDT'): continue
            base = sym.replace('-USDT', '').upper()
            if any(base == ex for ex in exclude): continue
            try:
                price = float(t.get('lastPrice', 0))
                vol   = float(t.get('volume', 0)) * price
                if vol >= MIN_VOL_24H and price > 0:
                    items.append({'sym': sym, 'vol': vol,
                                  'change': float(t.get('priceChangePercent', 0))})
            except: continue
        items.sort(key=lambda x: x['vol'], reverse=True)
        self.symbols = [x['sym'] for x in items[:MAX_SYMBOLS_SCAN]]
        log.info(f"  Escaneando {len(self.symbols)} símbolos")

    def _scan_one(self, symbol):
        try:
            return self.detector.analyze(symbol)
        except Exception as e:
            log.debug(f"  {symbol}: {e}")
            return None

    def _should_alert(self, symbol, confidence):
        """Evita repetir la misma alerta en 30 minutos."""
        prev = self.alerted.get(symbol)
        if not prev: return True
        prev_conf, prev_ts = prev
        elapsed = time.time() - prev_ts
        # Re-alertar si: han pasado 30min O la confianza subió significativamente
        if elapsed > 1800: return True
        if confidence >= prev_conf + 15: return True
        return False

    def _format_alert(self, result):
        c   = result['confidence']
        sym = result['symbol']
        lvl = "🔴 CRÍTICO" if c >= 80 else "🟠 ALTO" if c >= 65 else "🟡 MEDIO"

        signals_text = "\n".join(f"  {s}" for s in result['signals'][:6])

        details = result['details']
        detail_line = (
            f"BB:{details.get('bb_width',99):.1f}% | "
            f"Z-Vol:{details.get('z_vol',0):.1f} | "
            f"CVD:{int(details.get('cvd',0.5)*100)}% | "
            f"OI:{details.get('oi_change',0):+.1f}% | "
            f"Fund:{details.get('funding',0):.3f}%"
        )

        msg = (
            f"{lvl} <b>{sym}</b> — Confianza: <b>{c}%</b>\n"
            f"─────────────────────────────\n"
            f"💲 Precio: ${result['price']:.6f} | 24h: {result['change_24']:+.2f}%\n"
            f"RSI: {result['rsi']:.0f} | MTF: {result['tf_aligned']}/3\n"
            f"{detail_line}\n"
            f"─────────────────────────────\n"
            f"Señales detectadas:\n{signals_text}\n"
            f"─────────────────────────────\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
        )
        if result.get('breakout'):
            msg += "\n🚀 <b>BREAKOUT ACTIVO — ENTRADA POSIBLE AHORA</b>"
        return msg

    def _daily_summary(self):
        """Resumen diario de rendimiento de las alertas."""
        today = datetime.utcnow().date()
        if today == self.daily_date: return
        if not self.daily_alerts: return

        n = len(self.daily_alerts)
        altas = [a for a in self.daily_alerts if a['confidence'] >= 80]
        medias= [a for a in self.daily_alerts if 65 <= a['confidence'] < 80]
        bajas = [a for a in self.daily_alerts if a['confidence'] < 65]

        tg(
            f"<b>📊 Resumen de alertas — {self.daily_date}</b>\n"
            f"Total: {n} alertas\n"
            f"🔴 Críticas (≥80%): {len(altas)}\n"
            f"🟠 Altas (65-79%): {len(medias)}\n"
            f"🟡 Medias (<65%): {len(bajas)}\n\n"
            f"Top señales del día:\n"
            + "\n".join(f"  {a['symbol']} — {a['confidence']}%" 
                        for a in sorted(self.daily_alerts, 
                                       key=lambda x: x['confidence'], reverse=True)[:5])
        )
        self.daily_alerts = []
        self.daily_date   = today

    def run(self):
        log.info(f"\n🚀 Scanner activo | {len(self.symbols)} símbolos\n")
        iteration = 0
        last_sym_refresh = 0

        while True:
            try:
                iteration += 1
                self._daily_summary()

                # Refresh símbolos cada 10 min
                if time.time() - last_sym_refresh > 600:
                    self._refresh_symbols()
                    last_sym_refresh = time.time()

                log.info(f"\n{'='*55}")
                log.info(f"  Scan #{iteration} | {datetime.now().strftime('%H:%M:%S')} | {len(self.symbols)} símbolos")
                log.info(f"{'='*55}")

                results = []
                with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                    futures = {ex.submit(self._scan_one, sym): sym
                               for sym in self.symbols}
                    for fut in as_completed(futures):
                        r = fut.result()
                        if r: results.append(r)

                # Ordenar por confianza
                results.sort(key=lambda x: x['confidence'], reverse=True)

                log.info(f"  ✅ {len(results)} señales encontradas")

                for r in results:
                    sym  = r['symbol']
                    conf = r['confidence']
                    lvl  = "🔴" if conf >= 80 else "🟠" if conf >= 65 else "🟡"

                    log.info(
                        f"  {lvl} {sym:<20} conf:{conf:>3}% | "
                        f"BB:{r['bb_width']:.1f}% Z:{r['z_vol']:.1f} "
                        f"CVD:{int(r['cvd']*100)}% OI:{r['oi_change']:+.1f}% "
                        f"MTF:{r['tf_aligned']}/3"
                    )

                    if self._should_alert(sym, conf):
                        alert_msg = self._format_alert(r)
                        tg(alert_msg)
                        self.alerted[sym]  = (conf, time.time())
                        self.daily_alerts.append(r)

                if not results:
                    log.info("  💤 Sin señales de explosión en este ciclo")

                # Resumen top 3 en log
                if results:
                    log.info(f"\n  🏆 TOP 3 del scan:")
                    for r in results[:3]:
                        log.info(f"     {r['symbol']}: {r['confidence']}% — "
                                 f"{' | '.join(r['signals'][:2])}")

                time.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                log.info("⏹️ Scanner detenido")
                break
            except Exception as e:
                log.error(f"Error scan #{iteration}: {e}", exc_info=True)
                time.sleep(30)

# ============================================================================
# INTEGRACIÓN CON EL BOT PRINCIPAL (opcional)
# ============================================================================

def get_top_explosion_candidates(n=5, min_confidence=70):
    """
    Función que puede llamar el bot principal para pre-filtrar candidatos.
    Retorna lista de símbolos ordenados por probabilidad de pump.
    
    Uso en bot_longs_v6.py:
        from explosion_scanner import get_top_explosion_candidates
        hot_symbols = get_top_explosion_candidates(n=10)
        # Escanear hot_symbols primero en el ciclo
    """
    scanner  = ExplosionDetector()
    exclude  = {'USDC','BUSD','TUSD','FRAX','DAI','EUR','GBP','JPY'}
    d = pub('/openApi/swap/v2/quote/ticker')
    if d.get('code') != 0: return []

    items = []
    for t in d.get('data', []):
        sym = t.get('symbol', '')
        if not sym.endswith('-USDT'): continue
        base = sym.replace('-USDT', '').upper()
        if any(base == ex for ex in exclude): continue
        try:
            vol = float(t.get('volume', 0)) * float(t.get('lastPrice', 0))
            if vol >= MIN_VOL_24H:
                items.append(sym)
        except: continue

    items = items[:100]  # top 100

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(scanner.analyze, sym): sym for sym in items}
        for fut in as_completed(futures):
            r = fut.result()
            if r and r['confidence'] >= min_confidence:
                results.append(r)

    results.sort(key=lambda x: x['confidence'], reverse=True)
    return [r['symbol'] for r in results[:n]]

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    scanner = ExplosionScanner()
    scanner.run()
