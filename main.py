"""
main.py — SMC Bot BingX v4.4 [REAL MONEY | 24/7 | AUTO-LEARN]
$10 por trade × 10x | Compounding | OB+FVG confluencia | Sin parar

BUGS CORREGIDOS:
  ✅ FIX#1 — KeyError 'total_trades' en reporte horario (campo no existía)
  ✅ FIX#2 — Notificación muestra si es OB+FVG confluencia o solo FVG
  ✅ FIX#3 — OB mitigado se indica en logs y Telegram
"""

import sys, os, time, traceback
from datetime import datetime, date, timezone
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    stream=sys.stdout,
    force=True,
)
if os.getenv("LOG_LEVEL", "").upper() == "DEBUG":
    logging.getLogger().setLevel(logging.DEBUG)

log = logging.getLogger("main")
log.info("=== ARRANQUE SMC BOT v4.2 ===")

try:
    import config, exchange, analizar, memoria, scanner_pares, metaclaw
    from config_pares import PARES as PARES_FIJOS
except Exception as e:
    log.error(f"ERROR importando módulos: {e}\n{traceback.format_exc()}")
    sys.exit(1)

errores_config = config.validar()
for err in errores_config:
    log.warning(f"⚠️  CONFIG: {err}")

log.info(f"Módulos OK | {config.VERSION}")


# ═══════════════════════════════════════════════════════
# CORRELACIÓN
# ═══════════════════════════════════════════════════════

GRUPOS_CORRELACION = [
    {"BTC-USDT", "ETH-USDT"},
    {"SOL-USDT", "AVAX-USDT", "APT-USDT", "SUI-USDT"},
    {"ARB-USDT", "OP-USDT"},
    {"DOGE-USDT", "PEPE-USDT", "WIF-USDT", "BONK-USDT"},
    {"BNB-USDT", "TRX-USDT"},
]

def hay_correlacion(par: str, lado: str, posiciones: dict) -> bool:
    if not config.CORRELACION_ACTIVO:
        return False
    for grupo in GRUPOS_CORRELACION:
        if par not in grupo:
            continue
        for p_ab, pos in posiciones.items():
            if p_ab in grupo and p_ab != par and pos["lado"] == lado:
                log.info(f"[CORR] {par} {lado} bloqueado — {p_ab} ya abierto")
                return True
    return False

def hay_hedge(par: str, lado: str, posiciones: dict) -> bool:
    # Comprobar memoria local
    if par in posiciones and posiciones[par]["lado"] != lado:
        log.info(f"[ANTI-HEDGE] {par} {lado} — ya hay {posiciones[par]['lado']} (local)")
        return True
    # Comprobar también si hay posición abierta del mismo par en BingX (cualquier lado)
    if par in posiciones:
        log.info(f"[ANTI-HEDGE] {par} {lado} — par ya en posición")
        return True
    return False


# ═══════════════════════════════════════════════════════
# ESTADO
# ═══════════════════════════════════════════════════════

class Estado:
    def __init__(self):
        self.posiciones = {}
        self.pnl_hoy    = 0.0
        self.dia_actual = str(date.today())
        self.wins       = 0
        self.losses     = 0

    def reset_diario(self):
        hoy = str(date.today())
        if hoy != self.dia_actual:
            self.dia_actual = hoy
            self.pnl_hoy    = 0.0
            log.info(f"[RESET] Nuevo día {hoy} — PnL reseteado")

    def registrar_cierre(self, pnl: float):
        self.pnl_hoy += pnl
        if pnl > 0:
            self.wins   += 1
        else:
            self.losses += 1

    def max_perdida_alcanzada(self) -> bool:
        return config.MAX_PERDIDA_DIA > 0 and self.pnl_hoy <= -config.MAX_PERDIDA_DIA


estado = Estado()


# ═══════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════

def _notif(msg: str):
    try:
        import requests as rq
        tok = config.TELEGRAM_TOKEN.strip()
        cid = config.TELEGRAM_CHAT_ID.strip()
        if not tok or not cid:
            return
        rq.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log.error(f"Telegram: {e}")


