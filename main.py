"""
main.py — Bucle principal ELITE v6
Mejoras v6:
  - StochRSI + ADX + confirmacion de vela en validacion de señal
  - 3 niveles de TP (TP1 30% / TP2 40% / TP3 trailing 30%)
  - Trailing dinamico: se ajusta segun volatilidad (ATR/precio)
  - Stale trade timeout: cierra trades sin movimiento en X horas
  - Filtro de sentimiento: Fear&Greed + noticias CryptoPanic
  - Blacklist del learner integrada
  - Ranking de señales mejorado (score + RSI extremo + volumen)
  - Cache de candles para no pedir dos veces el mismo par
"""
import time
import logging
import traceback
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import pandas as pd

import config as cfg
import strategy
import exchange as ex
import notifier as tg
import database as db
import learner
import sentiment as snt
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
# INDICADORES TECNICOS AUXILIARES
# ═══════════════════════════════════════════════════════════

def calc_stoch_rsi(closes: pd.Series, period=14, k=3, d=3):
    """Stochastic RSI — retorna (K, D) entre 0 y 100."""
    delta  = closes.diff()
    gain   = delta.clip(lower=0).rolling(period).mean()
    loss   = (-delta.clip(upper=0)).rolling(period).mean()
    rs     = gain / loss.replace(0, 1e-10)
    rsi    = 100 - (100 / (1 + rs))
    lo     = rsi.rolling(period).min()
    hi     = rsi.rolling(period).max()
    stoch  = (rsi - lo) / (hi - lo).replace(0, 1e-10) * 100
    k_line = stoch.rolling(k).mean()
    d_line = k_line.rolling(d).mean()
    return float(k_line.iloc[-1] or 0), float(d_line.iloc[-1] or 0)


def calc_adx(df: pd.DataFrame, period=14) -> float:
    """ADX — fuerza de tendencia (0-100)."""
    hi, lo, cl = df["high"], df["low"], df["close"]
    tr  = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    pdm = hi.diff().clip(lower=0)
    ndm = (-lo.diff()).clip(lower=0)
    pdm = pdm.where(pdm > ndm, 0)
    ndm = ndm.where(ndm > pdm, 0)
    atr = tr.rolling(period).mean()
    pdi = 100 * pdm.rolling(period).mean() / atr.replace(0, 1e-10)
    ndi = 100 * ndm.rolling(period).mean() / atr.replace(0, 1e-10)
    dx  = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1e-10)
    return float(dx.rolling(period).mean().iloc[-1] or 0)


def is_solid_candle(df: pd.DataFrame) -> bool:
    """
    Vela de señal tiene cuerpo > CANDLE_CONFIRM_MIN_BODY del rango.
    Evita entradas en doji / velas de indecision.
    """
    if not cfg.CANDLE_CONFIRM_ENABLED or len(df) < 2:
        return True
    last = df.iloc[-1]
    rng  = last["high"] - last["low"]
    body = abs(last["close"] - last["open"])
    return (body / rng >= cfg.CANDLE_CONFIRM_MIN_BODY) if rng > 0 else False


def calc_trailing_dynamic(pos: dict, cur_price: float, atr: float) -> float:
    """
    Trailing stop dinamico: se ajusta segun volatilidad actual.
    Alta vol (ATR/precio > threshold) → trailing mas amplio.
    Baja vol → trailing mas ajustado para proteger ganancias.
    """
    if not cfg.TRAILING_DYNAMIC_ENABLED:
        mult = cfg.TRAILING_STOP_ATR
    else:
        atr_ratio = atr / cur_price if cur_price > 0 else 0
        if atr_ratio > cfg.TRAILING_VOL_THRESHOLD:
            mult = cfg.TRAILING_ATR_HIGH_VOL   # alta vol: mas espacio
        else:
            mult = cfg.TRAILING_ATR_LOW_VOL    # baja vol: mas ajustado

    side   = pos.get("side", "long")
    entry  = pos["entry"]
    gain   = (cur_price - entry) / entry if side == "long" else (entry - cur_price) / entry

    # Solo activar si hay ganancia minima
    if gain < cfg.TRAILING_ACTIVATE_PCT / 100:
        return pos["sl"]

    if side == "long":
        new_sl = cur_price - atr * mult
        return max(new_sl, pos["sl"])   # nunca bajar el SL
    else:
        new_sl = cur_price + atr * mult
        return min(new_sl, pos["sl"])   # nunca subir el SL en short


