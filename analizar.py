"""
analizar.py — Señales RSI + Bollinger Bands + ATR  v6
MEJORAS:
  - EMA50 en 1h como filtro de tendencia
  - Confirmación multi-timeframe (RSI en 15m)
  - Detección de divergencia alcista RSI
  - Filtro de volumen relativo
  - Filtro horario (evita 00-03 UTC)
  - Scoring mejorado (0-100) con todos los factores
"""

import numpy as np
from datetime import datetime, timezone
import config
import exchange


# ============================================================
# INDICADORES
# ============================================================

def calcular_rsi(closes: list, periodo: int = 14) -> float:
    if len(closes) < periodo + 1:
        return 50.0

    arr      = np.array(closes, dtype=float)
    deltas   = np.diff(arr)
    ganancias = np.where(deltas > 0, deltas, 0.0)
    perdidas  = np.where(deltas < 0, -deltas, 0.0)

    avg_g = float(np.mean(ganancias[:periodo]))
    avg_p = float(np.mean(perdidas[:periodo]))

    for i in range(periodo, len(deltas)):
        avg_g = (avg_g * (periodo - 1) + ganancias[i]) / periodo
        avg_p = (avg_p * (periodo - 1) + perdidas[i]) / periodo

    if avg_p == 0:
        return 100.0

    rs = avg_g / avg_p
    return 100.0 - (100.0 / (1.0 + rs))


def calcular_rsi_serie(closes: list, periodo: int = 14) -> list:
    """Devuelve la serie completa de valores RSI (para detectar divergencias)."""
    if len(closes) < periodo + 2:
        return [50.0] * len(closes)

    arr      = np.array(closes, dtype=float)
    deltas   = np.diff(arr)
    ganancias = np.where(deltas > 0, deltas, 0.0)
    perdidas  = np.where(deltas < 0, -deltas, 0.0)

    rsi_values = [50.0] * (periodo + 1)  # Primeros valores no calculables

    avg_g = float(np.mean(ganancias[:periodo]))
    avg_p = float(np.mean(perdidas[:periodo]))

    def _rsi(ag, ap):
        if ap == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + ag / ap))

    rsi_values.append(_rsi(avg_g, avg_p))

    for i in range(periodo, len(deltas)):
        avg_g = (avg_g * (periodo - 1) + ganancias[i]) / periodo
        avg_p = (avg_p * (periodo - 1) + perdidas[i]) / periodo
        rsi_values.append(_rsi(avg_g, avg_p))

    return rsi_values


def calcular_ema(closes: list, periodo: int = 50) -> float:
    """EMA Exponential Moving Average."""
    if len(closes) < periodo:
        return float(np.mean(closes)) if closes else 0.0

    arr = np.array(closes, dtype=float)
    k   = 2.0 / (periodo + 1)
    ema = float(np.mean(arr[:periodo]))  # SMA inicial
    for precio in arr[periodo:]:
        ema = precio * k + ema * (1 - k)
    return ema


def calcular_bb(closes: list, periodo: int = 20, std_mult: float = 2.0) -> dict:
    vacio = {"media": 0, "superior": 0, "inferior": 0, "posicion": 0.5, "ancho": 0}
    if len(closes) < periodo:
        return vacio

    serie    = np.array(closes[-periodo:], dtype=float)
    media    = float(np.mean(serie))
    std      = float(np.std(serie))
    superior = media + std_mult * std
    inferior = media - std_mult * std
    ancho    = superior - inferior
    precio   = float(closes[-1])
    posicion = float((precio - inferior) / ancho) if ancho > 0 else 0.5

    return {
        "media":    media,
        "superior": superior,
        "inferior": inferior,
        "posicion": posicion,   # 0 = banda inferior, 1 = banda superior
        "ancho":    ancho
    }


def calcular_atr(highs: list, lows: list, closes: list, periodo: int = 14) -> float:
    if len(closes) < 2:
        return float(closes[-1]) * 0.02 if closes else 0.01

    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i]  - closes[i - 1]),
            abs(lows[i]   - closes[i - 1])
        )
        trs.append(tr)

    if not trs:
        return float(closes[-1]) * 0.02

    if len(trs) < periodo:
        return float(np.mean(trs))

    atr = float(np.mean(trs[:periodo]))
    for i in range(periodo, len(trs)):
        atr = (atr * (periodo - 1) + trs[i]) / periodo

    return atr


# ============================================================
# DIVERGENCIA ALCISTA RSI
# ============================================================

