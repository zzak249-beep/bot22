"""
analizar_bellsz.py — Motor de señales Bellsz v2.0
==================================================
Lógica replicada exactamente del Pine Script "Liquidez Lateral [Bellsz]"
- BSL/SSL: ta.highest/lowest con lookahead_off (velas HTF cerradas)
- Purga: low <= ssl*(1+margen/100) and close > ssl  (igual que Pine)
- Confirmación: EMA 9/21 + RSI momentum
- Score de confluencia con OB, FVG, CHoCH, BOS, Sweep
"""

import logging
import os
import time
from datetime import datetime, timezone
import concurrent.futures

import config
import exchange

log = logging.getLogger("analizar")

_cooldown_ts:   dict = {}
_kz_stats:      dict = {}
_macro_btc:     dict = {"htf": "NEUTRAL", "ts": 0.0}
_niveles_cache: dict = {}
_NIVELES_TTL          = 900  # 15 min — HTF no cambia en cada ciclo


# ══════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════

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


def calc_adx(highs, lows, closes, period=14) -> float:
    if len(highs) < period * 2:
        return 25.0
    try:
        trs, plus_dm, minus_dm = [], [], []
        for i in range(1, len(highs)):
            tr   = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            up   = highs[i] - highs[i-1]
            down = lows[i-1] - lows[i]
            trs.append(tr)
            plus_dm.append(max(up, 0) if up > down else 0)
            minus_dm.append(max(down, 0) if down > up else 0)
        def smooth(d):
            s = sum(d[:period])
            for i in range(period, len(d)):
                s = s - s/period + d[i]
            return s
        atr14 = smooth(trs[-period*2:])
        if atr14 <= 0:
            return 25.0
        pdi = 100 * smooth(plus_dm[-period*2:])  / atr14
        mdi = 100 * smooth(minus_dm[-period*2:]) / atr14
        deno = pdi + mdi
        return abs(pdi - mdi) / deno * 100 if deno > 0 else 25.0
    except Exception:
        return 25.0


def calc_vwap(candles: list) -> float:
    hoy = datetime.now(timezone.utc).date()
    vc  = [c for c in candles if datetime.fromtimestamp(c["ts"]/1000, tz=timezone.utc).date() == hoy]
    if not vc:
        vc = candles[-50:]
    tv = sum(((c["high"]+c["low"]+c["close"])/3) * c["volume"] for c in vc)
    vt = sum(c["volume"] for c in vc)
    return tv / vt if vt > 0 else candles[-1]["close"]


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
    ml  = macd_hist[-1]
    sl  = calc_ema(macd_hist, signal)
    return ml, sl, ml - (sl or 0)


# ══════════════════════════════════════════════════════
# CAPA 1 — LIQUIDEZ (núcleo Bellsz)
# Replica exactamente Pine Script con lookahead_off
# ══════════════════════════════════════════════════════

def get_niveles_liquidez(par: str) -> dict:
    """
    Replica: ta.highest(high, lookback) con lookahead_off
    Solo usa velas HTF cerradas (ts < ts_actual de la vela 5m)
    Cacheado 15 min para no hacer 240 llamadas API por ciclo.
    """
    cached = _niveles_cache.get(par)
    if cached and (time.time() - cached["ts"]) < _NIVELES_TTL:
        return cached["data"]

    resultado = {
        "bsl_h1": 0.0, "ssl_h1": 0.0,
        "bsl_h4": 0.0, "ssl_h4": 0.0,
        "bsl_d":  0.0, "ssl_d":  0.0,
        "ok": False,
    }
    try:
        lb = config.LIQ_LOOKBACK
        if config.LIQ_LOOKBACK >= 1:
            c = exchange.get_candles(par, config.HTF_H1_TF, lb + 10)
            if len(c) >= lb:
                # lookahead_off: excluir la última vela (aún abierta)
                cerradas = c[:-1]
                rec = cerradas[-lb:]
                resultado["bsl_h1"] = max(x["high"] for x in rec)
                resultado["ssl_h1"] = min(x["low"]  for x in rec)

            c4 = exchange.get_candles(par, config.HTF_H4_TF, lb + 10)
            if len(c4) >= lb:
                cerradas4 = c4[:-1]
                rec4 = cerradas4[-lb:]
                resultado["bsl_h4"] = max(x["high"] for x in rec4)
                resultado["ssl_h4"] = min(x["low"]  for x in rec4)

            cd = exchange.get_candles(par, config.HTF_D_TF, min(lb, 30) + 5)
            if len(cd) >= 5:
                cerradasd = cd[:-1]
                recd = cerradasd[-min(lb, 30):]
                resultado["bsl_d"] = max(x["high"] for x in recd)
                resultado["ssl_d"] = min(x["low"]  for x in recd)

        resultado["ok"] = True
    except Exception as e:
        log.debug(f"[LIQ] {par}: {e}")

    _niveles_cache[par] = {"data": resultado, "ts": time.time()}
    return resultado


