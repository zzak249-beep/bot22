"""
main.py — Loop principal del bot v6
MEJORAS:
  - Trailing stop dinámico basado en ATR
  - Cierre parcial al 50% del TP → mover SL a breakeven
  - Registro completo en DB (score, divergencia, vol_relativo, mtf_rsi)
  - Señales Telegram enriquecidas (muestra divergencia, MTF)
  - Reporte periódico cada hora
"""

import time
import traceback
from datetime import datetime, date, timedelta

import config
import database
import exchange
import analizar
import learner
import notifier
import symbols_loader

# Estado en memoria
posiciones_abiertas: dict = {}   # par → trade_dict
balance_inicio_dia:  float = 0.0
fecha_actual:        date  = None
ultimo_reporte_hora: int   = -1  # hora del último reporte Telegram


# ============================================================
# INICIALIZACIÓN
# ============================================================

def inicializar():
    global balance_inicio_dia, fecha_actual

    print("=" * 60)
    print("  BOT22 — BB+RSI Elite v6")
    print(f"  Modo: {'DEMO 🧪' if config.MODO_DEMO else 'REAL 💰'}")
    print(f"  EMA={config.EMA_FILTRO_ACTIVO} | MTF={config.MTF_ACTIVO} | "
          f"Trailing={config.TRAILING_STOP_ACTIVO} | ParcialClose={config.CIERRE_PARCIAL_ACTIVO}")
    print("=" * 60)

    # Cargar TODOS los pares de BingX Futuros
    print("[MAIN] Cargando pares desde BingX Futuros...")
    pares_cargados = symbols_loader.load_symbols(force=True)
    print(f"[MAIN] {len(pares_cargados)} pares cargados y listos para escanear")

    database.init_db()
    balance_inicio_dia = exchange.get_balance()
    fecha_actual       = date.today()

    print(f"[MAIN] Balance inicial: ${balance_inicio_dia:.2f}")
    print(f"[MAIN] Pares activos: {len(config.PARES)}")

    notifier.bot_iniciado(config.PARES, balance_inicio_dia)


def nuevo_dia():
    global balance_inicio_dia, fecha_actual
    balance_inicio_dia = exchange.get_balance()
    fecha_actual       = date.today()
    print(f"[MAIN] ── Nuevo día: {fecha_actual} | Balance: ${balance_inicio_dia:.2f} ──")


# ============================================================
# CIRCUIT BREAKER
# ============================================================

def circuit_breaker_activo() -> tuple:
    balance_actual = exchange.get_balance()
    if balance_actual <= 0:
        return False, ""

    pnl_dia = balance_actual - balance_inicio_dia
    pnl_pct = pnl_dia / balance_inicio_dia if balance_inicio_dia > 0 else 0

    if pnl_pct <= config.MAX_PNL_NEGATIVO_DIA:
        return True, f"PnL día {pnl_pct*100:.1f}%"

    racha = database.get_racha_perdidas_hoy()
    if racha >= config.MAX_PERDIDAS_SEGUIDAS:
        return True, f"{racha} pérdidas consecutivas"

    return False, ""


# ============================================================
# ABRIR POSICIÓN
# ============================================================

def abrir_posicion(senal: dict, balance: float) -> bool:
    par    = senal["par"]
    precio = senal["precio"]
    sl     = senal["sl"]
    tp     = senal["tp"]

    if par in posiciones_abiertas:
        return False
    if len(posiciones_abiertas) >= config.MAX_POSICIONES:
        return False

    cantidad = exchange.calcular_cantidad(par, balance, precio)
    if cantidad <= 0:
        print(f"[MAIN] {par}: cantidad=0, skip")
        return False

    exchange.set_leverage(par, config.LEVERAGE)
    trade = exchange.abrir_long(par, cantidad, precio, sl, tp)
    if not trade:
        return False

    # Enriquecer el trade dict con datos de la señal
    trade.update({
        "rsi":              senal["rsi"],
        "atr":              senal["atr"],
        "bb":               senal.get("bb", {}),
        "rr":               senal["rr"],
        "score":            senal["score"],
        "divergencia":      senal.get("divergencia", False),
        "vol_relativo":     senal.get("vol_relativo", 1.0),
        "mtf_rsi":          senal.get("mtf_rsi", 50.0),
        "balance_antes":    balance,
        "cantidad_inicial": cantidad,
        "sl_original":      sl,
        "tp_original":      tp,
        # Flags de gestión activa
        "parcial_cerrado":  False,
        "trailing_activo":  False,
        "breakeven_activo": False,
    })

    posiciones_abiertas[par] = trade
    print(f"[MAIN] ✅ ABIERTO {par} | entrada:{precio:.6f} SL:{sl:.6f} TP:{tp:.6f} qty:{cantidad} score:{senal['score']}")
    notifier.trade_abierto(trade)
    return True


