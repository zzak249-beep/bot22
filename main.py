"""
main.py — BB+RSI ELITE v14.0
Loop principal: escanea pares, ejecuta señales, gestiona posiciones.
"""
import time
import logging
import traceback
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("main")

import config as cfg
import data_feed
import strategy
import trader
import risk_manager as rm
import telegram_notifier as tg

SYMBOLS       = cfg.SYMBOLS
POLL_INTERVAL = cfg.POLL_INTERVAL
TRADE_MODE    = cfg.TRADE_MODE
VERSION       = cfg.VERSION
HEARTBEAT_N   = 6     # cada 6 ciclos → heartbeat


def _check_balance_on_start() -> float:
    balance = trader.get_balance()
    log.info(f"{'='*52}")
    if TRADE_MODE == "live":
        if balance > 0:
            log.info(f"✅ Balance Futures: ${balance:.2f} USDT")
        else:
            log.error("❌ Balance Futures = $0.00")
            log.error("   → BingX: Assets → Transfer → Spot to Perpetual Futures")
            log.error("   → O pon TRADE_MODE=paper en Variables de Railway")
            tg.notify_error(
                "🚨 Balance Futures=$0\n"
                "→ BingX: Assets → Transfer → Spot to Perpetual Futures\n"
                "→ O pon TRADE_MODE=paper en Railway"
            )
    else:
        log.info(f"📄 PAPER MODE — Balance: ${balance:.2f} (INITIAL_BAL=${cfg.INITIAL_BAL})")
    log.info(f"{'='*52}")
    return balance


def run_cycle(cycle: int):
    balance = trader.get_balance()
    rm.update_peak(balance)

    blocked, reason = rm.check_circuit_breaker(balance)
    if blocked:
        log.warning(f"⛔ CIRCUIT BREAKER: {reason}")
        tg.notify_circuit_breaker(reason)
        return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    positions = trader.get_positions()
    log.info(f"{'='*52}")
    log.info(f"CICLO #{cycle}  {now_str}")
    log.info(f"Balance: ${balance:.2f}  Modo: {TRADE_MODE.upper()}  Posiciones: {len(positions)}")
    log.info(f"{'='*52}")

    signals_found = 0

    for sym in SYMBOLS:
        try:
            # ── Gestionar posición existente ──────────
            if sym in positions:
                df_cur = data_feed.get_df(sym, interval=cfg.CANDLE_TF, limit=5)
                if not df_cur.empty:
                    current_price = float(df_cur["close"].iloc[-1])
                    trader.check_exits(sym, current_price)
                    pos = trader.get_positions().get(sym)
                    if pos:
                        pnl_est = (current_price - pos["entry"]) / pos["entry"] * 100
                        if pos["side"] == "short":
                            pnl_est = -pnl_est
                        log.info(f"  {sym:20s} 🔓 {pos['side'].upper():5s} "
                                 f"e={pos['entry']:.5g} p={current_price:.5g} "
                                 f"({pnl_est:+.2f}%) SL={pos['sl']:.5g}")
                    else:
                        log.info(f"  {sym:20s} 🔒 cerrada")
                continue

            # ── Buscar señal ──────────────────────────
            df = data_feed.get_df(sym, interval=cfg.CANDLE_TF, limit=250)
            if df.empty:
                log.debug(f"  {sym}: sin datos")
                continue

            current_price = float(df["close"].iloc[-1])
            reentry_info  = trader.get_reentry_info(sym)
            sig = strategy.get_signal(df, symbol=sym, reentry_info=reentry_info)

            if sig:
                signals_found += 1
                tag = " [RE-ENTRY]" if sig.get("reentry") else ""
                log.info(f"  {sym:20s} 🚀 {sig['side'].upper()} "
                         f"score={sig['score']} rsi={sig['rsi']} "
                         f"rr={sig.get('rr',0):.2f} 4h={sig['bias_4h']}{tag}")
                trader.open_trade(sym, sig, balance)
                balance = trader.get_balance()
            else:
                log.debug(f"  {sym}: sin señal")

        except Exception as e:
            log.error(f"  {sym} ERROR: {e}")
            if cfg.MODO_DEBUG:
                log.error(traceback.format_exc())

        time.sleep(0.3)   # pausa mínima entre pares

    log.info(f"Ciclo #{cycle} completado — {signals_found} señal(es) encontrada(s)")

    if cycle % HEARTBEAT_N == 0:
        stats = rm.get_stats(balance)
        summary = trader.get_summary()
        stats["wins"]       = summary.get("wins", 0)
        stats["losses"]     = summary.get("losses", 0)
        stats["wr"]         = summary.get("wr", 0)
        stats["pnl_today"]  = summary.get("pnl", 0)
        tg.notify_heartbeat(VERSION, cycle, balance,
                            len(trader.get_positions()), TRADE_MODE, stats)


def main():
    log.info(f"{'='*52}")
    log.info(f"  BB+RSI ELITE {VERSION} — {TRADE_MODE.upper()}")
    log.info(f"  Pares: {len(SYMBOLS)}")
    log.info(f"  Ciclo: cada {POLL_INTERVAL//60}min")
    log.info(f"  RSI LONG<{cfg.RSI_LONG}  SHORT>{cfg.RSI_SHORT}")
    log.info(f"  Score≥{cfg.SCORE_MIN}  R:R≥{cfg.MIN_RR}  Lev:{cfg.LEVERAGE}x")
    log.info(f"  Trailing:{cfg.TRAIL_FROM_START}  ParcialTP:{cfg.CIERRE_PARCIAL_ACTIVO}")
    log.info(f"{'='*52}")

    balance = _check_balance_on_start()

    tg.start_command_listener()
    tg.notify_start(VERSION, SYMBOLS, TRADE_MODE, balance)

    cycle = 1
    while True:
        try:
            run_cycle(cycle)
        except Exception as e:
            msg = f"Error fatal ciclo #{cycle}: {e}"
            log.error(msg)
            log.error(traceback.format_exc())
            tg.notify_error(msg)

        cycle += 1
        next_t = datetime.now(timezone.utc).strftime("%H:%M UTC")
        log.info(f"⏰ Próximo ciclo en {POLL_INTERVAL//60}min ({next_t})")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
