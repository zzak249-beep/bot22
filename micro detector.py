#!/usr/bin/env python3
"""
MICRO-CAP PUMP DETECTOR v1.0
════════════════════════════════════════════════════════════════════════════════

POR QUÉ EL SCANNER ANTERIOR NO DETECTÓ MRLV-USDT:
  ❌ MIN_VOL=1,000,000 → MRLV tenía <500K volumen normal → filtrado
  ❌ Señales calibradas para altcoins grandes (BTC correlación, breadth...)
  ❌ No detecta el patrón "moneda dormida → explosión"

LOS PUMPS TIPO MRLV TIENEN ESTAS HUELLAS ÚNICAS:
  ✅ Volumen 24h normalmente MUY bajo (<500K USDT)
  ✅ Días/semanas sin movimiento → precio comprimido lateral
  ✅ De repente: volumen x10-x50 en UNA sola vela
  ✅ El primer movimiento es el más rápido (+5-30% en minutos)
  ✅ OI crece MASIVAMENTE de golpe (de 0 a mucho)
  ✅ Precio rompe todos los máximos históricos recientes
  ✅ Funding rate salta de neutro a positivo muy rápido
  ✅ Spread bid-ask se amplia (liquidez saliendo = manos débiles fuera)

ESTRATEGIA:
  - Escanear TODO BingX incluyendo monedas de bajo volumen
  - Detectar el despertar ANTES de que el precio explote
  - Alertar en los primeros segundos del movimiento
  - No entrar en el pico — entrar en el primer pullback
"""

import os, sys, time, math, re, json, logging, requests, threading, random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict, deque

# ============================================================================
# CONFIG
# ============================================================================

TG_TOKEN  = os.getenv('TELEGRAM_BOT_TOKEN', '')
TG_CHAT   = os.getenv('TELEGRAM_CHAT_ID', '')
BASE_URL  = "https://open-api.bingx.com"

# Umbrales micro-cap
MIN_VOL_MICRO    = float(os.getenv('MIN_VOL_MICRO',    '50000'))   # $50K mínimo (antes 1M)
MAX_VOL_NORMAL   = float(os.getenv('MAX_VOL_NORMAL',   '2000000')) # si tenía >2M vol → no es micro
SCAN_INTERVAL    = int(os.getenv('SCAN_INTERVAL',      '60'))      # cada 60 seg (más rápido)
MIN_CONFIDENCE   = int(os.getenv('MIN_CONFIDENCE',     '50'))
WORKERS          = int(os.getenv('WORKERS',            '12'))
MAX_SYMS         = int(os.getenv('MAX_SYMBOLS',        '500'))     # escanear TODOS

# Umbrales de detección de despertar
VOL_SPIKE_X      = float(os.getenv('VOL_SPIKE_X',     '5.0'))     # vol x5 vs promedio
VOL_EXTREME_X    = float(os.getenv('VOL_EXTREME_X',   '15.0'))    # vol x15 = alerta máxima
PRICE_MOVE_MIN   = float(os.getenv('PRICE_MOVE_MIN',  '2.0'))     # +2% mínimo en curso
CANDLE_BULL_N    = int(os.getenv('CANDLE_BULL_N',     '3'))       # N velas alcistas seguidas
DORMANT_DAYS     = int(os.getenv('DORMANT_DAYS',      '3'))       # días "dormida" previos
OI_SPIKE_PCT     = float(os.getenv('OI_SPIKE_PCT',    '20.0'))    # OI sube 20%+

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('MICRO')

# ============================================================================
# API
# ============================================================================

def pub(path, params=None):
    try:
        r = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10)
        return r.json()
    except: return {}

def tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        print(f"[TG] {msg[:100]}"); return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
            timeout=6
        )
    except: pass

# ============================================================================
# INDICADORES
# ============================================================================

def ema(prices, n):
    if len(prices) < 2: return prices[-1] if prices else 0
    k, e = 2/(n+1), prices[0]
    for p in prices[1:]: e = p*k + e*(1-k)
    return e

def rsi(prices, n=14):
    if len(prices) < n+1: return 50.0
    g = [max(prices[i]-prices[i-1], 0) for i in range(1, len(prices))]
    l = [max(prices[i-1]-prices[i], 0) for i in range(1, len(prices))]
    ag = sum(g[-n:])/n; al = sum(l[-n:])/n
    return 100.0 if al == 0 else 100 - 100/(1+ag/al)

