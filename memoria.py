#!/usr/bin/env python3
"""
memoria.py — Sistema de memoria del bot
Penaliza pares problemáticos y aprende de errores
"""

import json
import os
import time
from datetime import datetime

MEMORIA_FILE = "bot_memoria.json"


def _cargar() -> dict:
    if os.path.exists(MEMORIA_FILE):
        try:
            with open(MEMORIA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "pares_bloqueados": {},
        "pares_penalizados": {},
        "pares_favoritos": {},
        "errores_api": {},
        "ultima_actualizacion": 0
    }


def _guardar(data: dict):
    try:
        with open(MEMORIA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[MEMORIA] Error guardando: {e}")


def esta_bloqueado(symbol: str) -> bool:
    """Verifica si un par está bloqueado"""
    data = _cargar()
    bloqueados = data.get("pares_bloqueados", {})

    if symbol in bloqueados:
        info = bloqueados[symbol]
        # Desbloquear después del tiempo de penalización
        if time.time() > info.get("hasta", 0):
            del bloqueados[symbol]
            _guardar(data)
            return False
        return True
    return False


def bloquear_par(symbol: str, razon: str, horas: float = 3.0):
    """Bloquea un par temporalmente"""
    data = _cargar()
    hasta = time.time() + (horas * 3600)
    data["pares_bloqueados"][symbol] = {
        "razon": razon,
        "hasta": hasta,
        "fecha": datetime.now().isoformat()
    }
    _guardar(data)
    print(f"[MEMORIA] 🚫 {symbol} bloqueado {horas}h: {razon}")


def penalizar_par(symbol: str, perdida: float):
    """Penaliza un par por pérdida"""
    data = _cargar()
    penalizados = data.get("pares_penalizados", {})

    if symbol not in penalizados:
        penalizados[symbol] = {"perdidas": 0, "total_pnl": 0.0, "count": 0}

    penalizados[symbol]["perdidas"] += 1
    penalizados[symbol]["total_pnl"] += perdida
    penalizados[symbol]["count"] += 1

    # Si acumula 3 pérdidas consecutivas → bloquear
    if penalizados[symbol]["perdidas"] >= 3:
        bloquear_par(symbol, f"3 pérdidas seguidas (${perdida:.2f})",
                     horas=config_horas(penalizados[symbol]["perdidas"]))
        penalizados[symbol]["perdidas"] = 0  # Reset contador

    data["pares_penalizados"] = penalizados
    _guardar(data)


def config_horas(num_perdidas: int) -> float:
    """Horas de bloqueo según número de pérdidas"""
    if num_perdidas <= 3:
        return 3.0
    elif num_perdidas <= 5:
        return 6.0
    elif num_perdidas <= 8:
        return 12.0
    else:
        return 24.0


def registrar_ganancia(symbol: str, ganancia: float):
    """Registra ganancia y reduce penalizaciones"""
    data = _cargar()
    penalizados = data.get("pares_penalizados", {})

    if symbol in penalizados:
        penalizados[symbol]["perdidas"] = max(0, penalizados[symbol]["perdidas"] - 1)
        penalizados[symbol]["total_pnl"] += ganancia

    favoritos = data.get("pares_favoritos", {})
    if symbol not in favoritos:
        favoritos[symbol] = {"ganancias": 0, "total_pnl": 0.0}
    favoritos[symbol]["ganancias"] += 1
    favoritos[symbol]["total_pnl"] += ganancia

    data["pares_penalizados"] = penalizados
    data["pares_favoritos"] = favoritos
    _guardar(data)


def registrar_error_api(symbol: str):
    """Registra error de API y bloquea si hay muchos"""
    data = _cargar()
    errores = data.get("errores_api", {})

    if symbol not in errores:
        errores[symbol] = {"count": 0, "ultimo": 0}

    errores[symbol]["count"] += 1
    errores[symbol]["ultimo"] = time.time()

    if errores[symbol]["count"] >= 5:
        bloquear_par(symbol, "Muchos errores API", horas=6.0)
        errores[symbol]["count"] = 0

    data["errores_api"] = errores
    _guardar(data)


def get_score_modificador(symbol: str) -> float:
    """
    Retorna modificador de score basado en historial:
    > 1.0 = par favorito (bonus)
    < 1.0 = par problemático (penalización)
    = 1.0 = neutro
    """
    data = _cargar()

    penalizados = data.get("pares_penalizados", {})
    if symbol in penalizados:
        perdidas = penalizados[symbol].get("perdidas", 0)
        if perdidas >= 2:
            return 0.7
        elif perdidas == 1:
            return 0.85

    favoritos = data.get("pares_favoritos", {})
    if symbol in favoritos:
        ganancias = favoritos[symbol].get("ganancias", 0)
        if ganancias >= 5:
            return 1.15
        elif ganancias >= 3:
            return 1.1

    return 1.0


def limpiar_bloqueados_viejos():
    """Limpia bloqueos expirados"""
    data = _cargar()
    ahora = time.time()
    bloqueados = data.get("pares_bloqueados", {})
    viejos = [s for s, info in bloqueados.items() if ahora > info.get("hasta", 0)]
    for s in viejos:
        del bloqueados[s]
    if viejos:
        data["pares_bloqueados"] = bloqueados
        _guardar(data)


def get_resumen() -> str:
    """Resumen del estado de memoria"""
    data = _cargar()
    n_bloq = len(data.get("pares_bloqueados", {}))
    n_pen = len(data.get("pares_penalizados", {}))
    n_fav = len(data.get("pares_favoritos", {}))
    return f"🧠 Memoria: {n_bloq} bloqueados | {n_pen} penalizados | {n_fav} favoritos"
