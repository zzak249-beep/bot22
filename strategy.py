"""
strategy.py — Motor de 4 Estrategias + Filtro Institucional v7
═══════════════════════════════════════════════════════════════
Cada señal técnica pasa por liquidity.py antes de ejecutarse:

  1. BB_RSI      — Bollinger Bands + RSI sobrevendido/sobrecomprado
  2. EMA_MULTI   — EMAs 3/8/21/55/200: continuaciones + reversiones
  3. BREAKOUT    — Ruptura de rango + volumen + expansión ATR
  4. FLASH_ARB   — Spread precio vs 3 precios justos independientes

Flujo institucional (liquidity.py):
  • Confirma la señal   → +bonus score (hasta +15)
  • Contradice suave    → penalización score (-10)
  • Contradice fuerte   → señal BLOQUEADA
  • Consenso 2 estrat.  → +10 score
  • Consenso 3+ estrat. → +15 score
"""
import logging
import statistics
import pandas as pd

import config as cfg

log = logging.getLogger("strategy")

# Import liquidity con fallback por si hay error de red
try:
    import liquidity as liq
    _LIQ_AVAILABLE = True
except Exception:
    _LIQ_AVAILABLE = False
    log.warning("liquidity.py no disponible — filtro institucional desactivado")


# ══════════════════════════════════════════════════════════
# HELPERS COMUNES
# ══════════════════════════════════════════════════════════

def _ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()

