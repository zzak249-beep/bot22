"""
learner.py — Aprendizaje adaptativo v6
MEJORAS:
  - Persiste los parámetros aprendidos en config.py en disco
  - Evalúa score promedio de las señales (no solo WR/PF)
  - Penalización proporcional (más agresiva si el par es muy malo)
  - Rehabilitación gradual (prueba con tamaño reducido)
  - Detección mejorada de horas malas
"""

import json
import os
import re
from datetime import datetime, timedelta
import config
import database
import notifier

ESTADO_FILE = "learner_estado.json"


# ============================================================
# ESTADO PERSISTENTE
# ============================================================

def _cargar_estado() -> dict:
    if os.path.exists(ESTADO_FILE):
        try:
            with open(ESTADO_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"[LEARNER] Error cargando estado: {e}")
    return {
        "ultima_evaluacion": None,
        "rsi_optimo":        config.RSI_OVERSOLD,
        "sl_optimo":         config.SL_ATR_MULT,
        "tp_optimo":         config.TP_ATR_MULT,
        "horas_malas":       [],
        "pares_penalizados": {},
        "historial_ajustes": []
    }


def _guardar_estado(estado: dict):
    try:
        with open(ESTADO_FILE, "w") as f:
            json.dump(estado, f, indent=2)
    except Exception as e:
        print(f"[LEARNER] Error guardando estado: {e}")


def _persistir_config_disco(rsi: float, sl: float, tp: float):
    """
    Actualiza config.py en disco con los valores aprendidos.
    Usa regex para reemplazar solo la línea correspondiente,
    manteniendo el resto del archivo intacto.
    """
    if not config.LEARNER_PERSISTIR:
        return

    config_path = os.path.join(os.path.dirname(__file__), "config.py")
    if not os.path.exists(config_path):
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            contenido = f.read()

        reemplazos = {
            r"^RSI_OVERSOLD\s*=\s*[\d.]+": f"RSI_OVERSOLD     = {int(rsi)}",
            r"^SL_ATR_MULT\s*=\s*[\d.]+":  f"SL_ATR_MULT      = {sl}",
            r"^TP_ATR_MULT\s*=\s*[\d.]+":  f"TP_ATR_MULT      = {tp}",
        }

        for patron, reemplazo in reemplazos.items():
            contenido = re.sub(patron, reemplazo, contenido, flags=re.MULTILINE)

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(contenido)

        print(f"[LEARNER] config.py actualizado: RSI={int(rsi)} SL={sl} TP={tp}")
    except Exception as e:
        print(f"[LEARNER] Error actualizando config.py en disco: {e}")


def necesita_evaluacion() -> bool:
    estado = _cargar_estado()
    ultima = estado.get("ultima_evaluacion")
    if not ultima:
        return True
    dt = datetime.fromisoformat(ultima)
    return datetime.now() - dt > timedelta(hours=config.LEARNER_CICLO_H)


# ============================================================
# ANÁLISIS DE TRADES
# ============================================================

def _analizar_trades(trades: list) -> dict:
    """Extrae patrones de wins vs losses."""
    if not trades:
        return {}

    wins   = [t for t in trades if t.get("resultado") == "WIN"]
    losses = [t for t in trades if t.get("resultado") == "LOSS"]
    total  = len(trades)

    analisis = {
        "total":  total,
        "wins":   len(wins),
        "losses": len(losses),
        "wr":     len(wins) / total * 100 if total else 0,
    }

    # RSI promedio
    if wins:
        analisis["rsi_wins"]  = sum(t.get("rsi_entrada", 30) for t in wins) / len(wins)
    if losses:
        analisis["rsi_losses"]= sum(t.get("rsi_entrada", 30) for t in losses) / len(losses)

    # BB posición promedio
    if wins:
        analisis["bb_wins"]   = sum(t.get("bb_posicion", 0.5) for t in wins) / len(wins)
    if losses:
        analisis["bb_losses"] = sum(t.get("bb_posicion", 0.5) for t in losses) / len(losses)

    # Score promedio wins vs losses
    if wins:
        analisis["score_wins"]  = sum(t.get("score_entrada", 50) for t in wins) / len(wins)
    if losses:
        analisis["score_losses"]= sum(t.get("score_entrada", 50) for t in losses) / len(losses)

    # Horas con muchos losses
    horas_loss = {}
    for t in losses:
        ts = t.get("timestamp_entrada", "")
        try:
            hora = int(ts[11:13])
            horas_loss[hora] = horas_loss.get(hora, 0) + 1
        except:
            pass
    analisis["horas_loss"] = horas_loss

    # PnL promedio
    if wins:
        analisis["avg_win"]  = sum(t.get("pnl_usd", 0) for t in wins) / len(wins)
    if losses:
        analisis["avg_loss"] = abs(sum(t.get("pnl_usd", 0) for t in losses) / len(losses))

    return analisis


