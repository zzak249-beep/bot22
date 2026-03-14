"""
config.py — SMC Bot v6.0 [MÁXIMA RENTABILIDAD]
===============================================

CAMBIOS vs v5.5 (basados en diagnóstico backtest):

DIAGNÓSTICO:
  ❌ WR=31% con SL:244 vs TP:100 → precio nunca llegaba al TP
  ❌ HTF=NEUTRAL WR=32% → NEUTRAL debería bloquear como BEAR
  ❌ R:R real=0.59x en V4 → SL estrecho pero TP muy lejos
  ❌ 368 trades en 14 días = overtrading
  ❌ OB mitigado no detectado → entradas en zonas inválidas

FIXES:
  ✅ SCORE_MIN: 7 → 8  (menos trades, más calidad)
  ✅ SL_ATR_MULT: 1.5 → 1.2  (SL más ajustado pero no tanto como v3.2)
  ✅ TP_ATR_MULT: 2.5 → 2.0  (TP más realista, más hits)
  ✅ PARTIAL_TP1_MULT: 1.2 → 0.9  (TP1 alcanzable en ~1 ATR)
  ✅ MIN_RR: 1.8 → 2.0  (exigir mejor R:R)
  ✅ COOLDOWN_VELAS: 8 → 10  (más espacio entre trades)
  ✅ TIME_EXIT_HORAS: 16 → 12  (cerrar más rápido trades muertos)
  ✅ MAX_POSICIONES: 3 → 3  (sin cambio, ya era correcto)
  ✅ MACD_ACTIVO: True  (confluencia adicional)
  ✅ MTF_4H_ACTIVO: True  (confirmación en 4h)
  ✅ PREMIUM_DISCOUNT_ACTIVO: True  (no entrar en zona equivocada)
  ✅ DISPLACEMENT_ACTIVO: True  (confirmar impulso institucional)
  ✅ SWEEP_ACTIVO: True  (señal institucional fuerte)
  ✅ VOLUMEN_MIN_24H: 2M → 5M  (solo pares con liquidez alta)
  ✅ RSI_BUY_MAX: 65 → 60  (no entrar en RSI alto)
  ✅ RSI_SELL_MIN: 35 → 40  (no entrar en RSI bajo)
  ✅ TRAILING_ACTIVAR: 1.5 → 1.2  (activar trailing antes)
  ✅ TRAILING_DISTANCIA: 1.2 → 1.0  (trail más ajustado)
"""
import os

VERSION = "SMC-Bot v6.0 [MaxRentabilidad]"


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
BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = (os.getenv("BINGX_SECRET_KEY", "")
                    or os.getenv("BINGX_API_SECRET", ""))
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Bot Control ───────────────────────────────────────────────
MODO_DEMO    = _bool("MODO_DEMO",    False)
LOOP_SECONDS = _int("LOOP_SECONDS",  60)

# ── MetaClaw (IA de validación) ───────────────────────────────
METACLAW_ACTIVO        = _bool("METACLAW_ACTIVO",        True)
METACLAW_CONFIANZA_MIN = _int("METACLAW_CONFIANZA_MIN",  4)
METACLAW_VETO_MINIMO   = _int("METACLAW_VETO_MINIMO",    5)

# ── Gestión de capital ────────────────────────────────────────
TRADE_USDT_BASE    = _float("TRADE_USDT_BASE",    10.0)
TRADE_USDT_MAX     = _float("TRADE_USDT_MAX",     50.0)
COMPOUND_STEP_USDT = _float("COMPOUND_STEP_USDT", 50.0)
COMPOUND_ADD_USDT  = _float("COMPOUND_ADD_USDT",   1.0)

LEVERAGE       = _int("LEVERAGE",       10)
MAX_POSICIONES = _int("MAX_POSICIONES",  3)

# ── SL/TP — OPTIMIZADO v6.0 ───────────────────────────────────
# FIX: TP más realista (2.0x), SL ajustado (1.2x)
# backtest mostró que TP nunca se alcanzaba con 2.5x ATR
TP_ATR_MULT       = _float("TP_ATR_MULT",      2.0)    # era 2.5 → reducido
SL_ATR_MULT       = _float("SL_ATR_MULT",      1.2)    # era 1.5 → más ajustado
PARTIAL_TP1_MULT  = _float("PARTIAL_TP1_MULT", 0.9)    # era 1.2 → TP1 alcanzable
PARTIAL_TP_ACTIVO = _bool("PARTIAL_TP_ACTIVO",  True)
MIN_RR            = _float("MIN_RR",            2.0)    # era 1.8 → más exigente

