"""
database.py — Almacenamiento persistente de todas las operaciones.

Tablas:
  trades    — cada operacion completa (entrada + salida + resultado)
  signals   — cada señal generada (compra, salida, hold)
  params    — historial de parametros del bot (para ver evolucion)
  daily     — resumen diario
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("database")
DB_PATH = Path("bot_data.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crea las tablas si no existen."""
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS trades (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol       TEXT    NOT NULL,
        entry_time   TEXT    NOT NULL,
        exit_time    TEXT,
        entry_price  REAL    NOT NULL,
        exit_price   REAL,
        sl           REAL,
        tp           REAL,
        qty          REAL,
        pnl          REAL,
        pnl_pct      REAL,
        result       TEXT,          -- WIN / LOSS / OPEN
        close_reason TEXT,          -- TP / SL / SIGNAL / MANUAL
        leverage     INTEGER,
        balance_at_entry REAL,
        -- condiciones de mercado al entrar
        rsi_at_entry   REAL,
        atr_at_entry   REAL,
        bb_sigma_used  REAL,
        bb_period_used INTEGER,
        rsi_ob_used    REAL,
        -- para analisis posterior
        notes        TEXT
    );

    CREATE TABLE IF NOT EXISTS signals (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT NOT NULL,
        symbol     TEXT NOT NULL,
        action     TEXT NOT NULL,   -- buy / exit / hold
        price      REAL,
        rsi        REAL,
        bb_lower   REAL,
        bb_basis   REAL,
        reason     TEXT,
        executed   INTEGER DEFAULT 0,   -- 1=ejecutado en exchange, 0=manual/ignorado
        trade_id   INTEGER REFERENCES trades(id)
    );

    CREATE TABLE IF NOT EXISTS params_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT NOT NULL,
        bb_period  INTEGER,
        bb_sigma   REAL,
        rsi_ob     REAL,
        sl_atr     REAL,
        reason     TEXT    -- por que se cambio
    );

    CREATE TABLE IF NOT EXISTS daily_summary (
        date       TEXT PRIMARY KEY,
        trades     INTEGER DEFAULT 0,
        wins       INTEGER DEFAULT 0,
        losses     INTEGER DEFAULT 0,
        pnl        REAL    DEFAULT 0,
        win_rate   REAL    DEFAULT 0,
        best_pair  TEXT,
        worst_pair TEXT,
        balance_end REAL
    );

    CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
    CREATE INDEX IF NOT EXISTS idx_trades_result ON trades(result);
    CREATE INDEX IF NOT EXISTS idx_signals_ts    ON signals(ts);
    """)
    conn.commit()
    conn.close()
    log.info(f"Base de datos lista: {DB_PATH.resolve()}")


# ═══════════════════════════════════════════════════════════
#  TRADES
# ═══════════════════════════════════════════════════════════

def open_trade(symbol: str, signal: dict, qty: float, balance: float,
               leverage: int, bb_sigma: float, bb_period: int, rsi_ob: float) -> int:
    """Registra apertura de trade. Retorna el ID."""
    conn = get_conn()
    cur  = conn.execute("""
        INSERT INTO trades
          (symbol, entry_time, entry_price, sl, tp, qty, result,
           balance_at_entry, rsi_at_entry, atr_at_entry,
           bb_sigma_used, bb_period_used, rsi_ob_used, leverage)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        symbol,
        datetime.now().isoformat(),
        signal["entry"],
        signal["sl"],
        signal["tp"],
        qty,
        "OPEN",
        balance,
        signal.get("rsi"),
        signal.get("atr"),
        bb_sigma,
        bb_period,
        rsi_ob,
        leverage,
    ))
    conn.commit()
    trade_id = cur.lastrowid
    conn.close()
    log.debug(f"Trade abierto ID={trade_id} {symbol}")
    return trade_id


