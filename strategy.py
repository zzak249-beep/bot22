"""EP5 v4 · Entrada Precisa 5m (reparada). Lógica pura, sin red.

Reparaciones respecto al Pine EP5-v3:
  1. HTF sin repintado: la EMA 15m se construye solo con velas 15m CERRADAS.
  2. distATR coherente: en v3 un LONG exigía htfBull (precio > EMA HTF) y a la vez
     distATR <= -0.4 (precio < EMA HTF) → casi imposible. Ahora el pullback válido
     es la banda [-touch_band, +pb_max] ATR alrededor de la EMA HTF a favor de tendencia.
  3. No-chase invertido en v3 (bloqueaba el long cuando estaba muy por DEBAJO).
     Ahora lo cubre pb_max: si está demasiado lejos a favor, no entra.
  4. Score redundante (todo lo que puntuaba ya era obligatorio). Ahora hay un núcleo
     obligatorio y un score de confluencias opcionales (sweep, FVG, killzone,
     dominancia, wavelet-trend) con mínimo configurable.
  5. El cruce ya no tiene que coincidir en la misma vela que todo lo demás: vale
     un cruce en las últimas cross_age velas si la tendencia sigue por encima.
  6. Trail monótono: el stop solo se mueve a favor (v3 podía bajarlo), y el BE
     cubre las comisiones.
  7. RVOL sin autoinclusión (media de volumen de las velas anteriores).
  8. Tamaño por riesgo (v3 usaba el 100% del equity) y salida por tiempo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, asdict

import numpy as np
import pandas as pd


def env_val(raw: str, typ):
    raw = raw.strip().strip('"').strip("'").strip()
    if typ is bool:
        return raw.lower() in ("1", "true", "yes", "si", "sí", "on")
    return typ(raw)


@dataclass
class Params:
    # Régimen
    look_energy: int = 40
    dom_umbral: float = 1.30
    er_rank_len: int = 100
    er_min_pct: float = 55.0
    # Wavelet
    wav_len: int = 32
    wav_s1: int = 3
    wav_s2: int = 8
    wav_s3: int = 21
    wav_trend_min: float = 0.38
    wav_noise_max: float = 0.45
    # Cruce
    approx_len: int = 8
    mra_smooth: int = 8
    cross_age: int = 2
    min_body_atr: float = 0.50
    min_close_loc: float = 0.60
    max_dist_approx: float = 1.20
    # HTF
    htf_minutes: int = 15
    htf_ema_len: int = 50
    htf_slope_bars: int = 3
    touch_band: float = 0.50
    pb_max: float = 1.20
    # Confluencias
    sweep_look: int = 12
    sweep_age: int = 3
    fvg_max_age: int = 20
    score_min: int = 2
    # Filtros
    session_mode: str = "LONDRES_NY"   # LONDRES_NY | KILLZONES | OVERLAP | TODO
    atr_len: int = 14
    atr_base_len: int = 50
    atr_min_mult: float = 0.90
    vol_len: int = 20
    vol_mult: float = 1.20
    vol_max: float = 5.0
    use_vwap: bool = True
    allow_long: bool = True
    allow_short: bool = True
    # Régimen BTC: largos solo con BTC 1h alcista, cortos solo con BTC 1h bajista
    btc_filter: bool = True
    btc_tf_min: int = 60
    btc_ema_len: int = 50
    btc_slope_bars: int = 3
    # Riesgo / gestión
    sl_atr: float = 1.4
    tp_atr: float = 2.2
    cost_pct: float = 0.10
    max_cost_r: float = 0.22
    be_r: float = 1.0
    trail_start_r: float = 1.5
    trail_atr: float = 1.2
    max_bars: int = 36
    cooldown_bars: int = 6

    @classmethod
    def from_env(cls) -> "Params":
        kw = {}
        for f in fields(cls):
            raw = os.getenv(f.name.upper())
            if raw is not None and raw.strip().strip('"').strip("'") != "":
                kw[f.name] = env_val(raw, type(f.default))
        return cls(**kw)


# ─────────────────────────── indicadores ───────────────────────────
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def true_atr(d: pd.DataFrame, n: int) -> pd.Series:
    pc = d.close.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return rma(tr, n)


def percentrank(s: pd.Series, n: int) -> pd.Series:
    """Como ta.percentrank: % de las n velas previas <= valor actual."""
    a = s.to_numpy(dtype=float)
    out = np.full(len(a), np.nan)
    if len(a) > n:
        w = np.lib.stride_tricks.sliding_window_view(a, n + 1)
        cur, prev = w[:, -1:], w[:, :-1]
        valid = ~np.isnan(w).any(axis=1)
        pr = (prev <= cur).sum(axis=1) / n * 100.0
        out[n:] = np.where(valid, pr, np.nan)
    return pd.Series(out, index=s.index)


def sessions(hour: pd.Series):
    lon = (hour >= 7) & (hour < 16)
    ny = (hour >= 13) & (hour < 21)
    ov = (hour >= 13) & (hour < 16)
    kz = ((hour >= 7) & (hour < 10)) | ov
    return lon, ny, ov, kz


def _htf(d: pd.DataFrame, t: pd.Series, p: Params, base_min: int) -> pd.DataFrame:
    return htf_trend(d, t, p.htf_minutes, p.htf_ema_len, p.htf_slope_bars, base_min)


def htf_trend(d: pd.DataFrame, t: pd.Series, m: int, ema_len: int, slope_bars: int, base_min: int = 5) -> pd.DataFrame:
    """EMA HTF con velas HTF cerradas, alineada sin lookahead a cada vela base."""
    per = max(1, m // base_min)
    x = pd.Series(d.close.to_numpy(), index=pd.DatetimeIndex(t))
    g = x.resample(f"{m}min", label="left", closed="left")
    hc = pd.DataFrame({"close": g.last(), "n": g.count()})
    hc = hc[hc.n == per].copy()
    hc["ema"] = ema(hc.close, ema_len)
    hc["slope"] = hc.ema - hc.ema.shift(slope_bars)
    hc["avail"] = hc.index + pd.Timedelta(minutes=m)
    left = pd.DataFrame({"ct": (t + pd.Timedelta(minutes=base_min)).to_numpy()})
    right = hc.reset_index(drop=True)[["avail", "close", "ema", "slope"]]
    right["avail"] = right["avail"].astype(left["ct"].dtype)
    return pd.merge_asof(left, right, left_on="ct", right_on="avail", direction="backward")


def compute(df: pd.DataFrame, p: Params, base_min: int = 5) -> pd.DataFrame:
    """df: time(ms, apertura), open, high, low, close, volume — SOLO velas cerradas."""
    d = df[["time", "open", "high", "low", "close", "volume"]].astype(float).reset_index(drop=True)
    d["time"] = d["time"].astype("int64")
    o, h, l, c, v = d.open, d.high, d.low, d.close, d.volume
    t = pd.Series(pd.to_datetime(d.time, unit="ms", utc=True))

    atr = true_atr(d, p.atr_len)
    atr_pct = atr / c * 100.0
    atr_exp = atr >= sma(atr, p.atr_base_len) * p.atr_min_mult

    trend = ema(c, p.mra_smooth)
    approx = sma(trend, p.approx_len)

    r_fino = c.diff().abs()
    r_grueso = (trend - trend.shift(max(1, p.mra_smooth // 2))).abs()
    ratio = sma(r_grueso ** 2, p.look_energy) / sma(r_fino ** 2, p.look_energy)
    dominante = ratio >= p.dom_umbral

    er = (c - c.shift(p.look_energy)).abs() / c.diff().abs().rolling(p.look_energy).sum()
    er_pct = percentrank(er, p.er_rank_len)
    er_ok = er_pct >= p.er_min_pct

    e1, e2, e3 = ema(c, p.wav_s1), ema(c, p.wav_s2), ema(c, p.wav_s3)
    en1 = sma((c - e1) ** 2, p.wav_len)
    en2 = sma((e1 - e2) ** 2, p.wav_len)
    en3 = sma((e2 - e3) ** 2, p.wav_len)
    tot = (en1 + en2 + en3).replace(0, np.nan)
    wave_noise = (en1 / tot) >= p.wav_noise_max
    wave_trend = (en3 / tot) >= p.wav_trend_min

    h8 = trend - trend.shift(p.mra_smooth)
    body = (c - o).abs()
    rng = h - l
    close_loc = ((c - l) / rng.replace(0, np.nan)).fillna(0.5)
    body_ok = body >= atr * p.min_body_atr

    cross_up = (trend > approx) & (trend.shift(1) <= approx.shift(1))
    cross_dn = (trend < approx) & (trend.shift(1) >= approx.shift(1))
    k = p.cross_age + 1
    cross_up_rec = cross_up.astype(int).rolling(k, min_periods=1).max().astype(bool) & (trend > approx)
    cross_dn_rec = cross_dn.astype(int).rolling(k, min_periods=1).max().astype(bool) & (trend < approx)
    no_chase_apx = ((trend - approx) / atr).abs() <= p.max_dist_approx

    rvol = v / sma(v, p.vol_len).shift(1)
    vol_ok = (rvol >= p.vol_mult) & (rvol <= p.vol_max)

    day = t.dt.floor("D")
    hlc3 = (h + l + c) / 3.0
    vwap = (hlc3 * v).groupby(day).cumsum() / v.groupby(day).cumsum().replace(0, np.nan)

    hx = _htf(d, t, p, base_min)
    htf_ema = hx["ema"]
    htf_bull = (hx["close"] > htf_ema) & (hx["slope"] > 0)
    htf_bear = (hx["close"] < htf_ema) & (hx["slope"] < 0)
    dist = (c - htf_ema) / atr
    dist_ok_l = (dist >= -p.touch_band) & (dist <= p.pb_max)
    dist_ok_s = (dist <= p.touch_band) & (dist >= -p.pb_max)

    lo_ref = l.shift(1).rolling(p.sweep_look).min()
    hi_ref = h.shift(1).rolling(p.sweep_look).max()
    sw_l = ((l < lo_ref) & (c > lo_ref)).astype(int).rolling(p.sweep_age + 1, min_periods=1).max().astype(bool)
    sw_s = ((h > hi_ref) & (c < hi_ref)).astype(int).rolling(p.sweep_age + 1, min_periods=1).max().astype(bool)
    fvg_l = (l > h.shift(2)).astype(int).rolling(p.fvg_max_age, min_periods=1).max().astype(bool)
    fvg_s = (h < l.shift(2)).astype(int).rolling(p.fvg_max_age, min_periods=1).max().astype(bool)

    hour = t.dt.hour
    lon, ny, ov, kz = sessions(hour)
    mode = p.session_mode.upper()
    if mode == "TODO":
        in_sess = pd.Series(True, index=d.index)
    elif mode == "OVERLAP":
        in_sess = ov
    elif mode == "KILLZONES":
        in_sess = kz
    else:
        in_sess = lon | ny

    sl_dist = atr * p.sl_atr
    coste_r = (p.cost_pct / 100.0 * c) / sl_dist
    cost_ok = coste_r <= p.max_cost_r

    vwap_l = (c > vwap) if p.use_vwap else pd.Series(True, index=d.index)
    vwap_s = (c < vwap) if p.use_vwap else pd.Series(True, index=d.index)

    score_l = sw_l.astype(int) + fvg_l.astype(int) + kz.astype(int) + dominante.astype(int) + wave_trend.astype(int)
    score_s = sw_s.astype(int) + fvg_s.astype(int) + kz.astype(int) + dominante.astype(int) + wave_trend.astype(int)

    common = er_ok & ~wave_noise & body_ok & vol_ok & atr_exp & in_sess & no_chase_apx & cost_ok
    long_sig = common & htf_bull & dist_ok_l & cross_up_rec & (h8 > 0) & (c > o) \
        & (close_loc >= p.min_close_loc) & vwap_l & (score_l >= p.score_min)
    short_sig = common & htf_bear & dist_ok_s & cross_dn_rec & (h8 < 0) & (c < o) \
        & (close_loc <= 1.0 - p.min_close_loc) & vwap_s & (score_s >= p.score_min)
    if not p.allow_long:
        long_sig[:] = False
    if not p.allow_short:
        short_sig[:] = False

    sig = np.where(long_sig, 1, np.where(short_sig, -1, 0))
    return pd.DataFrame({
        "time": d.time, "open": o, "high": h, "low": l, "close": c,
        "atr": atr, "atr_pct": atr_pct, "sig": sig,
        "score": np.where(sig == 1, score_l, np.where(sig == -1, score_s, np.maximum(score_l, score_s))),
        "dist": dist, "er_pct": er_pct, "rvol": rvol, "coste_r": coste_r,
        "htf_bull": htf_bull, "htf_bear": htf_bear,
        # diagnóstico por filtro
        "f_er": er_ok, "f_noise_ok": ~wave_noise, "f_body": body_ok, "f_vol": vol_ok, "f_atrexp": atr_exp,
        "f_sess": in_sess, "f_apx": no_chase_apx, "f_cost": cost_ok, "f_distL": dist_ok_l, "f_distS": dist_ok_s,
        "f_crossL": cross_up_rec, "f_crossS": cross_dn_rec, "f_vwapL": vwap_l, "f_vwapS": vwap_s,
        "scoreL": score_l, "scoreS": score_s,
    })


# ─────────────────────────── régimen BTC ───────────────────────────
def regime_series(btc: pd.DataFrame, p: Params, base_min: int = 5) -> pd.DataFrame:
    """time → reg (+1 alcista, -1 bajista, 0 neutro) de BTC en velas 1h cerradas."""
    d = btc[["time", "close"]].reset_index(drop=True).copy()
    d["time"] = d["time"].astype("int64")
    d["close"] = d["close"].astype(float)
    t = pd.Series(pd.to_datetime(d.time, unit="ms", utc=True))
    hx = htf_trend(d, t, p.btc_tf_min, p.btc_ema_len, p.btc_slope_bars, base_min)
    reg = np.where((hx["close"] > hx["ema"]) & (hx["slope"] > 0), 1,
                   np.where((hx["close"] < hx["ema"]) & (hx["slope"] < 0), -1, 0))
    return pd.DataFrame({"time": d.time, "reg": reg})


def apply_regime(feat: pd.DataFrame, reg: pd.DataFrame | None, p: Params) -> pd.DataFrame:
    """Anula señales contra el régimen BTC. Sin datos de BTC y filtro activo → sin señales."""
    f = feat.copy()
    if not p.btc_filter:
        f["btc_reg"] = 0
        return f
    if reg is None or reg.empty:
        f["btc_reg"] = 0
        f["sig"] = 0
        return f
    m = pd.merge_asof(f[["time"]].astype("int64"), reg.astype({"time": "int64"}).sort_values("time"),
                      on="time", direction="backward")
    r = m["reg"].fillna(0).astype(int).to_numpy()
    s = f["sig"].to_numpy()
    f["btc_reg"] = r
    f["sig"] = np.where(((s == 1) & (r == 1)) | ((s == -1) & (r == -1)), s, 0)
    return f


# ─────────────────────────── gestión de posición ───────────────────────────
@dataclass
class Pos:
    symbol: str
    side: int
    entry: float
    sl: float
    tp: float
    r: float
    atr: float
    opened_ms: int
    last_ms: int
    bars: int = 0
    extreme: float = 0.0
    sl_state: str = "inicial"      # inicial | be | trail
    mfe: float = 0.0
    mae: float = 0.0
    score: int = 0
    dist: float = 0.0
    er_pct: float = 0.0
    rvol: float = 0.0
    atr_pct: float = 0.0
    coste_r: float = 0.0
    qty: float = 0.0
    risk_usdt: float = 0.0
    pos_side: str = ""
    sl_order: str = ""
    tp_order: str = ""
    sl_live: float = 0.0
    signal_px: float = 0.0
    btc_reg: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Pos":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def new_pos(symbol: str, row, p: Params, entry: float | None = None) -> Pos:
    side = int(row.sig)
    e = float(entry if entry is not None else row.close)
    r = float(row.atr) * p.sl_atr
    return Pos(
        symbol=symbol, side=side, entry=e, sl=e - side * r, tp=e + side * float(row.atr) * p.tp_atr,
        r=r, atr=float(row.atr), opened_ms=int(row.time), last_ms=int(row.time), extreme=e,
        score=int(row.score), dist=float(row.dist), er_pct=float(row.er_pct), rvol=float(row.rvol),
        atr_pct=float(row.atr_pct), coste_r=p.cost_pct / 100.0 * e / r,
        signal_px=float(row.close), btc_reg=int(getattr(row, "btc_reg", 0) or 0),
    )


def track(pos: Pos, bar) -> None:
    if pos.side == 1:
        pos.extreme = max(pos.extreme, bar.high)
        pos.mae = max(pos.mae, (pos.entry - bar.low) / pos.r)
    else:
        pos.extreme = min(pos.extreme, bar.low)
        pos.mae = max(pos.mae, (bar.high - pos.entry) / pos.r)
    pos.mfe = max(pos.mfe, pos.side * (pos.extreme - pos.entry) / pos.r)


def check_exit(pos: Pos, bar):
    """Stop tiene prioridad si stop y objetivo caen en la misma vela (conservador)."""
    reason_stop = {"inicial": "stop", "be": "be", "trail": "trail"}[pos.sl_state]
    if pos.side == 1:
        if bar.low <= pos.sl:
            return min(pos.sl, bar.open), reason_stop
        if bar.high >= pos.tp:
            return max(pos.tp, bar.open), "objetivo"
    else:
        if bar.high >= pos.sl:
            return max(pos.sl, bar.open), reason_stop
        if bar.low <= pos.tp:
            return min(pos.tp, bar.open), "objetivo"
    return None


def update_levels(pos: Pos, bar, atr_now: float, p: Params) -> bool:
    """BE + chandelier. Solo mueve el stop a favor y nunca por encima/debajo del cierre."""
    fav_r = pos.side * (pos.extreme - pos.entry) / pos.r
    new_sl, state = pos.sl, pos.sl_state
    better = max if pos.side == 1 else min
    if state == "inicial" and fav_r >= p.be_r:
        be = pos.entry * (1 + pos.side * p.cost_pct / 100.0)
        if better(new_sl, be) != new_sl:
            new_sl, state = be, "be"
    if fav_r >= p.trail_start_r and atr_now > 0:
        ch = pos.extreme - pos.side * p.trail_atr * atr_now
        if better(new_sl, ch) != new_sl:
            new_sl, state = ch, "trail"
    if pos.side == 1 and new_sl >= bar.close:
        return False
    if pos.side == -1 and new_sl <= bar.close:
        return False
    if new_sl != pos.sl:
        pos.sl, pos.sl_state = new_sl, state
        return True
    return False


def result(pos: Pos, exit_price: float) -> dict:
    r_bruto = pos.side * (exit_price - pos.entry) / pos.r
    return {"r_bruto": r_bruto, "coste_r": pos.coste_r, "r_neto": r_bruto - pos.coste_r}


def backtest_symbol(feat: pd.DataFrame, p: Params, symbol: str) -> list[dict]:
    """Entrada al cierre de la vela de señal, gestión vela a vela (misma lógica que el bot)."""
    trades, pos, last_exit_i = [], None, -10 ** 9
    rows = list(feat.itertuples(index=False))
    for i, bar in enumerate(rows):
        if pos is not None:
            track(pos, bar)
            ex = check_exit(pos, bar)
            pos.bars += 1
            if ex is None:
                update_levels(pos, bar, bar.atr, p)
                if pos.bars >= p.max_bars:
                    ex = (bar.close, "tiempo")
            if ex is not None:
                res = result(pos, ex[0])
                trades.append({
                    "symbol": symbol, "lado": "LONG" if pos.side == 1 else "SHORT",
                    "abierta_ms": pos.opened_ms, "cerrada_ms": int(bar.time), "entrada": pos.entry,
                    "salida": ex[0], "motivo": ex[1], **res, "barras": pos.bars, "score": pos.score,
                    "dist_htf": pos.dist, "er_pct": pos.er_pct, "rvol": pos.rvol, "atr_pct": pos.atr_pct,
                    "mfe": pos.mfe, "mae": pos.mae,
                })
                pos, last_exit_i = None, i
            continue
        if bar.sig != 0 and i - last_exit_i >= p.cooldown_bars and np.isfinite(bar.atr) and bar.atr > 0:
            pos = new_pos(symbol, bar, p)
    return trades
