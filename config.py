# ══════════════════════════════════════════════════════════════
# config.py — BB+RSI ELITE v15
# Lee variables de Railway (entorno) con fallback a valores
# óptimos del backtest v13 (WR:75% PF:2.42)
# ══════════════════════════════════════════════════════════════

import os

def _float(key, default):
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return float(default)

def _int(key, default):
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return int(default)

def _bool(key, default):
    val = os.environ.get(key, str(default)).lower()
    return val in ("1", "true", "yes", "on")

def _str(key, default):
    return os.environ.get(key, default)


# ── API Keys (OBLIGATORIO en Railway → Variables) ─────────────
BINGX_API_KEY = _str("BINGX_API_KEY", "")
BINGX_SECRET  = _str("BINGX_SECRET",  "")

# ── Telegram ──────────────────────────────────────────────────
TELEGRAM_TOKEN   = _str("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = _str("TELEGRAM_CHAT_ID", "")

# ── Gestión de riesgo ─────────────────────────────────────────
# ⚠️  ATENCIÓN: RISK_PCT=0.9 en Railway = 90% del balance por trade
#     Una operación perdida puede liquidar la cuenta.
#     Valores recomendados: 0.02 (2%) conservador | 0.05 (5%) agresivo
#     El backtest rentable v13 usaba 0.02 — cambia Railway a "0.02"
RISK_PCT      = _float("RISK_PCT",      0.02)
LEVERAGE      = _int(  "LEVERAGE",      2)
MAX_POSITIONS = _int(  "MAX_POSITIONS", 3)

# ── Filtro de volumen mínimo de mercado ───────────────────────
MIN_VOLUME_USDT = _float("MIN_VOLUME_USDT", 5_000_000)

# ── Loop y balance ────────────────────────────────────────────
LOOP_SECONDS     = _int(  "LOOP_SECONDS",     300)
SCAN_BATCH_SIZE  = _int(  "SCAN_BATCH_SIZE",   10)
MIN_USDT_BALANCE = _float("MIN_USDT_BALANCE",  5.0)
BALANCE_SNAPSHOT = 0.0

# ── Direcciones ───────────────────────────────────────────────
SHORT_ENABLED = _bool("SHORT_ENABLED", False)
LONG_ONLY_UP  = True   # CRÍTICO — bloquea trades en flat (sin esto WR cae de 75% a 22%)

# ── Bollinger Bands ───────────────────────────────────────────
BB_PERIOD = 20
BB_SIGMA  = 2.0

# ── RSI ───────────────────────────────────────────────────────
RSI_PERIOD = 14
RSI_LONG   = 30    # <30 = sobreventa real (v13: WR 75% con este valor)
RSI_SHORT  = 70
RSI_OB     = 70
RSI_OS     = 30

# ── StochRSI ──────────────────────────────────────────────────
STOCH_RSI_ENABLED = True
STOCH_RSI_PERIOD  = 14
STOCH_RSI_K       = 3
STOCH_RSI_D       = 3
STOCH_RSI_OB      = 80
STOCH_RSI_OS      = 20

# ── ADX ───────────────────────────────────────────────────────
ADX_FILTER_ENABLED = True
ADX_PERIOD         = 14
ADX_MIN            = 20

# ── Tendencia ─────────────────────────────────────────────────
TREND_LOOKBACK    = 8
TREND_THRESH      = 0.25
SMA_PERIOD        = 50
EMA_TREND_ENABLED = True

# ── SL / TP ───────────────────────────────────────────────────
SL_ATR         = 1.8
SL_BUFFER      = 0.003
PARTIAL_TP_ATR = 2.0
SCORE_MIN      = 48
COOLDOWN_BARS  = 3
MIN_RR         = 1.5
MIN_RR_RATIO   = 1.5

# ── Volumen de vela ───────────────────────────────────────────
VOLUME_CONFIRM_ENABLED = True
VOLUME_CONFIRM_MULT    = 0.8

# ── Confirmación de vela ──────────────────────────────────────
CANDLE_CONFIRM_ENABLED  = True
CANDLE_CONFIRM_MIN_BODY = 0.4

# ── Take Profit múltiple ──────────────────────────────────────
MULTI_TP_ENABLED = True
TP1_ATR_MULT     = 1.2
TP2_ATR_MULT     = 2.0
TP1_CLOSE_PCT    = 0.30
TP2_CLOSE_PCT    = 0.40

PARTIAL_TP_ENABLED = False
PARTIAL_TP_PCT     = 0.50

# ── Trailing stop ─────────────────────────────────────────────
TRAILING_STOP_ENABLED    = _bool( "TRAILING_STOP_ENABLED",  True)
TRAILING_STOP_ATR        = _float("TRAILING_STOP_ATR",      1.5)
TRAILING_ACTIVATE_PCT    = _float("TRAILING_ACTIVATE_PCT",  0.5)
TRAILING_DYNAMIC_ENABLED = True
TRAILING_VOL_THRESHOLD   = 0.02
TRAILING_ATR_HIGH_VOL    = 2.0
TRAILING_ATR_LOW_VOL     = 1.2

# ── Stale trade ───────────────────────────────────────────────
STALE_TRADE_ENABLED  = True
STALE_TRADE_HOURS    = 48
STALE_TRADE_MIN_MOVE = 0.005

# ── Horario ───────────────────────────────────────────────────
TIME_FILTER_ENABLED   = False
TIME_FILTER_OFF_START = 2
TIME_FILTER_OFF_END   = 6

# ── Timeframes ────────────────────────────────────────────────
TIMEFRAME    = "1h"
TIMEFRAME_HI = "4h"

# ── Sentimiento (desactivado — mejora WR sin él) ──────────────
SENTIMENT_ENABLED  = False
FEAR_GREED_ENABLED = False

# ── Circuit breaker ───────────────────────────────────────────
CIRCUIT_BREAKER_ENABLED = True
MAX_DAILY_LOSS_PCT      = 5.0
CB_MAX_DAILY_LOSS_PCT   = 0.05
CB_MAX_CONSECUTIVE_LOSS = 5

# ── Misc ──────────────────────────────────────────────────────
INITIAL_BAL = 100.0

# ── Pares activos ─────────────────────────────────────────────
# Núcleo v13 (WR:75% PF:2.42) + mejores del scanner de 626 pares
# Eliminados DEEP y AKE: tokens sin historial suficiente
SYMBOLS = [
    "LINK-USDT",   # PF:16.09  WR:67%
    "ZEC-USDT",    # PF: 7.33  WR:67%
    "ZEN-USDT",    # PF: 2.67  WR:50%
    "BMT-USDT",    # PF: 3.56  WR:60%
    "SUSHI-USDT",  # PF: 2.60  WR:67%
    "SOL-USDT",    # PF: 2.01  WR:67%
    "LTC-USDT",    # PF: 1.57  WR:50%
    "ATOM-USDT",   # WR:100%  (v13)
    "OP-USDT",     # WR:100%  (v13)
    "BTC-USDT",    # WR:100%  (v13)
    "ETH-USDT",    # WR:100%  (v13)
]

VERSION = "v15-scanner"
