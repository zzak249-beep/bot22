"""
config.py — SMC Bot v3.3 [PARÁMETROS OPTIMIZADOS POR BACKTEST]
==============================================================
Resultados bt_v3.py — Mejor config encontrada:
  ✅ SL=0.6x ATR   TP=2.0x ATR  (PnL +$4.77 en 7 días)
  ✅ PARTIAL_TP_ACTIVO = False   (el partial_be destruía el R:R)
  ✅ HTF flexible (NEUTRAL permite operar)
  ✅ PARES_BLOQUEADOS: KAVA-USDT, SOL-USDT (destroyers consistentes)
  ✅ SCORE_MIN = 5

Benchmark antes/después:
  ANTES: WR 26%  R:R real 0.37x  PF 0.07  PnL -$4.25/sem
  AHORA: WR 34%  R:R real 2.28x  PF 1.17  PnL +$4.77/sem
"""
import os

VERSION = "SMC-Bot v3.3 [BACKTEST-OPTIMIZED]"

def _int(v,d):
    try: return int(os.getenv(v,str(d)).split()[0].split("(")[0].strip())
    except: return d

def _float(v,d):
    try: return float(os.getenv(v,str(d)).split()[0].split("(")[0].strip())
    except: return d

def _bool(v,d):
    raw=os.getenv(v,"true" if d else "false")
    return raw.strip().lower().split()[0] in ("true","1","yes")

# ── Credenciales ──────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY",    "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── General ───────────────────────────────────────────────────
MODO_DEMO    = _bool("MODO_DEMO",    False)
LOOP_SECONDS = _int("LOOP_SECONDS",  60)

# ── Capital ───────────────────────────────────────────────────
TRADE_USDT_BASE    = _float("TRADE_USDT_BASE",    10.0)
TRADE_USDT_MAX     = _float("TRADE_USDT_MAX",     50.0)
COMPOUND_STEP_USDT = _float("COMPOUND_STEP_USDT", 50.0)
COMPOUND_ADD_USDT  = _float("COMPOUND_ADD_USDT",   1.0)

# ── Posiciones ────────────────────────────────────────────────
LEVERAGE       = _int("LEVERAGE",       10)
MAX_POSICIONES = _int("MAX_POSICIONES",  3)

# ── TP / SL — OPTIMIZADOS POR BACKTEST ───────────────────────
# Antes: SL=1.0x TP=2.0x + partial → R:R real 0.5x → PÉRDIDA
# Ahora: SL=0.6x TP=2.0x sin partial → R:R real 2.28x → +$4.77
TP_ATR_MULT       = _float("TP_ATR_MULT",      2.0)   # sin cambio
SL_ATR_MULT       = _float("SL_ATR_MULT",      0.6)   # ✅ era 1.0
PARTIAL_TP1_MULT  = _float("PARTIAL_TP1_MULT", 1.0)
PARTIAL_TP_ACTIVO = _bool("PARTIAL_TP_ACTIVO", False)  # ✅ DESACTIVADO
MIN_RR            = _float("MIN_RR",            1.5)

# ── Trailing Stop ─────────────────────────────────────────────
TRAILING_ACTIVO    = _bool("TRAILING_ACTIVO",    True)
TRAILING_ACTIVAR   = _float("TRAILING_ACTIVAR",  1.5)
TRAILING_DISTANCIA = _float("TRAILING_DISTANCIA",0.8)

# ── Protección ────────────────────────────────────────────────
TIME_EXIT_HORAS = _float("TIME_EXIT_HORAS", 8.0)
MAX_PERDIDA_DIA = _float("MAX_PERDIDA_DIA", 25.0)

# ── Score ─────────────────────────────────────────────────────
SCORE_MIN    = _int("SCORE_MIN",      5)
FVG_MIN_PIPS = _float("FVG_MIN_PIPS", 0.0)
EQ_LOOKBACK  = _int("EQ_LOOKBACK",   50)
EQ_THRESHOLD = _float("EQ_THRESHOLD", 0.1)
EQ_PIVOT_LEN = _int("EQ_PIVOT_LEN",   5)

