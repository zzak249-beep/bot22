"""
volob-standalone — Volume Order Block Scanner
Bot nuevo, aislado. Estrategia propia (strategy_vol_ob.py), sin
backtesting, sin track record.

Construido con las lecciones del resto del fleet desde el día 1:
  - MAX_OPEN_TRADES escopado a state.get_tracked_positions(), no a
    toda la cuenta.
  - Gate de margen (1.5x) antes de cada entrada, no solo cap de notional.
  - _manage_open_positions itera solo lo propio.
  - Sin reduceOnly en bingx_client.py (BingX lo rechaza en hedge mode).
  - Todo el estado persiste vía state.py desde el arranque.
"""
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import config
import state
from bingx_client import BingXClient
from position_manager import PositionManager
from risk_manager import RiskManager
from strategy_vol_ob import get_signal, check_tp_exit
from telegram_client import TelegramClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("scanner")


# ── Health server ─────────────────────────────────────────────

def _start_health():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *a): pass
    try:
        s = HTTPServer(("0.0.0.0", config.PORT), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        log.info(f"Health server :{config.PORT}/health")
    except Exception as e:
        log.warning(f"Health server: {e}")


# ── Exit helper ───────────────────────────────────────────────

def _close_position(client, pos_mgr, risk, tg, symbol, side, reason):
    pos = pos_mgr.get_position(symbol, side)
    if not pos:
        state.clear(symbol, side)
        return
    price = client.get_mark_price(symbol)
    pnl   = pos["unrealizedPnl"]
    if side == "LONG":
        ok = pos_mgr.close_long(symbol, pos["size"], reason)
    else:
        ok = pos_mgr.close_short(symbol, pos["size"], reason)
    if not ok:
        log.error(f"close_position {symbol} {side}: falló, no se registra el cierre")
        return
    risk.record_trade(pnl)
    tg.exit_trade(config.BOT_NAME, symbol, side, price, reason, pnl)


# ── Position management ───────────────────────────────────────

def _manage_open_positions(client, pos_mgr, risk, tg):
    for sym, side in state.get_tracked_positions():
        pos = pos_mgr.get_position(sym, side)
        if not pos:
            # FIX: antes solo se limpiaba el estado, sin registrar PnL
            # ni avisar — la mayoría de los cierres reales pasan por
            # aquí (SL o TP1 del exchange disparando solos), no por
            # max_hold/trail_stop. Eso dejaba day_pnl/day_trades
            # incompletos y sin ningún aviso de Telegram para estos
            # casos. Estimación aproximada (precio actual vs entrada
            # guardada) — no es el fill exacto del exchange, pero es
            # mejor que no registrar nada.
            entry_price, qty = state.get_entry_details(sym, side)
            state.clear(sym, side)
            if entry_price and qty:
                try:
                    close_price = client.get_mark_price(sym)
                    pnl = ((close_price - entry_price) if side == "LONG"
                           else (entry_price - close_price)) * qty
                    risk.record_trade(pnl)
                    tg.exit_trade(config.BOT_NAME, sym, side, close_price,
                                  "cierre_externo(SL/TP aprox)", pnl)
                    log.info(f"{sym} {side}: cierre externo — PnL aprox {pnl:+.4f} USDT")
                except Exception as e:
                    log.warning(f"No se pudo estimar PnL de cierre externo {sym}: {e}")
            else:
                log.info(f"state.clear {sym} {side}: ya no existe en el exchange (sin datos para estimar PnL)")
            continue

        try:
            price   = client.get_mark_price(sym)
            candles = client.get_klines(sym, config.TIMEFRAME, 120)
            if len(candles) < 30:
                continue

            sig = get_signal(candles, config)
            atr = sig["atr"] or 0
            if not atr:
                continue

            if pos_mgr.is_max_hold_expired(sym, side):
                _close_position(client, pos_mgr, risk, tg, sym, side, "max_hold")
                continue

            stop, hit = pos_mgr.tick_trail(sym, side, price, atr)
            if hit:
                _close_position(client, pos_mgr, risk, tg, sym, side, "trail_stop")
                continue

        except Exception as e:
            log.error(f"manage {sym}: {e}")


# ── Signal scanning ───────────────────────────────────────────

def _scan_and_enter(client, pos_mgr, risk, tg, symbols, equity) -> int:
    opened = 0
    for sym in symbols:
        if sym in config.BLACKLIST:
            continue

        tracked = state.get_tracked_positions()
        if len(tracked) >= config.MAX_OPEN_TRADES:
            log.warning(f"MAX_OPEN_TRADES (propias) alcanzado: {len(tracked)}/{config.MAX_OPEN_TRADES}")
            break

        allowed, reason = risk.can_trade(equity)
        if not allowed:
            log.warning(f"Risk block: {reason}")
            break

        try:
            candles = client.get_klines(sym, config.TIMEFRAME, 200)
            if len(candles) < 60:
                continue

            sig = get_signal(candles, config)
            if not sig["signal"]:
                continue

            direction = sig["signal"]
            if pos_mgr.has_position(sym, direction):
                continue

            mark = client.get_mark_price(sym)
            atr  = sig["atr"]
            qty  = pos_mgr.calc_qty(sym, mark, atr, equity)
            if not qty:
                continue

            # FIX (lección de renewed-love): gate de margen antes de abrir
            margin_needed = qty * mark / config.LEVERAGE
            # FIX: subido de 1.5x a 2x tras ver [101204] Insufficient margin
            # en ZEC-USDC — 17s después de abrir ATH-USDT, con el gate ya
            # superado. Probable desfase de BingX entre posición confirmada
            # y margen disponible actualizado. 2x no lo garantiza del todo
            # si el desfase es del lado del exchange, pero es más margen de
            # seguridad que antes.
            if client.get_available_margin() < margin_needed * 2.0:
                log.warning(f"Margen insuficiente para {sym}, skip")
                continue

            log.info(
                f"VOL_OB signal={direction} {sym}  "
                f"zone={sig['zone_bot']:.6g}-{sig['zone_top']:.6g}  "
                f"buy_ratio={sig['buy_ratio']:.2f}  trend={sig['trend']}  "
                f"tp={sig['tp_price']:.6g}  atr={atr:.4g}"
            )

            if direction == "LONG":
                ok = pos_mgr.open_long(sym, qty, atr)
            else:
                ok = pos_mgr.open_short(sym, qty, atr)

            if ok:
                pos_mgr.place_tp_sl(sym, direction, mark, qty, atr, sig["tp_price"])
                try:
                    stop = state.get_trail(sym, direction)
                    tg.entry(config.BOT_NAME, sym, direction, mark, qty, stop, equity)
                except Exception as e:
                    log.warning(f"telegram entry {sym}: {e}")
                opened += 1

        except Exception as e:
            log.error(f"scan {sym}: {e}")

    return opened


# ── Main loop ─────────────────────────────────────────────────

def main():
    _start_health()
    log.info(f"=== {config.BOT_NAME} starting (DIRECTION={config.DIRECTION}) ===")
    log.info("⚠️ Estrategia sin backtesting propio — sin track record todavía")

    client  = BingXClient(config.API_KEY, config.SECRET_KEY, config.BASE_URL)
    pos_mgr = PositionManager(client)
    risk    = RiskManager(config)
    tg      = TelegramClient(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT)

    equity = client.get_equity()
    risk.new_day(equity)
    log.info(f"New day — equity: {equity:.2f} USDT")
    tg.startup(config.BOT_NAME, config.TIMEFRAME, config.LEVERAGE)

    last_scan_t = 0.0
    iteration   = 0

    while True:
        try:
            now    = time.time()
            equity = client.get_equity()

            _manage_open_positions(client, pos_mgr, risk, tg)

            if now - last_scan_t >= config.SCAN_INTERVAL:
                last_scan_t = now
                iteration  += 1
                t0 = time.time()

                symbols = client.get_top_symbols(config.TOP_N_SYMBOLS, config.MIN_VOLUME_USDT)
                opened  = _scan_and_enter(client, pos_mgr, risk, tg, symbols, equity)

                log.info(
                    f"scanner | Iter {iteration} | {len(symbols)} símbolos "
                    f"| {opened} abiertos | {time.time()-t0:.1f}s"
                )

        except KeyboardInterrupt:
            log.info("Stopping.")
            break
        except Exception as e:
            log.error(f"Loop error: {e}")
            tg.error(config.BOT_NAME, str(e)[:400])
            time.sleep(30)

        time.sleep(config.TRAILING_CHECK_SEC)


if __name__ == "__main__":
    main()
