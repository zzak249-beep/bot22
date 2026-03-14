"""
config.py — SMC Bot v8.0 [BingX Edition]
==========================================
Generado automáticamente. Edita las variables de entorno en Railway.
"""

import os

# ══════════════════════════════════════════════════════════════
# API KEYS (desde variables de entorno de Railway)
# ══════════════════════════════════════════════════════════════
API_KEY    = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")

# ══════════════════════════════════════════════════════════════
# EXCHANGE
# ══════════════════════════════════════════════════════════════
EXCHANGE = "bingx"

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
COMPOUND_STEP_USDT = float(os.getenv("COMPOUND_STEP_USDT", "100"))  # cada X ganados → subir
COMPOUND_ADD_USDT  = float(os.getenv("COMPOUND_ADD_USDT",  "10"))   # cuánto sube por nivel

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
ATR_PERIOD  = int(os.getenv("ATR_PERIOD", "14"))
RSI_PERIOD  = int(os.getenv("RSI_PERIOD", "14"))
EMA_FAST    = int(os.getenv("EMA_FAST",   "21"))
EMA_SLOW    = int(os.getenv("EMA_SLOW",   "50"))
EMA_LOCAL_FAST = int(os.getenv("EMA_LOCAL_FAST", "9"))
EMA_LOCAL_SLOW = int(os.getenv("EMA_LOCAL_SLOW", "21"))

RSI_BUY_MAX  = float(os.getenv("RSI_BUY_MAX",  "60"))
RSI_SELL_MIN = float(os.getenv("RSI_SELL_MIN", "40"))

VWAP_ACTIVO = os.getenv("VWAP_ACTIVO", "true").lower() == "true"
VWAP_PCT    = float(os.getenv("VWAP_PCT", "0.3"))

# ══════════════════════════════════════════════════════════════
# SL / TP
# ══════════════════════════════════════════════════════════════
SL_ATR_MULT    = float(os.getenv("SL_ATR_MULT",    "1.5"))
TP_DIST_MULT   = float(os.getenv("TP_DIST_MULT",   "1.2"))   # ganador bt_v4
TP1_DIST_MULT  = float(os.getenv("TP1_DIST_MULT",  "0.5"))
MIN_RR         = float(os.getenv("MIN_RR",         "1.0"))

# ══════════════════════════════════════════════════════════════
# SCORING
# ══════════════════════════════════════════════════════════════
SCORE_MIN = int(os.getenv("SCORE_MIN", "4"))

# ══════════════════════════════════════════════════════════════
# COOLDOWN (en velas)
# ══════════════════════════════════════════════════════════════
COOLDOWN_VELAS = int(os.getenv("COOLDOWN_VELAS", "3"))

# ══════════════════════════════════════════════════════════════
# FILTROS SMC
# ══════════════════════════════════════════════════════════════
OB_ACTIVO        = os.getenv("OB_ACTIVO",  "true").lower() == "true"
OB_LOOKBACK      = int(os.getenv("OB_LOOKBACK", "20"))
BOS_ACTIVO       = os.getenv("BOS_ACTIVO", "true").lower() == "true"
FVG_MIN_PIPS     = float(os.getenv("FVG_MIN_PIPS", "0.0"))
ASIA_RANGE_ACTIVO = os.getenv("ASIA_RANGE_ACTIVO", "true").lower() == "true"
VELA_CONFIRMACION = os.getenv("VELA_CONFIRMACION", "true").lower() == "true"
SWEEP_ACTIVO      = os.getenv("SWEEP_ACTIVO", "true").lower() == "true"
SWEEP_LOOKBACK    = int(os.getenv("SWEEP_LOOKBACK", "20"))
DISPLACEMENT_ACTIVO = os.getenv("DISPLACEMENT_ACTIVO", "true").lower() == "true"
PREMIUM_DISCOUNT_ACTIVO = os.getenv("PREMIUM_DISCOUNT_ACTIVO", "true").lower() == "true"
PREMIUM_DISCOUNT_LB     = int(os.getenv("PREMIUM_DISCOUNT_LB", "50"))
PINBAR_RATIO     = float(os.getenv("PINBAR_RATIO", "0.50"))

# ══════════════════════════════════════════════════════════════
# EQUAL HIGHS / LOWS
# ══════════════════════════════════════════════════════════════
EQ_LOOKBACK  = int(os.getenv("EQ_LOOKBACK",  "50"))
EQ_PIVOT_LEN = int(os.getenv("EQ_PIVOT_LEN", "5"))
EQ_THRESHOLD = float(os.getenv("EQ_THRESHOLD", "0.1"))  # % tolerancia

# ══════════════════════════════════════════════════════════════
# PIVOTES DIARIOS
# ══════════════════════════════════════════════════════════════
PIVOT_NEAR_PCT = float(os.getenv("PIVOT_NEAR_PCT", "0.3"))  # % cercanía al nivel

# ══════════════════════════════════════════════════════════════
# KILL ZONES (minutos desde 00:00 UTC)
# ══════════════════════════════════════════════════════════════
KZ_ASIA_START   = int(os.getenv("KZ_ASIA_START",   "0"))    # 00:00
KZ_ASIA_END     = int(os.getenv("KZ_ASIA_END",     "240"))  # 04:00
KZ_LONDON_START = int(os.getenv("KZ_LONDON_START", "480"))  # 08:00
KZ_LONDON_END   = int(os.getenv("KZ_LONDON_END",   "720"))  # 12:00
KZ_NY_START     = int(os.getenv("KZ_NY_START",     "780"))  # 13:00
KZ_NY_END       = int(os.getenv("KZ_NY_END",       "960"))  # 16:00

# ══════════════════════════════════════════════════════════════
# PARES
# ══════════════════════════════════════════════════════════════
SOLO_LONG       = os.getenv("SOLO_LONG", "false").lower() == "true"
PARES_BLOQUEADOS: list = []

# ══════════════════════════════════════════════════════════════
# CONCURRENCIA
# ══════════════════════════════════════════════════════════════
ANALISIS_WORKERS = int(os.getenv("ANALISIS_WORKERS", "4"))

# ══════════════════════════════════════════════════════════════
# TELEGRAM (opcional)
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# ══════════════════════════════════════════════════════════════
# VALIDACIÓN
# ══════════════════════════════════════════════════════════════
def validar():
    """Valida que las variables críticas estén configuradas."""
    errores = []
    if not API_KEY:
        errores.append("API_KEY no configurada")
    if not API_SECRET:
        errores.append("API_SECRET no configurada")
    if TRADE_USDT_BASE <= 0:
        errores.append("TRADE_USDT_BASE debe ser > 0")
    return errores
