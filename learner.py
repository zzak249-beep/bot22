"""
learner.py — Aprendizaje automatico basado en historial de trades.

Logica:
  Despues de cada N trades analiza los resultados y ajusta
  BB_SIGMA, RSI_OB y SL_ATR para maximizar el win rate futuro.

  Reglas de ajuste:
  ┌─ Win rate < 45%  → endurecer filtros (subir RSI_OB, subir BB_SIGMA)
  ├─ Win rate > 70%  → relajar filtros para mas señales
  ├─ Muchos SL       → ampliar stop loss (subir SL_ATR)
  ├─ Perdidas en RSI alto (>55) → bajar umbral RSI
  └─ Ganancias en RSI bajo (<40) → confirmar ese rango
"""

import logging
from dataclasses import dataclass
from typing import Optional
import database as db
import config as cfg

log = logging.getLogger("learner")

# Cada cuantos trades cerrados hacer una revision
REVIEW_EVERY  = 10
# Limites de los parametros (no salirse de estos rangos)
LIMITS = {
    "bb_sigma": (1.4, 2.5),
    "rsi_ob":   (45.0, 75.0),
    "sl_atr":   (1.0, 3.5),
}

@dataclass
class ParamUpdate:
    param:    str
    old_val:  float
    new_val:  float
    reason:   str


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def analyze_and_adjust() -> list[ParamUpdate]:
    """
    Analiza los ultimos trades y propone ajustes de parametros.
    Retorna lista de cambios aplicados.
    """
    trades = db.get_recent_trades(30)
    if len(trades) < REVIEW_EVERY:
        log.debug(f"Learner: solo {len(trades)} trades, esperando {REVIEW_EVERY} para revisar")
        return []

    updates: list[ParamUpdate] = []

    total  = len(trades)
    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    wr     = len(wins) / total

    log.info(f"Learner: revisando {total} trades | WR={wr*100:.1f}%")

    # ── Analisis de RSI en entrada ───────────────────────────
    rsi_wins   = [t["rsi_at_entry"] for t in wins   if t["rsi_at_entry"] is not None]
    rsi_losses = [t["rsi_at_entry"] for t in losses if t["rsi_at_entry"] is not None]

    avg_rsi_win  = sum(rsi_wins)   / len(rsi_wins)   if rsi_wins   else cfg.RSI_OB
    avg_rsi_loss = sum(rsi_losses) / len(rsi_losses) if rsi_losses else cfg.RSI_OB

    # ── Ajuste 1: Win Rate bajo → endurecer ─────────────────
    if wr < 0.45:
        new_sigma = _clamp(cfg.BB_SIGMA + 0.1, *LIMITS["bb_sigma"])
        new_rsi   = _clamp(cfg.RSI_OB   - 3.0, *LIMITS["rsi_ob"])
        reason    = f"WR bajo ({wr*100:.1f}%) — endureciendo filtros"

        if new_sigma != cfg.BB_SIGMA:
            updates.append(ParamUpdate("bb_sigma", cfg.BB_SIGMA, new_sigma,
                                        reason + f" | BB_SIGMA {cfg.BB_SIGMA}→{new_sigma}"))
            cfg.BB_SIGMA = new_sigma

        if new_rsi != cfg.RSI_OB:
            updates.append(ParamUpdate("rsi_ob", cfg.RSI_OB, new_rsi,
                                        reason + f" | RSI_OB {cfg.RSI_OB}→{new_rsi}"))
            cfg.RSI_OB = new_rsi

    # ── Ajuste 2: Win Rate alto → relajar para mas señales ───
    elif wr > 0.72 and total >= 20:
        new_sigma = _clamp(cfg.BB_SIGMA - 0.05, *LIMITS["bb_sigma"])
        new_rsi   = _clamp(cfg.RSI_OB   + 2.0,  *LIMITS["rsi_ob"])
        reason    = f"WR excelente ({wr*100:.1f}%) — relajando para mas señales"

        if new_sigma != cfg.BB_SIGMA:
            updates.append(ParamUpdate("bb_sigma", cfg.BB_SIGMA, new_sigma, reason))
            cfg.BB_SIGMA = new_sigma

        if new_rsi != cfg.RSI_OB:
            updates.append(ParamUpdate("rsi_ob", cfg.RSI_OB, new_rsi, reason))
            cfg.RSI_OB = new_rsi

    # ── Ajuste 3: RSI medio de perdidas > RSI medio de ganancias ─
    if rsi_losses and rsi_wins and avg_rsi_loss > avg_rsi_win + 5:
        # Las perdidas ocurren con RSI mas alto → bajar umbral
        new_rsi = _clamp(cfg.RSI_OB - 2.0, *LIMITS["rsi_ob"])
        reason  = (f"Perdidas con RSI alto (avg={avg_rsi_loss:.1f}) "
                   f"vs ganancias (avg={avg_rsi_win:.1f}) → bajando RSI_OB")
        if new_rsi != cfg.RSI_OB:
            updates.append(ParamUpdate("rsi_ob", cfg.RSI_OB, new_rsi, reason))
            cfg.RSI_OB = new_rsi

    # ── Ajuste 4: Muchos SL → ampliar stop ───────────────────
    sl_hits  = [t for t in losses if t.get("close_reason") == "SL"]
    sl_ratio = len(sl_hits) / max(len(losses), 1)
    if sl_ratio > 0.85 and cfg.SL_ATR < LIMITS["sl_atr"][1]:
        new_sl = _clamp(cfg.SL_ATR + 0.2, *LIMITS["sl_atr"])
        reason = f"{sl_ratio*100:.0f}% de perdidas son SL → ampliando SL_ATR"
        updates.append(ParamUpdate("sl_atr", cfg.SL_ATR, new_sl, reason))
        cfg.SL_ATR = new_sl

    # ── Guardar cambios en DB ─────────────────────────────────
    if updates:
        db.log_params(cfg.BB_PERIOD, cfg.BB_SIGMA, cfg.RSI_OB, cfg.SL_ATR,
                      " | ".join(u.reason for u in updates))
        for u in updates:
            log.info(f"Learner: {u.param} {u.old_val} → {u.new_val} | {u.reason}")
    else:
        log.info("Learner: sin cambios necesarios")

    return updates