# ============================================================
# AJUSTE DE RSI
# ============================================================

def _ajustar_rsi(estado: dict, analisis: dict) -> tuple:
    ajustes    = []
    rsi_actual = estado.get("rsi_optimo", config.RSI_OVERSOLD)
    wr         = analisis.get("wr", 50)
    rsi_wins   = analisis.get("rsi_wins")
    rsi_losses = analisis.get("rsi_losses")

    # Si wins tienen RSI más bajo → ser más selectivo
    if rsi_wins and rsi_losses and rsi_wins < rsi_losses - 2:
        nuevo = max(20, rsi_actual - 1)
        if nuevo != rsi_actual:
            ajustes.append(f"RSI {rsi_actual}→{nuevo} (wins con RSI más bajo)")
            estado["rsi_optimo"] = nuevo

    # Si losses tienen RSI muy bajo → relajar un poco
    elif rsi_wins and rsi_losses and rsi_losses < rsi_wins - 2:
        nuevo = min(40, rsi_actual + 1)
        if nuevo != rsi_actual:
            ajustes.append(f"RSI {rsi_actual}→{nuevo} (losses con RSI muy bajo)")
            estado["rsi_optimo"] = nuevo

    # WR muy baja → más restrictivo
    if wr < 35 and rsi_actual > 22:
        nuevo = rsi_actual - 2
        if nuevo != rsi_actual:
            ajustes.append(f"RSI {rsi_actual}→{nuevo} (WR baja: {wr:.1f}%)")
            estado["rsi_optimo"] = nuevo

    # WR muy alta con pocas señales → relajar para más señales
    elif wr > 75 and analisis.get("total", 0) < 10 and rsi_actual < 35:
        nuevo = rsi_actual + 1
        if nuevo != rsi_actual:
            ajustes.append(f"RSI {rsi_actual}→{nuevo} (WR alta, pocas señales)")
            estado["rsi_optimo"] = nuevo

    return estado, ajustes


# ============================================================
# AJUSTE DE SL / TP
# ============================================================

def _ajustar_sl_tp(estado: dict, analisis: dict) -> tuple:
    ajustes   = []
    avg_win   = analisis.get("avg_win",  0)
    avg_loss  = analisis.get("avg_loss", 0)
    sl_actual = estado.get("sl_optimo", config.SL_ATR_MULT)
    tp_actual = estado.get("tp_optimo", config.TP_ATR_MULT)

    if avg_win > 0 and avg_loss > 0:
        rr_real = avg_win / avg_loss

        if rr_real < 1.0:
            # Losses mayores que wins → ajustar SL más ajustado
            nuevo_sl = round(max(1.0, sl_actual - 0.1), 1)
            if nuevo_sl != sl_actual:
                ajustes.append(f"SL {sl_actual:.1f}→{nuevo_sl:.1f} (R:R real {rr_real:.2f})")
                estado["sl_optimo"] = nuevo_sl

        elif rr_real > 3.0 and tp_actual < 4.0:
            # R:R excelente → ampliar TP
            nuevo_tp = round(min(4.0, tp_actual + 0.2), 1)
            if nuevo_tp != tp_actual:
                ajustes.append(f"TP {tp_actual:.1f}→{nuevo_tp:.1f} (R:R excelente {rr_real:.2f})")
                estado["tp_optimo"] = nuevo_tp

    return estado, ajustes


# ============================================================
# DETECCIÓN DE HORAS MALAS
# ============================================================