def atr(highs, lows, closes, n=14):
    if len(closes) < 2: return 0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, min(len(closes), n+1))]
    return sum(trs)/len(trs) if trs else 0

# ============================================================================
# SEÑALES ESPECÍFICAS DE MICRO-CAP PUMPS
# ============================================================================

def signal_vol_despertar(volumes, closes):
    """
    LA señal más importante: moneda dormida que de repente tiene volumen.
    Compara el volumen actual vs el promedio de los últimos días.
    """
    if len(volumes) < 30: return 0, "insuf"

    # Volumen promedio "normal" (excluyendo las últimas 3 velas)
    vol_normal = sum(volumes[-30:-3]) / 27

    # Volumen reciente (últimas 3 velas)
    vol_reciente = sum(volumes[-3:]) / 3

    if vol_normal <= 0: return 0, "sin vol previo"

    ratio = vol_reciente / vol_normal

    if ratio >= VOL_EXTREME_X:
        pts = 40
        return pts, f"🚨 VOL x{ratio:.0f} EXTREMO"
    elif ratio >= VOL_SPIKE_X * 2:
        pts = 30
        return pts, f"🔥 VOL x{ratio:.0f} muy alto"
    elif ratio >= VOL_SPIKE_X:
        pts = 20
        return pts, f"⚡ VOL x{ratio:.1f} spike"
    elif ratio >= 2.0:
        pts = 8
        return pts, f"📈 VOL x{ratio:.1f} elevado"
    return 0, "normal"


def signal_dormant_breakout(closes, highs, volumes, days_1h=72):
    """
    Moneda dormida N días que rompe su máximo histórico reciente.
    Esto es lo que caracteriza a MRLV y similares.
    """
    if len(closes) < days_1h + 5: return 0, "insuf"

    # Máximo de los últimos N períodos (excluyendo actuales)
    hist_highs = highs[-days_1h:-3]
    if not hist_highs: return 0, "insuf"

    resistance = max(hist_highs)
    current    = closes[-1]
    prev       = closes[-4]  # hace 3 velas

    # ¿Rompió el máximo histórico?
    broke = current > resistance

    # ¿Cuánto tiempo estuvo "dormida"? (precio plano)
    price_range = (max(hist_highs) - min(hist_highs[-days_1h:])) / max(hist_highs)
    dormant = price_range < 0.15  # menos del 15% de rango en N días

    if broke and dormant:
        strength = (current/resistance - 1) * 100
        if strength > 5:  return 35, f"🚀 Rompe {strength:.1f}% sobre máx histórico + dormida"
        else:             return 25, f"📈 Rompe máximo tras período dormido"
    elif broke:
        return 15, f"📊 Breakout histórico"
    elif dormant and current > max(hist_highs) * 0.97:
        return 8, "⏳ Dormida + cerca del máximo"
    return 0, "sin breakout"


def signal_vela_impulso(closes, opens, volumes, n=5):
    """
    Busca la vela de impulso inicial: cuerpo grande alcista + volumen extremo.
    En micro-caps, la primera vela de impulso es la señal de entrada.
    """
    if len(closes) < n + 3: return 0, "insuf"

    signals = []
    for i in range(-n, 0):
        c = closes[i]; o = opens[i]; v = volumes[i]
        cuerpo = (c - o) / o * 100 if o > 0 else 0

        # Vela alcista con cuerpo > 2%
        if cuerpo >= 2.0:
            vol_avg = sum(volumes[i-5:i]) / 5 if i >= 5 else sum(volumes[:abs(i)]) / max(1, abs(i))
            vol_ratio = v / vol_avg if vol_avg > 0 else 1

            if cuerpo >= 5.0 and vol_ratio >= 5.0:
                signals.append(f"💥 Vela +{cuerpo:.1f}% vol x{vol_ratio:.0f}")
            elif cuerpo >= 3.0 and vol_ratio >= 3.0:
                signals.append(f"🔥 Vela +{cuerpo:.1f}% vol x{vol_ratio:.1f}")

    if signals:
        pts = min(25 * len(signals), 35)
        return pts, " | ".join(signals[:2])
    return 0, "sin impulso"


