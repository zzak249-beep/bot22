"""
analizar_bellsz.py — Bellsz Bot v2.0 [SIMPLIFICADO]
====================================================
3 condiciones. Sin score mínimo. Sin ADX. Sin OB/FVG/BOS.

  LONG:  EMA9 > EMA21  +  RSI entre 35-65  +  RVOL >= 1.2
  SHORT: EMA9 < EMA21  +  RSI entre 35-65  +  RVOL >= 1.2

  Entrada en pullback: precio toca EMA21 y rebota.
  SL bajo/sobre el swing reciente. TP = dist × 3.
"""

import logging
import os
import time
from datetime import datetime, timezone
import concurrent.futures

import config
import exchange

log = logging.getLogger("analizar")

_cooldown_ts: dict = {}


# ══════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════

def _ema(prices, p):
    if len(prices) < p:
        return None
    k = 2 / (p + 1)
    v = sum(prices[:p]) / p
    for x in prices[p:]:
        v = x * k + v * (1 - k)
    return v

def _rsi(prices, p=14):
    if len(prices) < p + 1:
        return 50.0
    d  = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    ag = sum(max(x, 0)      for x in d[:p]) / p
    al = sum(abs(min(x, 0)) for x in d[:p]) / p
    for x in d[p:]:
        ag = (ag*(p-1) + max(x, 0))      / p
        al = (al*(p-1) + abs(min(x, 0))) / p
    return 100.0 if al == 0 else round(100 - 100/(1 + ag/al), 2)

def _atr(hi, lo, cl, p=14):
    if len(hi) < p + 1:
        return 0.0
    trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
           for i in range(1, len(hi))]
    return sum(trs[-p:]) / p

def _sma(v, p):
    return sum(v[-p:]) / p if len(v) >= p else None

def _supertrend(hi, lo, cl, factor=3.0, p=10):
    if len(cl) < p + 2:
        return False, False
    atrs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
            for i in range(1, len(cl))]
    av = sum(atrs[:p]) / p
    as2 = [av]
    for a in atrs[p:]:
        av = (av*(p-1) + a) / p
        as2.append(av)
    n = len(as2); off = len(cl) - n
    ub = [0.]*n; lb = [0.]*n; dr = [1]*n; st = [0.]*n
    for i in range(n):
        ci = i + off; h2 = (hi[ci] + lo[ci]) / 2
        u = h2 + factor*as2[i]; l = h2 - factor*as2[i]
        ub[i] = min(u, ub[i-1]) if i > 0 and cl[ci-1] < ub[i-1] else u
        lb[i] = max(l, lb[i-1]) if i > 0 and cl[ci-1] > lb[i-1] else l
        if i == 0:   dr[i] = 1
        elif st[i-1] == ub[i-1]: dr[i] = 1 if cl[ci] < ub[i] else -1
        else:        dr[i] = -1 if cl[ci] > lb[i] else 1
        st[i] = ub[i] if dr[i] == 1 else lb[i]
    return dr[-1] < 0, dr[-1] > 0  # (bull, bear)


# ══════════════════════════════════════════════════════
# KILL ZONES + COOLDOWN
# ══════════════════════════════════════════════════════

def en_killzone() -> dict:
    m    = datetime.now(timezone.utc)
    mins = m.hour * 60 + m.minute
    london = config.KZ_LONDON_START <= mins < config.KZ_LONDON_END
    ny     = config.KZ_NY_START     <= mins < config.KZ_NY_END
    return {
        "in_kz":  london or ny,
        "nombre": "LONDON" if london else ("NY" if ny else "FUERA"),
    }

def _cooldown_ok(par):
    cooldown = getattr(config, "COOLDOWN_VELAS", 5) * 300
    return (time.time() - _cooldown_ts.get(par, 0)) >= cooldown

def registrar_senal_ts(par):
    _cooldown_ts[par] = time.time()

# compatibilidad con main_bellsz
def registrar_trade_kz(kz, ganado): pass
def actualizar_macro_btc():         pass
def invalidar_niveles(par):         pass


