"""
strategy.py — BB+RSI ELITE v4
Basado en investigacion: BB+RSI+MACD logra 77% win rate en BTC (backtest 2024).
Mean reversion BB+RSI: 58-62% win rate historico en crypto.

Mejoras activas:
  1.  Trailing Stop adaptativo
  2.  Multi-timeframe 4h con MACD
  3.  TP Parcial automatico
  4.  SHORT con filtros
  5.  Filtro volumen anomalo
  6.  Mercado lateral / fines de semana
  7.  Divergencia RSI bullish/bearish
  8.  MACD histograma confirmacion
  9.  Circuit Breaker PnL + racha
  10. Filtro anti-caida libre (momentum)
  11. BB Squeeze (Keltner) — explosion inminente
  12. Score de calidad 0-100 por señal
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
    """Keltner Channel para detectar squeeze con BB."""
    mid = df["close"].ewm(span=period, adjust=False).mean()
    atr = calc_atr(df, period)
    return mid + mult * atr, mid, mid - mult * atr


def calc_volume_spike(volume, period=20):
    avg = volume.rolling(period).mean()
    return volume / avg.replace(0, float("nan"))


def calc_stoch_rsi(close, period=14, smooth_k=3, smooth_d=3):
    """StochRSI para deteccion mas precisa de extremos."""
    rsi    = calc_rsi(close, period)
    rsi_min = rsi.rolling(period).min()
    rsi_max = rsi.rolling(period).max()
    rng    = (rsi_max - rsi_min).replace(0, float("nan"))
    k      = ((rsi - rsi_min) / rng * 100).rolling(smooth_k).mean()
    d      = k.rolling(smooth_d).mean()
    return k, d


# ═══════════════════════════════════════════════════════════
# DETECCION DE REGIMEN DE MERCADO
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
    """
    Detecta mercado lateral: BB estrecho + ATR bajo.
    Umbral configurable desde config.py.
    """
    cur   = df.iloc[-1]
    basis = float(cur["basis"])
    if basis == 0:
        return False
    bb_width  = (float(cur["upper"]) - float(cur["lower"])) / basis
    atr_avg   = df["atr"].rolling(50).mean().iloc[-1]
    atr_ratio = float(cur["atr"]) / atr_avg if atr_avg and atr_avg > 0 else 1.0
    return bb_width < cfg.SIDEWAYS_BB_WIDTH and atr_ratio < cfg.SIDEWAYS_ATR_RATIO


def detect_squeeze(df):
    """
    BB Squeeze: BB dentro de Keltner = baja volatilidad comprimida.
    Cuando el squeeze se libera viene un movimiento explosivo.
    Retorna: True si hay squeeze activo ahora.
    """
    upper_bb, _, lower_bb = calc_bb(df["close"])
    upper_kc, _, lower_kc = calc_keltner(df)
    sq = (lower_bb.iloc[-1] > lower_kc.iloc[-1]) and \
         (upper_bb.iloc[-1] < upper_kc.iloc[-1])
    return sq


# ═══════════════════════════════════════════════════════════
# DIVERGENCIAS
# ═══════════════════════════════════════════════════════════

def detect_rsi_divergence(df, lookback=6):
    """
    Bullish: precio min mas bajo, RSI min mas alto → compra.
    Bearish: precio max mas alto, RSI max mas bajo → venta.
    """
    if len(df) < lookback + 2:
        return None
    rec = df.tail(lookback + 1)

    # Bullish
    p_low_now  = float(rec["close"].iloc[-1])
    p_low_prev = float(rec["close"].iloc[:-1].min())
    r_now      = float(rec["rsi"].iloc[-1])
    r_prev_min = float(rec["rsi"].iloc[:-1].min())
    if p_low_now < p_low_prev and r_now > r_prev_min + 3:
        return "bullish"

    # Bearish
    p_hi_now   = float(rec["close"].iloc[-1])
    p_hi_prev  = float(rec["close"].iloc[:-1].max())
    r_hi_prev  = float(rec["rsi"].iloc[:-1].max())
    if p_hi_now > p_hi_prev and r_now < r_hi_prev - 3:
        return "bearish"

    return None


# ═══════════════════════════════════════════════════════════
# FILTROS DE CALIDAD
# ═══════════════════════════════════════════════════════════

def has_momentum_confirmation(df, side="long"):
    """Verifica que la caida/subida se esta desacelerando antes de entrar."""
    if len(df) < 4:
        return True
    bodies = (df["close"] - df["open"]).abs().tail(3)
    return float(bodies.iloc[-1]) <= float(bodies.iloc[-2]) * 1.6


def calc_signal_score(rsi, trend, divergence, macd_aligned,
                      squeeze, sideways, side="long"):
    """
    Score 0-100 para cada señal. Solo entra si score >= 55.
    Basado en: cuantas confirmaciones coinciden.
    """
    score = 40  # base por tener señal BB

    if side == "long":
        if rsi < 30:              score += 20   # RSI muy sobrevendido
        elif rsi < 40:            score += 12
        if trend == "bull":       score += 15
        elif trend == "neutral":  score += 5
        if divergence == "bullish": score += 15
        if macd_aligned:          score += 10
        if squeeze:               score += 8    # explosion inminente
        if sideways:              score += 5    # lateral = mas fiable
    else:  # short
        if rsi > 70:              score += 20
        elif rsi > 60:            score += 12
        if trend == "bear":       score += 15
        elif trend == "neutral":  score += 5
        if divergence == "bearish": score += 15
        if macd_aligned:          score += 10
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

    # ── Filtro volumen anómalo ─────────────────────────────
    if cfg.VOLUME_SPIKE_ENABLED and vol_spike > cfg.VOLUME_SPIKE_MULT:
        return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                "reason": f"Volumen anomalo x{vol_spike:.1f}"}

    # ── Analisis de regimen ────────────────────────────────
    trend      = get_trend_htf(df_4h)
    sideways   = is_sideways(df)
    squeeze    = detect_squeeze(df)
    divergence = detect_rsi_divergence(df)

    # ── BB width: solo entrar con bandas suficientemente anchas
    # (señales en BB estrecho sin modo lateral son menos fiables)
    bb_width = (float(cur["upper"]) - float(cur["lower"])) / float(cur["basis"])

    # ─────────────────────────────────────────────────────
    # MERCADO LATERAL — mean reversion pura
    # ─────────────────────────────────────────────────────
    if sideways:
        if price < float(cur["lower"]) * 1.003 and rsi < 42 and stoch_k_v < 25 and trend != "bear":
            sl      = round(price - cfg.SL_ATR * atr * 0.7, 4)
            tp      = round(float(cur["basis"]), 4)
            tp_part = round(price + (tp - price) * 0.45, 4)
            score   = calc_signal_score(rsi, trend, divergence, macd_bull, squeeze, True, "long")
            return {
                "action": "buy", "entry": round(price, 4),
                "sl": sl, "tp": tp, "tp_partial": tp_part,
                "rsi": round(rsi, 1), "atr": round(atr, 4),
                "score": score, "trend_4h": trend,
                "reason": f"LONG LATERAL | RSI={round(rsi,1)} | Stoch={round(stoch_k_v,1)} | Score={score}"
            }
        if cfg.SHORT_ENABLED and price > float(cur["upper"]) * 0.997 and rsi > 58 and stoch_k_v > 75 and trend != "bull":
            sl      = round(price + cfg.SL_ATR * atr * 0.7, 4)
            tp      = round(float(cur["basis"]), 4)
            tp_part = round(price - (price - tp) * 0.45, 4)
            score   = calc_signal_score(rsi, trend, divergence, macd_bear, squeeze, True, "short")
            return {
                "action": "sell_short", "entry": round(price, 4),
                "sl": sl, "tp": tp, "tp_partial": tp_part,
                "rsi": round(rsi, 1), "atr": round(atr, 4),
                "score": score, "trend_4h": trend,
                "reason": f"SHORT LATERAL | RSI={round(rsi,1)} | Stoch={round(stoch_k_v,1)} | Score={score}"
            }

    # ─────────────────────────────────────────────────────
    # SEÑAL LONG — BB inferior + confirmaciones
    # ─────────────────────────────────────────────────────
    bb_cross_long = float(prev["close"]) >= float(prev["lower"]) and price < float(cur["lower"])
    div_long      = divergence == "bullish" and rsi < 45 and price <= float(cur["lower"]) * 1.006

    if (bb_cross_long or div_long) and rsi < cfg.RSI_OB and atr > 0:
        if trend == "bear" and not div_long:
            return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                    "reason": "LONG bloqueado: tendencia 4h bajista"}
        if cfg.REQUIRE_MOMENTUM and not has_momentum_confirmation(df, "long"):
            return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                    "reason": "LONG bloqueado: caida libre sin desaceleracion"}

        score = calc_signal_score(rsi, trend, divergence, macd_bull, squeeze, sideways, "long")
        if score < 55:
            return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                    "reason": f"LONG descartado: score bajo ({score}/100)"}

        sl      = round(price - cfg.SL_ATR * atr, 4)
        tp_full = round(float(cur["basis"]), 4)
        tp_part = round(price + cfg.PARTIAL_TP_ATR * atr, 4)
        tag     = "DIV" if div_long else ("SQZ" if squeeze else "BB")
        reason  = (f"LONG {tag} | RSI={round(rsi,1)} | Stoch={round(stoch_k_v,1)} | "
                   f"MACD={'↑' if macd_bull else '↓'} | 4h={trend} | Score={score}")
        return {
            "action": "buy", "entry": round(price, 4),
            "sl": sl, "tp": tp_full, "tp_partial": tp_part,
            "rsi": round(rsi, 1), "atr": round(atr, 4),
            "score": score, "trend_4h": trend,
            "bb_lower": round(float(cur["lower"]), 4),
            "bb_basis": round(float(cur["basis"]), 4),
            "reason": reason
        }

    # ─────────────────────────────────────────────────────
    # SEÑAL SHORT — BB superior + confirmaciones
    # ─────────────────────────────────────────────────────
    if cfg.SHORT_ENABLED:
        bb_cross_short = float(prev["close"]) <= float(prev["upper"]) and price > float(cur["upper"])
        div_short      = divergence == "bearish" and rsi > 55 and price >= float(cur["upper"]) * 0.994

        if (bb_cross_short or div_short) and rsi > cfg.RSI_OS and atr > 0:
            if trend == "bull" and not div_short:
                return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                        "reason": "SHORT bloqueado: tendencia 4h alcista"}
            if cfg.REQUIRE_MOMENTUM and not has_momentum_confirmation(df, "short"):
                return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                        "reason": "SHORT bloqueado: subida sin desaceleracion"}

            score = calc_signal_score(rsi, trend, divergence, macd_bear, squeeze, sideways, "short")
            if score < 55:
                return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1),
                        "reason": f"SHORT descartado: score bajo ({score}/100)"}

            sl      = round(price + cfg.SL_ATR * atr, 4)
            tp_full = round(float(cur["basis"]), 4)
            tp_part = round(price - cfg.PARTIAL_TP_ATR * atr, 4)
            tag     = "DIV" if div_short else ("SQZ" if squeeze else "BB")
            reason  = (f"SHORT {tag} | RSI={round(rsi,1)} | Stoch={round(stoch_k_v,1)} | "
                       f"MACD={'↑' if macd_bull else '↓'} | 4h={trend} | Score={score}")
            return {
                "action": "sell_short", "entry": round(price, 4),
                "sl": sl, "tp": tp_full, "tp_partial": tp_part,
                "rsi": round(rsi, 1), "atr": round(atr, 4),
                "score": score, "trend_4h": trend,
                "bb_upper": round(float(cur["upper"]), 4),
                "bb_basis": round(float(cur["basis"]), 4),
                "reason": reason
            }

    # ─────────────────────────────────────────────────────
    # SALIDAS
    # ─────────────────────────────────────────────────────
    if float(prev["close"]) <= float(prev["basis"]) and price > float(cur["basis"]):
        return {"action": "exit_long", "entry": round(price, 4), "rsi": round(rsi, 1),
                "reason": "LONG exit: cruzo media BB arriba"}

    if float(prev["close"]) >= float(prev["basis"]) and price < float(cur["basis"]):
        return {"action": "exit_short", "entry": round(price, 4), "rsi": round(rsi, 1),
                "reason": "SHORT exit: cruzo media BB abajo"}

    return {"action": "hold", "entry": round(price, 4), "rsi": round(rsi, 1), "reason": "Sin señal"}


# ═══════════════════════════════════════════════════════════
# SALIDA INTELIGENTE — detecta agotamiento de tendencia
# ═══════════════════════════════════════════════════════════

def should_exit_early(df, pos):
    """
    Detecta cuando las ganancias ya no pueden alargarse mas.
    Combina 4 señales de agotamiento — sale si 2 o mas coinciden.

    Señales:
      1. MACD histograma girando contra la posicion
      2. RSI divergencia contraria (precio sube pero RSI baja = largo agotado)
      3. StochRSI en zona extrema contraria
      4. Velas de agotamiento: mechas largas en direccion contraria

    Retorna: (bool, str) — (debe_salir, razon)
    """
    if len(df) < 20:
        return False, ""

    side  = pos.get("side", "long")
    entry = pos["entry"]
    cur   = df.iloc[-1]
    prev  = df.iloc[-2]
    price = float(cur["close"])

    # Solo evaluar si hay ganancia minima del 0.3% (no salir antes de ganar algo)
    if side == "long"  and price < entry * 1.003:
        return False, ""
    if side == "short" and price > entry * 0.997:
        return False, ""

    signals = []

    # ── Señal 1: MACD histograma girando en contra ─────────
    _, _, macd_hist = calc_macd(df["close"])
    h_now  = float(macd_hist.iloc[-1])
    h_prev = float(macd_hist.iloc[-2])
    h_prev2 = float(macd_hist.iloc[-3])
    if side == "long":
        # Histograma bajando 2 velas seguidas desde positivo
        if h_now < h_prev < h_prev2 and h_prev2 > 0:
            signals.append("MACD↓ decreciendo")
        # Cruce a negativo
        if h_prev > 0 and h_now < 0:
            signals.append("MACD cruzó negativo")
    else:
        if h_now > h_prev > h_prev2 and h_prev2 < 0:
            signals.append("MACD↑ creciendo")
        if h_prev < 0 and h_now > 0:
            signals.append("MACD cruzó positivo")

    # ── Señal 2: Divergencia RSI contraria ─────────────────
    rsi_s = df["rsi"] if "rsi" in df.columns else calc_rsi(df["close"])
    rsi_now  = float(rsi_s.iloc[-1])
    rsi_prev = float(rsi_s.iloc[-3:-1].mean())
    if side == "long":
        # Precio subiendo pero RSI bajando = agotamiento alcista
        p_prev = float(df["close"].iloc[-3:-1].mean())
        if price > p_prev and rsi_now < rsi_prev - 3:
            signals.append("Div RSI bajista")
        # RSI llego a zona alta
        if rsi_now > 72:
            signals.append("RSI sobrecomprado")
    else:
        p_prev = float(df["close"].iloc[-3:-1].mean())
        if price < p_prev and rsi_now > rsi_prev + 3:
            signals.append("Div RSI alcista")
        if rsi_now < 28:
            signals.append("RSI sobrevendido")

    # ── Señal 3: StochRSI en zona extrema contraria ────────
    if "stoch_k" in df.columns:
        stoch = float(cur["stoch_k"]) if not pd.isna(cur["stoch_k"]) else 50
        if side == "long"  and stoch > 85:
            signals.append(f"StochRSI={round(stoch,1)} sobrecomprado")
        if side == "short" and stoch < 15:
            signals.append(f"StochRSI={round(stoch,1)} sobrevendido")

    # ── Señal 4: Vela de agotamiento (mecha larga en contra) ─
    body     = abs(float(cur["close"]) - float(cur["open"]))
    if side == "long":
        upper_wick = float(cur["high"]) - max(float(cur["close"]), float(cur["open"]))
        if body > 0 and upper_wick > body * 2.0:
            signals.append("Mecha superior larga (rechazo)")
    else:
        lower_wick = min(float(cur["close"]), float(cur["open"])) - float(cur["low"])
        if body > 0 and lower_wick > body * 2.0:
            signals.append("Mecha inferior larga (rechazo)")

    # ── Decisión: salir si 2 o más señales coinciden ───────
    if len(signals) >= 2:
        reason = "EXIT_AGOTAMIENTO: " + " | ".join(signals)
        return True, reason

    return False, ""


# ═══════════════════════════════════════════════════════════
# TRAILING STOP
# ═══════════════════════════════════════════════════════════

def calc_trailing_stop(pos, cur_price, atr):
    """Mueve el SL en favor de la operacion. Nunca retrocede."""
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
    """
    Para el bot si:
    - Perdida diaria >= CB_MAX_DAILY_LOSS_PCT del balance inicial
    - O racha de CB_MAX_CONSECUTIVE_LOSS perdidas sin ninguna ganancia
    """
    pnl    = stats.get("pnl_today", 0)
    losses = stats.get("losses", 0)
    wins   = stats.get("wins", 0)
    trades = stats.get("trades_today", 0)

    # Condicion 1: % de perdida diaria sobre balance snapshot
    if balance_snapshot > 0 and pnl < 0 and trades >= 2:
        pct = abs(pnl) / balance_snapshot
        if pct >= cfg.CB_MAX_DAILY_LOSS_PCT:
            return True, f"{round(pct*100,1)}% balance perdido hoy (limite={cfg.CB_MAX_DAILY_LOSS_PCT*100:.0f}%)"

    # Condicion 2: racha de perdidas puras
    if losses >= cfg.CB_MAX_CONSECUTIVE_LOSS and wins == 0 and trades >= cfg.CB_MAX_CONSECUTIVE_LOSS:
        return True, f"{losses} perdidas consecutivas sin ganancias"

    return False, None