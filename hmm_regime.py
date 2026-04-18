"""
hmm_regime.py — Detector de Régimen con Hidden Markov Model (HMM)
═══════════════════════════════════════════════════════════════════

INTEGRACIÓN CON app.py (v6.1):
  from hmm_regime import get_regime, active_regimes

  En analyze_symbol():
    hmm_regime = get_regime(symbol, candles_hmm, notify_fn=send_telegram)
    if hmm_regime == "RANGING":   → return None (skip)
    if hmm_regime == "VOLATILE":  → effective_usd = ORDER_SIZE_USD * 0.5
    if hmm_regime == "TRENDING":  → parámetros normales

ESTADOS HMM:
  RANGING  → mercado lateral, no operar
  TRENDING → tendencia activa, parámetros normales
  VOLATILE → alta volatilidad, reducir tamaño ×0.5, SL ×1.2

ARQUITECTURA:
  - Cada símbolo tiene su propio modelo (SymbolHMM)
  - Reentrenamiento en cada scan (Baum-Welch, 60s)
  - Viterbi para predicción del estado actual
  - Fallback automático a reglas ADX+BB si hmmlearn no está

INSTALACIÓN:
  pip install hmmlearn scikit-learn
"""

import os
import math
import logging
import numpy as np

logger = logging.getLogger("hmm_regime")

# ══════════════════════════════════════════════════════════════════════════════
# IMPORTAR hmmlearn — fallback automático si no está instalado
# ══════════════════════════════════════════════════════════════════════════════
try:
    from hmmlearn.hmm import GaussianHMM
    HMM_AVAILABLE = True
    logger.info("✅ hmmlearn disponible — HMM activo")
except ImportError:
    HMM_AVAILABLE = False
    logger.warning(
        "⚠️  hmmlearn no instalado — usando fallback ADX+BB. "
        "Instala con: pip install hmmlearn scikit-learn"
    )

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
N_STATES       = 3
MIN_TRAIN_BARS = int(os.environ.get("HMM_MIN_BARS",   80))
N_ITER         = int(os.environ.get("HMM_N_ITER",    100))
ATR_LEN        = int(os.environ.get("ATR_LEN",        14))
ADX_LEN        = int(os.environ.get("ADX_LEN",        14))
ADX_TREND      = float(os.environ.get("ADX_TREND",   22.0))
BB_LEN         = int(os.environ.get("BB_LEN",         20))
BB_VOL_PCTILE  = float(os.environ.get("BB_VOL_PCTILE", 0.35))


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# Las 4 features que observa el HMM por cada vela
# ══════════════════════════════════════════════════════════════════════════════

def _build_features(candles: list) -> np.ndarray:
    """
    Construye la matriz de observaciones X de shape [N-1, 4]:

    Feature 0 — log-return:        log(close_i / close_{i-1})
    Feature 1 — ATR normalizado:   True Range / close
    Feature 2 — volumen relativo:  vol_i / media_vol_20_barras
    Feature 3 — BB width:          2×std(closes_20) / close
    """
    n     = len(candles)
    feats = []

    for i in range(1, n):
        c      = candles[i]
        c_prev = candles[i - 1]
        close  = c["close"]
        if close <= 0:
            continue

        # Feature 0: log-return
        lr = math.log(close / c_prev["close"]) if c_prev["close"] > 0 else 0.0

        # Feature 1: ATR normalizado
        tr       = max(c["high"] - c["low"],
                       abs(c["high"] - c_prev["close"]),
                       abs(c["low"]  - c_prev["close"]))
        atr_norm = tr / close

        # Feature 2: volumen normalizado
        start    = max(0, i - 20)
        vol_avg  = float(np.mean([c2["volume"] for c2 in candles[start:i]])) or 1.0
        vol_norm = c["volume"] / vol_avg

        # Feature 3: BB width normalizado
        start2   = max(0, i - BB_LEN)
        closes_w = [c2["close"] for c2 in candles[start2:i + 1]]
        bb_std   = float(np.std(closes_w)) if len(closes_w) > 1 else 0.0
        bb_norm  = (bb_std * 2) / close

        feats.append([lr, atr_norm, vol_norm, bb_norm])

    return np.array(feats, dtype=float)


