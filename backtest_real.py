"""
backtest_real.py — Backtesting con datos REALES de BingX
Ejecutar en Railway con: python backtest_real.py
O localmente con acceso a internet.

Descarga datos históricos reales y ejecuta el mismo motor de señales
que usa el bot en producción, para obtener métricas reales de WR y PnL.

Uso:
  python backtest_real.py              # últimos 7 días, 10 pares principales
  python backtest_real.py --dias 14    # últimos 14 días
  python backtest_real.py --par BTC-USDT --dias 30  # un par específico
"""

import sys, os, time, json, math, argparse, logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("backtest")

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

# ─────────────────────────────────────────────────
# CONFIG (mismos valores que config.py v5.5)
# ─────────────────────────────────────────────────
LEVERAGE       = 10
TRADE_USDT     = 10.0
COMMISSION     = 0.00045    # 0.045% por lado
SLIPPAGE       = 0.0003     # 0.03%
BALANCE_INIT   = 90.0
MAX_POSICIONES = 3
SCORE_MIN_VIEJA = 6
SCORE_MIN_NUEVA = 8
TIME_EXIT_VIEJA = 8  * 12  # horas × velas_por_hora
TIME_EXIT_NUEVA = 16 * 12
COOLDOWN_VELAS  = 5
MIN_RR          = 2.0

BASE_URL = "https://open-api.bingx.com"

PARES_DEFAULT = [
    "BTC-USDT","ETH-USDT","SOL-USDT","DOGE-USDT","AXS-USDT",
    "KAVA-USDT","ORDI-USDT","FLOKI-USDT","AVAX-USDT","INJ-USDT",
]

# ─────────────────────────────────────────────────
# DESCARGA DE DATOS REALES
# ─────────────────────────────────────────────────

def fetch_candles_real(par: str, dias: int = 7, tf: str = "5m") -> list:
    """
    Descarga velas históricas de BingX (endpoint público, sin auth).
    Pagina automáticamente para obtener el período completo.
    """
    ms_per_candle = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000}
    ms_tf   = ms_per_candle.get(tf, 300_000)
    end_ts  = int(time.time() * 1000)
    start_ts= end_ts - dias * 86_400_000

    all_candles = []
    current_start = start_ts
    limit = 500

    while current_start < end_ts:
        try:
            r = requests.get(
                f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params={"symbol": par, "interval": tf,
                        "startTime": current_start, "endTime": end_ts, "limit": limit},
                timeout=15,
            )
            data = r.json().get("data", []) or []
            if not data:
                break
            for c in data:
                try:
                    all_candles.append({
                        "ts":     int(c[0]),
                        "open":   float(c[1]),
                        "high":   float(c[2]),
                        "low":    float(c[3]),
                        "close":  float(c[4]),
                        "volume": float(c[5]),
                    })
                except Exception:
                    continue
            if len(data) < limit:
                break
            current_start = int(data[-1][0]) + ms_tf
            time.sleep(0.15)
        except Exception as e:
            log.warning(f"  {par} fetch error: {e}")
            break

    # deduplicar y ordenar
    seen = set()
    unique = []
    for c in sorted(all_candles, key=lambda x: x["ts"]):
        if c["ts"] not in seen:
            seen.add(c["ts"])
            unique.append(c)
    return unique

# ─────────────────────────────────────────────────
# INDICADORES (idénticos al bot real)
# ─────────────────────────────────────────────────

def calc_ema(prices: list, period: int):
    if len(prices) < period: return None
    k   = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_rsi(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1: return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    ag = sum(max(d, 0)      for d in deltas[:period]) / period
    al = sum(abs(min(d, 0)) for d in deltas[:period]) / period
    for d in deltas[period:]:
        ag = (ag * (period-1) + max(d, 0))      / period
        al = (al * (period-1) + abs(min(d, 0))) / period
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag/al), 2)

def calc_atr(highs, lows, closes, period: int = 7) -> float:
    if len(highs) < period + 1: return 0.0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(highs))]
    return sum(trs[-period:]) / period if len(trs) >= period else (sum(trs)/len(trs) if trs else 0)

