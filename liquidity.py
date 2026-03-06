"""
liquidity.py — Flujo de Liquidez Institucional v1
══════════════════════════════════════════════════
Detecta la huella de grandes operadores, ballenas y exchanges
usando datos públicos de BingX Futuros:

  1. ORDER BOOK IMBALANCE  — ratio BID vs ASK (20 niveles)
  2. OPEN INTEREST DELTA   — cambio % de contratos abiertos
  3. FUNDING RATE          — coste de mantener posición
  4. LONG/SHORT RATIO      — posicionamiento de top traders
  5. CVD                   — Cumulative Volume Delta (velas 15m)
  6. LIQUIDATION ZONES     — muros de liquidez en el libro

Retorna LiquidityBias con:
  bias:   "bullish" | "bearish" | "neutral"
  score:  0-100
  block:  True si señal debe bloquearse
  detail: componentes individuales para logs/Telegram
"""

import logging
import time
import requests

import config as cfg

log = logging.getLogger("liquidity")

BASE_URL = "https://open-api.bingx.com"
_cache: dict = {}

CACHE_TTL = {
    "orderbook": 15,
    "oi":        60,
    "funding":  300,
    "lsr":      120,
    "cvd":       30,
    "liq":       20,
}


def _get(path: str, params: dict = None) -> dict:
    try:
        r = requests.get(BASE_URL + path, params=params or {}, timeout=6)
        return r.json()
    except Exception as e:
        log.debug(f"liq GET {path}: {e}")
        return {}


def _cached(key: str, ttl: int, fn):
    now = time.time()
    if key in _cache:
        val, ts = _cache[key]
        if now - ts < ttl:
            return val
    try:
        val = fn()
    except Exception as e:
        log.debug(f"_cached {key}: {e}")
        return None
    _cache[key] = (val, now)
    return val


# ══════════════════════════════════════════════════════════
# 1. ORDER BOOK IMBALANCE
# ══════════════════════════════════════════════════════════

def get_orderbook_imbalance(symbol: str) -> dict:
    par = symbol.replace("/", "-")
    def fetch():
        d    = _get("/openApi/swap/v2/quote/depth", {"symbol": par, "limit": 20})
        bids = d.get("data", {}).get("bids", [])
        asks = d.get("data", {}).get("asks", [])
        if not bids or not asks:
            return None
        bv = sum(float(b[1]) for b in bids[:20] if len(b) >= 2)
        av = sum(float(a[1]) for a in asks[:20] if len(a) >= 2)
        total = bv + av
        if total == 0:
            return None
        return {
            "bid_vol": round(bv, 2),
            "ask_vol": round(av, 2),
            "ratio":   round(bv / total, 4),   # >0.58 bullish, <0.42 bearish
            "bids_raw": bids,
            "asks_raw": asks,
        }
    return _cached(f"ob_{par}", CACHE_TTL["orderbook"], fetch) or {"ratio": 0.5}


# ══════════════════════════════════════════════════════════
# 2. OPEN INTEREST
# ══════════════════════════════════════════════════════════

def get_open_interest(symbol: str) -> dict:
    par = symbol.replace("/", "-")
    def fetch():
        r  = _get("/openApi/swap/v2/quote/openInterest", {"symbol": par})
        oi = float(r.get("data", {}).get("openInterest", 0))
        if oi == 0:
            r2 = _get("/openApi/contract/v1/openInterest", {"symbol": par})
            oi = float(r2.get("data", {}).get("openInterest", 0))
        return {"oi": round(oi, 2)}
    return _cached(f"oi_{par}", CACHE_TTL["oi"], fetch) or {"oi": 0}


# ══════════════════════════════════════════════════════════
# 3. FUNDING RATE
# ══════════════════════════════════════════════════════════

def get_funding_rate(symbol: str) -> dict:
    par = symbol.replace("/", "-")
    def fetch():
        r = _get("/openApi/swap/v2/quote/fundingRate", {"symbol": par})
        d = r.get("data", {})
        if isinstance(d, list):
            d = d[0] if d else {}
        rate = float(d.get("fundingRate", d.get("lastFundingRate", 0)))
        return {"rate_raw": rate, "rate": round(rate * 100, 4)}
    return _cached(f"fr_{par}", CACHE_TTL["funding"], fetch) or {"rate": 0, "rate_raw": 0}


# ══════════════════════════════════════════════════════════
# 4. LONG/SHORT RATIO (Top Traders)
# ══════════════════════════════════════════════════════════

