"""
trade_history.py — Persistencia de trades en SQLite

Guarda cada trade cerrado con PnL, métricas y estadísticas acumuladas.
Los datos sobreviven reinicios del bot en Railway si usas un volumen montado.

Uso:
    from trade_history import TradeHistory
    db = TradeHistory()
    db.record_open(symbol, side, entry, sl, tp1, qty, score, rr)
    db.record_close(trade_id, exit_price, pnl_usdt, close_reason)
    stats = db.get_stats()
"""

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("trade_history")

# ── Ruta de la DB ──────────────────────────────────────────────────────────────
# Railway: monta un volumen en /data para persistencia real.
# Si no hay volumen, guarda en /tmp (se borra al reiniciar).
DB_PATH = Path("/data/trades.db") if Path("/data").exists() else Path("/tmp/trades.db")


class TradeHistory:

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
        log.info("TradeHistory inicializado en %s", self.db_path)

    # ── Init ───────────────────────────────────────────────────────────────────

    def _init_db(self):
        with self._conn() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol        TEXT    NOT NULL,
                    side          TEXT    NOT NULL,          -- LONG / SHORT
                    entry_price   REAL    NOT NULL,
                    sl_price      REAL    NOT NULL,
                    tp1_price     REAL    NOT NULL,
                    qty           REAL    NOT NULL,
                    score         INTEGER DEFAULT 0,
                    rr            REAL    DEFAULT 0,
                    open_ts       TEXT    NOT NULL,          -- ISO UTC
                    close_ts      TEXT,
                    exit_price    REAL,
                    pnl_usdt      REAL,                      -- + ganancia, - pérdida
                    pnl_pct       REAL,
                    close_reason  TEXT,                      -- TP1/TP2/TP3/SL/MANUAL
                    is_dry_run    INTEGER DEFAULT 0,         -- 1 = simulado
                    status        TEXT    DEFAULT 'OPEN'     -- OPEN / CLOSED
                );

                CREATE TABLE IF NOT EXISTS daily_summary (
                    date          TEXT PRIMARY KEY,          -- YYYY-MM-DD UTC
                    trades_closed INTEGER DEFAULT 0,
                    wins          INTEGER DEFAULT 0,
                    losses        INTEGER DEFAULT 0,
                    pnl_usdt      REAL    DEFAULT 0,
                    win_rate      REAL    DEFAULT 0,
                    balance_end   REAL
                );
            """)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    # ── Abrir trade ────────────────────────────────────────────────────────────

    def record_open(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        sl_price: float,
        tp1_price: float,
        qty: float,
        score: int = 0,
        rr: float = 0.0,
        is_dry_run: bool = False,
    ) -> int:
        """Registra apertura. Devuelve el ID del trade."""
        ts = datetime.now(timezone.utc).isoformat()
        with self._conn() as con:
            cur = con.execute("""
                INSERT INTO trades
                    (symbol, side, entry_price, sl_price, tp1_price,
                     qty, score, rr, open_ts, is_dry_run, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (symbol, side, entry_price, sl_price, tp1_price,
                  qty, score, rr, ts, int(is_dry_run), "OPEN"))
            trade_id = cur.lastrowid
        log.info("📂 Trade #%d abierto: %s %s @ %.6g", trade_id, side, symbol, entry_price)
        return trade_id

    # ── Cerrar trade ───────────────────────────────────────────────────────────

    def record_close(
        self,
        trade_id: int,
        exit_price: float,
        pnl_usdt: float,
        close_reason: str,       # "TP1", "TP2", "TP3", "SL", "MANUAL"
        balance_after: Optional[float] = None,
    ):
        """Registra cierre y actualiza resumen diario."""
        ts    = datetime.now(timezone.utc).isoformat()
        today = datetime.now(timezone.utc).date().isoformat()

        with self._conn() as con:
            # Obtener datos del trade para calcular pnl_pct
            row = con.execute(
                "SELECT entry_price, qty FROM trades WHERE id=?", (trade_id,)
            ).fetchone()

            pnl_pct = 0.0
            if row:
                entry, qty = row
                cost = entry * qty
                pnl_pct = (pnl_usdt / cost * 100) if cost > 0 else 0.0

            con.execute("""
                UPDATE trades
                SET close_ts=?, exit_price=?, pnl_usdt=?, pnl_pct=?,
                    close_reason=?, status='CLOSED'
                WHERE id=?
            """, (ts, exit_price, pnl_usdt, pnl_pct, close_reason, trade_id))

            # Actualizar daily_summary
            is_win = 1 if pnl_usdt > 0 else 0
            con.execute("""
                INSERT INTO daily_summary (date, trades_closed, wins, losses, pnl_usdt)
                VALUES (?, 1, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    trades_closed = trades_closed + 1,
                    wins          = wins   + excluded.wins,
                    losses        = losses + (1 - excluded.wins),
                    pnl_usdt      = pnl_usdt + excluded.pnl_usdt
            """, (today, is_win, 1 - is_win, pnl_usdt))

            # Calcular y guardar win_rate del día
            row2 = con.execute(
                "SELECT wins, trades_closed FROM daily_summary WHERE date=?", (today,)
            ).fetchone()
            if row2 and row2[1] > 0:
                wr = row2[0] / row2[1] * 100
                con.execute(
                    "UPDATE daily_summary SET win_rate=?, balance_end=? WHERE date=?",
                    (wr, balance_after, today)
                )

        emoji = "✅" if pnl_usdt > 0 else "❌"
        log.info("%s Trade #%d cerrado: %s | PnL %.2f USDT (%.2f%%)",
                 emoji, trade_id, close_reason, pnl_usdt, pnl_pct)

    # ── Estadísticas ───────────────────────────────────────────────────────────

    def get_stats(self, days: int = 30) -> dict:
        """Estadísticas globales de los últimos N días (solo trades reales)."""
        with self._conn() as con:
            row = con.execute("""
                SELECT
                    COUNT(*)                                    AS total,
                    SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN pnl_usdt <= 0 THEN 1 ELSE 0 END) AS losses,
                    ROUND(SUM(pnl_usdt), 4)                    AS total_pnl,
                    ROUND(AVG(pnl_usdt), 4)                    AS avg_pnl,
                    ROUND(MAX(pnl_usdt), 4)                    AS best_trade,
                    ROUND(MIN(pnl_usdt), 4)                    AS worst_trade,
                    ROUND(AVG(score), 1)                       AS avg_score,
                    ROUND(AVG(rr), 2)                          AS avg_rr
                FROM trades
                WHERE status='CLOSED'
                  AND is_dry_run=0
                  AND close_ts >= datetime('now', ? || ' days')
            """, (f"-{days}",)).fetchone()

        if not row or row[0] == 0:
            return {"total": 0, "message": "Sin trades reales cerrados aún."}

        total, wins, losses, total_pnl, avg_pnl, best, worst, avg_score, avg_rr = row
        win_rate = (wins / total * 100) if total > 0 else 0

        # Profit factor
        with self._conn() as con:
            pf_row = con.execute("""
                SELECT
                    SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END),
                    ABS(SUM(CASE WHEN pnl_usdt < 0 THEN pnl_usdt ELSE 0 END))
                FROM trades
                WHERE status='CLOSED' AND is_dry_run=0
                  AND close_ts >= datetime('now', ? || ' days')
            """, (f"-{days}",)).fetchone()

        gross_profit, gross_loss = pf_row
        profit_factor = (gross_profit / gross_loss) if gross_loss and gross_loss > 0 else float("inf")

        return {
            "total":         total,
            "wins":          wins,
            "losses":        losses,
            "win_rate":      round(win_rate, 1),
            "total_pnl":     total_pnl,
            "avg_pnl":       avg_pnl,
            "best_trade":    best,
            "worst_trade":   worst,
            "profit_factor": round(profit_factor, 2),
            "avg_score":     avg_score,
            "avg_rr":        avg_rr,
            "days":          days,
        }

    def get_daily_summary(self, days: int = 7) -> list[dict]:
        """Resumen por día de los últimos N días."""
        with self._conn() as con:
            rows = con.execute("""
                SELECT date, trades_closed, wins, losses,
                       ROUND(pnl_usdt, 4), ROUND(win_rate, 1), balance_end
                FROM daily_summary
                WHERE date >= date('now', ? || ' days')
                ORDER BY date DESC
            """, (f"-{days}",)).fetchall()

        return [
            {
                "date":    r[0], "trades": r[1], "wins":    r[2],
                "losses":  r[3], "pnl":    r[4], "win_rate": r[5],
                "balance": r[6],
            }
            for r in rows
        ]

    def get_open_trades(self) -> list[dict]:
        """Trades actualmente abiertos."""
        with self._conn() as con:
            rows = con.execute("""
                SELECT id, symbol, side, entry_price, sl_price,
                       tp1_price, qty, score, open_ts, is_dry_run
                FROM trades WHERE status='OPEN'
                ORDER BY open_ts DESC
            """).fetchall()
        return [
            {
                "id": r[0], "symbol": r[1], "side": r[2],
                "entry": r[3], "sl": r[4], "tp1": r[5],
                "qty": r[6], "score": r[7], "open_ts": r[8],
                "dry_run": bool(r[9]),
            }
            for r in rows
        ]

    def format_stats_text(self, days: int = 30) -> str:
        """Texto formateado para Telegram."""
        s = self.get_stats(days)
        if s.get("total", 0) == 0:
            return "📊 Sin trades reales cerrados aún."

        lines = [
            f"📊 *Rentabilidad — Últimos {s['days']}d*\n",
            f"✅ Wins:    `{s['wins']}`",
            f"❌ Losses:  `{s['losses']}`",
            f"📈 Win Rate: `{s['win_rate']}%`",
            f"💰 PnL Total: `{s['total_pnl']:+.2f} USDT`",
            f"📉 Avg PnL:  `{s['avg_pnl']:+.2f} USDT`",
            f"🏆 Mejor:   `{s['best_trade']:+.2f} USDT`",
            f"💀 Peor:    `{s['worst_trade']:+.2f} USDT`",
            f"⚖️  PF:      `{s['profit_factor']}`",
            f"⭐ Score avg: `{s['avg_score']}/100`",
        ]
        return "\n".join(lines)

    def format_daily_text(self, days: int = 7) -> str:
        """Resumen diario para Telegram."""
        rows = self.get_daily_summary(days)
        if not rows:
            return "📅 Sin datos diarios aún."

        lines = [f"📅 *Resumen últimos {days} días*\n"]
        for r in rows:
            pnl_sign = "🟢" if (r["pnl"] or 0) >= 0 else "🔴"
            bal = f" | Bal: `{r['balance']:.2f}`" if r["balance"] else ""
            lines.append(
                f"{pnl_sign} `{r['date']}` — "
                f"T:{r['trades']} W:{r['wins']} L:{r['losses']} "
                f"WR:`{r['win_rate']}%` PnL:`{r['pnl']:+.2f}USDT`{bal}"
            )
        return "\n".join(lines)
