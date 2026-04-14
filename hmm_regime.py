"""
hmm_regime.py — Detector de Régimen con Hidden Markov Model (HMM)
═══════════════════════════════════════════════════════════════════

¿QUÉ ES UN HMM? (para quien empieza desde cero)
────────────────────────────────────────────────
Un HMM asume que el mercado se encuentra siempre en uno de N "estados ocultos"
(no los vemos directamente), y que lo que SÍ vemos (precio, volumen, volatilidad)
son "observaciones" que se generan según una distribución de probabilidad
diferente en cada estado.

Los 5 componentes de un HMM (de las imágenes que enviaste):
  N  → número de estados ocultos        (aquí: 3 → RANGING, TRENDING, VOLATILE)
  M  → número de posibles observaciones (aquí: continuo → usamos Gaussianas)
  A  → matriz de transición A[i][j]     P(estado_j | estado_i) ← aprende sola
  B  → distribución de emisión          P(observación | estado) ← aprende sola
  π  → probabilidad inicial de estado                           ← aprende sola

El modelo aprende A, B y π automáticamente con el algoritmo Baum-Welch
(un tipo de Expectation-Maximization) usando las velas históricas.
Para predecir el estado más probable usa el algoritmo de Viterbi.

ARQUITECTURA EN ESTE BOT
────────────────────────
- Cada symbol tiene su PROPIO modelo HMM (instancia de SymbolHMM)
- Se re-entrena y predice en cada scan del bot (cada 60 segundos)
- Si el régimen cambia respecto al scan anterior → notificación Telegram
- Fallback automático a reglas ADX+BB si hmmlearn no está instalado

INTEGRACIÓN CON BULL TARAMA (aplicado en bot.py)
─────────────────────────────────────────────────
  RANGING  → skip entrada (no abrir posiciones para ese par)
  TRENDING → parámetros normales
  VOLATILE → TRADE_USDT × 0.5  +  SL_ATR × 1.2
"""

import os
import math
import logging
import numpy as np

logger = logging.getLogger("hmm_regime")

# ══════════════════════════════════════════════════════════════════════════════
# INTENTO DE IMPORTAR hmmlearn
# Si no está instalado, el bot funciona igualmente con reglas ADX+BB
# ══════════════════════════════════════════════════════════════════════════════
try:
    from hmmlearn.hmm import GaussianHMM
    HMM_AVAILABLE = True
    logger.info("✅ hmmlearn disponible — HMM activo")
except ImportError:
    HMM_AVAILABLE = False
    logger.warning("⚠️  hmmlearn no instalado — usando fallback ADX+BB. "
                   "Instala con: pip install hmmlearn scikit-learn")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
N_STATES       = 3
MIN_TRAIN_BARS = int(os.environ.get("HMM_MIN_BARS",   80))
N_ITER         = int(os.environ.get("HMM_N_ITER",    100))
ATR_LEN        = int(os.environ.get("ATR_LEN",        14))
ADX_LEN        = int(os.environ.get("ADX_LEN",        14))
ADX_TREND      = float(os.environ.get("ADX_TREND",  22.0))
BB_LEN         = int(os.environ.get("BB_LEN",         20))
BB_VOL_PCTILE  = float(os.environ.get("BB_VOL_PCTILE", 0.35))


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# Las "observaciones" que ve el HMM son estas 4 features por vela
# ══════════════════════════════════════════════════════════════════════════════

