#!/usr/bin/env python3
"""
learner.py — Optimización automática de parámetros
Ajusta RSI, SL/TP y SCORE_MIN según resultados reales
"""

import json
import os
import time
import config
import database

LEARNER_FILE = "learner_estado.json"


def _cargar() -> dict:
    if os.path.exists(LEARNER_FILE):
        try:
            with open(LEARNER_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "ultimo_ciclo": 0,
        "iteraciones": 0,
        "mejor_wr": 0.0,
        "parametros_actuales": {
            "score_min": config.SCORE_MIN,
            "rsi_oversold": config.RSI_OVERSOLD,
            "rsi_overbought": config.RSI_OVERBOUGHT,
            "sl_atr_mult": config.SL_ATR_MULT,
            "tp_atr_mult": config.TP_ATR_MULT,
        },
        "historial": []
    }


def _guardar(data: dict):
    try:
        with open(LEARNER_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[LEARNER] Error: {e}")


def debe_ejecutar() -> bool:
    """¿Es momento de optimizar?"""
    estado = _cargar()
    ultimo = estado.get("ultimo_ciclo", 0)
    intervalo = config.LEARNER_CICLO_H * 3600
    return (time.time() - ultimo) >= intervalo


def optimizar():
    """
    Analiza resultados recientes y ajusta parámetros si es necesario.
    Solo modifica parámetros si hay suficientes trades.
    """
    estado = _cargar()
    trades = database.get_trades_recientes(50)

    if len(trades) < config.LEARNER_MIN_TRADES:
        print(f"[LEARNER] Pocos trades ({len(trades)}), esperando...")
        estado["ultimo_ciclo"] = time.time()
        _guardar(estado)
        return

    # Calcular métricas
    wins = sum(1 for t in trades if t["resultado"] == "WIN")
    losses = sum(1 for t in trades if t["resultado"] == "LOSS")
    total = wins + losses

    if total == 0:
        return

    wr = wins / total * 100
    pnl_total = sum(t.get("pnl", 0) for t in trades)
    pf = abs(sum(t["pnl"] for t in trades if t["pnl"] > 0)) / max(
        abs(sum(t["pnl"] for t in trades if t["pnl"] < 0)), 0.01
    )

    print(f"[LEARNER] WR:{wr:.0f}% PF:{pf:.2f} Trades:{total}")

    params = estado["parametros_actuales"].copy()
    cambios = []

    # ── LÓGICA DE AJUSTE ─────────────────────────────────

    # Si WR < mínimo → subir score_min (más selectivo)
    if wr < config.LEARNER_MIN_WR and total >= 10:
        nuevo = min(params["score_min"] + 3, 85)
        if nuevo != params["score_min"]:
            params["score_min"] = nuevo
            cambios.append(f"score_min ↑ {nuevo}")

    # Si WR > 60% y PF > 1.5 → bajar score_min (más trades)
    elif wr > 60 and pf > 1.5 and total >= 15:
        nuevo = max(params["score_min"] - 2, 55)
        if nuevo != params["score_min"]:
            params["score_min"] = nuevo
            cambios.append(f"score_min ↓ {nuevo}")

    # Si PF < mínimo → ajustar SL/TP
    if pf < config.LEARNER_MIN_PF and total >= 10:
        # Ajustar TP más amplio
        nuevo_tp = min(params["tp_atr_mult"] + 0.2, 6.0)
        params["tp_atr_mult"] = round(nuevo_tp, 1)
        cambios.append(f"tp_mult ↑ {nuevo_tp:.1f}")

    # Si muchas pérdidas grandes → ajustar SL más ajustado
    avg_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0)) / max(losses, 1)
    avg_win = sum(t["pnl"] for t in trades if t["pnl"] > 0) / max(wins, 1)

    if avg_loss > avg_win * 1.5 and losses >= 5:
        nuevo_sl = max(params["sl_atr_mult"] - 0.1, 0.8)
        params["sl_atr_mult"] = round(nuevo_sl, 1)
        cambios.append(f"sl_mult ↓ {nuevo_sl:.1f}")

    # Aplicar cambios
    if cambios:
        print(f"[LEARNER] Ajustes: {', '.join(cambios)}")
        _aplicar_params(params)

    # Guardar historial
    estado["ultimo_ciclo"] = time.time()
    estado["iteraciones"] += 1
    estado["parametros_actuales"] = params
    estado["historial"].append({
        "timestamp": int(time.time()),
        "wr": wr,
        "pf": pf,
        "trades": total,
        "cambios": cambios
    })

    # Mantener historial últimas 50 entradas
    estado["historial"] = estado["historial"][-50:]
    _guardar(estado)


def _aplicar_params(params: dict):
    """Aplica parámetros dinámicamente a config"""
    try:
        config.SCORE_MIN = params.get("score_min", config.SCORE_MIN)
        config.RSI_OVERSOLD = params.get("rsi_oversold", config.RSI_OVERSOLD)
        config.RSI_OVERBOUGHT = params.get("rsi_overbought", config.RSI_OVERBOUGHT)
        config.SL_ATR_MULT = params.get("sl_atr_mult", config.SL_ATR_MULT)
        config.TP_ATR_MULT = params.get("tp_atr_mult", config.TP_ATR_MULT)
        print(f"[LEARNER] ✅ Parámetros aplicados")
    except Exception as e:
        print(f"[LEARNER] Error aplicando: {e}")


def get_estado() -> dict:
    return _cargar()