def detectar_purga(candles: list, niveles: dict, precio: float) -> dict:
    """
    Replica exactamente Pine Script:
      purga_alcista = low <= ssl*(1 + margen_pip/100) and close > ssl
      purga_bajista = high >= bsl*(1 - margen_pip/100) and close < bsl
    margen_pip = config.LIQ_MARGEN (ej: 0.1 = 0.1%)
    """
    if len(candles) < 3:
        return {"purga_alcista": False, "purga_bajista": False,
                "purga_nivel": "", "purga_nivel_l": "", "purga_nivel_s": "", "purga_peso": 0}

    c = candles[-1]
    m = config.LIQ_MARGEN / 100  # igual que margen_pip/100 en Pine

    purga_alcista = False
    purga_bajista = False
    purga_nivel_l = ""
    purga_nivel_s = ""
    purga_peso    = 0

    # ── PURGAS ALCISTAS (LONG) ────────────────────────
    ssl_h1 = niveles.get("ssl_h1", 0)
    if ssl_h1 > 0 and c["low"] <= ssl_h1 * (1 + m) and c["close"] > ssl_h1:
        purga_alcista = True; purga_nivel_l += "SSL_H1 "; purga_peso += 1

    ssl_h4 = niveles.get("ssl_h4", 0)
    if ssl_h4 > 0 and c["low"] <= ssl_h4 * (1 + m) and c["close"] > ssl_h4:
        purga_alcista = True; purga_nivel_l += "SSL_H4 "; purga_peso += 2

    ssl_d = niveles.get("ssl_d", 0)
    if ssl_d > 0 and c["low"] <= ssl_d * (1 + m) and c["close"] > ssl_d:
        purga_alcista = True; purga_nivel_l += "SSL_D "; purga_peso += 3

    # ── PURGAS BAJISTAS (SHORT) ───────────────────────
    bsl_h1 = niveles.get("bsl_h1", 0)
    if bsl_h1 > 0 and c["high"] >= bsl_h1 * (1 - m) and c["close"] < bsl_h1:
        purga_bajista = True; purga_nivel_s += "BSL_H1 "; purga_peso += 1

    bsl_h4 = niveles.get("bsl_h4", 0)
    if bsl_h4 > 0 and c["high"] >= bsl_h4 * (1 - m) and c["close"] < bsl_h4:
        purga_bajista = True; purga_nivel_s += "BSL_H4 "; purga_peso += 2

    bsl_d = niveles.get("bsl_d", 0)
    if bsl_d > 0 and c["high"] >= bsl_d * (1 - m) and c["close"] < bsl_d:
        purga_bajista = True; purga_nivel_s += "BSL_D "; purga_peso += 3

    purga_nivel = (purga_nivel_l if purga_alcista else purga_nivel_s).strip()

    return {
        "purga_alcista": purga_alcista,
        "purga_bajista": purga_bajista,
        "purga_nivel":   purga_nivel,
        "purga_nivel_l": purga_nivel_l.strip(),
        "purga_nivel_s": purga_nivel_s.strip(),
        "purga_peso":    purga_peso,
    }


# ══════════════════════════════════════════════════════
# CAPA 2 — EMA (replica Pine Script)
# ══════════════════════════════════════════════════════

