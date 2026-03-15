"""
config.py — SMC Bot BingX v4.3 [FIXES DEFAULTS]

CAMBIOS v4.3:
  ✅ KZ_REQUERIDA=False  → el bot operaba SOLO 10h/día (fuera de KZ = "Sin señales")
  ✅ SCORE_MIN=5         → 7 era demasiado alto, apenas generaba señales
  ✅ COOLDOWN_VELAS=5    → 8 bloqueaba señales válidas (25min vs 40min)
  ✅ TIME_EXIT_HORAS=8   → coherente con Telegram (antes 6h)
  ✅ VOLUMEN_MIN_24H=200k → más pares escaneados con liquidez suficiente
"""
import os

VERSION = "SMC-Bot v4.5 [PRECISION+APRENDE+COMPOUNDING]"

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

# ── Credenciales ──────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── General ───────────────────────────────────────────────────
MODO_DEMO    = _bool("MODO_DEMO",    False)
LOOP_SECONDS = _int("LOOP_SECONDS",  60)

# ── Capital y Compounding ─────────────────────────────────────
TRADE_USDT_BASE    = _float("TRADE_USDT_BASE",    10.0)
TRADE_USDT_MAX     = _float("TRADE_USDT_MAX",     50.0)
COMPOUND_STEP_USDT = _float("COMPOUND_STEP_USDT", 30.0)
COMPOUND_ADD_USDT  = _float("COMPOUND_ADD_USDT",   1.0)

# ── Posiciones ────────────────────────────────────────────────
LEVERAGE       = _int("LEVERAGE",       10)
MAX_POSICIONES = _int("MAX_POSICIONES",  5)   # FIX v4.4: solo cuenta trades del bot, no manuales

# ── TP / SL ───────────────────────────────────────────────────
TP_ATR_MULT       = _float("TP_ATR_MULT",      2.5)
SL_ATR_MULT       = _float("SL_ATR_MULT",      1.0)
PARTIAL_TP1_MULT  = _float("PARTIAL_TP1_MULT", 1.2)
PARTIAL_TP_ACTIVO = _bool("PARTIAL_TP_ACTIVO",  True)
MIN_RR            = _float("MIN_RR",            2.0)

# ── Trailing Stop ─────────────────────────────────────────────
TRAILING_ACTIVO    = _bool("TRAILING_ACTIVO",    True)
TRAILING_ACTIVAR   = _float("TRAILING_ACTIVAR",  1.2)
TRAILING_DISTANCIA = _float("TRAILING_DISTANCIA", 0.8)

# ── Protección ────────────────────────────────────────────────
TIME_EXIT_HORAS = _float("TIME_EXIT_HORAS", 8.0)    # FIX: era 6h, Telegram muestra 8h
MAX_PERDIDA_DIA = _float("MAX_PERDIDA_DIA", 20.0)

# ── Score ─────────────────────────────────────────────────────
# FIX: SCORE_MIN era 7, generaba muy pocas señales
# Bajar a 5 permite operar con FVG + 3 confluencias adicionales
SCORE_MIN    = _int("SCORE_MIN",       2)     # v4.5: score puro — sube a 4-5 via Railway si hay muchas señales
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

# FIX CRÍTICO: KZ_REQUERIDA era True → el bot no generaba señales de 16:00 a 0:00 UTC
# Con False el bot opera las 24h; el score de KZ sigue sumando +1 cuando estamos en KZ
KZ_REQUERIDA = _bool("KZ_REQUERIDA", False)   # FIX: era True → False

# ── Indicadores ───────────────────────────────────────────────
EMA_FAST       = _int("EMA_FAST",      21)
EMA_SLOW       = _int("EMA_SLOW",      50)
EMA_LOCAL_FAST = _int("EMA_LOCAL_FAST",  9)
EMA_LOCAL_SLOW = _int("EMA_LOCAL_SLOW", 21)
RSI_PERIOD     = _int("RSI_PERIOD",    14)
RSI_BUY_MAX    = _float("RSI_BUY_MAX",  70.0)   # FIX: era 68 → 70 (más permisivo)
RSI_SELL_MIN   = _float("RSI_SELL_MIN", 30.0)   # FIX: era 32 → 30
ATR_PERIOD     = _int("ATR_PERIOD",    14)
ATR_FAST       = _int("ATR_FAST",       7)

PIVOT_NEAR_PCT = _float("PIVOT_NEAR_PCT", 2.0)   # FIX: era 1.5% → 2.0% (zona más amplia)

