"""
Risk Manager
Position sizing, stop-loss, take-profit, and max-drawdown controls.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TradeParams:
    quantity: float
    sl_price: Optional[float]
    tp_price: Optional[float]
    notional:  float          # USDT value of position


class RiskManager:
    def __init__(
        self,
        risk_pct: float       = 1.0,    # % of balance to risk per trade
        sl_pct:   float       = 1.5,    # stop-loss distance % from entry
        tp_ratio: float       = 2.0,    # take-profit = sl_pct * tp_ratio
        max_dd_pct: float     = 10.0,   # halt if drawdown > this %
        leverage: int         = 5,
        min_notional: float   = 5.0,    # BingX minimum order size in USDT
    ):
        self.risk_pct      = risk_pct / 100.0
        self.sl_pct        = sl_pct   / 100.0
        self.tp_ratio      = tp_ratio
        self.max_dd_pct    = max_dd_pct / 100.0
        self.leverage      = leverage
        self.min_notional  = min_notional
        self.peak_balance: Optional[float] = None

    def compute(
        self,
        balance: float,     # available USDT balance
        price:   float,     # entry price
        side:    str,       # "LONG" or "SHORT"
        qty_step: float = 0.001,  # minimum quantity step from exchange
        price_precision: int = 4,
    ) -> Optional[TradeParams]:
        """
        Returns TradeParams or None if position is too small / drawdown exceeded.
        """
        if self.peak_balance is None:
            self.peak_balance = balance

        self.peak_balance = max(self.peak_balance, balance)

        drawdown = (self.peak_balance - balance) / self.peak_balance
        if drawdown >= self.max_dd_pct:
            logger.warning(
                f"Max drawdown {drawdown*100:.1f}% exceeded {self.max_dd_pct*100:.1f}% — trading halted"
            )
            return None

        # Risk amount in USDT
        risk_usdt = balance * self.risk_pct

        # With leverage, notional = risk_usdt * leverage / sl_pct
        # Derived from: risk_usdt = notional * sl_pct / leverage
        notional = (risk_usdt * self.leverage) / self.sl_pct

        if notional < self.min_notional:
            logger.warning(f"Notional {notional:.2f} < minimum {self.min_notional} — skipping")
            return None

        # Quantity in base asset
        raw_qty = notional / price
        quantity = float(int(raw_qty / qty_step) * qty_step)

        if quantity <= 0:
            return None

        # Stop-loss / Take-profit prices
        sl_dist = price * self.sl_pct
        tp_dist = sl_dist * self.tp_ratio

        if side == "LONG":
            sl_price = round(price - sl_dist, price_precision)
            tp_price = round(price + tp_dist, price_precision)
        else:  # SHORT
            sl_price = round(price + sl_dist, price_precision)
            tp_price = round(price - tp_dist, price_precision)

        logger.info(
            f"RiskManager | side={side} balance={balance:.2f} "
            f"risk={risk_usdt:.2f}USDT notional={notional:.2f}USDT "
            f"qty={quantity} sl={sl_price} tp={tp_price}"
        )

        return TradeParams(
            quantity=quantity,
            sl_price=sl_price,
            tp_price=tp_price,
            notional=notional,
        )

    def trailing_stop(
        self,
        side: str,
        entry: float,
        current_price: float,
        current_sl: float,
        trail_pct: float = 0.8,   # activate when profit > trail_pct %
    ) -> float:
        """
        Moves stop-loss upward (LONG) or downward (SHORT) as price moves in our favor.
        Returns new stop-loss price.
        """
        trail = current_price * (trail_pct / 100.0)
        if side == "LONG":
            new_sl = current_price - trail
            return max(new_sl, current_sl)
        else:
            new_sl = current_price + trail
            return min(new_sl, current_sl)
