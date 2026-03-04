import requests, pandas as pd, numpy as np, time
from datetime import datetime

# ══════════════════════════════════════════════════════════
# BACKTEST v4.1 — Fix EMA filter demasiado agresivo
#
# PROBLEMA v4: 0 trades
#   El filtro EMA50 (price >= ema*0.97) bloqueaba TODO
#   porque en Ene-Mar 2026 crypto estaba en bajada fuerte
#   y el precio estaba consistentemente >3% bajo EMA50
#
# FIXES v4.1:
#   ✅ EMA filter relajado: ema*0.93 (7% margen)
#   ✅ VOL_FACTOR = 0.5 (más permisivo)
#   ✅ MIN_RR = 1.3 (menos restrictivo)
#   ✅ Resto igual que v4
# ══════════════════════════════════════════════════════════

BB_PERIOD      = 20
BB_SIGMA       = 2.0
RSI_PERIOD     = 14
RSI_ENTRY      = 50
SL_ATR         = 2.5
PARTIAL_TP_ATR = 2.5
LEVERAGE       = 2
RISK_PCT       = 0.02
INITIAL_BAL    = 100.0
SCORE_MIN      = 42
COOLDOWN_BARS  = 3
MIN_RR         = 1.3      # FIX: era 1.5 → 1.3
EMA_TREND      = 50
VOL_FACTOR     = 0.5      # FIX: era 0.8 → 0.5
EMA_MARGIN     = 0.93     # FIX: era 0.97 → 0.93 (7% margen)

SYMBOLS = [
    "BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
    "ADA-USDT","DOGE-USDT","AVAX-USDT","LINK-USDT","DOT-USDT"
]


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
                first_ts = c[0][0] if isinstance(c[0], list) else c[0].get("t", c[0].get("time", 0))
                end = int(first_ts) - 1
                time.sleep(0.3); success = True
            except Exception as e:
                print(f" ERR:{e}", end=""); break
        if success and all_c: break
    if not all_c: print(" sin datos"); return pd.DataFrame()
    rows = []
    for c in all_c:
        if isinstance(c, list): rows.append(c[:6])
        else: rows.append([c.get("t") or c.get("time",0), c.get("o",0),
                           c.get("h",0), c.get("l",0), c.get("c",0), c.get("v",0)])
    df = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"])
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["ts"] = pd.to_datetime(pd.to_numeric(df["ts"], errors="coerce"), unit="ms")
    df = df.dropna().sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if df.empty: print(" sin datos válidos"); return pd.DataFrame()
    print(f" {len(df)} velas ({str(df['ts'].iloc[0])[:10]} -> {str(df['ts'].iloc[-1])[:10]})")
    return df


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


def calc_score(r, dv, mb, stv):
    s = 40
    if   r < 20: s += 30
    elif r < 25: s += 22
    elif r < 30: s += 15
    elif r < 35: s += 10
    elif r < 40: s += 7
    elif r < 45: s += 5
    elif r < 50: s += 2
    if dv == "bull": s += 18
    if mb: s += 7
    if stv is not None and not np.isnan(stv):
        if   stv < 10: s += 15
        elif stv < 20: s += 10
        elif stv < 30: s += 5
    return min(s, 100)


def backtest(df, sym, balance):
    if len(df) < max(BB_PERIOD, EMA_TREND) + 30:
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
    df["ema50"]  = df["close"].ewm(span=EMA_TREND, adjust=False).mean()
    df["vol_ma"] = df["volume"].rolling(20).mean()

    trades = []; pos = None; cool = 0

    for i in range(max(BB_PERIOD, EMA_TREND) + 30, len(df)):
        cur  = df.iloc[i]
        prev = df.iloc[i-1]
        price = float(cur["close"])
        r   = float(cur["rsi"])   if not np.isnan(cur["rsi"])   else 50.0
        a   = float(cur["atr"])   if not np.isnan(cur["atr"])   else 0.0
        mb  = float(cur["macd"]) > 0 if not np.isnan(cur["macd"]) else True
        stv = float(cur["stoch"]) if not np.isnan(cur["stoch"]) else 50.0
        if a <= 0: continue

        blo  = float(cur["lower"])
        bhi  = float(cur["upper"])
        bmid = float(cur["basis"])
        ema  = float(cur["ema50"])  if not np.isnan(cur["ema50"])  else price
        vol  = float(cur["volume"])
        vol_ma = float(cur["vol_ma"]) if not np.isnan(cur["vol_ma"]) else vol

        if pos:
            side  = pos["side"]
            entry = pos["entry"]

            if not pos.get("pd"):
                hp = (side == "long"  and price >= pos["tp_p"]) or \
                     (side == "short" and price <= pos["tp_p"])
                if hp:
                    pos["qty"] *= 0.5
                    pos["pd"]   = True
                    pos["sl"]   = entry

            if pos.get("pd") and side == "long":
                trail = price - a * 1.2
                if trail > pos["sl"]: pos["sl"] = trail
            elif pos.get("pd") and side == "short":
                trail = price + a * 1.2
                if trail < pos["sl"]: pos["sl"] = trail

            sl_hit = (side == "long"  and price <= pos["sl"]) or \
                     (side == "short" and price >= pos["sl"])
            tp_hit = (side == "long"  and price >= pos["tp"]) or \
                     (side == "short" and price <= pos["tp"])
            sig_exit = (side == "long" and
                        float(prev["close"]) <= float(prev["basis"]) and
                        price > bmid and pos.get("pd"))

            reason = None; exit_p = price
            if   sl_hit:   exit_p = pos["sl"]; reason = "SL"
            elif tp_hit:   exit_p = pos["tp"]; reason = "TP"
            elif sig_exit: reason = "SIGNAL"

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
                    "bars": i - pos["oi"], "rsi_e": pos["rsi_e"]
                })
                pos  = None
                cool = COOLDOWN_BARS
            continue

        if cool > 0: cool -= 1; continue

        dv    = divergence(df["close"].iloc[max(0,i-8):i+1],
                           df["rsi"].iloc[max(0,i-8):i+1])
        touch = (price <= blo * 1.003) or \
                (dv == "bull" and r < 50 and price <= blo * 1.012)

        if touch and r < RSI_ENTRY and balance > 0:

            # Filtro EMA: máx 7% bajo EMA50 (antes era 3% → bloqueaba todo)
            trend_ok = price >= ema * EMA_MARGIN

            # Filtro volumen: 50% de media (antes era 80%)
            vol_ok = vol >= vol_ma * VOL_FACTOR

            if not trend_ok or not vol_ok:
                continue

            sc = calc_score(r, dv, mb, stv)
            if sc >= SCORE_MIN:
                sl   = price - SL_ATR * a
                tp   = bhi
                tp_p = price + PARTIAL_TP_ATR * a

                if sl > 0 and tp > price and (price - sl) > 0:
                    rr_val = (tp - price) / (price - sl)
                    if rr_val >= MIN_RR:
                        qty = (balance * RISK_PCT * LEVERAGE) / price
                        pos = {
                            "side": "long", "entry": price,
                            "sl": sl, "tp": tp, "tp_p": tp_p,
                            "qty": qty, "sc": sc, "oi": i,
                            "pd": False, "rsi_e": round(r, 1)
                        }

    return trades, balance


