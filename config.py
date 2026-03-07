import os

# ══════════════════════════════════════════════════════
# config.py — BB+RSI ELITE v14.0  (FIXED & COMPLETE)
# ══════════════════════════════════════════════════════

VERSION = "v14.0"

# ── Credenciales ───────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")
BINGX_SECRET_KEY = BINGX_API_SECRET
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Modo ───────────────────────────────────────────────
TRADE_MODE    = os.getenv("TRADE_MODE", "paper")
MODO_DEMO     = (TRADE_MODE != "live")
MODO_DEBUG    = os.getenv("MODO_DEBUG", "false").lower() == "true"

# ── Temporalidad ───────────────────────────────────────
CANDLE_TF      = "15m"
TIMEFRAME      = CANDLE_TF
MTF_INTERVAL   = "1h"
TIMEFRAME_HI   = MTF_INTERVAL
POLL_INTERVAL  = int(os.getenv("POLL_INTERVAL", 900))
LOOP_SECONDS   = POLL_INTERVAL
CICLO_SEGUNDOS = POLL_INTERVAL

# ── Indicadores ────────────────────────────────────────
BB_PERIOD      = 20
BB_PERIODO     = BB_PERIOD
BB_SIGMA       = 2.0
BB_STD         = BB_SIGMA
RSI_PERIOD     = 14
RSI_LONG       = 35
RSI_SHORT      = 65
RSI_OVERSOLD   = RSI_LONG
RSI_OVERBOUGHT = RSI_SHORT
PARTIAL_TP_ATR = 2.0
SMA_PERIOD     = 50

# ── Riesgo ────────────────────────────────────────────
LEVERAGE         = int(os.getenv("LEVERAGE", 3))
RISK_PCT         = float(os.getenv("RISK_PCT", 0.015))
RIESGO_POR_TRADE = RISK_PCT
INITIAL_BAL      = float(os.getenv("INITIAL_BAL", 100.0))
BALANCE_INICIAL  = INITIAL_BAL
MIN_RR           = 1.3
SL_BUFFER        = 0.002
SL_ATR_MULT      = 1.5
TP_ATR_MULT      = 2.5
SCORE_MIN        = 35
STRATEGY_MIN_SCORE = SCORE_MIN
RR_MINIMO        = MIN_RR
COOLDOWN_BARS    = 2

# ── Posiciones ────────────────────────────────────────
MAX_CONCURRENT_POS = int(os.getenv("MAX_POSITIONS", 3))
MAX_POSICIONES     = MAX_CONCURRENT_POS

# ── Circuit breaker ────────────────────────────────────
MAX_DAILY_LOSS_PCT      = 0.08
MAX_DRAWDOWN_PCT        = 0.15
CIRCUIT_BREAKER_LOSS    = 3
CB_MAX_DAILY_LOSS_PCT   = MAX_DAILY_LOSS_PCT
CB_MAX_CONSECUTIVE_LOSS = CIRCUIT_BREAKER_LOSS

# ── Trailing SL ───────────────────────────────────────
TRAIL_FROM_START     = True
TRAIL_ATR_MULT_INIT  = 1.8
TRAIL_ATR_MULT_AFTER = 1.2
TRAILING_STOP_ACTIVO = TRAIL_FROM_START
TRAILING_ATR_MULT    = TRAIL_ATR_MULT_INIT

# ── Cierre parcial ─────────────────────────────────────
CIERRE_PARCIAL_ACTIVO = True
CIERRE_PARCIAL_PCT    = 0.5

# ── EMA filtro ────────────────────────────────────────
EMA_FILTRO_ACTIVO = True
EMA_PERIODO       = 50

# ── ATR sizing ────────────────────────────────────────
ATR_SIZING      = True
ATR_SIZING_BASE = 0.02

# ── Re-entry ──────────────────────────────────────────
REENTRY_ENABLED   = True
REENTRY_COOLDOWN  = 2
REENTRY_SCORE_MIN = 50

# ── Volumen ───────────────────────────────────────────
VOLUME_FILTER    = True
VOLUME_MA_PERIOD = 20
VOLUME_MIN_RATIO = 0.6
VOLUMEN_MIN_USD  = 300_000
SPREAD_MAX_PCT   = 0.8

# ── MTF ───────────────────────────────────────────────
MTF_ENABLED       = True
MTF_ACTIVO        = MTF_ENABLED
MTF_BLOCK_COUNTER = True
MTF_RSI_MAX       = RSI_SHORT

# ── Horario ───────────────────────────────────────────
HORA_FILTRO_ACTIVO = False
HORAS_EXCLUIDAS    = []

# ── Tendencia ─────────────────────────────────────────
TREND_LOOKBACK = 8
TREND_THRESH   = 0.03

# ── Scan / misc ───────────────────────────────────────
SCAN_BATCH_SIZE = 15
ALERT_ALWAYS    = True

# ── Liquidez (desactivado — añade latencia) ────────────
LIQUIDITY_ENABLED     = False
LIQUIDITY_BLOCK_SCORE = 20

# ── Dashboard ─────────────────────────────────────────
DASHBOARD_PORT    = int(os.getenv("PORT", 8080))
DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "false").lower() == "true"

# ── Learner ───────────────────────────────────────────
LEARNER_PERSISTIR      = False
LEARNER_CICLO_H        = 4
LEARNER_MIN_TRADES     = 8
LEARNER_MIN_WR         = 38.0
LEARNER_MIN_PF         = 0.9
LEARNER_PENALIZACION_H = 12

# ── Pares (alta liquidez + volatilidad probada) ────────
SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
    "DOGE-USDT", "AVAX-USDT", "LINK-USDT", "ADA-USDT", "DOT-USDT",
    "ARB-USDT", "OP-USDT", "NEAR-USDT", "INJ-USDT", "TIA-USDT",
    "PEPE-USDT", "WIF-USDT", "BONK-USDT", "LTC-USDT", "ATOM-USDT",
    "AAVE-USDT", "UNI-USDT", "FTM-USDT", "SUSHI-USDT", "ZEC-USDT",
    "CRO-USDT",
]
PARES = SYMBOLS