# ============================================================
# MONITOREO DE POSICIONES (trailing, parcial, cierre)
# ============================================================

def monitorear_posiciones():
    if not posiciones_abiertas:
        return

    for par, trade in list(posiciones_abiertas.items()):
        try:
            precio_actual  = exchange.get_precio(par)
            if precio_actual <= 0:
                continue

            precio_entrada = trade["precio_entrada"]
            sl             = trade["sl"]
            tp             = trade["tp_original"]
            atr            = trade.get("atr", 0)
            cantidad       = trade.get("cantidad", trade.get("cantidad_inicial", 0))

            recorrido_total = tp - precio_entrada
            recorrido_actual= precio_actual - precio_entrada

            cerrada       = False
            motivo        = ""
            precio_salida = precio_actual

            # ── Comprobar si el exchange cerró la posición (SL/TP hit) ─────
            if not config.MODO_DEMO:
                posicion_exchange = exchange.get_posicion(par)
                if not posicion_exchange:
                    cerrada       = True
                    motivo        = "SL/TP (exchange)"
                    precio_salida = precio_actual
            else:
                # Modo demo: simular SL/TP
                if precio_actual <= sl:
                    cerrada       = True
                    motivo        = "SL"
                    precio_salida = sl
                elif precio_actual >= tp:
                    cerrada       = True
                    motivo        = "TP"
                    precio_salida = tp

            if cerrada:
                _cerrar_y_registrar(par, precio_salida, motivo)
                continue

            # ── Cierre parcial al 50% del recorrido hacia el TP ─────────────
            if (config.CIERRE_PARCIAL_ACTIVO
                    and not trade["parcial_cerrado"]
                    and recorrido_total > 0
                    and recorrido_actual >= recorrido_total * config.CIERRE_PARCIAL_TP_PCT):

                cant_parcial = round(cantidad * config.CIERRE_PARCIAL_PCT, 4)
                if cant_parcial >= 0.0001:
                    resultado_parcial = exchange.cerrar_parcial(par, cant_parcial)
                    if resultado_parcial:
                        precio_parcial = resultado_parcial.get("precio_salida", precio_actual)
                        pnl_parcial    = ((precio_parcial - precio_entrada) / precio_entrada
                                          * config.LEVERAGE * trade["balance_antes"]
                                          * config.RIESGO_POR_TRADE * config.CIERRE_PARCIAL_PCT)

                        # Actualizar trade
                        trade["parcial_cerrado"] = True
                        trade["cantidad"]        = round(cantidad - cant_parcial, 4)
                        trade["pnl_parcial_usd"] = pnl_parcial

                        # Mover SL a breakeven
                        if config.BREAKEVEN_ACTIVO:
                            nuevo_sl = precio_entrada * 1.0001  # Breakeven + pequeño buffer
                            if nuevo_sl > sl:
                                exchange.actualizar_sl(par, trade["cantidad"], nuevo_sl)
                                trade["sl"]               = nuevo_sl
                                trade["breakeven_activo"] = True
                                print(f"[MAIN] 🔒 BREAKEVEN {par} SL→{nuevo_sl:.6f}")

                        print(f"[MAIN] 📤 PARCIAL {par} qty:{cant_parcial} @ {precio_parcial:.6f} | PnL: ${pnl_parcial:+.4f}")
                        notifier.cierre_parcial(par, precio_entrada, precio_parcial, pnl_parcial, trade["cantidad"])

            # ── Trailing Stop ────────────────────────────────────────────────
            if (config.TRAILING_STOP_ACTIVO
                    and atr > 0
                    and recorrido_total > 0
                    and recorrido_actual >= recorrido_total * config.TRAILING_ACTIVAR_PCT):

                nuevo_trailing = precio_actual - (atr * config.TRAILING_ATR_MULT)

                if nuevo_trailing > trade["sl"]:
                    exchange.actualizar_sl(par, trade.get("cantidad", cantidad), nuevo_trailing)
                    trade["sl"]             = nuevo_trailing
                    trade["trailing_activo"]= True
                    if config.MODO_DEBUG:
                        print(f"[MAIN] 📈 TRAILING {par} SL→{nuevo_trailing:.6f} (precio:{precio_actual:.6f})")

        except Exception as e:
            print(f"[MAIN] Error monitoreando {par}: {e}")
            if config.MODO_DEBUG:
                traceback.print_exc()


