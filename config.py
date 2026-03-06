"""
config.py — Configuración central v7
100% compatible con main.py (usa nombres en español: MAX_POSICIONES, CICLO_SEGUNDOS, etc.)
Los pares se cargan dinámicamente desde BingX al arrancar (symbols_loader.py)
"""
import os
from dotenv import load_dotenv
load_dotenv()

# ══════════════════════════════════════════════════════════
# CREDENCIALES
# ══════════════════════════════════════════════════════════
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")

# ══════════════════════════════════════════════════════════
# PARES — se reemplaza en runtime por symbols_loader
# Fallback: los 30 pares más líquidos de BingX Futuros
# ══════════════════════════════════════════════════════════
PARES = [
    "BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
    "DOGE-USDT","AVAX-USDT","LINK-USDT","ADA-USDT","DOT-USDT",
    "MATIC-USDT","LTC-USDT","UNI-USDT","ATOM-USDT","NEAR-USDT",
    "ARB-USDT","OP-USDT","APT-USDT","SUI-USDT","INJ-USDT",
    "TRX-USDT","FIL-USDT","ICP-USDT","AAVE-USDT","MKR-USDT",
    "TON-USDT","WLD-USDT","STX-USDT","PEPE-USDT","SHIB-USDT",
]
# Formato slash para ccxt — se sincroniza automáticamente
SYMBOLS = [p.replace("-", "/") for p in PARES]

# ══════════════════════════════════════════════════════════
# MODO
# ══════════════════════════════════════════════════════════
MODO_DEMO  = False   # FALSE = trades REALES en BingX
MODO_DEBUG = False

# ══════════════════════════════════════════════════════════
# OPERACIÓN / LOOP
# ══════════════════════════════════════════════════════════
CICLO_SEGUNDOS  = 300    # intervalo del loop principal (5 min)
LOOP_SECONDS    = 300
SCAN_BATCH_SIZE = 10     # pares por lote al escanear
COOLDOWN_BARS   = 3
TIMEFRAME       = "15m"
TIMEFRAME_HI    = "4h"

# ══════════════════════════════════════════════════════════
# CAPITAL Y RIESGO
# ══════════════════════════════════════════════════════════
LEVERAGE         = 5
RIESGO_POR_TRADE = 0.01    # 1% del balance por trade
RISK_PCT         = 0.01
MIN_USDT_BALANCE = 10.0
MAX_POSICIONES   = 5       # ← nombre que usa main.py
MAX_POSITIONS    = 5       # alias inglés
BALANCE_SNAPSHOT = 0.0
BALANCE_INICIAL  = 100.0

# ══════════════════════════════════════════════════════════
# BOLLINGER BANDS
# ══════════════════════════════════════════════════════════
BB_PERIODO = 20
BB_PERIOD  = 20
BB_STD     = 2.0
BB_SIGMA   = 2.0

# ══════════════════════════════════════════════════════════
# RSI
# ══════════════════════════════════════════════════════════
RSI_PERIODO  = 14
RSI_OVERSOLD = 30    # ← nombre que usa main.py
RSI_OB       = 70
RSI_OS       = 30
RSI_OB_SHORT = 70

# ══════════════════════════════════════════════════════════
# ATR / SL / TP
# ══════════════════════════════════════════════════════════
ATR_PERIODO  = 14
SL_ATR_MULT  = 1.5   # ← nombre que usa main.py
SL_ATR       = 1.5
TP_ATR_MULT  = 2.5   # ← nombre que usa main.py
TP_ATR       = 2.5
MIN_RR_RATIO = 1.5
RR_MINIMO    = 1.5

# ══════════════════════════════════════════════════════════
# EMA
# ══════════════════════════════════════════════════════════
EMA_FILTRO_ACTIVO = True   # ← nombre que usa main.py
EMA_TREND_ENABLED = True
EMA_PERIODO       = 200

# ══════════════════════════════════════════════════════════
# MULTI-TIMEFRAME
# ══════════════════════════════════════════════════════════
MTF_ACTIVO   = True    # ← nombre que usa main.py
MTF_RSI_MAX  = 60

# ══════════════════════════════════════════════════════════
# ADX
# ══════════════════════════════════════════════════════════
ADX_FILTER_ENABLED = True
ADX_PERIOD         = 14
ADX_MIN            = 18

# ══════════════════════════════════════════════════════════
# STOCH RSI
# ══════════════════════════════════════════════════════════
STOCH_RSI_ENABLED = True
STOCH_RSI_PERIOD  = 14
STOCH_RSI_K       = 3
STOCH_RSI_D       = 3
STOCH_RSI_OB      = 85
STOCH_RSI_OS      = 15