def confirmar_ema(closes: list) -> dict:
    er = calc_ema(closes, config.EMA_FAST)
    el = calc_ema(closes, config.EMA_SLOW)
    if er is None or el is None:
        return {"bull": False, "bear": False, "cruce_bull": False, "cruce_bear": False, "er": 0, "el": 0}

    prev = closes[:-1]
    er_p = calc_ema(prev, config.EMA_FAST) if len(prev) >= config.EMA_FAST else None
    el_p = calc_ema(prev, config.EMA_SLOW) if len(prev) >= config.EMA_SLOW else None

    cruce_bull = er_p is not None and el_p is not None and er_p <= el_p and er > el
    cruce_bear = er_p is not None and el_p is not None and er_p >= el_p and er < el

    return {
        "bull":       er > el * 1.001,
        "bear":       er < el * 0.999,
        "cruce_bull": cruce_bull,
        "cruce_bear": cruce_bear,
        "er": er, "el": el,
    }


# ══════════════════════════════════════════════════════
# CAPA 3 — RSI (replica Pine Script)
# ══════════════════════════════════════════════════════

def confirmar_rsi(closes: list) -> dict:
    rv = calc_rsi(closes, config.RSI_PERIOD)
    if rv is None:
        return {"ok_long": False, "ok_short": False, "momentum_bull": False, "momentum_bear": False, "valor": 50.0}

    # RSI EMA 3 para momentum (igual que ta.ema(rsi_val,3) en Pine)
    rsi_serie = [calc_rsi(closes[:i+1]) or 50 for i in range(max(0, len(closes)-8), len(closes))]
    rsi_ema3  = calc_ema(rsi_serie, 3) if len(rsi_serie) >= 3 else rv

    mb = rsi_ema3 is not None and rv > rsi_ema3
    ms = rsi_ema3 is not None and rv < rsi_ema3

    ok_l = config.RSI_SELL_MIN < rv < config.RSI_BUY_MAX
    ok_s = config.RSI_SELL_MIN < rv < config.RSI_BUY_MAX

    return {"ok_long": ok_l, "ok_short": ok_s, "momentum_bull": mb, "momentum_bear": ms, "valor": rv}


# ══════════════════════════════════════════════════════
# CONFLUENCIAS EXTRA
# ══════════════════════════════════════════════════════

def detectar_order_blocks(candles: list) -> dict:
    lb = min(config.OB_LOOKBACK, len(candles) - 3)
    bull_ob = bear_ob = False
    bull_top = bull_bot = bear_top = bear_bot = 0.0
    for i in range(len(candles)-3, max(len(candles)-lb-3, 1), -1):
        c = candles
        if not bull_ob and c[i]["close"] < c[i]["open"]:
            if (i+2 < len(c) and c[i+1]["close"] > c[i+1]["open"]
                    and c[i+2]["close"] > c[i+2]["open"]
                    and c[i+2]["high"] > c[i]["high"]):
                bull_ob  = True
                bull_top = max(c[i]["open"], c[i]["close"])
                bull_bot = c[i]["low"]
        if not bear_ob and c[i]["close"] > c[i]["open"]:
            if (i+2 < len(c) and c[i+1]["close"] < c[i+1]["open"]
                    and c[i+2]["close"] < c[i+2]["open"]
                    and c[i+2]["low"] < c[i]["low"]):
                bear_ob  = True
                bear_top = c[i]["high"]
                bear_bot = min(c[i]["open"], c[i]["close"])
        if bull_ob and bear_ob:
            break
    precio  = candles[-1]["close"]
    iob_b = bull_ob and bull_bot <= precio <= bull_top * 1.005
    iob_r = bear_ob and bear_bot * 0.995 <= precio <= bear_top
    return {"bull_ob": bull_ob, "bull_ob_top": bull_top, "bull_ob_bottom": bull_bot,
            "bear_ob": bear_ob, "bear_ob_top": bear_top, "bear_ob_bottom": bear_bot,
            "iob_bull": iob_b, "iob_bear": iob_r}


