#!/usr/bin/env python3
"""
BOT SHORTS PROFESIONAL v2.0
FIX crítico: position sizing en USDT directo (quoteOrderQty)
MEJORAS:
  - Órdenes en USDT usando quoteOrderQty → siempre $7 exactos
  - Score recalibrado (MIN_SCORE 75)
  - Cooldown por par (evita re-entrar en mismo par)
  - Mejor gestión trailing stop
  - Reporte horario a Telegram
  - Retry automático en errores de API
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

BASE_URL = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ============================================================================
# API BINGX con retry
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
                log.warning(f"  API retry {attempt+1}/{retries}: {e}")
                time.sleep(1.5)
            else:
                raise

# ============================================================================
# INDICADORES
# ============================================================================

def calc_ema(prices, period):
    if not prices: return 0
    if len(prices) < period:
        return sum(prices) / len(prices)
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
        log.info("=" * 65)
        log.info("  BOT SHORTS PROFESIONAL v2.0")
        log.info("=" * 65)
        log.info(f"  Modo:      {'AUTO SHORTS' if AUTO_TRADING else 'SOLO SEÑALES'}")
        log.info(f"  Capital:   ${POSITION_SIZE} USDT por trade")
        log.info(f"  Leverage:  {LEVERAGE}x  →  posicion ${POSITION_SIZE * LEVERAGE:.1f} USDT")
        log.info(f"  TP / SL:   {TP_PCT}% / {SL_PCT}%   (RR {TP_PCT/SL_PCT:.1f}:1)")
        log.info(f"  Max:       {MAX_TRADES} trades | Score≥{MIN_SCORE}")
        log.info(f"  Trailing:  {'ON' if TRAILING else 'OFF'}")
        log.info("=" * 65)

        self.symbols      = []
        self.open_trades  = {}
        self._contracts   = {}
        self._cooldowns   = {}
        self._last_report = datetime.now()
        self.stats = {'exec': 0, 'closed': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()
        self._tg(
            f"<b>🔴 Bot SHORTS v2.0 iniciado</b>\n"
            f"{'✅ AUTO ON' if AUTO_TRADING else '⚠️ Solo señales'}\n"
            f"Capital: ${POSITION_SIZE} x{LEVERAGE} | TP:{TP_PCT}% SL:{SL_PCT}%\n"
            f"Score≥{MIN_SCORE} | Pares:{MAX_SYMBOLS} | Ciclo:{INTERVAL}s"
        )

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING:
            log.info("Modo señales — trades desactivados")
            return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("Credenciales vacías — AUTO_TRADING OFF")
            AUTO_TRADING = False
            return
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/balance', {})
            d = r.json()
            if d.get('code') == 0:
                bal = d.get('data', {})
                eq  = bal.get('equity', bal.get('balance', '?'))
                log.info(f"BingX OK | Balance: ${eq} USDT | API: {BINGX_API_KEY[:10]}...")
            else:
                log.error(f"BingX error [{d.get('code')}]: {d.get('msg')}")
                AUTO_TRADING = False
        except Exception as e:
            log.error(f"Error verificando API: {e}")
            AUTO_TRADING = False

    def _load_contracts(self):
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                for c in d.get('data', []):
                    sym = c.get('symbol', '')
                    self._contracts[sym] = {
                        'step':  float(c.get('tradeMinQuantity', 1)),
                        'prec':  int(c.get('quantityPrecision', 2)),
                        'ctval': float(c.get('contractSize', 1)),
                    }
                log.info(f"Contratos cargados: {len(self._contracts)}")
        except Exception as e:
            log.warning(f"Error contratos: {e}")

    def _get_symbols(self):
        excl = {
            'GOLD','SILVER','XAG','XAU','PAXG','XAUT','OIL','BRENT','WTI','CRUDE',
            'PLATINUM','PALLADIUM','COPPER','NICKEL','TSLA','AAPL','MSFT','GOOGL',
            'AMZN','META','NVDA','COIN','MSTR','SP500','SPX','SPY','QQQ','NASDAQ',
            'DOW','DOWJONES','DJI','RUSSELL','DAX','FTSE','CAC','NIKKEI','HANG',
            'HSI','BOVESPA','IBEX','DOW30','DJIA','US30','NAS100','US500',
            'EUR','GBP','JPY','CHF','AUD','CAD','NZD','EURUSD','GBPUSD','USDJPY',
            'WHEAT','CORN','SUGAR','COFFEE','COTTON','LUMBER',
        }
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                items = []
                for t in d.get('data', []):
                    sym = t.get('symbol', '')
                    if not sym.endswith('-USDT'): continue
                    base = sym.replace('-USDT', '').upper()
                    if any(k in base for k in excl): continue
                    if any(w in base for w in ['DOW','JONES','NASDAQ','INDEX','SP500','STOCK']): continue
                    try:
                        price = float(t.get('lastPrice', 0))
                        vol   = float(t.get('volume', 0)) * price
                        if vol < MIN_VOLUME or price < 0.000001: continue
                        items.append({'symbol': sym, 'vol': vol})
                    except: continue
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                log.info(f"Pares cargados: {len(self.symbols)}")
                return
        except Exception as e:
            log.warning(f"Error símbolos: {e}")
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT',
                        'DOGE-USDT','ADA-USDT','AVAX-USDT','LINK-USDT','MATIC-USDT']

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
                return {'price': float(t.get('lastPrice', 0)),
                        'change': float(t.get('priceChangePercent', 0))}
        except: pass
        return None

    # ---------------------------------------------------------------- sizing

    def _qty_contratos(self, symbol, price):
        """Calcula cantidad en contratos para fallback."""
        info  = self._contracts.get(symbol, {'step': 1.0, 'prec': 2, 'ctval': 1.0})
        step  = max(info['step'], 0.0001)
        prec  = info['prec']
        ctval = info.get('ctval', 1.0)
        price_ct = price * ctval if ctval != 1.0 else price
        if price_ct <= 0: return None, 0
        raw  = POSITION_SIZE / price_ct
        qty  = round(math.ceil(raw / step) * step, prec)
        val  = qty * price_ct
        i = 0
        while val < MIN_TRADE and i < 500:
            qty += step; qty = round(qty, prec); val = qty * price_ct; i += 1
        if val > POSITION_SIZE * 1.3:
            qty = round(math.floor((POSITION_SIZE / price_ct) / step) * step, prec)
            val = qty * price_ct
        return qty, round(val, 4)

    # ---------------------------------------------------------------- análisis

    def _cooldown_ok(self, symbol, minutes=10):
        ts = self._cooldowns.get(symbol)
        return not (ts and (time.time() - ts) < minutes * 60)

    def analyze(self, symbol):
        if symbol in self.open_trades or not self._cooldown_ok(symbol):
            return None

        closes, highs, lows, volumes, opens = self._klines(symbol, '5m', 60)
        if not closes or len(closes) < 26: return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0: return None

        price  = ticker['price']
        change = ticker['change']

        ema9  = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, min(50, len(closes)))
        rsi   = calc_rsi(closes, 14)
        rsi_r = calc_rsi(closes[-30:], 14)
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

        if not ema_bear:
            return None

        ss, sr = 0, []

        # EMA bajista
        if ema_gap_pct > 1.5:
            p = min(35, 28 + int(ema_gap_pct * 4)); ss += p; sr.append(f"EMA--({p})")
        else:
            p = min(28, 20 + int(ema_gap_pct * 5)); ss += p; sr.append(f"EMA-({p})")

        # RSI sobrecomprado
        rsi_max = max(rsi, rsi_r)
        if   rsi_max > 82: ss += 38; sr.append(f"RSI{rsi_max:.0f}(38)")
        elif rsi_max > 76: ss += 30; sr.append(f"RSI{rsi_max:.0f}(30)")
        elif rsi_max > 70: ss += 20; sr.append(f"RSI{rsi_max:.0f}(20)")
        elif rsi_max > 65: ss += 10; sr.append(f"RSI{rsi_max:.0f}(10)")
        else:              ss -= 18; sr.append(f"RSI{rsi_max:.0f}(-18)")

        # MACD
        if ml < sg and hist < 0:
            p = 22 if abs(hist) > abs(ml) * 0.35 else 15
            ss += p; sr.append(f"MACD-({p})")
        elif ml > 0 and hist > 0:
            ss -= 12; sr.append("MACD+(-12)")

        # Bollinger
        if   bb_pos >= 0.95: ss += 25; sr.append("BB_top(25)")
        elif bb_pos >= 0.85: ss += 17; sr.append("BB_high(17)")
        elif bb_pos >= 0.70: ss += 8;  sr.append("BB_mid+(8)")
        elif bb_pos <  0.40: ss -= 10; sr.append("BB_low(-10)")

        # Volumen
        if vs >= 2.0 and trend_5 < -0.3:
            p = min(18, int(vs * 8)); ss += p; sr.append(f"VolVenta{vs:.1f}x({p})")
        elif vs >= 1.5:
            p = min(12, int(vs * 6)); ss += p; sr.append(f"Vol{vs:.1f}x({p})")
        elif vs < 1.2:
            ss -= 8; sr.append("VolBajo(-8)")

        # Tendencia
        if trend_5 < -1.5 and trend_10 < -2.0:
            ss += 18; sr.append("Bajada--(18)")
        elif trend_5 < -0.5:
            ss += 10; sr.append("Bajada-(10)")
        elif trend_5 > 0.8:
            ss -= 12; sr.append("Subida(-12)")

        # Cambio 24h
        if   change > 6.0: p = min(15, int(change * 2));   ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change > 3.0: p = min(10, int(change * 1.5)); ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change < -3.0: ss -= 10; sr.append(f"24h{change:.1f}%(-10)")

        # Máximos + velas rojas
        if near_high:           ss += 12; sr.append("NearHigh(12)")
        if red_candles >= 3:    ss += 10; sr.append(f"Rojas{red_candles}(10)")

        atr_pct = (atr / price * 100) if price > 0 else 0
        tp_dyn  = max(TP_PCT, min(TP_PCT * 2.5, atr_pct * 2.0))

        if ss >= MIN_SCORE:
            return {
                'price': price, 'change': change, 'score': ss,
                'reasons': ' | '.join(sr), 'rsi': rsi,
                'vol': vs, 'tp_pct': tp_dyn, 'sl_pct': SL_PCT,
                'bb_pos': round(bb_pos * 100, 1),
            }
        return None

    # ---------------------------------------------------------------- órdenes

    def _place_short(self, symbol, usdt_qty):
        """
        FIX: usa quoteOrderQty (USDT directo) para garantizar siempre $7 exactos.
        Fallback automático a quantity en contratos si falla.
        """
        # Intento 1: quoteOrderQty (USDT directo)
        r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':        symbol,
            'side':          'SELL',
            'positionSide':  'SHORT',
            'type':          'MARKET',
            'quoteOrderQty': str(usdt_qty),
        })
        d = r.json()
        if d.get('code') == 0:
            log.info(f"  SHORT quoteOrderQty OK (${usdt_qty} USDT)")
            return d.get('data', {}).get('orderId', 'OK'), 'quote'

        # Intento 2: contratos (fallback)
        log.warning(f"  quoteOrderQty falló [{d.get('code')}] — fallback contratos")
        tk = self._ticker(symbol)
        if not tk: return None, None
        qty, val = self._qty_contratos(symbol, tk['price'])
        if not qty or val < MIN_TRADE:
            log.error(f"  Qty inválida: {qty} (${val:.2f})")
            return None, None
        r2 = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':       symbol,
            'side':         'SELL',
            'positionSide': 'SHORT',
            'type':         'MARKET',
            'quantity':     str(qty),
        })
        d2 = r2.json()
        if d2.get('code') == 0:
            log.info(f"  SHORT contratos OK (qty={qty} ≈${val:.2f})")
            return d2.get('data', {}).get('orderId', 'OK'), 'contracts'
        log.error(f"  Ambos métodos fallaron [{d2.get('code')}]: {d2.get('msg')}")
        return None, None

    def _cond_order(self, symbol, qty, usdt_qty, stop_price, otype, method='quote'):
        try:
            if method == 'quote':
                params = {'symbol': symbol, 'side': 'BUY', 'positionSide': 'SHORT',
                          'type': otype, 'quoteOrderQty': str(round(usdt_qty, 2)),
                          'stopPrice': str(round(stop_price, 8))}
            else:
                params = {'symbol': symbol, 'side': 'BUY', 'positionSide': 'SHORT',
                          'type': otype, 'quantity': str(qty),
                          'stopPrice': str(round(stop_price, 8))}
            r  = bingx_request('POST', '/openApi/swap/v2/trade/order', params)
            d  = r.json()
            ok = d.get('code') == 0
            lbl = "TP" if "TAKE" in otype else "SL"
            log.info(f"  {lbl} {'OK' if ok else f'ERR [{d.get(chr(99))}]'} @ ${stop_price:.6f}")
            return ok
        except Exception as e:
            log.warning(f"  {otype} exc: {e}")
            return False

    def _close_short_order(self, symbol, t):
        method   = t.get('method', 'quote')
        usdt_qty = t.get('usdt_qty', POSITION_SIZE)
        qty      = t.get('qty', 0)
        if method == 'quote':
            params = {'symbol': symbol, 'side': 'BUY', 'positionSide': 'SHORT',
                      'type': 'MARKET', 'quoteOrderQty': str(round(usdt_qty, 2)),
                      'reduceOnly': 'true'}
        else:
            params = {'symbol': symbol, 'side': 'BUY', 'positionSide': 'SHORT',
                      'type': 'MARKET', 'quantity': str(qty), 'reduceOnly': 'true'}
        r = bingx_request('POST', '/openApi/swap/v2/trade/order', params)
        return r.json().get('code') == 0

    def _tiene_posicion_bingx(self, symbol):
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
            d = r.json()
            if d.get('code') == 0:
                for p in (d.get('data') or []):
                    amt = float(p.get('positionAmt', 0) or 0)
                    if abs(amt) > 0:
                        return True, 'LONG' if amt > 0 else 'SHORT'
        except Exception as e:
            log.debug(f"_posicion {symbol}: {e}")
        return False, None

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  [SEÑAL] SHORT {symbol} score:{sig['score']:.0f}")
            return False
        if symbol in self.open_trades: return False

        tiene_pos, pos_dir = self._tiene_posicion_bingx(symbol)
        if tiene_pos:
            log.info(f"  {symbol} ya tiene {pos_dir} — skip")
            return False

        price    = sig['price']
        usdt_qty = round(max(min(POSITION_SIZE, POSITION_SIZE), MIN_TRADE), 2)
        tp       = price * (1 - sig['tp_pct'] / 100)
        sl       = price * (1 + sig['sl_pct'] / 100)

        log.info(f"\n  ➤ ABRIENDO SHORT {symbol}")
        log.info(f"  Score:{sig['score']:.0f} | RSI:{sig['rsi']:.0f} | BB:{sig['bb_pos']}%")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entrada:${price:.6f} | Capital:${usdt_qty} USDT | TP:{sig['tp_pct']:.1f}% SL:{sig['sl_pct']:.1f}%")

        oid, method = self._place_short(symbol, usdt_qty)
        if not oid:
            log.error(f"  No se pudo abrir {symbol}")
            return False

        time.sleep(0.5)
        qt_c, _ = self._qty_contratos(symbol, price)
        time.sleep(0.3)
        tp_ok = self._cond_order(symbol, qt_c, usdt_qty, tp, 'TAKE_PROFIT_MARKET', method)
        time.sleep(0.3)
        sl_ok = self._cond_order(symbol, qt_c, usdt_qty, sl, 'STOP_MARKET', method)

        self.open_trades[symbol] = {
            'entry': price, 'qty': qt_c, 'usdt_qty': usdt_qty, 'method': method,
            'tp': tp, 'sl': sl, 'tp_pct': sig['tp_pct'], 'sl_pct': sig['sl_pct'],
            'lowest': price, 'order_id': oid, 'tp_ok': tp_ok, 'sl_ok': sl_ok,
            'opened_at': datetime.now(), 'score': sig['score'],
        }
        self.stats['exec'] += 1

        self._tg(
            f"<b>🔴 SHORT ABIERTO</b>\n<b>{symbol}</b> | Score:{sig['score']:.0f}/100\n"
            f"Entrada: ${price:.6f}\n"
            f"{'✅' if tp_ok else '⚠️'} TP: ${tp:.6f} (-{sig['tp_pct']:.1f}%)\n"
            f"{'✅' if sl_ok else '⚠️'} SL: ${sl:.6f} (+{sig['sl_pct']:.1f}%)\n"
            f"Capital: ${usdt_qty} x{LEVERAGE} = ${usdt_qty * LEVERAGE:.1f} USDT\n"
            f"RSI:{sig['rsi']:.0f} | BB:{sig['bb_pos']}% | Vol:{sig['vol']:.1f}x\n"
            f"{sig['reasons']}"
        )
        return True

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades: return False
        t = self.open_trades[symbol]
        self._close_short_order(symbol, t)
        COMISION = 0.001
        cambio   = (t['entry'] - cur_price) / t['entry']
        pnl      = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION)
        pnl_pct  = (pnl / t['usdt_qty']) * 100
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

    async def _sync_bingx(self):
        if not self.open_trades or not AUTO_TRADING: return
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {})
            d = r.json()
            if d.get('code') != 0: return
            posiciones = {p.get('symbol'): float(p.get('positionAmt', 0) or 0)
                          for p in (d.get('data') or [])
                          if abs(float(p.get('positionAmt', 0) or 0)) > 0}
            for sym in list(self.open_trades.keys()):
                if sym not in posiciones:
                    t   = self.open_trades[sym]
                    tk  = self._ticker(sym)
                    cur = tk['price'] if tk else t['entry']
                    COMISION = 0.001
                    cambio   = (t['entry'] - cur) / t['entry']
                    pnl      = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION)
                    pnl_pct  = (pnl / t['usdt_qty']) * 100
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
                t  = self.open_trades[sym]
                tk = self._ticker(sym)
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
                            log.info(f"  Trailing SL {sym}: ${new_sl:.6f}")
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
            pos_txt += f"  📍 {sym}: {pnl_pct:+.2f}%\n"
        self._tg(
            f"<b>📊 Reporte horario</b>\n"
            f"PnL total: ${self.stats['pnl']:+.3f}\n"
            f"WR: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)\n"
            f"Trades cerrados: {self.stats['closed']}\n"
            f"Abiertos: {len(self.open_trades)}/{MAX_TRADES}\n"
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

    async def run(self):
        log.info("\n▶  Bot SHORT arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                now = time.time()
                if now - last_refresh > 600:
                    self._get_symbols(); last_refresh = now
                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                log.info(f"\n{'='*65}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
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
                            log.info(f"  ★ {sym} score:{sig['score']:.0f} RSI:{sig['rsi']:.0f} BB:{sig['bb_pos']}%")
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.12)
                        if (i + 1) % 25 == 0:
                            log.info(f"  ...{i+1}/{len(self.symbols)} analizados")
                    log.info(f"\n  {len(self.symbols)} pares | {found} señales")
                else:
                    log.info(f"  Max trades ({MAX_TRADES}) — esperando cierre")
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