def _notif_entrada(s: dict, trade_usdt: float, ejecutado: bool):
    lado  = "🟢 LONG" if s["lado"] == "LONG" else "🔴 SHORT"
    if ejecutado == "ok":
        ex = "✅ *Ejecutado*"
    elif isinstance(ejecutado, str) and ejecutado.startswith("bloq:"):
        razon = ejecutado[5:]
        ex = f"⚠️ *No ejecutado* — `{razon}`"
    elif isinstance(ejecutado, str) and ejecutado.startswith("error:"):
        razon = ejecutado[6:]
        ex = f"🚨 *Error API* — `{razon}`"
    else:
        ex = "⚠️ *No ejecutado*"
    motiv = " + ".join(s.get("motivos", []))

    extras = ""
    # OB+FVG confluencia es la señal más fuerte — mostrar prominentemente
    if s.get("ob_fvg_bull") or s.get("ob_fvg_bear"):
        extras += "🏆 `OB + FVG Confluencia` ← Entry Area\n"
    elif s.get("ob_bull") or s.get("ob_bear"):
        mit = s.get("ob_mitigado", False)
        extras += f"📦 `Order Block` {'⚠️ mitigado' if mit else '✅ no mitigado'}\n"

    if s.get("sweep_bull") or s.get("sweep_bear"):
        extras += "💧 `Liquidity Sweep`\n"
    if s.get("patron"):
        extras += f"🕯️ `{s['patron']}`\n"
    if s.get("choch_bull") or s.get("choch_bear"):
        extras += "🔄 `Change of Character`\n"
    elif s.get("bos_bull") or s.get("bos_bear"):
        extras += "🔨 `Break of Structure`\n"
    if s.get("htf") in ("BULL", "BEAR"):
        extras += f"📈 MTF 1h: `{s['htf']}`\n"
    if s.get("asia_valido"):
        extras += "🌙 `Rango Asia activo`\n"
    if s.get("vwap"):
        vwap_pos = "sobre" if s.get("sobre_vwap") else "bajo"
        extras += f"📊 VWAP: `{s['vwap']:.6f}` ({vwap_pos})\n"

    _notif(
        f"{lado} — `{s['par']}` [{s.get('kz', '')}]\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entrada : `{s['precio']:.6f}`\n"
        f"🔶 TP1     : `{s['tp1']:.6f}` (50%)\n"
        f"✅ TP2     : `{s['tp']:.6f}`\n"
        f"🛑 SL      : `{s['sl']:.6f}`\n"
        f"📊 R:R     : `{s['rr']:.2f}x`\n"
        f"🏅 Score   : `{s['score']}/16`\n"
        f"📉 RSI     : `{s['rsi']:.1f}`\n"
        f"🧩 Señales : `{motiv}`\n"
        f"{extras}"
        f"💵 Trade   : `${trade_usdt:.2f}` × {config.LEVERAGE}x\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{ex}"
    )


def _notif_cierre(par, lado, entrada, salida, pnl, razon="", trade_usdt=0):
    ico  = "✅" if pnl >= 0 else "❌"
    comp = memoria._data["compounding"]
    _notif(
        f"{ico} *CIERRE {lado}* ({razon}) — `{par}`\n"
        f"`{entrada:.6f}` → `{salida:.6f}`\n"
        f"PnL: `${pnl:+.4f} USDT`\n"
        f"💰 Pool reinversión: `${comp['ganancias']:.2f}`\n"
        f"📈 Próx trade: `${memoria.get_trade_amount():.2f} USDT`"
    )


def _mcl_aprender(pos: dict, pnl: float):
    """Llama a MetaClaw.aprender en todos los cierres de trade."""
    if not config.METACLAW_ACTIVO:
        return
    try:
        import os as _os
        if _os.getenv("ANTHROPIC_API_KEY"):
            metaclaw.aprender(pos, ganado=(pnl > 0), pnl=pnl)
            log.debug(f"[MCL] aprender llamado PnL={pnl:+.4f}")
    except Exception as e:
        log.debug(f"[MCL] aprender error: {e}")


# ═══════════════════════════════════════════════════════
# CARGAR POSICIONES AL ARRANQUE
# ═══════════════════════════════════════════════════════

def cargar_posiciones_desde_bingx():
    if config.MODO_DEMO:
        return
    try:
        pos_reales = exchange.get_posiciones_abiertas()
        if not pos_reales:
            log.info("[ARRANQUE] Sin posiciones abiertas en BingX")
            return
        cargadas = 0
        for p in pos_reales:
            amt = float(p.get("positionAmt", 0) or 0)
            if amt == 0:
                continue
            symbol = p.get("symbol", "")
            par    = symbol if "-" in symbol else symbol.replace("USDT", "-USDT")
            if par in estado.posiciones:
                continue
            lado  = "LONG" if amt > 0 else "SHORT"
            entry = float(p.get("entryPrice", 0) or 0)
            qty   = abs(amt)
            if entry <= 0 or qty <= 0:
                continue
            estado.posiciones[par] = {
                "lado": lado, "entrada": entry, "qty": qty,
                "sl": float(p.get("stopLoss", 0) or 0),
                "tp": float(p.get("takeProfit", 0) or 0),
                "tp1": 0.0, "atr": 0.0,
                "sl_trailing": float(p.get("stopLoss", 0) or 0),
                "tp1_hit": False,
                "ts": datetime.now(timezone.utc).isoformat(),
                "recuperada": True,
                "trade_usdt": config.TRADE_USDT_BASE,
            }
            cargadas += 1
            log.info(f"[ARRANQUE] ✅ {lado} {par} @ {entry:.6f} qty={qty}")

        if cargadas:
            _notif(
                f"♻️ *Bot reiniciado — {cargadas} posición(es) recuperada(s)*\n"
                + "\n".join(
                    f"  {'🟢' if v['lado']=='LONG' else '🔴'} `{k}` {v['lado']} @ `{v['entrada']:.6f}`"
                    for k, v in estado.posiciones.items() if v.get("recuperada")
                )
            )
    except Exception as e:
        log.error(f"[ARRANQUE] {e}")