def _detectar_horas_malas(estado: dict, analisis: dict) -> tuple:
    ajustes    = []
    horas_loss = analisis.get("horas_loss", {})

    # Horas con 3+ losses
    horas_malas_nuevas = [h for h, n in horas_loss.items() if n >= 3]

    # Mantener horas ya detectadas + nuevas
    horas_existentes = set(estado.get("horas_malas", []))
    horas_malas      = list(horas_existentes | set(horas_malas_nuevas))

    if horas_malas_nuevas:
        ajustes.append(f"Horas problemáticas detectadas: {horas_malas_nuevas}")
        estado["horas_malas"] = horas_malas

    return estado, ajustes


# ============================================================
# PENALIZACIÓN Y REHABILITACIÓN DE PARES
# ============================================================

def _penalizar_pares_malos(estado: dict, pares: list) -> tuple:
    penalizados   = []
    rehabilitados = []
    ahora = datetime.now()
    pares_pen = estado.get("pares_penalizados", {})

    # Rehabilitar pares cuya penalización expiró
    for par, hasta_str in list(pares_pen.items()):
        try:
            hasta = datetime.fromisoformat(hasta_str)
            if ahora >= hasta:
                del pares_pen[par]
                rehabilitados.append(par)
                database.rehabilitar_par(par)
                notifier.learner_ajuste(par, "REHABILITAR", "penalización expirada")
                print(f"  ✓ REHABILITADO {par}")
        except:
            del pares_pen[par]

    # Evaluar cada par
    for par in pares:
        if par in pares_pen:
            continue

        m = database.get_metricas_par(par)
        if not m or m.get("total_trades", 0) < config.LEARNER_MIN_TRADES:
            continue

        wr  = m.get("wr", 0)
        pf  = m.get("pf", 0)
        avg_score = m.get("avg_score", 50)

        motivo = None
        horas_pen = config.LEARNER_PENALIZACION_H

        if wr < 25 and pf < 0.6:
            motivo    = f"WR={wr:.0f}% PF={pf:.2f} (crítico)"
            horas_pen = 48   # Penalización doble si es muy malo
        elif wr < config.LEARNER_MIN_WR and pf < config.LEARNER_MIN_PF:
            motivo = f"WR={wr:.0f}% PF={pf:.2f} bajo mínimo"
        elif avg_score > 0 and avg_score < 40 and wr < 40:
            motivo = f"score promedio bajo ({avg_score:.0f}) con WR={wr:.0f}%"

        if motivo:
            hasta = (ahora + timedelta(hours=horas_pen)).isoformat()
            pares_pen[par] = hasta
            penalizados.append(par)
            database.penalizar_par(par, hasta, motivo)
            notifier.learner_ajuste(par, "PENALIZAR", motivo)
            print(f"  ⛔ PENALIZADO {par} {horas_pen}h: {motivo}")
        else:
            if m.get("total_trades", 0) >= config.LEARNER_MIN_TRADES:
                print(f"  ✓ OK {par}: WR={wr:.0f}% PF={pf:.2f} Score={avg_score:.0f}")

    estado["pares_penalizados"] = pares_pen
    return estado, penalizados, rehabilitados


# ============================================================
# APLICAR CAMBIOS A CONFIG EN MEMORIA Y EN DISCO
# ============================================================

def _aplicar_aprendizaje(estado: dict) -> list:
    rsi_nuevo = estado.get("rsi_optimo", config.RSI_OVERSOLD)
    sl_nuevo  = estado.get("sl_optimo",  config.SL_ATR_MULT)
    tp_nuevo  = estado.get("tp_optimo",  config.TP_ATR_MULT)

    cambiado = []
    if rsi_nuevo != config.RSI_OVERSOLD:
        cambiado.append(f"RSI_OVERSOLD: {config.RSI_OVERSOLD} → {rsi_nuevo}")
        config.RSI_OVERSOLD = rsi_nuevo
    if sl_nuevo != config.SL_ATR_MULT:
        cambiado.append(f"SL_ATR_MULT: {config.SL_ATR_MULT} → {sl_nuevo}")
        config.SL_ATR_MULT = sl_nuevo
    if tp_nuevo != config.TP_ATR_MULT:
        cambiado.append(f"TP_ATR_MULT: {config.TP_ATR_MULT} → {tp_nuevo}")
        config.TP_ATR_MULT = tp_nuevo

    # Persistir en disco si hubo cambios
    if cambiado:
        _persistir_config_disco(rsi_nuevo, sl_nuevo, tp_nuevo)

    return cambiado


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================

