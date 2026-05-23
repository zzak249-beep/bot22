"""
position_manager.py — Ciclo de vida completo de una posición

Flujo:
  1. open()  → coloca orden + SL + TP1 (50%) en BingX
  2. monitor() cada 3 min:
       - Verifica SL tocado → cierra y registra pérdida
       - TP1 tocado → cierra 50%, mueve SL a breakeven, activa trail
       - TP2 tocado → cierra 25% más
       - TP3 / trail → cierra el resto
  3. close() → cancela órdenes pendientes, cierra a mercado, notifica
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from bingx_client import BingXClient, BingXError
from telegram_client import TelegramClient
from risk_manager import RiskManager
from strategy import Signal
from config import cfg

log = logging.getLogger("pos_mgr")


@dataclass
class LivePosition:
    symbol:    str
    side:      str         # LONG | SHORT
    entry:     float
    qty_total: float       # cantidad total abierta
    qty_open:  float       # cantidad aún abierta
    sl:        float
    tp1:       float
    tp2:       float
    tp3:       float
    score:     int
    atr:       float
    leverage:  int
    opened_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    tp1_hit:   bool = False
    tp2_hit:   bool = False
    trail_sl:  float = 0.0
    trail_active: bool = False
    best_price: float = 0.0   # mejor precio alcanzado (para trailing)

    @property
    def age_min(self) -> int:
        return int((datetime.now(timezone.utc)
                    - self.opened_at).total_seconds() / 60)

    def update_trail(self, price: float) -> Optional[float]:
        """Retorna nuevo SL si debe moverse, None si no."""
        if not self.trail_active:
            return None
        if self.side == "LONG":
            if price > self.best_price:
                self.best_price = price
                new_sl = price * (1 - cfg.TRAIL_PCT / 100)
                if new_sl > self.sl:
                    self.sl = new_sl
                    return new_sl
        else:
            if self.best_price == 0 or price < self.best_price:
                self.best_price = price
                new_sl = price * (1 + cfg.TRAIL_PCT / 100)
                if new_sl < self.sl:
                    self.sl = new_sl
                    return new_sl
        return None


class PositionManager:

    def __init__(self, client: BingXClient,
                 tg: TelegramClient, risk: RiskManager):
        self.client = client
        self.tg     = tg
        self.risk   = risk
        self._pos: dict[str, LivePosition] = {}  # key = symbol:side

    def has_position(self, symbol: str = "") -> bool:
        if symbol:
            return any(symbol in k for k in self._pos)
        return len(self._pos) > 0

    def count(self) -> int:
        return len(self._pos)

    # ══════════════════════════════════════════════════════════
    #  ABRIR POSICIÓN
    # ══════════════════════════════════════════════════════════

    async def open(self, sig: Signal, qty: float) -> bool:
        if qty <= 0:
            log.warning("qty=0 para %s — no abrir", sig.symbol)
            return False

        key      = f"{sig.symbol}:{sig.side}"
        pos_side = sig.side   # LONG | SHORT
        bx_side  = "BUY" if sig.side == "LONG" else "SELL"

        log.info("📥 Abriendo %s %s | qty=%.4f | SL=%.6g | TP1=%.6g",
                 sig.side, sig.symbol, qty, sig.sl, sig.tp1)

        try:
            # Configurar margen y apalancamiento
            await self.client.set_margin_isolated(sig.symbol)
            await asyncio.gather(
                self.client.set_leverage(sig.symbol, cfg.LEVERAGE, "LONG"),
                self.client.set_leverage(sig.symbol, cfg.LEVERAGE, "SHORT"),
            )

            # Cancelar órdenes previas en ese símbolo
            await self.client.cancel_all(sig.symbol)

            # Orden principal con SL + TP1 (50%)
            qty_tp1 = round(qty * 0.5, 4)   # 50% en TP1
            res = await self.client.place_order(
                symbol   = sig.symbol,
                side     = bx_side,
                pos_side = pos_side,
                qty      = qty,
                sl       = sig.sl,
                tp       = sig.tp1,
            )

            if res.get("code", -1) != 0:
                err = res.get("msg", "desconocido")
                log.error("❌ Orden falló %s: %s", sig.symbol, err)
                await self.tg.error(f"Orden falló `{sig.symbol}`: {err}")
                return False

            order_id = res.get("data", {}).get("orderId", "OK")
            log.info("✅ Orden ejecutada: %s", order_id)

            # Registrar posición
            self._pos[key] = LivePosition(
                symbol    = sig.symbol,
                side      = sig.side,
                entry     = sig.price,
                qty_total = qty,
                qty_open  = qty,
                sl        = sig.sl,
                tp1       = sig.tp1,
                tp2       = sig.tp2,
                tp3       = sig.tp3,
                score     = sig.score,
                atr       = sig.atr,
                leverage  = cfg.LEVERAGE,
            )

            await self.tg.order_opened(sig, qty, order_id)
            return True

        except BingXError as e:
            log.error("BingXError abriendo %s: %s", sig.symbol, e)
            await self.tg.error(f"Error abriendo `{sig.symbol}`: {e.msg}")
            return False
        except Exception as e:
            log.error("Error inesperado abriendo %s: %s", sig.symbol, e)
            return False

    # ══════════════════════════════════════════════════════════
    #  MONITOREAR TODAS LAS POSICIONES
    # ══════════════════════════════════════════════════════════

    async def monitor_all(self) -> int:
        """Llamar en cada ciclo del scanner. Retorna posiciones cerradas."""
        if not self._pos:
            # Verificar también posiciones externas con pérdida grave
            await self._check_external_positions()
            return 0

        closed = 0
        for key in list(self._pos.keys()):
            pos = self._pos[key]
            try:
                closed_now = await self._monitor_one(pos)
                if closed_now:
                    del self._pos[key]
                    closed += 1
            except Exception as e:
                log.error("Error monitoreando %s: %s", key, e)
        return closed

    async def _monitor_one(self, pos: LivePosition) -> bool:
        """Retorna True si la posición fue cerrada."""

        # Precio actual
        ticker = await self.client.get_ticker(pos.symbol)
        if not ticker:
            return False

        price = float(ticker.get("lastPrice",
                      ticker.get("price", pos.entry)))
        if price == 0:
            return False

        # ── PnL actual ────────────────────────────────────────
        if pos.side == "LONG":
            pnl_pct  = (price - pos.entry) / pos.entry * 100 * pos.leverage
        else:
            pnl_pct  = (pos.entry - price) / pos.entry * 100 * pos.leverage
        pnl_usdt = (pnl_pct / 100) * (pos.entry * pos.qty_open)

        log.debug("%s %s | precio=%.6g | PnL=%.1f%% (%.2f USDT) | %dmin",
                  pos.side, pos.symbol, price, pnl_pct, pnl_usdt, pos.age_min)

        # ── Cierre emergencia: pérdida > 50% ─────────────────
        if pnl_pct < -50:
            log.warning("🚨 EMERGENCIA %s PnL=%.1f%%", pos.symbol, pnl_pct)
            await self._close_full(pos, price, f"Emergencia {pnl_pct:.1f}%")
            return True

        # ── SL alcanzado ──────────────────────────────────────
        sl_hit = ((pos.side == "LONG"  and price <= pos.sl) or
                  (pos.side == "SHORT" and price >= pos.sl))
        if sl_hit:
            await self._close_full(pos, price, f"SL @ {pos.sl:.6g}")
            return True

        # ── TP1 alcanzado (50%) ───────────────────────────────
        if not pos.tp1_hit:
            tp1_hit = ((pos.side == "LONG"  and price >= pos.tp1) or
                       (pos.side == "SHORT" and price <= pos.tp1))
            if tp1_hit:
                qty_close = round(pos.qty_open * 0.5, 4)
                await self._close_partial(pos, price, qty_close,
                                          f"TP1 @ {pos.tp1:.6g}")
                pos.qty_open  = round(pos.qty_open - qty_close, 4)
                pos.tp1_hit   = True
                pos.sl        = pos.entry   # SL → breakeven
                pos.trail_active = True
                pos.best_price   = price
                await self.tg.tp_hit(pos.symbol, pos.side, 1, price,
                                     pnl_usdt * 0.5, pos.qty_open)
                log.info("TP1 hit %s | SL→ breakeven %.6g | trail ON",
                         pos.symbol, pos.entry)

        # ── TP2 alcanzado (25% más) ───────────────────────────
        if pos.tp1_hit and not pos.tp2_hit:
            tp2_hit = ((pos.side == "LONG"  and price >= pos.tp2) or
                       (pos.side == "SHORT" and price <= pos.tp2))
            if tp2_hit:
                qty_close = round(pos.qty_open * 0.5, 4)
                await self._close_partial(pos, price, qty_close,
                                          f"TP2 @ {pos.tp2:.6g}")
                pos.qty_open = round(pos.qty_open - qty_close, 4)
                pos.tp2_hit  = True
                await self.tg.tp_hit(pos.symbol, pos.side, 2, price,
                                     pnl_usdt * 0.25, pos.qty_open)

        # ── TP3 — cierra el resto ─────────────────────────────
        if pos.tp2_hit:
            tp3_hit = ((pos.side == "LONG"  and price >= pos.tp3) or
                       (pos.side == "SHORT" and price <= pos.tp3))
            if tp3_hit:
                await self._close_full(pos, price, f"TP3 @ {pos.tp3:.6g}")
                return True

        # ── Trailing stop ─────────────────────────────────────
        new_sl = pos.update_trail(price)
        if new_sl:
            log.info("Trail SL actualizado %s → %.6g", pos.symbol, new_sl)
            # Verificar si el trailing SL fue tocado
            trail_hit = ((pos.side == "LONG"  and price <= new_sl) or
                         (pos.side == "SHORT" and price >= new_sl))
            if trail_hit:
                await self._close_full(pos, price, f"Trailing SL @ {new_sl:.6g}")
                return True

        # ── Tiempo máximo de vida: 4h sin resolver ────────────
        if pos.age_min >= 240 and not pos.tp1_hit:
            await self._close_full(pos, price, "Tiempo máximo 4h")
            return True

        return False

    # ══════════════════════════════════════════════════════════
    #  CIERRES
    # ══════════════════════════════════════════════════════════

    async def _close_full(self, pos: LivePosition,
                           price: float, reason: str):
        qty = pos.qty_open
        if qty <= 0:
            return
        try:
            await self.client.cancel_all(pos.symbol)
            await self.client.close_market(pos.symbol, pos.side, qty)
            pnl = self.risk.record(
                pos.symbol, pos.side, pos.entry, price,
                qty, pos.leverage, pos.score)
            log.info("🔒 Cerrado %s %s | PnL=%.2f USDT | %s",
                     pos.side, pos.symbol, pnl, reason)
            await self.tg.position_closed(
                pos.symbol, pos.side, pos.entry,
                price, pnl, reason)
        except Exception as e:
            log.error("Error cerrando %s: %s", pos.symbol, e)
            await self.tg.error(f"Error cerrando `{pos.symbol}`: {e}")

    async def _close_partial(self, pos: LivePosition,
                              price: float, qty: float, reason: str):
        try:
            await self.client.close_market(pos.symbol, pos.side, qty)
            pnl = self.risk.record(
                pos.symbol, pos.side, pos.entry, price,
                qty, pos.leverage, pos.score)
            log.info("🔓 Parcial %s %s qty=%.4f | PnL=%.2f USDT | %s",
                     pos.side, pos.symbol, qty, pnl, reason)
        except Exception as e:
            log.error("Error cierre parcial %s: %s", pos.symbol, e)

    async def _check_external_positions(self):
        """Cierra posiciones externas con pérdida > 45%."""
        try:
            positions = await self.client.get_positions()
            for p in positions:
                symbol   = p.get("symbol", "")
                pos_side = p.get("positionSide", "LONG")
                qty      = abs(float(p.get("positionAmt", 0)))
                entry    = float(p.get("avgPrice", 0))
                unreal   = float(p.get("unrealizedProfit", 0))
                lev      = int(p.get("leverage", cfg.LEVERAGE))

                if qty == 0 or entry == 0:
                    continue

                ticker = await self.client.get_ticker(symbol)
                price  = float(ticker.get("lastPrice", entry))
                if pos_side == "LONG":
                    pnl_pct = (price - entry) / entry * 100 * lev
                else:
                    pnl_pct = (entry - price) / entry * 100 * lev

                if pnl_pct < -45:
                    log.warning("⚠️ Pos externa %s PnL=%.1f%% — cerrando",
                                symbol, pnl_pct)
                    await self.client.cancel_all(symbol)
                    await self.client.close_market(symbol, pos_side, qty)
                    await self.tg.position_closed(
                        symbol, pos_side, entry, price,
                        unreal, f"Emergencia externa {pnl_pct:.1f}%")
        except Exception as e:
            log.debug("check_external: %s", e)