# ═══════════════════════════════════════════════════════
# SINCRONIZACIÓN
# ═══════════════════════════════════════════════════════

def sincronizar_posiciones():
    if not estado.posiciones or config.MODO_DEMO:
        return
    try:
        pos_reales = exchange.get_posiciones_abiertas()
        reales     = set()
        for p in pos_reales:
            s = p.get("symbol", "")
            reales.add(s)
            reales.add(s.replace("-", ""))
            if "USDT" in s and "-" not in s:
                reales.add(s.replace("USDT", "-USDT"))

        cerradas = [
            par for par in estado.posiciones
            if par not in reales and par.replace("-", "") not in reales
        ]
        for par in cerradas:
            pos    = estado.posiciones[par]
            lado   = pos["lado"]
            entry  = pos["entrada"]
            qty    = pos["qty"]
            sl_ef  = pos.get("sl_trailing", pos["sl"])
            tp     = pos["tp"]
            precio = exchange.get_precio(par)

            if sl_ef > 0 and tp > 0:
                if lado == "LONG":
                    salida, razon = (tp, "TP") if precio >= tp * 0.98 else (sl_ef, "SL")
                    pnl = qty * (salida - entry)
                else:
                    salida, razon = (tp, "TP") if precio <= tp * 1.02 else (sl_ef, "SL")
                    pnl = qty * (entry - salida)
            else:
                salida, razon = precio, "BINGX"
                pnl = qty * ((precio - entry) if lado == "LONG" else (entry - precio))

            estado.registrar_cierre(pnl)
            memoria.registrar_resultado(par, pnl, lado,
                kz=pos.get("kz", ""), motivos=pos.get("motivos", []))
            # ── MetaClaw: aprender del resultado ────────────────
            _mcl_aprender(pos, pnl)
            del estado.posiciones[par]
            _notif_cierre(par, lado, entry, salida, pnl, f"BingX-{razon}")
            log.info(f"[SYNC] {par} cerrado ({razon}) PnL≈{pnl:+.4f}")
    except Exception as e:
        log.error(f"[SYNC] {e}")


# ═══════════════════════════════════════════════════════
# TRAILING STOP
# ═══════════════════════════════════════════════════════

def actualizar_trailing(par, pos, precio):
    if not config.TRAILING_ACTIVO:
        return
    atr  = pos.get("atr", 0)
    lado = pos["lado"]
    if atr <= 0:
        return

    # Activar trailing solo cuando hay profit real (≥ TRAILING_ACTIVAR × ATR)
    activar_dist = atr * max(config.TRAILING_ACTIVAR, 1.5)
    # Distancia del trailing: 1.0 ATR mínimo para evitar ruido
    trail_dist = atr * max(config.TRAILING_DISTANCIA, 1.0)

    if lado == "LONG":
        profit = precio - pos["entrada"]
        if profit < activar_dist:
            return
        nuevo = precio - trail_dist
        # Nunca bajar el trailing (solo subir)
        actual = pos.get("sl_trailing", pos["sl"])
        if nuevo > actual:
            pos["sl_trailing"] = nuevo
            log.debug(f"[TRAIL] {par} LONG SL → {nuevo:.6f} (+{profit/atr:.1f}ATR)")
    else:
        profit = pos["entrada"] - precio
        if profit < activar_dist:
            return
        nuevo = precio + trail_dist
        actual = pos.get("sl_trailing", pos["sl"])
        if nuevo < actual:
            pos["sl_trailing"] = nuevo
            log.debug(f"[TRAIL] {par} SHORT SL → {nuevo:.6f} (+{profit/atr:.1f}ATR)")


# ═══════════════════════════════════════════════════════
# PARTIAL TP
# ═══════════════════════════════════════════════════════

def gestionar_partial_tp(par, pos, precio):
    if not config.PARTIAL_TP_ACTIVO or pos.get("tp1_hit"):
        return
    tp1  = pos.get("tp1", 0)
    lado = pos["lado"]
    if tp1 <= 0:
        return
    alcanzado = (precio >= tp1) if lado == "LONG" else (precio <= tp1)
    if not alcanzado:
        return

    qty_tp1 = round(pos["qty"] * 0.5, 6)
    if not config.MODO_DEMO:
        res         = exchange.cerrar_posicion(par, qty_tp1, lado)
        salida_real = (res or {}).get("precio_salida", precio) or precio
    else:
        salida_real = precio

    entrada = pos["entrada"]
    pnl_p   = qty_tp1 * ((salida_real - entrada) if lado == "LONG" else (entrada - salida_real))
    estado.pnl_hoy += pnl_p
    memoria.registrar_ganancia_compounding(pnl_p)

    be             = entrada * 1.0005 if lado == "LONG" else entrada * 0.9995
    pos["sl"]      = pos["sl_trailing"] = be
    pos["qty"]     = round(pos["qty"] - qty_tp1, 6)
    pos["tp1_hit"] = True

    log.info(f"[TP1] {par} 50% @ {salida_real:.6f} PnL_p={pnl_p:+.4f} SL→BE={be:.6f}")
    _notif(
        f"🔶 *TP1* — `{par}` {lado}\n"
        f"50% @ `{salida_real:.6f}` | PnL: `${pnl_p:+.4f}`\n"
        f"🔄 SL → `{be:.6f}` (breakeven)\n"
        f"📊 Pool: `${memoria._data['compounding']['ganancias']:.2f}` | "
        f"Próx: `${memoria.get_trade_amount():.2f} USDT`"
    )


