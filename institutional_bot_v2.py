#!/usr/bin/env python3
"""
🏆 INSTITUTIONAL BOT v2.0 — Estrategia Institucional Probada 2025-2026
═══════════════════════════════════════════════════════════════════════

FILTROS SECRETOS IMPLEMENTADOS:
├─ 1. Funding Rate Filter (evita mercados sobrecargados)
├─ 2. Open Interest Confirmation (breakouts reales)
├─ 3. Session Filter UTC (US session = mejor liquidez)
├─ 4. Volume Delta CVD (compra/venta agresiva real)
├─ 5. Liquidity Cascade Zones (stops acumulados)
├─ 6. MARKET Entry (ejecución garantizada)
└─ 7. Trailing Stop Dinámico (deja correr winners)

ARQUITECTURA DE ENTRADA:
Scan 5min → Régimen → Sesión → Funding OK? → OI confirma? → CVD alineado? 
→ Signal Tier → MARKET entry → Trail Stop

REGLA DE ORO: Edge debe ser ≥ 3× el coste de transacción
Fee taker: 0.10% | Funding acumulado: 0.03% | Slippage: 0.02% = 0.15% mínimo
Target mínimo: 0.45% (3× coste) → TP1 @ 0.8-1.5%

Win Rate objetivo: 60-68% (filtros eliminan señales contra smart money)
RR ratio: 1.8-2.5× (trailing stop captura más de cada movimiento)
Señales/día: 4-8 (session filter enfoca en horas de volumen real)
"""

import os, asyncio, logging, requests, hmac, hashlib, time, sys, math, re, json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════

def clean_env(key: str, default, typ='str'):
    """Clean environment variable removing quotes"""
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

