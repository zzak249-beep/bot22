import requests, pandas as pd, numpy as np, time
from datetime import datetime

# ══════════════════════════════════════════════════════
# scanner_pares.py — ESCANER COMPLETO DE PARES BINGX
#
# Descarga TODOS los pares USDT disponibles en BingX,
# corre el backtest con los parámetros óptimos (v15),
# y devuelve un ranking de los mejores para config.py
#
# Uso:
#   python scanner_pares.py
#
# Output:
#   - Ranking por pantalla ordenado por PF
#   - ranking_pares.txt con resultados completos
#   - config_recomendado.py listo para copiar
# ══════════════════════════════════════════════════════

# ── Parámetros del backtest (v15 óptimos) ─────────────
BB_PERIOD        = 20
BB_SIGMA         = 2.0
RSI_PERIOD       = 14
RSI_LONG         = 30
SL_BUFFER        = 0.003
PARTIAL_TP_ATR   = 2.0
LEVERAGE         = 2
RISK_PCT         = 0.02
INITIAL_BAL      = 100.0
SCORE_MIN        = 48
COOLDOWN_BARS    = 3
MIN_RR           = 1.5
TREND_LOOKBACK   = 8
TREND_THRESH     = 0.25
SMA_PERIOD       = 50
LONG_ONLY_UP     = True

# ── Filtros de selección ───────────────────────────────
MIN_TRADES       = 3      # mínimo de trades para considerar el par
MIN_WR           = 50.0   # WR mínimo para aparecer en top
MIN_PF           = 1.2    # Profit Factor mínimo
TOP_N            = 15     # cuántos pares mostrar en el top


# ══════════════════════════════════════════════════════
# OBTENER TODOS LOS PARES DE BINGX
# ══════════════════════════════════════════════════════

def get_all_symbols():
    """Descarga todos los pares perpetuos USDT de BingX"""
    urls = [
        "https://open-api.bingx.com/openApi/swap/v2/quote/contracts",
        "https://open-api.bingx.com/openApi/contract/v1/allContracts",
    ]
    symbols = []
    for url in urls:
        try:
            r = requests.get(url, timeout=15).json()
            data = r if isinstance(r, list) else r.get("data", [])
            if not data:
                continue
            for item in data:
                sym = item.get("symbol") or item.get("contractId", "")
                if sym and sym.endswith("-USDT") and "USDT" in sym:
                    symbols.append(sym)
            if symbols:
                break
        except Exception as e:
            print(f"  ERR obteniendo pares: {e}")

    if not symbols:
        # Fallback: lista amplia conocida
        print("  API de contratos no disponible, usando lista de referencia...")
        symbols = [
            "BTC-USDT","ETH-USDT","BNB-USDT","XRP-USDT","ADA-USDT",
            "DOGE-USDT","SOL-USDT","DOT-USDT","MATIC-USDT","AVAX-USDT",
            "LINK-USDT","LTC-USDT","UNI-USDT","ATOM-USDT","NEAR-USDT",
            "OP-USDT","ARB-USDT","APT-USDT","SUI-USDT","FTM-USDT",
            "SAND-USDT","MANA-USDT","AXS-USDT","GALA-USDT","ENJ-USDT",
            "AAVE-USDT","MKR-USDT","COMP-USDT","SNX-USDT","CRV-USDT",
            "INJ-USDT","TIA-USDT","SEI-USDT","WLD-USDT","BLUR-USDT",
            "HBAR-USDT","ALGO-USDT","VET-USDT","XLM-USDT","EOS-USDT",
            "FIL-USDT","ICP-USDT","THETA-USDT","ETC-USDT","XMR-USDT",
            "CAKE-USDT","GMT-USDT","FLOW-USDT","ROSE-USDT","KAVA-USDT",
            "CHZ-USDT","BAND-USDT","ZIL-USDT","ONE-USDT","ANKR-USDT",
            "POL-USDT","JTO-USDT","PYTH-USDT","JUP-USDT","STRK-USDT",
            "MANTA-USDT","ALT-USDT","DYM-USDT","PIXEL-USDT","PORTAL-USDT",
        ]

    # Filtrar duplicados y ordenar
    symbols = sorted(list(set(symbols)))
    print(f"  {len(symbols)} pares encontrados")
    return symbols


# ══════════════════════════════════════════════════════
# FETCH DE DATOS
# ══════════════════════════════════════════════════════

