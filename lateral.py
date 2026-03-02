"""
╔══════════════════════════════════════════════════════════════╗
║         LATERAL MARKET MODULE — SATY ELITE v15              ║
║  Detecta mercado lateral y genera señales de range trading  ║
║  Integrado automáticamente en el bot principal              ║
╚══════════════════════════════════════════════════════════════╝

USO EN EL BOT PRINCIPAL:
    from lateral import LateralDetector, lateral_scan
    
    # En el loop de scan:
    lat = lateral_scan(ex, sym, df)
    if lat:
        send_signal(lat)  # LONG soporte / SHORT resistencia

ESTRATEGIA:
    1. Detectar ADX < 20 (sin tendencia)  
    2. BB Width estrecho (baja volatilidad)
    3. Precio define soporte/resistencia claros
    4. LONG en soporte + RSI < 40
    5. SHORT en resistencia + RSI > 60
    6. TP = zona media del rango / opuesto
    7. SL = fuera del rango (rotura)
"""

import os, logging
from typing import Optional, Tuple
from dataclasses import dataclass

import pandas as pd
import numpy as np

log = logging.getLogger("lateral")

# ── Parámetros ajustables ─────────────────────────────────
LATERAL_ADX_MAX   = float(os.environ.get("LATERAL_ADX_MAX",   "20"))   # ADX máximo para considerar lateral
LATERAL_BBW_MAX   = float(os.environ.get("LATERAL_BBW_MAX",   "0.04")) # BB Width máximo
LATERAL_RNG_MAX   = float(os.environ.get("LATERAL_RNG_MAX",   "8.0"))  # Rango % máximo
LATERAL_RNG_MIN   = float(os.environ.get("LATERAL_RNG_MIN",   "1.5"))  # Rango % mínimo (evita flat)
LATERAL_LOOKBACK  = int(os.environ.get("LATERAL_LOOKBACK",    "50"))   # Velas para definir rango
LATERAL_PROXIMITY = float(os.environ.get("LATERAL_PROXIMITY", "0.015"))# 1.5% del borde → señal
LATERAL_RSI_LONG  = float(os.environ.get("LATERAL_RSI_LONG",  "40"))   # RSI máximo para LONG
LATERAL_RSI_SHORT = float(os.environ.get("LATERAL_RSI_SHORT", "60"))   # RSI mínimo para SHORT
LATERAL_MIN_SCORE = int(os.environ.get("LATERAL_MIN_SCORE",   "5"))    # Score mínimo lateral
LATERAL_ENABLED   = os.environ.get("LATERAL_ENABLED", "true").lower() == "true"

@dataclass
class LateralZone:
    """Describe el rango lateral detectado."""
    symbol: str
    is_lateral: bool
    rng_high: float
    rng_low: float
    rng_mid: float
    rng_pct: float
    adx: float
    bbw: float
    touches_high: int    # cuántas veces tocó resistencia
    touches_low: int     # cuántas veces tocó soporte
    quality: str         # "A", "B", "C"

@dataclass
class LateralSignal:
    """Señal generada por el detector lateral."""
    symbol: str
    base: str
    direction: str       # "long" o "short"
    score: int
    modules: str         # "LATERAL"
    signals: str
    price: float
    atr: float
    rsi: float
    adx: float
    rr: float
    tp1: float
    tp2: float
    tp3: float
    sl: float
    zone: LateralZone