# ═══════════════════════════════════════════════════════
# TIME EXIT
# ═══════════════════════════════════════════════════════

def check_time_exit(par, pos) -> bool:
    ts_str = pos.get("ts", "")
    if not ts_str:
        return False
    try:
        ts    = datetime.fromisoformat(ts_str)
        ahora = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (ahora - ts).total_seconds() / 3600 >= config.TIME_EXIT_HORAS
    except Exception:
        return False


# ═══════════════════════════════════════════════════════
# GESTIONAR POSICIONES
# ═══════════════════════════════════════════════════════

def gestionar_posiciones():
    for par, pos in list(estado.posiciones.items()):
        try:
            precio = exchange.get_precio(par)
            if precio <= 0:
                continue
            lado = pos["lado"]
            qty  = pos["qty"]

            gestionar_partial_tp(par, pos, precio)

            if check_time_exit(par, pos):
                res         = exchange.cerrar_posicion(par, qty, lado)
                salida_real = (res or {}).get("precio_salida", precio) or precio
                pnl = qty * ((salida_real - pos["entrada"]) if lado == "LONG" else (pos["entrada"] - salida_real))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado,
                    kz=pos.get("kz", ""), motivos=pos.get("motivos", []))
                _mcl_aprender(pos, pnl)
                del estado.posiciones[par]
                _notif_cierre(par, lado, pos["entrada"], salida_real, pnl, "TIME")
                continue

            actualizar_trailing(par, pos, precio)
            sl_ef  = pos.get("sl_trailing", pos["sl"])
            tp     = pos["tp"]
            sl_hit = (precio <= sl_ef) if lado == "LONG" else (precio >= sl_ef)
            tp_hit = (precio >= tp)    if lado == "LONG" else (precio <= tp)

            razon = salida = None
            if sl_hit:
                razon  = "TRAIL-SL" if pos.get("tp1_hit") else "SL"
                salida = sl_ef
            elif tp_hit:
                razon  = "TP2"
                salida = tp

            if razon:
                res         = exchange.cerrar_posicion(par, qty, lado)
                salida_real = (res or {}).get("precio_salida", salida) or salida
                pnl = qty * ((salida_real - pos["entrada"]) if lado == "LONG" else (pos["entrada"] - salida_real))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado,
                    kz=pos.get("kz", ""), motivos=pos.get("motivos", []))
                _mcl_aprender(pos, pnl)
                del estado.posiciones[par]
                log.info(f"CIERRE {lado} {par} @ {salida_real:.6f} PnL={pnl:+.4f} ({razon})")
                _notif_cierre(par, lado, pos["entrada"], salida_real, pnl, razon,
                              pos.get("trade_usdt", config.TRADE_USDT_BASE))

        except Exception as e:
            log.error(f"gestionar {par}: {e}")
        time.sleep(0.3)


# ═══════════════════════════════════════════════════════
# EJECUTAR SEÑAL
# ═══════════════════════════════════════════════════════


def _pos_bot_count() -> int:
    """Posiciones abiertas por el bot (excluye manuales con recuperada=True)."""
    return sum(1 for v in estado.posiciones.values() if not v.get("recuperada", False))


