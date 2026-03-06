import os

# ══════════════════════════════════════════════════════
# config.py — BB+RSI ELITE v13.1
# ══════════════════════════════════════════════════════

VERSION = "v13.1"

# ── Credenciales ───────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Modo ───────────────────────────────────────────────
# "paper" = simula | "live" = opera real en BingX
TRADE_MODE    = os.getenv("TRADE_MODE", "paper")

# ── Temporalidad ───────────────────────────────────────
CANDLE_TF     = "15m"
MTF_INTERVAL  = "1h"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 900))   # 15 min

# ── Indicadores ────────────────────────────────────────
BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14
RSI_LONG       = 36
RSI_SHORT      = 64
PARTIAL_TP_ATR = 2.5
SMA_PERIOD     = 50

# ── Riesgo base ────────────────────────────────────────
LEVERAGE       = int(os.getenv("LEVERAGE",    2))
RISK_PCT       = float(os.getenv("RISK_PCT",  0.02))
INITIAL_BAL    = float(os.getenv("INITIAL_BAL", 100.0))
MIN_RR         = 1.2
SL_BUFFER      = 0.001
SCORE_MIN      = 40
COOLDOWN_BARS  = 2

# ── Circuit breaker ────────────────────────────────────
MAX_DAILY_LOSS_PCT   = 0.50
MAX_DRAWDOWN_PCT     = 0.50
MAX_CONCURRENT_POS   = 5
CIRCUIT_BREAKER_LOSS = 4

# ── Trailing SL dinámico ───────────────────────────────
TRAIL_FROM_START     = True
TRAIL_ATR_MULT_INIT  = 2.0
TRAIL_ATR_MULT_AFTER = 1.5

# ── Sizing dinámico por ATR ────────────────────────────
ATR_SIZING      = True
ATR_SIZING_BASE = 0.02

# ── Re-entry ───────────────────────────────────────────
REENTRY_ENABLED   = True
REENTRY_COOLDOWN  = 1
REENTRY_SCORE_MIN = 55

# ── Filtro de volumen ──────────────────────────────────
VOLUME_FILTER    = True
VOLUME_MA_PERIOD = 20
VOLUME_MIN_RATIO = 0.7

# ── Multi-timeframe 1h como confirmación ──────────────
MTF_ENABLED       = True
MTF_BLOCK_COUNTER = True

# ── Tendencia ─────────────────────────────────────────
TREND_LOOKBACK = 8
TREND_THRESH   = 0.04

# ── Alertas ───────────────────────────────────────────
ALERT_ALWAYS   = True

# ── Liquidez institucional (liquidity.py) ─────────────
# LIQUIDITY_ENABLED: activa el filtro de order book / funding / CVD
LIQUIDITY_ENABLED    = True
# LIQUIDITY_BLOCK_SCORE: score extremo que bloquea la señal
# (<25 bearish extremo bloquea LONG; >75 bullish extremo bloquea SHORT)
LIQUIDITY_BLOCK_SCORE = 25

# ── Dashboard ─────────────────────────────────────────
DASHBOARD_PORT    = int(os.getenv("PORT", 8080))
DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "true").lower() == "true"

# ══════════════════════════════════════════════════════
# TOP 30 PARES
# ★ = confirmado en backtest propio
# ◆ = nuevo, alta liquidez/volatilidad
# ══════════════════════════════════════════════════════
SYMBOLS = [
    # ── TOP 15 originales ★ ────────────────────────────
    "RSR-USDT",
    "NCSKGME2USD-USDT",
    "LINK-USDT",
    "ZEC-USDT",
    "AKE-USDT",
    "DEEP-USDT",
    "BLESS-USDT",
    "VANRY-USDT",
    "ZEN-USDT",
    "PROVE-USDT",
    "BOME-USDT",
    "BMT-USDT",
    "SUSHI-USDT",
    "SQD-USDT",
    "CRO-USDT",
    # ── 15 nuevos de alta liquidez ◆ ───────────────────
    "SOL-USDT",
    "DOGE-USDT",
    "ADA-USDT",
    "AVAX-USDT",
    "ARB-USDT",
    "OP-USDT",
    "INJ-USDT",
    "TIA-USDT",
    "WIF-USDT",
    "PEPE-USDT",
    "BONK-USDT",
    "FTM-USDT",
    "NEAR-USDT",
    "AAVE-USDT",
    "UNI-USDT",
]
