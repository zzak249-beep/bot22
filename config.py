"""
config.py — Toda la configuración del bot cargada desde .env
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── EXCHANGE ────────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")
DRY_RUN          = os.getenv("DRY_RUN", "true").lower() == "true"

# ─── MERCADO ─────────────────────────────────────────────────
SYMBOL        = os.getenv("SYMBOL", "BTC/USDT:USDT")   # perpetual futures
TIMEFRAME     = os.getenv("TIMEFRAME", "2h")
LEVERAGE      = int(os.getenv("LEVERAGE", "5"))

# ─── GESTIÓN DE RIESGO ───────────────────────────────────────
RISK_PCT          = float(os.getenv("RISK_PCT", "1.0"))        # % de balance por trade
MAX_DAILY_LOSS_PCT= float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0")) # pausa si pierde este %
MAX_TRADES_DAY    = int(os.getenv("MAX_TRADES_DAY", "5"))
TP1_RR            = float(os.getenv("TP1_RR", "1.0"))          # TP1 en 1:1
TP2_RR            = float(os.getenv("TP2_RR", "2.0"))          # TP2 en 1:2
TP1_QTY_PCT       = float(os.getenv("TP1_QTY_PCT", "50"))      # 50% en TP1
MIN_ATR_MULT      = float(os.getenv("MIN_ATR_MULT", "0.5"))    # SL mínimo = 0.5x ATR

# ─── EMAs ────────────────────────────────────────────────────
EMA_FAST    = 9
EMA_MID     = 21
EMA_TREND   = 50
EMA_STRUCT  = 120
EMA_MACRO   = 200

# ─── FILTROS EXTRA (mejoras vs Pine Script original) ─────────
RSI_PERIOD      = int(os.getenv("RSI_PERIOD", "14"))
RSI_BULL_MIN    = float(os.getenv("RSI_BULL_MIN", "45"))  # long solo si RSI > 45
RSI_BEAR_MAX    = float(os.getenv("RSI_BEAR_MAX", "55"))  # short solo si RSI < 55
VOL_MA_PERIOD   = int(os.getenv("VOL_MA_PERIOD", "20"))   # volumen > media 20 velas
COOLDOWN_BARS   = int(os.getenv("COOLDOWN_BARS", "3"))    # velas de espera tras SL

# ─── CANDLES A CARGAR ────────────────────────────────────────
CANDLES_NEEDED = 300   # suficientes para EMA200 + margen

# ─── TELEGRAM ────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── LOOP ────────────────────────────────────────────────────
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))  # segundos entre chequeos