def ejecutar_senal(s: dict) -> str:
    par  = s["par"]
    lado = s["lado"]

    if par in estado.posiciones:
        return "skip"
    # Anti-hedge: verificar posiciones reales en BingX (no solo RAM)
    if not config.MODO_DEMO:
        try:
            pos_reales = exchange.get_posiciones_abiertas()
            for p in pos_reales:
                sym = p.get("symbol", "")
                par_norm = sym if "-" in sym else sym.replace("USDT", "-USDT")
                amt = float(p.get("positionAmt", 0) or 0)
                if par_norm == par and amt != 0:
                    log.info(f"[ANTI-HEDGE] {par} bloqueado — posición real ya abierta en BingX")
                    return "skip"
        except Exception:
            pass
    if hay_hedge(par, lado, estado.posiciones):
        return "bloq:anti-hedge activo"
    if hay_correlacion(par, lado, estado.posiciones):
        return "bloq:correlacion (par similar ya abierto)"
    if memoria.esta_bloqueado(par):
        return "bloq:par bloqueado por aprendizaje"

    # ── MetaClaw: validación IA ────────────────────────────────
    if config.METACLAW_ACTIVO and os.getenv("ANTHROPIC_API_KEY"):
        try:
            mc = metaclaw.validar(s)
            if mc.get("metaclaw_activo"):
                confianza = mc.get("confianza", 5)
                aprobar   = mc.get("aprobar", True)
                razon_mc  = mc.get("razon", "")
                log.info(f"[MCL] {par} {s['lado']} → {'✅ APROBADO' if aprobar else '❌ RECHAZADO'} confianza={confianza} {razon_mc}")
                if not aprobar and confianza >= config.METACLAW_VETO_MINIMO:
                    return f"bloq:MetaClaw rechazó (conf={confianza}) {razon_mc[:50]}"
        except Exception as e:
            log.debug(f"[MCL] Error validando: {e}")
    bot_pos = _pos_bot_count()
    if bot_pos >= config.MAX_POSICIONES:
        return f"bloq:MAX_POSICIONES ({bot_pos}/{config.MAX_POSICIONES} trades bot)"
    if estado.max_perdida_alcanzada():
        log.warning("[BLOQUEO] Max perdida diaria")
        return f"bloq:circuit-breaker PnL={estado.pnl_hoy:.2f}"

    # FIX v5.3: separar balance TOTAL (para compounding) de margen DISPONIBLE (para ejecutar)
    balance_total = exchange.get_balance()
    margen_libre  = exchange.get_available_margin()

    # Check: ¿hay margen libre suficiente para abrir el trade?
    margen_min = max(config.TRADE_USDT_BASE / config.LEVERAGE * 1.5, 2.0)
    if margen_libre < margen_min and not config.MODO_DEMO:
        log.warning(f"Margen libre insuficiente: ${margen_libre:.2f} (necesario: ${margen_min:.2f}) — balance total: ${balance_total:.2f}")
        return f"bloq:margen libre insuficiente (${margen_libre:.2f})"

    trade_usdt = memoria.get_trade_amount()

    # Compounding basado en balance TOTAL (no margen libre)
    balance = balance_total  # alias para compatibilidad con el resto del bloque
    if balance > config.TRADE_USDT_BASE * 2 and not config.MODO_DEMO:
        trade_por_balance = balance * 0.12  # 12% del balance total disponible
        trade_usdt = min(max(trade_usdt, trade_por_balance), config.TRADE_USDT_MAX)
        trade_usdt = round(trade_usdt, 2)

    qty = exchange.calcular_cantidad(par, trade_usdt, s["precio"])
    if qty <= 0:
        return f"bloq:qty=0 precio={s['precio']:.8g}"

    if balance < trade_usdt and not config.MODO_DEMO:
        # Reducir trade si balance insuficiente para el tamaño objetivo
        trade_usdt_reducido = balance * 0.80
        if trade_usdt_reducido >= config.TRADE_USDT_BASE:
            trade_usdt = round(trade_usdt_reducido, 2)
            qty = exchange.calcular_cantidad(par, trade_usdt, s["precio"])
            if qty <= 0:
                return f"bloq:margen (${balance:.2f} < ${trade_usdt:.2f})"
            log.info(f"[{par}] Trade reducido a ${trade_usdt:.2f} (balance ${balance:.2f})")
        else:
            log.warning(f"[{par}] Balance ${balance:.2f} < trade ${trade_usdt:.2f}")
            return f"bloq:margen (${balance:.2f} < ${trade_usdt:.2f})"

    if lado == "LONG":
        res = exchange.abrir_long(par, qty, s["precio"], s["sl"], s["tp"])
    else:
        res = exchange.abrir_short(par, qty, s["precio"], s["sl"], s["tp"])

    if not res or "error" in res:
        err = (res or {}).get("error", "respuesta vacía")
        log.error(f"Orden fallida {lado} {par}: {err}")
        memoria.registrar_error_api(par)
        return f"error:{err[:80]}"

    entrada_real = float(res.get("fill_price", 0) or 0)
    if entrada_real <= 0:
        entrada_real = exchange.get_precio(par) or s["precio"]

    atr    = s.get("atr", 0)
    precio = s["precio"]
    if atr > 0:
        sl_r  = (entrada_real - atr * config.SL_ATR_MULT)  if lado == "LONG" else (entrada_real + atr * config.SL_ATR_MULT)
        tp_r  = (entrada_real + atr * config.TP_ATR_MULT)  if lado == "LONG" else (entrada_real - atr * config.TP_ATR_MULT)
        tp1_r = (entrada_real + atr * config.PARTIAL_TP1_MULT) if lado == "LONG" else (entrada_real - atr * config.PARTIAL_TP1_MULT)
    else:
        ratio = entrada_real / precio if precio > 0 else 1.0
        sl_r  = s["sl"]  * ratio
        tp_r  = s["tp"]  * ratio
        tp1_r = s["tp1"] * ratio

    qty_real = float(res.get("executedQty", qty) or qty)
    memoria.registrar_inversion(trade_usdt)

    estado.posiciones[par] = {
        "lado":        lado,
        "entrada":     entrada_real,
        "qty":         qty_real,
        "sl":          sl_r,
        "tp":          tp_r,
        "tp1":         tp1_r,
        "atr":         atr,
        "sl_trailing": sl_r,
        "tp1_hit":     False,
        "ts":          datetime.now(timezone.utc).isoformat(),
        "recuperada":  False,
        "score":       s["score"],
        "motivos":     s.get("motivos", []),
        "kz":          s.get("kz", ""),
        "trade_usdt":  trade_usdt,
        "ob_fvg":      s.get("ob_fvg_bull") or s.get("ob_fvg_bear"),
    }

    slip = abs(entrada_real - precio) / precio * 100 if precio > 0 else 0
    log.info(
        f"✅ {lado} {par} fill:{entrada_real:.6f} "
        f"{'⚠️ SLIP:'+str(round(slip,1))+'%' if slip > 0.5 else ''} "
        f"${trade_usdt:.2f}×{config.LEVERAGE}x "
        f"SL:{sl_r:.6f} TP:{tp_r:.6f} score:{s['score']}/16 "
        f"{'🏆OB+FVG' if s.get('ob_fvg_bull') or s.get('ob_fvg_bear') else ''}"
    )
    return "ok"


