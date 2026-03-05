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
MIN_RR           = 1.2
MIN_RR_RATIO     = 1.2
TREND_LOOKBACK   = 10
TREND_THRESH     = 0.05
LONG_ONLY_UP     = False
SHORT_ENABLED    = True
REQUIRE_MOMENTUM = False

# ── PARES ACTIVOS — Top 15 ranking 2026-03-04 ─────────────────
SYMBOLS = [
    "RSR-USDT",       # WR:100% PF:999  $+0.83
    "LINK-USDT",      # WR: 67% PF:16.1 $+0.36
    "DEEP-USDT",      # WR: 67% PF: 8.6 $+0.33
    "BLESS-USDT",     # WR: 67% PF: 8.5 $+0.29
    "ZEC-USDT",       # WR: 67% PF: 7.3 $+0.37
    "VANRY-USDT",     # WR: 67% PF: 4.7 $+0.25
    "PROVE-USDT",     # WR: 50% PF: 4.1 $+0.19
    "AKE-USDT",       # WR: 50% PF: 3.8 $+0.43
    "BOME-USDT",      # WR: 50% PF: 3.6 $+0.20
    "BMT-USDT",       # WR: 60% PF: 3.6 $+0.17
    "ZEN-USDT",       # WR: 50% PF: 2.7 $+0.25
    "SUSHI-USDT",     # WR: 67% PF: 2.6 $+0.11
    "SQD-USDT",       # WR: 50% PF: 2.3 $+0.11
    "CRO-USDT",       # WR: 67% PF: 2.2 $+0.07
    "SOL-USDT",       # liquido y conocido
]

# ── EXCHANGE / TIMEFRAMES ──────────────────────────────────────
TIMEFRAME        = "1h"
TIMEFRAME_HI     = "4h"

# ── BUCLE PRINCIPAL ────────────────────────────────────────────
LOOP_SECONDS     = 300
MAX_POSITIONS    = 3
SCAN_BATCH_SIZE  = 5

# ── TP PARCIAL (legacy) ────────────────────────────────────────
PARTIAL_TP_ENABLED = True
PARTIAL_TP_PCT     = 0.5

# ── MULTI-TP (3 niveles) ───────────────────────────────────────
MULTI_TP_ENABLED   = False
TP1_ATR_MULT       = 1.2
TP2_ATR_MULT       = 2.0
TP1_CLOSE_PCT      = 0.30
TP2_CLOSE_PCT      = 0.40

# ── TRAILING STOP ──────────────────────────────────────────────
TRAILING_STOP_ENABLED    = True
TRAILING_STOP_ATR        = 1.5
TRAILING_DYNAMIC_ENABLED = True
TRAILING_VOL_THRESHOLD   = 0.015
TRAILING_ATR_HIGH_VOL    = 2.0
TRAILING_ATR_LOW_VOL     = 1.0
TRAILING_ACTIVATE_PCT    = 0.5

# ── STALE TRADE ────────────────────────────────────────────────
STALE_TRADE_ENABLED  = True
STALE_TRADE_HOURS    = 48
STALE_TRADE_MIN_MOVE = 0.005

# ── FILTRO HORARIO ─────────────────────────────────────────────
TIME_FILTER_ENABLED   = True
TIME_FILTER_OFF_START = 2
TIME_FILTER_OFF_END   = 6

# ── ADX ────────────────────────────────────────────────────────
ADX_FILTER_ENABLED = False
ADX_PERIOD         = 14
ADX_MIN            = 20

# ── STOCHASTIC RSI ─────────────────────────────────────────────
STOCH_RSI_ENABLED = True
STOCH_RSI_PERIOD  = 14
STOCH_RSI_K       = 3
STOCH_RSI_D       = 3
STOCH_RSI_OB      = 80
STOCH_RSI_OS      = 20

# ── CONFIRMACIÓN DE VELA ───────────────────────────────────────
CANDLE_CONFIRM_ENABLED  = True
CANDLE_CONFIRM_MIN_BODY = 0.3

# ── VOLUMEN ────────────────────────────────────────────────────
VOLUME_CONFIRM_ENABLED = False
VOLUME_CONFIRM_MULT    = 0.8
VOLUME_SPIKE_ENABLED   = False
VOLUME_SPIKE_MULT      = 5.0

# ── EMA TENDENCIA ──────────────────────────────────────────────
EMA_TREND_ENABLED = True
EMA_TREND_PERIOD  = 200

# ── MERCADO LATERAL ────────────────────────────────────────────
SIDEWAYS_BB_WIDTH  = 0.04
SIDEWAYS_ATR_RATIO = 0.8

# ── CIRCUIT BREAKER ────────────────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = 0.05
CB_MAX_CONSECUTIVE_LOSS = 5

# ── SENTIMENT ──────────────────────────────────────────────────
SENTIMENT_ENABLED  = False
FEAR_GREED_ENABLED = False

# ── API KEYS (Railway las inyecta como variables de entorno) ────
import os
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY",    "")
BINGX_SECRET     = os.environ.get("BINGX_SECRET",     "")
TELEGRAM_TOKEN   = os.environ.get("TG_TOKEN",         os.environ.get("TELEGRAM_TOKEN", ""))
TELEGRAM_CHAT_ID = os.environ.get("TG_CHAT_ID",       os.environ.get("TELEGRAM_CHAT_ID", ""))
<<<<<<< HEAD
CB_MAX_DAILY_LOSS_PCT   = 0.05
CB_MAX_CONSECUTIVE_LOSS = 5
=======
>>>>>>> c02cc637994de8190a6ef457a9f559b30ef4d425
