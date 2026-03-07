"""
risk_manager.py — Gestión de riesgo v14.0
"""
import logging
import time
from datetime import datetime, timezone

log = logging.getLogger("risk_manager")

try:
    import config as cfg
    RISK_PCT         = cfg.RISK_PCT
    LEVERAGE         = cfg.LEVERAGE
    MAX_CONCURRENT   = cfg.MAX_CONCURRENT_POS
    MAX_DAILY_LOSS   = getattr(cfg, "CB_MAX_DAILY_LOSS_PCT",   0.12)
    MAX_DD           = getattr(cfg, "MAX_DRAWDOWN_PCT",         0.20)
    CB_LOSS          = getattr(cfg, "CB_MAX_CONSECUTIVE_LOSS",  5)
    ATR_SIZING       = cfg.ATR_SIZING
    ATR_SIZING_BASE  = cfg.ATR_SIZING_BASE
except Exception:
    RISK_PCT = 0.015; LEVERAGE = 3; MAX_CONCURRENT = 3
    MAX_DAILY_LOSS = 0.12; MAX_DD = 0.20; CB_LOSS = 5
    ATR_SIZING = True; ATR_SIZING_BASE = 0.02

# Estado interno
_state = {
    "peak_balance":     0.0,
    "daily_start_bal":  0.0,
    "daily_pnl":        0.0,
    "consecutive_loss": 0,
    "wins":             0,
    "losses":           0,
    "today":            "",
    "cb_until":         0,     # timestamp hasta el que el CB está activo
    "paused":           False,
}


def reset_daily_if_needed(balance: float):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _state["today"] != today:
        _state["today"]            = today
        _state["daily_start_bal"]  = balance
        _state["daily_pnl"]        = 0.0
        _state["consecutive_loss"] = 0
        log.info(f"Reset diario — balance inicio: ${balance:.2f}")


def update_peak(balance: float):
    if balance > _state["peak_balance"]:
        _state["peak_balance"] = balance


def record_win():
    _state["wins"] += 1
    _state["consecutive_loss"] = 0


def record_loss():
    _state["losses"] += 1
    _state["consecutive_loss"] += 1


def check_circuit_breaker(balance: float) -> tuple[bool, str]:
    """Retorna (bloqueado, motivo)."""
    # CB temporal activo
    if _state["cb_until"] > time.time():
        remaining = int((_state["cb_until"] - time.time()) / 60)
        return True, f"CB activo — espera {remaining}min"

    # Drawdown desde pico
    peak = _state["peak_balance"]
    if peak > 0 and balance < peak * (1 - MAX_DD):
        dd_pct = (peak - balance) / peak * 100
        _activate_cb(f"Drawdown {dd_pct:.1f}% > {MAX_DD*100:.0f}%", hours=1)
        return True, f"Drawdown excesivo ({dd_pct:.1f}%)"

    # Pérdida diaria
    start = _state["daily_start_bal"]
    if start > 0 and (start - balance) / start > MAX_DAILY_LOSS:
        pct = (start - balance) / start * 100
        _activate_cb(f"Pérdida diaria {pct:.1f}%", hours=3)
        return True, f"Pérdida diaria {pct:.1f}%"

    # Pérdidas consecutivas
    if _state["consecutive_loss"] >= CB_LOSS:
        _activate_cb(f"{CB_LOSS} pérdidas consecutivas", hours=1)
        return True, f"{CB_LOSS} pérdidas consecutivas"

    return False, ""


def _activate_cb(reason: str, hours: float = 1):
    _state["cb_until"] = time.time() + hours * 3600
    log.warning(f"Circuit breaker activado: {reason} — pausa {hours}h")


def is_manually_paused() -> bool:
    return _state["paused"]


def can_open_position(open_count: int, balance: float) -> tuple[bool, str]:
    if open_count >= MAX_CONCURRENT:
        return False, f"Máx. posiciones ({MAX_CONCURRENT}) alcanzado"
    if balance < 5.0:
        return False, f"Balance ${balance:.2f} < $5 mínimo"
    return True, ""


def calc_position_size(balance: float, price: float, sl: float, atr: float = 0) -> float:
    """
    Sizing dinámico: arriesgar RISK_PCT del balance por trade.
    Con ATR sizing: ajusta el tamaño según la volatilidad.
    """
    if price <= 0 or sl <= 0 or balance <= 0:
        return 0.0

    risk_usd  = balance * RISK_PCT
    sl_dist   = abs(price - sl)

    if sl_dist <= 0:
        return 0.0

    # Contratos = riesgo_usd / (distancia_SL × leverage)
    # El leverage amplifica tanto ganancia como pérdida
    contracts = (risk_usd * LEVERAGE) / sl_dist

    # ATR sizing: si ATR es más grande que el promedio, reducir tamaño
    if ATR_SIZING and atr > 0 and price > 0:
        atr_pct = atr / price
        if atr_pct > ATR_SIZING_BASE * 2:
            # Volatilidad alta → reducir tamaño
            contracts *= ATR_SIZING_BASE / atr_pct

    # Redondear a 4 decimales
    contracts = round(contracts, 4)

    # Sanity check: no más del 20% del balance en valor nocional
    max_notional = balance * 0.20 * LEVERAGE
    if contracts * price > max_notional:
        contracts = round(max_notional / price, 4)

    return max(contracts, 0.0001)


def get_stats(balance: float) -> dict:
    w = _state["wins"]
    l = _state["losses"]
    return {
        "wins":        w,
        "losses":      l,
        "wr":          round(w / (w + l) * 100, 1) if (w + l) > 0 else 0,
        "consecutive": _state["consecutive_loss"],
        "peak":        _state["peak_balance"],
        "drawdown_pct": round((1 - balance / _state["peak_balance"]) * 100, 1)
                        if _state["peak_balance"] > 0 else 0,
    }
