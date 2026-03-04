import requests, pandas as pd, numpy as np, time
from datetime import datetime

# ══════════════════════════════════════════════════════════
# BACKTEST v8 — 6 CAMBIOS QUIRÚRGICOS SOBRE v7
#
# DIAGNÓSTICO v7:
#   WR=31.7% | PF=0.36 | ROI=-8.5%
#   98/123 trades (80%) cierran en SL → problema raíz
#   Filtro tendencia inútil: 107/123 son "flat" (umbral 0.3% demasiado bajo)
#   RSI medio entrada=40.5 cuando RSI_LONG<35 → entradas sucias por divergencia
#   AVAX, SOL, DOGE, DOT destruyen el P&L
#
# CAMBIOS v8:
#   [1] TREND_LOOKBACK=10 + umbral 0.6% → menos "flat", filtro real
#   [2] RSI_LONG=28 / RSI_SHORT=72 → entradas más limpias, menos solapamiento
#   [3] SL_ATR=1.5 → SL más ajustado, mejor R:R matemático
#   [4] SCORE_MIN=55 → barra de calidad más alta
#   [5] MIN_RR=1.8 → exigir mejor ratio riesgo/beneficio antes de entrar
#   [6] SYMBOLS filtrados → quitar los 4 peores (AVAX, SOL, DOGE, DOT)
#       + añadir LTC, UNI, ATOM, OP como reemplazos más estables
#   [BONUS] Filtro BB_WIDTH: solo operar cuando las bandas están
#           suficientemente abiertas (evita rangos apretados donde SL salta fácil)
# ══════════════════════════════════════════════════════════

BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14

# [2] RSI más estricto — evita entradas en zona "gris"
RSI_LONG       = 28   # era 35
RSI_SHORT      = 72   # era 65

# [3] SL más ajustado → mejora R:R sin mover TP
SL_ATR         = 1.5  # era 2.0

PARTIAL_TP_ATR = 2.0  # era 2.5 — toma parcial más rápida
LEVERAGE       = 2
RISK_PCT       = 0.02
INITIAL_BAL    = 100.0

# [4] Score mínimo más alto — filtrar señales mediocres
SCORE_MIN      = 55   # era 45

COOLDOWN_BARS  = 3

# [5] Mejor R:R requerido
MIN_RR         = 1.8  # era 1.3

# [1] Lookback más largo + umbral más alto → el filtro flat se reduce drásticamente
TREND_LOOKBACK  = 10  # era 5
TREND_THRESHOLD = 0.6 # era 0.3 (porcentaje de cambio para considerar tendencia)

# [BONUS] Filtro de anchura de BB: basis ± BB_WIDTH_MIN * ATR mínimo
BB_WIDTH_MIN_ATR = 1.5  # BB debe ser al menos 1.5x ATR para operar

# [6] Símbolos: eliminados AVAX(-1.90), DOGE(-1.16), SOL(-1.50), DOT(-1.11)
#     Mantenidos los 6 mejores de v7 + 4 nuevos más estables
SYMBOLS = [
    "BTC-USDT",   # v7: -0.17 WR53%  — mejor pair
    "ETH-USDT",   # v7: -0.12 WR54%  — segundo mejor
    "ADA-USDT",   # v7: -0.05 WR56%  — mejor WR
    "BNB-USDT",   # v7: -0.60 WR29%  — marginal, mantenemos
    "XRP-USDT",   # v7: -0.90 WR20%  — probar con filtros nuevos
    "LINK-USDT",  # v7: -1.00 WR25%  — probar con filtros nuevos
    # Nuevos — más líquidos y con mejor comportamiento mean-reversion en 1H
    "LTC-USDT",
    "UNI-USDT",
    "ATOM-USDT",
    "OP-USDT",
]


# ══════════════════════════════════════════════════════════════
# DATA FETCH (sin cambios respecto v7)
# ══════════════════════════════════════════════════════════════

