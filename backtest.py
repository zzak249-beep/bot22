"""
╔══════════════════════════════════════════════════════════════╗
║           BACKTEST v2 — Motor propio SATY ELITE             ║
║  Walk-forward · Lateral detection · Brain learning sim      ║
║  Uso: python backtest.py [--symbol BTC/USDT:USDT] [--tf 5m]║
║       [--days 90] [--plot] [--lateral]                      ║
╚══════════════════════════════════════════════════════════════╝
"""
import os, sys, json, time, logging, argparse
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict

import ccxt
import pandas as pd
import numpy as np

# ── Importar módulos del bot ──────────────────────────────
try:
    from bingx_bot2_saty_v15 import (
        fetch_df, atr, rsi, adx, bb, ema, sma, sqz_mom, pct_b,
        ph, pl, htf_bias,
        mod_conf_pro, mod_bollinger_hunter, mod_smc,
        mod_powertrend, mod_bbpct, mod_rsi_plus,
        consensus,
        TP1_M, TP2_M, TP3_M, SL_M, MIN_SCORE, MIN_MODULES,
        ADX_MIN, MIN_RR, FAST, SLOW, BB_LEN, ATR_LEN
    )
    BOT_MODULE = "bingx_bot2_saty_v15"
except ImportError:
    print("⚠️  No se encontró bingx_bot2_saty_v15.py — importando indicadores locales")
    # Fallback: definir indicadores básicos si el bot no está disponible
    def ema(s,n):   return s.ewm(span=n,adjust=False).mean()
    def sma(s,n):   return s.rolling(n).mean()
    def atr(df,n=14):
        h,l,c=df["high"],df["low"],df["close"]
        tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        return tr.ewm(span=n,adjust=False).mean()
    def rsi(s,n=14):
        d=s.diff(); g=d.clip(lower=0).ewm(span=n,adjust=False).mean()
        lo=(-d.clip(upper=0)).ewm(span=n,adjust=False).mean()
        return 100-(100/(1+g/lo.replace(0,np.nan)))
    def adx(df,n=14):
        h,l=df["high"],df["low"]; up,dn=h.diff(),-l.diff()
        pdm=up.where((up>dn)&(up>0),0.0); mdm=dn.where((dn>up)&(dn>0),0.0)
        a=atr(df,n)
        dip=100*pdm.ewm(span=n,adjust=False).mean()/a
        dim=100*mdm.ewm(span=n,adjust=False).mean()/a
        dx=100*(dip-dim).abs()/(dip+dim).replace(0,np.nan)
        return dip,dim,dx.ewm(span=n,adjust=False).mean()
    TP1_M=1.2; TP2_M=2.5; TP3_M=4.5; SL_M=1.0
    MIN_SCORE=5; MIN_MODULES=2; ADX_MIN=20; MIN_RR=1.5
    FAST=9; SLOW=21; BB_LEN=20; ATR_LEN=14
    BOT_MODULE = "fallback"

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backtest")

# ══════════════════════════════════════════════════════════
# DETECCIÓN DE MERCADO LATERAL
# ══════════════════════════════════════════════════════════
def detect_lateral(df: pd.DataFrame, lookback: int = 50) -> Tuple[bool, float, float, float]:
    """
    Detecta si el mercado está lateral.
    Returns: (is_lateral, range_high, range_low, range_pct)
    
    Criterios:
    - ADX < 20 (sin tendencia)
    - Precio oscila dentro de un rango definido (BB Width estrecho)
    - Sin nuevos máximos/mínimos significativos
    """
    if len(df) < lookback + 10:
        return False, 0, 0, 0

    i = len(df) - 2
    window = df.iloc[max(0, i-lookback):i+1]

    # ADX bajo → sin tendencia
    _, _, adx_s = adx(df.iloc[max(0, i-lookback-20):i+1])
    adx_val = float(adx_s.iloc[-2]) if not adx_s.empty else 30.0

    # Bollinger Width → volatilidad relativa
    mid, upper, lower = bb(df["close"])
    bbw = float(((upper - lower) / mid).iloc[i])

    # Rango del precio en la ventana
    rng_hi = float(window["high"].max())
    rng_lo = float(window["low"].min())
    rng_pct = (rng_hi - rng_lo) / rng_lo * 100 if rng_lo > 0 else 999

    # Criterio lateral: ADX<20, BBW<0.04, rango<8%
    is_lateral = adx_val < 20 and bbw < 0.04 and rng_pct < 8.0

    return is_lateral, rng_hi, rng_lo, rng_pct

