"""
notifier.py — Notificaciones Telegram Elite v4
FIX: función _esc() escapa guiones bajos y caracteres especiales
     que rompen el parseado Markdown de Telegram (Error 400).
     send_startup acepta dict O string.
"""
import logging
import os
import requests
import config as cfg

log = logging.getLogger("notifier")


def _esc(text: str) -> str:
    """Escapa caracteres especiales de Markdown en valores dinámicos."""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def _get_tg_token():
    return (getattr(cfg, "TELEGRAM_TOKEN", None)
            or os.environ.get("TELEGRAM_TOKEN")
            or os.environ.get("TG_TOKEN"))


def _get_tg_chat():
    return (getattr(cfg, "TELEGRAM_CHAT_ID", None)
            or os.environ.get("TELEGRAM_CHAT_ID")
            or os.environ.get("TG_CHAT_ID"))


def _send(text: str, parse_mode="Markdown"):
    token   = _get_tg_token()
    chat_id = _get_tg_chat()
    if not token or not chat_id:
        log.warning("Telegram no configurado (TG_TOKEN / TG_CHAT_ID)")
        return
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": parse_mode,
        }, timeout=10)
        if not resp.ok:
            log.warning(f"Telegram error {resp.status_code}: {resp.text[:120]}")
    except Exception as e:
        log.error(f"Error enviando Telegram: {e}")


def send_raw(message: str):
    _send(message)


def send_startup(symbol_stats=None):
    rsi_l    = getattr(cfg, "RSI_LONG",                getattr(cfg, "RSI_OB", "N/A"))
    rsi_s    = getattr(cfg, "RSI_SHORT",               getattr(cfg, "RSI_OB", "N/A"))
    cb_loss  = getattr(cfg, "CB_MAX_DAILY_LOSS_PCT",   0.05)
    cb_cons  = getattr(cfg, "CB_MAX_CONSECUTIVE_LOSS", 5)
    loop_s   = getattr(cfg, "LOOP_SECONDS",   "N/A")
    max_pos  = getattr(cfg, "MAX_POSITIONS",  "N/A")
    sl_atr   = getattr(cfg, "SL_ATR",         "N/A")
    bb_sigma = getattr(cfg, "BB_SIGMA",       "N/A")
    leverage = getattr(cfg, "LEVERAGE",       "N/A")
    symbols  = getattr(cfg, "SYMBOLS",        [])

    # FIX: symbol_stats puede ser dict o string
    if isinstance(symbol_stats, dict):
        n      = len(symbols)
        top5   = ", ".join(symbols[:5]) if symbols else "N/A"
        stats  = f"{n} pares | Top 5: {top5}"
    elif symbol_stats:
        stats  = str(symbol_stats)
    else:
        stats  = f"{len(symbols)} pares cargados"

    _send(
        f"🤖 *BB+RSI Bot Elite v6 arrancado*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {stats}\n"
        f"⚙️ RSI\\_L:`{rsi_l}` RSI\\_S:`{rsi_s}` BB\\_σ:`{bb_sigma}` "
        f"SL:`{sl_atr}xATR` Lev:`{leverage}x`\n"
        f"🔁 Ciclo: `{loop_s}s` | Max pos: `{max_pos}`\n"
        f"🛡 CB: `-{cb_loss*100:.0f}%` dia | `{cb_cons}` perdidas"
    )


def send_buy_signal(symbol: str, sig: dict, balance: float, executed: bool):
    side = "🟢 LONG" if sig["action"] == "buy" else "🔴 SHORT"
    ex   = "✅ Ejecutado" if executed else "⚠️ No ejecutado"
    score_line = f"Score   : `{sig.get('score','N/A')}/100`\n" if sig.get("score") else ""
    _send(
        f"{side} — `{symbol}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entrada : `{sig['entry']}`\n"
        f"🛑 SL   : `{sig['sl']}`\n"
        f"🎯 TP   : `{sig['tp']}`\n"
        f"TP 50%  : `{sig.get('tp_partial','N/A')}`\n"
        f"RSI     : `{sig.get('rsi','N/A')}`\n"
        f"{score_line}"
        f"4h      : `{sig.get('trend_4h','N/A')}`\n"
        f"Balance : `${balance:.2f}`\n"
        f"Razon   : `{_esc(sig.get('reason',''))}`\n"
        f"{ex}"
    )


def send_close_signal(symbol: str, entry: float, exit_price: float,
                      pnl: float, reason: str, executed: bool):
    emoji  = "✅" if pnl >= 0 else "❌"
    ex_txt = "Ejecutado" if executed else "Simulado"
    _send(
        f"{emoji} *CIERRE* — `{symbol}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entrada : `{entry}`\n"
        f"Salida  : `{exit_price}`\n"
        f"PnL est : `${pnl:+.2f}`\n"
        f"Razon   : `{_esc(reason)}`\n"
        f"Estado  : {ex_txt}"
    )


def send_status(positions: list, balance: float, stats: dict, perf: str):
    pos_lines = ""
    for p in positions:
        side_e = "🟢" if p.get("side") == "long" else "🔴"
        diff   = p.get("current", p["entry"]) - p["entry"]
        pos_lines += f"  {side_e} `{p['symbol']}` e:`{p['entry']}` ({diff:+.4f})\n"
    if not pos_lines:
        pos_lines = "  sin posiciones abiertas\n"

    wins  = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    total  = wins + losses
    wr     = f"{wins/total*100:.1f}%" if total > 0 else "N/A"

    pnl_total  = stats.get("pnl_total", None)
    roi_total  = stats.get("roi_total", None)
    ttrades    = stats.get("total_trades", "")
    total_line = (
        f"📊 Total: `{ttrades}tr` PnL:`${pnl_total:+.2f}` ROI:`{roi_total:+.1f}%`\n"
        if pnl_total is not None else ""
    )
    _send(
        f"📊 *Reporte horario*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: `${balance:.2f} USDT`\n"
        f"📈 Hoy: `{wins}W/{losses}L` WR:`{wr}` PnL:`${stats.get('pnl_today',0):+.2f}`\n"
        f"{total_line}"
        f"📋 Posiciones:\n{pos_lines}"
        f"🤖 Learner: `{_esc(perf)}`"
    )


def send_no_funds(symbol: str, sig: dict, balance: float):
    pass  # reemplazado por send_raw en main.py


def send_error(msg: str):
    _send(f"🚨 *ERROR BOT*\n`{_esc(str(msg)[:200])}`")


def send_param_update(updates: dict, perf: str):
    lines = "\n".join([f"  `{_esc(str(k))}`: `{_esc(str(v))}`" for k, v in updates.items()])
    _send(
        f"🔧 *Learner — Ajuste parametros*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{lines}\n"
        f"📊 `{_esc(perf)}`"
    )


def send_symbols_update(symbols: list):
    _send(
        f"🔄 *Pares actualizados*\n"
        f"Total: `{len(symbols)}`\n"
        f"Top 5: `{', '.join(symbols[:5])}`"
    )
