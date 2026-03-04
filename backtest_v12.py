import requests, pandas as pd, numpy as np, time
from datetime import datetime

# ══════════════════════════════════════════════════════════
# BACKTEST v12 — ESCALAR EL SISTEMA RENTABLE
#
# ESTADO v11: [OK] RENTABLE — PF=1.83  WR=68.2%  ROI=+1.0%
# PROBLEMA v11: Solo 22 trades en 2 meses → frecuencia baja
#               para un bot en producción (objetivo: 40-60tr)
#
# DIAGNÓSTICO v11:
#   Vol alto: 20tr WR:70% +$1.10 → diferencia 20pp → FILTRO DURO ✓
#   INJ:       3tr WR:33% -$0.25 → eliminar
#   ARB:       0 trades → sin datos útiles, reemplazar
#   RSI medio entrada: 25.8 → señales muy raras en 1H
#   Solo "up" + RSI<30 + vol → muy restrictivo para 1H
#
# CAMBIOS v12:
#   [1] Volumen: bonus → FILTRO DURO (vol > vol_ma * 1.0)
#       Vol alto tuvo 20pp más WR → confirmado como señal real
#   [2] RSI_LONG 30→34 para generar más señales válidas
#       RSI<30 ocurre ~5% del tiempo en 1H
#       RSI<34 ocurre ~10% → dobla la frecuencia de señales
#   [3] Añadir timeframe 4H como confirmador de tendencia:
#       basis 4H subiendo → refuerza la tendencia 1H
#       Implementado como check simple (EWM sobre basis)
#   [4] SCORE_MIN 50→47 — compensar RSI más amplio
#       con barra de score ligeramente más baja
#   [5] Símbolos: eliminar INJ y ARB
#       Añadir SOL (volvió a funcionar en alcista), NEAR, SUI
#   [6] Trailing stop más agresivo tras parcial:
#       trail = price - a * 1.0 (era 1.2)
#       Protege más ganancias en tendencia fuerte
#   [MANTENER] SL_ATR=1.8 — 45% SL es sano
#   [MANTENER] Bloqueo flat + solo longs + BB_WIDTH>=1.5
# ══════════════════════════════════════════════════════════

BB_PERIOD        = 20
BB_SIGMA         = 2.0
RSI_PERIOD       = 14
RSI_LONG         = 34    # [2] era 30 — más señales
SL_ATR           = 1.8   # mantenido
PARTIAL_TP_ATR   = 2.0
LEVERAGE         = 2
RISK_PCT         = 0.02
INITIAL_BAL      = 100.0
SCORE_MIN_LONG   = 47    # [4] era 50
COOLDOWN_BARS    = 3
MIN_RR           = 1.5
TREND_LOOKBACK   = 8
TREND_THRESHOLD  = 0.25
BB_WIDTH_MIN_ATR = 1.5
VOL_LOOKBACK     = 20
VOL_MIN_MULT     = 1.0   # [1] filtro duro: vol >= 1.0x media

