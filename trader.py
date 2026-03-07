"""
trader.py — Gestión de posiciones v14.0
Corrige todos los bugs de versiones anteriores.
"""
import json
import os
import logging
from datetime import datetime, timezone

log = logging.getLogger("trader")

try:
    import config as cfg
    LEVERAGE     = cfg.LEVERAGE
    TRADE_MODE   = cfg.TRADE_MODE
    VERSION      = cfg.VERSION
    TRAIL_INIT   = cfg.TRAIL_ATR_MULT_INIT
    TRAIL_AFTER  = cfg.TRAIL_ATR_MULT_AFTER
    TRAIL_START  = cfg.TRAIL_FROM_START
    REENTRY_EN   = cfg.REENTRY_ENABLED
    REENTRY_COOL = cfg.REENTRY_COOLDOWN
    INITIAL_BAL  = cfg.INITIAL_BAL
    PARCIAL_ACTIVO = cfg.CIERRE_PARCIAL_ACTIVO
    PARTIAL_ATR  = cfg.PARTIAL_TP_ATR
except Exception:
    LEVERAGE = 3; TRADE_MODE = "paper"; VERSION = "v14"
    TRAIL_INIT = 1.8; TRAIL_AFTER = 1.2; TRAIL_START = True
    REENTRY_EN = True; REENTRY_COOL = 2; INITIAL_BAL = 100.0
    PARCIAL_ACTIVO = True; PARTIAL_ATR = 2.0

import bingx_api as api
import telegram_notifier as tg
import risk_manager as rm

STATE_FILE   = "positions.json"
PAPER_FILE   = "paper_trades.json"
REENTRY_FILE = "reentry_log.json"


def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.error(f"_save {path}: {e}")


_positions:   dict = _load(STATE_FILE, {})
_reentry_log: dict = _load(REENTRY_FILE, {})


def get_positions() -> dict:
    return _positions


def get_balance() -> float:
    if TRADE_MODE == "live":
        try:
            b = api.get_balance()
            if b <= 0:
                log.warning("Balance Futures = $0 — verifica fondos en BingX")
            rm.reset_daily_if_needed(b)
            rm.update_peak(b)
            return max(b, 0.0)
        except Exception as e:
            log.error(f"get_balance live: {e}")
            return 0.0
    else:
        trades  = _load(PAPER_FILE, [])
        pnl_sum = sum(t.get("pnl", 0) for t in trades)
        bal     = INITIAL_BAL + pnl_sum
        rm.reset_daily_if_needed(max(bal, INITIAL_BAL))
        rm.update_peak(max(bal, INITIAL_BAL))
        return max(bal, 1.0)


def get_reentry_info(sym: str) -> dict | None:
    if not REENTRY_EN:
        return None
    entry = _reentry_log.get(sym)
    if not entry:
        return None
    try:
        last  = datetime.fromisoformat(entry["time"].replace("Z", "+00:00"))
        now   = datetime.now(timezone.utc)
        hours = (now - last).total_seconds() / 3600
        if hours >= REENTRY_COOL:
            return entry
    except Exception:
        pass
    return None


def open_trade(sym: str, signal: dict, balance: float = None) -> bool:
    if sym in _positions:
        return False

    balance = get_balance()
    open_count = len(_positions)
    can, reason = rm.can_open_position(open_count, balance)
    if not can:
        log.info(f"[{sym}] bloqueado: {reason}")
        return False

    side  = signal["side"]
    price = signal["price"]
    sl    = signal["sl"]
    tp    = signal["tp"]
    tp_p  = signal.get("tp_p", price + (tp - price) * 0.5)
    atr   = signal.get("atr", 0)

    if balance <= 0:
        log.error(f"[{sym}] balance=$0 — trade bloqueado")
        tg.notify_error(f"Balance $0 — {sym} bloqueado. Transfiere fondos a Futures.")
        return False

    qty = rm.calc_position_size(balance, price, sl, atr)
    if qty <= 0:
        log.warning(f"[{sym}] qty=0")
        return False

    executed = False
    if TRADE_MODE == "live":
        api.set_leverage(sym, LEVERAGE)
        result = api.open_order(sym, side, qty, sl, tp)
        if result.get("code", -1) == 0:
            executed = True
            oid = (result.get("data") or {}).get("order", {}).get("orderId", "?")
            log.info(f"[{sym}] ✅ orderId={oid}")
        else:
            code = result.get("code", "?")
            msg  = result.get("msg", "desconocido")
            log.error(f"[{sym}] BingX error code={code}: {msg}")
            tg.notify_error(f"Error BingX {sym} [{code}]: {msg}")
            return False
    else:
        executed = True  # paper mode

    if executed:
        _positions[sym] = {
            "side":         side,
            "entry":        price,
            "qty":          qty,
            "qty_initial":  qty,
            "sl":           sl,
            "sl_initial":   sl,
            "tp":           tp,
            "tp_p":         tp_p,
            "partial_done": False,
            "score":        signal.get("score", 0),
            "rsi_e":        signal.get("rsi", 50),
            "trend":        signal.get("trend", "?"),
            "bias_4h":      signal.get("bias_4h", "?"),
            "strategy":     signal.get("strategy", "?"),
            "reentry":      signal.get("reentry", False),
            "open_time":    datetime.now(timezone.utc).isoformat(),
            "atr":          atr,
        }
        _save(STATE_FILE, _positions)
        log.info(f"[{sym}] POSICIÓN ABIERTA {side.upper()} "
                 f"e={price} sl={sl:.6f} tp={tp:.6f} qty={qty}")

    new_bal = get_balance()
    tg.notify_signal(
        sym, side,
        signal.get("score", 0), signal.get("rsi", 50),
        price, sl, tp,
        signal.get("trend", "?"), executed, new_bal,
        bias_4h=signal.get("bias_4h", "?"),
        strategy=signal.get("strategy", "?")
    )
    return executed


