"""
config.py — v5 CORREGIDO tras backtest real.
PROBLEMA: SL demasiado ajustado = 108 trades perdidos por SL = -$31.86
SOLUCION: SL=3.5xATR, Leverage=2x, BB_SIGMA=2.2, score>=45
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
MIN_VOLUME_USDT  = float(os.getenv("MIN_VOLUME_USDT", "50000"))
QUOTE_CURRENCY   = os.getenv("QUOTE_CURRENCY", "USDT")
EXCLUDE_SYMBOLS  = [s.strip() for s in os.getenv("EXCLUDE_SYMBOLS", "").split(",") if s.strip()]
MAX_SYMBOLS      = int(os.getenv("MAX_SYMBOLS", "80"))
SYMBOLS: list    = []

# ── Estrategia BB+RSI — CORREGIDA ────────────────────────
TIMEFRAME    = os.getenv("TIMEFRAME",   "1h")
TIMEFRAME_HI = os.getenv("TIMEFRAME_HI","4h")
BB_PERIOD    = int(os.getenv("BB_PERIOD",   "20"))   # 20 = mas reactivo
BB_SIGMA     = float(os.getenv("BB_SIGMA",  "2.2"))  # 2.2 = entradas mas extremas
RSI_PERIOD   = int(os.getenv("RSI_PERIOD",  "14"))
RSI_OB       = float(os.getenv("RSI_OB",    "50"))   # mas señales
RSI_OS       = float(os.getenv("RSI_OS",    "50"))

# ── CLAVE: SL ampliado — causa principal de perdidas ─────
SL_ATR = float(os.getenv("SL_ATR", "3.5"))           # era 2.5 → ahora 3.5

# ── Gestion de capital ───────────────────────────────────
RISK_PCT      = float(os.getenv("RISK_PCT",      "0.02"))
LEVERAGE      = int(os.getenv("LEVERAGE",         "2"))   # bajado 3→2, mas margen
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS",    "3"))
COOLDOWN_BARS = int(os.getenv("COOLDOWN_BARS",    "4"))

# ── Operacion ────────────────────────────────────────────
LOOP_SECONDS     = int(os.getenv("LOOP_SECONDS",       "300"))
MIN_USDT_BALANCE = float(os.getenv("MIN_USDT_BALANCE", "5"))
SCAN_BATCH_SIZE  = int(os.getenv("SCAN_BATCH_SIZE",    "20"))

# ── SHORT ────────────────────────────────────────────────
SHORT_ENABLED = os.getenv("SHORT_ENABLED", "false").lower() == "true"

# ── Trailing Stop ────────────────────────────────────────
TRAILING_STOP_ENABLED = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_STOP_ATR     = float(os.getenv("TRAILING_STOP_ATR",     "2.0"))
TRAILING_ACTIVATE_PCT = float(os.getenv("TRAILING_ACTIVATE_PCT", "0.8"))

# ── TP Parcial ───────────────────────────────────────────
PARTIAL_TP_ENABLED = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_PCT     = float(os.getenv("PARTIAL_TP_PCT", "0.5"))
PARTIAL_TP_ATR     = float(os.getenv("PARTIAL_TP_ATR", "3.0"))  # TP mas lejos

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

# ── Filtros de calidad ───────────────────────────────────
MIN_ATR_USDT         = float(os.getenv("MIN_ATR_USDT",         "0"))
REQUIRE_MACD_CONFIRM = os.getenv("REQUIRE_MACD_CONFIRM", "true").lower()  == "true"
REQUIRE_MOMENTUM     = os.getenv("REQUIRE_MOMENTUM",     "false").lower() == "true"  # OFF: causaba bloqueos
MIN_SIGNAL_SCORE     = int(os.getenv("MIN_SIGNAL_SCORE", "45"))  # era 55 → ahora 45