# ── Filtros de precisión ──────────────────────────────────────
PINBAR_RATIO     = _float("PINBAR_RATIO",    0.50)   # FIX: era 0.55 → 0.50 (más detecciones)
ENGULF_ACTIVO    = _bool("ENGULF_ACTIVO",    True)
VWAP_ACTIVO      = _bool("VWAP_ACTIVO",      True)
VWAP_PCT         = _float("VWAP_PCT",        0.50)   # FIX: era 0.20% → 0.50% (menos restrictivo)

# FIX: COOLDOWN_VELAS era 8 (40min en 5m) → 5 (25min)
# Evitamos señales dobles sin bloquear setups válidos consecutivos
COOLDOWN_VELAS   = _int("COOLDOWN_VELAS",    5)      # FIX: era 8 → 5
MOMENTUM_ACTIVO  = _bool("MOMENTUM_ACTIVO",  True)

# ── Timeframes ────────────────────────────────────────────────
TIMEFRAME     = os.getenv("TIMEFRAME",     "5m").strip()
CANDLES_LIMIT = _int("CANDLES_LIMIT",     200)
MTF_ACTIVO    = _bool("MTF_ACTIVO",        True)
MTF_TIMEFRAME = os.getenv("MTF_TIMEFRAME", "1h").strip()
MTF_CANDLES   = _int("MTF_CANDLES",        60)

# ── Módulos activos ───────────────────────────────────────────
OB_ACTIVO          = _bool("OB_ACTIVO",          True)
OB_LOOKBACK        = _int("OB_LOOKBACK",          30)
BOS_ACTIVO         = _bool("BOS_ACTIVO",          True)
ASIA_RANGE_ACTIVO  = _bool("ASIA_RANGE_ACTIVO",   True)
VELA_CONFIRMACION  = _bool("VELA_CONFIRMACION",   True)
CORRELACION_ACTIVO = _bool("CORRELACION_ACTIVO",  True)
MACD_ACTIVO        = _bool("MACD_ACTIVO",         True)
SWEEP_ACTIVO       = _bool("SWEEP_ACTIVO",        True)
SWEEP_LOOKBACK     = _int("SWEEP_LOOKBACK",       20)

# ── Liquidez / Bellsz ─────────────────────────────────────────
# Todos los parámetros que usa main_bellsz.py y liquidez.py
LIQ_MARGEN       = _float("LIQ_MARGEN",       0.15)   # % margen de distancia al nivel BSL/SSL
LIQ_LOOKBACK     = _int("LIQ_LOOKBACK",        30)    # velas lookback para detección de niveles
LIQ_RVOL_MIN     = _float("LIQ_RVOL_MIN",      1.2)   # RVOL mínimo para confirmar sweep institucional
LIQ_ATR_DEPTH    = _float("LIQ_ATR_DEPTH",     0.20)  # profundidad mínima sweep en múltiplos ATR
LIQ_ANALISIS_HTF = _bool("LIQ_ANALISIS_HTF",   True)  # activar análisis HTF en bellsz
LIQ_SCORE_MIN    = _int("LIQ_SCORE_MIN",        1)    # score mínimo del módulo liquidez


# ── Bellsz / main_bellsz.py ───────────────────────────────────
# Parámetros específicos de la estrategia de liquidez lateral
TP_DIST_MULT       = _float("TP_DIST_MULT",       3.0)   # TP = entry ± ATR * mult
TP1_DIST_MULT      = _float("TP1_DIST_MULT",      1.5)   # TP parcial = entry ± ATR * mult
TRAILING_ACTIVAR   = _float("TRAILING_ACTIVAR",   1.5)   # activar trailing a X ATR de profit
TRAILING_DISTANCIA = _float("TRAILING_DISTANCIA", 1.0)   # distancia trailing en ATR
BE_ACTIVO          = _bool("BE_ACTIVO",           True)  # breakeven automático
DISPLACEMENT_ACTIVO     = _bool("DISPLACEMENT_ACTIVO",      True)
PREMIUM_DISCOUNT_ACTIVO = _bool("PREMIUM_DISCOUNT_ACTIVO",  True)
MTF_4H_ACTIVO      = _bool("MTF_4H_ACTIVO",       True)  # usar 4H como HTF adicional
LOOP_SECONDS       = _int("LOOP_SECONDS",          90)   # segundos entre ciclos
METACLAW_ACTIVO    = _bool("METACLAW_ACTIVO",      False) # integración MetaClaw

