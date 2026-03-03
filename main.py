"""
main.py — Bucle principal ELITE v5
- 24/7 indestructible: nunca para, se auto-recupera de cualquier error
- Reinversion de ganancias automatica (compound interest)
- Aprende de cada trade (learner integrado)
- Sin fondos → señal manual completa a Telegram
- Circuit Breaker + watchdog de salud
- Filtro horario (baja liquidez nocturna)
- Correlacion de pares (no abrir posiciones correlacionadas)
"""
import time
import logging
import traceback
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

# ── Grupos de pares correlacionados ──────────────────────
CORRELATED_GROUPS = [
    {"SOL", "AVAX", "NEAR", "APT", "SUI", "TON"},
    {"BNB", "CAKE"},
    {"MATIC", "ARB", "OP", "STRK", "MANTA"},
    {"BTC", "ETH"},
    {"DOGE", "SHIB", "PEPE", "FLOKI", "WIF"},
    {"LINK", "BAND", "API3"},
    {"UNI", "SUSHI", "CRV", "BAL"},
]

def count_correlated(symbol: str, positions: dict) -> int:
    base = symbol.split("/")[0].upper()
    for group in CORRELATED_GROUPS:
        if base in group:
            return sum(1 for s in positions if s.split("/")[0].upper() in group)
    return 0


class BotState:
    def __init__(self):
        self.positions        = {}
        self.cooldowns        = defaultdict(int)
        self.last_report      = datetime.now()
        self.last_health      = datetime.now()
        self.trades_closed    = 0
        self.iteration        = 0
        self.circuit_open     = False
        self.circuit_reason   = ""
        self.consecutive_errors = 0
        self.initial_balance  = 0.0
        self.peak_balance     = 0.0
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
            if self.circuit_open:
                self.circuit_open     = False
                self.circuit_reason   = ""
                self.consecutive_errors = 0
                log.info("Circuit breaker + errores reseteados (nuevo dia)")

    def record_close(self, pnl):
        self.stats["trades_today"] += 1
        self.stats["pnl_today"]    += pnl
        if pnl >= 0:
            self.stats["wins"]   += 1
        else:
            self.stats["losses"] += 1
        self.trades_closed += 1

    def update_peak(self, balance: float):
        if balance > self.peak_balance:
            self.peak_balance = balance


state = BotState()


def fetch_candles(symbol, tf, limit=200):
    bars = ex.get_exchange().fetch_ohlcv(symbol, tf, limit=limit)
    df   = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.reset_index(drop=True)


def handle_open_position(symbol, sig, balance):
    # ── Circuit Breaker ───────────────────────────────────
    if state.circuit_open:
        log.warning(f"Circuit breaker activo — señal ignorada: {symbol} ({state.circuit_reason})")
        return False

    # ── Sin fondos: señal manual a Telegram ───────────────
    if balance < cfg.MIN_USDT_BALANCE:
        log.warning(f"Sin fondos (${balance:.2f}) — señal manual: {symbol}")
        db.log_signal(symbol, sig, executed=False)
        side_txt  = "🟢 LONG"  if sig["action"] == "buy" else "🔴 SHORT"
        score_txt = f"Score: `{sig.get('score', 'N/A')}/100`\n" if sig.get("score") else ""
        msg = (
            f"📡 *SEÑAL MANUAL* — Bot sin fondos\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Par     : `{symbol}`\n"
            f"Lado    : {side_txt}\n"
            f"Entrada : `{sig['entry']}`\n"
            f"🛑 SL   : `{sig['sl']}`\n"
            f"🎯 TP   : `{sig['tp']}`\n"
            f"TP 50%  : `{sig.get('tp_partial', 'N/A')}`\n"
            f"RSI     : `{sig.get('rsi', 'N/A')}`\n"
            f"{score_txt}"
            f"Razon   : _{sig.get('reason', '')}_\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Ejecutar manualmente en BingX\n"
            f"💰 Balance bot: ${balance:.4f} USDT"
        )
        tg.send_raw(msg)
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
            "side":         result["side"],
            "entry":        sig["entry"],
            "sl":           sig["sl"],
            "tp":           sig["tp"],
            "tp_partial":   sig.get("tp_partial"),
            "qty":          result["qty"],
            "trade_id":     trade_id,
            "open_time":    datetime.now(),
            "current":      sig["entry"],
            "partial_done": False,
            "score":        sig.get("score", 0),
        }
        tg.send_buy_signal(symbol, sig, balance, executed=True)
        log.info(f"ABIERTO {result['side'].upper()}: {symbol} @ {sig['entry']} | {sig.get('reason','')}")
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

    pnl_pct = (cur_price - entry) / entry if side == "long" else (entry - cur_price) / entry
    pnl_est = qty * entry * pnl_pct * cfg.LEVERAGE

    executed = ex.close_long(symbol, qty) if side == "long" else ex.close_short(symbol, qty)

    if trade_id:
        db.close_trade(trade_id, cur_price, pnl_est, reason)
        if sig:
            db.log_signal(symbol, sig, executed=executed, trade_id=trade_id)

    tg.send_close_signal(
        symbol=symbol, entry=entry, exit_price=cur_price,
        pnl=pnl_est, reason=reason, executed=executed
    )
    state.record_close(pnl_est)
    state.cooldowns[symbol] = cfg.COOLDOWN_BARS
    del state.positions[symbol]
    log.info(f"CERRADO {side.upper()}: {symbol} | PnL~${pnl_est:+.2f} | {reason}")

    # ── Circuit Breaker ───────────────────────────────────
    triggered, cb_reason = strategy.check_circuit_breaker(
        state.stats, cfg.BALANCE_SNAPSHOT
    )
    if triggered and not state.circuit_open:
        state.circuit_open   = True
        state.circuit_reason = cb_reason
        log.warning(f"CIRCUIT BREAKER: {cb_reason}")
        tg.send_error(f"⚠️ Circuit Breaker activado\n{cb_reason}\nBot pausado hasta mañana.")

    # ── Learner ───────────────────────────────────────────
    if learner.should_review(state.trades_closed):
        updates = learner.analyze_and_adjust()
        if updates:
            tg.send_param_update(updates, learner.get_performance_report())
        state.trades_closed = 0


