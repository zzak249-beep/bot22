"""Backtest EP5 con datos reales de BingX (misma lógica que el bot, incluido el filtro BTC).

Uso:
  python backtest.py --days 60 --top 30
  python backtest.py --days 60 --symbols BTC-USDT,ETH-USDT,SOL-USDT
  python backtest.py --csv mis_velas.csv      (time,open,high,low,close,volume[,symbol])
Parámetros de estrategia: mismas variables de entorno que el bot (SL_ATR=1.6 python backtest.py ...).
Las velas se guardan en ./bt_cache para no descargarlas otra vez (walkforward.py las reutiliza).
Salida: resumen en consola + bt_trades.csv
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np
import pandas as pd

from bingx import BingX
from config import norm_symbol
from strategy import Params, apply_regime, backtest_symbol, compute, regime_series

BAR_MS = 300_000
BTC = "BTC-USDT"


def fetch_history(bx: BingX, sym: str, days: int) -> pd.DataFrame:
    end = int(time.time() * 1000) // BAR_MS * BAR_MS
    start = end - days * 86_400_000
    parts, cur = [], start
    while cur < end:
        df = bx.klines(sym, "5m", 1000, start_ms=cur, end_ms=min(end, cur + 1000 * BAR_MS) - 1)
        if df.empty:
            cur += 1000 * BAR_MS
            continue
        parts.append(df)
        cur = int(df.time.iloc[-1]) + BAR_MS
        time.sleep(0.15)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts).drop_duplicates("time").sort_values("time")
    return out[out.time + BAR_MS <= end].reset_index(drop=True)


def pick_symbols(bx: BingX, top: int, min_vol: float) -> list[str]:
    tk = [t for t in bx.tickers() if t.get("symbol", "").endswith("-USDT")
          and not t["symbol"][:-5].endswith("USD") and float(t.get("quoteVolume") or 0) >= min_vol]
    return [t["symbol"] for t in sorted(tk, key=lambda x: -float(x["quoteVolume"]))[:top]]


def load_data(syms: list[str], days: int, cache: str = "bt_cache", verbose: bool = True) -> dict:
    """Velas por símbolo (con caché en disco). Incluye siempre BTC-USDT para el régimen."""
    os.makedirs(cache, exist_ok=True)
    bx = BingX()
    out = {}
    all_syms = list(dict.fromkeys(syms + [BTC]))
    for i, s in enumerate(all_syms, 1):
        path = os.path.join(cache, f"{s}_{days}d.csv")
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 12 * 3600:
            df = pd.read_csv(path)
        else:
            df = fetch_history(bx, s, days)
            if not df.empty:
                df.to_csv(path, index=False)
        if verbose:
            print(f"[{i}/{len(all_syms)}] {s}: {len(df)} velas")
        if len(df) >= 500:
            out[s] = df
    return out


def run(p: Params, data: dict, feats: dict | None = None) -> pd.DataFrame:
    """Opera todos los símbolos cargados (BTC incluido). feats: caché opcional de compute()."""
    reg = regime_series(data[BTC], p) if p.btc_filter and BTC in data else None
    trades = []
    for s, df in data.items():
        f = feats[s] if feats is not None and s in feats else compute(df, p)
        f = apply_regime(f, reg, p)
        trades += backtest_symbol(f, p, s)
    return pd.DataFrame(trades)


def stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n": 0, "avg": np.nan, "wr": np.nan, "pf": np.nan, "tot": 0.0, "dd": 0.0}
    g = t.sort_values("cerrada_ms").r_neto
    loss = -g[g < 0].sum()
    eq = g.cumsum()
    return {"n": len(g), "avg": g.mean(), "wr": (g > 0).mean(), "pf": g[g > 0].sum() / loss if loss else np.inf,
            "tot": g.sum(), "dd": (eq - eq.cummax()).min()}


def summary(t: pd.DataFrame) -> str:
    if t.empty:
        return "sin operaciones"
    s = stats(t)
    days = (t.cerrada_ms.max() - t.abierta_ms.min()) / 86_400_000
    lines = [
        f"operaciones {s['n']} ({s['n'] / max(days, 1):.1f}/día) · WR {s['wr'] * 100:.1f}% · PF {s['pf']:.2f}",
        f"R neto total {s['tot']:+.1f} · medio {s['avg']:+.3f} · bruto medio {t.r_bruto.mean():+.3f} · "
        f"coste medio {t.coste_r.mean():.3f} · maxDD {s['dd']:.1f}R",
    ]
    for col in ["lado", "motivo"]:
        lines.append(t.groupby(col).r_neto.agg(["count", "mean", "sum"]).round(3).to_string())
    t = t.assign(semana=pd.to_datetime(t.abierta_ms, unit="ms").dt.to_period("W"))
    lines.append(t.groupby("semana").r_neto.agg(["count", "sum"]).round(2).to_string())
    top = t.groupby("symbol").r_neto.sum()
    lines.append(f"mejor símbolo {top.idxmax()} {top.max():+.1f}R · peor {top.idxmin()} {top.min():+.1f}R · "
                 f"sin el mejor: {s['tot'] - top.max():+.1f}R")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--min-vol", type=float, default=20_000_000)
    ap.add_argument("--csv", default="")
    ap.add_argument("--cache", default="bt_cache")
    ap.add_argument("--out", default="bt_trades.csv")
    a = ap.parse_args()
    p = Params.from_env()
    if a.csv:
        raw = pd.read_csv(a.csv)
        data = {s: g.sort_values("time") for s, g in raw.groupby("symbol")} if "symbol" in raw else {"CSV": raw}
        if BTC not in data and p.btc_filter:
            print("aviso: el CSV no trae BTC-USDT → filtro BTC desactivado")
            p.btc_filter = False
    else:
        syms = [norm_symbol(s) for s in a.symbols.split(",") if s.strip()] or \
            pick_symbols(BingX(), a.top, a.min_vol)
        data = load_data(syms, a.days, a.cache)
    t = run(p, data)
    if not t.empty:
        t.to_csv(a.out, index=False)
    print("\n" + summary(t))
    print(f"\nParams: {p}")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
