"""
risk_manager.py — Gestión de riesgo profesional v5

Ventajas sobre bots normales:
  1. Kelly Criterion parcial — tamaño óptimo según win rate histórico real
  2. Drawdown adaptativo — reduce size 50% tras 2% DD, pausa tras 5%
  3. Límite de pérdida diaria y semanal independientes
  4. Cierre parcial en TP1 (50%), mueve SL a breakeven automáticamente
  5. Registro de todas las operaciones para auto-calibración
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from strategy import Signal

log = logging.getLogger("risk")


@dataclass
class TradeRecord:
    symbol:    str
    side:      str
    entry:     float
    exit:      float
    pnl_usdt:  float
    pnl_pct:   float
    score:     int
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))


class RiskManager:
    def __init__(self, cfg):
        self.cfg     = cfg
        self._trades: list[TradeRecord] = []
        self._daily_pnl:   dict[str, float] = {}
        self._weekly_pnl:  dict[str, float] = {}

    # ── Registro ──────────────────────────────────────────────

    def record(self, symbol: str, side: str, entry: float,
               exit_price: float, qty: float, leverage: int, score: int):
        if side == "LONG":
            pnl_pct  = (exit_price - entry) / entry * 100 * leverage
            pnl_usdt = (exit_price - entry) * qty * leverage
        else:
            pnl_pct  = (entry - exit_price) / entry * 100 * leverage
            pnl_usdt = (entry - exit_price) * qty * leverage

        rec = TradeRecord(symbol=symbol, side=side, entry=entry,
                          exit=exit_price, pnl_usdt=pnl_usdt,
                          pnl_pct=pnl_pct, score=score)
        self._trades.append(rec)

        today = datetime.now(timezone.utc).date().isoformat()
        week  = datetime.now(timezone.utc).strftime("%Y-W%W")
        self._daily_pnl[today]  = self._daily_pnl.get(today, 0) + pnl_usdt
        self._weekly_pnl[week]  = self._weekly_pnl.get(week, 0) + pnl_usdt

        log.info("📝 Trade registrado: %s %s PnL=%.2f USDT (%.1f%%)",
                 side, symbol, pnl_usdt, pnl_pct)
        return pnl_usdt

    # ── Estadísticas en tiempo real ───────────────────────────

    def win_rate(self, last_n: int = 20) -> float:
        recent = self._trades[-last_n:]
        if not recent:
            return 0.55   # asumir 55% si no hay historial
        wins = sum(1 for t in recent if t.pnl_usdt > 0)
        return wins / len(recent)

    def avg_win_loss_ratio(self, last_n: int = 20) -> float:
        recent = self._trades[-last_n:]
        wins   = [t.pnl_usdt for t in recent if t.pnl_usdt > 0]
        losses = [abs(t.pnl_usdt) for t in recent if t.pnl_usdt < 0]
        if not wins or not losses:
            return 1.5
        return (sum(wins) / len(wins)) / (sum(losses) / len(losses))

    def daily_pnl(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        return self._daily_pnl.get(today, 0.0)

    def weekly_pnl(self) -> float:
        week = datetime.now(timezone.utc).strftime("%Y-W%W")
        return self._weekly_pnl.get(week, 0.0)

    def total_pnl(self) -> float:
        return sum(t.pnl_usdt for t in self._trades)

    def summary_text(self) -> str:
        wr   = self.win_rate() * 100
        wpnl = self.daily_pnl()
        tot  = self.total_pnl()
        n    = len(self._trades)
        return (f"Trades: `{n}` | Win Rate: `{wr:.1f}%`\n"
                f"PnL hoy: `{wpnl:+.2f} USDT` | Total: `{tot:+.2f} USDT`")

    # ── Validación antes de abrir ─────────────────────────────

    def can_trade(self, balance: float, open_count: int) -> tuple[bool, str]:
        cfg = self.cfg

        if balance < 20:
            return False, f"Balance insuficiente: {balance:.2f} USDT"

        if open_count >= cfg.MAX_OPEN_TRADES:
            return False, f"Máx posiciones ({cfg.MAX_OPEN_TRADES})"

        # Drawdown diario
        dd_pct = abs(min(self.daily_pnl(), 0)) / max(balance, 1) * 100
        if dd_pct >= cfg.MAX_DD_DAY_PCT:
            return False, f"DD diario {dd_pct:.1f}% ≥ {cfg.MAX_DD_DAY_PCT}%"

        # Drawdown semanal
        wk_pct = abs(min(self.weekly_pnl(), 0)) / max(balance, 1) * 100
        if wk_pct >= cfg.MAX_DD_WEEK_PCT:
            return False, f"DD semanal {wk_pct:.1f}% ≥ {cfg.MAX_DD_WEEK_PCT}%"

        return True, "ok"

    # ── Tamaño de posición — Kelly Criterion parcial ──────────

    def position_size(self, balance: float, sig: Signal) -> float:
        """
        Kelly fracción = WR - (1-WR)/RR
        Usamos Kelly × 0.25 (cuarto-Kelly) para máxima seguridad.
        Reducción adicional si hay drawdown reciente.
        """
        cfg = self.cfg
        wr  = self.win_rate(last_n=20)
        rr  = sig.rr if sig.rr > 0 else 1.5

        # Kelly fracción
        kelly  = wr - (1 - wr) / rr
        kelly  = max(0.01, min(kelly, 0.25))   # clamp 1%-25%
        frac   = kelly * 0.25                  # quarter-Kelly

        # Reducir size si hay drawdown
        dd_pct = abs(min(self.daily_pnl(), 0)) / max(balance, 1) * 100
        if dd_pct >= 2.0:
            frac *= 0.5
            log.info("Size reducido 50%% por DD=%.1f%%", dd_pct)
        if dd_pct >= 3.5:
            frac *= 0.5
            log.info("Size reducido 75%% por DD=%.1f%%", dd_pct)

        risk_usdt   = balance * frac
        sl_distance = abs(sig.price - sig.sl)
        if sl_distance == 0:
            return 0.0

        qty = (risk_usdt * cfg.LEVERAGE) / sig.price
        max_by_sl = (risk_usdt) / sl_distance
        qty = min(qty, max_by_sl)

        log.info("Kelly=%.3f frac=%.3f | Risk=%.2f USDT | qty=%.4f",
                 kelly, frac, risk_usdt, qty)
        return round(max(qty, 0), 4)
