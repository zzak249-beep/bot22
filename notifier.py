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

# ══════════════════════════════════════════════════════════
# FUNCIONES ADICIONALES para main.py v6
# ══════════════════════════════════════════════════════════

def send_error(msg: str):
    """Alias de error_critico para main.py v6."""
    error_critico(msg)


def send_buy_signal(symbol: str, sig: dict, balance: float, executed: bool = True):
    """Notifica apertura de posición."""
    modo = "REAL" if executed else "SEÑAL MANUAL"
    action = sig.get("action", "buy")
    side_txt = "🟢 LONG" if action == "buy" else "🔴 SHORT"
    entry = sig.get("entry", 0)
    sl    = sig.get("sl", 0)
    tp    = sig.get("tp", 0)
    score = sig.get("score", 0)
    rsi   = sig.get("rsi", "N/A")
    atr   = sig.get("atr", 0)
    reason = sig.get("reason", "")
    strategy = sig.get("strategy", "?")
    rr    = sig.get("rr", 0)
    trend = sig.get("trend_4h", "N/A")

    risk   = abs(entry - sl) if sl else 0
    sl_pct = round(risk / entry * 100, 2) if entry > 0 else 0
    reward = abs(tp - entry) if tp else 0
    tp_pct = round(reward / entry * 100, 2) if entry > 0 else 0

    _send(
        f"{side_txt} *{modo}* — `{symbol}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entrada : `{entry}`\n"
        f"🛑 SL      : `{sl}` _(-{sl_pct}%)_\n"
        f"🎯 TP      : `{tp}` _(+{tp_pct}%)_\n"
        f"📐 R:R     : `{rr}x` | Score: `{score}/100`\n"
        f"📊 RSI: `{rsi}` | ATR: `{round(atr,4) if atr else 'N/A'}`\n"
        f"🧠 Estrategia: `{strategy}` | Tendencia: `{trend}`\n"
        f"💬 _{reason}_\n"
        f"💼 Balance: `${balance:.2f}`"
    )


def send_close_signal(symbol: str, entry: float, cur_price: float,
                      pnl_est: float, reason: str, executed: bool):
    """Notifica cierre de posición."""
    emoji  = "✅" if pnl_est >= 0 else "❌"
    modo   = "" if executed else " _(no ejecutado)_"
    change = (cur_price - entry) / entry * 100 if entry > 0 else 0
    _send(
        f"{emoji} *CIERRE{modo}* — `{symbol}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entrada  : `{entry}`\n"
        f"Salida   : `{cur_price}` _({change:+.2f}%)_\n"
        f"PnL est. : `${pnl_est:+.2f}`\n"
        f"Motivo   : `{reason}`"
    )


def send_startup(symbol_stats: dict):
    """Notifica arranque del bot."""
    total  = symbol_stats.get("total", 0)
    source = symbol_stats.get("source", "?")
    loaded = symbol_stats.get("loaded_at", "?")
    _send(
        f"🚀 *Bot ELITE v6 iniciado*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Pares cargados: `{total}` (desde {source})\n"
        f"🕐 A las: `{loaded}`\n"
        f"⚙️ Estrategias: BB_RSI + EMA_CROSS + BREAKOUT\n"
        f"🔧 Leverage: `{cfg.LEVERAGE}x` | Riesgo: `{cfg.RISK_PCT*100:.0f}%`\n"
        f"{'🧪 MODO DEMO' if cfg.MODO_DEMO else '💰 MODO REAL'}"
    )


def send_symbols_update(symbols: list):
    """Notifica actualización de lista de pares."""
    _send(f"🔄 *Pares actualizados*: `{len(symbols)}` pares cargados")


def send_param_update(updates: dict, perf_report: str):
    """Notifica ajustes del learner."""
    lines = "\n".join(f"  • `{k}`: {v}" for k, v in updates.items())
    _send(
        f"🧠 *Learner — ajuste de parámetros*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{lines}\n"
        f"📊 _{perf_report}_"
    )
