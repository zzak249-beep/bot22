"""
telegram_client.py — Notificaciones Telegram v5

Mensajes ricos con todos los niveles para trade manual y automático.
Comandos: /status /balance /trades /pausa /reanudar /scan /stop
"""
from __future__ import annotations
import logging
import aiohttp
from strategy import Signal

log = logging.getLogger("telegram")
BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self._offset  = 0

    async def _send(self, text: str):
        if not self.token or not self.chat_id:
            return
        url = BASE.format(token=self.token, method="sendMessage")
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={
                    "chat_id":                  self.chat_id,
                    "text":                     text[:4000],
                    "parse_mode":               "Markdown",
                    "disable_web_page_preview": True,
                })
        except Exception as e:
            log.error("Telegram: %s", e)

    async def get_updates(self) -> list[dict]:
        url = BASE.format(token=self.token, method="getUpdates")
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(url, params={"offset": self._offset,
                                              "timeout": 1})
                data = await r.json()
            updates = data.get("result", [])
            if updates:
                self._offset = updates[-1]["update_id"] + 1
            return updates
        except Exception:
            return []

    # ── Mensajes ──────────────────────────────────────────────

    async def startup(self, dry_run: bool, symbols: int, balance: float):
        mode = "🟡 PAPER (simulación)" if dry_run else "🟢 REAL"
        await self._send(
            f"🤖 *Edge Bot v5 — Iniciado*\n\n"
            f"Modo: `{mode}`\n"
            f"Balance: `{balance:.2f} USDT`\n"
            f"Símbolos: `{symbols}`\n\n"
            f"Estrategia: EMA8/21/55 + ADX + FVG + Volume Imbalance + Funding\n"
            f"Score mínimo: `{60}/100`\n"
            f"RR mínimo: `1.5`\n\n"
            f"Comandos:\n"
            f"`/status` `/balance` `/trades` `/pausa` `/reanudar` `/stop`"
        )

    async def order_opened(self, sig: Signal, qty: float, order_id: str):
        emoji  = "🟢" if sig.side == "LONG" else "🔴"
        dir_es = "LARGO ↑" if sig.side == "LONG" else "CORTO ↓"
        sl_pct  = abs(sig.price - sig.sl)  / sig.price * 100
        tp1_pct = abs(sig.tp1  - sig.price) / sig.price * 100
        tp2_pct = abs(sig.tp2  - sig.price) / sig.price * 100
        tp3_pct = abs(sig.tp3  - sig.price) / sig.price * 100

        await self._send(
            f"{emoji} *{sig.side} — {sig.symbol}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 *{dir_es}* | Régimen: `{sig.regime}`\n"
            f"💲 Entrada:  `{sig.price:.6g}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 SL:   `{sig.sl:.6g}`  _(-{sl_pct:.2f}%)_\n"
            f"🎯 TP1:  `{sig.tp1:.6g}` _(+{tp1_pct:.2f}%)_ — 50%\n"
            f"🎯 TP2:  `{sig.tp2:.6g}` _(+{tp2_pct:.2f}%)_ — 25%\n"
            f"🎯 TP3:  `{sig.tp3:.6g}` _(+{tp3_pct:.2f}%)_ — 25%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚖️  R/R: `1:{sig.rr:.2f}` | ⭐ Score: `{sig.score}/100`\n"
            f"📋 `{sig.reason}`\n"
            f"📦 Cantidad: `{qty:.4f}` contratos\n"
            f"🆔 `{order_id}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👆 _Puedes replicar manualmente en BingX_"
        )

    async def tp_hit(self, symbol: str, side: str, tp_n: int,
                     price: float, pnl: float, qty_left: float):
        emoji = "💰" if pnl > 0 else "💸"
        await self._send(
            f"{emoji} *TP{tp_n} Alcanzado — {symbol}*\n"
            f"Precio: `{price:.6g}` | PnL parcial: `{pnl:+.2f} USDT`\n"
            f"Posición restante: `{qty_left:.4f}`\n"
            f"_SL movido a breakeven — trailing activado_"
        )

    async def position_closed(self, symbol: str, side: str,
                               entry: float, exit_p: float,
                               pnl: float, reason: str):
        emoji = "💰" if pnl >= 0 else "💸"
        label = "GANANCIA" if pnl >= 0 else "PÉRDIDA"
        pct   = (exit_p - entry) / entry * 100 if entry > 0 else 0
        if side == "SHORT":
            pct = -pct
        await self._send(
            f"{emoji} *Cerrado — {label}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"`{symbol}` | `{side}`\n"
            f"Entrada: `{entry:.6g}` → Salida: `{exit_p:.6g}`\n"
            f"Mov: `{pct:+.2f}%` | PnL: `{pnl:+.2f} USDT`\n"
            f"Razón: `{reason}`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

    async def heartbeat(self, balance: float, open_pos: int,
                        wr: float, pnl_day: float, scan_n: int):
        wr_emoji = "📈" if wr >= 55 else "📉"
        await self._send(
            f"💓 *Heartbeat*\n"
            f"Balance: `{balance:.2f} USDT`\n"
            f"Posiciones: `{open_pos}` | Scan #`{scan_n}`\n"
            f"{wr_emoji} Win Rate: `{wr:.0f}%` | PnL hoy: `{pnl_day:+.2f} USDT`"
        )

    async def accuracy_report(self, report: str):
        await self._send(report)

    async def daily_summary(self, text: str):
        await self._send(f"📊 *Resumen diario*\n{text}")

    async def status(self, balance: float, pos_count: int,
                     risk_summary: str, scan_n: int):
        await self._send(
            f"ℹ️ *Estado del Bot*\n\n"
            f"Balance: `{balance:.2f} USDT`\n"
            f"Posiciones: `{pos_count}`\n"
            f"Scans: `{scan_n}`\n\n"
            f"{risk_summary}"
        )

    async def paused(self):
        await self._send("⏸ *Bot pausado* — `/reanudar` para continuar")

    async def resumed(self):
        await self._send("▶️ *Bot reanudado*")

    async def error(self, msg: str):
        await self._send(f"⚠️ *Error*\n`{msg[:400]}`")

    async def auth_ok(self, balance: float, pos: int):
        await self._send(
            f"🔑 *API BingX verificada* ✅\n"
            f"Balance futuros: `{balance:.2f} USDT`\n"
            f"Posiciones abiertas: `{pos}`"
        )