# ═══════════════════════════════════════════════════════════
# STALE TRADE: cerrar trades sin movimiento
# ═══════════════════════════════════════════════════════════

def is_stale_trade(pos: dict, cur_price: float) -> bool:
    """
    Detecta si un trade lleva demasiado tiempo sin moverse.
    Condicion: abierto hace > STALE_TRADE_HOURS y
               movimiento desde entrada < STALE_TRADE_MIN_MOVE (0.5%)
    """
    if not cfg.STALE_TRADE_ENABLED:
        return False
    open_time = pos.get("open_time")
    if not open_time:
        return False
    hours_open = (datetime.now() - open_time).total_seconds() / 3600
    if hours_open < cfg.STALE_TRADE_HOURS:
        return False
    entry = pos.get("entry", cur_price)
    move  = abs(cur_price - entry) / entry if entry > 0 else 0
    return move < cfg.STALE_TRADE_MIN_MOVE


# ═══════════════════════════════════════════════════════════
# MULTI-TP: 3 niveles de toma de ganancias
# ═══════════════════════════════════════════════════════════

def handle_multi_tp(symbol: str, cur_price: float, atr: float):
    """
    Gestiona 3 niveles de TP:
      TP1 (1.2x ATR) → cerrar 30%  — mover SL a breakeven
      TP2 (2.0x ATR) → cerrar 40%  — mover SL a TP1
      TP3             → trailing sobre el 30% restante
    """
    if not cfg.MULTI_TP_ENABLED or symbol not in state.positions:
        return
    pos   = state.positions[symbol]
    side  = pos.get("side", "long")
    entry = pos["entry"]

    if atr <= 0:
        return

    # Calcular niveles
    tp1 = entry + atr * cfg.TP1_ATR_MULT if side == "long" else entry - atr * cfg.TP1_ATR_MULT
    tp2 = entry + atr * cfg.TP2_ATR_MULT if side == "long" else entry - atr * cfg.TP2_ATR_MULT

    tp1_hit = (side == "long" and cur_price >= tp1) or (side == "short" and cur_price <= tp1)
    tp2_hit = (side == "long" and cur_price >= tp2) or (side == "short" and cur_price <= tp2)

    # ── TP2: cerrar 40% ──────────────────────────────────
    if tp2_hit and not pos.get("tp2_done"):
        qty_40 = round(pos["qty"] * cfg.TP2_CLOSE_PCT, 4)
        if qty_40 > 0:
            ex.close_long(symbol, qty_40) if side == "long" else ex.close_short(symbol, qty_40)
            pos["qty"]     -= qty_40
            pos["tp2_done"] = True
            pnl_pct = (cur_price - entry) / entry if side == "long" else (entry - cur_price) / entry
            pnl_est = qty_40 * entry * pnl_pct * cfg.LEVERAGE
            tg.send_close_signal(symbol, entry, cur_price, pnl_est, "TP2_40%", True)
            log.info(f"TP2 {symbol}: 40% cerrado @ {cur_price} PnL~${pnl_est:+.2f}")
            pos["sl"] = tp1   # mover SL a TP1
            log.info(f"SL → TP1 ({tp1:.4f})")
        return

    # ── TP1: cerrar 30% ──────────────────────────────────
    if tp1_hit and not pos.get("tp1_done"):
        qty_30 = round(pos["qty"] * cfg.TP1_CLOSE_PCT, 4)
        if qty_30 > 0:
            ex.close_long(symbol, qty_30) if side == "long" else ex.close_short(symbol, qty_30)
            pos["qty"]     -= qty_30
            pos["tp1_done"] = True
            pnl_pct = (cur_price - entry) / entry if side == "long" else (entry - cur_price) / entry
            pnl_est = qty_30 * entry * pnl_pct * cfg.LEVERAGE
            tg.send_close_signal(symbol, entry, cur_price, pnl_est, "TP1_30%", True)
            log.info(f"TP1 {symbol}: 30% cerrado @ {cur_price} PnL~${pnl_est:+.2f}")
            pos["sl"] = entry  # SL a breakeven
            log.info(f"SL → breakeven ({entry:.4f})")