def report(all_trades, final_bal):
    if not all_trades:
        print(f"\n  Sin trades generados.")
        return
    wins   = [t for t in all_trades if t["result"] == "WIN"]
    losses = [t for t in all_trades if t["result"] == "LOSS"]
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

    sep = "=" * 60
    lines = [
        sep,
        "  BB+RSI ELITE v4.1 — BACKTEST CORREGIDO",
        f"  SL={SL_ATR}xATR | RSI<{RSI_ENTRY} | Score≥{SCORE_MIN} | R:R≥{MIN_RR}",
        f"  EMA{EMA_TREND} margen={int((1-EMA_MARGIN)*100)}% | Vol≥{int(VOL_FACTOR*100)}% media",
        sep,
        f"  Balance inicial  : ${INITIAL_BAL:.2f}",
        f"  Balance final    : ${final_bal:.2f}   ROI: {roi:+.1f}%",
        f"  Total trades     : {total}",
        f"  Ganados/Perdidos : {len(wins)}/{len(losses)}   WR: {wr:.1f}%",
        f"  PnL total        : ${tp_sum:+.2f}",
        f"  Ganancia media   : ${aw:+.4f}",
        f"  Pérdida media    : ${al:+.4f}",
        f"  Profit Factor    : {pf:.2f}",
        f"  Expectativa/trade: ${exp:+.4f}",
        f"  RSI medio entrada: {rsi_avg:.1f}",
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
    lines.append("  POR RAZÓN CIERRE:")
    for r2, v in sorted(by_r.items(), key=lambda x: -x[1]["n"]):
        wr_r = v["w"] / v["n"] * 100 if v["n"] else 0
        lines.append(f"  {r2:8s}  {v['n']:3d}tr  WR:{wr_r:.0f}%  ${v['pnl']:+.4f}")
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

    if pf >= 1.5 and wr >= 50:   verdict = "RENTABLE ✓";    tag = "OK"
    elif pf >= 1.2 and wr >= 45: verdict = "MARGINAL ~";    tag = "!!"
    else:                         verdict = "NO RENTABLE ✗"; tag = "XX"
    lines.append(f"  [{tag}] {verdict} — PF={pf:.2f}  WR={wr:.1f}%  ROI={roi:+.1f}%")
    lines.append(sep)

    out = "\n".join(lines)
    print(out)
    with open("backtest_resultado_v4.txt", "w", encoding="utf-8") as f:
        f.write(out + f"\n\nGenerado: {datetime.now()}\n")
    print("\n  ✅ Guardado en: backtest_resultado_v4.txt")


def main():
    print("=" * 60)
    print("  BACKTEST v4.1 — BingX 1h (~83 días)")
    print(f"  SL={SL_ATR}xATR | RSI<{RSI_ENTRY} | Score≥{SCORE_MIN} | R:R≥{MIN_RR}")
    print(f"  EMA{EMA_TREND} margen={int((1-EMA_MARGIN)*100)}% | Vol≥{int(VOL_FACTOR*100)}%")
    print("=" * 60)
    all_trades = []; balance = INITIAL_BAL
    for sym in SYMBOLS:
        df = fetch(sym)
        if df.empty: continue
        trades, balance = backtest(df, sym, balance)
        all_trades.extend(trades)
        w = sum(1 for t in trades if t["result"] == "WIN")
        p = sum(t["pnl"] for t in trades)
        print(f"  -> {len(trades)} trades  {w}G {len(trades)-w}P  PnL:${p:+.4f}")
        time.sleep(0.5)
    print(f"\n  Total: {len(all_trades)} trades")
    report(all_trades, balance)

main()