def lateral_signal(df: pd.DataFrame, lookback: int = 50) -> Optional[dict]:
    """
    Genera señal para mercado lateral:
    - LONG en soporte del rango + RSI sobreventa
    - SHORT en resistencia del rango + RSI sobrecompra
    """
    is_lat, rng_hi, rng_lo, rng_pct = detect_lateral(df, lookback)
    if not is_lat or rng_pct < 1.0:
        return None

    i = len(df) - 2
    price = float(df["close"].iloc[i])
    rs = rsi(df["close"])
    rv = float(rs.iloc[i])
    atr_v = float(atr(df).iloc[i])

    rng_mid = (rng_hi + rng_lo) / 2
    proximity = 0.015  # 1.5% del borde del rango

    # LONG: cerca del soporte + RSI < 40
    if price <= rng_lo * (1 + proximity) and rv < 40:
        return {
            "direction": "long",
            "score": 6,
            "modules": "LATERAL",
            "signals": f"range_support RSI:{rv:.0f} range:{rng_pct:.1f}%",
            "tp1": rng_mid,
            "tp2": rng_hi * 0.98,
            "tp3": rng_hi * 0.995,
            "sl": rng_lo * (1 - proximity * 2),
            "rng_hi": rng_hi,
            "rng_lo": rng_lo,
            "rng_pct": rng_pct,
            "atr": atr_v,
        }

    # SHORT: cerca de la resistencia + RSI > 60
    if price >= rng_hi * (1 - proximity) and rv > 60:
        return {
            "direction": "short",
            "score": 6,
            "modules": "LATERAL",
            "signals": f"range_resistance RSI:{rv:.0f} range:{rng_pct:.1f}%",
            "tp1": rng_mid,
            "tp2": rng_lo * 1.02,
            "tp3": rng_lo * 1.005,
            "sl": rng_hi * (1 + proximity * 2),
            "rng_hi": rng_hi,
            "rng_lo": rng_lo,
            "rng_pct": rng_pct,
            "atr": atr_v,
        }

    return None

# ══════════════════════════════════════════════════════════
# DATACLASSES BACKTEST
# ══════════════════════════════════════════════════════════
@dataclass
class BtTrade:
    idx: int
    symbol: str
    side: str
    entry: float
    tp1: float
    tp2: float
    tp3: float
    sl: float
    score: int
    modules: str
    rsi: float
    adx: float
    atr: float
    rr: float
    market_mode: str   # "trend" o "lateral"
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    bars_held: int = 0
    win: bool = False

