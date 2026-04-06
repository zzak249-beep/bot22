"""
Telegram Alerts for Sniper Bot
"""
import aiohttp
import logging

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        url = f"{self.base}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload) as r:
                    result = await r.json()
                    if not result.get("ok"):
                        logger.error(f"Telegram error: {result}")
                        return False
                    return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    # ── Formatted Messages ─────────────────────────────────────────────────────

    async def signal(self, direction: str, symbol: str, levels: dict, scores: dict, timeframe: str):
        emoji = "🟢" if direction == "BUY" else "🔴"
        arrow = "📈 LONG" if direction == "BUY" else "📉 SHORT"
        bias_emoji = "💚" if "BULL" in scores["bias"] else "❤️"

        msg = (
            f"{emoji} <b>SNIPER SIGNAL — {arrow}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Symbol:</b> {symbol}  |  TF: {timeframe}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Entry:</b>  <code>{levels['entry']:.4f}</code>\n"
            f"🛑 <b>SL:</b>     <code>{levels['sl']:.4f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>TP1:</b>    <code>{levels['tp1']:.4f}</code>  (+1R)\n"
            f"🎯 <b>TP2:</b>    <code>{levels['tp2']:.4f}</code>  (+2R)\n"
            f"🎯 <b>TP3:</b>    <code>{levels['tp3']:.4f}</code>  (+3R)\n"
            f"🎯 <b>TP4:</b>    <code>{levels['tp4']:.4f}</code>  (+4R)\n"
            f"🎯 <b>TP5:</b>    <code>{levels['tp5']:.4f}</code>  (+5R)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{bias_emoji} <b>Bias:</b>  {scores['bias']}\n"
            f"🐂 <b>Bull Score:</b>  {scores['bull_pct']:.0f}%\n"
            f"🐻 <b>Bear Score:</b>  {scores['bear_pct']:.0f}%\n"
            f"📊 <b>RSI:</b>    {scores['rsi']:.1f}  |  <b>ADX:</b>  {scores['adx']:.1f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚙️ <i>Sniper V.02 by KhanSaab</i>"
        )
        await self.send(msg)

    async def tp_hit(self, symbol: str, tp_num: int, price: float, pnl_pct: float):
        msg = (
            f"🔥 <b>TARGET HIT — TP{tp_num}</b>\n"
            f"Symbol: {symbol}\n"
            f"Price:  <code>{price:.4f}</code>\n"
            f"🏆 PnL: <b>+{pnl_pct:.2f}%</b>"
        )
        await self.send(msg)

    async def sl_hit(self, symbol: str, price: float, pnl_pct: float):
        msg = (
            f"🛑 <b>STOP LOSS HIT</b>\n"
            f"Symbol: {symbol}\n"
            f"Price:  <code>{price:.4f}</code>\n"
            f"📉 PnL: <b>{pnl_pct:.2f}%</b>"
        )
        await self.send(msg)

    async def order_filled(self, symbol: str, direction: str, quantity: float, price: float):
        msg = (
            f"✅ <b>ORDER FILLED</b>\n"
            f"{'🟢 LONG' if direction == 'BUY' else '🔴 SHORT'}  {symbol}\n"
            f"Qty:   <code>{quantity}</code>\n"
            f"Price: <code>{price:.4f}</code>"
        )
        await self.send(msg)

    async def error_alert(self, message: str):
        msg = f"⚠️ <b>BOT ERROR</b>\n<code>{message}</code>"
        await self.send(msg)

    async def heartbeat(self, symbol: str, balance: float, pnl: float, open_trade: str):
        msg = (
            f"💓 <b>BOT ALIVE</b>\n"
            f"Symbol: {symbol}\n"
            f"Balance: <code>${balance:.2f}</code>\n"
            f"Unrealized PnL: <code>${pnl:.2f}</code>\n"
            f"Trade: {open_trade}"
        )
        await self.send(msg)
