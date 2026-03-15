"""
main_bellsz.py — Bellsz Bot v2.0 [Liquidez Lateral]
Loop principal 24/7 para BingX Perpetual Futures.
"""

import sys, os, time, traceback
from datetime import datetime, date, timezone
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    stream=sys.stdout, force=True,
)
if os.getenv("LOG_LEVEL","").upper() == "DEBUG":
    logging.getLogger().setLevel(logging.DEBUG)

log = logging.getLogger("main")
log.info("=== ARRANQUE BELLSZ BOT v2.0 ===")

try:
    import config, exchange, memoria, scanner_pares, metaclaw
    from config_pares import PARES as PARES_FIJOS
    import analizar_bellsz as analizar
except Exception as e:
    log.error(f"ERROR importando módulos: {e}\n{traceback.format_exc()}")
    sys.exit(1)

try:
    import optimizador
    _opt_ok = True
    log.info("✅ optimizador cargado")
except Exception:
    _opt_ok = False

for err in config.validar():
    log.warning(f"⚠️  CONFIG: {err}")


# ══════════════════════════════════════════════════════
# CORRELACIÓN
# ══════════════════════════════════════════════════════

GRUPOS_CORR = [
    {"BTC-USDT","ETH-USDT"},
    {"SOL-USDT","APT-USDT"},
    {"AVAX-USDT","SUI-USDT"},
    {"ARB-USDT","OP-USDT"},
    {"DOGE-USDT","SHIB-USDT","PEPE-USDT","WIF-USDT","BONK-USDT"},
    {"AAVE-USDT","UNI-USDT","MKR-USDT"},
    {"BNB-USDT","TRX-USDT"},
]

def hay_correlacion(par, lado, posiciones):
    if not config.CORRELACION_ACTIVO:
        return False
    for grupo in GRUPOS_CORR:
        if par not in grupo:
            continue
        for p, pos in posiciones.items():
            if p in grupo and p != par and pos["lado"] == lado:
                return True
    return False


# ══════════════════════════════════════════════════════
# ESTADO
# ══════════════════════════════════════════════════════

class Estado:
    def __init__(self):
        self.posiciones = {}
        self.pnl_hoy    = 0.0
        self.dia_actual = str(date.today())
        self.wins = self.losses = 0

    def reset_diario(self):
        hoy = str(date.today())
        if hoy != self.dia_actual:
            self.dia_actual = hoy
            self.pnl_hoy    = 0.0
            log.info(f"[RESET] Nuevo día {hoy}")

    def registrar_cierre(self, pnl):
        self.pnl_hoy += pnl
        if pnl > 0: self.wins   += 1
        else:       self.losses += 1

    def max_perdida(self):
        return config.MAX_PERDIDA_DIA > 0 and self.pnl_hoy <= -config.MAX_PERDIDA_DIA


estado = Estado()


# ══════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════

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


def _notif_entrada(s, trade_usdt, estado_ej):
    lado  = "🟢 LONG" if s["lado"] == "LONG" else "🔴 SHORT"
    ex    = "✅ *Ejecutado*" if estado_ej == "ok" else f"⚠️ `{estado_ej}`"
    motiv = " + ".join(s.get("motivos", [])[:5])
    extra = ""
    if s.get("purga_nivel"):
        extra += f"💧 *Purga* `{s['purga_nivel']}` (peso={s.get('purga_peso',0)})\n"
    if s.get("ob_fvg_bull") or s.get("ob_fvg_bear"):
        extra += "🏆 `OB + FVG`\n"
    if s.get("sweep_bull") or s.get("sweep_bear"):
        extra += "🌊 `Liquidity Sweep`\n"
    if s.get("choch_bull") or s.get("choch_bear"):
        extra += "🔄 `CHoCH`\n"
    _notif(
        f"{lado} — `{s['par']}` [{s.get('kz','')}]\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entrada : `{s['precio']:.6f}`\n"
        f"🔶 TP1     : `{s['tp1']:.6f}` (50%)\n"
        f"✅ TP2     : `{s['tp']:.6f}`\n"
        f"🛑 SL      : `{s['sl']:.6f}`\n"
        f"📊 R:R     : `{s['rr']:.2f}x`\n"
        f"🏅 Score   : `{s['score']}`\n"
        f"📉 RSI     : `{s['rsi']:.1f}`\n"
        f"🧩 Señales : `{motiv}`\n"
        f"{extra}"
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
        f"💰 Pool: `${comp['ganancias']:.2f}`\n"
        f"📈 Próx: `${memoria.get_trade_amount():.2f} USDT`"
    )


