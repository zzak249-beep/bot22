#!/usr/bin/env python3
"""
main.py — BingX Trading Bot v5.0 AGRESIVO
Loop principal con circuit breaker, compound y auto-optimización
"""

import time
import sys
from datetime import datetime

import config
import exchange
import analizar
import database
import notifier
import memoria
import learner
import config_pares


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def verificar_credenciales():
    if not config.BINGX_API_KEY or not config.BINGX_SECRET_KEY:
        log("❌ BINGX_API_KEY o BINGX_SECRET_KEY no configurados")
        log("   Configura las variables en Railway → Variables")
        sys.exit(1)


def circuit_breaker_check(balance_inicio: float, balance_actual: float,
                          perdidas_consecutivas: int) -> bool:
    """
    Verifica si se debe activar el circuit breaker.
    Returns True si el bot debe pausarse.
    """
    # Pérdida diaria máxima
    if balance_inicio > 0:
        perdida_pct = (balance_inicio - balance_actual) / balance_inicio
        if perdida_pct >= config.CB_MAX_DAILY_LOSS_PCT:
            notifier.notify_circuit_breaker(
                f"Pérdida diaria {perdida_pct*100:.1f}%",
                perdida_pct * 100
            )
            return True

    # Pérdidas consecutivas
    if perdidas_consecutivas >= config.CB_MAX_CONSECUTIVE_LOSS:
        notifier.notify_circuit_breaker(
            f"{perdidas_consecutivas} pérdidas consecutivas",
            0.0
        )
        return True

    return False


def monitorear_posiciones_abiertas():
    """Monitorea posiciones abiertas y las cierra si es necesario"""
    posiciones_db = database.get_posiciones_activas()
    if not posiciones_db:
        return

    for pos in posiciones_db:
        symbol = pos["symbol"]
        lado = pos["lado"]
        precio_entrada = pos["precio_entrada"]
        sl = pos["sl"]
        tp = pos["tp"]

        precio_actual = exchange.get_precio(symbol)
        if precio_actual <= 0:
            continue

        # Verificar si SL o TP fue tocado
        cerrar = False
        resultado = "ABIERTO"
        pnl = 0.0

        if lado == "LONG":
            if precio_actual <= sl:
                cerrar = True
                resultado = "LOSS"
                pnl = (precio_actual - precio_entrada) * pos["cantidad"]
            elif precio_actual >= tp:
                cerrar = True
                resultado = "WIN"
                pnl = (precio_actual - precio_entrada) * pos["cantidad"]

        elif lado == "SHORT":
            if precio_actual >= sl:
                cerrar = True
                resultado = "LOSS"
                pnl = (precio_entrada - precio_actual) * pos["cantidad"]
            elif precio_actual <= tp:
                cerrar = True
                resultado = "WIN"
                pnl = (precio_entrada - precio_actual) * pos["cantidad"]

        if cerrar:
            log(f"{'✅' if resultado=='WIN' else '❌'} Cerrando {lado} {symbol}: {resultado} ${pnl:+.2f}")
            exchange.cerrar_posicion(symbol, lado)
            database.cerrar_posicion_db(symbol, precio_actual, pnl, resultado)

            if resultado == "WIN":
                memoria.registrar_ganancia(symbol, pnl)
            else:
                memoria.penalizar_par(symbol, pnl)

            duracion = int((time.time() - pos["timestamp"]) / 60)
            notifier.notify_cierre(symbol, lado, precio_entrada, precio_actual,
                                   pnl, resultado, duracion)


def escanear_oportunidades(posiciones_actuales: int) -> int:
    """
    Escanea pares en busca de señales.
    Returns: número de nuevas posiciones abiertas
    """
    if posiciones_actuales >= config.MAX_POSICIONES:
        return 0

    balance = exchange.get_balance()
    if balance <= 0:
        log("⚠️ Balance = 0, verificar API keys")
        return 0

    pares = config_pares.get_pares_por_prioridad()
    posiciones_db = database.get_posiciones_activas()
    simbolos_abiertos = {p["symbol"] for p in posiciones_db}
    nuevas_posiciones = 0

    for symbol in pares:
        if posiciones_actuales + nuevas_posiciones >= config.MAX_POSICIONES:
            break

        if symbol in simbolos_abiertos:
            continue

        if memoria.esta_bloqueado(symbol):
            continue

        try:
            resultado = analizar.analizar_par(symbol)

            if resultado["señal"] == "NONE":
                continue

            # Aplicar modificador de memoria
            mod = memoria.get_score_modificador(symbol)
            score_ajustado = int(resultado["score"] * mod)

            if score_ajustado < config.SCORE_MIN:
                continue

            señal = resultado["señal"]
            precio = resultado["precio"]
            sl = resultado["sl"]
            tp = resultado["tp"]
            cantidad = exchange.calcular_cantidad(symbol, precio, balance)

            if cantidad <= 0:
                continue

            log(f"🎯 Señal {señal} {symbol} score:{score_ajustado} precio:{precio:.4f}")

            # Abrir posición
            if señal == "LONG":
                resp = exchange.abrir_long(symbol, cantidad, sl, tp)
            else:
                resp = exchange.abrir_short(symbol, cantidad, sl, tp)

            if resp and (resp.get("demo") or resp.get("data") or resp.get("orderId")):
                database.guardar_posicion(symbol, señal, precio, cantidad, sl, tp, score_ajustado)
                notifier.notify_señal(symbol, señal, precio, sl, tp, score_ajustado, cantidad, balance)
                nuevas_posiciones += 1
                simbolos_abiertos.add(symbol)
                log(f"✅ Posición abierta: {señal} {symbol}")
            else:
                log(f"⚠️ Error abriendo {symbol}: {resp}")
                memoria.registrar_error_api(symbol)

        except Exception as e:
            log(f"⚠️ Error analizando {symbol}: {e}")
            if config.MODO_DEBUG:
                import traceback
                traceback.print_exc()

        time.sleep(0.3)  # Rate limiting

    return nuevas_posiciones