def _rsi(closes: pd.Series, period: int = 14) -> float:
    d   = closes.diff()
    g   = d.clip(lower=0).rolling(period).mean()
    l   = (-d.clip(upper=0)).rolling(period).mean()
    rs  = g / l.replace(0, 1e-10)
    val = (100 - 100 / (1 + rs)).iloc[-1]
    return float(val) if val == val else 50.0

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    hi, lo, cl = df["high"], df["low"], df["close"]
    tr  = pd.concat([(hi - lo),
                     (hi - cl.shift()).abs(),
                     (lo - cl.shift()).abs()], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if val == val else 0.0

def _bb(closes: pd.Series, period: int = 20, sigma: float = 2.0) -> dict:
    m  = closes.rolling(period).mean()
    s  = closes.rolling(period).std()
    up = m + sigma * s
    dn = m - sigma * s
    price = float(closes.iloc[-1])
    upper = float(up.iloc[-1]); lower = float(dn.iloc[-1])
    width = upper - lower
    return {
        "mid":   float(m.iloc[-1]),
        "upper": upper,
        "lower": lower,
        "pos":   (price - lower) / width if width > 0 else 0.5,
        "width": width,
    }

def _trend_4h(df_4h) -> str:
    if df_4h is None or len(df_4h) < 50:
        return "flat"
    p = float(df_4h["close"].iloc[-1])
    e = float(_ema(df_4h["close"], 50).iloc[-1])
    if p > e * 1.01:  return "up"
    if p < e * 0.99:  return "down"
    return "flat"

def _vol_ratio(df: pd.DataFrame, period: int = 20) -> float:
    v = df["volume"]
    a = float(v.iloc[-period - 1:-1].mean())
    return float(v.iloc[-1]) / a if a > 0 else 1.0

def _sl_tp(price: float, atr: float, side: str):
    """Calcula SL, TP y R:R para long o short."""
    if side == "long":
        sl = price - atr * cfg.SL_ATR
        tp = price + atr * cfg.TP_ATR_MULT
        rr_n = tp - price; rr_d = price - sl
    else:
        sl = price + atr * cfg.SL_ATR
        tp = price - atr * cfg.TP_ATR_MULT
        rr_n = price - tp; rr_d = sl - price
    rr = rr_n / rr_d if rr_d > 0 else 0
    return round(sl, 8), round(tp, 8), round(rr, 2)

def _base(price, atr, side, strategy, score, reason, rsi, trend):
    """Construye el dict base de una señal."""
    sl, tp, rr = _sl_tp(price, atr, side)
    return {
        "action":   "buy" if side == "long" else "sell_short",
        "strategy": strategy,
        "entry":    price,
        "sl":       sl,
        "tp":       tp,
        "rr":       rr,
        "atr":      atr,
        "score":    min(100, max(0, score)),
        "reason":   reason,
        "rsi":      round(rsi, 1) if rsi else 50,
        "trend_4h": trend,
    }


# ══════════════════════════════════════════════════════════
# ESTRATEGIA 1: BOLLINGER BANDS + RSI
# ══════════════════════════════════════════════════════════

def _strategy_bb_rsi(df: pd.DataFrame, df_4h=None) -> dict:
    """
    LONG : precio ≤ banda inferior BB + RSI sobrevendido
    SHORT: precio ≥ banda superior BB + RSI sobrecomprado
    Bonus: profundidad de RSI, posición en BB, tendencia 4h
    """
    if not getattr(cfg, "STRATEGY_BB_RSI_ENABLED", True) or len(df) < 30:
        return {"action": "none", "score": 0, "reason": "BB_RSI desactivado"}

    closes = df["close"]
    price  = float(closes.iloc[-1])
    rsi    = _rsi(closes)
    bb     = _bb(closes, cfg.BB_PERIOD, cfg.BB_SIGMA)
    atr    = _atr(df)
    trend  = _trend_4h(df_4h)

    if atr <= 0:
        return {"action": "none", "score": 0, "reason": "ATR=0"}

    # ── LONG ──────────────────────────────────────────────
    if price <= bb["lower"] * 1.003 and rsi < cfg.RSI_OS and trend != "down":
        score = 50
        score += min(20, int(cfg.RSI_OS - rsi))      # RSI más bajo = más señal
        if bb["pos"] < 0.05:  score += 12             # muy dentro de la banda
        elif bb["pos"] < 0.1: score += 7
        if trend == "up":     score += 10
        return _base(price, atr, "long", "BB_RSI", score,
                     f"BB_RSI LONG | RSI={rsi:.1f} pos={bb['pos']:.2f} trend={trend}",
                     rsi, trend)

    # ── SHORT ─────────────────────────────────────────────
    if price >= bb["upper"] * 0.997 and rsi > cfg.RSI_OB and trend != "up":
        score = 50
        score += min(20, int(rsi - cfg.RSI_OB))
        if bb["pos"] > 0.95:  score += 12
        elif bb["pos"] > 0.9: score += 7
        if trend == "down":   score += 10
        return _base(price, atr, "short", "BB_RSI", score,
                     f"BB_RSI SHORT | RSI={rsi:.1f} pos={bb['pos']:.2f} trend={trend}",
                     rsi, trend)

    return {"action": "none", "score": 0,
            "reason": f"BB_RSI sin señal RSI={rsi:.1f} pos={bb['pos']:.2f}"}


# ══════════════════════════════════════════════════════════
# ESTRATEGIA 2: EMA MULTI 3/8/21/55/200
# Continuaciones Y Reversiones
# ══════════════════════════════════════════════════════════

def _strategy_ema_multi(df: pd.DataFrame, df_4h=None) -> dict:
    """
    4 modos de entrada usando la estructura completa de EMAs:

    CONTINUACIÓN ALCISTA:
      Todas EMAs alineadas 3>8>21>55>200 + pullback a EMA8/21 + bounce

    REVERSIÓN ALCISTA:
      EMA200 pendiente positiva + precio rebota en EMA55 + cruce 3>8

    CONTINUACIÓN BAJISTA (mirror):
      Todas EMAs 3<8<21<55<200 + rebote en EMA8/21 bajista

    REVERSIÓN BAJISTA (mirror):
      EMA200 pendiente negativa + precio rechazado en EMA55 + cruce 3<8
    """
    if not getattr(cfg, "STRATEGY_EMA_CROSS_ENABLED", True) or len(df) < 210:
        return {"action": "none", "score": 0, "reason": "EMA_MULTI sin datos (necesita 210 velas)"}

    closes = df["close"]
    price  = float(closes.iloc[-1])
    atr    = _atr(df)
    trend  = _trend_4h(df_4h)
    vr     = _vol_ratio(df)

    if atr <= 0:
        return {"action": "none", "score": 0, "reason": "ATR=0"}

    # Calcular todas las EMAs
    e3   = _ema(closes, 3);   e8   = _ema(closes, 8)
    e21  = _ema(closes, 21);  e55  = _ema(closes, 55)
    e200 = _ema(closes, 200)

    v3   = float(e3.iloc[-1]);   v3p  = float(e3.iloc[-2])
    v8   = float(e8.iloc[-1]);   v8p  = float(e8.iloc[-2])
    v21  = float(e21.iloc[-1])
    v55  = float(e55.iloc[-1])
    v200 = float(e200.iloc[-1]);  v200_10 = float(e200.iloc[-10])

    # Pendiente EMA200 (momentum de largo plazo)
    slope200 = (v200 - v200_10) / v200_10 if v200_10 > 0 else 0

    # Estructura de EMAs
    aligned_bull = v3 > v8 > v21 > v55 > v200
    aligned_bear = v3 < v8 < v21 < v55 < v200

    # Cruce EMA3 sobre/bajo EMA8
    cross_up   = v3p <= v8p and v3 > v8
    cross_down = v3p >= v8p and v3 < v8

    rsi = _rsi(closes)

    # ── CONTINUACIÓN ALCISTA ───────────────────────────────
    if aligned_bull and trend != "down":
        touch  = v55 < price <= max(v8, v21) * 1.005
        bounce = cross_up or (price > v8 and float(closes.iloc[-2]) <= v8 * 1.001)
        if touch and bounce and vr >= 1.0:
            score = 60
            if trend == "up":       score += 10
            if vr > 1.5:            score += 8
            if slope200 > 0.001:    score += 7
            if cross_up:            score += 5
            return _base(price, atr, "long", "EMA_MULTI", score,
                         f"EMA_CONT_BULL pull EMA8/21 vol={vr:.1f}x slope200={slope200*100:.3f}%",
                         rsi, trend)

    # ── REVERSIÓN ALCISTA ──────────────────────────────────
    if not aligned_bull and slope200 > 0 and trend != "down":
        near_e55 = v55 * 0.99 <= price <= v55 * 1.02
        if near_e55 and price > v200 and cross_up and rsi < 50 and vr >= 1.0:
            score = 55
            if slope200 > 0.002: score += 8
            if trend == "up":    score += 8
            if vr > 1.3:         score += 7
            if rsi < 40:         score += 5
            return _base(price, atr, "long", "EMA_MULTI", score,
                         f"EMA_REV_BULL toque EMA55={v55:.5f} cross3>8 RSI={rsi:.1f}",
                         rsi, trend)

    # ── CONTINUACIÓN BAJISTA ───────────────────────────────
    if aligned_bear and trend != "up":
        touch  = min(v8, v21) * 0.995 <= price < v55
        bounce = cross_down or (price < v8 and float(closes.iloc[-2]) >= v8 * 0.999)
        if touch and bounce and vr >= 1.0:
            score = 60
            if trend == "down":     score += 10
            if vr > 1.5:            score += 8
            if slope200 < -0.001:   score += 7
            if cross_down:          score += 5
            return _base(price, atr, "short", "EMA_MULTI", score,
                         f"EMA_CONT_BEAR pull EMA8/21 vol={vr:.1f}x slope200={slope200*100:.3f}%",
                         rsi, trend)

    # ── REVERSIÓN BAJISTA ──────────────────────────────────
    if not aligned_bear and slope200 < 0 and trend != "up":
        near_e55 = v55 * 0.98 <= price <= v55 * 1.01
        if near_e55 and price < v200 and cross_down and rsi > 50 and vr >= 1.0:
            score = 55
            if slope200 < -0.002: score += 8
            if trend == "down":   score += 8
            if vr > 1.3:          score += 7
            if rsi > 60:          score += 5
            return _base(price, atr, "short", "EMA_MULTI", score,
                         f"EMA_REV_BEAR toque EMA55={v55:.5f} cross3<8 RSI={rsi:.1f}",
                         rsi, trend)

    return {"action": "none", "score": 0,
            "reason": f"EMA_MULTI sin señal | alin_bull={aligned_bull} alin_bear={aligned_bear} slope200={slope200*100:.3f}%"}


# ══════════════════════════════════════════════════════════
# ESTRATEGIA 3: BREAKOUT DE RANGO
# ══════════════════════════════════════════════════════════

def _strategy_breakout(df: pd.DataFrame, df_4h=None) -> dict:
    """
    LONG : precio rompe máximo de 20 velas + volumen alto + ATR expandiéndose
    SHORT: precio rompe mínimo de 20 velas + volumen alto + ATR expandiéndose
    Evita falsas rupturas exigiendo expansión de ATR y volumen 1.3x.
    """
    if not getattr(cfg, "STRATEGY_BREAKOUT_ENABLED", True) or len(df) < 30:
        return {"action": "none", "score": 0, "reason": "BREAKOUT desactivado"}

    closes = df["close"]; highs = df["high"]; lows = df["low"]
    price  = float(closes.iloc[-1])
    atr    = _atr(df)
    trend  = _trend_4h(df_4h)
    vr     = _vol_ratio(df)

    prev_high = float(highs.iloc[-21:-1].max())
    prev_low  = float(lows.iloc[-21:-1].min())
    rng       = prev_high - prev_low

    atr_now  = _atr(df.iloc[-15:]) if len(df) >= 15 else atr
    atr_prev = _atr(df.iloc[-30:-15]) if len(df) >= 30 else atr
    atr_exp  = atr_now > atr_prev * 1.1

    # LONG
    if price > prev_high * 1.001 and vr >= 1.3 and atr_exp and trend != "down":
        score = 55
        if vr > 2.0:   score += 15
        elif vr > 1.5: score += 8
        if trend == "up":  score += 10
        if rng > 0 and (price - prev_high) / rng > 0.03: score += 5
        return _base(price, atr, "long", "BREAKOUT", score,
                     f"BREAKOUT LONG rango {prev_low:.5f}-{prev_high:.5f} vol={vr:.1f}x",
                     _rsi(closes), trend)

    # SHORT
    if price < prev_low * 0.999 and vr >= 1.3 and atr_exp and trend != "up":
        score = 55
        if vr > 2.0:    score += 15
        elif vr > 1.5:  score += 8
        if trend == "down": score += 10
        if rng > 0 and (prev_low - price) / rng > 0.03: score += 5
        return _base(price, atr, "short", "BREAKOUT", score,
                     f"BREAKOUT SHORT rango {prev_low:.5f}-{prev_high:.5f} vol={vr:.1f}x",
                     _rsi(closes), trend)

    return {"action": "none", "score": 0,
            "reason": f"BREAKOUT sin señal vol={vr:.1f}x ATR_exp={atr_exp}"}


# ══════════════════════════════════════════════════════════
# ESTRATEGIA 4: FLASH ARB — Spread precio multi-capa
# ══════════════════════════════════════════════════════════

def _strategy_flash_arb(df: pd.DataFrame, df_4h=None) -> dict:
    """
    Inspirada en el contrato de arbitraje flash (Solidity):
    El contrato detecta diferencias entre DEXs para obtener ganancia.

    Aquí calculamos 3 "precios justos" como si fueran 3 DEXs:
      DEX1 = (EMA3 + EMA8) / 2    → precio de corto plazo
      DEX2 = EMA21                 → precio de medio plazo
      DEX3 = media BB              → precio "fundamental"

    LONG : precio spot por debajo de los 3 simultáneamente (ineficiencia bajista)
    SHORT: precio spot por encima de los 3 simultáneamente (ineficiencia alcista)

    El spread mínimo se calcula como porcentaje del ATR (como minProfit en el contrato).
    """
    if not getattr(cfg, "STRATEGY_FLASH_ARB_ENABLED", True) or len(df) < 55:
        return {"action": "none", "score": 0, "reason": "FLASH_ARB desactivado"}

    closes = df["close"]
    price  = float(closes.iloc[-1])
    atr    = _atr(df)
    trend  = _trend_4h(df_4h)
    rsi    = _rsi(closes)
    vr     = _vol_ratio(df)

    if atr <= 0 or price <= 0:
        return {"action": "none", "score": 0, "reason": "FLASH_ARB ATR/precio=0"}

    # 3 precios justos independientes
    fair_fast = (float(_ema(closes, 3).iloc[-1]) + float(_ema(closes, 8).iloc[-1])) / 2
    fair_mid  = float(_ema(closes, 21).iloc[-1])
    fair_slow = _bb(closes, cfg.BB_PERIOD, cfg.BB_SIGMA)["mid"]

    # Spread mínimo ajustado por volatilidad (como minProfit del contrato)
    min_spread = max(0.004, (atr / price) * 0.3)

    # ── LONG: precio injustamente bajo ─────────────────────
    sf = (fair_fast - price) / price
    sm = (fair_mid  - price) / price
    ss = (fair_slow - price) / price

    if sf > min_spread and sm > min_spread and ss > min_spread and rsi < 55 and trend != "down":
        avg   = (sf + sm + ss) / 3
        score = 50 + min(25, int(avg * 1000))
        if trend == "up":  score += 10
        if vr > 1.2:       score += 7
        if rsi < 40:       score += 8
        try:
            if statistics.stdev([sf, sm, ss]) < 0.003: score += 5  # spreads coherentes
        except Exception:
            pass
        return _base(price, atr, "long", "FLASH_ARB", score,
                     f"FLASH_ARB LONG fast={sf*100:.2f}% mid={sm*100:.2f}% slow={ss*100:.2f}% min={min_spread*100:.2f}%",
                     rsi, trend)

    # ── SHORT: precio injustamente alto ────────────────────
    sf2 = (price - fair_fast) / price
    sm2 = (price - fair_mid)  / price
    ss2 = (price - fair_slow) / price

    if sf2 > min_spread and sm2 > min_spread and ss2 > min_spread and rsi > 45 and trend != "up":
        avg   = (sf2 + sm2 + ss2) / 3
        score = 50 + min(25, int(avg * 1000))
        if trend == "down": score += 10
        if vr > 1.2:        score += 7
        if rsi > 60:        score += 8
        try:
            if statistics.stdev([sf2, sm2, ss2]) < 0.003: score += 5
        except Exception:
            pass
        return _base(price, atr, "short", "FLASH_ARB", score,
                     f"FLASH_ARB SHORT fast={sf2*100:.2f}% mid={sm2*100:.2f}% slow={ss2*100:.2f}% min={min_spread*100:.2f}%",
                     rsi, trend)

    return {"action": "none", "score": 0,
            "reason": f"FLASH_ARB sin señal | min_req={min_spread*100:.2f}% fast={sf*100:.2f}% mid={sm*100:.2f}% slow={ss*100:.2f}%"}


# ══════════════════════════════════════════════════════════
# AGREGADOR PRINCIPAL
# ══════════════════════════════════════════════════════════

def get_signal(df: pd.DataFrame, df_4h=None) -> dict:
    """
    Flujo completo:
      1. Ejecutar las 4 estrategias técnicas independientes
      2. Elegir la de mayor score
      3. Aplicar bonus por consenso (2+ estrategias misma dirección)
      4. Aplicar filtro de liquidez institucional (liquidity.py)
    """
    if df is None or len(df) < 25:
        return {"action": "none", "score": 0, "reason": "datos insuficientes",
                "entry": 0, "sl": 0, "tp": 0, "atr": 0, "rsi": 50, "trend_4h": "flat"}

    price = float(df["close"].iloc[-1])

    # ── 1. Ejecutar estrategias ────────────────────────────
    results = [
        _strategy_bb_rsi(df, df_4h),
        _strategy_ema_multi(df, df_4h),
        _strategy_breakout(df, df_4h),
        _strategy_flash_arb(df, df_4h),
    ]

    active = [r for r in results if r["action"] in ("buy", "sell_short")]

    if not active:
        best_none = max(results, key=lambda x: len(x.get("reason", "")))
        return {
            "action": "none", "score": 0,
            "reason": best_none.get("reason", "sin señal"),
            "entry": price, "sl": 0, "tp": 0,
            "atr": _atr(df), "rsi": _rsi(df["close"]), "trend_4h": _trend_4h(df_4h),
        }

    # ── 2. Mejor señal ─────────────────────────────────────
    best = max(active, key=lambda x: x["score"])

    # ── 3. Bonus por consenso ──────────────────────────────
    same = [r for r in active if r["action"] == best["action"]]
    n    = len(same)
    if n >= 2:
        best   = best.copy()
        bonus  = 10 if n == 2 else 15
        strats = " + ".join(r.get("strategy", "?") for r in same)
        best["score"]  = min(100, best["score"] + bonus)
        best["reason"] = f"[CONSENSO {n}x:{strats} +{bonus}] " + best["reason"]
        log.info(f"Consenso {n} estrategias ({strats}): {best['action']} score={best['score']}")

    # ── 4. Filtro Institucional ────────────────────────────
    if _LIQ_AVAILABLE and getattr(cfg, "LIQUIDITY_ENABLED", True):
        try:
            symbol = df.attrs.get("symbol", "")
            if symbol:
                lbias = liq.analyze(symbol, price, best.get("atr", 0))
                best  = liq.apply_liquidity_filter(best, lbias)
                best["liquidity_bias"]    = lbias.bias
                best["liquidity_score"]   = lbias.score
                best["liquidity_summary"] = lbias.summary
        except Exception as e:
            log.debug(f"liquidity filter: {e}")

    # Garantizar campos mínimos
    best.setdefault("rsi",      _rsi(df["close"]))
    best.setdefault("trend_4h", _trend_4h(df_4h))
    best.setdefault("atr",      _atr(df))

    return best


# ══════════════════════════════════════════════════════════
# SALIDA ANTICIPADA
# ══════════════════════════════════════════════════════════

def should_exit_early(df: pd.DataFrame, pos: dict) -> tuple:
    """
    Detecta agotamiento de tendencia para salir antes del SL.
    Retorna (salir: bool, razón: str)
    """
    if df is None or len(df) < 20:
        return False, ""

    closes = df["close"]
    side   = pos.get("side", "long")
    entry  = pos.get("entry", 0)
    price  = float(closes.iloc[-1])
    rsi    = _rsi(closes)
    bb     = _bb(closes, cfg.BB_PERIOD, cfg.BB_SIGMA)

    if side == "long":
        # RSI sobrecomprado en banda superior → salir con ganancia
        if rsi > 75 and bb["pos"] > 0.92 and price > entry:
            return True, "EXIT_OVERBOUGHT"
        # Divergencia bajista: precio en máximos pero RSI bajando
        if len(closes) >= 10:
            p_high = float(closes.iloc[-10:].max())
            r_prev = _rsi(closes.iloc[:-5])
            if price >= p_high * 0.99 and r_prev - rsi > 5 and price > entry:
                return True, "EXIT_DIVERGENCE"

    if side == "short":
        # RSI sobrevendido en banda inferior → salir con ganancia
        if rsi < 25 and bb["pos"] < 0.08 and price < entry:
            return True, "EXIT_OVERSOLD"

    return False, ""


# ══════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════

def check_circuit_breaker(stats: dict, balance_snapshot: float) -> tuple:
    pnl = stats.get("pnl_today", 0)
    if balance_snapshot > 0:
        pct = pnl / balance_snapshot
        if pct < cfg.MAX_PNL_NEGATIVO_DIA:
            return True, f"Pérdida día {pct*100:.1f}% > límite {cfg.MAX_PNL_NEGATIVO_DIA*100:.0f}%"
    if stats.get("losses", 0) >= cfg.MAX_PERDIDAS_SEGUIDAS and stats.get("wins", 0) == 0:
        return True, f"{stats['losses']} pérdidas seguidas sin wins"
    return False, ""
