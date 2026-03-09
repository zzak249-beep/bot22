"""
notifier.py — Alertas Telegram con mensajes ricos
"""
import logging
import aiohttp
import config as cfg

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self):
        self.enabled = bool(cfg.TELEGRAM_TOKEN and cfg.TELEGRAM_CHAT_ID)
        self._url    = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"

    async def send(self, text: str, silent: bool = False):
        if not self.enabled:
            log.info(f"[TG disabled] {text[:80]}")
            return
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(self._url, json={
                    "chat_id"              : cfg.TELEGRAM_CHAT_ID,
                    "text"                 : text,
                    "parse_mode"           : "Markdown",
                    "disable_notification" : silent,
                }, timeout=aiohttp.ClientTimeout(total=8))
        except Exception as e:
            log.warning(f"Telegram error: {e}")

    # ─── Templates ───────────────────────────────────────────
    async def on_start(self, balance: float):
        mode = "🔵 DRY RUN" if cfg.DRY_RUN else "🔴 *LIVE — DINERO REAL*"
        await self.send(
            f"🤖 *EMA Institutional Bot Iniciado*\n"
            f"Modo     : {mode}\n"
            f"Par      : `{cfg.SYMBOL}`\n"
            f"TF       : `{cfg.TIMEFRAME}`\n"
            f"Leverage : `{cfg.LEVERAGE}x`\n"
            f"Riesgo   : `{cfg.RISK_PCT}%` por trade\n"
            f"Balance  : `${balance:.2f} USDT`\n"
            f"TP1/TP2  : `{cfg.TP1_RR}R / {cfg.TP2_RR}R`"
        )

    async def on_signal(self, sig, balance: float):
        emoji = "🟢" if sig.direction == "LONG" else "🔴"
        mode  = "[DRY]" if cfg.DRY_RUN else ""
        await self.send(
            f"{emoji} *SEÑAL {sig.direction}* {mode}\n"
            f"Par    : `{cfg.SYMBOL}` {cfg.TIMEFRAME}\n"
            f"Entry  : `{sig.entry:.4f}`\n"
            f"SL     : `{sig.sl:.4f}` ({sig.risk_pct:.2f}%)\n"
            f"TP1    : `{sig.tp1:.4f}` (+{sig.risk_pct * cfg.TP1_RR:.2f}%)\n"
            f"TP2    : `{sig.tp2:.4f}` (+{sig.risk_pct * cfg.TP2_RR:.2f}%)\n"
            f"ATR    : `{sig.atr:.4f}`\n"
            f"Balance: `${balance:.2f}`\n"
            f"🕐 `{sig.bar_time.strftime('%Y-%m-%d %H:%M')} UTC`"
        )

    async def on_tp1(self, pos: dict):
        await self.send(
            f"✅ *TP1 alcanzado*\n"
            f"`{pos['direction']}` {cfg.SYMBOL}\n"
            f"TP1 @ `{pos['tp1']:.4f}` (50% cerrado)\n"
            f"🔒 SL movido a *breakeven* @ `{pos['entry']:.4f}`"
        )

    async def on_tp2(self, pos: dict, pnl: float):
        await self.send(
            f"🏆 *TP2 alcanzado — Trade completado*\n"
            f"`{pos['direction']}` {cfg.SYMBOL}\n"
            f"TP2 @ `{pos['tp2']:.4f}`\n"
            f"PnL estimado: `+${pnl:.2f} USDT`"
        )

    async def on_sl(self, pos: dict, loss: float):
        await self.send(
            f"🛑 *Stop Loss tocado*\n"
            f"`{pos['direction']}` {cfg.SYMBOL}\n"
            f"SL @ `{pos['sl']:.4f}`\n"
            f"Pérdida: `−${abs(loss):.2f} USDT`"
        )

    async def on_risk_pause(self, reason: str):
        await self.send(
            f"⚠️ *Bot en pausa por gestión de riesgo*\n{reason}"
        )

    async def on_heartbeat(self, stats: dict):
        await self.send(
            f"📊 *Heartbeat 30min*\n"
            f"Balance : `${stats['balance']:.2f}`\n"
            f"PnL hoy : `${stats['daily_pnl']:+.2f}`\n"
            f"Trades  : {stats['trades_today']}/{cfg.MAX_TRADES_DAY}\n"
            f"Ciclos  : {stats['cycles']}\n"
            f"Posición: {stats['position'] or 'ninguna'}",
            silent=True,
        )
