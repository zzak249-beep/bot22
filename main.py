import time
import traceback
from datetime import datetime, timezone

# ══════════════════════════════════════════════════════
# main.py v13.2 — importaciones defensivas
# Nunca crashea por variables faltantes en config.py
# ══════════════════════════════════════════════════════

import config as _cfg

SYMBOLS           = getattr(_cfg, "SYMBOLS",           [])
VERSION           = getattr(_cfg, "VERSION",           "v13.x")
POLL_INTERVAL     = getattr(_cfg, "POLL_INTERVAL",     900)
TRADE_MODE        = getattr(_cfg, "TRADE_MODE",        "paper")
DASHBOARD_ENABLED = getattr(_cfg, "DASHBOARD_ENABLED", True)

import data_feed
import strategy
import trader
import risk_manager as rm
import telegram_notifier as tg

HEARTBEAT_EVERY = 6


def _check_balance_on_start() -> float:
    balance = trader.get_balance()
    print(f"\n{'='*56}")
    if TRADE_MODE == "live":
        if balance > 0:
            print(f"  ✅ Balance Futures: ${balance:.2f}")
        else:
            print(f"  ❌ Balance Futures: $0.00  ← PROBLEMA")
            print(f"     1) Fondos en Spot, no en Perpetual Futures")
            print(f"        → BingX: Assets → Transfer → Spot to Perpetual")
            print(f"     2) API key sin permiso 'Trade'")
            print(f"        → BingX: API Mgmt → nueva key con Read+Trade")
            tg.notify_error(
                "🚨 Balance Futures=$0\n"
                "El bot NO ejecutará trades.\n"
                "→ BingX: Assets → Transfer → Spot to Perpetual Futures\n"
                "→ O pon TRADE_MODE=paper en Railway Variables para simular"
            )
    else:
        initial = getattr(_cfg, "INITIAL_BAL", 100.0)
        print(f"  📄 PAPER MODE — Balance: ${balance:.2f} (INITIAL_BAL=${initial})")
    print(f"{'='*56}")
    return balance


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
        print(f"\n  ⏸  Pausado — /resume para reactivar")
        return

    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f"\n{'='*56}")
    print(f"  CICLO #{cycle}  {now_str}")
    print(f"  Balance: ${balance:.2f}  Modo: {TRADE_MODE.upper()}")
    print(f"  Posiciones: {len(trader.get_positions())}")
    if balance <= 0 and TRADE_MODE == "live":
        print(f"  ⚠️  Balance=0 → señales OK pero trades bloqueados")
    print(f"{'='*56}")

    signals_found = 0

    for sym in SYMBOLS:
        try:
            df = data_feed.get_df(sym, interval="1h", limit=300)
            if df.empty:
                print(f"  {sym}: ⚠️  sin datos")
                continue

            current_price = float(df["close"].iloc[-1])
            print(f"  {sym}  P={current_price:.6g}", end="  ")

            if sym in trader.get_positions():
                trader.check_exits(sym, current_price)
                pos = trader.get_positions().get(sym)
                if pos:
                    print(f"🔓 {pos['side'].upper()}  SL={pos['sl']:.5g}  TP={pos['tp']:.5g}")
                else:
                    print(f"🔒 cerrada")
                continue

            reentry_info = trader.get_reentry_info(sym)
            sig = strategy.get_signal(df, symbol=sym, reentry_info=reentry_info)

            if sig:
                signals_found += 1
                liq = f" liq={sig.get('liq_bias','?')}" if sig.get('liq_bias') else ""
                tag = " [RE-ENTRY]" if sig.get("reentry") else ""
                print(f"🚀 {sig['side'].upper()} score={sig['score']} "
                      f"rsi={sig['rsi']} 4h={sig['bias_4h']}{liq}{tag}")
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

    print(f"\n  Ciclo #{cycle} — {signals_found} señal(es)")

    if cycle % HEARTBEAT_EVERY == 0:
        stats = rm.get_stats(balance)
        tg.notify_heartbeat(VERSION, cycle, balance,
                            len(trader.get_positions()), TRADE_MODE, stats)


def main():
    print(f"\n{'='*56}")
    print(f"  BB+RSI ELITE {VERSION}  —  {TRADE_MODE.upper()}")
    print(f"  Pares: {len(SYMBOLS)}")
    print(f"  Intervalo: {POLL_INTERVAL//60}min")
    print(f"{'='*56}")

    MTF_ENABLED      = getattr(_cfg, "MTF_ENABLED",       True)
    VOLUME_FILTER    = getattr(_cfg, "VOLUME_FILTER",     True)
    REENTRY_ENABLED  = getattr(_cfg, "REENTRY_ENABLED",   True)
    TRAIL_FROM_START = getattr(_cfg, "TRAIL_FROM_START",  True)
    SCORE_MIN        = getattr(_cfg, "SCORE_MIN",         40)
    MIN_RR           = getattr(_cfg, "MIN_RR",            1.2)
    RSI_LONG         = getattr(_cfg, "RSI_LONG",          36)
    RSI_SHORT        = getattr(_cfg, "RSI_SHORT",         64)
    LIQ_ENABLED      = getattr(_cfg, "LIQUIDITY_ENABLED", True)

    print(f"  RSI LONG<{RSI_LONG} | RSI SHORT>{RSI_SHORT} | Score≥{SCORE_MIN} | RR≥{MIN_RR}")
    print(f"  MTF:{'✅' if MTF_ENABLED else '❌'} "
          f"Vol:{'✅' if VOLUME_FILTER else '❌'} "
          f"Reentry:{'✅' if REENTRY_ENABLED else '❌'} "
          f"Trail:{'✅' if TRAIL_FROM_START else '❌'} "
          f"Liq:{'✅' if LIQ_ENABLED else '❌'}")
    print(f"{'='*56}")

    balance = _check_balance_on_start()

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
        print(f"\n  ⏰ Próximo ciclo en {POLL_INTERVAL//60}min  "
              f"({datetime.now(timezone.utc).strftime('%H:%M UTC')})\n")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
