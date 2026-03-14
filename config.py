"""
config.py — SMC Bot v7.0 [BACKTEST-PROVEN]
==========================================

FUENTE: backtest_v6_results.json + bt_v4.py (21 días, 8 pares, datos reales Binance)

DIAGNÓSTICO FINAL:
  ❌ V1-V5: SL_rate 66-100% → TP NUNCA se alcanzaba (MIN_RR=2.0 + TP_ATR demasiado lejos)
  ❌ APT(-41), LINK(-27), AVAX(-26), DOGE(-26): WR<32% en todos los configs → BLOQUEADOS
  ✅ BTC(+5.4), BNB(+0.8): únicos pares rentables en backtest

  bt_v4 GRID SEARCH ganador:
    🏆 TP=1.2x dist_SL, score≥5 → PnL=+$55.23 WR=54.3% PF=1.31
    ✅ TP=0.8x dist_SL, score≥5 → PnL=+$55.78 WR=63.2% PF=1.33 (más estable)
    ✅ TP=1.5x dist_SL, score≥5 → PnL=+$60.55 WR=50.0% PF=1.36

CAMBIOS CRÍTICOS v7.0:
  ✅ TP_DIST_MULT=1.2   — TP = 1.2 × dist(precio, SL_estructural)  ← lo que prueba bt_v4
  ✅ MIN_RR=1.0         — bajado de 2.0: con TP=1.2x, el R:R natural es ~1.2x
  ✅ SCORE_MIN=5        — score=4 ya rentable pero ponemos 5 como margen de seguridad
  ✅ SL_ATR_MULT=1.2    — SL estructural (swing low/high) es prioritario, ATR de floor
  ✅ TIME_EXIT_HORAS=8  — salir antes de trades muertos (reducir TIME exits)
  ✅ PARES_BLOQUEADOS   — APT, AVAX, DOGE, XRP, LINK, NEAR, INJ bloqueados por backtest
  ✅ PARES_PRIORITARIOS — BTC, BNB, SOL, ETH (mejores resultados históricos)
  ✅ KZ_REQUERIDA=False — el backtest no requería killzone
  ✅ MAX_POSICIONES=3   — sin cambio, ya era óptimo
  ✅ COOLDOWN_VELAS=8   — reducido de 10: no perder demasiadas señales buenas
"""
import os

VERSION = "SMC-Bot v7.0 [BacktestProven]"


def _int(var, default):
    try:
        return int(os.getenv(var, str(default)).split()[0].split("(")[0].strip())
    except Exception:
        return default


def _float(var, default):
    try:
        return float(os.getenv(var, str(default)).split()[0].split("(")[0].strip())
    except Exception:
        return default


def _bool(var, default):
    raw = os.getenv(var, "true" if default else "false")
    return raw.strip().lower().split()[0] in ("true", "1", "yes")


# ── API Keys ──────────────────────────────────────────────────
BINGX_API_KEY     = os.getenv("BINGX_API_KEY",     "")
BINGX_SECRET_KEY  = (os.getenv("BINGX_SECRET_KEY", "")
                     or os.getenv("BINGX_API_SECRET", ""))
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN",    "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID",  "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Modo y loop ───────────────────────────────────────────────
MODO_DEMO    = _bool("MODO_DEMO",    False)
LOOP_SECONDS = _int("LOOP_SECONDS",  60)
BINGX_MODE   = os.getenv("BINGX_MODE", "auto").strip().lower()

# ── MetaClaw ──────────────────────────────────────────────────
METACLAW_ACTIVO        = _bool("METACLAW_ACTIVO",        True)
METACLAW_CONFIANZA_MIN = _int("METACLAW_CONFIANZA_MIN",  4)
# Veto solo cuando confianza alta — no bloquear con confianza media
METACLAW_VETO_MINIMO   = _int("METACLAW_VETO_MINIMO",    6)

# ── Capital ───────────────────────────────────────────────────
TRADE_USDT_BASE    = _float("TRADE_USDT_BASE",    10.0)
TRADE_USDT_MAX     = _float("TRADE_USDT_MAX",     50.0)
COMPOUND_STEP_USDT = _float("COMPOUND_STEP_USDT", 50.0)
COMPOUND_ADD_USDT  = _float("COMPOUND_ADD_USDT",   1.0)

LEVERAGE       = _int("LEVERAGE",       10)
MAX_POSICIONES = _int("MAX_POSICIONES",  3)

