"""Pruebas offline sin red: firma, estrategia sin lookahead, bot PAPER y bot REAL contra un exchange simulado.

  python test_offline.py
"""
from __future__ import annotations

import os
import shutil
import tempfile

import numpy as np
import pandas as pd

BAR = 300_000


def synth(n=3000, seed=1, end_ms=None, p0=100.0):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.0025, n) + 0.0008 * np.sin(np.arange(n) / 150)
    c = p0 * np.exp(np.cumsum(ret))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.001, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.001, n)))
    v = rng.lognormal(10, 0.5, n)
    end_ms = end_ms or 1_790_000_000_000 // BAR * BAR
    t = end_ms - (n - np.arange(n)) * BAR
    return pd.DataFrame(dict(time=t, open=o, high=h, low=l, close=c, volume=v))


LOOSE = dict(SCORE_MIN="0", SESSION_MODE="TODO", VOL_MULT="0", ER_MIN_PCT="0", MAX_COST_R="5",
             ATR_MIN_MULT="0", MIN_BODY_ATR="0.1", MIN_CLOSE_LOC="0.5", PB_MAX="50", TOUCH_BAND="50",
             WAV_NOISE_MAX="1.1", COOLDOWN_BARS="2")


def test_sign():
    from bingx import build_query
    import hashlib, hmac
    qs = build_query({"symbol": "BTC-USDT", "b": 2, "a": 1}, "s3cr3t", ts_ms=1700000000000)
    body, sig = qs.rsplit("&signature=", 1)
    assert body == "a=1&b=2&recvWindow=5000&symbol=BTC-USDT&timestamp=1700000000000", body
    assert sig == hmac.new(b"s3cr3t", body.encode(), hashlib.sha256).hexdigest()
    print("OK firma")


def test_strategy():
    from strategy import Params, backtest_symbol, compute
    df = synth(4000)
    p = Params()
    f = compute(df, p)
    f2 = compute(df.iloc[:3000], p)
    cols = ["sig", "dist", "er_pct", "rvol", "atr", "score"]
    assert np.allclose(f.loc[:2999, cols].fillna(0).to_numpy(float), f2[cols].fillna(0).to_numpy(float)), "lookahead!"
    for k, v in LOOSE.items():
        os.environ[k] = v
    pl = Params.from_env()
    tr = pd.DataFrame(backtest_symbol(compute(df, pl), pl, "SYN"))
    assert len(tr) > 10, len(tr)
    assert set(tr.motivo) <= {"stop", "be", "trail", "objetivo", "tiempo"}
    assert (tr.r_bruto[tr.motivo == "stop"] <= -0.99).all()
    print(f"OK estrategia · sin lookahead · {len(tr)} ops sintéticas · motivos {tr.motivo.value_counts().to_dict()}")


