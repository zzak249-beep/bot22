"""
config.py — SMC Bot v8.0 [BingX Edition]
==========================================
Generado automáticamente cubriendo todos los módulos:
main.py, analizar_v8.py, memoria.py
"""

import os

# ══════════════════════════════════════════════════════════════
# VERSIÓN
# ══════════════════════════════════════════════════════════════
VERSION = os.getenv("VERSION", "SMC Bot BingX v8.0")

# ══════════════════════════════════════════════════════════════
# API KEYS (variables de entorno en Railway)
# ══════════════════════════════════════════════════════════════
API_KEY    = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")

# ══════════════════════════════════════════════════════════════
# EXCHANGE
# ══════════════════════════════════════════════════════════════
EXCHANGE   = "bingx"
LEVERAGE   = int(os.getenv("LEVERAGE", "10"))
MODO_DEMO  = os.getenv("MODO_DEMO", "false").lower() == "true"

# ══════════════════════════════════════════════════════════════
# MEMORIA / PERSISTENCIA
# ══════════════════════════════════════════════════════════════
MEMORY_DIR = os.getenv("MEMORY_DIR", "/app/data")

# ══════════════════════════════════════════════════════════════
# TRADING — TAMAÑO DE POSICIÓN
# ══════════════════════════════════════════════════════════════
TRADE_USDT_BASE = float(os.getenv("TRADE_USDT_BASE", "50"))
TRADE_USDT_MAX  = float(os.getenv("TRADE_USDT_MAX",  "200"))

# ══════════════════════════════════════════════════════════════
# COMPOUNDING
# ══════════════════════════════════════════════════════════════
COMPOUND_STEP_USDT = float(os.getenv("COMPOUND_STEP_USDT", "100"))
COMPOUND_ADD_USDT  = float(os.getenv("COMPOUND_ADD_USDT",  "10"))

# ══════════════════════════════════════════════════════════════
# TIMEFRAMES Y VELAS
# ══════════════════════════════════════════════════════════════
TIMEFRAME     = os.getenv("TIMEFRAME", "5m")
CANDLES_LIMIT = int(os.getenv("CANDLES_LIMIT", "200"))

# MTF (Multi-TimeFrame)
MTF_ACTIVO    = os.getenv("MTF_ACTIVO", "true").lower() == "true"
MTF_TIMEFRAME = os.getenv("MTF_TIMEFRAME", "1h")
MTF_CANDLES   = int(os.getenv("MTF_CANDLES", "100"))
MTF_4H_ACTIVO = os.getenv("MTF_4H_ACTIVO", "true").lower() == "true"

# ══════════════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════════════
ATR_PERIOD     = int(os.getenv("ATR_PERIOD", "14"))
RSI_PERIOD     = int(os.getenv("RSI_PERIOD", "14"))
EMA_FAST       = int(os.getenv("EMA_FAST",   "21"))
EMA_SLOW       = int(os.getenv("EMA_SLOW",   "50"))
EMA_LOCAL_FAST = int(os.getenv("EMA_LOCAL_FAST", "9"))
EMA_LOCAL_SLOW = int(os.getenv("EMA_LOCAL_SLOW", "21"))

RSI_BUY_MAX  = float(os.getenv("RSI_BUY_MAX",  "60"))
RSI_SELL_MIN = float(os.getenv("RSI_SELL_MIN", "40"))

VWAP_ACTIVO = os.getenv("VWAP_ACTIVO", "true").lower() == "true"
VWAP_PCT    = float(os.getenv("VWAP_PCT", "0.3"))

# ══════════════════════════════════════════════════════════════
# SL / TP
# ══════════════════════════════════════════════════════════════
SL_ATR_MULT      = float(os.getenv("SL_ATR_MULT",      "1.5"))
TP_ATR_MULT      = float(os.getenv("TP_ATR_MULT",      "2.0"))
TP_DIST_MULT     = float(os.getenv("TP_DIST_MULT",     "1.2"))
TP1_DIST_MULT    = float(os.getenv("TP1_DIST_MULT",    "0.5"))
PARTIAL_TP1_MULT = float(os.getenv("PARTIAL_TP1_MULT", "1.0"))
MIN_RR           = float(os.getenv("MIN_RR",           "1.0"))

# ══════════════════════════════════════════════════════════════
# TRAILING STOP
# ══════════════════════════════════════════════════════════════
TRAILING_ACTIVO    = os.getenv("TRAILING_ACTIVO", "true").lower() == "true"
TRAILING_ACTIVAR   = float(os.getenv("TRAILING_ACTIVAR",   "1.5"))
TRAILING_DISTANCIA = float(os.getenv("TRAILING_DISTANCIA", "1.0"))

# ══════════════════════════════════════════════════════════════
# PARTIAL TP
# ══════════════════════════════════════════════════════════════
PARTIAL_TP_ACTIVO = os.getenv("PARTIAL_TP_ACTIVO", "true").lower() == "true"

# ══════════════════════════════════════════════════════════════
# TIME EXIT
# ══════════════════════════════════════════════════════════════
TIME_EXIT_HORAS = float(os.getenv("TIME_EXIT_HORAS", "8.0"))

# ══════════════════════════════════════════════════════════════
# SCORING Y POSICIONES
# ══════════════════════════════════════════════════════════════
SCORE_MIN      = int(os.getenv("SCORE_MIN", "4"))
MAX_POSICIONES = int(os.getenv("MAX_POSICIONES", "3"))

# ══════════════════════════════════════════════════════════════
# COOLDOWN (en velas)
# ══════════════════════════════════════════════════════════════
COOLDOWN_VELAS = int(os.getenv("COOLDOWN_VELAS", "3"))