def detectar_fvg(candles: list) -> dict:
    r = {"bull_fvg": False, "bear_fvg": False, "fvg_top": 0.0, "fvg_bottom": 0.0,
         "fvg_rellenado": True, "en_zona": False}
    if len(candles) < 5:
        return r
    precio = candles[-1]["close"]
    for i in range(len(candles)-1, max(len(candles)-20, 2), -1):
        c = candles
        if c[i]["low"] > c[i-2]["high"]:
            r.update({"bull_fvg": True, "fvg_top": c[i]["low"], "fvg_bottom": c[i-2]["high"],
                      "fvg_rellenado": precio < c[i-2]["high"],
                      "en_zona": c[i-2]["high"] <= precio <= c[i]["low"]})
            return r
        if c[i]["high"] < c[i-2]["low"]:
            r.update({"bear_fvg": True, "fvg_top": c[i-2]["low"], "fvg_bottom": c[i]["high"],
                      "fvg_rellenado": precio > c[i-2]["low"],
                      "en_zona": c[i]["high"] <= precio <= c[i-2]["low"]})
            return r
    return r


def detectar_bos_choch(candles: list) -> dict:
    r = {"bos_bull": False, "bos_bear": False, "choch_bull": False, "choch_bear": False}
    if len(candles) < 20:
        return r
    rec  = candles[-20:]
    ph   = max(c["high"] for c in rec[:-1])
    pl   = min(c["low"]  for c in rec[:-1])
    ult  = candles[-1]
    if ult["close"] > ph: r["bos_bull"] = True
    if ult["close"] < pl: r["bos_bear"] = True
    if len(candles) >= 40:
        prev = candles[-40:-20]
        ph_p = max(c["high"] for c in prev)
        pl_p = min(c["low"]  for c in prev)
        if ult["close"] > ph_p and not r["bos_bull"]:  r["choch_bull"] = True
        if ult["close"] < pl_p and not r["bos_bear"]:  r["choch_bear"] = True
    return r


def detectar_sweep(candles: list) -> dict:
    lb = min(config.SWEEP_LOOKBACK, len(candles) - 2)
    if lb < 5:
        return {"sweep_bull": False, "sweep_bear": False}
    rec    = candles[-(lb+1):-1]
    ultimo = candles[-1]
    return {
        "sweep_bull": ultimo["low"]  < min(c["low"]  for c in rec) and ultimo["close"] > min(c["low"]  for c in rec),
        "sweep_bear": ultimo["high"] > max(c["high"] for c in rec) and ultimo["close"] < max(c["high"] for c in rec),
    }


def detectar_patron_vela(candles: list) -> dict:
    if len(candles) < 3:
        return {"patron": None, "lado": None, "confianza": 0}
    c = candles[-1]
    cuerpo = abs(c["close"] - c["open"])
    rango  = c["high"] - c["low"]
    if rango <= 0:
        return {"patron": None, "lado": None, "confianza": 0}
    rc = cuerpo / rango
    mb = (min(c["close"], c["open"]) - c["low"])  / rango
    ma = (c["high"] - max(c["close"], c["open"])) / rango
    if mb > config.PINBAR_RATIO and rc < 0.35:
        return {"patron": "PIN_BAR_BULL", "lado": "LONG",  "confianza": 2}
    if ma > config.PINBAR_RATIO and rc < 0.35:
        return {"patron": "PIN_BAR_BEAR", "lado": "SHORT", "confianza": 2}
    p = candles[-2]
    if (p["close"] < p["open"] and c["close"] > c["open"]
            and c["open"] < p["close"] and c["close"] > p["open"]):
        return {"patron": "ENGULFING_BULL", "lado": "LONG",  "confianza": 2}
    if (p["close"] > p["open"] and c["close"] < c["open"]
            and c["open"] > p["close"] and c["close"] < p["open"]):
        return {"patron": "ENGULFING_BEAR", "lado": "SHORT", "confianza": 2}
    return {"patron": None, "lado": None, "confianza": 0}