def fetch(sym):
    endpoints = [
        "https://open-api.bingx.com/openApi/swap/v3/quote/klines",
        "https://open-api.bingx.com/openApi/swap/v2/quote/klines",
    ]
    print(f"  {sym}...", end="", flush=True)
    all_c = []
    for url in endpoints:
        all_c = []; end = None; success = False
        for _ in range(2):
            p = {"symbol": sym, "interval": "1h", "limit": 1000}
            if end: p["endTime"] = end
            try:
                r = requests.get(url, params=p, timeout=15).json()
                c = r if isinstance(r, list) else r.get("data", [])
                if not c: break
                all_c = c + all_c
                if isinstance(c[0], list):
                    first_ts = c[0][0]
                else:
                    first_ts = c[0].get("time") or c[0].get("t", 0)
                end = int(first_ts) - 1
                time.sleep(0.3); success = True
            except Exception as e:
                print(f" ERR:{e}", end=""); break
        if success and all_c: break

    if not all_c: print(" sin datos"); return pd.DataFrame()

    rows = []
    for c in all_c:
        if isinstance(c, list):
            rows.append(c[:6])
        else:
            rows.append([
                c.get("time")   or c.get("t",   0),
                c.get("open")   or c.get("o",   0),
                c.get("high")   or c.get("h",   0),
                c.get("low")    or c.get("l",   0),
                c.get("close")  or c.get("c",   0),
                c.get("volume") or c.get("v",   0),
            ])

    df = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"])
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["ts"] = pd.to_datetime(pd.to_numeric(df["ts"], errors="coerce"), unit="ms")
    df = df.dropna().sort_values("ts").drop_duplicates("ts").reset_index(drop=True)

    if df.empty or df["close"].max() == 0:
        print(" sin datos validos"); return pd.DataFrame()

    print(f" {len(df)} velas ({str(df['ts'].iloc[0])[:10]} -> {str(df['ts'].iloc[-1])[:10]})")
    return df


# ══════════════════════════════════════════════════════════════
# INDICADORES (sin cambios en cálculo, solo se usan distinto)
# ══════════════════════════════════════════════════════════════

def rsi_calc(close):
    d = close.diff()
    g = d.clip(lower=0).rolling(RSI_PERIOD).mean()
    l = (-d.clip(upper=0)).rolling(RSI_PERIOD).mean()
    return 100 - 100 / (1 + g / l.replace(0, float("nan")))