# ============================================================
# CERRAR Y REGISTRAR EN DB
# ============================================================

def _cerrar_y_registrar(par: str, precio_salida: float, motivo: str):
    trade = posiciones_abiertas.pop(par, None)
    if not trade:
        return

    precio_entrada    = trade.get("precio_entrada", 0)
    cantidad          = trade.get("cantidad", trade.get("cantidad_inicial", 0))
    cantidad_inicial  = trade.get("cantidad_inicial", cantidad)
    balance_antes     = trade.get("balance_antes", 0)
    pnl_parcial_usd   = trade.get("pnl_parcial_usd", 0.0)

    pnl_pct = ((precio_salida - precio_entrada) / precio_entrada * config.LEVERAGE
               if precio_entrada > 0 else 0)
    pnl_usd = pnl_pct * balance_antes * config.RIESGO_POR_TRADE
    resultado = "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "BE")

    # PnL total incluyendo cierre parcial
    pnl_total_usd = pnl_usd + pnl_parcial_usd

    balance_actual = exchange.get_balance()
    if config.MODO_DEMO:
        exchange.demo_actualizar_balance(pnl_total_usd)
        balance_actual = exchange.get_balance()

    database.guardar_trade({
        "par":               par,
        "lado":              "LONG",
        "precio_entrada":    precio_entrada,
        "precio_salida":     precio_salida,
        "cantidad":          cantidad,
        "cantidad_inicial":  cantidad_inicial,
        "pnl_usd":           pnl_usd,
        "pnl_pct":           pnl_pct * 100,
        "pnl_parcial_usd":   pnl_parcial_usd,
        "rsi_entrada":       trade.get("rsi", 0),
        "bb_posicion":       trade.get("bb", {}).get("posicion", 0),
        "atr_entrada":       trade.get("atr", 0),
        "sl_precio":         trade.get("sl", 0),
        "sl_original":       trade.get("sl_original", 0),
        "tp_precio":         trade.get("tp_original", 0),
        "resultado":         resultado,
        "motivo_cierre":     motivo,
        "parcial_cerrado":   int(trade.get("parcial_cerrado", False)),
        "trailing_activado": int(trade.get("trailing_activo", False)),
        "divergencia":       int(trade.get("divergencia", False)),
        "vol_relativo":      trade.get("vol_relativo", 1.0),
        "mtf_rsi":           trade.get("mtf_rsi", 50.0),
        "score_entrada":     trade.get("score", 0),
        "balance_antes":     balance_antes,
        "balance_despues":   balance_actual,
        "timestamp_entrada": trade.get("timestamp", ""),
        "timestamp_salida":  datetime.now().isoformat(),
        "order_id_entrada":  trade.get("order_id", ""),
        "order_id_salida":   "",
    })

    emoji = "✅ WIN" if resultado == "WIN" else "❌ LOSS" if resultado == "LOSS" else "➡️ BE"
    extras = []
    if trade.get("parcial_cerrado"):
        extras.append(f"parcial=${pnl_parcial_usd:+.4f}")
    if trade.get("trailing_activo"):
        extras.append("trailing")
    extras_str = f" [{', '.join(extras)}]" if extras else ""

    print(f"[MAIN] {emoji} {par} | {precio_entrada:.6f}→{precio_salida:.6f} | "
          f"PnL: ${pnl_total_usd:+.4f} | {motivo}{extras_str}")

    trade["precio_salida"] = precio_salida
    notifier.trade_cerrado(trade, pnl_total_usd, motivo, balance_actual)


# ============================================================
# ENVIAR SEÑAL A TELEGRAM
# ============================================================

def enviar_senal_telegram(senal: dict, balance: float):
    par    = senal["par"]
    precio = senal["precio"]
    sl     = senal["sl"]
    tp     = senal["tp"]
    rsi    = senal["rsi"]
    rr     = senal["rr"]
    score  = senal["score"]
    div    = senal.get("divergencia", False)
    mtf    = senal.get("mtf_rsi", 50)
    vol_r  = senal.get("vol_relativo", 1.0)

    modo     = "🤖 AUTO" if balance > 1 else "👤 MANUAL"
    div_icon = "★ DIVERGENCIA " if div else ""

    import requests as req
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return

    msg = (
        f"📈 <b>SEÑAL LONG — {modo}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Par     : <b>{par}</b> {div_icon}\n"
        f"💵 Entrada : <b>${precio:.6f}</b>\n"
        f"🔴 SL      : ${sl:.6f}\n"
        f"🟢 TP      : ${tp:.6f}\n"
        f"📐 R:R     : {rr:.2f} | RSI5m: {rsi:.1f} | RSI15m: {mtf:.1f}\n"
        f"📊 VolRel  : {vol_r:.1f}x | Score: {score}/100\n"
        f"🏦 Balance : ${balance:.2f}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )

    try:
        req.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"[TELEGRAM] Error: {e}")