def fetch(sym, verbose=False):
    endpoints = [
        "https://open-api.bingx.com/openApi/swap/v3/quote/klines",
        "https://open-api.bingx.com/openApi/swap/v2/quote/klines",
    ]
    if verbose:
        print(f"  {sym}...", end="", flush=True)
    all_c = []
    for url in endpoints:
        all_c = []; end = None; success = False
        for _ in range(2):
            p = {"symbol": sym, "interval": "1h", "limit": 1000}
            if end: p["endTime"] = end
            try:
                r = requests.get(url, params=p, timeout=10).json()
                c = r if isinstance(r, list) else r.get("data", [])
                if not c: break
                all_c = c + all_c
                first_ts = c[0][0] if isinstance(c[0], list) else (c[0].get("time") or c[0].get("t", 0))
                end = int(first_ts) - 1
                time.sleep(0.2); success = True
            except Exception:
                break
        if success and all_c: break

    if not all_c: return pd.DataFrame()

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
    if df.empty or df["close"].max() == 0: return pd.DataFrame()
    return df


# ══════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════

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

def get_trend(basis_series, i):
    if i < TREND_LOOKBACK: return "flat"
    now  = float(basis_series.iloc[i])
    prev = float(basis_series.iloc[i - TREND_LOOKBACK])
    if now == 0 or prev == 0: return "flat"
    pct = (now - prev) / prev * 100
    if   pct >  TREND_THRESH: return "up"
    elif pct < -TREND_THRESH: return "down"
    return "flat"

def calc_score_long(r, dv, mb, stv):
    s = 40
    if   r < 18: s += 35
    elif r < 22: s += 28
    elif r < 27: s += 20
    elif r < 30: s += 14
    if dv:  s += 18
    if mb:  s += 5
    if stv is not None and not np.isnan(stv):
        if   stv < 10: s += 15
        elif stv < 20: s += 10
        elif stv < 30: s += 5
    return min(s, 100)


# ══════════════════════════════════════════════════════
# BACKTEST POR PAR
# ══════════════════════════════════════════════════════

def backtest_sym(df):
    if len(df) < max(BB_PERIOD, SMA_PERIOD) + 30:
        return []

    df = df.copy()
    basis = df["close"].rolling(BB_PERIOD).mean()
    std   = df["close"].rolling(BB_PERIOD).std()
    df["upper"]   = basis + BB_SIGMA * std
    df["basis"]   = basis
    df["lower"]   = basis - BB_SIGMA * std
    df["rsi"]     = rsi_calc(df["close"])
    df["atr"]     = atr_calc(df)
    df["macd"]    = macd_calc(df["close"])
    df["stoch"]   = stoch_rsi_calc(df["close"])
    df["sma50"]   = df["close"].rolling(SMA_PERIOD).mean()
    df["vol_ma"]  = df["volume"].rolling(20).mean()

    trades = []; pos = None; cool = 0
    balance = INITIAL_BAL

    for i in range(max(BB_PERIOD, SMA_PERIOD) + 30, len(df)):
        cur   = df.iloc[i]
        prev  = df.iloc[i-1]
        price = float(cur["close"])
        r     = float(cur["rsi"])   if not np.isnan(cur["rsi"])   else 50.0
        a     = float(cur["atr"])   if not np.isnan(cur["atr"])   else 0.0
        mb    = float(cur["macd"]) > 0 if not np.isnan(cur["macd"]) else True
        stv   = float(cur["stoch"]) if not np.isnan(cur["stoch"]) else 50.0
        sma   = float(cur["sma50"]) if not np.isnan(cur["sma50"]) else price
        vol   = float(cur["volume"]) if not np.isnan(cur["volume"]) else 0.0
        vma   = float(cur["vol_ma"]) if not np.isnan(cur["vol_ma"]) else 0.0
        vol_ok = (vma > 0) and (vol >= vma)
        if a <= 0: continue

        blo  = float(cur["lower"])
        bhi  = float(cur["upper"])
        bmid = float(cur["basis"])
        trend = get_trend(df["basis"], i)

        if pos:
            entry = pos["entry"]
            if not pos.get("pd"):
                if price >= pos["tp_p"]:
                    pos["qty"] *= 0.5; pos["pd"] = True; pos["sl"] = entry
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
                    "pnl": round(pnl, 4),
                    "result": "WIN" if pnl > 0 else "LOSS",
                    "reason": reason,
                    "date": str(cur["ts"])[:10],
                })
                pos = None; cool = COOLDOWN_BARS
            continue

        if cool > 0: cool -= 1; continue

        # Filtros: solo longs en tendencia alcista
        if LONG_ONLY_UP and trend != "up": continue
        if not vol_ok: continue
        if price < sma * 0.97: continue

        dv = divergence_bull(df["close"].iloc[max(0,i-8):i+1],
                             df["rsi"].iloc[max(0,i-8):i+1])

        touch = (price <= blo * 1.002) or (dv and r < RSI_LONG and price <= blo * 1.005)

        if touch and r < RSI_LONG and balance > 0:
            sc = calc_score_long(r, dv, mb, stv)
            if sc >= SCORE_MIN:
                sl   = float(cur["low"]) * (1 - SL_BUFFER)
                tp   = bhi
                tp_p = price + PARTIAL_TP_ATR * a
                if sl > 0 and tp > price and (price - sl) > 0:
                    rr_val = (tp - price) / (price - sl)
                    if rr_val >= MIN_RR:
                        qty = (balance * RISK_PCT * LEVERAGE) / price
                        pos = {"entry": price, "sl": sl, "tp": tp,
                               "tp_p": tp_p, "qty": qty, "sc": sc,
                               "oi": i, "pd": False}

    return trades


