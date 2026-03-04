# ══════════════════════════════════════════════════════════════
# config.py — COMPLETO para BB+RSI Elite v6
# Edita SOLO este archivo para ajustar parámetros.
# ══════════════════════════════════════════════════════════════

# ── VERSIÓN ────────────────────────────────────────────────────
VERSION = "v15"

# ── INDICADORES BASE ───────────────────────────────────────────
BB_PERIOD       = 20
BB_SIGMA        = 2.0
RSI_PERIOD      = 14
RSI_LONG        = 32      # RSI máximo para señal LONG
RSI_SHORT       = 68      # RSI mínimo para señal SHORT
RSI_OB          = 68      # alias de RSI_SHORT (usado en db.log_params)
RSI_OS          = 32      # alias de RSI_LONG  (usado en strategy.py SHORT)
SL_ATR          = 1.5     # multiplicador ATR para Stop Loss
SL_BUFFER       = 0.001
PARTIAL_TP_ATR  = 2.5
SMA_PERIOD      = 50

# ── GESTIÓN DE CAPITAL ─────────────────────────────────────────
LEVERAGE         = 2
RISK_PCT         = 0.02    # 2% del balance por trade
INITIAL_BAL      = 100.0
MIN_USDT_BALANCE = 5.0     # mínimo para ejecutar trades reales
BALANCE_SNAPSHOT = 0.0     # se actualiza en runtime (main.py)

# ── SEÑALES / FILTROS ──────────────────────────────────────────
SCORE_MIN        = 45
COOLDOWN_BARS    = 3
MIN_RR           = 1.2     # alias usado en backtest_final.py
MIN_RR_RATIO     = 1.2     # alias usado en main.py / validate_signal
TREND_LOOKBACK   = 10
TREND_THRESH     = 0.05
LONG_ONLY_UP     = False   # True = solo LONG cuando tendencia 4h es "bull"
SHORT_ENABLED    = True    # activar señales SHORT
REQUIRE_MOMENTUM = False   # requerir desaceleración antes de entrar

# ── PARES ACTIVOS ──────────────────────────────────────────────
SYMBOLS = [
    "BTC-USDT",
    "ETH-USDT",
    "BNB-USDT",
    "XRP-USDT",
    "ADA-USDT",
    "DOGE-USDT",
    "LINK-USDT",
    "LTC-USDT",
    "SOL-USDT",
    "AVAX-USDT",
]

# ── EXCHANGE / TIMEFRAMES ──────────────────────────────────────
TIMEFRAME        = "1h"    # timeframe principal
TIMEFRAME_HI     = "4h"    # timeframe alto para tendencia

# ── BUCLE PRINCIPAL ────────────────────────────────────────────
LOOP_SECONDS     = 300     # segundos entre ciclos (5 min)
MAX_POSITIONS    = 3       # máximo posiciones simultáneas
SCAN_BATCH_SIZE  = 5       # pares por lote (evita saturar API)

# ── TP PARCIAL (legacy) ────────────────────────────────────────
PARTIAL_TP_ENABLED = True  # activar TP parcial
PARTIAL_TP_PCT     = 0.5   # % a cerrar en primer TP

# ── MULTI-TP (3 niveles) ───────────────────────────────────────
MULTI_TP_ENABLED   = False # True = 3 niveles; False = TP parcial legacy
TP1_ATR_MULT       = 1.2
TP2_ATR_MULT       = 2.0
TP1_CLOSE_PCT      = 0.30  # cerrar 30% en TP1
TP2_CLOSE_PCT      = 0.40  # cerrar 40% en TP2

# ── TRAILING STOP ──────────────────────────────────────────────
TRAILING_STOP_ENABLED    = True
TRAILING_STOP_ATR        = 1.5    # multiplicador base
TRAILING_DYNAMIC_ENABLED = True   # ajusta según volatilidad
TRAILING_VOL_THRESHOLD   = 0.015  # ATR/precio > umbral = alta volatilidad
TRAILING_ATR_HIGH_VOL    = 2.0
TRAILING_ATR_LOW_VOL     = 1.0
TRAILING_ACTIVATE_PCT    = 0.5    # % ganancia mínima para activar

# ── STALE TRADE ────────────────────────────────────────────────
STALE_TRADE_ENABLED  = True
STALE_TRADE_HOURS    = 48     # horas sin movimiento → cerrar
STALE_TRADE_MIN_MOVE = 0.005  # movimiento mínimo (0.5%)

# ── FILTRO HORARIO ─────────────────────────────────────────────
TIME_FILTER_ENABLED   = True
TIME_FILTER_OFF_START = 2     # hora UTC inicio sin operar
TIME_FILTER_OFF_END   = 6     # hora UTC fin sin operar

# ── ADX ────────────────────────────────────────────────────────
ADX_FILTER_ENABLED = False    # False = no bloquear por ADX (más señales)
ADX_PERIOD         = 14
ADX_MIN            = 20

# ── STOCHASTIC RSI ─────────────────────────────────────────────
STOCH_RSI_ENABLED = True
STOCH_RSI_PERIOD  = 14
STOCH_RSI_K       = 3
STOCH_RSI_D       = 3
STOCH_RSI_OB      = 80    # sobrecomprado — bloquea LONG
STOCH_RSI_OS      = 20    # sobrevendido  — bloquea SHORT

# ── CONFIRMACIÓN DE VELA ───────────────────────────────────────
CANDLE_CONFIRM_ENABLED  = True
CANDLE_CONFIRM_MIN_BODY = 0.3  # cuerpo > 30% del rango total

# ── VOLUMEN ────────────────────────────────────────────────────
VOLUME_CONFIRM_ENABLED = False  # False = más señales
VOLUME_CONFIRM_MULT    = 0.8
VOLUME_SPIKE_ENABLED   = False  # bloquear volumen anómalo
VOLUME_SPIKE_MULT      = 5.0

# ── EMA TENDENCIA ──────────────────────────────────────────────
EMA_TREND_ENABLED = True
EMA_TREND_PERIOD  = 200

# ── MERCADO LATERAL ────────────────────────────────────────────
SIDEWAYS_BB_WIDTH  = 0.04  # BB width < 4% = lateral
SIDEWAYS_ATR_RATIO = 0.8   # ATR actual < 80% del ATR medio = lateral

# ── CIRCUIT BREAKER ────────────────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = 0.05  # -5% del balance en el día → pausar
CB_MAX_CONSECUTIVE_LOSS = 5     # 5 pérdidas seguidas → pausar

# ── API KEYS (Railway las inyecta como variables de entorno) ────
import os
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY",    "")
BINGX_SECRET     = os.environ.get("BINGX_SECRET",     "")
TELEGRAM_TOKEN   = os.environ.get("TG_TOKEN",         os.environ.get("TELEGRAM_TOKEN", ""))
TELEGRAM_CHAT_ID = os.environ.get("TG_CHAT_ID",       os.environ.get("TELEGRAM_CHAT_ID", ""))
