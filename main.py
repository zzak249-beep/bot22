#!/usr/bin/env python3
"""
BOT DE TRADING PROFESIONAL - SOLO SHORTS v1.0
Estrategia especializada para ventas en corto

OPTIMIZACIONES ESPECÍFICAS SHORT:
- Score mínimo 80 (muy selectivo)
- RSI > 75 como mínimo
- EMA bajista confirmada
- MACD divergencia bajista
- Rechazo Bollinger superior
- Volumen confirmación bajista
- Capital 7 USDT + PnL real con leverage
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math
from datetime import datetime
from urllib.parse import urlencode

# ============================================================================
# CONFIGURACION
# ============================================================================

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default)).strip().strip('"').strip("'").strip()
    if typ == 'int':   return int(v)
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

BINGX_API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

# SHORTS: más conservador que LONGS
AUTO_TRADING  = clean('AUTO_TRADING_ENABLED',  'true',  'bool')
POSITION_SIZE = clean('MAX_POSITION_SIZE',       '7',   'float')
MIN_TRADE     = clean('MIN_TRADE_USDT',          '5',   'float')
LEVERAGE      = clean('LEVERAGE',                '3',   'int')
TP_PCT        = clean('TAKE_PROFIT_PCT',         '4.0', 'float')  # Mayor TP para SHORT
SL_PCT        = clean('STOP_LOSS_PCT',           '1.5', 'float')  # Mayor SL (más riesgo)
MAX_TRADES    = clean('MAX_OPEN_TRADES',         '2',   'int')
INTERVAL      = clean('CHECK_INTERVAL',         '60',   'int')
MIN_VOLUME    = clean('MIN_VOLUME_24H',     '500000',   'float')
MAX_SYMBOLS   = clean('MAX_SYMBOLS_TO_ANALYZE', '80',   'int')
MIN_SCORE     = clean('MIN_SCORE',              '80',   'float')  # MUY selectivo
TRAILING      = clean('TRAILING_STOP_ENABLED', 'true',  'bool')

BASE_URL = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ============================================================================
# FIRMA BINGX
# ============================================================================

def bingx_request(method, endpoint, params):
    params['timestamp'] = int(time.time() * 1000)
    sp  = sorted(params.items())
    qs  = urlencode(sp)
    sig = hmac.new(BINGX_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
    hdr = {'X-BX-APIKEY': BINGX_API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}
    if method == 'GET':
        return requests.get(url, headers=hdr, timeout=10)
    return requests.post(url, headers=hdr, timeout=10)

# ============================================================================
# INDICADORES TECNICOS
# ============================================================================

def calc_ema(prices, period):
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    k = 2 / (period + 1)
    e = prices[0]
    for p in prices[1:]:
        e = p * k + e * (1 - k)
    return e

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains  = [max(0,  prices[i] - prices[i-1]) for i in range(1, len(prices))]
    losses = [max(0, prices[i-1] - prices[i])  for i in range(1, len(prices))]
    ag = sum(gains[-period:])  / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))

def calc_macd(prices):
    if len(prices) < 26:
        return 0, 0, 0
    fast = calc_ema(prices, 12)
    slow = calc_ema(prices, 26)
    ml   = fast - slow
    sig  = ml * 0.9
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
    if len(closes) < 2:
        return 0
    trs = []
    for i in range(1, min(len(closes), period+1)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1])
        ))
    return sum(trs) / len(trs) if trs else 0

def vol_spike(volumes):
    if len(volumes) < 5:
        return 1.0
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    return (volumes[-1] / avg) if avg > 0 else 1.0

# ============================================================================
# BOT SHORT
# ============================================================================

class ShortTradingBot:

    def __init__(self):
        log.info("=" * 70)
        log.info("BOT TRADING PROFESIONAL - SOLO SHORTS v1.0")
        log.info("Estrategia especializada para ventas en corto")
        log.info("=" * 70)
        log.info(f"AUTO-TRADING:   {'ON - SHORTS REALES' if AUTO_TRADING else 'OFF'}")
        log.info(f"Capital:        ${POSITION_SIZE} USDT (min ${MIN_TRADE})")
        log.info(f"Leverage:       {LEVERAGE}x => posicion ${POSITION_SIZE * LEVERAGE}")
        log.info(f"TP/SL:          {TP_PCT}% / {SL_PCT}%  (RR {TP_PCT/SL_PCT:.1f}:1)")
        log.info(f"Max trades:     {MAX_TRADES}")
        log.info(f"Score minimo:   {MIN_SCORE}/100 (MUY SELECTIVO)")
        log.info(f"Volumen min:    ${MIN_VOLUME/1e6:.1f}M")
        log.info(f"Trailing stop:  {'ON' if TRAILING else 'OFF'} (activa +0.5%)")
        log.info(f"Estrategia:     SOLO SHORTS - RSI>75, EMA bajista, MACD-")
        log.info("=" * 70)

        self.symbols         = []
        self.open_trades     = {}
        self._contracts      = {}
        self.stats = {'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()

        self._tg(
            f"<b>Bot SHORT v1.0 iniciado</b>\n"
            f"{'AUTO ON - SHORTS reales' if AUTO_TRADING else 'Solo señales'}\n"
            f"Capital: ${POSITION_SIZE} x{LEVERAGE} | TP:{TP_PCT}% SL:{SL_PCT}%\n"
            f"Score min:{MIN_SCORE} (MUY selectivo) | MaxTrades:{MAX_TRADES}\n"
            f"🔴 SOLO SHORTS - Estrategia optimizada bajista\n"
            f"Filtros: RSI>75, EMA-, MACD-, BB-high, Vol+"
        )

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING:
            log.info("Modo SEÑALES - trades desactivados")
            return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("Credenciales faltantes")
            AUTO_TRADING = False
            return
        log.info(f"API Key: {BINGX_API_KEY[:12]}...")
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/balance', {})
            d = r.json()
            if d.get('code') == 0:
                bal = d.get('data', {})
                eq  = bal.get('equity', bal.get('balance', '?'))
                log.info(f"BingX OK | Balance: ${eq} USDT")
            else:
                log.error(f"Error BingX [{d.get('code')}]: {d.get('msg')}")
                AUTO_TRADING = False
        except Exception as e:
            log.error(f"Error: {e}")
            AUTO_TRADING = False

    def _load_contracts(self):
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                for c in d.get('data', []):
                    self._contracts[c.get('symbol','')] = {
                        'step': float(c.get('tradeMinQuantity', 1)),
                        'prec': int(c.get('quantityPrecision', 2)),
                    }
                log.info(f"Contratos cargados: {len(self._contracts)}")
        except Exception as e:
            log.warning(f"Error contratos: {e}")

    def _get_symbols(self):
        excl = {
            'GOLD','SILVER','XAG','XAU','PAXG','XAUT','OIL','BRENT','WTI','CRUDE',
            'PLATINUM','PALLADIUM','COPPER','NICKEL',
            'TSLA','AAPL','MSFT','GOOGL','AMZN','META','NVDA','COIN','MSTR',
            'TESLA','APPLE','MICROSOFT','GOOGLE','AMAZON','FACEBOOK',
            'SP500','SPX','SPY','QQQ','NASDAQ','DOW','DOWJONES','DJI','RUSSELL',
            'DAX','FTSE','CAC','NIKKEI','HANG','HSI','BOVESPA','IBEX',
            'DOW30','DJIA','US30','NAS100','US500',
            'EUR','GBP','JPY','CHF','AUD','CAD','NZD','100','1000',
            'EURUSD','GBPUSD','USDJPY','AUDUSD','NZDUSD',
            'WHEAT','CORN','SUGAR','COFFEE','COTTON','LUMBER',
        }
        
        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                items = []
                excluded = []
                for t in d.get('data', []):
                    sym = t.get('symbol','')
                    if not sym.endswith('-USDT'):
                        continue
                    
                    base = sym.replace('-USDT','').upper()
                    
                    if any(k in base for k in excl):
                        excluded.append(base)
                        continue
                    
                    if any(word in base for word in ['DOW','JONES','NASDAQ','INDEX','SP500','STOCK']):
                        excluded.append(base)
                        continue
                    
                    try:
                        vol = float(t.get('volume',0)) * float(t.get('lastPrice',0))
                        if vol < MIN_VOLUME or float(t.get('lastPrice',0)) < 0.0001:
                            continue
                        items.append({'symbol':sym, 'vol':vol})
                    except:
                        continue
                
                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]
                
                if excluded:
                    log.info(f"Excluidos {len(excluded)} no-cripto: {', '.join(excluded[:10])}")
                log.info(f"{len(self.symbols)} criptomonedas para SHORT (vol>${MIN_VOLUME/1e6:.1f}M)")
                return
        except Exception as e:
            log.warning(f"Error simbolos: {e}")
        
        self.symbols = ['BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT',
                        'DOGE-USDT','ADA-USDT','AVAX-USDT','LINK-USDT','MATIC-USDT']

    def _klines(self, symbol, interval='5m', limit=50):
        try:
            r = requests.get(
                f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params={'symbol':symbol,'interval':interval,'limit':limit},
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
                )
        except:
            pass
        return None, None, None, None

    def _ticker(self, symbol):
        try:
            r = requests.get(
                f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                params={'symbol':symbol}, timeout=8
            )
            d = r.json()
            if d.get('code') == 0 and d.get('data'):
                t = d['data']
                return {
                    'price':  float(t.get('lastPrice',0)),
                    'change': float(t.get('priceChangePercent',0)),
                }
        except:
            pass
        return None

    # ---------------------------------------------------------------- SEÑAL SHORT

    def analyze(self, symbol):
        """
        Estrategia ESPECIALIZADA para SHORTS
        Requiere confirmaciones FUERTES de sobrecompra y reversión bajista
        """
        if symbol in self.open_trades:
            return None

        closes, highs, lows, volumes = self._klines(symbol, '5m', 50)
        if not closes or len(closes) < 20:
            return None

        ticker = self._ticker(symbol)
        if not ticker or ticker['price'] <= 0:
            return None

        price  = ticker['price']
        change = ticker['change']

        # Indicadores
        ema9  = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, min(50, len(closes)))
        rsi_v = calc_rsi(closes, 14)
        ml, sg, hist = calc_macd(closes)
        bb_u, bb_m, bb_l = calc_bollinger(closes, 20)
        atr_v = calc_atr(highs, lows, closes, 14)
        vspike = vol_spike(volumes)

        ema_bear = ema9 < ema21 < ema50
        ema_gap  = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
        short_trend = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0

        # SCORE SHORT - ESTRATEGIA OPTIMIZADA
        ss, sr = 0, []
        
        # 1. EMA BAJISTA (CRÍTICO - 35 pts)
        if ema_bear:
            if ema_gap > 1.5:
                p = 35 + min(20, ema_gap*12); ss += p; sr.append(f"EMA--({p:.0f})")
            else:
                p = 30 + min(12, ema_gap*10); ss += p; sr.append(f"EMA-({p:.0f})")
        else:
            # RECHAZAR si no hay EMA bajista
            sr.append("SinEMA-(-99)")
            return None
        
        # 2. RSI OVERBOUGHT EXTREMO (MUY CRÍTICO - 40 pts)
        if rsi_v > 80:
            ss += 40; sr.append("RSI>80(40)")
        elif rsi_v > 75:
            ss += 32; sr.append("RSI>75(32)")
        elif rsi_v > 70:
            ss += 22; sr.append("RSI>70(22)")
        elif rsi_v > 65:
            ss += 12; sr.append("RSI>65(12)")
        else:
            # PENALIZAR si RSI no está sobrecalentado
            ss -= 20; sr.append(f"RSI{rsi_v:.0f}(-20)")
        
        # 3. MACD BAJISTA (25 pts)
        if ml < sg and hist < 0:
            if abs(hist) > abs(ml) * 0.4:  # Histograma MUY negativo
                ss += 25; sr.append("MACD--(25)")
            else:
                ss += 18; sr.append("MACD-(18)")
        else:
            # PENALIZAR si MACD no confirma
            ss -= 15; sr.append("MACD+(-15)")
        
        # 4. BOLLINGER SUPERIOR (rechazo de banda - 25 pts)
        if price >= bb_u * 0.998:
            ss += 25; sr.append("BB-high(25)")
        elif price >= bb_m * 1.03:
            ss += 15; sr.append("BB-mid+(15)")
        elif price < bb_m:
            # PENALIZAR si precio bajo
            ss -= 12; sr.append("BB-low(-12)")
        
        # 5. VOLUMEN EN VENTA (20 pts)
        if vspike >= 2.5 and short_trend < -0.3:
            p = min(20, vspike*10); ss += p; sr.append(f"VolVenta{vspike:.1f}x({p:.0f})")
        elif vspike >= 1.8:
            p = min(15, vspike*8); ss += p; sr.append(f"Vol{vspike:.1f}x({p:.0f})")
        elif vspike < 1.3:
            # PENALIZAR bajo volumen
            ss -= 10; sr.append("VolBajo(-10)")
        
        # 6. TENDENCIA BAJISTA (18 pts)
        if short_trend < -1.5:
            ss += 18; sr.append("trend--(18)")
        elif short_trend < -0.5:
            ss += 12; sr.append("trend-(12)")
        elif short_trend > 0.5:
            # PENALIZAR tendencia alcista
            ss -= 15; sr.append("trend+(-15)")
        
        # 7. CAMBIO 24H POSITIVO (sobrecompra - 15 pts)
        if change > 5.0:
            p = min(15, change * 2.5); ss += p; sr.append(f"24h+{change:.1f}%({p:.0f})")
        elif change > 3.0:
            p = min(10, change * 2); ss += p; sr.append(f"24h+{change:.1f}%({p:.0f})")
        elif change < -2.0:
            # PENALIZAR si ya cayó mucho
            ss -= 12; sr.append(f"24h{change:.1f}%(-12)")
        
        # 8. CONFIRMACIÓN ADICIONAL: Precio cerca de máximos recientes
        recent_high = max(closes[-10:]) if len(closes) >= 10 else price
        if price >= recent_high * 0.98:
            ss += 15; sr.append("NearHigh(15)")
        
        # TP dinamico más agresivo para SHORT
        atr_pct = (atr_v / price * 100) if price > 0 else 0
        tp_dyn  = max(TP_PCT, min(TP_PCT * 3, atr_pct * 2.5))
        
        # DECISIÓN: Solo SHORT si score >= MIN_SCORE
        if ss >= MIN_SCORE:
            return {'signal':'SHORT', 'price':price,'change':change,'score':ss,
                    'reasons':' | '.join(sr),'rsi':rsi_v,'vol':vspike,'tp_pct':tp_dyn,'sl_pct':SL_PCT}
        
        return None

    def _qty(self, symbol, price):
        info  = self._contracts.get(symbol, {'step':1.0,'prec':2})
        step  = info['step']
        prec  = info['prec']
        capital = min(POSITION_SIZE, 7.0)
        if capital < MIN_TRADE:
            capital = MIN_TRADE
        log.info(f"  Calculando cantidad para ${capital:.2f} USDT a precio ${price:.6f}")
        raw   = capital / price
        stepped = math.ceil(raw / step) * step if step > 0 else raw
        qty   = round(stepped, prec)
        val   = qty * price
        i = 0
        while val < MIN_TRADE and step > 0 and i < 1000:
            qty += step; qty = round(qty, prec); val = qty * price; i += 1
        if val > 8:
            log.warning(f"  Capital ${val:.2f} > 8 USDT, ajustando...")
            qty = (7.0 / price)
            qty = round(math.floor(qty / step) * step, prec) if step > 0 else round(qty, prec)
            val = qty * price
        log.info(f"  Cantidad final: {qty} (${val:.2f} USDT)")
        return qty, round(val, 4)

    def _tiene_posicion_bingx(self, symbol):
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
            d = r.json()
            if d.get('code') == 0:
                for p in (d.get('data') or []):
                    amt = float(p.get('positionAmt', 0) or 0)
                    if abs(amt) > 0:
                        direccion = 'LONG' if amt > 0 else 'SHORT'
                        return True, direccion
        except Exception as e:
            log.debug(f"_tiene_posicion_bingx {symbol}: {e}")
        return False, None

    def open_trade(self, symbol, sig):
        if not AUTO_TRADING:
            log.info(f"  SEÑAL SHORT {symbol} score:{sig['score']:.0f} [AUTO-TRADING OFF]")
            return False

        price = sig['price']

        if symbol in self.open_trades:
            return False

        if AUTO_TRADING:
            tiene_pos, pos_dir = self._tiene_posicion_bingx(symbol)
            if tiene_pos:
                if pos_dir == 'SHORT':
                    log.info(f"  {symbol} ya tiene SHORT en BingX")
                    self.open_trades[symbol] = {
                        'direction': 'SHORT', 'entry': price, 'qty': 0, 'val': 0,
                        'tp': 0, 'sl': 0, 'tp_pct': sig['tp_pct'], 'sl_pct': sig['sl_pct'],
                        'highest': price, 'lowest': price, 'order_id': 'EXISTENTE',
                        'tp_ok': False, 'sl_ok': False, 'opened_at': datetime.now(),
                        'score': sig['score'],
                    }
                    return False
                else:
                    log.warning(f"  ⚠️ {symbol} ya tiene LONG, no puede SHORT")
                    return False

        qty, val = self._qty(symbol, price)
        
        if val > 10:
            log.error(f"  {symbol} RECHAZADO: capital ${val:.2f} > 10 USDT")
            return False
        if val < MIN_TRADE:
            log.warning(f"  {symbol} rechazado ${val:.2f} < ${MIN_TRADE}")
            return False
        
        log.info(f"  ✓ Capital validado: ${val:.2f} USDT")

        tp_pct = sig['tp_pct']
        sl_pct = sig['sl_pct']
        tp = price * (1 - tp_pct/100)
        sl = price * (1 + sl_pct/100)

        log.info(f"\n  Abriendo SHORT {symbol}")
        log.info(f"  Score:{sig['score']:.0f}/100 RSI:{sig['rsi']:.1f} Vol:{sig['vol']:.1f}x")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entry:${price:.6f} Qty:{qty} (${val:.2f}) TP:{tp_pct:.1f}% SL:{sl_pct:.1f}%")

        r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol,
            'side':   'SELL',
            'positionSide': 'SHORT',
            'type':   'MARKET',
            'quantity': str(qty),
        })
        d = r.json()
        if d.get('code') != 0:
            log.error(f"  Error [{d.get('code')}]: {d.get('msg')}")
            return False

        oid = d.get('data',{}).get('orderId','N/A')
        log.info(f"  SHORT ABIERTO ID:{oid}")

        time.sleep(0.4)
        tp_ok = self._cond_order(symbol, qty, tp, 'TAKE_PROFIT_MARKET')
        time.sleep(0.4)
        sl_ok = self._cond_order(symbol, qty, sl, 'STOP_MARKET')

        self.open_trades[symbol] = {
            'direction': 'SHORT', 'entry': price, 'qty': qty, 'val': val,
            'tp': tp, 'sl': sl, 'tp_pct': tp_pct, 'sl_pct': sl_pct,
            'highest': price, 'lowest': price, 'order_id': oid,
            'tp_ok': tp_ok, 'sl_ok': sl_ok, 'opened_at': datetime.now(),
            'score': sig['score'],
        }
        self.stats['exec'] += 1

        self._tg(
            f"<b>SHORT ABIERTO</b>\n"
            f"{symbol} | Score:{sig['score']:.0f}/100\n"
            f"Entry: ${price:.4f}\n"
            f"{'OK' if tp_ok else 'ERR'} TP: ${tp:.4f} (-{tp_pct:.1f}%)\n"
            f"{'OK' if sl_ok else 'ERR'} SL: ${sl:.4f} (+{sl_pct:.1f}%)\n"
            f"Capital: ${val:.2f} x{LEVERAGE} = ${val*LEVERAGE:.2f}\n"
            f"Cantidad: {qty}\n"
            f"{sig['reasons']}"
        )
        return True

    def _cond_order(self, symbol, qty, stop_price, otype):
        try:
            r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol,
                'side':   'BUY',
                'positionSide': 'SHORT',
                'type':   otype,
                'quantity': str(qty),
                'stopPrice': str(round(stop_price, 6)),
            })
            d = r.json()
            ok = d.get('code') == 0
            lbl = "TP" if "TAKE" in otype else "SL"
            if ok:
                log.info(f"  {lbl} OK @ ${stop_price:.6f}")
            else:
                log.warning(f"  {lbl} ERR [{d.get('code')}]: {d.get('msg')}")
            return ok
        except Exception as e:
            log.warning(f"  {otype} exc: {e}")
            return False

    def close_trade(self, symbol, cur_price, reason):
        if symbol not in self.open_trades:
            return False
        t = self.open_trades[symbol]
        try:
            r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol,
                'side':   'BUY',
                'positionSide': 'SHORT',
                'type':   'MARKET',
                'quantity': str(t['qty']),
            })
            d = r.json()
            if d.get('code') == 0:
                COMISION_TOTAL = 0.001
                cambio_pct = (t['entry'] - cur_price) / t['entry']
                pnl = (t['val'] * LEVERAGE * cambio_pct) - (t['val'] * LEVERAGE * COMISION_TOTAL)
                pnl_pct = (pnl / t['val']) * 100
                
                self.stats['closed'] += 1
                self.stats['pnl']    += pnl
                if pnl > 0: self.stats['wins']   += 1
                else:        self.stats['losses'] += 1
                
                total = self.stats['wins'] + self.stats['losses']
                wr = self.stats['wins'] / total * 100 if total else 0
                mins = int((datetime.now() - t['opened_at']).total_seconds() / 60)
                
                log.info(f"  CERRADO({reason}) {symbol} PnL:${pnl:+.2f}({pnl_pct:+.1f}%) {mins}min")
                self._tg(
                    f"<b>SHORT CERRADO - {reason}</b>\n"
                    f"{symbol}\n"
                    f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                    f"Entry: ${t['entry']:.4f} → Exit: ${cur_price:.4f}\n"
                    f"Capital: ${t['val']:.2f} x{LEVERAGE}\n"
                    f"Duracion: {mins} min\n"
                    f"Total PnL: ${self.stats['pnl']:+.2f}\n"
                    f"WR: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)"
                )
                del self.open_trades[symbol]
                return True
        except Exception as e:
            log.error(f"  Error cerrando {symbol}: {e}")
        return False

    async def _sync_con_bingx(self):
        if not self.open_trades or not AUTO_TRADING:
            return
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/positions', {})
            d = r.json()
            if d.get('code') != 0:
                return
            
            posiciones_bingx = {}
            for p in (d.get('data') or []):
                sym = p.get('symbol','')
                amt = float(p.get('positionAmt', 0) or 0)
                if abs(amt) > 0:
                    posiciones_bingx[sym] = amt
            
            for symbol in list(self.open_trades.keys()):
                if symbol not in posiciones_bingx:
                    t = self.open_trades[symbol]
                    if t.get('order_id') not in ('EXTERNO', 'EXISTENTE', ''):
                        tk = self._ticker(symbol)
                        cur = tk['price'] if tk else t['entry']
                        
                        COMISION_TOTAL = 0.001
                        cambio_pct = (t['entry'] - cur) / t['entry']
                        pnl = (t['val'] * LEVERAGE * cambio_pct) - (t['val'] * LEVERAGE * COMISION_TOTAL)
                        pnl_pct = (pnl / t['val']) * 100
                        
                        self.stats['closed'] += 1
                        self.stats['pnl']    += pnl
                        if pnl >= 0: self.stats['wins']   += 1
                        else:        self.stats['losses'] += 1
                        
                        total = self.stats['wins'] + self.stats['losses']
                        wr = self.stats['wins'] / total * 100 if total else 0
                        mins = int((datetime.now() - t['opened_at']).total_seconds() / 60)
                        
                        log.info(f"  SYNC: {symbol} cerrado por BingX PnL=${pnl:+.2f}")
                        self._tg(
                            f"<b>SHORT CERRADO por BingX</b>\n{symbol}\n"
                            f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                            f"Entry: ${t['entry']:.4f} → Exit: ${cur:.4f}\n"
                            f"Duracion: {mins} min\n"
                            f"Total: ${self.stats['pnl']:+.2f} | WR:{wr:.1f}%"
                        )
                    del self.open_trades[symbol]
        except Exception as e:
            log.debug(f"sync: {e}")

    async def monitor_trades(self):
        await self._sync_con_bingx()
        
        for symbol in list(self.open_trades.keys()):
            try:
                t  = self.open_trades[symbol]
                tk = self._ticker(symbol)
                if not tk: continue
                cur = tk['price']

                pnl_pct = (t['entry'] - cur) / t['entry'] * 100
                hit_tp  = cur <= t['tp']
                hit_sl  = cur >= t['sl']
                
                if TRAILING and cur < t['lowest']:
                    t['lowest'] = cur
                    if pnl_pct >= 0.5:
                        profit = t['entry'] - cur
                        new_sl = t['entry'] - (profit * 0.65)
                        if new_sl < t['sl']:
                            t['sl'] = new_sl
                            log.info(f"  Trailing SL SHORT {symbol} -> ${new_sl:.4f}")

                if abs(pnl_pct) > 0.4:
                    log.info(f"  {symbol} SHORT PnL:{pnl_pct:+.1f}% ${cur:.4f} SL:${t['sl']:.4f}")

                if hit_tp:   self.close_trade(symbol, cur, "TAKE PROFIT")
                elif hit_sl: self.close_trade(symbol, cur, "STOP LOSS")

            except Exception as e:
                log.debug(f"Monitor {symbol}: {e}")

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=5
                )
        except: pass

    async def run(self):
        log.info("\nBot SHORT arrancado\n")
        iteration = 0
        last_refresh = 0

        while True:
            try:
                iteration += 1
                now = time.time()

                if now - last_refresh > 600:
                    log.info("Actualizando monedas...")
                    self._get_symbols()
                    last_refresh = now

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0

                log.info(f"\n{'='*70}")
                log.info(
                    f"#{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                    f"SHORTS:{len(self.open_trades)}/{MAX_TRADES} | "
                    f"PnL:${self.stats['pnl']:+.2f} | "
                    f"WR:{wr:.1f}%({self.stats['wins']}W/{self.stats['losses']}L)"
                )
                log.info(f"{'='*70}\n")

                await self.monitor_trades()

                if len(self.open_trades) < MAX_TRADES:
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.open_trades) >= MAX_TRADES:
                            break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            log.info(
                                f"  SEÑAL SHORT {sym} "
                                f"score:{sig['score']:.0f} RSI:{sig['rsi']:.0f} Vol:{sig['vol']:.1f}x"
                            )
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.15)
                        if (i+1) % 20 == 0:
                            log.info(f"  {i+1}/{len(self.symbols)} analizadas")

                    log.info(f"\n  {len(self.symbols)} monedas | {found} señales SHORT")
                else:
                    log.info(f"  Max SHORTS ({MAX_TRADES}) - esperando")

                log.info(f"\n  Proxima en {INTERVAL}s\n")
                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Detenido"); break
            except Exception as e:
                log.error(f"Error loop: {e}")
                await asyncio.sleep(15)

async def main():
    try:
        await ShortTradingBot().run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Terminado")
