"""
config.py — Configuracion del bot.
SYMBOLS se carga dinamicamente desde BingX en tiempo de ejecucion.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── BingX ────────────────────────────────────────────────
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET  = os.getenv("BINGX_SECRET", "")

# ── Telegram ─────────────────────────────────────────────
TG_TOKEN   = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

# ── Simbolos ─────────────────────────────────────────────
# Si SYMBOLS esta definido en .env usa esos, si no carga todos los de BingX
SYMBOLS_OVERRIDE = os.getenv("SYMBOLS", "")

# Filtros para seleccion automatica de pares
MIN_VOLUME_USDT = float(os.getenv("MIN_VOLUME_USDT", "0"))  # 0 = TODOS los pares sin filtro de volumen
QUOTE_CURRENCY  = os.getenv("QUOTE_CURRENCY", "USDT")             # solo pares /USDT
EXCLUDE_SYMBOLS = [s for s in os.getenv("EXCLUDE_SYMBOLS", "").split(",") if s]
MAX_SYMBOLS     = int(os.getenv("MAX_SYMBOLS", "0"))              # 0 = sin limite

# Lista activa (se rellena en arranque por symbols_loader.py)
SYMBOLS: list = []

# ── Estrategia BB+RSI ────────────────────────────────────
TIMEFRAME  = os.getenv("TIMEFRAME", "1h")
BB_PERIOD  = int(os.getenv("BB_PERIOD", "30"))
BB_SIGMA   = float(os.getenv("BB_SIGMA", "1.8"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OB     = float(os.getenv("RSI_OB", "65"))
SL_ATR     = float(os.getenv("SL_ATR", "2.0"))

# ── Gestion de capital ───────────────────────────────────
RISK_PCT      = float(os.getenv("RISK_PCT", "0.02"))
LEVERAGE      = int(os.getenv("LEVERAGE", "3"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))
COOLDOWN_BARS = int(os.getenv("COOLDOWN_BARS", "3"))

# ── Operacion ────────────────────────────────────────────
LOOP_SECONDS     = int(os.getenv("LOOP_SECONDS", "300"))
MIN_USDT_BALANCE = float(os.getenv("MIN_USDT_BALANCE", "5"))
SCAN_BATCH_SIZE  = int(os.getenv("SCAN_BATCH_SIZE", "20"))  # pares por lote
