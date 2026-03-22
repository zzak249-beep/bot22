#!/usr/bin/env python3
"""
BOT SHORTS PROFESIONAL v2.7.0
════════════════════════════════════════════════
FIXES v2.7.0:
  1. CRÍTICO: Eliminado quoteOrderQty en entrada — causaba trades de <$1
     Ahora SIEMPRE usa quantity (contratos) calculados correctamente
  2. Verificación de balance ANTES de abrir trade
  3. Reconciliación de posiciones SHORT al arrancar con TP/SL
  4. Detección explícita de "Insufficient margin" → aborta sin dejar posición sin TP/SL
  5. _posiciones_reales_bingx() verifica BingX, no solo memoria interna
  6. Balance actualizado en cada ciclo
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re
from datetime import datetime, timedelta
from urllib.parse import urlencode

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

AUTO_TRADING       = clean('AUTO_TRADING_ENABLED',   'true',  'bool')
POSITION_SIZE      = clean('MAX_POSITION_SIZE',        '7',   'float')
MIN_TRADE          = clean('MIN_TRADE_USDT',           '5',   'float')
LEVERAGE           = clean('LEVERAGE',                 '3',   'int')
TP_PCT             = clean('TAKE_PROFIT_PCT',          '1.5', 'float')
SL_PCT             = clean('STOP_LOSS_PCT',            '1.0', 'float')
MAX_TRADES         = clean('MAX_OPEN_TRADES',          '2',   'int')
INTERVAL           = clean('CHECK_INTERVAL',          '120',  'int')
MIN_VOLUME         = clean('MIN_VOLUME_24H',       '500000',  'float')
MAX_SYMBOLS        = clean('MAX_SYMBOLS_TO_ANALYZE',   '50',  'int')
MIN_SCORE          = clean('MIN_SCORE',                '80',  'float')
TRAILING           = clean('TRAILING_STOP_ENABLED',  'true',  'bool')
USE_LIMIT_ORDERS   = clean('USE_LIMIT_ORDERS',       'true',  'bool')
BTC_BULL_BLOCK_PCT = clean('BTC_BULL_BLOCK_PCT',      '1.5',  'float')

LIMIT_OFFSET_PCT = 0.05
SKIP_HOURS_UTC   = {0, 1}
BASE_URL         = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

COMISION_MAKER  = 0.0002
COMISION_TAKER  = 0.0005
COMISION_ACTUAL = COMISION_MAKER if USE_LIMIT_ORDERS else COMISION_TAKER
TP_MIN_RENTABLE = round((COMISION_ACTUAL / LEVERAGE + 0.002) * 100, 3)

def bingx_request(method, endpoint, params, retries=2):
    for attempt in range(retries + 1):
        try:
            p = dict(params)
            p['timestamp'] = int(time.time() * 1000)
            qs  = urlencode(sorted(p.items()))
            sig = hmac.new(BINGX_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY': BINGX_API_KEY,
                   'Content-Type': 'application/x-www-form-urlencoded'}
            r = requests.get(url, headers=hdr, timeout=12) if method == 'GET' \
                else requests.post(url, headers=hdr, timeout=12)
            return r
        except Exception as e:
            if attempt < retries:
                log.warning(f"  retry {attempt+1}: {e}"); time.sleep(1.5)
            else:
                raise

def calc_ema(prices, period):
    if not prices: return 0
    if len(prices) < period: return sum(prices) / len(prices)
    k, e = 2 / (period + 1), prices[0]
    for p in prices[1:]: e = p * k + e * (1 - k)
    return e

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    gains  = [max(0,  prices[i] - prices[i-1]) for i in range(1, len(prices))]
    losses = [max(0, prices[i-1] - prices[i])  for i in range(1, len(prices))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))

def calc_macd(prices):
    if len(prices) < 26: return 0, 0, 0
    ml = calc_ema(prices, 12) - calc_ema(prices, 26)
    return ml, ml * 0.9, ml * 0.1

def calc_bollinger(prices, period=20):
    if len(prices) < period:
        m = sum(prices) / len(prices); return m, m, m
    w = prices[-period:]
    mid = sum(w) / period
    std = (sum((p - mid)**2 for p in w) / period) ** 0.5
    return mid + 2*std, mid, mid - 2*std

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2: return 0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, min(len(closes), period+1))]
    return sum(trs) / len(trs) if trs else 0

def vol_spike(volumes):
    if len(volumes) < 5: return 1.0
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    return (volumes[-1] / avg) if avg > 0 else 1.0

class ShortBot:

    def __init__(self):
        fee_lbl = f"LÍMITE maker {COMISION_MAKER*100:.2f}%" if USE_LIMIT_ORDERS \
                  else f"MERCADO taker {COMISION_TAKER*100:.2f}%"
        log.info("=" * 65)
        log.info("  BOT SHORTS PROFESIONAL v2.7.0")
        log.info("  FIX: qty contratos siempre, balance check, reconciliación")
        log.info("=" * 65)
        log.info(f"  Modo:      {'AUTO' if AUTO_TRADING else 'SEÑALES'}")
        log.info(f"  Capital:   ${POSITION_SIZE} USDT | Leverage: {LEVERAGE}x")
        log.info(f"  TP/SL:     {TP_PCT}% / {SL_PCT}%  RR:{TP_PCT/SL_PCT:.1f}:1")
        log.info(f"  TP mín:    {TP_MIN_RENTABLE}%")
        log.info(f"  Fee:       {fee_lbl}")
        log.info(f"  Score mín: {MIN_SCORE}")
        log.info(f"  Max trades:{MAX_TRADES}")
        log.info("=" * 65)

        self.symbols        = []
        self.open_trades    = {}
        self._contracts     = {}
        self._cooldowns     = {}
        self._last_report   = datetime.now()
        self._btc_change_1h = 0.0
        self._balance       = 0.0
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()
        self._reconciliar_posiciones()

        self._tg(
            f"<b>🔴 Bot SHORTS v2.7.0 iniciado</b>\n"
            f"FIX: qty contratos siempre correctos\n"
            f"Capital: ${POSITION_SIZE} x{LEVERAGE} | TP:{TP_PCT}% SL:{SL_PCT}%\n"
            f"Fee: {fee_lbl} | Score≥{MIN_SCORE}\n"
            f"Balance: ${self._balance:.2f} USDT"
        )

    # ---------------------------------------------------------------- setup

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING: return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("  API keys vacías — modo SEÑALES")
            AUTO_TRADING = False; return
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            log.info(f"  Balance API raw: {str(d)[:200]}")
            if d.get('code') == 0:
                eq = self._extraer_balance(d)
                self._balance = eq
                log.info(f"BingX OK | Balance: ${eq:.2f} USDT")
                if eq < MIN_TRADE:
                    log.warning(f"  Balance ${eq:.2f} bajo pero continuando")
            else:
                log.error(f"BingX [{d.get('code')}]: {d.get('msg')}"); AUTO_TRADING = False
        except Exception as e:
            log.error(f"Error API: {e}"); AUTO_TRADING = False

    def _extraer_balance(self, d):
        """FIX v2.7: Extrae balance de cualquier formato de respuesta BingX."""
        try:
            data = d.get('data', {})
            # Caso 1: data es una lista
            if isinstance(data, list):
                data = data[0] if data else {}
            # Caso 2: data es un dict con 'balance' que es otro dict
            bal = data.get('balance', None)
            if isinstance(bal, dict):
                for key in ['equity', 'balance', 'availableMargin', 'availableBalance', 'walletBalance']:
                    v = bal.get(key, None)
                    if v is not None:
                        return float(str(v) or 0)
            # Caso 3: data tiene equity/balance directamente
            for key in ['equity', 'balance', 'availableMargin', 'availableBalance', 'walletBalance']:
                v = data.get(key, None)
                if v is not None and not isinstance(v, dict):
                    return float(str(v) or 0)
            # Caso 4: buscar recursivamente cualquier número flotante
            def buscar_balance(obj, depth=0):
                if depth > 3: return None
                if isinstance(obj, (int, float)): return float(obj)
                if isinstance(obj, str):
                    try: return float(obj)
                    except: return None
                if isinstance(obj, dict):
                    for k in ['equity', 'balance', 'availableMargin', 'availableBalance']:
                        if k in obj:
                            r = buscar_balance(obj[k], depth+1)
                            if r is not None and r > 0: return r
                if isinstance(obj, list) and obj:
                    return buscar_balance(obj[0], depth+1)
                return None
            result = buscar_balance(data)
            return result if result is not None else 0.0
        except Exception as e:
            log.error(f"  Error extrayendo balance: {e}")
            return 0.0

    def _update_balance(self):
        """Actualiza balance en cada ciclo."""
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/balance', {}).json()
            if d.get('code') == 0:
                eq = self._extraer_balance(d)
                self._balance = eq
                return eq
        except: pass
        return self._balance

    def _balance_suficiente(self):
        """FIX v2.7: Verifica balance real antes de abrir trade."""
        bal = self._update_balance()
        needed = POSITION_SIZE / LEVERAGE
        if bal < needed:
            log.warning(f"  Balance ${bal:.2f} < margen necesario ${needed:.2f} — skip")
            self._tg(f"⚠️ Balance insuficiente: ${bal:.2f} USDT (necesario: ${needed:.2f})")
            return False
        return True

    def _load_contracts(self):
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=15).json()
            if d.get('code') == 0:
                for c in d.get('data', []):
                    self._contracts[c.get('symbol','')] = {
                        'step':  float(c.get('tradeMinQuantity', 1)),
                        'prec':  int(c.get('quantityPrecision', 2)),
                        'ctval': float(c.get('contractSize', 1)),
                    }
                log.info(f"Contratos: {len(self._contracts)}")
        except Exception as e:
            log.warning(f"Error contratos: {e}")

    def _get_symbols(self):
        NO_CRIPTO = [
            'DOW','JONES','SP500','SPX','SPY','QQQ','NASDAQ','RUSSELL',
            'DAX','FTSE','CAC','NIKKEI','HANG','BOVESPA','IBEX',
            'US30','NAS100','US500','DJI','INDEX',
            'GOLD','SILVER','XAU','XAG','PAXG','XAUT',
            'OIL','BRENT','WTI','CRUDE','PETROLEUM',
            'GAS','GASOLINE','NATURAL','PETROL','DIESEL',
            'PLATINUM','PALLADIUM','COPPER','NICKEL','ZINC','IRON',
            'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
            'EUR','GBP','JPY','CHF','AUD','CAD','NZD',
            'WHEAT','CORN','SUGAR','COFFEE','COTTON','LUMBER','SOYBEAN',
        ]
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15).json()
            if d.get('code') == 0:
                items, excl = [], []
                for t in d.get('data', []):
                    sym = t.get('symbol','')
                    if not sym.endswith('-USDT'): continue
                    base = sym.replace('-USDT','').upper()
                    if any(kw in base for kw in NO_CRIPTO): excl.append(base); continue
                    try:
                        price = float(t.get('lastPrice',0))
                        vol   = float(t.get('volume',0)) * price
                        if vol < MIN_VOLUME or price < 0.000001: continue
                        items.append({'symbol':sym,'vol':vol})
                    except: continue
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                log.info(f"Pares: {len(self.symbols)} | Excluidos: {len(excl)}")
                return
        except Exception as e:
            log.warning(f"Error símbolos: {e}")
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT']

    def _reconciliar_posiciones(self):
        """FIX v2.7: Al arrancar detecta SHORTs propios del bot en BingX."""
        if not AUTO_TRADING: return
        log.info("  🔍 Reconciliando posiciones SHORT en BingX...")
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') != 0: return
            recuperadas = 0
            for p in (d.get('data') or []):
                try: amt = float(p.get('positionAmt', 0) or 0)
                except: continue
                if amt >= 0: continue  # Solo SHORTs (negativos)
                sym = p.get('symbol', '')
                if not sym: continue
                try:
                    lev = int(float(p.get('leverage', 0) or 0))
                except: lev = 0
                if lev != 0 and lev != LEVERAGE:
                    log.info(f"  ⏭ {sym} ignorado — leverage {lev}x ≠ {LEVERAGE}x")
                    continue
                try:
                    entry = float(p.get('avgPrice') or p.get('entryPrice') or 0)
                except: entry = 0
                if entry <= 0:
                    tk = self._ticker(sym)
                    entry = tk['price'] if tk else 0
                if entry <= 0: continue
                qty_c    = abs(amt)
                tp_price = entry * (1 - TP_PCT / 100)
                sl_price = entry * (1 + SL_PCT / 100)
                tp_ok = self._cond_order(sym, qty_c, tp_price, 'TAKE_PROFIT_MARKET')
                time.sleep(0.3)
                sl_ok = self._cond_order(sym, qty_c, sl_price, 'STOP_MARKET')
                self.open_trades[sym] = {
                    'entry':entry,'qty_c':qty_c,'usdt_qty':POSITION_SIZE,
                    'tp':tp_price,'sl':sl_price,'tp_pct':TP_PCT,'sl_pct':SL_PCT,
                    'lowest':entry,'order_id':'RECONCILIADO',
                    'tp_ok':tp_ok,'sl_ok':sl_ok,
                    'opened_at':datetime.now(),'score':0,
                }
                recuperadas += 1
                log.info(f"  📉 {sym} SHORT reconciliado | entry=${entry:.6f} qty={qty_c} | TP:{'✅' if tp_ok else '❌'} SL:{'✅' if sl_ok else '❌'}")
            log.info(f"  ✅ Reconciliación: {recuperadas} posiciones SHORT registradas")
        except Exception as e:
            log.error(f"  Error reconciliación: {e}")

    def _posiciones_reales_bingx(self):
        """FIX v2.7: Cuenta SHORTs reales en BingX."""
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') == 0:
                return sum(1 for p in (d.get('data') or [])
                           if float(p.get('positionAmt', 0) or 0) < 0)
        except: pass
        return len(self.open_trades)

    # ---------------------------------------------------------------- datos

    def _klines(self, symbol, interval='5m', limit=60):
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params={'symbol':symbol,'interval':interval,'limit':limit}, timeout=10).json()
            if d.get('code') == 0 and d.get('data'):
                k = d['data']
                return ([float(x['close']) for x in k], [float(x['high']) for x in k],
                        [float(x['low']) for x in k],   [float(x['volume']) for x in k],
                        [float(x['open']) for x in k])
        except: pass
        return None, None, None, None, None

    def _ticker(self, symbol):
        try:
            d = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                             params={'symbol':symbol}, timeout=8).json()
            if d.get('code') == 0 and d.get('data'):
                t = d['data']
                return {'price': float(t.get('lastPrice',0)),
                        'change': float(t.get('priceChangePercent',0))}
        except: pass
        return None

    def _update_btc_trend(self):
        try:
            closes, *_ = self._klines('BTC-USDT', '1h', 3)
            if closes and len(closes) >= 2:
                self._btc_change_1h = (closes[-1] - closes[-2]) / closes[-2] * 100
        except: pass

    # ---------------------------------------------------------------- sizing

    def _qty_contratos(self, symbol, price, usdt_amount=None):
        """
        FIX v2.7: SIEMPRE calcula qty en contratos.
        NUNCA uses quoteOrderQty — BingX ejecuta el mínimo posible con ese parámetro.
        """
        if usdt_amount is None: usdt_amount = POSITION_SIZE
        info  = self._contracts.get(symbol, {'step':1.0,'prec':2,'ctval':1.0})
        step  = max(info['step'], 0.0001)
        prec  = info['prec']
        ctval = info.get('ctval', 1.0)
        ppc   = price * ctval if ctval != 1.0 else price
        if ppc <= 0: return None, 0

        qty = round(math.ceil(usdt_amount / ppc / step) * step, prec)
        val = qty * ppc

        i = 0
        while val < MIN_TRADE and i < 500:
            qty += step; qty = round(qty, prec); val = qty * ppc; i += 1

        if val > usdt_amount * 1.3:
            qty = round(math.floor((usdt_amount * 1.3 / ppc) / step) * step, prec)
            val = qty * ppc

        log.info(f"    qty_contratos: {qty} × ${ppc:.6f} = ${val:.2f} USDT")
        return qty, round(val, 4)

    # ---------------------------------------------------------------- filtros

    def _cooldown_ok(self, symbol):
        ts = self._cooldowns.get(symbol)
        return not (ts and (time.time() - ts) < 15 * 60)

    def _hora_ok(self):
        return datetime.utcnow().hour not in SKIP_HOURS_UTC

    # ---------------------------------------------------------------- análisis

    def analyze(self, symbol):
        if symbol in self.open_trades or not self._cooldown_ok(symbol): return None
        if not self._hora_ok(): return None
        if self._btc_change_1h >= BTC_BULL_BLOCK_PCT: return None

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
        rsi_r = calc_rsi(closes[-20:], 10)
        ml, sg, hist = calc_macd(closes)
        bb_u, bb_m, bb_l = calc_bollinger(closes, 20)
        atr   = calc_atr(highs, lows, closes, 14)
        vs    = vol_spike(volumes)

        if not (ema9 < ema21 < ema50): return None

        ema_gap  = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
        trend_5  = (closes[-1] - closes[-6])  / closes[-6]  * 100 if len(closes) >= 6  else 0
        trend_10 = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0
        bb_pos   = (price - bb_l) / (bb_u - bb_l) if (bb_u - bb_l) > 0 else 0.5
        near_high   = price >= max(closes[-15:]) * 0.98 if len(closes) >= 15 else False
        red_candles = sum(1 for i in range(-4, 0) if opens and closes[i] < opens[i]) if opens else 0
        atr_pct     = (atr / price * 100) if price > 0 else 0

        score_min = MIN_SCORE + (10 if self._btc_change_1h > 0.5 else 0)
        ss, sr = 0, []

        p = min(35, 28 + int(ema_gap * 4)) if ema_gap > 1.5 else min(28, 20 + int(ema_gap * 5))
        ss += p; sr.append(f"EMA-({p})")

        rsi_max = max(rsi, rsi_r)
        if   rsi_max > 82: ss += 38; sr.append(f"RSI{rsi_max:.0f}(38)")
        elif rsi_max > 76: ss += 30; sr.append(f"RSI{rsi_max:.0f}(30)")
        elif rsi_max > 70: ss += 20; sr.append(f"RSI{rsi_max:.0f}(20)")
        elif rsi_max > 65: ss += 10; sr.append(f"RSI{rsi_max:.0f}(10)")
        else:              ss -= 20; sr.append(f"RSI{rsi_max:.0f}(-20)")

        if ml < sg and hist < 0:
            p = 22 if abs(hist) > abs(ml) * 0.35 else 15
            ss += p; sr.append(f"MACD-({p})")
        elif ml > 0 and hist > 0:
            ss -= 15; sr.append("MACD+(-15)")

        if   bb_pos >= 0.95: ss += 25; sr.append("BB_top(25)")
        elif bb_pos >= 0.85: ss += 17; sr.append("BB_high(17)")
        elif bb_pos >= 0.70: ss += 8;  sr.append("BB_mid+(8)")
        elif bb_pos <  0.40: ss -= 12; sr.append("BB_low(-12)")

        if vs >= 2.0 and trend_5 < -0.3:
            p = min(18, int(vs*8)); ss += p; sr.append(f"VolVenta{vs:.1f}x({p})")
        elif vs >= 1.5:
            p = min(12, int(vs*6)); ss += p; sr.append(f"Vol{vs:.1f}x({p})")
        elif vs < 1.2:
            ss -= 8; sr.append("VolBajo(-8)")

        if trend_5 < -1.5 and trend_10 < -2.5: ss += 20; sr.append("Bajada--(20)")
        elif trend_5 < -0.8:                    ss += 12; sr.append("Bajada-(12)")
        elif trend_5 > 1.0:                     ss -= 15; sr.append("Subida(-15)")

        if   change > 6.0: p = min(15, int(change*2));   ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change > 3.0: p = min(10, int(change*1.5)); ss += p; sr.append(f"24h+{change:.1f}%({p})")
        elif change < -4.0: ss -= 12; sr.append(f"24h{change:.1f}%(-12)")

        if near_high:        ss += 12; sr.append("NearHigh(12)")
        if red_candles >= 3: ss += 10; sr.append(f"Rojas{red_candles}(10)")
        if atr_pct < 0.3:    ss -= 10; sr.append("ATRbajo(-10)")
        elif atr_pct > 1.5:  ss += 8;  sr.append(f"ATR{atr_pct:.1f}%(8)")

        tp_dyn = max(TP_PCT, TP_MIN_RENTABLE, min(TP_PCT*2.5, atr_pct*2.0))

        if ss >= score_min:
            return {'price':price,'change':change,'score':ss,'reasons':' | '.join(sr),
                    'rsi':rsi,'vol':vs,'tp_pct':tp_dyn,'sl_pct':SL_PCT,
                    'bb_pos':round(bb_pos*100,1),'atr_pct':round(atr_pct,2),'score_min':score_min}
        return None

    # ---------------------------------------------------------------- órdenes

    def _place_short_entry(self, symbol, usdt_qty, price):
        """
        FIX v2.7 CRÍTICO: SIEMPRE usa quantity (contratos).
        NUNCA quoteOrderQty — BingX ejecuta mínimo posible con ese param.
        """
        qty_c, val = self._qty_contratos(symbol, price, usdt_qty)
        if not qty_c:
            log.error(f"  No se pudo calcular qty_c para {symbol}")
            return None, None

        log.info(f"  Abriendo SHORT {symbol}: {qty_c} contratos = ${val:.2f} USDT")

        if USE_LIMIT_ORDERS:
            limit_price = round(price * (1 + LIMIT_OFFSET_PCT / 100), 8)
            d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':'SELL','positionSide':'SHORT',
                'type':'LIMIT','price':str(limit_price),
                'quantity':str(qty_c),'timeInForce':'GTC',
            }).json()
            if d.get('code') == 0:
                log.info(f"  ENTRADA LÍMITE OK {qty_c} contratos @ ${limit_price:.6f} maker")
                return d.get('data',{}).get('orderId','OK'), qty_c
            if 'margin' in str(d.get('msg','')).lower() or d.get('code') in [101204, 80012]:
                log.error(f"  Insufficient margin — abortando")
                return None, None
            log.warning(f"  Límite falló [{d.get('code')}] — fallback mercado")

        # FIX v2.7: SIEMPRE quantity en contratos, NUNCA quoteOrderQty
        d = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':symbol,'side':'SELL','positionSide':'SHORT',
            'type':'MARKET','quantity':str(qty_c),
        }).json()
        if d.get('code') == 0:
            log.info(f"  ENTRADA MERCADO OK {qty_c} contratos (${val:.2f}) taker")
            return d.get('data',{}).get('orderId','OK'), qty_c

        if 'margin' in str(d.get('msg','')).lower():
            log.error(f"  Insufficient margin — abortando")
            return None, None

        log.error(f"  Entrada fallida [{d.get('code')}]: {d.get('msg')}")
        return None, None

    def _cond_order(self, symbol, qty_c, stop_price, otype):
        if not qty_c or qty_c <= 0:
            log.error(f"  {otype} cancelado: qty_c inválido ({qty_c})")
            return False
        try:
            is_tp = "TAKE" in otype
            lbl   = "TP" if is_tp else "SL"

            if is_tp:
                params = {
                    'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                    'type':'TAKE_PROFIT','quantity':str(qty_c),
                    'price':str(round(stop_price, 8)),
                    'stopPrice':str(round(stop_price, 8)),'timeInForce':'GTC',
                }
            else:
                params = {
                    'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                    'type':'STOP_MARKET','quantity':str(qty_c),
                    'stopPrice':str(round(stop_price, 8)),
                }

            d  = bingx_request('POST', '/openApi/swap/v2/trade/order', params).json()
            ok = d.get('code') == 0
            if ok:
                log.info(f"  {lbl} ✅ @ ${stop_price:.6f} qty={qty_c}")
            else:
                if is_tp:
                    log.warning(f"  TP límite rechazado — fallback mercado")
                    p2 = {'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                          'type':'TAKE_PROFIT_MARKET','quantity':str(qty_c),
                          'stopPrice':str(round(stop_price,8))}
                    d2 = bingx_request('POST', '/openApi/swap/v2/trade/order', p2).json()
                    ok = d2.get('code') == 0
                    if ok: log.info(f"  TP ✅ fallback @ ${stop_price:.6f}")
                    else:  log.error(f"  TP ❌ [{d2.get('code')}]: {d2.get('msg')}")
                else:
                    log.error(f"  {lbl} ❌ [{d.get('code')}]: {d.get('msg')}")
            return ok
        except Exception as e:
            log.error(f"  {otype} excepción: {e}"); return False

    def _close_short(self, symbol, t):
        qty_c = t.get('qty_c', 0)
        if qty_c and qty_c > 0:
            params = {'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                      'type':'MARKET','quantity':str(qty_c),'reduceOnly':'true'}
        else:
            usdt = t.get('usdt_qty', POSITION_SIZE)
            params = {'symbol':symbol,'side':'BUY','positionSide':'SHORT',
                      'type':'MARKET','quoteOrderQty':str(round(usdt,2)),'reduceOnly':'true'}
        return bingx_request('POST', '/openApi/swap/v2/trade/order', params).json().get('code') == 0

    def _tiene_posicion(self, symbol):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol':symbol}).json()
            if d.get('code') == 0:
                for p in (d.get('data') or []):
                    amt = float(p.get('positionAmt',0) or 0)
                    if abs(amt) > 0:
                        return True, 'LONG' if amt > 0 else 'SHORT'
        except: pass
        return False, None

    def _set_leverage(self, symbol):
        """FIX v2.7: Establece el leverage correcto en BingX antes de abrir."""
        try:
            d = bingx_request('POST', '/openApi/swap/v2/trade/leverage', {
                'symbol': symbol,
                'side': 'SHORT',
                'leverage': str(LEVERAGE),
            }).json()
            if d.get('code') == 0:
                log.info(f"  Leverage {LEVERAGE}x configurado en BingX para {symbol}")
            else:
                log.warning(f"  Leverage no configurado [{d.get('code')}]: {d.get('msg')}")
        except Exception as e:
            log.debug(f"  _set_leverage: {e}")

    def _esperar_posicion(self, symbol, timeout=45):
        """FIX v2.7: Detecta SHORT correctamente — amt<0 O positionSide=SHORT."""
        log.info(f"  Esperando posición SHORT {symbol} (max {timeout}s)...")
        for i in range(timeout):
            try:
                d = bingx_request('GET', '/openApi/swap/v2/user/positions',
                                  {'symbol': symbol}).json()
                if d.get('code') == 0:
                    data = d.get('data') or []
                    if i < 5: log.info(f"  [debug {i+1}s] positions: {str(data)[:150]}")
                    for p in data:
                        try: amt = float(p.get('positionAmt', 0) or 0)
                        except: amt = 0
                        pos_side = str(p.get('positionSide', '')).upper()
                        # SHORT: amt negativo O positionSide=SHORT con cualquier cantidad
                        is_short = (amt < 0) or (pos_side == 'SHORT' and abs(amt) > 0)
                        if is_short:
                            try:
                                entry_real = float(p.get('avgPrice') or
                                                   p.get('entryPrice') or
                                                   p.get('averagePrice') or 0)
                            except: entry_real = 0
                            qty_real = abs(amt)
                            log.info(f"  ✅ SHORT confirmado: qty={qty_real} entry=${entry_real:.6f} ({i+1}s)")
                            return qty_real, entry_real
            except Exception as e:
                log.debug(f"  _esperar: {e}")
            time.sleep(1)
        log.warning(f"  ⏱ Timeout {timeout}s — posición no detectada")
        return None, None

    def _cancelar_ordenes(self, symbol):
        try:
            d = bingx_request('GET', '/openApi/swap/v2/trade/openOrders',
                              {'symbol': symbol}).json()
            if d.get('code') == 0:
                for o in (d.get('data', {}).get('orders') or []):
                    oid = o.get('orderId','')
                    if oid:
                        bingx_request('DELETE', '/openApi/swap/v2/trade/order',
                                      {'symbol': symbol, 'orderId': str(oid)})
                        log.info(f"  Orden {oid} cancelada")
        except Exception as e:
            log.debug(f"  _cancelar: {e}")

    # ---------------------------------------------------------------- lifecycle

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  [SEÑAL] SHORT {symbol} score:{sig['score']:.0f}"); return False
        if symbol in self.open_trades: return False

        # FIX v2.7: verificar balance ANTES de abrir
        if not self._balance_suficiente():
            return False

        tiene, dir_bx = self._tiene_posicion(symbol)
        if tiene: log.info(f"  {symbol} ya tiene {dir_bx} — skip"); return False

        price    = sig['price']
        usdt_qty = round(max(POSITION_SIZE, MIN_TRADE), 2)
        tp_price = price * (1 - sig['tp_pct'] / 100)
        sl_price = price * (1 + sig['sl_pct'] / 100)

        # FIX v2.7: configurar leverage ANTES de abrir
        self._set_leverage(symbol)

        log.info(f"\n  ➤ SHORT {symbol}")
        log.info(f"  Score:{sig['score']:.0f}/{sig['score_min']:.0f} | RSI:{sig['rsi']:.0f} | Balance:${self._balance:.2f}")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entry:${price:.6f} | Capital:${usdt_qty} | TP:{sig['tp_pct']:.2f}% SL:{sig['sl_pct']:.1f}%")

        oid, qty_c = self._place_short_entry(symbol, usdt_qty, price)
        if not oid:
            log.error(f"  No se pudo abrir {symbol}"); return False

        qty_real, entry_real = self._esperar_posicion(symbol, timeout=30)

        if qty_real is None:
            log.warning(f"  LIMIT no ejecutada → cancelando + MARKET")
            self._cancelar_ordenes(symbol)
            time.sleep(0.5)
            d_mkt = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':symbol,'side':'SELL','positionSide':'SHORT',
                'type':'MARKET','quantity':str(qty_c),
            }).json()
            if d_mkt.get('code') == 0:
                qty_real, entry_real = self._esperar_posicion(symbol, timeout=15)
            if qty_real is None:
                self._tg(f"⚠️ SHORT {symbol} SIN TP/SL — no confirmado. Fijar manual.")
                self.open_trades[symbol] = {
                    'entry':price,'qty_c':qty_c,'usdt_qty':usdt_qty,
                    'tp':tp_price,'sl':sl_price,'tp_pct':sig['tp_pct'],'sl_pct':sig['sl_pct'],
                    'lowest':price,'order_id':oid,'tp_ok':False,'sl_ok':False,
                    'opened_at':datetime.now(),'score':sig['score'],
                }
                return True

        if entry_real and entry_real > 0:
            tp_price = entry_real * (1 - sig['tp_pct'] / 100)
            sl_price = entry_real * (1 + sig['sl_pct'] / 100)

        qty_final   = qty_real if qty_real else qty_c
        entry_final = entry_real if (entry_real and entry_real > 0) else price

        tp_ok = self._cond_order(symbol, qty_final, tp_price, 'TAKE_PROFIT_MARKET')
        time.sleep(0.3)
        sl_ok = self._cond_order(symbol, qty_final, sl_price, 'STOP_MARKET')

        for delay in [3, 5]:
            if tp_ok and sl_ok: break
            log.warning(f"  TP:{tp_ok} SL:{sl_ok} — reintentando en {delay}s")
            time.sleep(delay)
            if not tp_ok: tp_ok = self._cond_order(symbol, qty_final, tp_price, 'TAKE_PROFIT_MARKET')
            if not sl_ok: sl_ok = self._cond_order(symbol, qty_final, sl_price, 'STOP_MARKET')

        self.open_trades[symbol] = {
            'entry':entry_final,'qty_c':qty_final,'usdt_qty':usdt_qty,
            'tp':tp_price,'sl':sl_price,'tp_pct':sig['tp_pct'],'sl_pct':sig['sl_pct'],
            'lowest':entry_final,'order_id':oid,'tp_ok':tp_ok,'sl_ok':sl_ok,
            'opened_at':datetime.now(),'score':sig['score'],
        }
        self.stats['exec'] += 1

        stp = "✅" if tp_ok else "❌ FIJAR MANUAL"
        ssl = "✅" if sl_ok else "❌ FIJAR MANUAL"
        self._tg(
            f"<b>🔴 SHORT ABIERTO</b>\n<b>{symbol}</b> | Score:{sig['score']:.0f}\n"
            f"Entrada: ${entry_final:.6f}\n"
            f"{stp} TP: ${tp_price:.6f} (-{sig['tp_pct']:.2f}%)\n"
            f"{ssl} SL: ${sl_price:.6f} (+{sig['sl_pct']:.1f}%)\n"
            f"Capital: ${usdt_qty} x{LEVERAGE} | Balance: ${self._balance:.2f}\n"
            f"RSI:{sig['rsi']:.0f} | {sig['reasons']}"
        )
        return True

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades: return False
        t = self.open_trades[symbol]
        self._close_short(symbol, t)
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

    async def _sync_bingx(self):
        if not self.open_trades or not AUTO_TRADING: return
        try:
            d = bingx_request('GET', '/openApi/swap/v2/user/positions', {}).json()
            if d.get('code') != 0: return
            pos = {p.get('symbol'): float(p.get('positionAmt',0) or 0)
                   for p in (d.get('data') or [])
                   if abs(float(p.get('positionAmt',0) or 0)) > 0}
            for sym in list(self.open_trades.keys()):
                if sym not in pos:
                    t   = self.open_trades[sym]
                    tk  = self._ticker(sym)
                    cur = tk['price'] if tk else t['entry']
                    cambio  = (t['entry'] - cur) / t['entry']
                    pnl     = (t['usdt_qty'] * LEVERAGE * cambio) - (t['usdt_qty'] * LEVERAGE * COMISION_ACTUAL)
                    pnl_pct = (pnl / t['usdt_qty']) * 100
                    self.stats['closed'] += 1; self.stats['pnl'] += pnl
                    if pnl >= 0: self.stats['wins'] += 1
                    else:        self.stats['losses'] += 1
                    total = self.stats['wins'] + self.stats['losses']
                    wr = self.stats['wins'] / total * 100 if total else 0
                    emoji = "✅" if pnl >= 0 else "❌"
                    mins  = int((datetime.now() - t['opened_at']).total_seconds() / 60)
                    self._tg(f"<b>{emoji} SHORT cerrado BingX</b>\n<b>{sym}</b>\n"
                             f"PnL: ${pnl:+.3f} ({pnl_pct:+.1f}%) | {mins}min\n"
                             f"Total: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
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
                if TRAILING and cur < t['lowest']:
                    t['lowest'] = cur
                    if pnl_pct >= 0.6:
                        new_sl = t['entry'] - (t['entry'] - cur) * 0.60
                        if new_sl < t['sl']:
                            t['sl'] = new_sl
                            log.info(f"  Trailing SL {sym}: ${new_sl:.6f}")
                if abs(pnl_pct) > 0.3:
                    log.info(f"  {sym}: {pnl_pct:+.2f}% | ${cur:.6f}")
                if cur <= t['tp']:   self.close_trade(sym, cur, "TAKE PROFIT")
                elif cur >= t['sl']: self.close_trade(sym, cur, "STOP LOSS")
            except Exception as e:
                log.debug(f"Monitor {sym}: {e}")

    def _reporte_horario(self):
        if datetime.now() - self._last_report < timedelta(hours=1): return
        self._last_report = datetime.now()
        total = self.stats['wins'] + self.stats['losses']
        wr    = self.stats['wins'] / total * 100 if total else 0
        self._tg(
            f"<b>📊 Reporte horario SHORTS</b>\n"
            f"PnL: ${self.stats['pnl']:+.3f} | WR:{wr:.1f}%\n"
            f"({self.stats['wins']}W/{self.stats['losses']}L | {self.stats['closed']} trades)\n"
            f"Abiertos: {len(self.open_trades)}/{MAX_TRADES} | BTC 1h:{self._btc_change_1h:+.2f}%\n"
            f"Balance: ${self._balance:.2f} USDT"
        )

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id':TELEGRAM_CHAT,'text':msg,'parse_mode':'HTML'}, timeout=6)
        except: pass

    async def run(self):
        log.info("\n▶  Bot SHORTS v2.7.0 arrancado\n")
        iteration, last_refresh = 0, 0
        while True:
            try:
                iteration += 1
                if time.time() - last_refresh > 600:
                    self._get_symbols(); last_refresh = time.time()

                self._update_btc_trend()
                self._update_balance()

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                btc_st  = "⚠️ BLOQUEADO" if self._btc_change_1h >= BTC_BULL_BLOCK_PCT else "OK"
                hora_st = "🌙 BAJA" if not self._hora_ok() else "☀️"

                log.info(f"\n{'='*65}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Abiertos:{len(self.open_trades)}/{MAX_TRADES} | "
                         f"PnL:${self.stats['pnl']:+.3f} | WR:{wr:.1f}%")
                log.info(f"  Balance:${self._balance:.2f} | BTC:{self._btc_change_1h:+.2f}% {btc_st} | {hora_st}")
                log.info(f"{'='*65}\n")

                await self.monitor_trades()
                self._reporte_horario()

                pos_reales = self._posiciones_reales_bingx()
                if pos_reales >= MAX_TRADES:
                    log.info(f"  Max trades SHORT ({pos_reales}/{MAX_TRADES}) en BingX — esperando")
                elif len(self.open_trades) < MAX_TRADES:
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.open_trades) >= MAX_TRADES: break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            log.info(f"  ★ {sym} score:{sig['score']:.0f} RSI:{sig['rsi']:.0f}")
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.12)
                        if (i+1) % 25 == 0:
                            log.info(f"  ...{i+1}/{len(self.symbols)} analizados")
                    log.info(f"\n  {len(self.symbols)} pares | {found} señales")
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