def tiene_divergencia_alcista(closes: list, ventana: int = 10) -> bool:
    """
    Divergencia alcista: precio hace mínimo más bajo pero RSI hace mínimo más alto.
    Señal de reversión alcista muy fiable.
    """
    if len(closes) < ventana * 2 + 5:
        return False

    rsi_serie = calcular_rsi_serie(closes, config.RSI_PERIODO)
    if len(rsi_serie) < ventana * 2:
        return False

    # Mitad reciente vs mitad anterior
    mitad      = ventana
    closes_rec = closes[-mitad:]
    closes_ant = closes[-mitad * 2:-mitad]
    rsi_rec    = rsi_serie[-mitad:]
    rsi_ant    = rsi_serie[-mitad * 2:-mitad]

    min_precio_rec = min(closes_rec)
    min_precio_ant = min(closes_ant)
    min_rsi_rec    = min(rsi_rec)
    min_rsi_ant    = min(rsi_ant)

    # Precio hace mínimo más bajo Y RSI hace mínimo más alto
    return min_precio_rec < min_precio_ant and min_rsi_rec > min_rsi_ant + 1.0


# ============================================================
# VOLUMEN RELATIVO
# ============================================================

def calcular_vol_relativo(vols: list, periodo: int = 20) -> float:
    """Retorna el ratio: volumen_actual / media_volumen_reciente."""
    if len(vols) < periodo + 1:
        return 1.0
    media = float(np.mean(vols[-periodo - 1:-1]))
    if media == 0:
        return 1.0
    return float(vols[-1]) / media


# ============================================================
# FILTRO HORARIO
# ============================================================

def hora_permitida() -> bool:
    """Retorna False si estamos en una hora de baja liquidez (UTC)."""
    if not config.HORA_FILTRO_ACTIVO:
        return True
    hora_utc = datetime.now(timezone.utc).hour
    return hora_utc not in config.HORAS_EXCLUIDAS


# ============================================================
# ANÁLISIS PRINCIPAL DE UN PAR
# ============================================================

