"""
main.py — Bucle principal con todas las mejoras
"""
import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd

import config as cfg
import strategy
import exchange as ex
import notifier as tg
import database as db
import learner
import symbols_loader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
log = logging.getLogger("main")


class BotState:
    def __init__(self):
        self.positions  = {}
        self.cooldowns  = defaultdict(int)
        self.last_report   = datetime.now()
        self.trades_closed = 0
        self.iteration     = 0
        self.stats = {"trades_today": 0, "wins": 0, "losses": 0,
                      "pnl_today": 0.0, "day": datetime.now().date()}

    def reset_daily(self):
        today = datetime.now().date()
        if self.stats["day"] != today:
            self.stats = {"trades_today": 0, "wins": 0, "losses": 0,
                          "pnl_today": 0.0, "day": today}

    def record_close(self, pnl):
        self.stats["trades_today"] += 1
        self.stats["pnl_today"]    += pnl
        if pnl >= 0: self.stats["wins"]   += 1
        else:        self.stats["losses"] += 1
        self.trades_closed += 1


state = BotState()


def fetch_candles(symbol, tf, limit=200):
    bars = ex.get_exchange().fetch_ohlcv(symbol, tf, limit=limit)
    df   = pd.DataFrame(bars, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.reset_index(drop=True)


def handle_open_position(symbol, sig, balance):
    if balance < cfg.MIN_USDT_BALANCE:
        log.warning(f"Sin fondos (${balance:.2f})")
        db.log_signal(symbol, sig, executed=False)
        tg.send_no_funds(symbol, sig, balance)
        return False

    side   = sig["action"]
    result = ex.open_long(symbol, sig) if side == "buy" else ex.open_short(symbol, sig)

    if result:
        trade_id = db.open_trade(
            symbol=symbol, signal=sig, qty=result["qty"],
            balance=balance, leverage=cfg.LEVERAGE,
            bb_sigma=cfg.BB_SIGMA, bb_period=cfg.BB_PERIOD, rsi_ob=cfg.RSI_OB,
        )
        db.log_signal(symbol, sig, executed=True, trade_id=trade_id)
        state.positions[symbol] = {
            "side":        result["side"],
            "entry":       sig["entry"],
            "sl":          sig["sl"],
            "tp":          sig["tp"],
            "tp_partial":  sig.get("tp_partial"),
            "qty":         result["qty"],
            "trade_id":    trade_id,
            "open_time":   datetime.now(),
            "current":     sig["entry"],
            "partial_done": False,   # si ya cerro el 50%
        }
        tg.send_buy_signal(symbol, sig, balance, executed=True)
        log.info(f"ABIERTO {result['side'].upper()}: {symbol} @ {sig['entry']}")
        return True
    else:
        tg.send_error(f"No se pudo abrir posicion en {symbol}")
        return False


def handle_close_position(symbol, cur_price, reason, sig=None):
    if symbol not in state.positions:
        return
    pos      = state.positions[symbol]
    side     = pos.get("side", "long")
    entry    = pos["entry"]
    qty      = pos.get("qty", 0)
    trade_id = pos.get("trade_id")

    if side == "long":
        pnl_pct = (cur_price - entry) / entry
    else:
        pnl_pct = (entry - cur_price) / entry
    pnl_est = qty * entry * pnl_pct * cfg.LEVERAGE

    if side == "long":
        executed = ex.close_long(symbol, qty)
    else:
        executed = ex.close_short(symbol, qty)

    if trade_id:
        db.close_trade(trade_id, cur_price, pnl_est, reason)
        if sig:
            db.log_signal(symbol, sig, executed=executed, trade_id=trade_id)

    tg.send_close_signal(symbol=symbol, entry=entry, exit_price=cur_price,
                         pnl=pnl_est, reason=reason, executed=executed)
    state.record_close(pnl_est)
    state.cooldowns[symbol] = cfg.COOLDOWN_BARS
    del state.positions[symbol]
    log.info(f"CERRADO {side.upper()}: {symbol} | PnL~${pnl_est:+.2f} | {reason}")

    if learner.should_review(state.trades_closed):
        updates = learner.analyze_and_adjust()
        if updates:
            tg.send_param_update(updates, learner.get_performance_report())
        state.trades_closed = 0


def handle_partial_tp(symbol, cur_price):
    """Cierra el 50% de la posicion al alcanzar el primer TP."""
    if symbol not in state.positions:
        return
    pos = state.positions[symbol]
    if pos.get("partial_done"):
        return

    side     = pos.get("side", "long")
    tp_part  = pos.get("tp_partial")
    if tp_part is None:
        return

    hit = (side == "long"  and cur_price >= tp_part) or \
          (side == "short" and cur_price <= tp_part)

    if hit:
        qty_half = round(pos["qty"] * cfg.PARTIAL_TP_PCT, 4)
        if side == "long":
            ex.close_long(symbol, qty_half)
        else:
            ex.close_short(symbol, qty_half)

        pos["qty"]          -= qty_half
        pos["partial_done"]  = True

        entry   = pos["entry"]
        pnl_pct = (cur_price - entry) / entry if side == "long" else (entry - cur_price) / entry
        pnl_est = qty_half * entry * pnl_pct * cfg.LEVERAGE

        tg.send_close_signal(symbol=symbol, entry=entry, exit_price=cur_price,
                             pnl=pnl_est, reason=f"TP_PARCIAL_50%", executed=True)
        log.info(f"TP PARCIAL {symbol}: cerrado 50% @ {cur_price} PnL~${pnl_est:+.2f}")

        # Mover SL a breakeven despues del TP parcial
        pos["sl"] = entry
        log.info(f"SL movido a breakeven: {entry}")


def run_cycle():
    state.iteration += 1
    state.reset_daily()

    if symbols_loader.needs_refresh():
        symbols_loader.load_symbols(force=True)
        tg.send_symbols_update(cfg.SYMBOLS)

    total_symbols = len(cfg.SYMBOLS)
    log.info(f"Ciclo #{state.iteration} | {datetime.now().strftime('%d/%m %H:%M')} | {total_symbols} pares")

    balance    = ex.get_balance()
    open_count = len(state.positions)
    log.info(f"Balance: ${balance:.2f} | Posiciones: {open_count}/{cfg.MAX_POSITIONS}")

    # Sincronizar cierres externos (SL/TP en BingX)
    live_positions = {p["symbol"]: p for p in ex.get_open_positions()}
    for sym in list(state.positions.keys()):
        if sym not in live_positions:
            log.info(f"{sym}: cerrado externamente (SL/TP)")
            pos     = state.positions[sym]
            side    = pos.get("side", "long")
            exit_px = pos["sl"]
            pnl_pct = (exit_px - pos["entry"]) / pos["entry"] if side == "long" \
                      else (pos["entry"] - exit_px) / pos["entry"]
            pnl_est = pos.get("qty", 0) * pos["entry"] * pnl_pct * cfg.LEVERAGE
            if pos.get("trade_id"):
                db.close_trade(pos["trade_id"], exit_px, pnl_est, "SL_EXCHANGE")
            tg.send_close_signal(sym, pos["entry"], exit_px, pnl_est, "SL_EXCHANGE", False)
            state.record_close(pnl_est)
            state.cooldowns[sym] = cfg.COOLDOWN_BARS
            del state.positions[sym]
    open_count = len(state.positions)

    signals_found = []

    for i in range(0, total_symbols, cfg.SCAN_BATCH_SIZE):
        batch = cfg.SYMBOLS[i:i + cfg.SCAN_BATCH_SIZE]
        log.info(f"Lote {i//cfg.SCAN_BATCH_SIZE+1} ({len(batch)} pares)...")

        for symbol in batch:
            if symbol in state.positions and symbol in live_positions:
                state.positions[symbol]["current"] = live_positions[symbol].get("current", 0)

            if state.cooldowns[symbol] > 0:
                state.cooldowns[symbol] -= 1
                continue

            try:
                df    = fetch_candles(symbol, cfg.TIMEFRAME)
                df_4h = None
                try:
                    df_4h = fetch_candles(symbol, cfg.TIMEFRAME_HI, limit=100)
                except Exception:
                    pass

                sig = strategy.get_signal(df, df_4h)

                if symbol in state.positions:
                    pos       = state.positions[symbol]
                    cur_price = sig["entry"]
                    atr       = sig.get("atr", 0)
                    side      = pos.get("side", "long")

                    # 3. TP Parcial
                    if cfg.PARTIAL_TP_ENABLED and not pos.get("partial_done"):
                        handle_partial_tp(symbol, cur_price)

                    # 1. Trailing Stop
                    if cfg.TRAILING_STOP_ENABLED and atr > 0:
                        new_sl = strategy.calc_trailing_stop(pos, cur_price, atr)
                        if new_sl != pos["sl"]:
                            log.info(f"Trailing SL {symbol}: {pos['sl']:.4f} -> {new_sl:.4f}")
                            pos["sl"] = new_sl

                    # Verificar SL
                    sl_hit = (side == "long"  and cur_price <= pos["sl"]) or \
                             (side == "short" and cur_price >= pos["sl"])
                    if sl_hit:
                        handle_close_position(symbol, cur_price, "SL", sig)
                        open_count -= 1
                        continue

                    # Verificar TP completo
                    tp_hit = (side == "long"  and cur_price >= pos["tp"]) or \
                             (side == "short" and cur_price <= pos["tp"])
                    if tp_hit:
                        handle_close_position(symbol, cur_price, "TP", sig)
                        open_count -= 1
                        continue

                    # Señal de salida
                    exit_actions = {
                        "long":  "exit_long",
                        "short": "exit_short",
                    }
                    if sig["action"] == exit_actions.get(side):
                        handle_close_position(symbol, cur_price, "SIGNAL", sig)
                        open_count -= 1
                        continue

                elif sig["action"] in ("buy", "sell_short"):
                    signals_found.append((symbol, sig))
                    log.info(f"  SEÑAL: {symbol} {sig['action']} RSI={sig.get('rsi')} — {sig['reason']}")

            except Exception as e:
                log.debug(f"  {symbol}: error — {str(e)[:80]}")

        if i + cfg.SCAN_BATCH_SIZE < total_symbols:
            time.sleep(1)

    signals_found.sort(key=lambda x: x[1].get("rsi", 99))
    for symbol, sig in signals_found:
        if open_count >= cfg.MAX_POSITIONS:
            log.info(f"Max posiciones ({cfg.MAX_POSITIONS}) — señal ignorada: {symbol}")
            db.log_signal(symbol, sig, executed=False)
            break
        if handle_open_position(symbol, sig, balance):
            open_count += 1

    if datetime.now() - state.last_report >= timedelta(hours=1):
        pos_list = [{"symbol": s, "entry": p["entry"], "current": p.get("current", p["entry"]),
                     "side": p.get("side","long")} for s, p in state.positions.items()]
        tg.send_status(pos_list, balance, state.stats, learner.get_performance_report())
        state.last_report = datetime.now()


def main():
    db.init_db()
    log.info("="*60)
    log.info("  BB+RSI DCA BOT — BingX Futuros (TODOS los pares)")
    log.info(f"  Riesgo: {cfg.RISK_PCT*100:.0f}% | Leverage: {cfg.LEVERAGE}x")
    log.info(f"  SHORT: {'ON' if cfg.SHORT_ENABLED else 'OFF'} | "
             f"Trailing: {'ON' if cfg.TRAILING_STOP_ENABLED else 'OFF'} | "
             f"TP Parcial: {'ON' if cfg.PARTIAL_TP_ENABLED else 'OFF'}")
    log.info("="*60)

    symbols = symbols_loader.load_symbols(force=True)
    if not symbols:
        log.error("No se pudieron cargar pares")
        return

    tg.send_startup(symbols_loader.get_symbol_stats())

    bal = ex.get_balance()
    log.info(f"Balance inicial: ${bal:.2f} USDT")
    db.log_params(cfg.BB_PERIOD, cfg.BB_SIGMA, cfg.RSI_OB, cfg.SL_ATR, "Arranque inicial")

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            log.info("Bot detenido")
            tg.send_error("Bot detenido manualmente")
            break
        except Exception as e:
            log.error(f"Error critico: {e}")
            tg.send_error(f"Error critico: {str(e)[:300]}")
        log.info(f"Esperando {cfg.LOOP_SECONDS}s...")
        time.sleep(cfg.LOOP_SECONDS)


if __name__ == "__main__":
    main()
