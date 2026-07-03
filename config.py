"""
volob-standalone — Volume Order Block Bot
Bot nuevo, aislado — pensado para cuenta/API key propia, no comparte
infraestructura ni cuenta de BingX con el resto del fleet.

Estrategia: order blocks ponderados por volumen — implementación propia
(ver strategy_vol_ob.py), no traducción de ningún script de terceros.
Sin backtesting, sin track record — empieza con tamaño mínimo.
"""
import os


def _bool(k, d="false"):
    return os.getenv(k, d).strip().split("#")[0].strip().lower() in ("1", "true", "yes")

def _float(k, d):
    try:
        return float(os.getenv(k, str(d)).strip().split("#")[0].strip())
    except Exception:
        return d

def _int(k, d):
    try:
        return int(os.getenv(k, str(d)).strip().split("#")[0].strip())
    except Exception:
        return d

def _str(k, d=""):
    return os.getenv(k, d).strip().split("#")[0].strip() or d

def _list(k, d=""):
    v = _str(k, d)
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


# ── Identity ─────────────────────────────────────────────────
BOT_NAME   = _str("BOT_NAME", "volob-standalone")
API_KEY    = _str("BINGX_API_KEY")
SECRET_KEY = _str("BINGX_SECRET_KEY")
BASE_URL   = "https://open-api.bingx.com"
TELEGRAM_TOKEN = _str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = _str("TELEGRAM_CHAT_ID")

# ── Universo de trading ──────────────────────────────────────
TOP_N_SYMBOLS   = _int("TOP_N_SYMBOLS", 150)
MIN_VOLUME_USDT = _float("MIN_VOLUME_USDT", 500_000)
BLACKLIST = set(_list("BLACKLIST",
    "ESPORTS,STABLEUSDT,EURUSD,SILVER,SILVERXAG,OILWTI,OILBRENT,PAXG,CUSDT,SYN,GOLD,GOLDXAU,XAU,GASOLINE"))
DIRECTION = _str("DIRECTION", "BOTH")   # LONG | SHORT | BOTH

# ── Timeframe / loop ─────────────────────────────────────────
TIMEFRAME           = _str("TIMEFRAME", "5m")
SCAN_INTERVAL        = _int("SCAN_INTERVAL", 60)
TRAILING_CHECK_SEC   = _int("TRAILING_CHECK_SEC", 30)

# ── Capital y riesgo ─────────────────────────────────────────
# FIX aplicado desde el principio (lección de renewed-love): gate de
# margen antes de abrir, no solo cap de notional — ver main.py.
CAPITAL             = _float("CAPITAL", 100.0)
LEVERAGE             = _int("LEVERAGE", 5)
RISK_PCT             = _float("RISK_PCT", 1.0)
FIXED_NOTIONAL_USDT  = _float("FIXED_NOTIONAL_USDT", 15.0)
MIN_NOTIONAL_USDT    = _float("MIN_NOTIONAL_USDT", 10.0)
MAX_NOTIONAL_USDT    = _float("MAX_NOTIONAL_USDT", 40.0)
MAX_OPEN_TRADES      = _int("MAX_OPEN_TRADES", 3)
MAX_DAILY_TRADES     = _int("MAX_DAILY_TRADES", 15)
DAILY_LOSS_PCT       = _float("DAILY_LOSS_PCT", 4.0)
MIN_MARGIN_USDT      = _float("MIN_MARGIN_USDT", 1.0)

# ── TP / SL / Trail ──────────────────────────────────────────
SL_ATR_MULT         = _float("SL_ATR_MULT", 1.5)
TRAIL_DISTANCE_ATR  = _float("TRAIL_DISTANCE_ATR", 1.5)
MAX_HOLD_MINUTES    = _int("MAX_HOLD_MINUTES", 180)

# ── Volume Order Block — parámetros de la estrategia ─────────
# Ver strategy_vol_ob.py para la lógica. Sin backtesting: puntos de
# partida razonados, no valores optimizados.
VOB_PIVOT_LEN     = _int("VOB_PIVOT_LEN", 7)
VOB_ATR_LEN       = _int("VOB_ATR_LEN", 14)
VOB_ATR_MULT      = _float("VOB_ATR_MULT", 3.5)
VOB_MIN_VOL_RATIO = _float("VOB_MIN_VOL_RATIO", 0.55)
VOB_RR            = _float("VOB_RR", 2.0)

# ── Infra ────────────────────────────────────────────────────
PORT       = _int("PORT", 8080)
# FIX aplicado desde el principio (lección de renewed-love): default
# ya apunta a /data — confirma igualmente que el Volume está montado
# de verdad en Railway antes de asumir que esto persiste.
STATE_FILE = _str("STATE_FILE", "/data/bot_state.json")