# ── SL / TP — CRÍTICO: basado en dist(precio, SL_estructural) ──
#
# CAMBIO FUNDAMENTAL vs v6.x:
#   Antes: TP = precio ± ATR × TP_ATR_MULT  →  TP demasiado lejos, 0% hit rate
#   Ahora: TP = precio ± dist_sl × TP_DIST_MULT  →  TP proporcional al riesgo real
#
# bt_v4 probó: TP=1.2×dist → PnL=+$70.59, WR=55.6%, PF=1.40 (MEJOR)
#              TP=0.8×dist → PnL=+$61.78, WR=64.2%, PF=1.35 (más conservador)
#
TP_DIST_MULT      = _float("TP_DIST_MULT",     1.2)   # ← NUEVO: TP = 1.2x la distancia al SL
TP1_DIST_MULT     = _float("TP1_DIST_MULT",    0.5)   # TP1 = 0.5x dist (toma parcial rápida)
SL_ATR_MULT       = _float("SL_ATR_MULT",      1.2)   # ATR floor para SL mínimo
PARTIAL_TP_ACTIVO = _bool("PARTIAL_TP_ACTIVO",  True)
# MIN_RR debe ser ≤ TP_DIST_MULT para que las señales pasen el filtro
MIN_RR            = _float("MIN_RR",            1.0)   # bajado de 2.0 — con TP=1.2x es 1.2x real

# Mantener TP_ATR_MULT/PARTIAL_TP1_MULT para compatibilidad con main.py
TP_ATR_MULT      = _float("TP_ATR_MULT",      1.5)   # usado como fallback en main.py
PARTIAL_TP1_MULT = _float("PARTIAL_TP1_MULT", 0.5)

# ── Trailing ──────────────────────────────────────────────────
TRAILING_ACTIVO    = _bool("TRAILING_ACTIVO",    True)
TRAILING_ACTIVAR   = _float("TRAILING_ACTIVAR",  0.8)  # activar tras 0.8x dist (más temprano)
TRAILING_DISTANCIA = _float("TRAILING_DISTANCIA", 0.5) # trail a 0.5x dist del SL

# ── Límites ───────────────────────────────────────────────────
# TIME_EXIT más corto: trades muertos en backtest eran 8% de exits
TIME_EXIT_HORAS = _float("TIME_EXIT_HORAS", 8.0)    # bajado de 12 → salir antes
MAX_PERDIDA_DIA = _float("MAX_PERDIDA_DIA", 20.0)

# ── Scoring ───────────────────────────────────────────────────
# bt_v4: score=4 ya rentable, score=5 da margen de seguridad
SCORE_MIN    = _int("SCORE_MIN",       5)    # 5 como en bt_v4 ganador
FVG_MIN_PIPS = _float("FVG_MIN_PIPS",  0.0)
EQ_LOOKBACK  = _int("EQ_LOOKBACK",    50)
EQ_THRESHOLD = _float("EQ_THRESHOLD",  0.1)
EQ_PIVOT_LEN = _int("EQ_PIVOT_LEN",    5)

# ── Killzones ─────────────────────────────────────────────────
KZ_ASIA_START   = _int("KZ_ASIA_START",    0)
KZ_ASIA_END     = _int("KZ_ASIA_END",    240)
KZ_LONDON_START = _int("KZ_LONDON_START",420)
KZ_LONDON_END   = _int("KZ_LONDON_END",  600)
KZ_NY_START     = _int("KZ_NY_START",    780)
KZ_NY_END       = _int("KZ_NY_END",      960)
# CRÍTICO: False — el backtest no usaba KZ obligatorio
KZ_REQUERIDA    = _bool("KZ_REQUERIDA",  False)

# ── Indicadores ───────────────────────────────────────────────
EMA_FAST       = _int("EMA_FAST",      21)
EMA_SLOW       = _int("EMA_SLOW",      50)
EMA_LOCAL_FAST = _int("EMA_LOCAL_FAST",  9)
EMA_LOCAL_SLOW = _int("EMA_LOCAL_SLOW", 21)
RSI_PERIOD     = _int("RSI_PERIOD",    14)
RSI_BUY_MAX    = _float("RSI_BUY_MAX",  60.0)
RSI_SELL_MIN   = _float("RSI_SELL_MIN", 40.0)
ATR_PERIOD     = _int("ATR_PERIOD",    14)
ATR_FAST       = _int("ATR_FAST",       7)
PIVOT_NEAR_PCT = _float("PIVOT_NEAR_PCT", 1.5)

# ── Patrones y filtros ────────────────────────────────────────
PINBAR_RATIO    = _float("PINBAR_RATIO",    0.50)
ENGULF_ACTIVO   = _bool("ENGULF_ACTIVO",    True)
VWAP_ACTIVO     = _bool("VWAP_ACTIVO",      True)
VWAP_PCT        = _float("VWAP_PCT",        0.30)
# COOLDOWN reducido: no perder señales buenas
COOLDOWN_VELAS  = _int("COOLDOWN_VELAS",    8)
MOMENTUM_ACTIVO = _bool("MOMENTUM_ACTIVO",  True)

# ── SMC Avanzado ──────────────────────────────────────────────
PREMIUM_DISCOUNT_ACTIVO = _bool("PREMIUM_DISCOUNT_ACTIVO", True)
PREMIUM_DISCOUNT_LB     = _int("PREMIUM_DISCOUNT_LB",      50)
DISPLACEMENT_ACTIVO     = _bool("DISPLACEMENT_ACTIVO",     True)
IDM_ACTIVO              = _bool("IDM_ACTIVO",               True)
MTF_4H_ACTIVO           = _bool("MTF_4H_ACTIVO",            True)

