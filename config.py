"""
Configuración — Bot Supertrend + Unicorn Model (standalone)
=============================================================
Todo se lee de variables de entorno (Railway-friendly). Los defaults
son razonables para arrancar pero DEBEN revisarse antes de operar real.
"""
import os


def _f(name, default):
    return float(os.getenv(name, default))


def _i(name, default):
    return int(os.getenv(name, default))


def _b(name, default):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ── Credenciales BingX ────────────────────────────────────────────────
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")
BINGX_BASE_URL = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com")
DRY_RUN = _b("DRY_RUN", True)  # True = solo loguea señales, no envía órdenes

# ── Scanner ────────────────────────────────────────────────────────────
SCAN_CONCURRENCY = _i("SCAN_CONCURRENCY", 5)        # bajado de 8: el rate limit 100410 seguía en
                                                     # loop porque cada reintento durante el bloqueo
                                                     # parece extenderlo — ver exchange_client.py.
                                                     # Menos concurrencia = ráfaga inicial más chica
                                                     # antes de que el cooldown compartido se conozca.
SCAN_INTERVAL_SEC = _i("SCAN_INTERVAL_SEC", 420)    # 45s (default viejo) era irreal con
                                                     # SCAN_ALL_SYMBOLS=True: ~694 símbolos x 5
                                                     # klines c/u ≈ 3470 requests/ciclo, y con el
                                                     # espaciado de exchange_client.py (~8 req/s)
                                                     # un ciclo completo tarda ~7 min de por sí
MIN_24H_VOLUME_USDT = _f("MIN_24H_VOLUME_USDT", 3_000_000)  # filtra símbolos ilíquidos (solo si SCAN_ALL_SYMBOLS=False)
SCAN_ALL_SYMBOLS = _b("SCAN_ALL_SYMBOLS", True)  # True = ignora MIN_24H_VOLUME_USDT, escanea TODO BingX (menos no-cripto)
NON_CRYPTO_PREFIXES = [  # instrumentos no-cripto que BingX a veces lista
    "XAU", "XAG", "US30", "US100", "US500", "GER40", "UK100", "JP225",
    "OIL", "WTI", "BRENT", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
    "NZDUSD", "USDCAD", "USDCHF", "EURGBP", "EURJPY", "GBPJPY",
]
MAX_ACTIVE_POSITIONS = _i("MAX_ACTIVE_POSITIONS", 6)

# ── Timeframes ───────────────────────────────────────────────────────
ENTRY_TF = os.getenv("ENTRY_TF", "3m")       # Unicorn Model — timing de entrada
BIAS_TF = os.getenv("BIAS_TF", "1H")         # Supertrend — bias macro
HTF_A_TF = os.getenv("HTF_A_TF", "15m")      # fuente liquidez A del Unicorn Model
HTF_B_TF = os.getenv("HTF_B_TF", "30m")      # fuente liquidez B
HTF_C_TF = os.getenv("HTF_C_TF", "1H")       # fuente liquidez C
OB_TF = os.getenv("OB_TF", "15m")            # Order Block Engine (BigBeluga) — su propio timeframe

# ── Supertrend (BigBeluga custom) ─────────────────────────────────────
ST_LEN = _i("ST_LEN", 50)
ST_MULT = _f("ST_MULT", 3.5)

# ── Unicorn Model ──────────────────────────────────────────────────────
UNICORN_SWEEP_LB = _i("UNICORN_SWEEP_LB", 30)     # lookback de velas para sweep
UNICORN_REQUIRE_FVG = _b("UNICORN_REQUIRE_FVG", True)  # Unicorn Mode ON
UNICORN_RR = _f("UNICORN_RR", 1.5)                # risk:reward del TP
UNICORN_SL_ATR_BUFFER = _f("UNICORN_SL_ATR_BUFFER", 0.2)  # colchón extra en SL
DIRECTION = os.getenv("DIRECTION", "BOTH")        # LONG | SHORT | BOTH

# Filtro de tamaño de breaker (en múltiplos de ATR) — descarta breakers
# demasiado chicos (ruido) o demasiado grandes (movimiento ya agotado)
BREAKER_MIN_ATR = _f("BREAKER_MIN_ATR", 0.3)
BREAKER_MAX_ATR = _f("BREAKER_MAX_ATR", 3.0)

