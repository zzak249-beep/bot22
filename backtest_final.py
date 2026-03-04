import requests, pandas as pd, numpy as np, time
from datetime import datetime
from config import *   # lee todos los parámetros de config.py

# ══════════════════════════════════════════════════════
# backtest_final.py — MOTOR DEL BACKTEST
# Este archivo NUNCA cambia.
# Para probar variantes: edita solo config.py
#
# Uso:
#   notepad config.py       ← cambia parámetros
#   python backtest_final.py ← ejecuta
# ══════════════════════════════════════════════════════


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


def get_trend(basis_series, i):
    if i < TREND_LOOKBACK: return "flat"
    now  = float(basis_series.iloc[i])
    prev = float(basis_series.iloc[i - TREND_LOOKBACK])
    if now == 0 or prev == 0: return "flat"
    change_pct = (now - prev) / prev * 100
    if   change_pct >  TREND_THRESH: return "up"
    elif change_pct < -TREND_THRESH: return "down"
    else:                             return "flat"


def calc_score_long(r, dv, mb, stv):
    s = 40
    if   r < 20: s += 30
    elif r < 25: s += 22
    elif r < 28: s += 15
    elif r < 30: s += 12
    elif r < 32: s += 8
    if dv == "bull": s += 18
    if mb: s += 5
    if stv is not None and not np.isnan(stv):
        if   stv < 10: s += 15
        elif stv < 20: s += 10
        elif stv < 30: s += 5
    return min(s, 100)


def calc_score_short(r, dv, mb, stv):
    s = 40
    if   r > 80: s += 30
    elif r > 75: s += 22
    elif r > 72: s += 15
    elif r > 70: s += 12
    elif r > 68: s += 8
    if dv == "bear": s += 18
    if not mb: s += 5
    if stv is not None and not np.isnan(stv):
        if   stv > 90: s += 15
        elif stv > 80: s += 10
        elif stv > 70: s += 5
    return min(s, 100)