# ══════════════════════════════════════════════════════
# ARRANQUE — RECUPERAR POSICIONES
# ══════════════════════════════════════════════════════

def cargar_posiciones():
    if config.MODO_DEMO:
        return
    try:
        pos_reales = exchange.get_posiciones_abiertas()
        cargadas   = 0
        for p in pos_reales:
            amt = float(p.get("positionAmt", 0) or 0)
            if amt == 0:
                continue
            sym = p.get("symbol","")
            par = sym if "-" in sym else sym.replace("USDT","-USDT")
            if par in estado.posiciones:
                continue
            lado  = "LONG" if amt > 0 else "SHORT"
            entry = float(p.get("entryPrice", 0) or 0)
            qty   = abs(amt)
            if entry <= 0 or qty <= 0:
                continue
            estado.posiciones[par] = {
                "lado": lado, "entrada": entry, "qty": qty,
                "sl": float(p.get("stopLoss",0) or 0),
                "tp": float(p.get("takeProfit",0) or 0),
                "tp1": 0.0, "atr": 0.0,
                "sl_trailing": float(p.get("stopLoss",0) or 0),
                "tp1_hit": False,
                "ts": datetime.now(timezone.utc).isoformat(),
                "recuperada": True, "trade_usdt": config.TRADE_USDT_BASE,
            }
            cargadas += 1
            log.info(f"[ARRANQUE] ♻️ {lado} {par} @ {entry:.6f}")
        if cargadas:
            _notif(f"♻️ *{cargadas} posición(es) recuperada(s)*")
    except Exception as e:
        log.error(f"[ARRANQUE] {e}")


# ══════════════════════════════════════════════════════
# SINCRONIZACIÓN
# ══════════════════════════════════════════════════════

def sincronizar_posiciones():
    if not estado.posiciones or config.MODO_DEMO:
        return
    try:
        pos_reales = exchange.get_posiciones_abiertas()
        reales = set()
        for p in pos_reales:
            s = p.get("symbol","")
            reales.add(s)
            reales.add(s.replace("-",""))
            if "USDT" in s and "-" not in s:
                reales.add(s.replace("USDT","-USDT"))

        cerradas = [par for par in estado.posiciones
                    if par not in reales and par.replace("-","") not in reales]

        for par in cerradas:
            pos    = estado.posiciones[par]
            lado   = pos["lado"]
            precio = exchange.get_precio(par)
            sl_ef  = pos.get("sl_trailing", pos["sl"])
            tp     = pos["tp"]

            if sl_ef > 0 and tp > 0:
                if lado == "LONG":
                    salida = tp if precio >= tp * 0.98 else sl_ef
                else:
                    salida = tp if precio <= tp * 1.02 else sl_ef
                razon = "TP-BINGX" if salida == tp else "SL-BINGX"
            else:
                salida, razon = precio, "BINGX"

            pnl = pos["qty"] * ((salida - pos["entrada"]) if lado == "LONG" else (pos["entrada"] - salida))
            estado.registrar_cierre(pnl)
            memoria.registrar_resultado(par, pnl, lado, kz=pos.get("kz",""), motivos=pos.get("motivos",[]))
            try: analizar.registrar_trade_kz(pos.get("kz","FUERA"), pnl > 0)
            except Exception: pass
            del estado.posiciones[par]
            _notif_cierre(par, lado, pos["entrada"], salida, pnl, razon)
            log.info(f"[SYNC] {par} cerrado ({razon}) PnL≈{pnl:+.4f}")
    except Exception as e:
        log.error(f"[SYNC] {e}")


# ══════════════════════════════════════════════════════
# GESTIÓN DE POSICIONES
# ══════════════════════════════════════════════════════