# ── Scanner ───────────────────────────────────────────────────
# FIX: VOLUMEN_MIN_24H era 500k → 200k para cubrir más pares con liquidez
VOLUMEN_MIN_24H  = _float("VOLUMEN_MIN_24H", 200_000.0)
MAX_PARES_SCAN   = _int("MAX_PARES_SCAN",    0)
ANALISIS_WORKERS = _int("ANALISIS_WORKERS",  6)

# ── Dirección ─────────────────────────────────────────────────
SOLO_LONG = _bool("SOLO_LONG", False)

# ── Listas ────────────────────────────────────────────────────
PARES_BLOQUEADOS   = [p.strip() for p in os.getenv("PARES_BLOQUEADOS",   "RESOLV-USDT").split(",") if p.strip()]
PARES_PRIORITARIOS = [p.strip() for p in os.getenv("PARES_PRIORITARIOS", "").split(",") if p.strip()]

# ── Persistencia ──────────────────────────────────────────────
MEMORY_DIR = os.getenv("MEMORY_DIR", "")

# ── Modo BingX ────────────────────────────────────────────────
# "auto"   = detectar automáticamente al arrancar (recomendado)
# "hedge"  = forzar modo Hedge (positionSide=LONG/SHORT)
# "oneway" = forzar modo One-Way
BINGX_MODE = os.getenv("BINGX_MODE", "auto").strip().lower()


# ── Campos adicionales para main_bellsz y analizar_bellsz ────
HTF_H1_TF   = os.getenv("HTF_H1_TF",   "1h")
HTF_H4_TF   = os.getenv("HTF_H4_TF",   "4h")
HTF_D_TF    = os.getenv("HTF_D_TF",    "1d")
HTF_CANDLES = _int("HTF_CANDLES",       60)

TP1_DIST_MULT     = _float("TP1_DIST_MULT",     1.5)
METACLAW_VETO_MINIMO = _int("METACLAW_VETO_MINIMO", 7)
VOLUMEN_MIN_24H   = _float("VOLUMEN_MIN_24H",   200_000.0)

# Campos de analizar_bellsz
PREMIUM_DISCOUNT_LB  = _int("PREMIUM_DISCOUNT_LB",  50)
KZ_ASIA_START        = _int("KZ_ASIA_START",          0)
KZ_ASIA_END          = _int("KZ_ASIA_END",          240)
KZ_LONDON_START      = _int("KZ_LONDON_START",      420)
KZ_LONDON_END        = _int("KZ_LONDON_END",        600)
KZ_NY_START          = _int("KZ_NY_START",          780)
KZ_NY_END            = _int("KZ_NY_END",            960)
OB_ACTIVO            = _bool("OB_ACTIVO",          True)
OB_LOOKBACK          = _int("OB_LOOKBACK",           30)
BOS_ACTIVO           = _bool("BOS_ACTIVO",         True)
FVG_ACTIVO           = _bool("FVG_ACTIVO",         True)
SWEEP_ACTIVO         = _bool("SWEEP_ACTIVO",       True)
SWEEP_LOOKBACK       = _int("SWEEP_LOOKBACK",        20)
PINBAR_RATIO         = _float("PINBAR_RATIO",      0.50)
VWAP_ACTIVO          = _bool("VWAP_ACTIVO",        True)
VWAP_PCT             = _float("VWAP_PCT",          0.50)
MTF_4H_ACTIVO        = _bool("MTF_4H_ACTIVO",      True)
ASIA_RANGE_ACTIVO    = _bool("ASIA_RANGE_ACTIVO",  True)
VELA_CONFIRMACION    = _bool("VELA_CONFIRMACION",  True)

def validar():
    errores = []
    if not MODO_DEMO:
        if not BINGX_API_KEY:    errores.append("BINGX_API_KEY no configurada")
        if not BINGX_SECRET_KEY: errores.append("BINGX_SECRET_KEY no configurada")
    if not TELEGRAM_TOKEN:
        errores.append("TELEGRAM_TOKEN no configurada")
    if LEVERAGE < 1 or LEVERAGE > 125:
        errores.append(f"LEVERAGE={LEVERAGE} fuera de rango")
    if TRADE_USDT_BASE < 1:
        errores.append(f"TRADE_USDT_BASE={TRADE_USDT_BASE} muy bajo")
    if SCORE_MIN < 1 or SCORE_MIN > 14:
        errores.append(f"SCORE_MIN={SCORE_MIN} debe ser 1-14")
    if MIN_RR < 1.0:
        errores.append(f"MIN_RR={MIN_RR} peligroso (mín 1.0)")
    return errores
