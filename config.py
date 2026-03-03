"""
config.py — Configuracion del bot v5 ELITE.
Todos los parametros ajustables desde .env sin tocar codigo.
"""
import os
from dotenv import load_dotenv
load_dotenv()

# ── BingX ────────────────────────────────────────────────
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET  = os.getenv("BINGX_SECRET", "")

# ── Telegram ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TG_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID", "")
TG_TOKEN   = TELEGRAM_TOKEN
TG_CHAT_ID = TELEGRAM_CHAT_ID

# ── Simbolos ─────────────────────────────────────────────
SYMBOLS_OVERRIDE = os.getenv("SYMBOLS", "")
MIN_VOLUME_USDT  = float(os.getenv("MIN_VOLUME_USDT", "1000000"))  # 1M USDT/dia minimo
QUOTE_CURRENCY   = os.getenv("QUOTE_CURRENCY", "USDT")
EXCLUDE_SYMBOLS  = [s.strip() for s in os.getenv("EXCLUDE_SYMBOLS", "").split(",") if s.strip()]
MAX_SYMBOLS      = int(os.getenv("MAX_SYMBOLS", "80"))
SYMBOLS: list    = []

# ── Estrategia BB+RSI ────────────────────────────────────
TIMEFRAME    = os.getenv("TIMEFRAME", "1h")
TIMEFRAME_HI = os.getenv("TIMEFRAME_HI", "4h")
BB_PERIOD    = int(os.getenv("BB_PERIOD", "30"))
BB_SIGMA     = float(os.getenv("BB_SIGMA", "2.0"))
RSI_PERIOD   = int(os.getenv("RSI_PERIOD", "14"))
RSI_OB       = float(os.getenv("RSI_OB", "40"))      # Solo LONG con RSI < 40
RSI_OS       = float(os.getenv("RSI_OS", "60"))      # Solo SHORT con RSI > 60
SL_ATR       = float(os.getenv("SL_ATR", "2.5"))
TP_ATR       = float(os.getenv("TP_ATR", "0"))       # 0 = usar BB basis como TP

# ── MEJORA: Filtro EMA de tendencia ─────────────────────
# Solo abrir LONG si precio > EMA200 (tendencia alcista confirmada)
# Solo abrir SHORT si precio < EMA200 (tendencia bajista confirmada)
EMA_TREND_ENABLED = os.getenv("EMA_TREND_ENABLED", "true").lower() == "true"
EMA_TREND_PERIOD  = int(os.getenv("EMA_TREND_PERIOD", "200"))

# ── MEJORA: Filtro ADX (fuerza de tendencia) ─────────────
# Solo operar si ADX > umbral: evita mercados laterales sin momentum
ADX_FILTER_ENABLED = os.getenv("ADX_FILTER_ENABLED", "true").lower() == "true"
ADX_MIN            = float(os.getenv("ADX_MIN", "20"))   # ADX > 20 = tendencia definida
ADX_PERIOD         = int(os.getenv("ADX_PERIOD", "14"))

# ── MEJORA: Filtro de volumen en entrada ─────────────────
# Volumen de la vela de señal debe ser > X veces la media
VOLUME_CONFIRM_ENABLED = os.getenv("VOLUME_CONFIRM_ENABLED", "true").lower() == "true"
VOLUME_CONFIRM_MULT    = float(os.getenv("VOLUME_CONFIRM_MULT", "1.2"))  # 1.2x la media

# ── MEJORA: Filtro horario ───────────────────────────────
# Evitar horas de baja liquidez (UTC). Crypto mas activo 08-00 UTC.
TIME_FILTER_ENABLED = os.getenv("TIME_FILTER_ENABLED", "true").lower() == "true"
TIME_FILTER_OFF_START = int(os.getenv("TIME_FILTER_OFF_START", "1"))   # hora UTC inicio baja liq.
TIME_FILTER_OFF_END   = int(os.getenv("TIME_FILTER_OFF_END",   "6"))   # hora UTC fin baja liq.

# ── MEJORA: RSI Divergencia ──────────────────────────────
# Confirmar entrada con divergencia RSI-precio para mayor precision
RSI_DIVERGENCE_ENABLED = os.getenv("RSI_DIVERGENCE_ENABLED", "false").lower() == "true"

# ── MEJORA: Filtro de spread minimo (R:R) ────────────────
# Solo entrar si el ratio Recompensa:Riesgo es >= este valor
MIN_RR_RATIO = float(os.getenv("MIN_RR_RATIO", "1.5"))  # TP debe ser >= 1.5x el SL

# ── Gestion de capital ───────────────────────────────────
RISK_PCT      = float(os.getenv("RISK_PCT", "0.015"))
LEVERAGE      = int(os.getenv("LEVERAGE", "3"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
COOLDOWN_BARS = int(os.getenv("COOLDOWN_BARS", "4"))

# ── Operacion ────────────────────────────────────────────
LOOP_SECONDS     = int(os.getenv("LOOP_SECONDS", "300"))
MIN_USDT_BALANCE = float(os.getenv("MIN_USDT_BALANCE", "5"))
SCAN_BATCH_SIZE  = int(os.getenv("SCAN_BATCH_SIZE", "20"))

# ── SHORT ────────────────────────────────────────────────
SHORT_ENABLED = os.getenv("SHORT_ENABLED", "false").lower() == "true"

# ── Trailing Stop ────────────────────────────────────────
TRAILING_STOP_ENABLED = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_STOP_ATR     = float(os.getenv("TRAILING_STOP_ATR", "1.5"))
TRAILING_ACTIVATE_PCT = float(os.getenv("TRAILING_ACTIVATE_PCT", "1.0"))

# ── TP Parcial ───────────────────────────────────────────
PARTIAL_TP_ENABLED = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_PCT     = float(os.getenv("PARTIAL_TP_PCT", "0.5"))
PARTIAL_TP_ATR     = float(os.getenv("PARTIAL_TP_ATR", "1.2"))

# ── Filtro volumen anomalo ───────────────────────────────
VOLUME_SPIKE_ENABLED = os.getenv("VOLUME_SPIKE_ENABLED", "true").lower() == "true"
VOLUME_SPIKE_MULT    = float(os.getenv("VOLUME_SPIKE_MULT", "4.0"))

# ── Circuit Breaker ──────────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = float(os.getenv("CB_MAX_DAILY_LOSS_PCT",   "0.08"))
CB_MAX_CONSECUTIVE_LOSS = int(os.getenv("CB_MAX_CONSECUTIVE_LOSS",   "4"))
BALANCE_SNAPSHOT        = 0.0

# ── Mercado lateral ──────────────────────────────────────
SIDEWAYS_BB_WIDTH  = float(os.getenv("SIDEWAYS_BB_WIDTH",  "0.04"))
SIDEWAYS_ATR_RATIO = float(os.getenv("SIDEWAYS_ATR_RATIO", "0.65"))

# ── Filtros de calidad de señal ──────────────────────────
MIN_ATR_USDT         = float(os.getenv("MIN_ATR_USDT",         "0"))
REQUIRE_MACD_CONFIRM = os.getenv("REQUIRE_MACD_CONFIRM", "true").lower() == "true"
REQUIRE_MOMENTUM     = os.getenv("REQUIRE_MOMENTUM",     "true").lower() == "true"
