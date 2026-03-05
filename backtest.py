"""
backtest.py — Backtesting RSI + Bollinger Bands + ATR v6
MEJORAS:
  - Simula trailing stop y cierre parcial
  - Evalúa impacto de filtro EMA50
  - Métricas adicionales: avg_score, drawdown máximo
  - Genera ranking_pares.txt y config_recomendado.py
"""

import time
import json
from datetime import datetime
import numpy as np

import exchange
import config
from analizar import calcular_rsi, calcular_bb, calcular_atr, calcular_ema, tiene_divergencia_alcista

# ============================================================
# PARÁMETROS DEL BACKTEST
# ============================================================
BT_INTERVALO    = "1h"
BT_VELAS        = 300
BT_RSI_OVERSOLD = 30
BT_BB_STD       = 2.0
BT_SL_ATR_MULT  = 1.5
BT_TP_ATR_MULT  = 2.5
BT_MIN_TRADES   = 3
BT_MIN_WR       = 50.0
BT_MIN_PF       = 1.2
BT_CAPITAL      = 100.0
BT_EMA_FILTRO   = True   # Simular filtro EMA50
BT_TRAILING     = True   # Simular trailing stop
BT_PARCIAL      = True   # Simular cierre parcial al 50%


def backtest_par(par: str) -> dict:
    klines = exchange.get_klines(par, intervalo=BT_INTERVALO, limit=BT_VELAS)
    if len(klines) < 60:
        return {"par": par, "status": "insuficiente", "trades": 0}

    data   = exchange.parsear_klines(klines)
    closes = data["closes"]
    highs  = data["highs"]
    lows   = data["lows"]
    vols   = data["vols"]

    if len(closes) < 40:
        return {"par": par, "status": "insuficiente", "trades": 0}

    trades      = []
    en_posicion = False
    entrada     = sl = tp = atr_entrada = 0.0
    trailing_sl = 0.0
    parcial_cerrado = False
    pnl_parcial     = 0.0

    for i in range(40, len(closes)):
        precio = closes[i]
        closes_prev = closes[:i]
        highs_prev  = highs[:i]
        lows_prev   = lows[:i]

        if not en_posicion:
            rsi  = calcular_rsi(closes_prev, 14)
            bb   = calcular_bb(closes_prev, 20, BT_BB_STD)
            atr  = calcular_atr(highs_prev, lows_prev, closes_prev, 14)

            # Filtro EMA50
            if BT_EMA_FILTRO:
                ema50 = calcular_ema(closes_prev, 50)
                if precio < ema50 * 0.995:
                    continue

            # Condiciones de entrada
            if rsi < BT_RSI_OVERSOLD and precio <= bb["inferior"] * 1.002 and atr > 0:
                entrada         = precio
                sl              = precio - (atr * BT_SL_ATR_MULT)
                tp              = precio + (atr * BT_TP_ATR_MULT)
                atr_entrada     = atr
                trailing_sl     = sl
                parcial_cerrado = False
                pnl_parcial     = 0.0
                en_posicion     = True

        else:
            recorrido_total  = tp - entrada
            recorrido_actual = precio - entrada
            sl_actual        = trailing_sl if BT_TRAILING else sl

            # Cierre parcial al 50% del recorrido
            if BT_PARCIAL and not parcial_cerrado and recorrido_total > 0:
                if recorrido_actual >= recorrido_total * 0.5:
                    pnl_parcial     = (precio - entrada) / entrada * 0.5
                    parcial_cerrado = True
                    # Mover SL a breakeven
                    if precio > entrada:
                        trailing_sl = max(trailing_sl, entrada * 1.0001)

            # Trailing stop
            if BT_TRAILING and atr_entrada > 0:
                nuevo_trailing = precio - atr_entrada
                if nuevo_trailing > trailing_sl:
                    trailing_sl = nuevo_trailing

            # Verificar salida
            if precio <= sl_actual:
                pnl_total = (sl_actual - entrada) / entrada * (0.5 if parcial_cerrado else 1.0) + pnl_parcial
                trades.append({
                    "resultado": "LOSS" if pnl_total < 0 else "WIN",
                    "pnl": pnl_total, "entrada": entrada, "salida": sl_actual
                })
                en_posicion = False

            elif precio >= tp:
                pnl_total = (tp - entrada) / entrada * (0.5 if parcial_cerrado else 1.0) + pnl_parcial
                trades.append({
                    "resultado": "WIN",
                    "pnl": pnl_total, "entrada": entrada, "salida": tp
                })
                en_posicion = False

    if not trades:
        return {"par": par, "status": "sin_trades", "trades": 0}

    wins      = [t for t in trades if t["resultado"] == "WIN"]
    losses    = [t for t in trades if t["resultado"] == "LOSS"]
    total     = len(trades)
    wr        = len(wins) / total * 100

    ganancias = sum(t["pnl"] for t in wins)
    perdidas  = abs(sum(t["pnl"] for t in losses))
    pf        = ganancias / perdidas if perdidas > 0 else 999.0
    pnl_usd   = sum(t["pnl"] for t in trades) * BT_CAPITAL

    avg_win  = (ganancias / len(wins)) * BT_CAPITAL if wins else 0
    avg_loss = (perdidas / len(losses)) * BT_CAPITAL if losses else 0

    # Drawdown máximo
    equity   = BT_CAPITAL
    peak     = BT_CAPITAL
    max_dd   = 0.0
    for t in trades:
        equity += t["pnl"] * BT_CAPITAL
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd

    return {
        "par":      par,
        "status":   "ok",
        "trades":   total,
        "wins":     len(wins),
        "losses":   len(losses),
        "wr":       wr,
        "pf":       pf,
        "pnl_usd":  pnl_usd,
        "avg_win":  avg_win,
        "avg_loss": avg_loss,
        "max_dd":   max_dd * 100,
        "rentable": wr >= BT_MIN_WR and pf >= BT_MIN_PF
    }