# ── Trailing Stop ─────────────────────────────────────────────
TRAILING_ACTIVO    = _bool("TRAILING_ACTIVO",    True)
TRAILING_ACTIVAR   = _float("TRAILING_ACTIVAR",  1.2)   # era 1.5 → activar antes
TRAILING_DISTANCIA = _float("TRAILING_DISTANCIA", 1.0)  # era 1.2 → trail más ajustado

# ── Límites de tiempo y pérdida ───────────────────────────────
TIME_EXIT_HORAS = _float("TIME_EXIT_HORAS", 12.0)   # era 16 → cerrar antes
MAX_PERDIDA_DIA = _float("MAX_PERDIDA_DIA", 20.0)

# ── Scoring — OPTIMIZADO v6.0 ─────────────────────────────────
SCORE_MIN    = _int("SCORE_MIN",       8)    # era 7 → más filtros
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
KZ_REQUERIDA    = _bool("KZ_REQUERIDA",  False)

# ── Indicadores técnicos ──────────────────────────────────────
EMA_FAST       = _int("EMA_FAST",      21)
EMA_SLOW       = _int("EMA_SLOW",      50)
EMA_LOCAL_FAST = _int("EMA_LOCAL_FAST",  9)
EMA_LOCAL_SLOW = _int("EMA_LOCAL_SLOW", 21)
RSI_PERIOD     = _int("RSI_PERIOD",    14)
RSI_BUY_MAX    = _float("RSI_BUY_MAX",  60.0)   # era 65 → no entrar en RSI alto
RSI_SELL_MIN   = _float("RSI_SELL_MIN", 40.0)   # era 35 → no entrar en RSI bajo
ATR_PERIOD     = _int("ATR_PERIOD",    14)
ATR_FAST       = _int("ATR_FAST",       7)
PIVOT_NEAR_PCT = _float("PIVOT_NEAR_PCT", 1.5)

# ── Patrones de vela ──────────────────────────────────────────
PINBAR_RATIO    = _float("PINBAR_RATIO",    0.50)
ENGULF_ACTIVO   = _bool("ENGULF_ACTIVO",    True)
VWAP_ACTIVO     = _bool("VWAP_ACTIVO",      True)
VWAP_PCT        = _float("VWAP_PCT",        0.30)   # era 0.50 → más sensible
COOLDOWN_VELAS  = _int("COOLDOWN_VELAS",    10)     # era 8 → más cooldown
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

# ── Filtros de volumen ────────────────────────────────────────
# v6.0: subido a 5M para solo operar pares muy líquidos
VOLUMEN_MIN_24H      = _float("VOLUMEN_MIN_24H",    5_000_000.0)   # era 2M → 5M
VOLUMEN_MIN_LOW_VOL  = _float("VOLUMEN_MIN_LOW_VOL",  500_000.0)
SCORE_MIN_LOW_VOL    = _int("SCORE_MIN_LOW_VOL",         10)
LOW_VOL_ACTIVO       = _bool("LOW_VOL_ACTIVO",          False)

# ── Scanner ───────────────────────────────────────────────────
MAX_PARES_SCAN   = _int("MAX_PARES_SCAN",    0)
ANALISIS_WORKERS = _int("ANALISIS_WORKERS",  6)
SOLO_LONG        = _bool("SOLO_LONG",        False)

# ── Range Trading ─────────────────────────────────────────────
RANGE_ACTIVO    = _bool("RANGE_ACTIVO",     False)
RANGE_ADX_MAX   = _float("RANGE_ADX_MAX",  22.0)
RANGE_SCORE_MIN = _int("RANGE_SCORE_MIN",    7)

# ── Pares bloqueados ──────────────────────────────────────────
# v6.0: lista expandida con pares problemáticos históricos
_bloqueados_default = (
    "RESOLV-USDT,KAVA-USDT,AXS-USDT,"
    "GRASS-USDT,NTRN-USDT,AWE-USDT,"
    "DUSK-USDT,ME-USDT,2Z-USDT,"
    "BROCCOLIF3B-USDT,PAXG-USDT,XAUT-USDT,"
    # Añadidos v6.0: pares del backtest con WR < 25%
    "APT-USDT,AVAX-USDT,XRP-USDT,DOGE-USDT"
)
PARES_BLOQUEADOS = [
    p.strip() for p in os.getenv("PARES_BLOQUEADOS", _bloqueados_default).split(",")
    if p.strip()
]
PARES_PRIORITARIOS = [
    p.strip() for p in os.getenv("PARES_PRIORITARIOS", "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT").split(",")
    if p.strip()
]

MEMORY_DIR = os.getenv("MEMORY_DIR", "")
BINGX_MODE = os.getenv("BINGX_MODE", "auto").strip().lower()


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
    if MIN_RR < 1.0:
        errores.append(f"MIN_RR={MIN_RR} peligroso (min 1.0)")
    return errores