# ═══════════════════════════════════════════════════════
# REPORTE HORARIO — FIX: total_trades no existe en compounding
# ═══════════════════════════════════════════════════════

def enviar_reporte(balance: float):
    pos_txt = ""
    for par, pos in estado.posiciones.items():
        p_actual = exchange.get_precio(par)
        pnl_est  = 0
        if p_actual > 0:
            pnl_est = pos["qty"] * (
                (p_actual - pos["entrada"]) if pos["lado"] == "LONG"
                else (pos["entrada"] - p_actual)
            )
        fase = "🔶→TP2" if pos.get("tp1_hit") else "▶️→TP1"
        ico  = "🟢" if pos["lado"] == "LONG" else "🔴"
        ob_tag = " 🏆" if pos.get("ob_fvg") else ""
        pos_txt += f"  {ico} `{par}` est:${pnl_est:+.2f} {fase} [{pos.get('score','?')}/16]{ob_tag}\n"
    if not pos_txt:
        pos_txt = "  _(sin posiciones)_\n"

    w, l = estado.wins, estado.losses
    wr   = f"{w/(w+l)*100:.1f}%" if (w+l) > 0 else "N/A"
    comp = memoria._data["compounding"]
    kz   = analizar.en_killzone()

    # FIX: usar len(memoria._data["trades"]) en lugar de comp["total_trades"]
    total_trades = len(memoria._data.get("trades", []))

    # MetaClaw resumen
    try:
        mc_txt = "\n" + metaclaw.get_resumen() if config.METACLAW_ACTIVO else ""
    except Exception:
        mc_txt = ""

    _notif(
        f"📊 *Reporte — {config.VERSION}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance    : `${balance:.2f} USDT`\n"
        f"📈 Sesión     : `{w}W / {l}L` WR:`{wr}`\n"
        f"PnL hoy       : `${estado.pnl_hoy:+.4f}` USDT\n"
        f"🕐 Killzone   : `{kz['nombre']}`\n"
        f"💵 Trade base : `$10.00` × {config.LEVERAGE}x\n"
        f"📊 Próx trade : `${memoria.get_trade_amount():.2f}`\n"
        f"💹 Pool reinv.: `${comp['ganancias']:.2f}` USDT\n"
        f"📊 Total PnL  : `${comp['total_ganado']:+.4f}`\n"
        f"🏆 Trades tot.: `{total_trades}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Posiciones:\n{pos_txt}"
        f"{mc_txt}"
    )


# ═══════════════════════════════════════════════════════
# MAIN LOOP — 24/7 sin parar
# ═══════════════════════════════════════════════════════

