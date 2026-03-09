"""
╔══════════════════════════════════════════════════════════════════╗
║   EMA INSTITUTIONAL HUNTER BOT — BingX Perpetual Futures        ║
║   Estrategia: EMA 9/21/50/120/200 + VWAP Weekly + RSI + ATR     ║
║   Deploy: GitHub → Railway                                       ║
╚══════════════════════════════════════════════════════════════════╝

FLUJO PRINCIPAL:
  1. Cada CHECK_INTERVAL segundos:
     a. Descarga velas 2H desde BingX
     b. Calcula todos los indicadores
     c. Detecta señal (solo en vela cerrada)
  2. Si hay señal y no hay posición:
     a. Verifica risk manager
     b. Abre posición + SL + TP1 + TP2
     c. Notifica Telegram
  3. Si hay posición abierta:
     a. Monitorea precio actual
     b. Si TP1 tocado → mueve SL a breakeven
     c. Si SL/TP2 tocados → registra cierre
"""

import asyncio
import logging
import time
from datetime import datetime

import config as cfg
from exchange  import BingXClient
from strategy  import detect
from indicators import build
from risk      import RiskManager
from notifier  import Notifier

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ],
)
log = logging.getLogger(__name__)


class Bot:
    def __init__(self):
        self.client   = BingXClient()
        self.risk     = RiskManager()
        self.tg       = Notifier()
        self.cycles   = 0
        self._last_hb = time.time()    # heartbeat Telegram
        self._last_sig_bar = None      # evitar señal duplicada en misma vela

    # ─── Heartbeat ───────────────────────────────────────────
    async def _maybe_heartbeat(self, balance: float):
        if time.time() - self._last_hb >= 1800:   # cada 30 min
            self._last_hb = time.time()
            await self.tg.on_heartbeat(
                self.risk.stats(balance, self.cycles)
            )

    # ─── Monitor posición abierta ────────────────────────────
    async def _monitor_position(self, current_price: float, balance: float):
        pos = self.risk.open_pos
        if not pos:
            return

        d  = pos["direction"]
        sl = pos["sl"]
        t1 = pos["tp1"]
        t2 = pos["tp2"]

        hit_tp1 = (d == "LONG"  and current_price >= t1) or \
                  (d == "SHORT" and current_price <= t1)

        hit_tp2 = (d == "LONG"  and current_price >= t2) or \
                  (d == "SHORT" and current_price <= t2)

        hit_sl  = (d == "LONG"  and current_price <= sl) or \
                  (d == "SHORT" and current_price >= sl)

        # TP1 → breakeven
        if hit_tp1 and not self.risk.tp1_done:
            self.risk.on_tp1()
            await self.client.move_sl_to_breakeven(pos)
            await self.tg.on_tp1(pos)

        # TP2 → cierre completo
        elif hit_tp2:
            risk_usdt  = balance * (cfg.RISK_PCT / 100)
            pnl        = risk_usdt * cfg.TP2_RR
            self.risk.on_close(pnl, by_sl=False)
            await self.tg.on_tp2(pos, pnl)
            log.info(f"🏆 TP2 hit @ {current_price:.4f}")

        # SL
        elif hit_sl and not self.risk.tp1_done:
            risk_usdt = balance * (cfg.RISK_PCT / 100)
            self.risk.on_close(-risk_usdt, by_sl=True)
            await self.tg.on_sl(pos, risk_usdt)
            log.info(f"🛑 SL hit @ {current_price:.4f}")

        # SL en breakeven (tras TP1)
        elif hit_sl and self.risk.tp1_done:
            # Solo perdemos comisión, PnL ≈ 0
            self.risk.on_close(0.0, by_sl=False)
            await self.tg.send(
                f"🔒 *Cerrado en breakeven* (TP1 ya tomado)\n"
                f"`{d}` {cfg.SYMBOL} @ `{current_price:.4f}`"
            )
            log.info(f"🔒 Breakeven cierre @ {current_price:.4f}")

    # ─── Ciclo principal ─────────────────────────────────────
    async def cycle(self):
        self.cycles += 1

        # 1. Datos
        try:
            df = await self.client.fetch_ohlcv()
        except Exception as e:
            log.error(f"fetch_ohlcv error: {e}")
            return

        df = build(df)
        current_price = float(df["close"].iloc[-1])
        balance       = await self.client.get_balance()

        log.debug(f"Ciclo {self.cycles} | precio={current_price:.4f} | balance={balance:.2f}")

        # 2. Monitorear posición abierta
        if self.risk.in_position:
            await self._monitor_position(current_price, balance)
            await self._maybe_heartbeat(balance)
            return

        # 3. Detectar señal
        signal = detect(df)

        if signal is None:
            if self.cycles % 10 == 0:
                log.info(
                    f"Ciclo {self.cycles} | "
                    f"{cfg.SYMBOL} {current_price:.4f} | "
                    f"Sin señal | "
                    f"{self.risk.stats(balance, self.cycles)['daily_pnl']:+.2f} PnL"
                )
            await self._maybe_heartbeat(balance)
            return

        # Evitar señal duplicada en la misma vela
        if signal.bar_time == self._last_sig_bar:
            log.debug("Señal ya procesada para esta vela, ignorando")
            return
        self._last_sig_bar = signal.bar_time

        log.info(f"📡 Señal detectada: {signal}")

        # 4. Verificar riesgo
        can, reason = self.risk.can_enter(balance)
        if not can:
            log.warning(f"⛔ {reason}")
            if "Pausado" in reason:
                await self.tg.on_risk_pause(reason)
            return

        # 5. Abrir posición
        await self.tg.on_signal(signal, balance)
        pos = await self.client.open_position(signal)

        if pos:
            self.risk.on_open(pos, balance)
            log.info(f"✅ Posición abierta: {pos}")
        else:
            log.error("❌ Fallo al abrir posición")
            await self.tg.send("❌ *Error* al ejecutar la orden en BingX")

        await self._maybe_heartbeat(balance)

    # ─── Run ─────────────────────────────────────────────────
    async def run(self):
        log.info(
            f"\n{'═'*58}\n"
            f"  EMA INSTITUTIONAL HUNTER BOT\n"
            f"  Modo    : {'DRY RUN 🔵' if cfg.DRY_RUN else 'LIVE 🔴'}\n"
            f"  Par     : {cfg.SYMBOL}\n"
            f"  TF      : {cfg.TIMEFRAME}\n"
            f"  Leverage: {cfg.LEVERAGE}x\n"
            f"  Riesgo  : {cfg.RISK_PCT}% por trade\n"
            f"  TP1/TP2 : {cfg.TP1_RR}R / {cfg.TP2_RR}R\n"
            f"  Telegram: {'✅' if self.tg.enabled else '❌'}\n"
            f"{'═'*58}"
        )

        try:
            await self.client.init()
            balance = await self.client.get_balance()
            self.risk.start_balance = balance
            await self.tg.on_start(balance)

            while True:
                try:
                    await self.cycle()
                except Exception as e:
                    log.error(f"Error en ciclo: {e}", exc_info=True)

                await asyncio.sleep(cfg.CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("Bot detenido manualmente.")
        finally:
            balance = await self.client.get_balance()
            s = self.risk.stats(balance, self.cycles)
            await self.tg.send(
                f"🛑 *Bot detenido*\n"
                f"PnL diario: `${s['daily_pnl']:+.2f}`\n"
                f"Trades    : {s['trades_today']}\n"
                f"Ciclos    : {self.cycles}"
            )
            await self.client.close()


if __name__ == "__main__":
    asyncio.run(Bot().run())
