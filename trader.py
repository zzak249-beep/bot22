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
# trader.py v13.2 — FIXES balance=0 + ejecución
#
# Bugs corregidos:
#   ① REENTRY_COOL_DOWN → REENTRY_COOLDOWN (NameError)
#   ② order success: code==0 en vez de "error" not in str
#   ③ balance=0 en paper mode: usa INITIAL_BAL como fallback
#   ④ balance=0 en live mode: diagnóstico claro por consola y Telegram
#   ⑤ re-fetch balance justo antes de abrir (más preciso)
# ══════════════════════════════════════════════════════

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
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

_positions:   dict = _load(STATE_FILE, {})
_reentry_log: dict = _load(REENTRY_FILE, {})


def get_positions() -> dict:
    return _positions


def get_balance() -> float:
    """
    Nunca devuelve negativo.
    Live:  API BingX cuenta Futures/Swap
    Paper: INITIAL_BAL + PnL acumulado (min INITIAL_BAL)
    """
    if TRADE_MODE == "live":
        try:
            b = api.get_balance()
            if b <= 0:
                print("[TRADER] ⚠️  Balance Futures = $0.00")
                print("[TRADER]    Posibles causas:")
                print("[TRADER]    1) Fondos en cuenta Spot, no en Perpetual Futures")
                print("[TRADER]       → BingX: Assets → Transfer → Spot to Futures")
                print("[TRADER]    2) API key sin permiso Trade (código 100004)")
                print("[TRADER]       → BingX: API Mgmt → crear nueva con Read+Trade")
                print("[TRADER]    3) TRADE_MODE=live pero quieres paper → cambia a paper")
            rm.reset_daily_if_needed(b)
            rm.update_peak(b)
            return max(b, 0.0)
        except Exception as e:
            print(f"[TRADER] Error balance API: {e}")
            return 0.0
    else:
        # Paper: nunca bajar de INITIAL_BAL aunque haya pérdidas
        trades  = _load(PAPER_FILE, [])
        pnl_sum = sum(t.get("pnl", 0) for t in trades)
        bal     = INITIAL_BAL + pnl_sum
        if bal <= 0:
            print(f"[TRADER] ℹ️  Paper balance caería a ${bal:.2f} → forzando INITIAL_BAL=${INITIAL_BAL}")
            return INITIAL_BAL
        return bal


def _can_reentry(sym: str, side: str) -> bool:
    if not REENTRY_ENABLED:
        return False
    log = _reentry_log.get(sym)
    if not log or log.get("side") != side:
        return False
    try:
        last = datetime.fromisoformat(log["time"])
        hours = (datetime.now(timezone.utc) - last.replace(tzinfo=timezone.utc)).total_seconds() / 3600
        return hours >= REENTRY_COOLDOWN   # FIX ①
    except Exception:
        return False

def _record_sl_for_reentry(sym: str, side: str):
    _reentry_log[sym] = {"side": side, "time": datetime.now(timezone.utc).isoformat()}
    _save(REENTRY_FILE, _reentry_log)