@dataclass
class BtResult:
    symbol: str
    tf: str
    days: int
    initial_capital: float
    final_capital: float
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    max_dd: float = 0.0
    max_dd_pct: float = 0.0
    sharpe: float = 0.0
    trades: List[BtTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    lateral_trades: int = 0
    trend_trades: int = 0
    lateral_wr: float = 0.0
    trend_wr: float = 0.0

    def wr(self):
        return (self.wins / self.total_trades * 100) if self.total_trades else 0.0

    def profit_factor(self):
        return (self.gross_profit / self.gross_loss) if self.gross_loss else 0.0

    def net_pnl(self):
        return self.final_capital - self.initial_capital

# ══════════════════════════════════════════════════════════
# MOTOR DE BACKTEST
# ══════════════════════════════════════════════════════════
class Backtester:
    def __init__(self,
                 api_key: str = "",
                 api_secret: str = "",
                 initial_capital: float = 100.0,
                 risk_pct: float = 2.0,
                 leverage: float = 10.0,
                 commission_pct: float = 0.05,
                 include_lateral: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.risk_pct = risk_pct
        self.leverage = leverage
        self.commission = commission_pct / 100
        self.include_lateral = include_lateral
        self.ex = None

    def _build_ex(self):
        self.ex = ccxt.bingx({
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "options": {"defaultType": "swap"},
            "enableRateLimit": True,
        })
        self.ex.load_markets()

    def _fetch_history(self, symbol: str, tf: str, days: int) -> pd.DataFrame:
        """Descarga histórico completo usando paginación."""
        log.info(f"Descargando {days} días de {symbol} [{tf}]...")
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        all_ohlcv = []
        while True:
            try:
                batch = self.ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
                if not batch: break
                all_ohlcv.extend(batch)
                since = batch[-1][0] + 1
                if len(batch) < 1000: break
                time.sleep(0.3)
            except Exception as e:
                log.warning(f"fetch error: {e}"); break
        if not all_ohlcv:
            raise ValueError(f"Sin datos para {symbol}")
        df = pd.DataFrame(all_ohlcv, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True); df = df.astype(float)
        log.info(f"  → {len(df)} velas descargadas")
        return df

    def _signal_at(self, df: pd.DataFrame, df_htf1: pd.DataFrame,
                   df_htf2: pd.DataFrame, i: int) -> Optional[dict]:
        """Genera señal en la vela i usando los 6 módulos."""
        if i < 250: return None
        sub  = df.iloc[:i+1]
        sub1 = df_htf1.iloc[:min(len(df_htf1), i//3+1)]
        sub2 = df_htf2.iloc[:min(len(df_htf2), i//12+1)]
        if len(sub) < 150 or len(sub1) < 50 or len(sub2) < 30:
            return None
        try:
            htf1b, htf1bear = htf_bias(sub1)
            htf2b, htf2bear = htf_bias(sub2)
            m1l, m1s = mod_conf_pro(sub, htf1b, htf1bear)
            m2l, m2s = mod_bollinger_hunter(sub, htf2b, htf2bear)
            m3l, m3s = mod_smc(sub, sub1)
            m4l, m4s = mod_powertrend(sub, htf1b, htf1bear)
            m5l, m5s = mod_bbpct(sub, htf1b, htf1bear)
            m6l, m6s = mod_rsi_plus(sub, htf1b, htf1bear)
            d, sc, mods, sigs = consensus(m1l, m1s, m2l, m2s, m3l, m3s,
                                           m4l, m4s, m5l, m5s, m6l, m6s)
            if d is None: return None
            at_v = float(atr(sub).iloc[-1])
            price = float(sub["close"].iloc[-1])
            sl_d = at_v * SL_M; tp_d = at_v * TP3_M
            rr = tp_d / max(sl_d, 1e-9)
            if rr < MIN_RR: return None
            rs = rsi(sub["close"]); ax = adx(sub)[2]
            return {"direction": d, "score": sc, "modules": mods,
                    "signals": sigs, "atr": at_v, "price": price,
                    "rsi": float(rs.iloc[-1]), "adx": float(ax.iloc[-1]),
                    "rr": round(rr, 2)}
        except Exception as e:
            log.debug(f"signal_at {i}: {e}")
            return None

    def _simulate_trade(self, df: pd.DataFrame, entry_i: int,
                        direction: str, entry: float, tp1: float,
                        tp2: float, tp3: float, sl: float) -> Tuple[float, str, int]:
        """
        Simula el trade barra a barra desde entry_i.
        Returns: (exit_price, reason, bars_held)
        """
        max_bars = 200
        tp1_hit = False
        for j in range(entry_i + 1, min(entry_i + max_bars, len(df))):
            h = float(df["high"].iloc[j])
            l = float(df["low"].iloc[j])
            c = float(df["close"].iloc[j])

            if direction == "long":
                if l <= sl:                 return sl,  "SL",  j-entry_i
                if not tp1_hit and h >= tp1:
                    tp1_hit = True; sl = entry  # break-even
                if tp1_hit and h >= tp2:
                    sl = tp1                    # lock TP1
                if h >= tp3:                return tp3, "TP3", j-entry_i
                if tp1_hit and l <= sl:     return sl,  "BE/TP1", j-entry_i
            else:
                if h >= sl:                 return sl,  "SL",  j-entry_i
                if not tp1_hit and l <= tp1:
                    tp1_hit = True; sl = entry
                if tp1_hit and l <= tp2:
                    sl = tp1
                if l <= tp3:                return tp3, "TP3", j-entry_i
                if tp1_hit and h >= sl:     return sl,  "BE/TP1", j-entry_i

        # Timeout
        return float(df["close"].iloc[min(entry_i + max_bars - 1, len(df)-1)]), "TIMEOUT", max_bars

    def run(self, symbol: str, tf: str = "5m", days: int = 90) -> BtResult:
        """Ejecuta el backtest completo con walk-forward."""
        if self.ex is None:
            self._build_ex()

        # Calcular HTF equivalentes en días
        days_needed = days + 10
        df      = self._fetch_history(symbol, tf, days_needed)
        df_htf1 = self._fetch_history(symbol, "15m", days_needed)
        df_htf2 = self._fetch_history(symbol, "1h", days_needed)

        result = BtResult(
            symbol=symbol, tf=tf, days=days,
            initial_capital=self.initial_capital,
            final_capital=self.initial_capital,
        )

        equity = self.initial_capital
        peak_eq = equity
        max_dd = 0.0
        equity_curve = [equity]
        trades: List[BtTrade] = []
        in_trade = False
        active: Optional[BtTrade] = None

        log.info(f"Simulando {len(df)} velas...")
        skip_until = 0

        for i in range(250, len(df) - 1):
            if i < skip_until: continue

            # ── Lateral detection ─────────────────────────
            lat_sig = None
            if self.include_lateral:
                lat_sig = lateral_signal(df.iloc[:i+1])

            # ── Trend signal ──────────────────────────────
            sig = self._signal_at(df, df_htf1, df_htf2, i)

            # Priorizar señal de tendencia sobre lateral
            chosen_sig = sig if sig else lat_sig
            if chosen_sig is None: continue

            d = chosen_sig["direction"]
            price = float(df["close"].iloc[i])
            at_v  = chosen_sig.get("atr", float(atr(df.iloc[:i+1]).iloc[-1]))

            # Calcular TP/SL
            if "tp1" in chosen_sig:  # señal lateral ya tiene TP/SL calculados
                tp1 = chosen_sig["tp1"]; tp2 = chosen_sig["tp2"]
                tp3 = chosen_sig["tp3"]; sl  = chosen_sig["sl"]
            else:
                if d == "long":
                    sl  = price - at_v * SL_M
                    tp1 = price + at_v * TP1_M
                    tp2 = price + at_v * TP2_M
                    tp3 = price + at_v * TP3_M
                else:
                    sl  = price + at_v * SL_M
                    tp1 = price - at_v * TP1_M
                    tp2 = price - at_v * TP2_M
                    tp3 = price - at_v * TP3_M

            rr = abs(tp3 - price) / max(abs(sl - price), 1e-9)
            if rr < MIN_RR: continue

            # Position sizing (riesgo fijo %)
            risk_usd  = equity * (self.risk_pct / 100)
            sl_dist   = abs(price - sl)
            contracts = (risk_usd * self.leverage) / max(sl_dist * price, 1e-6)
            notional  = contracts * price

            # Simular
            exit_p, reason, bars = self._simulate_trade(df, i, d, price, tp1, tp2, tp3, sl)

            # PnL
            raw_pnl = (exit_p - price if d == "long" else price - exit_p) * contracts
            commission = notional * self.commission * 2  # entrada + salida
            net_pnl = raw_pnl - commission
            pnl_pct = net_pnl / equity * 100

            equity += net_pnl
            equity_curve.append(round(equity, 4))
            peak_eq = max(peak_eq, equity)
            dd = (peak_eq - equity) / peak_eq * 100
            max_dd = max(max_dd, dd)

            win = net_pnl > 0
            rs_v = chosen_sig.get("rsi", 50.0)
            ax_v = chosen_sig.get("adx", 20.0)
            market_mode = "lateral" if lat_sig and not sig else "trend"

            t = BtTrade(
                idx=i, symbol=symbol, side=d,
                entry=round(price, 8), tp1=round(tp1, 8),
                tp2=round(tp2, 8), tp3=round(tp3, 8), sl=round(sl, 8),
                score=chosen_sig.get("score", 0),
                modules=chosen_sig.get("modules", ""),
                rsi=round(rs_v, 1), adx=round(ax_v, 1),
                atr=round(at_v, 8), rr=round(rr, 2),
                market_mode=market_mode,
                exit_price=round(exit_p, 8),
                exit_reason=reason,
                pnl_pct=round(pnl_pct, 3),
                bars_held=bars, win=win
            )
            trades.append(t)
            result.total_trades += 1
            if win:
                result.wins += 1; result.gross_profit += net_pnl
            else:
                result.losses += 1; result.gross_loss += abs(net_pnl)

            if market_mode == "lateral": result.lateral_trades += 1
            else:                        result.trend_trades += 1

            skip_until = i + max(1, bars)

            if result.total_trades % 20 == 0:
                log.info(f"  Progreso: {result.total_trades} trades | "
                         f"Equity: ${equity:.2f} | DD: {dd:.1f}%")

        # Estadísticas finales
        result.final_capital = round(equity, 2)
        result.max_dd = round(max_dd, 2)
        result.max_dd_pct = round(max_dd, 2)
        result.trades = trades
        result.equity_curve = equity_curve

        # WR por modo
        lat_t = [t for t in trades if t.market_mode == "lateral"]
        trd_t = [t for t in trades if t.market_mode == "trend"]
        result.lateral_wr = (sum(1 for t in lat_t if t.win) / len(lat_t) * 100) if lat_t else 0
        result.trend_wr   = (sum(1 for t in trd_t if t.win) / len(trd_t) * 100) if trd_t else 0

        # Sharpe (simplificado: PnL medio / std de PnLs)
        pnls = [t.pnl_pct for t in trades]
        if len(pnls) > 2:
            result.sharpe = round(np.mean(pnls) / (np.std(pnls) + 1e-9) * np.sqrt(252), 2)

        return result

# ══════════════════════════════════════════════════════════
# REPORTE
# ══════════════════════════════════════════════════════════
def print_report(r: BtResult):
    sep = "═" * 60
    print(f"\n{sep}")
    print(f"  BACKTEST SATY ELITE v15 — {r.symbol} [{r.tf}] {r.days}d")
    print(sep)
    print(f"  Capital inicial:  ${r.initial_capital:.2f}")
    print(f"  Capital final:    ${r.final_capital:.2f}")
    nk = r.net_pnl()
    print(f"  PnL neto:         ${nk:+.2f} ({nk/r.initial_capital*100:+.1f}%)")
    print(f"  Max Drawdown:     {r.max_dd_pct:.1f}%")
    print(f"  Sharpe ratio:     {r.sharpe:.2f}")
    print(sep)
    print(f"  Total trades:     {r.total_trades}")
    print(f"  Ganadores:        {r.wins} ({r.wr():.1f}%)")
    print(f"  Perdedores:       {r.losses}")
    print(f"  Profit Factor:    {r.profit_factor():.2f}")
    print(sep)
    print(f"  TENDENCIA:        {r.trend_trades} trades | WR: {r.trend_wr:.1f}%")
    print(f"  LATERAL:          {r.lateral_trades} trades | WR: {r.lateral_wr:.1f}%")
    print(sep)

    # Top 5 mejores trades
    if r.trades:
        best = sorted(r.trades, key=lambda x: x.pnl_pct, reverse=True)[:5]
        print(f"\n  🏆 TOP 5 trades:")
        for t in best:
            print(f"    {'🟢' if t.win else '🔴'} {t.side.upper()} [{t.modules}] "
                  f"R/R:{t.rr} → {t.exit_reason} {t.pnl_pct:+.2f}% "
                  f"({t.market_mode})")

        # Top 5 peores
        worst = sorted(r.trades, key=lambda x: x.pnl_pct)[:5]
        print(f"\n  ❌ TOP 5 pérdidas:")
        for t in worst:
            print(f"    {'🟢' if t.win else '🔴'} {t.side.upper()} [{t.modules}] "
                  f"R/R:{t.rr} → {t.exit_reason} {t.pnl_pct:+.2f}% "
                  f"({t.market_mode})")

        # Análisis por módulo
        combos = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0.0})
        for t in r.trades:
            combos[t.modules]["w" if t.win else "l"] += 1
            combos[t.modules]["pnl"] += t.pnl_pct
        print(f"\n  📊 Por combo:")
        for c, s in sorted(combos.items(), key=lambda x: x[1]["pnl"], reverse=True)[:8]:
            tot = s["w"] + s["l"]
            cwr = s["w"] / tot * 100 if tot else 0
            print(f"    {c[:35]:35s} {tot:3d}T WR:{cwr:.0f}% PnL:{s['pnl']:+.1f}%")

    print(f"\n{sep}\n")

def save_report(r: BtResult, path: str = "backtest_report.json"):
    """Guarda reporte en JSON para análisis externo."""
    data = {
        "symbol": r.symbol, "tf": r.tf, "days": r.days,
        "initial_capital": r.initial_capital,
        "final_capital": r.final_capital,
        "net_pnl": r.net_pnl(),
        "net_pnl_pct": round(r.net_pnl() / r.initial_capital * 100, 2),
        "total_trades": r.total_trades,
        "wins": r.wins, "losses": r.losses,
        "win_rate": round(r.wr(), 2),
        "profit_factor": round(r.profit_factor(), 2),
        "max_dd_pct": r.max_dd_pct,
        "sharpe": r.sharpe,
        "trend_trades": r.trend_trades, "trend_wr": round(r.trend_wr, 1),
        "lateral_trades": r.lateral_trades, "lateral_wr": round(r.lateral_wr, 1),
        "equity_curve": r.equity_curve,
        "trades": [
            {"idx": t.idx, "side": t.side, "entry": t.entry,
             "exit": t.exit_price, "reason": t.exit_reason,
             "pnl_pct": t.pnl_pct, "bars": t.bars_held,
             "modules": t.modules, "score": t.score,
             "rsi": t.rsi, "adx": t.adx, "rr": t.rr,
             "market_mode": t.market_mode, "win": t.win}
            for t in r.trades
        ]
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"Reporte guardado en {path}")

def plot_equity(r: BtResult):
    """Grafica la curva de equity (requiere matplotlib)."""
    try:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 1, figsize=(14, 8))

        # Equity curve
        axes[0].plot(r.equity_curve, color="cyan", linewidth=1.5)
        axes[0].axhline(r.initial_capital, color="gray", linestyle="--", alpha=0.5)
        axes[0].fill_between(range(len(r.equity_curve)),
                             r.initial_capital, r.equity_curve,
                             where=[e >= r.initial_capital for e in r.equity_curve],
                             alpha=0.3, color="green")
        axes[0].fill_between(range(len(r.equity_curve)),
                             r.initial_capital, r.equity_curve,
                             where=[e < r.initial_capital for e in r.equity_curve],
                             alpha=0.3, color="red")
        axes[0].set_title(f"SATY v15 Backtest — {r.symbol} [{r.tf}] {r.days}d | "
                          f"WR:{r.wr():.1f}% PF:{r.profit_factor():.2f} DD:{r.max_dd_pct:.1f}%")
        axes[0].set_ylabel("Capital ($)")
        axes[0].grid(alpha=0.3)

        # PnL por trade
        colors = ["green" if t.win else "red" for t in r.trades]
        axes[1].bar(range(len(r.trades)),
                    [t.pnl_pct for t in r.trades], color=colors, alpha=0.7)
        axes[1].axhline(0, color="white", linewidth=0.5)
        axes[1].set_title(f"PnL por trade (verde=tendencia, rojo=lateral)")
        axes[1].set_ylabel("PnL %")
        axes[1].grid(alpha=0.3)

        plt.tight_layout()
        fname = f"backtest_{r.symbol.replace('/', '_').replace(':', '_')}_{r.tf}.png"
        plt.savefig(fname, dpi=150, bbox_inches="tight",
                    facecolor="#1a1a2e", edgecolor="none")
        log.info(f"Gráfico guardado: {fname}")
        plt.show()
    except ImportError:
        log.warning("matplotlib no disponible — instala con: pip install matplotlib")

# ══════════════════════════════════════════════════════════
# WALK-FORWARD VALIDATION
# ══════════════════════════════════════════════════════════
def walk_forward(bt: Backtester, symbol: str, tf: str,
                 total_days: int = 180, window_days: int = 30) -> List[BtResult]:
    """
    Walk-forward: entrena en 2/3, valida en 1/3 de cada ventana.
    Muestra si los parámetros son robustos fuera de muestra.
    """
    results = []
    start = 0
    while start + window_days <= total_days:
        r = bt.run(symbol, tf, window_days)
        results.append(r)
        log.info(f"  WF ventana {start}-{start+window_days}d: "
                 f"WR:{r.wr():.1f}% PF:{r.profit_factor():.2f} PnL:${r.net_pnl():+.2f}")
        start += window_days // 2  # 50% overlap

    # Resumen walk-forward
    print("\n" + "═"*60)
    print("  WALK-FORWARD VALIDATION")
    print("═"*60)
    for i, r in enumerate(results):
        print(f"  Ventana {i+1}: WR:{r.wr():.1f}% PF:{r.profit_factor():.2f} "
              f"DD:{r.max_dd_pct:.1f}% PnL:${r.net_pnl():+.2f}")
    avg_wr = sum(r.wr() for r in results) / len(results) if results else 0
    avg_pf = sum(r.profit_factor() for r in results) / len(results) if results else 0
    print(f"  PROMEDIO: WR:{avg_wr:.1f}% PF:{avg_pf:.2f}")
    print("═"*60)
    return results

# ══════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="SATY ELITE v15 Backtester")
    parser.add_argument("--symbol",   default="BTC/USDT:USDT", help="Par a testear")
    parser.add_argument("--tf",       default="5m",             help="Timeframe")
    parser.add_argument("--days",     type=int, default=90,     help="Días históricos")
    parser.add_argument("--capital",  type=float, default=100,  help="Capital inicial")
    parser.add_argument("--risk",     type=float, default=2.0,  help="Riesgo por trade %%")
    parser.add_argument("--leverage", type=float, default=10.0, help="Apalancamiento")
    parser.add_argument("--plot",     action="store_true",      help="Mostrar gráfico")
    parser.add_argument("--wf",       action="store_true",      help="Walk-forward")
    parser.add_argument("--no-lateral", action="store_true",    help="Sin mercado lateral")
    parser.add_argument("--multi",    action="store_true",      help="Testear múltiples pares")
    args = parser.parse_args()

    api_key    = os.environ.get("BINGX_API_KEY","").strip().strip('"').strip("'")
    api_secret = os.environ.get("BINGX_API_SECRET","").strip().strip('"').strip("'")

    bt = Backtester(
        api_key=api_key, api_secret=api_secret,
        initial_capital=args.capital,
        risk_pct=args.risk, leverage=args.leverage,
        include_lateral=not args.no_lateral
    )
    bt._build_ex()

    if args.multi:
        # Testear los top 10 pares por volumen
        symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
                   "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
                   "ADA/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT", "DOT/USDT:USDT"]
        all_results = []
        for sym in symbols:
            try:
                r = bt.run(sym, args.tf, args.days)
                print_report(r)
                all_results.append(r)
            except Exception as e:
                log.error(f"{sym}: {e}")
        # Resumen multi
        print("\n" + "═"*60)
        print("  RESUMEN MULTI-SÍMBOLO")
        print("═"*60)
        for r in sorted(all_results, key=lambda x: x.net_pnl(), reverse=True):
            print(f"  {r.symbol:25s} WR:{r.wr():.1f}% PF:{r.profit_factor():.2f} "
                  f"PnL:${r.net_pnl():+.2f} DD:{r.max_dd_pct:.1f}%")
    elif args.wf:
        walk_forward(bt, args.symbol, args.tf, args.days * 2, args.days)
    else:
        r = bt.run(args.symbol, args.tf, args.days)
        print_report(r)
        save_report(r)
        if args.plot:
            plot_equity(r)

if __name__ == "__main__":
    main()
