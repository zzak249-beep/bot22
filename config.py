"""
config.py — v15 FULL SCAN
Base: v13 [OK] PF=2.42  WR=75%  ROI=+1.1%

MODO: Scanner automático — analiza TODOS los pares USDT de BingX
  con volumen 24h > MIN_VOLUME_USDT y spread < 1.5%.
  symbols_loader.py carga y refresca la lista cada 6h automáticamente.
  La lista SYMBOLS aquí es solo fallback si el scanner falla.

main.py requiere Elite v6 — este config cubre TODOS los atributos.
Las variables de entorno (API keys, Telegram) se leen desde .env / Railway.
"""

import os
from dotenv import load_dotenv
load_dotenv()

# ══════════════════════════════════════════════════════════════
# CREDENCIALES — se inyectan desde Railway Variables / .env
# ══════════════════════════════════════════════════════════════
BINGX_API_KEY  = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET   = os.getenv("BINGX_SECRET", "")
TELEGRAM_TOKEN = os.getenv("TG_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID", "")

# ══════════════════════════════════════════════════════════════
# ESTRATEGIA BB + RSI
# ══════════════════════════════════════════════════════════════
BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14
RSI_OB         = 30     # alias usado por strategy/learner/notifier (= RSI_LONG)
RSI_OS         = 70     # sobrecomprado — usado en strategy.py para SHORT
SL_ATR         = 1.5    # multiplicador ATR para stop loss
PARTIAL_TP_ATR = 2.0    # multiplicador ATR para TP parcial

# ── Filtro de tendencia ────────────────────────────────────
TREND_LOOKBACK = 8
TREND_THRESH   = 0.25
SMA_PERIOD     = 50
LONG_ONLY_UP   = True   # v15: solo longs cuando trend="up" (WR 62-75%)

# ══════════════════════════════════════════════════════════════
# GESTIÓN DE CAPITAL
# ══════════════════════════════════════════════════════════════
LEVERAGE        = 2
RISK_PCT        = 0.02   # 2% del balance por trade
INITIAL_BAL     = 100.0
MIN_USDT_BALANCE = 5.0   # balance mínimo para ejecutar (si < → señal manual)
BALANCE_SNAPSHOT = 0.0   # se actualiza en main.py al arrancar

# ══════════════════════════════════════════════════════════════
# FILTROS DE SEÑAL
# ══════════════════════════════════════════════════════════════
SCORE_MIN      = 48
MIN_RR         = 1.5     # usado en strategy.py (backtest)
MIN_RR_RATIO   = 1.5     # usado en main.py (validación live)
COOLDOWN_BARS  = 3
SHORT_ENABLED  = True    # permitir operaciones SHORT

# ── ADX ───────────────────────────────────────────────────
ADX_FILTER_ENABLED = False  # True = requiere ADX > ADX_MIN para entrar
ADX_MIN            = 20
ADX_PERIOD         = 14

# ── EMA 200 ───────────────────────────────────────────────
EMA_TREND_ENABLED  = False  # True = filtra entradas contra EMA200

# ── StochRSI ──────────────────────────────────────────────
STOCH_RSI_ENABLED = True
STOCH_RSI_PERIOD  = 14
STOCH_RSI_K       = 3
STOCH_RSI_D       = 3
STOCH_RSI_OB      = 80   # sobrecomprado — bloquea LONGs
STOCH_RSI_OS      = 20   # sobrevendido  — bloquea SHORTs

# ── Confirmación de vela ──────────────────────────────────
CANDLE_CONFIRM_ENABLED  = True
CANDLE_CONFIRM_MIN_BODY = 0.4   # cuerpo > 40% del rango

# ── Volumen ───────────────────────────────────────────────
VOLUME_CONFIRM_ENABLED = True
VOLUME_CONFIRM_MULT    = 0.8    # volumen actual >= 80% de la media
VOLUME_SPIKE_ENABLED   = True
VOLUME_SPIKE_MULT      = 1.5    # spike de volumen para señal fuerte

# ── Momentum ──────────────────────────────────────────────
REQUIRE_MOMENTUM   = True

# ── Mercado lateral ───────────────────────────────────────
SIDEWAYS_BB_WIDTH  = 0.03   # ancho BB < 3% = mercado lateral
SIDEWAYS_ATR_RATIO = 0.005  # ATR/precio < 0.5% = sin movimiento

# ══════════════════════════════════════════════════════════════
# TRAILING STOP
# ══════════════════════════════════════════════════════════════
TRAILING_STOP_ENABLED    = True
TRAILING_STOP_ATR        = 1.5   # multiplicador ATR base
TRAILING_ACTIVATE_PCT    = 0.5   # activar cuando ganancia > 0.5%
TRAILING_DYNAMIC_ENABLED = True
TRAILING_VOL_THRESHOLD   = 0.015  # ATR/precio > 1.5% = alta volatilidad
TRAILING_ATR_HIGH_VOL    = 2.0    # trailing amplio en alta vol
TRAILING_ATR_LOW_VOL     = 1.0    # trailing ajustado en baja vol

# ══════════════════════════════════════════════════════════════
# MULTI-TP (3 niveles)
# ══════════════════════════════════════════════════════════════
MULTI_TP_ENABLED  = True
PARTIAL_TP_ENABLED = True   # fallback si MULTI_TP_ENABLED=False
PARTIAL_TP_PCT    = 0.50    # cerrar 50% en TP parcial (legado)
TP1_ATR_MULT      = 1.2
TP2_ATR_MULT      = 2.0
TP1_CLOSE_PCT     = 0.30    # cerrar 30% en TP1
TP2_CLOSE_PCT     = 0.40    # cerrar 40% en TP2

