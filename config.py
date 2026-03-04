"""
config.py — Configuracion del bot v6 ELITE
Mejoras basadas en investigacion 2025-2026 + Intellectia.ai:
  - ADX minimo 25 (backtests confirman 55-65% accuracy)
  - Stochastic RSI para filtrado preciso de sobrecompra/venta
  - 3 niveles de TP (30% / 40% / trailing 30%)
  - Stale trade timeout: cierra trades sin movimiento en X horas
  - Trailing dinamico segun volatilidad actual
  - Confirmacion de vela (cuerpo solido, sin doji)
  - Filtro de sentimiento de noticias via CryptoPanic (gratis)
  - Filtro de Fear & Greed Index
"""
import os
from dotenv import load_dotenv
load_dotenv()

# ── BingX ─────────────────────────────────────────────────
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET  = os.getenv("BINGX_SECRET", "")

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TG_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID", "")
TG_TOKEN   = TELEGRAM_TOKEN
TG_CHAT_ID = TELEGRAM_CHAT_ID

# ── Simbolos ──────────────────────────────────────────────
SYMBOLS_OVERRIDE = os.getenv("SYMBOLS", "")
MIN_VOLUME_USDT  = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
QUOTE_CURRENCY   = os.getenv("QUOTE_CURRENCY", "USDT")
EXCLUDE_SYMBOLS  = [s.strip() for s in os.getenv("EXCLUDE_SYMBOLS", "").split(",") if s.strip()]
MAX_SYMBOLS      = int(os.getenv("MAX_SYMBOLS", "80"))
SYMBOLS: list    = []

# ── Estrategia BB+RSI ─────────────────────────────────────
TIMEFRAME    = os.getenv("TIMEFRAME", "1h")
TIMEFRAME_HI = os.getenv("TIMEFRAME_HI", "4h")
BB_PERIOD    = int(os.getenv("BB_PERIOD", "30"))
BB_SIGMA     = float(os.getenv("BB_SIGMA", "2.0"))
RSI_PERIOD   = int(os.getenv("RSI_PERIOD", "14"))
RSI_OB       = float(os.getenv("RSI_OB", "45"))   # FIX: subido de 40 a 45 — genera más señales LONG
RSI_OS       = float(os.getenv("RSI_OS", "55"))   # FIX: bajado de 60 a 55 — genera más señales SHORT
SL_ATR       = float(os.getenv("SL_ATR", "2.5"))
TP_ATR       = float(os.getenv("TP_ATR", "0"))

# ── Filtro EMA de tendencia ───────────────────────────────
EMA_TREND_ENABLED = os.getenv("EMA_TREND_ENABLED", "true").lower() == "true"
EMA_TREND_PERIOD  = int(os.getenv("EMA_TREND_PERIOD", "200"))

# ── ADX: fuerza de tendencia ──────────────────────────────
# ADX > 25 = tendencia definida. Investigacion 2025: 55-65% accuracy
ADX_FILTER_ENABLED = os.getenv("ADX_FILTER_ENABLED", "true").lower() == "true"
ADX_MIN            = float(os.getenv("ADX_MIN", "18"))   # 18 permite mas señales, sube a 25 si hay ruido
ADX_PERIOD         = int(os.getenv("ADX_PERIOD", "14"))

# ── Stochastic RSI ────────────────────────────────────────
# Mas preciso que RSI solo para detectar sobrecompra/venta extrema
# Solo LONG si StochRSI_K < 20 | Solo SHORT si StochRSI_K > 80
STOCH_RSI_ENABLED = os.getenv("STOCH_RSI_ENABLED", "true").lower() == "true"
STOCH_RSI_PERIOD  = int(os.getenv("STOCH_RSI_PERIOD", "14"))
STOCH_RSI_K       = int(os.getenv("STOCH_RSI_K", "3"))
STOCH_RSI_D       = int(os.getenv("STOCH_RSI_D", "3"))
STOCH_RSI_OB      = float(os.getenv("STOCH_RSI_OB", "80"))
STOCH_RSI_OS      = float(os.getenv("STOCH_RSI_OS", "30"))  # < 30 sobrevendido (antes 20, muy estricto)

