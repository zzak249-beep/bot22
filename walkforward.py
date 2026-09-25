"""Walk-forward EP5: ajusta en la 1ª parte del periodo (IS) y valida en la 2ª (OOS), sin tocar nada.

Uso:
  python walkforward.py --days 60 --top 30
  python walkforward.py --days 90 --top 40 --split 0.6
Reutiliza ./bt_cache de backtest.py. Salida: tabla en consola + wf_results.csv + VEREDICTO.

Criterio para pasar a PAPER/real (todas):
  · la combinación elegida en IS da en OOS: R medio ≥ +0.10, PF ≥ 1.2 y ≥ 50 operaciones
  · al menos el 60% de las 10 mejores combinaciones IS siguen positivas en OOS (robustez)
  · los parámetros actuales no son negativos en OOS
"""
from __future__ import annotations

import argparse
import itertools
from dataclasses import replace

import numpy as np
import pandas as pd

from backtest import BTC, load_data, pick_symbols, stats
from bingx import BingX
from config import norm_symbol
from strategy import Params, apply_regime, backtest_symbol, compute, regime_series

GRID = {
    "sl_atr": [1.2, 1.4, 1.8],
    "tp_atr": [1.8, 2.2, 3.0],
    "score_min": [1, 2, 3],
    "session_mode": ["LONDRES_NY", "TODO"],
    "btc_filter": [True, False],
}
COMPUTE_KEYS = ("sl_atr", "score_min", "session_mode")      # los que cambian la señal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--min-vol", type=float, default=20_000_000)
    ap.add_argument("--split", type=float, default=0.5)
    ap.add_argument("--min-trades", type=int, default=40)
    ap.add_argument("--cache", default="bt_cache")
    a = ap.parse_args()

    base = Params.from_env()
    syms = [norm_symbol(s) for s in a.symbols.split(",") if s.strip()] or pick_symbols(BingX(), a.top, a.min_vol)
    data = load_data(syms, a.days, a.cache)
    t_all = np.concatenate([df.time.to_numpy() for df in data.values()])
    cut = int(t_all.min() + a.split * (t_all.max() - t_all.min()))
    print(f"\nIS hasta {pd.to_datetime(cut, unit='ms')} · OOS después · {len(data)} símbolos")

    keys = list(GRID)
    combos = list(itertools.product(*GRID.values()))
    feat_cache: dict = {}
    reg = regime_series(data[BTC], base) if BTC in data else None

    def evaluate(p: Params) -> dict:
        ck = tuple(getattr(p, k) for k in COMPUTE_KEYS)
        if ck not in feat_cache:
            feat_cache[ck] = {s: compute(df, p) for s, df in data.items()}
        tr = []
        for s, f in feat_cache[ck].items():
            tr += backtest_symbol(apply_regime(f, reg, p), p, s)
        t = pd.DataFrame(tr)
        is_ = stats(t[t.abierta_ms < cut]) if len(t) else stats(t)
        oos = stats(t[t.abierta_ms >= cut]) if len(t) else stats(t)
        return {**{k: getattr(p, k) for k in keys},
                **{f"is_{k}": v for k, v in is_.items()}, **{f"oos_{k}": v for k, v in oos.items()}}

    rows = []
    for i, vals in enumerate(combos, 1):
        rows.append(evaluate(replace(base, **dict(zip(keys, vals)))))
        if i % 12 == 0:
            print(f"  {i}/{len(combos)} combinaciones")
    d = pd.DataFrame([evaluate(base)])                  # parámetros actuales (env), estén o no en la rejilla

    r = pd.DataFrame(rows)
    r.to_csv("wf_results.csv", index=False)
    ok = r[r.is_n >= a.min_trades].sort_values("is_avg", ascending=False)
    cols = keys + ["is_n", "is_avg", "is_pf", "oos_n", "oos_avg", "oos_pf", "oos_dd"]
    pd.set_option("display.width", 200)
    print("\nTOP 10 por IS (validación en OOS):")
    print(ok[cols].head(10).round(3).to_string(index=False))

    print("\nParámetros actuales (variables de entorno):")
    print(d[cols].round(3).to_string(index=False))
    print(f"\nRobustez: {(r.oos_avg > 0).mean() * 100:.0f}% de TODAS las combinaciones positivas en OOS · "
          f"mediana OOS {r.oos_avg.median():+.3f}R")

    if ok.empty:
        print("\nVEREDICTO: NO PASA — ninguna combinación con suficientes operaciones en IS")
        return
    best = ok.iloc[0]
    top10_pos = (ok.head(10).oos_avg > 0).mean()
    default_oos = float(d.oos_avg.iloc[0])
    checks = {
        f"OOS R medio {best.oos_avg:+.3f} ≥ +0.10": best.oos_avg >= 0.10,
        f"OOS PF {best.oos_pf:.2f} ≥ 1.2": best.oos_pf >= 1.2,
        f"OOS operaciones {int(best.oos_n)} ≥ 50": best.oos_n >= 50,
        f"top-10 IS positivas en OOS {top10_pos * 100:.0f}% ≥ 60%": top10_pos >= 0.6,
        f"parámetros actuales OOS {default_oos:+.3f} ≥ 0": bool(np.isfinite(default_oos) and default_oos >= 0),
    }
    print("\nVEREDICTO:")
    for k, v in checks.items():
        print(f"  {'✔' if v else '✘'} {k}")
    if all(checks.values()):
        print("  → PASA. Variables para Railway:")
        for k in keys:
            v = best[k]
            print(f"{k.upper()}={str(v).lower() if isinstance(v, (bool, np.bool_)) else v}")
    else:
        print("  → NO PASA. No activar en real.")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