# ══════════════════════════════════════════════════════════
# VOLUMEN
# ══════════════════════════════════════════════════════════
VOLUME_CONFIRM_ENABLED = True
VOLUME_CONFIRM_MULT    = 0.7
VOLUMEN_MIN_USD        = 500_000
SPREAD_MAX_PCT         = 2.0
VOL_RELATIVO_ACTIVO    = True
VOL_RELATIVO_MIN       = 0.9

# ══════════════════════════════════════════════════════════
# TRAILING STOP
# ══════════════════════════════════════════════════════════
TRAILING_STOP_ACTIVO     = True    # ← nombre que usa main.py
TRAILING_STOP_ENABLED    = True
TRAILING_ATR_MULT        = 1.0    # ← nombre que usa main.py
TRAILING_STOP_ATR        = 1.5
TRAILING_ACTIVAR_PCT     = 0.5    # ← nombre que usa main.py
TRAILING_ACTIVATE_PCT    = 0.5
TRAILING_DYNAMIC_ENABLED = True
TRAILING_VOL_THRESHOLD   = 0.02
TRAILING_ATR_HIGH_VOL    = 2.0
TRAILING_ATR_LOW_VOL     = 1.2

# ══════════════════════════════════════════════════════════
# CIERRE PARCIAL
# ══════════════════════════════════════════════════════════
CIERRE_PARCIAL_ACTIVO = False   # ← nombre que usa main.py
CIERRE_PARCIAL_PCT    = 0.5    # ← nombre que usa main.py
CIERRE_PARCIAL_TP_PCT = 0.5    # ← nombre que usa main.py
BREAKEVEN_ACTIVO      = True   # ← nombre que usa main.py
PARTIAL_TP_ENABLED    = False
PARTIAL_TP_PCT        = 0.5

# ══════════════════════════════════════════════════════════
# MULTI-TP
# ══════════════════════════════════════════════════════════
MULTI_TP_ENABLED = True
TP1_ATR_MULT     = 1.2
TP2_ATR_MULT     = 2.0
TP1_CLOSE_PCT    = 0.30
TP2_CLOSE_PCT    = 0.40

# ══════════════════════════════════════════════════════════
# STALE TRADE
# ══════════════════════════════════════════════════════════
STALE_TRADE_ENABLED  = True
STALE_TRADE_HOURS    = 12
STALE_TRADE_MIN_MOVE = 0.005

# ══════════════════════════════════════════════════════════
# FILTRO HORARIO (UTC)
# ══════════════════════════════════════════════════════════
HORA_FILTRO_ACTIVO    = True
HORAS_EXCLUIDAS       = [1, 2, 3]
TIME_FILTER_ENABLED   = True
TIME_FILTER_OFF_START = 1
TIME_FILTER_OFF_END   = 4

# ══════════════════════════════════════════════════════════
# DIVERGENCIA RSI
# ══════════════════════════════════════════════════════════
DIVERGENCIA_ACTIVA  = True
DIVERGENCIA_VENTANA = 10

# ══════════════════════════════════════════════════════════
# SENTIMIENTO
# ══════════════════════════════════════════════════════════
SENTIMENT_ENABLED    = True
FEAR_GREED_ENABLED   = True
FG_EXTREME_FEAR      = 15
FG_EXTREME_GREED     = 88
CRYPTOPANIC_ENABLED  = False

# ══════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════
MAX_PNL_NEGATIVO_DIA  = -0.06   # ← nombre que usa main.py
MAX_PERDIDAS_SEGUIDAS = 5       # ← nombre que usa main.py

# ══════════════════════════════════════════════════════════
# LEARNER
# ══════════════════════════════════════════════════════════
LEARNER_MIN_TRADES     = 5
LEARNER_MIN_WR         = 38.0
LEARNER_MIN_PF         = 0.9
LEARNER_PENALIZACION_H = 24
LEARNER_CICLO_H        = 6
LEARNER_PERSISTIR      = True

# ══════════════════════════════════════════════════════════
# ESTRATEGIAS
# ══════════════════════════════════════════════════════════
STRATEGY_BB_RSI_ENABLED    = True
STRATEGY_EMA_CROSS_ENABLED = True
STRATEGY_BREAKOUT_ENABLED  = True
STRATEGY_FLASH_ARB_ENABLED = True
STRATEGY_MIN_SCORE         = 45

# ══════════════════════════════════════════════════════════
# LIQUIDEZ INSTITUCIONAL
# ══════════════════════════════════════════════════════════
LIQUIDITY_ENABLED     = True
LIQUIDITY_BLOCK_SCORE = 25
