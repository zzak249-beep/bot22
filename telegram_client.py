"""
Telegram Client — EMA Bot
Todos los métodos de notificación usados en main.py
"""
import logging
import requests

logger = logging.getLogger(__name__)
BASE = "https://api.telegram.org/bot"


class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._base   = f"{BASE}{token}"

    def _send(self, text: str):
        try:
            r = requests.post(
                f"{self._base}/sendMessage",
                json={"chat_id": self.chat_id, "text": text[:4096],
                      "parse_mode": "HTML"},
                timeout=10,
            )
            if not r.ok:
                logger.warning(f"Telegram {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"Telegram send: {e}")

    def get_updates(self, offset: int = 0) -> list:
        try:
            r = requests.get(
                f"{self._base}/getUpdates",
                params={"offset": offset, "timeout": 2},
                timeout=5,
            )
            if r.ok:
                return r.json().get("result", [])
        except Exception as e:
            logger.debug(f"Telegram updates: {e}")
        return []

    # ── Mensajes estructurados ─────────────────────────────────────────────

    def send_startup(self, symbol: str, interval: str, leverage: int, demo: bool):
        mode = "PAPER 🟡" if demo else "LIVE 🟢"
        self._send(
            f"🚀 <b>EMA Bot arrancando</b>\n\n"
            f"Par:       <code>{symbol}</code>\n"
            f"Timeframe: <code>{interval}</code>\n"
            f"Leverage:  <code>{leverage}x</code>\n"
            f"Modo:      <code>{mode}</code>"
        )

    def send_signal(self, symbol, action, price, ema1, ema2, ema3,
                    rsi, adx, atr_pct, reason, qty, leverage, score,
                    sl_price, tp1, tp2):
        emoji = "🟢" if action == "LONG" else "🔴"
        self._send(
            f"{emoji} <b>SEÑAL {action} — {symbol}</b>\n\n"
            f"Precio:  <code>{price:.6f}</code>\n"
            f"EMA1/2/3:<code>{ema1:.6f} / {ema2:.6f} / {ema3:.6f}</code>\n"
            f"RSI: <code>{rsi:.1f}</code> | ADX: <code>{adx:.1f}</code> | ATR: <code>{atr_pct:.2f}%</code>\n"
            f"SL:  <code>{sl_price:.6f}</code>\n"
            f"TP1: <code>{tp1:.6f}</code>\n"
            f"TP2: <code>{tp2:.6f}</code>\n"
            f"Qty: <code>{qty}</code> × <code>{leverage}x</code>\n"
            f"Score: <code>{score}</code>\n"
            f"Razón: <i>{reason}</i>"
        )

    def send_order_filled(self, symbol, action, price, order_id, qty):
        emoji = "✅"
        self._send(
            f"{emoji} <b>ORDEN EJECUTADA</b>\n\n"
            f"Par:     <code>{symbol}</code>\n"
            f"Lado:    <code>{action}</code>\n"
            f"Precio:  <code>{price:.6f}</code>\n"
            f"Qty:     <code>{qty}</code>\n"
            f"OrderID: <code>{order_id}</code>"
        )

    def send_close(self, symbol, side, entry, price, pnl, qty, reason):
        emoji = "🟢" if pnl >= 0 else "🔴"
        self._send(
            f"{emoji} <b>POSICIÓN CERRADA — {symbol}</b>\n\n"
            f"Lado:   <code>{side}</code>\n"
            f"Entrada:<code>{entry:.6f}</code>\n"
            f"Salida: <code>{price:.6f}</code>\n"
            f"PnL:    <code>{pnl:+.2f} USDT</code>\n"
            f"Qty:    <code>{qty}</code>\n"
            f"Razón:  <i>{reason}</i>"
        )

    def send_tp_hit(self, symbol, side, tp_num, price, pnl, remaining_qty):
        self._send(
            f"🎯 <b>TP{tp_num} ALCANZADO — {symbol}</b>\n\n"
            f"Precio: <code>{price:.6f}</code>\n"
            f"PnL TP{tp_num}: <code>+{pnl:.2f} USDT</code>\n"
            f"Qty restante: <code>{remaining_qty}</code>\n"
            f"SL movido a breakeven"
        )

    def send_heartbeat(self, symbol, price, position, ema3, balance, candles, total_pnl):
        pos_emoji = "🟢" if position == "LONG" else ("🔴" if position == "SHORT" else "⬜")
        self._send(
            f"💓 <b>Heartbeat</b>\n\n"
            f"Par:     <code>{symbol}</code>\n"
            f"Precio:  <code>{price:.6f}</code>\n"
            f"EMA3:    <code>{ema3:.6f}</code>\n"
            f"Posición:{pos_emoji} <code>{position}</code>\n"
            f"Balance: <code>${balance:,.2f}</code>\n"
            f"PnL:     <code>{total_pnl:+.2f} USDT</code>\n"
            f"Velas:   <code>{candles}</code>"
        )

    def send_balance(self, balance: float):
        self._send(f"💰 <b>Balance:</b> <code>${balance:,.2f} USDT</code>")

    def send_stats(self, summary: str):
        self._send(f"📊 <b>Estadísticas</b>\n\n<code>{summary}</code>")

    def send_error(self, context: str, msg: str):
        self._send(
            f"⚠️ <b>Error — {context}</b>\n\n"
            f"<code>{msg[:800]}</code>"
        )

    def send_paused(self):
        self._send("⏸️ <b>Bot pausado</b>\nUsa /reanudar para continuar.")

    def send_resumed(self):
        self._send("▶️ <b>Bot reanudado</b>")