# ── Killzones ─────────────────────────────────────────────────
KZ_ASIA_START   = _int("KZ_ASIA_START",    0)
KZ_ASIA_END     = _int("KZ_ASIA_END",    240)
KZ_LONDON_START = _int("KZ_LONDON_START",420)
KZ_LONDON_END   = _int("KZ_LONDON_END",  600)
KZ_NY_START     = _int("KZ_NY_START",    780)
KZ_NY_END       = _int("KZ_NY_END",      960)

# ── Indicadores ───────────────────────────────────────────────
EMA_FAST       = _int("EMA_FAST",      21)
EMA_SLOW       = _int("EMA_SLOW",      50)
RSI_PERIOD     = _int("RSI_PERIOD",    14)
RSI_BUY_MAX    = _float("RSI_BUY_MAX",  55.0)
RSI_SELL_MIN   = _float("RSI_SELL_MIN", 45.0)
ATR_PERIOD     = _int("ATR_PERIOD",    14)
PIVOT_NEAR_PCT = _float("PIVOT_NEAR_PCT",0.80)

# ── Timeframe ─────────────────────────────────────────────────
TIMEFRAME     = os.getenv("TIMEFRAME", "5m").strip()
CANDLES_LIMIT = _int("CANDLES_LIMIT", 200)

# ── MTF ───────────────────────────────────────────────────────
MTF_ACTIVO    = _bool("MTF_ACTIVO",    True)
MTF_TIMEFRAME = os.getenv("MTF_TIMEFRAME", "1h").strip()
MTF_CANDLES   = _int("MTF_CANDLES",   60)

# ── Order Blocks ──────────────────────────────────────────────
OB_ACTIVO   = _bool("OB_ACTIVO",    True)
OB_LOOKBACK = _int("OB_LOOKBACK",  30)

# ── BOS / CHoCH ───────────────────────────────────────────────
BOS_ACTIVO = _bool("BOS_ACTIVO", True)

# ── Rango Asia ────────────────────────────────────────────────
ASIA_RANGE_ACTIVO  = _bool("ASIA_RANGE_ACTIVO",  True)
VELA_CONFIRMACION  = _bool("VELA_CONFIRMACION",  True)
CORRELACION_ACTIVO = _bool("CORRELACION_ACTIVO", True)

# ── Scanner ───────────────────────────────────────────────────
VOLUMEN_MIN_24H  = _float("VOLUMEN_MIN_24H", 500_000.0)
MAX_PARES_SCAN   = _int("MAX_PARES_SCAN",    0)
ANALISIS_WORKERS = _int("ANALISIS_WORKERS",  8)

SOLO_LONG = _bool("SOLO_LONG", False)

# ── Pares bloqueados ──────────────────────────────────────────
# ✅ KAVA-USDT y SOL-USDT bloqueados por backtest
# (PnL consistentemente negativo en todos los tests)
_bloq_env = os.getenv("PARES_BLOQUEADOS", "")
_bloq_default = ["KAVA-USDT", "SOL-USDT"]
PARES_BLOQUEADOS = list(set(
    [p.strip() for p in _bloq_env.split(",") if p.strip()] + _bloq_default
))
PARES_PRIORITARIOS = [p.strip() for p in os.getenv("PARES_PRIORITARIOS","").split(",") if p.strip()]

MEMORY_DIR = os.getenv("MEMORY_DIR", "")

def validar():
    e = []
    if not MODO_DEMO:
        if not BINGX_API_KEY:    e.append("BINGX_API_KEY no configurada")
        if not BINGX_SECRET_KEY: e.append("BINGX_SECRET_KEY no configurada")
    if not TELEGRAM_TOKEN:       e.append("TELEGRAM_TOKEN no configurada")
    if LEVERAGE < 1 or LEVERAGE > 125:
        e.append(f"LEVERAGE={LEVERAGE} fuera de rango (1-125)")
    if TRADE_USDT_BASE < 1:
        e.append(f"TRADE_USDT_BASE={TRADE_USDT_BASE} muy bajo (min $1)")
    if PARTIAL_TP_ACTIVO:
        e.append("⚠️  PARTIAL_TP_ACTIVO=True destruye el R:R (backtest probado)")
    return e