# ── Confirmacion de vela ──────────────────────────────────
# Cuerpo de la vela de señal > 60% del rango (evita doji)
CANDLE_CONFIRM_ENABLED  = os.getenv("CANDLE_CONFIRM_ENABLED", "true").lower() == "true"
CANDLE_CONFIRM_MIN_BODY = float(os.getenv("CANDLE_CONFIRM_MIN_BODY", "0.45"))  # antes 0.6, muy estricto

# ── Confirmacion de volumen ───────────────────────────────
VOLUME_CONFIRM_ENABLED = os.getenv("VOLUME_CONFIRM_ENABLED", "true").lower() == "true"
VOLUME_CONFIRM_MULT    = float(os.getenv("VOLUME_CONFIRM_MULT", "0.9"))  # antes 1.2 bloqueaba demasiado

# ── 3 niveles de TP ───────────────────────────────────────
# TP1 = 1.2x ATR → cerrar 30%  |  TP2 = 2.0x ATR → cerrar 40%
# TP3 = trailing sobre el 30% restante
MULTI_TP_ENABLED = os.getenv("MULTI_TP_ENABLED", "true").lower() == "true"
TP1_ATR_MULT     = float(os.getenv("TP1_ATR_MULT", "1.2"))
TP1_CLOSE_PCT    = float(os.getenv("TP1_CLOSE_PCT", "0.30"))
TP2_ATR_MULT     = float(os.getenv("TP2_ATR_MULT", "2.0"))
TP2_CLOSE_PCT    = float(os.getenv("TP2_CLOSE_PCT", "0.40"))

# ── Stale Trade Timeout ───────────────────────────────────
# Cerrar trades sin movimiento en X horas — libera capital
STALE_TRADE_ENABLED  = os.getenv("STALE_TRADE_ENABLED", "true").lower() == "true"
STALE_TRADE_HOURS    = int(os.getenv("STALE_TRADE_HOURS", "12"))
STALE_TRADE_MIN_MOVE = float(os.getenv("STALE_TRADE_MIN_MOVE", "0.005"))  # 0.5%

# ── Trailing Stop Dinamico ────────────────────────────────
# Se aprieta con baja vol, se amplia con alta vol (ATR/precio)
TRAILING_STOP_ENABLED    = os.getenv("TRAILING_STOP_ENABLED", "true").lower() == "true"
TRAILING_STOP_ATR        = float(os.getenv("TRAILING_STOP_ATR", "1.5"))
TRAILING_ACTIVATE_PCT    = float(os.getenv("TRAILING_ACTIVATE_PCT", "1.0"))
TRAILING_DYNAMIC_ENABLED = os.getenv("TRAILING_DYNAMIC_ENABLED", "true").lower() == "true"
TRAILING_ATR_LOW_VOL     = float(os.getenv("TRAILING_ATR_LOW_VOL",  "1.0"))
TRAILING_ATR_HIGH_VOL    = float(os.getenv("TRAILING_ATR_HIGH_VOL", "2.5"))
TRAILING_VOL_THRESHOLD   = float(os.getenv("TRAILING_VOL_THRESHOLD", "0.015"))  # ATR/precio > 1.5% = alta vol

# ── NUEVO: Sentimiento de noticias (CryptoPanic API - gratis) ─
# Bloquea entradas en pares con noticias muy negativas recientes
# Requiere CRYPTOPANIC_API_KEY en .env (gratis en cryptopanic.com)
# Sentimiento — OFF por defecto hasta configurar CRYPTOPANIC_API_KEY en .env
SENTIMENT_ENABLED     = os.getenv("SENTIMENT_ENABLED", "false").lower() == "true"
CRYPTOPANIC_API_KEY   = os.getenv("CRYPTOPANIC_API_KEY", "")
SENTIMENT_BLOCK_PANIC = os.getenv("SENTIMENT_BLOCK_PANIC", "true").lower() == "true"
SENTIMENT_CACHE_MIN   = int(os.getenv("SENTIMENT_CACHE_MIN", "30"))