# ══════════════════════════════════════════════════════
# CALCULAR MÉTRICAS POR PAR
# ══════════════════════════════════════════════════════

def calc_metrics(sym, trades):
    if not trades:
        return None
    total  = len(trades)
    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    wr     = len(wins) / total * 100
    pnl    = sum(t["pnl"] for t in trades)
    gw     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    pf     = gw / gl if gl > 0 else (999 if gw > 0 else 0)
    aw     = gw / len(wins)   if wins   else 0
    al     = gl / len(losses) if losses else 0
    roi    = pnl  # sobre INITIAL_BAL=100

    return {
        "symbol": sym,
        "trades": total,
        "wins":   len(wins),
        "losses": len(losses),
        "wr":     round(wr, 1),
        "pnl":    round(pnl, 4),
        "pf":     round(pf, 2),
        "aw":     round(aw, 4),
        "al":     round(al, 4),
        "roi":    round(roi, 2),
    }


# ══════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════

def main():
    sep = "=" * 65
    print(sep)
    print("  SCANNER DE PARES BINGX — TODOS LOS SIMBOLOS USDT")
    print(f"  RSI<{RSI_LONG} | Score>={SCORE_MIN} | R:R>={MIN_RR} | Solo LONG UP + VOL")
    print(sep)
    print()

    # 1. Obtener todos los pares
    print("Obteniendo pares disponibles en BingX...")
    all_symbols = get_all_symbols()
    print()

    # 2. Correr backtest en cada par
    results = []
    errors  = []
    total   = len(all_symbols)

    print(f"Analizando {total} pares (esto tarda ~{total//4} minutos)...\n")

    for idx, sym in enumerate(all_symbols, 1):
        pct = idx / total * 100
        print(f"  [{idx:3d}/{total}] {pct:4.0f}%  {sym:<16}", end="", flush=True)
        try:
            df = fetch(sym)
            if df.empty:
                print("sin datos")
                errors.append(sym)
                continue

            velas = len(df)
            trades = backtest_sym(df)
            m = calc_metrics(sym, trades)

            if m and m["trades"] >= MIN_TRADES:
                results.append(m)
                tag = "✓" if m["pf"] >= MIN_PF and m["wr"] >= MIN_WR else " "
                print(f"{tag}  {m['trades']:2d}tr  WR:{m['wr']:4.0f}%  PF:{m['pf']:.2f}  ${m['pnl']:+.4f}")
            else:
                n = m["trades"] if m else 0
                print(f"   {n}tr (insuficiente)")

        except Exception as e:
            print(f"ERR: {str(e)[:40]}")
            errors.append(sym)

        time.sleep(0.4)

    # 3. Ranking
    results.sort(key=lambda x: (-x["pf"], -x["wr"], -x["pnl"]))

    # Filtrar por criterios mínimos
    top_all  = [r for r in results if r["trades"] >= MIN_TRADES]
    top_good = [r for r in top_all if r["pf"] >= MIN_PF and r["wr"] >= MIN_WR]

    print()
    print(sep)
    print(f"  RESULTADOS — {len(results)} pares con datos suficientes")
    print(f"  Criterio rentable: WR>={MIN_WR}% y PF>={MIN_PF}")
    print(sep)

    lines = []
    lines.append(sep)
    lines.append(f"  SCANNER BINGX — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  RSI<{RSI_LONG} | Score>={SCORE_MIN} | R:R>={MIN_RR} | Solo LONG UP + VOL")
    lines.append(f"  Total pares analizados: {total} | Con datos: {len(results)} | Rentables: {len(top_good)}")
    lines.append(sep)
    lines.append("")
    lines.append(f"  {'PAR':<14} {'TR':>3} {'WR':>6} {'PF':>6} {'PnL':>9} {'AW':>8} {'AL':>8}")
    lines.append("  " + "-" * 60)

    shown = 0
    for r in top_all[:TOP_N * 2]:
        tag = "★" if r["pf"] >= MIN_PF and r["wr"] >= MIN_WR else " "
        line = (f"  {tag}{r['symbol']:<13} {r['trades']:>3}tr "
                f"WR:{r['wr']:4.0f}% PF:{r['pf']:5.2f} "
                f"${r['pnl']:>+7.4f} "
                f"↑${r['aw']:>6.4f} ↓${r['al']:>6.4f}")
        lines.append(line)
        shown += 1

    lines.append("")
    lines.append(f"  TOP {min(TOP_N, len(top_good))} RENTABLES (WR>={MIN_WR}% y PF>={MIN_PF}):")
    lines.append("  " + "-" * 60)
    for i, r in enumerate(top_good[:TOP_N], 1):
        lines.append(f"  {i:2d}. {r['symbol']:<14} WR:{r['wr']:.0f}%  PF:{r['pf']:.2f}  ${r['pnl']:+.4f}")

    lines.append("")
    if errors:
        lines.append(f"  Sin datos: {len(errors)} pares ({', '.join(errors[:10])}{'...' if len(errors)>10 else ''})")
    lines.append(sep)

    out = "\n".join(lines)
    print(out)

    # Guardar ranking completo
    with open("ranking_pares.txt", "w", encoding="utf-8") as f:
        f.write(out + f"\n\nGenerado: {datetime.now()}\n")
    print("\n  Guardado en: ranking_pares.txt")

    # 4. Generar config_recomendado.py
    best_symbols = [r["symbol"] for r in top_good[:TOP_N]]
    if len(best_symbols) < 5 and top_all:
        # Si hay pocos rentables, añadir los mejores aunque no cumplan
        extras = [r["symbol"] for r in top_all if r["symbol"] not in best_symbols]
        best_symbols += extras[:10 - len(best_symbols)]

    config_lines = [
        "# ══════════════════════════════════════════════════════",
        f"# config_recomendado.py — generado por scanner {datetime.now().strftime('%Y-%m-%d')}",
        f"# {len(best_symbols)} mejores pares de {total} analizados",
        "# Copia este contenido a config.py para usar",
        "# ══════════════════════════════════════════════════════",
        "",
        "BB_PERIOD      = 20",
        "BB_SIGMA       = 2.0",
        "RSI_PERIOD     = 14",
        f"RSI_LONG       = {RSI_LONG}",
        "RSI_SHORT      = 70",
        f"SL_BUFFER      = {SL_BUFFER}",
        f"PARTIAL_TP_ATR = {PARTIAL_TP_ATR}",
        f"LEVERAGE       = {LEVERAGE}",
        f"RISK_PCT       = {RISK_PCT}",
        f"INITIAL_BAL    = {INITIAL_BAL}",
        f"SCORE_MIN      = {SCORE_MIN}",
        f"COOLDOWN_BARS  = {COOLDOWN_BARS}",
        f"MIN_RR         = {MIN_RR}",
        f"TREND_LOOKBACK = {TREND_LOOKBACK}",
        f"TREND_THRESH   = {TREND_THRESH}",
        f"SMA_PERIOD     = {SMA_PERIOD}",
        "LONG_ONLY_UP   = True",
        "",
        "SYMBOLS = [",
    ]

    for sym in best_symbols:
        # Buscar métricas para el comentario
        m = next((r for r in results if r["symbol"] == sym), None)
        if m:
            comment = f"  # {m['trades']}tr WR:{m['wr']:.0f}% PF:{m['pf']:.2f} ${m['pnl']:+.4f}"
        else:
            comment = ""
        config_lines.append(f'    "{sym}",{comment}')

    config_lines += [
        "]",
        "",
        f'VERSION = "scanner_{datetime.now().strftime("%m%d")}"',
    ]

    config_out = "\n".join(config_lines)
    with open("config_recomendado.py", "w", encoding="utf-8") as f:
        f.write(config_out + "\n")
    print("  Guardado en: config_recomendado.py")
    print()
    print("  ► Copia config_recomendado.py → config.py para usar los mejores pares")
    print(sep)


main()