def gestionar_partial_tp(par, pos, precio):
    if not config.PARTIAL_TP_ACTIVO or pos.get("tp1_hit"):
        return
    tp1  = pos.get("tp1", 0)
    lado = pos["lado"]
    if tp1 <= 0:
        return
    if not ((precio >= tp1) if lado == "LONG" else (precio <= tp1)):
        return

    score = pos.get("score", 0)
    pct   = 0.25 if score >= 12 else (0.30 if score >= 9 else 0.50)
    qty1  = round(pos["qty"] * pct, 6)

    salida = precio
    if not config.MODO_DEMO:
        res    = exchange.cerrar_posicion(par, qty1, lado)
        salida = (res or {}).get("precio_salida", precio) or precio

    pnl = qty1 * ((salida - pos["entrada"]) if lado == "LONG" else (pos["entrada"] - salida))
    estado.pnl_hoy += pnl
    memoria.registrar_ganancia_compounding(pnl)

    be         = pos["entrada"] * (1.0005 if lado == "LONG" else 0.9995)
    pos["sl"]  = pos["sl_trailing"] = be
    pos["qty"] = round(pos["qty"] - qty1, 6)
    pos["tp1_hit"] = True

    log.info(f"[TP1] {par} {pct*100:.0f}% @ {salida:.6f} PnL={pnl:+.4f} BE→{be:.6f}")
    _notif(f"🔶 *TP1* — `{par}` {lado}\n`{pct*100:.0f}%` @ `{salida:.6f}` | `${pnl:+.4f}`\n🔄 SL→BE `{be:.6f}`")


def actualizar_trailing(par, pos, precio):
    if not config.TRAILING_ACTIVO:
        return
    atr  = pos.get("atr", 0)
    lado = pos["lado"]
    if atr <= 0:
        return
    act_d = atr * max(config.TRAILING_ACTIVAR, 1.5)
    tr_d  = atr * max(config.TRAILING_DISTANCIA, 1.0)

    if lado == "LONG":
        if precio - pos["entrada"] < act_d:
            return
        nuevo  = precio - tr_d
        actual = pos.get("sl_trailing", pos["sl"])
        if nuevo > actual:
            pos["sl_trailing"] = nuevo
            exchange.actualizar_sl_bingx(par, nuevo, lado)
    else:
        if pos["entrada"] - precio < act_d:
            return
        nuevo  = precio + tr_d
        actual = pos.get("sl_trailing", pos["sl"])
        if nuevo < actual:
            pos["sl_trailing"] = nuevo
            exchange.actualizar_sl_bingx(par, nuevo, lado)


def check_time_exit(par, pos):
    ts_str = pos.get("ts","")
    if not ts_str:
        return False
    try:
        ts    = datetime.fromisoformat(ts_str)
        ahora = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        horas = (ahora - ts).total_seconds() / 3600
        if horas < 2.0 or pos.get("tp1_hit"):
            return False
        precio  = exchange.get_precio(par)
        entrada = pos["entrada"]
        return abs(precio - entrada) / entrada * 100 < 0.15
    except Exception:
        return False


