"""
analizar.py — Puente entre main.py y el motor de estrategias v7
════════════════════════════════════════════════════════════════
main.py llama: analizar.analizar_todos(pares)
Este módulo:
  1. Obtiene los klines de BingX para cada par
  2. Llama a strategy.get_signal() que ejecuta las 4 estrategias
     + filtro de liquidez institucional
  3. Traduce el resultado al formato que espera main.py
     (campos: par, señal, precio, sl, tp, rr, rsi, atr, score, motivo...)

También expone analizar_par() para compatibilidad con backtest.py
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone

import config
import exchange
import strategy

log = logging.getLogger("analizar")


# ══════════════════════════════════════════════════════════
# HELPERS: klines → DataFrame
# ══════════════════════════════════════════════════════════

def _klines_to_df(klines: list) -> pd.DataFrame | None:
    """Convierte la respuesta de BingX a DataFrame OHLCV."""
    rows = []
    for k in klines:
        try:
            if isinstance(k, dict):
                ts = int(k.get("time", k.get("t", 0)))
                o  = float(k.get("open",   k.get("o", 0)))
                h  = float(k.get("high",   k.get("h", 0)))
                l  = float(k.get("low",    k.get("l", 0)))
                c  = float(k.get("close",  k.get("c", 0)))
                v  = float(k.get("volume", k.get("v", 0)))
            elif isinstance(k, (list, tuple)) and len(k) >= 6:
                ts, o, h, l, c, v = int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
            else:
                continue
            rows.append({"time": ts, "open": o, "high": h, "low": l, "close": c, "volume": v})
        except Exception:
            continue

    if len(rows) < 25:
        return None

    df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    df = df[df["close"] > 0]
    return df if len(df) >= 25 else None


def _get_df(par: str, intervalo: str, limit: int) -> pd.DataFrame | None:
    klines = exchange.get_klines(par, intervalo=intervalo, limit=limit)
    if not klines:
        return None
    return _klines_to_df(klines)


# ══════════════════════════════════════════════════════════
# FILTROS PREVIOS (rápidos, sin estrategia)
# ══════════════════════════════════════════════════════════

def _filtros_basicos(par: str) -> tuple[bool, str]:
    """Rechaza pares con bajo volumen, spread alto u hora excluida."""
    # Filtro horario
    if config.HORA_FILTRO_ACTIVO:
        h = datetime.now(timezone.utc).hour
        if h in config.HORAS_EXCLUIDAS:
            return False, f"hora excluida ({h}h UTC)"

    # Volumen
    vol = exchange.get_volumen_24h(par)
    if vol < config.VOLUMEN_MIN_USD:
        return False, f"vol ${vol:,.0f} < min ${config.VOLUMEN_MIN_USD:,.0f}"

    # Spread
    spread = exchange.get_spread_pct(par)
    if spread > config.SPREAD_MAX_PCT:
        return False, f"spread {spread:.2f}% > {config.SPREAD_MAX_PCT}%"

    return True, ""


# ══════════════════════════════════════════════════════════
# ANALIZAR UN PAR — devuelve formato main.py
# ══════════════════════════════════════════════════════════

def analizar_par(par: str) -> dict:
    """
    Analiza un par y retorna dict compatible con main.py.
    Campos obligatorios que main.py necesita:
      par, señal, precio, sl, tp, rr, rsi, atr, bb, score, motivo,
      divergencia, vol_relativo, mtf_rsi
    """
    resultado = {
        "par":          par,
        "señal":        False,
        "precio":       0.0,
        "sl":           0.0,
        "tp":           0.0,
        "rr":           0.0,
        "rsi":          50.0,
        "atr":          0.0,
        "bb":           {"posicion": 0.5},
        "score":        0,
        "motivo":       "",
        "divergencia":  False,
        "vol_relativo": 1.0,
        "mtf_rsi":      50.0,
        # extra: info institucional
        "liquidity_bias":  "neutral",
        "liquidity_score": 50,
    }

    # ── Filtros rápidos ────────────────────────────────────
    ok, razon = _filtros_basicos(par)
    if not ok:
        resultado["motivo"] = razon
        return resultado

    # ── Obtener klines principales (15m) ──────────────────
    df = _get_df(par, config.TIMEFRAME, 250)
    if df is None or len(df) < 25:
        resultado["motivo"] = f"klines 15m insuficientes"
        return resultado

    # Pasar el símbolo al DataFrame para que liquidity.py sepa qué par es
    df.attrs["symbol"] = par

    # ── Obtener klines de tendencia (4h) ──────────────────
    df_4h = _get_df(par, config.TIMEFRAME_HI, 100)

    # ── Ejecutar motor de estrategias ─────────────────────
    sig = strategy.get_signal(df, df_4h)

    # Si no hay señal, retornar sin señal
    if sig.get("action") not in ("buy", "sell_short"):
        resultado["motivo"] = sig.get("reason", "sin señal")
        # Incluir datos técnicos aunque no haya señal (útil para debug)
        resultado["rsi"]   = sig.get("rsi", 50.0)
        resultado["atr"]   = sig.get("atr", 0.0)
        resultado["precio"] = float(df["close"].iloc[-1])
        return resultado

    # ── Hay señal — verificar score mínimo ────────────────
    score = sig.get("score", 0)
    if score < config.STRATEGY_MIN_SCORE:
        resultado["motivo"] = f"score {score} < mínimo {config.STRATEGY_MIN_SCORE}"
        return resultado

    # ── Verificar R:R mínimo ──────────────────────────────
    rr = sig.get("rr", 0)
    if rr < config.RR_MINIMO:
        resultado["motivo"] = f"R:R {rr:.2f} < mínimo {config.RR_MINIMO}"
        return resultado

    # ── Volumen relativo ──────────────────────────────────
    vols = df["volume"].tolist()
    if len(vols) >= 21:
        media_vol = float(np.mean(vols[-21:-1]))
        vol_rel   = float(vols[-1]) / media_vol if media_vol > 0 else 1.0
    else:
        vol_rel = 1.0

    # ── BB posición actual ─────────────────────────────────
    closes = df["close"].tolist()
    from analizar import calcular_bb
    bb_data = calcular_bb(closes, config.BB_PERIODO, config.BB_STD)

    # ── MTF RSI (15m ya calculado internamente, tomamos el valor) ─
    mtf_rsi = sig.get("rsi", 50.0)  # strategy ya usa multi-tf internamente

    # ── Construir resultado final ──────────────────────────
    precio = sig.get("entry", float(df["close"].iloc[-1]))

    resultado.update({
        "señal":           True,
        "precio":          precio,
        "sl":              sig["sl"],
        "tp":              sig["tp"],
        "rr":              rr,
        "rsi":             sig.get("rsi", 50.0),
        "atr":             sig.get("atr", 0.0),
        "bb":              {"posicion": bb_data.get("posicion", 0.5), **bb_data},
        "score":           score,
        "motivo":          sig.get("reason", ""),
        "divergencia":     sig.get("divergencia", False),
        "vol_relativo":    vol_rel,
        "mtf_rsi":         mtf_rsi,
        "strategy":        sig.get("strategy", ""),
        "trend_4h":        sig.get("trend_4h", "flat"),
        "liquidity_bias":  sig.get("liquidity_bias", "neutral"),
        "liquidity_score": sig.get("liquidity_score", 50),
        "liquidity_summary": sig.get("liquidity_summary", ""),
    })

    if config.MODO_DEBUG:
        liq_str = f" 🏦{resultado['liquidity_bias'].upper()}({resultado['liquidity_score']})" \
                  if resultado['liquidity_score'] != 50 else ""
        print(f"  ✓ SEÑAL {par}: {sig.get('strategy','')} score={score} "
              f"R:R={rr:.2f} trend={resultado['trend_4h']}{liq_str}")

    return resultado


# ══════════════════════════════════════════════════════════
# HELPERS BB / RSI para compatibilidad con backtest.py
# ══════════════════════════════════════════════════════════

def calcular_rsi(closes: list, periodo: int = 14) -> float:
    if len(closes) < periodo + 1:
        return 50.0
    arr    = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    g = np.where(deltas > 0, deltas, 0.0)
    p = np.where(deltas < 0, -deltas, 0.0)
    ag = float(np.mean(g[:periodo]))
    ap = float(np.mean(p[:periodo]))
    for i in range(periodo, len(deltas)):
        ag = (ag * (periodo - 1) + g[i]) / periodo
        ap = (ap * (periodo - 1) + p[i]) / periodo
    if ap == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / ap))


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
    return {"media": media, "superior": superior, "inferior": inferior,
            "posicion": posicion, "ancho": ancho}


def calcular_atr(highs: list, lows: list, closes: list, periodo: int = 14) -> float:
    if len(closes) < 2:
        return float(closes[-1]) * 0.02 if closes else 0.01
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    if not trs:
        return float(closes[-1]) * 0.02
    if len(trs) < periodo:
        return float(np.mean(trs))
    atr = float(np.mean(trs[:periodo]))
    for i in range(periodo, len(trs)):
        atr = (atr * (periodo - 1) + trs[i]) / periodo
    return atr


def calcular_ema(closes: list, periodo: int = 50) -> float:
    if len(closes) < periodo:
        return float(np.mean(closes)) if closes else 0.0
    arr = np.array(closes, dtype=float)
    k   = 2.0 / (periodo + 1)
    ema = float(np.mean(arr[:periodo]))
    for precio in arr[periodo:]:
        ema = precio * k + ema * (1 - k)
    return ema


# ══════════════════════════════════════════════════════════
# ANALIZAR LISTA DE PARES
# ══════════════════════════════════════════════════════════

def analizar_todos(pares: list) -> list:
    """
    Analiza todos los pares en lotes.
    Retorna lista de señales ordenadas por score DESC.
    """
    import time as _time

    señales  = []
    total    = len(pares)
    batch    = getattr(config, "SCAN_BATCH_SIZE", 10)

    print(f"[ANALIZAR] Escaneando {total} pares con 4 estrategias + filtro institucional...")

    for i, par in enumerate(pares):
        try:
            r = analizar_par(par)
            if r["señal"]:
                señales.append(r)
                if config.MODO_DEBUG:
                    liq = f" 🏦{r['liquidity_bias'].upper()}" if r.get("liquidity_bias") != "neutral" else ""
                    print(f"  ✓ {par}: score={r['score']} strategy={r.get('strategy','')}{liq}")
            elif config.MODO_DEBUG:
                print(f"  ✗ {par}: {r['motivo'][:60]}")

            # Pausa pequeña entre pares para no saturar la API
            if (i + 1) % batch == 0:
                _time.sleep(0.5)
                pct = int((i + 1) / total * 100)
                print(f"[ANALIZAR] {i+1}/{total} ({pct}%) — {len(señales)} señales hasta ahora")

        except Exception as e:
            log.error(f"[ANALIZAR] {par}: {e}")
            if config.MODO_DEBUG:
                import traceback
                traceback.print_exc()

    señales.sort(key=lambda x: x["score"], reverse=True)

    print(f"[ANALIZAR] Completado: {total} pares → {len(señales)} señales")
    return señales
