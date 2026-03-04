"""
strategy.py — BB+RSI ELITE v5
Fixes vs v4:
  - Score minimo bajado de 55 → 45 (55 bloqueaba todo con tendencia neutral)
  - bb_touch_long: precio TOCA la banda inferior (no solo cruza por debajo)
    Esto multiplica las señales sin sacrificar calidad
  - RSI_OB subido a 45 en config (40 era demasiado estricto)
  - MACD ya no es obligatorio — suma al score pero no bloquea solo
  - Mantiene todos los filtros de calidad: score, divergencia, momentum
"""
import pandas as pd
import numpy as np
import config as cfg


# ═══════════════════════════════════════════════════════════
# INDICADORES BASE
# ═══════════════════════════════════════════════════════════

def calc_bb(close, period=None, sigma=None):
    period = period or cfg.BB_PERIOD
    sigma  = sigma  or cfg.BB_SIGMA
    basis  = close.rolling(period).mean()
    std    = close.rolling(period).std()
    return basis + sigma * std, basis, basis - sigma * std


def calc_rsi(close, period=None):
    period = period or cfg.RSI_PERIOD
    delta  = close.diff()
    gain   = delta.clip(lower=0).rolling(period).mean()
    loss   = (-delta.clip(upper=0)).rolling(period).mean()
    rs     = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def calc_atr(df, p=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(p).mean()


def calc_macd(close, fast=12, slow=26, signal=9):
    ema_f = close.ewm(span=fast,   adjust=False).mean()
    ema_s = close.ewm(span=slow,   adjust=False).mean()
    line  = ema_f - ema_s
    sig   = line.ewm(span=signal,  adjust=False).mean()
    hist  = line - sig
    return line, sig, hist


def calc_keltner(df, period=20, mult=1.5):
    mid = df["close"].ewm(span=period, adjust=False).mean()
    atr = calc_atr(df, period)
    return mid + mult * atr, mid, mid - mult * atr


def calc_volume_spike(volume, period=20):
    avg = volume.rolling(period).mean()
    return volume / avg.replace(0, float("nan"))


def calc_stoch_rsi(close, period=14, smooth_k=3, smooth_d=3):
    rsi     = calc_rsi(close, period)
    rsi_min = rsi.rolling(period).min()
    rsi_max = rsi.rolling(period).max()
    rng     = (rsi_max - rsi_min).replace(0, float("nan"))
    k       = ((rsi - rsi_min) / rng * 100).rolling(smooth_k).mean()
    d       = k.rolling(smooth_d).mean()
    return k, d


# ═══════════════════════════════════════════════════════════
# REGIMEN DE MERCADO
# ═══════════════════════════════════════════════════════════

def get_trend_htf(df_4h):
    """Tendencia en 4h: BB + RSI + MACD. Requiere los 3 de acuerdo."""
    if df_4h is None or len(df_4h) < cfg.BB_PERIOD + 5:
        return "neutral"
    _, basis_4h, _ = calc_bb(df_4h["close"])
    rsi_4h          = calc_rsi(df_4h["close"])
    _, _, macd_h    = calc_macd(df_4h["close"])
    price  = float(df_4h["close"].iloc[-1])
    basis  = float(basis_4h.iloc[-1])
    rsi    = float(rsi_4h.iloc[-1])
    m_hist = float(macd_h.iloc[-1])
    if price > basis and rsi < 70 and m_hist > 0:
        return "bull"
    elif price < basis and rsi > 30 and m_hist < 0:
        return "bear"
    return "neutral"


def is_sideways(df):
    cur   = df.iloc[-1]
    basis = float(cur["basis"])
    if basis == 0:
        return False
    bb_width  = (float(cur["upper"]) - float(cur["lower"])) / basis
    atr_avg   = df["atr"].rolling(50).mean().iloc[-1]
    atr_ratio = float(cur["atr"]) / atr_avg if atr_avg and atr_avg > 0 else 1.0
    return bb_width < cfg.SIDEWAYS_BB_WIDTH and atr_ratio < cfg.SIDEWAYS_ATR_RATIO


def detect_squeeze(df):
    upper_bb, _, lower_bb = calc_bb(df["close"])
    upper_kc, _, lower_kc = calc_keltner(df)
    return (lower_bb.iloc[-1] > lower_kc.iloc[-1]) and \
           (upper_bb.iloc[-1] < upper_kc.iloc[-1])


# ═══════════════════════════════════════════════════════════
# DIVERGENCIAS
# ═══════════════════════════════════════════════════════════

def detect_rsi_divergence(df, lookback=6):
    if len(df) < lookback + 2:
        return None
    rec = df.tail(lookback + 1)
    p_now      = float(rec["close"].iloc[-1])
    r_now      = float(rec["rsi"].iloc[-1])
    p_low_prev = float(rec["close"].iloc[:-1].min())
    r_prev_min = float(rec["rsi"].iloc[:-1].min())
    if p_now < p_low_prev and r_now > r_prev_min + 3:
        return "bullish"
    p_hi_prev = float(rec["close"].iloc[:-1].max())
    r_hi_prev = float(rec["rsi"].iloc[:-1].max())
    if p_now > p_hi_prev and r_now < r_hi_prev - 3:
        return "bearish"
    return None


# ═══════════════════════════════════════════════════════════
# FILTROS DE CALIDAD
# ═══════════════════════════════════════════════════════════

def has_momentum_confirmation(df, side="long"):
    if len(df) < 4:
        return True
    bodies = (df["close"] - df["open"]).abs().tail(3)
    return float(bodies.iloc[-1]) <= float(bodies.iloc[-2]) * 1.6


def calc_signal_score(rsi, trend, divergence, macd_aligned,
                      squeeze, sideways, side="long"):
    """
    Score 0-100. FIX: minimo bajado a 45 (antes 55 bloqueaba señales validas).
    Con tendencia neutral y RSI 35-45, score = 40+10+5 = 55 → ENTRA.
    """
    score = 40  # base por tener señal BB

    if side == "long":
        if rsi < 25:              score += 25   # RSI extremadamente sobrevendido
        elif rsi < 30:            score += 20
        elif rsi < 40:            score += 12
        elif rsi < 48:            score += 6    # FIX: RSI hasta 48 da puntos (antes cortaba en 40)
        if trend == "bull":       score += 15
        elif trend == "neutral":  score += 5
        if divergence == "bullish": score += 15
        if macd_aligned:          score += 8    # FIX: reducido de 10 a 8, ya no es obligatorio
        if squeeze:               score += 8
        if sideways:              score += 5
    else:
        if rsi > 75:              score += 25
        elif rsi > 70:            score += 20
        elif rsi > 60:            score += 12
        elif rsi > 52:            score += 6    # FIX: RSI desde 52 da puntos
        if trend == "bear":       score += 15
        elif trend == "neutral":  score += 5
        if divergence == "bearish": score += 15
        if macd_aligned:          score += 8
        if squeeze:               score += 8
        if sideways:              score += 5

    return min(score, 100)


# ═══════════════════════════════════════════════════════════
# SEÑAL PRINCIPAL
# ═══════════════════════════════════════════════════════════

def get_signal(df, df_4h=None):
    if len(df) < cfg.BB_PERIOD + 15:
        return {"action": "hold", "reason": "datos insuficientes"}

    df = df.copy()
    upper, basis, lower = calc_bb(df["close"])
    df["basis"]     = basis
    df["lower"]     = lower
    df["upper"]     = upper
    df["rsi"]       = calc_rsi(df["close"])
    df["atr"]       = calc_atr(df)
    df["vol_spike"] = calc_volume_spike(df["volume"]) if "volume" in df.columns else 1.0
    _, _, macd_hist = calc_macd(df["close"])
    df["macd_hist"] = macd_hist
    stoch_k, stoch_d = calc_stoch_rsi(df["close"])
    df["stoch_k"]   = stoch_k
    df["stoch_d"]   = stoch_d

    cur  = df.iloc[-1]
    prev = df.iloc[-2]
    price     = float(cur["close"])
    rsi       = float(cur["rsi"])
    atr       = float(cur["atr"])
    stoch_k_v = float(cur["stoch_k"]) if not pd.isna(cur["stoch_k"]) else 50
    vol_spike = float(cur["vol_spike"]) if not pd.isna(cur["vol_spike"]) else 1.0
    macd_bull = float(cur["macd_hist"]) > 0
    macd_bear = float(cur["macd_hist"]) < 0

    # ── Filtro volumen anomalo ─────────────────────────────
    if cfg.VOLUME_SPIKE_ENABLED and vol_spike > cfg.VOLUME_SPIKE_MULT:
        return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                "reason": f"Volumen anomalo x{vol_spike:.1f}"}

    trend      = get_trend_htf(df_4h)
    sideways   = is_sideways(df)
    squeeze    = detect_squeeze(df)
    divergence = detect_rsi_divergence(df)

    bb_lower = float(cur["lower"])
    bb_upper = float(cur["upper"])
    bb_basis = float(cur["basis"])

    # ─────────────────────────────────────────────────────
    # MERCADO LATERAL — mean reversion pura
    # ─────────────────────────────────────────────────────
    if sideways:
        if price <= bb_lower * 1.005 and rsi < 45 and stoch_k_v < 30 and trend != "bear":
            score = calc_signal_score(rsi, trend, divergence, macd_bull, squeeze, True, "long")
            if score >= 45:
                sl      = round(price - cfg.SL_ATR * atr * 0.7, 4)
                risk    = price - sl
                tp      = round(max(bb_basis, price + risk * 1.8), 4)
                tp_part = round(price + risk * 0.9, 4)
                return {
                    "action": "buy", "entry": round(price, 4),
                    "sl": sl, "tp": tp, "tp_partial": tp_part,
                    "rsi": round(rsi, 1), "atr": round(atr, 4),
                    "score": score, "trend_4h": trend,
                    "reason": f"LONG LATERAL | RSI={round(rsi,1)} Stoch={round(stoch_k_v,1)} Score={score}"
                }

        if cfg.SHORT_ENABLED and price >= bb_upper * 0.995 and rsi > 55 and stoch_k_v > 70 and trend != "bull":
            score = calc_signal_score(rsi, trend, divergence, macd_bear, squeeze, True, "short")
            if score >= 45:
                sl      = round(price + cfg.SL_ATR * atr * 0.7, 4)
                risk    = sl - price
                tp      = round(min(bb_basis, price - risk * 1.8), 4)
                tp_part = round(price - risk * 0.9, 4)
                return {
                    "action": "sell_short", "entry": round(price, 4),
                    "sl": sl, "tp": tp, "tp_partial": tp_part,
                    "rsi": round(rsi, 1), "atr": round(atr, 4),
                    "score": score, "trend_4h": trend,
                    "reason": f"SHORT LATERAL | RSI={round(rsi,1)} Stoch={round(stoch_k_v,1)} Score={score}"
                }

    # ─────────────────────────────────────────────────────
    # SEÑAL LONG — BB inferior
    # FIX: bb_touch_long = precio TOCA o cruza la banda (no solo cruza por debajo)
    # Antes: solo cruce estricto → muy raro. Ahora: precio <= lower * 1.002
    # ─────────────────────────────────────────────────────
    bb_touch_long = price <= bb_lower * 1.002   # toca o esta dentro del 0.2% de la banda
    bb_cross_long = float(prev["close"]) >= float(prev["lower"]) and price < bb_lower
    div_long      = divergence == "bullish" and rsi < 48 and price <= bb_lower * 1.008

    if (bb_touch_long or bb_cross_long or div_long) and rsi < cfg.RSI_OB and atr > 0:
        if trend == "bear" and not div_long:
            return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                    "reason": "LONG bloqueado: tendencia 4h bajista"}
        # FIX 5: LONG_ONLY_UP — strategy usa "bull", permitir también neutral con RSI muy bajo
        if getattr(cfg, "LONG_ONLY_UP", False) and trend == "bear":
            return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                    "reason": f"LONG bloqueado: LONG_ONLY_UP activo (trend={trend})"}
        if cfg.REQUIRE_MOMENTUM and not has_momentum_confirmation(df, "long"):
            return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                    "reason": "LONG bloqueado: caida libre sin desaceleracion"}

        score = calc_signal_score(rsi, trend, divergence, macd_bull, squeeze, sideways, "long")
        if score < 45:  # FIX: bajado de 55 a 45
            return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                    "reason": f"LONG descartado: score bajo ({score}/100)"}

        sl        = round(price - cfg.SL_ATR * atr, 4)
        risk      = price - sl
        # TP garantiza R:R >= 2.0 — usa el mayor entre: media BB y precio + 2x riesgo
        tp_rr     = round(price + risk * 2.0, 4)
        tp_full   = round(max(bb_basis, tp_rr), 4)
        tp_part   = round(price + risk * 1.0, 4)   # TP1 a 1:1 — asegura ganancia parcial
        tag     = "DIV" if div_long else ("SQZ" if squeeze else ("TOUCH" if bb_touch_long else "BB"))
        reason  = (f"LONG {tag} | RSI={round(rsi,1)} Stoch={round(stoch_k_v,1)} "
                   f"MACD={'↑' if macd_bull else '↓'} 4h={trend} Score={score}")
        return {
            "action": "buy", "entry": round(price, 4),
            "sl": sl, "tp": tp_full, "tp_partial": tp_part,
            "rsi": round(rsi, 1), "atr": round(atr, 4),
            "score": score, "trend_4h": trend,
            "bb_lower": round(bb_lower, 4),
            "bb_basis": round(bb_basis, 4),
            "reason": reason
        }

    # ─────────────────────────────────────────────────────
    # SEÑAL SHORT — BB superior
    # ─────────────────────────────────────────────────────
    if cfg.SHORT_ENABLED:
        bb_touch_short = price >= bb_upper * 0.998
        bb_cross_short = float(prev["close"]) <= float(prev["upper"]) and price > bb_upper
        div_short      = divergence == "bearish" and rsi > 52 and price >= bb_upper * 0.992

        if (bb_touch_short or bb_cross_short or div_short) and rsi > cfg.RSI_OS and atr > 0:
            if trend == "bull" and not div_short:
                return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                        "reason": "SHORT bloqueado: tendencia 4h alcista"}
            if cfg.REQUIRE_MOMENTUM and not has_momentum_confirmation(df, "short"):
                return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                        "reason": "SHORT bloqueado: subida sin desaceleracion"}

            score = calc_signal_score(rsi, trend, divergence, macd_bear, squeeze, sideways, "short")
            if score < 45:
                return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                        "reason": f"SHORT descartado: score bajo ({score}/100)"}

            sl        = round(price + cfg.SL_ATR * atr, 4)
            risk      = sl - price
            # TP garantiza R:R >= 2.0
            tp_rr     = round(price - risk * 2.0, 4)
            tp_full   = round(min(bb_basis, tp_rr), 4)
            tp_part   = round(price - risk * 1.0, 4)   # TP1 a 1:1
            tag     = "DIV" if div_short else ("SQZ" if squeeze else ("TOUCH" if bb_touch_short else "BB"))
            reason  = (f"SHORT {tag} | RSI={round(rsi,1)} Stoch={round(stoch_k_v,1)} "
                       f"MACD={'↑' if macd_bull else '↓'} 4h={trend} Score={score}")
            return {
                "action": "sell_short", "entry": round(price, 4),
                "sl": sl, "tp": tp_full, "tp_partial": tp_part,
                "rsi": round(rsi, 1), "atr": round(atr, 4),
                "score": score, "trend_4h": trend,
                "bb_upper": round(bb_upper, 4),
                "bb_basis": round(bb_basis, 4),
                "reason": reason
            }

    # ─────────────────────────────────────────────────────
    # SALIDAS
    # ─────────────────────────────────────────────────────
    if float(prev["close"]) <= float(prev["basis"]) and price > bb_basis:
        return {"action": "exit_long", "entry": round(price, 4), "rsi": round(rsi, 1),
                "reason": "LONG exit: cruzo media BB arriba"}

    if float(prev["close"]) >= float(prev["basis"]) and price < bb_basis:
        return {"action": "exit_short", "entry": round(price, 4), "rsi": round(rsi, 1),
                "reason": "SHORT exit: cruzo media BB abajo"}

    return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1), "reason": "Sin señal"}