def gestionar_posiciones():
    for par, pos in list(estado.posiciones.items()):
        try:
            precio = exchange.get_precio(par)
            if precio <= 0:
                continue
            lado = pos["lado"]
            qty  = pos["qty"]

            # Verificar si BingX ya cerró la posición
            if not config.MODO_DEMO and not pos.get("recuperada"):
                try:
                    pos_reales = exchange.get_posiciones_abiertas()
                    syms = {p.get("symbol","") for p in pos_reales}
                    syms |= {s.replace("-","") for s in syms}
                    if par not in syms and par.replace("-","") not in syms:
                        sl_ef = pos.get("sl_trailing", pos["sl"])
                        tp    = pos["tp"]
                        if tp > 0 and sl_ef > 0:
                            salida = tp if ((lado=="LONG" and precio>=tp*0.98) or (lado=="SHORT" and precio<=tp*1.02)) else sl_ef
                        else:
                            salida = precio
                        razon = "TP-BINGX" if salida == tp else "SL-BINGX"
                        pnl   = qty * ((salida-pos["entrada"]) if lado=="LONG" else (pos["entrada"]-salida))
                        estado.registrar_cierre(pnl)
                        memoria.registrar_resultado(par, pnl, lado, kz=pos.get("kz",""), motivos=pos.get("motivos",[]))
                        del estado.posiciones[par]
                        _notif_cierre(par, lado, pos["entrada"], salida, pnl, razon)
                        log.info(f"[GES-SYNC] {par} cerrado en BingX ({razon}) PnL={pnl:+.4f}")
                        continue
                except Exception as e:
                    log.debug(f"[GES-SYNC] {par}: {e}")

            gestionar_partial_tp(par, pos, precio)

            # Time exit (sin movimiento)
            if check_time_exit(par, pos):
                res    = exchange.cerrar_posicion(par, qty, lado)
                salida = (res or {}).get("precio_salida", precio) or precio
                pnl    = qty * ((salida-pos["entrada"]) if lado=="LONG" else (pos["entrada"]-salida))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado, kz=pos.get("kz",""), motivos=pos.get("motivos",[]))
                del estado.posiciones[par]
                _notif_cierre(par, lado, pos["entrada"], salida, pnl, "SIN-MOVIMIENTO")
                continue

            # Time exit (máx horas)
            try:
                ts    = datetime.fromisoformat(pos.get("ts",""))
                ahora = datetime.now(timezone.utc)
                if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
                if (ahora-ts).total_seconds()/3600 >= config.TIME_EXIT_HORAS:
                    res    = exchange.cerrar_posicion(par, qty, lado)
                    salida = (res or {}).get("precio_salida", precio) or precio
                    pnl    = qty * ((salida-pos["entrada"]) if lado=="LONG" else (pos["entrada"]-salida))
                    estado.registrar_cierre(pnl)
                    memoria.registrar_resultado(par, pnl, lado, kz=pos.get("kz",""), motivos=pos.get("motivos",[]))
                    del estado.posiciones[par]
                    _notif_cierre(par, lado, pos["entrada"], salida, pnl, "TIME")
                    continue
            except Exception:
                pass

            actualizar_trailing(par, pos, precio)

            sl_ef  = pos.get("sl_trailing", pos["sl"])
            tp     = pos["tp"]
            sl_hit = (precio <= sl_ef) if lado == "LONG" else (precio >= sl_ef)
            tp_hit = (precio >= tp)    if lado == "LONG" else (precio <= tp)

            if sl_hit or tp_hit:
                razon  = ("TRAIL-SL" if pos.get("tp1_hit") else "SL") if sl_hit else "TP2"
                salida = sl_ef if sl_hit else tp
                res    = exchange.cerrar_posicion(par, qty, lado)
                salida = (res or {}).get("precio_salida", salida) or salida
                pnl    = qty * ((salida-pos["entrada"]) if lado=="LONG" else (pos["entrada"]-salida))
                estado.registrar_cierre(pnl)
                memoria.registrar_resultado(par, pnl, lado, kz=pos.get("kz",""), motivos=pos.get("motivos",[]))
                try: analizar.registrar_trade_kz(pos.get("kz","FUERA"), pnl > 0)
                except Exception: pass
                del estado.posiciones[par]
                log.info(f"CIERRE {lado} {par} @ {salida:.6f} PnL={pnl:+.4f} ({razon})")
                _notif_cierre(par, lado, pos["entrada"], salida, pnl, razon, pos.get("trade_usdt", config.TRADE_USDT_BASE))

        except Exception as e:
            log.error(f"gestionar {par}: {e}")
        time.sleep(0.2)


# ══════════════════════════════════════════════════════
# EJECUTAR SEÑAL
# ══════════════════════════════════════════════════════

def _pos_bot_count():
    return sum(1 for v in estado.posiciones.values() if not v.get("recuperada", False))


def _streak_mult():
    trades = memoria._data.get("trades",[])[-5:]
    if len(trades) < 3: return 1.0
    wins = sum(1 for t in trades if t.get("ganado"))
    if wins >= 4: return 1.4
    if wins >= 3: return 1.2
    if (len(trades)-wins) >= 4: return 0.6
    if (len(trades)-wins) >= 3: return 0.8
    return 1.0


