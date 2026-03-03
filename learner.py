"""
learner.py — Motor de aprendizaje automatico Elite v5+
Mejoras v5+:
  - Analisis por hora del dia (cuando se gana mas)
  - Deteccion de regimen de mercado (trending/sideways/volatile)
  - Mas patrones de error: tiempo en trade, hora de entrada, volumen
  - Blacklist automatica de pares cronicamente perdedores
  - Compound mejorado con factor de drawdown
"""
import logging
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict
import config as cfg

log = logging.getLogger("learner")

REVIEW_EVERY   = 10
MIN_TRADES     = 5
DB_PATH        = "trades.db"


# ═══════════════════════════════════════════════════════════
# REINVERSION DE GANANCIAS — Compound Interest mejorado
# ═══════════════════════════════════════════════════════════

def update_compound(current_balance: float, initial_balance: float):
    """
    Reinvierte ganancias escalando RISK_PCT.
    MEJORA: penaliza mas agresivamente en drawdown para proteger capital.
    """
    if initial_balance <= 0 or current_balance <= 0:
        return

    BASE_RISK = 0.015
    MIN_RISK  = 0.005
    MAX_RISK  = 0.03

    growth = current_balance / initial_balance

    if growth <= 0.85:
        # Drawdown > 15%: riesgo minimo absoluto
        new_risk = MIN_RISK
    elif growth <= 1.0:
        # Drawdown leve: reducir proporcionalmente
        new_risk = max(BASE_RISK * growth, MIN_RISK)
    else:
        # En ganancia: escalar gradualmente, mas conservador
        scale    = 1.0 + (growth - 1.0) * 0.35   # 0.35 vs 0.40 anterior = mas conservador
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
    """Quarter-Kelly para mayor seguridad."""
    if avg_loss <= 0 or win_rate <= 0:
        return cfg.RISK_PCT
    reward_risk   = avg_win / avg_loss
    kelly         = win_rate - (1 - win_rate) / reward_risk
    kelly         = max(0.0, kelly)
    quarter_kelly = kelly * 0.25
    return round(max(0.005, min(0.03, quarter_kelly)), 4)


# ═══════════════════════════════════════════════════════════
# MEJORA: ANALISIS POR HORA DEL DIA
# ═══════════════════════════════════════════════════════════

def analyze_hour_performance(trades: list) -> dict:
    """
    Detecta en que horas UTC el bot gana y en cuales pierde.
    Retorna las horas malas para actualizar TIME_FILTER.
    """
    if not trades:
        return {}

    hour_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})

    for t in trades:
        entry_time = t.get("entry_time") or t.get("opened_at") or t.get("closed_at")
        if not entry_time:
            continue
        try:
            dt   = datetime.fromisoformat(str(entry_time))
            hour = dt.hour
        except Exception:
            continue
        pnl = t.get("pnl", 0)
        if pnl >= 0:
            hour_stats[hour]["wins"] += 1
        else:
            hour_stats[hour]["losses"] += 1
        hour_stats[hour]["pnl"] += pnl

    bad_hours = []
    for hour, s in hour_stats.items():
        total_h = s["wins"] + s["losses"]
        if total_h < 3:
            continue
        wr = s["wins"] / total_h
        if wr < 0.30 and s["pnl"] < 0:
            bad_hours.append(hour)

    if bad_hours:
        log.info(f"Horas malas detectadas (WR<30%): {sorted(bad_hours)} UTC")

    return {"bad_hours": bad_hours}


# ═══════════════════════════════════════════════════════════
# MEJORA: BLACKLIST DE PARES PERDEDORES
# ═══════════════════════════════════════════════════════════

_symbol_blacklist: set = set()