# ══════════════════════════════════════════════════════════════════════════════
# ASIGNACIÓN DINÁMICA DE ETIQUETAS
# El HMM aprende 3 estados (0,1,2) sin nombre. Los etiquetamos por sus medias.
# ══════════════════════════════════════════════════════════════════════════════

def _assign_labels(model) -> dict:
    """
    Lee model.means_ [N_STATES, 4] y asigna:
      - Mayor ATR (col 1) → VOLATILE
      - De los restantes, mayor BB width (col 3) → TRENDING
      - El que queda → RANGING
    """
    means        = model.means_
    volatile_idx = int(np.argmax(means[:, 1]))
    remaining    = [i for i in range(N_STATES) if i != volatile_idx]
    trending_idx = remaining[int(np.argmax(means[remaining, 3]))]
    ranging_idx  = [i for i in range(N_STATES)
                    if i not in (volatile_idx, trending_idx)][0]

    label_map = {
        ranging_idx:  "RANGING",
        trending_idx: "TRENDING",
        volatile_idx: "VOLATILE",
    }
    logger.debug(f"HMM labels → {label_map} | ATR medias: {means[:, 1].round(6)}")
    return label_map


# ══════════════════════════════════════════════════════════════════════════════
# CLASE SymbolHMM — modelo independiente por símbolo
# ══════════════════════════════════════════════════════════════════════════════

class SymbolHMM:
    """
    Un modelo HMM independiente por símbolo (ej: BTC/USDT:USDT).

    Ciclo de vida por scan:
      1. get_regime() llama a predict(candles)
      2. predict() → _train() con Baum-Welch → predice con Viterbi
      3. Devuelve "RANGING", "TRENDING" o "VOLATILE"
      4. Si el régimen cambia → notifica Telegram
    """

    def __init__(self, symbol: str):
        self.symbol      = symbol
        self._model      = None
        self._label_map  = {}
        self.last_regime = None

    def _train(self, candles: list):
        if not HMM_AVAILABLE:
            return

        X = _build_features(candles)
        if len(X) < MIN_TRAIN_BARS:
            return

        try:
            model = GaussianHMM(
                n_components=N_STATES,
                covariance_type="diag",
                n_iter=N_ITER,
                random_state=42,
                verbose=False,
            )
            model.fit(X)
            self._model     = model
            self._label_map = _assign_labels(model)
            logger.debug(f"{self.symbol} HMM entrenado con {len(X)} observaciones")
        except Exception as e:
            logger.warning(f"{self.symbol} HMM train error: {e}")
            self._model = None

    def predict(self, candles: list) -> str:
        if len(candles) < MIN_TRAIN_BARS:
            return _rule_based_regime(candles)

        self._train(candles)

        if self._model is not None and HMM_AVAILABLE:
            try:
                X      = _build_features(candles[-MIN_TRAIN_BARS:])
                states = self._model.predict(X)
                idx    = int(states[-1])
                regime = self._label_map.get(idx, "RANGING")
                logger.debug(f"{self.symbol} → estado {idx} → {regime}")
                return regime
            except Exception as e:
                logger.warning(f"{self.symbol} HMM predict error: {e}")

        return _rule_based_regime(candles)


# ══════════════════════════════════════════════════════════════════════════════
# REGISTRO GLOBAL — un SymbolHMM por símbolo
# ══════════════════════════════════════════════════════════════════════════════

_registry: dict = {}


def get_regime(symbol: str, candles: list, notify_fn=None) -> str:
    """
    Función principal. Llamar desde app.py en cada scan.

    Args:
        symbol:    ej. "BTC/USDT:USDT"
        candles:   lista de dicts [{open, high, low, close, volume}, ...]
        notify_fn: send_telegram — para avisar cambios por Telegram

    Returns:
        "TRENDING" | "RANGING" | "VOLATILE"
    """
    if symbol not in _registry:
        _registry[symbol] = SymbolHMM(symbol)

    hmm    = _registry[symbol]
    prev   = hmm.last_regime
    regime = hmm.predict(candles)

    # Detectar cambio y notificar
    if prev is not None and prev != regime and notify_fn is not None:
        emoji = {"TRENDING": "📈", "RANGING": "😴", "VOLATILE": "⚡"}.get(regime, "🔄")
        notify_fn(
            f"{emoji} <b>CAMBIO DE RÉGIMEN HMM</b>\n"
            f"Par: <b>{symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Antes:  {prev}\n"
            f"Ahora:  <b>{regime}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"{_action_text(regime)}"
        )

    hmm.last_regime = regime
    return regime