class FakeBX:
    """Exchange mínimo: velas que avanzan con un reloj, mercado al cierre, STOP/TP por high/low."""

    def __init__(self, data: dict, clock: dict, hedge=True):
        self.data, self.clock, self.hedge = data, clock, hedge
        self.pos, self.orders, self.oid, self.log, self.realized = {}, {}, 0, [], {}

    def _vis(self, sym):
        d = self.data[sym]
        return d[d.time + BAR <= self.clock["now"]]

    def contracts(self):
        return {s: {"qprec": 3, "pprec": 4, "min_qty": 0.001, "min_usdt": 2, "status": 1} for s in self.data}

    def tickers(self):
        return [{"symbol": s, "quoteVolume": 5e7} for s in self.data]

    def klines(self, sym, interval="5m", limit=1000, **k):
        return self._vis(sym).tail(limit).reset_index(drop=True)

    def equity(self):
        return 1000.0

    def premium_index(self, sym):
        return {"rate": 0.0001, "next_ms": self.clock["now"] + 3_600_000}

    def income(self, sym, start, end):
        return self.realized.pop(sym, 0.0)

    def hedge_mode(self):
        return self.hedge

    def set_leverage(self, *a):
        return {}

    def positions(self, symbol=None):
        return [{"symbol": s, "positionSide": "LONG" if a > 0 else "SHORT", "positionAmt": a, "avgPrice": px}
                for s, (a, px) in self.pos.items() if a != 0]

    def order(self, **p):
        self.oid += 1
        oid = str(self.oid)
        self.log.append(p)
        sym, qty = p["symbol"], float(p["quantity"])
        if p["type"] == "MARKET":
            px = float(self._vis(sym).close.iloc[-1])
            a, _ = self.pos.get(sym, (0, 0))
            if a == 0:
                self.pos[sym] = (qty if p["side"] == "BUY" else -qty, px)
            else:
                self._close(sym, px)
        else:
            self.orders[oid] = p
        return oid

    def cancel(self, sym, oid):
        self.orders.pop(str(oid), None)

    def cancel_all(self, sym):
        for k in [k for k, o in self.orders.items() if o["symbol"] == sym]:
            self.orders.pop(k)

    def open_orders(self, sym):
        return [{"orderId": k, **o} for k, o in self.orders.items() if o["symbol"] == sym]

    def step(self):
        """Dispara STOP/TP con la última vela cerrada."""
        for k, o in list(self.orders.items()):
            sym = o["symbol"]
            a, _ = self.pos.get(sym, (0, 0))
            if a == 0:
                continue
            bar = self._vis(sym).iloc[-1]
            sp = float(o["stopPrice"])
            long_ = a > 0
            hit = (o["type"] == "STOP_MARKET" and ((long_ and bar.low <= sp) or (not long_ and bar.high >= sp))) or \
                  (o["type"] == "TAKE_PROFIT_MARKET" and ((long_ and bar.high >= sp) or (not long_ and bar.low <= sp)))
            if hit:
                self._close(sym, sp)
                self.orders.pop(k)

    def _close(self, sym, px):
        a, e = self.pos[sym]
        self.realized[sym] = self.realized.get(sym, 0.0) + a * (px - e) - abs(a) * (e + px) * 0.0005
        self.pos[sym] = (0, 0)


def run_bot(live: bool, steps=400):
    import main as M
    tmp = tempfile.mkdtemp()
    for k, v in LOOSE.items():
        os.environ[k] = v
    os.environ.update(DATA_DIR=tmp, DRY_RUN="false" if live else "true", BINGX_API_KEY="k",
                      BINGX_API_SECRET="s", MAX_POSITIONS="3", HEARTBEAT_H="0", TELEGRAM_TOKEN="")
    end = 1_790_000_000_000 // BAR * BAR
    data = {f"S{i}-USDT": synth(3000, seed=10 + i, end_ms=end, p0=10 * (i + 1)) for i in range(4)}
    data["BTC-USDT"] = synth(3000, seed=99, end_ms=end, p0=80000)
    clock = {"now": end - steps * BAR}
    M.now_ms = lambda: clock["now"] + 10_000
    bot = M.Bot()
    bot.bx = FakeBX(data, clock)
    bot.contracts = bot.bx.contracts()
    bot.hedge = bot.bx.hedge_mode()
    bot.refresh_universe(force=True)
    for _ in range(steps):
        clock["now"] += BAR
        bot.bx.step()
        bot.safe_cycle()
    j = pd.read_csv(os.path.join(tmp, "ep5_journal.csv"))
    shutil.rmtree(tmp)
    return bot, j


def test_bot_paper():
    bot, j = run_bot(live=False)
    assert len(j) > 5, len(j)
    assert j.r_neto.notna().all()
    print(f"OK bot PAPER · {len(j)} cerradas · {j.r_neto.sum():+.1f}R · motivos {j.motivo.value_counts().to_dict()} "
          f"· equity {bot.st.paper_equity:.2f}")


def test_bot_live():
    bot, j = run_bot(live=True)
    types = pd.Series([o["type"] for o in bot.bx.log]).value_counts().to_dict()
    assert len(j) > 5 and types.get("STOP_MARKET", 0) >= len(j), types
    # toda posición abierta en el exchange debe tener su stop
    for sym, (a, _) in bot.bx.pos.items():
        if a:
            assert any(o["symbol"] == sym and o["type"] == "STOP_MARKET" for o in bot.bx.orders.values()), sym
    orphans = [o for o in bot.bx.orders.values() if bot.bx.pos.get(o["symbol"], (0, 0))[0] == 0]
    assert not orphans, f"órdenes huérfanas: {orphans}"
    print(f"OK bot REAL simulado · {len(j)} cerradas · órdenes {types} · motivos {j.motivo.value_counts().to_dict()} "
          f"· sin huérfanas")


