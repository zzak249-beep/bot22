"""
learner.py — Motor de aprendizaje automatico Elite v5
Analiza cada trade cerrado y ajusta parametros en tiempo real.
Aprende de errores: detecta patrones de perdida y los evita.
Reinvierte ganancias: escala el riesgo segun el crecimiento del balance.
"""
import logging
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict
import config as cfg

log = logging.getLogger("learner")

REVIEW_EVERY   = 10   # revisar cada N trades
MIN_TRADES     = 5    # minimo de trades para ajustar
DB_PATH        = "trades.db"


# ═══════════════════════════════════════════════════════════
# REINVERSION DE GANANCIAS — Compound Interest
# ═══════════════════════════════════════════════════════════

def update_compound(current_balance: float, initial_balance: float):
    """
    Reinvierte ganancias automaticamente escalando RISK_PCT.
    Cuanto mayor es el balance vs el inicial, mas arriesga.
    Nunca supera MAX_RISK ni baja de MIN_RISK.

    Escala:
      balance x1.0 → riesgo base (1.5%)
      balance x1.5 → riesgo x1.2 (1.8%)
      balance x2.0 → riesgo x1.4 (2.1%)
      balance x3.0 → riesgo x1.6 (2.4%)
    """
    if initial_balance <= 0 or current_balance <= 0:
        return

    BASE_RISK = 0.015
    MIN_RISK  = 0.005
    MAX_RISK  = 0.03

    growth = current_balance / initial_balance
    if growth <= 1.0:
        # En drawdown: reducir riesgo
        new_risk = max(BASE_RISK * growth, MIN_RISK)
    else:
        # En ganancia: escalar riesgo gradualmente
        scale    = 1.0 + (growth - 1.0) * 0.4
        new_risk = min(BASE_RISK * scale, MAX_RISK)

    old_risk = cfg.RISK_PCT
    if abs(new_risk - old_risk) > 0.001:
        cfg.RISK_PCT = round(new_risk, 4)
        log.info(
            f"Compound: balance ${current_balance:.2f} "
            f"(x{growth:.2f}) → RISK_PCT {old_risk:.3f} → {cfg.RISK_PCT:.3f}"
        )


# ═══════════════════════════════════════════════════════════
# KELLY CRITERION — position sizing optimo
# ═══════════════════════════════════════════════════════════

