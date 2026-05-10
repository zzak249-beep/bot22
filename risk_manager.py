"""
Risk Manager v3
- SL dinámico basado en ATR (más inteligente que % fijo)
- Take profit parcial: 50% en 1R, 50% en 2R
- Breakeven automático cuando toca 1R
- Trailing stop activado desde 1.5R
- Anti-martingala: reduce tamaño tras rachas perdedoras
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TradeParams:
    quantity:   float
    sl_price:   float
    tp1_price:  float   # primer TP (50% posición)
    tp2_price:  float   # segundo TP (50% posición)
    qty_tp1:    float   # cantidad para TP1
    qty_tp2:    float   # cantidad para TP2
    notional:   float
    risk_usdt:  float
    r_distance: float   # distancia en USDT de 1R


@dataclass
class PerformanceTracker:
    trades:       List[dict] = field(default_factory=list)
    wins:         int = 0
    losses:       int = 0
    total_pnl:    float = 0.0
    peak_balance: float = 0.0
    max_drawdown: float = 0.0
    streak:       int = 0    # + = racha ganadora, - = perdedora

    @property
    def win_rate(self): return self.wins / max(self.wins + self.losses, 1) * 100
    @property
    def total_trades(self): return self.wins + self.losses

    def record(self, pnl: float, symbol: str, side: str):
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
            self.streak = max(0, self.streak) + 1
        else:
            self.losses += 1
            self.streak = min(0, self.streak) - 1
        self.trades.append({"pnl": pnl, "symbol": symbol, "side": side})
        logger.info(
            f"Trade registrado | pnl={pnl:+.2f} wins={self.wins} "
            f"losses={self.losses} wr={self.win_rate:.0f}%"
        )

    def summary(self) -> str:
        if not self.trades:
            return "Sin trades aún"
        avg_win  = np.mean([t["pnl"] for t in self.trades if t["pnl"] > 0]) if self.wins else 0
        avg_loss = np.mean([t["pnl"] for t in self.trades if t["pnl"] <= 0]) if self.losses else 0
        pf = abs(avg_win * self.wins / (avg_loss * self.losses)) if self.losses and avg_loss else 999
        return (
            f"Trades: {self.total_trades} | WR: {self.win_rate:.0f}%\n"
            f"PnL: {self.total_pnl:+.2f}$ | PF: {pf:.2f}\n"
            f"Racha: {'+' if self.streak > 0 else ''}{self.streak}"
        )


class RiskManager:
    def __init__(
        self,
        risk_pct:    float = 1.0,    # % balance por trade
        atr_sl_mult: float = 1.5,    # SL = ATR × multiplicador
        tp1_r:       float = 1.0,    # TP1 en 1R (50% posición)
        tp2_r:       float = 2.5,    # TP2 en 2.5R (50% posición)
        max_dd_pct:  float = 10.0,
        leverage:    int   = 5,
        min_notional:float = 5.0,
        max_streak_loss: int = 3,    # reducir size tras N pérdidas seguidas
    ):
        self.risk_pct        = risk_pct / 100
        self.atr_sl_mult     = atr_sl_mult
        self.tp1_r           = tp1_r
        self.tp2_r           = tp2_r
        self.max_dd_pct      = max_dd_pct / 100
        self.leverage        = leverage
        self.min_notional    = min_notional
        self.max_streak_loss = max_streak_loss
        self.tracker         = PerformanceTracker()
        self.peak_balance:   Optional[float] = None

    def _size_factor(self) -> float:
        """Reduce posición ante rachas perdedoras (anti-martingala)"""
        streak = self.tracker.streak
        if streak <= -self.max_streak_loss:
            return 0.5    # mitad del tamaño normal
        if streak == -(self.max_streak_loss - 1):
            return 0.75
        return 1.0

    def compute(
        self,
        balance: float,
        price:   float,
        side:    str,
        atr:     float,          # ATR absoluto del símbolo
        qty_step: float = 0.001,
        price_precision: int = 4,
    ) -> Optional[TradeParams]:

        if self.peak_balance is None:
            self.peak_balance = balance
        self.peak_balance = max(self.peak_balance, balance)

        dd = (self.peak_balance - balance) / max(self.peak_balance, 1)
        if dd >= self.max_dd_pct:
            logger.warning(f"Max DD {dd*100:.1f}% — trading pausado")
            return None

        # SL basado en ATR (más adaptativo que % fijo)
        sl_dist   = atr * self.atr_sl_mult
        sl_pct    = sl_dist / price

        # Ajuste anti-martingala
        factor    = self._size_factor()
        risk_usdt = balance * self.risk_pct * factor

        # Notional: cuánto comprar para que si toca SL pierda risk_usdt
        # riesgo = notional × sl_pct / leverage
        notional  = (risk_usdt * self.leverage) / sl_pct

        if notional < self.min_notional:
            logger.warning(f"Notional {notional:.1f} < {self.min_notional} — skip")
            return None

        raw_qty  = notional / price
        quantity = float(int(raw_qty / qty_step) * qty_step)
        if quantity <= 0:
            return None

        r = sl_dist   # 1R en precio

        if side == "LONG":
            sl_price   = round(price - sl_dist,       price_precision)
            tp1_price  = round(price + r * self.tp1_r, price_precision)
            tp2_price  = round(price + r * self.tp2_r, price_precision)
        else:
            sl_price   = round(price + sl_dist,       price_precision)
            tp1_price  = round(price - r * self.tp1_r, price_precision)
            tp2_price  = round(price - r * self.tp2_r, price_precision)

        qty_tp1 = float(int(quantity * 0.5 / qty_step) * qty_step)
        qty_tp2 = round(quantity - qty_tp1, 8)

        logger.info(
            f"Risk | {side} notional={notional:.1f}$ qty={quantity} "
            f"sl={sl_price} tp1={tp1_price} tp2={tp2_price} "
            f"ATR={atr:.4f} factor={factor}"
        )

        return TradeParams(
            quantity=quantity, sl_price=sl_price,
            tp1_price=tp1_price, tp2_price=tp2_price,
            qty_tp1=qty_tp1, qty_tp2=qty_tp2,
            notional=notional, risk_usdt=risk_usdt,
            r_distance=r,
        )

    def breakeven_sl(self, side: str, entry: float, current_price: float,
                     sl: float, r: float, activate_r: float = 1.2) -> float:
        """Mueve SL a breakeven cuando el precio supera activate_r × R"""
        if side == "LONG":
            if current_price >= entry + r * activate_r:
                return max(sl, entry + 0.0001)
        else:
            if current_price <= entry - r * activate_r:
                return min(sl, entry - 0.0001)
        return sl

    def trailing_sl(self, side: str, current_price: float, sl: float,
                    trail_atr: float, activate_r: float = 1.5,
                    entry: float = 0, r: float = 0) -> float:
        """Trailing ATR-based desde activate_r × R"""
        if r > 0:
            if side == "LONG" and current_price < entry + r * activate_r:
                return sl
            if side == "SHORT" and current_price > entry - r * activate_r:
                return sl
        trail = trail_atr * 1.0
        if side == "LONG":
            return max(sl, current_price - trail)
        else:
            return min(sl, current_price + trail)
