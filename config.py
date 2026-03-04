# ══════════════════════════════════════════════════════
# config.py — v14
# Base: v13 [OK] PF=2.42  WR=75%  ROI=+1.1%
#
# PROBLEMA v13: solo 16 trades en 2 meses → poco para producción
# CAUSA:        RSI medio entrada 24.0 → RSI<30 casi nunca activa
# OBJETIVO v14: 35-50 trades manteniendo WR>65% PF>1.5
#
# CAMBIOS v14 (mínimos — no romper lo que funciona):
#   [1] RSI_LONG 30→33 — RSI<30 ocurre 5% del tiempo en 1H
#                         RSI<33 ocurre ~9% → casi dobla señales
#   [2] Eliminar DOT  (WR 0%,  -$0.22 en v13)
#   [3] Eliminar AAVE (WR 50%, -$0.005 en v13)
#   [4] Añadir 4 pares nuevos para compensar volumen de señales
#   [MANTENER] Todo lo demás idéntico a v13
# ══════════════════════════════════════════════════════

BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14

RSI_LONG       = 33    # [1] era 30 — dobla frecuencia de señales
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

SYMBOLS = [
    # Núcleo v13 — todos positivos, orden por PnL
    "ATOM-USDT",   # v13: 2tr WR100% +$0.39
    "OP-USDT",     # v13: 1tr WR100% +$0.21
    "BTC-USDT",    # v13: 2tr WR100% +$0.20
    "LINK-USDT",   # v13: 3tr WR67%  +$0.20
    "SOL-USDT",    # v13: 1tr WR100% +$0.15
    "ETH-USDT",    # v13: 1tr WR100% +$0.12
    "LTC-USDT",    # v13: 3tr WR67%  +$0.09
    # Nuevos — liquidez alta y buena reversión en 1H
    "NEAR-USDT",
    "FTM-USDT",
    "SAND-USDT",
    "HBAR-USDT",
]

VERSION = "v14"