def _build_features(candles: list) -> np.ndarray:
    """
    Construye la matriz de observaciones X de shape [N-1, 4]:

    Feature 0 — log-return
        log(close_i / close_{i-1})
        Captura dirección y magnitud del movimiento de precio.

    Feature 1 — ATR normalizado  (volatilidad intra-barra)
        True Range del bar / close
        Valores altos = mercado volátil o con gaps.

    Feature 2 — volumen normalizado
        vol_i / media_vol_20_barras_anteriores
        > 1 = actividad inusual, posible breakout o liquidación.

    Feature 3 — BB width normalizado  (expansión/contracción)
        2 × std(closes_últimas_20) / close
        Ancho alto = tendencia o volatilidad. Estrecho = lateralización.

    Por qué estas 4:
        Cubren las 3 dimensiones que distinguen regímenes:
        dirección (log-return), volatilidad (ATR, BB), actividad (volumen).
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
# El HMM aprende 3 estados numerados (0,1,2) pero no sabe sus nombres.
# Los etiquetamos comparando las medias de cada estado entrenado.
# ══════════════════════════════════════════════════════════════════════════════

def _assign_labels(model) -> dict:
    """
    Lee model.means_ (shape: [N_STATES, 4]) y asigna etiquetas:
      - Mayor ATR normalizado (col 1) → VOLATILE
      - De los restantes, mayor BB width (col 3) → TRENDING
      - El que queda → RANGING

    Por ejemplo tras entrenar con BTC podría quedar:
      {0: "RANGING", 1: "TRENDING", 2: "VOLATILE"}
    o cualquier otra combinación — el orden varía por entrenamiento.
    """
    means = model.means_   # [N_STATES, 4]

    volatile_idx = int(np.argmax(means[:, 1]))   # mayor ATR
    remaining    = [i for i in range(N_STATES) if i != volatile_idx]
    trending_idx = remaining[int(np.argmax(means[remaining, 3]))]   # mayor BB
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
# CLASE SymbolHMM — un modelo independiente por símbolo
# ══════════════════════════════════════════════════════════════════════════════

class SymbolHMM:
    """
    Modelo HMM individual para un símbolo (ej: BTC-USDT).

    Ciclo de vida por cada scan (60 segundos):
      1. bot.py llama a get_regime(symbol, candles)
      2. SymbolHMM.predict(candles) → entrena → predice con Viterbi
      3. Devuelve "RANGING", "TRENDING" o "VOLATILE"
      4. El registro global detecta cambio → notifica Telegram si aplica
    """

    def __init__(self, symbol: str):
        self.symbol       = symbol
        self._model       = None    # GaussianHMM entrenado
        self._label_map   = {}      # {estado_idx: "RANGING"|"TRENDING"|"VOLATILE"}
        self.last_regime  = None    # último régimen predicho (para detectar cambios)

    def _train(self, candles: list):
        """
        Entrena el GaussianHMM con las velas disponibles usando Baum-Welch.

        GaussianHMM de hmmlearn:
          - n_components = número de estados ocultos (3)
          - covariance_type = "diag": cada estado tiene su propia varianza
            por feature, sin correlaciones cruzadas (más rápido, suficiente aquí)
          - n_iter: número de iteraciones del EM (más = más preciso, más lento)
          - random_state: semilla para reproducibilidad

        model.fit(X) hace el aprendizaje completo de A, B y π.
        """
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
            self._model   = model
            self._label_map = _assign_labels(model)
            logger.debug(f"{self.symbol} HMM entrenado con {len(X)} observaciones")
        except Exception as e:
            logger.warning(f"{self.symbol} HMM train error: {e}")
            self._model = None

    def predict(self, candles: list) -> str:
        """
        Entrena con las últimas velas y predice el régimen actual.

        Proceso:
          1. _train() → model.fit(X) → aprende A, B, π con Baum-Welch
          2. model.predict(X) → algoritmo Viterbi → secuencia de estados
          3. states[-1] → estado de la última vela → traducir con label_map

        El re-entrenamiento en cada scan (no solo periódico) garantiza que
        el modelo siempre refleja las condiciones más recientes.
        """
        if len(candles) < MIN_TRAIN_BARS:
            return _rule_based_regime(candles)

        self._train(candles)

        if self._model is not None and HMM_AVAILABLE:
            try:
                X      = _build_features(candles[-MIN_TRAIN_BARS:])
                states = self._model.predict(X)     # ← Viterbi aquí
                idx    = int(states[-1])             # estado de la última vela
                regime = self._label_map.get(idx, "RANGING")
                logger.debug(f"{self.symbol} → estado {idx} → {regime}")
                return regime
            except Exception as e:
                logger.warning(f"{self.symbol} HMM predict error: {e}")

        return _rule_based_regime(candles)


# ══════════════════════════════════════════════════════════════════════════════
# REGISTRO GLOBAL — un SymbolHMM por símbolo
# ══════════════════════════════════════════════════════════════════════════════

_registry: dict = {}   # {symbol: SymbolHMM}


def get_regime(symbol: str, candles: list, notify_fn=None) -> str:
    """
    Función principal. Llamar desde bot.py en cada scan por símbolo.

    Args:
        symbol:    ej. "BTC-USDT"
        candles:   lista de velas 1min [{open,high,low,close,volume}, ...]
        notify_fn: notifier.send — para avisar cambios por Telegram

    Returns:
        "TRENDING" | "RANGING" | "VOLATILE"

    Detecta automáticamente cambios de régimen y notifica por Telegram.
    """
    if symbol not in _registry:
        _registry[symbol] = SymbolHMM(symbol)

    hmm    = _registry[symbol]
    prev   = hmm.last_regime
    regime = hmm.predict(candles)

    # ── Detectar cambio y notificar ───────────────────────────────────────────
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
    if regime == "RANGING":   return "🚫 Entradas pausadas para este par"
    if regime == "VOLATILE":  return "⚠️ Tamaño ×0.5 | SL ×1.2"
    return "✅ Parámetros normales"


def active_regimes() -> dict:
    """Devuelve {symbol: regime} de todos los pares monitoreados."""
    return {s: h.last_regime for s, h in _registry.items() if h.last_regime}


# ══════════════════════════════════════════════════════════════════════════════
# FALLBACK — reglas ADX+BB sin hmmlearn
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
    if atr_now / atr_avg > 2.0:   return "VOLATILE"
    if adx_val < ADX_TREND and bb_pct < BB_VOL_PCTILE:   return "RANGING"
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
    H = [c["high"] for c in candles]; L = [c["low"] for c in candles]
    C = [c["close"] for c in candles]
    pdm, mdm, trl = [], [], []
    for i in range(1, len(candles)):
        hd = H[i]-H[i-1]; ld = L[i-1]-L[i]
        pdm.append(hd if hd > ld and hd > 0 else 0)
        mdm.append(ld if ld > hd and ld > 0 else 0)
        trl.append(max(H[i]-L[i], abs(H[i]-C[i-1]), abs(L[i]-C[i-1])))
    def wilder(a, p):
        r = [sum(a[:p])]
        for x in a[p:]: r.append(r[-1] - r[-1]/p + x)
        return r
    aw=wilder(trl,period); pw=wilder(pdm,period); mw=wilder(mdm,period)
    dx = [100*abs(100*p/a - 100*m/a)/(100*p/a + 100*m/a)
          for a,p,m in zip(aw,pw,mw) if a > 0 and (p+m) > 0]
    return float(np.mean(dx[-period:])) if len(dx) >= period else 0.0


def _bb_percentile(closes, period=20, lookback=100) -> float:
    if len(closes) < period + lookback: return 0.5
    widths = [float(np.std(closes[i-period:i])*2)
              for i in range(len(closes)-lookback, len(closes)) if i >= period]
    if not widths: return 0.5
    return sum(1 for w in widths if w <= widths[-1]) / len(widths)
