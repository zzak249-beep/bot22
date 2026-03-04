"""
database.py — SQLite para BB+RSI ELITE v6
Almacena trades, señales y parámetros del bot.
"""
import sqlite3
import logging
from datetime import datetime

log     = logging.getLogger("database")
DB_PATH = "bot_data.db"


def init_db():
    """Crear tablas si no existen."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT,
            side         TEXT,
            entry_price  REAL,
            exit_price   REAL,
            qty          REAL,
            leverage     INTEGER,
            pnl          REAL,
            close_reason TEXT,
            entry_time   TEXT,
            exit_time    TEXT,
            rsi_at_entry REAL,
            score        INTEGER,
            trend_4h     TEXT,
            bb_sigma     REAL,
            bb_period    INTEGER,
            rsi_ob       REAL,
            sl_atr       REAL,
            balance_at_entry REAL
        );

        CREATE TABLE IF NOT EXISTS signals (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT,
            symbol     TEXT,
            action     TEXT,
            entry      REAL,
            sl         REAL,
            tp         REAL,
            rsi        REAL,
            score      INTEGER,
            reason     TEXT,
            executed   INTEGER,
            trade_id   INTEGER
        );

        CREATE TABLE IF NOT EXISTS params_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT,
            bb_period  INTEGER,
            bb_sigma   REAL,
            rsi_ob     REAL,
            sl_atr     REAL,
            note       TEXT
        );

        CREATE TABLE IF NOT EXISTS learner_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT,
            win_rate   REAL,
            avg_win    REAL,
            avg_loss   REAL,
            total_pnl  REAL,
            changes    TEXT
        );
    """)
    conn.commit()
    conn.close()
    log.info("Base de datos inicializada.")


def open_trade(symbol, signal, qty, balance, leverage,
               bb_sigma=None, bb_period=None, rsi_ob=None):
    """Registrar apertura de trade. Retorna el ID."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.execute("""
            INSERT INTO trades
              (symbol, side, entry_price, qty, leverage,
               rsi_at_entry, score, trend_4h,
               bb_sigma, bb_period, rsi_ob, balance_at_entry,
               entry_time)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol,
            signal.get("action", "buy"),
            signal.get("entry", 0),
            qty,
            leverage,
            signal.get("rsi"),
            signal.get("score"),
            signal.get("trend_4h"),
            bb_sigma,
            bb_period,
            rsi_ob,
            balance,
            datetime.now().isoformat(),
        ))
        conn.commit()
        trade_id = cur.lastrowid
        conn.close()
        return trade_id
    except Exception as e:
        log.error(f"open_trade error: {e}")
        return None


def close_trade(trade_id, exit_price, pnl, reason):
    """Actualizar trade con datos de cierre."""
    if not trade_id:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            UPDATE trades
            SET exit_price=?, pnl=?, close_reason=?, exit_time=?
            WHERE id=?
        """, (exit_price, pnl, reason, datetime.now().isoformat(), trade_id))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"close_trade error: {e}")


def log_signal(symbol, signal, executed=False, trade_id=None):
    """Registrar cada señal generada."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO signals
              (ts, symbol, action, entry, sl, tp, rsi, score, reason, executed, trade_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now().isoformat(),
            symbol,
            signal.get("action"),
            signal.get("entry"),
            signal.get("sl"),
            signal.get("tp"),
            signal.get("rsi"),
            signal.get("score"),
            signal.get("reason"),
            1 if executed else 0,
            trade_id,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"log_signal error: {e}")


def log_params(bb_period, bb_sigma, rsi_ob, sl_atr, note=""):
    """Guardar snapshot de parámetros actuales."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO params_log (ts, bb_period, bb_sigma, rsi_ob, sl_atr, note)
            VALUES (?,?,?,?,?,?)
        """, (datetime.now().isoformat(), bb_period, bb_sigma, rsi_ob, sl_atr, note))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"log_params error: {e}")


def get_stats_summary():
    """
    Retorna estadísticas globales de todos los trades cerrados.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute("""
            SELECT
                COUNT(*)                              AS total,
                SUM(CASE WHEN pnl >= 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl <  0 THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(pnl), 0)                 AS total_pnl,
                COALESCE(AVG(CASE WHEN pnl >= 0 THEN pnl END), 0) AS avg_win,
                COALESCE(AVG(CASE WHEN pnl <  0 THEN pnl END), 0) AS avg_loss
            FROM trades
            WHERE exit_price IS NOT NULL
        """).fetchone()
        conn.close()
        if row:
            return {
                "total":     row[0] or 0,
                "wins":      row[1] or 0,
                "losses":    row[2] or 0,
                "total_pnl": row[3] or 0.0,
                "avg_win":   row[4] or 0.0,
                "avg_loss":  row[5] or 0.0,
            }
    except Exception as e:
        log.error(f"get_stats_summary error: {e}")
    return {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
