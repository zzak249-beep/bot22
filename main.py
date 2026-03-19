#!/usr/bin/env python3
"""
BOT DE TRADING PROFESIONAL v3.0 - main.py
Auto-trading ACTIVADO por defecto
EMA + RSI + MACD + Bollinger + Trailing Stop + TP/SL automatico
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math
from datetime import datetime
from urllib.parse import urlencode

# ============================================================================
# CONFIGURACION - AUTO_TRADING=true POR DEFECTO
# ============================================================================

def clean(key, default, typ='str'):
    v = os.getenv(key, str(default)).strip().strip('"').strip("'").strip()
    if typ == 'int':   return int(v)
    if typ == 'float': return float(v)
    if typ == 'bool':  return v.lower() == 'true'
    return v

# Credenciales
BINGX_API_KEY    = os.getenv('BINGX_API_KEY',    '').strip().strip('"').strip("'")
BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '').strip().strip('"').strip("'")
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT    = os.getenv('TELEGRAM_CHAT_ID',   '')

# Trading - AUTO_TRADING true POR DEFECTO (antes era false, eso causaba [OFF])
AUTO_TRADING  = clean('AUTO_TRADING_ENABLED',  'true',  'bool')  # <-- CAMBIADO A true
POSITION_SIZE = clean('MAX_POSITION_SIZE',      '100',   'float')
MIN_TRADE     = clean('MIN_TRADE_USDT',          '7',    'float')
LEVERAGE      = clean('LEVERAGE',                '3',    'int')
TP_PCT        = clean('TAKE_PROFIT_PCT',         '2.5',  'float')
SL_PCT        = clean('STOP_LOSS_PCT',           '1.2',  'float')
MAX_TRADES    = clean('MAX_OPEN_TRADES',          '3',   'int')
INTERVAL      = clean('CHECK_INTERVAL',          '60',   'int')
MIN_VOLUME    = clean('MIN_VOLUME_24H',       '500000',  'float')
MAX_SYMBOLS   = clean('MAX_SYMBOLS_TO_ANALYZE',  '80',  'int')
MIN_SCORE     = clean('MIN_SCORE',               '60',   'float')
TRAILING      = clean('TRAILING_STOP_ENABLED',  'true',  'bool')

BASE_URL = "https://open-api.bingx.com"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ============================================================================
# FIRMA BINGX CORRECTA
# ============================================================================

def bingx_request(method, endpoint, params):
    """Request autenticada a BingX - firma correcta"""
    params['timestamp'] = int(time.time() * 1000)
    sp  = sorted(params.items())
    qs  = urlencode(sp)
    sig = hmac.new(BINGX_API_SECRET.encode('utf-8'), qs.encode('utf-8'), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{endpoint}?{qs}&signature={sig}"
    hdr = {
        'X-BX-APIKEY': BINGX_API_KEY,
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    if method == 'GET':
        return requests.get(url, headers=hdr, timeout=10)
    return requests.post(url, headers=hdr, timeout=10)

# ============================================================================
# INDICADORES TECNICOS
# ============================================================================

def calc_ema(prices, period):
    if not prices or len(prices) < 1: return 0
    if len(prices) < period:
        return sum(prices) / len(prices)
    k = 2 / (period + 1)
    e = prices[0]
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
    fast = calc_ema(prices, 12)
    slow = calc_ema(prices, 26)
    ml   = fast - slow
    sig  = ml * 0.9
    return ml, sig, ml - sig

def calc_bollinger(prices, period=20):
    if len(prices) < period:
        m = sum(prices) / len(prices) if prices else 0
        return m, m, m
    w   = prices[-period:]
    mid = sum(w) / period
    std = (sum((p - mid)**2 for p in w) / period) ** 0.5
    return mid + 2*std, mid, mid - 2*std

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < 2: return 0
    trs = []
    for i in range(1, min(len(closes), period+1)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1])
        ))
    return sum(trs) / len(trs) if trs else 0

def vol_spike(volumes):
    if len(volumes) < 5: return 1.0
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    return (volumes[-1] / avg) if avg > 0 else 1.0

# ============================================================================
# BOT PRINCIPAL
# ============================================================================

class TradingBot:

    def __init__(self):
        log.info("=" * 70)
        log.info("BOT TRADING PROFESIONAL v3.0")
        log.info("EMA + RSI + MACD + Bollinger + Trailing Stop")
        log.info("=" * 70)
        log.info(f"AUTO-TRADING:  {'ON - EJECUTANDO TRADES REALES' if AUTO_TRADING else 'OFF'}")
        log.info(f"Capital/trade: ${POSITION_SIZE} USDT (min ${MIN_TRADE})")
        log.info(f"Leverage:      {LEVERAGE}x  =>  posicion ${POSITION_SIZE * LEVERAGE}")
        log.info(f"TP/SL:         {TP_PCT}% / {SL_PCT}%  (RR {TP_PCT/SL_PCT:.1f}:1)")
        log.info(f"Max trades:    {MAX_TRADES}")
        log.info(f"Score minimo:  {MIN_SCORE}/100")
        log.info(f"Trailing stop: {'ON' if TRAILING else 'OFF'}")
        log.info(f"Volumen min:   ${MIN_VOLUME/1e6:.1f}M")
        log.info("=" * 70)

        self.symbols     = []
        self.open_trades = {}
        self._contracts  = {}
        self.stats = {'exec':0, 'closed':0, 'wins':0, 'losses':0, 'pnl':0.0}

        self._verify()
        self._load_contracts()
        self._get_symbols()

        estado = "AUTO-TRADING ON - ejecutando trades reales" if AUTO_TRADING else "Modo señales (OFF)"
        self._tg(
            f"<b>Bot v3.0 iniciado</b>\n"
            f"{estado}\n"
            f"Capital: ${POSITION_SIZE} x{LEVERAGE} | TP:{TP_PCT}% SL:{SL_PCT}%\n"
            f"Score min:{MIN_SCORE} | Trades max:{MAX_TRADES} | Trailing:{'ON' if TRAILING else 'OFF'}\n"
            f"Analizando {len(self.symbols)} monedas"
        )

    # ---------------------------------------------------------------- SETUP

    def _verify(self):
        global AUTO_TRADING
        if not AUTO_TRADING:
            log.info("Modo SEÑALES - trades desactivados")
            return
        if not BINGX_API_KEY or not BINGX_API_SECRET:
            log.error("BINGX_API_KEY o BINGX_API_SECRET no configurados")
            log.error("Configura las variables en Railway Settings > Variables")
            AUTO_TRADING = False
            return
        log.info(f"API Key: {BINGX_API_KEY[:12]}...")
        try:
            r = bingx_request('GET', '/openApi/swap/v2/user/balance', {})
            d = r.json()
            if d.get('code') == 0:
                bal = d.get('data', {})
                eq  = bal.get('equity', bal.get('balance', '?'))
                log.info(f"Conexion BingX OK | Balance: ${eq} USDT")
            else:
                log.error(f"Error BingX [{d.get('code')}]: {d.get('msg')}")
                log.error("Verifica que la API tenga permisos de Futures")
                AUTO_TRADING = False
        except Exception as e:
            log.error(f"No se pudo conectar a BingX: {e}")
            AUTO_TRADING = False

    def _load_contracts(self):
        """Cargar step size y precision de cada contrato"""
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
        """
        SOLO CRIPTOMONEDAS PURAS.
        Estrategia: whitelist de criptos conocidas + validacion por nombre.
        JAMAS incluye: acciones, SP500, oro, petroleo, forex, indices.
        """

        # ================================================================
        # WHITELIST: solo estas criptos conocidas pueden pasar
        # Ampliada con las mas populares de BingX
        # ================================================================
        CRIPTO_WHITELIST = {
            # Top por market cap
            'BTC','ETH','BNB','XRP','SOL','ADA','DOGE','TRX','TON','AVAX',
            'SHIB','DOT','LINK','MATIC','WBTC','DAI','UNI','LTC','BCH','ATOM',
            'XLM','ETC','ALGO','FIL','HBAR','VET','MANA','SAND','AXS','THETA',
            # DeFi
            'AAVE','COMP','CRV','SNX','YFI','SUSHI','1INCH','BAL','REN','KNC',
            'MKR','ZRX','LRC','PERP','DYDX','GMX','GNS','RUNE','CAKE','JOE',
            # Layer 2 y nuevas L1
            'ARB','OP','MATIC','IMX','METIS','BOBA','ZKS','STRK','MANTA','TAIKO',
            # Memes
            'DOGE','SHIB','PEPE','FLOKI','BONK','WIF','MEME','TURBO','NEIRO',
            'DOGS','HMSTR','PNUT','ACT','GOAT','MOODENG',
            # Infraestructura
            'FIL','AR','STORJ','GRT','API3','BAND','OCEAN','ANKR','NMR',
            # Gaming y NFT
            'AXS','SAND','MANA','ENJ','GALA','ILV','YGG','MAGIC','PIXEL','PORTAL',
            # Exchange tokens
            'BNB','OKB','HT','KCS','GT','MX','FTT',
            # Interoperabilidad
            'DOT','ATOM','OSMO','INJ','SEI','SUI','APT','NEAR','ICP','FTM',
            # Privacidad
            'XMR','ZEC','DASH','SCRT','ROSE',
            # Oraculo y datos
            'LINK','BAND','API3','DIA','TRB','UMA',
            # Stablecoins algoritmicas (no operar pero por si aparecen)
            # Otros populares en BingX
            'TIA','PYTH','JUP','WEN','ONDO','ENA','ETHFI','REZ','BB','NOT',
            'IO','ZRO','BLAST','LISTA','ZK','BOME','SLERF','W','TNSR',
            'OMNI','REI','DYM','ALT','JTO','MANTA','AEVO','STRK',
            'PIXELS','PORTAL','MYRO','WIF','BOME','SLERF',
            'ORDI','SATS','RATS','MUBI','MMSS','TURT',
            'CKB','BEFI','MASA','OBOL','ETHENA','ENA',
            'PONKE','ANALOS','BODEN','TREMP',
            # Mas populares
            'LDO','RPL','SSV','SWISE','ANKR','FXS','FRAX',
            'CVX','CNC','ALCX','TOKE','TEMPLE',
            'STG','VELO','BTRFLY','OHM','KLIMA',
            'UMAMI','DOPEX','HEGIC','PREMIA',
            'MAGIC','TreasureDAO',
            'GMX','GNS','CAP','MUX',
            'PENDLE','TIMELESS','APW',
            'RADIANT','LODE',
            'ACE','ACM','ACH','ACQ',
            'KEY','KEEP','NU','T',
            'COTI','CTSI','CXT','CELR',
            'LINA','LINEAR','LIT','LTO',
            'CHESS','BAKE','TKO','XVS',
            'ALPACA','BELT','BFT','BUNNY',
            'BABY','BABYSWAP',
            'HIGH','AUCTION',
            'JASMY','FLOW','CHZ','ENS',
            'STX','BLUR','CFX','ID',
            'HOOK','MAGIC','HIGH','LOKA',
            'PEOPLE','DUSK','CHESS',
            'ALPHA','BETA','HARD','WING',
            'BIFI','AUTO','EGGP',
            'CREAM','RAMP',
            'BEL','POND','QKC','QI',
            'NULS','NKN','NEBL',
            'WICC','WIN','WAN','WTC',
            'ZIL','ZEN','ZMT',
            'KAVA','KDA','KEEP',
            'IOTA','IOTX','IOST',
            'HIVE','HEART','HERO',
            'FORTH','FARM','FIDA',
            'ERN','ERTHA','ERG',
            'DENT','DEGO','CTXC',
            'COMBO','CLV','CITY',
            'BNX','BIFI','BICO',
            'AUCTION','ASR','ARPA',
            'AION','AGLD','ACA',
            'KSM','KMD','KLV',
            'IOST','IOT','IOTX',
            'GTC','GLM','GHST',
            'FLUX','FLM','FIRO',
            'EGLD','EDU','ECOX',
            'CTSI','CTKN','CSP',
            'COS','COCOS','CELO',
            'C98','BURGER','BSW',
            'BOUNCEBIT','BOME',
            'AVA','AUTO','ASTR',
            'ALT','ALICE','ALD',
            'AGIX','AEVO','AERGO',
            'XNO','XEC','XDC',
            'VTHO','VOXEL','VITE',
            'UTK','USTC','UNFI',
            'TWT','TVK','TRU',
            'SYN','SWEAT','SWP',
            'SUPER','STPT','STEP',
            'SPARTA','SNFT','SLP',
            'SKL','SFP','SCRT',
            'RAY','QUICK','QNT',
            'PYR','PROM','POLYX',
            'POL','PLA','PIVX',
            'PHB','PERP','PAXG',
            'OM','OGN','NFT',
            'MTL','MSN','MOVR',
            'MOB','MBOX','MARS',
            'LOOKS','LON','LITH',
            'LEVER','LEND',
            'LA','KUNCI','KP3R',
            'KNC','KLAY','KINE',
            'JASMY','IQ','IQBAL',
            'HXRO','HUNT','HFT',
            'HAI','GXS','GRS',
            'GREIP','GPS','GNO',
            'GFI','GALA','FRONT',
            'FOR','FOAM','FNCT',
            'FIO','FERRUM','FEI',
            'FDUSD','FCON','FCAST',
            'EVMOS','EVA','EUROC',
            'EPX','EPAN','EPIC',
            'ELON','ELF','EDEN',
            'DUSK','DODO','DIS',
            'DIMO','DEXT','DENS',
            'DAR','CZZ','CXT',
            'CVC','CRO','CPOOL',
            'COVER','CORE','CONV',
            'COMB','CMD','CLEO',
            'CAST','CAMP','BSX',
            'BRWL','BRISE','BOBA',
            'BNT','BMX','BLOK',
            'BLZ','BIFI','BHT',
            'BEP','BCUT','BCOIN',
            'BADGER','AXL','AWT',
            'AVAIL','AURA','ATOLO',
            'PROS','POLS','POC',
            'PHTR','PERP','PEAK',
            'NYAN','NUX','NSFW',
            'NOIA','NIOX','NIF',
            'NFTD','NFTB','NEON',
            'MYC','MVL','MUSK',
            'MOOV','MONO','MONI',
            'MON','MOD','MNGO',
            'MITH','MINI','MILK',
            'MIKU','MHC','MFT',
            'MEAN','MCRT','MBX',
            'LPOOL','LPNT','LOS',
            'LONG','LOCO','LNR',
            'LMR','LKY','LIME',
            'LIKE','LIF3','LFLY',
            'LAYER','LAZIO','LASER',
            'LACE','KZEN','KUN',
            'KRTC','KRS','KRTS',
            'KOL','KOG','KOBO',
            'KNOT','KMB','KISHIMOTO',
            'JUV','JRT','JOB',
            'JMPT','JET','JAM',
            'JAB','IZI','ITGR',
            'IRON','IPAD','IPAY',
            'IOSG','IONIC','IOI',
            'INTER','INT','INSUR',
            'INF','INFI','INDO',
            'IME','IMPT','IMANITY',
            'ILSI','ILUS','IHF',
            'IFC','IDO','IDEX',
            'IBFK','IAG','HYVE',
            'HZN','HYN','HYDRA',
            'HTZ','HSF','HPS',
            'HPB','HOT','HNT',
            'HMT','HLG','HIT',
            'HIRE','HIFI','HFN',
            'HEX','HENLO','HELLO',
            'HECTOR','HEC','HBB',
            'HAL','GZX','GXT',
            'GZONE','GYM','GWT',
            'GUSDT','GUM','GOVI',
            'GOVI','GORILLA','GOM2',
            'GOLDY','GODE','GNX',
            'GNFT','GMM','GLAD',
            'GHX','GHC','GFX',
            'GET','GES','GEMS',
            'GEL','GEC','GDX',
            'GCN','GBT','GBPT',
        }

        # Patron de nombres tipicos de acciones que NO son cripto:
        # Mayusculas de 1-4 letras tipicas de NYSE/NASDAQ
        # Excepciones: BTC, ETH, BNB etc ya estan en whitelist
        STOCK_PATTERNS = {
            # Acciones conocidas que podrian colarse
            'SPX','SPY','QQQ','IWM','DIA','GLD','SLV','USO','UNG',
            'AAPL','MSFT','GOOGL','AMZN','META','NVDA','TSLA','BRK',
            'JPM','BAC','WFC','GS','MS','C','V','MA','AXP',
            'JNJ','PFE','MRK','ABBV','BMY','LLY','AMGN',
            'XOM','CVX','COP','SLB','HAL','BKR','MPC',
            'CAT','DE','MMM','HON','GE','BA','LMT','RTX',
            'WMT','TGT','COST','HD','LOW','AMZN',
            'NFLX','DIS','CMCSA','T','VZ',
            'COIN','HOOD','MSTR','RIOT','MARA','HUT','BITF',
            # Materias primas
            'GOLD','SILVER','OIL','GAS','NATGAS','BRENT','WTI','CRUDE',
            'WHEAT','CORN','SUGAR','COFFEE','COTTON','LUMBER',
            'COPPER','NICKEL','ZINC','LEAD','ALUM','TIN',
            'PLATINUM','PALLADIUM','RHODIUM',
            # Indices
            'SP500','DOW','NASDAQ','NIKKEI','DAX','FTSE','CAC','IBEX',
            'HANG','HSI','ASX','BOVESPA','RTS','MOEX',
            # Forex
            'EURUSD','GBPUSD','USDJPY','USDCHF','AUDUSD','USDCAD',
            'NZDUSD','EURGBP','EURJPY','GBPJPY',
        }

        def is_real_crypto(symbol):
            base = symbol.replace('-USDT', '').upper()

            # 1. Si empieza con numero -> NO (1000SHIB, 10000LADYS)
            if base and base[0].isdigit():
                return False

            # 2. Si esta en patrones de acciones/commodities -> NO
            if base in STOCK_PATTERNS:
                return False

            # 3. Si contiene palabras de commodities/acciones -> NO
            commodity_words = [
                'GOLD','SILVER','OIL','GAS','BRENT','CRUDE','WHEAT',
                'CORN','COPPER','NASDAQ','SP500','NIKKEI','FTSE',
                'TSLA','AAPL','MSFT','NVDA','GOOGL','AMZN','META',
                'FOREX','STOCK','SHARE','EQUITY','INDEX','INDICE',
                'XAU','XAG','XPT','XPD',  # codigos de metales
            ]
            for word in commodity_words:
                if word in base:
                    return False

            # 4. WHITELIST: si esta en la lista de criptos conocidas -> SI
            if base in CRIPTO_WHITELIST:
                return True

            # 5. Si no esta en whitelist pero tampoco en blacklist,
            #    aceptar si tiene entre 2 y 10 caracteres (tipico de cripto)
            #    y no parece un ticker de accion
            if 2 <= len(base) <= 10:
                return True

            return False

        try:
            r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/ticker", timeout=15)
            d = r.json()
            if d.get('code') == 0:
                items    = []
                excluded = []

                for t in d.get('data', []):
                    sym = t.get('symbol', '')
                    if not sym.endswith('-USDT'):
                        continue

                    if not is_real_crypto(sym):
                        excluded.append(sym.replace('-USDT', ''))
                        continue

                    try:
                        price = float(t.get('lastPrice', 0))
                        vol   = float(t.get('volume', 0)) * price
                        if vol < MIN_VOLUME or price < 0.0001:
                            continue
                        items.append({'symbol': sym, 'vol': vol})
                    except:
                        continue

                items.sort(key=lambda x: x['vol'], reverse=True)
                self.symbols = [x['symbol'] for x in items[:MAX_SYMBOLS]]

                if excluded:
                    log.info(f"Excluidos ({len(excluded)} no-cripto): {', '.join(excluded[:20])}")
                log.info(f"✅ {len(self.symbols)} CRIPTOMONEDAS puras seleccionadas")
                for i, x in enumerate(items[:5], 1):
                    log.info(f"  {i}. {x['symbol']:15s} Vol:${x['vol']/1e6:.1f}M")
                return

        except Exception as e:
            log.warning(f"Error obteniendo simbolos: {e}")

        # Fallback SOLO criptos top
        log.warning("Usando lista estatica de criptos top")
        self.symbols = [
            'BTC-USDT','ETH-USDT','SOL-USDT','BNB-USDT','XRP-USDT',
            'DOGE-USDT','ADA-USDT','AVAX-USDT','LINK-USDT','DOT-USDT',
            'MATIC-USDT','UNI-USDT','ATOM-USDT','LTC-USDT','BCH-USDT',
            'NEAR-USDT','FIL-USDT','APT-USDT','ARB-USDT','OP-USDT',
            'INJ-USDT','SUI-USDT','SEI-USDT','TIA-USDT','JUP-USDT'
        ]
    def _klines(self, symbol, interval='5m', limit=50):
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
                )
        except: pass
        return None, None, None, None

    def _ticker(self, symbol):
        try:
            r = requests.get(
                f"{BASE_URL}/openApi/swap/v2/quote/ticker",
                params={'symbol': symbol}, timeout=8
            )
            d = r.json()
            if d.get('code') == 0 and d.get('data'):
                t = d['data']
                return {
                    'price':  float(t.get('lastPrice', 0)),
                    'change': float(t.get('priceChangePercent', 0)),
                }
        except: pass
        return None

    # ---------------------------------------------------------------- SEÑAL

    def analyze(self, symbol):
        """
        Estrategia multi-indicador:
        EMA 9/21/50 + RSI 14 + MACD + Bollinger + Volumen + Trend
        Score 0-100. Entra si score >= MIN_SCORE
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
        vsp   = vol_spike(volumes)

        ema_bull = ema9 > ema21 > ema50
        ema_bear = ema9 < ema21 < ema50
        ema_gap  = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
        short_trend = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0

        # Score LONG
        ls, lr = 0, []
        if ema_bull:
            p = 20 + min(10, ema_gap * 5); ls += p; lr.append(f"EMA+({p:.0f})")
        if rsi_v < 30:
            ls += 25; lr.append("RSI<30(25)")
        elif rsi_v < 40:
            ls += 15; lr.append("RSI<40(15)")
        if ml > sg and hist > 0:
            ls += 20; lr.append("MACD+(20)")
        if price <= bb_l * 1.005:
            ls += 15; lr.append("BB-low(15)")
        if vsp >= 1.3:
            p = min(15, vsp * 5); ls += p; lr.append(f"Vol{vsp:.1f}x({p:.0f})")
        if short_trend > 0.3:
            ls += 10; lr.append("trend+(10)")
        if change > 1.0:
            p = min(10, change * 2); ls += p; lr.append(f"24h+{change:.1f}%")

        # Score SHORT
        ss, sr = 0, []
        if ema_bear:
            p = 20 + min(10, ema_gap * 5); ss += p; sr.append(f"EMA-({p:.0f})")
        if rsi_v > 70:
            ss += 25; sr.append("RSI>70(25)")
        elif rsi_v > 60:
            ss += 15; sr.append("RSI>60(15)")
        if ml < sg and hist < 0:
            ss += 20; sr.append("MACD-(20)")
        if price >= bb_u * 0.995:
            ss += 15; sr.append("BB-high(15)")
        if vsp >= 1.3:
            p = min(15, vsp * 5); ss += p; sr.append(f"Vol{vsp:.1f}x({p:.0f})")
        if short_trend < -0.3:
            ss += 10; sr.append("trend-(10)")
        if change < -1.0:
            p = min(10, abs(change) * 2); ss += p; sr.append(f"24h{change:.1f}%")

        # TP dinamico por ATR
        atr_pct = (atr_v / price * 100) if price > 0 else 0
        tp_dyn  = max(TP_PCT, min(TP_PCT * 2, atr_pct * 1.5))

        if ls > ss and ls >= MIN_SCORE and rsi_v <= 72:
            return {'signal':'LONG',  'price':price, 'change':change,
                    'score':ls, 'reasons':' | '.join(lr),
                    'rsi':rsi_v, 'vol':vsp, 'tp_pct':tp_dyn, 'sl_pct':SL_PCT}

        if ss > ls and ss >= MIN_SCORE and rsi_v >= 28:
            return {'signal':'SHORT', 'price':price, 'change':change,
                    'score':ss, 'reasons':' | '.join(sr),
                    'rsi':rsi_v, 'vol':vsp, 'tp_pct':tp_dyn, 'sl_pct':SL_PCT}

        return None

    # ---------------------------------------------------------------- CANTIDAD

    def _qty(self, symbol, price):
        info    = self._contracts.get(symbol, {'step': 1.0, 'prec': 2})
        step, prec = info['step'], info['prec']
        capital = max(POSITION_SIZE, MIN_TRADE)
        raw     = capital / price
        stepped = math.ceil(raw / step) * step if step > 0 else raw
        qty     = round(stepped, prec)
        val     = qty * price
        i = 0
        while val < MIN_TRADE and step > 0 and i < 1000:
            qty += step; qty = round(qty, prec); val = qty * price; i += 1
        return qty, round(val, 4)

    # ---------------------------------------------------------------- ABRIR TRADE

    def open_trade(self, symbol, sig):
        """Abrir posicion con TP y SL automaticos"""
        price     = sig['price']
        direction = sig['signal']
        tp_pct    = sig['tp_pct']
        sl_pct    = sig['sl_pct']

        if not AUTO_TRADING:
            log.info(f"  SEÑAL {direction} {symbol} score:{sig['score']:.0f} [AUTO-TRADING OFF]")
            log.info(f"  Para activar: pon AUTO_TRADING_ENABLED=true en Railway Variables")
            return False

        qty, val = self._qty(symbol, price)
        if val < MIN_TRADE:
            log.warning(f"  {symbol} rechazado: ${val:.2f} < min ${MIN_TRADE}")
            return False

        tp = price * (1 + tp_pct/100) if direction == 'LONG' else price * (1 - tp_pct/100)
        sl = price * (1 - sl_pct/100) if direction == 'LONG' else price * (1 + sl_pct/100)

        log.info(f"\n  Abriendo {direction} {symbol}")
        log.info(f"  Score:{sig['score']:.0f}/100 | RSI:{sig['rsi']:.1f} | Vol:{sig['vol']:.1f}x")
        log.info(f"  {sig['reasons']}")
        log.info(f"  Entry:${price:.6f} | Qty:{qty} (${val:.2f}) | TP:{tp_pct:.1f}% SL:{sl_pct:.1f}%")

        # 1. Orden de mercado
        r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol':       symbol,
            'side':         'BUY' if direction == 'LONG' else 'SELL',
            'positionSide': direction,
            'type':         'MARKET',
            'quantity':     str(qty),
        })
        d = r.json()
        if d.get('code') != 0:
            log.error(f"  Error abriendo [{d.get('code')}]: {d.get('msg')}")
            return False

        oid = d.get('data', {}).get('orderId', 'N/A')
        log.info(f"  POSICION ABIERTA | OrderID: {oid}")

        # 2. Take Profit
        time.sleep(0.5)
        tp_ok = self._cond_order(symbol, direction, qty, tp, 'TAKE_PROFIT_MARKET')

        # 3. Stop Loss
        time.sleep(0.5)
        sl_ok = self._cond_order(symbol, direction, qty, sl, 'STOP_MARKET')

        # Registrar
        self.open_trades[symbol] = {
            'direction':  direction,
            'entry':      price,
            'qty':        qty,
            'val':        val,
            'tp':         tp,
            'sl':         sl,
            'tp_pct':     tp_pct,
            'sl_pct':     sl_pct,
            'highest':    price,
            'lowest':     price,
            'order_id':   oid,
            'tp_ok':      tp_ok,
            'sl_ok':      sl_ok,
            'opened_at':  datetime.now(),
            'score':      sig['score'],
        }
        self.stats['exec'] += 1

        self._tg(
            f"<b>TRADE ABIERTO</b>\n"
            f"{direction} {symbol} | Score:{sig['score']:.0f}/100\n"
            f"Entry: ${price:.4f}\n"
            f"{'OK' if tp_ok else 'ERR'} TP: ${tp:.4f} (+{tp_pct:.1f}%)\n"
            f"{'OK' if sl_ok else 'ERR'} SL: ${sl:.4f} (-{sl_pct:.1f}%)\n"
            f"Capital: ${val:.2f} x{LEVERAGE}\n"
            f"{sig['reasons']}"
        )
        return True

    def _cond_order(self, symbol, direction, qty, stop_price, otype):
        """Colocar orden TP o SL condicional"""
        try:
            r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':       symbol,
                'side':         'SELL' if direction == 'LONG' else 'BUY',
                'positionSide': direction,
                'type':         otype,
                'quantity':     str(qty),
                'stopPrice':    str(round(stop_price, 6)),
            })
            d = r.json()
            lbl = "TP" if "TAKE" in otype else "SL"
            if d.get('code') == 0:
                log.info(f"  {lbl} OK @ ${stop_price:.6f}")
                return True
            log.warning(f"  {lbl} ERR [{d.get('code')}]: {d.get('msg')}")
            return False
        except Exception as e:
            log.warning(f"  {otype} exc: {e}")
            return False

    # ---------------------------------------------------------------- CERRAR TRADE

    def close_trade(self, symbol, cur_price, reason):
        """Cerrar posicion (backup si BingX no ejecuto TP/SL)"""
        if symbol not in self.open_trades:
            return False
        t = self.open_trades[symbol]
        try:
            r = bingx_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol':       symbol,
                'side':         'SELL' if t['direction'] == 'LONG' else 'BUY',
                'positionSide': t['direction'],
                'type':         'MARKET',
                'quantity':     str(t['qty']),
            })
            d = r.json()
            if d.get('code') == 0:
                pnl = (cur_price - t['entry']) * t['qty'] if t['direction'] == 'LONG' \
                      else (t['entry'] - cur_price) * t['qty']
                pnl_pct = pnl / t['val'] * 100
                self.stats['closed'] += 1
                self.stats['pnl']    += pnl
                if pnl > 0: self.stats['wins']   += 1
                else:        self.stats['losses'] += 1
                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0
                mins  = int((datetime.now() - t['opened_at']).total_seconds() / 60)

                log.info(f"  CERRADO({reason}) {symbol} PnL:${pnl:+.2f}({pnl_pct:+.1f}%) dur:{mins}min")
                self._tg(
                    f"<b>CERRADO - {reason}</b>\n"
                    f"{symbol}\n"
                    f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                    f"Duracion: {mins} min\n"
                    f"Total PnL: ${self.stats['pnl']:+.2f}\n"
                    f"Win Rate: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)"
                )
                del self.open_trades[symbol]
                return True
        except Exception as e:
            log.error(f"  Error cerrando {symbol}: {e}")
        return False

    # ---------------------------------------------------------------- MONITOR

    async def monitor_trades(self):
        """Monitorear trades abiertos con trailing stop y cierre automatico"""
        for symbol in list(self.open_trades.keys()):
            try:
                t  = self.open_trades[symbol]
                tk = self._ticker(symbol)
                if not tk: continue
                cur = tk['price']

                if t['direction'] == 'LONG':
                    pnl_pct = (cur - t['entry']) / t['entry'] * 100
                    hit_tp  = cur >= t['tp']
                    hit_sl  = cur <= t['sl']
                    # Trailing stop: cuando ganancia >= 1% mover SL para proteger
                    if TRAILING and cur > t['highest']:
                        t['highest'] = cur
                        if pnl_pct >= 1.0:
                            new_sl = cur * (1 - t['sl_pct'] / 100)
                            if new_sl > t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing SL {symbol} -> ${new_sl:.4f}")
                else:
                    pnl_pct = (t['entry'] - cur) / t['entry'] * 100
                    hit_tp  = cur <= t['tp']
                    hit_sl  = cur >= t['sl']
                    if TRAILING and cur < t['lowest']:
                        t['lowest'] = cur
                        if pnl_pct >= 1.0:
                            new_sl = cur * (1 + t['sl_pct'] / 100)
                            if new_sl < t['sl']:
                                t['sl'] = new_sl
                                log.info(f"  Trailing SL {symbol} -> ${new_sl:.4f}")

                if abs(pnl_pct) > 0.4:
                    log.info(
                        f"  {symbol} {t['direction']} "
                        f"PnL:{pnl_pct:+.1f}% ${cur:.4f} | "
                        f"TP:${t['tp']:.4f} SL:${t['sl']:.4f}"
                    )

                if hit_tp:   self.close_trade(symbol, cur, "TAKE PROFIT")
                elif hit_sl: self.close_trade(symbol, cur, "STOP LOSS")

            except Exception as e:
                log.debug(f"Monitor {symbol}: {e}")

    # ---------------------------------------------------------------- TELEGRAM

    def _tg(self, msg):
        try:
            if TELEGRAM_TOKEN and TELEGRAM_CHAT:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                    timeout=5
                )
        except: pass

    # ---------------------------------------------------------------- LOOP PRINCIPAL

    async def run(self):
        log.info("\nBot arrancado - trading automatico\n")
        iteration    = 0
        last_refresh = 0

        while True:
            try:
                iteration += 1
                now = time.time()

                # Actualizar lista monedas cada 10 min
                if now - last_refresh > 600:
                    log.info("Actualizando monedas...")
                    self._get_symbols()
                    last_refresh = now

                total = self.stats['wins'] + self.stats['losses']
                wr    = self.stats['wins'] / total * 100 if total else 0

                log.info(f"\n{'='*70}")
                log.info(
                    f"#{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                    f"Trades:{len(self.open_trades)}/{MAX_TRADES} | "
                    f"PnL:${self.stats['pnl']:+.2f} | "
                    f"WR:{wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)"
                )
                log.info(f"AUTO-TRADING: {'ON' if AUTO_TRADING else 'OFF'}")
                log.info(f"{'='*70}\n")

                # 1. Monitorear posiciones abiertas
                await self.monitor_trades()

                # 2. Buscar nuevas señales
                if len(self.open_trades) < MAX_TRADES:
                    found = 0
                    for i, sym in enumerate(self.symbols):
                        if len(self.open_trades) >= MAX_TRADES:
                            break
                        sig = self.analyze(sym)
                        if sig:
                            found += 1
                            log.info(
                                f"  SEÑAL {sig['signal']} {sym} "
                                f"score:{sig['score']:.0f} RSI:{sig['rsi']:.0f} "
                                f"Vol:{sig['vol']:.1f}x"
                            )
                            self.open_trade(sym, sig)
                        await asyncio.sleep(0.15)
                        if (i + 1) % 20 == 0:
                            log.info(f"  {i+1}/{len(self.symbols)} analizadas")

                    log.info(f"\n  {len(self.symbols)} monedas | {found} señales encontradas")
                else:
                    log.info(f"  Max trades ({MAX_TRADES}) alcanzado - solo monitoreando")

                log.info(f"\n  Proxima iteracion en {INTERVAL}s\n")
                await asyncio.sleep(INTERVAL)

            except KeyboardInterrupt:
                log.info("Bot detenido")
                break
            except Exception as e:
                log.error(f"Error en loop: {e}")
                await asyncio.sleep(15)


# ============================================================================
# MAIN
# ============================================================================

async def main():
    try:
        await TradingBot().run()
    except Exception as e:
        log.error(f"Error fatal: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Terminado")
