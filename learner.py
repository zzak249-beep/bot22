"""
learner.py — Motor de aprendizaje v6 ELITE
Mejoras v6:
  - Analisis por hora del dia (detecta horas ganadoras/perdedoras)
  - Deteccion de regimen de mercado (trending/sideways/volatile)
  - Blacklist automatica de pares cronicamente perdedores
  - Patron 5: trades muy cortos (ruido de mercado)
  - Patron 6: R:R insuficiente en ganadores
  - Compound mejorado con penalizacion en drawdown severo
"""
import logging
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict
import config as cfg

log = logging.getLogger("learner")

REVIEW_EVERY = 10
MIN_TRADES   = 5
DB_PATH      = "trades.db"

_symbol_blacklist: set = set()


# ═══════════════════════════════════════════════════════════
# COMPOUND — reinversion de ganancias
# ═══════════════════════════════════════════════════════════

def update_compound(current_balance: float, initial_balance: float):
    """
    Escala RISK_PCT segun crecimiento del balance.
    Penaliza mas agresivamente en drawdown > 15%.
    """
    if initial_balance <= 0 or current_balance <= 0:
        return

    BASE_RISK = 0.015
    MIN_RISK  = 0.005
    MAX_RISK  = 0.03
    growth    = current_balance / initial_balance

    if growth <= 0.85:
        new_risk = MIN_RISK                             # drawdown > 15%: minimo absoluto
    elif growth <= 1.0:
        new_risk = max(BASE_RISK * growth, MIN_RISK)   # drawdown leve: reducir
    else:
        scale    = 1.0 + (growth - 1.0) * 0.35
        new_risk = min(BASE_RISK * scale, MAX_RISK)    # ganancia: escalar conservador

    if abs(new_risk - cfg.RISK_PCT) > 0.001:
        old = cfg.RISK_PCT
        cfg.RISK_PCT = round(new_risk, 4)
        log.info(f"Compound: ${current_balance:.2f} (x{growth:.2f}) → RISK {old:.3f}→{cfg.RISK_PCT:.3f}")


# ═══════════════════════════════════════════════════════════
# KELLY CRITERION
# ═══════════════════════════════════════════════════════════

def calc_kelly_risk(win_rate: float, avg_win: float, avg_loss: float) -> float:
    if avg_loss <= 0 or win_rate <= 0:
        return cfg.RISK_PCT
    rr            = avg_win / avg_loss
    kelly         = win_rate - (1 - win_rate) / rr
    quarter_kelly = max(0.0, kelly) * 0.25
    return round(max(0.005, min(0.03, quarter_kelly)), 4)


# ═══════════════════════════════════════════════════════════
# ANALISIS POR HORA DEL DIA
# ═══════════════════════════════════════════════════════════

def analyze_hour_performance(trades: list) -> dict:
    """
    Detecta en que horas UTC el bot gana y pierde sistematicamente.
    """
    hour_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
    for t in trades:
        ts = t.get("entry_time") or t.get("opened_at") or t.get("closed_at")
        if not ts:
            continue
        try:
            hour = datetime.fromisoformat(str(ts)).hour
        except Exception:
            continue
        pnl = t.get("pnl", 0)
        if pnl >= 0:
            hour_stats[hour]["wins"] += 1
        else:
            hour_stats[hour]["losses"] += 1
        hour_stats[hour]["pnl"] += pnl

    bad_hours  = []
    good_hours = []
    for hour, s in hour_stats.items():
        total = s["wins"] + s["losses"]
        if total < 3:
            continue
        wr = s["wins"] / total
        if wr < 0.30 and s["pnl"] < 0:
            bad_hours.append(hour)
        elif wr > 0.60 and s["pnl"] > 0:
            good_hours.append(hour)

    if bad_hours:
        log.info(f"Horas malas detectadas (WR<30%): {sorted(bad_hours)} UTC")
    if good_hours:
        log.info(f"Horas buenas detectadas (WR>60%): {sorted(good_hours)} UTC")

    return {"bad_hours": bad_hours, "good_hours": good_hours}


# ═══════════════════════════════════════════════════════════
# BLACKLIST DE PARES PERDEDORES
# ═══════════════════════════════════════════════════════════

