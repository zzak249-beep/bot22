# ══════════════════════════════════════════════════════
# config.py — v15 SCANNER
# Pares curados del análisis de 626 símbolos BingX
#
# RSR/NCSKGME2USD excluidos: PF:999 pero solo 3 trades
# → muestra estadísticamente inútil (3 wins = suerte)
# Criterio real: PF>1.5 + liquidez + historial v13
# ══════════════════════════════════════════════════════

BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14
RSI_LONG       = 30
RSI_SHORT      = 70
SL_BUFFER      = 0.003
PARTIAL_TP_ATR = 2.0
LEVERAGE       = 2
RISK_PCT       = 0.02
INITIAL_BAL    = 100.0
SCORE_MIN      = 48
COOLDOWN_BARS  = 3
MIN_RR         = 1.5
TREND_LOOKBACK = 8
TREND_THRESH   = 0.25
SMA_PERIOD     = 50
LONG_ONLY_UP   = True

# Parámetros del bot producción (main.py los lee)
RSI_OB                    = 70
RSI_OS                    = 30
SL_ATR                    = 1.8
TIMEFRAME                 = "1h"
TIMEFRAME_HI              = "4h"
MAX_POSITIONS             = 3
MIN_USDT_BALANCE          = 5.0
LOOP_SECONDS              = 300
SCAN_BATCH_SIZE           = 10
BALANCE_SNAPSHOT          = 0.0
EMA_TREND_ENABLED         = True
ADX_FILTER_ENABLED        = True
ADX_PERIOD                = 14
ADX_MIN                   = 20
STOCH_RSI_ENABLED         = True
STOCH_RSI_PERIOD          = 14
STOCH_RSI_K               = 3
STOCH_RSI_D               = 3
STOCH_RSI_OB              = 80
STOCH_RSI_OS              = 20
VOLUME_CONFIRM_ENABLED    = True
VOLUME_CONFIRM_MULT       = 0.8
CANDLE_CONFIRM_ENABLED    = True
CANDLE_CONFIRM_MIN_BODY   = 0.4
MULTI_TP_ENABLED          = True
TP1_ATR_MULT              = 1.2
TP2_ATR_MULT              = 2.0
TP1_CLOSE_PCT             = 0.30
TP2_CLOSE_PCT             = 0.40
PARTIAL_TP_ENABLED        = False
PARTIAL_TP_PCT            = 0.50
TRAILING_STOP_ENABLED     = True
TRAILING_STOP_ATR         = 1.5
TRAILING_DYNAMIC_ENABLED  = True
TRAILING_VOL_THRESHOLD    = 0.02
TRAILING_ATR_HIGH_VOL     = 2.0
TRAILING_ATR_LOW_VOL      = 1.2
TRAILING_ACTIVATE_PCT     = 0.5
STALE_TRADE_ENABLED       = True
STALE_TRADE_HOURS         = 48
STALE_TRADE_MIN_MOVE      = 0.005
TIME_FILTER_ENABLED       = False
TIME_FILTER_OFF_START     = 2
TIME_FILTER_OFF_END       = 6
SENTIMENT_ENABLED         = False
FEAR_GREED_ENABLED        = False
MIN_RR_RATIO              = 1.5
CIRCUIT_BREAKER_ENABLED   = True
MAX_DAILY_LOSS_PCT        = 5.0

SYMBOLS = [
    # Scanner: PF>1.5, liquidez real, excluidos tokens dudosos
    "LINK-USDT",    # PF:16.09  WR:67%  +$0.36
    "ZEC-USDT",     # PF: 7.33  WR:67%  +$0.37
    "DEEP-USDT",    # PF: 8.63  WR:67%  +$0.33
    "AKE-USDT",     # PF: 3.79  WR:50%  +$0.43
    "ZEN-USDT",     # PF: 2.67  WR:50%  +$0.25
    "BMT-USDT",     # PF: 3.56  WR:60%  +$0.17
    "SUSHI-USDT",   # PF: 2.60  WR:67%  +$0.11
    "SOL-USDT",     # PF: 2.01  WR:67%  +$0.12
    "LTC-USDT",     # PF: 1.57  WR:50%  +$0.09
    # Núcleo v13 probado
    "ATOM-USDT",    # v13: WR100% +$0.39
    "OP-USDT",      # v13: WR100% +$0.21
    "BTC-USDT",     # v13: WR100% +$0.20
    "ETH-USDT",     # v13: WR100% +$0.12
]

VERSION = "v15-scanner"
