"""
Risk Management — Position sizing and trade guards
"""
import logging

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(
        self,
        risk_pct: float = 1.0,       # % of balance to risk per trade
        max_leverage: int = 10,
        min_balance_usdt: float = 20.0,
        max_open_trades: int = 1,
    ):
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
        self.min_balance_usdt = min_balance_usdt
        self.max_open_trades = max_open_trades

    def position_size(
        self,
        balance: float,
        entry: float,
        sl: float,
        leverage: int,
        contract_size: float = 1.0,   # BingX qty precision (usually 0.001 BTC etc)
    ) -> float:
        """
        Risk a fixed % of balance.
        Returns quantity in base asset units (e.g. BTC).
        """
        if balance < self.min_balance_usdt:
            logger.warning("Balance below minimum, skipping trade.")
            return 0.0

        risk_usdt = balance * (self.risk_pct / 100)
        sl_distance = abs(entry - sl)
        if sl_distance == 0:
            return 0.0

        # qty = risk_usdt / sl_distance  (no leverage needed — BingX margin auto-handles)
        qty = risk_usdt / sl_distance
        # Round down to contract precision
        qty = round(qty, 3)
        return max(qty, 0.0)

    def check_trade_allowed(self, open_trades: int, balance: float) -> tuple[bool, str]:
        if open_trades >= self.max_open_trades:
            return False, f"Max open trades reached ({self.max_open_trades})"
        if balance < self.min_balance_usdt:
            return False, f"Balance too low (${balance:.2f})"
        return True, "OK"

    def risk_reward(self, entry: float, sl: float, tp: float) -> float:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk == 0:
            return 0.0
        return reward / risk

    def validate_levels(self, direction: str, entry: float, sl: float, tp1: float) -> bool:
        if direction == "BUY":
            return sl < entry < tp1
        else:
            return tp1 < entry < sl
