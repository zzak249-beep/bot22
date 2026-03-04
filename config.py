# ══════════════════════════════════════════════════════
# config.py — PARAMETROS DEL BACKTEST
# Solo edita este archivo para probar variantes.
# El backtest_final.py nunca cambia.
# ══════════════════════════════════════════════════════

BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14
RSI_LONG       = 32
RSI_SHORT      = 68
PARTIAL_TP_ATR = 2.5
LEVERAGE       = 2
RISK_PCT       = 0.02
INITIAL_BAL    = 100.0
SCORE_MIN      = 45
COOLDOWN_BARS  = 3
MIN_RR         = 1.2
TREND_LOOKBACK = 10
TREND_THRESH   = 0.05
SMA_PERIOD     = 50
SL_BUFFER      = 0.001

# Pares activos — comenta los que quieras desactivar
SYMBOLS = [
    "BTC-USDT",
    "ETH-USDT",
    "BNB-USDT",
    "XRP-USDT",
    "ADA-USDT",
    "DOGE-USDT",
    # "AVAX-USDT",  # WR:0% — desactivado
    "LINK-USDT",
    # "SOL-USDT",   # WR:20% — desactivado
    # "DOT-USDT",   # WR:0%  — desactivado
]

VERSION = "v12.1"