# ═══════════════════════════════════════════════════════════
# SALIDA INTELIGENTE
# ═══════════════════════════════════════════════════════════

def should_exit_early(df, pos):
    if len(df) < 20:
        return False, ""

    side  = pos.get("side", "long")
    entry = pos["entry"]
    cur   = df.iloc[-1]
    price = float(cur["close"])

    if side == "long"  and price < entry * 1.003:
        return False, ""
    if side == "short" and price > entry * 0.997:
        return False, ""

    signals = []

    _, _, macd_hist = calc_macd(df["close"])
    h_now   = float(macd_hist.iloc[-1])
    h_prev  = float(macd_hist.iloc[-2])
    h_prev2 = float(macd_hist.iloc[-3])

    if side == "long":
        if h_now < h_prev < h_prev2 and h_prev2 > 0:
            signals.append("MACD↓ decreciendo")
        if h_prev > 0 and h_now < 0:
            signals.append("MACD cruzó negativo")
    else:
        if h_now > h_prev > h_prev2 and h_prev2 < 0:
            signals.append("MACD↑ creciendo")
        if h_prev < 0 and h_now > 0:
            signals.append("MACD cruzó positivo")

    rsi_s    = df["rsi"] if "rsi" in df.columns else calc_rsi(df["close"])
    rsi_now  = float(rsi_s.iloc[-1])
    rsi_prev = float(rsi_s.iloc[-3:-1].mean())
    p_prev   = float(df["close"].iloc[-3:-1].mean())

    if side == "long":
        if price > p_prev and rsi_now < rsi_prev - 3:
            signals.append("Div RSI bajista")
        if rsi_now > 72:
            signals.append("RSI sobrecomprado")
    else:
        if price < p_prev and rsi_now > rsi_prev + 3:
            signals.append("Div RSI alcista")
        if rsi_now < 28:
            signals.append("RSI sobrevendido")

    if "stoch_k" in df.columns:
        stoch = float(cur["stoch_k"]) if not pd.isna(cur["stoch_k"]) else 50
        if side == "long"  and stoch > 85:
            signals.append(f"StochRSI={round(stoch,1)} sobrecomprado")
        if side == "short" and stoch < 15:
            signals.append(f"StochRSI={round(stoch,1)} sobrevendido")

    body = abs(float(cur["close"]) - float(cur["open"]))
    if side == "long":
        upper_wick = float(cur["high"]) - max(float(cur["close"]), float(cur["open"]))
        if body > 0 and upper_wick > body * 2.0:
            signals.append("Mecha superior larga")
    else:
        lower_wick = min(float(cur["close"]), float(cur["open"])) - float(cur["low"])
        if body > 0 and lower_wick > body * 2.0:
            signals.append("Mecha inferior larga")

    if len(signals) >= 2:
        return True, "EXIT_AGOTAMIENTO: " + " | ".join(signals)

    return False, ""