def main():
    log.info("=" * 65)
    log.info(f"  {config.VERSION}")
    log.info(f"  TRADE: $10 base | COMPOUNDING cada ${config.COMPOUND_STEP_USDT} ganados")
    log.info(f"  LEV:{config.LEVERAGE}x | MAX_POS:{config.MAX_POSICIONES} | TF:{config.TIMEFRAME} | MTF:{config.MTF_TIMEFRAME}")
    log.info(f"  SCORE≥{config.SCORE_MIN}/16 | PIVOT_PCT:{config.PIVOT_NEAR_PCT}% | MIN_RR:{config.MIN_RR}")
    log.info(f"  MetaClaw: {'✅ ACTIVO' if config.METACLAW_ACTIVO else '❌ INACTIVO'} | Premium/Discount | Displacement | IDM")
    log.info(f"  PIN_BAR:{config.PINBAR_RATIO} | VWAP:{config.VWAP_ACTIVO} | SWEEP:{config.SWEEP_ACTIVO}")
    log.info(f"  COOLDOWN:{config.COOLDOWN_VELAS} velas | DEMO:{config.MODO_DEMO}")
    log.info(f"  OB+FVG CONFLUENCIA ACTIVO | OB MITIGADO = INVÁLIDO")
    log.info("=" * 65)

    # FIX v4.4: sync PRIMERO — get_balance usa timestamp firmado,
    # si el reloj local difiere del servidor BingX → data=null → balance=$0
    # Crear directorio de memoria si no existe
    if config.MEMORY_DIR:
        import pathlib
        pathlib.Path(config.MEMORY_DIR).mkdir(parents=True, exist_ok=True)
        log.info(f"[MEM] Directorio memoria: {config.MEMORY_DIR}")

    log.info("Sincronizando tiempo con servidor BingX...")
    exchange.sync_server_time()
    exchange.diagnostico_balance()  # ← muestra RAW de todos los endpoints de balance

    # Con el reloj ya sincronizado, leer balance real
    balance = exchange.get_balance()
    log.info(f"Balance inicial: ${balance:.2f} USDT")

    if balance <= 0 and not config.MODO_DEMO:
        # Reintentar una vez antes de reportar (primera petición puede fallar)
        log.warning("[BALANCE] Balance=0, reintentando en 3s...")
        time.sleep(3)
        balance = exchange.get_balance()
        log.info(f"Balance reintento: ${balance:.2f} USDT")
        if balance <= 0:
            _notif("🚨 *Balance = $0*\nVerifica las API keys en Railway.")

    # FIX v4.4: wrap startup en try/except para evitar crash por fallo de red
    try:
        log.info("Cargando contratos de futuros perpetuos...")
        exchange._cargar_contratos()
        log.info(f"Contratos cargados: {len(exchange._CONTRATOS_FUTURES)} pares válidos")
    except Exception as e:
        log.warning(f"[STARTUP] _cargar_contratos falló: {e} — continuando sin filtro de contratos")

    try:
        cargar_posiciones_desde_bingx()
    except Exception as e:
        log.warning(f"[STARTUP] cargar_posiciones falló: {e} — continuando con posiciones vacías")

    log.info("Cargando pares de BingX...")
    try:
        pares_todos = scanner_pares.get_pares_cached(config.VOLUMEN_MIN_24H)
    except Exception as e:
        log.warning(f"[STARTUP] scanner_pares falló: {e} — usando lista de respaldo")
        from config_pares import PARES as pares_todos
    bloq_config  = set(config.PARES_BLOQUEADOS)
    futuros_validos = exchange._CONTRATOS_FUTURES
    pares_todos  = [p for p in pares_todos if p not in bloq_config
                    and (not futuros_validos or p in futuros_validos)]
    prioritarios = [p for p in (PARES_FIJOS + config.PARES_PRIORITARIOS) if p in set(pares_todos)]
    top_memoria  = [p for p in memoria.get_top_pares(10) if p in set(pares_todos)]
    resto        = [p for p in pares_todos if p not in set(prioritarios) and p not in set(top_memoria)]
    pares        = prioritarios + top_memoria + resto
    if config.MAX_PARES_SCAN > 0:
        pares = pares[:config.MAX_PARES_SCAN]

    log.info(f"Total pares: {len(pares)} ({len(prioritarios)} prioritarios + {len(top_memoria)} top memoria)")

    _notif(
        f"🤖 *{config.VERSION}* arrancado\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance    : `${balance:.2f} USDT`\n"
        f"💵 Trade      : `$10.00` × {config.LEVERAGE}x (real)\n"
        f"📊 Pares      : `{len(pares)}` (vol >${config.VOLUMEN_MIN_24H/1e6:.0f}M)\n"
        f"🏅 Score≥`{config.SCORE_MIN}/14` | Min R:R `{config.MIN_RR}x`\n"
        f"💹 Compounding: cada ${config.COMPOUND_STEP_USDT:.0f} ganados → +${config.COMPOUND_ADD_USDT:.0f}/trade\n"
        f"🏆 OB+FVG Confluencia | OB no mitigado\n"
        f"🧠 Pin Bar + Engulfing + Sweeps + VWAP\n"
        f"⏱️ Cooldown: {config.COOLDOWN_VELAS} velas | Time exit: {config.TIME_EXIT_HORAS}h\n"
        f"🔁 Anti-hedge + Correlación\n"
        f"🔴 *LIVE — DINERO REAL — 24/7*"
    )

    ciclo           = 0
    last_reporte    = time.time()
    last_scan_pares = time.time()

    while True:
        try:
            ciclo += 1
            main._errores_ciclo = 0  # FIX: reset contador errores por ciclo
            estado.reset_diario()
            balance = exchange.get_balance()
            kz      = analizar.en_killzone()

            log.info(
                f"Ciclo {ciclo} | {datetime.now(timezone.utc).strftime('%H:%M UTC')} | "
                f"Bal:${balance:.2f} | Pos:{_pos_bot_count()}bot/{len(estado.posiciones)}tot | "
                f"PnL:${estado.pnl_hoy:+.4f} | KZ:{kz['nombre']} | "
                f"Trade:${memoria.get_trade_amount():.2f}"
            )

            # Refrescar pares cada hora
            if time.time() - last_scan_pares > 3600:
                nuevos  = scanner_pares.get_pares_cached(config.VOLUMEN_MIN_24H)
                fv      = exchange._CONTRATOS_FUTURES
                nuevos  = [p for p in nuevos if p not in bloq_config
                           and (not fv or p in fv)]
                bloq_m  = set(memoria.get_pares_bloqueados())
                top_m   = [p for p in memoria.get_top_pares(10) if p in set(nuevos)]
                resto_n = [p for p in nuevos if p not in set(top_m) and p not in bloq_m]
                pares   = prioritarios + top_m + resto_n
                if config.MAX_PARES_SCAN > 0:
                    pares = pares[:config.MAX_PARES_SCAN]
                log.info(f"Pares actualizados: {len(pares)}")
                last_scan_pares = time.time()

            # Circuit breaker diario
            if estado.max_perdida_alcanzada():
                log.warning(f"🛑 Máx pérdida diaria (${estado.pnl_hoy:.2f}) — pausa 30min")
                _notif(
                    f"🛑 *Máx pérdida diaria* `${estado.pnl_hoy:.2f}`\n"
                    f"Pausa 30 minutos (retoma automáticamente)"
                )
                time.sleep(1800)
                continue

            sincronizar_posiciones()

            if estado.posiciones:
                gestionar_posiciones()
                balance = exchange.get_balance()

            if _pos_bot_count() < config.MAX_POSICIONES:
                bloq_ahora = set(memoria.get_pares_bloqueados())
                pares_scan = [
                    p for p in pares
                    if p not in estado.posiciones and p not in bloq_ahora
                ]

                log.info(f"Escaneando {len(pares_scan)} pares | Score≥{config.SCORE_MIN}/14 | KZ:{kz['nombre']}")
                senales = analizar.analizar_todos(pares_scan, workers=config.ANALISIS_WORKERS)

                if senales:
                    log.info(f"✓ {len(senales)} señal(es):")
                    for s in senales[:5]:
                        ob_tag = "🏆OB+FVG" if (s.get("ob_fvg_bull") or s.get("ob_fvg_bear")) else ""
                        log.info(
                            f"  {s['lado']:5s} {s['par']:15s} "
                            f"score={s['score']}/16 RSI={s['rsi']:.1f} "
                            f"R:R={s['rr']:.2f} KZ={s['kz']} HTF={s.get('htf','?')} "
                            f"patron={s.get('patron','-')} {ob_tag}"
                        )
                else:
                    log.info("Sin señales este ciclo")

                for s in senales:
                    if _pos_bot_count() >= config.MAX_POSICIONES:
                        break
                    if s["par"] in estado.posiciones:
                        continue

                    s["score"] = memoria.ajustar_score(
                        s["par"], s["score"],
                        kz=s.get("kz", ""),
                        motivos=s.get("motivos", []),
                    )
                    if s["score"] < config.SCORE_MIN:
                        log.info(f"[APRENDE] {s['par']} score={s['score']} < {config.SCORE_MIN}")
                        continue

                    # Saltar pares bloqueados permanentemente (sin Telegram)
                    if not exchange.par_es_soportado(s["par"]):
                        log.debug(f"[SKIP-API] {s['par']} bloqueado permanente")
                        continue

                    resultado = ejecutar_senal(s)

                    # skip = par ya en estado, no notificar
                    if resultado == "skip":
                        continue

                    # Error de API → loguear, Telegram solo primeros 3 por ciclo
                    if resultado and resultado.startswith("error:"):
                        log.error(f"[API-ERR] {s['par']}: {resultado[6:]}")
                        if not exchange.par_es_soportado(s["par"]):
                            log.warning(f"[BLOCKED] {s['par']} bloqueado tras fallo")
                        # FIX anti-spam: máx 3 notificaciones de error por ciclo
                        if not hasattr(main, '_errores_ciclo'):
                            main._errores_ciclo = 0
                        if main._errores_ciclo < 3:
                            _notif(f"🚨 *Orden fallida {s['lado']} `{s['par']}`*\n❌ `{resultado[6:80]}`")
                            main._errores_ciclo += 1
                        continue

                    # bloq:MAX_POSICIONES → no notificar señal (evitar spam)
                    if resultado and "MAX_POSICIONES" in resultado:
                        log.info(f"[SKIP] {s['par']} — {resultado}")
                        continue

                    # Para cualquier otro resultado: notificar (ejecutado o bloqueado con razón)
                    _notif_entrada(s, memoria.get_trade_amount(), resultado)
                    if resultado == "ok":
                        balance = exchange.get_balance()
                        time.sleep(2)

            # Reporte horario
            if time.time() - last_reporte >= 3600:
                enviar_reporte(balance)
                _notif(memoria.resumen())
                last_reporte = time.time()

        except KeyboardInterrupt:
            log.info("Detenido (Ctrl+C)")
            _notif("🛑 *SMC Bot v4.4 detenido manualmente.*")
            break
        except Exception as e:
            log.error(f"ERROR CICLO {ciclo}: {e}\n{traceback.format_exc()}")
            try:
                _notif(f"🚨 *Error ciclo {ciclo}*\n`{str(e)[:200]}`")
            except Exception:
                pass

        log.info(f"Próximo ciclo en {config.LOOP_SECONDS}s")
        log.info("-" * 60)
        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    main()