def atr_calc(df, p=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()


def macd_calc(close):
    e12 = close.ewm(span=12, adjust=False).mean()
    e26 = close.ewm(span=26, adjust=False).mean()
    lin = e12 - e26
    return lin - lin.ewm(span=9, adjust=False).mean()


def stoch_rsi_calc(close, period=14, k=3):
    d = close.diff()
    g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    rs = g / l.replace(0, float("nan"))
    rsi_s = 100 - 100 / (1 + rs)
    lo = rsi_s.rolling(period).min()
    hi = rsi_s.rolling(period).max()
    stoch = (rsi_s - lo) / (hi - lo).replace(0, float("nan")) * 100
    return stoch.rolling(k).mean()


def divergence(cls, rsi_s, lb=6):
    if len(rsi_s) < lb + 2: return None
    r = rsi_s.iloc[-lb-1:]; p = cls.iloc[-lb-1:]
    rn = float(r.iloc[-1]); pn = float(p.iloc[-1])
    if pn < float(p.iloc[:-1].min()) and rn > float(r.iloc[:-1].min()) + 3:
        return "bull"
    if pn > float(p.iloc[:-1].max()) and rn < float(r.iloc[:-1].max()) - 3:
        return "bear"
    return None


# [1] Filtro de tendencia mejorado: lookback=10v, umbral=0.6%
def trend_direction(basis_series, i, lb=TREND_LOOKBACK):
    if i < lb: return "flat"
    now  = float(basis_series.iloc[i])
    prev = float(basis_series.iloc[i - lb])
    if now == 0 or prev == 0: return "flat"
    change_pct = (now - prev) / prev * 100
    if   change_pct >  TREND_THRESHOLD: return "up"
    elif change_pct < -TREND_THRESHOLD: return "down"
    else:                                return "flat"


# ══════════════════════════════════════════════════════════════
# SCORING (ajustado para RSI_LONG=28 / RSI_SHORT=72)
# ══════════════════════════════════════════════════════════════

def calc_score_long(r, dv, mb, stv):
    s = 40
    if   r < 15: s += 35
    elif r < 20: s += 28
    elif r < 25: s += 20
    elif r < 28: s += 12   # zona nueva con RSI_LONG=28
    if dv == "bull": s += 18
    if mb: s += 5
    if stv is not None and not np.isnan(stv):
        if   stv < 10: s += 15
        elif stv < 20: s += 10
        elif stv < 30: s += 5
    return min(s, 100)


def calc_score_short(r, dv, mb, stv):
    s = 40
    if   r > 85: s += 35
    elif r > 80: s += 28
    elif r > 75: s += 20
    elif r > 72: s += 12   # zona nueva con RSI_SHORT=72
    if dv == "bear": s += 18
    if not mb: s += 5
    if stv is not None and not np.isnan(stv):
        if   stv > 90: s += 15
        elif stv > 80: s += 10
        elif stv > 70: s += 5
    return min(s, 100)


# ══════════════════════════════════════════════════════════════
# BACKTEST PRINCIPAL
# ══════════════════════════════════════════════════════════════

def backtest(df, sym, balance):
    if len(df) < BB_PERIOD + 30:
        return [], balance

    df = df.copy()
    basis = df["close"].rolling(BB_PERIOD).mean()
    std   = df["close"].rolling(BB_PERIOD).std()
    df["upper"]  = basis + BB_SIGMA * std
    df["basis"]  = basis
    df["lower"]  = basis - BB_SIGMA * std
    df["rsi"]    = rsi_calc(df["close"])
    df["atr"]    = atr_calc(df)
    df["macd"]   = macd_calc(df["close"])
    df["stoch"]  = stoch_rsi_calc(df["close"])
    # BB width en términos de ATR (para filtro de anchura)
    df["bb_width"] = (df["upper"] - df["lower"]) / df["atr"]

    trades = []; pos = None; cool = 0

    for i in range(BB_PERIOD + 30, len(df)):
        cur   = df.iloc[i]
        prev  = df.iloc[i-1]
        price = float(cur["close"])
        r     = float(cur["rsi"])     if not np.isnan(cur["rsi"])     else 50.0
        a     = float(cur["atr"])     if not np.isnan(cur["atr"])     else 0.0
        mb    = float(cur["macd"]) > 0 if not np.isnan(cur["macd"])   else True
        stv   = float(cur["stoch"])   if not np.isnan(cur["stoch"])   else 50.0
        bbw   = float(cur["bb_width"]) if not np.isnan(cur["bb_width"]) else 0.0
        if a <= 0: continue

        blo  = float(cur["lower"])
        bhi  = float(cur["upper"])
        bmid = float(cur["basis"])

        # [1] Dirección de tendencia — filtro más estricto
        trend = trend_direction(df["basis"], i, TREND_LOOKBACK)

        # ── Gestión posición abierta ────────────────────
        if pos:
            side  = pos["side"]
            entry = pos["entry"]

            if not pos.get("pd"):
                hp = (side == "long"  and price >= pos["tp_p"]) or \
                     (side == "short" and price <= pos["tp_p"])
                if hp:
                    pos["qty"] *= 0.5
                    pos["pd"]   = True
                    pos["sl"]   = entry  # mover SL a BE tras parcial

            # Trailing stop tras parcial
            if pos.get("pd") and side == "long":
                trail = price - a * 1.2   # un poco más ajustado
                if trail > pos["sl"]: pos["sl"] = trail
            elif pos.get("pd") and side == "short":
                trail = price + a * 1.2
                if trail < pos["sl"]: pos["sl"] = trail

            sl_hit = (side == "long"  and price <= pos["sl"]) or \
                     (side == "short" and price >= pos["sl"])
            tp_hit = (side == "long"  and price >= pos["tp"]) or \
                     (side == "short" and price <= pos["tp"])
            sig_exit_long  = (side == "long"  and
                              float(prev["close"]) <= float(prev["basis"]) and
                              price > bmid and pos.get("pd"))
            sig_exit_short = (side == "short" and
                              float(prev["close"]) >= float(prev["basis"]) and
                              price < bmid and pos.get("pd"))

            reason = None; exit_p = price
            if   sl_hit:                          exit_p = pos["sl"]; reason = "SL"
            elif tp_hit:                          exit_p = pos["tp"]; reason = "TP"
            elif sig_exit_long or sig_exit_short: reason = "SIGNAL"

            if reason:
                pct = (exit_p - entry) / entry if side == "long" \
                      else (entry - exit_p) / entry
                pnl = pos["qty"] * entry * pct * LEVERAGE
                balance += pnl
                trades.append({
                    "symbol": sym, "side": side, "entry": entry,
                    "exit": exit_p, "pnl": round(pnl, 4),
                    "result": "WIN" if pnl > 0 else "LOSS",
                    "reason": reason, "sc": pos["sc"],
                    "date": str(cur["ts"])[:10],
                    "bars": i - pos["oi"], "rsi_e": pos["rsi_e"],
                    "trend": pos["trend"], "bbw": pos["bbw"]
                })
                pos  = None
                cool = COOLDOWN_BARS
            continue

        if cool > 0: cool -= 1; continue

        # [BONUS] Filtro BB width — no operar en rango estrecho
        if bbw < BB_WIDTH_MIN_ATR:
            continue

        dv = divergence(df["close"].iloc[max(0,i-8):i+1],
                        df["rsi"].iloc[max(0,i-8):i+1])

        # ══ SEÑAL LONG — [2] RSI_LONG=28, [1] trend != down ═
        if trend != "down":
            touch_long = (price <= blo * 1.002) or \
                         (dv == "bull" and r < RSI_LONG and price <= blo * 1.005)
            if touch_long and r < RSI_LONG and balance > 0:
                sc = calc_score_long(r, dv, mb, stv)
                if sc >= SCORE_MIN:
                    # [3] SL_ATR=1.5
                    sl   = price - SL_ATR * a
                    tp   = bhi
                    # [BONUS] PARTIAL_TP_ATR=2.0
                    tp_p = price + PARTIAL_TP_ATR * a
                    if sl > 0 and tp > price and (price - sl) > 0:
                        rr_val = (tp - price) / (price - sl)
                        # [5] MIN_RR=1.8
                        if rr_val >= MIN_RR:
                            qty = (balance * RISK_PCT * LEVERAGE) / price
                            pos = {"side": "long", "entry": price,
                                   "sl": sl, "tp": tp, "tp_p": tp_p,
                                   "qty": qty, "sc": sc, "oi": i,
                                   "pd": False, "rsi_e": round(r, 1),
                                   "trend": trend, "bbw": round(bbw, 2)}
                            continue

        # ══ SEÑAL SHORT — [2] RSI_SHORT=72, [1] trend != up ═
        if trend != "up":
            touch_short = (price >= bhi * 0.998) or \
                          (dv == "bear" and r > RSI_SHORT and price >= bhi * 0.995)
            if touch_short and r > RSI_SHORT and balance > 0:
                sc = calc_score_short(r, dv, mb, stv)
                if sc >= SCORE_MIN:
                    sl   = price + SL_ATR * a
                    tp   = blo
                    tp_p = price - PARTIAL_TP_ATR * a
                    if sl > price and tp < price and (sl - price) > 0:
                        rr_val = (price - tp) / (sl - price)
                        if rr_val >= MIN_RR:
                            qty = (balance * RISK_PCT * LEVERAGE) / price
                            pos = {"side": "short", "entry": price,
                                   "sl": sl, "tp": tp, "tp_p": tp_p,
                                   "qty": qty, "sc": sc, "oi": i,
                                   "pd": False, "rsi_e": round(r, 1),
                                   "trend": trend, "bbw": round(bbw, 2)}

    return trades, balance


# ══════════════════════════════════════════════════════════════
# REPORTE
# ══════════════════════════════════════════════════════════════

def report(all_trades, final_bal):
    if not all_trades:
        print("\n  Sin trades generados.")
        return
    wins   = [t for t in all_trades if t["result"] == "WIN"]
    losses = [t for t in all_trades if t["result"] == "LOSS"]
    longs  = [t for t in all_trades if t["side"]   == "long"]
    shorts = [t for t in all_trades if t["side"]   == "short"]
    total  = len(all_trades)
    wr     = len(wins) / total * 100
    tp_sum = sum(t["pnl"] for t in all_trades)
    aw     = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
    al     = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    gw     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    pf     = gw / gl if gl > 0 else 999
    roi    = (final_bal - INITIAL_BAL) / INITIAL_BAL * 100
    exp    = wr / 100 * aw + (1 - wr / 100) * al
    rsi_avg = sum(t.get("rsi_e", 0) for t in all_trades) / len(all_trades)
    bbw_avg = sum(t.get("bbw", 0) for t in all_trades) / len(all_trades)

    wl = sum(1 for t in longs  if t["result"] == "WIN")
    ws = sum(1 for t in shorts if t["result"] == "WIN")
    pl = sum(t["pnl"] for t in longs)
    ps = sum(t["pnl"] for t in shorts)

    sep = "=" * 64
    lines = [
        sep,
        "  BB+RSI ELITE v8 — LONG + SHORT + FILTROS REFORZADOS",
        f"  SL={SL_ATR}xATR | RSI_L<{RSI_LONG} RSI_S>{RSI_SHORT} | Score>={SCORE_MIN} | R:R>={MIN_RR}",
        f"  Tendencia: basis slope {TREND_LOOKBACK}v ±{TREND_THRESHOLD}% | BBwidth>={BB_WIDTH_MIN_ATR}xATR",
        sep,
        f"  Balance inicial  : ${INITIAL_BAL:.2f}",
        f"  Balance final    : ${final_bal:.2f}   ROI: {roi:+.1f}%",
        f"  Total trades     : {total}  ({len(longs)} longs / {len(shorts)} shorts)",
        f"  Ganados/Perdidos : {len(wins)}/{len(losses)}   WR: {wr:.1f}%",
    ]
    if longs:
        lines.append(f"  LONG  : {len(longs):3d}tr  {wl}G {len(longs)-wl}P  WR:{wl/len(longs)*100:.0f}%  PnL:${pl:+.4f}")
    if shorts:
        lines.append(f"  SHORT : {len(shorts):3d}tr  {ws}G {len(shorts)-ws}P  WR:{ws/len(shorts)*100:.0f}%  PnL:${ps:+.4f}")
    lines += [
        f"  PnL total        : ${tp_sum:+.2f}",
        f"  Ganancia media   : ${aw:+.4f}",
        f"  Perdida media    : ${al:+.4f}",
        f"  Profit Factor    : {pf:.2f}",
        f"  Expectativa/trade: ${exp:+.4f}",
        f"  RSI medio entrada: {rsi_avg:.1f}",
        f"  BB Width medio   : {bbw_avg:.2f}x ATR",
        ""
    ]

    by_sym = {}
    for t in all_trades:
        s = t["symbol"]
        if s not in by_sym: by_sym[s] = {"n": 0, "w": 0, "pnl": 0}
        by_sym[s]["n"] += 1; by_sym[s]["pnl"] += t["pnl"]
        if t["result"] == "WIN": by_sym[s]["w"] += 1
    lines.append("  POR PAR:")
    for s, v in sorted(by_sym.items(), key=lambda x: -x[1]["pnl"]):
        wr_s = v["w"] / v["n"] * 100 if v["n"] else 0
        e = "+" if v["pnl"] >= 0 else "-"
        lines.append(f"  {e} {s:12s}  {v['n']:3d}tr  WR:{wr_s:.0f}%  ${v['pnl']:+.4f}")
    lines.append("")

    by_r = {}
    for t in all_trades:
        r2 = t["reason"]
        if r2 not in by_r: by_r[r2] = {"n": 0, "w": 0, "pnl": 0}
        by_r[r2]["n"] += 1; by_r[r2]["pnl"] += t["pnl"]
        if t["result"] == "WIN": by_r[r2]["w"] += 1
    lines.append("  POR RAZON CIERRE:")
    for r2, v in sorted(by_r.items(), key=lambda x: -x[1]["n"]):
        wr_r = v["w"] / v["n"] * 100 if v["n"] else 0
        lines.append(f"  {r2:8s}  {v['n']:3d}tr  WR:{wr_r:.0f}%  ${v['pnl']:+.4f}")
    lines.append("")

    by_trend = {}
    for t in all_trades:
        tr = t.get("trend","?")
        if tr not in by_trend: by_trend[tr] = {"n":0,"w":0,"pnl":0}
        by_trend[tr]["n"] += 1; by_trend[tr]["pnl"] += t["pnl"]
        if t["result"] == "WIN": by_trend[tr]["w"] += 1
    lines.append("  POR TENDENCIA AL ENTRAR:")
    for tr, v in sorted(by_trend.items()):
        wr_t = v["w"]/v["n"]*100 if v["n"] else 0
        e = "+" if v["pnl"] >= 0 else "-"
        lines.append(f"  {e} {tr:6s}  {v['n']:3d}tr  WR:{wr_t:.0f}%  ${v['pnl']:+.4f}")
    lines.append("")

    monthly = {}
    for t in sorted(all_trades, key=lambda x: x["date"]):
        m = t["date"][:7]
        if m not in monthly: monthly[m] = 0
        monthly[m] += t["pnl"]
    lines.append("  CURVA MENSUAL:")
    bal2 = INITIAL_BAL
    for m, pnl in sorted(monthly.items()):
        bal2 += pnl
        e = "+" if pnl >= 0 else "-"
        lines.append(f"  {e} {m}  ${pnl:>+7.4f}  Balance:${bal2:.2f}")
    lines.append("")

    # Diagnóstico vs v7
    lines.append("  COMPARATIVA v7 → v8:")
    lines.append(f"  WR   : 31.7% → {wr:.1f}%")
    lines.append(f"  PF   :  0.36 → {pf:.2f}")
    lines.append(f"  ROI  :  -8.5% → {roi:+.1f}%")
    lines.append(f"  Trades: 123 → {total}")
    lines.append("")

    if pf >= 1.5 and wr >= 50:   verdict = "RENTABLE"; tag = "OK"
    elif pf >= 1.2 and wr >= 45: verdict = "MARGINAL"; tag = "!!"
    else:                         verdict = "NO RENTABLE"; tag = "XX"
    lines.append(f"  [{tag}] {verdict} — PF={pf:.2f}  WR={wr:.1f}%  ROI={roi:+.1f}%")
    lines.append(sep)

    out = "\n".join(lines)
    print(out)
    with open("backtest_resultado_v8.txt", "w", encoding="utf-8") as f:
        f.write(out + f"\n\nGenerado: {datetime.now()}\n")
    print("\n  Guardado en: backtest_resultado_v8.txt")


def main():
    print("=" * 64)
    print("  BACKTEST v8 — FILTROS REFORZADOS (6 cambios sobre v7)")
    print(f"  SL={SL_ATR}xATR | RSI_L<{RSI_LONG} RSI_S>{RSI_SHORT} | Score>={SCORE_MIN} | R:R>={MIN_RR}")
    print(f"  Tendencia: basis slope {TREND_LOOKBACK}v ±{TREND_THRESHOLD}% | BBwidth>={BB_WIDTH_MIN_ATR}xATR")
    print("=" * 64)
    all_trades = []; balance = INITIAL_BAL
    for sym in SYMBOLS:
        df = fetch(sym)
        if df.empty: continue
        trades, balance = backtest(df, sym, balance)
        all_trades.extend(trades)
        w  = sum(1 for t in trades if t["result"] == "WIN")
        nl = sum(1 for t in trades if t["side"]   == "long")
        ns = sum(1 for t in trades if t["side"]   == "short")
        p  = sum(t["pnl"] for t in trades)
        print(f"  -> {len(trades)} trades ({nl}L/{ns}S)  {w}G {len(trades)-w}P  PnL:${p:+.4f}")
        time.sleep(0.5)
    print(f"\n  Total: {len(all_trades)} trades")
    report(all_trades, balance)

main()