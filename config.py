# ══════════════════════════════════════════════════════
# config.py — BingX RSI+BB Bot v5.0 AGRESIVO
# 🔥 DINERO REAL - Railway Production
# ══════════════════════════════════════════════════════
import os

# ── CREDENCIALES (desde Railway Variables) ──────────
BINGX_API_KEY    = os.environ.get("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── MODO ────────────────────────────────────────────
MODO_DEMO        = False    # 🔥 DINERO REAL
MODO_DEBUG       = False
BALANCE_INICIAL  = 100.0

# ── INDICADORES ─────────────────────────────────────
RSI_PERIODO   = 14
RSI_OVERSOLD  = 38    # 🔥 Más permisivo
RSI_OVERBOUGHT= 62    # 🔥 Más permisivo
BB_PERIODO    = 20
BB_STD        = 2.0
ATR_PERIODO   = 14

# ── FILTRO DE SCORE ─────────────────────────────────
SCORE_MIN     = 65    # 🔥 AGRESIVO

# ── CALIDAD DE MERCADO ──────────────────────────────
VOLUMEN_MIN_USD = 400_000
SPREAD_MAX_PCT  = 2.0

# ── SL / TP ─────────────────────────────────────────
SL_ATR_MULT   = 1.2    # 🔥 SL ajustado
TP_ATR_MULT   = 4.0    # 🔥 TP amplio (R:R 3.3)
RR_MINIMO     = 1.5

# ── RIESGO ──────────────────────────────────────────
RIESGO_POR_TRADE = 0.03   # 🔥 3% por trade
LEVERAGE         = 5      # 🔥 5x leverage
MAX_POSICIONES   = 8      # 🔥 8 posiciones

# ── CIRCUIT BREAKER ─────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = 0.08
CB_MAX_CONSECUTIVE_LOSS = 5

# ── OPERACIÓN ───────────────────────────────────────
LOOP_SECONDS  = 300
CICLO_SEGUNDOS = 300

# ── LEARNER ─────────────────────────────────────────
LEARNER_CICLO_H = 4
LEARNER_MIN_TRADES = 5
LEARNER_MIN_WR = 30
LEARNER_MIN_PF = 0.7
LEARNER_PENALIZACION_H = 3

VERSION = "BingX-RSI+BB-v5.0-AGRESIVO-REAL"
