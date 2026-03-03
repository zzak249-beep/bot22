"""
main.py — Bucle principal ELITE v5+
Mejoras v5+:
  - Filtro horario: no operar en horas de baja liquidez (configurable)
  - Confirmacion de volumen en entrada
  - Filtro R:R minimo antes de abrir posicion
  - Ranking de señales mejorado (score + RSI + volumen)
  - Blacklist de pares perdedores del learner
  - Watchdog de salud mejorado
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


# ═══════════════════════════════════════════════════════════
# MEJORA: FILTRO HORARIO
# ═══════════════════════════════════════════════════════════

def is_low_liquidity_hour() -> bool:
    """
    Retorna True si estamos en hora de baja liquidez configurada.
    Por defecto evita 01:00-06:00 UTC (madrugada asiatica/europea).
    """
    if not cfg.TIME_FILTER_ENABLED:
        return False
    hour = datetime.utcnow().hour
    start = cfg.TIME_FILTER_OFF_START
    end   = cfg.TIME_FILTER_OFF_END
    if start < end:
        return start <= hour < end
    else:
        # Rango que cruza medianoche (ej: 22-06)
        return hour >= start or hour < end


# ═══════════════════════════════════════════════════════════
# MEJORA: VALIDACION DE SEÑAL ANTES DE ABRIR
# ═══════════════════════════════════════════════════════════

def validate_signal_quality(symbol: str, sig: dict, df: pd.DataFrame) -> tuple[bool, str]:
    """
    Valida una señal antes de ejecutarla.
    Retorna (es_valida, razon_rechazo).
    MEJORAS: R:R minimo, volumen de confirmacion, blacklist.
    """
    # ── Blacklist de pares perdedores ─────────────────────
    if symbol in learner.get_blacklist():
        return False, f"Par en blacklist (demasiadas perdidas)"

    # ── Filtro R:R minimo ─────────────────────────────────
    entry = sig.get("entry", 0)
    sl    = sig.get("sl",    0)
    tp    = sig.get("tp",    0)
    if entry and sl and tp and entry != sl:
        risk   = abs(entry - sl)
        reward = abs(tp - entry)
        rr     = reward / risk if risk > 0 else 0
        if rr < cfg.MIN_RR_RATIO:
            return False, f"R:R insuficiente ({rr:.2f} < {cfg.MIN_RR_RATIO})"

    # ── Confirmacion de volumen ───────────────────────────
    if cfg.VOLUME_CONFIRM_ENABLED and len(df) >= 20:
        vol_actual = df["volume"].iloc[-1]
        vol_media  = df["volume"].iloc[-20:].mean()
        if vol_actual < vol_media * cfg.VOLUME_CONFIRM_MULT:
            return False, f"Volumen bajo ({vol_actual:.0f} < {vol_media * cfg.VOLUME_CONFIRM_MULT:.0f})"

    return True, ""


class BotState:
    def __init__(self):
        self.positions          = {}
        self.cooldowns          = defaultdict(int)
        self.last_report        = datetime.now()
        self.last_health        = datetime.now()
        self.trades_closed      = 0
        self.iteration          = 0
        self.circuit_open       = False
        self.circuit_reason     = ""
        self.consecutive_errors = 0
        self.initial_balance    = 0.0
        self.peak_balance       = 0.0
        self.signals_skipped_rr     = 0  # MEJORA: contador de señales rechazadas
        self.signals_skipped_vol    = 0
        self.signals_skipped_hour   = 0
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
            # Resetear contadores diarios
            self.signals_skipped_rr   = 0
            self.signals_skipped_vol  = 0
            self.signals_skipped_hour = 0
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


def handle_open_position(symbol, sig, balance, df=None):
    # ── Circuit Breaker ───────────────────────────────────
    if state.circuit_open:
        log.warning(f"Circuit breaker activo — señal ignorada: {symbol} ({state.circuit_reason})")
        return False

    # ── MEJORA: Filtro horario ────────────────────────────
    if is_low_liquidity_hour():
        hour = datetime.utcnow().hour
        state.signals_skipped_hour += 1
        log.info(f"Hora baja liquidez ({hour}h UTC) — señal ignorada: {symbol}")
        return False

    # ── MEJORA: Validacion de calidad de señal ────────────
    if df is not None:
        valid, reason = validate_signal_quality(symbol, sig, df)
        if not valid:
            log.info(f"Señal rechazada {symbol}: {reason}")
            if "R:R" in reason:
                state.signals_skipped_rr += 1
            elif "Volumen" in reason:
                state.signals_skipped_vol += 1
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
    pos["sl"] = entry
    log.info(f"SL → breakeven: {entry}")


def run_cycle():
    state.iteration += 1
    state.reset_daily()

    if symbols_loader.needs_refresh():
        symbols_loader.load_symbols(force=True)
        tg.send_symbols_update(cfg.SYMBOLS)

    total = len(cfg.SYMBOLS)

    # ── MEJORA: Log de hora y filtros activos ─────────────
    hour_utc = datetime.utcnow().hour
    low_liq  = is_low_liquidity_hour()
    hour_txt = f" | 🌙 Baja liq. ({hour_utc}h UTC)" if low_liq else f" | ☀️ {hour_utc}h UTC"
    cb_txt   = f" | ⚠️CB: {state.circuit_reason}" if state.circuit_open else ""
    log.info(
        f"Ciclo #{state.iteration} | {datetime.now().strftime('%d/%m %H:%M')} | "
        f"{total} pares | W:{state.stats['wins']} L:{state.stats['losses']} "
        f"PnL:${state.stats['pnl_today']:+.2f}{hour_txt}{cb_txt}"
    )

    balance    = ex.get_balance()
    open_count = len(state.positions)
    log.info(f"Balance: ${balance:.2f} | Posiciones: {open_count}/{cfg.MAX_POSITIONS}")

    # ── Sincronizar cierres externos ──────────────────────
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
    df_cache      = {}   # MEJORA: cache de candles para no pedir dos veces

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
                df_cache[symbol] = df   # guardar para validacion posterior
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

                    exit_map = {"long": "exit_long", "short": "exit_short"}
                    if sig["action"] == exit_map.get(side):
                        handle_close_position(symbol, cur_price, "SIGNAL", sig)
                        open_count -= 1
                        continue

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

    # ── MEJORA: Ranking de señales mejorado ───────────────
    # Ordena por: score desc → RSI mas extremo → volumen relativo desc
    def signal_rank(item):
        sym, sig = item
        df_s     = df_cache.get(sym)
        score    = sig.get("score", 0)
        rsi      = sig.get("rsi", 50)
        # RSI mas alejado del centro (50) = señal mas extrema = mejor
        rsi_extreme = abs(50 - rsi)
        # Volumen relativo de la vela de señal vs media
        vol_ratio = 1.0
        if df_s is not None and len(df_s) >= 20:
            vol_now  = df_s["volume"].iloc[-1]
            vol_avg  = df_s["volume"].iloc[-20:].mean()
            vol_ratio = vol_now / vol_avg if vol_avg > 0 else 1.0
        return (-score, -rsi_extreme, -vol_ratio)

    signals_found.sort(key=signal_rank)

    # ── MEJORA: Log de señales filtradas ──────────────────
    if state.signals_skipped_rr or state.signals_skipped_vol or state.signals_skipped_hour:
        log.info(
            f"Señales descartadas hoy — R:R:{state.signals_skipped_rr} "
            f"Vol:{state.signals_skipped_vol} Hora:{state.signals_skipped_hour}"
        )

    for symbol, sig in signals_found:
        if open_count >= cfg.MAX_POSITIONS:
            log.info(f"Max posiciones ({cfg.MAX_POSITIONS}) — señal ignorada: {symbol}")
            db.log_signal(symbol, sig, executed=False)
            break
        df_s = df_cache.get(symbol)
        if handle_open_position(symbol, sig, balance, df=df_s):
            open_count += 1

    # ── Reporte horario ───────────────────────────────────
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
    log.info("  BB+RSI BOT ELITE v5+ — 24/7 INDESTRUCTIBLE")
    log.info(f"  Riesgo: {cfg.RISK_PCT*100:.1f}% | Leverage: {cfg.LEVERAGE}x")
    log.info(f"  Compound: ON | Learner: ON | Circuit Breaker: ON")
    log.info(f"  EMA Filter: {cfg.EMA_TREND_ENABLED} | ADX Filter: {cfg.ADX_FILTER_ENABLED}")
    log.info(f"  Vol.min: ${cfg.MIN_VOLUME_USDT:,.0f} | MaxPares: {cfg.MAX_SYMBOLS}")
    log.info(f"  Filtro horario: {cfg.TIME_FILTER_ENABLED} "
             f"({cfg.TIME_FILTER_OFF_START}h-{cfg.TIME_FILTER_OFF_END}h UTC off)")
    log.info(f"  R:R minimo: {cfg.MIN_RR_RATIO}x")
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

    db.log_params(cfg.BB_PERIOD, cfg.BB_SIGMA, cfg.RSI_OB, cfg.SL_ATR, "Arranque Elite v5+")

    consecutive_errors = 0
    while True:
        try:
            run_cycle()
            consecutive_errors = 0
        except KeyboardInterrupt:
            log.info("Bot detenido por usuario (Ctrl+C)")
            tg.send_error("Bot detenido manualmente por usuario")
            break
        except Exception as e:
            consecutive_errors += 1
            tb = traceback.format_exc()
            log.error(f"Error ciclo #{state.iteration} ({consecutive_errors} consecutivos): {e}")
            log.debug(tb)

            if consecutive_errors % 5 == 1:
                tg.send_error(
                    f"Error #{consecutive_errors}: {str(e)[:200]}\n"
                    f"Bot continua automaticamente..."
                )

            if consecutive_errors >= 10:
                wait = min(cfg.LOOP_SECONDS * 5, 1800)
                log.warning(f"{consecutive_errors} errores seguidos — esperando {wait}s")
                time.sleep(wait)
                continue

        wait = cfg.LOOP_SECONDS
        log.info(f"Esperando {wait}s...")
        time.sleep(wait)


if __name__ == "__main__":
    main()
