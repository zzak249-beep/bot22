"""
Telegram Notification Client
Sends real-time trade alerts and status updates
"""

import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self.session = requests.Session()

    def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        url = TELEGRAM_API.format(token=self.token, method="sendMessage")
        try:
            r = self.session.post(url, json={
                "chat_id":    self.chat_id,
                "text":       text,
                "parse_mode": parse_mode,
            }, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────
    # Formatted messages
    # ──────────────────────────────────────────────────────────────────────
    def send_startup(self, symbol: str, interval: str, leverage: int, demo: bool):
        mode = "🟡 PAPER TRADING" if demo else "🟢 LIVE TRADING"
        self._send(
            f"<b>🤖 EMA Bot Started</b>\n\n"
            f"Mode: {mode}\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Timeframe: <code>{interval}</code>\n"
            f"Leverage: <code>{leverage}x</code>\n\n"
            f"Monitoring for EMA Slope + Cross signals..."
        )

    def send_signal(self, symbol: str, action: str, price: float,
                    ema1: float, ema2: float, ema3: float,
                    reason: str, qty: float, leverage: int,
                    sl_price: Optional[float] = None,
                    tp_price: Optional[float] = None):
        emoji = "🟢 LONG" if action == "LONG" else "🔴 SHORT"
        sl_line = f"\n🛡 Stop Loss:   <code>${sl_price:,.4f}</code>" if sl_price else ""
        tp_line = f"\n🎯 Take Profit: <code>${tp_price:,.4f}</code>" if tp_price else ""
        self._send(
            f"<b>{emoji} — {symbol}</b>\n\n"
            f"📌 Price:    <code>${price:,.4f}</code>\n"
            f"📊 EMA1({1}): <code>{ema1:,.4f}</code>\n"
            f"📊 EMA2({4}): <code>{ema2:,.4f}</code>\n"
            f"📊 EMA3({20}): <code>{ema3:,.4f}</code>\n"
            f"📦 Quantity: <code>{qty}</code>  ({leverage}x)\n"
            f"💡 Reason:   {reason}"
            f"{sl_line}{tp_line}"
        )

    def send_order_filled(self, symbol: str, action: str, fill_price: float,
                          order_id: str, qty: float):
        emoji = "✅" if action == "LONG" else "✅"
        self._send(
            f"{emoji} <b>Order Filled</b>\n\n"
            f"Symbol:   <code>{symbol}</code>\n"
            f"Side:     <code>{action}</code>\n"
            f"Price:    <code>${fill_price:,.4f}</code>\n"
            f"Qty:      <code>{qty}</code>\n"
            f"Order ID: <code>{order_id}</code>"
        )

    def send_close(self, symbol: str, closed_side: str, entry: float,
                   exit_price: float, pnl: float, qty: float):
        pnl_emoji = "💰" if pnl >= 0 else "📉"
        pnl_sign  = "+" if pnl >= 0 else ""
        self._send(
            f"{pnl_emoji} <b>Position Closed — {symbol}</b>\n\n"
            f"Side:    <code>{closed_side}</code>\n"
            f"Entry:   <code>${entry:,.4f}</code>\n"
            f"Exit:    <code>${exit_price:,.4f}</code>\n"
            f"Qty:     <code>{qty}</code>\n"
            f"PnL:     <b>{pnl_sign}{pnl:.4f} USDT</b>"
        )

    def send_balance(self, balance: float, equity: float, margin: float):
        self._send(
            f"💼 <b>Account Update</b>\n\n"
            f"Balance:  <code>${balance:,.2f}</code>\n"
            f"Equity:   <code>${equity:,.2f}</code>\n"
            f"Margin:   <code>${margin:,.2f}</code>"
        )

    def send_error(self, context: str, error: str):
        self._send(
            f"⚠️ <b>Bot Error</b>\n\n"
            f"Context: {context}\n"
            f"Error: <code>{error}</code>"
        )

    def send_heartbeat(self, symbol: str, price: float, position: str,
                       ema3: float, balance: float, candles_processed: int):
        pos_emoji = {"LONG": "🟢", "SHORT": "🔴", "FLAT": "⚪"}.get(position, "⚪")
        self._send(
            f"💓 <b>Heartbeat</b> — {symbol}\n\n"
            f"Price:    <code>${price:,.4f}</code>\n"
            f"EMA3:     <code>{ema3:,.4f}</code>\n"
            f"Position: {pos_emoji} <code>{position}</code>\n"
            f"Balance:  <code>${balance:,.2f}</code>\n"
            f"Candles:  <code>{candles_processed}</code>"
        )