def should_review(trades_since_last: int) -> bool:
    return trades_since_last >= REVIEW_EVERY


def get_performance_report() -> dict:
    """Resumen de rendimiento para Telegram."""
    stats    = db.get_stats_summary()
    trades   = db.get_recent_trades(50)
    by_sym: dict[str, dict] = {}

    for t in trades:
        s = t["symbol"]
        if s not in by_sym:
            by_sym[s] = {"wins": 0, "losses": 0, "pnl": 0}
        if t["result"] == "WIN":
            by_sym[s]["wins"] += 1
        else:
            by_sym[s]["losses"] += 1
        by_sym[s]["pnl"] += t.get("pnl") or 0

    best_sym  = max(by_sym, key=lambda s: by_sym[s]["pnl"], default="N/A") if by_sym else "N/A"
    worst_sym = min(by_sym, key=lambda s: by_sym[s]["pnl"], default="N/A") if by_sym else "N/A"

    total = stats.get("total", 0)
    wins  = stats.get("wins",  0)
    wr    = (wins / total * 100) if total else 0

    avg_win  = stats.get("avg_win")  or 0
    avg_loss = stats.get("avg_loss") or 0
    exp      = (wr/100 * avg_win) + ((1 - wr/100) * avg_loss) if total else 0

    return {
        "total":         total,
        "wins":          wins,
        "losses":        stats.get("losses", 0),
        "win_rate":      round(wr, 1),
        "total_pnl":     stats.get("total_pnl", 0),
        "avg_win":       round(avg_win, 2),
        "avg_loss":      round(avg_loss, 2),
        "expectancy":    round(exp, 2),
        "best_symbol":   best_sym,
        "worst_symbol":  worst_sym,
        "by_symbol":     by_sym,
        "current_params": {
            "bb_sigma": cfg.BB_SIGMA,
            "rsi_ob":   cfg.RSI_OB,
            "sl_atr":   cfg.SL_ATR,
        }
    }