def update_symbol_blacklist(trades: list) -> set:
    """
    Añade a blacklist pares con WR < 25% en >= 6 trades.
    Los rehabilita si mejoran.
    """
    sym_stats = defaultdict(lambda: {"wins": 0, "total": 0, "pnl": 0.0})
    for t in trades:
        sym = t.get("symbol", "")
        if not sym:
            continue
        sym_stats[sym]["total"] += 1
        if t.get("pnl", 0) >= 0:
            sym_stats[sym]["wins"] += 1
        sym_stats[sym]["pnl"] += t.get("pnl", 0)

    new_blacklist = set()
    for sym, s in sym_stats.items():
        if s["total"] >= 6:
            wr = s["wins"] / s["total"]
            if wr < 0.25 and s["pnl"] < -2.0:
                new_blacklist.add(sym)

    global _symbol_blacklist
    added   = new_blacklist - _symbol_blacklist
    removed = _symbol_blacklist - new_blacklist
    if added:
        log.warning(f"Blacklist añadidos: {added}")
    if removed:
        log.info(f"Blacklist rehabilitados: {removed}")

    _symbol_blacklist = new_blacklist

    if new_blacklist and cfg.SYMBOLS:
        before       = len(cfg.SYMBOLS)
        cfg.SYMBOLS  = [s for s in cfg.SYMBOLS if s not in new_blacklist]
        if len(cfg.SYMBOLS) < before:
            log.info(f"SYMBOLS: {before} → {len(cfg.SYMBOLS)} (blacklist)")

    return new_blacklist


def get_blacklist() -> set:
    return _symbol_blacklist


# ═══════════════════════════════════════════════════════════
# DETECCION DE REGIMEN DE MERCADO
# ═══════════════════════════════════════════════════════════

def detect_market_regime(trades: list) -> str:
    """
    Detecta regimen: 'trending', 'sideways', 'volatile'.
    Ajusta parametros segun regimen.
    """
    if len(trades) < 8:
        return "unknown"

    recent    = trades[:8]
    pnls      = [t.get("pnl", 0) for t in recent]
    tp_closes = sum(1 for t in recent if t.get("reason", "") in ("TP", "TP_PARCIAL", "TP1", "TP2", "TP3"))
    sl_closes = sum(1 for t in recent if t.get("reason", "") == "SL")
    avg_pnl   = sum(pnls) / len(pnls)
    pnl_var   = sum((p - avg_pnl) ** 2 for p in pnls) / len(pnls)

    if tp_closes >= 5 and avg_pnl > 0:
        regime = "trending"
    elif sl_closes >= 5 and pnl_var > 1.0:
        regime = "volatile"
    else:
        regime = "sideways"

    log.info(f"Regimen: {regime} | TP={tp_closes} SL={sl_closes} AvgPnL=${avg_pnl:.2f}")

    if regime == "trending" and cfg.SL_ATR < 3.0:
        cfg.SL_ATR = round(min(cfg.SL_ATR + 0.2, 3.0), 1)
        log.info(f"Trending → SL_ATR ampliado a {cfg.SL_ATR}")
    elif regime == "sideways" and cfg.BB_SIGMA < 2.3:
        cfg.BB_SIGMA = round(min(cfg.BB_SIGMA + 0.1, 2.3), 1)
        log.info(f"Sideways → BB_SIGMA endurecido a {cfg.BB_SIGMA}")
    elif regime == "volatile":
        cfg.RISK_PCT = max(cfg.RISK_PCT * 0.8, 0.005)
        cfg.SL_ATR   = round(min(cfg.SL_ATR + 0.3, 4.0), 1)
        log.info(f"Volatile → RISK={cfg.RISK_PCT:.3f} SL_ATR={cfg.SL_ATR}")

    return regime


# ═══════════════════════════════════════════════════════════
# PATRONES DE ERROR
# ═══════════════════════════════════════════════════════════