def premium_discount_zone(candles: list) -> dict:
    r = {"premium": False, "discount": False, "zona_pct": 50.0}
    if len(candles) < 10:
        return r
    lb    = min(config.PREMIUM_DISCOUNT_LB, len(candles))
    rec   = candles[-lb:]
    max_h = max(c["high"] for c in rec)
    min_l = min(c["low"]  for c in rec)
    rng   = max_h - min_l
    if rng <= 0:
        return r
    zona = (candles[-1]["close"] - min_l) / rng * 100
    return {"premium": zona >= 60, "discount": zona <= 40, "zona_pct": round(zona, 1)}


def en_killzone() -> dict:
    ahora = datetime.now(timezone.utc)
    tim   = ahora.hour * 60 + ahora.minute
    asia   = config.KZ_ASIA_START   <= tim < config.KZ_ASIA_END
    london = config.KZ_LONDON_START <= tim < config.KZ_LONDON_END
    ny     = config.KZ_NY_START     <= tim < config.KZ_NY_END
    return {
        "in_kz":  asia or london or ny,
        "nombre": "ASIA" if asia else ("LONDON" if london else ("NY" if ny else "FUERA")),
    }


def tendencia_htf(par: str, tf: str = None, n: int = 100) -> str:
    if not config.MTF_ACTIVO:
        return "NEUTRAL"
    try:
        ch = exchange.get_candles(par, tf or config.MTF_TIMEFRAME, n)
        if len(ch) < 50:
            return "NEUTRAL"
        cl = [c["close"] for c in ch]
        ef = calc_ema(cl, config.EMA_FAST)
        es = calc_ema(cl, config.EMA_SLOW)
        if ef and es:
            if ef > es * 1.001: return "BULL"
            if ef < es * 0.999: return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def actualizar_macro_btc():
    if time.time() - _macro_btc["ts"] < 900:
        return
    try:
        ch = exchange.get_candles("BTC-USDT", "4h", 50)
        if len(ch) < 50:
            return
        cl = [c["close"] for c in ch]
        ef = calc_ema(cl, 21)
        es = calc_ema(cl, 50)
        if ef and es:
            if   ef > es * 1.005: _macro_btc["htf"] = "BULL"
            elif ef < es * 0.995: _macro_btc["htf"] = "BEAR"
            else:                 _macro_btc["htf"] = "NEUTRAL"
        _macro_btc["ts"] = time.time()
    except Exception:
        pass


def _cooldown_ok(par: str) -> bool:
    return (time.time() - _cooldown_ts.get(par, 0)) >= config.COOLDOWN_VELAS * 300


def registrar_senal_ts(par: str):
    _cooldown_ts[par] = time.time()


def registrar_trade_kz(kz: str, ganado: bool):
    s = _kz_stats.setdefault(kz, {"trades": 0, "wins": 0})
    s["trades"] += 1
    s["wins"]   += int(ganado)


# ══════════════════════════════════════════════════════
# SL ESTRUCTURAL Y TP PROPORCIONAL
# ══════════════════════════════════════════════════════

def _calcular_sl_estructural(candles, ob, lado, atr, precio):
    if lado == "LONG":
        swing  = min(c["low"] for c in candles[-16:-1])
        sl_ob  = ob["bull_ob_bottom"] * 0.997 if ob["bull_ob"] else 0
        sl_sw  = swing * 0.997 if swing > 0 else 0
        sl_atr = precio - atr * config.SL_ATR_MULT
        cands  = [x for x in [sl_ob, sl_sw] if 0 < x < precio]
        return min(max(cands) if cands else sl_atr, precio - atr * 0.5)
    else:
        swing  = max(c["high"] for c in candles[-16:-1])
        sl_ob  = ob["bear_ob_top"] * 1.003 if ob["bear_ob"] else 0
        sl_sw  = swing * 1.003 if swing > 0 else 0
        sl_atr = precio + atr * config.SL_ATR_MULT
        cands  = [x for x in [sl_ob, sl_sw] if x > precio]
        return max(min(cands) if cands else sl_atr, precio + atr * 0.5)


