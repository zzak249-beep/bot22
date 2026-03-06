import time
import traceback
from datetime import datetime, timezone
from config import (SYMBOLS, VERSION, POLL_INTERVAL, TRADE_MODE, DASHBOARD_ENABLED)
import data_feed
import strategy
import trader
import risk_manager as rm
import telegram_notifier as tg

# ══════════════════════════════════════════════════════
# main.py v12.4 — Loop principal con diagnóstico
# ══════════════════════════════════════════════════════

HEARTBEAT_EVERY = 6


def run_cycle(cycle: int):
    balance = trader.get_balance()
    rm.reset_daily_if_needed(balance)
    rm.update_peak(balance)

    blocked, reason = rm.check_circuit_breaker(balance)
    if blocked:
        print(f"\n  ⛔ CIRCUIT BREAKER: {reason}")
        tg.notify_circuit_breaker(reason)
        return

    if rm.is_manually_paused():
        print(f"\n  ⏸  Bot pausado manualmente — usa /resume")
        return

    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f"\n{'='*56}")
    print(f"  CICLO #{cycle}  {now_str}")
    print(f"  Balance: ${balance:.2f}  Modo: {TRADE_MODE.upper()}")
    print(f"  Posiciones abiertas: {len(trader.get_positions())}")
    print(f"{'='*56}")

    signals_found = 0

    for sym in SYMBOLS:
        try:
            df = data_feed.get_df(sym, interval="1h", limit=300)
            if df.empty:
                print(f"  {sym}: ⚠️  sin datos de BingX")
                continue

            current_price = float(df["close"].iloc[-1])
            print(f"  {sym}  P={current_price:.6g}", end="  ")

            # ── Gestionar posición abierta ─────────────
            if sym in trader.get_positions():
                trader.check_exits(sym, current_price)
                pos = trader.get_positions().get(sym)
                if pos:
                    print(f"🔓 abierta {pos['side'].upper()}  SL={pos['sl']:.5g}  TP={pos['tp']:.5g}")
                else:
                    print(f"🔒 cerrada en este ciclo")
                continue

            # ── Buscar señal ───────────────────────────
            reentry_info = trader.get_reentry_info(sym)
            sig = strategy.get_signal(df, symbol=sym, reentry_info=reentry_info)

            if sig:
                signals_found += 1
                tag = " [RE-ENTRY]" if sig.get("reentry") else ""
                print(f"🚀 SEÑAL {sig['side'].upper()} score={sig['score']} rsi={sig['rsi']} 4h={sig['bias_4h']}{tag}")
                if sig.get("reentry"):
                    tg.notify_reentry(sym, sig["side"], sig["score"])
                opened = trader.open_trade(sym, sig, balance)
                if opened:
                    balance = trader.get_balance()
            else:
                print(f"— sin señal")

        except Exception as e:
            msg = f"{sym}: {e}\n{traceback.format_exc()}"
            print(f"  ERROR {msg[:300]}")
            tg.notify_error(msg)

        time.sleep(0.5)

    print(f"\n  Ciclo #{cycle} finalizado — {signals_found} señal(es) encontrada(s)")

    # ── Heartbeat cada 6 ciclos (~6h) ─────────────────
    if cycle % HEARTBEAT_EVERY == 0:
        stats = rm.get_stats(balance)
        tg.notify_heartbeat(VERSION, cycle, balance, len(trader.get_positions()), TRADE_MODE, stats)


def main():
    balance = trader.get_balance()

    print(f"\n{'='*56}")
    print(f"  BB+RSI ELITE {VERSION}  —  {TRADE_MODE.upper()}")
    print(f"  Balance: ${balance:.2f}")
    print(f"  Pares: {len(SYMBOLS)}")
    print(f"  Intervalo: {POLL_INTERVAL//60}min")
    print(f"{'='*56}")
    print(f"  Filtros activos:")
    from config import (MTF_ENABLED, VOLUME_FILTER, REENTRY_ENABLED,
                        TRAIL_FROM_START, MAX_DAILY_LOSS_PCT, MAX_DRAWDOWN_PCT,
                        SCORE_MIN, MIN_RR, RSI_LONG, RSI_SHORT)
    print(f"    RSI LONG < {RSI_LONG}  |  RSI SHORT > {RSI_SHORT}")
    print(f"    Score mínimo: {SCORE_MIN}  |  R:R mínimo: {MIN_RR}")
    print(f"    MTF 4h: {'✅' if MTF_ENABLED else '❌'}")
    print(f"    Volumen: {'✅' if VOLUME_FILTER else '❌'}")
    print(f"    Re-entry: {'✅' if REENTRY_ENABLED else '❌'}")
    print(f"    Trailing desde apertura: {'✅' if TRAIL_FROM_START else '❌'}")
    print(f"    Circuit breaker: pérdida diaria>{MAX_DAILY_LOSS_PCT*100:.0f}%  drawdown>{MAX_DRAWDOWN_PCT*100:.0f}%")
    print(f"{'='*56}\n")

    if DASHBOARD_ENABLED:
        try:
            from dashboard import start_dashboard
            start_dashboard()
        except Exception as e:
            print(f"  [WEB] Dashboard no disponible: {e}")

    tg.start_command_listener()
    tg.notify_start(VERSION, SYMBOLS, TRADE_MODE, balance)

    cycle = 1
    while True:
        try:
            run_cycle(cycle)
        except Exception as e:
            msg = f"Error fatal ciclo #{cycle}: {e}\n{traceback.format_exc()}"
            print(f"  FATAL: {msg[:300]}")
            tg.notify_error(msg)

        cycle += 1
        print(f"\n  ⏰ Próximo ciclo en {POLL_INTERVAL//60}min  ({datetime.now(timezone.utc).strftime('%H:%M UTC')})\n")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