def ejecutar_senal(s: dict) -> str:
    par  = s["par"]
    lado = s["lado"]

    if par in estado.posiciones:
        return "skip"

    # Anti-hedge real
    if not config.MODO_DEMO:
        try:
            pos_reales = exchange.get_posiciones_abiertas()
            for p in pos_reales:
                sym      = p.get("symbol","")
                par_norm = sym if "-" in sym else sym.replace("USDT","-USDT")
                if par_norm == par and float(p.get("positionAmt",0) or 0) != 0:
                    return "skip"
        except Exception:
            pass

    if par in {p for p in estado.posiciones} or hay_correlacion(par, lado, estado.posiciones):
        return "bloq:correlacion"
    if memoria.esta_bloqueado(par):
        return "bloq:par bloqueado"

    # MetaClaw
    if config.METACLAW_ACTIVO and os.getenv("ANTHROPIC_API_KEY"):
        try:
            mc = metaclaw.validar(s)
            if mc.get("metaclaw_activo") and not mc.get("aprobar", True) and mc.get("confianza",5) >= config.METACLAW_VETO_MINIMO:
                return f"bloq:MetaClaw {mc.get('razon','')[:40]}"
        except Exception:
            pass

    if _pos_bot_count() >= config.MAX_POSICIONES:
        return f"bloq:MAX_POSICIONES"
    if estado.max_perdida():
        return f"bloq:circuit-breaker"

    balance      = exchange.get_balance()
    margen_libre = exchange.get_available_margin()

    if balance > 0 and not config.MODO_DEMO:
        if (balance - margen_libre) / balance * 100 > 75:
            return "bloq:exposición alta"
    if margen_libre < max(config.TRADE_USDT_BASE / config.LEVERAGE * 1.3, 2.0) and not config.MODO_DEMO:
        return f"bloq:margen libre insuficiente (${margen_libre:.2f})"

    trade_usdt = round(min(memoria.get_trade_amount() * _streak_mult(), config.TRADE_USDT_MAX), 2)

    # Sizing por purga_peso
    pp = s.get("purga_peso", 0)
    if pp >= 3: mult = 2.0
    elif pp >= 2: mult = 1.5
    else: mult = 1.0
    if mult > 1.0:
        trade_usdt = round(min(trade_usdt * mult, config.TRADE_USDT_MAX, balance * 0.15), 2)

    qty = exchange.calcular_cantidad(par, trade_usdt, s["precio"])
    if qty <= 0:
        return f"bloq:qty=0"

    if lado == "LONG":
        res = exchange.abrir_long(par, qty, s["precio"], s["sl"], s["tp"])
    else:
        res = exchange.abrir_short(par, qty, s["precio"], s["sl"], s["tp"])

    if not res or "error" in res:
        err = (res or {}).get("error","respuesta vacía")
        memoria.registrar_error_api(par)
        return f"error:{err[:80]}"

    entrada_real = float(res.get("fill_price",0) or 0) or exchange.get_precio(par) or s["precio"]
    qty_real     = float(res.get("executedQty", qty) or qty)
    ratio        = entrada_real / s["precio"] if s["precio"] > 0 else 1.0
    dist         = s.get("dist_sl",0) * ratio
    atr          = s.get("atr",0)

    if dist > 0:
        sl_r  = (entrada_real - dist) if lado=="LONG" else (entrada_real + dist)
        tp_r  = (entrada_real + dist*config.TP_DIST_MULT)  if lado=="LONG" else (entrada_real - dist*config.TP_DIST_MULT)
        tp1_r = (entrada_real + dist*config.TP1_DIST_MULT) if lado=="LONG" else (entrada_real - dist*config.TP1_DIST_MULT)
    elif atr > 0:
        sl_r  = (entrada_real - atr*config.SL_ATR_MULT) if lado=="LONG" else (entrada_real + atr*config.SL_ATR_MULT)
        tp_r  = (entrada_real + atr*config.TP_DIST_MULT) if lado=="LONG" else (entrada_real - atr*config.TP_DIST_MULT)
        tp1_r = (entrada_real + atr*config.TP1_DIST_MULT) if lado=="LONG" else (entrada_real - atr*config.TP1_DIST_MULT)
    else:
        sl_r  = s["sl"] * ratio
        tp_r  = s["tp"] * ratio
        tp1_r = s["tp1"] * ratio

    memoria.registrar_inversion(trade_usdt)
    estado.posiciones[par] = {
        "lado": lado, "entrada": entrada_real, "qty": qty_real,
        "sl": sl_r, "tp": tp_r, "tp1": tp1_r, "atr": atr,
        "sl_trailing": sl_r, "tp1_hit": False,
        "ts": datetime.now(timezone.utc).isoformat(),
        "recuperada": False, "score": s["score"],
        "motivos": s.get("motivos",[]), "kz": s.get("kz",""),
        "trade_usdt": trade_usdt, "purga_nivel": s.get("purga_nivel",""),
        "purga_peso": s.get("purga_peso",0),
    }

    log.info(
        f"✅ {lado} {par} fill:{entrada_real:.6f} "
        f"${trade_usdt:.2f}×{config.LEVERAGE}x "
        f"SL:{sl_r:.6f} TP:{tp_r:.6f} "
        f"score:{s['score']} purga:{s.get('purga_nivel','')}"
    )
    return "ok"


