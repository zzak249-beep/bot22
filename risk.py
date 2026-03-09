"""
risk.py — Gestión de riesgo: pérdida diaria, límite de trades,
          cooldown tras SL, estado de posición abierta.
"""
import time
import logging
from datetime import datetime

import config as cfg

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self):
        self.daily_pnl       = 0.0
        self.start_balance   = 0.0
        self.trades_today    = 0
        self.day_start       = datetime.now().date()
        self.paused          = False
        self.pause_reason    = ""
        self._cooldown_until = 0.0   # timestamp
        self.in_position     = False
        self.open_pos        = None  # dict con datos de la posición abierta
        self.tp1_done        = False

    # ─── Nuevo día ───────────────────────────────────────────
    def _check_new_day(self, balance: float):
        if datetime.now().date() != self.day_start:
            log.info("🔄 Nuevo día — reseteando contadores")
            self.daily_pnl    = 0.0
            self.trades_today = 0
            self.day_start    = datetime.now().date()
            self.paused       = False
            self.start_balance = balance

    # ─── ¿Puede operar? ──────────────────────────────────────
    def can_enter(self, balance: float) -> tuple[bool, str]:
        self._check_new_day(balance)

        if self.paused:
            return False, f"Pausado: {self.pause_reason}"

        if self.in_position:
            return False, "Ya hay posición abierta"

        if time.time() < self._cooldown_until:
            secs = int(self._cooldown_until - time.time())
            return False, f"Cooldown: {secs}s restantes"

        if self.trades_today >= cfg.MAX_TRADES_DAY:
            return False, f"Máximo {cfg.MAX_TRADES_DAY} trades/día alcanzado"

        if self.start_balance > 0:
            loss_pct = (-self.daily_pnl / self.start_balance) * 100
            if loss_pct >= cfg.MAX_DAILY_LOSS_PCT:
                self.paused       = True
                self.pause_reason = (
                    f"Pérdida diaria {loss_pct:.2f}% ≥ límite {cfg.MAX_DAILY_LOSS_PCT}%"
                )
                return False, self.pause_reason

        return True, ""

    # ─── Registrar apertura ──────────────────────────────────
    def on_open(self, pos: dict, balance: float):
        self.in_position   = True
        self.open_pos      = pos
        self.tp1_done      = False
        self.trades_today += 1
        if self.start_balance == 0:
            self.start_balance = balance
        log.info(f"📂 Posición abierta: {pos['direction']} @ {pos['entry']:.4f}")

    # ─── Registrar TP1 ───────────────────────────────────────
    def on_tp1(self):
        self.tp1_done = True
        log.info("✅ TP1 marcado — SL a breakeven activado")

    # ─── Registrar cierre ────────────────────────────────────
    def on_close(self, pnl: float, by_sl: bool = False):
        self.daily_pnl  += pnl
        self.in_position = False
        self.open_pos    = None
        self.tp1_done    = False

        if by_sl:
            # Cooldown tras stop loss (COOLDOWN_BARS × aprox minutos por vela)
            mins = cfg.COOLDOWN_BARS * _tf_minutes(cfg.TIMEFRAME)
            self._cooldown_until = time.time() + mins * 60
            log.info(f"⏳ Cooldown {mins}min tras SL")

        log.info(f"📁 Posición cerrada | PnL={pnl:+.2f} | PnL diario={self.daily_pnl:+.2f}")

    # ─── Stats snapshot ──────────────────────────────────────
    def stats(self, balance: float, cycles: int) -> dict:
        pos_str = None
        if self.in_position and self.open_pos:
            d   = self.open_pos["direction"]
            e   = self.open_pos["entry"]
            pos_str = f"{d} @ {e:.4f}"
        return {
            "balance"     : balance,
            "daily_pnl"   : self.daily_pnl,
            "trades_today": self.trades_today,
            "cycles"      : cycles,
            "position"    : pos_str,
        }


def _tf_minutes(tf: str) -> int:
    """Convierte '2h' → 120, '15m' → 15, '1d' → 1440."""
    tf = tf.lower().strip()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 1440
    return 120