def signal_consecutive_bull(closes, opens, volumes):
    """
    Velas alcistas consecutivas con volumen creciente = momentum real.
    """
    if len(closes) < 5: return 0, "insuf"

    count = 0
    vol_creciente = True
    for i in range(-CANDLE_BULL_N, 0):
        if closes[i] > opens[i]:
            count += 1
        else:
            break
        if i < -1 and volumes[i] < volumes[i-1]:
            vol_creciente = False

    if count >= CANDLE_BULL_N and vol_creciente:
        pts = count * 6
        return pts, f"🎯 {count} velas bull + vol creciente"
    elif count >= CANDLE_BULL_N:
        pts = count * 3
        return pts, f"📈 {count} velas bull seguidas"
    return 0, "sin momentum"


def signal_oi_explosion(symbol, oi_cache):
    """
    En micro-caps, el OI subiendo de repente significa que manos fuertes
    están abriendo posiciones — la moneda va a moverse.
    """
    d = pub('/openApi/swap/v2/quote/openInterest', {'symbol': symbol})
    oi_curr = float((d.get('data') or {}).get('openInterest', 0) or 0)

    prev = oi_cache.get(symbol, {}).get('oi', 0)
    oi_cache[symbol] = {'oi': oi_curr, 'ts': time.time()}

    if prev <= 0 or oi_curr <= 0: return 0, "oi sin datos", oi_curr
    chg = (oi_curr - prev) / prev * 100

    if chg >= OI_SPIKE_PCT * 3:  return 25, f"🐳 OI +{chg:.0f}% EXPLOSIÓN", oi_curr
    elif chg >= OI_SPIKE_PCT:     return 15, f"📈 OI +{chg:.0f}%", oi_curr
    elif chg >= OI_SPIKE_PCT/2:   return 6,  f"📊 OI +{chg:.0f}%", oi_curr
    return 0, f"OI {chg:+.1f}%", oi_curr


def signal_price_momentum(closes, change_24h):
    """
    Ya en movimiento: ¿cuánto ha subido ya? ¿Hay continuación?
    """
    if len(closes) < 10: return 0, "insuf"

    # Momentum últimas horas
    chg_1h = (closes[-1] - closes[-12]) / closes[-12] * 100 if closes[-12] > 0 else 0
    chg_4h = (closes[-1] - closes[-48]) / closes[-48] * 100 if len(closes) >= 48 and closes[-48] > 0 else 0

    # Zona de entrada: ya subió algo pero no demasiado
    if 3 <= chg_1h <= 15:
        pts = 15
        return pts, f"🚀 +{chg_1h:.1f}% en 1h — momentum activo"
    elif 15 < chg_1h <= 30:
        pts = 8
        return pts, f"⚠️ +{chg_1h:.1f}% en 1h — algo tarde"
    elif chg_1h > 30:
        pts = -10
        return pts, f"🔴 +{chg_1h:.1f}% — demasiado tarde"
    elif 1 <= chg_1h < 3:
        pts = 8
        return pts, f"📈 +{chg_1h:.1f}% en 1h — inicio"
    return 0, f"flat {chg_1h:+.1f}%"


def signal_funding_spike(symbol):
    """
    Funding rate pasando de 0 a positivo rápido = compradores pagando.
    En micro-caps esto es muy significativo.
    """
    d = pub('/openApi/swap/v2/quote/premiumIndex', {'symbol': symbol})
    fund = float((d.get('data') or {}).get('lastFundingRate', 0) or 0) * 100

    if fund >= 0.08:  return 15, f"💰 Funding +{fund:.3f}% muy positivo"
    elif fund >= 0.03: return 8,  f"💰 Funding +{fund:.3f}% positivo"
    elif fund <= -0.05: return 10, f"💰 Funding {fund:.3f}% negativo = longs a ganar"
    return 0, f"fund {fund:.3f}%"


