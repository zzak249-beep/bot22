"""
Risk Manager — EMA Bot
SL=1.5×ATR | TP1=1.5×ATR (50%) | TP2=2.5×ATR (50%)
Breakeven en TP1 | Trailing desde 1.5R | Anti-martingala
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class TradeParams:
    quantity:    float
    sl_price:    float
    tp1_price:   float
    tp2_price:   float
    qty_tp1:     float
    qty_tp2:     float
    notional:    float
    risk_usdt:   float
    r_distance:  float


@dataclass
class Tracker:
    trades: List[dict] = field(default_factory=list)
    wins:   int   = 0
    losses: int   = 0
    pnl:    float = 0.0
    streak: int   = 0

    @property
    def wr(self):
        return self.wins / max(self.wins + self.losses, 1)

    # ✅ Alias para compatibilidad con cualquier código que use .total_pnl
    @property
    def total_pnl(self):
        return self.pnl

    def record(self, pnl, symbol, side):
        self.pnl += pnl
        if pnl > 0:
            self.wins += 1
            self.streak = max(0, self.streak) + 1
        else:
            self.losses += 1
            self.streak = min(0, self.streak) - 1
        self.trades.append({"pnl": pnl, "symbol": symbol, "side": side})
        logger.info(f"Trade {side} {symbol} pnl={pnl:+.2f} wr={self.wr*100:.0f}%")

    def summary(self):
        if not self.trades:
            return "Sin trades aún"
        wl = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        ll = [t["pnl"] for t in self.trades if t["pnl"] <= 0]
        pf = abs(sum(wl) / sum(ll)) if ll and sum(ll) != 0 else 999
        return (
            f"Trades: {len(self.trades)} | WR: {self.wr*100:.0f}%\n"
            f"PnL: {self.pnl:+.2f}$ | PF: {pf:.2f}\n"
            f"Racha: {'+' if self.streak > 0 else ''}{self.streak}"
        )


class RiskManager:
    def __init__(self, risk_pct=1.0, atr_sl_mult=1.5, tp1_r=1.5, tp2_r=2.5,
                 max_dd_pct=10.0, leverage=5, min_notional=5.0):
        self.risk_pct      = risk_pct / 100
        self.sl_mult       = atr_sl_mult
        self.tp1_r         = tp1_r
        self.tp2_r         = tp2_r
        self.max_dd        = max_dd_pct / 100
        self.leverage      = leverage
        self.min_notional  = min_notional
        self.tracker       = Tracker()
        self.peak: Optional[float] = None

    def _factor(self):
        s = self.tracker.streak
        if s <= -4: return 0.30
        if s <= -3: return 0.50
        if s <= -2: return 0.70
        if s >= 4:  return 1.15
        return 1.0

    def compute(self, balance, price, side, atr, score=50,
                qty_step=0.001, price_precision=4, max_notional=None) -> Optional[TradeParams]:

        if self.peak is None: self.peak = balance
        self.peak = max(self.peak, balance)

        # Max drawdown check
        if (self.peak - balance) / max(self.peak, 1) >= self.max_dd:
            logger.warning("Max DD alcanzado — pausando"); return None

        sl_dist = atr * self.sl_mult
        sl_pct  = sl_dist / price if price > 0 else 0
        risk    = balance * self.risk_pct * self._factor()
        notional = (risk * self.leverage) / max(sl_pct, 0.0003)

        if max_notional and notional > max_notional:
            logger.info(f"Notional {notional:.1f} > max {max_notional} — reduciendo")
            notional = max_notional

        if notional < self.min_notional:
            logger.warning(
                f"Notional {notional:.2f} < mínimo {self.min_notional} | "
                f"balance={balance:.2f} atr={atr:.6f} sl_pct={sl_pct*100:.3f}%"
            )
            return None

        # ✅ FIX: calcular qty y verificar que no sea 0 por qty_step grande
        qty_raw = notional / price
        qty = float(int(qty_raw / qty_step) * qty_step)

        if qty <= 0:
            # Intentar con una sola unidad mínima
            min_qty_notional = qty_step * price
            if min_qty_notional > self.min_notional * 3:
                logger.warning(
                    f"qty_step demasiado grande: 1u = ${min_qty_notional:.2f} "
                    f"con balance=${balance:.2f} — rechazado"
                )
                return None
            qty = qty_step

        real_notional = qty * price
        if real_notional < self.min_notional:
            logger.warning(f"Notional real ${real_notional:.2f} < ${self.min_notional}")
            return None

        r = sl_dist
        if side == "LONG":
            sl = round(price - r,          price_precision)
            t1 = round(price + r * self.tp1_r, price_precision)
            t2 = round(price + r * self.tp2_r, price_precision)
        else:
            sl = round(price + r,          price_precision)
            t1 = round(price - r * self.tp1_r, price_precision)
            t2 = round(price - r * self.tp2_r, price_precision)

        q1 = float(int(qty * 0.50 / qty_step) * qty_step)
        q2 = round(qty - q1, 8)
        if q1 <= 0: q1 = qty_step
        if q2 <= 0: q2 = qty_step

        logger.info(
            f"Risk | {side} qty={qty} sl={sl} tp1={t1} tp2={t2} "
            f"notional=${real_notional:.2f} factor={self._factor():.2f}"
        )
        return TradeParams(
            quantity=qty, sl_price=sl, tp1_price=t1, tp2_price=t2,
            qty_tp1=q1, qty_tp2=q2, notional=real_notional,
            risk_usdt=risk, r_distance=r,
        )

    def breakeven(self, side, entry, price, sl, r, mult=1.05):
        """Mueve SL a breakeven cuando el precio alcanza entry + r * mult."""
        if side == "LONG"  and price >= entry + r * mult: return max(sl, entry)
        if side == "SHORT" and price <= entry - r * mult: return min(sl, entry)
        return sl

    # Alias para compatibilidad con llamadas antiguas
    def breakeven_sl(self, side, entry, price, sl, r, mult=1.05):
        return self.breakeven(side, entry, price, sl, r, mult)

    def trailing(self, side, price, sl, atr, entry=0, r=0, mult=1.8):
        """Trailing SL — solo activa cuando el precio supera entry + r * mult."""
        if r > 0:
            if side == "LONG"  and price < entry + r * mult: return sl
            if side == "SHORT" and price > entry - r * mult: return sl
        if side == "LONG":
            return max(sl, price - atr)
        else:
            return min(sl, price + atr)

    # Alias para compatibilidad con llamadas antiguas
    def trailing_sl(self, side, price, sl, atr, activate_r=1.5, entry=0, r=0):
        return self.trailing(side, price, sl, atr, entry=entry, r=r, mult=activate_r)