class LateralDetector:
    """Detector y generador de señales para mercado lateral."""

    def __init__(self):
        self._detected_zones: dict = {}  # cache de zonas por símbolo

    # ── Indicadores básicos (si no están disponibles del bot) ──
    @staticmethod
    def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
    @staticmethod
    def _sma(s, n): return s.rolling(n).mean()

    @staticmethod
    def _atr(df, n=14):
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(span=n, adjust=False).mean()

    @staticmethod
    def _rsi(s, n=14):
        d = s.diff()
        g = d.clip(lower=0).ewm(span=n, adjust=False).mean()
        lo = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
        return 100 - (100 / (1 + g / lo.replace(0, np.nan)))

    @staticmethod
    def _adx(df, n=14):
        h, l = df["high"], df["low"]
        up, dn = h.diff(), -l.diff()
        pdm = up.where((up > dn) & (up > 0), 0.0)
        mdm = dn.where((dn > up) & (dn > 0), 0.0)
        a = LateralDetector._atr(df, n)
        dip = 100 * pdm.ewm(span=n, adjust=False).mean() / a
        dim = 100 * mdm.ewm(span=n, adjust=False).mean() / a
        dx = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
        return dip, dim, dx.ewm(span=n, adjust=False).mean()

    @staticmethod
    def _bb(s, n=20, m=2.0):
        mid = LateralDetector._sma(s, n)
        std = s.rolling(n).std()
        return mid, mid + m * std, mid - m * std

    def detect_zone(self, df: pd.DataFrame, symbol: str = "") -> LateralZone:
        """
        Analiza el DataFrame y devuelve la zona lateral si existe.
        """
        i = len(df) - 2
        if i < LATERAL_LOOKBACK + 20:
            return LateralZone(symbol=symbol, is_lateral=False,
                               rng_high=0, rng_low=0, rng_mid=0,
                               rng_pct=0, adx=0, bbw=0,
                               touches_high=0, touches_low=0, quality="C")

        window = df.iloc[max(0, i - LATERAL_LOOKBACK):i + 1]

        # ADX
        _, _, adx_s = self._adx(df.iloc[max(0, i - LATERAL_LOOKBACK - 20):i + 1])
        adx_val = float(adx_s.iloc[-2]) if len(adx_s) > 2 else 30.0

        # BB Width
        mid, upper, lower = self._bb(df["close"])
        bbw = float(((upper - lower) / mid.replace(0, np.nan)).iloc[i])

        # Rango de precio en la ventana
        rng_hi = float(window["high"].max())
        rng_lo = float(window["low"].min())
        rng_mid = (rng_hi + rng_lo) / 2
        rng_pct = (rng_hi - rng_lo) / rng_lo * 100 if rng_lo > 0 else 999

        # Criterio lateral
        is_lateral = (
            adx_val < LATERAL_ADX_MAX and
            bbw < LATERAL_BBW_MAX and
            LATERAL_RNG_MIN <= rng_pct <= LATERAL_RNG_MAX
        )

        # Contar toques al soporte/resistencia (calidad del rango)
        tol = (rng_hi - rng_lo) * 0.08
        touches_hi = int(((window["high"] >= rng_hi - tol)).sum())
        touches_lo = int(((window["low"] <= rng_lo + tol)).sum())

        # Calidad del rango
        if touches_hi >= 3 and touches_lo >= 3:
            quality = "A"
        elif touches_hi >= 2 and touches_lo >= 2:
            quality = "B"
        else:
            quality = "C"

        return LateralZone(
            symbol=symbol, is_lateral=is_lateral,
            rng_high=round(rng_hi, 8), rng_low=round(rng_lo, 8),
            rng_mid=round(rng_mid, 8), rng_pct=round(rng_pct, 2),
            adx=round(adx_val, 1), bbw=round(bbw, 4),
            touches_high=touches_hi, touches_low=touches_lo,
            quality=quality
        )

    def generate_signal(self, df: pd.DataFrame, zone: LateralZone,
                        symbol: str = "") -> Optional[LateralSignal]:
        """
        Genera señal LONG/SHORT basada en la zona lateral.
        Solo calidad A o B, con confirmación de RSI y vela.
        """
        if not zone.is_lateral or zone.quality == "C":
            return None

        i = len(df) - 2
        price = float(df["close"].iloc[i])
        high_i = float(df["high"].iloc[i])
        low_i  = float(df["low"].iloc[i])
        open_i = float(df["open"].iloc[i])
        atr_v  = float(self._atr(df).iloc[i])
        rs     = self._rsi(df["close"])
        rv     = float(rs.iloc[i])

        prox = LATERAL_PROXIMITY
        rng_hi = zone.rng_high
        rng_lo = zone.rng_low
        rng_mid = zone.rng_mid

        # Score base según calidad
        base_score = 7 if zone.quality == "A" else 5

        # ── LONG en soporte ────────────────────────────────
        near_support = price <= rng_lo * (1 + prox)
        bullish_candle = price > open_i  # vela alcista

        if near_support and rv < LATERAL_RSI_LONG and bullish_candle:
            # SL: debajo del soporte
            sl = rng_lo * (1 - prox * 1.5)
            # TP en niveles del rango
            tp1 = rng_mid
            tp2 = rng_hi * 0.97
            tp3 = rng_hi * 0.995
            sl_dist = abs(price - sl)
            tp_dist = abs(tp3 - price)
            rr = tp_dist / max(sl_dist, 1e-9)

            score = base_score + int(rv < 30) + int(zone.touches_low >= 3)
            sigs = (f"range_support q:{zone.quality} "
                    f"RSI:{rv:.0f} ADX:{zone.adx:.0f} "
                    f"range:{zone.rng_pct:.1f}% "
                    f"touches:{zone.touches_low}")

            return LateralSignal(
                symbol=symbol, base=symbol.split("/")[0],
                direction="long", score=score,
                modules="LATERAL", signals=sigs,
                price=price, atr=atr_v, rsi=rv, adx=zone.adx,
                rr=round(rr, 2),
                tp1=round(tp1, 8), tp2=round(tp2, 8), tp3=round(tp3, 8),
                sl=round(sl, 8), zone=zone
            )

        # ── SHORT en resistencia ───────────────────────────
        near_resistance = price >= rng_hi * (1 - prox)
        bearish_candle = price < open_i  # vela bajista

        if near_resistance and rv > LATERAL_RSI_SHORT and bearish_candle:
            sl = rng_hi * (1 + prox * 1.5)
            tp1 = rng_mid
            tp2 = rng_lo * 1.03
            tp3 = rng_lo * 1.005
            sl_dist = abs(sl - price)
            tp_dist = abs(price - tp3)
            rr = tp_dist / max(sl_dist, 1e-9)

            score = base_score + int(rv > 70) + int(zone.touches_high >= 3)
            sigs = (f"range_resistance q:{zone.quality} "
                    f"RSI:{rv:.0f} ADX:{zone.adx:.0f} "
                    f"range:{zone.rng_pct:.1f}% "
                    f"touches:{zone.touches_high}")

            return LateralSignal(
                symbol=symbol, base=symbol.split("/")[0],
                direction="short", score=score,
                modules="LATERAL", signals=sigs,
                price=price, atr=atr_v, rsi=rv, adx=zone.adx,
                rr=round(rr, 2),
                tp1=round(tp1, 8), tp2=round(tp2, 8), tp3=round(tp3, 8),
                sl=round(sl, 8), zone=zone
            )

        return None

    def scan(self, df: pd.DataFrame, symbol: str = "") -> Optional[LateralSignal]:
        """Entry point principal: detecta zona y genera señal."""
        if not LATERAL_ENABLED:
            return None
        zone = self.detect_zone(df, symbol)
        if not zone.is_lateral:
            return None
        return self.generate_signal(df, zone, symbol)

    def telegram_zone_msg(self, zone: LateralZone) -> str:
        """Mensaje Telegram describiendo la zona lateral."""
        q_emoji = {"A": "🔥", "B": "⚡", "C": "⚪"}
        return (f"📊 <b>ZONA LATERAL</b> {q_emoji.get(zone.quality,'⚪')} Cal:{zone.quality}\n"
                f"  📈 Resistencia: <code>{zone.rng_high:.6g}</code> ({zone.touches_high} toques)\n"
                f"  📉 Soporte:     <code>{zone.rng_low:.6g}</code> ({zone.touches_low} toques)\n"
                f"  ↔️ Rango:       {zone.rng_pct:.2f}%\n"
                f"  📊 ADX:{zone.adx:.1f} BBW:{zone.bbw:.4f}")