# ── Order Block Engine (BigBeluga) — segundo motor de entrada, en paralelo ─
# al Unicorn Model: si Unicorn no confirma, se intenta este. Usa el MISMO
# ST_LEN/ST_MULT de arriba para su propia tendencia interna (así ambos
# motores están de acuerdo en qué es "tendencia" si corrieran sobre las
# mismas velas). Solo entra si además coincide con el Supertrend de BIAS_TF.
ENABLE_OB_ENGINE = _b("ENABLE_OB_ENGINE", True)
OB_PIVOT_LEN = _i("OB_PIVOT_LEN", 7)              # barras a cada lado para confirmar un pivote
OB_MIN_BUY_PCT = _f("OB_MIN_BUY_PCT", 50.0)       # % mínimo de volumen comprador para aceptar retest LONG
OB_MIN_SELL_PCT = _f("OB_MIN_SELL_PCT", 50.0)     # % mínimo de volumen vendedor para aceptar retest SHORT
OB_DELETE_ON_BREAK = _b("OB_DELETE_ON_BREAK", True)  # invalida el Order Block si el precio lo rompe del todo
OB_RR = _f("OB_RR", 1.5)                          # risk:reward del TP en señales del Order Block Engine
OB_SL_ATR_BUFFER = _f("OB_SL_ATR_BUFFER", 0.2)    # colchón ATR extra en el SL

# ── CVD Filter (Cumulative Volume Delta, sin llamada extra a la API) ─────
# Inspirado en tu Confluence Gate de TradingView. Reusa candles_entry
# (ENTRY_TF) como timeframe fino para aproximar volumen intrabar — no pide
# velas 1m aparte. Off por defecto hasta validar en DRY_RUN, mismo criterio
# que Order Flow / Funding-OI.
ENABLE_CVD_FILTER = _b("ENABLE_CVD_FILTER", False)
CVD_LOOKBACK = _i("CVD_LOOKBACK", 20)              # velas finas hacia atrás (mismo default que cvd_len en Pine)

# ── Order Flow / Absorción (confirmación final, post Supertrend+Unicorn) ──
ENABLE_ORDER_FLOW_FILTER = _b("ENABLE_ORDER_FLOW_FILTER", False)  # off por defecto
ORDER_FLOW_TRADES_LIMIT = _i("ORDER_FLOW_TRADES_LIMIT", 1000)     # trades recientes a pedir
ORDER_FLOW_MIN_ABSORPTION_RATIO = _f("ORDER_FLOW_MIN_ABSORPTION_RATIO", 0.55)
ORDER_FLOW_MIN_VOLUME = _f("ORDER_FLOW_MIN_VOLUME", 0)  # volumen mínimo en la vela sweep (unidades base)

# ── Funding Rate + Open Interest ─────────────────────────────────────────
ENABLE_FUNDING_OI_FILTER = _b("ENABLE_FUNDING_OI_FILTER", False)
FUNDING_OI_MODE = os.getenv("FUNDING_OI_MODE", "inform")  # "inform" | "confirm"
FUNDING_EXTREME_THRESHOLD = _f("FUNDING_EXTREME_THRESHOLD", 0.0005)
OI_MIN_CHANGE_PCT = _f("OI_MIN_CHANGE_PCT", 1.0)

# ── Regime Filter (Choppiness Index) ────────────────────────────────────
ENABLE_REGIME_FILTER = _b("ENABLE_REGIME_FILTER", True)
REGIME_CHOP_LENGTH = _i("REGIME_CHOP_LENGTH", 14)
REGIME_CHOP_THRESHOLD = _f("REGIME_CHOP_THRESHOLD", 61.8)

# ── Correlation Manager ───────────────────────────────────────────────────
ENABLE_CORRELATION_FILTER = _b("ENABLE_CORRELATION_FILTER", True)
CORR_THRESHOLD = _f("CORR_THRESHOLD", 0.75)
CORR_LOOKBACK = _i("CORR_LOOKBACK", 30)
MAX_CORRELATED_POSITIONS = _i("MAX_CORRELATED_POSITIONS", 2)

# ── Persistencia (Railway Volume) ───────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "/data")

# ── Setup Memory (aprendizaje adaptativo) ────────────────────────────────
ENABLE_SETUP_MEMORY_FILTER = _b("ENABLE_SETUP_MEMORY_FILTER", True)
SETUP_MEMORY_MIN_SAMPLES = _i("SETUP_MEMORY_MIN_SAMPLES", 15)
SETUP_MEMORY_MIN_WIN_RATE = _f("SETUP_MEMORY_MIN_WIN_RATE", 0.35)
SETUP_MEMORY_FILE = os.path.join(DATA_DIR, "unicorn_st_setup_memory.json")

# ── Gestión de riesgo ───────────────────────────────────────────────────
RISK_PCT_PER_TRADE = _f("RISK_PCT_PER_TRADE", 0.5)   # % del balance por operación
LEVERAGE = _i("LEVERAGE", 10)
DAILY_MAX_LOSS_PCT = _f("DAILY_MAX_LOSS_PCT", 5.0)   # circuit breaker diario
MAX_CONCURRENT_RISK_PCT = _f("MAX_CONCURRENT_RISK_PCT", 3.0)  # riesgo total abierto

# ── Persistencia adicional (mismo Railway Volume) ───────────────────────
JOURNAL_FILE = os.path.join(DATA_DIR, "unicorn_st_journal.json")
STATE_FILE = os.path.join(DATA_DIR, "unicorn_st_state.json")

# ── Logging ─────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