def get_lsr(symbol: str) -> dict:
    par = symbol.replace("/", "-")
    def fetch():
        for ep in [
            "/openApi/swap/v2/quote/globalLongShortAccountRatio",
            "/openApi/swap/v2/quote/topLongShortAccountRatio",
        ]:
            r    = _get(ep, {"symbol": par, "period": "1h", "limit": 4})
            data = r.get("data", [])
            if data:
                break
        if not data:
            return {"ratio": 1.0, "long_pct": 50.0, "short_pct": 50.0, "trend": "flat"}

        rows = []
        for item in (data if isinstance(data, list) else [data]):
            lp = float(item.get("longAccount",  item.get("longRatio",  0.5)))
            sp = float(item.get("shortAccount", item.get("shortRatio", 0.5)))
            if lp < 1.0: lp *= 100; sp *= 100
            rows.append(lp / sp if sp > 0 else 1.0)

        ratio = rows[-1] if rows else 1.0
        trend = ("increasing" if len(rows) >= 2 and rows[-1] > rows[0] else
                 "decreasing" if len(rows) >= 2 and rows[-1] < rows[0] else "flat")
        last = data[-1] if isinstance(data, list) else data
        lp = float(last.get("longAccount", last.get("longRatio", 0.5)))
        sp = float(last.get("shortAccount", last.get("shortRatio", 0.5)))
        if lp < 1.0: lp *= 100; sp *= 100
        return {"ratio": round(ratio, 3), "long_pct": round(lp, 1),
                "short_pct": round(sp, 1), "trend": trend}
    return _cached(f"lsr_{par}", CACHE_TTL["lsr"], fetch) or {"ratio": 1.0, "long_pct": 50.0, "short_pct": 50.0, "trend": "flat"}


# ══════════════════════════════════════════════════════════
# 5. CVD — Cumulative Volume Delta
# ══════════════════════════════════════════════════════════

def get_cvd(symbol: str, n: int = 20) -> dict:
    par = symbol.replace("/", "-")
    def fetch():
        r      = _get("/openApi/swap/v3/quote/klines", {"symbol": par, "interval": "15m", "limit": n + 5})
        klines = r.get("data", [])
        if not klines:
            return {"cvd_pct": 0, "bias": "neutral"}
        buy_v = sell_v = 0.0
        for k in klines[-n:]:
            try:
                if isinstance(k, dict):
                    o = float(k.get("open",   k.get("o", 0)))
                    h = float(k.get("high",   k.get("h", 0)))
                    l = float(k.get("low",    k.get("l", 0)))
                    c = float(k.get("close",  k.get("c", 0)))
                    v = float(k.get("volume", k.get("v", 0)))
                else:
                    o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
                rng = h - l
                br  = (c - l) / rng if rng > 0 else 0.5
                buy_v  += v * br
                sell_v += v * (1 - br)
            except Exception:
                continue
        total   = buy_v + sell_v
        cvd_pct = (buy_v - sell_v) / total * 100 if total > 0 else 0
        bias    = "bullish" if cvd_pct > 5 else "bearish" if cvd_pct < -5 else "neutral"
        return {"cvd_pct": round(cvd_pct, 2), "buy_vol": round(buy_v, 2),
                "sell_vol": round(sell_v, 2), "bias": bias}
    return _cached(f"cvd_{par}", CACHE_TTL["cvd"], fetch) or {"cvd_pct": 0, "bias": "neutral"}


# ══════════════════════════════════════════════════════════
# 6. ZONAS DE LIQUIDACIÓN (muros en el libro)
# ══════════════════════════════════════════════════════════

def get_liquidation_zones(symbol: str, price: float, atr: float) -> dict:
    if price <= 0 or atr <= 0:
        return {"zone": "clear", "bid_walls": 0, "ask_walls": 0}
    ob  = get_orderbook_imbalance(symbol)
    bids = ob.get("bids_raw", [])
    asks = ob.get("asks_raw", [])
    if not bids or not asks:
        return {"zone": "clear", "bid_walls": 0, "ask_walls": 0}
    bid_vols = [(float(b[0]), float(b[1])) for b in bids[:30] if len(b) >= 2]
    ask_vols = [(float(a[0]), float(a[1])) for a in asks[:30] if len(a) >= 2]
    avg_b = sum(v for _, v in bid_vols) / len(bid_vols) if bid_vols else 0
    avg_a = sum(v for _, v in ask_vols) / len(ask_vols) if ask_vols else 0
    bid_walls = [(p, v) for p, v in bid_vols if v > avg_b * 3]
    ask_walls = [(p, v) for p, v in ask_vols if v > avg_a * 3]
    zone = "clear"
    for p, _ in bid_walls:
        if abs(price - p) < atr * 2:
            zone = "hunting_longs"
    for p, _ in ask_walls:
        if abs(price - p) < atr * 2:
            zone = "hunting_shorts"
    return {"zone": zone, "bid_walls": len(bid_walls), "ask_walls": len(ask_walls)}


# ══════════════════════════════════════════════════════════
# CLASE RESULTADO
# ══════════════════════════════════════════════════════════

class LiquidityBias:
    def __init__(self):
        self.bias    = "neutral"
        self.score   = 50
        self.block   = False
        self.detail  = {}
        self.summary = ""
    def __repr__(self):
        return f"LiqBias({self.bias} score={self.score} block={self.block})"