# ══════════════════════════════════════════════════════════════
# STALE TRADE (cerrar trades parados)
# ══════════════════════════════════════════════════════════════
STALE_TRADE_ENABLED  = True
STALE_TRADE_HOURS    = 48      # cerrar si lleva > 48h sin moverse
STALE_TRADE_MIN_MOVE = 0.005   # movimiento mínimo 0.5%

# ══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════════
CB_MAX_DAILY_LOSS_PCT    = 0.05   # pausar si pérdida diaria > 5%
CB_MAX_CONSECUTIVE_LOSS  = 4      # pausar tras 4 pérdidas seguidas

# ══════════════════════════════════════════════════════════════
# FILTRO HORARIO
# ══════════════════════════════════════════════════════════════
TIME_FILTER_ENABLED   = True
TIME_FILTER_OFF_START = 1    # UTC — no operar de 01h a 06h (baja liquidez)
TIME_FILTER_OFF_END   = 6

# ══════════════════════════════════════════════════════════════
# SENTIMIENTO
# ══════════════════════════════════════════════════════════════
SENTIMENT_ENABLED     = False   # CryptoPanic (requiere API key para uso intenso)
SENTIMENT_CACHE_MIN   = 30      # minutos de cache
SENTIMENT_BLOCK_PANIC = True
CRYPTOPANIC_API_KEY   = os.getenv("CRYPTOPANIC_API_KEY", "")

FEAR_GREED_ENABLED    = True
FEAR_GREED_MIN        = 15    # bloquear LONGs si FGI < 15 (pánico extremo)
FEAR_GREED_MAX        = 90    # bloquear SHORTs si FGI > 90 (euforia extrema)
FEAR_GREED_CACHE_MIN  = 60    # minutos de cache

# ══════════════════════════════════════════════════════════════
# LOOP / OPERATIVA
# ══════════════════════════════════════════════════════════════
LOOP_SECONDS    = 300    # escanear cada 5 minutos
TIMEFRAME       = "1h"
TIMEFRAME_HI    = "4h"
MAX_POSITIONS   = 4      # 4 simultáneos — conservador con 18 pares validados
SCAN_BATCH_SIZE = 15     # lotes más grandes para cubrir más pares

# ══════════════════════════════════════════════════════════════
# SÍMBOLOS — FULL SCAN AUTOMÁTICO
# symbols_loader.py descarga TODOS los pares activos de BingX,
# filtra por volumen y spread, y actualiza cfg.SYMBOLS cada 6h.
# La lista SYMBOLS es solo FALLBACK si el API falla al arrancar.
# ══════════════════════════════════════════════════════════════
SYMBOLS_OVERRIDE = os.getenv("SYMBOLS_OVERRIDE", "")  # CSV en .env para forzar pares concretos
QUOTE_CURRENCY   = "USDT"
MIN_VOLUME_USDT  = 1_000_000   # volumen mínimo 24h: 1M USDT — captura ~80-120 pares en BingX
MAX_SYMBOLS      = 0           # 0 = usar lista SYMBOLS de arriba (scanner desactivado)
EXCLUDE_SYMBOLS  = [           # siempre excluidos
    "USDC-USDT", "BUSD-USDT", "TUSD-USDT", "USDP-USDT",  # stablecoins
]

# ── Pares validados por scanner_pares.py sobre 626 pares BingX ──
# Criterio: WR >= 50% Y PF >= 1.5  (backtest 2026-01 a 2026-03)
# NCSKGME2USD excluido — es acción tokenizada, no cripto pura
# IMPORTANTE: formato ccxt BingX futuros = "XXX/USDT:USDT"
SYMBOLS = [
    # ★ Elite — WR 67-100%, PF >= 4.0
    "RSR/USDT:USDT",     # WR:100% PF:999  $+0.83 — mejor par del scanner
    "LINK/USDT:USDT",    # WR: 67% PF:16.1 $+0.36
    "DEEP/USDT:USDT",    # WR: 67% PF: 8.6 $+0.33
    "BLESS/USDT:USDT",   # WR: 67% PF: 8.5 $+0.29
    "ZEC/USDT:USDT",     # WR: 67% PF: 7.3 $+0.37
    "VANRY/USDT:USDT",   # WR: 67% PF: 4.7 $+0.25
    # ★ Sólidos — WR 50-67%, PF 2.0-4.1
    "PROVE/USDT:USDT",   # WR: 50% PF: 4.1 $+0.19
    "AKE/USDT:USDT",     # WR: 50% PF: 3.8 $+0.43
    "BOME/USDT:USDT",    # WR: 50% PF: 3.6 $+0.20
    "BMT/USDT:USDT",     # WR: 60% PF: 3.6 $+0.17
    "ZEN/USDT:USDT",     # WR: 50% PF: 2.7 $+0.25
    "SUSHI/USDT:USDT",   # WR: 67% PF: 2.6 $+0.11
    "SQD/USDT:USDT",     # WR: 50% PF: 2.3 $+0.11
    "CRO/USDT:USDT",     # WR: 67% PF: 2.2 $+0.07
    "SOL/USDT:USDT",     # WR: 67% PF: 2.0 $+0.12
    "W/USDT:USDT",       # WR: 50% PF: 1.8 $+0.11
    "PUFFER/USDT:USDT",  # WR: 67% PF: 1.6 $+0.07
    "LTC/USDT:USDT",     # WR: 50% PF: 1.6 $+0.09
]

VERSION = "v15-scanner"

# ── Alias de compatibilidad ────────────────────────────────
TP = PARTIAL_TP_ATR   # por si algún módulo referencia cfg.TP directamente