def test_regime_and_protections():
    """Filtro BTC, pausa por drawdown, comandos Telegram, exposición y funding."""
    import main as M
    from strategy import Params, apply_regime, compute, regime_series
    p = Params()
    btc = synth(3000, seed=99, p0=80000)
    reg = regime_series(btc, p)
    assert set(reg.reg.unique()) <= {-1, 0, 1} and (reg.reg != 0).any()
    for k, v in LOOSE.items():
        os.environ[k] = v
    pl = Params.from_env()
    f = compute(synth(3000, seed=3), pl)
    g = apply_regime(f, reg, pl)
    bad = ((g.sig == 1) & (g.btc_reg != 1)) | ((g.sig == -1) & (g.btc_reg != -1))
    assert not bad.any() and (g.sig != 0).sum() <= (f.sig != 0).sum()
    assert (apply_regime(f, None, pl).sig == 0).all(), "sin BTC y filtro ON debe bloquear"

    tmp = tempfile.mkdtemp()
    os.environ.update(DATA_DIR=tmp, DRY_RUN="true", MAX_DD_R="3", HEARTBEAT_H="0", TELEGRAM_TOKEN="")
    bot = M.Bot()
    sent, queue = [], []
    bot.tg.send = lambda t: sent.append(t)
    bot.tg.poll = lambda: [queue.pop(0)] if queue else []
    # drawdown: 3 pérdidas de -1.1R → pausa
    from strategy import Pos
    for i in range(3):
        pos = Pos(symbol=f"X{i}-USDT", side=1, entry=100, sl=99, tp=102, r=1, atr=1, opened_ms=0, last_ms=0,
                  risk_usdt=1, signal_px=100)
        bot.st.positions[pos.symbol] = pos
        bot.close_record(pos, 99, "stop", 1)
    assert bot.st.paused and "drawdown" in bot.st.pause_reason, bot.st.pause_reason
    queue += ["/estado", "/reanudar", "/pausa", "/ayuda", "/xyz"]
    for _ in range(5):
        bot.handle_commands()
    assert bot.st.paused and bot.st.pause_reason == "manual" and bot.st.peak_r == bot.st.cum_r
    assert any("total" in t for t in sent) and any("desconocido" in t for t in sent)
    # exposición
    bot.st.positions = {"A-USDT": Pos(symbol="A-USDT", side=1, entry=100, sl=99, tp=102, r=1, atr=1,
                                      opened_ms=0, last_ms=0, qty=29.5)}
    assert abs(bot.room_notional(1000) - 50) < 1e-6
    # funding en contra a 5 min → no abre; a favor → abre
    class FB:
        def __init__(s, rate): s.rate = rate
        def premium_index(s, sym): return {"rate": s.rate, "next_ms": M.now_ms() + 5 * 60_000}
    bot.bx = FB(0.0005)
    assert bot.funding_ok("A-USDT", 1) is False and bot.funding_ok("A-USDT", -1) is True
    # /cerrar_todo en paper
    queue.append("/cerrar_todo")
    bot.st.paused = False
    bot.handle_commands()
    assert not bot.st.positions and bot.st.paused
    shutil.rmtree(tmp)
    for k in ("MAX_DD_R",):
        os.environ.pop(k, None)
    print("OK régimen BTC · pausa por DD · comandos · exposición · funding · cerrar_todo")


def test_walkforward():
    import sys
    import walkforward as W
    end = 1_790_000_000_000 // BAR * BAR
    data = {f"S{i}-USDT": synth(3000, seed=20 + i, end_ms=end, p0=5 * (i + 1)) for i in range(3)}
    data["BTC-USDT"] = synth(3000, seed=99, end_ms=end, p0=80000)
    W.load_data = lambda syms, days, cache: data
    W.pick_symbols = lambda *a: list(data)
    for k, v in LOOSE.items():
        os.environ[k] = v
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)
    sys.argv = ["walkforward.py", "--min-trades", "5"]
    try:
        W.main()
        r = pd.read_csv("wf_results.csv")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp)
    assert len(r) == 108 and r.is_n.max() > 0
    print(f"OK walkforward · {len(r)} combinaciones")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    test_sign()
    test_strategy()
    test_bot_paper()
    test_bot_live()
    test_regime_and_protections()
    test_walkforward()
    print("\nTODO OK")
