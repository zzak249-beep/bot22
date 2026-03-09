import os

# ══════════════════════════════════════════════════════
# config.py v5.0 — LIVE MÁXIMO (<$100, riesgo total)
# ══════════════════════════════════════════════════════

VERSION = "v5.0-LIVE-YOLO"

TRADE_MODE  = os.getenv("TRADE_MODE",  "live")
INITIAL_BAL = float(os.getenv("INITIAL_BAL", "100"))

BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── RIESGO MÁXIMO ───────────────────────────────────
LEVERAGE      = int(os.getenv("LEVERAGE",     "10"))  # 10x — techo BingX altcoins
RISK_PCT      = float(os.getenv("RISK_PCT",   "0.05")) # 5% por trade
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS","4"))    # 4 simultáneas → 20% capital en juego

# ─── VELOCIDAD MÁXIMA ────────────────────────────────
CANDLE_TF     = os.getenv("CANDLE_TF",       "15m")   # 15m → el doble de señales que 30m
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL","300"))  # cada 5 min

BB_PERIOD = 20; BB_STD = 2.0; SMA_PERIOD = 50
RSI_PERIOD = 14; ATR_PERIOD = 14
MACD_FAST = 12; MACD_SLOW = 26; MACD_SIGNAL = 9
STOCH_K = 14; STOCH_D = 3

# ─── SEÑALES MÁS PERMISIVAS POSIBLES ─────────────────
SCORE_MIN     = int(os.getenv("SCORE_MIN",   "20"))   # mínimo absoluto
RSI_LONG      = int(os.getenv("RSI_LONG",    "45"))   # casi neutral
RSI_SHORT     = int(os.getenv("RSI_SHORT",   "55"))   # casi neutral
MIN_RR        = float(os.getenv("MIN_RR",    "1.0"))
VOLUME_FILTER = False
MTF_ENABLED       = False
MTF_INTERVAL      = "4h"
MTF_BLOCK_COUNTER = False

# ─── TP:SL 4:1 ───────────────────────────────────────
TP_ATR_MULT    = float(os.getenv("TP_ATR_MULT",    "4.0"))
SL_ATR_MULT    = float(os.getenv("SL_ATR_MULT",    "1.0"))
PARTIAL_TP_ATR = float(os.getenv("PARTIAL_TP_ATR", "2.0"))
SL_BUFFER      = 0.0001

# ─── TRAILING ULTRA AGRESIVO ──────────────────────────
TRAIL_FROM_START     = True
TRAIL_ATR_MULT_INIT  = 1.0   # trailing muy pegado
TRAIL_ATR_MULT_AFTER = 0.5

# ─── RE-ENTRY INMEDIATO ───────────────────────────────
REENTRY_ENABLED   = True
REENTRY_COOLDOWN  = 1
REENTRY_SCORE_MIN = 20

# ─── CIRCUIT BREAKER MÍNIMO (no para el bot) ─────────
MAX_DAILY_LOSS_PCT = 0.60   # 60% — solo para catástrofe total
MAX_DRAWDOWN_PCT   = 0.70   # 70%
MAX_CONSEC_LOSSES  = 12

# ─── SELECTOR Y LEARNER ──────────────────────────────
SELECTOR_ENABLED  = True
SELECTOR_TOP_N    = int(os.getenv("SELECTOR_TOP_N",  "22"))
SELECTOR_ROTATE_H = int(os.getenv("SELECTOR_ROTATE_H","36")) # rotar cada 36h

LEARNER_ENABLED   = True
DASHBOARD_ENABLED = True
DASHBOARD_PORT    = int(os.getenv("PORT","8080"))

# ─── 28 PARES — máxima cobertura ─────────────────────
SYMBOLS = [
    "LINK-USDT","OP-USDT","ARB-USDT","NEAR-USDT","LTC-USDT",
    "ONDO-USDT","POPCAT-USDT","KAITO-USDT","MYX-USDT","RSR-USDT",
    "ZEC-USDT","DEEP-USDT","SUSHI-USDT","ZRX-USDT",
    "BERA-USDT","GRASS-USDT","PI-USDT","LAYER-USDT","MOODENG-USDT",
    "SOL-USDT","AVAX-USDT","DOT-USDT","ATOM-USDT","INJ-USDT",
    "SUI-USDT","TIA-USDT","STRK-USDT","WIF-USDT",
]

BLACKLIST = ["BTC-USDT","ETH-USDT","ADA-USDT","DOGE-USDT","XRP-USDT"]

PAIR_SIZE_MULT = {
    "LINK-USDT":1.5,"BERA-USDT":1.5,"SOL-USDT":1.4,
    "OP-USDT":1.4,"ARB-USDT":1.4,"SUI-USDT":1.3,
    "INJ-USDT":1.3,"WIF-USDT":1.3,"POPCAT-USDT":1.3,
}
DEFAULT_SIZE_MULT = 1.0

TREND_LOOKBACK = 20
TREND_THRESH   = 0.015  # muy sensible