def backtest(df, sym, balance):
    if len(df) < max(BB_PERIOD, SMA_PERIOD) + 30:
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
    df["sma50"]  = df["close"].rolling(SMA_PERIOD).mean()

    trades = []
    pos    = None
    cool   = 0
    pending_long  = False
    pending_short = False
    pending_sc    = 0
    pending_trend = "flat"
    pending_rsi   = 0.0
    pending_sl    = 0.0

    for i in range(max(BB_PERIOD, SMA_PERIOD) + 30, len(df)):
        cur   = df.iloc[i]
        prev  = df.iloc[i-1]
        price = float(cur["close"])
        r     = float(cur["rsi"])   if not np.isnan(cur["rsi"])   else 50.0
        a     = float(cur["atr"])   if not np.isnan(cur["atr"])   else 0.0
        mb    = float(cur["macd"]) > 0 if not np.isnan(cur["macd"]) else True
        stv   = float(cur["stoch"]) if not np.isnan(cur["stoch"]) else 50.0
        sma   = float(cur["sma50"]) if not np.isnan(cur["sma50"]) else price
        if a <= 0: continue

        blo   = float(cur["lower"])
        bhi   = float(cur["upper"])
        bmid  = float(cur["basis"])
        trend = get_trend(df["basis"], i)

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
                trail = price - a * 1.5
                if trail > pos["sl"]: pos["sl"] = trail
            elif pos.get("pd") and side == "short":
                trail = price + a * 1.5
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
            if   sl_hit:                            exit_p = pos["sl"]; reason = "SL"
            elif tp_hit:                            exit_p = pos["tp"]; reason = "TP"
            elif sig_exit_long or sig_exit_short:   reason = "SIGNAL"

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
                    "trend": pos["trend"]
                })
                pos           = None
                cool          = COOLDOWN_BARS
                pending_long  = False
                pending_short = False
            continue

        if cool > 0: cool -= 1; continue

        if pending_long and price > blo and price > float(prev["close"]):
            sl = pending_sl
            tp = float(cur["upper"])
            tp_p = price + PARTIAL_TP_ATR * a
            if sl > 0 and tp > price and (price - sl) > 0:
                rr_val = (tp - price) / (price - sl)
                if rr_val >= MIN_RR and pending_sc >= SCORE_MIN and balance > 0:
                    qty = (balance * RISK_PCT * LEVERAGE) / price
                    pos = {"side": "long", "entry": price,
                           "sl": sl, "tp": tp, "tp_p": tp_p,
                           "qty": qty, "sc": pending_sc, "oi": i,
                           "pd": False, "rsi_e": pending_rsi,
                           "trend": pending_trend}
            pending_long = False
            if pos: continue

        if pending_short and price < bhi and price < float(prev["close"]):
            sl = pending_sl
            tp = float(cur["lower"])
            tp_p = price - PARTIAL_TP_ATR * a
            if sl > price and tp < price and (sl - price) > 0:
                rr_val = (price - tp) / (sl - price)
                if rr_val >= MIN_RR and pending_sc >= SCORE_MIN and balance > 0:
                    qty = (balance * RISK_PCT * LEVERAGE) / price
                    pos = {"side": "short", "entry": price,
                           "sl": sl, "tp": tp, "tp_p": tp_p,
                           "qty": qty, "sc": pending_sc, "oi": i,
                           "pd": False, "rsi_e": pending_rsi,
                           "trend": pending_trend}
            pending_short = False
            if pos: continue

        pending_long  = False
        pending_short = False

        dv = divergence(df["close"].iloc[max(0,i-8):i+1],
                        df["rsi"].iloc[max(0,i-8):i+1])

        if trend == "down":
            continue

        if price >= sma * 0.97:
            touch_long = (price <= blo * 1.002) or \
                         (dv == "bull" and r < RSI_LONG and price <= blo * 1.01)
            if touch_long and r < RSI_LONG:
                sc = calc_score_long(r, dv, mb, stv)
                if sc >= SCORE_MIN:
                    pending_long  = True
                    pending_sc    = sc
                    pending_trend = trend
                    pending_rsi   = round(r, 1)
                    pending_sl    = float(cur["low"]) * (1 - SL_BUFFER)

        if trend == "flat" and price <= sma * 1.03:
            touch_short = (price >= bhi * 0.998) or \
                          (dv == "bear" and r > RSI_SHORT and price >= bhi * 0.99)
            if touch_short and r > RSI_SHORT:
                sc = calc_score_short(r, dv, mb, stv)
                if sc >= SCORE_MIN:
                    pending_short = True
                    pending_sc    = sc
                    pending_trend = trend
                    pending_rsi   = round(r, 1)
                    pending_sl    = float(cur["high"]) * (1 + SL_BUFFER)

    return trades, balance


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
    be_wr  = abs(al) / (aw + abs(al)) * 100 if (aw + abs(al)) > 0 else 50

    wl = sum(1 for t in longs  if t["result"] == "WIN")
    ws = sum(1 for t in shorts if t["result"] == "WIN")
    pl = sum(t["pnl"] for t in longs)
    ps = sum(t["pnl"] for t in shorts)

    sep = "=" * 64
    lines = [
        sep,
        f"  BB+RSI ELITE {VERSION}",
        f"  RSI_L<{RSI_LONG} RSI_S>{RSI_SHORT} | Score>={SCORE_MIN} | R:R>={MIN_RR}",
        f"  Pares: {len(SYMBOLS)} | down=BLOQUEADO",
        sep,
        f"  Balance inicial  : ${INITIAL_BAL:.2f}",
        f"  Balance final    : ${final_bal:.2f}   ROI: {roi:+.1f}%",
        f"  Total trades     : {total}  ({len(longs)}L / {len(shorts)}S)",
        f"  Ganados/Perdidos : {len(wins)}/{len(losses)}   WR: {wr:.1f}%  (breakeven: {be_wr:.1f}%)",
    ]
    if longs:
        lines.append(f"  LONG  : {len(longs):3d}tr  {wl}G {len(longs)-wl}P  WR:{wl/len(longs)*100:.0f}%  PnL:${pl:+.4f}")
    if shorts:
        lines.append(f"  SHORT : {len(shorts):3d}tr  {ws}G {len(shorts)-ws}P  WR:{ws/len(shorts)*100:.0f}%  PnL:${ps:+.4f}")
    lines += [
        f"  PnL total        : ${tp_sum:+.2f}",
        f"  Ganancia media   : ${aw:+.4f}",
        f"  Perdida media    : ${al:+.4f}",
        f"  R:R real         : {abs(aw/al):.2f}" if al != 0 else "  R:R real         : inf",
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
    lines.append("  POR RAZON CIERRE:")
    for r2, v in sorted(by_r.items(), key=lambda x: -x[1]["n"]):
        wr_r = v["w"] / v["n"] * 100 if v["n"] else 0
        lines.append(f"  {r2:8s}  {v['n']:3d}tr  WR:{wr_r:.0f}%  ${v['pnl']:+.4f}")
    lines.append("")

    by_trend = {}
    for t in all_trades:
        tr = t.get("trend", "?")
        if tr not in by_trend: by_trend[tr] = {"n": 0, "w": 0, "pnl": 0}
        by_trend[tr]["n"] += 1; by_trend[tr]["pnl"] += t["pnl"]
        if t["result"] == "WIN": by_trend[tr]["w"] += 1
    lines.append("  POR TENDENCIA:")
    for tr, v in sorted(by_trend.items()):
        wr_t = v["w"] / v["n"] * 100 if v["n"] else 0
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

    if pf >= 1.5 and wr >= 45:   verdict = "RENTABLE"; tag = "OK"
    elif pf >= 1.2 and wr >= 38: verdict = "MARGINAL"; tag = "!!"
    elif pf >= 1.0:               verdict = "BREAKEVEN"; tag = "~"
    else:                         verdict = "NO RENTABLE"; tag = "XX"
    lines.append(f"  [{tag}] {verdict} — PF={pf:.2f}  WR={wr:.1f}%  ROI={roi:+.1f}%")
    lines.append(sep)

    out = "\n".join(lines)
    print(out)
    fname = f"resultado_{VERSION}.txt"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(out + f"\n\nGenerado: {datetime.now()}\n")
    print(f"\n  Guardado en: {fname}")


def main():
    print("=" * 64)
    print(f"  BACKTEST {VERSION} — BingX 1h")
    print(f"  RSI_L<{RSI_LONG} RSI_S>{RSI_SHORT} | Score>={SCORE_MIN} | R:R>={MIN_RR}")
    print(f"  Pares activos: {', '.join(SYMBOLS)}")
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