# ═══════════════════════════════════════════════════════════
# FILTRO HORARIO
# ═══════════════════════════════════════════════════════════

def is_low_liquidity_hour() -> bool:
    if not cfg.TIME_FILTER_ENABLED:
        return False
    hour  = datetime.now(timezone.utc).hour
    start = cfg.TIME_FILTER_OFF_START
    end   = cfg.TIME_FILTER_OFF_END
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


# ═══════════════════════════════════════════════════════════
# VALIDACION DE SEÑAL
# ═══════════════════════════════════════════════════════════

def validate_signal(symbol: str, sig: dict, df: pd.DataFrame) -> tuple:
    """
    Valida una señal antes de ejecutarla.
    Retorna (es_valida, razon_rechazo).
    """
    action = sig.get("action", "buy")

    # Blacklist
    if symbol in learner.get_blacklist():
        return False, "Par en blacklist"

    # R:R minimo
    entry, sl, tp = sig.get("entry", 0), sig.get("sl", 0), sig.get("tp", 0)
    if entry and sl and tp and entry != sl:
        risk   = abs(entry - sl)
        reward = abs(tp - entry)
        rr     = reward / risk if risk > 0 else 0
        if rr < cfg.MIN_RR_RATIO:
            return False, f"R:R bajo ({rr:.2f})"

    # ADX
    if cfg.ADX_FILTER_ENABLED and len(df) >= 30:
        adx = calc_adx(df, cfg.ADX_PERIOD)
        if adx < cfg.ADX_MIN:
            return False, f"ADX bajo ({adx:.1f} < {cfg.ADX_MIN})"

    # StochRSI — solo bloquea en zonas extremas opuestas
    if cfg.STOCH_RSI_ENABLED and len(df) >= 40:
        k, d = calc_stoch_rsi(df["close"], cfg.STOCH_RSI_PERIOD, cfg.STOCH_RSI_K, cfg.STOCH_RSI_D)
        if action == "buy" and k > cfg.STOCH_RSI_OB:        # LONG bloqueado si sobrecomprado
            return False, f"StochRSI sobrecomprado para LONG ({k:.1f})"
        if action == "sell_short" and k < cfg.STOCH_RSI_OS: # SHORT bloqueado si sobrevendido
            return False, f"StochRSI sobrevendido para SHORT ({k:.1f})"

    # Confirmacion de vela
    if not is_solid_candle(df):
        return False, "Vela de indecision (doji)"

    # Volumen
    if cfg.VOLUME_CONFIRM_ENABLED and len(df) >= 20:
        vol_now = df["volume"].iloc[-1]
        vol_avg = df["volume"].iloc[-20:].mean()
        if vol_now < vol_avg * cfg.VOLUME_CONFIRM_MULT:
            return False, f"Volumen bajo ({vol_now:.0f} < {vol_avg * cfg.VOLUME_CONFIRM_MULT:.0f})"

    # Sentimiento
    sent_ok, sent_reason = snt.sentiment_ok(symbol, action)
    if not sent_ok:
        return False, sent_reason

    return True, ""


# ═══════════════════════════════════════════════════════════
# ESTADO DEL BOT
# ═══════════════════════════════════════════════════════════