# ═══════════════════════════════════════════════════════════
# TRAILING STOP
# ═══════════════════════════════════════════════════════════

def calc_trailing_stop(pos, cur_price, atr):
    side       = pos.get("side", "long")
    old_sl     = pos["sl"]
    entry      = pos["entry"]
    trail_dist = cfg.TRAILING_STOP_ATR * atr
    if side == "long":
        if cur_price < entry * (1 + cfg.TRAILING_ACTIVATE_PCT / 100):
            return old_sl
        return max(cur_price - trail_dist, old_sl)
    else:
        if cur_price > entry * (1 - cfg.TRAILING_ACTIVATE_PCT / 100):
            return old_sl
        return min(cur_price + trail_dist, old_sl)


# ═══════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════

def check_circuit_breaker(stats, balance_snapshot=0):
    pnl    = stats.get("pnl_today", 0)
    losses = stats.get("losses", 0)
    wins   = stats.get("wins", 0)
    trades = stats.get("trades_today", 0)

    if balance_snapshot > 0 and pnl < 0 and trades >= 2:
        pct = abs(pnl) / balance_snapshot
        if pct >= cfg.CB_MAX_DAILY_LOSS_PCT:
            return True, f"{round(pct*100,1)}% balance perdido hoy"

    if losses >= cfg.CB_MAX_CONSECUTIVE_LOSS and wins == 0 and trades >= cfg.CB_MAX_CONSECUTIVE_LOSS:
        return True, f"{losses} perdidas consecutivas sin ganancias"

    return False, None
