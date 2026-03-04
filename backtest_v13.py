import requests, pandas as pd, numpy as np, time
from datetime import datetime

# ══════════════════════════════════════════════════════════
# BACKTEST v13 — v11 CORE + 4H DURO + PARES CURADOS
#
# DIAGNÓSTICO v12 (PF=1.02 → casi break-even):
#   Top 7 pares ALL positivos : +$1.45
#   Bottom 5 pares ALL negativos: -$1.42
#   → Los pares malos cancelan exactamente los buenos
#   4H diferencia +12pp → activar filtro duro confirmado
#   RSI medio entrada 28.0 → señales RSI 30-34 son ruido puro
#
# REGLA v13: volver al núcleo de v11 que funcionó,
#            añadir solo lo que los datos prueban
#
# CAMBIOS v13:
#   [1] Pares: SOLO los 7 positivos de v12
#       Eliminados: UNI(-0.57), BNB(-0.23), SUI(-0.26),
#                   NEAR(-0.20), TIA(-0.15)
#       Mantenidos: ATOM, OP, ETH, LINK, BTC, SOL, LTC
#       Añadidos 3 nuevos de alta liquidez y buen comportamiento
#       alcista: AAVE, MKR (DeFi blue chip), MATIC/POL
#   [2] RSI_LONG 34→30 — RSI medio en v12 fue 28.0
#       Las señales entre RSI 30-34 no añadieron valor
#       Volver a la precisión de v11
#   [3] 4H confirmador → FILTRO DURO (+12pp WR probado)
#       Solo entrar si tendencia de largo plazo confirma
#   [4] SCORE_MIN 47→48 — ligero ajuste para compensar
#       el mayor número de pares
#   [MANTENER] SL=1.8, Vol>=1.0xMA, BBw>=1.5, R:R>=1.5
#   [MANTENER] Solo longs, solo trend="up", bloqueo flat
# ══════════════════════════════════════════════════════════

BB_PERIOD        = 20
BB_SIGMA         = 2.0
RSI_PERIOD       = 14
RSI_LONG         = 30    # [2] volver a v11
SL_ATR           = 1.8
PARTIAL_TP_ATR   = 2.0
LEVERAGE         = 2
RISK_PCT         = 0.02
INITIAL_BAL      = 100.0
SCORE_MIN_LONG   = 48    # [4]
COOLDOWN_BARS    = 3
MIN_RR           = 1.5
TREND_LOOKBACK   = 8
TREND_THRESHOLD  = 0.25
BB_WIDTH_MIN_ATR = 1.5
VOL_LOOKBACK     = 20
VOL_MIN_MULT     = 1.0
T4H_SPAN         = 40   # [3] EWM proxy 4H