# ── Instancia global ──────────────────────────────────────
_detector = LateralDetector()

def lateral_scan(df: pd.DataFrame, symbol: str = "") -> Optional[LateralSignal]:
    """
    Función principal para usar desde el bot:
        sig = lateral_scan(df, "BTC/USDT:USDT")
        if sig: enviar_señal(sig)
    """
    return _detector.scan(df, symbol)

def lateral_signal_to_dict(sig: LateralSignal) -> dict:
    """Convierte LateralSignal a dict compatible con el bot principal."""
    return {
        "sym": sig.symbol,
        "base": sig.base,
        "direction": sig.direction,
        "score": sig.score,
        "modules": sig.modules,
        "signals": sig.signals,
        "price": sig.price,
        "atr": sig.atr,
        "rsi": sig.rsi,
        "adx": sig.adx,
        "rr": sig.rr,
        "tp1": sig.tp1,
        "tp2": sig.tp2,
        "tp3": sig.tp3,
        "sl": sig.sl,
        "market_mode": "lateral"
    }

def detect_zone(df: pd.DataFrame, symbol: str = "") -> LateralZone:
    """Función directa para detectar zona sin generar señal."""
    return _detector.detect_zone(df, symbol)

def is_lateral_market(df: pd.DataFrame) -> bool:
    """Función simple: ¿está el mercado lateral?"""
    zone = _detector.detect_zone(df)
    return zone.is_lateral