# Fear & Greed — ON por defecto (API gratuita, no requiere clave)
FEAR_GREED_ENABLED    = os.getenv("FEAR_GREED_ENABLED", "true").lower() == "true"
FEAR_GREED_MIN        = int(os.getenv("FEAR_GREED_MIN", "15"))   # relajado de 20 a 15
FEAR_GREED_MAX        = int(os.getenv("FEAR_GREED_MAX", "85"))   # relajado de 80 a 85
FEAR_GREED_CACHE_MIN  = int(os.getenv("FEAR_GREED_CACHE_MIN", "60"))

# ── Filtro horario ────────────────────────────────────────
# Filtro horario — por defecto OFF para no bloquear señales (activa si el bot opera de noche)
TIME_FILTER_ENABLED   = os.getenv("TIME_FILTER_ENABLED", "false").lower() == "true"
TIME_FILTER_OFF_START = int(os.getenv("TIME_FILTER_OFF_START", "2"))
TIME_FILTER_OFF_END   = int(os.getenv("TIME_FILTER_OFF_END",   "5"))

# ── R:R minimo ────────────────────────────────────────────
MIN_RR_RATIO = float(os.getenv("MIN_RR_RATIO", "1.2"))  # antes 1.5, muy estricto para BB signals

# ── Gestion de capital ────────────────────────────────────
RISK_PCT      = float(os.getenv("RISK_PCT", "0.015"))
LEVERAGE      = int(os.getenv("LEVERAGE", "3"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
COOLDOWN_BARS = int(os.getenv("COOLDOWN_BARS", "4"))

# ── Operacion ─────────────────────────────────────────────
LOOP_SECONDS     = int(os.getenv("LOOP_SECONDS", "300"))
MIN_USDT_BALANCE = float(os.getenv("MIN_USDT_BALANCE", "5"))
SCAN_BATCH_SIZE  = int(os.getenv("SCAN_BATCH_SIZE", "20"))

# ── SHORT ─────────────────────────────────────────────────
SHORT_ENABLED = os.getenv("SHORT_ENABLED", "false").lower() == "true"

# ── TP Parcial legacy (reemplazado por MULTI_TP) ──────────
PARTIAL_TP_ENABLED = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_PCT     = float(os.getenv("PARTIAL_TP_PCT", "0.5"))
PARTIAL_TP_ATR     = float(os.getenv("PARTIAL_TP_ATR", "1.2"))

# ── Filtro volumen anomalo ────────────────────────────────
VOLUME_SPIKE_ENABLED = os.getenv("VOLUME_SPIKE_ENABLED", "true").lower() == "true"
VOLUME_SPIKE_MULT    = float(os.getenv("VOLUME_SPIKE_MULT", "4.0"))

# ── Circuit Breaker ───────────────────────────────────────
CB_MAX_DAILY_LOSS_PCT   = float(os.getenv("CB_MAX_DAILY_LOSS_PCT",   "0.08"))
CB_MAX_CONSECUTIVE_LOSS = int(os.getenv("CB_MAX_CONSECUTIVE_LOSS",   "4"))
BALANCE_SNAPSHOT        = 0.0

# ── Mercado lateral ───────────────────────────────────────
SIDEWAYS_BB_WIDTH  = float(os.getenv("SIDEWAYS_BB_WIDTH",  "0.04"))
SIDEWAYS_ATR_RATIO = float(os.getenv("SIDEWAYS_ATR_RATIO", "0.65"))

# ── Filtros de calidad de señal ───────────────────────────
MIN_ATR_USDT         = float(os.getenv("MIN_ATR_USDT",         "0"))
REQUIRE_MACD_CONFIRM = os.getenv("REQUIRE_MACD_CONFIRM", "true").lower() == "true"
REQUIRE_MOMENTUM     = os.getenv("REQUIRE_MOMENTUM",     "true").lower() == "true"
