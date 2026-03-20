#!/usr/bin/env python3
"""
BOT SHORTS PROFESIONAL v2.1
════════════════════════════════════════════════
MEJORAS sobre v2.0:
  FIX 1: Filtro no-cripto AMPLIADO por palabras clave contenidas
          → captura GAS, GASOLINE, NATURALGAS, DOWJONES, SP500, etc.
  FIX 2: Órdenes LÍMITE (maker fee 0.02% vs taker 0.05%) → -60% comisiones
  FIX 3: Tendencia global — bloquea shorts si BTC/ETH suben fuerte
  FIX 4: TP mínimo recalculado para cubrir comisiones con margen
  FIX 5: Score más exigente en condiciones alcistas generales
  FIX 6: Cooldown ampliado a 15 min (evita re-entrar en reversal)
  FIX 7: Filtro de hora — evita las primeras velas del día (spread alto)
  MANTENIDO: quoteOrderQty, retry, reporte horario, trailing stop
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re
from datetime import datetime, timedelta
from urllib.parse import urlencode

# ============================================================================
# CONFIGURACION
# ============================================================================

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default))
    v = v.strip().strip('"').strip("'").strip()
    if typ in ('int', 'float'):
        v = v.replace(',', '.')
        m = re.match(r'^-?\d+\.?\d*', v)
        v = m.group(0) if m else str(default)
    if typ == 'int':   return int(float(v))
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

BINGX_API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

AUTO_TRADING  = clean('AUTO_TRADING_ENABLED',  'true',  'bool')
POSITION_SIZE = clean('MAX_POSITION_SIZE',       '7',   'float')
MIN_TRADE     = clean('MIN_TRADE_USDT',          '5',   'float')
LEVERAGE      = clean('LEVERAGE',                '3',   'int')
TP_PCT        = clean('TAKE_PROFIT_PCT',         '1.5', 'float')
SL_PCT        = clean('STOP_LOSS_PCT',           '1.0', 'float')
MAX_TRADES    = clean('MAX_OPEN_TRADES',         '2',   'int')
INTERVAL      = clean('CHECK_INTERVAL',         '120',  'int')
MIN_VOLUME    = clean('MIN_VOLUME_24H',       '50000',  'float')
MAX_SYMBOLS   = clean('MAX_SYMBOLS_TO_ANALYZE', '90',   'int')
MIN_SCORE     = clean('MIN_SCORE',              '75',   'float')
TRAILING      = clean('TRAILING_STOP_ENABLED', 'true',  'bool')

# FIX 2: órdenes límite → maker fee (0.02%) en lugar de taker (0.05%)
USE_LIMIT_ORDERS = clean('USE_LIMIT_ORDERS', 'true', 'bool')
LIMIT_OFFSET_PCT = 0.05   # precio límite = mercado - 0.05% (llena casi inmediato)

# FIX 3: bloquear shorts si BTC sube más de este % en 1h
BTC_BULL_BLOCK_PCT = clean('BTC_BULL_BLOCK_PCT', '1.5', 'float')

# FIX 7: no operar en las primeras N horas UTC (spread alto en apertura)
SKIP_HOURS_UTC = {0, 1}   # 00:00-02:00 UTC suelen ser horas de bajo volumen

BASE_URL = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ============================================================================
# COMISIÓN REAL según tipo de orden
# ============================================================================
# maker (límite): 0.02% por lado → 0.04% ida+vuelta
# taker (mercado): 0.05% por lado → 0.10% ida+vuelta
# Con $7 x 3 = $21 de posición:
#   taker: $0.021 | maker: $0.0084  → ahorro $0.013 por trade
COMISION_MAKER  = 0.0004   # 0.04% ida+vuelta
COMISION_TAKER  = 0.0010   # 0.10% ida+vuelta
COMISION_ACTUAL = COMISION_MAKER if USE_LIMIT_ORDERS else COMISION_TAKER

# TP mínimo que cubre comisiones + 0.2% de ganancia neta
# Con 3x leverage: necesitas > (comisión / leverage) en precio para no perder
TP_MIN_RENTABLE = round((COMISION_ACTUAL / LEVERAGE + 0.002) * 100, 3)

# ============================================================================
# API BINGX — con retry automático
# ============================================================================

def bingx_request(method, endpoint, params, retries=2):
    for attempt in range(retries + 1):
        try:
            p = dict(params)
            p['timestamp'] = int(time.time() * 1000)
            sp  = sorted(p.items())
            qs  = urlencode(sp)
            sig = hmac.new(BINGX_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY': BINGX_API_KEY,
                   'Content-Type': 'application/x-www-form-urlencoded'}
            if method == 'GET':
                return requests.get(url, headers=hdr, timeout=12)
            return requests.post(url, headers=hdr, timeout=12)
        except Exception as e:
            if attempt < retries:
                log.warning(f"  retry {attempt+1}: {e}")
                time.sleep(1.5)
            else:
                raise

# ============================================================================
# INDICADORES TECNICOS
# ============================================================================

def calc_ema(prices, period):
    if not prices: return 0
    if len(prices) < period: return sum(prices) / len(prices)
    k, e = 2 / (period + 1), prices[0]
    for p in prices[1:]:
        e = p * k + e * (1 - k)
    return e

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    gains  = [max(0,  prices[i] - prices[i-1]) for i in range(1, len(prices))]
    losses = [max(0, prices[i-1] - prices[i])  for i in range(1, len(prices))]
    ag = sum(gains[-period:])  / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))

def calc_macd(prices):
    if len(prices) < 26: return 0, 0, 0
    ml  = calc_ema(prices, 12) - calc_ema(prices, 26)
    sig = ml * 0.9
    return ml, sig, ml - sig

def calc_bollinger(prices, period=20):
    if len(prices) < period:
        m = sum(prices) / len(prices)
        return m, m, m
    w   = prices[-period:]
    mid = sum(w) / period
    std = (sum((p - mid)**2 for p in w) / period) ** 0.5
    return mid + 2*std, mid, mid - 2*std

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2: return 0
    trs = []
    for i in range(1, min(len(closes), period + 1)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i-1]),
                       abs(lows[i]  - closes[i-1])))
    return sum(trs) / len(trs) if trs else 0

def vol_spike(volumes):
    if len(volumes) < 5: return 1.0
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    return (volumes[-1] / avg) if avg > 0 else 1.0

# ============================================================================
# BOT
# ============================================================================

class ShortBot:

    def __init__(self):
        fee_label = f"LÍMITE maker {COMISION_MAKER*100:.2f}%" if USE_LIMIT_ORDERS \
                    else f"MERCADO taker {COMISION_TAKER*100:.2f}%"
        rr = round(TP_PCT / SL_PCT, 1)

        log.info("=" * 65)
        log.info("  BOT SHORTS PROFESIONAL v2.1")
        log.info("=" * 65)
        log.info(f"  Modo:        {'AUTO SHORTS' if AUTO_TRADING else 'SOLO SEÑALES'}")
        log.info(f"  Capital:     ${POSITION_SIZE} USDT por trade")
        log.info(f"  Leverage:    {LEVERAGE}x  →  posición ${POSITION_SIZE * LEVERAGE:.1f} USDT")
        log.info(f"  TP / SL:     {TP_PCT}% / {SL_PCT}%   (RR {rr}:1)")
        log.info(f"  TP mínimo:   {TP_MIN_RENTABLE}% para cubrir comisiones")
        log.info(f"  Órdenes:     {fee_label}")
        log.info(f"  Score min:   {MIN_SCORE}/100")
        log.info(f"  Cooldown:    15 min por par")
        log.info(f"  BTC filtro:  shorts bloqueados si BTC sube >{BTC_BULL_BLOCK_PCT}% en 1h")
        log.info("=" * 65)

        self.symbols      = []
        self.open_trades  = {}
        self._contracts   = {}
        self._cooldowns   = {}
        self._last_report = datetime.now()
        self._btc_change_1h = 0.0   # FIX 3: tendencia BTC
        self.stats = {'exec': 0, 'closed': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()
        self._tg(
            f"<b>🔴 Bot SHORTS v2.1 iniciado</b>\n"
            f"{'AUTO ON' if AUTO_TRADING else 'Solo señales'}\n"
            f"Capital: ${POSITION_SIZE} x{LEVERAGE} | TP:{TP_PCT}% SL:{SL_PCT}%\n"
            f"Fee: {fee_label}\n"
            f"Score≥{MIN_SCORE} | BTC filtro:{BTC_BULL_BLOCK_PCT}%\n"
            f"TP mín rentable: {TP_MIN_RENTABLE}%"
        )

    # ---------------------------------------------------------------- setup

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING:
            log.info("Modo señales")
            return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("Credenciales vacías")
            AUTO_TRADING = False
            return
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/balance', {})
            d = r.json()
            if d.get('code') == 0:
                bal = d.get('data', {})
                eq  = bal.get('equity', bal.get('balance', '?'))
                log.info(f"BingX OK | Balance: ${eq} USDT")
            else:
                log.error(f"BingX [{d.get('code')}]: {d.get('msg')}")
                AUTO_TRADING = False
        except Exception as e:
            log.error(f"Error API: {e}")
            AUTO_TRADING = False

    def _load_contracts(self):
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                for c in d.get('data', []):
                    self._contracts[c.get('symbol', '')] = {
                        'step':  float(c.get('tradeMinQuantity', 1)),
                        'prec':  int(c.get('quantityPrecision', 2)),
                        'ctval': float(c.get('contractSize', 1)),
                    }
                log.info(f"Contratos: {len(self._contracts)}")
        except Exception as e:
            log.warning(f"Error contratos: {e}")

    def _get_symbols(self):
        """
        FIX 1: filtro no-cripto por palabras clave CONTENIDAS en el nombre.
        Captura NATURALGAS, GASOLINEUSDT, DOWJONESUSDT, SP500USDT, etc.
        """
        NO_CRIPTO = [
            # Índices bursátiles
            'DOW', 'JONES', 'SP500', 'SPX', 'SPY', 'QQQ', 'NASDAQ', 'RUSSELL',
            'DAX', 'FTSE', 'CAC', 'NIKKEI', 'HANG', 'BOVESPA', 'IBEX',
            'US30', 'NAS100', 'US500', 'DJI', 'INDEX',
            # Energía y materias primas
            'GOLD', 'SILVER', 'XAU', 'XAG', 'PAXG', 'XAUT',
            'OIL', 'BRENT', 'WTI', 'CRUDE', 'PETROLEUM',
            'GAS', 'GASOLINE', 'NATURAL', 'PETROL', 'DIESEL',
            'PLATINUM', 'PALLADIUM', 'COPPER', 'NICKEL', 'ZINC', 'IRON',
            # Acciones
            'TSLA', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA',
            'COIN', 'MSTR', 'TESLA', 'APPLE', 'GOOGLE', 'AMAZON',
            # Forex
            'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD',
            # Agrícolas
            'WHEAT', 'CORN', 'SUGAR', 'COFFEE', 'COTTON', 'LUMBER', 'SOYBEAN',
        ]
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                items, excl = [], []
                for t in d.get('data', []):
                    sym = t.get('symbol', '')
                    if not sym.endswith('-USDT'): continue
                    base = sym.replace('-USDT', '').upper()
                    if any(kw in base for kw in NO_CRIPTO):
                        excl.append(base); continue
                    try:
                        price = float(t.get('lastPrice', 0))
                        vol   = float(t.get('volume', 0)) * price
                        if vol < MIN_VOLUME or price < 0.000001: continue
                        items.append({'symbol': sym, 'vol': vol})
                    except: continue
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                log.info(f"Pares SHORT: {len(self.symbols)} | Excluidos no-cripto: {len(excl)}")
                if excl: log.info(f"  No-cripto excluidos: {', '.join(excl[:15])}")
                return
        except Exception as e:
            log.warning(f"Error símbolos: {e}")
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT',
                        'DOGE-USDT','ADA-USDT','AVAX-USDT','LINK-USDT','DOT-USDT']

    # ---------------------------------------------------------------- datos

    def _klines(self, symbol, interval='5m', limit=60):
        try:
            r = requests.get(
                f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params={'symbol': symbol, 'interval': interval, 'limit': limit},
                timeout=10
            )
            d = r.json()
            if d.get('code') == 0 and d.get('data'):
                k = d['data']
                return (
                    [float(x['close'])  for x in k],
                    [float(x['high'])   for x in k],
                    [float(x['low'])    for x in k],
                    [float(x['volume']) for x in k],
                    [float(x['open'])   for x in k],
                )
        except: pass
        return None, None, None, None, None

    def _ticker(self, symbol):
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                             params={'symbol': symbol}, timeout=8)
            d = r.json()
            if d.get('code') == 0 and d.get('data'):
                t = d['data']
                return {'price':  float(t.get('lastPrice', 0)),
                        'change': float(t.get('priceChangePercent', 0))}
        except: pass
        return None

    def _update_btc_trend(self):
        """FIX 3: detectar si el mercado está subiendo (bloquea shorts)."""
        try:
            closes, *_ = self._klines('BTC-USDT', '1h', 3)
            if closes and len(closes) >= 2:
                self._btc_change_1h = (closes[-1] - closes[-2]) / closes[-2] * 100
                log.info(f"  BTC 1h: {self._btc_change_1h:+.2f}%")
        except Exception as e:
            log.debug(f"BTC trend: {e}")

    # ---------------------------------------------------------------- sizing

    def _qty_contratos(self, symbol, price):
        info  = self._contracts.get(symbol, {'step': 1.0, 'prec': 2, 'ctval': 1.0})
        step  = max(info['step'], 0.0001)
        prec  = info['prec']
        ctval = info.get('ctval', 1.0)
        ppc   = price * ctval if ctval != 1.0 else price
        if ppc <= 0: return None, 0
        qty = round(math.ceil(POSITION_SIZE / ppc / step) * step, prec)
        val = qty * ppc
        i = 0
        while val < MIN_TRADE and i < 500:
            qty += step; qty = round(qty, prec); val = qty * ppc; i += 1
        if val > POSITION_SIZE * 1.3:
            qty = round(math.floor((POSITION_SIZE * 1.3 / ppc) / step) * step, prec)
            val = qty * ppc
        return qty, round(val, 4)

    # ---------------------------------------------------------------- análisis

    def _cooldown_ok(self, symbol, minutes=15):  # FIX 6: 15 min (era 10)
        ts = self._cooldowns.get(symbol)
        return not (ts and (time.time() - ts) < minutes * 60)

    def _hora_ok(self):
        """FIX 7: evitar horas de bajo volumen."""
        hora_utc = datetime.utcnow().hour
        if hora_utc in SKIP_HOURS_UTC:
            return False
        return True

    def analyze(self, symbol):
        if symbol in self.open_trades or not self._cooldown_ok(symbol):
            return None

        # FIX 7: no operar en horas de bajo volumen
        if not self._hora_ok():
            return None

        # FIX 3: no hacer shorts si BTC está subiendo con fuerza
        if self._btc_change_1h >= BTC_BULL_BLOCK_PCT:
            return None

        closes, highs, lows, volumes, opens = self._klines(symbol, '5m', 60)
        if not closes or len(closes) < 26: return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0: return None

        price  = ticker['price']
        change = ticker['change']

        # ── Indicadores ────────────────────────────────────────────────
        ema9  = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, min(50, len(closes)))
        rsi   = calc_rsi(closes, 14)
        rsi_r = calc_rsi(closes[-20:], 10)    # RSI corto — más reactivo a reversals
        ml, sg, hist = calc_macd(closes)
        bb_u, bb_m, bb_l = calc_bollinger(closes, 20)
        atr   = calc_atr(highs, lows, closes, 14)
        vs    = vol_spike(volumes)

        ema_bear    = ema9 < ema21 < ema50
        ema_gap_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
        trend_5     = (closes[-1] - closes[-6])  / closes[-6]  * 100 if len(closes) >= 6  else 0
        trend_10    = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0
        bb_pos      = (price - bb_l) / (bb_u - bb_l) if (bb_u - bb_l) > 0 else 0.5
        near_high   = price >= max(closes[-15:]) * 0.98 if len(closes) >= 15 else False
        red_candles = sum(1 for i in range(-4, 0) if opens and closes[i] < opens[i]) if opens else 0

        # ATR como % del precio — mide la volatilidad real
        atr_pct = (atr / price * 100) if price > 0 else 0

        # ── Requisito duro: EMA bajista ─────────────────────────────────
        if not ema_bear:
            return None

        # FIX 5: si BTC está subiendo moderado, exigir score más alto
        score_min = MIN_SCORE + (10 if self._btc_change_1h > 0.5 else 0)

        ss, sr = 0, []

        # ── 1. EMA bajista (base) ────────────────────────────────────────
        if ema_gap_pct > 1.5:
            p = min(35, 28 + int(ema_gap_pct * 4)); ss += p; sr.append(f"EMA--({p})")
        else:
            p = min(28, 20 + int(ema_gap_pct * 5)); ss += p; sr.append(f"EMA-({p})")

        # ── 2. RSI sobrecomprado ─────────────────────────────────────────
        rsi_max = max(rsi, rsi_r)
        if   rsi_max > 82: ss += 38; sr.append(f"RSI{rsi_max:.0f}(38)")
        elif rsi_max > 76: ss += 30; sr.append(f"RSI{rsi_max:.0f}(30)")
        elif rsi_max > 70: ss += 20; sr.append(f"RSI{rsi_max:.0f}(20)")
        elif rsi_max > 65: ss += 10; sr.append(f"RSI{rsi_max:.0f}(10)")
        else:              ss -= 20; sr.append(f"RSI{rsi_max:.0f}(-20)")  # no sobrecomprado

        # ── 3. MACD bajista ──────────────────────────────────────────────
        if ml < sg and hist < 0:
            p = 22 if abs(hist) > abs(ml) * 0.35 else 15
            ss += p; sr.append(f"MACD-({p})")
        elif ml > 0 and hist > 0:
            ss -= 15; sr.append("MACD+(-15)")

        # ── 4. Bollinger — precio en zona alta ───────────────────────────
        if   bb_pos >= 0.95: ss += 25; sr.append("BB_top(25)")
        elif bb_pos >= 0.85: ss += 17; sr.append("BB_high(17)")
        elif bb_pos >= 0.70: ss += 8;  sr.append("BB_mid+(8)")
        elif bb_pos <  0.40: ss -= 12; sr.append("BB_low(-12)")  # precio ya bajo

        # ── 5. Volumen bajista ───────────────────────────────────────────
        if vs >= 2.0 and trend_5 < -0.3:
            p = min(18, int(vs * 8)); ss += p; sr.append(f"VolVenta{vs:.1f}x({p})")
        elif vs >= 1.5:
            p = min(12, int(vs * 6)); ss += p; sr.append(f"Vol{vs:.1f}x({p})")
        elif vs < 1.2:
            ss -= 8; sr.append("VolBajo(-8)")

        # ── 6. Tendencia reciente ─────────────────────────────────────────
        if trend_5 < -1.5 and trend_10 < -2.5:
            ss += 20; sr.append("Bajada--(20)")
        elif trend_5 < -0.8:
            ss += 12; sr.append("Bajada-(12)")
        elif trend_5 > 1.0:
            ss -= 15; sr.append("Subida(-15)")  # tendencia opuesta

        # ── 7. Cambio 24h ─────────────────────────────────────────────────
        if   change > 6.0: p = min(15, int(change * 2));   ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change > 3.0: p = min(10, int(change * 1.5)); ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change < -4.0: ss -= 12; sr.append(f"24h{change:.1f}%(-12)")  # ya cayó mucho

        # ── 8. Confirmaciónnes adicionales ──────────────────────────────
        if near_high:        ss += 12; sr.append("NearHigh(12)")
        if red_candles >= 3: ss += 10; sr.append(f"Rojas{red_candles}(10)")

        # ── 9. Volatilidad útil — shorts necesitan movimiento ────────────
        if atr_pct < 0.3:
            ss -= 10; sr.append("ATRbajo(-10)")  # demasiado flat para short
        elif atr_pct > 1.5:
            ss += 8; sr.append(f"ATR{atr_pct:.1f}%(8)")

        # ── FIX 4: TP dinámico garantizando rentabilidad ─────────────────
        # Mínimo: cubrir comisión + 0.2% ganancia neta con el leverage
        tp_dyn = max(
            TP_PCT,
            TP_MIN_RENTABLE,                          # nunca por debajo del break-even
            min(TP_PCT * 2.5, atr_pct * 2.0)          # ATR-based cap
        )

        if ss >= score_min:
            return {
                'price': price, 'change': change, 'score': ss,
                'reasons': ' | '.join(sr), 'rsi': rsi,
                'vol': vs, 'tp_pct': tp_dyn, 'sl_pct': SL_PCT,
                'bb_pos': round(bb_pos * 100, 1), 'atr_pct': round(atr_pct, 2),
                'score_min': score_min,
            }
        return None

    # ---------------------------------------------------------------- órdenes (FIX 2)

    def _place_short(self, symbol, usdt_qty, price):
        """
        FIX 2: intenta orden LÍMITE primero (maker 0.02%).
        Fallback 1: mercado con quoteOrderQty.
        Fallback 2: mercado con contratos.
        """
        qty_c, _ = self._qty_contratos(symbol, price)

        # Intento 1: LÍMITE — maker fee 0.02%
        if USE_LIMIT_ORDERS:
            limit_price = round(price * (1 - LIMIT_OFFSET_PCT / 100), 8)
            r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':        symbol,
                'side':          'SELL',
                'positionSide':  'SHORT',
                'type':          'LIMIT',
                'price':         str(limit_price),
                'quoteOrderQty': str(round(usdt_qty, 2)),
                'timeInForce':   'GTC',
            })
            d = r.json()
            if d.get('code') == 0:
                log.info(f"  SHORT LÍMITE OK ${usdt_qty} @ ${limit_price:.6f} (maker 0.02%)")
                return d.get('data', {}).get('orderId', 'OK'), 'quote'
            log.warning(f"  Límite falló [{d.get('code')}] — fallback mercado")

        # Intento 2: mercado con quoteOrderQty
        r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':        symbol,
            'side':          'SELL',
            'positionSide':  'SHORT',
            'type':          'MARKET',
            'quoteOrderQty': str(round(usdt_qty, 2)),
        })
        d = r.json()
        if d.get('code') == 0:
            log.info(f"  SHORT MERCADO quoteQty OK (${usdt_qty})")
            return d.get('data', {}).get('orderId', 'OK'), 'quote'

        # Intento 3: contratos
        log.warning(f"  quoteOrderQty falló [{d.get('code')}] — fallback contratos")
        if not qty_c: return None, None
        r2 = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':       symbol,
            'side':         'SELL',
            'positionSide': 'SHORT',
            'type':         'MARKET',
            'quantity':     str(qty_c),
        })
        d2 = r2.json()
        if d2.get('code') == 0:
            return d2.get('data', {}).get('orderId', 'OK'), 'contracts'
        log.error(f"  Todos los métodos fallaron [{d2.get('code')}]: {d2.get('msg')}")
        return None, None

    def _cond_order(self, symbol, qty_c, usdt_qty, stop_price, otype, method='quote'):
        try:
            if method == 'quote':
                params = {'symbol': symbol, 'side': 'BUY', 'positionSide': 'SHORT',
                          'type': otype, 'quoteOrderQty': str(round(usdt_qty, 2)),
                          'stopPrice': str(round(stop_price, 8))}
            else:
                params = {'symbol': symbol, 'side': 'BUY', 'positionSide': 'SHORT',
                          'type': otype, 'quantity': str(qty_c),
                          'stopPrice': str(round(stop_price, 8))}
            r  = bingx_request('POST', '/openApi/swap/v2/trade/order', params)
            d  = r.json()
            ok = d.get('code') == 0
            lbl = "TP" if "TAKE" in otype else "SL"
            log.info(f"  {lbl} {'OK' if ok else f'ERR [{d.get(chr(99))}]'} @ ${stop_price:.6f}")
            return ok
        except Exception as e:
            log.warning(f"  {otype}: {e}")
            return False

    def _close_short_order(self, symbol, t):
        method   = t.get('method', 'quote')
        usdt_qty = t.get('usdt_qty', POSITION_SIZE)
        qty_c    = t.get('qty_c', 0)
        if method == 'quote':
            params = {'symbol': symbol, 'side': 'BUY', 'positionSide': 'SHORT',
                      'type': 'MARKET', 'quoteOrderQty': str(round(usdt_qty, 2)),
                      'reduceOnly': 'true'}
        else:
            params = {'symbol': symbol, 'side': 'BUY', 'positionSide': 'SHORT',
                      'type': 'MARKET', 'quantity': str(qty_c), 'reduceOnly': 'true'}
        r = bingx_request('POST', '/openApi/swap/v2/trade/order', params)
        return r.json().get('code') == 0

    def _tiene_posicion(self, symbol):
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
            d = r.json()
            if d.get('code') == 0:
                for p in (d.get('data') or []):
                    amt = float(p.get('positionAmt', 0) or 0)
                    if abs(amt) > 0:
                        return True, 'LONG' if amt > 0 else 'SHORT'
        except Exception as e:
            log.debug(f"posicion {symbol}: {e}")
        return False, None

    # ---------------------------------------------------------------- lifecycle

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  [SEÑAL] SHORT {symbol} score:{sig['score']:.0f}/{sig['score_min']:.0f}")
            return False
        if symbol in self.open_trades: return False

        tiene, dir_bx = self._tiene_posicion(symbol)
        if tiene:
            log.info(f"  {symbol} ya tiene {dir_bx} — skip")
            return False

        price    = sig['price']
        usdt_qty = round(max(min(POSITION_SIZE, 8.0), MIN_TRADE), 2)
        tp       = price * (1 - sig['tp_pct'] / 100)
        sl       = price * (1 + sig['sl_pct'] / 100)

        log.info(f"\n  ➤ SHORT {symbol}")
        log.info(f"  Score:{sig['score']:.0f}/{sig['score_min']:.0f} RSI:{sig['rsi']:.0f} BB:{sig['bb_pos']}% ATR:{sig['atr_pct']}%")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entry:${price:.6f} Capital:${usdt_qty} TP:{sig['tp_pct']:.2f}% SL:{sig['sl_pct']:.1f}%")

        oid, method = self._place_short(symbol, usdt_qty, price)
        if not oid:
            log.error(f"  No se pudo abrir {symbol}")
            return False

        time.sleep(0.5)
        qty_c, _ = self._qty_contratos(symbol, price)
        time.sleep(0.3)
        tp_ok = self._cond_order(symbol, qty_c, usdt_qty, tp, 'TAKE_PROFIT_MARKET', method)
        time.sleep(0.3)
        sl_ok = self._cond_order(symbol, qty_c, usdt_qty, sl, 'STOP_MARKET', method)

        self.open_trades[symbol] = {
            'entry': price, 'qty_c': qty_c, 'usdt_qty': usdt_qty, 'method': method,
            'tp': tp, 'sl': sl, 'tp_pct': sig['tp_pct'], 'sl_pct': sig['sl_pct'],
            'lowest': price, 'order_id': oid, 'tp_ok': tp_ok, 'sl_ok': sl_ok,
            'opened_at': datetime.now(), 'score': sig['score'],
        }
        self.stats['exec'] += 1

        fee_label = "maker 0.02%" if USE_LIMIT_ORDERS else "taker 0.05%"
        self._tg(
            f"<b>🔴 SHORT ABIERTO</b>\n<b>{symbol}</b> | Score:{sig['score']:.0f}/100\n"
            f"Entrada: ${price:.6f}\n"
            f"{'✅' if tp_ok else '⚠️'} TP: ${tp:.6f} (-{sig['tp_pct']:.2f}%)\n"
            f"{'✅' if sl_ok else '⚠️'} SL: ${sl:.6f} (+{sig['sl_pct']:.1f}%)\n"
            f"Capital: ${usdt_qty} x{LEVERAGE} = ${usdt_qty*LEVERAGE:.1f} USDT\n"
            f"Fee: {fee_label} | RSI:{sig['rsi']:.0f} BB:{sig['bb_pos']}%\n"
            f"{sig['reasons']}"
        )
        return True

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades: return False
        t = self.open_trades[symbol]
        self._close_short_order(symbol, t)

        cambio  = (t['entry'] - cur_price) / t['entry']
        pnl     = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION_ACTUAL)
        pnl_pct = (pnl / t['usdt_qty']) * 100

        self.stats['closed'] += 1
        self.stats['pnl']    += pnl
        if pnl > 0: self.stats['wins']   += 1
        else:        self.stats['losses'] += 1

        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        mins  = int((datetime.now() - t['opened_at']).total_seconds() / 60)
        emoji = "✅" if pnl > 0 else "❌"

        log.info(f"  {emoji} {reason} {symbol} PnL:${pnl:+.3f}({pnl_pct:+.1f}%) {mins}min")
        self._tg(
            f"<b>{emoji} SHORT CERRADO — {reason}</b>\n<b>{symbol}</b>\n"
            f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%)\n"
            f"Entry: ${t['entry']:.6f} → Exit: ${cur_price:.6f}\n"
            f"Duración: {mins} min\n"
            f"<b>Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)</b>"
        )
        self._cooldowns[symbol] = time.time()
        del self.open_trades[symbol]
        return True

    # ---------------------------------------------------------------- monitor

    async def _sync_bingx(self):
        if not self.open_trades or not AUTO_TRADING: return
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {})
            d = r.json()
            if d.get('code') != 0: return
            pos = {p.get('symbol'): float(p.get('positionAmt', 0) or 0)
                   for p in (d.get('data') or [])
                   if abs(float(p.get('positionAmt', 0) or 0)) > 0}
            for sym in list(self.open_trades.keys()):
                if sym not in pos:
                    t   = self.open_trades[sym]
                    tk  = self._ticker(sym)
                    cur = tk['price'] if tk else t['entry']
                    cambio  = (t['entry'] - cur) / t['entry']
                    pnl     = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION_ACTUAL)
                    pnl_pct = (pnl / t['usdt_qty']) * 100
                    self.stats['closed'] += 1
                    self.stats['pnl']    += pnl
                    if pnl >= 0: self.stats['wins']   += 1
                    else:        self.stats['losses'] += 1
                    total = self.stats['wins'] + self.stats['losses']
                    wr    = self.stats['wins'] / total * 100 if total else 0
                    mins  = int((datetime.now() - t['opened_at']).total_seconds() / 60)
                    emoji = "✅" if pnl >= 0 else "❌"
                    log.info(f"  SYNC: {sym} cerrado BingX ${pnl:+.3f}")
                    self._tg(
                        f"<b>{emoji} SHORT cerrado BingX</b>\n<b>{sym}</b>\n"
                        f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%)\n"
                        f"Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%"
                    )
                    self._cooldowns[sym] = time.time()
                    del self.open_trades[sym]
        except Exception as e:
            log.debug(f"sync: {e}")

    async def monitor_trades(self):
        await self._sync_bingx()
        for sym in list(self.open_trades.keys()):
            try:
                t   = self.open_trades[sym]
                tk  = self._ticker(sym)
                if not tk: continue
                cur     = tk['price']
                pnl_pct = (t['entry'] - cur) / t['entry'] * 100

                # Trailing stop
                if TRAILING and cur < t['lowest']:
                    t['lowest'] = cur
                    if pnl_pct >= 0.6:
                        profit = t['entry'] - cur
                        new_sl = t['entry'] - profit * 0.60
                        if new_sl < t['sl']:
                            t['sl'] = new_sl
                            log.info(f"  Trailing SL {sym}: ${new_sl:.6f} (protege {pnl_pct*0.6:.1f}%)")

                if abs(pnl_pct) > 0.3:
                    log.info(f"  {sym}: {pnl_pct:+.2f}% entry:${t['entry']:.4f} cur:${cur:.4f}")

                if cur <= t['tp']:   self.close_trade(sym, cur, "TAKE PROFIT")
                elif cur >= t['sl']: self.close_trade(sym, cur, "STOP LOSS")
            except Exception as e:
                log.debug(f"Monitor {sym}: {e}")

    def _reporte_horario(self):
        if datetime.now() - self._last_report < timedelta(hours=1): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        pos_txt = ""
        for sym, t in self.open_trades.items():
            tk      = self._ticker(sym)
            cur     = tk['price'] if tk else t['entry']
            pnl_pct = (t['entry'] - cur) / t['entry'] * 100
            pos_txt += f"  {sym}: {pnl_pct:+.2f}%\n"
        fee_label = "maker 0.02%" if USE_LIMIT_ORDERS else "taker 0.05%"
        self._tg(
            f"<b>📊 Reporte horario</b>\n"
            f"PnL total: ${self.stats['pnl']:+.3f}\n"
            f"WR: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)\n"
            f"Trades: {self.stats['closed']} | Abiertos: {len(self.open_trades)}/{MAX_TRADES}\n"
            f"BTC 1h: {self._btc_change_1h:+.2f}% | Fee: {fee_label}\n"
            + (pos_txt if pos_txt else "  sin posiciones\n")
        )

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=6
                )
        except: pass

    # ---------------------------------------------------------------- loop

    async def run(self):
        log.info("\n▶  Bot SHORT arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                now = time.time()

                if now - last_refresh > 600:
                    self._get_symbols(); last_refresh = now

                # FIX 3: actualizar tendencia BTC cada ciclo
                self._update_btc_trend()

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                btc_state = "⚠️ BTC SUBE" if self._btc_change_1h >= BTC_BULL_BLOCK_PCT else "OK"
                hora_state = "🌙 HORA BAJA" if not self._hora_ok() else "☀️"

                log.info(f"\n{'='*65}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                log.info(f"  BTC 1h:{self._btc_change_1h:+.2f}% {btc_state} | {hora_state}")
                log.info(f"{'='*65}\n")

                await self.monitor_trades()
                self._reporte_horario()

                if len(self.open_trades) < MAX_TRADES:
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.open_trades) >= MAX_TRADES: break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            log.info(f"  ★ SHORT {sym} score:{sig['score']:.0f} RSI:{sig['rsi']:.0f} BB:{sig['bb_pos']}%")
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.12)
                        if (i + 1) % 25 == 0:
                            log.info(f"  ...{i+1}/{len(self.symbols)} analizados")
                    log.info(f"\n  {len(self.symbols)} pares | {found} señales SHORT")
                else:
                    log.info(f"  Max ({MAX_TRADES}) — esperando cierre")

                log.info(f"\n  Próximo ciclo en {INTERVAL}s\n")
                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Detenido"); break
            except Exception as e:
                log.error(f"Error loop #{iteration}: {e}")
                await asyncio.sleep(20)

async def main():
    try:
        await ShortBot().run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Terminado")