def handle_partial_tp(symbol, cur_price):
    """Cierra el 50% al alcanzar primer TP y mueve SL a breakeven."""
    if symbol not in state.positions:
        return
    pos = state.positions[symbol]
    if pos.get("partial_done"):
        return
    side    = pos.get("side", "long")
    tp_part = pos.get("tp_partial")
    if tp_part is None:
        return
    hit = (side == "long"  and cur_price >= tp_part) or \
          (side == "short" and cur_price <= tp_part)
    if not hit:
        return

    qty_half = round(pos["qty"] * cfg.PARTIAL_TP_PCT, 4)
    if side == "long":
        ex.close_long(symbol, qty_half)
    else:
        ex.close_short(symbol, qty_half)

    pos["qty"]         -= qty_half
    pos["partial_done"] = True
    entry   = pos["entry"]
    pnl_pct = (cur_price - entry) / entry if side == "long" else (entry - cur_price) / entry
    pnl_est = qty_half * entry * pnl_pct * cfg.LEVERAGE

    tg.send_close_signal(
        symbol=symbol, entry=entry, exit_price=cur_price,
        pnl=pnl_est, reason="TP_PARCIAL_50%", executed=True
    )
    log.info(f"TP PARCIAL {symbol}: 50% @ {cur_price} PnL~${pnl_est:+.2f}")
    pos["sl"] = entry   # mover SL a breakeven
    log.info(f"SL → breakeven: {entry}")


def run_cycle():
    state.iteration += 1
    state.reset_daily()

    if symbols_loader.needs_refresh():
        symbols_loader.load_symbols(force=True)
        tg.send_symbols_update(cfg.SYMBOLS)

    total = len(cfg.SYMBOLS)
    cb_txt = f" | ⚠️CB: {state.circuit_reason}" if state.circuit_open else ""
    log.info(
        f"Ciclo #{state.iteration} | {datetime.now().strftime('%d/%m %H:%M')} | "
        f"{total} pares | W:{state.stats['wins']} L:{state.stats['losses']} "
        f"PnL:${state.stats['pnl_today']:+.2f}{cb_txt}"
    )

    balance    = ex.get_balance()
    open_count = len(state.positions)
    log.info(f"Balance: ${balance:.2f} | Posiciones: {open_count}/{cfg.MAX_POSITIONS}")

    # ── Sincronizar cierres externos (SL/TP ejecutados en BingX) ──
    live_positions = {p["symbol"]: p for p in ex.get_open_positions()}
    for sym in list(state.positions.keys()):
        if sym not in live_positions:
            log.info(f"{sym}: cerrado externamente (SL/TP en exchange)")
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

    for i in range(0, total, cfg.SCAN_BATCH_SIZE):
        batch = cfg.SYMBOLS[i:i + cfg.SCAN_BATCH_SIZE]
        log.info(f"Lote {i//cfg.SCAN_BATCH_SIZE+1}/{-(-total//cfg.SCAN_BATCH_SIZE)} ({len(batch)} pares)...")

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

                    if cfg.PARTIAL_TP_ENABLED and not pos.get("partial_done"):
                        handle_partial_tp(symbol, cur_price)

                    if cfg.TRAILING_STOP_ENABLED and atr > 0:
                        new_sl = strategy.calc_trailing_stop(pos, cur_price, atr)
                        if new_sl != pos["sl"]:
                            log.info(f"Trailing SL {symbol}: {pos['sl']:.4f} → {new_sl:.4f}")
                            pos["sl"] = new_sl

                    sl_hit = (side == "long"  and cur_price <= pos["sl"]) or \
                             (side == "short" and cur_price >= pos["sl"])
                    if sl_hit:
                        handle_close_position(symbol, cur_price, "SL", sig)
                        open_count -= 1
                        continue

                    tp_hit = (side == "long"  and cur_price >= pos["tp"]) or \
                             (side == "short" and cur_price <= pos["tp"])
                    if tp_hit:
                        handle_close_position(symbol, cur_price, "TP", sig)
                        open_count -= 1
                        continue

                    # ── Salida por señal de estrategia ────────
                    exit_map = {"long": "exit_long", "short": "exit_short"}
                    if sig["action"] == exit_map.get(side):
                        handle_close_position(symbol, cur_price, "SIGNAL", sig)
                        open_count -= 1
                        continue

                    # ── Salida inteligente por agotamiento ────
                    exit_now, exit_reason = strategy.should_exit_early(df, pos)
                    if exit_now:
                        log.info(f"AGOTAMIENTO detectado {symbol}: {exit_reason}")
                        handle_close_position(symbol, cur_price, exit_reason, sig)
                        open_count -= 1
                        continue

                elif sig["action"] in ("buy", "sell_short"):
                    signals_found.append((symbol, sig))
                    log.info(
                        f"  SEÑAL: {symbol} {sig['action']} "
                        f"RSI={sig.get('rsi')} Score={sig.get('score','?')} — {sig['reason']}"
                    )

            except Exception as e:
                log.debug(f"  {symbol}: error — {str(e)[:80]}")

        if i + cfg.SCAN_BATCH_SIZE < total:
            time.sleep(1)

    # Ordenar por score desc, luego por RSI mas extremo
    signals_found.sort(key=lambda x: (-x[1].get("score", 0), x[1].get("rsi", 99)))

    for symbol, sig in signals_found:
        if open_count >= cfg.MAX_POSITIONS:
            log.info(f"Max posiciones ({cfg.MAX_POSITIONS}) — señal ignorada: {symbol}")
            db.log_signal(symbol, sig, executed=False)
            break
        if handle_open_position(symbol, sig, balance):
            open_count += 1

    # Reporte horario
    if datetime.now() - state.last_report >= timedelta(hours=1):
        pos_list = [
            {"symbol": s, "entry": p["entry"],
             "current": p.get("current", p["entry"]), "side": p.get("side", "long")}
            for s, p in state.positions.items()
        ]
        tg.send_status(pos_list, balance, state.stats, learner.get_performance_report())
        state.last_report = datetime.now()


