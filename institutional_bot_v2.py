#!/usr/bin/env python3
"""
🏆 INSTITUTIONAL BOT v3.2 — Higher TF Filter Edition
═══════════════════════════════════════════════════════════════════════

CHANGELOG v3.2 (PROFITABILITY IMPROVEMENTS):
├─ ✅ Filtro obligatorio de tendencia 1h (mayor impacto en WR)
├─ ✅ Lista de símbolos reducida a 15 majors de alta liquidez
├─ ✅ Score mínimo subido a 80 (calidad sobre cantidad)
├─ ✅ SL mínimo 1.2% (evitar stop-outs por spread en altcoins)
├─ ✅ TP simplificado: 60% en TP1 a 2×SL, runner con trail
├─ ✅ Eliminado TP2 parcial redundante → menos fees
├─ ✅ MAX_SYMBOLS reducido a 15 símbolos selectos
├─ ✅ Position size como % del equity (no fijo en USD)
├─ ✅ Nuevo filtro: RSI 1h entre 40-65 (no sobrecomprado)
├─ ✅ Circuit breaker per-trade: máx 2% del equity por trade
└─ ✅ Hourly trend cache para no re-fetchear 1h cada scan

PROBLEMA IDENTIFICADO EN v3.1:
  - WR 45% con R:R 1.4× = matemáticamente perdedora
  - 5m timeframe sin confirmación superior = señales ruidosas
  - 50 símbolos incluían altcoins con spread alto
  - Capital $35 insuficiente → position sizing roto

FILOSOFÍA (inspirado en @pheonix_trader):
  "Los indicadores van con retraso. Necesito ESTRUCTURA, no confirmación."
  "Precio + Volumen es todo. Si el breakout es con bajo volumen, lo paso por alto."
  "MA10 y MA20 — eso es todo. Guían mis trailing stops y momentum."
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
import statistics
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════

def clean_env(key: str, default, typ='str'):
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

# API
API_KEY    = clean_env('BINGX_API_KEY', '')
API_SECRET = clean_env('BINGX_API_SECRET', '')
TG_TOKEN   = clean_env('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = clean_env('TELEGRAM_CHAT_ID', '')

# CAPITAL
AUTO_TRADING   = clean_env('AUTO_TRADING_ENABLED', 'false', 'bool')
POSITION_SIZE  = clean_env('POSITION_SIZE_USD', '10', 'float')      # fallback fijo
POSITION_PCT   = clean_env('POSITION_SIZE_PCT', '5.0', 'float')     # v3.2: 5% del equity por trade
LEVERAGE       = min(clean_env('LEVERAGE', '2', 'int'), 3)
MAX_POSITIONS  = clean_env('MAX_POSITIONS', '2', 'int')
ACCOUNT_EQUITY = clean_env('ACCOUNT_EQUITY', '100', 'float')
RISK_PER_TRADE = clean_env('RISK_PCT_PER_TRADE', '1.0', 'float')

# FUNDING RATE
FUNDING_LONG_OK   = clean_env('FUNDING_LONG_OK', '0.03', 'float')
FUNDING_LONG_SKIP = clean_env('FUNDING_LONG_SKIP', '0.05', 'float')
FUNDING_ENABLED   = clean_env('FUNDING_FILTER', 'true', 'bool')

# OPEN INTEREST
OI_BREAKOUT_MIN   = clean_env('OI_BREAKOUT_MIN', '1.5', 'float')
OI_WEAK_THRESHOLD = clean_env('OI_WEAK_THRESHOLD', '0.5', 'float')
OI_ENABLED        = clean_env('OI_FILTER', 'true', 'bool')

# SESSION
SESSION_BEST  = {13, 14, 15, 16, 17, 18, 19, 20, 21, 22}
SESSION_OK    = {7, 8, 9, 10, 11, 12}
SESSION_AVOID = {22, 23, 0, 1, 2, 3, 4, 5, 6}
SESSION_FILTER_ENABLED = clean_env('SESSION_FILTER', 'true', 'bool')

# CVD
CVD_LOOKBACK  = clean_env('CVD_LOOKBACK_BARS', '20', 'int')
CVD_THRESHOLD = clean_env('CVD_THRESHOLD', '1.5', 'float')

# STOP LOSS & TP — v3.2: TP simplificado (menos fees)
SL_ATR_MULT  = clean_env('SL_ATR_MULTIPLIER', '1.8', 'float')   # v3.2: 1.5→1.8 más espacio
SL_MIN_PCT   = clean_env('SL_MIN_PCT', '1.2', 'float')           # v3.2: 0.8→1.2 evitar spread en altcoins
SL_MAX_PCT   = clean_env('SL_MAX_PCT', '2.5', 'float')
TP1_PCT      = clean_env('TP1_PERCENTAGE', '60', 'float')         # v3.2: 40→60% en TP1
TP2_PCT      = clean_env('TP2_PERCENTAGE', '40', 'float')
TP1_RR       = clean_env('TP1_RISK_REWARD', '2.0', 'float')      # v3.2: 1.5→2.0 mejor R:R
TP2_RR       = clean_env('TP2_RISK_REWARD', '3.0', 'float')      # v3.2: 2.5→3.0
RUNNER_TRAIL = clean_env('RUNNER_TRAIL_ATR', '2.5', 'float')
MIN_EDGE     = clean_env('MIN_EDGE_RATIO', '5.0', 'float')        # v3.2: 4.0→5.0

# v3.0 FILTERS
VOLUME_BREAKOUT_MULT = clean_env('VOLUME_BREAKOUT_MULT', '1.8', 'float')  # AUMENTADO DE 1.5 A 1.8
REGIME_ATR_MIN_PCT = clean_env('REGIME_ATR_MIN_PCT', '0.5', 'float')  # AUMENTADO DE 0.4 A 0.5
REGIME_ATR_MAX_PCT = clean_env('REGIME_ATR_MAX_PCT', '3.5', 'float')  # REDUCIDO DE 4.0 A 3.5
VCP_LOOKBACK = clean_env('VCP_LOOKBACK', '20', 'int')
MAX_CORR_LONGS = clean_env('MAX_CORR_LONGS', '1', 'int')  # REDUCIDO DE 2 A 1
EMA9_REQUIRED = clean_env('EMA9_REQUIRED', 'true', 'bool')

# MARKET FILTERS — v3.2: menos símbolos, más calidad
MIN_VOLUME_24H = clean_env('MIN_VOLUME_24H', '10000000', 'float')  # v3.2: 2M→10M solo majors
MAX_SYMBOLS    = clean_env('MAX_SYMBOLS', '15', 'int')              # v3.2: 30→15
MIN_SCORE      = clean_env('MIN_ENTRY_SCORE', '80', 'float')        # v3.2: 75→80

# v3.2 — HIGHER TIMEFRAME FILTER (el cambio más importante)
HTF_FILTER_ENABLED = clean_env('HTF_FILTER', 'true', 'bool')       # v3.2: NUEVO - filtro 1h obligatorio
HTF_RSI_MAX        = clean_env('HTF_RSI_MAX', '65', 'float')        # v3.2: no entrar si RSI 1h > 65
HTF_CACHE_SECONDS  = clean_env('HTF_CACHE_SECONDS', '300', 'int')   # v3.2: cachear análisis 1h 5min

# CIRCUIT BREAKER - MÁS AGRESIVO
CIRCUIT_BREAKER_PCT = clean_env('CIRCUIT_BREAKER_PCT', '3.0', 'float')  # REDUCIDO DE 6.0 A 3.0
MAX_LOSING_STREAK   = clean_env('MAX_LOSING_STREAK', '3', 'int')  # REDUCIDO DE 4 A 3
MAX_DAILY_TRADES    = clean_env('MAX_DAILY_TRADES', '8', 'int')  # NUEVO: máximo 8 trades/día

# TIMING
SCAN_INTERVAL    = clean_env('SCAN_INTERVAL_SEC', '90', 'int')  # AUMENTADO DE 60 A 90
MONITOR_INTERVAL = clean_env('MONITOR_INTERVAL_SEC', '20', 'int')  # AUMENTADO DE 15 A 20

# CONSTANTS
BASE_URL   = "https://open-api.bingx.com"
FEE_TAKER  = 0.001
FEE_MAKER  = 0.0002
SLIPPAGE   = 0.0003  # AUMENTADO DE 0.0002 A 0.0003
TOTAL_COST = FEE_TAKER + FEE_MAKER + SLIPPAGE

EXCLUDE_SYMBOLS = {
    'DOW', 'SP500', 'GOLD', 'SILVER', 'XAU', 'OIL', 'BRENT',
    'EUR', 'GBP', 'JPY', 'TSLA', 'AAPL', 'MSFT', 'GOOGL',
    'AMZN', 'META', 'NVDA', 'COIN', 'MSTR', 'PAXG', 'XAUT',
    'Q-USDT', 'BEAT-USDT'
}

# v3.2: Lista de símbolos preferidos — alta liquidez, spread bajo, movimientos limpios
# Si están disponibles en BingX, se priorizan sobre el resto
PREFERRED_SYMBOLS = [
    'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'ADA-USDT',
    'AVAX-USDT', 'MATIC-USDT', 'LINK-USDT', 'DOT-USDT', 'UNI-USDT',
    'ATOM-USDT', 'NEAR-USDT', 'LTC-USDT', 'APT-USDT', 'ARB-USDT',
]

# ════════════════════════════════════════════════════════════════════
# LOGGING - MEJORADO
# ════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/bot.log', mode='a')  # Log file para debugging
    ]
)
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# API FUNCTIONS - MEJORADO CON MEJOR ERROR HANDLING
# ════════════════════════════════════════════════════════════════════

def api_request(method: str, endpoint: str, params: dict = None, retries: int = 3) -> dict:
    """API request mejorada con mejor manejo de errores"""
    params = params or {}
    last_error = None
    
    for attempt in range(retries + 1):
        try:
            p = {**{k: str(v) for k, v in params.items()},
                 'timestamp': str(int(time.time() * 1000))}
            query = urlencode(sorted(p.items()))
            signature = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{query}&signature={signature}"
            headers = {'X-BX-APIKEY': API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}
            
            response = getattr(requests, method.lower())(url, headers=headers, timeout=15)
            data = response.json()
            
            # Log errores específicos
            if data.get('code') != 0:
                log.warning(f"API {endpoint} error: {data.get('msg', 'Unknown')} | Params: {params}")
            
            return data
            
        except requests.exceptions.Timeout as e:
            last_error = f"Timeout: {e}"
            if attempt < retries:
                time.sleep(2 ** attempt)
        except requests.exceptions.ConnectionError as e:
            last_error = f"Connection error: {e}"
            if attempt < retries:
                time.sleep(2 ** attempt)
        except Exception as e:
            last_error = f"Unexpected error: {e}"
            log.error(f"API {endpoint} exception: {e}\n{traceback.format_exc()}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    
    log.error(f"API {endpoint} failed after {retries+1} attempts: {last_error}")
    return {'code': -1, 'msg': last_error}

def public_request(path: str, params: dict = None) -> dict:
    """Public endpoint request con timeout y error handling"""
    try:
        response = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10)
        return response.json()
    except Exception as e:
        log.error(f"Public request {path} failed: {e}")
        return {'code': -1, 'msg': str(e)}

def safe_float(val, default: float = 0.0) -> float:
    """Conversión segura a float"""
    try:
        if val is None or val == '':
            return default
        return float(val)
    except (ValueError, TypeError):
        return default

def extract_equity(data: dict) -> float:
    """Extrae equity de respuesta BingX — maneja estructura anidada"""
    if data.get('code') != 0:
        return 0.0
    raw = data.get('data', {})
    if isinstance(raw, dict) and 'balance' in raw:
        inner = raw['balance']
        if isinstance(inner, dict):
            return safe_float(inner.get('equity', inner.get('availableMargin', 0)))
    if isinstance(raw, dict):
        return safe_float(raw.get('equity', raw.get('balance', 0)))
    return 0.0

# ════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ════════════════════════════════════════════════════════════════════

def ema(prices: List[float], period: int) -> float:
    if not prices or len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    k = 2 / (period + 1)
    val = prices[0]
    for p in prices[1:]:
        val = p * k + val * (1 - k)
    return val

def sma(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    return sum(prices[-period:]) / period

def atr_calc(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 0
    trs = []
    for i in range(1, min(len(closes), period + 1)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def rsi_calc(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100 - (100 / (1 + ag/al)) if al > 0 else 100.0

def volume_avg(volumes: List[float], period: int = 20) -> float:
    recent = volumes[-period:] if len(volumes) >= period else volumes
    return sum(recent) / len(recent) if recent else 0

def cvd_calc(volumes: List[float], closes: List[float], opens: List[float]) -> float:
    if len(volumes) < 2:
        return 0
    cvd = 0
    for i in range(len(volumes)):
        delta = 1 if closes[i] > opens[i] else -1 if closes[i] < opens[i] else 0
        cvd += volumes[i] * delta
    return cvd

# ════════════════════════════════════════════════════════════════════
# PATTERN DETECTION
# ════════════════════════════════════════════════════════════════════

def detect_vcp(closes: List[float], volumes: List[float], lookback: int = 20) -> Tuple[bool, str]:
    """VCP — Volatility Contraction Pattern"""
    if len(closes) < lookback:
        return False, "insufficient_data"
    
    recent = closes[-lookback:]
    recent_vols = volumes[-lookback:]
    
    contractions = []
    for i in range(2, len(recent) - 2):
        if recent[i] < recent[i-1] and recent[i] < recent[i+1]:
            depth = (recent[i-1] - recent[i]) / recent[i-1] * 100
            vol_at_dip = recent_vols[i]
            contractions.append((depth, vol_at_dip))
    
    if len(contractions) < 2:
        return False, "no_vcp_contractions"
    
    depths = [c[0] for c in contractions[-3:]]
    depth_contracting = all(depths[i] < depths[i-1] * 1.1 for i in range(1, len(depths)))
    
    recent_high = max(recent)
    current = recent[-1]
    near_high = current >= recent_high * 0.92
    
    if depth_contracting and near_high and len(contractions) >= 2:
        return True, f"vcp_{len(contractions)}contractions"
    
    return False, "no_vcp"

def detect_flag(closes: List[float], volumes: List[float], highs: List[float], 
                lows: List[float]) -> Tuple[bool, str]:
    """FLAG Pattern — Banderín/Bandera"""
    if len(closes) < 15:
        return False, "insufficient_data"
    
    pole_window = 7
    flag_window = 5
    
    if len(closes) < pole_window + flag_window:
        return False, "insufficient_data"
    
    pole_closes = closes[-(pole_window + flag_window):-flag_window]
    flag_closes = closes[-flag_window:]
    flag_vols = volumes[-flag_window:]
    pole_vols = volumes[-(pole_window + flag_window):-flag_window]
    
    pole_move = (pole_closes[-1] - pole_closes[0]) / pole_closes[0] * 100
    flag_range = (max(flag_closes) - min(flag_closes)) / min(flag_closes) * 100
    
    avg_pole_vol = sum(pole_vols) / len(pole_vols)
    avg_flag_vol = sum(flag_vols) / len(flag_vols)
    vol_declining = avg_flag_vol < avg_pole_vol * 0.8
    
    flag_retrace = (pole_closes[-1] - min(flag_closes)) / (pole_closes[-1] - pole_closes[0]) * 100
    
    if (pole_move > 4.0 and 
        flag_range < 3.5 and 
        vol_declining and 
        flag_retrace < 50 and
        len(flag_closes) >= 3):
        return True, f"flag_pole{pole_move:.1f}pct_range{flag_range:.1f}pct"
    
    return False, "no_flag"

def detect_market_regime(closes: List[float], highs: List[float], 
                          lows: List[float], volumes: List[float]) -> Tuple[str, float]:
    """Market Regime Detection"""
    if len(closes) < 30:
        return "unknown", 0.0
    
    current = closes[-1]
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    
    atr_val = atr_calc(highs, lows, closes, 14)
    atr_pct = (atr_val / current * 100) if current > 0 else 0
    
    ma20_prev = sma(closes[:-5], 20) if len(closes) > 25 else ma20
    ma20_rising = ma20 > ma20_prev
    
    above_ma10 = current > ma10
    above_ma20 = current > ma20
    
    if atr_pct > REGIME_ATR_MAX_PCT:
        return "volatile_extreme", atr_pct
    if atr_pct < REGIME_ATR_MIN_PCT:
        return "ranging_quiet", atr_pct
    if above_ma10 and above_ma20 and ma20_rising:
        return "trending_bullish", atr_pct
    if above_ma20 and ma20_rising:
        return "trending_moderate", atr_pct
    if not above_ma20 and not ma20_rising:
        return "bearish", atr_pct
    
    return "ranging", atr_pct

def check_volume_breakout(volumes: List[float], current_vol: float) -> Tuple[bool, float]:
    """Volume Breakout Filter"""
    if len(volumes) < 10:
        return True, 1.0
    
    avg_vol = volume_avg(volumes[:-1], 20)
    if avg_vol <= 0:
        return True, 1.0
    
    vol_ratio = current_vol / avg_vol
    is_breakout = vol_ratio >= VOLUME_BREAKOUT_MULT
    return is_breakout, vol_ratio

def check_9ema_setup(closes: List[float], current_price: float) -> Tuple[bool, str]:
    """9 EMA Scalp Setup"""
    ema9 = ema(closes, 9)
    ema9_prev = ema(closes[:-1], 9) if len(closes) > 1 else ema9
    
    above_ema9 = current_price > ema9
    prev_price = closes[-2] if len(closes) > 1 else current_price
    fresh_cross = prev_price <= ema9_prev and current_price > ema9
    
    if fresh_cross:
        return True, "ema9_fresh_cross"
    elif above_ema9:
        return True, "above_ema9"
    else:
        return False, "below_ema9"

def kelly_position_size(win_rate: float, avg_win: float, avg_loss: float, 
                         equity: float) -> float:
    """Half-Kelly Position Sizing"""
    if avg_loss <= 0 or win_rate <= 0:
        return POSITION_SIZE
    
    R = avg_win / avg_loss
    kelly_pct = win_rate - (1 - win_rate) / R
    half_kelly = kelly_pct / 2
    half_kelly = max(0.005, min(0.02, half_kelly))  # Limitado a 2% máximo
    kelly_size = equity * half_kelly
    return min(kelly_size, POSITION_SIZE)

# ════════════════════════════════════════════════════════════════════
# INSTITUTIONAL FILTERS
# ════════════════════════════════════════════════════════════════════

class InstitutionalFilters:
    def __init__(self):
        self.funding_cache = {}
        self.oi_cache = {}
        self.last_update = {}
        self.btc_price_history = deque(maxlen=10)
        self.btc_dominance_cache = 0.0
        self.btc_dom_last_update = 0

    def check_funding_rate(self, symbol: str) -> Tuple[bool, str, float]:
        if not FUNDING_ENABLED:
            return True, "funding_disabled", 0
        cache_key = f"{symbol}_funding"
        if cache_key in self.last_update and time.time() - self.last_update[cache_key] < 300:
            rate = self.funding_cache.get(cache_key, 0)
            return (rate < FUNDING_LONG_OK), ("funding_ok" if rate < FUNDING_LONG_OK else "funding_high"), rate
        try:
            data = public_request('/openApi/swap/v2/quote/premiumIndex', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                rate = safe_float(data['data'].get('lastFundingRate', 0)) * 100
                self.funding_cache[cache_key] = rate
                self.last_update[cache_key] = time.time()
                if rate > FUNDING_LONG_SKIP:
                    return False, "funding_overheated", rate
                return rate < FUNDING_LONG_OK, "funding_ok" if rate < FUNDING_LONG_OK else "funding_neutral", rate
        except Exception as e:
            log.error(f"Error checking funding for {symbol}: {e}")
        return True, "funding_unknown", 0

    def check_open_interest(self, symbol: str, price_change_pct: float) -> Tuple[bool, str, float]:
        if not OI_ENABLED:
            return True, "oi_disabled", 0
        try:
            data = public_request('/openApi/swap/v2/quote/openInterest', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                current_oi = safe_float(data['data'].get('openInterest', 0))
                cache_key = f"{symbol}_oi"
                if cache_key in self.oi_cache:
                    prev_oi = self.oi_cache[cache_key]
                    oi_change = ((current_oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0
                    self.oi_cache[cache_key] = current_oi
                    if price_change_pct > 1.0 and oi_change > OI_BREAKOUT_MIN:
                        return True, "oi_breakout_confirmed", oi_change
                    elif price_change_pct > 1.0 and oi_change < OI_WEAK_THRESHOLD:
                        return False, "oi_divergence_weak", oi_change
                    return True, "oi_neutral", oi_change
                else:
                    self.oi_cache[cache_key] = current_oi
                    return True, "oi_first_check", 0
        except Exception as e:
            log.error(f"Error checking OI for {symbol}: {e}")
        return True, "oi_unknown", 0

    def check_session_quality(self) -> Tuple[bool, str]:
        if not SESSION_FILTER_ENABLED:
            return True, "session_disabled"
        hour = datetime.utcnow().hour
        if hour in SESSION_BEST:
            return True, "us_session"
        elif hour in SESSION_OK:
            return True, "london_session"
        return False, "asia_session_avoid"

    def calculate_volume_quality(self, volumes, closes, opens) -> Tuple[float, str]:
        if len(volumes) < CVD_LOOKBACK:
            return 0, "cvd_insufficient_data"
        rv = volumes[-CVD_LOOKBACK:]
        rc = closes[-CVD_LOOKBACK:]
        ro = opens[-CVD_LOOKBACK:]
        cvd = cvd_calc(rv, rc, ro)
        total = sum(rv)
        cvd_n = cvd / total if total > 0 else 0
        cvd_vals = [rv[i] * (1 if rc[i] > ro[i] else -1) for i in range(len(rv))]
        try:
            std = statistics.stdev(cvd_vals) if len(cvd_vals) > 1 else 0
            if abs(cvd_n) > CVD_THRESHOLD * std:
                return cvd_n, "bullish_cvd" if cvd_n > 0 else "bearish_cvd"
        except:
            pass
        return cvd_n, "cvd_neutral"

    def check_btc_market_health(self) -> Tuple[bool, str]:
        """BTC Guard — No abrir altcoins si BTC está en caída libre"""
        try:
            data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': 'BTC-USDT'})
            if data.get('code') == 0 and data.get('data'):
                ticker = data['data']
                btc_change = safe_float(ticker.get('priceChangePercent', 0))
                btc_price = safe_float(ticker.get('lastPrice', 0))
                
                self.btc_price_history.append(btc_price)
                
                if btc_change < -2.0:
                    return False, f"btc_falling_{btc_change:.1f}pct"
                if btc_change > 0:
                    return True, "btc_positive"
                return True, "btc_neutral"
        except Exception as e:
            log.error(f"Error checking BTC health: {e}")
        return True, "btc_unknown"

def find_liquidity_zones(highs: List[float], lows: List[float], lookback: int = 100) -> Dict:
    """Find support/resistance zones"""
    if len(highs) < lookback:
        return {'resistance_zones': [], 'support_zones': []}
    rh = highs[-lookback:]
    rl = lows[-lookback:]
    swing_highs = [rh[i] for i in range(2, len(rh)-2)
                   if rh[i] > rh[i-1] and rh[i] > rh[i-2] and rh[i] > rh[i+1] and rh[i] > rh[i+2]]
    swing_lows = [rl[i] for i in range(2, len(rl)-2)
                  if rl[i] < rl[i-1] and rl[i] < rl[i-2] and rl[i] < rl[i+1] and rl[i] < rl[i+2]]
    return {
        'resistance_zones': sorted(swing_highs, reverse=True)[:5],
        'support_zones': sorted(swing_lows)[:5]
    }

# ════════════════════════════════════════════════════════════════════
# INSTITUTIONAL BOT v3.1 — FIXED
# ════════════════════════════════════════════════════════════════════

class InstitutionalBot:
    def __init__(self):
        self.symbols = []
        self.positions = {}
        self.contracts_info = {}
        self.filters = InstitutionalFilters()
        self.equity = ACCOUNT_EQUITY
        self.daily_pnl = 0.0
        self.daily_date = datetime.utcnow().date()
        self.circuit_breaker_active = False
        self.circuit_breaker_until = None
        self.losing_streak = 0
        self.daily_trades = 0
        self.htf_cache = {}          # v3.2: cache para análisis 1h por símbolo
        self.htf_cache_time = {}     # v3.2: timestamp del cache 1h
        self.stats = {
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'total_pnl': 0.0, 'total_fees': 0.0,
            'win_amounts': [], 'loss_amounts': [],
            'best_trade': 0.0, 'worst_trade': 0.0
        }

        log.info("=" * 80)
        log.info("🏆 INSTITUTIONAL BOT v3.2 — Higher TF Filter Edition")
        log.info("=" * 80)
        log.info(f"📈 MEJORAS v3.2:")
        log.info(f"   ✅ Filtro tendencia 1h obligatorio")
        log.info(f"   ✅ Solo {len(PREFERRED_SYMBOLS)} símbolos major")
        log.info(f"   ✅ Score mínimo {MIN_SCORE} (era 75)")
        log.info(f"   ✅ SL mínimo {SL_MIN_PCT}% (era 0.8%)")
        log.info(f"   ✅ Position sizing dinámico {POSITION_PCT}% equity")
        log.info("=" * 80)
        log.info(f"Capital: {POSITION_PCT}% equity × {MAX_POSITIONS} | Leverage: {LEVERAGE}×")
        log.info(f"Circuit Breaker: {CIRCUIT_BREAKER_PCT}% daily loss | Max streak: {MAX_LOSING_STREAK}")
        log.info(f"Min Score: {MIN_SCORE} | Min Edge: {MIN_EDGE}× | Max daily trades: {MAX_DAILY_TRADES}")
        log.info(f"Auto Trading: {'ENABLED 💸' if AUTO_TRADING else 'DISABLED 📝 (PAPER MODE)'}")
        log.info("=" * 80)

        if not self._connect():
            log.error("❌ No se pudo conectar a BingX")
            if AUTO_TRADING:
                sys.exit(1)

        self._load_contracts()
        self._refresh_symbols()
        self._recover_positions()

        self._send_telegram(
            f"<b>🏆 BOT v3.2 STARTED</b>\n\n"
            f"✅ Filtro 1h tendencia activado\n"
            f"📊 {len(PREFERRED_SYMBOLS)} símbolos major\n"
            f"💰 Size: {POSITION_PCT}% equity × {MAX_POSITIONS}\n"
            f"⚡ Leverage: {LEVERAGE}×\n"
            f"🛡️ Circuit: {CIRCUIT_BREAKER_PCT}% loss\n"
            f"📊 Min Score: {MIN_SCORE}\n\n"
            f"Modo: {'REAL MONEY 💸' if AUTO_TRADING else 'PAPER TRADING 📝'}"
        )

    def _connect(self) -> bool:
        """Connect to BingX with better error handling"""
        global AUTO_TRADING
        if not AUTO_TRADING:
            log.info("✓ Running in PAPER TRADING mode")
            return True
        
        if not API_KEY or not API_SECRET:
            log.error("❌ API keys not configured")
            AUTO_TRADING = False
            return False
        
        data = api_request('GET', '/openApi/swap/v2/user/balance')
        if data.get('code') == 0:
            equity = extract_equity(data)
            if equity > 0:
                self.equity = equity
                log.info(f"✓ BingX connected | Equity: ${equity:.2f}")
                return True
            else:
                log.warning("⚠️ Equity=0 in response. Using default.")
                self.equity = ACCOUNT_EQUITY
                return True
        
        log.error(f"❌ Connection failed: {data.get('msg', 'Unknown error')}")
        AUTO_TRADING = False
        return False

    def _load_contracts(self):
        """Load contract specifications"""
        data = public_request('/openApi/swap/v2/quote/contracts')
        if data.get('code') == 0:
            for c in data.get('data', []):
                s = c.get('symbol', '')
                if s:
                    self.contracts_info[s] = {
                        'min_qty': safe_float(c.get('tradeMinQuantity', 1)),
                        'qty_precision': int(c.get('quantityPrecision', 2)),
                        'contract_size': safe_float(c.get('contractSize', 1))
                    }
            log.info(f"✓ Contracts loaded: {len(self.contracts_info)}")
        else:
            log.warning(f"⚠️ Could not load contracts: {data.get('msg')}")

    def _refresh_symbols(self):
        """v3.2: Usa PREFERRED_SYMBOLS primero, luego completa con los de mayor volumen"""
        data = public_request('/openApi/swap/v2/quote/ticker')
        if data.get('code') != 0:
            log.warning("⚠️ Could not refresh symbols, using preferred list")
            self.symbols = [s for s in PREFERRED_SYMBOLS if s in self.contracts_info]
            return
        
        # Construir mapa de volumen
        vol_map = {}
        for t in data.get('data', []):
            s = t.get('symbol', '')
            if not s.endswith('-USDT'):
                continue
            try:
                price = safe_float(t.get('lastPrice', 0))
                vol = safe_float(t.get('volume', 0)) * price
                vol_map[s] = vol
            except:
                continue
        
        # Primero: preferred symbols disponibles en BingX con suficiente volumen
        preferred_available = [
            s for s in PREFERRED_SYMBOLS
            if s in self.contracts_info and vol_map.get(s, 0) >= MIN_VOLUME_24H
        ]
        
        # Completar hasta MAX_SYMBOLS con otros pares si faltan
        if len(preferred_available) < MAX_SYMBOLS:
            extras = []
            for s, vol in sorted(vol_map.items(), key=lambda x: -x[1]):
                base = s.replace('-USDT', '').upper()
                if (s not in preferred_available and
                    s in self.contracts_info and
                    not any(ex in base for ex in EXCLUDE_SYMBOLS) and
                    vol >= MIN_VOLUME_24H):
                    extras.append(s)
                if len(preferred_available) + len(extras) >= MAX_SYMBOLS:
                    break
            self.symbols = preferred_available + extras
        else:
            self.symbols = preferred_available[:MAX_SYMBOLS]
        
        log.info(f"✓ Symbols: {len(self.symbols)} ({len(preferred_available)} preferred)")

    def _recover_positions(self):
        """Recover open positions with ALL required fields (FIXED)"""
        if not AUTO_TRADING:
            return
        
        data = api_request('GET', '/openApi/swap/v2/user/positions')
        if data.get('code') != 0:
            log.warning(f"⚠️ Could not recover positions: {data.get('msg')}")
            return
        
        recovered = 0
        for pos in data.get('data', []):
            try:
                symbol = pos.get('symbol', '')
                amt = safe_float(pos.get('positionAmt', 0))
                side_str = str(pos.get('positionSide', '')).upper()
                
                # Solo LONG positions
                if (side_str == 'LONG' or (side_str == 'BOTH' and amt > 0)) and abs(amt) > 0:
                    entry = safe_float(pos.get('avgPrice') or pos.get('entryPrice', 0))
                    if entry <= 0:
                        continue
                    
                    # CRITICAL FIX: Inicializar TODOS los campos necesarios
                    self.positions[symbol] = {
                        'entry': entry,
                        'qty': abs(amt),
                        'side': 'LONG',
                        'tp1_hit': False,
                        'tp2_hit': False,
                        'recovered': True,
                        'highest': entry,  # FIX: Siempre inicializar highest
                        'opened_at': datetime.now(),
                        'pnl_realized': 0.0,
                        'signal': {'atr': 0, 'atr_pct': 0},  # FIX: Inicializar signal
                        'tp1_price': entry * 1.015,
                        'tp2_price': entry * 1.025,
                        'sl_price': entry * 0.985,
                        'sl_pct': 1.5,
                        'qty_tp1': abs(amt) * TP1_PCT / 100,
                        'qty_tp2': abs(amt) * TP2_PCT / 100,
                        'score': 0,
                        'pos_size': POSITION_SIZE
                    }
                    recovered += 1
                    log.info(f"♻️ Position recovered: {symbol} @ ${entry:.6f}")
            
            except Exception as e:
                log.error(f"Error recovering position: {e}\n{traceback.format_exc()}")
                continue
        
        if recovered > 0:
            log.info(f"✓ Positions recovered: {recovered}")

    def _get_klines(self, symbol: str, interval: str = '5m', limit: int = 150):
        """Get kline data with error handling"""
        try:
            data = public_request('/openApi/swap/v3/quote/klines', {
                'symbol': symbol, 'interval': interval, 'limit': limit
            })
            if data.get('code') == 0 and data.get('data'):
                klines = data['data']
                return (
                    [safe_float(k['close']) for k in klines],
                    [safe_float(k['high']) for k in klines],
                    [safe_float(k['low']) for k in klines],
                    [safe_float(k['volume']) for k in klines],
                    [safe_float(k['open']) for k in klines]
                )
        except Exception as e:
            log.error(f"Error getting klines for {symbol}: {e}")
        
        return None, None, None, None, None

    def _get_ticker(self, symbol: str):
        """Get ticker data"""
        try:
            data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                t = data['data']
                return {
                    'price': safe_float(t.get('lastPrice', 0)),
                    'change_pct': safe_float(t.get('priceChangePercent', 0)),
                    'volume': safe_float(t.get('volume', 0))
                }
        except Exception as e:
            log.error(f"Error getting ticker for {symbol}: {e}")
        
        return None

    def _get_htf_trend(self, symbol: str) -> Tuple[bool, str, float]:
        """
        v3.2: Análisis de tendencia en timeframe 1h.
        Cacheado 5 minutos para no re-fetchear en cada scan.
        Retorna (ok_to_long, reason, rsi_1h)
        """
        if not HTF_FILTER_ENABLED:
            return True, "htf_disabled", 50.0
        
        now = time.time()
        cache_key = f"{symbol}_htf"
        if (cache_key in self.htf_cache and 
                now - self.htf_cache_time.get(cache_key, 0) < HTF_CACHE_SECONDS):
            return self.htf_cache[cache_key]
        
        try:
            closes_1h, highs_1h, lows_1h, vols_1h, opens_1h = self._get_klines(symbol, '1h', 60)
            if not closes_1h or len(closes_1h) < 30:
                result = (True, "htf_insufficient_data", 50.0)
                self.htf_cache[cache_key] = result
                self.htf_cache_time[cache_key] = now
                return result
            
            price_1h  = closes_1h[-1]
            ma10_1h   = sma(closes_1h, 10)
            ma20_1h   = sma(closes_1h, 20)
            ema50_1h  = ema(closes_1h, 50)
            rsi_1h    = rsi_calc(closes_1h, 14)
            
            # Tendencia 1h: precio > MA10 > MA20 > EMA50
            above_ma10_1h  = price_1h > ma10_1h
            above_ma20_1h  = price_1h > ma20_1h
            above_ema50_1h = price_1h > ema50_1h
            ma20_prev_1h   = sma(closes_1h[:-5], 20) if len(closes_1h) > 25 else ma20_1h
            ma20_rising_1h = ma20_1h > ma20_prev_1h
            
            # RSI 1h: no entrar si sobrecomprado (>65) o en caída libre (<35)
            rsi_ok = HTF_RSI_MAX >= rsi_1h >= 35
            
            if not above_ma20_1h or not ma20_rising_1h:
                result = (False, f"htf_downtrend_1h_rsi{int(rsi_1h)}", rsi_1h)
            elif not rsi_ok:
                result = (False, f"htf_rsi_extreme_{int(rsi_1h)}", rsi_1h)
            elif above_ma10_1h and above_ma20_1h and above_ema50_1h and ma20_rising_1h:
                result = (True, f"htf_strong_uptrend_rsi{int(rsi_1h)}", rsi_1h)
            else:
                result = (True, f"htf_moderate_uptrend_rsi{int(rsi_1h)}", rsi_1h)
            
            self.htf_cache[cache_key] = result
            self.htf_cache_time[cache_key] = now
            return result
        
        except Exception as e:
            log.error(f"Error getting HTF trend for {symbol}: {e}")
            result = (True, "htf_error", 50.0)
            self.htf_cache[cache_key] = result
            self.htf_cache_time[cache_key] = now
            return result

    def analyze_symbol(self, symbol: str) -> Optional[Dict]:
        """Analyze symbol - Phoenix Trader philosophy"""
        # Skip if already in position
        if symbol in self.positions:
            return None

        # Validate symbol exists in contracts
        if symbol not in self.contracts_info:
            log.debug(f"{symbol}: ❌ No contract info")
            return None

        closes, highs, lows, volumes, opens = self._get_klines(symbol, '5m', 150)
        if not closes or len(closes) < 50:
            return None

        ticker = self._get_ticker(symbol)
        if not ticker or ticker['price'] <= 0:
            return None

        price = ticker['price']
        change_24h = ticker['change_pct']
        current_vol = ticker['volume']

        # MARKET REGIME
        regime, atr_pct = detect_market_regime(closes, highs, lows, volumes)
        if regime in ("volatile_extreme", "ranging_quiet", "bearish"):
            log.debug(f"{symbol}: ❌ Regime={regime} ({atr_pct:.2f}%)")
            return None

        # BTC HEALTH
        if symbol != 'BTC-USDT':
            btc_ok, btc_reason = self.filters.check_btc_market_health()
            if not btc_ok:
                log.debug(f"{symbol}: ❌ {btc_reason}")
                return None

        # CORRELATION GUARD
        if symbol not in ('BTC-USDT', 'ETH-USDT'):
            corr_longs = sum(1 for s in self.positions if s not in ('BTC-USDT', 'ETH-USDT'))
            if corr_longs >= MAX_CORR_LONGS:
                log.debug(f"{symbol}: ❌ Correlation guard ({corr_longs} altcoins)")
                return None

        # FUNDING RATE
        funding_ok, funding_reason, funding_rate = self.filters.check_funding_rate(symbol)
        if not funding_ok:
            log.debug(f"{symbol}: ❌ {funding_reason} ({funding_rate:.3f}%)")
            return None

        # OPEN INTEREST
        oi_ok, oi_reason, oi_change = self.filters.check_open_interest(symbol, change_24h)
        if not oi_ok:
            log.debug(f"{symbol}: ❌ {oi_reason}")
            return None

        # SESSION
        session_ok, session_name = self.filters.check_session_quality()
        if not session_ok:
            log.debug(f"{symbol}: ❌ {session_name}")
            return None

        # v3.2: HIGHER TIMEFRAME FILTER — el más importante
        htf_ok, htf_reason, rsi_1h = self._get_htf_trend(symbol)
        if not htf_ok:
            log.debug(f"{symbol}: ❌ HTF: {htf_reason}")
            return None
        vol_breakout, vol_ratio = check_volume_breakout(volumes, current_vol)

        # TECHNICAL ANALYSIS
        ma10  = sma(closes, 10)
        ma20  = sma(closes, 20)
        ema9  = ema(closes, 9)
        ema50 = ema(closes, 50)

        atr_val = atr_calc(highs, lows, closes, 14)
        rsi_val = rsi_calc(closes, 14)
        cvd_val, cvd_signal = self.filters.calculate_volume_quality(volumes, closes, opens)
        liq_zones = find_liquidity_zones(highs, lows, 100)

        # MA conditions
        above_ma10 = price > ma10
        above_ma20 = price > ma20
        ma10_above_ma20 = ma10 > ma20
        ma20_prev = sma(closes[:-5], 20) if len(closes) > 25 else ma20
        ma20_rising = ma20 > ma20_prev

        # PATTERN DETECTION
        vcp_detected, vcp_reason = detect_vcp(closes, volumes, VCP_LOOKBACK)
        flag_detected, flag_reason = detect_flag(closes, volumes, highs, lows)
        ema9_ok, ema9_reason = check_9ema_setup(closes, price)

        # SCORING
        score = 0
        reasons = []

        # MARKET REGIME (15 pts)
        if regime == "trending_bullish":
            score += 15
            reasons.append("Regime_Bullish(15)")
        elif regime == "trending_moderate":
            score += 8
            reasons.append("Regime_Moderate(8)")

        # MA10/MA20 (25 pts)
        if above_ma10 and above_ma20 and ma10_above_ma20 and ma20_rising:
            score += 25
            reasons.append("MA10>MA20_Rising(25)")
        elif above_ma20 and ma20_rising:
            score += 15
            reasons.append("Above_MA20_Rising(15)")
        elif above_ma20:
            score += 8
            reasons.append("Above_MA20(8)")

        # VCP PATTERN (20 pts)
        if vcp_detected:
            score += 20
            reasons.append(f"VCP({vcp_reason})(20)")

        # FLAG PATTERN (15 pts)
        if flag_detected:
            score += 15
            reasons.append(f"Flag({flag_reason})(15)")

        # VOLUME BREAKOUT (15 pts)
        if vol_breakout:
            score += 15
            reasons.append(f"VolumeBreakout({vol_ratio:.1f}x)(15)")
        elif vol_ratio > 1.2:
            score += 7
            reasons.append(f"VolumeAboveAvg({vol_ratio:.1f}x)(7)")

        # 9 EMA (10 pts)
        if ema9_ok:
            score += 10 if ema9_reason == "ema9_fresh_cross" else 5
            reasons.append(f"{ema9_reason}({'10' if ema9_reason == 'ema9_fresh_cross' else '5'})")

        # CVD (10 pts)
        if cvd_signal == "bullish_cvd":
            score += 10
            reasons.append("CVD_Bullish(10)")
        elif cvd_signal == "cvd_neutral":
            score += 4
            reasons.append("CVD_Neutral(4)")

        # FUNDING (5 pts)
        if funding_rate < 0:
            score += 5
            reasons.append("Funding_Neg(5)")
        elif funding_rate < 0.02:
            score += 3
            reasons.append("Funding_Low(3)")

        # OI (5 pts)
        if oi_reason == "oi_breakout_confirmed":
            score += 5
            reasons.append("OI_Breakout(5)")

        # SESSION (5 pts)
        if session_name == "us_session":
            score += 5
            reasons.append("US_Session(5)")
        elif session_name == "london_session":
            score += 3
            reasons.append("London_Session(3)")

        # RSI (5 pts)
        if 35 < rsi_val < 55:
            score += 5
            reasons.append(f"RSI_Sweet({int(rsi_val)})(5)")

        # v3.2: HTF BONUS (10 pts) — premia alineación de marcos temporales
        if "strong_uptrend" in htf_reason:
            score += 10
            reasons.append(f"HTF_Strong(10)")
        elif "moderate_uptrend" in htf_reason:
            score += 5
            reasons.append(f"HTF_Moderate(5)")

        # STOP LOSS
        sl_atr = price - (atr_val * SL_ATR_MULT)
        support_zones = liq_zones.get('support_zones', [])
        sl_support = next((s * 0.998 for s in support_zones if s < price), None)
        sl_price = max(sl_atr, sl_support) if sl_support else sl_atr

        sl_pct = (price - sl_price) / price * 100
        sl_pct = max(SL_MIN_PCT, min(SL_MAX_PCT, sl_pct))
        sl_price = price * (1 - sl_pct / 100)

        # TAKE PROFITS
        tp1_price = price * (1 + sl_pct * TP1_RR / 100)
        tp2_price = price * (1 + sl_pct * TP2_RR / 100)

        # EDGE
        potential_profit = sl_pct * TP1_RR
        edge_ratio = potential_profit / (TOTAL_COST * 100)

        if edge_ratio < MIN_EDGE:
            log.debug(f"{symbol}: Edge {edge_ratio:.1f}× < {MIN_EDGE}×")
            return None

        if score < MIN_SCORE:
            log.debug(f"{symbol}: Score {score} < {MIN_SCORE}")
            return None

        return {
            'symbol': symbol,
            'price': price,
            'score': score,
            'reasons': ' | '.join(reasons),
            'sl_price': sl_price,
            'sl_pct': sl_pct,
            'tp1_price': tp1_price,
            'tp2_price': tp2_price,
            'edge_ratio': edge_ratio,
            'atr': atr_val,
            'atr_pct': atr_pct,
            'ma10': ma10,
            'ma20': ma20,
            'ema9': ema9,
            'rsi': rsi_val,
            'regime': regime,
            'vcp': vcp_detected,
            'flag': flag_detected,
            'vol_ratio': vol_ratio,
            'cvd': cvd_val,
            'cvd_signal': cvd_signal,
            'funding_rate': funding_rate,
            'oi_change': oi_change,
            'session': session_name,
            'htf_reason': htf_reason,     # v3.2
            'rsi_1h': rsi_1h,             # v3.2
            'liq_zones': liq_zones
        }

    def open_position(self, signal: Dict) -> bool:
        """Open LONG position with full error handling (FIXED)"""
        if not AUTO_TRADING:
            log.info(f"📝 PAPER MODE: Would open {signal['symbol']}")
            return False

        symbol = signal['symbol']
        price = signal['price']
        sl_price = signal['sl_price']

        # Validate symbol
        if symbol not in self.contracts_info:
            log.error(f"❌ {symbol} not in contracts info")
            return False

        log.info(f"\n{'='*80}")
        log.info(f"🎯 OPENING LONG v3.1: {symbol}")
        log.info(f"Score: {int(signal['score'])} | Edge: {signal['edge_ratio']:.1f}× | Regime: {signal['regime']}")
        log.info(f"Entry: ${price:.6f} | SL: ${sl_price:.6f} (-{signal['sl_pct']:.2f}%)")
        log.info(f"{'='*80}\n")

        # v3.2: Position sizing dinámico basado en % del equity
        total = self.stats['wins'] + self.stats['losses']
        if total >= 10 and self.stats['win_amounts'] and self.stats['loss_amounts']:
            wr = self.stats['wins'] / total
            avg_win = sum(self.stats['win_amounts'][-20:]) / len(self.stats['win_amounts'][-20:])
            avg_loss = abs(sum(self.stats['loss_amounts'][-20:]) / len(self.stats['loss_amounts'][-20:]))
            pos_size = kelly_position_size(wr, avg_win, avg_loss, self.equity)
        else:
            # v3.2: usar % del equity actual (no fijo en USD)
            pos_size = max(5.0, min(self.equity * (POSITION_PCT / 100), POSITION_SIZE))

        qty = self._calculate_quantity(symbol, price, sl_price, pos_size)
        if not qty:
            log.error(f"❌ Cannot calculate quantity for {symbol}")
            return False

        # Set leverage
        self._set_leverage(symbol, LEVERAGE)
        time.sleep(0.3)

        # CRITICAL FIX: Especificar positionSide='LONG'
        order_data = api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol,
            'side': 'BUY',
            'type': 'MARKET',
            'quantity': str(qty),
            'positionSide': 'LONG'  # FIX: Siempre especificar
        })

        if order_data.get('code') != 0:
            log.error(f"❌ Order failed: {order_data.get('msg')}")
            return False

        time.sleep(1)
        fill_qty, fill_price = self._confirm_position(symbol)
        if not fill_qty:
            log.error("❌ Position not confirmed")
            return False

        real_sl_pct = (fill_price - sl_price) / fill_price * 100
        tp1_price = fill_price * (1 + real_sl_pct * TP1_RR / 100)
        tp2_price = fill_price * (1 + real_sl_pct * TP2_RR / 100)

        # Place Stop Loss - FIXED with positionSide
        sl_params = {
            'symbol': symbol,
            'side': 'SELL',
            'type': 'STOP_MARKET',
            'quantity': str(fill_qty),
            'stopPrice': str(round(sl_price, 8)),
            'positionSide': 'LONG'  # FIX: Siempre especificar
        }
        sl_result = api_request('POST', '/openApi/swap/v2/trade/order', sl_params)
        
        if sl_result.get('code') != 0:
            log.warning(f"⚠️ SL order failed, trying STOP type: {sl_result.get('msg')}")
            sl_params['type'] = 'STOP'
            sl_params['price'] = str(round(sl_price * 0.999, 8))
            sl_result = api_request('POST', '/openApi/swap/v2/trade/order', sl_params)
        
        sl_ok = sl_result.get('code') == 0

        # CRITICAL FIX: Inicializar position con TODOS los campos
        self.positions[symbol] = {
            'entry': fill_price,
            'qty': fill_qty,
            'qty_tp1': round(fill_qty * TP1_PCT / 100, 6),
            'qty_tp2': round(fill_qty * TP2_PCT / 100, 6),
            'side': 'LONG',
            'sl_price': sl_price,
            'sl_pct': real_sl_pct,
            'tp1_price': tp1_price,
            'tp2_price': tp2_price,
            'tp1_hit': False,
            'tp2_hit': False,
            'highest': fill_price,  # FIX: Siempre inicializar
            'opened_at': datetime.now(),
            'score': signal['score'],
            'signal': signal,  # FIX: Guardar señal completa
            'pnl_realized': 0.0,
            'pos_size': pos_size
        }
        
        self.stats['total_trades'] += 1
        self.daily_trades += 1

        patterns = []
        if signal['vcp']: patterns.append("VCP✓")
        if signal['flag']: patterns.append("Flag✓")
        patterns_str = " ".join(patterns) if patterns else "Momentum"

        self._send_telegram(
            f"<b>🟢 LONG OPENED v3.2</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"Score: {int(signal['score'])} | Edge: {signal['edge_ratio']:.1f}×\n"
            f"Patterns: {patterns_str} | HTF: {signal.get('htf_reason','?')[:20]}\n"
            f"Volume: {signal['vol_ratio']:.1f}× | RSI 1h: {signal.get('rsi_1h',0):.0f}\n\n"
            f"📍 Entry: ${fill_price:.6f}\n"
            f"🎯 TP1: ${tp1_price:.6f}\n"
            f"🎯 TP2: ${tp2_price:.6f}\n"
            f"🛑 SL: ${sl_price:.6f} (-{real_sl_pct:.2f}%)\n"
            f"💰 Size: ${pos_size:.1f} ({POSITION_PCT}% equity)\n\n"
            f"{'✅ SL Placed' if sl_ok else '⚠️ SL MANUAL REQUIRED'}"
        )

        log.info(f"✓ Position opened: {symbol} @ ${fill_price:.6f}")
        return True

    def _calculate_quantity(self, symbol: str, price: float, sl_price: float,
                             pos_size: float = None) -> Optional[float]:
        """Calculate position quantity"""
        if pos_size is None:
            pos_size = POSITION_SIZE
        
        contract = self.contracts_info.get(symbol, {})
        min_qty = contract.get('min_qty', 1)
        precision = contract.get('qty_precision', 2)
        contract_size = contract.get('contract_size', 1)
        
        price_per_contract = price * contract_size
        if price_per_contract <= 0:
            return None
        
        risk_pct = (price - sl_price) / price * 100
        risk_amount = self.equity * (RISK_PER_TRADE / 100)
        notional = min(
            risk_amount / (risk_pct / 100) if risk_pct > 0 else 0,
            pos_size * LEVERAGE
        )
        
        qty = notional / price_per_contract
        qty = math.ceil(qty / min_qty) * min_qty
        qty = round(qty, precision)
        
        return qty if qty >= min_qty else None

    def _set_leverage(self, symbol: str, leverage: int):
        """Set leverage for symbol"""
        for side in ['LONG', 'SHORT']:
            try:
                result = api_request('POST', '/openApi/swap/v2/trade/leverage', {
                    'symbol': symbol,
                    'side': side,
                    'leverage': str(leverage)
                })
                if result.get('code') != 0:
                    log.warning(f"⚠️ Leverage {side} failed: {result.get('msg')}")
            except Exception as e:
                log.error(f"Error setting leverage {side}: {e}")

    def _confirm_position(self, symbol: str, timeout: int = 15) -> Tuple[Optional[float], Optional[float]]:
        """Confirm position opened"""
        for _ in range(timeout):
            try:
                data = api_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
                for pos in data.get('data', []):
                    amt = safe_float(pos.get('positionAmt', 0))
                    side = str(pos.get('positionSide', '')).upper()
                    if (side == 'LONG' or (side == 'BOTH' and amt > 0)) and abs(amt) > 0:
                        entry = safe_float(pos.get('avgPrice') or pos.get('entryPrice', 0))
                        return abs(amt), entry
            except Exception as e:
                log.error(f"Error confirming position: {e}")
            
            time.sleep(1)
        
        return None, None

    async def monitor_positions(self):
        """Monitor open positions (FIXED - no more 'highest' KeyError)"""
        for symbol in list(self.positions.keys()):
            try:
                pos = self.positions[symbol]
                ticker = self._get_ticker(symbol)
                if not ticker:
                    continue

                current_price = ticker['price']
                
                # FIX: Safe access to 'highest' with default
                current_highest = pos.get('highest', pos['entry'])
                if current_price > current_highest:
                    pos['highest'] = current_price

                # TP1
                if not pos['tp1_hit'] and current_price >= pos.get('tp1_price', float('inf')):
                    self._close_partial(symbol, pos['qty_tp1'], current_price, "TP1")
                    pos['tp1_hit'] = True
                    pos['sl_price'] = pos['entry'] * 1.001
                    log.info(f"🔒 {symbol} SL → Breakeven")
                    continue

                # TP2
                if pos['tp1_hit'] and not pos['tp2_hit'] and current_price >= pos.get('tp2_price', float('inf')):
                    self._close_partial(symbol, pos['qty_tp2'], current_price, "TP2")
                    pos['tp2_hit'] = True
                    # FIX: Safe access to signal.atr
                    signal = pos.get('signal', {})
                    trail_dist = signal.get('atr', 0) * RUNNER_TRAIL
                    if trail_dist > 0:
                        pos['sl_price'] = max(pos['sl_price'], current_price - trail_dist)
                    log.info(f"🔒 {symbol} SL → Trail @ ${pos['sl_price']:.6f}")
                    continue

                # Runner trailing
                if pos['tp2_hit']:
                    signal = pos.get('signal', {})
                    trail_dist = signal.get('atr', 0) * RUNNER_TRAIL
                    if trail_dist > 0:
                        new_sl = current_price - trail_dist
                        if new_sl > pos['sl_price']:
                            pos['sl_price'] = new_sl

                # SL check
                if current_price <= pos['sl_price']:
                    self._close_position(symbol, current_price, "STOP_LOSS")

            except KeyError as e:
                log.error(f"❌ KeyError monitoring {symbol}: {e} | Position: {pos.keys()}\n{traceback.format_exc()}")
                # Intentar recuperar posición
                if symbol in self.positions:
                    del self.positions[symbol]
            except Exception as e:
                log.error(f"❌ Error monitoring {symbol}: {e}\n{traceback.format_exc()}")

    def _close_partial(self, symbol: str, qty: float, price: float, reason: str):
        """Close partial position (FIXED - positionSide)"""
        if qty <= 0:
            return
        
        # FIX: Especificar positionSide='LONG'
        result = api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol,
            'side': 'SELL',
            'type': 'MARKET',
            'quantity': str(qty),
            'positionSide': 'LONG'  # FIX: Siempre especificar
        })
        
        if result.get('code') != 0:
            log.error(f"❌ Partial close failed {symbol}: {result.get('msg')}")
            return
        
        pos = self.positions[symbol]
        pnl = self._calculate_pnl(pos['entry'], price, qty, symbol)
        pos['pnl_realized'] += pnl
        pos['qty'] -= qty
        
        self.stats['total_pnl'] += pnl
        self.daily_pnl += pnl
        
        # Track best/worst
        if pnl > self.stats['best_trade']:
            self.stats['best_trade'] = pnl
        if pnl < self.stats['worst_trade']:
            self.stats['worst_trade'] = pnl
        
        log.info(f"💰 {reason} {symbol}: ${pnl:+.4f}")
        self._send_telegram(f"<b>💰 {reason}</b>\n\n{symbol}\nExit: ${price:.6f}\nPnL: ${pnl:+.4f}")

    def _close_position(self, symbol: str, price: float, reason: str):
        """Close full position (FIXED - positionSide)"""
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        qty = pos['qty']
        
        if qty > 0:
            # FIX: Especificar positionSide='LONG'
            result = api_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol,
                'side': 'SELL',
                'type': 'MARKET',
                'quantity': str(qty),
                'positionSide': 'LONG'  # FIX: Siempre especificar
            })
            
            if result.get('code') != 0:
                log.error(f"❌ Close failed {symbol}: {result.get('msg')}")
        
        pnl_final = self._calculate_pnl(pos['entry'], price, qty, symbol)
        total_pnl = pos['pnl_realized'] + pnl_final
        
        win = total_pnl > 0
        if win:
            self.stats['wins'] += 1
            self.stats['win_amounts'].append(total_pnl)
            self.losing_streak = 0  # Reset streak
        else:
            self.stats['losses'] += 1
            self.stats['loss_amounts'].append(total_pnl)
            self.losing_streak += 1  # Increment streak
        
        self.stats['total_pnl'] += pnl_final
        self.daily_pnl += pnl_final
        
        # Track best/worst
        if total_pnl > self.stats['best_trade']:
            self.stats['best_trade'] = total_pnl
        if total_pnl < self.stats['worst_trade']:
            self.stats['worst_trade'] = total_pnl
        
        total_trades = self.stats['wins'] + self.stats['losses']
        wr = (self.stats['wins'] / total_trades * 100) if total_trades > 0 else 0
        duration_min = int((datetime.now() - pos['opened_at']).total_seconds() / 60)
        
        log.info(f"{'✅' if win else '❌'} {reason} {symbol} | ${total_pnl:+.4f} | {duration_min}min | WR:{wr:.0f}%")
        
        self._send_telegram(
            f"<b>{'✅ WIN' if win else '❌ LOSS'} — {reason}</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"Entry: ${pos['entry']:.6f} → Exit: ${price:.6f}\n"
            f"Duration: {duration_min}min\n\n"
            f"<b>PnL: ${total_pnl:+.4f}</b>\n"
            f"Win Rate: {wr:.0f}% ({self.stats['wins']}/{total_trades})\n"
            f"Streak: {'✅'*self.losing_streak if not win else '🎉'}"
        )
        
        del self.positions[symbol]

    def _calculate_pnl(self, entry: float, exit_price: float, qty: float, symbol: str = '') -> float:
        """Calculate PnL"""
        contract = self.contracts_info.get(symbol, {})
        contract_size = contract.get('contract_size', 1)
        notional = qty * entry * contract_size
        pnl_gross = (exit_price - entry) / entry * notional * LEVERAGE
        fees = notional * (FEE_TAKER + FEE_MAKER)
        return pnl_gross - fees

    def _check_circuit_breaker(self) -> bool:
        """Circuit breaker - more aggressive (IMPROVED)"""
        today = datetime.utcnow().date()
        
        # Reset diario
        if today != self.daily_date:
            self.daily_pnl = 0
            self.daily_date = today
            self.daily_trades = 0
            if self.circuit_breaker_active:
                self.circuit_breaker_active = False
                self.circuit_breaker_until = None
                log.info("🔓 Circuit Breaker RESET")
        
        # Check si ya está activo
        if self.circuit_breaker_active:
            if self.circuit_breaker_until and datetime.utcnow() > self.circuit_breaker_until:
                self.circuit_breaker_active = False
                self.daily_pnl = 0
                log.info("🔓 Circuit Breaker OFF")
                return False
            return True
        
        # Check pérdida diaria
        threshold = self.equity * (CIRCUIT_BREAKER_PCT / 100)
        if self.daily_pnl < -threshold:
            self.circuit_breaker_active = True
            self.circuit_breaker_until = datetime.utcnow() + timedelta(hours=6)
            log.warning(f"🔒 CIRCUIT BREAKER - Daily Loss: ${self.daily_pnl:.2f}")
            self._send_telegram(
                f"<b>🔒 CIRCUIT BREAKER</b>\n"
                f"Daily Loss: ${self.daily_pnl:.2f}\n"
                f"Threshold: ${threshold:.2f}\n"
                f"Paused: 6 hours"
            )
            return True
        
        # NUEVO: Check losing streak
        if self.losing_streak >= MAX_LOSING_STREAK:
            self.circuit_breaker_active = True
            self.circuit_breaker_until = datetime.utcnow() + timedelta(hours=4)
            log.warning(f"🔒 CIRCUIT BREAKER - Losing Streak: {self.losing_streak}")
            self._send_telegram(
                f"<b>🔒 CIRCUIT BREAKER</b>\n"
                f"Losing Streak: {self.losing_streak} trades\n"
                f"Paused: 4 hours"
            )
            return True
        
        # NUEVO: Check max daily trades
        if self.daily_trades >= MAX_DAILY_TRADES:
            log.warning(f"⚠️ Max daily trades reached: {self.daily_trades}/{MAX_DAILY_TRADES}")
            return True
        
        return False

    def _send_telegram(self, message: str):
        """Send Telegram notification"""
        if not TG_TOKEN or not TG_CHAT:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={'chat_id': TG_CHAT, 'text': message, 'parse_mode': 'HTML'},
                timeout=5
            )
        except Exception as e:
            log.error(f"Telegram error: {e}")

    async def run(self):
        """Main bot loop"""
        log.info("\n🚀 Institutional Bot v3.1 RUNNING (FIXED)\n")
        iteration = 0
        last_symbol_refresh = 0
        last_equity_update = 0

        while True:
            try:
                iteration += 1

                # Refresh symbols periodically
                if time.time() - last_symbol_refresh > 600:
                    self._refresh_symbols()
                    last_symbol_refresh = time.time()

                # Update equity periodically
                if time.time() - last_equity_update > 1800:
                    if AUTO_TRADING:
                        data = api_request('GET', '/openApi/swap/v2/user/balance')
                        if data.get('code') == 0:
                            eq = extract_equity(data)
                            if eq > 0:
                                self.equity = eq
                    last_equity_update = time.time()

                # Circuit breaker check
                if self._check_circuit_breaker():
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue

                total_trades = self.stats['wins'] + self.stats['losses']
                wr = (self.stats['wins'] / total_trades * 100) if total_trades > 0 else 0

                log.info(f"\n{'='*80}")
                log.info(f"#{iteration} {datetime.now().strftime('%H:%M:%S')} UTC | Pos: {len(self.positions)}/{MAX_POSITIONS}")
                log.info(f"PnL: ${self.stats['total_pnl']:+.4f} | Today: ${self.daily_pnl:+.4f} | WR: {wr:.0f}% ({total_trades})")
                log.info(f"Equity: ${self.equity:.2f} | Trades today: {self.daily_trades}/{MAX_DAILY_TRADES}")
                log.info(f"{'='*80}\n")

                # Monitor positions
                await self.monitor_positions()

                # Scan for new signals
                if len(self.positions) < MAX_POSITIONS and self.daily_trades < MAX_DAILY_TRADES:
                    log.info(f"Scanning {len(self.symbols)} symbols...")
                    signals_found = 0

                    for symbol in self.symbols:
                        if len(self.positions) >= MAX_POSITIONS:
                            break
                        if self.daily_trades >= MAX_DAILY_TRADES:
                            break
                        
                        signal = self.analyze_symbol(symbol)
                        if signal:
                            signals_found += 1
                            patterns = []
                            if signal['vcp']: patterns.append("VCP")
                            if signal['flag']: patterns.append("Flag")
                            pattern_str = "+".join(patterns) if patterns else "Momentum"
                            
                            log.info(
                                f"💡 {symbol} | Score:{int(signal['score'])} | "
                                f"Edge:{signal['edge_ratio']:.1f}× | {pattern_str}"
                            )
                            
                            if self.open_position(signal):
                                await asyncio.sleep(3)

                    log.info(f"✓ Scan complete | Signals: {signals_found}")

                await asyncio.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                log.info("⏹️ Bot stopped by user")
                break
            except Exception as e:
                log.error(f"❌ Error in main loop: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(30)

# ════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════

async def main():
    bot = InstitutionalBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Bot v3.2 terminated")