def analyze_losing_patterns(trades: list) -> dict:
    if not trades:
        return {}

    losing  = [t for t in trades if t.get("pnl", 0) < 0]
    winning = [t for t in trades if t.get("pnl", 0) > 0]

    if not losing:
        return {}

    adjustments = {}
    patterns    = []

    # Patron 1: RSI alto en perdidas
    l_rsi = [t.get("rsi_entry", 50) for t in losing  if t.get("rsi_entry")]
    w_rsi = [t.get("rsi_entry", 50) for t in winning if t.get("rsi_entry")]
    if l_rsi and w_rsi:
        avg_lr = sum(l_rsi) / len(l_rsi)
        avg_wr = sum(w_rsi) / len(w_rsi)
        if avg_lr > avg_wr + 5:
            new_ob = max(30, cfg.RSI_OB - 3)
            if new_ob != cfg.RSI_OB:
                adjustments["RSI_OB"] = new_ob
                patterns.append(f"RSI alto en perdidas ({avg_lr:.1f} vs {avg_wr:.1f})")

    # Patron 2: SL muy ajustado
    sl_hits  = [t for t in losing if t.get("reason", "") == "SL"]
    sl_ratio = len(sl_hits) / len(losing) if losing else 0
    if sl_ratio > 0.7:
        new_sl = min(cfg.SL_ATR + 0.3, 4.0)
        if new_sl != cfg.SL_ATR:
            adjustments["SL_ATR"] = round(new_sl, 1)
            patterns.append(f"SL muy ajustado ({sl_ratio*100:.0f}% son SL)")

    # Patron 3: BB Sigma bajo
    if len(losing) > len(winning) and cfg.BB_SIGMA < 2.2:
        new_sigma = round(min(cfg.BB_SIGMA + 0.1, 2.5), 1)
        adjustments["BB_SIGMA"] = new_sigma
        patterns.append("Muchas perdidas: endurecer BB_SIGMA")

    # Patron 4: Longs contra tendencia 4h
    bear_losses = [t for t in losing if t.get("trend_4h") == "bear" and t.get("side") == "long"]
    if len(bear_losses) > 2:
        patterns.append("Longs contra tendencia bajista 4h")

    # Patron 5: Trades cerrados en menos de 30 min (ruido)
    short_trades = []
    for t in losing:
        try:
            opened = datetime.fromisoformat(str(t.get("opened_at") or t.get("entry_time", "")))
            closed = datetime.fromisoformat(str(t.get("closed_at") or t.get("exit_time",  "")))
            if (closed - opened).total_seconds() / 60 < 30:
                short_trades.append(t)
        except Exception:
            pass
    if len(short_trades) > 3:
        patterns.append(f"{len(short_trades)} trades < 30min (ruido)")
        if cfg.SL_ATR < 3.0:
            adjustments["SL_ATR"] = round(min(cfg.SL_ATR + 0.2, 3.0), 1)

    # Patron 6: R:R bajo en ganadores
    if winning:
        avg_win_pnl  = sum(t.get("pnl", 0) for t in winning) / len(winning)
        avg_loss_pnl = abs(sum(t.get("pnl", 0) for t in losing)) / len(losing)
        rr = avg_win_pnl / avg_loss_pnl if avg_loss_pnl > 0 else 0
        if rr < 1.0:
            patterns.append(f"R:R bajo ({rr:.2f}) — TP demasiado conservador")

    if patterns:
        log.info(f"Patrones detectados: {' | '.join(patterns)}")

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
        rows = conn.execute(
            "SELECT * FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"Error cargando trades: {e}")
        return []


def analyze_and_adjust() -> dict:
    trades = _load_recent_trades(50)
    if len(trades) < MIN_TRADES:
        log.info(f"Learner: {len(trades)} trades, necesita {MIN_TRADES}")
        return {}

    wins      = [t for t in trades if t.get("pnl", 0) >= 0]
    losses    = [t for t in trades if t.get("pnl", 0) <  0]
    total     = len(trades)
    win_rate  = len(wins) / total if total > 0 else 0
    avg_win   = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss  = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.001
    total_pnl = sum(t.get("pnl", 0) for t in trades)

    log.info(
        f"Learner: {total} trades | WR={win_rate*100:.1f}% | "
        f"AvgW=${avg_win:.2f} AvgL=${avg_loss:.2f} | PnL=${total_pnl:.2f}"
    )

    changes = {}

    # Kelly
    kelly_risk = calc_kelly_risk(win_rate, avg_win, avg_loss)
    if abs(kelly_risk - cfg.RISK_PCT) > 0.002:
        changes["RISK_PCT"] = kelly_risk
        cfg.RISK_PCT = kelly_risk
        log.info(f"Kelly → RISK_PCT={kelly_risk:.3f}")

    # Patrones
    for param, val in analyze_losing_patterns(trades).items():
        changes[param] = val
        setattr(cfg, param, val)

    # BB_SIGMA por WR
    if win_rate < 0.40 and cfg.BB_SIGMA < 2.3:
        cfg.BB_SIGMA = round(cfg.BB_SIGMA + 0.1, 1)
        changes["BB_SIGMA"] = cfg.BB_SIGMA
        log.info(f"WR bajo → BB_SIGMA={cfg.BB_SIGMA}")
    elif win_rate > 0.65 and cfg.BB_SIGMA > 1.7:
        cfg.BB_SIGMA = round(cfg.BB_SIGMA - 0.05, 2)
        changes["BB_SIGMA"] = cfg.BB_SIGMA
        log.info(f"WR alto → BB_SIGMA={cfg.BB_SIGMA}")

    # Regimen de mercado
    regime = detect_market_regime(trades)
    changes["market_regime"] = regime

    # Blacklist
    blacklist = update_symbol_blacklist(trades)
    if blacklist:
        changes["blacklisted"] = list(blacklist)

    # Analisis horario
    hour_info = analyze_hour_performance(trades)
    if hour_info.get("bad_hours"):
        changes["bad_hours"] = hour_info["bad_hours"]

    # Guardar en DB
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
            (datetime.now().isoformat(), win_rate, avg_win, avg_loss,
             total_pnl, json.dumps(changes))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Error guardando learner log: {e}")

    return changes


def get_performance_report() -> str:
    trades = _load_recent_trades(20)
    if not trades:
        return "Sin datos"
    wins      = sum(1 for t in trades if t.get("pnl", 0) >= 0)
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    wr        = wins / len(trades) * 100
    return (
        f"WR={wr:.1f}% | PnL=${total_pnl:.2f} | "
        f"Risk={cfg.RISK_PCT*100:.2f}% | BB_σ={cfg.BB_SIGMA} | "
        f"Blacklist={len(_symbol_blacklist)}"
    )