# ══════════════════════════════════════════════════════
# REPORTE
# ══════════════════════════════════════════════════════

def enviar_reporte(balance):
    pos_txt = ""
    for par, pos in estado.posiciones.items():
        p = exchange.get_precio(par)
        pnl_est = pos["qty"] * ((p - pos["entrada"]) if pos["lado"]=="LONG" else (pos["entrada"] - p)) if p > 0 else 0
        ico     = "🟢" if pos["lado"] == "LONG" else "🔴"
        fase    = "🔶→TP2" if pos.get("tp1_hit") else "▶️→TP1"
        pos_txt += f"  {ico} `{par}` est:`${pnl_est:+.2f}` {fase} [{pos.get('score','?')}]\n"
    if not pos_txt:
        pos_txt = "  _(sin posiciones)_\n"
    w, l = estado.wins, estado.losses
    wr   = f"{w/(w+l)*100:.1f}%" if (w+l) > 0 else "N/A"
    comp = memoria._data["compounding"]
    kz   = analizar.en_killzone()
    _notif(
        f"📊 *Reporte — {config.VERSION}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance  : `${balance:.2f} USDT`\n"
        f"📈 Sesión   : `{w}W / {l}L` WR:`{wr}`\n"
        f"PnL hoy     : `${estado.pnl_hoy:+.4f}` USDT\n"
        f"🕐 Killzone : `{kz['nombre']}`\n"
        f"💵 Trade    : `${config.TRADE_USDT_BASE:.0f}` × {config.LEVERAGE}x\n"
        f"📊 Próx     : `${memoria.get_trade_amount():.2f}`\n"
        f"💹 Pool     : `${comp['ganancias']:.2f}` USDT\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Posiciones:\n{pos_txt}"
    )


# ══════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════

