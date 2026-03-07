#!/usr/bin/env python3
"""
database.py — Persistencia SQLite
Guarda trades, posiciones y estadísticas
"""

import sqlite3
import time
import os

DB_FILE = "bot_trades.db"


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Inicializa tablas"""
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            lado TEXT NOT NULL,
            precio_entrada REAL,
            precio_salida REAL,
            cantidad REAL,
            sl REAL,
            tp REAL,
            pnl REAL DEFAULT 0,
            resultado TEXT DEFAULT 'ABIERTO',
            score INTEGER DEFAULT 0,
            atr REAL DEFAULT 0,
            rsi REAL DEFAULT 50,
            timestamp_entrada INTEGER,
            timestamp_salida INTEGER,
            duracion_min INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS posiciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE NOT NULL,
            lado TEXT NOT NULL,
            precio_entrada REAL,
            cantidad REAL,
            sl REAL,
            tp REAL,
            score INTEGER DEFAULT 0,
            trade_id INTEGER,
            timestamp INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS stats_diarias (
            fecha TEXT PRIMARY KEY,
            trades_total INTEGER DEFAULT 0,
            trades_win INTEGER DEFAULT 0,
            trades_loss INTEGER DEFAULT 0,
            pnl_total REAL DEFAULT 0,
            balance_inicio REAL DEFAULT 0,
            balance_fin REAL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()
    print("[DB] Base de datos inicializada")


def guardar_posicion(symbol: str, lado: str, precio: float, cantidad: float,
                     sl: float, tp: float, score: int = 0) -> int:
    conn = get_conn()
    c = conn.cursor()
    ts = int(time.time())

    # Insertar trade abierto
    c.execute("""
        INSERT INTO trades (symbol, lado, precio_entrada, cantidad, sl, tp, score, timestamp_entrada)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, lado, precio, cantidad, sl, tp, score, ts))
    trade_id = c.lastrowid

    # Guardar posición activa
    c.execute("""
        INSERT OR REPLACE INTO posiciones (symbol, lado, precio_entrada, cantidad, sl, tp, score, trade_id, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, lado, precio, cantidad, sl, tp, score, trade_id, ts))

    conn.commit()
    conn.close()
    return trade_id


def cerrar_posicion_db(symbol: str, precio_salida: float, pnl: float, resultado: str):
    conn = get_conn()
    c = conn.cursor()
    ts = int(time.time())

    # Obtener posición
    c.execute("SELECT * FROM posiciones WHERE symbol = ?", (symbol,))
    pos = c.fetchone()

    if pos:
        duracion = int((ts - pos["timestamp"]) / 60)

        # Actualizar trade
        c.execute("""
            UPDATE trades SET precio_salida=?, pnl=?, resultado=?, 
            timestamp_salida=?, duracion_min=?
            WHERE id=?
        """, (precio_salida, pnl, resultado, ts, duracion, pos["trade_id"]))

        # Eliminar posición activa
        c.execute("DELETE FROM posiciones WHERE symbol = ?", (symbol,))

        # Actualizar stats diarias
        from datetime import date
        hoy = str(date.today())
        c.execute("""
            INSERT INTO stats_diarias (fecha, trades_total, trades_win, trades_loss, pnl_total)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(fecha) DO UPDATE SET
                trades_total = trades_total + 1,
                trades_win = trades_win + ?,
                trades_loss = trades_loss + ?,
                pnl_total = pnl_total + ?
        """, (
            hoy,
            1 if resultado == "WIN" else 0,
            1 if resultado == "LOSS" else 0,
            pnl,
            1 if resultado == "WIN" else 0,
            1 if resultado == "LOSS" else 0,
            pnl
        ))

    conn.commit()
    conn.close()


def get_posiciones_activas() -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM posiciones")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_stats_hoy() -> dict:
    from datetime import date
    hoy = str(date.today())
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM stats_diarias WHERE fecha = ?", (hoy,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"trades_total": 0, "trades_win": 0, "trades_loss": 0, "pnl_total": 0.0}


def get_trades_recientes(limite: int = 20) -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM trades WHERE resultado != 'ABIERTO'
        ORDER BY timestamp_salida DESC LIMIT ?
    """, (limite,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_win_rate_par(symbol: str, min_trades: int = 5) -> float:
    """Win rate de un par específico"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN resultado='WIN' THEN 1 ELSE 0 END) as wins
        FROM trades WHERE symbol=? AND resultado != 'ABIERTO'
    """, (symbol,))
    row = c.fetchone()
    conn.close()
    if row and row["total"] >= min_trades:
        return row["wins"] / row["total"] * 100
    return -1.0


def get_pnl_total() -> float:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT SUM(pnl) as total FROM trades WHERE resultado != 'ABIERTO'")
    row = c.fetchone()
    conn.close()
    return float(row["total"] or 0)


def get_perdidas_consecutivas() -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT resultado FROM trades WHERE resultado != 'ABIERTO'
        ORDER BY timestamp_salida DESC LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()
    count = 0
    for r in rows:
        if r["resultado"] == "LOSS":
            count += 1
        else:
            break
    return count
