import json
import threading
from datetime import datetime
from flask import Flask, jsonify, render_template_string
import trader
import risk_manager as rm
from config import VERSION, SYMBOLS, TRADE_MODE, DASHBOARD_PORT

# ══════════════════════════════════════════════════════
# dashboard.py — Dashboard web en tiempo real v12.3
# Puerto: DASHBOARD_PORT (default 8080 / Railway: PORT)
# ══════════════════════════════════════════════════════

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>BB+RSI Elite {{ version }}</title>
<style>
  :root {
    --bg: #0d1117; --card: #161b22; --border: #30363d;
    --green: #3fb950; --red: #f85149; --yellow: #d29922;
    --blue: #58a6ff; --text: #e6edf3; --muted: #8b949e;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', monospace; font-size: 14px; }
  header { background: var(--card); border-bottom: 1px solid var(--border);
           padding: 16px 24px; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 18px; }
  .badge { padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: bold; }
  .live   { background: #1a3a1a; color: var(--green); border: 1px solid var(--green); }
  .paper  { background: #3a3a1a; color: var(--yellow); border: 1px solid var(--yellow); }
  .paused { background: #3a1a1a; color: var(--red); border: 1px solid var(--red); }
  .grid   { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px;
            padding: 20px 24px; }
  .card   { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; margin-bottom: 6px; }
  .card .value { font-size: 22px; font-weight: bold; }
  .card .sub   { color: var(--muted); font-size: 12px; margin-top: 4px; }
  .green { color: var(--green); }
  .red   { color: var(--red); }
  .yellow { color: var(--yellow); }
  section { padding: 0 24px 24px; }
  section h2 { font-size: 14px; color: var(--muted); text-transform: uppercase;
               margin-bottom: 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: var(--muted); font-size: 11px; text-transform: uppercase;
       padding: 8px 12px; border-bottom: 1px solid var(--border); }
  td { padding: 10px 12px; border-bottom: 1px solid #1c2128; font-size: 13px; }
  tr:hover td { background: #1c2128; }
  .win  { color: var(--green); }
  .loss { color: var(--red); }
  .side-long  { color: var(--green); font-weight: bold; }
  .side-short { color: var(--red); font-weight: bold; }
  .progress-bar { background: #21262d; border-radius: 4px; height: 6px; margin-top: 6px; }
  .progress-fill { height: 100%; border-radius: 4px; transition: width .3s; }
  footer { text-align: center; padding: 20px; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>🤖 BB+RSI Elite {{ version }}</h1>
  <span class="badge {{ mode }}">{{ mode.upper() }}</span>
  {% if paused %}<span class="badge paused">⏸ PAUSADO</span>{% endif %}
  <span style="margin-left: auto; color: var(--muted); font-size: 12px;">
    {{ now }} · auto-refresh 60s
  </span>
</header>

<div class="grid">
  <div class="card">
    <div class="label">Balance</div>
    <div class="value">${{ balance }}</div>
    <div class="sub">Inicial: ${{ initial }}</div>
  </div>
  <div class="card">
    <div class="label">ROI Total</div>
    <div class="value {{ 'green' if roi >= 0 else 'red' }}">${{ '+' if roi >= 0 else '' }}{{ roi }}%</div>
    <div class="sub">PnL: ${{ pnl_total }}</div>
  </div>
  <div class="card">
    <div class="label">Win Rate</div>
    <div class="value {{ 'green' if wr >= 50 else 'yellow' }}">{{ wr }}%</div>
    <div class="sub">{{ wins }}W / {{ losses }}L  ({{ total }} trades)</div>
  </div>
  <div class="card">
    <div class="label">Profit Factor</div>
    <div class="value {{ 'green' if pf >= 1.5 else ('yellow' if pf >= 1.0 else 'red') }}">{{ pf }}</div>
    <div class="sub">Expectativa: ${{ expectativa }}</div>
  </div>
  <div class="card">
    <div class="label">Drawdown</div>
    <div class="value {{ 'red' if drawdown >= 8 else ('yellow' if drawdown >= 4 else 'green') }}">{{ drawdown }}%</div>
    <div class="sub">Máximo: ${{ peak }}</div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{{ [drawdown*8,100]|min }}%; background:{{ '#f85149' if drawdown >= 8 else '#d29922' }};"></div>
    </div>
  </div>
  <div class="card">
    <div class="label">Posiciones Abiertas</div>
    <div class="value">{{ open_pos }}</div>
    <div class="sub">Pérd. consec.: {{ consec_loss }}</div>
  </div>
  <div class="card">
    <div class="label">PnL Hoy</div>
    <div class="value {{ 'green' if daily_pnl >= 0 else 'red' }}">${{ '+' if daily_pnl >= 0 else '' }}{{ daily_pnl }}</div>
    <div class="sub">{{ daily_pnl_pct }}% del balance</div>
  </div>
</div>

{% if positions %}
<section>
  <h2>📋 Posiciones Abiertas</h2>
  <table>
    <tr><th>Par</th><th>Lado</th><th>Entrada</th><th>SL</th><th>TP</th>
        <th>Score</th><th>RSI</th><th>4h Bias</th><th>Apertura</th></tr>
    {% for sym, p in positions.items() %}
    <tr>
      <td><b>{{ sym }}</b></td>
      <td class="side-{{ p.side }}">{{ p.side.upper() }}</td>
      <td><code>{{ p.entry }}</code></td>
      <td class="red"><code>{{ "%.6g"|format(p.sl) }}</code></td>
      <td class="green"><code>{{ "%.6g"|format(p.tp) }}</code></td>
      <td>{{ p.score }}</td>
      <td>{{ p.rsi_e }}</td>
      <td>{{ p.get('bias_4h', '?') }}</td>
      <td style="color:var(--muted)">{{ p.open_time[:16] }}</td>
    </tr>
    {% endfor %}
  </table>
</section>
{% endif %}

<section>
  <h2>📈 Últimos 30 Trades</h2>
  <table>
    <tr><th>Fecha</th><th>Par</th><th>Lado</th><th>Entrada</th><th>Salida</th>
        <th>PnL</th><th>R</th><th>Score</th><th>Razón</th><th>4h</th></tr>
    {% for t in trades %}
    <tr>
      <td style="color:var(--muted)">{{ t.date }}</td>
      <td><b>{{ t.symbol }}</b></td>
      <td class="side-{{ t.side }}">{{ t.side.upper() }}</td>
      <td><code>{{ "%.5g"|format(t.entry) }}</code></td>
      <td><code>{{ "%.5g"|format(t.exit) }}</code></td>
      <td class="{{ 'win' if t.pnl >= 0 else 'loss' }}">${{ '%+.4f'|format(t.pnl) }}</td>
      <td class="{{ 'win' if t.pnl >= 0 else 'loss' }}">{{ t.result }}</td>
      <td>{{ t.score }}</td>
      <td style="color:var(--muted)">{{ t.reason }}</td>
      <td style="color:var(--muted)">{{ t.get('bias_4h','?') }}</td>
    </tr>
    {% endfor %}
  </table>
</section>

<footer>BB+RSI Elite {{ version }} · Railway · BingX Perpetual Swap</footer>
</body>
</html>
"""


@app.route("/")
def index():
    from config import INITIAL_BAL
    bal     = trader.get_balance()
    pos     = trader.get_positions()
    history = trader.get_trade_history(30)
    summ    = trader.get_summary()
    stats   = rm.get_stats(bal)
    rm_state = rm.get_state()

    wins   = summ.get("wins", 0)
    losses = summ.get("losses", 0)
    total  = summ.get("total", 0)
    pnl_total = summ.get("pnl", 0)
    pf     = summ.get("pf", 0)
    wr     = summ.get("wr", 0)
    roi    = round((bal - INITIAL_BAL) / INITIAL_BAL * 100, 2) if INITIAL_BAL > 0 else 0

    # Expectativa
    trades_list = trader.get_trade_history(1000)
    w_list = [t["pnl"] for t in trades_list if t["pnl"] > 0]
    l_list = [t["pnl"] for t in trades_list if t["pnl"] <= 0]
    aw = sum(w_list) / len(w_list) if w_list else 0
    al = sum(l_list) / len(l_list) if l_list else 0
    exp = round(wr/100 * aw + (1 - wr/100) * al, 4) if total > 0 else 0

    return render_template_string(HTML,
        version=VERSION, mode=TRADE_MODE,
        paused=rm_state.get("paused", False),
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        balance=f"{bal:.2f}", initial=f"{INITIAL_BAL:.2f}",
        roi=f"{roi:+.2f}", pnl_total=f"{pnl_total:+.4f}",
        wr=wr, wins=wins, losses=losses, total=total,
        pf=pf, expectativa=f"{exp:+.4f}",
        drawdown=stats["drawdown_pct"], peak=f"{stats['peak_balance']:.2f}",
        open_pos=len(pos), consec_loss=stats["consecutive_losses"],
        daily_pnl=f"{stats['daily_pnl']:+.4f}",
        daily_pnl_pct=f"{stats['daily_pnl_pct']:+.2f}",
        positions=pos, trades=list(reversed(history)),
    )


@app.route("/api/status")
def api_status():
    bal   = trader.get_balance()
    summ  = trader.get_summary()
    stats = rm.get_stats(bal)
    return jsonify({
        "version": VERSION, "mode": TRADE_MODE,
        "balance": round(bal, 2),
        "positions": len(trader.get_positions()),
        **summ, **stats,
    })


@app.route("/api/trades")
def api_trades():
    return jsonify(trader.get_trade_history(100))


@app.route("/health")
def health():
    return "OK", 200


def start_dashboard():
    from waitress import serve
    t = threading.Thread(
        target=lambda: serve(app, host="0.0.0.0", port=DASHBOARD_PORT, threads=4),
        daemon=True
    )
    t.start()
    print(f"  [WEB] Dashboard en http://0.0.0.0:{DASHBOARD_PORT}")