def main():
    global _opt_ok
    log.info("=" * 65)
    log.info(f"  {config.VERSION}")
    log.info(f"  TRADE: ${config.TRADE_USDT_BASE} × {config.LEVERAGE}x | MAX: ${config.TRADE_USDT_MAX}")
    log.info(f"  TF:{config.TIMEFRAME} | HTF:H1/H4/D | LIQ_MARGEN:{config.LIQ_MARGEN}% | LOOKBACK:{config.LIQ_LOOKBACK}")
    log.info(f"  SCORE≥{config.SCORE_MIN} | MIN_RR:{config.MIN_RR} | TP={config.TP_DIST_MULT}×dist_SL")
    log.info(f"  EMA:{config.EMA_FAST}/{config.EMA_SLOW} | RSI:{config.RSI_SELL_MIN}/{config.RSI_BUY_MAX}")
    log.info(f"  MetaClaw: {'✅' if config.METACLAW_ACTIVO else '❌'} | MODO: {'🟡 DEMO' if config.MODO_DEMO else '🔴 LIVE'}")
    log.info(f"  WORKERS:{config.ANALISIS_WORKERS} | LOOP:{config.LOOP_SECONDS}s | MAX_POS:{config.MAX_POSICIONES}")
    log.info("=" * 65)

    import pathlib
    pathlib.Path(config.MEMORY_DIR).mkdir(parents=True, exist_ok=True)

    log.info("Sincronizando tiempo BingX...")
    exchange.sync_server_time()
    exchange.diagnostico_balance()

    balance = exchange.get_balance()
    log.info(f"Balance: ${balance:.2f} USDT")

    if _opt_ok:
        try:
            optimizador.iniciar()
        except Exception as e:
            log.warning(f"[OPT] optimizador.iniciar falló: {e}")
            _opt_ok = False

    try:
        exchange._cargar_contratos()
        log.info(f"Contratos: {len(exchange._CONTRATOS_FUTURES)} pares")
    except Exception as e:
        log.warning(f"[STARTUP] contratos: {e}")

    cargar_posiciones()

    # Construir lista de pares con filtro de volumen Y futuros válidos
    try:
        pares_raw = scanner_pares.get_pares_cached(config.VOLUMEN_MIN_24H)
    except Exception:
        from config_pares import PARES as pares_raw

    bloq_cfg    = set(config.PARES_BLOQUEADOS)
    futuros_ok  = exchange._CONTRATOS_FUTURES
    pares_todos = [p for p in pares_raw if p not in bloq_cfg and (not futuros_ok or p in futuros_ok)]
    prioritarios = [p for p in config.PARES_PRIORITARIOS if p in set(pares_todos)]
    top_mem      = [p for p in memoria.get_top_pares(10) if p in set(pares_todos)]
    resto        = [p for p in pares_todos if p not in set(prioritarios) and p not in set(top_mem)]
    pares        = (prioritarios + top_mem + resto)[:config.MAX_PARES_SCAN]

    log.info(f"Pares: {len(pares)} ({len(prioritarios)} prioritarios)")

    _notif(
        f"🤖 *{config.VERSION}* arrancado\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance : `${balance:.2f} USDT`\n"
        f"💵 Trade   : `${config.TRADE_USDT_BASE:.0f}` × {config.LEVERAGE}x\n"
        f"📊 Pares   : `{len(pares)}`\n"
        f"💧 Estrategia: *Liquidez Lateral [Bellsz]*\n"
        f"🎯 Núcleo  : Purgas BSL/SSL H1+H4+Diario\n"
        f"🏅 Score≥`{config.SCORE_MIN}` | R:R `{config.MIN_RR}x`\n"
        f"🔴 *LIVE — DINERO REAL — 24/7*"
    )

    ciclo           = 0
    last_reporte    = time.time()
    last_scan_pares = time.time()

    while True:
        try:
            ciclo += 1
            estado.reset_diario()
            balance = exchange.get_balance()
            kz      = analizar.en_killzone()

            try: analizar.actualizar_macro_btc()
            except Exception: pass

            log.info(
                f"Ciclo {ciclo} | {datetime.now(timezone.utc).strftime('%H:%M UTC')} | "
                f"Bal:${balance:.2f} | Pos:{_pos_bot_count()} | "
                f"PnL:${estado.pnl_hoy:+.4f} | KZ:{kz['nombre']} | "
                f"Trade:${memoria.get_trade_amount():.2f}"
            )

            # Refrescar pares cada hora
            if time.time() - last_scan_pares > 3600:
                try:
                    nuevos   = scanner_pares.get_pares_cached(config.VOLUMEN_MIN_24H)
                    nuevos   = [p for p in nuevos if p not in bloq_cfg and (not futuros_ok or p in futuros_ok)]
                    bloq_m   = set(memoria.get_pares_bloqueados())
                    top_m    = [p for p in memoria.get_top_pares(10) if p in set(nuevos)]
                    resto_n  = [p for p in nuevos if p not in set(top_m) and p not in bloq_m]
                    pares    = (prioritarios + top_m + resto_n)[:config.MAX_PARES_SCAN]
                    log.info(f"Pares actualizados: {len(pares)}")
                    last_scan_pares = time.time()
                except Exception as e:
                    log.warning(f"[SCAN] {e}")

            if estado.max_perdida():
                log.warning(f"🛑 Máx pérdida diaria ${estado.pnl_hoy:.2f} — pausa 30min")
                _notif(f"🛑 *Máx pérdida diaria* `${estado.pnl_hoy:.2f}`\nPausa 30 min.")
                time.sleep(1800)
                continue

            sincronizar_posiciones()

            if estado.posiciones:
                gestionar_posiciones()
                balance = exchange.get_balance()

            if _pos_bot_count() < config.MAX_POSICIONES:
                bloq_ahora = set(memoria.get_pares_bloqueados())
                pares_scan = [p for p in pares if p not in estado.posiciones and p not in bloq_ahora]

                log.info(
                    f"Escaneando {len(pares_scan)} pares | "
                    f"Score≥{config.SCORE_MIN} | KZ:{kz['nombre']} | "
                    f"Pos:{_pos_bot_count()}/{config.MAX_POSICIONES}"
                )
                senales = analizar.analizar_todos(pares_scan, workers=config.ANALISIS_WORKERS)

                if senales:
                    log.info(f"✓ {len(senales)} señal(es):")
                    for s in senales[:5]:
                        log.info(f"  {s['lado']:5s} {s['par']:15s} score={s['score']} purga={s.get('purga_nivel','?')} RSI={s['rsi']:.1f} R:R={s['rr']:.2f}")
                else:
                    log.info("Sin señales este ciclo")

                for s in senales:
                    if _pos_bot_count() >= config.MAX_POSICIONES:
                        break
                    if s["par"] in estado.posiciones:
                        continue

                    s["score"] = memoria.ajustar_score(s["par"], s["score"], kz=s.get("kz",""), motivos=s.get("motivos",[]))
                    if s["score"] < config.SCORE_MIN:
                        continue

                    if not exchange.par_es_soportado(s["par"]):
                        continue

                    resultado = ejecutar_senal(s)

                    if resultado == "skip":
                        continue
                    if resultado and resultado.startswith("error:"):
                        log.error(f"[API-ERR] {s['par']}: {resultado[6:]}")
                        if "margin" in resultado.lower() or "insufficient" in resultado.lower():
                            continue
                        _notif(f"🚨 *Orden fallida {s['lado']} `{s['par']}`*\n`{resultado[6:80]}`")
                        continue
                    if resultado and any(x in resultado for x in ("MAX_POSICIONES","exposición","margen","correlacion")):
                        log.info(f"[SKIP] {s['par']} — {resultado}")
                        continue

                    _notif_entrada(s, memoria.get_trade_amount(), resultado)
                    if resultado == "ok":
                        balance = exchange.get_balance()
                        time.sleep(2)

            if time.time() - last_reporte >= 3600:
                enviar_reporte(balance)
                _notif(memoria.resumen())
                last_reporte = time.time()

        except KeyboardInterrupt:
            log.info("Detenido")
            _notif("🛑 *Bellsz Bot detenido.*")
            break
        except Exception as e:
            log.error(f"ERROR ciclo {ciclo}: {e}\n{traceback.format_exc()}")
            try: _notif(f"🚨 *Error ciclo {ciclo}*\n`{str(e)[:200]}`")
            except Exception: pass
            time.sleep(10)  # pausa breve antes de reintentar
            continue

        log.info(f"Próximo ciclo en {config.LOOP_SECONDS}s")
        log.info("-" * 60)
        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            log.info("Bot detenido por el usuario.")
            break
        except Exception as e:
            log.error(f"[CRASH] main() crasheó: {e}\n{traceback.format_exc()}")
            try:
                import requests as _rq
                tok = os.getenv("TELEGRAM_TOKEN","").strip()
                cid = os.getenv("TELEGRAM_CHAT_ID","").strip()
                if tok and cid:
                    _rq.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                             json={"chat_id": cid, "text": f"⚠️ Bot reiniciando tras crash:\n`{str(e)[:200]}`",
                                   "parse_mode":"Markdown"}, timeout=8)
            except Exception:
                pass
            log.info("Reiniciando en 15 segundos...")
            time.sleep(15)
