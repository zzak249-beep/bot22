"""
notifier.py — Notificaciones Telegram v6
Nuevas funciones: cierre_parcial, trailing activado
"""

import logging
import requests
import config as cfg

log = logging.getLogger("notifier")


def _send(text: str, parse_mode: str = "Markdown"):
    if not cfg.TELEGRAM_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado")
        return
    try:
        url  = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    cfg.TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": parse_mode,
        }, timeout=10)
        if not resp.ok:
            log.warning(f"Telegram error {resp.status_code}: {resp.text[:120]}")
    except Exception as e:
        log.error(f"Error enviando Telegram: {e}")


def send_raw(message: str):
    _send(message)


# ============================================================
# BOT INICIADO
# ============================================================

def bot_iniciado(pares: list, balance: float):
    modo = "DEMO 🧪" if cfg.MODO_DEMO else "REAL 💰"
    extras = []
    if cfg.TRAILING_STOP_ACTIVO:
        extras.append(f"Trailing:`{cfg.TRAILING_ATR_MULT}x ATR`")
    if cfg.CIERRE_PARCIAL_ACTIVO:
        extras.append(f"Cierre parcial:`{int(cfg.CIERRE_PARCIAL_PCT*100)}%`")
    if cfg.EMA_FILTRO_ACTIVO:
        extras.append(f"EMA:`{cfg.EMA_PERIODO}`")
    if cfg.MTF_ACTIVO:
        extras.append(f"MTF RSI<`{cfg.MTF_RSI_MAX}`")

    extras_str = " | ".join(extras)

    _send(
        f"🤖 *Bot iniciado — {modo}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: `${balance:.2f}`\n"
        f"📋 Pares: `{len(pares)}`\n"
        f"⚙️ RSI<`{cfg.RSI_OVERSOLD}` | SL:`{cfg.SL_ATR_MULT}x` | TP:`{cfg.TP_ATR_MULT}x` | Lev:`{cfg.LEVERAGE}x`\n"
        f"🔧 {extras_str}\n"
        f"🔁 Ciclo: cada `{cfg.CICLO_SEGUNDOS}s`"
    )


# ============================================================
# TRADE ABIERTO
# ============================================================

def trade_abierto(trade: dict):
    par     = trade.get("par", "?")
    entrada = trade.get("precio_entrada", 0)
    sl      = trade.get("sl", 0)
    tp      = trade.get("tp_original", trade.get("tp", 0))
    qty     = trade.get("cantidad", 0)
    rr      = trade.get("rr", 0)
    score   = trade.get("score", 0)
    div     = trade.get("divergencia", False)
    vol_r   = trade.get("vol_relativo", 1.0)
    mtf_rsi = trade.get("mtf_rsi", 50.0)
    modo    = "DEMO" if cfg.MODO_DEMO else "REAL"
    div_str = " ★DIV" if div else ""

    _send(
        f"📈 *LONG ABIERTO [{modo}]* — `{par}`{div_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Entrada  : `{entrada:.6f}`\n"
        f"🔴 SL       : `{sl:.6f}`\n"
        f"🟢 TP       : `{tp:.6f}`\n"
        f"📐 R:R      : `{rr:.2f}` | Score: `{score}/100`\n"
        f"📊 VolRel   : `{vol_r:.1f}x` | MTF RSI: `{mtf_rsi:.1f}`\n"
        f"🔢 Cantidad : `{qty}`"
    )


# ============================================================
# CIERRE PARCIAL
# ============================================================

def cierre_parcial(par: str, precio_entrada: float, precio_parcial: float,
                   pnl_parcial: float, cantidad_restante: float):
    emoji = "✅" if pnl_parcial >= 0 else "❌"
    _send(
        f"{emoji} *CIERRE PARCIAL* — `{par}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entrada   : `{precio_entrada:.6f}`\n"
        f"Salida 50%: `{precio_parcial:.6f}`\n"
        f"PnL       : `${pnl_parcial:+.4f}`\n"
        f"Restante  : `{cantidad_restante}` contratos\n"
        f"🔒 SL movido a _breakeven_"
    )


# ============================================================
# TRADE CERRADO
# ============================================================

def trade_cerrado(trade: dict, pnl_usd: float, motivo: str, balance: float):
    par     = trade.get("par", "?")
    entrada = trade.get("precio_entrada", 0)
    salida  = trade.get("precio_salida", 0)
    emoji   = "✅" if pnl_usd >= 0 else "❌"

    extras = []
    if trade.get("parcial_cerrado"):
        parcial = trade.get("pnl_parcial_usd", 0)
        extras.append(f"parcial `${parcial:+.4f}`")
    if trade.get("trailing_activo"):
        extras.append("trailing stop")
    extras_str = f"\n⚙️ _{', '.join(extras)}_" if extras else ""

    _send(
        f"{emoji} *CIERRE* — `{par}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entrada  : `{entrada:.6f}`\n"
        f"Salida   : `{salida:.6f}`\n"
        f"PnL total: `${pnl_usd:+.4f}`\n"
        f"Motivo   : `{motivo}`\n"
        f"Balance  : `${balance:.2f}`"
        f"{extras_str}"
    )


# ============================================================
# CIRCUIT BREAKER
# ============================================================

def circuit_breaker(motivo: str, balance: float):
    _send(
        f"🛑 *CIRCUIT BREAKER ACTIVADO*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Motivo  : `{motivo}`\n"
        f"Balance : `${balance:.2f}`\n"
        f"⏸️ Bot pausado 1 hora"
    )


# ============================================================
# ERROR CRÍTICO
# ============================================================

def error_critico(mensaje: str):
    _send(f"🚨 *ERROR CRÍTICO*\n`{mensaje[:300]}`")


# ============================================================
# LEARNER AJUSTE
# ============================================================

def learner_ajuste(par: str, accion: str, motivo: str):
    emoji = "⛔" if accion == "PENALIZAR" else "✅"
    _send(
        f"{emoji} *Learner — {accion}*\n"
        f"Par    : `{par}`\n"
        f"Motivo : _{motivo}_"
    )


# ============================================================
# REPORTE DE ESTADO HORARIO
# ============================================================

def send_status(positions: list, balance: float, stats: dict, perf: str):
    pos_lines = ""
    for p in positions:
        diff = p.get("current", p["entry"]) - p["entry"]
        pct  = diff / p["entry"] * 100 if p["entry"] else 0
        emoji = "🟢" if diff >= 0 else "🔴"
        pos_lines += f"  {emoji} `{p['symbol']}` e:{p['entry']:.6f} ({pct:+.2f}%)\n"
    if not pos_lines:
        pos_lines = "  _(sin posiciones)_\n"

    wins   = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    total  = wins + losses
    wr     = f"{wins/total*100:.1f}%" if total > 0 else "N/A"

    _send(
        f"📊 *Reporte horario*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance : `${balance:.2f} USDT`\n"
        f"📈 Sesión  : `{wins}W / {losses}L` | WR: `{wr}`\n"
        f"💹 PnL hoy : `${stats.get('pnl_today',0):+.2f}` | Total: `${stats.get('pnl_total',0):+.2f}`\n"
        f"📋 Posiciones:\n{pos_lines}"
        f"🧠 Learner : _{perf}_"
    )
