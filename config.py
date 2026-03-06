import os

# ══════════════════════════════════════════════════════
# config.py — BB+RSI ELITE v13.0
# ══════════════════════════════════════════════════════

VERSION = "v13.0"

# ── Credenciales ───────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Modo ───────────────────────────────────────────────
# "paper" = simula | "live" = opera real en BingX
TRADE_MODE    = os.getenv("TRADE_MODE", "paper")

# ── Temporalidad ───────────────────────────────────────
CANDLE_TF     = "15m"   # velas principales (señal)
MTF_INTERVAL  = "1h"    # velas confirmación tendencia
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 900))   # 15 min

# ── Indicadores ────────────────────────────────────────
BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14
RSI_LONG       = 36     # ampliado de 32 → más señales LONG
RSI_SHORT      = 64     # ampliado de 68 → más señales SHORT
PARTIAL_TP_ATR = 2.5
SMA_PERIOD     = 50

# ── Riesgo base ────────────────────────────────────────
LEVERAGE       = int(os.getenv("LEVERAGE",    2))
RISK_PCT       = float(os.getenv("RISK_PCT",  0.02))
INITIAL_BAL    = float(os.getenv("INITIAL_BAL", 100.0))
MIN_RR         = 1.2
SL_BUFFER      = 0.001
SCORE_MIN      = 40     # bajado de 45 → más señales
COOLDOWN_BARS  = 2

# ── Circuit breaker ────────────────────────────────────
MAX_DAILY_LOSS_PCT   = 0.50   # pausa si pierde >50% en el día
MAX_DRAWDOWN_PCT     = 0.50   # pausa si drawdown >50% desde máximo
MAX_CONCURRENT_POS   = 5      # máximo posiciones abiertas
CIRCUIT_BREAKER_LOSS = 4      # pérdidas consecutivas → reduce size 50%

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
VOLUME_MIN_RATIO = 0.03

# ── Multi-timeframe 1h como confirmación ──────────────
MTF_ENABLED       = True
MTF_BLOCK_COUNTER = True

# ── Tendencia ─────────────────────────────────────────
TREND_LOOKBACK = 8
TREND_THRESH   = 0.04

# ── Alertas siempre activas ───────────────────────────
ALERT_ALWAYS   = True   # notifica aunque max_pos o sin fondos

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
    "RSR-USDT",           # ★ WR:100% PF:999  +$0.83
    "NCSKGME2USD-USDT",   # ★ WR:100% PF:999  +$0.31
    "LINK-USDT",          # ★ WR: 67% PF:16.1 +$0.36
    "ZEC-USDT",           # ★ WR: 67% PF: 7.3 +$0.37
    "AKE-USDT",           # ★ WR: 50% PF: 3.8 +$0.43
    "DEEP-USDT",          # ★ WR: 67% PF: 8.6 +$0.33
    "BLESS-USDT",         # ★ WR: 67% PF: 8.5 +$0.29
    "VANRY-USDT",         # ★ WR: 67% PF: 4.7 +$0.25
    "ZEN-USDT",           # ★ WR: 50% PF: 2.7 +$0.25
    "PROVE-USDT",         # ★ WR: 50% PF: 4.1 +$0.19
    "BOME-USDT",          # ★ WR: 50% PF: 3.6 +$0.20
    "BMT-USDT",           # ★ WR: 60% PF: 3.6 +$0.17
    "SUSHI-USDT",         # ★ WR: 67% PF: 2.6 +$0.11
    "SQD-USDT",           # ★ WR: 50% PF: 2.3 +$0.11
    "CRO-USDT",           # ★ WR: 67% PF: 2.2 +$0.07
    # ── 15 nuevos de alta liquidez ◆ ───────────────────
    "SOL-USDT",           # ◆ alta liquidez, muy activo
    "DOGE-USDT",          # ◆ alta volatilidad
    "ADA-USDT",           # ◆ ciclos BB claros
    "AVAX-USDT",          # ◆ buena amplitud BB
    "ARB-USDT",           # ◆ volátil, RSI frecuente
    "OP-USDT",            # ◆ similar a ARB
    "INJ-USDT",           # ◆ alta volatilidad
    "TIA-USDT",           # ◆ movimientos fuertes
    "WIF-USDT",           # ◆ memecoin volátil
    "PEPE-USDT",          # ◆ alta frecuencia señales
    "BONK-USDT",          # ◆ similar a PEPE
    "FTM-USDT",           # ◆ ciclos técnicos buenos
    "NEAR-USDT",          # ◆ tendencias claras
    "AAVE-USDT",          # ◆ rebotes BB frecuentes
    "UNI-USDT",           # ◆ liquidez alta
]