class BotState:
    def __init__(self):
        self.positions          = {}
        self.cooldowns          = defaultdict(int)
        self.last_report        = datetime.now()
        self.trades_closed      = 0
        self.iteration          = 0
        self.circuit_open       = False
        self.circuit_reason     = ""
        self.initial_balance    = 0.0
        self.peak_balance       = 0.0
        self.skip_rr            = 0
        self.skip_vol           = 0
        self.skip_hour          = 0
        self.skip_adx           = 0
        self.skip_sent          = 0
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
            self.skip_rr = self.skip_vol = self.skip_hour = self.skip_adx = self.skip_sent = 0
            if self.circuit_open:
                self.circuit_open   = False
                self.circuit_reason = ""
                log.info("Circuit breaker reseteado (nuevo dia)")

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


# ═══════════════════════════════════════════════════════════
# CANDLES
# ═══════════════════════════════════════════════════════════

def fetch_candles(symbol, tf, limit=200):
    bars = ex.get_exchange().fetch_ohlcv(symbol, tf, limit=limit)
    df   = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.reset_index(drop=True)


# ═══════════════════════════════════════════════════════════
# ABRIR POSICION
# ═══════════════════════════════════════════════════════════

def handle_open_position(symbol, sig, balance, df=None):
    if state.circuit_open:
        log.warning(f"Circuit breaker — ignorando {symbol}")
        return False

    # Filtro horario
    if is_low_liquidity_hour():
        state.skip_hour += 1
        log.info(f"Hora baja liq. ({datetime.now(timezone.utc).hour}h UTC) — {symbol}")
        return False

    # Validacion de señal
    if df is not None:
        valid, reason = validate_signal(symbol, sig, df)
        if not valid:
            log.info(f"Señal rechazada {symbol}: {reason}")
            if "R:R"     in reason: state.skip_rr   += 1
            elif "ADX"   in reason: state.skip_adx  += 1
            elif "olumen" in reason: state.skip_vol  += 1
            elif "entim" in reason: state.skip_sent += 1
            return False

    # Sin fondos: señal manual completa a Telegram
    if balance < cfg.MIN_USDT_BALANCE:
        db.log_signal(symbol, sig, executed=False)
        action     = sig.get("action", "buy")
        side_txt   = "🟢 LONG" if action == "buy" else "🔴 SHORT"
        side_bingx = "BUY / LONG" if action == "buy" else "SELL / SHORT"
        entry      = sig["entry"]
        sl         = sig["sl"]
        tp         = sig["tp"]
        tp1        = sig.get("tp_partial", "")
        rsi        = sig.get("rsi", "N/A")
        atr        = sig.get("atr", 0)
        score      = sig.get("score", 0)
        reason     = sig.get("reason", "")
        trend_4h   = sig.get("trend_4h", "N/A")

        # Calcular R:R y distancias
        risk       = abs(entry - sl) if sl else 0
        reward     = abs(tp - entry) if tp else 0
        rr         = round(reward / risk, 2) if risk > 0 else 0
        sl_pct     = round(risk / entry * 100, 2) if entry > 0 else 0
        tp_pct     = round(reward / entry * 100, 2) if entry > 0 else 0

        # Nombre limpio del par para BingX
        base       = symbol.split("/")[0]
        pair_clean = f"{base}/USDT"

        mood = snt.get_market_mood()

        tg.send_raw(
            f"{'🟢' if action == 'buy' else '🔴'} *SEÑAL {side_txt}* — `{pair_clean}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *Entrada*  : `{entry}`\n"
            f"🛑 *Stop Loss*: `{sl}` _(-{sl_pct}%)_\n"
            f"🎯 *TP final* : `{tp}` _(+{tp_pct}%)_\n"
            + (f"🎯 *TP1 (50%)*: `{tp1}`\n" if tp1 else "") +
            f"📊 *R:R*      : `{rr}x`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 RSI        : `{rsi}`\n"
            f"📉 ATR        : `{round(atr,4) if atr else 'N/A'}`\n"
            f"🕐 Tendencia 4h: `{trend_4h}`\n"
            f"⭐ Score      : `{score}/100`\n"
            f"💬 Razón      : _{reason}_\n"
            f"🌡 Mercado    : _{mood}_\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 *PASOS EN BINGX FUTUROS:*\n"
            f"1️⃣ Buscar `{pair_clean}` → Futuros\n"
            f"2️⃣ Lado: *{side_bingx}*\n"
            f"3️⃣ Apalancamiento: *{cfg.LEVERAGE}x*\n"
            f"4️⃣ Tipo: *Mercado* al precio ~`{entry}`\n"
            f"5️⃣ Stop Loss: `{sl}`\n"
            f"6️⃣ Take Profit: `{tp}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ _Señal válida ~15min — actúa rápido_"
        )
        return False

    result = ex.open_long(symbol, sig) if sig["action"] == "buy" else ex.open_short(symbol, sig)
    if result:
        trade_id = db.open_trade(
            symbol=symbol, signal=sig, qty=result["qty"],
            balance=balance, leverage=cfg.LEVERAGE,
            bb_sigma=cfg.BB_SIGMA, bb_period=cfg.BB_PERIOD, rsi_ob=cfg.RSI_OB,
        )
        db.log_signal(symbol, sig, executed=True, trade_id=trade_id)
        state.positions[symbol] = {
            "side":       result["side"],
            "entry":      sig["entry"],
            "sl":         sig["sl"],
            "tp":         sig["tp"],
            "qty":        result["qty"],
            "trade_id":   trade_id,
            "open_time":  datetime.now(),
            "current":    sig["entry"],
            "tp1_done":   False,
            "tp2_done":   False,
            "score":      sig.get("score", 0),
        }
        tg.send_buy_signal(symbol, sig, balance, executed=True)
        log.info(f"ABIERTO {result['side'].upper()}: {symbol} @ {sig['entry']} | {sig.get('reason','')}")
        return True
    else:
        tg.send_error(f"No se pudo abrir {symbol}")
        return False