# ══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER DIARIO
# ══════════════════════════════════════════════════════════════
MAX_PERDIDA_DIA = float(os.getenv("MAX_PERDIDA_DIA", "30.0"))

# ══════════════════════════════════════════════════════════════
# FILTROS SMC
# ══════════════════════════════════════════════════════════════
OB_ACTIVO               = os.getenv("OB_ACTIVO",  "true").lower() == "true"
OB_LOOKBACK             = int(os.getenv("OB_LOOKBACK", "20"))
BOS_ACTIVO              = os.getenv("BOS_ACTIVO", "true").lower() == "true"
FVG_MIN_PIPS            = float(os.getenv("FVG_MIN_PIPS", "0.0"))
ASIA_RANGE_ACTIVO       = os.getenv("ASIA_RANGE_ACTIVO", "true").lower() == "true"
VELA_CONFIRMACION       = os.getenv("VELA_CONFIRMACION", "true").lower() == "true"
SWEEP_ACTIVO            = os.getenv("SWEEP_ACTIVO", "true").lower() == "true"
SWEEP_LOOKBACK          = int(os.getenv("SWEEP_LOOKBACK", "20"))
DISPLACEMENT_ACTIVO     = os.getenv("DISPLACEMENT_ACTIVO", "true").lower() == "true"
PREMIUM_DISCOUNT_ACTIVO = os.getenv("PREMIUM_DISCOUNT_ACTIVO", "true").lower() == "true"
PREMIUM_DISCOUNT_LB     = int(os.getenv("PREMIUM_DISCOUNT_LB", "50"))
PINBAR_RATIO            = float(os.getenv("PINBAR_RATIO", "0.50"))
CORRELACION_ACTIVO      = os.getenv("CORRELACION_ACTIVO", "true").lower() == "true"
RANGE_ACTIVO            = os.getenv("RANGE_ACTIVO", "false").lower() == "true"

# ══════════════════════════════════════════════════════════════
# EQUAL HIGHS / LOWS
# ══════════════════════════════════════════════════════════════
EQ_LOOKBACK  = int(os.getenv("EQ_LOOKBACK",  "50"))
EQ_PIVOT_LEN = int(os.getenv("EQ_PIVOT_LEN", "5"))
EQ_THRESHOLD = float(os.getenv("EQ_THRESHOLD", "0.1"))

# ══════════════════════════════════════════════════════════════
# PIVOTES DIARIOS
# ══════════════════════════════════════════════════════════════
PIVOT_NEAR_PCT = float(os.getenv("PIVOT_NEAR_PCT", "0.3"))

# ══════════════════════════════════════════════════════════════
# KILL ZONES (minutos desde 00:00 UTC)
# ══════════════════════════════════════════════════════════════
KZ_ASIA_START   = int(os.getenv("KZ_ASIA_START",   "0"))
KZ_ASIA_END     = int(os.getenv("KZ_ASIA_END",     "240"))
KZ_LONDON_START = int(os.getenv("KZ_LONDON_START", "480"))
KZ_LONDON_END   = int(os.getenv("KZ_LONDON_END",   "720"))
KZ_NY_START     = int(os.getenv("KZ_NY_START",     "780"))
KZ_NY_END       = int(os.getenv("KZ_NY_END",       "960"))

# ══════════════════════════════════════════════════════════════
# PARES
# ══════════════════════════════════════════════════════════════
SOLO_LONG          = os.getenv("SOLO_LONG", "false").lower() == "true"
PARES_BLOQUEADOS:   list = []
PARES_PRIORITARIOS: list = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT",
    "NEAR-USDT", "AVAX-USDT", "ARB-USDT", "OP-USDT",
]

# ══════════════════════════════════════════════════════════════
# SCANNER DE PARES
# ══════════════════════════════════════════════════════════════
VOLUMEN_MIN_24H = float(os.getenv("VOLUMEN_MIN_24H", "10000000"))
MAX_PARES_SCAN  = int(os.getenv("MAX_PARES_SCAN", "80"))

# ══════════════════════════════════════════════════════════════
# LOOP
# ══════════════════════════════════════════════════════════════
LOOP_SECONDS = int(os.getenv("LOOP_SECONDS", "60"))

# ══════════════════════════════════════════════════════════════
# CONCURRENCIA
# ══════════════════════════════════════════════════════════════
ANALISIS_WORKERS = int(os.getenv("ANALISIS_WORKERS", "4"))

# ══════════════════════════════════════════════════════════════
# METACLAW (IA validación de señales — requiere ANTHROPIC_API_KEY)
# ══════════════════════════════════════════════════════════════
METACLAW_ACTIVO      = os.getenv("METACLAW_ACTIVO", "false").lower() == "true"
METACLAW_VETO_MINIMO = int(os.getenv("METACLAW_VETO_MINIMO", "7"))

# ══════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ══════════════════════════════════════════════════════════════
# VALIDACIÓN — llamada por main.py en el arranque
# ══════════════════════════════════════════════════════════════
def validar() -> list:
    """
    Valida variables críticas.
    Retorna lista de advertencias (no bloquea el arranque).
    """
    errores = []
    if not API_KEY:
        errores.append("API_KEY no configurada — el bot no podrá operar")
    if not API_SECRET:
        errores.append("API_SECRET no configurada — el bot no podrá operar")
    if TRADE_USDT_BASE <= 0:
        errores.append("TRADE_USDT_BASE debe ser > 0")
    if LEVERAGE <= 0:
        errores.append("LEVERAGE debe ser > 0")
    if not TELEGRAM_TOKEN:
        errores.append("TELEGRAM_TOKEN no configurado — sin notificaciones")
    return errores
