"""
database.py — Memoria persistente del bot v6
Nuevos campos: parcial_cerrado, trailing_activado, sl_original
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "bot22.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    # Tabla principal de trades
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            par               TEXT NOT NULL,
            lado              TEXT NOT NULL DEFAULT 'LONG',
            precio_entrada    REAL,
            precio_salida     REAL,
            cantidad          REAL,
            cantidad_inicial  REAL,
            pnl_usd           REAL,
            pnl_pct           REAL,
            pnl_parcial_usd   REAL DEFAULT 0,
            rsi_entrada       REAL,
            bb_posicion       REAL,
            atr_entrada       REAL,
            sl_precio         REAL,
            sl_original       REAL,
            tp_precio         REAL,
            resultado         TEXT,        -- WIN / LOSS / BE
            motivo_cierre     TEXT,        -- SL / TP / TRAILING / MANUAL
            parcial_cerrado   INTEGER DEFAULT 0,
            trailing_activado INTEGER DEFAULT 0,
            divergencia       INTEGER DEFAULT 0,
            vol_relativo      REAL DEFAULT 1.0,
            mtf_rsi           REAL DEFAULT 50.0,
            score_entrada     INTEGER DEFAULT 0,
            balance_antes     REAL,
            balance_despues   REAL,
            timestamp_entrada TEXT,
            timestamp_salida  TEXT,
            order_id_entrada  TEXT,
            order_id_salida   TEXT
        )
    """)

    # Métricas acumuladas por par (para el learner)
    c.execute("""
        CREATE TABLE IF NOT EXISTS metricas_par (
            par              TEXT PRIMARY KEY,
            total_trades     INTEGER DEFAULT 0,
            wins             INTEGER DEFAULT 0,
            losses           INTEGER DEFAULT 0,
            pnl_total        REAL DEFAULT 0,
            pf               REAL DEFAULT 0,
            wr               REAL DEFAULT 0,
            avg_score        REAL DEFAULT 0,
            ultimo_trade     TEXT,
            activo           INTEGER DEFAULT 1,
            penalizado_hasta TEXT
        )
    """)

    # Log de ajustes del learner
    c.execute("""
        CREATE TABLE IF NOT EXISTS learner_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT,
            par           TEXT,
            accion        TEXT,
            motivo        TEXT,
            valor_antes   TEXT,
            valor_despues TEXT
        )
    """)

    # Historial de balance para compound y drawdown
    c.execute("""
        CREATE TABLE IF NOT EXISTS balance_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            balance   REAL,
            equity    REAL,
            pnl_dia   REAL
        )
    """)

    # Intentar añadir columnas nuevas a tablas existentes (migración)
    columnas_nuevas = [
        ("trades", "cantidad_inicial",  "REAL"),
        ("trades", "pnl_parcial_usd",   "REAL DEFAULT 0"),
        ("trades", "sl_original",       "REAL"),
        ("trades", "parcial_cerrado",   "INTEGER DEFAULT 0"),
        ("trades", "trailing_activado", "INTEGER DEFAULT 0"),
        ("trades", "divergencia",       "INTEGER DEFAULT 0"),
        ("trades", "vol_relativo",      "REAL DEFAULT 1.0"),
        ("trades", "mtf_rsi",           "REAL DEFAULT 50.0"),
        ("trades", "score_entrada",     "INTEGER DEFAULT 0"),
        ("metricas_par", "avg_score",   "REAL DEFAULT 0"),
    ]
    for tabla, col, tipo in columnas_nuevas:
        try:
            c.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
        except sqlite3.OperationalError:
            pass  # Columna ya existe

    conn.commit()
    conn.close()
    print("[DB] Base de datos inicializada ✓")


