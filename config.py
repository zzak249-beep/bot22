"""
config.py — Configuracion del bot v4 ELITE.
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
BB_SIGMA     = float(os.getenv("BB_SIGMA", "2.0"))   # 2.0 = bandas mas extremas = señales mas fiables
RSI_PERIOD   = int(os.getenv("RSI_PERIOD", "14"))
RSI_OB       = float(os.getenv("RSI_OB", "40"))      # Solo entrar LONG con RSI < 40
RSI_OS       = float(os.getenv("RSI_OS", "60"))      # Solo entrar SHORT con RSI > 60
SL_ATR       = float(os.getenv("SL_ATR", "2.5"))     # SL = 2.5x ATR
TP_ATR       = float(os.getenv("TP_ATR", "0"))       # 0 = usar BB basis como TP

# ── Gestion de capital ───────────────────────────────────
RISK_PCT      = float(os.getenv("RISK_PCT", "0.015")) # 1.5% por trade (conservador)
LEVERAGE      = int(os.getenv("LEVERAGE", "3"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
COOLDOWN_BARS = int(os.getenv("COOLDOWN_BARS", "4"))  # esperar 4 velas tras cerrar

# ── Operacion ────────────────────────────────────────────
LOOP_SECONDS     = int(os.getenv("LOOP_SECONDS", "300"))
MIN_USDT_BALANCE = float(os.getenv("MIN_USDT_BALANCE", "5"))
SCAN_BATCH_SIZE  = int(os.getenv("SCAN_BATCH_SIZE", "20"))

# ── SHORT ────────────────────────────────────────────────
SHORT_ENABLED = os.getenv("SHORT_ENABLED", "false").lower() == "true"

# ── Trailing Stop ────────────────────────────────────────
TRAILING_STOP_ENABLED = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_STOP_ATR     = float(os.getenv("TRAILING_STOP_ATR", "1.5"))
TRAILING_ACTIVATE_PCT = float(os.getenv("TRAILING_ACTIVATE_PCT", "1.0")) # activar con +1% ganancia

# ── TP Parcial ───────────────────────────────────────────
PARTIAL_TP_ENABLED = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_PCT     = float(os.getenv("PARTIAL_TP_PCT", "0.5"))   # cerrar 50%
PARTIAL_TP_ATR     = float(os.getenv("PARTIAL_TP_ATR", "1.2"))

# ── Filtro volumen anomalo ───────────────────────────────
VOLUME_SPIKE_ENABLED = os.getenv("VOLUME_SPIKE_ENABLED", "true").lower() == "true"
VOLUME_SPIKE_MULT    = float(os.getenv("VOLUME_SPIKE_MULT", "4.0")) # ignorar velas 4x volumen

# ── Circuit Breaker ──────────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = float(os.getenv("CB_MAX_DAILY_LOSS_PCT",   "0.08")) # parar con -8% dia
CB_MAX_CONSECUTIVE_LOSS = int(os.getenv("CB_MAX_CONSECUTIVE_LOSS",   "4"))    # parar con 4 perdidas seguidas
BALANCE_SNAPSHOT        = 0.0  # se actualiza en runtime al arrancar

# ── Mercado lateral ──────────────────────────────────────
SIDEWAYS_BB_WIDTH  = float(os.getenv("SIDEWAYS_BB_WIDTH",  "0.04"))  # BB width < 4% = lateral
SIDEWAYS_ATR_RATIO = float(os.getenv("SIDEWAYS_ATR_RATIO", "0.65"))  # ATR < 65% media = lateral

# ── Filtros de calidad de señal ──────────────────────────
MIN_ATR_USDT         = float(os.getenv("MIN_ATR_USDT",         "0"))     # ATR minimo en USDT (0=off)
REQUIRE_MACD_CONFIRM = os.getenv("REQUIRE_MACD_CONFIRM", "true").lower() == "true"
REQUIRE_MOMENTUM     = os.getenv("REQUIRE_MOMENTUM",     "true").lower() == "true"