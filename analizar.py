"""
analizar.py — SMC Bot v7.0 [BACKTEST-PROVEN]
=============================================

CAMBIO FUNDAMENTAL basado en backtest_v6_results.json + bt_v4.py:

PROBLEMA RAIZ (todas las versiones anteriores):
  TP era precio +/- ATR x mult  =>  demasiado lejos  =>  SL_rate 66-100%  =>  nunca rentable
  V1: SL=244 TP=100  =>  66% SL rate
  V4: SL=51  TP=0    =>  98% SL rate (peor: SL estrecho + TP inalcanzable)

SOLUCION bt_v4 (probada con datos reales Binance, 21 dias, 8 pares):
  TP = precio +/- dist_SL x TP_DIST_MULT
  donde dist_SL = abs(precio - SL_estructural)
  => TP proporcional al riesgo real, no a ATR arbitrario

  Ganador: TP=1.2xdist => PnL=+$70.59 WR=55.6% PF=1.40 (score>=4)
  2do:     TP=0.8xdist => PnL=+$61.78 WR=64.2% PF=1.35 (mas hits)

OTROS FIXES v7.0:
  * base_long/short: KZ NO obligatorio (backtest no lo requeria)
  * HTF: NEUTRAL permite operar - solo BULL/BEAR explicito bloquea el contrario
  * Choppiness: mantenido
  * OB mitigado: mantenido (v6.0 fix correcto)
  * FVG rellenado: mantenido (v6.1 fix correcto)
  * MIN_RR=1.0: bajado de 2.0 — con TP=1.2xdist el R:R es 1.2x siempre
"""

import logging
import time
from datetime import datetime, timezone
import concurrent.futures

import config
import exchange

log = logging.getLogger("analizar")

_cooldown_ts: dict = {}
_kz_stats:    dict = {}


def registrar_senal_ts(par: str):
    _cooldown_ts[par] = time.time()


def registrar_trade_kz(kz: str, ganado: bool):
    s = _kz_stats.setdefault(kz, {"trades": 0, "wins": 0})
    s["trades"] += 1
    s["wins"]   += int(ganado)


def _cooldown_ok(par: str) -> bool:
    ultimo = _cooldown_ts.get(par, 0)
    if ultimo == 0:
        return True
    return (time.time() - ultimo) >= config.COOLDOWN_VELAS * 300


_macro_btc_cache: dict = {"htf": "NEUTRAL", "ts": 0.0}


def actualizar_macro_btc():
    if time.time() - _macro_btc_cache["ts"] < 900:
        return
    try:
        ch = exchange.get_candles("BTC-USDT", "4h", 50)
        if len(ch) < 50:
            return
        cl = [c["close"] for c in ch]
        ef = calc_ema(cl, 21)
        es = calc_ema(cl, 50)
        if ef and es:
            if   ef > es * 1.005: _macro_btc_cache["htf"] = "BULL"
            elif ef < es * 0.995: _macro_btc_cache["htf"] = "BEAR"
            else:                 _macro_btc_cache["htf"] = "NEUTRAL"
        _macro_btc_cache["ts"] = time.time()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════════════

def calc_ema(prices: list, period: int):
    if len(prices) < period:
        return None
    k   = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def calc_rsi(prices: list, period: int = 14):
    if len(prices) < period + 1:
        return None
    d  = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    ag = sum(max(x, 0)      for x in d[:period]) / period
    al = sum(abs(min(x, 0)) for x in d[:period]) / period
    for x in d[period:]:
        ag = (ag * (period - 1) + max(x, 0))      / period
        al = (al * (period - 1) + abs(min(x, 0))) / period
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 2)


def calc_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return 0.0
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i-1]),
               abs(lows[i]  - closes[i-1]))
           for i in range(1, len(highs))]
    return sum(trs[-period:]) / period if len(trs) >= period else (sum(trs) / len(trs) if trs else 0.0)