def guardar_trade(data: dict):
    conn = get_conn()
    c = conn.cursor()

    campos = [
        "par", "lado", "precio_entrada", "precio_salida", "cantidad",
        "cantidad_inicial", "pnl_usd", "pnl_pct", "pnl_parcial_usd",
        "rsi_entrada", "bb_posicion", "atr_entrada",
        "sl_precio", "sl_original", "tp_precio",
        "resultado", "motivo_cierre",
        "parcial_cerrado", "trailing_activado", "divergencia",
        "vol_relativo", "mtf_rsi", "score_entrada",
        "balance_antes", "balance_despues",
        "timestamp_entrada", "timestamp_salida",
        "order_id_entrada", "order_id_salida"
    ]

    placeholders = ", ".join(f":{c}" for c in campos)
    cols_sql     = ", ".join(campos)

    # Valores por defecto para campos opcionales
    defaults = {
        "cantidad_inicial":  data.get("cantidad", 0),
        "pnl_parcial_usd":   0.0,
        "sl_original":       data.get("sl_precio", 0),
        "parcial_cerrado":   0,
        "trailing_activado": 0,
        "divergencia":       0,
        "vol_relativo":      1.0,
        "mtf_rsi":           50.0,
        "score_entrada":     0,
    }
    row = {**defaults, **data}

    c.execute(f"INSERT INTO trades ({cols_sql}) VALUES ({placeholders})", row)
    conn.commit()
    conn.close()
    _actualizar_metricas(data["par"])


def _actualizar_metricas(par: str):
    conn = get_conn()
    c = conn.cursor()

    rows = c.execute(
        "SELECT resultado, pnl_usd, score_entrada FROM trades WHERE par=? ORDER BY id DESC LIMIT 50",
        (par,)
    ).fetchall()

    if not rows:
        conn.close()
        return

    total    = len(rows)
    wins     = sum(1 for r in rows if r["resultado"] == "WIN")
    losses   = sum(1 for r in rows if r["resultado"] == "LOSS")
    pnl      = sum(r["pnl_usd"] for r in rows)
    scores   = [r["score_entrada"] for r in rows if r["score_entrada"]]
    avg_score = sum(scores) / len(scores) if scores else 0

    ganancias = sum(r["pnl_usd"] for r in rows if r["pnl_usd"] > 0)
    perdidas  = abs(sum(r["pnl_usd"] for r in rows if r["pnl_usd"] < 0))
    pf  = ganancias / perdidas if perdidas > 0 else 999.0
    wr  = (wins / total * 100) if total > 0 else 0

    c.execute("""
        INSERT INTO metricas_par (par, total_trades, wins, losses, pnl_total, pf, wr, avg_score, ultimo_trade)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(par) DO UPDATE SET
            total_trades=excluded.total_trades,
            wins=excluded.wins,
            losses=excluded.losses,
            pnl_total=excluded.pnl_total,
            pf=excluded.pf,
            wr=excluded.wr,
            avg_score=excluded.avg_score,
            ultimo_trade=excluded.ultimo_trade
    """, (par, total, wins, losses, pnl, pf, wr, avg_score, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def get_metricas_par(par: str) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM metricas_par WHERE par=?", (par,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_todos_pares_activos() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT par FROM metricas_par WHERE activo=1").fetchall()
    conn.close()
    return [r["par"] for r in rows]


def penalizar_par(par: str, hasta: str, motivo: str):
    conn = get_conn()
    conn.execute(
        "UPDATE metricas_par SET activo=0, penalizado_hasta=? WHERE par=?",
        (hasta, par)
    )
    conn.execute(
        "INSERT INTO learner_log (timestamp, par, accion, motivo) VALUES (?,?,?,?)",
        (datetime.now().isoformat(), par, "PENALIZAR", motivo)
    )
    conn.commit()
    conn.close()


def rehabilitar_par(par: str):
    conn = get_conn()
    conn.execute(
        "UPDATE metricas_par SET activo=1, penalizado_hasta=NULL WHERE par=?", (par,)
    )
    conn.execute(
        "INSERT INTO learner_log (timestamp, par, accion, motivo) VALUES (?,?,?,?)",
        (datetime.now().isoformat(), par, "REHABILITAR", "penalización expirada")
    )
    conn.commit()
    conn.close()


def guardar_balance(balance: float, equity: float, pnl_dia: float):
    conn = get_conn()
    conn.execute(
        "INSERT INTO balance_history (timestamp, balance, equity, pnl_dia) VALUES (?,?,?,?)",
        (datetime.now().isoformat(), balance, equity, pnl_dia)
    )
    conn.commit()
    conn.close()


def get_pnl_hoy() -> float:
    conn = get_conn()
    hoy = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT SUM(pnl_usd) + SUM(COALESCE(pnl_parcial_usd,0)) as total "
        "FROM trades WHERE timestamp_salida LIKE ?",
        (f"{hoy}%",)
    ).fetchone()
    conn.close()
    return row["total"] or 0.0


