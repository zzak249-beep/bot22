# ══════════════════════════════════════════════════════
# config.py — v15
# Base: v13 [OK] PF=2.42  WR=75%  ROI=+1.1%
#
# PROBLEMA v14: backtest_final.py permite longs en "flat"
#   flat: 27tr WR:22% -$1.09 → destruye todo
#   up:   16tr WR:62% +$0.74 → rentable
# SOLUCIÓN: LONG_ONLY_UP = True + parche 1 línea en backtest_final.py
# ══════════════════════════════════════════════════════

BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14
RSI_LONG       = 30    # volver a 30 — RSI medio era 35.1 en v14 (señales sucias)
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

# NUEVO — requiere el parche de 1 línea en backtest_final.py
# True  = solo longs cuando trend="up"  (probado: WR 62-75%)
# False = longs en "up" y "flat"        (comportamiento original)
LONG_ONLY_UP   = True

SYMBOLS = [
    # Positivos en v13 Y v14
    "LTC-USDT",    # v14: WR60% +$0.23  v13: WR67% +$0.09
    "ETH-USDT",    # v14: WR33% +$0.10  v13: WR100% +$0.12
    "LINK-USDT",   # v14: WR67% +$0.09  v13: WR67%  +$0.20
    "OP-USDT",     # v14: WR50% +$0.07  v13: WR100% +$0.21
    "NEAR-USDT",   # v14: WR50% +$0.12  (nuevo en v14, positivo)
    "ATOM-USDT",   # v13: WR100% +$0.39 (caída en v14 por flat)
    "SOL-USDT",    # v13: WR100% +$0.15 (caída en v14 por flat)
    "BTC-USDT",    # v13: WR100% +$0.20 (caída en v14 por flat)
    # Nuevos — reemplazan HBAR(-$0.33) y SAND(-$0.18)
    "FTM-USDT",
    "AVAX-USDT",
]

VERSION = "v15"