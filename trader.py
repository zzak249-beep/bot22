import json
import os
from datetime import datetime, timezone
from config import (LEVERAGE, TRADE_MODE, VERSION,
                    TRAIL_ATR_MULT_INIT, TRAIL_ATR_MULT_AFTER, TRAIL_FROM_START,
                    REENTRY_ENABLED, REENTRY_COOLDOWN, INITIAL_BAL)
import bingx_api as api
import telegram_notifier as tg
import risk_manager as rm

# ══════════════════════════════════════════════════════
# trader.py — Ejecución y gestión de posiciones v12.3
# Mejoras: trailing SL desde apertura, re-entry,
#          verificación de orden, sizing dinámico
# ══════════════════════════════════════════════════════

STATE_FILE       = "positions.json"
PAPER_FILE       = "paper_trades.json"
REENTRY_FILE     = "reentry_log.json"


# ─── Estado persistente ────────────────────────────────
def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except Exception: pass
    return default

def _save(path, data):
    with open(path, "w") as f: json.dump(data, f, indent=2)


_positions: dict = _load(STATE_FILE, {})
_reentry_log: dict = _load(REENTRY_FILE, {})


def get_positions() -> dict:
    return _positions


def get_balance() -> float:
    if TRADE_MODE == "live":
        b = api.get_balance()
        rm.reset_daily_if_needed(b)
        rm.update_peak(b)
        return b
    # paper: balance inicial + suma PnL
    trades = _load(PAPER_FILE, [])
    return INITIAL_BAL + sum(t["pnl"] for t in trades)


def _can_reentry(sym: str, side: str) -> bool:
    """True si han pasado suficientes horas desde el último SL en este par."""
    if not REENTRY_ENABLED:
        return False
    log = _reentry_log.get(sym)
    if not log:
        return False
    if log.get("side") != side:
        return False   # SL fue en la misma dirección
    try:
        last = datetime.fromisoformat(log["time"])
        hours_passed = (datetime.now(timezone.utc) - last.replace(tzinfo=timezone.utc)).seconds / 3600
        return hours_passed >= REENTRY_COOL_DOWN
    except Exception:
        return False

def _record_sl_for_reentry(sym: str, side: str):
    _reentry_log[sym] = {"side": side, "time": datetime.now(timezone.utc).isoformat()}
    _save(REENTRY_FILE, _reentry_log)


# ─── Abrir posición ────────────────────────────────────
def open_trade(sym: str, signal: dict, balance: float) -> bool:
    if sym in _positions:
        return False

    open_count = len(_positions)
    can, reason = rm.can_open_position(open_count, balance)
    if not can:
        print(f"  [{sym}] bloqueado: {reason}")
        return False

    side  = signal["side"]
    price = signal["price"]
    sl    = signal["sl"]
    tp    = signal["tp"]
    tp_p  = signal["tp_p"]
    atr   = signal.get("atr", 0)

    if balance <= 0:
        tg.notify_no_funds(sym, side, signal["score"], signal["rsi"], price, sl, tp)
        return False

    qty = rm.calc_position_size(balance, price, sl, atr)

    executed = False
    if TRADE_MODE == "live":
        api.set_leverage(sym, LEVERAGE)
        result = api.open_order(sym, side, qty, sl, tp)
        if "error" not in str(result.get("code", "")) and result.get("data"):
            executed = True
        else:
            print(f"  [{sym}] Error orden: {result}")
            tg.notify_error(f"Error abriendo {sym}: {result}")
            return False
    else:
        executed = True  # paper siempre ejecuta

    if executed:
        _positions[sym] = {
            "side":         side,
            "entry":        price,
            "qty":          qty,
            "sl":           sl,
            "sl_initial":   sl,
            "tp":           tp,
            "tp_p":         tp_p,
            "partial_done": False,
            "score":        signal["score"],
            "rsi_e":        signal["rsi"],
            "trend":        signal["trend"],
            "bias_4h":      signal.get("bias_4h", "flat"),
            "reentry":      signal.get("reentry", False),
            "open_time":    datetime.now(timezone.utc).isoformat(),
            "atr":          atr,
        }
        _save(STATE_FILE, _positions)

    new_bal = get_balance()
    tg.notify_signal(sym, side, signal["score"], signal["rsi"],
                     price, sl, tp, signal["trend"], executed, new_bal)
    return executed


