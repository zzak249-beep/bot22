"""
PositionManager — volob-standalone.
Construido con las lecciones ya aprendidas en el resto del fleet:
side-aware desde el día 1 (no hardcodeado a un solo lado), confirma
la posición tras abrir (no confía en code=0 como éxito garantizado),
y persiste todo vía state.py.
"""
import logging
import math
import time

import config
import state
from bingx_client import BingXClient

log = logging.getLogger("pos_mgr")


class PositionManager:
    def __init__(self, client: BingXClient):
        self.client = client

    # ── Symbol info ───────────────────────────────────────────

    def _sym_info(self, symbol: str) -> dict:
        try:
            return self.client.get_symbol_info(symbol)
        except Exception:
            return {}

    def _round_qty(self, symbol: str, qty: float) -> float:
        info   = self._sym_info(symbol)
        scale  = int(info.get("quantityScale", 3))
        factor = 10 ** scale
        return math.floor(qty * factor) / factor

    def _min_qty(self, symbol: str) -> float:
        info = self._sym_info(symbol)
        return float(info.get("tradeMinQuantity", 0.001))

    # ── Position sizing ───────────────────────────────────────

    def calc_qty(self, symbol: str, mark_price: float,
                 atr: float, equity: float) -> float | None:
        if mark_price <= 0 or atr <= 0:
            return None

        if config.FIXED_NOTIONAL_USDT > 0:
            qty = config.FIXED_NOTIONAL_USDT / mark_price
        else:
            risk_usdt = equity * (config.RISK_PCT / 100.0)
            sl_usdt   = atr * config.SL_ATR_MULT
            qty       = risk_usdt / sl_usdt if sl_usdt > 0 else 0.0

        qty = min(qty, config.MAX_NOTIONAL_USDT / mark_price)
        qty = self._round_qty(symbol, qty)
        qty = max(qty, self._min_qty(symbol))

        actual = qty * mark_price
        if actual < config.MIN_NOTIONAL_USDT * 0.9:
            log.warning(f"SKIP {symbol}: notional {actual:.2f} < min {config.MIN_NOTIONAL_USDT}")
            return None
        return qty

    # ── Position queries ──────────────────────────────────────

    def get_position(self, symbol: str, side: str) -> dict | None:
        for p in self.client.get_positions(symbol):
            if p["positionSide"] == side:
                return p
        return None

    def has_position(self, symbol: str, side: str) -> bool:
        return self.get_position(symbol, side) is not None

    def count_open(self) -> int:
        return len(state.get_tracked_positions())   # solo propias

    # ── Max hold ──────────────────────────────────────────────

    def is_max_hold_expired(self, symbol: str, side: str) -> bool:
        return state.is_max_hold_expired(symbol, side, config.MAX_HOLD_MINUTES)

    # ── Entries ───────────────────────────────────────────────

    def open_long(self, symbol: str, qty: float, atr: float) -> bool:
        try:
            state.clear(symbol, "LONG")
            self.client.set_leverage(symbol, config.LEVERAGE)
            self.client.place_market_order(symbol, "BUY", "LONG", qty)

            time.sleep(1.0)
            confirmed = self.get_position(symbol, "LONG")
            if not confirmed:
                log.error(f"open_long {symbol}: orden aceptada pero SIN posición tras 1s (qty={qty})")
                return False

            state.save_entry(symbol, "LONG")
            state.save_entry_details(symbol, "LONG", confirmed["entryPrice"], confirmed["size"])
            state.set_tp1_hit(symbol, "LONG", False)
            state.set_be_moved(symbol, "LONG", False)
            mark = self.client.get_mark_price(symbol)
            init_stop = mark - atr * config.TRAIL_DISTANCE_ATR
            state.save_trail(symbol, "LONG", init_stop)
            log.info(f"OPEN LONG  {symbol}  qty={confirmed['size']}  stop={init_stop:.6g}")
            return True
        except Exception as e:
            log.error(f"open_long {symbol}: {e}")
            return False

    def open_short(self, symbol: str, qty: float, atr: float) -> bool:
        try:
            state.clear(symbol, "SHORT")
            self.client.set_leverage(symbol, config.LEVERAGE)
            self.client.place_market_order(symbol, "SELL", "SHORT", qty)

            time.sleep(1.0)
            confirmed = self.get_position(symbol, "SHORT")
            if not confirmed:
                log.error(f"open_short {symbol}: orden aceptada pero SIN posición tras 1s (qty={qty})")
                return False

            state.save_entry(symbol, "SHORT")
            state.save_entry_details(symbol, "SHORT", confirmed["entryPrice"], confirmed["size"])
            state.set_tp1_hit(symbol, "SHORT", False)
            state.set_be_moved(symbol, "SHORT", False)
            mark = self.client.get_mark_price(symbol)
            init_stop = mark + atr * config.TRAIL_DISTANCE_ATR
            state.save_trail(symbol, "SHORT", init_stop)
            log.info(f"OPEN SHORT {symbol}  qty={confirmed['size']}  stop={init_stop:.6g}")
            return True
        except Exception as e:
            log.error(f"open_short {symbol}: {e}")
            return False

    # ── Exits ─────────────────────────────────────────────────

    def close_long(self, symbol: str, qty: float, reason: str = "") -> bool:
        try:
            self.client.cancel_all_open_orders(symbol)
            self.client.close_position(symbol, "LONG", qty)
            state.clear(symbol, "LONG")
            log.info(f"CLOSE LONG  {symbol}  qty={qty}  [{reason}]")
            return True
        except Exception as e:
            log.error(f"close_long {symbol}: {e}")
            return False

    def close_short(self, symbol: str, qty: float, reason: str = "") -> bool:
        try:
            self.client.cancel_all_open_orders(symbol)
            self.client.close_position(symbol, "SHORT", qty)
            state.clear(symbol, "SHORT")
            log.info(f"CLOSE SHORT {symbol}  qty={qty}  [{reason}]")
            return True
        except Exception as e:
            log.error(f"close_short {symbol}: {e}")
            return False

    # ── Trail stop ────────────────────────────────────────────

    def tick_trail(self, symbol: str, side: str,
                   price: float, atr: float) -> tuple:
        current = state.get_trail(symbol, side)
        if current is None:
            new_stop = (price - atr * config.TRAIL_DISTANCE_ATR if side == "LONG"
                        else price + atr * config.TRAIL_DISTANCE_ATR)
            state.save_trail(symbol, side, new_stop)
            return new_stop, False

        if side == "LONG":
            new_stop = max(current, price - atr * config.TRAIL_DISTANCE_ATR)
            state.save_trail(symbol, side, new_stop)
            return new_stop, price <= new_stop
        else:
            new_stop = min(current, price + atr * config.TRAIL_DISTANCE_ATR)
            state.save_trail(symbol, side, new_stop)
            return new_stop, price >= new_stop

    # ── TP/SL placement ────────────────────────────────────────

    def place_tp_sl(self, symbol: str, side: str,
                    entry_price: float, qty: float, atr: float, tp_price: float):
        """SL + TP de la estrategia (tp_price ya calculado por get_signal)."""
        if side == "LONG":
            sl_price       = entry_price - atr * config.SL_ATR_MULT
            tp1_order_side = "SELL"
        else:
            sl_price       = entry_price + atr * config.SL_ATR_MULT
            tp1_order_side = "BUY"

        tp_qty = self._round_qty(symbol, qty * 0.5)

        try:
            self.client.cancel_all_open_orders(symbol)
        except Exception as e:
            log.warning(f"cancel_all {symbol}: {e}")

        try:
            self.client.place_stop_market(symbol, side, sl_price, qty)
            log.info(f"SL colocado {symbol}  sl={sl_price:.6g}")
        except Exception as e:
            log.error(f"place_sl {symbol}: {e}")

        if tp_qty >= self._min_qty(symbol):
            try:
                self.client.place_limit_order(symbol, tp1_order_side, side, tp_price, tp_qty)
                log.info(f"TP colocado {symbol}  tp={tp_price:.6g}  qty={tp_qty}")
            except Exception as e:
                log.error(f"place_tp {symbol}: {e}")
