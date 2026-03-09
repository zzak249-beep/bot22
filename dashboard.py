#!/usr/bin/env python3
"""
dashboard.py v4.0 — Dashboard HTTP minimalista
Accesible en Railway en el puerto asignado por PORT env var.
"""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from pathlib import Path
import os

PORT = int(os.getenv("PORT", 8080))


def _get_data() -> dict:
    """Recopila datos actuales del bot."""
    try:
        import trader
        import risk_manager as rm
        import config

        balance = trader.get_balance()
        stats   = rm.get_stats(balance)
        summary = trader.get_summary()
        history = trader.get_trade_history(20)

        return {
            "version":    config.VERSION,
            "mode":       config.TRADE_MODE,
            "leverage":   config.LEVERAGE,
            "tf":         config.CANDLE_TF,
            "balance":    round(balance, 2),
            "positions":  trader.get_positions(),
            "stats":      stats,
            "summary":    summary,
            "history":    history,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "paused":     rm.is_manually_paused(),
        }
    except Exception as e:
        return {"error": str(e), "timestamp": datetime.now(timezone.utc).isoformat()}


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🤖 Bot Trading v4.0</title>
<style>
  body {{ font-family: 'Courier New', monospace; background:#0d1117; color:#c9d1d9; margin:0; padding:20px; }}
  h1 {{ color:#58a6ff; border-bottom:1px solid #30363d; pb:10px; }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:6px; padding:16px; margin:10px 0; }}
  .green {{ color:#3fb950; }} .red {{ color:#f85149; }} .yellow {{ color:#d29922; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:10px; }}
  .stat {{ background:#21262d; padding:12px; border-radius:6px; text-align:center; }}
  .stat .val {{ font-size:1.8em; font-weight:bold; }}
  .stat .lbl {{ font-size:0.8em; color:#8b949e; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; }}
  td,th {{ padding:6px 10px; border-bottom:1px solid #21262d; text-align:left; font-size:0.85em; }}
  th {{ color:#8b949e; }}
  pre {{ background:#21262d; padding:12px; border-radius:4px; overflow-x:auto; font-size:0.8em; }}
</style>
</head>
<body>
<h1>🤖 Bot Trading {version} — {mode}</h1>
<p style="color:#8b949e">Actualización automática cada 30s | {timestamp}</p>
{paused_banner}
<div class="grid">
  <div class="stat"><div class="val green">${balance}</div><div class="lbl">Balance</div></div>
  <div class="stat"><div class="val">{open_pos}</div><div class="lbl">Posiciones abiertas</div></div>
  <div class="stat"><div class="val {wr_color}">{wr}%</div><div class="lbl">Win Rate</div></div>
  <div class="stat"><div class="val">{total_trades}</div><div class="lbl">Total trades</div></div>
  <div class="stat"><div class="val {pnl_color}">${pnl}</div><div class="lbl">PnL Total</div></div>
  <div class="stat"><div class="val yellow">{dd}%</div><div class="lbl">Drawdown</div></div>
</div>

<div class="card">
<b>Config activa:</b>
Leverage {leverage}x | Timeframe {tf} | TP:{tp_mult}xATR | SL:{sl_mult}xATR | Ratio ~{ratio}:1
</div>

{positions_html}
{history_html}

<div class="card">
<b>Stats JSON:</b>
<pre>{stats_json}</pre>
</div>
</body></html>"""


def _build_html(data: dict) -> str:
    if "error" in data:
        return f"<h1>Error: {data['error']}</h1>"

    summary   = data.get("summary", {})
    stats     = data.get("stats", {})
    positions = data.get("positions", {})
    history   = data.get("history", [])

    wr    = summary.get("wr", 0)
    pnl   = summary.get("pnl", 0)
    dd    = stats.get("drawdown_pct", 0)

    wr_color  = "green" if wr > 50 else "red"
    pnl_color = "green" if pnl >= 0 else "red"

    paused = '<div class="card" style="border-color:#d29922;color:#d29922">⏸️ BOT PAUSADO</div>' \
             if data.get("paused") else ""

    # Posiciones abiertas
    if positions:
        rows = "".join(
            f"<tr><td>{s}</td><td>{p['side'].upper()}</td>"
            f"<td>{p['entry']:.4f}</td><td>{p['sl']:.4f}</td><td>{p['tp']:.4f}</td>"
            f"<td>{p['score']}</td></tr>"
            for s, p in positions.items()
        )
        pos_html = f"""<div class="card"><b>Posiciones abiertas ({len(positions)}):</b>
<table><tr><th>Par</th><th>Side</th><th>Entry</th><th>SL</th><th>TP</th><th>Score</th></tr>
{rows}</table></div>"""
    else:
        pos_html = '<div class="card">Sin posiciones abiertas</div>'

    # Historial últimos trades
    if history:
        rows = "".join(
            f"<tr><td>{t.get('symbol','')}</td><td>{t.get('side','').upper()}</td>"
            f"<td class='{'green' if t.get('pnl',0)>0 else 'red'}'>${t.get('pnl',0):+.4f}</td>"
            f"<td>{t.get('reason','')}</td><td>{t.get('date','')}</td></tr>"
            for t in reversed(history[-10:])
        )
        hist_html = f"""<div class="card"><b>Últimos trades:</b>
<table><tr><th>Par</th><th>Side</th><th>PnL</th><th>Razón</th><th>Fecha</th></tr>
{rows}</table></div>"""
    else:
        hist_html = '<div class="card">Sin historial de trades aún</div>'

    try:
        import config
        tp_mult = config.TP_ATR_MULT
        sl_mult = config.SL_ATR_MULT
        ratio   = round(tp_mult / sl_mult, 1)
    except Exception:
        tp_mult = sl_mult = ratio = "?"

    return HTML_TEMPLATE.format(
        version=data.get("version", "v4.0"),
        mode=data.get("mode", "?").upper(),
        timestamp=data.get("timestamp", "")[:19],
        paused_banner=paused,
        balance=data.get("balance", 0),
        open_pos=len(positions),
        wr=wr, wr_color=wr_color,
        total_trades=summary.get("total", 0),
        pnl=round(pnl, 4), pnl_color=pnl_color,
        dd=round(dd, 1),
        leverage=data.get("leverage", "?"),
        tf=data.get("tf", "?"),
        tp_mult=tp_mult, sl_mult=sl_mult, ratio=ratio,
        positions_html=pos_html,
        history_html=hist_html,
        stats_json=json.dumps(stats, indent=2),
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silenciar logs HTTP

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if self.path == "/api":
            data = _get_data()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        data = _get_data()
        html = _build_html(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


def start_dashboard():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"📊 Dashboard en http://0.0.0.0:{PORT}")