def escanear_todos(pares: list = None, max_pares: int = None) -> list:
    if pares is None:
        pares = config.PARES
    if max_pares:
        pares = pares[:max_pares]

    flags = []
    if BT_EMA_FILTRO: flags.append("EMA50")
    if BT_TRAILING:   flags.append("Trailing")
    if BT_PARCIAL:    flags.append("ParcialClose")

    print("=" * 70)
    print(f"  BACKTEST BINGX v6 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  RSI<{BT_RSI_OVERSOLD} | R:R>={BT_TP_ATR_MULT/BT_SL_ATR_MULT:.1f} | " + " | ".join(flags))
    print(f"  Pares: {len(pares)}")
    print("=" * 70)

    resultados = []
    total = len(pares)

    for i, par in enumerate(pares):
        pct       = int((i + 1) / total * 100)
        resultado = backtest_par(par)

        if resultado["trades"] < BT_MIN_TRADES:
            status = f"  [{i+1:3}/{total}] {pct:3}%  {par:<25} — insuficiente ({resultado['trades']}tr)"
        else:
            marca  = "✓" if resultado.get("rentable") else " "
            dd_str = f"DD:{resultado['max_dd']:.1f}%" if resultado.get("max_dd") is not None else ""
            status = (
                f"  [{i+1:3}/{total}] {pct:3}%  {par:<25}"
                f"{marca}  {resultado['trades']}tr  "
                f"WR:{resultado['wr']:5.0f}%  "
                f"PF:{min(resultado['pf'], 999):.2f}  "
                f"${resultado['pnl_usd']:+7.2f}  "
                f"{dd_str}"
            )

        print(status)
        resultados.append(resultado)
        time.sleep(0.1)

    return resultados


def generar_ranking(resultados: list) -> list:
    con_datos = [r for r in resultados if r.get("trades", 0) >= BT_MIN_TRADES]
    rentables = [r for r in con_datos if r.get("rentable")]
    # Ordenar por PF × WR, penalizando drawdown alto
    rentables.sort(
        key=lambda x: (x["pf"] * x["wr"]) / (1 + x.get("max_dd", 0) / 100),
        reverse=True
    )
    return rentables


def guardar_resultados(resultados: list, rentables: list):
    total_con_datos = len([r for r in resultados if r.get("trades", 0) >= BT_MIN_TRADES])

    with open("ranking_pares.txt", "w", encoding="utf-8") as f:
        f.write(f"BACKTEST BINGX v6 — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"RSI<{BT_RSI_OVERSOLD} | EMA={BT_EMA_FILTRO} | Trailing={BT_TRAILING} | Parcial={BT_PARCIAL}\n")
        f.write(f"Analizados: {len(resultados)} | Con datos: {total_con_datos} | Rentables: {len(rentables)}\n\n")
        f.write(f"{'PAR':<28} {'TR':>4} {'WR':>6} {'PF':>6} {'PnL':>8} {'DD':>6}\n")
        f.write("-" * 60 + "\n")

        for i, r in enumerate(rentables[:20], 1):
            f.write(
                f"{r['par']:<27} "
                f"{r['trades']:>3}tr "
                f"WR:{r['wr']:5.0f}% "
                f"PF:{min(r['pf'], 999):6.2f} "
                f"${r['pnl_usd']:+7.2f} "
                f"DD:{r.get('max_dd',0):.1f}%\n"
            )

    print(f"\n  Guardado: ranking_pares.txt")

    top15 = [r["par"] for r in rentables[:15]]
    with open("config_recomendado.py", "w", encoding="utf-8") as f:
        f.write(f'# Generado automáticamente — {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
        f.write(f'# TOP {len(top15)} pares rentables — EMA={BT_EMA_FILTRO} Trailing={BT_TRAILING}\n\n')
        f.write(f'PARES = {json.dumps(top15, indent=4)}\n\n')
        f.write(f'RSI_OVERSOLD = {BT_RSI_OVERSOLD}\n')
        f.write(f'BB_STD       = {BT_BB_STD}\n')
        f.write(f'SL_ATR_MULT  = {BT_SL_ATR_MULT}\n')
        f.write(f'TP_ATR_MULT  = {BT_TP_ATR_MULT}\n')

    print(f"  Guardado: config_recomendado.py")
    print(f"\n  ► Copia config_recomendado.py → config.py para usar los mejores pares")


def imprimir_resumen(rentables: list, total_con_datos: int):
    print("\n" + "=" * 70)
    print(f"  RESUMEN — {total_con_datos} pares con datos suficientes")
    print(f"  Rentables (WR>={BT_MIN_WR}% y PF>={BT_MIN_PF}): {len(rentables)}")
    print("=" * 70)
    print(f"\n  TOP {min(15, len(rentables))} PARES:")
    print("  " + "-" * 65)
    for i, r in enumerate(rentables[:15], 1):
        print(
            f"  {i:2}. {r['par']:<26} "
            f"WR:{r['wr']:.0f}%  PF:{min(r['pf'],999):.2f}  "
            f"${r['pnl_usd']:+.2f}  DD:{r.get('max_dd',0):.1f}%"
        )
    print("=" * 70)


if __name__ == "__main__":
    import sys
    max_p = int(sys.argv[1]) if len(sys.argv) > 1 else None
    resultados = escanear_todos(pares=config.PARES, max_pares=max_p)
    rentables  = generar_ranking(resultados)
    imprimir_resumen(rentables, len([r for r in resultados if r.get("trades", 0) >= BT_MIN_TRADES]))
    guardar_resultados(resultados, rentables)
