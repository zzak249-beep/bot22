"""
main.py — Edge Bot v5 — Punto de entrada

Loop:
  cada 3 minutos:
    1. Procesar comandos Telegram
    2. Monitorear posiciones abiertas (SL/TP/trail)
    3. Si hay cupo: escanear mercado y abrir mejores señales
    4. Heartbeat / resumen diario cada hora

CAMBIOS v5.1:
  - Persistencia de trades en SQLite (trade_history.py)
  - Comando /rentabilidad para ver stats acumuladas
  - Comando /historial para ver resumen por día
  - record_open() al abrir, record_close() al cerrar posición
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
from trade_history import TradeHistory          # ← NUEVO

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
        self.history  = TradeHistory()          # ← NUEVO

        self._paused:         bool  = False
        self._scan_count:     int   = 0
        self._last_heartbeat: float = 0
        self._last_summary_day: str = ""
        self._stop:           bool  = False

    # ── Arranque ──────────────────────────────────────────────

    async def setup(self):
        log.info("══════════════════════════════════════════")
        log.info("  Edge Bot v5.1 — BingX Futures")
        log.info("  Modo: %s", "DRY RUN" if cfg.DRY_RUN else "REAL 🟢")
        log.info("  Leverage: %dx | Score min: %d | RR min: 1.5",
                 cfg.LEVERAGE, cfg.SCORE_MIN)
        log.info("  DB trades: %s", self.history.db_path)
        log.info("══════════════════════════════════════════")

        ok, msg = await self.client.test_connection()
        if ok:
            log.info("✅ BingX OK: %s", msg)
        else:
            log.error("❌ BingX falló: %s", msg)
            await self.tg.error(
                f"API BingX no responde:\n{msg}\n\n"
                f"Verifica BINGX_API_KEY y BINGX_API_SECRET en Railway."
            )

        balance = await self.client.get_balance()
        pos     = await self.client.get_positions()
        symbols = await self.scanner.get_symbols()

        log.info("Balance: %.2f USDT | Posiciones: %d | Símbolos: %d",
                 balance, len(pos), len(symbols))

        if balance > 0:
            await self.tg.auth_ok(balance, len(pos))
        await self.tg.startup(cfg.DRY_RUN, len(symbols), balance)

        # Mostrar stats al arrancar si hay historial
        stats = self.history.get_stats(days=30)
        if stats.get("total", 0) > 0:
            log.info("📊 Historial: %d trades | WR %.1f%% | PnL %.2f USDT",
                     stats["total"], stats["win_rate"], stats["total_pnl"])

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
                    f"📊 *Historial sesión*\n{self.risk.summary_text()}")

            # ── NUEVOS COMANDOS ──────────────────────────────
            elif text == "/rentabilidad":
                # Stats acumuladas de los últimos 30 días en DB
                text_30 = self.history.format_stats_text(days=30)
                await self.tg._send(text_30)

            elif text == "/historial":
                # Resumen día a día últimos 7 días
                text_7 = self.history.format_daily_text(days=7)
                await self.tg._send(text_7)

            elif text == "/abiertas":
                # Trades abiertos registrados en DB
                open_t = self.history.get_open_trades()
                if not open_t:
                    await self.tg._send("📂 Sin trades abiertos en DB.")
                else:
                    lines = ["📂 *Trades abiertos:*"]
                    for t in open_t:
                        e = "🟢" if t["side"] == "LONG" else "🔴"
                        dry = " *(DRY)*" if t["dry_run"] else ""
                        lines.append(
                            f"{e} #{t['id']} `{t['symbol']}` {t['side']}{dry}\n"
                            f"   Entrada `{t['entry']:.6g}` | Score `{t['score']}`"
                        )
                    await self.tg._send("\n".join(lines))
            # ────────────────────────────────────────────────

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
                    lines = ["*Top señales ahora:*"]
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
            # Resumen de sesión (en memoria)
            await self.tg.daily_summary(self.risk.summary_text())
            # Resumen persistido en DB ← NUEVO
            db_summary = self.history.format_daily_text(days=1)
            await self.tg._send(db_summary)

    # ── Tick principal ────────────────────────────────────────

    async def tick(self):
        self._scan_count += 1

        await self._process_commands()
        if self._stop:
            return

        # Monitorear posiciones — si cierra alguna, registrar en DB
        closed_list = await self.pos_mgr.monitor_all_with_result()  # ← ver nota abajo
        for closed in closed_list:
            # closed debe ser un dict: {trade_id, exit_price, pnl_usdt, reason}
            if closed.get("trade_id"):
                bal_now = await self.client.get_balance()
                self.history.record_close(
                    trade_id     = closed["trade_id"],
                    exit_price   = closed["exit_price"],
                    pnl_usdt     = closed["pnl_usdt"],
                    close_reason = closed.get("reason", "UNKNOWN"),
                    balance_after= bal_now,
                )

        balance = await self.client.get_balance()
        await self._maybe_heartbeat(balance)
        await self._maybe_daily_summary(balance)

        if self._paused:
            log.info("⏸ Bot pausado — esperando /reanudar")
            return

        open_count = self.pos_mgr.count()
        can, reason = self.risk.can_trade(balance, open_count)
        if not can:
            log.info("❌ No operar: %s", reason)
            return

        signals = await self.scanner.run()
        if not signals:
            log.info("Sin señales esta vela.")
            return

        best = signals[0]
        log.info("🎯 Mejor señal: %s %s score=%d RR=%.2f",
                 best.side, best.symbol, best.score, best.rr)

        qty = self.risk.position_size(balance, best)
        if qty <= 0:
            log.warning("qty=0 para %s — balance insuficiente", best.symbol)
            return

        if cfg.DRY_RUN:
            log.info("DRY_RUN — señal simulada: %s %s @ %.6g",
                     best.side, best.symbol, best.price)
            # Registrar en DB aunque sea simulado ← NUEVO
            trade_id = self.history.record_open(
                symbol      = best.symbol,
                side        = best.side,
                entry_price = best.price,
                sl_price    = best.sl,
                tp1_price   = best.tp1,
                qty         = qty,
                score       = best.score,
                rr          = best.rr,
                is_dry_run  = True,
            )
            await self.tg.order_opened(best, qty, f"DRY_RUN #{trade_id}")
            return

        # Abrir posición real ← registrar en DB
        opened = await self.pos_mgr.open(best, qty)
        if opened:
            trade_id = self.history.record_open(
                symbol      = best.symbol,
                side        = best.side,
                entry_price = best.price,
                sl_price    = best.sl,
                tp1_price   = best.tp1,
                qty         = qty,
                score       = best.score,
                rr          = best.rr,
                is_dry_run  = False,
            )
            # Pasar trade_id al position_manager para usarlo al cerrar
            self.pos_mgr.set_db_trade_id(best.symbol, trade_id)
            log.info("✅ Posición abierta: %s %s qty=%.4f (DB #%d)",
                     best.side, best.symbol, qty, trade_id)

    # ── Loop principal ────────────────────────────────────────

    async def run(self):
        await self.setup()

        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        log.info("🚀 Bot en marcha. Próxima vela en %ds.", cfg.SCAN_INTERVAL)
        await self.tg._send(
            f"🚀 *Bot v5.1 corriendo*\n"
            f"Intervalo: `{cfg.SCAN_INTERVAL}s` | "
            f"Símbolos: `{len(await self.scanner.get_symbols())}`\n"
            f"Comandos: `/status` `/balance` `/trades` `/scan`\n"
            f"📊 `/rentabilidad` `/historial` `/abiertas` `/pausa` `/stop`"
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
