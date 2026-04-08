"""
Telegram Bot - Notificaciones para Trading Bot V2
Maneja todos los mensajes hacia Telegram con formato HTML
"""
import logging
import aiohttp
from typing import Dict, Optional

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramBot:
    """Cliente asíncrono para enviar notificaciones a Telegram"""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._url = TELEGRAM_API.format(token=token)

    # ── Core ──────────────────────────────────────────────────────────────

    async def send(self, text: str) -> bool:
        """Enviar mensaje genérico en formato HTML"""
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self._url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        body = await r.text()
                        log.error(f"Telegram error {r.status}: {body}")
                        return False
                    return True
        except Exception as e:
            log.error(f"Telegram send error: {e}")
            return False

    # ── Specialized Messages ──────────────────────────────────────────────

    async def signal(
        self,
        direction: str,
        symbol: str,
        levels: Dict,
        signals: Dict,
        timeframe: str,
    ) -> bool:
        """Notificación de nueva señal de trading"""
        emoji = "🟢" if direction == "BUY" else "🔴"
        dir_label = "LONG" if direction == "BUY" else "SHORT"

        tp_lines = ""
        for i in range(1, 6):
            tp_key = f"tp{i}"
            if tp_key in levels:
                tp_lines += f"  TP{i}: <code>{levels[tp_key]:.4f}</code>\n"

        msg = (
            f"{emoji} <b>{dir_label} SIGNAL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Par: <b>{symbol}</b> | TF: {timeframe}\n"
            f"💵 Entrada: <code>{levels.get('entry', 0):.4f}</code>\n"
            f"🛑 SL: <code>{levels.get('sl', 0):.4f}</code>\n"
            f"⚖️ Breakeven: <code>{levels.get('breakeven', 0):.4f}</code>\n"
            f"{tp_lines}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🐂 Bull: {signals.get('bull_pct', 0):.1f}% | "
            f"🐻 Bear: {signals.get('bear_pct', 0):.1f}%\n"
            f"📈 Bias: <b>{signals.get('bias', 'N/A')}</b>\n"
            f"📉 RSI: {signals.get('rsi', 0):.1f} | "
            f"ADX: {signals.get('adx', 0):.1f}\n"
            f"🔧 Strategy: {signals.get('strategy', 'N/A')}"
        )
        return await self.send(msg)

    async def order_filled(
        self,
        symbol: str,
        direction: str,
        qty: float,
        price: float,
    ) -> bool:
        """Notificación de orden ejecutada"""
        emoji = "✅🟢" if direction == "BUY" else "✅🔴"
        msg = (
            f"{emoji} <b>Orden Ejecutada</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Par: <b>{symbol}</b>\n"
            f"Dirección: <b>{direction}</b>\n"
            f"Precio: <code>{price:.4f}</code>\n"
            f"Cantidad: <code>{qty:.4f}</code>"
        )
        return await self.send(msg)

    async def tp_hit(
        self,
        symbol: str,
        tp_number: int,
        price: float,
        pnl_pct: float,
    ) -> bool:
        """Notificación de Take Profit alcanzado"""
        msg = (
            f"🎯 <b>TP{tp_number} Alcanzado!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Par: <b>{symbol}</b>\n"
            f"Precio: <code>{price:.4f}</code>\n"
            f"PnL: <b>+{pnl_pct:.2f}%</b>"
        )
        return await self.send(msg)

    async def error_alert(self, message: str) -> bool:
        """Notificación de error"""
        msg = (
            f"⚠️ <b>Error Alert</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{message}"
        )
        return await self.send(msg)

    async def trade_closed(
        self,
        symbol: str,
        direction: str,
        entry: float,
        exit_price: float,
        pnl_net: float,
        reason: str,
    ) -> bool:
        """Notificación de trade cerrado"""
        emoji = "✅" if pnl_net >= 0 else "❌"
        msg = (
            f"{emoji} <b>Trade Cerrado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Par: <b>{symbol}</b> | {direction}\n"
            f"Entrada: <code>{entry:.4f}</code>\n"
            f"Salida: <code>{exit_price:.4f}</code>\n"
            f"Razón: {reason}\n"
            f"<b>PnL Neto: {'+'if pnl_net>=0 else ''}{pnl_net:.2f} USDT</b>"
        )
        return await self.send(msg)