def _calcular_tp(precio, sl, lado):
    dist = abs(precio - sl)
    if lado == "LONG":
        return precio + dist * config.TP_DIST_MULT, precio + dist * config.TP1_DIST_MULT
    return precio - dist * config.TP_DIST_MULT, precio - dist * config.TP1_DIST_MULT


# ══════════════════════════════════════════════════════
# SEÑAL PRINCIPAL
# ══════════════════════════════════════════════════════

def analizar_par(par: str):
    try:
        candles = exchange.get_candles(par, config.TIMEFRAME, config.CANDLES_LIMIT)
        if len(candles) < 80 or not _cooldown_ok(par):
            return None

        cl  = [c["close"] for c in candles]
        hi  = [c["high"]  for c in candles]
        lo  = [c["low"]   for c in candles]
        precio = cl[-1]
        if precio <= 0:
            return None

        atr = calc_atr(hi, lo, cl, config.ATR_PERIOD)
        atr_min_pct = float(os.getenv("ATR_MIN_PCT", "0.02"))
        if atr <= 0 or atr / precio * 100 < atr_min_pct:
            return None

        adx = calc_adx(hi, lo, cl)
        adx_min = float(os.getenv("ADX_MIN", "8"))
        if adx < adx_min:
            return None

        # ── CAPA 1: PURGAS (primero — filtro principal) ────
        niveles = get_niveles_liquidez(par)
        purga   = detectar_purga(candles, niveles, precio)

        if not purga["purga_alcista"] and not purga["purga_bajista"]:
            return None

        # ── CAPA 2: EMA ────────────────────────────────────
        ema_conf = confirmar_ema(cl)

        # ── CAPA 3: RSI ────────────────────────────────────
        rsi_conf = confirmar_rsi(cl)

        # ── CONFLUENCIAS ───────────────────────────────────
        ob      = detectar_order_blocks(candles)
        fvg     = detectar_fvg(candles)
        bos     = detectar_bos_choch(candles)
        sweep   = detectar_sweep(candles)
        pat     = detectar_patron_vela(candles)
        pd_zone = premium_discount_zone(candles)
        kz      = en_killzone()
        htf     = tendencia_htf(par)
        htf_4h  = tendencia_htf(par, "4h", 50)
        vwap    = calc_vwap(candles)
        _, _, macd_hist = calc_macd(cl)

        sobre_vwap = precio > vwap * (1 + config.VWAP_PCT / 100)
        bajo_vwap  = precio < vwap * (1 - config.VWAP_PCT / 100)
        ob_fvg_b   = ob["iob_bull"] and fvg["bull_fvg"] and ob["bull_ob_bottom"] <= fvg.get("fvg_bottom",0) <= ob["bull_ob_top"]
        ob_fvg_r   = ob["iob_bear"] and fvg["bear_fvg"] and ob["bear_ob_bottom"] <= fvg.get("fvg_top",0) <= ob["bear_ob_top"]
        desp       = (config.DISPLACEMENT_ACTIVO and len(candles) >= 3
                      and abs(candles[-1]["close"]-candles[-1]["open"]) > sum(abs(candles[i]["close"]-candles[i]["open"]) for i in [-3,-2]) / 2 * 1.5)

        # ── SCORING ────────────────────────────────────────
        sl_pts = ss_pts = 0
        ml: list = []
        ms: list = []

        def add(cond, pts, lbl, side):
            nonlocal sl_pts, ss_pts
            if cond:
                if side in ("L","B"): sl_pts += pts; ml.append(lbl)
                if side in ("S","B"): ss_pts += pts; ms.append(lbl)

        pnl = purga.get("purga_nivel_l","")
        pns = purga.get("purga_nivel_s","")

        # Capa 1
        add(purga["purga_alcista"] and "H1" in pnl, 1, "PURGA_SSL_H1", "L")
        add(purga["purga_alcista"] and "H4" in pnl, 2, "PURGA_SSL_H4", "L")
        add(purga["purga_alcista"] and "_D"  in pnl, 3, "PURGA_SSL_D",  "L")
        add(purga["purga_bajista"] and "H1" in pns, 1, "PURGA_BSL_H1", "S")
        add(purga["purga_bajista"] and "H4" in pns, 2, "PURGA_BSL_H4", "S")
        add(purga["purga_bajista"] and "_D"  in pns, 3, "PURGA_BSL_D",  "S")
        # Capa 2
        add(ema_conf["cruce_bull"],                       2, "CRUCE_EMA_BULL", "L")
        add(ema_conf["cruce_bear"],                       2, "CRUCE_EMA_BEAR", "S")
        add(ema_conf["bull"] and not ema_conf["cruce_bull"], 1, "EMA_BULL", "L")
        add(ema_conf["bear"] and not ema_conf["cruce_bear"], 1, "EMA_BEAR", "S")
        # Capa 3
        add(rsi_conf["ok_long"],        1, f"RSI{rsi_conf['valor']:.0f}", "L")
        add(rsi_conf["ok_short"],       1, f"RSI{rsi_conf['valor']:.0f}", "S")
        add(rsi_conf["momentum_bull"],  1, "RSI_BULL", "L")
        add(rsi_conf["momentum_bear"],  1, "RSI_BEAR", "S")
        # Confluencias
        add(ob["iob_bull"],             2, "OB+",      "L")
        add(ob["iob_bear"],             2, "OB-",      "S")
        add(ob_fvg_b,                   1, "OB+FVG",   "L")
        add(ob_fvg_r,                   1, "OB+FVG",   "S")
        add(fvg["bull_fvg"] and not fvg["fvg_rellenado"], 2, "FVG", "L")
        add(fvg["bear_fvg"] and not fvg["fvg_rellenado"], 2, "FVG", "S")
        add(sweep["sweep_bull"],        2, "SWEEP",    "L")
        add(sweep["sweep_bear"],        2, "SWEEP",    "S")
        add(bos["choch_bull"],          2, "CHoCH",    "L")
        add(bos["choch_bear"],          2, "CHoCH",    "S")
        add(bos["bos_bull"] and not bos["choch_bull"], 1, "BOS", "L")
        add(bos["bos_bear"] and not bos["choch_bear"], 1, "BOS", "S")
        add(htf == "BULL",              1, "MTF1H",    "L")
        add(htf == "BEAR",              1, "MTF1H",    "S")
        add(htf_4h == "BULL",           1, "MTF4H",    "L")
        add(htf_4h == "BEAR",           1, "MTF4H",    "S")
        add(pd_zone["discount"],        1, "DISC",     "L")
        add(pd_zone["premium"],         1, "PREM",     "S")
        add(bajo_vwap and config.VWAP_ACTIVO,   1, "VWAP_B", "L")
        add(sobre_vwap and config.VWAP_ACTIVO,  1, "VWAP_H", "S")
        add(kz["in_kz"],                1, f"KZ_{kz['nombre']}", "B")
        add(desp,                       1, "DISP",     "B")
        add(macd_hist and macd_hist > 0, 1, "MACD",   "L")
        add(macd_hist and macd_hist < 0, 1, "MACD",   "S")
        if pat.get("patron"):
            add(pat["lado"] == "LONG",  pat["confianza"], pat["patron"], "L")
            add(pat["lado"] == "SHORT", pat["confianza"], pat["patron"], "S")

        # ── CONDICIÓN BASE (replica Pine Script) ───────────
        # purga + (EMA alcista/bajista O RSI en rango)
        ema_ok_l = ema_conf["bull"] or ema_conf["cruce_bull"]
        ema_ok_s = ema_conf["bear"] or ema_conf["cruce_bear"]
        rsi_ok_l = rsi_conf["ok_long"]
        rsi_ok_s = rsi_conf["ok_short"]

        base_l = purga["purga_alcista"] and (ema_ok_l or rsi_ok_l)
        base_s = purga["purga_bajista"] and (ema_ok_s or rsi_ok_s)

        trend_ok_l = (htf != "BEAR")
        trend_ok_s = (htf != "BULL")

        lado = score = None
        motivos: list = []

        if not config.SOLO_LONG:
            if base_s and ss_pts >= config.SCORE_MIN and trend_ok_s:
                if ss_pts > sl_pts:
                    lado, score, motivos = "SHORT", ss_pts, ms

        if base_l and sl_pts >= config.SCORE_MIN and trend_ok_l:
            if lado is None or sl_pts >= ss_pts:
                lado, score, motivos = "LONG", sl_pts, ml

        if lado is None:
            if sl_pts >= 3 or ss_pts >= 3:
                log.info(
                    f"[NO-SENAL] {par} L:{sl_pts}({','.join(ml[:4])}) "
                    f"S:{ss_pts}({','.join(ms[:4])}) "
                    f"purga_L={purga['purga_alcista']}({pnl}) "
                    f"purga_S={purga['purga_bajista']}({pns}) "
                    f"ema_bull={ema_conf['bull']} rsi={rsi_conf['valor']:.1f} htf={htf}"
                )
            return None

        # ── SL / TP ────────────────────────────────────────
        sl_p = _calcular_sl_estructural(candles, ob, lado, atr, precio)
        tp_p, tp1_p = _calcular_tp(precio, sl_p, lado)
        dist = abs(precio - sl_p)
        if dist <= 0:
            return None
        rr = abs(tp_p - precio) / dist
        if rr < config.MIN_RR:
            return None

        # Veto macro BTC para scores bajos
        macro = _macro_btc["htf"]
        if score < 6 and macro != "NEUTRAL":
            if lado == "LONG"  and macro == "BEAR": return None
            if lado == "SHORT" and macro == "BULL": return None

        registrar_senal_ts(par)

        vol_avg   = sum(c["volume"] for c in candles[-21:-1]) / 20
        vol_ratio = round(candles[-1]["volume"] / (vol_avg + 1e-9), 2)

        return {
            "par": par, "lado": lado, "precio": precio,
            "sl": round(sl_p, 8), "tp": round(tp_p, 8), "tp1": round(tp1_p, 8), "tp2": round(tp_p, 8),
            "atr": round(atr, 8), "dist_sl": round(dist, 8),
            "score": score, "rsi": rsi_conf["valor"], "rr": round(rr, 2),
            "motivos": motivos, "kz": kz["nombre"], "htf": htf, "htf_4h": htf_4h,
            "vwap": round(vwap, 8), "sobre_vwap": sobre_vwap,
            "fvg_top": fvg.get("fvg_top", 0), "fvg_bottom": fvg.get("fvg_bottom", 0),
            "fvg_rellenado": fvg.get("fvg_rellenado", True),
            "ob_bull": ob["bull_ob"], "ob_bear": ob["bear_ob"],
            "ob_fvg_bull": ob_fvg_b, "ob_fvg_bear": ob_fvg_r,
            "ob_mitigado": not ob["bull_ob"] and not ob["bear_ob"],
            "bos_bull": bos["bos_bull"], "bos_bear": bos["bos_bear"],
            "choch_bull": bos["choch_bull"], "choch_bear": bos["choch_bear"],
            "sweep_bull": sweep["sweep_bull"], "sweep_bear": sweep["sweep_bear"],
            "patron": pat.get("patron"), "vela_conf": pat.get("patron") is not None,
            "premium": pd_zone["premium"], "discount": pd_zone["discount"],
            "displacement": desp, "macd_hist": round(macd_hist, 8) if macd_hist else 0,
            "vol_ratio": vol_ratio, "asia_valido": True,
            "purga_nivel": purga["purga_nivel"], "purga_peso": purga["purga_peso"],
            "bsl_h1": round(niveles["bsl_h1"], 8), "ssl_h1": round(niveles["ssl_h1"], 8),
            "bsl_h4": round(niveles["bsl_h4"], 8), "ssl_h4": round(niveles["ssl_h4"], 8),
            "bsl_d":  round(niveles["bsl_d"],  8), "ssl_d":  round(niveles["ssl_d"],  8),
            "ema_r":  round(ema_conf["er"], 8), "ema_l": round(ema_conf["el"], 8),
            "adx": round(adx, 1), "inducement": False,
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
