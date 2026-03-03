"""
notifier.py — Notificaciones por Telegram
"""
import requests
import logging
from datetime import datetime
import config as cfg

log = logging.getLogger("notifier")
BASE_URL = f"https://api.telegram.org/bot{cfg.TG_TOKEN}"


def _send(text: str, parse_mode: str = "HTML") -> bool:
    if not cfg.TG_TOKEN or not cfg.TG_CHAT_ID:
        log.warning("Telegram no configurado")
        return False
    try:
        r = requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": cfg.TG_CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False


def send_startup():
    _send(
        "🤖 <b>BB+RSI DCA Bot iniciado</b>\n"
        f"📊 Pares: {', '.join(cfg.SYMBOLS)}\n"
        f"⚙️ Riesgo: {cfg.RISK_PCT*100:.0f}% | Leverage: {cfg.LEVERAGE}x\n"
        f"🧠 Aprendizaje: activo (cada 10 trades)\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )


def send_buy_signal(symbol: str, signal: dict, balance: float, executed: bool):
    status = "✅ <b>ORDEN EJECUTADA</b>" if executed else "⚠️ <b>SEÑAL MANUAL</b> (sin fondos)"
    _send(
        f"📈 {status}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Par:     <code>{symbol}</code>\n"
        f"💰 Entrada: <code>${signal['entry']:,.4f}</code>\n"
        f"🛑 Stop:    <code>${signal['sl']:,.4f}</code>\n"
        f"🎯 Target:  <code>${signal['tp']:,.4f}</code>\n"
        f"📉 RSI:     <code>{signal['rsi']}</code>\n"
        f"💵 Balance: <code>${balance:.2f} USDT</code>\n"
        + ("" if executed else
           f"━━━━━━━━━━━━━━━━━━━━\n"
           f"👆 <b>Entra manualmente:\n"
           f"BingX → Futuros → {symbol}\n"
           f"LONG mercado | SL={signal['sl']}</b>\n")
        + f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )


def send_close_signal(symbol: str, entry: float, exit_price: float,
                      pnl: float, reason: str, executed: bool):
    emoji  = "🟢" if pnl >= 0 else "🔴"
    status = "✅ <b>CIERRE EJECUTADO</b>" if executed else "⚠️ <b>CIERRE MANUAL</b>"
    _send(
        f"{emoji} {status}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Par:    <code>{symbol}</code>\n"
        f"📥 Entrada:<code>${entry:,.4f}</code>\n"
        f"📤 Salida: <code>${exit_price:,.4f}</code>\n"
        f"💵 PnL:    <code>${pnl:+.2f}</code>\n"
        f"📝 Razon:  {reason}\n"
        + ("" if executed else
           f"👆 <b>Cierra en BingX → Futuros → {symbol} → Cerrar</b>\n")
        + f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )


def send_status(positions: list, balance: float, stats: dict, perf: dict = None):
    pos_text = ""
    if positions:
        for p in positions:
            pnl_pct = (p["current"] - p["entry"]) / p["entry"] * 100 if p["entry"] else 0
            e = "🟢" if pnl_pct >= 0 else "🔴"
            pos_text += f"  {e} {p['symbol']}: ${p['entry']:.4f} ({pnl_pct:+.1f}%)\n"
    else:
        pos_text = "  Sin posiciones abiertas\n"

    perf_text = ""
    if perf and perf.get("total", 0) > 0:
        perf_text = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Historico total</b>\n"
            f"  Trades: {perf['total']}  |  WR: {perf['win_rate']}%\n"
            f"  PnL acumulado: <code>${perf['total_pnl']:+.2f}</code>\n"
            f"  Expectativa: <code>${perf['expectancy']:+.4f}</code>/trade\n"
            f"  Mejor par: {perf['best_symbol']}\n"
            f"  Params actuales: σ={perf['current_params']['bb_sigma']} "
            f"RSI&lt;{perf['current_params']['rsi_ob']} "
            f"SL={perf['current_params']['sl_atr']}x\n"
        )

    _send(
        f"📊 <b>Reporte horario</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Balance: <code>${balance:.2f} USDT</code>\n"
        f"📈 Hoy: {stats.get('trades_today',0)} trades | "
        f"✅{stats.get('wins',0)} ❌{stats.get('losses',0)} | "
        f"<code>${stats.get('pnl_today',0):+.2f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Posiciones:</b>\n{pos_text}"
        f"{perf_text}"
        f"🕐 {datetime.now().strftime('%d/%m %H:%M')}"
    )


def send_param_update(updates: list, perf: dict):
    lines = "\n".join(
        f"  • {u.param}: {u.old_val} → <b>{u.new_val}</b>"
        for u in updates
    )
    reasons = updates[0].reason[:100] if updates else ""
    _send(
        f"🧠 <b>LEARNER — Parametros ajustados</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{lines}\n"
        f"📝 {reasons}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Basado en {perf.get('total',0)} trades\n"
        f"WR: {perf.get('win_rate',0)}%  |  "
        f"PnL: ${perf.get('total_pnl',0):+.2f}\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )


def send_error(msg: str):
    _send(f"❗ <b>ERROR</b>\n{msg}\n🕐 {datetime.now().strftime('%H:%M:%S')}")


def send_no_funds(symbol: str, signal: dict, balance: float):
    send_buy_signal(symbol, signal, balance, executed=False)