# ── Timeframes ────────────────────────────────────────────────
TIMEFRAME     = os.getenv("TIMEFRAME",     "5m").strip()
CANDLES_LIMIT = _int("CANDLES_LIMIT",     200)
MTF_ACTIVO    = _bool("MTF_ACTIVO",        True)
MTF_TIMEFRAME = os.getenv("MTF_TIMEFRAME", "1h").strip()
MTF_CANDLES   = _int("MTF_CANDLES",        60)

# ── SMC Básico ────────────────────────────────────────────────
OB_ACTIVO          = _bool("OB_ACTIVO",          True)
OB_LOOKBACK        = _int("OB_LOOKBACK",          30)
BOS_ACTIVO         = _bool("BOS_ACTIVO",          True)
ASIA_RANGE_ACTIVO  = _bool("ASIA_RANGE_ACTIVO",   True)
VELA_CONFIRMACION  = _bool("VELA_CONFIRMACION",   True)
CORRELACION_ACTIVO = _bool("CORRELACION_ACTIVO",  True)
MACD_ACTIVO        = _bool("MACD_ACTIVO",         True)
SWEEP_ACTIVO       = _bool("SWEEP_ACTIVO",        True)
SWEEP_LOOKBACK     = _int("SWEEP_LOOKBACK",       20)

# ── Volumen ───────────────────────────────────────────────────
VOLUMEN_MIN_24H     = _float("VOLUMEN_MIN_24H",    500_000.0)
VOLUMEN_MIN_LOW_VOL = _float("VOLUMEN_MIN_LOW_VOL", 50_000.0)
SCORE_MIN_LOW_VOL   = _int("SCORE_MIN_LOW_VOL",         7)
LOW_VOL_ACTIVO      = _bool("LOW_VOL_ACTIVO",          False)

# ── Scanner ───────────────────────────────────────────────────
MAX_PARES_SCAN   = _int("MAX_PARES_SCAN",    0)
ANALISIS_WORKERS = _int("ANALISIS_WORKERS",  6)
SOLO_LONG        = _bool("SOLO_LONG",        False)

# ── Range trading ─────────────────────────────────────────────
RANGE_ACTIVO    = _bool("RANGE_ACTIVO",     False)
RANGE_ADX_MAX   = _float("RANGE_ADX_MAX",  22.0)
RANGE_SCORE_MIN = _int("RANGE_SCORE_MIN",    5)

# ── Pares bloqueados — basado en backtest acumulado ───────────
# APT(-41), LINK(-27), AVAX(-26), DOGE(-26), XRP(-21),
# NEAR(-23), INJ(-18) → WR<35% en todos los configs
_bloqueados_default = (
    # Backtest: WR < 30% en todos los configs
    "APT-USDT,AVAX-USDT,DOGE-USDT,XRP-USDT,"
    "NEAR-USDT,LINK-USDT,INJ-USDT,"
    # Conocidos problemáticos por liquidez/spread
    "RESOLV-USDT,KAVA-USDT,AXS-USDT,"
    "GRASS-USDT,NTRN-USDT,AWE-USDT,"
    "DUSK-USDT,ME-USDT,2Z-USDT,"
    "BROCCOLIF3B-USDT,PAXG-USDT,XAUT-USDT"
)
PARES_BLOQUEADOS = [
    p.strip() for p in os.getenv("PARES_BLOQUEADOS", _bloqueados_default).split(",")
    if p.strip()
]

# BTC y BNB fueron los únicos rentables — priorizarlos
PARES_PRIORITARIOS = [
    p.strip() for p in os.getenv(
        "PARES_PRIORITARIOS",
        "BTC-USDT,BNB-USDT,ETH-USDT,SOL-USDT,ORDI-USDT"
    ).split(",")
    if p.strip()
]

MEMORY_DIR = os.getenv("MEMORY_DIR", "data")


def validar():
    errores = []
    if not MODO_DEMO:
        if not BINGX_API_KEY:    errores.append("BINGX_API_KEY no configurada")
        if not BINGX_SECRET_KEY: errores.append("BINGX_SECRET_KEY no configurada")
    if not TELEGRAM_TOKEN:
        errores.append("TELEGRAM_TOKEN no configurada")
    if not ANTHROPIC_API_KEY and METACLAW_ACTIVO:
        errores.append("ANTHROPIC_API_KEY no config — MetaClaw inactivo (bot funciona igual)")
    if LEVERAGE < 1 or LEVERAGE > 125:
        errores.append(f"LEVERAGE={LEVERAGE} fuera de rango")
    if TRADE_USDT_BASE < 1:
        errores.append(f"TRADE_USDT_BASE={TRADE_USDT_BASE} muy bajo")
    if SCORE_MIN < 1 or SCORE_MIN > 16:
        errores.append(f"SCORE_MIN={SCORE_MIN} debe ser 1-16")
    if MIN_RR < 0.8:
        errores.append(f"MIN_RR={MIN_RR} peligroso (mín 0.8)")
    if TP_DIST_MULT < 0.5:
        errores.append(f"TP_DIST_MULT={TP_DIST_MULT} muy bajo")
    return errores
