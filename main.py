"""
main.py — Bucle principal del BB+RSI DCA Bot
BingX Futuros | Telegram 24/7 | Railway deployment | Aprendizaje automatico
"""

import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict

import ccxt
import pandas as pd

import config as cfg
import strategy
import exchange as ex
import notifier as tg
import database as db
import learner

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
        self.positions:  dict = {}
        self.cooldowns:  dict = defaultdict(int)
        self.last_report    = datetime.now()
        self.trades_closed  = 0
        self.iteration      = 0
        self.stats = {
            "trades_today": 0, "wins": 0, "losses": 0,
            "pnl_today": 0.0, "day": datetime.now().date()
        }

    def reset_daily(self):
        today = datetime.now().date()
        if self.stats["day"] != today:
            self.stats = {
                "trades_today": 0, "wins": 0, "losses": 0,
                "pnl_today": 0.0, "day": today
            }

    def record_close(self, pnl: float):
        self.stats["trades_today"] += 1
        self.stats["pnl_today"]    += pnl
        if pnl >= 0: self.stats["wins"]   += 1
        else:        self.stats["losses"] += 1
        self.trades_closed += 1


state = BotState()
_data_src = None


def get_data_source():
    global _data_src
    if _data_src is None:
        _data_src = ccxt.binance()
    return _data_src