def close_trade(trade_id: int, exit_price: float, pnl: float, reason: str):
    """Registra cierre de trade."""
    conn = get_conn()
    row  = conn.execute("SELECT entry_price FROM trades WHERE id=?", (trade_id,)).fetchone()
    pnl_pct = 0.0
    if row:
        entry = row["entry_price"]
        pnl_pct = round((exit_price - entry) / entry * 100, 3) if entry else 0

    result = "WIN" if pnl >= 0 else "LOSS"
    conn.execute("""
        UPDATE trades SET
            exit_time    = ?,
            exit_price   = ?,
            pnl          = ?,
            pnl_pct      = ?,
            result       = ?,
            close_reason = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), exit_price, round(pnl, 4),
          pnl_pct, result, reason, trade_id))
    conn.commit()
    conn.close()
    log.debug(f"Trade cerrado ID={trade_id} resultado={result} pnl={pnl:+.2f}")

    _update_daily(pnl, result)


def _update_daily(pnl: float, result: str):
    today = datetime.now().date().isoformat()
    conn  = get_conn()
    conn.execute("""
        INSERT INTO daily_summary (date, trades, wins, losses, pnl)
        VALUES (?, 1, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            trades = trades + 1,
            wins   = wins   + excluded.wins,
            losses = losses + excluded.losses,
            pnl    = pnl    + excluded.pnl
    """, (today, 1 if result == "WIN" else 0, 1 if result == "LOSS" else 0, pnl))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
#  SEÑALES
# ═══════════════════════════════════════════════════════════

def log_signal(symbol: str, signal: dict, executed: bool = False, trade_id: int = None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO signals
          (ts, symbol, action, price, rsi, bb_lower, bb_basis, reason, executed, trade_id)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(),
        symbol,
        signal.get("action"),
        signal.get("entry"),
        signal.get("rsi"),
        signal.get("bb_lower"),
        signal.get("bb_basis"),
        signal.get("reason"),
        1 if executed else 0,
        trade_id,
    ))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
#  HISTORIAL DE PARAMETROS
# ═══════════════════════════════════════════════════════════

def log_params(bb_period: int, bb_sigma: float, rsi_ob: float, sl_atr: float, reason: str):
    conn = get_conn()
    conn.execute("""
        INSERT INTO params_history (ts, bb_period, bb_sigma, rsi_ob, sl_atr, reason)
        VALUES (?,?,?,?,?,?)
    """, (datetime.now().isoformat(), bb_period, bb_sigma, rsi_ob, sl_atr, reason))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
#  CONSULTAS PARA EL LEARNER
# ═══════════════════════════════════════════════════════════

def get_recent_trades(n: int = 30) -> list[dict]:
    conn   = get_conn()
    rows   = conn.execute("""
        SELECT * FROM trades
        WHERE result IN ('WIN','LOSS')
        ORDER BY exit_time DESC
        LIMIT ?
    """, (n,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trades_by_symbol(symbol: str, n: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM trades
        WHERE symbol=? AND result IN ('WIN','LOSS')
        ORDER BY exit_time DESC LIMIT ?
    """, (symbol, n)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_win_rate_last_n(n: int = 20) -> float:
    trades = get_recent_trades(n)
    if not trades:
        return 0.5
    wins = sum(1 for t in trades if t["result"] == "WIN")
    return wins / len(trades)


def get_all_trades_df():
    """Retorna todos los trades cerrados como DataFrame (para analisis)."""
    import pandas as pd
    conn = get_conn()
    df   = pd.read_sql("SELECT * FROM trades WHERE result IN ('WIN','LOSS')", conn)
    conn.close()
    return df


def get_stats_summary() -> dict:
    conn = get_conn()
    row  = conn.execute("""
        SELECT
            COUNT(*)                                     AS total,
            SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
            ROUND(SUM(pnl), 2)                           AS total_pnl,
            ROUND(AVG(CASE WHEN result='WIN'  THEN pnl END), 2) AS avg_win,
            ROUND(AVG(CASE WHEN result='LOSS' THEN pnl END), 2) AS avg_loss,
            ROUND(AVG(rsi_at_entry), 1)                  AS avg_rsi_entry,
            MIN(entry_time)                              AS first_trade,
            MAX(entry_time)                              AS last_trade
        FROM trades WHERE result IN ('WIN','LOSS')
    """).fetchone()
    conn.close()
    return dict(row) if row else {}