# ============================================================
# REPORTE HORARIO
# ============================================================

def _reporte_horario_si_procede():
    global ultimo_reporte_hora
    hora_actual = datetime.now().hour
    if hora_actual != ultimo_reporte_hora:
        ultimo_reporte_hora = hora_actual
        try:
            balance = exchange.get_balance()
            equity  = exchange.get_equity()
            pnl_dia = database.get_pnl_hoy()
            stats   = database.get_stats_resumen()
            learner_estado = learner.get_estado_actual()

            pos_list = [
                {
                    "symbol":  par,
                    "entry":   t["precio_entrada"],
                    "current": exchange.get_precio(par),
                    "side":    "long",
                }
                for par, t in posiciones_abiertas.items()
            ]

            perf_str = (
                f"RSI={config.RSI_OVERSOLD} SL={config.SL_ATR_MULT} TP={config.TP_ATR_MULT} | "
                f"Penalizados: {len(learner_estado['pares_penalizados'])}"
            )

            notifier.send_status(pos_list, balance, stats, perf_str)
            database.guardar_balance(balance, equity, pnl_dia)
        except Exception as e:
            print(f"[MAIN] Error reporte horario: {e}")


# ============================================================
# CICLO PRINCIPAL
# ============================================================

def ciclo_principal():
    global fecha_actual

    # Nuevo día
    if date.today() != fecha_actual:
        nuevo_dia()

    # Circuit Breaker
    pausado, motivo_pausa = circuit_breaker_activo()
    if pausado:
        print(f"[MAIN] 🛑 CIRCUIT BREAKER: {motivo_pausa}")
        notifier.circuit_breaker(motivo_pausa, exchange.get_balance())
        time.sleep(3600)
        return

    # Learner (cada N horas)
    if learner.necesita_evaluacion():
        pares_validos = learner.evaluar_y_ajustar(config.PARES)
        learner.ajustar_parametros_globales()
    else:
        pares_validos = [
            p for p in config.PARES
            if p not in learner._cargar_estado().get("pares_penalizados", {})
        ]

    # Monitorear posiciones abiertas (trailing, parcial, cierre)
    monitorear_posiciones()

    # Buscar nuevas señales
    balance      = exchange.get_balance()
    pares_libres = [p for p in pares_validos if p not in posiciones_abiertas]

    if pares_libres:
        print(f"\n[MAIN] Analizando {len(pares_libres)} pares | Balance: ${balance:.2f} | "
              f"Pos: {len(posiciones_abiertas)}/{config.MAX_POSICIONES}")
        senales = analizar.analizar_todos(pares_libres)

        if senales:
            print(f"[MAIN] {len(senales)} señal(es) encontrada(s)")
            for senal in senales:
                # Siempre notificar por Telegram (útil incluso sin fondos)
                enviar_senal_telegram(senal, balance)

                # Abrir orden automática si hay fondos y slots disponibles
                if balance > 1 and len(posiciones_abiertas) < config.MAX_POSICIONES:
                    abrir_posicion(senal, balance)
        else:
            if config.MODO_DEBUG:
                print("[MAIN] Sin señales válidas este ciclo")

    # Reporte horario a Telegram
    _reporte_horario_si_procede()

    pnl_dia = database.get_pnl_hoy()
    print(
        f"[MAIN] Balance: ${balance:.2f} | PnL hoy: ${pnl_dia:+.4f} | "
        f"Pos: {len(posiciones_abiertas)}/{config.MAX_POSICIONES} | "
        f"Próximo ciclo: {config.CICLO_SEGUNDOS}s — {datetime.now().strftime('%H:%M:%S')}"
    )
    print("-" * 60)


# ============================================================
# ENTRY POINT
# ============================================================

def run():
    inicializar()
    ciclos = 0
    while True:
        try:
            ciclos += 1
            print(f"\n[MAIN] ═══ CICLO #{ciclos} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ═══")
            ciclo_principal()
            time.sleep(config.CICLO_SEGUNDOS)
        except KeyboardInterrupt:
            print("\n[MAIN] Bot detenido por el usuario")
            break
        except Exception as e:
            print(f"\n[MAIN] ❌ ERROR: {e}")
            if config.MODO_DEBUG:
                traceback.print_exc()
            notifier.error_critico(str(e))
            time.sleep(60)


if __name__ == "__main__":
    run()