def main():
    db.init_db()
    log.info("=" * 60)
    log.info("  BB+RSI BOT ELITE v5 — 24/7 INDESTRUCTIBLE")
    log.info(f"  Riesgo: {cfg.RISK_PCT*100:.1f}% | Leverage: {cfg.LEVERAGE}x")
    log.info(f"  Compound: ON | Learner: ON | Circuit Breaker: ON")
    log.info(f"  Vol.min: ${cfg.MIN_VOLUME_USDT:,.0f} | MaxPares: {cfg.MAX_SYMBOLS}")
    log.info("=" * 60)

    symbols = symbols_loader.load_symbols(force=True)
    if not symbols:
        log.error("No se pudieron cargar pares. Revisa API keys.")
        return

    tg.send_startup(symbols_loader.get_symbol_stats())

    bal = ex.get_balance()
    state.initial_balance = bal if bal > 0 else 1.0
    state.peak_balance    = bal
    cfg.BALANCE_SNAPSHOT  = bal
    log.info(f"Balance inicial: ${bal:.4f} USDT")

    if bal < cfg.MIN_USDT_BALANCE:
        log.warning(
            f"Balance ${bal:.4f} < minimo ${cfg.MIN_USDT_BALANCE}. "
            f"Enviando señales manuales a Telegram."
        )

    db.log_params(cfg.BB_PERIOD, cfg.BB_SIGMA, cfg.RSI_OB, cfg.SL_ATR, "Arranque Elite v5")

    # ── Bucle 24/7 INDESTRUCTIBLE ────────────────────────
    # Nunca para. Cualquier error se captura, se loguea y continua.
    consecutive_errors = 0
    while True:
        try:
            run_cycle()
            consecutive_errors = 0  # reset en ciclo exitoso
        except KeyboardInterrupt:
            log.info("Bot detenido por usuario (Ctrl+C)")
            tg.send_error("Bot detenido manualmente por usuario")
            break
        except Exception as e:
            consecutive_errors += 1
            tb = traceback.format_exc()
            log.error(f"Error ciclo #{state.iteration} ({consecutive_errors} consecutivos): {e}")
            log.debug(tb)

            # Notificar solo cada 5 errores para no spam
            if consecutive_errors % 5 == 1:
                tg.send_error(
                    f"Error #{consecutive_errors}: {str(e)[:200]}\n"
                    f"Bot continua automaticamente..."
                )

            # Si hay muchos errores seguidos, esperar mas tiempo
            if consecutive_errors >= 10:
                wait = min(cfg.LOOP_SECONDS * 5, 1800)  # max 30 min
                log.warning(f"{consecutive_errors} errores seguidos — esperando {wait}s")
                time.sleep(wait)
                continue

        wait = cfg.LOOP_SECONDS
        log.info(f"Esperando {wait}s...")
        time.sleep(wait)


if __name__ == "__main__":
    main()