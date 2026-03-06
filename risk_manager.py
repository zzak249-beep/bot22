import json, os
from datetime import datetime, date, timezone
from config import (ATR_SIZING, ATR_SIZING_BASE, CIRCUIT_BREAKER_LOSS,
                    RISK_PCT, LEVERAGE, INITIAL_BAL)

# ══════════════════════════════════════════════════════
# risk_manager.py v13.1
# Circuit breaker DESACTIVADO — bot nunca se para solo
# Solo se pausa con /pause manual desde Telegram
# ══════════════════════════════════════════════════════

_STATE_FILE = "risk_state.json"

def _load() -> dict:
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "peak_balance":       INITIAL_BAL,
        "daily_start_bal":    INITIAL_BAL,
        "daily_date":         str(date.today()),
        "consecutive_losses": 0,
        "paused":             False,
        "pause_reason":       "",
    }

def _save(s: dict):
    with open(_STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)

_state = _load()

def reset_daily_if_needed(balance: float):
    global _state
    today = str(date.today())
    if _state.get("daily_date") != today:
        _state["daily_date"]      = today
        _state["daily_start_bal"] = balance
        _save(_state)

def update_peak(balance: float):
    global _state
    if balance > _state["peak_balance"]:
        _state["peak_balance"] = balance
        _save(_state)

def record_loss():
    global _state
    _state["consecutive_losses"] += 1
    _save(_state)

def record_win():
    global _state
    _state["consecutive_losses"] = 0
    _save(_state)

def check_circuit_breaker(balance: float) -> tuple:
    """
    Circuit breaker DESACTIVADO.
    El bot nunca se para automáticamente.
    Solo se pausa con /pause desde Telegram.
    """
    global _state
    reset_daily_if_needed(balance)
    update_peak(balance)
    return False, ""   # ← siempre permite operar

def is_manually_paused() -> bool:
    return _state.get("paused", False)

def pause(reason: str = "manual"):
    global _state
    _state["paused"]       = True
    _state["pause_reason"] = reason
    _save(_state)

def resume():
    global _state
    _state["paused"]       = False
    _state["pause_reason"] = ""
    _save(_state)

def get_state() -> dict:
    return dict(_state)

def can_open_position(open_count: int, balance: float) -> tuple:
    from config import MAX_CONCURRENT_POS
    if _state.get("paused"):
        return False, f"Pausado: {_state.get('pause_reason', 'manual')}"
    if open_count >= MAX_CONCURRENT_POS:
        return False, f"Máximo {MAX_CONCURRENT_POS} posiciones alcanzado"
    return True, ""

def calc_position_size(balance: float, price: float, sl: float, atr: float) -> float:
    if not ATR_SIZING or atr <= 0 or price <= 0:
        risk = RISK_PCT
    else:
        atr_pct = atr / price
        ref_pct = 0.02
        ratio   = ref_pct / atr_pct if atr_pct > 0 else 1.0
        ratio   = max(0.5, min(2.0, ratio))
        risk    = ATR_SIZING_BASE * ratio
    if _state["consecutive_losses"] >= CIRCUIT_BREAKER_LOSS:
        risk *= 0.5
    return (balance * risk * LEVERAGE) / price

def get_stats(balance: float) -> dict:
    reset_daily_if_needed(balance)
    peak = _state["peak_balance"]
    ds   = _state["daily_start_bal"]
    return {
        "peak_balance":       round(peak, 2),
        "drawdown_pct":       round((peak - balance) / peak * 100, 2) if peak > 0 else 0,
        "daily_pnl":          round(balance - ds, 4),
        "daily_pnl_pct":      round((balance - ds) / ds * 100, 2) if ds > 0 else 0,
        "consecutive_losses": _state["consecutive_losses"],
        "paused":             _state.get("paused", False),
        "pause_reason":       _state.get("pause_reason", ""),
    }
