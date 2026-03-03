"""
analyzer.py — Exporta y analiza el historial de operaciones.

Uso:
  python analyzer.py              → reporte en consola
  python analyzer.py --csv        → exporta trades.csv
  python analyzer.py --full       → reporte completo con graficos de texto
"""

import sys
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("bot_data.db")


def get_conn():
    if not DB_PATH.exists():
        print(f"No se encuentra {DB_PATH} — ejecuta el bot al menos una vez primero.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def export_csv(output: str = "trades_exportados.csv"):
    """Exporta todos los trades a CSV."""
    try:
        import pandas as pd
        conn = get_conn()
        df = pd.read_sql("SELECT * FROM trades ORDER BY entry_time", conn)
        conn.close()
        df.to_csv(output, index=False)
        print(f"✅ Exportado: {output}  ({len(df)} trades)")
    except ImportError:
        # fallback sin pandas
        conn   = get_conn()
        trades = conn.execute("SELECT * FROM trades ORDER BY entry_time").fetchall()
        conn.close()
        if not trades:
            print("Sin trades registrados.")
            return
        with open(output, "w", encoding="utf-8") as f:
            f.write(",".join(trades[0].keys()) + "\n")
            for t in trades:
                f.write(",".join(str(v or "") for v in tuple(t)) + "\n")
        print(f"✅ Exportado: {output}  ({len(trades)} trades)")


def bar(value: float, max_val: float, width: int = 20, fill: str = "█") -> str:
    n = int(value / max_val * width) if max_val else 0
    return fill * n + "░" * (width - n)


def print_report(full: bool = False):
    conn = get_conn()

    # ── Stats generales ──────────────────────────────────
    stats = conn.execute("""
        SELECT
            COUNT(*) total,
            SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) losses,
            ROUND(SUM(pnl),2)  total_pnl,
            ROUND(AVG(CASE WHEN result='WIN'  THEN pnl END),2) avg_win,
            ROUND(AVG(CASE WHEN result='LOSS' THEN pnl END),2) avg_loss,
            MIN(entry_time) first_trade,
            MAX(exit_time)  last_trade
        FROM trades WHERE result IN ('WIN','LOSS')
    """).fetchone()

    total = stats["total"] or 0
    if total == 0:
        print("Sin trades cerrados todavia.")
        conn.close()
        return

    wins  = stats["wins"]  or 0
    wr    = wins / total * 100
    avg_w = stats["avg_win"]  or 0
    avg_l = stats["avg_loss"] or 0
    exp   = (wr/100 * avg_w) + ((1 - wr/100) * avg_l)
    pf_raw = abs(avg_w * wins) / abs(avg_l * (total - wins)) if (total - wins) > 0 and avg_l != 0 else 0

    print("\n" + "═"*55)
    print("  BB+RSI BOT — REPORTE COMPLETO")
    print("═"*55)
    print(f"  Periodo:       {stats['first_trade'][:10]}  →  {(stats['last_trade'] or '')[:10]}")
    print(f"  Total trades:  {total}")
    print(f"  Ganados:       {wins}  ({wr:.1f}%)")
    print(f"  Perdidos:      {total - wins}  ({100-wr:.1f}%)")
    print(f"  PnL total:     ${stats['total_pnl']:+.2f}")
    print(f"  Ganancia media: ${avg_w:+.2f}  |  Perdida media: ${avg_l:+.2f}")
    print(f"  Expectativa:   ${exp:+.4f} por trade")
    print(f"  Profit Factor: {pf_raw:.2f}")
    print()

    # ── Por simbolo ──────────────────────────────────────
    print("  POR PAR:")
    by_sym = conn.execute("""
        SELECT symbol,
            COUNT(*) total,
            SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
            ROUND(SUM(pnl),2) pnl,
            ROUND(AVG(pnl),2) avg_pnl
        FROM trades WHERE result IN ('WIN','LOSS')
        GROUP BY symbol ORDER BY pnl DESC
    """).fetchall()

    max_pnl = max(abs(r["pnl"]) for r in by_sym) if by_sym else 1
    for r in by_sym:
        wr_s  = r["wins"] / r["total"] * 100 if r["total"] else 0
        emoji = "🟢" if r["pnl"] >= 0 else "🔴"
        b     = bar(abs(r["pnl"]), max_pnl, 15)
        print(f"  {emoji} {r['symbol']:12s}  {b}  ${r['pnl']:>8.2f}  WR:{wr_s:.0f}%  ({r['total']} trades)")

    # ── Por razon de cierre ───────────────────────────────
    print("\n  POR RAZON DE CIERRE:")
    by_reason = conn.execute("""
        SELECT close_reason, COUNT(*) n,
            SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
            ROUND(SUM(pnl),2) pnl
        FROM trades WHERE result IN ('WIN','LOSS')
        GROUP BY close_reason ORDER BY n DESC
    """).fetchall()
    for r in by_reason:
        wr_r  = r["wins"] / r["n"] * 100 if r["n"] else 0
        emoji = "🟢" if r["pnl"] >= 0 else "🔴"
        print(f"  {emoji} {(r['close_reason'] or 'N/A'):12s}  {r['n']:3d} trades  WR:{wr_r:.0f}%  PnL:${r['pnl']:+.2f}")

    # ── Historial de parametros ───────────────────────────
    if full:
        print("\n  EVOLUCIÓN DE PARAMETROS (learner):")
        params = conn.execute("""
            SELECT ts, bb_sigma, rsi_ob, sl_atr, reason
            FROM params_history ORDER BY ts DESC LIMIT 10
        """).fetchall()
        if params:
            for p in params:
                print(f"  {p['ts'][:16]}  σ={p['bb_sigma']}  RSI<{p['rsi_ob']}  SL={p['sl_atr']}x")
                print(f"             → {p['reason'][:65]}")
        else:
            print("  Sin cambios de parametros todavia.")

        # ── Trades recientes ──────────────────────────────
        print("\n  ULTIMOS 10 TRADES:")
        recent = conn.execute("""
            SELECT symbol, entry_time, entry_price, exit_price,
                   pnl, result, close_reason, rsi_at_entry
            FROM trades WHERE result IN ('WIN','LOSS')
            ORDER BY exit_time DESC LIMIT 10
        """).fetchall()
        print(f"  {'Par':12s} {'Fecha':10s} {'Entrada':>10s} {'Salida':>10s} {'PnL':>8s} {'RSI':>6s} Razon")
        print("  " + "-"*70)
        for t in recent:
            emoji = "✅" if t["result"] == "WIN" else "❌"
            date  = (t["entry_time"] or "")[:10]
            print(f"  {emoji} {t['symbol']:10s} {date} "
                  f"${t['entry_price']:>9.4f} ${(t['exit_price'] or 0):>9.4f} "
                  f"${(t['pnl'] or 0):>+7.2f} {(t['rsi_at_entry'] or 0):>5.1f} "
                  f"{t['close_reason'] or ''}")

    # ── Resumen diario ────────────────────────────────────
    if full:
        print("\n  RESUMEN DIARIO (ultimos 14 dias):")
        daily = conn.execute("""
            SELECT date, trades, wins, losses, ROUND(pnl,2) pnl
            FROM daily_summary
            ORDER BY date DESC LIMIT 14
        """).fetchall()
        for d in daily:
            wr_d  = d["wins"] / d["trades"] * 100 if d["trades"] else 0
            emoji = "🟢" if d["pnl"] >= 0 else "🔴"
            print(f"  {emoji} {d['date']}  {d['trades']:2d} trades  WR:{wr_d:.0f}%  PnL:${d['pnl']:+.2f}")

    print("\n" + "═"*55)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analizador de trades BB+RSI Bot")
    parser.add_argument("--csv",  action="store_true", help="Exportar a CSV")
    parser.add_argument("--full", action="store_true", help="Reporte completo")
    parser.add_argument("--out",  default="trades_exportados.csv", help="Nombre archivo CSV")
    args = parser.parse_args()

    if args.csv:
        export_csv(args.out)
    else:
        print_report(full=args.full)
