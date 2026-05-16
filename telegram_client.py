"""
Telegram Client v3
- Mensajes en español
- Comandos: /status /balance /trades /pausa /reanudar /stop
- Formato mejorado con emojis y tablas
"""

import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{t}/{m}"


class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self.session = requests.Session()
        self._bot_ref = None   # referencia al bot para comandos

    def set_bot(self, bot): self._bot_ref = bot

    def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        try:
            r = self.session.post(
                API.format(t=self.token, m="sendMessage"),
                json={"chat_id": self.chat_id, "text": text,
                      "parse_mode": parse_mode}, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False

    def get_updates(self, offset: int = 0) -> list:
        try:
            r = self.session.get(
                API.format(t=self.token, m="getUpdates"),
                params={"offset": offset, "timeout": 1}, timeout=5)
            r.raise_for_status()
            return r.json().get("result", [])
        except:
            return []

    # ── Mensajes formateados ──────────────────────────────────────────────

    def send_startup(self, symbol, interval, leverage, demo):
        mode = "🟡 PAPER TRADING" if demo else "🟢 LIVE TRADING"
        self._send(
            f"<b>🤖 EMA Bot v3 Iniciado</b>\n\n"
            f"Modo: {mode}\n"
            f"Par: <code>{symbol or 'Scanner automático'}</code>\n"
            f"Temporalidad: <code>{interval}</code>\n"
            f"Apalancamiento: <code>{leverage}x</code>\n\n"
            f"Escaneando 30 pares cada vela...\n"
            f"Comandos: /status /balance /trades /pausa /reanudar"
        )

    def send_scan_result(self, n_signals: int, best_symbol: str, best_score: float):
        if n_signals == 0:
            self._send("🔍 Scan completado — sin señales activas ahora.")
        else:
            self._send(
                f"🔍 <b>Scan: {n_signals} señal(es) encontrada(s)</b>\n"
                f"Mejor: <b>{best_symbol}</b> — Score: <code>{best_score}</code>"
            )

    def send_signal(self, symbol, action, price, ema1, ema2, ema3,
                    rsi, adx, atr_pct, reason, qty, leverage, score,
                    sl_price=None, tp1=None, tp2=None):
        e = "🟢 LARGO" if action == "LONG" else "🔴 CORTO"
        sl_l  = f"\n🛡 Stop Loss:  <code>${sl_price:,.4f}</code>" if sl_price else ""
        tp1_l = f"\n🎯 TP1 (1R):   <code>${tp1:,.4f}</code>" if tp1 else ""
        tp2_l = f"\n🎯 TP2 (2.5R): <code>${tp2:,.4f}</code>" if tp2 else ""
        self._send(
            f"<b>{e} — {symbol}</b>  [Score: {score}]\n\n"
            f"💰 Entrada: <code>${price:,.4f}</code>\n"
            f"📦 Cantidad: <code>{qty}</code>  ({leverage}x)\n"
            f"📊 RSI: <code>{rsi:.0f}</code> | ADX: <code>{adx:.0f}</code> | ATR: <code>{atr_pct:.2f}%</code>\n"
            f"🧠 {reason}"
            f"{sl_l}{tp1_l}{tp2_l}"
        )

    def send_tp_hit(self, symbol, side, tp_num, price, pnl, qty_remaining):
        self._send(
            f"🎯 <b>TP{tp_num} alcanzado — {symbol}</b>\n\n"
            f"Precio: <code>${price:,.4f}</code>\n"
            f"PnL parcial: <b>+{pnl:.2f} USDT</b>\n"
            f"SL movido a breakeven ✓\n"
            f"Qty restante: <code>{qty_remaining}</code>"
        )

    def send_close(self, symbol, side, entry, exit_p, pnl, qty, reason=""):
        emoji = "💰" if pnl >= 0 else "📉"
        sign  = "+" if pnl >= 0 else ""
        self._send(
            f"{emoji} <b>Posición cerrada — {symbol}</b>\n\n"
            f"Lado:   <code>{side}</code>\n"
            f"Entrada: <code>${entry:,.4f}</code>\n"
            f"Salida:  <code>${exit_p:,.4f}</code>\n"
            f"PnL:    <b>{sign}{pnl:.2f} USDT</b>\n"
            + (f"Razón: {reason}" if reason else "")
        )

    def send_stats(self, stats_text: str):
        self._send(f"📈 <b>Estadísticas del Bot</b>\n\n{stats_text}")

    def send_balance(self, balance, equity=None, margin=None):
        lines = [f"💼 <b>Balance</b>\n\nDisponible: <code>${balance:,.2f}</code>"]
        if equity:  lines.append(f"Equity:     <code>${equity:,.2f}</code>")
        if margin:  lines.append(f"Margen:     <code>${margin:,.2f}</code>")
        self._send("\n".join(lines))

    def send_heartbeat(self, symbol, price, position, ema3, balance, candles, pnl_session=0.0):
        pos_e = {"LONG":"🟢","SHORT":"🔴"}.get(position, "⚪")
        self._send(
            f"💓 <b>Heartbeat</b>\n\n"
            f"Par:      <code>{symbol}</code>\n"
            f"Precio:   <code>${price:,.4f}</code>\n"
            f"EMA3:     <code>{ema3:,.4f}</code>\n"
            f"Posición: {pos_e} <code>{position}</code>\n"
            f"Balance:  <code>${balance:,.2f}</code>\n"
            f"PnL sesión: <code>{pnl_session:+.2f}$</code>\n"
            f"Velas:    <code>{candles}</code>"
        )

    def send_error(self, ctx, err):
        self._send(f"⚠️ <b>Error</b>\n\nContexto: {ctx}\n<code>{err[:300]}</code>")

    def send_paused(self):
        self._send("⏸ Bot <b>pausado</b> — no abrirá nuevas posiciones.\nUsa /reanudar para continuar.")

    def send_resumed(self):
        self._send("▶️ Bot <b>reanudado</b> — buscando señales.")

    def send_order_filled(self, symbol, action, price, order_id, qty):
        self._send(
            f"✅ <b>Orden ejecutada</b>\n\n"
            f"Par:  <code>{symbol}</code>\n"
            f"Lado: <code>{action}</code>\n"
            f"Precio: <code>${price:,.4f}</code>\n"
            f"Qty: <code>{qty}</code>"
        )
