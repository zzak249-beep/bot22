"""
config.py — Configuración central del bot v6
Nuevos parámetros: EMA, multi-timeframe, trailing stop, cierre parcial
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CREDENCIALES (se leen del .env)
# ============================================================
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# PARES ACTIVOS — TOP 15 del último backtest
# ============================================================
PARES = [
    "RSR-USDT",
    "LINK-USDT",
    "ZEC-USDT",
    "AKE-USDT",
    "BOME-USDT",
    "DEEP-USDT",
    "BLESS-USDT",
    "VANRY-USDT",
    "PROVE-USDT",
    "BMT-USDT",
    "ZEN-USDT",
    "SUSHI-USDT",
    "SQD-USDT",
    "CRO-USDT",
    "SOL-USDT",
]

# ============================================================
# SEÑALES DE ENTRADA — Indicadores base
# ============================================================
RSI_PERIODO      = 14
RSI_OVERSOLD     = 30          # Entrada LONG cuando RSI < este valor
BB_PERIODO       = 20
BB_STD           = 2.0         # Bandas de Bollinger σ
ATR_PERIODO      = 14
VOLUMEN_MIN_USD  = 1_000_000   # Volumen mínimo 24h en USD
SPREAD_MAX_PCT   = 1.5         # Spread máximo permitido %

# ============================================================
# FILTROS NUEVOS v6
# ============================================================
# EMA tendencia
EMA_PERIODO           = 50     # EMA en temporalidad superior (1h)
EMA_FILTRO_ACTIVO     = True   # True = solo LONG si precio > EMA50 en 1h

# Multi-timeframe
MTF_ACTIVO            = True   # True = confirmar con RSI en 15m
MTF_RSI_MAX           = 55     # RSI en 15m debe ser < este valor (no sobrecomprado)

# Volumen relativo
VOL_RELATIVO_ACTIVO   = True   # True = filtrar por volumen relativo
VOL_RELATIVO_MIN      = 1.1    # Volumen actual >= X veces la media de 20 velas

# Divergencia RSI
DIVERGENCIA_ACTIVA    = True   # True = bonus de score por divergencia alcista
DIVERGENCIA_VENTANA   = 10     # Velas para detectar divergencia

# Filtro horario (UTC)
HORA_FILTRO_ACTIVO    = True   # True = evitar horas de baja liquidez
HORAS_EXCLUIDAS       = [0, 1, 2, 3]  # 00-03 UTC → baja liquidez

# ============================================================
# GESTIÓN DE RIESGO
# ============================================================
LEVERAGE              = 2      # Apalancamiento (conservador)
SL_ATR_MULT           = 1.5    # Stop Loss = entrada - (ATR × mult)
TP_ATR_MULT           = 2.5    # Take Profit = entrada + (ATR × mult)
RIESGO_POR_TRADE      = 0.02   # 2% del balance por trade
MAX_POSICIONES        = 5      # Máximo posiciones simultáneas
RR_MINIMO             = 1.5    # Risk/Reward mínimo para entrar

# ============================================================
# GESTIÓN ACTIVA DE POSICIÓN (NUEVO v6)
# ============================================================
TRAILING_STOP_ACTIVO  = True   # True = trailing stop dinámico basado en ATR
TRAILING_ATR_MULT     = 1.0    # Trailing = precio - (ATR × mult)
TRAILING_ACTIVAR_PCT  = 0.5    # Activar trailing cuando ganancia >= 50% del TP

CIERRE_PARCIAL_ACTIVO = True   # True = cerrar 50% al llegar al TP parcial
CIERRE_PARCIAL_PCT    = 0.5    # Cerrar este % de la posición
CIERRE_PARCIAL_TP_PCT = 0.5    # Activar al alcanzar este % del camino al TP
BREAKEVEN_ACTIVO      = True   # True = mover SL a breakeven tras cierre parcial

# ============================================================
# COMPOUND — REINVERSIÓN AUTOMÁTICA
# ============================================================
COMPOUND              = True   # True = tamaño dinámico según balance real
BALANCE_INICIAL       = 100.0  # Solo para referencia inicial

# ============================================================
# CIRCUIT BREAKER
# ============================================================
MAX_PNL_NEGATIVO_DIA  = -0.05  # -5% del balance → parar el día
MAX_PERDIDAS_SEGUIDAS = 4      # 4 pérdidas seguidas → pausa 1 hora

# ============================================================
# OPERACIÓN
# ============================================================
CICLO_SEGUNDOS        = 300    # Intervalo del loop principal (5 min)
MODO_DEMO             = True   # True = simular sin órdenes reales
MODO_DEBUG            = True   # True = logs detallados

# ============================================================
# LEARNER — Umbrales para penalizar/rehabilitar pares
# ============================================================
LEARNER_MIN_TRADES     = 5     # Mínimo trades para evaluar un par
LEARNER_MIN_WR         = 40.0  # WR mínimo para seguir usando el par
LEARNER_MIN_PF         = 1.0   # PF mínimo
LEARNER_PENALIZACION_H = 24    # Horas de penalización
LEARNER_CICLO_H        = 6     # Cada cuántas horas evalúa el learner
LEARNER_PERSISTIR      = True  # True = guardar parámetros aprendidos en disco
