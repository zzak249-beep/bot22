#!/usr/bin/env python3
"""
analizar.py — Estrategia RSI + Bollinger Bands + ATR
Retorna señales LONG / SHORT / NONE con score 0-100
"""

import numpy as np
import config
import exchange


def calcular_rsi(closes: np.ndarray, periodo: int = None) -> float:
    if periodo is None:
        periodo = config.RSI_PERIODO
    if len(closes) < periodo + 1:
        return 50.0
    deltas = np.diff(closes)
    ganancias = np.where(deltas > 0, deltas, 0)
    perdidas = np.where(deltas < 0, -deltas, 0)
    avg_g = np.mean(ganancias[-periodo:])
    avg_p = np.mean(perdidas[-periodo:])
    if avg_p == 0:
        return 100.0
    rs = avg_g / avg_p
    return 100 - (100 / (1 + rs))


def calcular_bb(closes: np.ndarray, periodo: int = None, std: float = None):
    if periodo is None:
        periodo = config.BB_PERIODO
    if std is None:
        std = config.BB_STD
    if len(closes) < periodo:
        return None, None, None
    media = np.mean(closes[-periodo:])
    desv = np.std(closes[-periodo:])
    return media - std * desv, media, media + std * desv


def calcular_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, periodo: int = None) -> float:
    if periodo is None:
        periodo = config.ATR_PERIODO
    if len(closes) < periodo + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    return float(np.mean(trs[-periodo:]))


def analizar_par(symbol: str) -> dict:
    """
    Analiza un par y retorna señal con score.
    
    Returns:
        {
            "señal": "LONG" | "SHORT" | "NONE",
            "score": 0-100,
            "precio": float,
            "sl": float,
            "tp": float,
            "atr": float,
            "rsi": float,
            "volumen_ok": bool,
            "razon": str
        }
    """
    resultado_base = {
        "señal": "NONE",
        "score": 0,
        "precio": 0.0,
        "sl": 0.0,
        "tp": 0.0,
        "atr": 0.0,
        "rsi": 50.0,
        "volumen_ok": False,
        "razon": "sin datos"
    }

    # Obtener velas
    klines = exchange.get_klines(symbol, interval="15m", limit=120)
    if not klines or len(klines) < 50:
        resultado_base["razon"] = "pocas velas"
        return resultado_base

    try:
        closes = np.array([float(k[4]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])
    except Exception as e:
        resultado_base["razon"] = f"error datos: {e}"
        return resultado_base

    precio = closes[-1]
    if precio <= 0:
        resultado_base["razon"] = "precio inválido"
        return resultado_base

    # Indicadores
    rsi = calcular_rsi(closes)
    bb_low, bb_mid, bb_high = calcular_bb(closes)
    atr = calcular_atr(highs, lows, closes)

    if bb_low is None or atr == 0:
        resultado_base["razon"] = "indicadores inválidos"
        return resultado_base

    # Volumen
    vol_usd = float(volumes[-1]) * precio
    volumen_ok = vol_usd >= config.VOLUMEN_MIN_USD

    # Spread check
    info = exchange.get_info_par(symbol)
    spread_ok = True
    if info.get("bid") and info.get("ask") and info["bid"] > 0:
        spread_pct = (info["ask"] - info["bid"]) / info["bid"] * 100
        spread_ok = spread_pct <= config.SPREAD_MAX_PCT

    # ── SCORING ──────────────────────────────────────────────
    score_long = 0
    score_short = 0

    # RSI
    if rsi <= config.RSI_OVERSOLD:  # Oversold → LONG
        score_long += 35 if rsi <= 30 else 25
    elif rsi >= config.RSI_OVERBOUGHT:  # Overbought → SHORT
        score_short += 35 if rsi >= 70 else 25

    # Bollinger Bands
    if precio <= bb_low:  # Toca banda inferior → LONG
        score_long += 30 if precio < bb_low else 20
    elif precio >= bb_high:  # Toca banda superior → SHORT
        score_short += 30 if precio > bb_high else 20

    # Tendencia (EMA rápida vs lenta)
    ema_fast = float(np.mean(closes[-10:]))
    ema_slow = float(np.mean(closes[-30:]))
    if ema_fast > ema_slow:
        score_long += 15
    else:
        score_short += 15

    # Momentum (últimas 3 velas)
    if len(closes) >= 4:
        momentum = closes[-1] - closes[-4]
        if momentum > 0:
            score_long += 10
        else:
            score_short += 10

    # Volumen bonus
    if volumen_ok:
        score_long += 5
        score_short += 5
    else:
        score_long = int(score_long * 0.7)
        score_short = int(score_short * 0.7)

    # Spread penalización
    if not spread_ok:
        score_long = int(score_long * 0.5)
        score_short = int(score_short * 0.5)

    # Seleccionar señal
    if score_long >= config.SCORE_MIN and score_long > score_short:
        señal = "LONG"
        score = min(score_long, 100)
        sl = precio - atr * config.SL_ATR_MULT
        tp = precio + atr * config.TP_ATR_MULT
        rr = (tp - precio) / (precio - sl) if (precio - sl) > 0 else 0
        if rr < config.RR_MINIMO:
            return {**resultado_base, "razon": f"RR bajo {rr:.2f}", "precio": precio}
        razon = f"RSI:{rsi:.0f} BB:bajo Trend:↑"

    elif score_short >= config.SCORE_MIN and score_short > score_long:
        señal = "SHORT"
        score = min(score_short, 100)
        sl = precio + atr * config.SL_ATR_MULT
        tp = precio - atr * config.TP_ATR_MULT
        rr = (precio - tp) / (sl - precio) if (sl - precio) > 0 else 0
        if rr < config.RR_MINIMO:
            return {**resultado_base, "razon": f"RR bajo {rr:.2f}", "precio": precio}
        razon = f"RSI:{rsi:.0f} BB:alto Trend:↓"

    else:
        return {
            **resultado_base,
            "precio": precio,
            "rsi": rsi,
            "atr": atr,
            "volumen_ok": volumen_ok,
            "razon": f"score insuf L:{score_long} S:{score_short}"
        }

    return {
        "señal": señal,
        "score": score,
        "precio": precio,
        "sl": sl,
        "tp": tp,
        "atr": atr,
        "rsi": rsi,
        "volumen_ok": volumen_ok,
        "razon": razon
    }
