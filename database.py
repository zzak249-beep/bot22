"""
database.py — Capa de persistencia SQLite
Tablas: trades, signals, params, learner_log
"""
import sqlite3
import logging
from datetime import datetime

log     = logging.getLogger("database")
DB_PATH = "bot_data.db"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT,
                side        TEXT,
                entry_price REAL,
                exit_price  REAL,
                qty         REAL,
                pnl         REAL,
                close_reason TEXT,
                entry_time  TEXT,
                exit_time   TEXT,
                balance_at_entry REAL,
                leverage    INTEGER,
                bb_sigma    REAL,
                bb_period   INTEGER,
                rsi_ob      REAL,
                score       INTEGER,
                trend_4h    TEXT,
                rsi_at_entry REAL
            );

            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT,
                symbol      TEXT,
                action      TEXT,
                entry       REAL,
                sl          REAL,
                tp          REAL,
                rsi         REAL,
                score       INTEGER,
                reason      TEXT,
                executed    INTEGER,
                trade_id    INTEGER
            );

            CREATE TABLE IF NOT EXISTS params (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT,
                bb_period   INTEGER,
                bb_sigma    REAL,
                rsi_ob      REAL,
                sl_atr      REAL,
                note        TEXT
            );

            CREATE TABLE IF NOT EXISTS learner_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT,
                win_rate    REAL,
                avg_win     REAL,
                avg_loss    REAL,
                total_pnl   REAL,
                changes     TEXT
            );
        """)
    log.info("Base de datos inicializada")


def open_trade(symbol, signal, qty, balance, leverage,
               bb_sigma, bb_period, rsi_ob):
    try:
        with _conn() as c:
            cur = c.execute(
                """INSERT INTO trades
                   (symbol, side, entry_price, qty, entry_time,
                    balance_at_entry, leverage, bb_sigma, bb_period, rsi_ob,
                    score, trend_4h, rsi_at_entry)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    symbol,
                    signal.get("action", "buy"),
                    signal.get("entry", 0),
                    qty,
                    datetime.now().isoformat(),
                    balance,
                    leverage,
                    bb_sigma,
                    bb_period,
                    rsi_ob,
                    signal.get("score", 0),
                    signal.get("trend_4h", ""),
                    signal.get("rsi", 0),
                )
            )
            return cur.lastrowid
    except Exception as e:
        log.error(f"open_trade error: {e}")
        return None


def close_trade(trade_id, exit_price, pnl, reason):
    if not trade_id:
        return
    try:
        with _conn() as c:
            c.execute(
                """UPDATE trades
                   SET exit_price=?, pnl=?, close_reason=?, exit_time=?
                   WHERE id=?""",
                (exit_price, pnl, reason, datetime.now().isoformat(), trade_id)
            )
    except Exception as e:
        log.error(f"close_trade error: {e}")


def log_signal(symbol, signal, executed=False, trade_id=None):
    try:
        with _conn() as c:
            c.execute(
                """INSERT INTO signals
                   (ts, symbol, action, entry, sl, tp, rsi, score, reason, executed, trade_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now().isoformat(),
                    symbol,
                    signal.get("action", ""),
                    signal.get("entry", 0),
                    signal.get("sl", 0),
                    signal.get("tp", 0),
                    signal.get("rsi", 0),
                    signal.get("score", 0),
                    signal.get("reason", ""),
                    1 if executed else 0,
                    trade_id,
                )
            )
    except Exception as e:
        log.error(f"log_signal error: {e}")


def log_params(bb_period, bb_sigma, rsi_ob, sl_atr, note=""):
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO params (ts,bb_period,bb_sigma,rsi_ob,sl_atr,note) VALUES (?,?,?,?,?,?)",
                (datetime.now().isoformat(), bb_period, bb_sigma, rsi_ob, sl_atr, note)
            )
    except Exception as e:
        log.error(f"log_params error: {e}")


def get_stats_summary():
    """Devuelve estadísticas globales de todos los trades cerrados."""
    try:
        with _conn() as c:
            row = c.execute("""
                SELECT
                    COUNT(*)                          AS total,
                    SUM(CASE WHEN pnl >= 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN pnl <  0 THEN 1 ELSE 0 END) AS losses,
                    COALESCE(SUM(pnl), 0)             AS total_pnl
                FROM trades
                WHERE exit_time IS NOT NULL
            """).fetchone()
            if row:
                return dict(row)
    except Exception as e:
        log.error(f"get_stats_summary error: {e}")
    return {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
