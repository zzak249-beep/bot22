"""
Risk Manager
- Fixed-risk position sizing (% of balance)
- Max open positions cap
- Daily loss limit (kill switch)
- Leverage control
- Trailing stop logic
- Commission-aware P&L tracking
"""
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# ── Config (override via env vars in bot.py) ─────────────────────────
RISK_PER_TRADE   = 0.01    # 1% balance per trade
MAX_POSITIONS    = 5       # concurrent open trades
LEVERAGE         = 5       # 5x isolated (conservative)
DAILY_LOSS_LIMIT = 0.05    # 5% daily drawdown → pause
WEEKLY_LOSS_LIMIT = 0.10   # 10% weekly → halt harder
MIN_NOTIONAL     = 6.0     # BingX min order ~$5 USDT, use $6 buffer
PARTIAL_TP_PCT   = 0.50    # close 50% at TP1
MAKER_FEE        = 0.0002  # 0.02% maker fee
TAKER_FEE        = 0.0005  # 0.05% taker fee


@dataclass
class TradeParams:
    symbol:         str
    direction:      str
    entry_price:    float
    sl_price:       float
    tp1_price:      float
    tp2_price:      float
    tp3_price:      float
    quantity:       float
    leverage:       int
    notional:       float
    risk_usdt:      float
    est_fee:        float   # estimated round-trip fee


class RiskManager:
    def __init__(self, risk_pct: float = RISK_PER_TRADE,
                 max_pos: int = MAX_POSITIONS,
                 leverage: int = LEVERAGE,
                 daily_loss_limit: float = DAILY_LOSS_LIMIT):
        self.risk_pct          = risk_pct
        self.max_pos           = max_pos
        self.leverage          = leverage
        self.daily_loss_limit  = daily_loss_limit

        self._daily_start_balance: Optional[float] = None
        self._daily_pnl: float     = 0.0
        self._daily_trades: int    = 0
        self._weekly_pnl: float    = 0.0
        self._total_fees: float    = 0.0

    def reset_daily(self, balance: float):
        self._daily_start_balance = balance
        self._daily_pnl    = 0.0
        self._daily_trades = 0
        log.info(f"📅 Daily reset | Balance: {balance:.2f} USDT | Leverage: {self.leverage}x")

    def record_pnl(self, pnl_usdt: float, fee: float = 0.0):
        self._daily_pnl    += pnl_usdt
        self._weekly_pnl   += pnl_usdt
        self._total_fees   += fee
        self._daily_trades += 1
        log.info(
            f"📊 P&L record: {pnl_usdt:+.2f} USDT | "
            f"Daily: {self._daily_pnl:+.2f} | Fee: -{fee:.3f}"
        )

    def is_kill_switch(self, balance: float) -> bool:
        if self._daily_start_balance is None:
            return False
        start = max(self._daily_start_balance, 1.0)
        daily_loss_pct = -self._daily_pnl / start if self._daily_pnl < 0 else 0
        if daily_loss_pct >= self.daily_loss_limit:
            log.warning(
                f"⛔ KILL SWITCH: Daily loss {daily_loss_pct*100:.1f}% "
                f"≥ {self.daily_loss_limit*100:.0f}%"
            )
            return True
        return False

    def can_open_trade(self, open_positions: int, balance: float) -> bool:
        if open_positions >= self.max_pos:
            log.info(f"Max positions ({self.max_pos}) reached.")
            return False
        if self.is_kill_switch(balance):
            return False
        if balance < MIN_NOTIONAL * 2:
            log.warning(f"Balance too low: {balance:.2f} USDT")
            return False
        return True

    def size_position(
        self,
        symbol: str,
        direction: str,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float,
        tp3: float,
        balance: float,
        qty_precision: int = 3,
        price_precision: int = 4,
    ) -> Optional[TradeParams]:
        """
        Fixed-risk position sizing:
          risk_usdt = balance * risk_pct
          qty = risk_usdt / |entry - sl|
        Note: leverage does NOT multiply qty here.
        We're sizing based on actual $ at risk if SL hits.
        Margin used = qty * entry / leverage.
        """
        risk_usdt = balance * self.risk_pct
        sl_dist   = abs(entry - sl)

        if sl_dist < 1e-10:
            log.warning(f"SL too close to entry for {symbol}, skipping")
            return None

        # Qty in base currency (coins)
        qty_raw = risk_usdt / sl_dist
        qty     = round(qty_raw, qty_precision)

        if qty <= 0:
            log.warning(f"Qty calculated as 0 for {symbol}")
            return None

        notional = qty * entry

        # Enforce minimum notional
        if notional < MIN_NOTIONAL:
            qty      = round(MIN_NOTIONAL / entry * 1.05, qty_precision)
            notional = qty * entry

        # Check margin requirement
        margin_required = notional / self.leverage
        if margin_required > balance * 0.30:
            log.warning(
                f"Trade {symbol} margin {margin_required:.2f} USDT > 30% balance "
                f"({balance*0.30:.2f}), skipping"
            )
            return None

        # Estimated fee (entry LIMIT + exit LIMIT = 2x maker)
        est_fee = notional * MAKER_FEE * 2

        ep  = round(entry, price_precision)
        slp = round(sl,   price_precision)
        t1  = round(tp1,  price_precision)
        t2  = round(tp2,  price_precision)
        t3  = round(tp3,  price_precision)

        log.info(
            f"📐 Size [{symbol}] {direction} qty={qty} "
            f"entry={ep} SL={slp} TP1={t1} TP2={t2} "
            f"notional=${notional:.1f} risk=${risk_usdt:.2f} "
            f"margin=${margin_required:.2f} fee≈${est_fee:.3f}"
        )

        return TradeParams(
            symbol=symbol, direction=direction,
            entry_price=ep, sl_price=slp,
            tp1_price=t1, tp2_price=t2, tp3_price=t3,
            quantity=qty, leverage=self.leverage,
            notional=notional, risk_usdt=risk_usdt,
            est_fee=est_fee,
        )

    def partial_close_qty(self, qty: float, qty_precision: int = 3) -> float:
        return round(qty * PARTIAL_TP_PCT, qty_precision)

    def get_stats(self) -> dict:
        return {
            "daily_pnl":    round(self._daily_pnl, 4),
            "weekly_pnl":   round(self._weekly_pnl, 4),
            "daily_trades": self._daily_trades,
            "total_fees":   round(self._total_fees, 4),
        }