# ══════════════════════════════════════════════════════════
# ANÁLISIS PRINCIPAL
# ══════════════════════════════════════════════════════════

def analyze(symbol: str, price: float = 0, atr: float = 0) -> LiquidityBias:
    bias = LiquidityBias()

    if not getattr(cfg, "LIQUIDITY_ENABLED", True):
        return bias

    ob  = get_orderbook_imbalance(symbol)
    fr  = get_funding_rate(symbol)
    lsr = get_lsr(symbol)
    cvd = get_cvd(symbol)
    liq = get_liquidation_zones(symbol, price, atr) if price > 0 else {"zone": "clear"}

    bias.detail = {"orderbook": ob, "funding": fr, "lsr": lsr, "cvd": cvd, "liq_zone": liq}

    score = 50

    # 1. Order Book Imbalance
    r = ob.get("ratio", 0.5)
    if r > 0.65:   score += 8
    elif r > 0.58: score += 4
    elif r < 0.35: score -= 8
    elif r < 0.42: score -= 4

    # 2. Funding Rate  (alto positivo = masa larga → inst. faden)
    fr_raw = fr.get("rate_raw", 0)
    if fr_raw > 0.001:    score -= 7
    elif fr_raw > 0.0005: score -= 3
    elif fr_raw < -0.001: score += 7
    elif fr_raw < -0.0005:score += 3

    # 3. L/S Ratio Top Traders
    lsr_r = lsr.get("ratio", 1.0)
    lsr_t = lsr.get("trend", "flat")
    if lsr_r > 1.5:   score += 10
    elif lsr_r > 1.2:  score += 5
    elif lsr_r < 0.7:  score -= 10
    elif lsr_r < 0.85: score -= 5
    if lsr_r > 1.3 and lsr_t == "increasing": score += 5
    if lsr_r < 0.77 and lsr_t == "decreasing": score -= 5

    # 4. CVD
    cvd_p = cvd.get("cvd_pct", 0)
    if cvd_p > 15:   score += 10
    elif cvd_p > 8:  score += 6
    elif cvd_p > 3:  score += 3
    elif cvd_p < -15: score -= 10
    elif cvd_p < -8:  score -= 6
    elif cvd_p < -3:  score -= 3

    # 5. Liquidation Zone
    z = liq.get("zone", "clear")
    if z == "hunting_longs":  score -= 8
    elif z == "hunting_shorts": score += 8

    score = max(0, min(100, score))
    bias.score = score
    bias.bias  = "bullish" if score >= 63 else "bearish" if score <= 37 else "neutral"
    bias.block = score <= getattr(cfg, "LIQUIDITY_BLOCK_SCORE", 25) or \
                 score >= (100 - getattr(cfg, "LIQUIDITY_BLOCK_SCORE", 25))

    bias.summary = (
        f"🏦 Inst:{bias.bias.upper()}({score}) "
        f"OB:{r:.2f} FR:{fr.get('rate',0):.3f}% "
        f"LSR:{lsr_r:.2f}({lsr_t}) CVD:{cvd_p:+.1f}% "
        f"Zone:{z}"
    )
    log.info(f"{symbol} {bias.summary}")
    return bias


# ══════════════════════════════════════════════════════════
# APLICAR A SEÑAL
# ══════════════════════════════════════════════════════════

def apply_liquidity_filter(signal: dict, lbias: LiquidityBias) -> dict:
    """
    Ajusta el score de la señal según el bias institucional.
    - Confirma señal  → +bonus
    - Contradice suave → -penalización
    - Contradice fuerte (block=True) → señal bloqueada
    """
    if lbias.bias == "neutral":
        return signal

    sig    = signal.copy()
    action = sig.get("action", "none")
    confirms   = (action == "buy" and lbias.bias == "bullish") or \
                 (action == "sell_short" and lbias.bias == "bearish")
    contradicts = (action == "buy" and lbias.bias == "bearish") or \
                  (action == "sell_short" and lbias.bias == "bullish")

    if confirms:
        bonus = int((abs(lbias.score - 50) / 50) * 15)
        sig["score"]  = min(100, sig.get("score", 50) + bonus)
        sig["reason"] = f"[🏦INST+{bonus}] " + sig.get("reason", "")

    elif contradicts:
        if lbias.block:
            sig["action"] = "none"
            sig["score"]  = 0
            sig["reason"] = f"[🏦BLOQUEADO inst:{lbias.bias} {lbias.score}/100] " + sig.get("reason", "")
        else:
            penalty = int((abs(lbias.score - 50) / 50) * 10)
            sig["score"]  = max(0, sig.get("score", 50) - penalty)
            sig["reason"] = f"[🏦INST-{penalty}] " + sig.get("reason", "")

    return sig