# [5] Sin INJ ni ARB. Añadidos SOL, NEAR, SUI
SYMBOLS = [
    "BTC-USDT",
    "ETH-USDT",
    "BNB-USDT",
    "LINK-USDT",
    "LTC-USDT",
    "OP-USDT",
    "ATOM-USDT",
    "UNI-USDT",
    "SOL-USDT",   # nuevo — buen historial alcista
    "NEAR-USDT",  # nuevo — buena reversión en 1H
    "SUI-USDT",   # nuevo — alta beta en alcista
    "TIA-USDT",   # nuevo — momentum consistente
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


# [3] Confirmador de tendencia 4H simulado sobre basis 1H
# Usa EWM de largo periodo sobre el basis — equivale a ver
# si la media de medias está subiendo (proxy de 4H)
def trend_4h_up(basis_series, i, span=40):
    """True si la tendencia de orden superior es alcista"""
    if i < span + 5: return True  # sin datos suficientes → no filtrar
    basis_slow = basis_series.ewm(span=span, adjust=False)
    vals = basis_slow.mean()
    if i < 2: return True
    return float(vals.iloc[i]) > float(vals.iloc[i - 3])


def calc_score_long(r, dv, mb, stv, vol_ok):
    s = 40
    if   r < 18: s += 35
    elif r < 22: s += 28
    elif r < 27: s += 20
    elif r < 30: s += 14
    elif r < 34: s += 8   # [2] nueva banda RSI 30-34
    if dv:    s += 18
    if mb:    s += 5
    if vol_ok: s += 8
    if stv is not None and not np.isnan(stv):
        if   stv < 10: s += 15
        elif stv < 20: s += 10
        elif stv < 30: s += 5
    return min(s, 100)


def backtest(df, sym, balance):
    if len(df) < BB_PERIOD + VOL_LOOKBACK + 30:
        return [], balance

    df = df.copy()
    basis = df["close"].rolling(BB_PERIOD).mean()
    std   = df["close"].rolling(BB_PERIOD).std()
    df["upper"]    = basis + BB_SIGMA * std
    df["basis"]    = basis
    df["lower"]    = basis - BB_SIGMA * std
    df["rsi"]      = rsi_calc(df["close"])
    df["atr"]      = atr_calc(df)
    df["macd"]     = macd_calc(df["close"])
    df["stoch"]    = stoch_rsi_calc(df["close"])
    df["bb_width"] = (df["upper"] - df["lower"]) / df["atr"]
    df["vol_ma"]   = df["volume"].rolling(VOL_LOOKBACK).mean()

    trades = []; pos = None; cool = 0

    for i in range(BB_PERIOD + VOL_LOOKBACK + 30, len(df)):
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
        # [1] Filtro duro de volumen
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
                # [6] Trailing más agresivo tras parcial
                trail = price - a * 1.0
                if trail > pos["sl"]: pos["sl"] = trail

            sl_hit  = price <= pos["sl"]
            tp_hit  = price >= pos["tp"]
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
                    "bbw": pos["bbw"], "t4h": pos["t4h"]
                })
                pos = None; cool = COOLDOWN_BARS
            continue

        if cool > 0: cool -= 1; continue

        # ── Filtros de mercado ──────────────────────────
        if bbw < BB_WIDTH_MIN_ATR: continue   # rango estrecho
        if trend != "up":          continue   # solo alcista
        if not vol_ok:             continue   # [1] filtro vol duro

        # [3] Confirmador 4H (no bloquea, anota para análisis)
        t4h = trend_4h_up(df["basis"], i)

        dv = divergence_bull(df["close"].iloc[max(0,i-8):i+1],
                             df["rsi"].iloc[max(0,i-8):i+1])

        touch_long = (price <= blo * 1.002) or \
                     (dv and r < RSI_LONG and price <= blo * 1.005)

        if touch_long and r < RSI_LONG and balance > 0:
            sc = calc_score_long(r, dv, mb, stv, vol_ok)
            if sc >= SCORE_MIN_LONG:
                # [3] Bonus R:R si 4H también alcista
                min_rr_adj = MIN_RR * (0.9 if t4h else 1.0)
                sl   = price - SL_ATR * a
                tp   = bhi
                tp_p = price + PARTIAL_TP_ATR * a
                if sl > 0 and tp > price and (price - sl) > 0:
                    rr_val = (tp - price) / (price - sl)
                    if rr_val >= min_rr_adj:
                        qty = (balance * RISK_PCT * LEVERAGE) / price
                        pos = {"entry": price, "sl": sl, "tp": tp,
                               "tp_p": tp_p, "qty": qty, "sc": sc,
                               "oi": i, "pd": False,
                               "rsi_e": round(r, 1),
                               "bbw": round(bbw, 2), "t4h": t4h}

    return trades, balance