# ─── Revisar salidas y trailing SL ─────────────────────
def check_exits(sym: str, current_price: float):
    pos = _positions.get(sym)
    if not pos:
        return

    side  = pos["side"]
    entry = pos["entry"]
    sl    = pos["sl"]
    tp    = pos["tp"]
    tp_p  = pos["tp_p"]
    qty   = pos["qty"]
    atr   = pos.get("atr", 0)
    p     = current_price

    # ── Trailing SL dinámico desde apertura ──────────────
    if TRAIL_FROM_START and not pos["partial_done"] and atr > 0:
        mult = TRAIL_ATR_MULT_INIT
        if side == "long":
            trail = p - atr * mult
            if trail > sl:
                pos["sl"] = trail
                _save(STATE_FILE, _positions)
        else:
            trail = p + atr * mult
            if trail < sl:
                pos["sl"] = trail
                _save(STATE_FILE, _positions)

    # ── Partial TP ────────────────────────────────────────
    if not pos["partial_done"]:
        hit_partial = (side == "long" and p >= tp_p) or \
                      (side == "short" and p <= tp_p)
        if hit_partial:
            if TRADE_MODE == "live":
                api.close_position(sym, side, qty * 0.5)
            pos["qty"]          *= 0.5
            pos["sl"]            = entry      # SL → breakeven
            pos["partial_done"]  = True
            _save(STATE_FILE, _positions)
            tg.notify_partial_tp(sym, side, p, get_balance())
            return

    # ── Trailing SL más ajustado tras partial TP ─────────
    if pos["partial_done"] and atr > 0:
        mult = TRAIL_ATR_MULT_AFTER
        if side == "long":
            trail = p - atr * mult
            if trail > pos["sl"]:
                pos["sl"] = trail
                _save(STATE_FILE, _positions)
        else:
            trail = p + atr * mult
            if trail < pos["sl"]:
                pos["sl"] = trail
                _save(STATE_FILE, _positions)

    # ── SL / TP hit ───────────────────────────────────────
    sl_hit = (side == "long" and p <= pos["sl"]) or \
             (side == "short" and p >= pos["sl"])
    tp_hit = (side == "long" and p >= tp) or \
             (side == "short" and p <= tp)

    reason = None; exit_p = p
    if sl_hit:  exit_p = pos["sl"]; reason = "SL"
    elif tp_hit: exit_p = tp;        reason = "TP"

    if reason:
        _execute_close(sym, pos, exit_p, reason)


def _execute_close(sym: str, pos: dict, exit_p: float, reason: str):
    side  = pos["side"]
    entry = pos["entry"]
    qty   = pos["qty"]

    if TRADE_MODE == "live":
        api.close_position(sym, side, qty)

    pct = (exit_p - entry) / entry if side == "long" else (entry - exit_p) / entry
    pnl = qty * entry * pct * LEVERAGE

    is_win = pnl > 0
    if is_win:
        rm.record_win()
    else:
        rm.record_loss()
        _record_sl_for_reentry(sym, side)

    if TRADE_MODE == "paper":
        trades = _load(PAPER_FILE, [])
        trades.append({
            "symbol":   sym, "side": side, "entry": entry,
            "exit":     exit_p, "pnl": round(pnl, 4),
            "result":   "WIN" if is_win else "LOSS",
            "reason":   reason, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "score":    pos.get("score", 0), "rsi_e": pos.get("rsi_e", 0),
            "trend":    pos.get("trend", "?"), "bias_4h": pos.get("bias_4h", "?"),
            "reentry":  pos.get("reentry", False),
            "open_time": pos.get("open_time", ""),
            "close_time": datetime.now(timezone.utc).isoformat(),
        })
        _save(PAPER_FILE, trades)

    del _positions[sym]
    _save(STATE_FILE, _positions)
    tg.notify_close(sym, side, entry, exit_p, pnl, reason, get_balance())


def get_reentry_info(sym: str) -> dict | None:
    """Retorna info de re-entry si aplica para este par."""
    log = _reentry_log.get(sym)
    if not log or not REENTRY_ENABLED:
        return None
    try:
        last = datetime.fromisoformat(log["time"].replace("Z", "+00:00"))
        now  = datetime.now(timezone.utc)
        hours = (now - last).total_seconds() / 3600
        if hours >= REENTRY_COOLDOWN:
            return log   # cooldown cumplido → puede re-entrar
    except Exception:
        pass
    return None


def get_trade_history(limit: int = 50) -> list:
    trades = _load(PAPER_FILE, [])
    return trades[-limit:]


def get_summary() -> dict:
    trades = _load(PAPER_FILE, [])
    if not trades:
        return {}
    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    total_pnl = sum(t["pnl"] for t in trades)
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    return {
        "total":    len(trades),
        "wins":     len(wins),
        "losses":   len(losses),
        "wr":       round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "pnl":      round(total_pnl, 4),
        "pf":       round(gw / gl, 2) if gl > 0 else 999,
    }