def analizar_par(par: str) -> dict:
    """
    Analiza un par con todos los filtros v6.

    Retorna dict con:
        señal: bool
        rsi, bb, atr: indicadores
        precio, sl, tp, rr: niveles
        score: 0-100
        motivo: descripción
        divergencia: bool
        vol_relativo: float
        ema_ok: bool
        mtf_rsi: float
    """
    resultado = {
        "par":          par,
        "señal":        False,
        "rsi":          50.0,
        "bb":           {},
        "atr":          0.0,
        "precio":       0.0,
        "sl":           0.0,
        "tp":           0.0,
        "rr":           0.0,
        "score":        0,
        "motivo":       "",
        "divergencia":  False,
        "vol_relativo": 1.0,
        "ema_ok":       False,
        "mtf_rsi":      50.0,
    }

    # ── Filtro horario ────────────────────────────────────────
    if not hora_permitida():
        hora_utc = datetime.now(timezone.utc).hour
        resultado["motivo"] = f"hora excluida ({hora_utc}h UTC)"
        return resultado

    # ── Klines 5m (señal principal) ───────────────────────────
    klines_5m = exchange.get_klines(par, intervalo="5m", limit=120)
    if len(klines_5m) < 30:
        resultado["motivo"] = f"klines 5m insuficientes ({len(klines_5m)})"
        return resultado

    data_5m = exchange.parsear_klines(klines_5m)
    if len(data_5m["closes"]) < 30:
        resultado["motivo"] = "closes 5m insuficientes"
        return resultado

    closes = data_5m["closes"]
    highs  = data_5m["highs"]
    lows   = data_5m["lows"]
    vols   = data_5m["vols"]
    precio = closes[-1]

    if precio <= 0:
        resultado["motivo"] = "precio = 0"
        return resultado

    resultado["precio"] = precio

    # ── Indicadores base ──────────────────────────────────────
    rsi = calcular_rsi(closes, config.RSI_PERIODO)
    bb  = calcular_bb(closes, config.BB_PERIODO, config.BB_STD)
    atr = calcular_atr(highs, lows, closes, config.ATR_PERIODO)

    resultado["rsi"] = rsi
    resultado["bb"]  = bb
    resultado["atr"] = atr

    # ── Filtros de calidad de mercado ─────────────────────────
    volumen = exchange.get_volumen_24h(par)
    if volumen < config.VOLUMEN_MIN_USD:
        resultado["motivo"] = f"vol bajo ${volumen:,.0f}"
        return resultado

    spread = exchange.get_spread_pct(par)
    if spread > config.SPREAD_MAX_PCT:
        resultado["motivo"] = f"spread {spread:.2f}%"
        return resultado

    # ── Filtro EMA50 en 1h (tendencia superior) ───────────────
    ema_ok = True
    if config.EMA_FILTRO_ACTIVO:
        klines_1h = exchange.get_klines(par, intervalo="1h", limit=80)
        if len(klines_1h) >= 60:
            data_1h = exchange.parsear_klines(klines_1h)
            ema50_1h = calcular_ema(data_1h["closes"], config.EMA_PERIODO)
            ema_ok   = precio > ema50_1h * 0.995  # Tolerancia 0.5%
            resultado["ema_ok"] = ema_ok
            if not ema_ok:
                resultado["motivo"] = f"precio bajo EMA50-1h ({ema50_1h:.6f})"
                return resultado
        else:
            resultado["ema_ok"] = True  # Sin datos suficientes: no bloquear

    resultado["ema_ok"] = ema_ok

    # ── Multi-timeframe: RSI en 15m ───────────────────────────
    mtf_rsi = 50.0
    if config.MTF_ACTIVO:
        klines_15m = exchange.get_klines(par, intervalo="15m", limit=60)
        if len(klines_15m) >= 20:
            data_15m = exchange.parsear_klines(klines_15m)
            mtf_rsi  = calcular_rsi(data_15m["closes"], config.RSI_PERIODO)
            resultado["mtf_rsi"] = mtf_rsi
            if mtf_rsi > config.MTF_RSI_MAX:
                resultado["motivo"] = f"RSI 15m sobrecomprado ({mtf_rsi:.1f} > {config.MTF_RSI_MAX})"
                return resultado

    resultado["mtf_rsi"] = mtf_rsi

    # ── Condiciones de entrada LONG ───────────────────────────
    condicion_rsi = rsi < config.RSI_OVERSOLD
    condicion_bb  = bb["inferior"] > 0 and precio <= bb["inferior"] * 1.002

    if not condicion_rsi:
        resultado["motivo"] = f"RSI={rsi:.1f} (necesita <{config.RSI_OVERSOLD})"
        return resultado

    if not condicion_bb:
        resultado["motivo"] = f"precio lejos BB inferior (pos={bb['posicion']:.2f})"
        return resultado

    # ── Volumen relativo ──────────────────────────────────────
    vol_rel = calcular_vol_relativo(vols)
    resultado["vol_relativo"] = vol_rel
    if config.VOL_RELATIVO_ACTIVO and vol_rel < config.VOL_RELATIVO_MIN:
        resultado["motivo"] = f"vol relativo bajo ({vol_rel:.2f}x)"
        return resultado

    # ── SL / TP ───────────────────────────────────────────────
    if atr <= 0:
        resultado["motivo"] = "ATR = 0"
        return resultado

    sl = precio - (atr * config.SL_ATR_MULT)
    tp = precio + (atr * config.TP_ATR_MULT)

    riesgo    = precio - sl
    beneficio = tp - precio
    rr = beneficio / riesgo if riesgo > 0 else 0.0

    resultado["sl"] = sl
    resultado["tp"] = tp
    resultado["rr"] = rr

    if rr < config.RR_MINIMO:
        resultado["motivo"] = f"R:R={rr:.2f} < {config.RR_MINIMO}"
        return resultado

    # ── Divergencia alcista ───────────────────────────────────
    divergencia = False
    if config.DIVERGENCIA_ACTIVA:
        divergencia = tiene_divergencia_alcista(closes, config.DIVERGENCIA_VENTANA)
    resultado["divergencia"] = divergencia

    # ── Score 0-100 ───────────────────────────────────────────
    score = 50

    # RSI: cuanto más bajo, mejor (máx +20 pts)
    score += min(20, int(max(0, config.RSI_OVERSOLD - rsi)))

    # R:R: hasta +15 pts
    score += min(15, int(rr * 4))

    # Posición BB: muy cerca del fondo → +10
    if bb["posicion"] < 0.05:
        score += 10
    elif bb["posicion"] < 0.1:
        score += 6

    # Divergencia alcista → +15 pts
    if divergencia:
        score += 15

    # Volumen relativo elevado → hasta +8 pts
    if vol_rel >= 2.0:
        score += 8
    elif vol_rel >= 1.5:
        score += 4

    # Multi-timeframe alineado (RSI 15m también bajo) → +7 pts
    if mtf_rsi < config.RSI_OVERSOLD:
        score += 7
    elif mtf_rsi < 40:
        score += 3

    # Penalización si hora es marginalmente aceptable (04-05 UTC)
    hora_utc = datetime.now(timezone.utc).hour
    if hora_utc in [4, 5]:
        score -= 5

    score = max(0, min(100, score))

    resultado["score"]  = score
    resultado["señal"]  = True
    resultado["motivo"] = (
        f"RSI={rsi:.1f} | BB={bb['posicion']:.2f} | R:R={rr:.2f} | "
        f"VolRel={vol_rel:.1f}x | MTF={mtf_rsi:.0f} | "
        f"{'DIV ✓' if divergencia else 'nodiv'} | score={score}"
    )

    return resultado


# ============================================================
# ANALIZAR LISTA DE PARES
# ============================================================

def analizar_todos(pares: list) -> list:
    """
    Analiza una lista de pares y retorna los que tienen señal,
    ordenados por score descendente.
    """
    señales = []
    for par in pares:
        try:
            r = analizar_par(par)
            if r["señal"]:
                señales.append(r)
                if config.MODO_DEBUG:
                    div = " ★DIV" if r.get("divergencia") else ""
                    print(f"  ✓ SEÑAL {par}: {r['motivo']}{div}")
            elif config.MODO_DEBUG:
                print(f"  ✗ {par}: {r['motivo']}")
        except Exception as e:
            print(f"  [ERROR] {par}: {e}")
            if config.MODO_DEBUG:
                import traceback
                traceback.print_exc()

    señales.sort(key=lambda x: x["score"], reverse=True)
    return señales