def detectar_ob_fvg(candles: list):
    if len(candles) < 8: return False, False
    p = candles[-1]["close"]
    # OB+FVG Bull
    ob_bull = False
    for i in range(max(0, len(candles)-6), len(candles)-2):
        c = candles[i]
        if c["close"] < c["open"] and i+2 < len(candles):
            c1, c2 = candles[i+1], candles[i+2]
            if c1["close"]>c1["open"] and c2["close"]>c2["open"] and c2["high"]>c["high"]:
                ob_top = max(c["open"], c["close"])
                if c["low"] <= p <= ob_top * 1.01:
                    ob_bull = True; break
    fvg_bull = False
    for i in range(2, min(8, len(candles)-1)):
        idx = len(candles) - i
        if idx >= 2:
            gap = candles[idx]["low"] - candles[idx-2]["high"]
            if gap > 0 and gap / p > 0.001:
                fvg_bull = True; break
    # OB+FVG Bear
    ob_bear = False
    for i in range(max(0, len(candles)-6), len(candles)-2):
        c = candles[i]
        if c["close"] > c["open"] and i+2 < len(candles):
            c1, c2 = candles[i+1], candles[i+2]
            if c1["close"]<c1["open"] and c2["close"]<c2["open"] and c2["low"]<c["low"]:
                ob_bot = min(c["open"], c["close"])
                if ob_bot * 0.99 <= p <= c["high"]:
                    ob_bear = True; break
    fvg_bear = False
    for i in range(2, min(8, len(candles)-1)):
        idx = len(candles) - i
        if idx >= 2:
            gap = candles[idx-2]["low"] - candles[idx]["high"]
            if gap > 0 and gap / p > 0.001:
                fvg_bear = True; break
    return (ob_bull and fvg_bull), (ob_bear and fvg_bear)

def detectar_sweep(candles: list, lookback: int = 15):
    if len(candles) < lookback + 2: return False, False
    ventana = candles[-(lookback+2):-1]
    prev_h  = max(c["high"] for c in ventana[:-1])
    prev_l  = min(c["low"]  for c in ventana[:-1])
    last    = ventana[-1]
    sweep_bull = last["low"] < prev_l and last["close"] > last["open"]
    sweep_bear = last["high"] > prev_h and last["close"] < last["open"]
    return sweep_bull, sweep_bear

# ─────────────────────────────────────────────────
# MOTOR DE SEÑALES
# ─────────────────────────────────────────────────