def report(all_trades, final_bal):
    total = len(all_trades)
    if total == 0:
        print("\n  Sin trades. Considera bajar RSI_LONG o SCORE_MIN.")
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
        "  BB+RSI ELITE v12 — LONG ONLY | VOL FILTRO DURO",
        f"  SL={SL_ATR}xATR | RSI<{RSI_LONG} | Score>={SCORE_MIN_LONG} | R:R>={MIN_RR}",
        f"  Tendencia: {TREND_LOOKBACK}v ±{TREND_THRESHOLD}% | Vol>={VOL_MIN_MULT}xMA | BBw>={BB_WIDTH_MIN_ATR}xATR",
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

    # Por par
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

    # Por razón de cierre
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

    # [3] Breakdown 4H confirmador
    t4h_y = [t for t in all_trades if t.get("t4h")]
    t4h_n = [t for t in all_trades if not t.get("t4h")]
    lines.append("  TENDENCIA 4H CONFIRMA vs NO confirma:")
    if t4h_y:
        wr_y = sum(1 for t in t4h_y if t["result"]=="WIN")/len(t4h_y)*100
        pnl_y = sum(t["pnl"] for t in t4h_y)
        lines.append(f"  + 4H ok : {len(t4h_y):3d}tr  WR:{wr_y:.0f}%  ${pnl_y:+.4f}")
    if t4h_n:
        wr_n = sum(1 for t in t4h_n if t["result"]=="WIN")/len(t4h_n)*100
        pnl_n = sum(t["pnl"] for t in t4h_n)
        lines.append(f"  - 4H no : {len(t4h_n):3d}tr  WR:{wr_n:.0f}%  ${pnl_n:+.4f}")
    if t4h_y and t4h_n:
        wr_y2 = sum(1 for t in t4h_y if t["result"]=="WIN")/len(t4h_y)*100
        wr_n2 = sum(1 for t in t4h_n if t["result"]=="WIN")/len(t4h_n)*100
        diff = wr_y2 - wr_n2
        tag = "ACTIVAR como filtro duro en v13" if diff > 10 else "mantener como bonus"
        lines.append(f"  (Diferencia {diff:+.0f}pp → {tag})")
    lines.append("")

    # Curva mensual
    monthly = {}
    for t in sorted(all_trades, key=lambda x: x["date"]):
        m = t["date"][:7]
        if m not in monthly: monthly[m] = 0
        monthly[m] += t["pnl"]
    lines.append("  CURVA MENSUAL:")
    bal2 = INITIAL_BAL
    pos_months = neg_months = 0
    for m, pnl in sorted(monthly.items()):
        bal2 += pnl
        e = "+" if pnl >= 0 else "-"
        if pnl >= 0: pos_months += 1
        else: neg_months += 1
        lines.append(f"  {e} {m}  ${pnl:>+7.4f}  Balance:${bal2:.2f}")
    lines.append(f"  Meses positivos/negativos: {pos_months}/{neg_months}")
    lines.append("")

    lines.append("  COMPARATIVA v7→v8→v9→v10→v11→v12:")
    lines.append(f"  WR    : 31.7%→41.4%→33.3%→46.3%→68.2%→ {wr:.1f}%")
    lines.append(f"  PF    :  0.36→ 0.79→ 0.63→ 0.78→ 1.83→ {pf:.2f}")
    lines.append(f"  ROI   : -8.5%→-1.7%→-0.1%→-0.8%→+1.0%→ {roi:+.1f}%")
    lines.append(f"  Trades:  123 → 111 →   3 →  41 →  22 → {total}")
    lines.append("")

    sl_pct = sum(1 for t in all_trades if t["reason"]=="SL") / total * 100
    lines.append(f"  NOTA PARA v13:")
    lines.append(f"  → SL rate: {sl_pct:.0f}% (objetivo <60%)")
    lines.append(f"  → Trades generados: {total} (objetivo 40-60)")
    if total < 30:
        lines.append(f"  → Frecuencia baja → subir RSI_LONG a {RSI_LONG+2} en v13")
    elif total > 70:
        lines.append(f"  → Frecuencia alta → subir SCORE_MIN a {SCORE_MIN_LONG+3} en v13")
    else:
        lines.append(f"  → Frecuencia óptima ✓")
    lines.append("")

    if pf >= 1.5 and wr >= 55:   verdict = "RENTABLE"; tag = "OK"
    elif pf >= 1.2 and wr >= 48: verdict = "MARGINAL"; tag = "!!"
    else:                         verdict = "NO RENTABLE"; tag = "XX"
    lines.append(f"  [{tag}] {verdict} — PF={pf:.2f}  WR={wr:.1f}%  ROI={roi:+.1f}%")
    lines.append(sep)

    out = "\n".join(lines)
    print(out)
    with open("backtest_resultado_v12.txt", "w", encoding="utf-8") as f:
        f.write(out + f"\n\nGenerado: {datetime.now()}\n")
    print("\n  Guardado en: backtest_resultado_v12.txt")


def main():
    print("=" * 64)
    print("  BACKTEST v12 — ESCALAR SISTEMA RENTABLE v11")
    print(f"  SL={SL_ATR}xATR | RSI<{RSI_LONG} | Score>={SCORE_MIN_LONG} | R:R>={MIN_RR}")
    print(f"  Tendencia {TREND_LOOKBACK}v ±{TREND_THRESHOLD}% | Vol>={VOL_MIN_MULT}xMA(duro) | BBw>={BB_WIDTH_MIN_ATR}xATR")
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