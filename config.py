"""
config.py — BB+RSI ELITE v6
Lee API keys y credenciales de variables de entorno (Railway/local .env).
Todos los parámetros tienen valores por defecto seguros.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════
# CREDENCIALES — leídas de variables de entorno
# ══════════════════════════════════════════════════════
BINGX_API_KEY  = os.environ.get("BINGX_API_KEY", "")
BINGX_SECRET   = os.environ.get("BINGX_SECRET", "")

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TG_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TG_CHAT_ID", "")

# ══════════════════════════════════════════════════════
# PARÁMETROS BOLLINGER BANDS + RSI
# ══════════════════════════════════════════════════════
BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14
RSI_OB         = 45      # RSI máximo para LONG (RSI_OB = oversold threshold)
RSI_OS         = 55      # RSI mínimo para SHORT (RSI_OS = overbought threshold)
RSI_LONG       = 30
RSI_SHORT      = 70

# ══════════════════════════════════════════════════════
# GESTIÓN DE RIESGO
# ══════════════════════════════════════════════════════
LEVERAGE           = int(os.environ.get("LEVERAGE", 2))
RISK_PCT           = float(os.environ.get("RISK_PCT", 0.02))
INITIAL_BAL        = 100.0
MIN_USDT_BALANCE   = 5.0        # balance mínimo para ejecutar — si hay menos, señal manual
BALANCE_SNAPSHOT   = 0.0        # se actualiza en main.py al arrancar
MAX_POSITIONS      = int(os.environ.get("MAX_POSITIONS", 3))

# ══════════════════════════════════════════════════════
# SL / TP
# ══════════════════════════════════════════════════════
SL_ATR          = 2.0           # distancia SL en múltiplos de ATR
SL_BUFFER       = 0.003
MIN_RR          = 1.5
MIN_RR_RATIO    = 1.5
PARTIAL_TP_ATR  = 2.0
PARTIAL_TP_ENABLED = True
PARTIAL_TP_PCT  = 0.5           # cerrar 50% en TP parcial

# ══════════════════════════════════════════════════════
# MULTI-TP (3 niveles)
# ══════════════════════════════════════════════════════
MULTI_TP_ENABLED  = True
TP1_ATR_MULT      = 1.2         # TP1 @ 1.2x ATR — cerrar 30%
TP2_ATR_MULT      = 2.0         # TP2 @ 2.0x ATR — cerrar 40%
TP1_CLOSE_PCT     = 0.30
TP2_CLOSE_PCT     = 0.40

# ══════════════════════════════════════════════════════
# TRAILING STOP
# ══════════════════════════════════════════════════════
TRAILING_STOP_ENABLED    = True
TRAILING_STOP_ATR        = 1.5
TRAILING_ACTIVATE_PCT    = 1.0   # activar trailing si ganancia > 1%
TRAILING_DYNAMIC_ENABLED = True
TRAILING_VOL_THRESHOLD   = 0.02  # ATR/precio > 2% = alta volatilidad
TRAILING_ATR_HIGH_VOL    = 2.0   # trailing amplio en alta vol
TRAILING_ATR_LOW_VOL     = 1.0   # trailing ajustado en baja vol

# ══════════════════════════════════════════════════════
# STALE TRADE — cerrar si no se mueve
# ══════════════════════════════════════════════════════
STALE_TRADE_ENABLED  = True
STALE_TRADE_HOURS    = 48        # máximo 48h abierto sin movimiento
STALE_TRADE_MIN_MOVE = 0.005     # movimiento mínimo desde entrada (0.5%)

# ══════════════════════════════════════════════════════
# FILTROS DE SEÑAL
# ══════════════════════════════════════════════════════
SCORE_MIN      = 45
COOLDOWN_BARS  = 3

# ADX — fuerza de tendencia
ADX_FILTER_ENABLED = True
ADX_PERIOD         = 14
ADX_MIN            = 20         # ADX mínimo para operar

# StochRSI
STOCH_RSI_ENABLED = True
STOCH_RSI_PERIOD  = 14
STOCH_RSI_K       = 3
STOCH_RSI_D       = 3
STOCH_RSI_OB      = 80          # bloquear LONG si StochRSI > 80
STOCH_RSI_OS      = 20          # bloquear SHORT si StochRSI < 20

# Confirmación de vela
CANDLE_CONFIRM_ENABLED  = True
CANDLE_CONFIRM_MIN_BODY = 0.3   # cuerpo debe ser >= 30% del rango

# Volumen
VOLUME_CONFIRM_ENABLED = True
VOLUME_CONFIRM_MULT    = 0.7    # volumen actual >= 70% del promedio 20 barras
VOLUME_SPIKE_ENABLED   = True
VOLUME_SPIKE_MULT      = 5.0    # bloquear si volumen > 5x promedio (anomalía)

# EMA200
EMA_TREND_ENABLED = False       # filtro adicional por EMA200

# Mercado lateral
SIDEWAYS_BB_WIDTH  = 0.04       # BB width < 4% = mercado lateral
SIDEWAYS_ATR_RATIO = 0.8        # ATR actual < 80% del ATR promedio

# SHORT
SHORT_ENABLED    = True
REQUIRE_MOMENTUM = True

# LONG solo en tendencia alcista
LONG_ONLY_UP = True

# ══════════════════════════════════════════════════════
# FILTRO HORARIO
# ══════════════════════════════════════════════════════
TIME_FILTER_ENABLED   = True
TIME_FILTER_OFF_START = 2       # hora UTC inicio baja liquidez
TIME_FILTER_OFF_END   = 6       # hora UTC fin baja liquidez

# ══════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ══════════════════════════════════════════════════════
CB_MAX_DAILY_LOSS_PCT   = 0.05  # pausar si pierde >5% del balance en el día
CB_MAX_CONSECUTIVE_LOSS = 5     # pausar tras 5 pérdidas consecutivas sin ganancias
MAX_DAILY_LOSS_PCT      = 0.05  # alias para compatibilidad
MAX_CONSECUTIVE_LOSS    = 5

# ══════════════════════════════════════════════════════
# TENDENCIA / TREND
# ══════════════════════════════════════════════════════
TREND_LOOKBACK = 8
TREND_THRESH   = 0.25
SMA_PERIOD     = 50

# ══════════════════════════════════════════════════════
# SENTIMIENTO
# ══════════════════════════════════════════════════════
SENTIMENT_ENABLED  = False
FEAR_GREED_ENABLED = False

# ══════════════════════════════════════════════════════
# TIMEFRAMES Y BUCLE
# ══════════════════════════════════════════════════════
TIMEFRAME      = "1h"
TIMEFRAME_HI   = "4h"
LOOP_SECONDS   = int(os.environ.get("LOOP_SECONDS", 300))   # 5 min
SCAN_BATCH_SIZE = 10

# ══════════════════════════════════════════════════════
# PARES — Top 15 del scanner 2026-03-04
# ══════════════════════════════════════════════════════
SYMBOLS = [
    "RSR-USDT",
    "LINK-USDT",
    "DEEP-USDT",
    "ZEC-USDT",
    "VANRY-USDT",
    "AKE-USDT",
    "BOME-USDT",
    "BMT-USDT",
    "ZEN-USDT",
    "SUSHI-USDT",
    "SQD-USDT",
    "CRO-USDT",
    "SOL-USDT",
    "LTC-USDT",
    "PROVE-USDT",
]

VERSION = "v6"
