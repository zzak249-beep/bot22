#!/usr/bin/env python3
"""
main.py v5.1 - Bot Trading BingX
Punto de entrada principal para Railway
"""

import time
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

import config
import data_feed
import strategy
import trader
import risk_manager as rm
import telegram_notifier as tg
import bingx_api as api

STATE_DIR = Path("bot_state")
STATE_DIR.mkdir(exist_ok=True)


def _init_extras():
    if config.SELECTOR_ENABLED:
        try:
            from selector import PairSelector
            sel = PairSelector()
            sel.update_active_pairs(config.SYMBOLS, n_top=config.SELECTOR_TOP_N)
            log.info("Selector activo")
        except Exception as e:
            log.warning(f"Selector no disponible: {e}")

    if config.DASHBOARD_ENABLED:
        try:
            from dashboard import start_dashboard
            start_dashboard()
            log.info(f"Dashboard en puerto {config.DASHBOARD_PORT}")
        except Exception as e:
            log.warning(f"Dashboard: {e}")


def _get_symbols():
    symbols = [s for s in config.SYMBOLS if s not in config.BLACKLIST]
    if config.SELECTOR_ENABLED:
        try:
            from selector import PairSelector
            sel = PairSelector()
            active = sel.state.get("active_pairs", [])
            if active:
                symbols = [s for s in symbols if s in active]
        except Exception:
            pass
    return symbols


def run_cycle(cycle):
    try:
        balance = trader.get_balance()
        rm.reset_daily_if_needed(balance)
        rm.update_peak(balance)

        blocked, reason = rm.check_circuit_breaker(balance)
        if blocked:
            log.warning(f"CIRCUIT BREAKER: {reason}")
            tg.notify_circuit_breaker(reason)
            return 0, 0

        if rm.is_manually_paused():
            log.info("Bot pausado manualmente")
            return 0, 0

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        log.info("=" * 55)
        log.info(f"CICLO #{cycle} | {now_str} | Balance: ${balance:.4f}")
        log.info("=" * 55)

        signals = 0
        trades = 0

        for sym in _get_symbols():
            try:
                df = data_feed.get_df(sym, interval=config.CANDLE_TF, limit=300)
                if df.empty:
                    continue

                price = float(df["close"].iloc[-1])

                # Gestionar posicion abierta
                if sym in trader.get_positions():
                    trader.check_exits(sym, price)
                    continue

                reentry = trader.get_reentry_info(sym)
                signal = strategy.get_signal(df, symbol=sym, reentry_info=reentry)

                if signal:
                    signals += 1
                    log.info(f"{sym}: SENAL {signal['side'].upper()} score={signal['score']} rsi={signal['rsi']}")
                    if trader.open_trade(sym, signal, balance):
                        trades += 1
                        balance = trader.get_balance()

                time.sleep(0.3)

            except Exception as e:
                log.error(f"{sym}: {str(e)[:120]}")

        # Heartbeat cada 6 ciclos (30 min)
        if cycle % 6 == 0:
            try:
                stats = rm.get_stats(balance)
                summary = trader.get_summary()
                tg.notify_heartbeat(config.VERSION, cycle, balance,
                                    len(trader.get_positions()),
                                    config.TRADE_MODE, stats)
            except Exception:
                pass

        log.info(f"Ciclo #{cycle} completo: {signals} senales | {trades} trades")
        return signals, trades

    except Exception as e:
        msg = f"ERROR FATAL ciclo #{cycle}: {e}"
        log.error(msg, exc_info=True)
        try:
            tg.notify_error(str(e)[:150])
        except Exception:
            pass
        return 0, 0


def main():
    log.info("=" * 55)
    log.info(f"BOT {config.VERSION} | Modo: {config.TRADE_MODE.upper()}")
    log.info(f"Pares: {len(config.SYMBOLS)} | Leverage: {config.LEVERAGE}x | TF: {config.CANDLE_TF}")
    log.info(f"TP: {config.TP_ATR_MULT}x ATR | SL: {config.SL_ATR_MULT}x ATR | Ratio: ~{config.TP_ATR_MULT/config.SL_ATR_MULT:.1f}:1")
    log.info(f"Score MIN: {config.SCORE_MIN} | RSI LONG < {config.RSI_LONG} | RSI SHORT > {config.RSI_SHORT}")
    log.info("=" * 55)

    # Verificar API
    try:
        balance = api.get_balance()
        log.info(f"BingX API OK - Balance: ${balance:.4f}")
    except Exception as e:
        log.error(f"BingX API ERROR: {e}")
        raise

    # Verificar data feed
    try:
        df = data_feed.get_df(config.SYMBOLS[0], interval="1h", limit=10)
        if df.empty:
            raise ValueError("DataFrame vacio")
        log.info("Data feed OK")
    except Exception as e:
        log.error(f"Data feed ERROR: {e}")
        raise

    _init_extras()

    try:
        tg.start_command_listener()
        symbols = _get_symbols()
        tg.notify_start(config.VERSION, symbols, config.TRADE_MODE, balance)
        log.info("Telegram activo")
    except Exception as e:
        log.warning(f"Telegram: {e}")

    log.info("INICIADO - Esperando ciclos...")

    cycle = 1
    while True:
        try:
            run_cycle(cycle)
            cycle += 1
            log.info(f"Proximo ciclo en {config.POLL_INTERVAL // 60} min")
            time.sleep(config.POLL_INTERVAL)
        except KeyboardInterrupt:
            log.info("Detenido por usuario")
            break
        except Exception as e:
            log.error(f"ERROR: {e}", exc_info=True)
            time.sleep(60)
            cycle += 1


if __name__ == "__main__":
    main()