def _action_text(regime: str) -> str:
    if regime == "RANGING":  return "🚫 Entradas pausadas para este par"
    if regime == "VOLATILE": return "⚠️ Tamaño ×0.5 | SL ×1.2"
    return "✅ Parámetros normales"


def active_regimes() -> dict:
    """Devuelve {symbol: regime} de todos los pares monitoreados."""
    return {s: h.last_regime for s, h in _registry.items() if h.last_regime}


# ══════════════════════════════════════════════════════════════════════════════
# FALLBACK — reglas ADX+BB si hmmlearn no está disponible
# ══════════════════════════════════════════════════════════════════════════════

def _rule_based_regime(candles: list) -> str:
    if len(candles) < ADX_LEN * 3:
        return "RANGING"
    closes  = np.array([c["close"] for c in candles])
    adx_val = _adx_simple(candles, ADX_LEN)
    bb_pct  = _bb_percentile(closes)
    atr_now = _atr_simple(candles, ATR_LEN)
    series  = _atr_series(candles, ATR_LEN)
    atr_avg = float(np.nanmean(series[-50:])) or 1.0
    if atr_now / atr_avg > 2.0:                          return "VOLATILE"
    if adx_val < ADX_TREND and bb_pct < BB_VOL_PCTILE:  return "RANGING"
    return "TRENDING"


def _atr_simple(candles, period=14) -> float:
    if len(candles) < period + 1: return 0.0
    trs = [max(candles[i]["high"] - candles[i]["low"],
               abs(candles[i]["high"] - candles[i-1]["close"]),
               abs(candles[i]["low"]  - candles[i-1]["close"]))
           for i in range(1, len(candles))]
    return float(np.mean(trs[-period:]))


def _atr_series(candles, period=14) -> np.ndarray:
    n = len(candles); out = np.full(n, np.nan)
    for i in range(period, n):
        trs = [max(candles[j]["high"] - candles[j]["low"],
                   abs(candles[j]["high"] - candles[j-1]["close"]),
                   abs(candles[j]["low"]  - candles[j-1]["close"]))
               for j in range(i - period + 1, i + 1)]
        out[i] = float(np.mean(trs))
    return out


def _adx_simple(candles, period=14) -> float:
    if len(candles) < period * 2: return 0.0
    H = [c["high"] for c in candles]
    L = [c["low"]  for c in candles]
    C = [c["close"] for c in candles]
    pdm, mdm, trl = [], [], []
    for i in range(1, len(candles)):
        hd = H[i] - H[i-1]; ld = L[i-1] - L[i]
        pdm.append(hd if hd > ld and hd > 0 else 0)
        mdm.append(ld if ld > hd and ld > 0 else 0)
        trl.append(max(H[i]-L[i], abs(H[i]-C[i-1]), abs(L[i]-C[i-1])))

    def wilder(a, p):
        r = [sum(a[:p])]
        for x in a[p:]: r.append(r[-1] - r[-1]/p + x)
        return r

    aw = wilder(trl, period); pw = wilder(pdm, period); mw = wilder(mdm, period)
    dx = [
        100 * abs(100*p/a - 100*m/a) / (100*p/a + 100*m/a)
        for a, p, m in zip(aw, pw, mw)
        if a > 0 and (p + m) > 0
    ]
    return float(np.mean(dx[-period:])) if len(dx) >= period else 0.0


def _bb_percentile(closes, period=20, lookback=100) -> float:
    if len(closes) < period + lookback: return 0.5
    widths = [
        float(np.std(closes[i-period:i]) * 2)
        for i in range(len(closes) - lookback, len(closes))
        if i >= period
    ]
    if not widths: return 0.5
    return sum(1 for w in widths if w <= widths[-1]) / len(widths)
