"""
config.py — Configuración central BB+RSI BOT ELITE v7
Compatible 100% con main.py v6 + strategy.py v7 + liquidity.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

# ══════════════════════════════════════════════════════════
# CREDENCIALES (Railway → Settings → Variables)
# ══════════════════════════════════════════════════════════
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")

# ══════════════════════════════════════════════════════════
# PARES
# ══════════════════════════════════════════════════════════
PARES = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
    "LINK-USDT", "AVAX-USDT", "NEAR-USDT", "ARB-USDT", "OP-USDT",
    "LTC-USDT", "DOGE-USDT", "MATIC-USDT", "APT-USDT", "SUI-USDT",
    "TON-USDT", "UNI-USDT", "ATOM-USDT", "DOT-USDT", "INJ-USDT",
]
SYMBOLS        = [p.replace("-", "/") for p in PARES]

# ══════════════════════════════════════════════════════════
# MODO
# ══════════════════════════════════════════════════════════
MODO_DEMO  = False   # ← FALSE = trades REALES en BingX
MODO_DEBUG = False

# ══════════════════════════════════════════════════════════
# OPERACIÓN
# ══════════════════════════════════════════════════════════
LOOP_SECONDS    = 300
SCAN_BATCH_SIZE = 5
COOLDOWN_BARS   = 3
TIMEFRAME       = "15m"
TIMEFRAME_HI    = "4h"
CICLO_SEGUNDOS  = 300

# ══════════════════════════════════════════════════════════
# CAPITAL Y RIESGO
# ══════════════════════════════════════════════════════════
RISK_PCT         = 0.01
RIESGO_POR_TRADE = 0.01
LEVERAGE         = 5
MIN_USDT_BALANCE = 10.0
MAX_POSITIONS    = 3
BALANCE_SNAPSHOT = 0.0
BALANCE_INICIAL  = 100.0

# ══════════════════════════════════════════════════════════
# BOLLINGER BANDS
# ══════════════════════════════════════════════════════════
BB_PERIOD = 20
BB_SIGMA  = 2.0
BB_STD    = 2.0

# ══════════════════════════════════════════════════════════
# RSI
# ══════════════════════════════════════════════════════════
RSI_PERIODO  = 14
RSI_OB       = 70
RSI_OS       = 30
RSI_OVERSOLD = 30

# ══════════════════════════════════════════════════════════
# ATR / SL / TP
# ══════════════════════════════════════════════════════════
ATR_PERIODO  = 14
SL_ATR       = 1.5
SL_ATR_MULT  = 1.5
TP_ATR_MULT  = 2.5
MIN_RR_RATIO = 1.5

# ══════════════════════════════════════════════════════════
# EMA
# ══════════════════════════════════════════════════════════
EMA_TREND_ENABLED = True
EMA_PERIODO       = 200
EMA_FILTRO_ACTIVO = True

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
# CONFIRMACIÓN DE VELA
# ══════════════════════════════════════════════════════════
CANDLE_CONFIRM_ENABLED  = True
CANDLE_CONFIRM_MIN_BODY = 0.3

# ══════════════════════════════════════════════════════════
# VOLUMEN
# ══════════════════════════════════════════════════════════
VOLUME_CONFIRM_ENABLED = True
VOLUME_CONFIRM_MULT    = 0.7
VOLUMEN_MIN_USD        = 500_000
SPREAD_MAX_PCT         = 2.0
MTF_ACTIVO             = True
MTF_RSI_MAX            = 60
VOL_RELATIVO_ACTIVO    = True
VOL_RELATIVO_MIN       = 0.9

# ══════════════════════════════════════════════════════════
# MULTI-TP
# ══════════════════════════════════════════════════════════
MULTI_TP_ENABLED  = True
TP1_ATR_MULT      = 1.2
TP2_ATR_MULT      = 2.0
TP1_CLOSE_PCT     = 0.30
TP2_CLOSE_PCT     = 0.40
PARTIAL_TP_ENABLED = False
PARTIAL_TP_PCT     = 0.50

# ══════════════════════════════════════════════════════════
# TRAILING STOP
# ══════════════════════════════════════════════════════════
TRAILING_STOP_ENABLED    = True
TRAILING_STOP_ACTIVO     = True
TRAILING_DYNAMIC_ENABLED = True
TRAILING_STOP_ATR        = 1.5
TRAILING_ATR_MULT        = 1.0
TRAILING_VOL_THRESHOLD   = 0.02
TRAILING_ATR_HIGH_VOL    = 2.0
TRAILING_ATR_LOW_VOL     = 1.2
TRAILING_ACTIVATE_PCT    = 0.5
TRAILING_ACTIVAR_PCT     = 0.5
CIERRE_PARCIAL_ACTIVO    = False
CIERRE_PARCIAL_PCT       = 0.5
CIERRE_PARCIAL_TP_PCT    = 0.5
BREAKEVEN_ACTIVO         = True

# ══════════════════════════════════════════════════════════
# STALE TRADE
# ══════════════════════════════════════════════════════════
STALE_TRADE_ENABLED  = True
STALE_TRADE_HOURS    = 12
STALE_TRADE_MIN_MOVE = 0.005

# ══════════════════════════════════════════════════════════
# FILTRO HORARIO (UTC)
# ══════════════════════════════════════════════════════════
TIME_FILTER_ENABLED   = True
TIME_FILTER_OFF_START = 1
TIME_FILTER_OFF_END   = 4
HORA_FILTRO_ACTIVO    = True
HORAS_EXCLUIDAS       = [1, 2, 3]

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
MAX_PNL_NEGATIVO_DIA  = -0.06
MAX_PERDIDAS_SEGUIDAS = 5

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
# DIVERGENCIA RSI
# ══════════════════════════════════════════════════════════
DIVERGENCIA_ACTIVA  = True
DIVERGENCIA_VENTANA = 10

# ══════════════════════════════════════════════════════════
# ESTRATEGIAS INDEPENDIENTES
# ══════════════════════════════════════════════════════════
STRATEGY_BB_RSI_ENABLED    = True   # Bollinger + RSI
STRATEGY_EMA_CROSS_ENABLED = True   # EMA 3/8/21/55/200 continuaciones+reversiones
STRATEGY_BREAKOUT_ENABLED  = True   # Ruptura de rango
STRATEGY_FLASH_ARB_ENABLED = True   # Spread precio multi-capa
STRATEGY_MIN_SCORE         = 45     # Score mínimo para ejecutar

# ══════════════════════════════════════════════════════════
# LIQUIDEZ INSTITUCIONAL
# ══════════════════════════════════════════════════════════
LIQUIDITY_ENABLED     = True
LIQUIDITY_BLOCK_SCORE = 25     # score ≤25 o ≥75 bloquea señal contraria