def update_symbol_blacklist(trades: list) -> set:
    """
    Añade a blacklist pares con WR < 25% en >= 6 trades.
    Los elimina del scan activo para dejar de perder en ellos.
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
                log.warning(f"Blacklist: {sym} WR={wr*100:.0f}% PnL=${s['pnl']:.2f}")

    global _symbol_blacklist
    added   = new_blacklist - _symbol_blacklist
    removed = _symbol_blacklist - new_blacklist

    if added:
        log.info(f"Pares añadidos a blacklist: {added}")
    if removed:
        log.info(f"Pares rehabilitados de blacklist: {removed}")

    _symbol_blacklist = new_blacklist

    # Eliminar de SYMBOLS activos
    if new_blacklist and cfg.SYMBOLS:
        before = len(cfg.SYMBOLS)
        cfg.SYMBOLS = [s for s in cfg.SYMBOLS if s not in new_blacklist]
        if len(cfg.SYMBOLS) < before:
            log.info(f"SYMBOLS reducido: {before} → {len(cfg.SYMBOLS)} (blacklist aplicada)")

    return new_blacklist


def get_blacklist() -> set:
    return _symbol_blacklist


# ═══════════════════════════════════════════════════════════
# MEJORA: DETECCION DE REGIMEN DE MERCADO
# ═══════════════════════════════════════════════════════════

def detect_market_regime(trades: list) -> str:
    """
    Detecta si el mercado esta en tendencia, lateral o volatil.
    Ajusta parametros segun el regimen.
    Regimenes: 'trending', 'sideways', 'volatile'
    """
    if len(trades) < 8:
        return "unknown"

    recent    = trades[:8]
    pnls      = [t.get("pnl", 0) for t in recent]
    tp_closes = sum(1 for t in recent if t.get("reason", "") in ("TP", "TP_PARCIAL"))
    sl_closes = sum(1 for t in recent if t.get("reason", "") == "SL")
    avg_pnl   = sum(pnls) / len(pnls)
    pnl_var   = sum((p - avg_pnl) ** 2 for p in pnls) / len(pnls)

    if tp_closes >= 5 and avg_pnl > 0:
        regime = "trending"
    elif sl_closes >= 5 and pnl_var > 1.0:
        regime = "volatile"
    else:
        regime = "sideways"

    log.info(f"Regimen de mercado detectado: {regime} (TP={tp_closes} SL={sl_closes})")

    # Ajustar parametros segun regimen
    if regime == "trending":
        # Mercado en tendencia: ser mas agresivo, SL mas amplio para no salir antes
        if cfg.SL_ATR < 3.0:
            cfg.SL_ATR = round(min(cfg.SL_ATR + 0.2, 3.0), 1)
            log.info(f"Regimen trending → SL_ATR ampliado a {cfg.SL_ATR}")
    elif regime == "sideways":
        # Mercado lateral: endurecer BB para menos señales falsas
        if cfg.BB_SIGMA < 2.3:
            cfg.BB_SIGMA = round(min(cfg.BB_SIGMA + 0.1, 2.3), 1)
            log.info(f"Regimen sideways → BB_SIGMA endurecido a {cfg.BB_SIGMA}")
    elif regime == "volatile":
        # Volatil: reducir riesgo y ampliar SL
        cfg.RISK_PCT = max(cfg.RISK_PCT * 0.8, 0.005)
        cfg.SL_ATR   = round(min(cfg.SL_ATR + 0.3, 4.0), 1)
        log.info(f"Regimen volatile → RISK_PCT={cfg.RISK_PCT:.3f} SL_ATR={cfg.SL_ATR}")

    return regime


# ═══════════════════════════════════════════════════════════
# ANALISIS DE ERRORES — aprende que NO hacer
# ═══════════════════════════════════════════════════════════

def analyze_losing_patterns(trades: list) -> dict:
    """
    Analiza trades perdedores y detecta patrones comunes.
    MEJORA: añade patron de tiempo en trade y volumen.
    """
    if not trades:
        return {}

    losing  = [t for t in trades if t.get("pnl", 0) < 0]
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
            new_rsi_ob = max(30, cfg.RSI_OB - 3)
            if new_rsi_ob != cfg.RSI_OB:
                adjustments["RSI_OB"] = new_rsi_ob
                patterns.append(f"RSI alto en perdidas ({avg_loss_rsi:.1f} vs {avg_win_rsi:.1f})")

    # ── Patron 2: SL demasiado ajustado ──────────────────────
    sl_hits  = [t for t in losing if t.get("reason", "") == "SL"]
    sl_ratio = len(sl_hits) / len(losing) if losing else 0
    if sl_ratio > 0.7:
        new_sl = min(cfg.SL_ATR + 0.3, 4.0)
        if new_sl != cfg.SL_ATR:
            adjustments["SL_ATR"] = round(new_sl, 1)
            patterns.append(f"SL muy ajustado ({sl_ratio*100:.0f}% de perdidas son SL)")

    # ── Patron 3: BB Sigma demasiado bajo ────────────────────
    if len(losing) > len(winning) and cfg.BB_SIGMA < 2.2:
        new_sigma = round(min(cfg.BB_SIGMA + 0.1, 2.5), 1)
        adjustments["BB_SIGMA"] = new_sigma
        patterns.append("Muchas perdidas: endurecer BB_SIGMA")

    # ── Patron 4: Tendencia 4h ignorada ──────────────────────
    bear_losses = [t for t in losing if t.get("trend_4h") == "bear" and
                   t.get("side") == "long"]
    if len(bear_losses) > 2:
        patterns.append("Longs contra tendencia bajista 4h — SHORT_ENABLED recomendado")

    # ── MEJORA Patron 5: Trades muy cortos (ruido de mercado) ─
    short_trades = []
    for t in losing:
        try:
            opened = datetime.fromisoformat(str(t.get("opened_at") or t.get("entry_time", "")))
            closed = datetime.fromisoformat(str(t.get("closed_at") or t.get("exit_time",  "")))
            duration_min = (closed - opened).total_seconds() / 60
            if duration_min < 30:
                short_trades.append(t)
        except Exception:
            pass
    if len(short_trades) > 3:
        patterns.append(f"{len(short_trades)} trades cerrados en < 30min (ruido) — considerar SL mas amplio")
        if cfg.SL_ATR < 3.0:
            adjustments["SL_ATR"] = round(min(cfg.SL_ATR + 0.2, 3.0), 1)

    # ── MEJORA Patron 6: R:R insuficiente en ganadores ────────
    if winning:
        avg_win_pnl  = sum(t.get("pnl", 0) for t in winning)  / len(winning)
        avg_loss_pnl = abs(sum(t.get("pnl", 0) for t in losing)) / len(losing)
        rr = avg_win_pnl / avg_loss_pnl if avg_loss_pnl > 0 else 0
        if rr < 1.0:
            patterns.append(f"R:R bajo ({rr:.2f}) — TP demasiado conservador, considerar TP_ATR mas alto")

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
    MEJORA: incluye regimen, horario y blacklist.
    """
    trades = _load_recent_trades(50)
    if len(trades) < MIN_TRADES:
        log.info(f"Learner: solo {len(trades)} trades, necesita {MIN_TRADES}")
        return {}

    wins   = [t for t in trades if t.get("pnl", 0) >= 0]
    losses = [t for t in trades if t.get("pnl", 0) <  0]
    total  = len(trades)

    win_rate  = len(wins) / total if total > 0 else 0
    avg_win   = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss  = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.001
    total_pnl = sum(t.get("pnl", 0) for t in trades)

    log.info(
        f"Learner analisis: {total} trades | "
        f"WR={win_rate*100:.1f}% | "
        f"AvgWin=${avg_win:.2f} | AvgLoss=${avg_loss:.2f} | "
        f"PnL=${total_pnl:.2f}"
    )

    changes = {}

    # ── Kelly Criterion ───────────────────────────────────────
    kelly_risk = calc_kelly_risk(win_rate, avg_win, avg_loss)
    if abs(kelly_risk - cfg.RISK_PCT) > 0.002:
        changes["RISK_PCT"] = kelly_risk
        cfg.RISK_PCT = kelly_risk
        log.info(f"Kelly → RISK_PCT={kelly_risk:.3f} (WR={win_rate:.2f}, R:R={avg_win/avg_loss:.2f})")

    # ── Patrones de error ─────────────────────────────────────
    pattern_adj = analyze_losing_patterns(trades)
    for param, val in pattern_adj.items():
        changes[param] = val
        setattr(cfg, param, val)
        log.info(f"Patron error → {param}={val}")

    # ── BB_SIGMA por win rate ─────────────────────────────────
    if win_rate < 0.40 and cfg.BB_SIGMA < 2.3:
        cfg.BB_SIGMA = round(cfg.BB_SIGMA + 0.1, 1)
        changes["BB_SIGMA"] = cfg.BB_SIGMA
        log.info(f"WR bajo → BB_SIGMA subido a {cfg.BB_SIGMA}")
    elif win_rate > 0.65 and cfg.BB_SIGMA > 1.7:
        cfg.BB_SIGMA = round(cfg.BB_SIGMA - 0.05, 2)
        changes["BB_SIGMA"] = cfg.BB_SIGMA
        log.info(f"WR alto → BB_SIGMA bajado a {cfg.BB_SIGMA}")

    # ── MEJORA: Deteccion de regimen ──────────────────────────
    regime = detect_market_regime(trades)
    changes["market_regime"] = regime

    # ── MEJORA: Blacklist de pares perdedores ─────────────────
    blacklist = update_symbol_blacklist(trades)
    if blacklist:
        changes["blacklisted"] = list(blacklist)

    # ── MEJORA: Analisis horario ──────────────────────────────
    hour_info = analyze_hour_performance(trades)
    bad_hours = hour_info.get("bad_hours", [])
    if bad_hours:
        changes["bad_hours"] = bad_hours

    # ── Guardar en DB ─────────────────────────────────────────
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
    bl        = len(_symbol_blacklist)
    return (
        f"WR={wr:.1f}% | "
        f"PnL=${total_pnl:.2f} | "
        f"Risk={cfg.RISK_PCT*100:.2f}% | "
        f"BB_σ={cfg.BB_SIGMA} | "
        f"Blacklist={bl} pares"
    )