def calc_kelly_risk(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Calcula el riesgo optimo por trade usando Kelly Criterion.
    Usa Quarter-Kelly (25%) para mayor seguridad.
    """
    if avg_loss <= 0 or win_rate <= 0:
        return cfg.RISK_PCT
    reward_risk = avg_win / avg_loss
    kelly = win_rate - (1 - win_rate) / reward_risk
    kelly = max(0.0, kelly)
    quarter_kelly = kelly * 0.25
    # Clamp entre 0.5% y 3%
    return round(max(0.005, min(0.03, quarter_kelly)), 4)


# ═══════════════════════════════════════════════════════════
# ANALISIS DE ERRORES — aprende que NO hacer
# ═══════════════════════════════════════════════════════════

def analyze_losing_patterns(trades: list) -> dict:
    """
    Analiza trades perdedores y detecta patrones comunes.
    Retorna ajustes recomendados.
    """
    if not trades:
        return {}

    losing = [t for t in trades if t.get("pnl", 0) < 0]
    winning = [t for t in trades if t.get("pnl", 0) > 0]

    if not losing:
        return {}

    adjustments = {}
    patterns    = []

    # ── Patron 1: RSI demasiado alto en entradas perdedoras ──
    losing_rsi  = [t.get("rsi_entry", 50) for t in losing  if t.get("rsi_entry")]
    winning_rsi = [t.get("rsi_entry", 50) for t in winning if t.get("rsi_entry")]
    if losing_rsi and winning_rsi:
        avg_loss_rsi = sum(losing_rsi)  / len(losing_rsi)
        avg_win_rsi  = sum(winning_rsi) / len(winning_rsi)
        if avg_loss_rsi > avg_win_rsi + 5:
            # Las perdidas entran con RSI mas alto → endurecer filtro
            new_rsi_ob = max(30, cfg.RSI_OB - 3)
            if new_rsi_ob != cfg.RSI_OB:
                adjustments["RSI_OB"] = new_rsi_ob
                patterns.append(f"RSI alto en perdidas ({avg_loss_rsi:.1f} vs {avg_win_rsi:.1f})")

    # ── Patron 2: SL demasiado ajustado ──────────────────────
    sl_hits  = [t for t in losing if t.get("reason", "") == "SL"]
    tp_hits  = [t for t in winning if t.get("reason", "") in ("TP", "TP_PARCIAL")]
    sl_ratio = len(sl_hits) / len(losing) if losing else 0
    if sl_ratio > 0.7:
        # Mas del 70% de perdidas son SL → ampliar stop
        new_sl = min(cfg.SL_ATR + 0.3, 4.0)
        if new_sl != cfg.SL_ATR:
            adjustments["SL_ATR"] = round(new_sl, 1)
            patterns.append(f"SL muy ajustado ({sl_ratio*100:.0f}% de perdidas son SL)")

    # ── Patron 3: BB Sigma demasiado bajo ────────────────────
    if len(losing) > len(winning) and cfg.BB_SIGMA < 2.2:
        new_sigma = round(min(cfg.BB_SIGMA + 0.1, 2.5), 1)
        adjustments["BB_SIGMA"] = new_sigma
        patterns.append("Muchas perdidas: endurecer BB_SIGMA")

    # ── Patron 4: Tendencia 4h ignorada en perdidas ──────────
    bear_losses = [t for t in losing if t.get("trend_4h") == "bear" and
                   t.get("side") == "long"]
    if len(bear_losses) > 2:
        patterns.append("Longs contra tendencia bajista 4h — SHORT_ENABLED recomendado")

    if patterns:
        log.info(f"Patrones de error detectados: {' | '.join(patterns)}")

    return adjustments


# ═══════════════════════════════════════════════════════════
# ANALISIS Y AJUSTE PRINCIPAL
# ═══════════════════════════════════════════════════════════

def should_review(trades_closed: int) -> bool:
    return trades_closed >= REVIEW_EVERY and trades_closed % REVIEW_EVERY == 0


def _load_recent_trades(limit=50) -> list:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()
        cur.execute("""
            SELECT * FROM trades
            WHERE closed_at IS NOT NULL
            ORDER BY closed_at DESC
            LIMIT ?
        """, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log.error(f"Error cargando trades: {e}")
        return []


def analyze_and_adjust() -> dict:
    """
    Analiza los ultimos trades y ajusta parametros automaticamente.
    Retorna dict con los cambios realizados.
    """
    trades = _load_recent_trades(50)
    if len(trades) < MIN_TRADES:
        log.info(f"Learner: solo {len(trades)} trades, necesita {MIN_TRADES}")
        return {}

    wins   = [t for t in trades if t.get("pnl", 0) >= 0]
    losses = [t for t in trades if t.get("pnl", 0) <  0]
    total  = len(trades)

    win_rate = len(wins) / total if total > 0 else 0
    avg_win  = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.001
    total_pnl = sum(t.get("pnl", 0) for t in trades)

    log.info(
        f"Learner analisis: {total} trades | "
        f"WR={win_rate*100:.1f}% | "
        f"AvgWin=${avg_win:.2f} | AvgLoss=${avg_loss:.2f} | "
        f"PnL=${total_pnl:.2f}"
    )

    changes = {}

    # ── Ajuste por Kelly Criterion ────────────────────────────
    kelly_risk = calc_kelly_risk(win_rate, avg_win, avg_loss)
    if abs(kelly_risk - cfg.RISK_PCT) > 0.002:
        changes["RISK_PCT"] = kelly_risk
        cfg.RISK_PCT = kelly_risk
        log.info(f"Kelly → RISK_PCT={kelly_risk:.3f} (WR={win_rate:.2f}, R:R={avg_win/avg_loss:.2f})")

    # ── Ajuste por patrones de error ──────────────────────────
    pattern_adj = analyze_losing_patterns(trades)
    for param, val in pattern_adj.items():
        changes[param] = val
        setattr(cfg, param, val)
        log.info(f"Patron error → {param}={val}")

    # ── Ajuste automatico de BB_SIGMA por win rate ────────────
    if win_rate < 0.40 and cfg.BB_SIGMA < 2.3:
        cfg.BB_SIGMA = round(cfg.BB_SIGMA + 0.1, 1)
        changes["BB_SIGMA"] = cfg.BB_SIGMA
        log.info(f"WR bajo → BB_SIGMA subido a {cfg.BB_SIGMA}")
    elif win_rate > 0.65 and cfg.BB_SIGMA > 1.7:
        cfg.BB_SIGMA = round(cfg.BB_SIGMA - 0.05, 2)
        changes["BB_SIGMA"] = cfg.BB_SIGMA
        log.info(f"WR alto → BB_SIGMA bajado a {cfg.BB_SIGMA}")

    # ── Guardar estado del learner ────────────────────────────
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learner_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, win_rate REAL, avg_win REAL, avg_loss REAL,
                total_pnl REAL, changes TEXT
            )
        """)
        conn.execute(
            "INSERT INTO learner_log VALUES (NULL,?,?,?,?,?,?)",
            (datetime.now().isoformat(), win_rate, avg_win,
             avg_loss, total_pnl, json.dumps(changes))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Error guardando learner log: {e}")

    return changes


def get_performance_report() -> str:
    trades = _load_recent_trades(20)
    if not trades:
        return "Sin datos suficientes"
    wins      = sum(1 for t in trades if t.get("pnl", 0) >= 0)
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    wr        = wins / len(trades) * 100
    return (
        f"WR={wr:.1f}% | "
        f"PnL=${total_pnl:.2f} | "
        f"Risk={cfg.RISK_PCT*100:.2f}% | "
        f"BB_σ={cfg.BB_SIGMA}"
    )