def analizar(par, candles, idx, score_min, cond_inst_req, macro_bajando=False):
    if idx < 200: return None
    hist   = candles[max(0, idx-200) : idx+1]
    if len(hist) < 60: return None

    closes = [c["close"]  for c in hist]
    highs  = [c["high"]   for c in hist]
    lows   = [c["low"]    for c in hist]
    precio = closes[-1]

    atr_v  = calc_atr(highs, lows, closes, 7)
    if atr_v <= 0: return None

    ema21  = calc_ema(closes, 21); ema50 = calc_ema(closes, 50)
    rsi_v  = calc_rsi(closes)
    e_bull = ema21 and ema50 and ema21 > ema50
    e_bear = ema21 and ema50 and ema21 < ema50

    htf    = closes[:-24]
    htf_bull = htf_bear = False
    if len(htf) > 50:
        h21 = calc_ema(htf, 21); h50 = calc_ema(htf, 50)
        htf_bull = h21 and h50 and h21 > h50
        htf_bear = h21 and h50 and h21 < h50

    ob_fvg_bull, ob_fvg_bear = detectar_ob_fvg(hist)
    sw_bull, sw_bear          = detectar_sweep(hist)

    vols    = [c["volume"] for c in hist[-21:-1]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    vol_spike = hist[-1]["volume"] > avg_vol * 1.3 if avg_vol > 0 else False

    kz = (idx % 288) in range(84, 120)  # aproximación KZ Londres

    def score(lado, obfvg, sw, inst):
        s = 0
        if lado == "LONG":
            if obfvg:       s += 3
            if sw:          s += 2
            if rsi_v <= 40: s += 2
            elif rsi_v<=65: s += 1
            elif rsi_v > 72:s -= 1
            if e_bull:      s += 1
            if htf_bull:    s += 1
            if kz:          s += 1
            if vol_spike:   s += 1
        else:
            if obfvg:       s += 3
            if sw:          s += 2
            if rsi_v >= 65: s += 2
            elif rsi_v>=30: s += 1
            elif rsi_v < 28:s -= 1
            if e_bear:      s += 1
            if htf_bear:    s += 1
            if kz:          s += 1
            if vol_spike:   s += 1
        if cond_inst_req and not inst:
            s = 0
        return s

    inst_l = ob_fvg_bull or sw_bull
    inst_s = ob_fvg_bear or sw_bear
    sl_l   = score("LONG",  ob_fvg_bull, sw_bull, inst_l)
    sl_s   = score("SHORT", ob_fvg_bear, sw_bear, inst_s)

    lado = inst = None
    if sl_s >= score_min and e_bear and rsi_v > 28:
        lado = "SHORT"; inst = inst_s
    if sl_l >= score_min and e_bull and rsi_v < 72 and (not macro_bajando or par=="BTC-USDT"):
        if lado is None or sl_l >= sl_s:
            lado = "LONG"; inst = inst_l

    if lado is None: return None

    if lado == "LONG":
        sl_p = max(min(c["low"] for c in hist[-5:]) * 0.997, precio - atr_v * 1.2)
        dist = precio - sl_p; tp_p = precio + dist * 2.5
        if dist <= 0 or abs(tp_p - precio) / dist < MIN_RR: return None
        entrada = precio * (1 + SLIPPAGE)
    else:
        sl_p = min(max(c["high"] for c in hist[-5:]) * 1.003, precio + atr_v * 1.2)
        dist = sl_p - precio; tp_p = precio - dist * 2.5
        if dist <= 0 or abs(tp_p - precio) / dist < MIN_RR: return None
        entrada = precio * (1 - SLIPPAGE)

    return {"lado": lado, "entrada": entrada, "sl": sl_p, "tp": tp_p, "inst": inst}

# ─────────────────────────────────────────────────
# SIMULACIÓN
# ─────────────────────────────────────────────────

def backtest(pares_data: dict, score_min: int, cond_inst: bool,
             time_exit_v: int, macro_filter: bool, streak_sizing: bool,
             sinmov_exit: bool, nombre: str):
    n_total = max(len(v) for v in pares_data.values())
    balance = BALANCE_INIT
    posiciones = {}
    cooldown   = {}
    trades     = []
    streak     = []
    balance_ts = []

    for idx in range(200, n_total):
        # cerrar posiciones que alcanzaron SL/TP/time-exit
        cerradas = []
        for par, pos in list(posiciones.items()):
            candles = pares_data[par]
            if idx >= len(candles): cerradas.append(par); continue
            c  = candles[idx]
            s  = pos["señal"]; ent = s["entrada"]
            qty = (TRADE_USDT * LEVERAGE) / ent
            cerrar = False; pnl_f = 0.0; razon = ""
            vab = idx - pos["idx0"]

            # sin-movimiento
            if sinmov_exit and vab >= 24 and not s["inst"]:
                if abs(c["close"] - ent) / ent * 100 < 0.12:
                    pnl_f = qty*((c["close"]-ent) if s["lado"]=="LONG" else (ent-c["close"]))
                    pnl_f -= TRADE_USDT*LEVERAGE*COMMISSION*2
                    cerrar=True; razon="SIN-MOV"

            if not cerrar:
                if s["lado"] == "LONG":
                    if c["low"] <= s["sl"]:
                        pnl_f = qty*(s["sl"]*(1-SLIPPAGE)-ent) - TRADE_USDT*LEVERAGE*COMMISSION*2
                        cerrar=True; razon="SL"
                    elif c["high"] >= s["tp"]:
                        pnl_f = qty*(s["tp"]*(1-SLIPPAGE)-ent) - TRADE_USDT*LEVERAGE*COMMISSION*2
                        cerrar=True; razon="TP"
                else:
                    if c["high"] >= s["sl"]:
                        pnl_f = qty*(ent-s["sl"]*(1+SLIPPAGE)) - TRADE_USDT*LEVERAGE*COMMISSION*2
                        cerrar=True; razon="SL"
                    elif c["low"] <= s["tp"]:
                        pnl_f = qty*(ent-s["tp"]*(1+SLIPPAGE)) - TRADE_USDT*LEVERAGE*COMMISSION*2
                        cerrar=True; razon="TP"

            if not cerrar and vab >= time_exit_v:
                pnl_f = qty*((c["close"]-ent) if s["lado"]=="LONG" else (ent-c["close"]))
                pnl_f -= TRADE_USDT*LEVERAGE*COMMISSION*2
                cerrar=True; razon="TIME"

            if cerrar:
                balance += pnl_f; balance = max(balance, 0.01)
                ganado = pnl_f > 0
                trades.append({"pnl": pnl_f, "ganado": ganado, "razon": razon,
                                "par": par, "lado": s["lado"], "inst": s["inst"]})
                streak.append(ganado)
                if len(streak) > 5: streak.pop(0)
                cerradas.append(par)
                ts_str = datetime.fromtimestamp(candles[idx]["ts"]/1000, tz=timezone.utc).strftime("%m/%d %H:%M")
                log.debug(f"  {'✅' if ganado else '❌'} {par} {s['lado']} {razon} PnL={pnl_f:+.3f}")

        for par in cerradas: posiciones.pop(par, None)
        balance_ts.append(round(balance, 4))
        if len(posiciones) >= MAX_POSICIONES: continue

        # macro BTC
        macro_bajando = False
        if macro_filter and "BTC-USDT" in pares_data:
            btc = pares_data["BTC-USDT"]
            if idx >= 12:
                pn = btc[min(idx, len(btc)-1)]["close"]
                pp = btc[max(0, idx-12)]["close"]
                if pp > 0 and (pn-pp)/pp*100 <= -1.5: macro_bajando = True

        # streak mult
        mult = 1.0
        if streak_sizing and len(streak) >= 3:
            w = sum(streak); lo = len(streak) - w
            if w >= 4:   mult = 1.4
            elif w >= 3: mult = 1.2
            elif lo >= 4:mult = 0.6
            elif lo >= 3:mult = 0.8

        for par, candles in pares_data.items():
            if par in posiciones or idx >= len(candles): continue
            if cooldown.get(par, 0) > idx - COOLDOWN_VELAS: continue
            if len(posiciones) >= MAX_POSICIONES: break

            señal = analizar(par, candles, idx, score_min, cond_inst, macro_bajando)
            if señal and balance > TRADE_USDT:
                posiciones[par] = {"señal": señal, "idx0": idx}
                cooldown[par]   = idx

    # estadísticas
    n_t   = len(trades)
    if n_t == 0:
        return {"nombre": nombre, "trades": 0, "wr": 0, "pnl": 0, "balance": balance}

    wins  = sum(1 for t in trades if t["ganado"])
    pnl_t = sum(t["pnl"] for t in trades)
    pnl_w = [t["pnl"] for t in trades if t["ganado"]]
    pnl_l = [t["pnl"] for t in trades if not t["ganado"]]
    dias_total = n_total * 5 / 60 / 24

    # max drawdown
    running = BALANCE_INIT; peak = BALANCE_INIT; max_dd = 0.0
    for t in trades:
        running += t["pnl"]; peak = max(peak, running)
        dd = (peak - running) / peak * 100
        if dd > max_dd: max_dd = dd

    pf = abs(sum(pnl_w) / sum(pnl_l)) if sum(pnl_l) != 0 else 999

    stats = {
        "nombre":      nombre,
        "trades":      n_t,
        "wins":        wins,
        "wr":          round(wins/n_t*100, 1),
        "pnl":         round(pnl_t, 4),
        "balance":     round(balance, 2),
        "pnl_dia":     round(pnl_t / dias_total, 3),
        "avg_win":     round(sum(pnl_w)/len(pnl_w), 4) if pnl_w else 0,
        "avg_loss":    round(sum(pnl_l)/len(pnl_l), 4) if pnl_l else 0,
        "profit_factor": round(pf, 2),
        "max_dd":      round(max_dd, 1),
        "dias":        round(dias_total, 1),
        "sl_n":        sum(1 for t in trades if t["razon"]=="SL"),
        "tp_n":        sum(1 for t in trades if t["razon"]=="TP"),
        "sinmov_n":    sum(1 for t in trades if t["razon"]=="SIN-MOV"),
        "time_n":      sum(1 for t in trades if t["razon"]=="TIME"),
        "con_inst_n":  sum(1 for t in trades if t["inst"]),
        "con_inst_wr": round(sum(1 for t in trades if t["inst"] and t["ganado"]) /
                             max(sum(1 for t in trades if t["inst"]),1) * 100, 1),
        "balance_hist": balance_ts[-500:],
        "trades_list": [(t["par"], t["lado"], round(t["pnl"],4), t["razon"], t["ganado"])
                        for t in trades[-50:]],
    }
    return stats


# ─────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias",  type=int,   default=7)
    parser.add_argument("--par",   type=str,   default=None)
    parser.add_argument("--pares", type=str,   default=None)
    parser.add_argument("--tf",    type=str,   default="5m")
    parser.add_argument("--out",   type=str,   default="backtest_real_results.json")
    args = parser.parse_args()

    if args.par:
        pares = [args.par]
    elif args.pares:
        pares = args.pares.split(",")
    else:
        pares = PARES_DEFAULT

    print(f"{'='*60}")
    print(f"BACKTEST REAL — {args.dias} días — {len(pares)} pares — tf={args.tf}")
    print(f"{'='*60}\n")

    print("Descargando datos de BingX...")
    pares_data = {}
    for par in pares:
        candles = fetch_candles_real(par, args.dias, args.tf)
        if len(candles) >= 60:
            pares_data[par] = candles
            print(f"  ✅ {par}: {len(candles)} velas ({len(candles)*5/60:.0f}h)")
        else:
            print(f"  ⚠️ {par}: solo {len(candles)} velas — omitido")
        time.sleep(0.2)

    if not pares_data:
        print("❌ Sin datos. Verifica conectividad.")
        sys.exit(1)

    print(f"\n{'─'*60}")
    configs = [
        ("VIEJA (score=6)",        SCORE_MIN_VIEJA, False, TIME_EXIT_VIEJA, False, False, False),
        ("NUEVA score=8+inst",     SCORE_MIN_NUEVA, True,  TIME_EXIT_NUEVA, False, False, False),
        ("V5.5 completo",          SCORE_MIN_NUEVA, True,  TIME_EXIT_NUEVA, True,  True,  True),
    ]

    all_stats = []
    for cfg in configs:
        nombre = cfg[0]
        print(f"\nSimulando: {nombre}...")
        stats = backtest(pares_data, cfg[1], cfg[2], cfg[3], cfg[4], cfg[5], cfg[6], nombre)
        all_stats.append(stats)
        if stats["trades"] == 0:
            print(f"  Sin trades generados")
            continue
        print(f"  Trades: {stats['trades']}  WR: {stats['wr']}%  PnL: ${stats['pnl']:+.2f}  "
              f"PnL/día: ${stats['pnl_dia']:+.2f}  MaxDD: {stats['max_dd']}%  "
              f"PF: {stats['profit_factor']}")
        print(f"  SL:{stats['sl_n']} TP:{stats['tp_n']} TIME:{stats['time_n']} SIN-MOV:{stats['sinmov_n']}")
        print(f"  Con condición inst: {stats['con_inst_n']} trades, WR={stats['con_inst_wr']}%")

    print(f"\n{'='*60}")
    print("RESUMEN COMPARATIVO")
    print(f"{'='*60}")
    print(f"{'Config':<25} {'Trades':>7} {'WR%':>6} {'PnL':>9} {'$/día':>7} {'MaxDD':>7} {'PF':>6}")
    print("─"*65)
    for s in all_stats:
        if s["trades"] == 0:
            print(f"{s['nombre']:<25} {'Sin trades':>50}")
            continue
        print(f"{s['nombre']:<25} {s['trades']:>7} {s['wr']:>6.1f} ${s['pnl']:>+8.2f} "
              f"${s['pnl_dia']:>+6.2f} {s['max_dd']:>6.1f}% {s['profit_factor']:>6.2f}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"configs": all_stats, "pares": list(pares_data.keys()),
                   "dias": args.dias, "tf": args.tf}, f, indent=2)
    print(f"\n✅ Resultados guardados en {args.out}")
    print("\nPara graficar el balance:")
    print("  python -c \"import json; d=json.load(open('backtest_real_results.json'));"
          " print(d['configs'][2]['balance_hist'][-10:])\"")