def fetch_candles(symbol: str, limit: int = 200) -> pd.DataFrame:
    bars = get_data_source().fetch_ohlcv(symbol, cfg.TIMEFRAME, limit=limit)
    df   = pd.DataFrame(bars, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.reset_index(drop=True)


def handle_open_position(symbol: str, sig: dict, balance: float) -> bool:
    if balance >= cfg.MIN_USDT_BALANCE:
        result = ex.open_long(symbol, sig)
        if result:
            trade_id = db.open_trade(
                symbol=symbol, signal=sig, qty=result["qty"],
                balance=balance, leverage=cfg.LEVERAGE,
                bb_sigma=cfg.BB_SIGMA, bb_period=cfg.BB_PERIOD, rsi_ob=cfg.RSI_OB,
            )
            db.log_signal(symbol, sig, executed=True, trade_id=trade_id)
            state.positions[symbol] = {
                "entry": sig["entry"], "sl": sig["sl"], "tp": sig["tp"],
                "qty": result["qty"], "trade_id": trade_id,
                "open_time": datetime.now(), "current": sig["entry"],
            }
            tg.send_buy_signal(symbol, sig, balance, executed=True)
            log.info(f"POSICION ABIERTA: {symbol} @ {sig['entry']}  trade_id={trade_id}")
            return True
        else:
            tg.send_error(f"No se pudo abrir posicion en {symbol}")
            return False
    else:
        log.warning(f"Fondos insuficientes (${balance:.2f}) — señal manual: {symbol}")
        db.log_signal(symbol, sig, executed=False)
        tg.send_no_funds(symbol, sig, balance)
        return False


def handle_close_position(symbol: str, cur_price: float, reason: str, sig: dict):
    if symbol not in state.positions:
        return
    pos      = state.positions[symbol]
    pnl_pct  = (cur_price - pos["entry"]) / pos["entry"]
    pnl_est  = pos.get("qty", 0) * pos["entry"] * pnl_pct * cfg.LEVERAGE
    trade_id = pos.get("trade_id")

    executed = ex.close_long(symbol, pos["qty"])

    if trade_id:
        db.close_trade(trade_id, cur_price, pnl_est, reason)
        db.log_signal(symbol, sig, executed=executed, trade_id=trade_id)

    tg.send_close_signal(
        symbol=symbol, entry=pos["entry"], exit_price=cur_price,
        pnl=pnl_est, reason=reason, executed=executed,
    )
    state.record_close(pnl_est)
    state.cooldowns[symbol] = cfg.COOLDOWN_BARS
    del state.positions[symbol]
    log.info(f"POSICION CERRADA: {symbol} | PnL~${pnl_est:+.2f} | {reason}")

    # Learner cada N trades
    if learner.should_review(state.trades_closed):
        log.info("Learner: revisando parametros...")
        updates = learner.analyze_and_adjust()
        if updates:
            tg.send_param_update(updates, learner.get_performance_report())
        state.trades_closed = 0


def run_cycle():
    state.iteration += 1
    state.reset_daily()
    log.info(f"Ciclo #{state.iteration} — {datetime.now().strftime('%d/%m %H:%M:%S')}")

    balance    = ex.get_balance()
    open_count = len(state.positions)
    log.info(f"Balance: ${balance:.2f} | Posiciones: {open_count}/{cfg.MAX_POSITIONS}")

    # Sincronizar con BingX (SL/TP ejecutados en el exchange)
    live_positions = {p["symbol"]: p for p in ex.get_open_positions()}
    for sym in list(state.positions.keys()):
        if sym not in live_positions:
            log.info(f"{sym}: cerrado externamente en exchange")
            pos = state.positions[sym]
            if pos.get("trade_id"):
                db.close_trade(pos["trade_id"], pos["sl"], -cfg.MIN_USDT_BALANCE, "SL_EXCHANGE")
            state.record_close(-cfg.MIN_USDT_BALANCE)
            state.cooldowns[sym] = cfg.COOLDOWN_BARS
            del state.positions[sym]
    open_count = len(state.positions)

    for symbol in cfg.SYMBOLS:
        if symbol in state.positions and symbol in live_positions:
            state.positions[symbol]["current"] = live_positions[symbol].get("current", 0)

        if state.cooldowns[symbol] > 0:
            state.cooldowns[symbol] -= 1
            continue

        try:
            df  = fetch_candles(symbol)
            sig = strategy.get_signal(df)
            log.info(f"  {symbol}: {sig['action'].upper():5s} — {sig['reason']}")

            if symbol in state.positions:
                pos       = state.positions[symbol]
                cur_price = sig["entry"]
                if cur_price <= pos["sl"]:
                    handle_close_position(symbol, cur_price, "SL", sig)
                    continue
                if sig["action"] == "exit":
                    handle_close_position(symbol, cur_price, "SIGNAL", sig)
                    continue
            elif sig["action"] == "buy" and open_count < cfg.MAX_POSITIONS:
                if handle_open_position(symbol, sig, balance):
                    open_count += 1

        except Exception as e:
            log.error(f"Error procesando {symbol}: {e}")
            tg.send_error(f"Error en {symbol}: {str(e)[:200]}")

    # Reporte horario
    if datetime.now() - state.last_report >= timedelta(hours=1):
        pos_list = [
            {"symbol": s, "entry": p["entry"], "current": p.get("current", p["entry"])}
            for s, p in state.positions.items()
        ]
        tg.send_status(pos_list, balance, state.stats, learner.get_performance_report())
        state.last_report = datetime.now()


def main():
    db.init_db()
    log.info("="*55)
    log.info("  BB+RSI DCA BOT — BingX Futuros")
    log.info(f"  Pares: {', '.join(cfg.SYMBOLS)}")
    log.info(f"  Riesgo: {cfg.RISK_PCT*100:.0f}% | Leverage: {cfg.LEVERAGE}x")
    log.info("="*55)

    tg.send_startup()
    bal = ex.get_balance()
    if bal == 0.0 and cfg.BINGX_API_KEY:
        tg.send_error("No se pudo obtener balance de BingX\nVerifica API keys")
    else:
        log.info(f"Balance inicial: ${bal:.2f} USDT")

    db.log_params(cfg.BB_PERIOD, cfg.BB_SIGMA, cfg.RSI_OB, cfg.SL_ATR, "Arranque inicial")

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            log.info("Bot detenido manualmente")
            tg.send_error("Bot detenido manualmente")
            break
        except Exception as e:
            log.error(f"Error critico: {e}")
            tg.send_error(f"Error critico: {str(e)[:300]}")
        log.info(f"Esperando {cfg.LOOP_SECONDS}s...")
        time.sleep(cfg.LOOP_SECONDS)


if __name__ == "__main__":
    main()