# ══════════════════════════════════════════════════════
# ANÁLISIS PRINCIPAL — 3 condiciones
# ══════════════════════════════════════════════════════

def analizar_par(par: str):
    try:
        if not _cooldown_ok(par):
            return None

        candles = exchange.get_candles(par, config.TIMEFRAME, config.CANDLES_LIMIT)
        if len(candles) < 50:
            return None

        cl   = [c["close"]  for c in candles]
        hi   = [c["high"]   for c in candles]
        lo   = [c["low"]    for c in candles]
        vols = [c["volume"] for c in candles]
        precio = cl[-1]
        if precio <= 0:
            return None

        # ── Indicadores ─────────────────────────────────────
        e_fast = getattr(config, "EMA_FAST", 9)
        e_slow = getattr(config, "EMA_SLOW", 21)

        e9  = _ema(cl, e_fast)
        e21 = _ema(cl, e_slow)
        if not e9 or not e21:
            return None

        atr = _atr(hi, lo, cl)
        if atr <= 0:
            return None

        rv      = _rsi(cl[-30:])
        avg20   = _sma(vols[:-1], 20) or 1
        rvol    = vols[-1] / avg20

        # Supertrend para filtro de dirección
        st_bull, st_bear = _supertrend(hi, lo, cl)

        c  = candles[-1]
        cp = candles[-2]

        # Parámetros via Railway
        rvol_min   = float(os.getenv("VOL_MULT",   "1.2"))
        body_ratio = float(os.getenv("BODY_RATIO", "0.25"))
        rsi_lo     = float(os.getenv("RSI_LO",     "35"))
        rsi_hi     = float(os.getenv("RSI_HI",     "65"))
        ema_tol    = float(os.getenv("EMA_TOL",    "0.003"))

        body = abs(c["close"] - c["open"])
        rng  = c["high"] - c["low"] if c["high"] > c["low"] else 1e-9

        # ── CONDICIÓN LONG ───────────────────────────────────
        # EMA9 > EMA21 + ST alcista + precio tocó EMA21 y rebotó + vol + RSI
        long_ok = (
            e9  > e21 * (1 + ema_tol * 0.2)       and  # EMA alineada
            st_bull                                 and  # Supertrend alcista
            cp["low"]  <= e21 * (1 + ema_tol)      and  # pullback tocó EMA21
            c["close"] > e21                        and  # cerró sobre EMA21
            c["close"] > c["open"]                  and  # vela alcista
            rvol >= rvol_min                         and  # volumen
            body / rng >= body_ratio                 and  # convicción
            rsi_lo <= rv <= rsi_hi                       # RSI ok
        )

        # ── CONDICIÓN SHORT ──────────────────────────────────
        short_ok = (
            e9  < e21 * (1 - ema_tol * 0.2)       and
            st_bear                                 and
            cp["high"] >= e21 * (1 - ema_tol)      and
            c["close"] < e21                        and
            c["close"] < c["open"]                  and
            rvol >= rvol_min                         and
            body / rng >= body_ratio                 and
            rsi_lo <= rv <= rsi_hi
        )

        if not long_ok and not short_ok:
            return None

        if short_ok and getattr(config, "SOLO_LONG", False):
            return None

        lado = "LONG" if long_ok else "SHORT"

        # ── SL / TP ──────────────────────────────────────────
        rec = candles[-16:-1]
        buf = atr * 0.2

        if lado == "LONG":
            sl_swing = min(c2["low"] for c2 in rec) - buf if rec else precio - atr * 1.5
            sl_ema   = e21 - buf
            sl       = max(sl_swing, sl_ema)
            if precio - sl > 3 * atr:
                sl = precio - atr * getattr(config, "SL_ATR_MULT", 1.5)
        else:
            sl_swing = max(c2["high"] for c2 in rec) + buf if rec else precio + atr * 1.5
            sl_ema   = e21 + buf
            sl       = min(sl_swing, sl_ema)
            if sl - precio > 3 * atr:
                sl = precio + atr * getattr(config, "SL_ATR_MULT", 1.5)

        dist = abs(precio - sl)
        if dist <= 0:
            return None

        tp_mult  = getattr(config, "TP_DIST_MULT",  3.0)
        tp1_mult = getattr(config, "TP1_DIST_MULT", 1.5)
        min_rr   = getattr(config, "MIN_RR",        2.0)

        tp  = (precio + dist * tp_mult)  if lado == "LONG" else (precio - dist * tp_mult)
        tp1 = (precio + dist * tp1_mult) if lado == "LONG" else (precio - dist * tp1_mult)
        rr  = abs(tp - precio) / dist
        if rr < min_rr:
            return None

        # ── Score (priorización) ─────────────────────────────
        kz    = en_killzone()
        score = 3
        if rvol >= 2.0:  score += 1
        if rvol >= 3.0:  score += 1
        if kz["in_kz"]:  score += 1

        motivos = [
            "EMA_PULLBACK",
            f"RVOL×{rvol:.1f}",
            f"RSI{rv:.0f}",
            "ST_BULL" if st_bull else "ST_BEAR",
        ]
        if kz["in_kz"]:
            motivos.append(f"KZ_{kz['nombre']}")

        registrar_senal_ts(par)

        log.info(
            f"[SEÑAL] {lado:5s} {par:15s} "
            f"E9={e9:.4f} E21={e21:.4f} "
            f"RVOL×{rvol:.1f} RSI={rv:.0f} "
            f"score={score} SL={sl:.6f} TP={tp:.6f} RR={rr:.2f}"
        )

        return {
            "par": par, "lado": lado, "precio": precio,
            "sl":  round(sl,  8), "tp":  round(tp,  8),
            "tp1": round(tp1, 8), "tp2": round(tp,  8),
            "atr": round(atr, 8), "dist_sl": round(dist, 8),
            "score": score, "rsi": rv, "rr": round(rr, 2),
            "motivos": motivos, "kz": kz["nombre"],
            "htf": "NEUTRAL", "htf_4h": "NEUTRAL",
            "purga_nivel": "EMA_PULLBACK", "purga_peso": score,
            "vol_ratio": round(rvol, 2),
            "bsl_h1": 0.0, "ssl_h1": 0.0, "bsl_h4": 0.0,
            "ssl_h4": 0.0, "bsl_d":  0.0, "ssl_d":  0.0,
            "ema_r": round(e9,  8), "ema_l": round(e21, 8),
            "vwap": 0.0, "sobre_vwap": False,
            "fvg_top": 0, "fvg_bottom": 0, "fvg_rellenado": True,
            "ob_bull": False, "ob_bear": False,
            "ob_fvg_bull": False, "ob_fvg_bear": False, "ob_mitigado": True,
            "bos_bull": False, "bos_bear": False,
            "choch_bull": False, "choch_bear": False,
            "sweep_bull": False, "sweep_bear": False,
            "patron": None, "vela_conf": True,
            "premium": False, "discount": False,
            "displacement": False, "macd_hist": 0,
            "asia_valido": True, "adx": 25.0, "inducement": False,
            "liq_bull": False, "liq_bear": False,
            "liq_z_up": rvol, "liq_z_dn": rvol,
            "liq_plot_trnd": 1 if lado == "LONG" else -1,
        }

    except Exception as e:
        log.error(f"analizar_par {par}: {e}")
        return None


def analizar_todos(pares: list, workers: int = 6) -> list:
    senales = []
    w = min(workers, len(pares), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=w) as ex:
        futuros = {ex.submit(analizar_par, p): p for p in pares}
        for fut in concurrent.futures.as_completed(futuros):
            try:
                r = fut.result()
                if r:
                    senales.append(r)
            except Exception as e:
                log.error(f"thread: {e}")
    senales.sort(key=lambda x: x["score"], reverse=True)
    return senales