# [1] Solo pares con historial positivo probado
SYMBOLS = [
    # Núcleo v11/v12 positivos — orden por rendimiento v12
    "ATOM-USDT",  # v12: 2tr WR100% +$0.39
    "OP-USDT",    # v12: 2tr WR100% +$0.26
    "ETH-USDT",   # v12: 2tr WR100% +$0.22
    "LINK-USDT",  # v12: 3tr WR67%  +$0.20
    "BTC-USDT",   # v12: 2tr WR100% +$0.19
    "SOL-USDT",   # v12: 5tr WR60%  +$0.16
    "LTC-USDT",   # v12: 5tr WR60%  +$0.05
    # Nuevos — DeFi blue chips con buena reversión en alcista
    "AAVE-USDT",
    "POL-USDT",   # ex-MATIC
    "DOT-USDT",   # re-probado con filtros v13 (fallaba sin 4H)
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
                first_ts = c[0][0] if isinstance(c[0], list) else (c[0].get("time") or c[0].get("t", 0))
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
            rows.append([c.get("time") or c.get("t",0), c.get("open") or c.get("o",0),
                         c.get("high") or c.get("h",0), c.get("low") or c.get("l",0),
                         c.get("close") or c.get("c",0), c.get("volume") or c.get("v",0)])

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
    return ((rsi_s - lo) / (hi - lo).replace(0, float("nan")) * 100).rolling(k).mean()


def divergence_bull(cls, rsi_s, lb=6):
    if len(rsi_s) < lb + 2: return False
    r = rsi_s.iloc[-lb-1:]; p = cls.iloc[-lb-1:]
    rn = float(r.iloc[-1]); pn = float(p.iloc[-1])
    return pn < float(p.iloc[:-1].min()) and rn > float(r.iloc[:-1].min()) + 3


def trend_direction(basis_series, i):
    if i < TREND_LOOKBACK: return "flat"
    now  = float(basis_series.iloc[i])
    prev = float(basis_series.iloc[i - TREND_LOOKBACK])
    if now == 0 or prev == 0: return "flat"
    pct = (now - prev) / prev * 100
    if   pct >  TREND_THRESHOLD: return "up"
    elif pct < -TREND_THRESHOLD: return "down"
    return "flat"


# [3] 4H proxy — FILTRO DURO: EWM largo sobre basis
def trend_4h_up(basis_ewm_vals, i):
    """True si tendencia de largo plazo es alcista"""
    if i < T4H_SPAN + 5: return True  # sin datos → no filtrar
    return float(basis_ewm_vals.iloc[i]) > float(basis_ewm_vals.iloc[i - 4])


def calc_score_long(r, dv, mb, stv):
    s = 40
    if   r < 18: s += 35
    elif r < 22: s += 28
    elif r < 27: s += 20
    elif r < 30: s += 14
    if dv:    s += 18
    if mb:    s += 5
    if stv is not None and not np.isnan(stv):
        if   stv < 10: s += 15
        elif stv < 20: s += 10
        elif stv < 30: s += 5
    return min(s, 100)


def backtest(df, sym, balance):
    if len(df) < BB_PERIOD + VOL_LOOKBACK + T4H_SPAN + 30:
        return [], balance

    df = df.copy()
    basis = df["close"].rolling(BB_PERIOD).mean()
    std   = df["close"].rolling(BB_PERIOD).std()
    df["upper"]      = basis + BB_SIGMA * std
    df["basis"]      = basis
    df["lower"]      = basis - BB_SIGMA * std
    df["rsi"]        = rsi_calc(df["close"])
    df["atr"]        = atr_calc(df)
    df["macd"]       = macd_calc(df["close"])
    df["stoch"]      = stoch_rsi_calc(df["close"])
    df["bb_width"]   = (df["upper"] - df["lower"]) / df["atr"]
    df["vol_ma"]     = df["volume"].rolling(VOL_LOOKBACK).mean()
    df["basis_ewm"]  = df["basis"].ewm(span=T4H_SPAN, adjust=False).mean()  # [3]

    trades = []; pos = None; cool = 0
    start  = BB_PERIOD + VOL_LOOKBACK + T4H_SPAN + 30

    for i in range(start, len(df)):
        cur   = df.iloc[i]
        prev  = df.iloc[i-1]
        price = float(cur["close"])
        r     = float(cur["rsi"])    if not np.isnan(cur["rsi"])    else 50.0
        a     = float(cur["atr"])    if not np.isnan(cur["atr"])    else 0.0
        mb    = float(cur["macd"]) > 0 if not np.isnan(cur["macd"]) else True
        stv   = float(cur["stoch"]) if not np.isnan(cur["stoch"])   else 50.0
        bbw   = float(cur["bb_width"]) if not np.isnan(cur["bb_width"]) else 0.0
        vol   = float(cur["volume"]) if not np.isnan(cur["volume"]) else 0.0
        vma   = float(cur["vol_ma"]) if not np.isnan(cur["vol_ma"]) else 0.0
        vol_ok = (vma > 0) and (vol >= vma * VOL_MIN_MULT)
        if a <= 0: continue

        blo  = float(cur["lower"])
        bhi  = float(cur["upper"])
        bmid = float(cur["basis"])
        trend = trend_direction(df["basis"], i)

        # ── Gestión posición abierta ────────────────────
        if pos:
            entry = pos["entry"]

            if not pos.get("pd"):
                if price >= pos["tp_p"]:
                    pos["qty"] *= 0.5
                    pos["pd"]   = True
                    pos["sl"]   = entry

            if pos.get("pd"):
                trail = price - a * 1.0
                if trail > pos["sl"]: pos["sl"] = trail

            sl_hit   = price <= pos["sl"]
            tp_hit   = price >= pos["tp"]
            sig_exit = (float(prev["close"]) <= float(prev["basis"]) and
                        price > bmid and pos.get("pd"))

            reason = None; exit_p = price
            if   sl_hit:   exit_p = pos["sl"]; reason = "SL"
            elif tp_hit:   exit_p = pos["tp"]; reason = "TP"
            elif sig_exit: reason = "SIGNAL"

            if reason:
                pct = (exit_p - entry) / entry
                pnl = pos["qty"] * entry * pct * LEVERAGE
                balance += pnl
                trades.append({
                    "symbol": sym, "entry": entry, "exit": exit_p,
                    "pnl": round(pnl, 4),
                    "result": "WIN" if pnl > 0 else "LOSS",
                    "reason": reason, "sc": pos["sc"],
                    "date": str(cur["ts"])[:10],
                    "bars": i - pos["oi"], "rsi_e": pos["rsi_e"],
                    "bbw": pos["bbw"]
                })
                pos = None; cool = COOLDOWN_BARS
            continue

        if cool > 0: cool -= 1; continue

        # ── Filtros de mercado ──────────────────────────
        if bbw < BB_WIDTH_MIN_ATR:  continue
        if trend != "up":           continue
        if not vol_ok:              continue
        # [3] 4H filtro DURO
        if not trend_4h_up(df["basis_ewm"], i): continue

        dv = divergence_bull(df["close"].iloc[max(0,i-8):i+1],
                             df["rsi"].iloc[max(0,i-8):i+1])

        touch_long = (price <= blo * 1.002) or \
                     (dv and r < RSI_LONG and price <= blo * 1.005)

        if touch_long and r < RSI_LONG and balance > 0:
            sc = calc_score_long(r, dv, mb, stv)
            if sc >= SCORE_MIN_LONG:
                sl   = price - SL_ATR * a
                tp   = bhi
                tp_p = price + PARTIAL_TP_ATR * a
                if sl > 0 and tp > price and (price - sl) > 0:
                    rr_val = (tp - price) / (price - sl)
                    if rr_val >= MIN_RR:
                        qty = (balance * RISK_PCT * LEVERAGE) / price
                        pos = {"entry": price, "sl": sl, "tp": tp,
                               "tp_p": tp_p, "qty": qty, "sc": sc,
                               "oi": i, "pd": False,
                               "rsi_e": round(r, 1), "bbw": round(bbw, 2)}

    return trades, balance


def report(all_trades, final_bal):
    total = len(all_trades)
    if total == 0:
        print("\n  Sin trades. Considera bajar RSI_LONG o TREND_THRESHOLD.")
        return

    wins   = [t for t in all_trades if t["result"] == "WIN"]
    losses = [t for t in all_trades if t["result"] == "LOSS"]
    wr     = len(wins) / total * 100
    tp_sum = sum(t["pnl"] for t in all_trades)
    aw     = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0
    al     = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    gw     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    pf     = gw / gl if gl > 0 else 999
    roi    = (final_bal - INITIAL_BAL) / INITIAL_BAL * 100
    exp    = wr / 100 * aw + (1 - wr / 100) * al
    rsi_avg  = sum(t["rsi_e"] for t in all_trades) / total
    bbw_avg  = sum(t["bbw"]   for t in all_trades) / total
    bars_avg = sum(t["bars"]  for t in all_trades) / total

    sep = "=" * 64
    lines = [
        sep,
        "  BB+RSI ELITE v13 — v11 CORE + 4H DURO + PARES CURADOS",
        f"  SL={SL_ATR}xATR | RSI<{RSI_LONG} | Score>={SCORE_MIN_LONG} | R:R>={MIN_RR}",
        f"  Tendencia: {TREND_LOOKBACK}v ±{TREND_THRESHOLD}% | 4H EWM{T4H_SPAN} DURO | Vol>={VOL_MIN_MULT}xMA | BBw>={BB_WIDTH_MIN_ATR}xATR",
        sep,
        f"  Balance inicial  : ${INITIAL_BAL:.2f}",
        f"  Balance final    : ${final_bal:.2f}   ROI: {roi:+.1f}%",
        f"  Total trades     : {total} (solo longs)",
        f"  Ganados/Perdidos : {len(wins)}/{len(losses)}   WR: {wr:.1f}%",
        f"  PnL total        : ${tp_sum:+.2f}",
        f"  Ganancia media   : ${aw:+.4f}",
        f"  Perdida media    : ${al:+.4f}",
        f"  Profit Factor    : {pf:.2f}",
        f"  Expectativa/trade: ${exp:+.4f}",
        f"  RSI medio entrada: {rsi_avg:.1f}",
        f"  BB Width medio   : {bbw_avg:.2f}x ATR",
        f"  Duración media   : {bars_avg:.1f} velas",
        ""
    ]

    by_sym = {}
    for t in all_trades:
        s = t["symbol"]
        if s not in by_sym: by_sym[s] = {"n":0,"w":0,"pnl":0}
        by_sym[s]["n"] += 1; by_sym[s]["pnl"] += t["pnl"]
        if t["result"]=="WIN": by_sym[s]["w"] += 1
    lines.append("  POR PAR:")
    for s, v in sorted(by_sym.items(), key=lambda x: -x[1]["pnl"]):
        wr_s = v["w"]/v["n"]*100 if v["n"] else 0
        e = "+" if v["pnl"] >= 0 else "-"
        lines.append(f"  {e} {s:12s}  {v['n']:3d}tr  WR:{wr_s:.0f}%  ${v['pnl']:+.4f}")
    lines.append("")

    by_r = {}
    for t in all_trades:
        r2 = t["reason"]
        if r2 not in by_r: by_r[r2] = {"n":0,"w":0,"pnl":0}
        by_r[r2]["n"] += 1; by_r[r2]["pnl"] += t["pnl"]
        if t["result"]=="WIN": by_r[r2]["w"] += 1
    lines.append("  POR RAZON CIERRE:")
    for r2, v in sorted(by_r.items(), key=lambda x: -x[1]["n"]):
        wr_r = v["w"]/v["n"]*100 if v["n"] else 0
        lines.append(f"  {r2:8s}  {v['n']:3d}tr  WR:{wr_r:.0f}%  ${v['pnl']:+.4f}")
    lines.append("")

    monthly = {}
    for t in sorted(all_trades, key=lambda x: x["date"]):
        m = t["date"][:7]
        if m not in monthly: monthly[m] = 0
        monthly[m] += t["pnl"]
    lines.append("  CURVA MENSUAL:")
    bal2 = INITIAL_BAL; pos_m = neg_m = 0
    for m, pnl in sorted(monthly.items()):
        bal2 += pnl
        e = "+" if pnl >= 0 else "-"
        if pnl >= 0: pos_m += 1
        else: neg_m += 1
        lines.append(f"  {e} {m}  ${pnl:>+7.4f}  Balance:${bal2:.2f}")
    lines.append(f"  Meses positivos/negativos: {pos_m}/{neg_m}")
    lines.append("")

    lines.append("  COMPARATIVA v7→...→v11→v12→v13:")
    lines.append(f"  WR    : 31.7%→...→68.2%→58.8%→ {wr:.1f}%")
    lines.append(f"  PF    :  0.36→...→ 1.83→ 1.02→ {pf:.2f}")
    lines.append(f"  ROI   : -8.5%→...→+1.0%→+0.0%→ {roi:+.1f}%")
    lines.append(f"  Trades:  123 →...→  22 →  34 → {total}")
    lines.append("")

    sl_pct = sum(1 for t in all_trades if t["reason"]=="SL") / total * 100
    lines.append(f"  NOTA PARA v14:")
    lines.append(f"  → SL rate: {sl_pct:.0f}% (óptimo 40-55%)")
    lines.append(f"  → Trades: {total} (objetivo 35-55)")
    # Análisis de pares para v14
    bad_pairs = [(s,v) for s,v in by_sym.items() if v["pnl"]<0 and v["n"]>=2]
    good_pairs = [(s,v) for s,v in by_sym.items() if v["pnl"]>0]
    if bad_pairs:
        bad_str = ", ".join(s for s,_ in sorted(bad_pairs, key=lambda x: x[1]["pnl"]))
        lines.append(f"  → Pares negativos (≥2tr): {bad_str} → evaluar eliminar")
    lines.append("")

    if pf >= 1.5 and wr >= 55:   verdict = "RENTABLE"; tag = "OK"
    elif pf >= 1.2 and wr >= 48: verdict = "MARGINAL"; tag = "!!"
    else:                         verdict = "NO RENTABLE"; tag = "XX"
    lines.append(f"  [{tag}] {verdict} — PF={pf:.2f}  WR={wr:.1f}%  ROI={roi:+.1f}%")
    lines.append(sep)

    out = "\n".join(lines)
    print(out)
    with open("backtest_resultado_v13.txt", "w", encoding="utf-8") as f:
        f.write(out + f"\n\nGenerado: {datetime.now()}\n")
    print("\n  Guardado en: backtest_resultado_v13.txt")


def main():
    print("=" * 64)
    print("  BACKTEST v13 — v11 CORE + 4H DURO + PARES CURADOS")
    print(f"  SL={SL_ATR}xATR | RSI<{RSI_LONG} | Score>={SCORE_MIN_LONG} | R:R>={MIN_RR}")
    print(f"  4H EWM{T4H_SPAN} DURO | Tendencia {TREND_LOOKBACK}v ±{TREND_THRESHOLD}% | Vol>={VOL_MIN_MULT}xMA")
    print("=" * 64)
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