def evaluar_y_ajustar(pares_config: list) -> list:
    print(f"\n[LEARNER] Evaluando {len(pares_config)} pares...")
    ahora  = datetime.now()
    estado = _cargar_estado()
    trades = database.get_ultimos_trades(100)
    analisis = _analizar_trades(trades)

    todos_ajustes = []

    if len(trades) >= config.LEARNER_MIN_TRADES:
        # 1. RSI
        estado, aj = _ajustar_rsi(estado, analisis)
        todos_ajustes.extend(aj)

        # 2. SL / TP
        estado, aj = _ajustar_sl_tp(estado, analisis)
        todos_ajustes.extend(aj)

        # 3. Horas malas
        estado, aj = _detectar_horas_malas(estado, analisis)
        todos_ajustes.extend(aj)

    # 4. Penalizar/rehabilitar pares
    estado, penalizados, rehabilitados = _penalizar_pares_malos(estado, pares_config)

    # 5. Aplicar cambios en memoria y en disco
    cambios_config = _aplicar_aprendizaje(estado)
    todos_ajustes.extend(cambios_config)

    # 6. Registrar historial
    if todos_ajustes:
        entrada = {
            "timestamp": ahora.isoformat(),
            "ajustes":   todos_ajustes,
            "stats": {
                "total_trades": analisis.get("total", 0),
                "wr":           round(analisis.get("wr", 0), 1),
                "rsi_optimo":   estado.get("rsi_optimo"),
                "sl_optimo":    estado.get("sl_optimo"),
                "tp_optimo":    estado.get("tp_optimo"),
            }
        }
        historial = estado.get("historial_ajustes", [])
        historial.append(entrada)
        estado["historial_ajustes"] = historial[-30:]

        print("[LEARNER] Ajustes aplicados:")
        for a in todos_ajustes:
            print(f"  → {a}")

        if cambios_config:
            msg = "🧠 <b>LEARNER — Parámetros ajustados</b>\n" + "\n".join(f"• {c}" for c in cambios_config)
            _telegram(msg)
    else:
        print("[LEARNER] Sin ajustes necesarios")

    wr_str = f"{analisis.get('wr', 0):.1f}%" if analisis else "N/A"
    print(
        f"[LEARNER] WR={wr_str} | Trades={analisis.get('total',0)} | "
        f"RSI={config.RSI_OVERSOLD} | SL={config.SL_ATR_MULT} | TP={config.TP_ATR_MULT} | "
        f"Penalizados={len(penalizados)}"
    )

    estado["ultima_evaluacion"] = ahora.isoformat()
    _guardar_estado(estado)

    # Retornar pares activos (sin penalizados)
    pares_pen    = estado.get("pares_penalizados", {})
    pares_activos = [p for p in pares_config if p not in pares_pen]
    return pares_activos


def ajustar_parametros_globales():
    """Comprobación adicional de horas problemáticas."""
    estado      = _cargar_estado()
    horas_malas = estado.get("horas_malas", [])
    if horas_malas:
        hora_actual = datetime.now().hour
        if hora_actual in horas_malas:
            print(f"[LEARNER] ⚠️ Hora actual ({hora_actual}h) marcada como problemática")


def get_estado_actual() -> dict:
    """Retorna el estado actual del learner para diagnóstico."""
    estado = _cargar_estado()
    return {
        "rsi_optimo":        estado.get("rsi_optimo", config.RSI_OVERSOLD),
        "sl_optimo":         estado.get("sl_optimo",  config.SL_ATR_MULT),
        "tp_optimo":         estado.get("tp_optimo",  config.TP_ATR_MULT),
        "horas_malas":       estado.get("horas_malas", []),
        "pares_penalizados": list(estado.get("pares_penalizados", {}).keys()),
        "ultima_evaluacion": estado.get("ultima_evaluacion", "nunca"),
        "total_ajustes":     len(estado.get("historial_ajustes", []))
    }


def _telegram(msg: str):
    import requests
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except:
        pass
