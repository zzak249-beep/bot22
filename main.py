"""
main.py — Edge Bot v5 — Punto de entrada

Loop:
  cada 3 minutos:
    1. Procesar comandos Telegram
    2. Monitorear posiciones abiertas (SL/TP/trail)
    3. Si hay cupo: escanear mercado y abrir mejores señales
    4. Heartbeat / resumen diario cada hora
"""
import asyncio
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from config import cfg
from bingx_client import BingXClient
from scanner import Scanner
from strategy import Signal
from risk_manager import RiskManager
from position_manager import PositionManager
from telegram_client import TelegramClient

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


class EdgeBot:

    def __init__(self):
        self.client   = BingXClient(cfg.BINGX_API_KEY, cfg.BINGX_API_SECRET,
                                    cfg.BINGX_BASE_URL)
        self.tg       = TelegramClient(cfg.TELEGRAM_TOKEN, cfg.TELEGRAM_CHAT_ID)
        self.risk     = RiskManager(cfg)
        self.pos_mgr  = PositionManager(self.client, self.tg, self.risk)
        self.scanner  = Scanner(self.client)

        self._paused:        bool  = False
        self._scan_count:    int   = 0
        self._last_heartbeat: float = 0
        self._last_summary_day: str = ""
        self._stop:          bool  = False

    # ── Arranque ──────────────────────────────────────────────

    async def setup(self):
        log.info("══════════════════════════════════════════")
        log.info("  Edge Bot v5 — BingX Futures")
        log.info("  Modo: %s", "DRY RUN" if cfg.DRY_RUN else "REAL 🟢")
        log.info("  Leverage: %dx | Score min: %d | RR min: 1.5",
                 cfg.LEVERAGE, cfg.SCORE_MIN)
        log.info("══════════════════════════════════════════")

        # Verificar API
        ok, msg = await self.client.test_connection()
        if ok:
            log.info("✅ BingX OK: %s", msg)
        else:
            log.error("❌ BingX falló: %s", msg)
            await self.tg.error(
                f"API BingX no responde:\n{msg}\n\n"
                f"Verifica BINGX_API_KEY y BINGX_API_SECRET en Railway."
            )

        balance  = await self.client.get_balance()
        pos      = await self.client.get_positions()
        symbols  = await self.scanner.get_symbols()

        log.info("Balance: %.2f USDT | Posiciones: %d | Símbolos: %d",
                 balance, len(pos), len(symbols))

        if balance > 0:
            await self.tg.auth_ok(balance, len(pos))
        await self.tg.startup(cfg.DRY_RUN, len(symbols), balance)

    # ── Comandos Telegram ─────────────────────────────────────

    async def _process_commands(self):
        updates = await self.tg.get_updates()
        for upd in updates:
            msg  = upd.get("message", {})
            text = msg.get("text", "").strip().lower()
            cid  = str(msg.get("chat", {}).get("id", ""))
            if cid != cfg.TELEGRAM_CHAT_ID:
                continue

            if text == "/status":
                bal = await self.client.get_balance()
                await self.tg.status(
                    bal, self.pos_mgr.count(),
                    self.risk.summary_text(), self._scan_count)

            elif text == "/balance":
                bal = await self.client.get_balance()
                await self.tg._send(f"💰 Balance: `{bal:.2f} USDT`")

            elif text == "/trades":
                await self.tg._send(
                    f"📊 *Historial*\n{self.risk.summary_text()}")

            elif text == "/pausa":
                self._paused = True
                await self.tg.paused()

            elif text == "/reanudar":
                self._paused = False
                await self.tg.resumed()

            elif text == "/scan":
                await self.tg._send("🔍 Escaneando ahora...")
                sigs = await self.scanner.run()
                if sigs:
                    lines = [f"*Top señales ahora:*"]
                    for s in sigs[:5]:
                        e = "🟢" if s.side == "LONG" else "🔴"
                        lines.append(
                            f"{e} `{s.symbol}` {s.side} | "
                            f"Score `{s.score}` | RR `1:{s.rr:.2f}`\n"
                            f"   Entrada `{s.price:.6g}` | SL `{s.sl:.6g}` | TP1 `{s.tp1:.6g}`"
                        )
                    await self.tg._send("\n".join(lines))
                else:
                    await self.tg._send("🔍 Sin señales ahora.")

            elif text == "/stop":
                await self.tg._send("⛔ Deteniendo bot...")
                self._stop = True

    # ── Heartbeat / resumen diario ────────────────────────────

    async def _maybe_heartbeat(self, balance: float):
        now = time.time()
        if now - self._last_heartbeat >= 3600:
            self._last_heartbeat = now
            wr  = self.risk.win_rate() * 100
            pnl = self.risk.daily_pnl()
            await self.tg.heartbeat(
                balance, self.pos_mgr.count(),
                wr, pnl, self._scan_count)

    async def _maybe_daily_summary(self, balance: float):
        today = datetime.now(timezone.utc).date().isoformat()
        hour  = datetime.now(timezone.utc).hour
        if hour == 0 and self._last_summary_day != today:
            self._last_summary_day = today
            await self.tg.daily_summary(self.risk.summary_text())

    # ── Tick principal ────────────────────────────────────────

    async def tick(self):
        self._scan_count += 1

        # 1. Comandos Telegram
        await self._process_commands()
        if self._stop:
            return

        # 2. Monitorear posiciones SIEMPRE (aunque pausado)
        closed = await self.pos_mgr.monitor_all()
        if closed:
            log.info("🔒 %d posición(es) cerrada(s)", closed)

        # 3. Balance y heartbeat
        balance = await self.client.get_balance()
        await self._maybe_heartbeat(balance)
        await self._maybe_daily_summary(balance)

        if self._paused:
            log.info("⏸ Bot pausado — esperando /reanudar")
            return

        # 4. Verificar si podemos abrir más posiciones
        open_count = self.pos_mgr.count()
        can, reason = self.risk.can_trade(balance, open_count)
        if not can:
            log.info("❌ No operar: %s", reason)
            return

        # 5. Escanear mercado
        signals = await self.scanner.run()
        if not signals:
            log.info("Sin señales esta vela.")
            return

        # 6. Tomar la mejor señal y abrir
        best = signals[0]
        log.info("🎯 Mejor señal: %s %s score=%d RR=%.2f",
                 best.side, best.symbol, best.score, best.rr)

        # Calcular qty con Kelly
        qty = self.risk.position_size(balance, best)
        if qty <= 0:
            log.warning("qty=0 para %s — balance insuficiente", best.symbol)
            return

        if cfg.DRY_RUN:
            log.info("DRY_RUN — señal simulada: %s %s @ %.6g",
                     best.side, best.symbol, best.price)
            await self.tg.order_opened(best, qty, "DRY_RUN")
            return

        # Abrir posición real
        opened = await self.pos_mgr.open(best, qty)
        if opened:
            log.info("✅ Posición abierta: %s %s qty=%.4f",
                     best.side, best.symbol, qty)

    # ── Loop principal ────────────────────────────────────────

    async def run(self):
        await self.setup()

        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        log.info("🚀 Bot en marcha. Próxima vela en %ds.", cfg.SCAN_INTERVAL)
        await self.tg._send(
            f"🚀 *Bot corriendo*\n"
            f"Intervalo: `{cfg.SCAN_INTERVAL}s` | "
            f"Símbolos: `{len(await self.scanner.get_symbols())}`\n"
            f"Comandos: `/status` `/balance` `/trades` `/scan` `/pausa` `/stop`"
        )

        while not stop_event.is_set() and not self._stop:
            try:
                await self.tick()
            except Exception as e:
                log.exception("Error en tick: %s", e)
                await self.tg.error(f"Error inesperado: {str(e)[:200]}")

            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=cfg.SCAN_INTERVAL)
            except asyncio.TimeoutError:
                pass

        await self.client.close()
        log.info("Bot detenido correctamente.")


if __name__ == "__main__":
    asyncio.run(EdgeBot().run())