# ═══════════════════════════════════════════════════════════
# CERRAR POSICION
# ═══════════════════════════════════════════════════════════

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

    tg.send_close_signal(symbol, entry, cur_price, pnl_est, reason, executed)
    state.record_close(pnl_est)
    state.cooldowns[symbol] = cfg.COOLDOWN_BARS
    del state.positions[symbol]
    log.info(f"CERRADO {side.upper()}: {symbol} | PnL~${pnl_est:+.2f} | {reason}")

    # Circuit Breaker
    triggered, cb_reason = strategy.check_circuit_breaker(state.stats, cfg.BALANCE_SNAPSHOT)
    if triggered and not state.circuit_open:
        state.circuit_open   = True
        state.circuit_reason = cb_reason
        log.warning(f"CIRCUIT BREAKER: {cb_reason}")
        tg.send_error(f"⚠️ Circuit Breaker\n{cb_reason}\nBot pausado hasta mañana.")

    # Learner
    if learner.should_review(state.trades_closed):
        updates = learner.analyze_and_adjust()
        if updates:
            tg.send_param_update(updates, learner.get_performance_report())
        state.trades_closed = 0


# ═══════════════════════════════════════════════════════════
# CICLO PRINCIPAL
# ═══════════════════════════════════════════════════════════

def run_cycle():
    state.iteration += 1
    state.reset_daily()

    if symbols_loader.needs_refresh():
        symbols_loader.load_symbols(force=True)
        tg.send_symbols_update(cfg.SYMBOLS)

    total    = len(cfg.SYMBOLS)
    hour_utc = datetime.now(timezone.utc).hour
    low_liq  = is_low_liquidity_hour()
    mood     = snt.get_market_mood()

    log.info(
        f"Ciclo #{state.iteration} | {datetime.now().strftime('%d/%m %H:%M')} | "
        f"{total} pares | W:{state.stats['wins']} L:{state.stats['losses']} "
        f"PnL:${state.stats['pnl_today']:+.2f} | {mood}"
        + (f" | 🌙{hour_utc}h UTC" if low_liq else f" | ☀️{hour_utc}h UTC")
        + (f" | ⚠️CB:{state.circuit_reason}" if state.circuit_open else "")
    )

    balance    = ex.get_balance()
    open_count = len(state.positions)
    log.info(f"Balance: ${balance:.2f} | Posiciones: {open_count}/{cfg.MAX_POSITIONS}")

    # Sincronizar cierres externos
    live_positions = {p["symbol"]: p for p in ex.get_open_positions()}
    for sym in list(state.positions.keys()):
        if sym not in live_positions:
            log.info(f"{sym}: cerrado externamente")
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
    df_cache      = {}

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
                df_cache[symbol] = df
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

                    # ── Multi-TP ──────────────────────────
                    if cfg.MULTI_TP_ENABLED:
                        handle_multi_tp(symbol, cur_price, atr)
                    elif cfg.PARTIAL_TP_ENABLED and not pos.get("tp1_done"):
                        # fallback legacy TP parcial
                        tp_part = pos.get("tp_partial") or pos.get("tp")
                        if tp_part:
                            hit = (side == "long"  and cur_price >= tp_part) or \
                                  (side == "short" and cur_price <= tp_part)
                            if hit:
                                qty_half = round(pos["qty"] * cfg.PARTIAL_TP_PCT, 4)
                                ex.close_long(symbol, qty_half) if side == "long" else ex.close_short(symbol, qty_half)
                                pos["qty"]     -= qty_half
                                pos["tp1_done"] = True
                                pos["sl"]       = pos["entry"]

                    # ── Trailing dinamico ─────────────────
                    if cfg.TRAILING_STOP_ENABLED and atr > 0:
                        new_sl = calc_trailing_dynamic(pos, cur_price, atr)
                        if new_sl != pos["sl"]:
                            log.info(f"Trailing SL {symbol}: {pos['sl']:.4f} → {new_sl:.4f}")
                            pos["sl"] = new_sl

                    # ── Stale trade ───────────────────────
                    if is_stale_trade(pos, cur_price):
                        log.info(f"STALE TRADE {symbol}: {cfg.STALE_TRADE_HOURS}h sin movimiento")
                        handle_close_position(symbol, cur_price, "STALE", sig)
                        open_count -= 1
                        continue

                    # ── SL ────────────────────────────────
                    sl_hit = (side == "long"  and cur_price <= pos["sl"]) or \
                             (side == "short" and cur_price >= pos["sl"])
                    if sl_hit:
                        handle_close_position(symbol, cur_price, "SL", sig)
                        open_count -= 1
                        continue

                    # ── TP final ──────────────────────────
                    tp_hit = (side == "long"  and cur_price >= pos["tp"]) or \
                             (side == "short" and cur_price <= pos["tp"])
                    if tp_hit:
                        handle_close_position(symbol, cur_price, "TP", sig)
                        open_count -= 1
                        continue

                    # ── Salida por señal ──────────────────
                    exit_map = {"long": "exit_long", "short": "exit_short"}
                    if sig["action"] == exit_map.get(side):
                        handle_close_position(symbol, cur_price, "SIGNAL", sig)
                        open_count -= 1
                        continue

                    # ── Salida por agotamiento ────────────
                    exit_now, exit_reason = strategy.should_exit_early(df, pos)
                    if exit_now:
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
                log.debug(f"  {symbol}: {str(e)[:80]}")

        if i + cfg.SCAN_BATCH_SIZE < total:
            time.sleep(1)

    # Ranking: score desc → RSI extremo desc → volumen relativo desc
    def signal_rank(item):
        sym, sig = item
        df_s      = df_cache.get(sym)
        score     = sig.get("score", 0)
        rsi_ext   = abs(50 - (sig.get("rsi", 50) or 50))
        vol_ratio = 1.0
        if df_s is not None and len(df_s) >= 20:
            vol_now   = df_s["volume"].iloc[-1]
            vol_avg   = df_s["volume"].iloc[-20:].mean()
            vol_ratio = vol_now / vol_avg if vol_avg > 0 else 1.0
        return (-score, -rsi_ext, -vol_ratio)

    signals_found.sort(key=signal_rank)

    skips = (f"Skip hoy → R:R:{state.skip_rr} ADX:{state.skip_adx} "
             f"Vol:{state.skip_vol} Hora:{state.skip_hour} Sent:{state.skip_sent}")
    if any([state.skip_rr, state.skip_adx, state.skip_vol, state.skip_hour, state.skip_sent]):
        log.info(skips)

    for symbol, sig in signals_found:
        if open_count >= cfg.MAX_POSITIONS:
            log.info(f"Max posiciones ({cfg.MAX_POSITIONS}) — ignorando: {symbol}")
            db.log_signal(symbol, sig, executed=False)
            break
        df_s = df_cache.get(symbol)
        if handle_open_position(symbol, sig, balance, df=df_s):
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


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    db.init_db()
    log.info("=" * 65)
    log.info("  BB+RSI BOT ELITE v6 — 24/7 INDESTRUCTIBLE")
    log.info(f"  Riesgo: {cfg.RISK_PCT*100:.1f}% | Leverage: {cfg.LEVERAGE}x")
    log.info(f"  Compound: ON | Learner: ON | Circuit Breaker: ON")
    log.info(f"  EMA200: {cfg.EMA_TREND_ENABLED} | ADX>{cfg.ADX_MIN}: {cfg.ADX_FILTER_ENABLED}")
    log.info(f"  StochRSI: {cfg.STOCH_RSI_ENABLED} | MultiTP: {cfg.MULTI_TP_ENABLED}")
    log.info(f"  Stale:{cfg.STALE_TRADE_HOURS}h | Trailing dinamico: {cfg.TRAILING_DYNAMIC_ENABLED}")
    log.info(f"  Sentimiento: {cfg.SENTIMENT_ENABLED} | F&G: {cfg.FEAR_GREED_ENABLED}")
    log.info(f"  Horario off: {cfg.TIME_FILTER_OFF_START}h-{cfg.TIME_FILTER_OFF_END}h UTC")
    log.info(f"  R:R min: {cfg.MIN_RR_RATIO}x | Candle confirm: {cfg.CANDLE_CONFIRM_ENABLED}")
    log.info("=" * 65)

    symbols = symbols_loader.load_symbols(force=True)
    if not symbols:
        log.error("No se pudieron cargar pares. Revisa API keys.")
        return

    tg.send_startup(symbols_loader.get_symbol_stats())

    bal = ex.get_balance()
    state.initial_balance = bal if bal > 0 else 1.0
    state.peak_balance    = bal
    cfg.BALANCE_SNAPSHOT  = bal
    log.info(f"Balance inicial: ${bal:.4f} USDT | Mercado: {snt.get_market_mood()}")

    if bal < cfg.MIN_USDT_BALANCE:
        log.warning(f"Balance ${bal:.4f} < minimo. Señales manuales a Telegram.")

    db.log_params(cfg.BB_PERIOD, cfg.BB_SIGMA, cfg.RSI_OB, cfg.SL_ATR, "Arranque Elite v6")

    consecutive_errors = 0
    while True:
        try:
            run_cycle()
            consecutive_errors = 0
        except KeyboardInterrupt:
            log.info("Bot detenido por usuario (Ctrl+C)")
            tg.send_error("Bot detenido manualmente")
            break
        except Exception as e:
            consecutive_errors += 1
            log.error(f"Error ciclo #{state.iteration} ({consecutive_errors}x): {e}")
            log.debug(traceback.format_exc())
            if consecutive_errors % 5 == 1:
                tg.send_error(f"Error #{consecutive_errors}: {str(e)[:200]}\nContinuando...")
            if consecutive_errors >= 10:
                wait = min(cfg.LOOP_SECONDS * 5, 1800)
                log.warning(f"{consecutive_errors} errores — esperando {wait}s")
                time.sleep(wait)
                continue

        log.info(f"Esperando {cfg.LOOP_SECONDS}s...")
        time.sleep(cfg.LOOP_SECONDS)


if __name__ == "__main__":
    main()