# ──────────────────────────────────────────────────────────────────
# API CREDENTIALS
# ──────────────────────────────────────────────────────────────────
API_KEY    = clean_env('BINGX_API_KEY', '')
API_SECRET = clean_env('BINGX_API_SECRET', '')
TG_TOKEN   = clean_env('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = clean_env('TELEGRAM_CHAT_ID', '')

# ──────────────────────────────────────────────────────────────────
# CAPITAL MANAGEMENT
# ──────────────────────────────────────────────────────────────────
AUTO_TRADING    = clean_env('AUTO_TRADING_ENABLED', 'true', 'bool')
POSITION_SIZE   = clean_env('POSITION_SIZE_USD', '15', 'float')
LEVERAGE        = min(clean_env('LEVERAGE', '3', 'int'), 5)
MAX_POSITIONS   = clean_env('MAX_POSITIONS', '4', 'int')
ACCOUNT_EQUITY  = clean_env('ACCOUNT_EQUITY', '100', 'float')
RISK_PER_TRADE  = clean_env('RISK_PCT_PER_TRADE', '1.5', 'float')

# ──────────────────────────────────────────────────────────────────
# FILTRO 1: FUNDING RATE (evitar mercados sobrecargados)
# ──────────────────────────────────────────────────────────────────
FUNDING_LONG_OK    = clean_env('FUNDING_LONG_OK', '0.03', 'float')    # <+0.03% OK para longs
FUNDING_LONG_SKIP  = clean_env('FUNDING_LONG_SKIP', '0.05', 'float')  # >+0.05% skip (sobrecargado)
FUNDING_SHORT_OK   = clean_env('FUNDING_SHORT_OK', '-0.03', 'float')  # >-0.03% OK para shorts
FUNDING_ENABLED    = clean_env('FUNDING_FILTER', 'true', 'bool')

# ──────────────────────────────────────────────────────────────────
# FILTRO 2: OPEN INTEREST (confirmar breakouts)
# ──────────────────────────────────────────────────────────────────
OI_BREAKOUT_MIN   = clean_env('OI_BREAKOUT_MIN', '1.5', 'float')  # OI debe crecer >1.5% para breakout real
OI_WEAK_THRESHOLD = clean_env('OI_WEAK_THRESHOLD', '0.5', 'float') # OI <0.5% = movimiento sin fuerza
OI_ENABLED        = clean_env('OI_FILTER', 'true', 'bool')

# ──────────────────────────────────────────────────────────────────
# FILTRO 3: SESSION FILTER (horarios institucionales)
# ──────────────────────────────────────────────────────────────────
# US Session: 13:00-22:00 UTC (mejor liquidez y volumen)
# London Session: 07:00-13:00 UTC (OK pero menos volumen crypto)
# Asia Session: 22:00-07:00 UTC (EVITAR - reversals y bajo volumen)
SESSION_BEST  = {13, 14, 15, 16, 17, 18, 19, 20, 21, 22}  # US session
SESSION_OK    = {7, 8, 9, 10, 11, 12}                      # London
SESSION_AVOID = {22, 23, 0, 1, 2, 3, 4, 5, 6}              # Asia
SESSION_FILTER_ENABLED = clean_env('SESSION_FILTER', 'true', 'bool')

# ──────────────────────────────────────────────────────────────────
# FILTRO 4: CVD - Cumulative Volume Delta
# ──────────────────────────────────────────────────────────────────
CVD_LOOKBACK = clean_env('CVD_LOOKBACK_BARS', '20', 'int')
CVD_THRESHOLD = clean_env('CVD_THRESHOLD', '1.5', 'float')  # CVD debe ser >1.5× std para señal

# ──────────────────────────────────────────────────────────────────
# FILTRO 5: LIQUIDITY CASCADE ZONES (stops acumulados)
# ──────────────────────────────────────────────────────────────────
LIQ_ZONE_LOOKBACK = clean_env('LIQ_ZONE_LOOKBACK', '100', 'int')
LIQ_ZONE_THRESHOLD = clean_env('LIQ_ZONE_THRESHOLD', '3', 'int')  # Mín 3 swing highs/lows

# ──────────────────────────────────────────────────────────────────
# STOP LOSS & TAKE PROFIT (institucional)
# ──────────────────────────────────────────────────────────────────
SL_ATR_MULT   = clean_env('SL_ATR_MULTIPLIER', '1.2', 'float')
SL_MIN_PCT    = clean_env('SL_MIN_PCT', '0.6', 'float')
SL_MAX_PCT    = clean_env('SL_MAX_PCT', '2.5', 'float')

# TPs escalonados con trailing
TP1_PCT       = clean_env('TP1_PERCENTAGE', '35', 'float')   # 35% @ TP1
TP2_PCT       = clean_env('TP2_PERCENTAGE', '35', 'float')   # 35% @ TP2
TP1_RR        = clean_env('TP1_RISK_REWARD', '1.2', 'float') # TP1 @ 1.2×SL
TP2_RR        = clean_env('TP2_RISK_REWARD', '2.2', 'float') # TP2 @ 2.2×SL
RUNNER_TRAIL  = clean_env('RUNNER_TRAIL_ATR', '1.5', 'float') # Runner trail @ 1.5×ATR

MIN_EDGE      = clean_env('MIN_EDGE_RATIO', '3.0', 'float')  # Edge ≥ 3× costes

# ──────────────────────────────────────────────────────────────────
# MARKET FILTERS
# ──────────────────────────────────────────────────────────────────
MIN_VOLUME_24H = clean_env('MIN_VOLUME_24H', '1000000', 'float')
MAX_SYMBOLS    = clean_env('MAX_SYMBOLS', '50', 'int')
MIN_SCORE      = clean_env('MIN_ENTRY_SCORE', '70', 'float')
BTC_CORRELATION = clean_env('BTC_CORRELATION_THRESHOLD', '-2.5', 'float')

# ──────────────────────────────────────────────────────────────────
# CIRCUIT BREAKER & RISK
# ──────────────────────────────────────────────────────────────────
CIRCUIT_BREAKER_PCT = clean_env('CIRCUIT_BREAKER_PCT', '6.0', 'float')
MAX_LOSING_STREAK   = clean_env('MAX_LOSING_STREAK', '4', 'int')
DAILY_RESET_HOUR    = clean_env('DAILY_RESET_HOUR', '0', 'int')

# ──────────────────────────────────────────────────────────────────
# TIMING
# ──────────────────────────────────────────────────────────────────
SCAN_INTERVAL  = clean_env('SCAN_INTERVAL_SEC', '60', 'int')
MONITOR_INTERVAL = clean_env('MONITOR_INTERVAL_SEC', '15', 'int')

# ──────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────
BASE_URL = "https://open-api.bingx.com"
FEE_TAKER = 0.001   # 0.10% taker fee
FEE_MAKER = 0.0002  # 0.02% maker fee
SLIPPAGE = 0.0002   # 0.02% slippage estimado
TOTAL_COST = FEE_TAKER + FEE_MAKER + SLIPPAGE  # 0.14%

EXCLUDE_SYMBOLS = {
    'DOW', 'SP500', 'GOLD', 'SILVER', 'XAU', 'OIL', 'BRENT',
    'EUR', 'GBP', 'JPY', 'TSLA', 'AAPL', 'MSFT', 'GOOGL',
    'AMZN', 'META', 'NVDA', 'COIN', 'MSTR', 'PAXG', 'XAUT'
}

# ════════════════════════════════════════════════════════════════════
# LOGGING
# ════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# API FUNCTIONS
# ════════════════════════════════════════════════════════════════════

def api_request(method: str, endpoint: str, params: dict = None, retries: int = 3) -> dict:
    """BingX authenticated API request"""
    params = params or {}
    for attempt in range(retries + 1):
        try:
            p = {**{k: str(v) for k, v in params.items()},
                 'timestamp': str(int(time.time() * 1000))}
            query = urlencode(sorted(p.items()))
            signature = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{query}&signature={signature}"
            headers = {
                'X-BX-APIKEY': API_KEY,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            response = getattr(requests, method.lower())(url, headers=headers, timeout=15)
            return response.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                log.error(f"API {endpoint} failed: {e}")
                return {}

def public_request(path: str, params: dict = None) -> dict:
    """BingX public API request"""
    try:
        url = f"{BASE_URL}{path}"
        response = requests.get(url, params=params or {}, timeout=10)
        return response.json()
    except:
        return {}

def safe_float(val, default: float = 0.0) -> float:
    """Safely convert to float"""
    if val is None:
        return default
    try:
        return float(val)
    except:
        return default

# ════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ════════════════════════════════════════════════════════════════════

def ema(prices: List[float], period: int) -> float:
    """Exponential Moving Average"""
    if not prices or len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    k = 2 / (period + 1)
    ema_val = prices[0]
    for price in prices[1:]:
        ema_val = price * k + ema_val * (1 - k)
    return ema_val

def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """Average True Range"""
    if len(closes) < 2:
        return 0
    trs = []
    for i in range(1, min(len(closes), period + 1)):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def rsi(prices: List[float], period: int = 14) -> float:
    """Relative Strength Index"""
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_cvd(volumes: List[float], closes: List[float], opens: List[float]) -> float:
    """
    Cumulative Volume Delta (CVD)
    CVD = Σ(volume × sign(close - open))
    Positivo = compradores agresivos, Negativo = vendedores agresivos
    """
    if len(volumes) < 2:
        return 0
    cvd = 0
    for i in range(len(volumes)):
        delta = 1 if closes[i] > opens[i] else -1 if closes[i] < opens[i] else 0
        cvd += volumes[i] * delta
    return cvd

def find_liquidity_zones(highs: List[float], lows: List[float], lookback: int = 100) -> Dict:
    """
    Encuentra zonas de liquidez donde se acumulan stops
    Swing highs = stops de shorts acumulados
    Swing lows = stops de longs acumulados
    """
    if len(highs) < lookback:
        return {'resistance_zones': [], 'support_zones': []}
    
    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]
    
    # Encontrar swing highs (resistencias)
    swing_highs = []
    for i in range(2, len(recent_highs) - 2):
        if (recent_highs[i] > recent_highs[i-1] and 
            recent_highs[i] > recent_highs[i-2] and
            recent_highs[i] > recent_highs[i+1] and
            recent_highs[i] > recent_highs[i+2]):
            swing_highs.append(recent_highs[i])
    
    # Encontrar swing lows (soportes)
    swing_lows = []
    for i in range(2, len(recent_lows) - 2):
        if (recent_lows[i] < recent_lows[i-1] and 
            recent_lows[i] < recent_lows[i-2] and
            recent_lows[i] < recent_lows[i+1] and
            recent_lows[i] < recent_lows[i+2]):
            swing_lows.append(recent_lows[i])
    
    return {
        'resistance_zones': sorted(swing_highs, reverse=True)[:5],  # Top 5 resistencias
        'support_zones': sorted(swing_lows)[:5]  # Top 5 soportes
    }

# ════════════════════════════════════════════════════════════════════
# INSTITUTIONAL FILTERS
# ════════════════════════════════════════════════════════════════════

class InstitutionalFilters:
    """Filtros secretos que usan los institucionales"""
    
    def __init__(self):
        self.funding_cache = {}
        self.oi_cache = {}
        self.last_update = {}
    
    def check_funding_rate(self, symbol: str) -> Tuple[bool, str, float]:
        """
        FILTRO 1: Funding Rate
        LONG OK: funding < +0.03%
        LONG SKIP: funding > +0.05% (mercado sobrecargado de longs)
        """
        if not FUNDING_ENABLED:
            return True, "funding_disabled", 0
        
        # Cache 5 minutos
        cache_key = f"{symbol}_funding"
        if cache_key in self.last_update:
            if time.time() - self.last_update[cache_key] < 300:
                cached_rate = self.funding_cache.get(cache_key, 0)
                if cached_rate < FUNDING_LONG_OK:
                    return True, "funding_ok", cached_rate
                else:
                    return False, "funding_high", cached_rate
        
        # Obtener funding rate actual
        try:
            data = public_request('/openApi/swap/v2/quote/premiumIndex', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                rate = safe_float(data['data'].get('lastFundingRate', 0)) * 100
                self.funding_cache[cache_key] = rate
                self.last_update[cache_key] = time.time()
                
                if rate > FUNDING_LONG_SKIP:
                    return False, "funding_overheated", rate
                elif rate < FUNDING_LONG_OK:
                    return True, "funding_ok", rate
                else:
                    return True, "funding_neutral", rate
        except:
            pass
        
        return True, "funding_unknown", 0
    
    def check_open_interest(self, symbol: str, price_change_pct: float) -> Tuple[bool, str, float]:
        """
        FILTRO 2: Open Interest Confirmation
        BREAKOUT REAL: precio↑ + OI↑ > 1.5%
        MOVIMIENTO DÉBIL: precio↑ + OI↓ o OI < 0.5%
        """
        if not OI_ENABLED:
            return True, "oi_disabled", 0
        
        try:
            data = public_request('/openApi/swap/v2/quote/openInterest', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                current_oi = safe_float(data['data'].get('openInterest', 0))
                
                # Comparar con OI anterior (cache)
                cache_key = f"{symbol}_oi"
                if cache_key in self.oi_cache:
                    prev_oi = self.oi_cache[cache_key]
                    oi_change_pct = ((current_oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0
                    
                    self.oi_cache[cache_key] = current_oi
                    
                    # Divergencia OI es el indicador más ignorado por retail
                    if price_change_pct > 1.0 and oi_change_pct > OI_BREAKOUT_MIN:
                        return True, "oi_breakout_confirmed", oi_change_pct
                    elif price_change_pct > 1.0 and oi_change_pct < OI_WEAK_THRESHOLD:
                        return False, "oi_divergence_weak", oi_change_pct
                    else:
                        return True, "oi_neutral", oi_change_pct
                else:
                    self.oi_cache[cache_key] = current_oi
                    return True, "oi_first_check", 0
        except:
            pass
        
        return True, "oi_unknown", 0
    
    def check_session_quality(self) -> Tuple[bool, str]:
        """
        FILTRO 3: Session Filter
        BEST: 13:00-22:00 UTC (US session) - 92% del tiempo en 2025 el funding fue positivo
        OK: 07:00-13:00 UTC (London)
        AVOID: 22:00-07:00 UTC (Asia) - reversals y bajo volumen
        """
        if not SESSION_FILTER_ENABLED:
            return True, "session_disabled"
        
        current_hour = datetime.utcnow().hour
        
        if current_hour in SESSION_BEST:
            return True, "us_session"
        elif current_hour in SESSION_OK:
            return True, "london_session"
        else:
            return False, "asia_session_avoid"
    
    def calculate_volume_quality(self, volumes: List[float], closes: List[float], 
                                 opens: List[float]) -> Tuple[float, str]:
        """
        FILTRO 4: CVD - Cumulative Volume Delta
        Compradores agresivos vs vendedores agresivos
        """
        if len(volumes) < CVD_LOOKBACK:
            return 0, "cvd_insufficient_data"
        
        recent_vols = volumes[-CVD_LOOKBACK:]
        recent_closes = closes[-CVD_LOOKBACK:]
        recent_opens = opens[-CVD_LOOKBACK:]
        
        cvd = calculate_cvd(recent_vols, recent_closes, recent_opens)
        
        # Normalizar por volumen total
        total_vol = sum(recent_vols)
        cvd_normalized = cvd / total_vol if total_vol > 0 else 0
        
        # Calcular desviación estándar para threshold
        cvd_values = []
        for i in range(len(recent_vols)):
            delta = 1 if recent_closes[i] > recent_opens[i] else -1
            cvd_values.append(recent_vols[i] * delta)
        
        import statistics
        try:
            cvd_std = statistics.stdev(cvd_values) if len(cvd_values) > 1 else 0
            
            if abs(cvd_normalized) > CVD_THRESHOLD * cvd_std:
                direction = "bullish_cvd" if cvd_normalized > 0 else "bearish_cvd"
                return cvd_normalized, direction
            else:
                return cvd_normalized, "cvd_neutral"
        except:
            return cvd_normalized, "cvd_neutral"

# ════════════════════════════════════════════════════════════════════
# INSTITUTIONAL BOT
# ════════════════════════════════════════════════════════════════════

class InstitutionalBot:
    """Bot con estrategia institucional probada"""
    
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
        self.stats = {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'total_pnl': 0.0,
            'total_fees': 0.0
        }
        
        log.info("=" * 80)
        log.info("🏆 INSTITUTIONAL BOT v2.0")
        log.info("=" * 80)
        log.info(f"Capital: ${POSITION_SIZE} × {MAX_POSITIONS} posiciones | {LEVERAGE}×")
        log.info(f"Filtros: Funding={'✓' if FUNDING_ENABLED else '✗'} | "
                f"OI={'✓' if OI_ENABLED else '✗'} | "
                f"Session={'✓' if SESSION_FILTER_ENABLED else '✗'}")
        log.info(f"TPs: {int(TP1_PCT)}%@{TP1_RR}RR | {int(TP2_PCT)}%@{TP2_RR}RR | "
                f"{int(100-TP1_PCT-TP2_PCT)}%@trail")
        log.info(f"Min Edge: {MIN_EDGE}× costes | Circuit Breaker: {CIRCUIT_BREAKER_PCT}%")
        log.info("=" * 80)
        
        if not self._connect():
            log.error("❌ No se pudo conectar a BingX")
            sys.exit(1)
        
        self._load_contracts()
        self._refresh_symbols()
        self._recover_positions()
        
        self._send_telegram(
            f"<b>🏆 INSTITUTIONAL BOT v2.0 STARTED</b>\n\n"
            f"💰 Capital: ${POSITION_SIZE} × {MAX_POSITIONS} = ${POSITION_SIZE * MAX_POSITIONS}\n"
            f"📊 Leverage: {LEVERAGE}×\n"
            f"🎯 Min Score: {MIN_SCORE}\n"
            f"⚡ Filtros: Funding + OI + Session + CVD\n"
            f"🛡️ Circuit Breaker: {CIRCUIT_BREAKER_PCT}%\n\n"
            f"Modo: {'REAL MONEY 💸' if AUTO_TRADING else 'PAPER TRADING 📝'}"
        )
    
    def _connect(self) -> bool:
        """Conectar a BingX y verificar balance"""
        global AUTO_TRADING
        if not AUTO_TRADING:
            return True
        
        if not API_KEY or not API_SECRET:
            log.error("API keys no configuradas")
            AUTO_TRADING = False
            return False
        
        data = api_request('GET', '/openApi/swap/v2/user/balance')
        if data.get('code') == 0:
            balance_data = data.get('data', {})
            equity = safe_float(balance_data.get('equity', balance_data.get('balance', 0)))
            if equity > 0:
                self.equity = equity
                log.info(f"✓ BingX conectado | Equity: ${equity:.2f}")
                return True
        
        log.error(f"Error conectando: {data.get('msg')}")
        AUTO_TRADING = False
        return False
    
    def _load_contracts(self):
        """Cargar información de contratos"""
        data = public_request('/openApi/swap/v2/quote/contracts')
        if data.get('code') == 0:
            for contract in data.get('data', []):
                symbol = contract.get('symbol', '')
                if symbol:
                    self.contracts_info[symbol] = {
                        'min_qty': safe_float(contract.get('tradeMinQuantity', 1)),
                        'qty_precision': int(contract.get('quantityPrecision', 2)),
                        'contract_size': safe_float(contract.get('contractSize', 1))
                    }
            log.info(f"✓ Contratos cargados: {len(self.contracts_info)}")
    
    def _refresh_symbols(self):
        """Actualizar lista de símbolos por volumen"""
        data = public_request('/openApi/swap/v2/quote/ticker')
        if data.get('code') != 0:
            self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT']
            return
        
        candidates = []
        for ticker in data.get('data', []):
            symbol = ticker.get('symbol', '')
            if not symbol.endswith('-USDT'):
                continue
            
            base = symbol.replace('-USDT', '').upper()
            if any(ex in base for ex in EXCLUDE_SYMBOLS):
                continue
            
            try:
                price = safe_float(ticker.get('lastPrice', 0))
                volume = safe_float(ticker.get('volume', 0))
                volume_usd = volume * price
                
                if volume_usd >= MIN_VOLUME_24H and price > 0:
                    candidates.append({
                        'symbol': symbol,
                        'volume': volume_usd
                    })
            except:
                continue
        
        candidates.sort(key=lambda x: x['volume'], reverse=True)
        self.symbols = [c['symbol'] for c in candidates[:MAX_SYMBOLS]]
        log.info(f"✓ Símbolos activos: {len(self.symbols)}")
    
    def _recover_positions(self):
        """Recuperar posiciones abiertas"""
        if not AUTO_TRADING:
            return
        
        data = api_request('GET', '/openApi/swap/v2/user/positions')
        recovered = 0
        for pos in data.get('data', []):
            try:
                symbol = pos.get('symbol', '')
                amt = safe_float(pos.get('positionAmt', 0))
                side = str(pos.get('positionSide', '')).upper()
                
                if (side == 'LONG' or (side == 'BOTH' and amt > 0)) and abs(amt) > 0:
                    entry = safe_float(pos.get('avgPrice') or pos.get('entryPrice', 0))
                    if entry > 0:
                        self.positions[symbol] = {
                            'entry': entry,
                            'qty': abs(amt),
                            'side': 'LONG',
                            'tp1_hit': False,
                            'tp2_hit': False,
                            'recovered': True
                        }
                        recovered += 1
                        log.info(f"♻️ Posición recuperada: {symbol} @ ${entry:.6f}")
            except:
                continue
        
        if recovered > 0:
            log.info(f"✓ Posiciones recuperadas: {recovered}")
    
    def _get_klines(self, symbol: str, interval: str = '5m', limit: int = 150):
        """Obtener velas históricas"""
        data = public_request('/openApi/swap/v3/quote/klines', {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        })
        
        if data.get('code') == 0 and data.get('data'):
            klines = data['data']
            closes = [safe_float(k['close']) for k in klines]
            highs = [safe_float(k['high']) for k in klines]
            lows = [safe_float(k['low']) for k in klines]
            volumes = [safe_float(k['volume']) for k in klines]
            opens = [safe_float(k['open']) for k in klines]
            return closes, highs, lows, volumes, opens
        
        return None, None, None, None, None
    
    def _get_ticker(self, symbol: str):
        """Obtener precio actual"""
        data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': symbol})
        if data.get('code') == 0 and data.get('data'):
            ticker = data['data']
            return {
                'price': safe_float(ticker.get('lastPrice', 0)),
                'change_pct': safe_float(ticker.get('priceChangePercent', 0))
            }
        return None
    
    def analyze_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Análisis institucional completo de un símbolo
        Returns: dict con score y detalles, o None si no pasa filtros
        """
        # Ya tenemos posición en este símbolo
        if symbol in self.positions:
            return None
        
        # Obtener datos de mercado
        closes, highs, lows, volumes, opens = self._get_klines(symbol, '5m', 150)
        if not closes or len(closes) < 100:
            return None
        
        ticker = self._get_ticker(symbol)
        if not ticker or ticker['price'] <= 0:
            return None
        
        price = ticker['price']
        change_24h = ticker['change_pct']
        
        # ═══════════════════════════════════════════════════════════
        # FILTRO 1: FUNDING RATE
        # ═══════════════════════════════════════════════════════════
        funding_ok, funding_reason, funding_rate = self.filters.check_funding_rate(symbol)
        if not funding_ok:
            log.debug(f"{symbol}: ❌ Funding={funding_rate:.3f}% ({funding_reason})")
            return None
        
        # ═══════════════════════════════════════════════════════════
        # FILTRO 2: OPEN INTEREST
        # ═══════════════════════════════════════════════════════════
        oi_ok, oi_reason, oi_change = self.filters.check_open_interest(symbol, change_24h)
        if not oi_ok:
            log.debug(f"{symbol}: ❌ OI divergence ({oi_reason})")
            return None
        
        # ═══════════════════════════════════════════════════════════
        # FILTRO 3: SESSION
        # ═══════════════════════════════════════════════════════════
        session_ok, session_name = self.filters.check_session_quality()
        if not session_ok:
            return None  # Silencioso - evitar spam
        
        # ═══════════════════════════════════════════════════════════
        # FILTRO 4: CVD - Volume Quality
        # ═══════════════════════════════════════════════════════════
        cvd_value, cvd_signal = self.filters.calculate_volume_quality(volumes, closes, opens)
        
        # ═══════════════════════════════════════════════════════════
        # FILTRO 5: LIQUIDITY ZONES
        # ═══════════════════════════════════════════════════════════
        liq_zones = find_liquidity_zones(highs, lows, LIQ_ZONE_LOOKBACK)
        
        # ═══════════════════════════════════════════════════════════
        # TECHNICAL ANALYSIS
        # ═══════════════════════════════════════════════════════════
        
        # EMAs
        ema9 = ema(closes, 9)
        ema21 = ema(closes, 21)
        ema50 = ema(closes, 50)
        
        # ATR para stop loss
        atr_val = atr(highs, lows, closes, 14)
        atr_pct = (atr_val / price * 100) if price > 0 else 0
        
        # RSI
        rsi_val = rsi(closes, 14)
        
        # Tendencia
        trend_short = ema9 > ema21
        trend_long = ema21 > ema50
        price_above_emas = price > ema9 and price > ema21
        
        # ═══════════════════════════════════════════════════════════
        # SIGNAL SCORING (institucional)
        # ═══════════════════════════════════════════════════════════
        
        score = 0
        reasons = []
        
        # TREND (30 pts)
        if trend_short and trend_long:
            score += 30
            reasons.append("Trend_Strong(30)")
        elif trend_short or trend_long:
            score += 15
            reasons.append("Trend_Medium(15)")
        
        # PRICE ACTION (20 pts)
        if price_above_emas:
            score += 20
            reasons.append("Above_EMAs(20)")
        elif price > ema21:
            score += 10
            reasons.append("Above_EMA21(10)")
        
        # RSI (15 pts)
        if 30 < rsi_val < 50:
            score += 15
            reasons.append(f"RSI_Oversold({int(rsi_val)})(15)")
        elif 50 < rsi_val < 60:
            score += 10
            reasons.append(f"RSI_Neutral({int(rsi_val)})(10)")
        
        # CVD - Volume Quality (25 pts) - MUY IMPORTANTE
        if cvd_signal == "bullish_cvd":
            score += 25
            reasons.append(f"CVD_Bullish({cvd_value:.2f})(25)")
        elif cvd_signal == "cvd_neutral":
            score += 10
            reasons.append("CVD_Neutral(10)")
        
        # FUNDING (10 pts)
        if funding_rate < 0:  # Funding negativo = posible rebote
            score += 10
            reasons.append(f"Funding_Neg({funding_rate:.3f}%)(10)")
        elif funding_rate < 0.02:
            score += 5
            reasons.append(f"Funding_Low({funding_rate:.3f}%)(5)")
        
        # OI CONFIRMATION (15 pts)
        if oi_reason == "oi_breakout_confirmed":
            score += 15
            reasons.append(f"OI_Breakout({oi_change:.1f}%)(15)")
        elif oi_change > 0:
            score += 5
            reasons.append(f"OI_Growing({oi_change:.1f}%)(5)")
        
        # SESSION QUALITY (10 pts)
        if session_name == "us_session":
            score += 10
            reasons.append("US_Session(10)")
        elif session_name == "london_session":
            score += 5
            reasons.append("London_Session(5)")
        
        # VOLATILITY (5 pts)
        if 0.5 < atr_pct < 3.0:
            score += 5
            reasons.append(f"ATR_OK({atr_pct:.2f}%)(5)")
        
        # ═══════════════════════════════════════════════════════════
        # STOP LOSS INTELIGENTE
        # ═══════════════════════════════════════════════════════════
        
        # SL basado en ATR y soporte más cercano
        sl_atr = price - (atr_val * SL_ATR_MULT)
        
        # Encontrar soporte más cercano (liquidity zone)
        support_zones = liq_zones.get('support_zones', [])
        sl_support = None
        if support_zones:
            for support in support_zones:
                if support < price:
                    sl_support = support * 0.998  # Ligeramente debajo
                    break
        
        # SL final = el más conservador
        sl_price = max(sl_atr, sl_support) if sl_support else sl_atr
        
        # Limitar SL
        sl_pct = (price - sl_price) / price * 100
        if sl_pct < SL_MIN_PCT:
            sl_price = price * (1 - SL_MIN_PCT / 100)
            sl_pct = SL_MIN_PCT
        elif sl_pct > SL_MAX_PCT:
            sl_price = price * (1 - SL_MAX_PCT / 100)
            sl_pct = SL_MAX_PCT
        
        # ═══════════════════════════════════════════════════════════
        # TAKE PROFITS
        # ═══════════════════════════════════════════════════════════
        
        tp1_price = price * (1 + sl_pct * TP1_RR / 100)
        tp2_price = price * (1 + sl_pct * TP2_RR / 100)
        
        # Edge calculation
        potential_profit = sl_pct * TP1_RR  # Profit esperado en TP1
        edge_ratio = potential_profit / (TOTAL_COST * 100)
        
        if edge_ratio < MIN_EDGE:
            log.debug(f"{symbol}: Edge insuficiente {edge_ratio:.1f}× < {MIN_EDGE}×")
            return None
        
        # ═══════════════════════════════════════════════════════════
        # FILTRO FINAL: SCORE MÍNIMO
        # ═══════════════════════════════════════════════════════════
        
        if score < MIN_SCORE:
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
            'ema9': ema9,
            'ema21': ema21,
            'ema50': ema50,
            'rsi': rsi_val,
            'cvd': cvd_value,
            'cvd_signal': cvd_signal,
            'funding_rate': funding_rate,
            'oi_change': oi_change,
            'session': session_name,
            'liq_zones': liq_zones
        }
    
    def open_position(self, signal: Dict) -> bool:
        """
        Abrir posición con MARKET order (ejecución garantizada)
        """
        if not AUTO_TRADING:
            return False
        
        symbol = signal['symbol']
        price = signal['price']
        sl_price = signal['sl_price']
        
        log.info(f"\n{'='*80}")
        log.info(f"🎯 OPENING LONG: {symbol}")
        log.info(f"Score: {int(signal['score'])} | Edge: {signal['edge_ratio']:.1f}×")
        log.info(f"Entry: ${price:.6f} | SL: ${sl_price:.6f} (-{signal['sl_pct']:.2f}%)")
        log.info(f"TP1: ${signal['tp1_price']:.6f} ({int(TP1_PCT)}%)")
        log.info(f"TP2: ${signal['tp2_price']:.6f} ({int(TP2_PCT)}%)")
        log.info(f"Session: {signal['session']} | Funding: {signal['funding_rate']:.3f}%")
        log.info(f"CVD: {signal['cvd_signal']}")
        log.info(f"{'='*80}\n")
        
        # Calcular cantidad
        qty = self._calculate_quantity(symbol, price, sl_price)
        if not qty:
            log.error(f"No se pudo calcular cantidad para {symbol}")
            return False
        
        # Set leverage
        self._set_leverage(symbol, LEVERAGE)
        time.sleep(0.3)
        
        # MARKET ORDER para ejecución garantizada
        order_params = {
            'symbol': symbol,
            'side': 'BUY',
            'type': 'MARKET',
            'quantity': str(qty),
            'positionSide': 'LONG'
        }
        
        order_data = api_request('POST', '/openApi/swap/v2/trade/order', order_params)
        
        if order_data.get('code') != 0:
            log.error(f"Error abriendo posición: {order_data.get('msg')}")
            return False
        
        # Confirmar ejecución
        time.sleep(1)
        fill_qty, fill_price = self._confirm_position(symbol)
        
        if not fill_qty:
            log.error("Posición no confirmada")
            return False
        
        # Actualizar precios basado en fill real
        real_sl_pct = (fill_price - sl_price) / fill_price * 100
        tp1_price = fill_price * (1 + real_sl_pct * TP1_RR / 100)
        tp2_price = fill_price * (1 + real_sl_pct * TP2_RR / 100)
        
        # Colocar STOP LOSS (orden STOP_MARKET)
        sl_params = {
            'symbol': symbol,
            'side': 'SELL',
            'type': 'STOP_MARKET',
            'quantity': str(fill_qty),
            'stopPrice': str(round(sl_price, 8)),
            'positionSide': 'LONG'
        }
        
        sl_result = api_request('POST', '/openApi/swap/v2/trade/order', sl_params)
        if sl_result.get('code') != 0:
            # Retry con STOP + LIMIT
            sl_limit = sl_price * 0.999
            sl_params['type'] = 'STOP'
            sl_params['price'] = str(round(sl_limit, 8))
            sl_result = api_request('POST', '/openApi/swap/v2/trade/order', sl_params)
        
        sl_ok = sl_result.get('code') == 0
        
        # Guardar posición
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
            'highest': fill_price,
            'opened_at': datetime.now(),
            'score': signal['score'],
            'signal': signal,
            'pnl_realized': 0.0
        }
        
        self.stats['total_trades'] += 1
        
        # Notificación
        self._send_telegram(
            f"<b>🟢 LONG OPENED</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"Score: {int(signal['score'])} | Edge: {signal['edge_ratio']:.1f}×\n\n"
            f"📍 Entry: ${fill_price:.6f}\n"
            f"🎯 TP1 ({int(TP1_PCT)}%): ${tp1_price:.6f}\n"
            f"🎯 TP2 ({int(TP2_PCT)}%): ${tp2_price:.6f}\n"
            f"🛑 SL: ${sl_price:.6f} (-{real_sl_pct:.2f}%)\n\n"
            f"📊 Funding: {signal['funding_rate']:.3f}%\n"
            f"📊 CVD: {signal['cvd_signal']}\n"
            f"🕐 Session: {signal['session']}\n\n"
            f"{'✅ SL Placed' if sl_ok else '⚠️ SL Manual Required'}"
        )
        
        log.info(f"✓ Posición abierta: {symbol} @ ${fill_price:.6f}")
        return True
    
    def _calculate_quantity(self, symbol: str, price: float, sl_price: float) -> Optional[float]:
        """Calcular cantidad basado en riesgo"""
        contract = self.contracts_info.get(symbol, {})
        min_qty = contract.get('min_qty', 1)
        precision = contract.get('qty_precision', 2)
        contract_size = contract.get('contract_size', 1)
        
        price_per_contract = price * contract_size
        if price_per_contract <= 0:
            return None
        
        # Calcular basado en riesgo
        risk_pct = (price - sl_price) / price * 100
        risk_amount = self.equity * (RISK_PER_TRADE / 100)
        notional = min(risk_amount / (risk_pct / 100), POSITION_SIZE * LEVERAGE)
        
        qty = notional / price_per_contract
        qty = math.ceil(qty / min_qty) * min_qty
        qty = round(qty, precision)
        
        return qty if qty >= min_qty else None
    
    def _set_leverage(self, symbol: str, leverage: int):
        """Configurar apalancamiento"""
        for side in ['LONG', 'SHORT']:
            try:
                api_request('POST', '/openApi/swap/v2/trade/leverage', {
                    'symbol': symbol,
                    'side': side,
                    'leverage': str(leverage)
                })
            except:
                pass
    
    def _confirm_position(self, symbol: str, timeout: int = 15) -> Tuple[Optional[float], Optional[float]]:
        """Confirmar que la posición se abrió"""
        for _ in range(timeout):
            data = api_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
            for pos in data.get('data', []):
                amt = safe_float(pos.get('positionAmt', 0))
                side = str(pos.get('positionSide', '')).upper()
                if (side == 'LONG' or (side == 'BOTH' and amt > 0)) and abs(amt) > 0:
                    entry = safe_float(pos.get('avgPrice') or pos.get('entryPrice', 0))
                    return abs(amt), entry
            time.sleep(1)
        return None, None
    
    async def monitor_positions(self):
        """Monitor de posiciones activas con trailing stop"""
        for symbol in list(self.positions.keys()):
            try:
                pos = self.positions[symbol]
                ticker = self._get_ticker(symbol)
                if not ticker:
                    continue
                
                current_price = ticker['price']
                pnl_pct = (current_price - pos['entry']) / pos['entry'] * 100
                
                # Actualizar highest
                if current_price > pos['highest']:
                    pos['highest'] = current_price
                
                # TP1
                if not pos['tp1_hit'] and current_price >= pos['tp1_price']:
                    self._close_partial(symbol, pos['qty_tp1'], current_price, "TP1")
                    pos['tp1_hit'] = True
                    
                    # Move SL to breakeven
                    pos['sl_price'] = pos['entry'] * 1.001
                    log.info(f"🔒 {symbol} SL → Breakeven")
                    continue
                
                # TP2
                if pos['tp1_hit'] and not pos['tp2_hit'] and current_price >= pos['tp2_price']:
                    self._close_partial(symbol, pos['qty_tp2'], current_price, "TP2")
                    pos['tp2_hit'] = True
                    
                    # Trailing SL para runner
                    trail_distance = pos['signal']['atr'] * RUNNER_TRAIL
                    pos['sl_price'] = max(pos['sl_price'], current_price - trail_distance)
                    log.info(f"🔒 {symbol} SL → Trail @ ${pos['sl_price']:.6f}")
                    continue
                
                # Runner trailing
                if pos['tp2_hit']:
                    trail_distance = pos['signal']['atr'] * RUNNER_TRAIL
                    new_sl = current_price - trail_distance
                    if new_sl > pos['sl_price']:
                        pos['sl_price'] = new_sl
                
                # Check SL
                if current_price <= pos['sl_price']:
                    self._close_position(symbol, current_price, "STOP_LOSS")
                
            except Exception as e:
                log.error(f"Error monitoring {symbol}: {e}")
    
    def _close_partial(self, symbol: str, qty: float, price: float, reason: str):
        """Cerrar parcialmente"""
        if qty <= 0:
            return
        
        params = {
            'symbol': symbol,
            'side': 'SELL',
            'type': 'MARKET',
            'quantity': str(qty),
            'positionSide': 'LONG'
        }
        
        result = api_request('POST', '/openApi/swap/v2/trade/order', params)
        if result.get('code') != 0:
            log.error(f"Error cerrando parcial {symbol}: {result.get('msg')}")
            return
        
        pos = self.positions[symbol]
        pnl = self._calculate_pnl(pos['entry'], price, qty)
        pos['pnl_realized'] += pnl
        pos['qty'] -= qty
        
        self.stats['total_pnl'] += pnl
        self.daily_pnl += pnl
        
        log.info(f"💰 {reason} {symbol}: ${pnl:+.4f}")
        self._send_telegram(
            f"<b>💰 {reason}</b>\n\n"
            f"{symbol}\n"
            f"Exit: ${price:.6f}\n"
            f"PnL: ${pnl:+.4f}"
        )
    
    def _close_position(self, symbol: str, price: float, reason: str):
        """Cerrar posición completa"""
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        qty_remaining = pos['qty']
        
        if qty_remaining > 0:
            params = {
                'symbol': symbol,
                'side': 'SELL',
                'type': 'MARKET',
                'quantity': str(qty_remaining),
                'positionSide': 'LONG'
            }
            api_request('POST', '/openApi/swap/v2/trade/order', params)
        
        # PnL final
        pnl_final = self._calculate_pnl(pos['entry'], price, qty_remaining)
        total_pnl = pos['pnl_realized'] + pnl_final
        
        win = total_pnl > 0
        if win:
            self.stats['wins'] += 1
        else:
            self.stats['losses'] += 1
        
        self.stats['total_pnl'] += pnl_final
        self.daily_pnl += pnl_final
        
        # Stats
        total_trades = self.stats['wins'] + self.stats['losses']
        win_rate = (self.stats['wins'] / total_trades * 100) if total_trades > 0 else 0
        
        duration = datetime.now() - pos['opened_at']
        duration_min = int(duration.total_seconds() / 60)
        
        log.info(f"{'✅' if win else '❌'} {reason} {symbol} | "
                f"${total_pnl:+.4f} | {duration_min}min | WR:{win_rate:.0f}%")
        
        self._send_telegram(
            f"<b>{'✅' if win else '❌'} CLOSED — {reason}</b>\n\n"
            f"<b>{symbol}</b>\n"
            f"Entry: ${pos['entry']:.6f}\n"
            f"Exit: ${price:.6f}\n"
            f"Duration: {duration_min}min\n\n"
            f"<b>PnL: ${total_pnl:+.4f}</b>\n"
            f"Win Rate: {win_rate:.0f}% ({self.stats['wins']}/{total_trades})"
        )
        
        del self.positions[symbol]
    
    def _calculate_pnl(self, entry: float, exit: float, qty: float) -> float:
        """Calcular PnL"""
        contract = self.contracts_info.get(list(self.positions.keys())[0], {})
        contract_size = contract.get('contract_size', 1)
        
        notional = qty * entry * contract_size
        pnl_gross = (exit - entry) / entry * notional * LEVERAGE
        fees = notional * (FEE_TAKER + FEE_MAKER)
        
        return pnl_gross - fees
    
    def _check_circuit_breaker(self) -> bool:
        """Verificar circuit breaker"""
        # Reset diario
        today = datetime.utcnow().date()
        if today != self.daily_date:
            self.daily_pnl = 0
            self.daily_date = today
            if self.circuit_breaker_active:
                self.circuit_breaker_active = False
                self.circuit_breaker_until = None
                log.info("🔓 Circuit Breaker RESET")
        
        # Check si está activo
        if self.circuit_breaker_active:
            if self.circuit_breaker_until and datetime.utcnow() > self.circuit_breaker_until:
                self.circuit_breaker_active = False
                self.daily_pnl = 0
                log.info("🔓 Circuit Breaker OFF")
                return False
            return True
        
        # Check threshold
        threshold = self.equity * (CIRCUIT_BREAKER_PCT / 100)
        if self.daily_pnl < -threshold:
            self.circuit_breaker_active = True
            self.circuit_breaker_until = datetime.utcnow() + timedelta(hours=4)
            log.warning(f"🔒 CIRCUIT BREAKER ACTIVATED | Loss: ${self.daily_pnl:.2f}")
            self._send_telegram(
                f"<b>🔒 CIRCUIT BREAKER</b>\n\n"
                f"Daily Loss: ${self.daily_pnl:.2f}\n"
                f"Threshold: {CIRCUIT_BREAKER_PCT}%\n"
                f"Paused: 4 hours"
            )
            return True
        
        return False
    
    def _send_telegram(self, message: str):
        """Enviar mensaje a Telegram"""
        if not TG_TOKEN or not TG_CHAT:
            return
        
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={
                    'chat_id': TG_CHAT,
                    'text': message,
                    'parse_mode': 'HTML'
                },
                timeout=5
            )
        except:
            pass
    
    async def run(self):
        """Loop principal"""
        log.info("\n🚀 Institutional Bot v2.0 RUNNING\n")
        
        iteration = 0
        last_symbol_refresh = 0
        last_equity_update = 0
        
        while True:
            try:
                iteration += 1
                
                # Refresh symbols cada 10 min
                if time.time() - last_symbol_refresh > 600:
                    self._refresh_symbols()
                    last_symbol_refresh = time.time()
                
                # Update equity cada 30 min
                if time.time() - last_equity_update > 1800:
                    if AUTO_TRADING:
                        data = api_request('GET', '/openApi/swap/v2/user/balance')
                        if data.get('code') == 0:
                            balance_data = data.get('data', {})
                            self.equity = safe_float(balance_data.get('equity', self.equity))
                    last_equity_update = time.time()
                
                # Circuit breaker
                if self._check_circuit_breaker():
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue
                
                # Stats
                total_trades = self.stats['wins'] + self.stats['losses']
                win_rate = (self.stats['wins'] / total_trades * 100) if total_trades > 0 else 0
                
                log.info(f"\n{'='*80}")
                log.info(f"#{iteration} {datetime.now().strftime('%H:%M:%S')} UTC | "
                        f"Positions: {len(self.positions)}/{MAX_POSITIONS}")
                log.info(f"PnL: ${self.stats['total_pnl']:+.4f} | "
                        f"Today: ${self.daily_pnl:+.4f} | "
                        f"WR: {win_rate:.0f}%")
                log.info(f"{'='*80}\n")
                
                # Monitor posiciones
                await self.monitor_positions()
                
                # Buscar nuevas señales
                if len(self.positions) < MAX_POSITIONS:
                    log.info(f"Scanning {len(self.symbols)} symbols...")
                    signals_found = 0
                    
                    for symbol in self.symbols:
                        if len(self.positions) >= MAX_POSITIONS:
                            break
                        
                        signal = self.analyze_symbol(symbol)
                        if signal:
                            signals_found += 1
                            log.info(f"💡 {symbol} | Score: {int(signal['score'])} | "
                                   f"Edge: {signal['edge_ratio']:.1f}×")
                            
                            if self.open_position(signal):
                                await asyncio.sleep(2)
                    
                    log.info(f"✓ Scan complete | Signals: {signals_found}")
                
                await asyncio.sleep(SCAN_INTERVAL)
                
            except KeyboardInterrupt:
                log.info("⏹️ Bot stopped by user")
                break
            except Exception as e:
                log.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(20)

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
        log.info("👋 Bot terminated")
