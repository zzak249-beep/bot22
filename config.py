"""
config.py — Configuración centralizada Edge Bot v5
Todos los parámetros cargados desde variables de entorno Railway.
"""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # ── API ────────────────────────────────────────────────────
    BINGX_API_KEY:    str = os.getenv("BINGX_API_KEY", "")
    BINGX_API_SECRET: str = os.getenv("BINGX_API_SECRET", "")
    BINGX_BASE_URL:   str = "https://open-api.bingx.com"

    TELEGRAM_TOKEN:   str = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── Modo ───────────────────────────────────────────────────
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    # ── Símbolos ───────────────────────────────────────────────
    SYMBOLS: list = field(default_factory=lambda:
        [s.strip() for s in os.getenv("SYMBOLS", "").split(",") if s.strip()])
    LIQUIDITY_MODE: str = os.getenv("LIQUIDITY_MODE", "high_only")
    # high_only = solo whitelist 30 pares
    # all = todos los pares (más señales, más ruido)

    # ── Timeframes ────────────────────────────────────────────
    TF_ENTRY: str = "3m"
    TF_TREND: str = "15m"
    TF_MACRO: str = "1h"

    # ── EMAs ──────────────────────────────────────────────────
    EMA_FAST:  int = 8
    EMA_MID:   int = 21
    EMA_SLOW:  int = 55
    EMA_MACRO: int = 200

    # ── ATR — Stop Loss y Take Profit dinámicos ───────────────
    ATR_PERIOD:   int   = 14
    ATR_SL_MULT:  float = float(os.getenv("ATR_SL_MULT",  "1.5"))
    ATR_TP1_MULT: float = float(os.getenv("ATR_TP1_MULT", "2.25"))  # RR 1.5
    ATR_TP2_MULT: float = float(os.getenv("ATR_TP2_MULT", "3.5"))   # RR 2.3
    ATR_TP3_MULT: float = float(os.getenv("ATR_TP3_MULT", "6.0"))   # RR 4.0

    # ── RSI ───────────────────────────────────────────────────
    RSI_PERIOD: int   = 14
    RSI_OB:     float = 68.0
    RSI_OS:     float = 32.0

    # ── ADX ───────────────────────────────────────────────────
    ADX_MIN: float = float(os.getenv("ADX_MIN", "25"))

    # ── Score mínimo ──────────────────────────────────────────
    SCORE_MIN: int = int(os.getenv("SCORE_MIN", "60"))

    # ── Funding rate límites ──────────────────────────────────
    FUNDING_LONG_MAX:  float = 0.001   # no LONG si funding > 0.1%
    FUNDING_SHORT_MIN: float = -0.0005  # no SHORT si funding < -0.05%

    # ── Gestión de riesgo ─────────────────────────────────────
    LEVERAGE:        int   = int(os.getenv("LEVERAGE",        "5"))
    MAX_OPEN_TRADES: int   = int(os.getenv("MAX_OPEN_TRADES", "3"))
    MAX_DD_DAY_PCT:  float = float(os.getenv("MAX_DD_DAY_PCT",  "5.0"))
    MAX_DD_WEEK_PCT: float = float(os.getenv("MAX_DD_WEEK_PCT", "12.0"))
    MAX_SIGNALS_DAY: int   = int(os.getenv("MAX_SIGNALS_DAY", "15"))
    COOLDOWN_BARS:   int   = int(os.getenv("COOLDOWN_BARS",   "4"))

    # ── Liquidez mínima por vela ──────────────────────────────
    MIN_VOL_USDT: float = float(os.getenv("MIN_VOL_USDT", "1000000"))
    # 1M USDT/vela = mínimo real para que el slippage sea bajo

    # ── Trailing stop ─────────────────────────────────────────
    TRAIL_PCT: float = float(os.getenv("TRAIL_PCT", "0.35"))
    # 0.35% de distancia cuando el trailing está activo

    # ── Scanner ───────────────────────────────────────────────
    SCAN_INTERVAL: int = int(os.getenv("SCAN_INTERVAL", "180"))  # 3 minutos

    # ── Heartbeat ─────────────────────────────────────────────
    HEARTBEAT_EVERY: int = int(os.getenv("HEARTBEAT_EVERY", "20"))
    # cada N scans (20 × 3min = 1 hora)

    def validate(self):
        missing = []
        for k in ("BINGX_API_KEY","BINGX_API_SECRET",
                  "TELEGRAM_TOKEN","TELEGRAM_CHAT_ID"):
            if not getattr(self, k):
                missing.append(k)
        if missing:
            raise EnvironmentError(
                f"Variables de entorno faltantes: {', '.join(missing)}")


cfg = Config()