def get_ultimos_trades(n: int = 20) -> list:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_racha_perdidas_hoy() -> int:
    conn = get_conn()
    hoy = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT resultado FROM trades WHERE timestamp_salida LIKE ? ORDER BY id DESC",
        (f"{hoy}%",)
    ).fetchall()
    conn.close()

    racha = 0
    for r in rows:
        if r["resultado"] == "LOSS":
            racha += 1
        else:
            break
    return racha


def get_stats_resumen() -> dict:
    """Estadísticas globales para el reporte periódico."""
    conn = get_conn()
    hoy  = datetime.now().strftime("%Y-%m-%d")

    total  = conn.execute("SELECT COUNT(*) as n FROM trades").fetchone()["n"]
    wins   = conn.execute("SELECT COUNT(*) as n FROM trades WHERE resultado='WIN'").fetchone()["n"]
    losses = conn.execute("SELECT COUNT(*) as n FROM trades WHERE resultado='LOSS'").fetchone()["n"]
    pnl_t  = conn.execute("SELECT SUM(pnl_usd) as s FROM trades").fetchone()["s"] or 0
    pnl_h  = conn.execute(
        "SELECT SUM(pnl_usd)+SUM(COALESCE(pnl_parcial_usd,0)) as s FROM trades WHERE timestamp_salida LIKE ?",
        (f"{hoy}%",)
    ).fetchone()["s"] or 0

    conn.close()
    wr = (wins / total * 100) if total > 0 else 0
    return {
        "total_trades": total,
        "wins":         wins,
        "losses":       losses,
        "wr":           wr,
        "pnl_total":    pnl_t,
        "pnl_today":    pnl_h,
    }


if __name__ == "__main__":
    init_db()
    print("[DB] Tablas creadas correctamente")

# ══════════════════════════════════════════════════════════
# FUNCIONES ADICIONALES para main.py v6
# ══════════════════════════════════════════════════════════

def open_trade(symbol: str, signal: dict, qty: float, balance: float,
               leverage: int, bb_sigma: float, bb_period: int, rsi_ob: int) -> int:
    conn = get_conn()
    c    = conn.cursor()
    c.execute("""
        INSERT INTO trades (
            par, lado, precio_entrada, cantidad, cantidad_inicial,
            rsi_entrada, bb_posicion, atr_entrada, sl_precio, sl_original,
            tp_precio, score_entrada, vol_relativo, mtf_rsi,
            balance_antes, timestamp_entrada
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        symbol.replace("/", "-"),
        "LONG" if signal.get("action") == "buy" else "SHORT",
        signal.get("entry", 0),
        qty, qty,
        signal.get("rsi", 50),
        signal.get("bb_pos", 0.5),
        signal.get("atr", 0),
        signal.get("sl", 0),
        signal.get("sl", 0),
        signal.get("tp", 0),
        signal.get("score", 0),
        signal.get("vol_ratio", 1.0),
        signal.get("mtf_rsi", 50),
        balance,
        datetime.now().isoformat(),
    ))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def close_trade(trade_id: int, exit_price: float, pnl_usd: float, reason: str):
    conn = get_conn()
    resultado = "WIN" if pnl_usd >= 0 else "LOSS"
    conn.execute("""
        UPDATE trades SET precio_salida=?, pnl_usd=?, resultado=?,
        motivo_cierre=?, timestamp_salida=? WHERE id=?
    """, (exit_price, pnl_usd, resultado, reason,
          datetime.now().isoformat(), trade_id))
    conn.commit()
    conn.close()
    row = get_conn().execute("SELECT par FROM trades WHERE id=?", (trade_id,)).fetchone()
    if row:
        _actualizar_metricas(row["par"])


def log_signal(symbol: str, signal: dict, executed: bool = False, trade_id: int = None):
    import logging
    logging.getLogger("database").debug(
        f"SIGNAL {'EXEC' if executed else 'SKIP'}: {symbol} "
        f"action={signal.get('action')} score={signal.get('score',0)}"
    )


def get_stats_summary() -> dict:
    return get_stats_resumen()


def log_params(bb_period, bb_sigma, rsi_ob, sl_atr, note=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO learner_log (timestamp, par, accion, motivo, valor_despues) VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(), "SYSTEM", "PARAMS", note,
         f"BB={bb_period}/{bb_sigma} RSI_OB={rsi_ob} SL={sl_atr}")
    )
    conn.commit()
    conn.close()