def main():
    log("=" * 60)
    log("  🤖 BOT BINGX v5.0 AGRESIVO INICIANDO")
    log("=" * 60)

    # Verificar credenciales
    verificar_credenciales()

    # Inicializar base de datos
    database.init_db()

    # Balance inicial
    balance_inicio_dia = exchange.get_balance()
    log(f"💰 Balance inicial: ${balance_inicio_dia:.2f} USDT")
    log(f"{'🔥 DINERO REAL' if not config.MODO_DEMO else '🧪 MODO DEMO'}")
    log(f"⚙️  Leverage:{config.LEVERAGE}x | Score:{config.SCORE_MIN} | MaxPos:{config.MAX_POSICIONES}")

    # Notificar inicio
    notifier.notify_inicio(balance_inicio_dia)

    # Limpiar bloqueados viejos
    memoria.limpiar_bloqueados_viejos()

    ciclo = 0
    ultimo_reporte = time.time()
    ultimo_reset_dia = time.time()
    circuit_activado = False
    circuit_hasta = 0

    while True:
        try:
            ciclo += 1
            ahora = time.time()

            # Reset diario (cada 24h)
            if (ahora - ultimo_reset_dia) >= 86400:
                balance_inicio_dia = exchange.get_balance()
                circuit_activado = False
                ultimo_reset_dia = ahora
                log(f"📅 Reset diario - Balance: ${balance_inicio_dia:.2f}")

            # Circuit breaker - esperar si está activado
            if circuit_activado:
                if ahora < circuit_hasta:
                    tiempo_restante = int((circuit_hasta - ahora) / 60)
                    log(f"⏸️ Circuit breaker activo - {tiempo_restante} min restantes")
                    time.sleep(60)
                    continue
                else:
                    circuit_activado = False
                    log("▶️ Circuit breaker desactivado - reanudando")

            log(f"\n{'='*40}\n🔄 CICLO #{ciclo}")

            # 1. Monitorear posiciones abiertas
            monitorear_posiciones_abiertas()

            # 2. Obtener estado actual
            balance = exchange.get_balance()
            posiciones = database.get_posiciones_activas()
            n_pos = len(posiciones)
            perdidas_consec = database.get_perdidas_consecutivas()

            log(f"💰 Balance: ${balance:.2f} | Posiciones: {n_pos}/{config.MAX_POSICIONES}")

            # 3. Circuit breaker check
            if circuit_breaker_check(balance_inicio_dia, balance, perdidas_consec):
                circuit_activado = True
                circuit_hasta = ahora + 3600  # Pausar 1 hora
                log("⛔ CIRCUIT BREAKER - Pausando 1 hora")
                time.sleep(60)
                continue

            # 4. Escanear nuevas oportunidades
            if n_pos < config.MAX_POSICIONES:
                nuevas = escanear_oportunidades(n_pos)
                if nuevas > 0:
                    log(f"📈 {nuevas} nueva(s) posición(es) abierta(s)")
            else:
                log(f"📊 Máximo de posiciones alcanzado ({n_pos}/{config.MAX_POSICIONES})")

            # 5. Learner - optimización automática
            if learner.debe_ejecutar():
                log("🧠 Ejecutando optimización automática...")
                learner.optimizar()

            # 6. Reporte periódico (cada hora)
            if (ahora - ultimo_reporte) >= 3600:
                stats = database.get_stats_hoy()
                notifier.notify_reporte(stats, balance, n_pos)
                log(memoria.get_resumen())
                ultimo_reporte = ahora

            # 7. Esperar siguiente ciclo
            log(f"⏳ Esperando {config.CICLO_SEGUNDOS}s...")
            time.sleep(config.CICLO_SEGUNDOS)

        except KeyboardInterrupt:
            log("\n👋 Bot detenido por usuario")
            sys.exit(0)

        except Exception as e:
            log(f"❌ Error en ciclo principal: {e}")
            if config.MODO_DEBUG:
                import traceback
                traceback.print_exc()
            notifier.notify_error(str(e))
            time.sleep(60)


if __name__ == "__main__":
    main()
