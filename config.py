"""
config.py — Toda la configuracion viene de variables de entorno.
En local: archivo .env
En Railway: Settings > Variables
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── BingX ────────────────────────────────────────────────
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET  = os.getenv("BINGX_SECRET", "")

# ── Telegram ─────────────────────────────────────────────
TG_TOKEN   = os.getenv("TG_TOKEN", "")      # token del bot  (@BotFather)
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")    # tu chat id     (@userinfobot)

# ── Estrategia BB+RSI ────────────────────────────────────
SYMBOLS    = os.getenv("SYMBOLS", "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT").split(",")
TIMEFRAME  = os.getenv("TIMEFRAME", "1h")
BB_PERIOD  = int(os.getenv("BB_PERIOD", "30"))
BB_SIGMA   = float(os.getenv("BB_SIGMA", "1.8"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OB     = float(os.getenv("RSI_OB", "65"))
SL_ATR     = float(os.getenv("SL_ATR", "2.0"))

# ── Gestion de capital ───────────────────────────────────
RISK_PCT       = float(os.getenv("RISK_PCT", "0.02"))      # 2% por trade
LEVERAGE       = int(os.getenv("LEVERAGE", "3"))            # 3x futuros
MAX_POSITIONS  = int(os.getenv("MAX_POSITIONS", "3"))
COOLDOWN_BARS  = int(os.getenv("COOLDOWN_BARS", "3"))

# ── Operacion ────────────────────────────────────────────
LOOP_SECONDS     = int(os.getenv("LOOP_SECONDS", "300"))    # cada 5 min
MIN_USDT_BALANCE = float(os.getenv("MIN_USDT_BALANCE", "5")) # minimo para operar