def check_exits(sym: str, current_price: float):
    pos = _positions.get(sym)
    if not pos:
        return

    side  = pos["side"]
    entry = pos["entry"]
    tp    = pos["tp"]
    tp_p  = pos["tp_p"]
    qty   = pos["qty"]
    atr   = pos.get("atr", 0)
    p     = current_price

    # ── Trailing desde el inicio ──────────────────────
    if TRAIL_START and not pos["partial_done"] and atr > 0:
        if side == "long":
            trail = p - atr * TRAIL_INIT
            if trail > pos["sl"]:
                pos["sl"] = trail
                _save(STATE_FILE, _positions)
        else:
            trail = p + atr * TRAIL_INIT
            if trail < pos["sl"]:
                pos["sl"] = trail
                _save(STATE_FILE, _positions)

    # ── Cierre parcial al TP50 ───────────────────────
    if PARCIAL_ACTIVO and not pos["partial_done"]:
        hit_p = (side == "long" and p >= tp_p) or (side == "short" and p <= tp_p)
        if hit_p:
            half_qty = round(qty * 0.5, 4)
            if TRADE_MODE == "live":
                api.close_position(sym, side, half_qty)
            pos["qty"]         = round(qty * 0.5, 4)
            pos["sl"]          = entry   # mover SL a breakeven
            pos["partial_done"] = True
            _save(STATE_FILE, _positions)
            tg.notify_partial_tp(sym, side, p, get_balance())
            return

    # ── Trailing tras cierre parcial ─────────────────
    if pos["partial_done"] and atr > 0:
        if side == "long":
            trail = p - atr * TRAIL_AFTER
            if trail > pos["sl"]:
                pos["sl"] = trail
                _save(STATE_FILE, _positions)
        else:
            trail = p + atr * TRAIL_AFTER
            if trail < pos["sl"]:
                pos["sl"] = trail
                _save(STATE_FILE, _positions)

    # ── Verificar SL / TP ─────────────────────────────
    sl_hit = (side == "long" and p <= pos["sl"]) or (side == "short" and p >= pos["sl"])
    tp_hit = (side == "long" and p >= tp)         or (side == "short" and p <= tp)

    if sl_hit:
        _execute_close(sym, pos, pos["sl"], "SL")
    elif tp_hit:
        _execute_close(sym, pos, tp, "TP")


def _execute_close(sym: str, pos: dict, exit_p: float, reason: str):
    side  = pos["side"]
    entry = pos["entry"]
    qty   = pos["qty"]

    if TRADE_MODE == "live":
        api.close_position(sym, side, qty)

    pct = (exit_p - entry) / entry if side == "long" else (entry - exit_p) / entry
    pnl = qty * entry * pct * LEVERAGE

    if pnl > 0:
        rm.record_win()
    else:
        rm.record_loss()
        _reentry_log[sym] = {
            "side": side,
            "time": datetime.now(timezone.utc).isoformat()
        }
        _save(REENTRY_FILE, _reentry_log)

    if TRADE_MODE != "live":
        trades = _load(PAPER_FILE, [])
        trades.append({
            "symbol":     sym,
            "side":       side,
            "entry":      entry,
            "exit":       exit_p,
            "pnl":        round(pnl, 6),
            "result":     "WIN" if pnl > 0 else "LOSS",
            "reason":     reason,
            "date":       datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "score":      pos.get("score", 0),
            "strategy":   pos.get("strategy", "?"),
            "open_time":  pos.get("open_time", ""),
            "close_time": datetime.now(timezone.utc).isoformat(),
        })
        _save(PAPER_FILE, trades)

    del _positions[sym]
    _save(STATE_FILE, _positions)
    log.info(f"[{sym}] CERRADO {reason} exit={exit_p:.6f} pnl=${pnl:+.4f}")
    tg.notify_close(sym, side, entry, exit_p, pnl, reason, get_balance())


def get_trade_history(limit: int = 50) -> list:
    trades = _load(PAPER_FILE, [])
    return trades[-limit:]


def get_summary() -> dict:
    trades = _load(PAPER_FILE, [])
    if not trades:
        return {}
    wins   = [t for t in trades if t.get("result") == "WIN"]
    losses = [t for t in trades if t.get("result") == "LOSS"]
    pnl    = sum(t.get("pnl", 0) for t in trades)
    gw     = sum(t.get("pnl", 0) for t in wins)
    gl     = abs(sum(t.get("pnl", 0) for t in losses))
    return {
        "total":   len(trades),
        "wins":    len(wins),
        "losses":  len(losses),
        "wr":      round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "pnl":     round(pnl, 4),
        "pf":      round(gw / gl, 2) if gl > 0 else 999,
    }
