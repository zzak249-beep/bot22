"""
exchange.py — Wrapper BingX Perpetual Futures via ccxt
Gestiona: datos OHLCV, balance, apertura/cierre de posiciones,
órdenes SL/TP, y breakeven tras TP1.
"""
import asyncio
import logging
import ccxt.async_support as ccxt

import config as cfg
from strategy import Signal

log = logging.getLogger(__name__)


class BingXClient:
    def __init__(self):
        self.ex = ccxt.bingx({
            "apiKey"         : cfg.BINGX_API_KEY,
            "secret"         : cfg.BINGX_API_SECRET,
            "enableRateLimit": True,
            "options"        : {"defaultType": "swap"},   # perpetual futures
        })
        self._markets_loaded = False

    # ─── Init ────────────────────────────────────────────────
    async def init(self):
        await self.ex.load_markets()
        self._markets_loaded = True
        if not cfg.DRY_RUN:
            await self._set_leverage()
        log.info(f"✅ BingX conectado | símbolo={cfg.SYMBOL} | leverage={cfg.LEVERAGE}x")

    async def _set_leverage(self):
        try:
            await self.ex.set_leverage(cfg.LEVERAGE, cfg.SYMBOL)
        except Exception as e:
            log.warning(f"set_leverage: {e}")

    # ─── OHLCV ───────────────────────────────────────────────
    async def fetch_ohlcv(self):
        """Retorna DataFrame con columnas open/high/low/close/volume e índice datetime."""
        import pandas as pd
        bars = await self.ex.fetch_ohlcv(
            cfg.SYMBOL, cfg.TIMEFRAME, limit=cfg.CANDLES_NEEDED
        )
        df = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df.set_index("ts", inplace=True)
        return df

    # ─── Balance ─────────────────────────────────────────────
    async def get_balance(self) -> float:
        if cfg.DRY_RUN:
            return 1000.0
        try:
            bal = await self.ex.fetch_balance()
            return float(bal["USDT"]["free"])
        except Exception as e:
            log.error(f"get_balance: {e}")
            return 0.0

    # ─── Posición actual ─────────────────────────────────────
    async def get_position(self) -> dict | None:
        if cfg.DRY_RUN:
            return None
        try:
            positions = await self.ex.fetch_positions([cfg.SYMBOL])
            for p in positions:
                if abs(float(p.get("contracts", 0))) > 0:
                    return p
        except Exception as e:
            log.error(f"get_position: {e}")
        return None

    # ─── Tamaño de posición ──────────────────────────────────
    async def calc_qty(self, signal: Signal) -> float:
        """
        Calcula contratos basado en riesgo % de balance.
        qty = (balance × risk%) / (entry - sl) × leverage_ajuste
        """
        balance   = await self.get_balance()
        risk_usdt = balance * (cfg.RISK_PCT / 100)
        price_risk = abs(signal.entry - signal.sl)
        if price_risk == 0:
            return 0.0

        # Contratos = dinero a arriesgar / pérdida por contrato
        qty = risk_usdt / price_risk

        # Redondear al mínimo del mercado
        try:
            market = self.ex.market(cfg.SYMBOL)
            precision = market.get("precision", {}).get("amount", 3)
            qty = float(self.ex.amount_to_precision(cfg.SYMBOL, qty))
        except Exception:
            qty = round(qty, 3)

        log.info(f"📐 Balance={balance:.2f} | RiskUSDT={risk_usdt:.2f} | qty={qty}")
        return qty

    # ─── OPEN POSITION ───────────────────────────────────────
    async def open_position(self, signal: Signal) -> dict | None:
        qty = await self.calc_qty(signal)
        if qty <= 0:
            log.error("qty calculado = 0, cancelando apertura")
            return None

        side       = "buy"  if signal.direction == "LONG"  else "sell"
        sl_side    = "sell" if signal.direction == "LONG"  else "buy"
        tp1_side   = sl_side
        tp2_side   = sl_side

        if cfg.DRY_RUN:
            log.info(f"[DRY] OPEN {signal.direction} qty={qty} @ {signal.entry:.4f}")
            return {
                "id": "DRY-001", "qty": qty, "entry": signal.entry,
                "sl": signal.sl, "tp1": signal.tp1, "tp2": signal.tp2,
                "direction": signal.direction,
            }

        try:
            # ── Orden de mercado ─────────────────────────────
            order = await self.ex.create_order(
                cfg.SYMBOL, "market", side, qty,
                params={"positionSide": "LONG" if side == "buy" else "SHORT"},
            )
            log.info(f"✅ Entrada ejecutada: {order['id']}")

            avg_entry = float(order.get("average") or signal.entry)

            # ── Stop Loss ────────────────────────────────────
            await self._place_sl(sl_side, qty, signal.sl)

            # ── TP1 (50%) ────────────────────────────────────
            qty_tp1 = round(qty * cfg.TP1_QTY_PCT / 100, 3)
            await self._place_tp(tp1_side, qty_tp1, signal.tp1)

            # ── TP2 (50% restante) ───────────────────────────
            qty_tp2 = qty - qty_tp1
            await self._place_tp(tp2_side, qty_tp2, signal.tp2)

            return {
                "id": order["id"], "qty": qty, "entry": avg_entry,
                "sl": signal.sl, "tp1": signal.tp1, "tp2": signal.tp2,
                "direction": signal.direction,
            }

        except Exception as e:
            log.error(f"open_position error: {e}")
            return None

    async def _place_sl(self, side: str, qty: float, price: float):
        try:
            await self.ex.create_order(
                cfg.SYMBOL, "stop_market", side, qty,
                params={"stopPrice": price, "reduceOnly": True},
            )
            log.info(f"  SL colocado @ {price:.4f}")
        except Exception as e:
            log.error(f"  SL error: {e}")

    async def _place_tp(self, side: str, qty: float, price: float):
        try:
            await self.ex.create_order(
                cfg.SYMBOL, "take_profit_market", side, qty,
                params={"stopPrice": price, "reduceOnly": True},
            )
            log.info(f"  TP colocado @ {price:.4f}")
        except Exception as e:
            log.error(f"  TP error: {e}")

    # ─── BREAKEVEN ───────────────────────────────────────────
    async def move_sl_to_breakeven(self, position: dict):
        """Mueve SL al entry después de que TP1 es tocado."""
        if cfg.DRY_RUN:
            log.info(f"[DRY] SL → breakeven @ {position['entry']:.4f}")
            return
        try:
            # Cancelar SL existente
            await self.cancel_open_orders()
            # Colocar nuevo SL en entry
            side = "sell" if position["direction"] == "LONG" else "buy"
            qty  = float(position.get("qty", 0))
            await self._place_sl(side, qty, position["entry"])
            log.info(f"  🔒 SL movido a breakeven @ {position['entry']:.4f}")
        except Exception as e:
            log.error(f"move_sl_to_breakeven error: {e}")

    # ─── CANCEL ORDERS ───────────────────────────────────────
    async def cancel_open_orders(self):
        if cfg.DRY_RUN:
            return
        try:
            await self.ex.cancel_all_orders(cfg.SYMBOL)
        except Exception as e:
            log.warning(f"cancel_open_orders: {e}")

    # ─── CLOSE ───────────────────────────────────────────────
    async def close_position(self, direction: str):
        if cfg.DRY_RUN:
            log.info(f"[DRY] Cerrando posición {direction}")
            return
        try:
            side = "sell" if direction == "LONG" else "buy"
            pos  = await self.get_position()
            if pos:
                qty = abs(float(pos["contracts"]))
                await self.ex.create_order(
                    cfg.SYMBOL, "market", side, qty,
                    params={"reduceOnly": True},
                )
        except Exception as e:
            log.error(f"close_position error: {e}")

    async def close(self):
        await self.ex.close()
