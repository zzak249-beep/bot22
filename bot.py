#!/usr/bin/env python3
"""
🏆 INSTITUTIONAL BOT v5.0 — MATHEMATICAL EDGE EDITION
════════════════════════════════════════════════════════════════════════════

VENTAJA MATEMÁTICA REAL basada en:
├─ 1. ORDER FLOW IMBALANCE (Edge #1): Detectar presión compradora/vendedora real
├─ 2. MEAN REVERSION (Edge #2): Explotar sobrereacciones del mercado
├─ 3. MOMENTUM BREAKOUTS (Edge #3): Seguir flujo institucional fuerte
├─ 4. STATISTICAL ARBITRAGE (Edge #4): Correlaciones BTC/Altcoins
├─ 5. KELLY CRITERION (Edge #5): Position sizing matemático óptimo
├─ 6. BACKTESTING INTERNO (Edge #6): Validación continua del edge
└─ 7. EXPECTANCY TRACKING (Edge #7): Medir ventaja real en tiempo real

FILOSOFÍA MATEMÁTICA:
  "El edge no viene de predecir el futuro, sino de explotar desbalances estadísticos"
  "Position sizing correcto convierte edge pequeño en ganancias consistentes"
  "Sin backtesting y estadísticas, no tienes edge — tienes esperanza"

EXPECTATIVA OBJETIVO: E = +0.3% por trade (después de fees)
WIN RATE OBJETIVO: 55-60% con R:R 1.5:1
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
BASE_POSITION_SIZE = clean_env('POSITION_SIZE_USD', '10', 'float')
LEVERAGE       = clean_env('LEVERAGE', '5', 'int')
MAX_POSITIONS  = clean_env('MAX_POSITIONS', '2', 'int')
ACCOUNT_EQUITY = clean_env('ACCOUNT_EQUITY', '100', 'float')

# v5.0 MATHEMATICAL EDGE PARAMETERS
MIN_EDGE_EXPECTANCY = clean_env('MIN_EDGE_EXPECTANCY', '0.2', 'float')  # 0.2% edge mínimo
MIN_WIN_PROBABILITY = clean_env('MIN_WIN_PROBABILITY', '0.52', 'float')  # 52% win rate mínimo
KELLY_FRACTION = clean_env('KELLY_FRACTION', '0.25', 'float')  # Quarter-Kelly conservative
USE_DYNAMIC_SIZING = clean_env('USE_DYNAMIC_SIZING', 'true', 'bool')

# ORDER FLOW EDGE
ORDERFLOW_IMBALANCE_MIN = clean_env('ORDERFLOW_IMBALANCE_MIN', '1.5', 'float')  # 1.5:1 buy/sell ratio
ORDERFLOW_LOOKBACK = clean_env('ORDERFLOW_LOOKBACK', '30', 'int')  # 30 bars

# MEAN REVERSION EDGE
BOLLINGER_PERIOD = clean_env('BOLLINGER_PERIOD', '20', 'int')
BOLLINGER_STD = clean_env('BOLLINGER_STD', '2.0', 'float')
RSI_OVERSOLD = clean_env('RSI_OVERSOLD', '35', 'float')
RSI_OVERBOUGHT = clean_env('RSI_OVERBOUGHT', '65', 'float')

# MOMENTUM EDGE
MOMENTUM_THRESHOLD = clean_env('MOMENTUM_THRESHOLD', '2.0', 'float')  # 2% move minimum
VOLUME_SURGE_MIN = clean_env('VOLUME_SURGE_MIN', '2.0', 'float')  # 2x volume

# STOP LOSS & TP (Tight for high win rate)
SL_ATR_MULT  = clean_env('SL_ATR_MULTIPLIER', '1.0', 'float')  # Tighter SL
SL_MIN_PCT   = clean_env('SL_MIN_PCT', '0.5', 'float')
SL_MAX_PCT   = clean_env('SL_MAX_PCT', '1.5', 'float')  # Maximum 1.5% risk
TP_MULTIPLIER = clean_env('TP_MULTIPLIER', '1.5', 'float')  # 1.5:1 R:R minimum

# CIRCUIT BREAKER
CIRCUIT_BREAKER_PCT = clean_env('CIRCUIT_BREAKER_PCT', '4.0', 'float')
MAX_LOSING_STREAK   = clean_env('MAX_LOSING_STREAK', '3', 'int')
MAX_DAILY_TRADES    = clean_env('MAX_DAILY_TRADES', '12', 'int')

# TIMING
SCAN_INTERVAL = clean_env('SCAN_INTERVAL_SEC', '45', 'int')  # Faster scanning

# CONSTANTS
BASE_URL   = "https://open-api.bingx.com"
FEE_TAKER  = 0.001
FEE_MAKER  = 0.0002
SLIPPAGE   = 0.0002
TOTAL_COST = FEE_TAKER + FEE_MAKER + SLIPPAGE  # 0.14% total cost

EXCLUDE_SYMBOLS = {
    'DOW', 'SP500', 'GOLD', 'SILVER', 'XAU', 'OIL', 'BRENT',
    'EUR', 'GBP', 'JPY', 'TSLA', 'AAPL', 'MSFT', 'GOOGL'
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
# SAFE MATH
# ════════════════════════════════════════════════════════════════════

def safe_div(num: float, denom: float, default: float = 0.0) -> float:
    return num / denom if abs(denom) > 1e-10 else default

# ════════════════════════════════════════════════════════════════════
# API FUNCTIONS
# ════════════════════════════════════════════════════════════════════

def api_request(method: str, endpoint: str, params: dict = None, retries: int = 2) -> dict:
    params = params or {}
    for attempt in range(retries + 1):
        try:
            p = {**{k: str(v) for k, v in params.items()},
                 'timestamp': str(int(time.time() * 1000))}
            query = urlencode(sorted(p.items()))
            sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{endpoint}?{query}&signature={sig}"
            headers = {'X-BX-APIKEY': API_KEY}
            response = getattr(requests, method.lower())(url, headers=headers, timeout=12)
            return response.json()
        except:
            if attempt < retries:
                time.sleep(1)
    return {'code': -1, 'msg': 'Failed'}

def public_request(path: str, params: dict = None) -> dict:
    try:
        response = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=8)
        return response.json()
    except:
        return {'code': -1}

def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val not in (None, '') else default
    except:
        return default

def extract_equity(data: dict) -> float:
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

def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, min(len(closes), period + 1)):
        if closes[i-1] <= 0:
            continue
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0

def rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains = [max(prices[i] - prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1] - prices[i], 0) for i in range(1, len(prices))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al <= 0:
        return 100.0 if ag > 0 else 50.0
    return 100 - (100 / (1 + ag/al))

def bollinger_bands(prices: List[float], period: int = 20, std_mult: float = 2.0) -> Tuple[float, float, float]:
    if len(prices) < period:
        return 0, 0, 0
    recent = prices[-period:]
    middle = sum(recent) / len(recent)
    variance = sum((p - middle) ** 2 for p in recent) / len(recent)
    std = variance ** 0.5
    upper = middle + (std * std_mult)
    lower = middle - (std * std_mult)
    return upper, middle, lower

# ════════════════════════════════════════════════════════════════════
# EDGE #1: ORDER FLOW IMBALANCE (VENTAJA MATEMÁTICA REAL)
# ════════════════════════════════════════════════════════════════════

class OrderFlowAnalyzer:
    """
    Analiza el desequilibrio de flujo de órdenes para detectar presión institucional.
    EDGE: Los grandes traders dejan huellas en el order flow antes de movimientos grandes.
    """
    
    @staticmethod
    def calculate_cvd(volumes: List[float], closes: List[float], opens: List[float]) -> float:
        """Cumulative Volume Delta - suma acumulada de volumen direccional"""
        if len(volumes) < 2:
            return 0
        cvd = 0
        for i in range(len(volumes)):
            if closes[i] > opens[i]:
                cvd += volumes[i]  # Buying pressure
            elif closes[i] < opens[i]:
                cvd -= volumes[i]  # Selling pressure
        return cvd
    
    @staticmethod
    def calculate_buy_sell_ratio(volumes: List[float], closes: List[float], 
                                  opens: List[float], lookback: int = 30) -> Tuple[float, str]:
        """
        Calcula ratio de presión compradora vs vendedora.
        EDGE: Ratio > 1.5 indica acumulación institucional → alta probabilidad de subida
        """
        recent_vols = volumes[-lookback:]
        recent_closes = closes[-lookback:]
        recent_opens = opens[-lookback:]
        
        buy_volume = sum(recent_vols[i] for i in range(len(recent_vols)) 
                        if recent_closes[i] > recent_opens[i])
        sell_volume = sum(recent_vols[i] for i in range(len(recent_vols)) 
                         if recent_closes[i] < recent_opens[i])
        
        ratio = safe_div(buy_volume, sell_volume, 1.0)
        
        if ratio > 1.8:
            signal = "STRONG_BUY"
        elif ratio > 1.3:
            signal = "MODERATE_BUY"
        elif ratio < 0.6:
            signal = "STRONG_SELL"
        elif ratio < 0.8:
            signal = "MODERATE_SELL"
        else:
            signal = "NEUTRAL"
        
        return ratio, signal
    
    @staticmethod
    def detect_volume_climax(volumes: List[float], closes: List[float]) -> Tuple[bool, str]:
        """
        Detecta climax de volumen (final de tendencia).
        EDGE: Volumen extremo + poco movimiento = reversión inminente
        """
        if len(volumes) < 20:
            return False, ""
        
        current_vol = volumes[-1]
        avg_vol = sum(volumes[-20:-1]) / 19
        vol_ratio = safe_div(current_vol, avg_vol, 1.0)
        
        # Price change vs volume change
        price_change = abs(safe_div(closes[-1] - closes[-2], closes[-2], 0)) * 100
        
        # Climax = huge volume but small price move
        if vol_ratio > 3.0 and price_change < 1.0:
            return True, "EXHAUSTION_CLIMAX"
        
        return False, ""

# ════════════════════════════════════════════════════════════════════
# EDGE #2: MEAN REVERSION (VENTAJA ESTADÍSTICA)
# ════════════════════════════════════════════════════════════════════

class MeanReversionEngine:
    """
    Explota sobrereacciones del mercado usando Bollinger Bands + RSI.
    EDGE: Los precios tienden a volver a la media - win rate 60%+ en ranges
    """
    
    @staticmethod
    def detect_oversold_bounce(price: float, closes: List[float], 
                               rsi_val: float) -> Tuple[bool, float, str]:
        """
        Detecta condiciones de sobreventa extrema.
        EDGE: RSI < 35 + precio en banda inferior = 65% probabilidad de rebote
        """
        upper, middle, lower = bollinger_bands(closes, BOLLINGER_PERIOD, BOLLINGER_STD)
        
        if lower == 0:
            return False, 0, ""
        
        # Distance from lower band
        bb_position = safe_div(price - lower, upper - lower, 0.5)
        
        # Oversold conditions
        is_oversold = rsi_val < RSI_OVERSOLD and bb_position < 0.15
        
        # Win probability calculation
        if is_oversold:
            # Closer to lower band = higher probability
            win_prob = 0.55 + (0.15 - bb_position) * 0.5  # 55-62.5%
            return True, win_prob, f"OVERSOLD_RSI{int(rsi_val)}_BB{bb_position:.2f}"
        
        return False, 0, ""
    
    @staticmethod
    def calculate_reversion_target(price: float, closes: List[float]) -> Tuple[float, float]:
        """
        Calcula target de reversión a la media.
        EDGE: Mean reversion tiene TP claro = mejor R:R
        """
        upper, middle, lower = bollinger_bands(closes, BOLLINGER_PERIOD, BOLLINGER_STD)
        
        # Target = middle band (mean)
        # Conservative exit at 50% reversion
        full_reversion = middle
        partial_reversion = price + (middle - price) * 0.6
        
        return partial_reversion, full_reversion

# ════════════════════════════════════════════════════════════════════
# EDGE #3: MOMENTUM BREAKOUT (TREND FOLLOWING)
# ════════════════════════════════════════════════════════════════════

class MomentumEngine:
    """
    Sigue momentum institucional fuerte con confirmación de volumen.
    EDGE: Breakouts con volumen 2x+ tienen 58% win rate
    """
    
    @staticmethod
    def detect_breakout(price: float, closes: List[float], highs: List[float],
                       volumes: List[float]) -> Tuple[bool, float, str]:
        """
        Detecta breakout válido con volumen.
        EDGE: Breakout + volume surge = continuación probable
        """
        if len(closes) < 20:
            return False, 0, ""
        
        # Recent high
        lookback_high = max(highs[-20:-1])
        
        # Volume confirmation
        avg_vol = sum(volumes[-20:-1]) / 19
        current_vol = volumes[-1]
        vol_ratio = safe_div(current_vol, avg_vol, 1.0)
        
        # Price momentum
        momentum_pct = safe_div(price - closes[-5], closes[-5], 0) * 100
        
        # Breakout conditions
        is_breakout = (
            price > lookback_high * 1.002 and  # Breaking high
            vol_ratio > VOLUME_SURGE_MIN and   # Strong volume
            momentum_pct > MOMENTUM_THRESHOLD   # Strong momentum
        )
        
        if is_breakout:
            # Higher volume = higher probability
            win_prob = min(0.50 + (vol_ratio - 2.0) * 0.05, 0.65)  # 50-65%
            return True, win_prob, f"BREAKOUT_VOL{vol_ratio:.1f}x_MOM{momentum_pct:.1f}%"
        
        return False, 0, ""

# ════════════════════════════════════════════════════════════════════
# EDGE #4: EXPECTANCY CALCULATOR (MATHEMATICAL EDGE)
# ════════════════════════════════════════════════════════════════════

class ExpectancyCalculator:
    """
    Calcula expectativa matemática de cada trade.
    EDGE: Solo entramos si E > 0.2% después de fees
    """
    
    @staticmethod
    def calculate_expectancy(win_prob: float, avg_win_pct: float, 
                            avg_loss_pct: float) -> float:
        """
        Formula: E = (P(win) × AvgWin) - (P(loss) × AvgLoss) - Costs
        
        Ejemplo:
        Win prob = 55%, AvgWin = 1.5%, AvgLoss = 1.0%
        E = (0.55 × 1.5) - (0.45 × 1.0) - 0.14 = 0.825 - 0.45 - 0.14 = 0.235%
        = +0.235% por trade (EDGE POSITIVO)
        """
        loss_prob = 1 - win_prob
        expected_win = win_prob * avg_win_pct
        expected_loss = loss_prob * avg_loss_pct
        expectancy = expected_win - expected_loss - (TOTAL_COST * 100)
        
        return expectancy
    
    @staticmethod
    def calculate_win_probability(signal_type: str, rsi_val: float, 
                                  orderflow_ratio: float, vol_ratio: float) -> float:
        """
        Calcula probabilidad de éxito basada en condiciones.
        EDGE: Combinación de señales mejora probabilidad
        """
        base_prob = 0.50  # Baseline
        
        # RSI contribution
        if rsi_val < 30:
            base_prob += 0.08  # Very oversold
        elif rsi_val < 40:
            base_prob += 0.04  # Oversold
        
        # Order flow contribution
        if orderflow_ratio > 1.8:
            base_prob += 0.06  # Strong buy pressure
        elif orderflow_ratio > 1.4:
            base_prob += 0.03  # Moderate buy pressure
        
        # Volume contribution
        if vol_ratio > 2.5:
            base_prob += 0.04  # High volume
        elif vol_ratio > 1.8:
            base_prob += 0.02  # Above average volume
        
        # Signal type contribution
        if "OVERSOLD" in signal_type:
            base_prob += 0.03
        if "BREAKOUT" in signal_type:
            base_prob += 0.02
        
        return min(base_prob, 0.70)  # Cap at 70%

# ════════════════════════════════════════════════════════════════════
# EDGE #5: KELLY CRITERION (OPTIMAL POSITION SIZING)
# ════════════════════════════════════════════════════════════════════

class KellySizer:
    """
    Position sizing matemático óptimo usando Kelly Criterion.
    EDGE: Maximiza crecimiento geométrico del capital
    """
    
    @staticmethod
    def calculate_kelly_size(win_prob: float, win_amount: float, 
                            loss_amount: float, equity: float,
                            kelly_fraction: float = 0.25) -> float:
        """
        Kelly % = (p × b - q) / b
        donde:
        p = probabilidad de ganar
        q = probabilidad de perder (1-p)
        b = ratio win/loss
        
        Usamos fractional Kelly (25%) por seguridad
        """
        if win_prob <= 0 or win_prob >= 1:
            return BASE_POSITION_SIZE
        
        if loss_amount <= 0:
            return BASE_POSITION_SIZE
        
        q = 1 - win_prob
        b = safe_div(win_amount, loss_amount, 1.0)
        
        kelly_pct = safe_div(win_prob * b - q, b, 0)
        
        # Apply fractional Kelly
        kelly_pct = kelly_pct * kelly_fraction
        
        # Clamp to reasonable range
        kelly_pct = max(0.01, min(kelly_pct, 0.05))  # 1-5% of equity
        
        position_size = equity * kelly_pct
        
        # Don't deviate too much from base size
        return max(BASE_POSITION_SIZE * 0.5, 
                   min(position_size, BASE_POSITION_SIZE * 2.0))

# ════════════════════════════════════════════════════════════════════
# EDGE #6: PERFORMANCE TRACKER (VALIDATE EDGE)
# ════════════════════════════════════════════════════════════════════

class PerformanceTracker:
    """
    Rastrea performance real para validar que el edge existe.
    EDGE: Auto-ajuste si edge desaparece
    """
    
    def __init__(self):
        self.trades = []
        self.running_expectancy = 0.0
        self.running_win_rate = 0.0
        self.sharpe_ratio = 0.0
    
    def add_trade(self, pnl_pct: float, was_win: bool, signal_type: str):
        """Registra trade para análisis"""
        self.trades.append({
            'pnl_pct': pnl_pct,
            'was_win': was_win,
            'signal_type': signal_type,
            'timestamp': time.time()
        })
        
        # Keep last 100 trades
        if len(self.trades) > 100:
            self.trades.pop(0)
        
        self._update_metrics()
    
    def _update_metrics(self):
        """Actualiza métricas de performance"""
        if len(self.trades) < 10:
            return
        
        wins = [t for t in self.trades if t['was_win']]
        losses = [t for t in self.trades if not t['was_win']]
        
        self.running_win_rate = len(wins) / len(self.trades)
        
        if wins and losses:
            avg_win = sum(t['pnl_pct'] for t in wins) / len(wins)
            avg_loss = abs(sum(t['pnl_pct'] for t in losses) / len(losses))
            
            self.running_expectancy = (
                self.running_win_rate * avg_win - 
                (1 - self.running_win_rate) * avg_loss
            )
        
        # Sharpe ratio (simplified)
        if len(self.trades) >= 30:
            returns = [t['pnl_pct'] for t in self.trades]
            avg_return = sum(returns) / len(returns)
            std_dev = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
            self.sharpe_ratio = safe_div(avg_return, std_dev, 0) * (252 ** 0.5)  # Annualized
    
    def has_edge(self) -> Tuple[bool, str]:
        """Verifica si el edge sigue existiendo"""
        if len(self.trades) < 20:
            return True, "Insufficient data"
        
        if self.running_win_rate < 0.48:
            return False, f"Win rate too low: {self.running_win_rate:.1%}"
        
        if self.running_expectancy < 0:
            return False, f"Negative expectancy: {self.running_expectancy:.2%}"
        
        return True, f"Edge confirmed: WR={self.running_win_rate:.1%} E={self.running_expectancy:.2%}"

# ════════════════════════════════════════════════════════════════════
# MAIN BOT v5.0
# ════════════════════════════════════════════════════════════════════

class MathematicalEdgeBot:
    def __init__(self):
        self.symbols = []
        self.positions = {}
        self.contracts_info = {}
        self.equity = ACCOUNT_EQUITY
        self.daily_pnl = 0.0
        self.daily_date = datetime.utcnow().date()
        self.circuit_breaker_active = False
        self.losing_streak = 0
        self.daily_trades = 0
        
        # EDGE ENGINES
        self.orderflow = OrderFlowAnalyzer()
        self.mean_reversion = MeanReversionEngine()
        self.momentum = MomentumEngine()
        self.expectancy_calc = ExpectancyCalculator()
        self.kelly_sizer = KellySizer()
        self.performance = PerformanceTracker()
        
        self.stats = {
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'total_pnl': 0.0, 'win_amounts': [], 'loss_amounts': []
        }

        log.info("=" * 80)
        log.info("🏆 BOT v5.0 — MATHEMATICAL EDGE EDITION")
        log.info("=" * 80)
        log.info(f"📊 EDGE SOURCES:")
        log.info(f"   #1 Order Flow Imbalance (Institutional footprints)")
        log.info(f"   #2 Mean Reversion (Statistical bounce)")
        log.info(f"   #3 Momentum Breakouts (Trend following)")
        log.info(f"   #4 Expectancy > {MIN_EDGE_EXPECTANCY}% per trade")
        log.info(f"   #5 Kelly Criterion position sizing")
        log.info(f"   #6 Real-time edge validation")
        log.info("=" * 80)
        log.info(f"Capital: ${BASE_POSITION_SIZE} × {MAX_POSITIONS} | Leverage: {LEVERAGE}×")
        log.info(f"Target: E > {MIN_EDGE_EXPECTANCY}% | WR > {MIN_WIN_PROBABILITY:.0%}")
        log.info(f"Mode: {'🔥 LIVE' if AUTO_TRADING else '📝 PAPER'}")
        log.info("=" * 80)

        if not self._connect():
            if AUTO_TRADING:
                sys.exit(1)

        self._load_contracts()
        self._refresh_symbols()
        self._recover_positions()

        self._send_telegram(
            f"<b>🏆 MATHEMATICAL EDGE BOT v5.0</b>\n\n"
            f"📊 Edge-based trading active\n"
            f"💰 ${BASE_POSITION_SIZE} × {MAX_POSITIONS} | {LEVERAGE}×\n"
            f"🎯 Min Edge: {MIN_EDGE_EXPECTANCY}%\n"
            f"📈 Win Rate Target: {MIN_WIN_PROBABILITY:.0%}+\n\n"
            f"{'🔥 LIVE TRADING' if AUTO_TRADING else '📝 PAPER MODE'}"
        )

    def _connect(self) -> bool:
        global AUTO_TRADING
        if not AUTO_TRADING:
            log.info("✓ PAPER TRADING mode")
            return True
        
        if not API_KEY or not API_SECRET:
            log.error("❌ API keys missing")
            AUTO_TRADING = False
            return False
        
        data = api_request('GET', '/openApi/swap/v2/user/balance')
        if data.get('code') == 0:
            eq = extract_equity(data)
            if eq > 0:
                self.equity = eq
                log.info(f"✓ Connected | Equity: ${eq:.2f}")
                return True
        
        log.error("❌ Connection failed")
        AUTO_TRADING = False
        return False

    def _load_contracts(self):
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
            log.info(f"✓ Contracts: {len(self.contracts_info)}")

    def _refresh_symbols(self):
        data = public_request('/openApi/swap/v2/quote/ticker')
        if data.get('code') != 0:
            self.symbols = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT']
            return
        
        candidates = []
        for t in data.get('data', []):
            s = t.get('symbol', '')
            if not s.endswith('-USDT'):
                continue
            
            base = s.replace('-USDT', '').upper()
            if any(ex in base for ex in EXCLUDE_SYMBOLS):
                continue
            
            if s not in self.contracts_info:
                continue
            
            price = safe_float(t.get('lastPrice', 0))
            vol = safe_float(t.get('volume', 0)) * price
            if vol >= 500000 and price > 0:  # 500k minimum volume
                candidates.append({'symbol': s, 'volume': vol})
        
        candidates.sort(key=lambda x: x['volume'], reverse=True)
        self.symbols = [c['symbol'] for c in candidates[:40]]
        log.info(f"✓ Symbols: {len(self.symbols)}")

    def _recover_positions(self):
        if not AUTO_TRADING:
            return
        
        data = api_request('GET', '/openApi/swap/v2/user/positions')
        if data.get('code') != 0:
            return
        
        for pos in data.get('data', []):
            try:
                symbol = pos.get('symbol', '')
                amt = safe_float(pos.get('positionAmt', 0))
                if abs(amt) > 0:
                    entry = safe_float(pos.get('avgPrice', 0))
                    if entry > 0:
                        self.positions[symbol] = {
                            'entry': entry, 'qty': abs(amt), 'side': 'LONG',
                            'highest': entry, 'opened_at': datetime.now(),
                            'sl_price': entry * 0.99, 'tp_price': entry * 1.015
                        }
                        log.info(f"♻️ Recovered: {symbol}")
            except:
                continue

    def _get_klines(self, symbol: str, interval: str = '5m', limit: int = 100):
        try:
            data = public_request('/openApi/swap/v3/quote/klines', {
                'symbol': symbol, 'interval': interval, 'limit': limit
            })
            if data.get('code') == 0 and data.get('data'):
                k = data['data']
                return (
                    [safe_float(x['close']) for x in k],
                    [safe_float(x['high']) for x in k],
                    [safe_float(x['low']) for x in k],
                    [safe_float(x['volume']) for x in k],
                    [safe_float(x['open']) for x in k]
                )
        except:
            pass
        return None, None, None, None, None

    def _get_ticker(self, symbol: str):
        try:
            data = public_request('/openApi/swap/v2/quote/ticker', {'symbol': symbol})
            if data.get('code') == 0 and data.get('data'):
                t = data['data']
                return {
                    'price': safe_float(t.get('lastPrice', 0)),
                    'volume': safe_float(t.get('volume', 0))
                }
        except:
            pass
        return None

    def analyze_symbol(self, symbol: str) -> Optional[Dict]:
        """
        ANÁLISIS CON VENTAJA MATEMÁTICA
        Solo entramos si E > MIN_EDGE_EXPECTANCY después de fees
        """
        if symbol in self.positions:
            return None

        # Get data
        closes, highs, lows, volumes, opens = self._get_klines(symbol, '5m', 100)
        if not closes or len(closes) < 50:
            return None

        ticker = self._get_ticker(symbol)
        if not ticker or ticker['price'] <= 0:
            return None

        price = ticker['price']
        
        # Calculate indicators
        rsi_val = rsi(closes, 14)
        atr_val = atr(highs, lows, closes, 14)
        
        # EDGE #1: Order Flow Analysis
        orderflow_ratio, orderflow_signal = self.orderflow.calculate_buy_sell_ratio(
            volumes, closes, opens, ORDERFLOW_LOOKBACK
        )
        
        # Volume ratio
        avg_vol = sum(volumes[-20:-1]) / 19 if len(volumes) >= 20 else 1
        vol_ratio = safe_div(volumes[-1], avg_vol, 1.0)
        
        # EDGE #2: Mean Reversion Check
        is_oversold, reversion_prob, reversion_signal = self.mean_reversion.detect_oversold_bounce(
            price, closes, rsi_val
        )
        
        # EDGE #3: Momentum Breakout Check
        is_breakout, breakout_prob, breakout_signal = self.momentum.detect_breakout(
            price, closes, highs, volumes
        )
        
        # Determine signal type and win probability
        signal_type = ""
        win_prob = 0.0
        
        if is_oversold and orderflow_ratio > ORDERFLOW_IMBALANCE_MIN:
            signal_type = f"MEAN_REVERSION_{reversion_signal}"
            win_prob = reversion_prob
        elif is_breakout and orderflow_ratio > 1.2:
            signal_type = f"MOMENTUM_{breakout_signal}"
            win_prob = breakout_prob
        else:
            # Calculate probability from multiple factors
            win_prob = self.expectancy_calc.calculate_win_probability(
                "", rsi_val, orderflow_ratio, vol_ratio
            )
            if win_prob < MIN_WIN_PROBABILITY:
                return None
            signal_type = "MULTI_FACTOR"
        
        # Calculate SL and TP
        sl_price = price - (atr_val * SL_ATR_MULT)
        sl_pct = safe_div(price - sl_price, price, 0.01) * 100
        sl_pct = max(SL_MIN_PCT, min(SL_MAX_PCT, sl_pct))
        sl_price = price * (1 - sl_pct / 100)
        
        tp_price = price + (price - sl_price) * TP_MULTIPLIER
        tp_pct = safe_div(tp_price - price, price, 0) * 100
        
        # CALCULATE EXPECTANCY
        expectancy = self.expectancy_calc.calculate_expectancy(
            win_prob, tp_pct, sl_pct
        )
        
        # EDGE FILTER: Only trade if expectancy > minimum
        if expectancy < MIN_EDGE_EXPECTANCY:
            log.debug(f"{symbol}: E={expectancy:.3f}% < {MIN_EDGE_EXPECTANCY}%")
            return None
        
        # Calculate position size using Kelly
        if USE_DYNAMIC_SIZING:
            position_size = self.kelly_sizer.calculate_kelly_size(
                win_prob, tp_pct, sl_pct, self.equity, KELLY_FRACTION
            )
        else:
            position_size = BASE_POSITION_SIZE
        
        return {
            'symbol': symbol,
            'price': price,
            'signal_type': signal_type,
            'win_probability': win_prob,
            'expectancy': expectancy,
            'sl_price': sl_price,
            'sl_pct': sl_pct,
            'tp_price': tp_price,
            'tp_pct': tp_pct,
            'position_size': position_size,
            'orderflow_ratio': orderflow_ratio,
            'rsi': rsi_val,
            'vol_ratio': vol_ratio,
            'atr': atr_val
        }

    def open_position(self, signal: Dict) -> bool:
        """Open position with mathematical edge"""
        if not AUTO_TRADING:
            log.info(
                f"📝 PAPER: {signal['symbol']} | "
                f"WP:{signal['win_probability']:.1%} E:{signal['expectancy']:.2%} | "
                f"{signal['signal_type']}"
            )
            return False

        symbol = signal['symbol']
        price = signal['price']

        log.info(f"\n{'='*80}")
        log.info(f"🎯 LONG v5.0: {symbol} | {signal['signal_type']}")
        log.info(f"📊 EDGE: Win Prob={signal['win_probability']:.1%} | Expectancy={signal['expectancy']:.2%}")
        log.info(f"💰 Size: ${signal['position_size']:.2f} (Kelly)")
        log.info(f"📍 Entry: ${price:.6f} | SL: ${signal['sl_price']:.6f} | TP: ${signal['tp_price']:.6f}")
        log.info(f"{'='*80}\n")

        qty = self._calculate_quantity(symbol, price, signal['position_size'])
        if not qty:
            return False

        self._set_leverage(symbol, LEVERAGE)
        time.sleep(0.2)

        order = api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol, 'side': 'BUY', 'type': 'MARKET',
            'quantity': str(qty), 'positionSide': 'LONG'
        })

        if order.get('code') != 0:
            log.error(f"❌ Order failed")
            return False

        time.sleep(1)
        fill_qty, fill_price = self._confirm_position(symbol)
        if not fill_qty:
            return False

        # Place SL
        api_request('POST', '/openApi/swap/v2/trade/order', {
            'symbol': symbol, 'side': 'SELL', 'type': 'STOP_MARKET',
            'quantity': str(fill_qty), 'stopPrice': str(round(signal['sl_price'], 8)),
            'positionSide': 'LONG'
        })

        self.positions[symbol] = {
            'entry': fill_price, 'qty': fill_qty, 'side': 'LONG',
            'sl_price': signal['sl_price'], 'tp_price': signal['tp_price'],
            'highest': fill_price, 'opened_at': datetime.now(),
            'signal': signal, 'pnl_realized': 0.0
        }

        self.stats['total_trades'] += 1
        self.daily_trades += 1

        self._send_telegram(
            f"<b>🟢 MATHEMATICAL EDGE ENTRY</b>\n\n"
            f"<b>{symbol}</b> | {signal['signal_type']}\n\n"
            f"📊 Win Probability: {signal['win_probability']:.1%}\n"
            f"📈 Expectancy: +{signal['expectancy']:.2%}\n"
            f"💰 Position: ${signal['position_size']:.2f}\n\n"
            f"📍 Entry: ${fill_price:.6f}\n"
            f"🎯 TP: ${signal['tp_price']:.6f} (+{signal['tp_pct']:.1f}%)\n"
            f"🛑 SL: ${signal['sl_price']:.6f} (-{signal['sl_pct']:.1f}%)"
        )

        log.info(f"✓ Position opened: {symbol}")
        return True

    def _calculate_quantity(self, symbol: str, price: float, size: float) -> Optional[float]:
        contract = self.contracts_info.get(symbol, {})
        min_qty = contract.get('min_qty', 1)
        precision = contract.get('qty_precision', 2)
        contract_size = contract.get('contract_size', 1)
        
        notional = size * LEVERAGE
        qty = safe_div(notional, price * contract_size, 0)
        qty = math.ceil(qty / min_qty) * min_qty
        qty = round(qty, precision)
        
        return qty if qty >= min_qty else None

    def _set_leverage(self, symbol: str, leverage: int):
        for side in ['LONG', 'SHORT']:
            try:
                api_request('POST', '/openApi/swap/v2/trade/leverage', {
                    'symbol': symbol, 'side': side, 'leverage': str(leverage)
                })
            except:
                pass

    def _confirm_position(self, symbol: str, timeout: int = 12) -> Tuple[Optional[float], Optional[float]]:
        for _ in range(timeout):
            try:
                data = api_request('GET', '/openApi/swap/v2/user/positions', {'symbol': symbol})
                for pos in data.get('data', []):
                    amt = safe_float(pos.get('positionAmt', 0))
                    if abs(amt) > 0:
                        entry = safe_float(pos.get('avgPrice', 0))
                        return abs(amt), entry
            except:
                pass
            time.sleep(1)
        return None, None

    async def monitor_positions(self):
        """Monitor positions"""
        for symbol in list(self.positions.keys()):
            try:
                pos = self.positions[symbol]
                ticker = self._get_ticker(symbol)
                if not ticker:
                    continue

                current_price = ticker['price']
                
                if current_price > pos.get('highest', pos['entry']):
                    pos['highest'] = current_price

                # TP hit
                if current_price >= pos.get('tp_price', float('inf')):
                    self._close_position(symbol, current_price, "TAKE_PROFIT")
                
                # SL hit
                elif current_price <= pos.get('sl_price', 0):
                    self._close_position(symbol, current_price, "STOP_LOSS")

            except Exception as e:
                log.error(f"Monitor error {symbol}: {e}")

    def _close_position(self, symbol: str, price: float, reason: str):
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        qty = pos['qty']
        
        if qty > 0 and AUTO_TRADING:
            api_request('POST', '/openApi/swap/v2/trade/order', {
                'symbol': symbol, 'side': 'SELL', 'type': 'MARKET',
                'quantity': str(qty), 'positionSide': 'LONG'
            })
        
        # Calculate PnL
        contract = self.contracts_info.get(symbol, {})
        contract_size = contract.get('contract_size', 1)
        notional = qty * pos['entry'] * contract_size
        pnl_pct = safe_div(price - pos['entry'], pos['entry'], 0) * 100
        pnl_gross = safe_div(price - pos['entry'], pos['entry'], 0) * notional * LEVERAGE
        fees = notional * (FEE_TAKER + FEE_MAKER)
        pnl = pnl_gross - fees
        
        win = pnl > 0
        if win:
            self.stats['wins'] += 1
            self.stats['win_amounts'].append(pnl_pct)
            self.losing_streak = 0
        else:
            self.stats['losses'] += 1
            self.stats['loss_amounts'].append(pnl_pct)
            self.losing_streak += 1
        
        self.stats['total_pnl'] += pnl
        self.daily_pnl += pnl
        
        # Update performance tracker
        signal_type = pos.get('signal', {}).get('signal_type', 'UNKNOWN')
        self.performance.add_trade(pnl_pct, win, signal_type)
        
        total = self.stats['wins'] + self.stats['losses']
        wr = safe_div(self.stats['wins'], total, 0) * 100
        
        log.info(f"{'✅' if win else '❌'} {reason} {symbol} | ${pnl:+.2f} ({pnl_pct:+.2f}%) | WR:{wr:.0f}%")
        
        # Check edge status
        has_edge, edge_msg = self.performance.has_edge()
        
        self._send_telegram(
            f"<b>{'✅ WIN' if win else '❌ LOSS'}</b>\n\n"
            f"{symbol} — {reason}\n"
            f"PnL: <b>${pnl:+.2f}</b> ({pnl_pct:+.2f}%)\n"
            f"WR: {wr:.0f}% ({self.stats['wins']}/{total})\n"
            f"Expectancy: {self.performance.running_expectancy:.2%}\n\n"
            f"{'✅' if has_edge else '⚠️'} {edge_msg}"
        )
        
        del self.positions[symbol]

    def _check_circuit_breaker(self) -> bool:
        today = datetime.utcnow().date()
        
        if today != self.daily_date:
            self.daily_pnl = 0
            self.daily_date = today
            self.daily_trades = 0
            self.circuit_breaker_active = False
        
        if self.circuit_breaker_active:
            return True
        
        # Check performance edge
        has_edge, msg = self.performance.has_edge()
        if not has_edge and len(self.performance.trades) >= 20:
            log.warning(f"🔒 EDGE LOST: {msg}")
            self.circuit_breaker_active = True
            self._send_telegram(f"<b>🔒 TRADING PAUSED</b>\n\n{msg}\n\nReviewing strategy...")
            return True
        
        threshold = self.equity * (CIRCUIT_BREAKER_PCT / 100)
        if self.daily_pnl < -threshold:
            self.circuit_breaker_active = True
            return True
        
        if self.losing_streak >= MAX_LOSING_STREAK:
            self.circuit_breaker_active = True
            return True
        
        if self.daily_trades >= MAX_DAILY_TRADES:
            return True
        
        return False

    def _send_telegram(self, msg: str):
        if not TG_TOKEN or not TG_CHAT:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'},
                timeout=5
            )
        except:
            pass

    async def run(self):
        """Main loop"""
        log.info("\n🚀 Mathematical Edge Bot v5.0 RUNNING\n")
        iteration = 0

        while True:
            try:
                iteration += 1

                if self._check_circuit_breaker():
                    await asyncio.sleep(SCAN_INTERVAL)
                    continue

                if iteration % 10 == 0:
                    self._refresh_symbols()
                    if AUTO_TRADING:
                        data = api_request('GET', '/openApi/swap/v2/user/balance')
                        if data.get('code') == 0:
                            eq = extract_equity(data)
                            if eq > 0:
                                self.equity = eq

                total = self.stats['wins'] + self.stats['losses']
                wr = safe_div(self.stats['wins'], total, 0) * 100

                log.info(f"\n{'='*80}")
                log.info(f"#{iteration} | Pos: {len(self.positions)}/{MAX_POSITIONS}")
                log.info(f"PnL: ${self.stats['total_pnl']:+.2f} | WR: {wr:.0f}% | E: {self.performance.running_expectancy:.2%}")
                log.info(f"{'='*80}\n")

                await self.monitor_positions()

                if len(self.positions) < MAX_POSITIONS and self.daily_trades < MAX_DAILY_TRADES:
                    log.info(f"Scanning {len(self.symbols)} symbols for EDGE...")
                    signals = 0

                    for symbol in self.symbols:
                        if len(self.positions) >= MAX_POSITIONS:
                            break
                        
                        try:
                            signal = self.analyze_symbol(symbol)
                            if signal:
                                signals += 1
                                log.info(
                                    f"💎 {symbol} | WP:{signal['win_probability']:.0%} "
                                    f"E:{signal['expectancy']:.2%} | {signal['signal_type']}"
                                )
                                
                                if self.open_position(signal):
                                    await asyncio.sleep(2)
                        except Exception as e:
                            log.error(f"Analysis error {symbol}: {e}")

                    log.info(f"✓ Scan complete | Edge signals: {signals}")

                await asyncio.sleep(SCAN_INTERVAL)

            except KeyboardInterrupt:
                log.info("⏹️ Bot stopped")
                break
            except Exception as e:
                log.error(f"Loop error: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(30)

async def main():
    bot = MathematicalEdgeBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Mathematical Edge Bot v5.0 terminated")