def open_trade(sym: str, signal: dict, balance: float) -> bool:
    if sym in _positions:
        return False

    # FIX ⑤: re-fetch balance real justo antes de abrir
    balance = get_balance()

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

    # FIX ④: diagnostico claro cuando balance=0
    if balance <= 0:
        print(f"  [{sym}] ❌ balance=$0 — trade bloqueado")
        if TRADE_MODE == "live":
            tg.notify_error(
                f"❌ SEÑAL {side.upper()} {sym} bloqueada\n"
                f"Balance Futures = $0.00\n"
                f"→ Transfiere fondos: BingX Assets → Spot to Perpetual\n"
                f"→ O verifica permisos API (necesitas Read + Trade)"
            )
        else:
            tg.notify_error(
                f"❌ Paper balance=$0 en {sym}\n"
                f"Revisa INITIAL_BAL en Railway Variables (ej: 100)"
            )
        return False

    qty = rm.calc_position_size(balance, price, sl, atr)
    if qty <= 0:
        print(f"  [{sym}] qty={qty} inválida")
        return False

    executed = False
    if TRADE_MODE == "live":
        api.set_leverage(sym, LEVERAGE)
        result = api.open_order(sym, side, qty, sl, tp)
        # FIX ②: code=0 significa éxito en BingX
        if result.get("code", -1) == 0:
            executed = True
            oid = (result.get("data") or {}).get("order", {}).get("orderId", "?")
            print(f"  [{sym}] ✅ Orden abierta en BingX — orderId={oid}")
        else:
            code = result.get("code", "?")
            msg  = result.get("msg", "desconocido")
            print(f"  [{sym}] ❌ Error BingX code={code}: {msg}")
            if code == 100004:
                print(f"  [{sym}]    → API key SIN permiso Trade")
            elif code == 100001:
                print(f"  [{sym}]    → Firma incorrecta — verifica BINGX_API_SECRET")
            elif code == 80014:
                print(f"  [{sym}]    → Fondos insuficientes en Futures")
            tg.notify_error(f"Error {sym} code={code}: {msg}")
            return False
    else:
        executed = True

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
            "liq_bias":     signal.get("liq_bias", "neutral"),
            "liq_score":    signal.get("liq_score", 50),
            "reentry":      signal.get("reentry", False),
            "open_time":    datetime.now(timezone.utc).isoformat(),
            "atr":          atr,
        }
        _save(STATE_FILE, _positions)

    new_bal = get_balance()
    tg.notify_signal(sym, side, signal["score"], signal["rsi"],
                     price, sl, tp, signal["trend"], executed, new_bal,
                     bias_4h=signal.get("bias_4h", "?"))
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

    if TRAIL_FROM_START and not pos["partial_done"] and atr > 0:
        mult = TRAIL_ATR_MULT_INIT
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

    if not pos["partial_done"]:
        hit_partial = (side == "long"  and p >= tp_p) or \
                      (side == "short" and p <= tp_p)
        if hit_partial:
            if TRADE_MODE == "live":
                api.close_position(sym, side, qty * 0.5)
            pos["qty"]         *= 0.5
            pos["sl"]           = entry
            pos["partial_done"] = True
            _save(STATE_FILE, _positions)
            tg.notify_partial_tp(sym, side, p, get_balance())
            return

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

    sl_hit = (side == "long"  and p <= pos["sl"]) or \
             (side == "short" and p >= pos["sl"])
    tp_hit = (side == "long"  and p >= tp) or \
             (side == "short" and p <= tp)

    reason = None
    exit_p = p
    if sl_hit:
        exit_p = pos["sl"]
        reason = "SL"
    elif tp_hit:
        exit_p = tp
        reason = "TP"

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
            "symbol":     sym,
            "side":       side,
            "entry":      entry,
            "exit":       exit_p,
            "pnl":        round(pnl, 4),
            "result":     "WIN" if is_win else "LOSS",
            "reason":     reason,
            "date":       datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "score":      pos.get("score", 0),
            "rsi_e":      pos.get("rsi_e", 0),
            "trend":      pos.get("trend", "?"),
            "bias_4h":    pos.get("bias_4h", "?"),
            "liq_bias":   pos.get("liq_bias", "neutral"),
            "liq_score":  pos.get("liq_score", 50),
            "reentry":    pos.get("reentry", False),
            "open_time":  pos.get("open_time", ""),
            "close_time": datetime.now(timezone.utc).isoformat(),
        })
        _save(PAPER_FILE, trades)

    del _positions[sym]
    _save(STATE_FILE, _positions)
    tg.notify_close(sym, side, entry, exit_p, pnl, reason, get_balance())


def get_reentry_info(sym: str) -> dict | None:
    log = _reentry_log.get(sym)
    if not log or not REENTRY_ENABLED:
        return None
    try:
        last  = datetime.fromisoformat(log["time"].replace("Z", "+00:00"))
        now   = datetime.now(timezone.utc)
        hours = (now - last).total_seconds() / 3600
        if hours >= REENTRY_COOLDOWN:
            return log
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
    wins      = [t for t in trades if t["result"] == "WIN"]
    losses    = [t for t in trades if t["result"] == "LOSS"]
    total_pnl = sum(t["pnl"] for t in trades)
    gw        = sum(t["pnl"] for t in wins)
    gl        = abs(sum(t["pnl"] for t in losses))
    return {
        "total":   len(trades),
        "wins":    len(wins),
        "losses":  len(losses),
        "wr":      round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "pnl":     round(total_pnl, 4),
        "pf":      round(gw / gl, 2) if gl > 0 else 999,
    }