def calc_macd(closes: list, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None, None
    macd_hist = []
    for i in range(slow, len(closes)):
        ef = calc_ema(closes[:i+1], fast)
        es = calc_ema(closes[:i+1], slow)
        if ef and es:
            macd_hist.append(ef - es)
    if len(macd_hist) < signal:
        return None, None, None
    macd_line   = macd_hist[-1]
    signal_line = calc_ema(macd_hist, signal)
    histogram   = macd_line - (signal_line or 0)
    return macd_line, signal_line, histogram


def calc_vwap(candles: list) -> float:
    hoy = datetime.now(timezone.utc).date()
    vc  = [c for c in candles
           if datetime.fromtimestamp(c["ts"] / 1000, tz=timezone.utc).date() == hoy]
    if not vc:
        vc = candles[-50:]
    tp_vol    = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"] for c in vc)
    vol_total = sum(c["volume"] for c in vc)
    return tp_vol / vol_total if vol_total > 0 else candles[-1]["close"]


def calc_pivotes(ph, pl, pc):
    pp = (ph + pl + pc) / 3
    return {"PP": pp, "R1": 2*pp-pl, "R2": pp+(ph-pl),
            "S1": 2*pp-ph, "S2": pp-(ph-pl)}


# ══════════════════════════════════════════════════════════════
# FILTROS
# ══════════════════════════════════════════════════════════════

def es_trending(candles: list, n: int = 20) -> bool:
    if len(candles) < n + 1:
        return True
    w = candles[-(n+1):]
    s = sum(max(w[i]["high"] - w[i]["low"],
                abs(w[i]["high"] - w[i-1]["close"]),
                abs(w[i]["low"]  - w[i-1]["close"]))
            for i in range(1, len(w)))
    rng = max(c["high"] for c in w) - min(c["low"] for c in w)
    return (s / rng / n * 100) < 61.8 if rng > 0 else False


def volumen_ok(candles: list) -> bool:
    if len(candles) < 21:
        return True
    vols = [c["volume"] for c in candles[-21:-1]]
    avg  = sum(vols) / len(vols) if vols else 0
    return avg <= 0 or candles[-1]["volume"] / avg >= 0.40


# ══════════════════════════════════════════════════════════════
# MTF
# ══════════════════════════════════════════════════════════════

def tendencia_htf(par: str, timeframe: str = None, candles_n: int = None) -> str:
    """
    Tendencia 1h. Umbral 0.1% (era 0.2%) para evitar NEUTRAL en mercados moderados.
    NEUTRAL no bloquea — solo suma menos puntos de score.
    """
    tf = timeframe or config.MTF_TIMEFRAME
    n  = candles_n or config.MTF_CANDLES
    if not config.MTF_ACTIVO:
        return "NEUTRAL"
    try:
        ch = exchange.get_candles(par, tf, n)
        if len(ch) < 50:
            return "NEUTRAL"
        cl = [c["close"] for c in ch]
        ef = calc_ema(cl, config.EMA_FAST)
        es = calc_ema(cl, config.EMA_SLOW)
        if ef is None or es is None:
            return "NEUTRAL"
        if ef > es * 1.001:   return "BULL"
        if ef < es * 0.999:   return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def tendencia_4h(par: str) -> str:
    if not getattr(config, "MTF_4H_ACTIVO", True):
        return "NEUTRAL"
    try:
        ch = exchange.get_candles(par, "4h", 50)
        if len(ch) < 30:
            return "NEUTRAL"
        cl = [c["close"] for c in ch]
        ef = calc_ema(cl, 21)
        es = calc_ema(cl, 50)
        if ef is None or es is None:
            return "NEUTRAL"
        if ef > es * 1.003:   return "BULL"
        if ef < es * 0.997:   return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


# ══════════════════════════════════════════════════════════════
# SMC ESTRUCTURAS
# ══════════════════════════════════════════════════════════════

def get_rango_asia(candles: list) -> dict:
    r = {"high": 0.0, "low": 999_999_999.0, "valido": False}
    if not config.ASIA_RANGE_ACTIVO:
        return r
    ac = [c for c in candles
          if 0 <= (datetime.fromtimestamp(c["ts"] / 1000, tz=timezone.utc).hour * 60
                   + datetime.fromtimestamp(c["ts"] / 1000, tz=timezone.utc).minute) < 240]
    if len(ac) >= 3:
        r.update({"high": max(c["high"] for c in ac),
                  "low":  min(c["low"]  for c in ac),
                  "valido": True})
    return r


def detectar_order_blocks(candles: list) -> dict:
    r = {
        "bull_ob": False, "bull_ob_top": 0.0, "bull_ob_bottom": 0.0,
        "bear_ob": False, "bear_ob_top": 0.0, "bear_ob_bottom": 0.0,
        "ob_mitigado": False, "ob_fvg_bull": False, "ob_fvg_bear": False,
    }
    if not config.OB_ACTIVO or len(candles) < 5:
        return r
    precio = candles[-1]["close"]
    lb     = min(config.OB_LOOKBACK, len(candles) - 2)
    b      = candles[-(lb + 2):-1]
    for i in range(len(b) - 3, 1, -1):
        c = b[i]
        if c["close"] < c["open"] and not r["bull_ob"] and i + 2 < len(b):
            c1, c2 = b[i+1], b[i+2]
            if c1["close"] > c1["open"] and c2["close"] > c2["open"] and c2["high"] > c["high"]:
                ob_top    = max(c["open"], c["close"])
                ob_bottom = c["low"]
                if precio >= ob_bottom * 0.998:
                    r.update({"bull_ob": True, "bull_ob_top": ob_top, "bull_ob_bottom": ob_bottom})
        if c["close"] > c["open"] and not r["bear_ob"] and i + 2 < len(b):
            c1, c2 = b[i+1], b[i+2]
            if c1["close"] < c1["open"] and c2["close"] < c2["open"] and c2["low"] < c["low"]:
                ob_top    = c["high"]
                ob_bottom = min(c["open"], c["close"])
                if precio <= ob_top * 1.002:
                    r.update({"bear_ob": True, "bear_ob_top": ob_top, "bear_ob_bottom": ob_bottom})
        if r["bull_ob"] and r["bear_ob"]:
            break
    return r


def detectar_fvg(candles: list) -> dict:
    r = {"bull_fvg": False, "bear_fvg": False,
         "fvg_top": 0.0, "fvg_bottom": 0.0,
         "fvg_rellenado": False, "en_zona": False}
    if len(candles) < 3:
        return r
    precio = candles[-1]["close"]
    for i in range(len(candles) - 1, max(len(candles) - 20, 2) - 1, -1):
        c0, c2 = candles[i], candles[i - 2]
        gap_up   = c0["low"] - c2["high"]
        gap_down = c2["low"] - c0["high"]
        if gap_up > config.FVG_MIN_PIPS:
            bot      = c2["high"]
            top      = c0["low"]
            mitigado = precio < bot * 0.998
            en_zona  = bot * 0.999 <= precio <= top * 1.001
            r.update({"bull_fvg": not mitigado, "fvg_top": top,
                      "fvg_bottom": bot, "fvg_rellenado": mitigado, "en_zona": en_zona})
            break
        if gap_down > config.FVG_MIN_PIPS:
            top      = c2["low"]
            bot      = c0["high"]
            mitigado = precio > top * 1.002
            en_zona  = bot * 0.999 <= precio <= top * 1.001
            r.update({"bear_fvg": not mitigado, "fvg_top": top,
                      "fvg_bottom": bot, "fvg_rellenado": mitigado, "en_zona": en_zona})
            break
    return r


def detectar_bos_choch(candles: list) -> dict:
    r = {"bos_bull": False, "bos_bear": False,
         "choch_bull": False, "choch_bear": False}
    if not config.BOS_ACTIVO or len(candles) < 20:
        return r
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    precio = closes[-1]
    lb     = min(50, len(candles))
    sh, sl = [], []
    for i in range(2, lb - 2):
        idx = len(candles) - lb + i
        if all(highs[idx] > highs[idx-k] and highs[idx] > highs[idx+k] for k in range(1, 3)):
            sh.append(highs[idx])
        if all(lows[idx] < lows[idx-k] and lows[idx] < lows[idx+k] for k in range(1, 3)):
            sl.append(lows[idx])
    if sh and precio > sh[-1]:
        r["bos_bull"] = True
        if len(sh) >= 2 and sh[-1] < sh[-2]:
            r["choch_bull"] = True
    if sl and precio < sl[-1]:
        r["bos_bear"] = True
        if len(sl) >= 2 and sl[-1] > sl[-2]:
            r["choch_bear"] = True
    return r


def detectar_sweep(candles: list) -> dict:
    r = {"sweep_bull": False, "sweep_bear": False}
    if not getattr(config, "SWEEP_ACTIVO", True) or len(candles) < 10:
        return r
    lb     = min(getattr(config, "SWEEP_LOOKBACK", 20), len(candles) - 2)
    previo = candles[-(lb+1):-1]
    actual = candles[-1]
    max_p  = max(c["high"] for c in previo)
    min_p  = min(c["low"]  for c in previo)
    if actual["low"]  < min_p and actual["close"] > min_p: r["sweep_bull"] = True
    if actual["high"] > max_p and actual["close"] < max_p: r["sweep_bear"] = True
    return r


def detectar_patron_vela(candles: list) -> dict:
    r = {"patron": None, "confianza": 0, "lado": None}
    if len(candles) < 2:
        return r
    c    = candles[-1]
    prev = candles[-2]
    rng  = c["high"] - c["low"]
    if rng <= 0:
        return r
    body       = abs(c["close"] - c["open"])
    body_pct   = body / rng
    upper_wick = c["high"] - max(c["open"], c["close"])
    lower_wick = min(c["open"], c["close"]) - c["low"]
    ratio      = getattr(config, "PINBAR_RATIO", 0.50)
    if lower_wick / rng >= ratio and body_pct < 0.35:
        return {"patron": "PIN_BAR", "confianza": 2, "lado": "LONG"}
    if upper_wick / rng >= ratio and body_pct < 0.35:
        return {"patron": "PIN_BAR", "confianza": 2, "lado": "SHORT"}
    if (c["close"] > c["open"] and c["close"] > prev["open"] and
            c["open"] < prev["close"] and body > abs(prev["close"] - prev["open"])):
        return {"patron": "ENGULFING", "confianza": 2, "lado": "LONG"}
    if (c["close"] < c["open"] and c["close"] < prev["open"] and
            c["open"] > prev["close"] and body > abs(prev["close"] - prev["open"])):
        return {"patron": "ENGULFING", "confianza": 2, "lado": "SHORT"}
    return r


def confirmar_vela(candles: list, lado: str) -> bool:
    if not config.VELA_CONFIRMACION or len(candles) < 2:
        return False
    c    = candles[-1]
    rng  = c["high"] - c["low"]
    if rng <= 0:
        return False
    body = abs(c["close"] - c["open"])
    bp   = body / rng
    uw   = c["high"] - max(c["open"], c["close"])
    lw   = min(c["open"], c["close"]) - c["low"]
    prev = candles[-2]
    if lado == "LONG":
        return (c["close"] > c["open"] and bp > 0.50) or \
               (lw / rng > 0.60) or \
               (c["close"] > prev["open"] and c["open"] <= prev["close"])
    return (c["close"] < c["open"] and bp > 0.50) or \
           (uw / rng > 0.60) or \
           (c["close"] < prev["open"] and c["open"] >= prev["close"])


def detectar_eqh_eql(candles: list) -> dict:
    r = {"is_eqh": False, "eqh_price": 0.0, "is_eql": False, "eql_price": 0.0}
    if len(candles) < config.EQ_LOOKBACK:
        return r
    highs  = [c["high"] for c in candles]
    lows   = [c["low"]  for c in candles]
    length = config.EQ_PIVOT_LEN
    thr    = config.EQ_THRESHOLD
    n, lb  = len(highs), config.EQ_LOOKBACK
    ph_list, pl_list = [], []

    def phigh(h, l, i):
        if i < l or i + l >= len(h): return None
        v = h[i]
        return v if all(h[j] < v for j in range(i-l, i+l+1) if j != i) else None

    def plow(lo, l, i):
        if i < l or i + l >= len(lo): return None
        v = lo[i]
        return v if all(lo[j] > v for j in range(i-l, i+l+1) if j != i) else None

    for i in range(max(length, n - lb - length), n - length):
        ph = phigh(highs, length, i)
        if ph: ph_list.append(ph)
        pl = plow(lows, length, i)
        if pl: pl_list.append(pl)

    if len(ph_list) >= 2:
        for i in range(len(ph_list) - 1, 0, -1):
            for j in range(i - 1, max(i - 10, -1), -1):
                if abs(ph_list[i] - ph_list[j]) / ph_list[i] * 100 <= thr:
                    r.update({"is_eqh": True, "eqh_price": ph_list[i]})
                    break
            if r["is_eqh"]: break

    if len(pl_list) >= 2:
        for i in range(len(pl_list) - 1, 0, -1):
            for j in range(i - 1, max(i - 10, -1), -1):
                if abs(pl_list[i] - pl_list[j]) / pl_list[i] * 100 <= thr:
                    r.update({"is_eql": True, "eql_price": pl_list[i]})
                    break
            if r["is_eql"]: break
    return r


def premium_discount_zone(candles: list) -> dict:
    r = {"premium": False, "discount": False, "zona_pct": 50.0}
    if not getattr(config, "PREMIUM_DISCOUNT_ACTIVO", True) or len(candles) < 10:
        return r
    lb    = min(getattr(config, "PREMIUM_DISCOUNT_LB", 50), len(candles))
    rec   = candles[-lb:]
    max_h = max(c["high"] for c in rec)
    min_l = min(c["low"]  for c in rec)
    rng   = max_h - min_l
    if rng <= 0:
        return r
    precio = candles[-1]["close"]
    zona   = (precio - min_l) / rng * 100
    return {"premium": zona >= 60, "discount": zona <= 40, "zona_pct": round(zona, 1)}


def en_killzone() -> dict:
    ahora = datetime.now(timezone.utc)
    tim   = ahora.hour * 60 + ahora.minute
    asia   = config.KZ_ASIA_START   <= tim < config.KZ_ASIA_END
    london = config.KZ_LONDON_START <= tim < config.KZ_LONDON_END
    ny     = config.KZ_NY_START     <= tim < config.KZ_NY_END
    return {
        "in_asia": asia, "in_london": london, "in_ny": ny,
        "in_kz":   asia or london or ny,
        "nombre":  "ASIA" if asia else ("LONDON" if london else ("NY" if ny else "FUERA")),
    }


def calcular_pivotes_diarios(candles_d: list):
    if len(candles_d) < 2:
        return None
    p = candles_d[-2]
    return calc_pivotes(p["high"], p["low"], p["close"])


# ══════════════════════════════════════════════════════════════
# SL ESTRUCTURAL
# ══════════════════════════════════════════════════════════════

def _swing_low(candles: list, n: int = 10) -> float:
    if len(candles) < n + 2:
        return 0.0
    return min(c["low"] for c in candles[-(n+1):-1])


def _swing_high(candles: list, n: int = 10) -> float:
    if len(candles) < n + 2:
        return 0.0
    return max(c["high"] for c in candles[-(n+1):-1])


def _calcular_sl_estructural(candles: list, ob: dict, lado: str,
                               atr: float, precio: float) -> float:
    """
    SL = swing low/high estructural con buffer.
    ATR solo como floor minimo para evitar ruido.
    """
    if lado == "LONG":
        swing   = _swing_low(candles, 15)
        sl_ob   = ob["bull_ob_bottom"] * 0.997 if ob["bull_ob"] else 0
        sl_sw   = swing * 0.997 if swing > 0 else 0
        sl_atr  = precio - atr * config.SL_ATR_MULT
        cands   = [x for x in [sl_ob, sl_sw] if 0 < x < precio]
        sl_estr = max(cands) if cands else sl_atr
        sl_min  = precio - atr * 0.5   # no mas cerca de 0.5 ATR
        return min(sl_estr, sl_min)
    else:
        swing   = _swing_high(candles, 15)
        sl_ob   = ob["bear_ob_top"] * 1.003 if ob["bear_ob"] else 0
        sl_sw   = swing * 1.003 if swing > 0 else 0
        sl_atr  = precio + atr * config.SL_ATR_MULT
        cands   = [x for x in [sl_ob, sl_sw] if x > precio]
        sl_estr = min(cands) if cands else sl_atr
        sl_max  = precio + atr * 0.5
        return max(sl_estr, sl_max)


# ══════════════════════════════════════════════════════════════
# TP BASADO EN DIST_SL — CAMBIO CRITICO v7.0
# ══════════════════════════════════════════════════════════════

def _calcular_tp(precio: float, sl: float, lado: str) -> tuple:
    """
    TP = precio +/- dist_SL x TP_DIST_MULT

    CRITICO: este es el cambio que hace el bot rentable segun bt_v4.
    Ganador probado: TP=1.2xdist => WR=55.6%, PF=1.40

    TP1 = 0.5xdist para toma parcial temprana.
    """
    dist     = abs(precio - sl)
    tp_mult  = getattr(config, "TP_DIST_MULT",  1.2)
    tp1_mult = getattr(config, "TP1_DIST_MULT", 0.5)

    if lado == "LONG":
        tp  = precio + dist * tp_mult
        tp1 = precio + dist * tp1_mult
    else:
        tp  = precio - dist * tp_mult
        tp1 = precio - dist * tp1_mult

    return tp, tp1


# ══════════════════════════════════════════════════════════════
# SEÑAL PRINCIPAL v7.0
# ══════════════════════════════════════════════════════════════

def analizar_par(par: str):
    try:
        candles = exchange.get_candles(par, config.TIMEFRAME, config.CANDLES_LIMIT)
        if len(candles) < 80:
            return None

        if not _cooldown_ok(par):
            return None

        if not es_trending(candles, 20):
            return None

        if not volumen_ok(candles):
            return None

        cl     = [c["close"] for c in candles]
        hi     = [c["high"]  for c in candles]
        lo     = [c["low"]   for c in candles]
        precio = cl[-1]
        if precio <= 0:
            return None

        atr = calc_atr(hi, lo, cl, config.ATR_PERIOD)
        if atr <= 0:
            return None
        if atr / precio * 100 < 0.03:
            return None

        ef       = calc_ema(cl, config.EMA_FAST)
        es_      = calc_ema(cl, config.EMA_SLOW)
        ef_loc   = calc_ema(cl, getattr(config, "EMA_LOCAL_FAST", 9))
        es_loc   = calc_ema(cl, getattr(config, "EMA_LOCAL_SLOW", 21))
        bull5    = ef is not None and es_ is not None and ef > es_ * 1.001
        bear5    = ef is not None and es_ is not None and ef < es_ * 0.999
        bull_loc = ef_loc is not None and es_loc is not None and ef_loc > es_loc * 1.0005
        bear_loc = ef_loc is not None and es_loc is not None and ef_loc < es_loc * 0.9995

        rsi   = calc_rsi(cl, config.RSI_PERIOD) or 50.0
        rsi_l = 25 <= rsi <= config.RSI_BUY_MAX
        rsi_s = config.RSI_SELL_MIN <= rsi <= 75

        _, _, macd_hist = calc_macd(cl)
        macd_bull = macd_hist is not None and macd_hist > 0
        macd_bear = macd_hist is not None and macd_hist < 0

        vwap       = calc_vwap(candles)
        sobre_vwap = precio > vwap * (1 + getattr(config, "VWAP_PCT", 0.3) / 100)
        bajo_vwap  = precio < vwap * (1 - getattr(config, "VWAP_PCT", 0.3) / 100)

        htf    = tendencia_htf(par)
        htf_4h = tendencia_4h(par)

        # HTF flexible: NEUTRAL no bloquea — solo BULL/BEAR bloquea el contrario
        trend_ok_l = bull5 and (htf != "BEAR")
        trend_ok_s = bear5 and (htf != "BULL")

        fvg     = detectar_fvg(candles)
        eq      = detectar_eqh_eql(candles)
        ob      = detectar_order_blocks(candles)
        bos     = detectar_bos_choch(candles)
        sweep   = detectar_sweep(candles)
        asia    = get_rango_asia(candles)
        kz      = en_killzone()
        pd_zone = premium_discount_zone(candles)
        pat     = detectar_patron_vela(candles)

        candles_d = exchange.get_candles(par, "1d", 5)
        pivotes   = calcular_pivotes_diarios(candles_d)
        pct       = config.PIVOT_NEAR_PCT / 100
        ns1 = ns2 = nr1 = nr2 = False
        if pivotes:
            ns1 = abs(precio - pivotes["S1"]) / precio < pct
            ns2 = abs(precio - pivotes["S2"]) / precio < pct
            nr1 = abs(precio - pivotes["R1"]) / precio < pct
            nr2 = abs(precio - pivotes["R2"]) / precio < pct

        nal = nah = False
        if asia["valido"]:
            nal = abs(precio - asia["low"])  / precio < pct
            nah = abs(precio - asia["high"]) / precio < pct

        iob_b    = (ob["bull_ob"] and ob["bull_ob_bottom"] <= precio <= ob["bull_ob_top"] * 1.005)
        iob_r    = (ob["bear_ob"] and ob["bear_ob_bottom"] * 0.995 <= precio <= ob["bear_ob_top"])
        ob_fvg_b = (iob_b and fvg["bull_fvg"] and
                    ob["bull_ob_bottom"] <= fvg.get("fvg_bottom", 0) <= ob["bull_ob_top"])
        ob_fvg_r = (iob_r and fvg["bear_fvg"] and
                    ob["bear_ob_bottom"] <= fvg.get("fvg_top", 0) <= ob["bear_ob_top"])

        desplazamiento = False
        if getattr(config, "DISPLACEMENT_ACTIVO", True) and len(candles) >= 3:
            avg_body      = sum(abs(c["close"] - c["open"]) for c in candles[-3:-1]) / 2
            curr_body     = abs(candles[-1]["close"] - candles[-1]["open"])
            desplazamiento = curr_body > avg_body * 1.5

        vcl = confirmar_vela(candles, "LONG")
        vcs = confirmar_vela(candles, "SHORT")

        # ── SCORING v7.0 (max 16) ─────────────────────────────
        sl = ss = 0
        ml: list = []
        ms: list = []

        def add(cond, pts, lbl, side):
            nonlocal sl, ss
            if cond:
                if side in ("L", "B"): sl += pts; ml.append(lbl)
                if side in ("S", "B"): ss += pts; ms.append(lbl)

        add(fvg["bull_fvg"] and not fvg["fvg_rellenado"], 2, "FVG",    "L")
        add(fvg["bear_fvg"] and not fvg["fvg_rellenado"], 2, "FVG",    "S")
        add(iob_b,                                         2, "OB+",   "L")
        add(iob_r,                                         2, "OB-",   "S")
        add(ob_fvg_b,                                      1, "OB+FVG","L")
        add(ob_fvg_r,                                      1, "OB+FVG","S")
        add(sweep["sweep_bull"],                           2, "SWEEP", "L")
        add(sweep["sweep_bear"],                           2, "SWEEP", "S")
        add(bos["choch_bull"],                             2, "CHoCH", "L")
        add(bos["choch_bear"],                             2, "CHoCH", "S")
        add(bos["bos_bull"] and not bos["choch_bull"],     1, "BOS",   "L")
        add(bos["bos_bear"] and not bos["choch_bear"],     1, "BOS",   "S")
        add(ns1 or ns2,         1, "S1/S2",   "L")
        add(eq["is_eql"],       1, "EQL",     "L")
        add(nal,                1, "ASIA_L",  "L")
        add(nr1 or nr2,         1, "R1/R2",   "S")
        add(eq["is_eqh"],       1, "EQH",     "S")
        add(nah,                1, "ASIA_H",  "S")
        add(pd_zone["discount"],1, "DISC",    "L")
        add(pd_zone["premium"], 1, "PREM",    "S")
        add(htf == "BULL",      1, "MTF1H",   "L")
        add(htf == "BEAR",      1, "MTF1H",   "S")
        add(htf_4h == "BULL",   1, "MTF4H",   "L")
        add(htf_4h == "BEAR",   1, "MTF4H",   "S")
        add(bull5,              1, "EMA",     "L")
        add(bear5,              1, "EMA",     "S")
        add(macd_bull,          1, "MACD",    "L")
        add(macd_bear,          1, "MACD",    "S")
        add(kz["in_kz"],        1, f"KZ_{kz['nombre']}", "B")
        add(vcl,                1, "VELA",    "L")
        add(vcs,                1, "VELA",    "S")
        add(pat.get("patron") and pat.get("lado") == "LONG",
            pat.get("confianza", 0), pat.get("patron", "PAT"), "L")
        add(pat.get("patron") and pat.get("lado") == "SHORT",
            pat.get("confianza", 0), pat.get("patron", "PAT"), "S")
        add(rsi_l,              1, f"RSI{rsi:.0f}", "L")
        add(rsi_s,              1, f"RSI{rsi:.0f}", "S")
        if getattr(config, "VWAP_ACTIVO", True):
            add(bajo_vwap,      1, "VWAP_B",  "L")
            add(sobre_vwap,     1, "VWAP_H",  "S")
        add(desplazamiento,     1, "DISP",    "B")

        # ── CONDICIONES BASE — KZ NO obligatorio ──────────────
        # CAMBIO CRITICO: el backtest NO requeria killzone obligatoria
        # KZ suma +1 punto al score pero no bloquea
        zona_l = ns1 or ns2 or eq["is_eql"] or nal or iob_b or pd_zone["discount"]
        zona_s = nr1 or nr2 or eq["is_eqh"] or nah or iob_r or pd_zone["premium"]

        base_l = (fvg["bull_fvg"] or iob_b or sweep["sweep_bull"]) and zona_l
        base_s = (fvg["bear_fvg"] or iob_r or sweep["sweep_bear"]) and zona_s

        lado = score = None
        motivos: list = []

        if not config.SOLO_LONG:
            if base_s and ss >= config.SCORE_MIN and trend_ok_s and rsi_s:
                if ss > sl:
                    lado, score, motivos = "SHORT", ss, ms

        if base_l and sl >= config.SCORE_MIN and trend_ok_l and rsi_l:
            if lado is None or sl >= ss:
                lado, score, motivos = "LONG", sl, ml

        if lado is None:
            if sl >= 3 or ss >= 3:
                log.debug(
                    f"[NO-SENHAL] {par} L:{sl}({','.join(ml)}) S:{ss}({','.join(ms)}) "
                    f"base_L={base_l} base_S={base_s} "
                    f"trend_L={trend_ok_l}(htf={htf},5m={'B' if bull5 else 'b'}) "
                    f"rsi={rsi:.0f}"
                )
            return None

        # ── SL estructural + TP proporcional ──────────────────
        sl_p = _calcular_sl_estructural(candles, ob, lado, atr, precio)
        tp_p, tp1_p = _calcular_tp(precio, sl_p, lado)

        dist = abs(precio - sl_p)
        if dist <= 0:
            return None

        rr = abs(tp_p - precio) / dist
        if rr < config.MIN_RR:
            log.debug(f"[NO-SENHAL] {par} R:R={rr:.2f} < {config.MIN_RR}")
            return None

        # Macro BTC veto solo para score muy bajo
        macro = _macro_btc_cache["htf"]
        if score < 6 and macro != "NEUTRAL":
            if lado == "LONG"  and macro == "BEAR": return None
            if lado == "SHORT" and macro == "BULL": return None

        registrar_senal_ts(par)

        vol_avg   = sum(c["volume"] for c in candles[-21:-1]) / 20
        vol_ratio = round(candles[-1]["volume"] / (vol_avg + 1e-9), 2)

        return {
            "par":           par,
            "lado":          lado,
            "precio":        precio,
            "sl":            round(sl_p, 8),
            "tp":            round(tp_p, 8),
            "tp1":           round(tp1_p, 8),
            "tp2":           round(tp_p, 8),
            "atr":           round(atr, 8),
            "dist_sl":       round(dist, 8),
            "score":         score,
            "rsi":           rsi,
            "rr":            round(rr, 2),
            "motivos":       motivos,
            "kz":            kz["nombre"],
            "htf":           htf,
            "htf_4h":        htf_4h,
            "vwap":          round(vwap, 8),
            "sobre_vwap":    sobre_vwap,
            "fvg_top":       fvg.get("fvg_top", 0),
            "fvg_bottom":    fvg.get("fvg_bottom", 0),
            "fvg_rellenado": fvg.get("fvg_rellenado", True),
            "ob_bull":       ob["bull_ob"],
            "ob_bear":       ob["bear_ob"],
            "ob_fvg_bull":   ob_fvg_b,
            "ob_fvg_bear":   ob_fvg_r,
            "ob_mitigado":   not ob["bull_ob"] and not ob["bear_ob"],
            "bos_bull":      bos["bos_bull"],
            "bos_bear":      bos["bos_bear"],
            "choch_bull":    bos["choch_bull"],
            "choch_bear":    bos["choch_bear"],
            "sweep_bull":    sweep["sweep_bull"],
            "sweep_bear":    sweep["sweep_bear"],
            "patron":        pat.get("patron"),
            "vela_conf":     vcl or vcs,
            "premium":       pd_zone["premium"],
            "discount":      pd_zone["discount"],
            "zona_pct":      pd_zone["zona_pct"],
            "displacement":  desplazamiento,
            "inducement":    False,
            "pivotes":       pivotes,
            "macd_hist":     round(macd_hist, 8) if macd_hist else 0,
            "vol_ratio":     vol_ratio,
            "asia_valido":   asia["valido"],
            "mercado_lateral": False,
        }

    except Exception as e:
        log.error(f"analizar_par {par}: {e}")
        return None


def analizar_todos(pares: list, workers: int = 4) -> list:
    senales = []
    workers = getattr(config, "ANALISIS_WORKERS", workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
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