def signal_pattern_accumulation(closes, volumes, n=20):
    """
    Acumulación: precio lateral con volumen ligeramente creciente.
    La mecha antes de la explosión.
    """
    if len(closes) < n + 5: return 0, "insuf"

    # Rango de precio
    p_max = max(closes[-n:])
    p_min = min(closes[-n:])
    rango = (p_max - p_min) / p_min * 100 if p_min > 0 else 99

    # Tendencia del volumen
    vol_primera_mitad = sum(volumes[-n:-n//2]) / (n//2)
    vol_segunda_mitad = sum(volumes[-n//2:]) / (n//2)
    vol_trend = vol_segunda_mitad / vol_primera_mitad if vol_primera_mitad > 0 else 1

    # Precio cerca del máximo del rango (presión compradora)
    near_top = (closes[-1] - p_min) / (p_max - p_min) > 0.7 if p_max > p_min else False

    if rango < 8 and vol_trend > 1.3 and near_top:
        pts = 18
        return pts, f"📦 Acumulación {rango:.1f}% rango + vol creciente + precio arriba"
    elif rango < 12 and vol_trend > 1.2:
        pts = 10
        return pts, f"📦 Consolidación {rango:.1f}% + vol aumentando"
    return 0, "sin patrón"

# ============================================================================
# DETECTOR PRINCIPAL
# ============================================================================

class MicroCapDetector:

    def __init__(self):
        self.oi_cache  = {}
        self.alerted   = {}   # {symbol: (conf, ts, price)}
        self.pumped    = {}   # {symbol: ts} — ya explotó, no re-alertar
        self.history   = defaultdict(list)  # historial de scores
        self.vol_history = defaultdict(deque)  # historial de vol 24h

    def _klines(self, symbol, interval='5m', limit=120):
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

    def analyze(self, symbol, ticker_data):
        """
        Análisis completo de una moneda micro-cap.
        Devuelve dict con score y señales, o None.
        """
        try:
            price     = ticker_data['price']
            change_24 = ticker_data['change']
            vol_24    = ticker_data['vol_24']

            if price <= 0: return None

            # Ya explotó hace poco — no re-alertar
            if symbol in self.pumped:
                elapsed = time.time() - self.pumped[symbol]
                if elapsed < 3600: return None  # silencio 1h después del pump
                else: del self.pumped[symbol]

            # Obtener velas
            k5  = self._klines(symbol, '5m',  120)
            k1h = self._klines(symbol, '1h',  100)

            if not k5 or len(k5['closes']) < 30: return None

            c5=k5['closes']; h5=k5['highs']; l5=k5['lows']; v5=k5['volumes']; o5=k5['opens']
            c1h=k1h['closes'] if k1h else c5
            h1h=k1h['highs']  if k1h else h5
            v1h=k1h['volumes'] if k1h else v5

            # Historial de vol 24h para detectar "despertar"
            self.vol_history[symbol].append(vol_24)
            if len(self.vol_history[symbol]) > 100:
                self.vol_history[symbol].popleft()

            conf   = 0
            sigs   = []
            detail = {}

            # ─────────────────────────────────────────────────────────
            # SEÑAL 1: DESPERTAR DE VOLUMEN (la más importante)
            # ─────────────────────────────────────────────────────────
            pts1, desc1 = signal_vol_despertar(v5, c5)
            if pts1 > 0: conf += pts1; sigs.append(desc1)
            detail['vol_spike'] = pts1

            # ─────────────────────────────────────────────────────────
            # SEÑAL 2: BREAKOUT DE MONEDA DORMIDA
            # ─────────────────────────────────────────────────────────
            pts2, desc2 = signal_dormant_breakout(c1h, h1h, v1h, days_1h=72)
            if pts2 > 0: conf += pts2; sigs.append(desc2)
            detail['dormant'] = pts2

            # ─────────────────────────────────────────────────────────
            # SEÑAL 3: VELA DE IMPULSO INICIAL
            # ─────────────────────────────────────────────────────────
            pts3, desc3 = signal_vela_impulso(c5, o5, v5, n=6)
            if pts3 > 0: conf += pts3; sigs.append(desc3)
            detail['impulse'] = pts3

            # ─────────────────────────────────────────────────────────
            # SEÑAL 4: VELAS CONSECUTIVAS ALCISTAS
            # ─────────────────────────────────────────────────────────
            pts4, desc4 = signal_consecutive_bull(c5, o5, v5)
            if pts4 > 0: conf += pts4; sigs.append(desc4)

            # ─────────────────────────────────────────────────────────
            # SEÑAL 5: EXPLOSIÓN DE OI
            # ─────────────────────────────────────────────────────────
            pts5, desc5, oi_val = signal_oi_explosion(symbol, self.oi_cache)
            if pts5 > 0: conf += pts5; sigs.append(desc5)
            detail['oi'] = oi_val

            # ─────────────────────────────────────────────────────────
            # SEÑAL 6: MOMENTUM DE PRECIO ACTUAL
            # ─────────────────────────────────────────────────────────
            pts6, desc6 = signal_price_momentum(c5, change_24)
            conf += pts6  # puede ser negativo
            if abs(pts6) > 0: sigs.append(desc6)
            detail['momentum'] = pts6

            # ─────────────────────────────────────────────────────────
            # SEÑAL 7: FUNDING RATE
            # ─────────────────────────────────────────────────────────
            pts7, desc7 = signal_funding_spike(symbol)
            if pts7 > 0: conf += pts7; sigs.append(desc7)

            # ─────────────────────────────────────────────────────────
            # SEÑAL 8: PATRÓN DE ACUMULACIÓN (pre-pump)
            # ─────────────────────────────────────────────────────────
            pts8, desc8 = signal_pattern_accumulation(c5, v5, n=20)
            if pts8 > 0: conf += pts8; sigs.append(desc8)
            detail['accum'] = pts8

            # ─────────────────────────────────────────────────────────
            # BONUS: Combos específicos de micro-caps
            # ─────────────────────────────────────────────────────────

            # Despertar extremo + dormida = combo perfecto MRLV-style
            if detail.get('vol_spike', 0) >= 30 and detail.get('dormant', 0) >= 25:
                conf += 20; sigs.append("💥 COMBO CLÁSICO MICRO-PUMP")

            # Vol spike + impulso = ya en movimiento
            if detail.get('vol_spike', 0) >= 20 and detail.get('impulse', 0) >= 15:
                conf += 12; sigs.append("🚀 COMBO VOL+IMPULSO")

            # Acumulación + vol despertar = muelle soltándose
            if detail.get('accum', 0) > 0 and detail.get('vol_spike', 0) >= 20:
                conf += 10; sigs.append("⚡ COMBO ACUM+DESPERTAR")

            # Penalización si el precio ya subió demasiado
            if change_24 > 40: conf -= 20; sigs.append("🔴 DEMASIADO TARDE +40%")
            elif change_24 > 25: conf -= 10; sigs.append("⚠️ TARDE +25%")

            conf = max(0, min(conf, 100))

            if conf < MIN_CONFIDENCE: return None

            # Calcular nivel de peligro (entrada óptima vs tardía)
            entry_quality = "ENTRADA ÓPTIMA" if change_24 < 10 else \
                           "ENTRADA MEDIA"  if change_24 < 25 else "ENTRADA TARDÍA"

            return {
                'symbol':    symbol,
                'confidence': conf,
                'price':     price,
                'change_24': change_24,
                'vol_24':    vol_24,
                'signals':   sigs,
                'detail':    detail,
                'entry_quality': entry_quality,
                'rsi':       rsi(c5, 14),
                'vol_spike_x': detail.get('vol_spike', 0) / 10,
            }

        except Exception as e:
            log.debug(f"  {symbol}: {e}")
            return None

    def should_alert(self, symbol, conf, price):
        prev = self.alerted.get(symbol)
        if not prev: return True
        prev_conf, prev_ts, prev_price = prev
        elapsed = time.time() - prev_ts
        price_move = abs(price - prev_price) / prev_price * 100 if prev_price > 0 else 0
        if elapsed > 900: return True           # re-alertar después de 15min
        if conf >= prev_conf + 20: return True  # confianza subió mucho
        if price_move > 5: return True          # precio se movió 5%+
        return False

    def format_alert(self, r):
        c   = r['confidence']
        sym = r['symbol']
        lvl = "🔴 PUMP DETECTADO" if c >= 75 else "🟠 POSIBLE PUMP" if c >= 60 else "🟡 VIGILAR"
        eq  = r['entry_quality']
        eq_icon = "✅" if "ÓPTIMA" in eq else "⚠️" if "MEDIA" in eq else "🔴"

        sigs_txt = "\n".join(f"  {s}" for s in r['signals'][:6])

        vol_m = r['vol_24'] / 1e6
        vol_str = f"${vol_m:.2f}M" if vol_m >= 1 else f"${r['vol_24']/1e3:.0f}K"

        msg = (
            f"{lvl}\n"
            f"<b>{sym}</b> — Conf: <b>{c}%</b>\n"
            f"───────────────────────\n"
            f"💲 ${r['price']:.8f}\n"
            f"📊 24h: <b>{r['change_24']:+.2f}%</b> | Vol: {vol_str}\n"
            f"RSI: {r['rsi']:.0f}\n"
            f"───────────────────────\n"
            f"Señales:\n{sigs_txt}\n"
            f"───────────────────────\n"
            f"{eq_icon} <b>{eq}</b>\n"
            f"⏰ {datetime.utcnow().strftime('%H:%M:%S UTC')}\n"
        )

        if "ÓPTIMA" in eq and c >= 70:
            msg += "\n⚡ <b>ACTUAR PRONTO — momentum activo</b>"
        if "TARDÍA" in eq:
            msg += "\n⚠️ Ya subió mucho — esperar pullback"

        return msg


# ============================================================================
# SCANNER PRINCIPAL
# ============================================================================

class MicroPumpScanner:

    def __init__(self):
        self.detector = MicroCapDetector()
        self.all_symbols = []
        self.scan_count  = 0
        self.daily_found = []
        self.daily_date  = datetime.utcnow().date()

        log.info("="*60)
        log.info("  🔥 MICRO-CAP PUMP DETECTOR v1.0")
        log.info(f"  Vol mínimo: ${MIN_VOL_MICRO:,.0f} | Máximo: ${MAX_VOL_NORMAL:,.0f}")
        log.info(f"  Conf mínima: {MIN_CONFIDENCE}% | Scan: {SCAN_INTERVAL}s")
        log.info(f"  Detecta: MRLV-type pumps, low-cap explosions")
        log.info("="*60)

        tg(
            f"<b>🔥 MICRO-CAP PUMP DETECTOR v1.0</b>\n"
            f"Detecta pumps tipo MRLV, PEPE early, etc.\n"
            f"Volumen: ${MIN_VOL_MICRO:,.0f} — ${MAX_VOL_NORMAL:,.0f}\n"
            f"Señales: Vol Spike · Dormant Breakout · OI · Impulso\n"
            f"📡 Scan cada {SCAN_INTERVAL}s"
        )

    def _get_all_symbols(self):
        """
        Obtiene TODOS los símbolos de BingX incluyendo micro-caps.
        El volumen mínimo es $50K — captura monedas dormidas.
        """
        d = pub('/openApi/swap/v2/quote/ticker')
        if d.get('code') != 0: return {}

        exclude = {'USDC','BUSD','TUSD','FRAX','DAI','EUR','GBP','JPY','CHF','AUD','CAD'}
        result = {}

        for t in d.get('data', []):
            sym = t.get('symbol', '')
            if not sym.endswith('-USDT'): continue
            base = sym.replace('-USDT', '').upper()
            if any(base == ex for ex in exclude): continue

            try:
                price  = float(t.get('lastPrice', 0))
                vol    = float(t.get('volume', 0)) * price
                change = float(t.get('priceChangePercent', 0))
                if price <= 0: continue
                if vol < MIN_VOL_MICRO: continue    # muy poca liquidez = trampa
                if vol > MAX_VOL_NORMAL * 3: continue  # demasiado grande = no es micro

                result[sym] = {
                    'price':    price,
                    'change':   change,
                    'vol_24':   vol,
                }
            except: continue

        return result

    def scan_once(self):
        self.scan_count += 1
        log.info(f"\n{'='*55}")
        log.info(f"  Scan #{self.scan_count} | {datetime.now().strftime('%H:%M:%S')}")

        # Obtener todos los tickers
        tickers = self._get_all_symbols()
        if not tickers:
            log.warning("  Sin datos de tickers"); return

        # Separar micro-caps de normales
        micro  = {s:t for s,t in tickers.items() if t['vol_24'] <= MAX_VOL_NORMAL}
        normal = {s:t for s,t in tickers.items() if t['vol_24'] > MAX_VOL_NORMAL}

        log.info(f"  Micro-caps (<${MAX_VOL_NORMAL/1e6:.1f}M): {len(micro)} | "
                 f"Normales: {len(normal)}")

        # Priorizar: monedas con cambio >2% o vol_24 inusual
        priority = {s:t for s,t in micro.items()
                    if t['change'] >= 2.0 or t['change'] <= -2.0}
        rest     = {s:t for s,t in micro.items() if s not in priority}

        log.info(f"  Prioritarias (cambio >2%): {len(priority)}")

        results = []

        # Analizar prioritarias primero
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(self.detector.analyze, s, t): s
                       for s, t in list(priority.items())[:MAX_SYMS//2]}
            for fut in as_completed(futures):
                r = fut.result()
                if r: results.append(r)

        # Luego el resto (con menos workers para no saturar)
        with ThreadPoolExecutor(max_workers=WORKERS//2) as ex:
            futures = {ex.submit(self.detector.analyze, s, t): s
                       for s, t in list(rest.items())[:MAX_SYMS//2]}
            for fut in as_completed(futures):
                r = fut.result()
                if r: results.append(r)

        results.sort(key=lambda x: x['confidence'], reverse=True)
        log.info(f"  ✅ {len(results)} señales detectadas")

        # Alertar
        for r in results:
            sym  = r['symbol']
            conf = r['confidence']
            lvl  = "🔴" if conf >= 75 else "🟠" if conf >= 60 else "🟡"

            log.info(f"  {lvl} {sym:<20} {conf:>3}% | "
                     f"24h:{r['change_24']:+.1f}% | "
                     f"Vol:${r['vol_24']/1e3:.0f}K | "
                     f"{r['entry_quality']}")

            if self.detector.should_alert(sym, conf, r['price']):
                alert_msg = self.detector.format_alert(r)
                tg(alert_msg)
                self.detector.alerted[sym] = (conf, time.time(), r['price'])
                self.daily_found.append(r)

                if conf >= 75:
                    self.detector.pumped[sym] = time.time()

        if not results:
            log.info("  💤 Sin pumps detectados en este ciclo")
        else:
            log.info(f"\n  🏆 TOP 5:")
            for r in results[:5]:
                log.info(f"     {r['symbol']}: {r['confidence']}% | "
                         f"{r['change_24']:+.1f}% | {' | '.join(r['signals'][:2])}")

    def _daily_reset(self):
        today = datetime.utcnow().date()
        if today == self.daily_date: return
        if self.daily_found:
            n  = len(self.daily_found)
            hs = [a for a in self.daily_found if a['confidence'] >= 75]
            tg(
                f"<b>📊 Resumen Micro-Pumps — {self.daily_date}</b>\n"
                f"Total detectados: {n}\n"
                f"🔴 Críticos (≥75%): {len(hs)}\n\n"
                f"Top pumps del día:\n"
                + "\n".join(f"  {a['symbol']} — {a['confidence']}% — {a['change_24']:+.1f}%"
                            for a in sorted(self.daily_found,
                                           key=lambda x: x['confidence'], reverse=True)[:5])
            )
        self.daily_found = []
        self.daily_date  = today

    def run(self):
        log.info(f"\n🚀 Scanner activo\n")
        while True:
            try:
                self._daily_reset()
                self.scan_once()
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                log.info("⏹️ Detenido"); break
            except Exception as e:
                log.error(f"Error: {e}", exc_info=True)
                time.sleep(30)


# ============================================================================
# FUNCIÓN PARA INTEGRAR EN BOT PRINCIPAL
# ============================================================================

_micro_scanner = None
_micro_lock    = threading.Lock()

def start_micro_scanner_background(tg_fn=None):
    """
    Arranca el micro scanner en background thread.
    Llamar desde __init__ del bot principal.
    """
    global _micro_scanner

    def _runner():
        global _micro_scanner
        _micro_scanner = MicroCapDetector()
        while True:
            try:
                scanner = MicroPumpScanner()
                scanner.run()
            except Exception as e:
                log.error(f"[MICRO-BG] {e}")
                time.sleep(60)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return t

def get_micro_hot(min_conf=60, n=10):
    """
    Retorna las micro-caps más calientes para que el bot las priorice.
    """
    if not _micro_scanner: return []
    # TODO: compartir resultados entre threads via Queue si se integra
    return []

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    scanner = MicroPumpScanner()
    scanner.run()
