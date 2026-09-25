"""EP5 v4 · bot BingX perpetuos 5m — Entrada Precisa (estrategia reparada).

DRY_RUN=true  → paper: señales reales, fills simulados, diario en /data/ep5_journal.csv
DRY_RUN=false → real: orden a mercado + STOP_MARKET + TAKE_PROFIT_MARKET en BingX,
                BE/trailing moviendo el stop en el exchange, salida por tiempo.

Protecciones v4.1: filtro régimen BTC 1h · pausa por drawdown total · límite de exposición ·
evita funding en contra · PnL real desde BingX · slippage en diario · watchdog · comandos Telegram
(/estado /pausa /reanudar /cerrar_todo /ayuda).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from bingx import BingX, BingXError, floor_to, fnum
from config import Cfg
from notify import Telegram
from state import Journal, State, iso
from strategy import (Params, Pos, apply_regime, check_exit, compute, new_pos, regime_series, result,
                      track, update_levels)

CODE_VERSION = "EP5-v4.1.0"
BAR_MS = 300_000
BTC = "BTC-USDT"
HELP = ("/estado · posiciones, R del día y acumulado\n/pausa · no abre nuevas (gestiona las abiertas)\n"
        "/reanudar · vuelve a abrir (reinicia el pico de drawdown)\n/cerrar_todo · cierra todo a mercado y pausa\n"
        "/ayuda")
log = logging.getLogger("ep5")


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Bot:
    def __init__(self):
        self.cfg = Cfg.from_env()
        self.p = Params.from_env()
        self.live = not self.cfg.dry_run
        self.bx = BingX(self.cfg.bingx_api_key, self.cfg.bingx_api_secret, self.cfg.bingx_base)
        tag = self.cfg.bot_name + ("" if self.live else " · PAPER")
        self.tg = Telegram(self.cfg.telegram_token, self.cfg.telegram_chat_id, f"[{tag}]")
        self.st = State(os.path.join(self.cfg.data_dir, "ep5_state.json"))
        self.jr = Journal(os.path.join(self.cfg.data_dir, "ep5_journal.csv"))
        if self.st.paper_equity is None:
            self.st.paper_equity = self.cfg.paper_equity
        self.contracts: dict = {}
        self.universe: list[str] = []
        self.univ_at = 0.0
        self.hedge = False
        self.last_hb = time.time()
        self.last_ok = time.time()
        self.last_px: dict[str, float] = {}
        self.reg: object = None
        self.btc_now = 0
        self.lock = threading.RLock()

    # ───────────────────────── arranque ─────────────────────────
    def run(self, once: bool = False):
        log.info("CODE_VERSION=%s modo=%s", CODE_VERSION, "REAL" if self.live else "PAPER")
        if self.live and not (self.cfg.bingx_api_key and self.cfg.bingx_api_secret):
            raise SystemExit("DRY_RUN=false pero faltan BINGX_API_KEY / BINGX_API_SECRET")
        self.contracts = self.bx.contracts()
        eq_txt = f"paper {self.st.paper_equity:.2f} USDT"
        if self.live:
            self.hedge = self.bx.hedge_mode()
            eq_txt = f"equity {self.bx.equity():.2f} USDT · modo {'HEDGE' if self.hedge else 'ONE-WAY'}"
        self.refresh_universe(force=True)
        self.tg.send(
            f"🚀 {CODE_VERSION} {'REAL' if self.live else 'PAPER'}\n{eq_txt}\n"
            f"universo {len(self.universe)} · riesgo {self.cfg.risk_pct}%/op · x{self.cfg.leverage}\n"
            f"SL {self.p.sl_atr} ATR · TP {self.p.tp_atr} ATR · sesión {self.p.session_mode} · "
            f"filtro BTC {'ON' if self.p.btc_filter else 'OFF'} · DD máx {self.cfg.max_dd_r}R\n"
            f"abiertas {len(self.st.positions)}"
            + (f"\n⏸ EN PAUSA: {self.st.pause_reason}" if self.st.paused else "")
            + ("\nComandos: /ayuda" if self.tg.enabled else ""))
        self.tg.poll()                                   # descarta comandos antiguos
        if self.cfg.watchdog_min > 0 and not once:
            threading.Thread(target=self.watchdog, daemon=True).start()
        if self.live:
            self.reconcile()
        self.safe_cycle()
        while not once:
            self.sleep_to_next_bar()
            self.safe_cycle()

    def sleep_to_next_bar(self):
        """Espera a la siguiente vela atendiendo comandos de Telegram cada ~3 s."""
        nxt = (now_ms() // BAR_MS + 1) * BAR_MS + self.cfg.loop_delay_s * 1000
        while True:
            self.handle_commands()
            left = (nxt - now_ms()) / 1000
            if left <= 0:
                return
            time.sleep(min(3.0, max(0.2, left)))

    def safe_cycle(self):
        try:
            with self.lock:
                self.cycle()
        except BingXError as e:
            log.exception("BingX")
            self.tg.error(f"BingX: {e}")
        except Exception as e:  # noqa: BLE001
            log.exception("ciclo")
            self.tg.error(f"error ciclo: {type(e).__name__}: {e}")
        finally:
            self.last_ok = time.time()
            try:
                self.st.save()
            except OSError as e:
                log.error("no se pudo guardar estado: %s", e)

    def watchdog(self):
        """Si no se completa un ciclo en watchdog_min, avisa y sale: Railway reinicia el proceso."""
        limit = self.cfg.watchdog_min * 60
        while True:
            time.sleep(30)
            if time.time() - self.last_ok > limit:
                self.tg.send(f"🐕 Watchdog: sin ciclo completo en {self.cfg.watchdog_min} min. Reiniciando.")
                logging.shutdown()
                os._exit(1)

    # ───────────────────────── ciclo ─────────────────────────
    def cycle(self):
        t0 = time.time()
        self.roll_day()
        self.refresh_universe()
        syms = set(self.universe) | set(self.st.positions)
        if self.p.btc_filter:
            syms.add(BTC)
        data = self.fetch_all(sorted(syms))
        for s, f in data.items():
            if f is not None:
                self.last_px[s] = float(f.close.iloc[-1])
        self.reg = None
        if self.p.btc_filter and data.get(BTC) is not None:
            self.reg = regime_series(data[BTC], self.p)
            self.btc_now = int(self.reg.reg.iloc[-1])
        self.manage(data)
        self.entries(data)
        self.heartbeat()
        log.info("ciclo %.1fs · datos %d/%d · abiertas %d · día %+.2fR · BTC %+d",
                 time.time() - t0, sum(v is not None for v in data.values()), len(syms),
                 len(self.st.positions), self.st.day_r, self.btc_now)

    def roll_day(self):
        d = utc_day()
        if self.st.day == d:
            return
        if self.st.day:
            wr = self.st.day_wins / self.st.day_closed * 100 if self.st.day_closed else 0
            extra = "" if self.live else f" · equity paper {self.st.paper_equity:.2f}"
            self.tg.send(f"📊 Resumen {self.st.day}: {self.st.day_closed} cerradas · WR {wr:.0f}% · "
                         f"{self.st.day_r:+.2f}R{extra}")
        self.st.day, self.st.day_r, self.st.day_trades = d, 0.0, 0
        self.st.day_wins, self.st.day_closed, self.st.blocked_notified = 0, 0, False

    def refresh_universe(self, force: bool = False):
        if not force and time.time() - self.univ_at < 3600:
            return
        fixed = self.cfg.symbols_list()
        if fixed:
            self.universe = [s for s in fixed if s in self.contracts] or fixed
        else:
            bl, rows = self.cfg.blacklist_set(), []
            for t in self.bx.tickers():
                s = t.get("symbol", "")
                if not s.endswith("-USDT") or s in bl or s[:-5].endswith("USD"):
                    continue
                c = self.contracts.get(s)
                if c is None or c["status"] != 1:
                    continue
                qv = float(t.get("quoteVolume") or 0)
                if qv >= self.cfg.min_quote_vol:
                    rows.append((qv, s))
            self.universe = [s for _, s in sorted(rows, reverse=True)[: self.cfg.top_n]]
        self.univ_at = time.time()
        log.info("universo %d: %s", len(self.universe), ",".join(self.universe[:15]))

    def fetch_all(self, syms: list[str]) -> dict:
        cutoff = now_ms()

        def one(sym):
            try:
                df = self.bx.klines(sym, "5m", self.cfg.kline_limit)
                df = df[df.time + BAR_MS <= cutoff]          # solo velas cerradas
                if len(df) < 400:
                    return sym, None
                return sym, compute(df, self.p)
            except Exception as e:  # noqa: BLE001
                log.warning("klines %s: %s", sym, e)
                return sym, None

        with ThreadPoolExecutor(max_workers=self.cfg.workers) as ex:
            return dict(ex.map(one, syms))

    # ───────────────────────── gestión ─────────────────────────
    def exch_map(self) -> dict:
        m = {}
        for x in self.bx.positions():
            amt = float(x.get("positionAmt", 0) or 0)
            ps = str(x.get("positionSide", "BOTH")).upper()
            side = 1 if ps == "LONG" else -1 if ps == "SHORT" else (1 if amt > 0 else -1)
            m[(x["symbol"], side)] = {"amt": abs(amt), "avg": float(x.get("avgPrice", 0) or 0)}
        return m

    def manage(self, data: dict):
        if not self.st.positions:
            return
        exch = self.exch_map() if self.live else {}
        for sym, pos in list(self.st.positions.items()):
            f = data.get(sym)
            if f is None:
                continue
            new = f[f.time > pos.last_ms]
            if self.live:
                self.manage_live(pos, new, f, exch)
            else:
                self.manage_paper(pos, new)

    def manage_paper(self, pos: Pos, new):
        for bar in new.itertuples(index=False):
            track(pos, bar)
            ex = check_exit(pos, bar)
            pos.bars += 1
            pos.last_ms = int(bar.time)
            if ex is None:
                prev = pos.sl_state
                update_levels(pos, bar, bar.atr, self.p)
                self.notify_sl_state(pos, prev)
                if pos.bars >= self.p.max_bars:
                    ex = (float(bar.close), "tiempo")
            if ex is not None:
                self.close_record(pos, float(ex[0]), ex[1], int(bar.time) + BAR_MS)
                return

    def manage_live(self, pos: Pos, new, f, exch: dict):
        inferred = None
        prev_state = pos.sl_state
        for bar in new.itertuples(index=False):
            track(pos, bar)
            pos.bars += 1
            pos.last_ms = int(bar.time)
            if inferred is None:
                hit = check_exit(pos, bar)
                if hit:
                    inferred = (float(hit[0]), hit[1], int(bar.time) + BAR_MS)
                else:
                    update_levels(pos, bar, bar.atr, self.p)
        sym = pos.symbol
        if (sym, pos.side) not in exch:                     # cerrada en el exchange (SL/TP/manual)
            price, reason, ts = inferred or (float(f.close.iloc[-1]), "externo", now_ms())
            self.try_(self.bx.cancel_all, sym)
            self.close_record(pos, price, reason, ts)
            return
        pos.qty = exch[(sym, pos.side)]["amt"]
        if pos.bars >= self.p.max_bars:
            self.close_live_market(pos)
            self.close_record(pos, float(f.close.iloc[-1]), "tiempo", now_ms())
            return
        tick = 10 ** -self.contracts.get(sym, {"pprec": 6})["pprec"]
        if inferred is None and abs(pos.sl - pos.sl_live) >= tick:
            self.replace_sl(pos)
            self.notify_sl_state(pos, prev_state)

    def notify_sl_state(self, pos: Pos, prev: str):
        if pos.sl_state != prev and pos.sl_state in ("be", "trail"):
            self.tg.send(f"🔒 {pos.symbol} {'LONG' if pos.side == 1 else 'SHORT'} stop → "
                         f"{'breakeven' if pos.sl_state == 'be' else 'trailing'} {pos.sl:.6g}")

    # ───────────────────────── entradas ─────────────────────────
    def entries(self, data: dict):
        st, cfg = self.st, self.cfg
        if st.paused:
            return
        if st.day_r <= -cfg.daily_max_loss_r:
            if not st.blocked_notified:
                st.blocked_notified = True
                self.tg.send(f"⛔ Límite diario alcanzado ({st.day_r:+.2f}R). Sin entradas hasta 00:00 UTC.")
            return
        if st.day_trades >= cfg.max_trades_day:
            return
        frames = [f for s, f in data.items() if f is not None and s in self.universe]
        if not frames:
            return
        latest = max(int(f.time.iloc[-1]) for f in frames)
        if now_ms() - (latest + BAR_MS) > 120_000:           # vela vieja (reinicio/lag): no entrar
            return
        cands = []
        for sym in self.universe:
            f = data.get(sym)
            if f is None or sym in st.positions:
                continue
            if int(f.time.iloc[-1]) != latest or int(f.sig.iloc[-1]) == 0:
                continue
            row = apply_regime(f.tail(1), self.reg, self.p).iloc[-1]
            if int(row.sig) == 0:
                log.info("%s señal %s anulada por régimen BTC (%+d)", sym,
                         "LONG" if int(f.sig.iloc[-1]) == 1 else "SHORT", self.btc_now)
                continue
            le = st.last_exit.get(sym)
            if le and (int(row.time) + BAR_MS - le) / BAR_MS < self.p.cooldown_bars:
                continue
            cands.append((int(row.score), float(row.rvol), sym, row))
        if not cands:
            return
        cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
        held = {s for s, _ in self.exch_map()} if self.live else set()
        for _, _, sym, row in cands:
            if len(st.positions) >= cfg.max_positions or st.day_trades >= cfg.max_trades_day:
                break
            side = int(row.sig)
            if sum(1 for p in st.positions.values() if p.side == side) >= cfg.max_per_side:
                continue
            if sym in held:
                log.info("%s ya tiene posición en la cuenta (¿manual?), se omite", sym)
                continue
            if not self.funding_ok(sym, side):
                continue
            ok = self.open_live(sym, row) if self.live else self.open_paper(sym, row)
            if ok:
                st.day_trades += 1
                st.save()

    def funding_ok(self, sym: str, side: int) -> bool:
        """Evita abrir justo antes de un cobro de funding que va en contra."""
        if self.cfg.funding_avoid_min <= 0:
            return True
        fi = self.try_(self.bx.premium_index, sym)
        if not fi or not fi["next_ms"]:
            return True
        mins = (fi["next_ms"] - now_ms()) / 60_000
        if 0 <= mins <= self.cfg.funding_avoid_min and side * fi["rate"] > 0:
            log.info("%s: funding %.4f%% en contra en %.0f min, se omite", sym, fi["rate"] * 100, mins)
            return False
        return True

    def room_notional(self, equity: float) -> float:
        """Nocional USDT que aún cabe bajo MAX_EXPOSURE_X."""
        used = sum(p.qty * p.entry for p in self.st.positions.values())
        return max(0.0, equity * self.cfg.max_exposure_x - used)

    def open_paper(self, sym: str, row) -> bool:
        side = int(row.sig)
        fill = float(row.close) * (1 + side * self.cfg.slippage_bps / 1e4)
        pos = new_pos(sym, row, self.p, entry=fill)
        pos.risk_usdt = self.st.paper_equity * self.cfg.risk_pct / 100
        pos.qty = pos.risk_usdt / pos.r
        room = self.room_notional(self.st.paper_equity)
        if pos.qty * fill > room:
            if room < 5:
                log.info("%s: sin hueco de exposición, se omite", sym)
                return False
            pos.qty = room / fill
            pos.risk_usdt = pos.qty * pos.r
        pos.pos_side = "PAPER"
        self.st.positions[sym] = pos
        self.msg_open(pos)
        return True

    def open_live(self, sym: str, row) -> bool:
        c = self.contracts.get(sym)
        if not c:
            return False
        side, price = int(row.sig), float(row.close)
        r = float(row.atr) * self.p.sl_atr
        eq = self.bx.equity()
        qty = min(eq * self.cfg.risk_pct / 100 / r, eq * self.cfg.leverage * 0.9 / price,
                  self.room_notional(eq) / price)
        qty = floor_to(qty, c["qprec"])
        if qty <= 0 or qty < c["min_qty"] or qty * price < max(c["min_usdt"], 2.0):
            log.info("%s qty %.8f por debajo del mínimo, se omite", sym, qty)
            return False
        self.ensure_leverage(sym)
        pos_side = ("LONG" if side == 1 else "SHORT") if self.hedge else "BOTH"
        self.bx.order(symbol=sym, side="BUY" if side == 1 else "SELL", positionSide=pos_side,
                      type="MARKET", quantity=fnum(qty, c["qprec"]))
        fill = None
        for _ in range(5):
            time.sleep(1.0)
            fill = self.exch_map().get((sym, side))
            if fill:
                break
        if not fill:
            self.tg.error(f"{sym}: orden enviada pero sin posición visible. Revisar a mano.", every_s=0)
            return False
        pos = new_pos(sym, row, self.p, entry=fill["avg"] or price)
        pos.qty, pos.pos_side = fill["amt"], pos_side
        pos.risk_usdt = pos.qty * pos.r
        try:
            pos.sl_order = self.place_stop(pos, pos.sl)
            pos.sl_live = round(pos.sl, c["pprec"])
        except Exception as e:  # noqa: BLE001
            self.tg.error(f"{sym}: SL rechazado ({e}). Cierre de emergencia.", every_s=0)
            self.close_live_market(pos)
            return False
        try:
            pos.tp_order = self.place_tp(pos)
        except Exception as e:  # noqa: BLE001
            self.tg.error(f"{sym}: TP rechazado ({e}); queda solo SL.", every_s=0)
        self.st.positions[sym] = pos
        self.msg_open(pos)
        return True

    def msg_open(self, pos: Pos):
        pp = self.contracts.get(pos.symbol, {"pprec": 6})["pprec"]
        self.tg.send(
            f"{'🟢 LONG' if pos.side == 1 else '🔴 SHORT'} {pos.symbol} @ {fnum(pos.entry, pp)}\n"
            f"SL {fnum(pos.sl, pp)} · TP {fnum(pos.tp, pp)} · riesgo {pos.risk_usdt:.2f} USDT\n"
            f"score {pos.score}/5 · dist {pos.dist:+.2f} ATR · rvol {pos.rvol:.1f} · coste {pos.coste_r:.2f}R")

    # ───────────────────────── órdenes en exchange ─────────────────────────
    def _exit_params(self, pos: Pos) -> dict:
        c = self.contracts[pos.symbol]
        d = {"symbol": pos.symbol, "side": "SELL" if pos.side == 1 else "BUY",
             "positionSide": pos.pos_side, "quantity": fnum(pos.qty, c["qprec"])}
        if not self.hedge:
            d["reduceOnly"] = "true"
        return d

    def place_stop(self, pos: Pos, price: float) -> str:
        pp = self.contracts[pos.symbol]["pprec"]
        return self.bx.order(**self._exit_params(pos), type="STOP_MARKET",
                             stopPrice=fnum(price, pp), workingType="MARK_PRICE")

    def place_tp(self, pos: Pos) -> str:
        pp = self.contracts[pos.symbol]["pprec"]
        return self.bx.order(**self._exit_params(pos), type="TAKE_PROFIT_MARKET",
                             stopPrice=fnum(pos.tp, pp), workingType="MARK_PRICE")

    def replace_sl(self, pos: Pos):
        """Primero el stop nuevo, luego se cancela el viejo: nunca queda desprotegida."""
        try:
            new_id = self.place_stop(pos, pos.sl)
        except Exception as e:  # noqa: BLE001
            log.warning("%s no se pudo mover SL: %s", pos.symbol, e)
            return
        old, pos.sl_order = pos.sl_order, new_id
        pos.sl_live = round(pos.sl, self.contracts[pos.symbol]["pprec"])
        self.try_(self.bx.cancel, pos.symbol, old)

    def close_live_market(self, pos: Pos):
        self.try_(lambda: self.bx.order(**self._exit_params(pos), type="MARKET"))
        self.try_(self.bx.cancel_all, pos.symbol)

    def ensure_leverage(self, sym: str):
        if sym in self.st.leverage_set:
            return
        for s in (("LONG", "SHORT") if self.hedge else ("BOTH",)):
            self.try_(self.bx.set_leverage, sym, s, self.cfg.leverage)
        self.st.leverage_set.append(sym)

    def reconcile(self):
        """Al arrancar: posiciones del estado vs exchange; repone SL/TP que falten."""
        if not self.st.positions:
            return
        exch = self.exch_map()
        for sym, pos in list(self.st.positions.items()):
            if (sym, pos.side) not in exch:
                last = self.bx.klines(sym, "5m", 2)
                px = float(last.close.iloc[-1]) if len(last) else pos.entry
                self.try_(self.bx.cancel_all, sym)
                self.close_record(pos, px, "externo", now_ms())
                continue
            pos.qty = exch[(sym, pos.side)]["amt"]
            ids = {str(o.get("orderId")) for o in (self.bx.open_orders(sym) or [])}
            if pos.sl_order not in ids:
                pos.sl_order = self.place_stop(pos, pos.sl)
                pos.sl_live = round(pos.sl, self.contracts[sym]["pprec"])
                self.tg.send(f"🛠 {sym}: SL repuesto en {pos.sl:.6g}")
            if pos.tp_order and pos.tp_order not in ids:
                self.try_(lambda p=pos: setattr(p, "tp_order", self.place_tp(p)))
        self.st.save()

    # ───────────────────────── cierre / utilidades ─────────────────────────
    def real_pnl(self, pos: Pos) -> float | None:
        """PnL real en BingX (realizado + comisiones + funding) desde la apertura."""
        if not self.live:
            return None
        time.sleep(1.5)                                  # dar tiempo a que BingX asiente el cierre
        return self.try_(self.bx.income, pos.symbol, pos.opened_ms, now_ms() + 60_000)

    def close_record(self, pos: Pos, price: float, reason: str, ts: int):
        res = result(pos, price)
        pnl = res["r_neto"] * pos.risk_usdt
        pnl_real = self.real_pnl(pos)
        r_real = pnl_real / pos.risk_usdt if pnl_real is not None and pos.risk_usdt > 0 else None
        r_book = r_real if r_real is not None else res["r_neto"]      # contabilidad: real si existe
        st = self.st
        if not self.live:
            st.paper_equity += pnl
        st.day_r += r_book
        st.cum_r += r_book
        st.peak_r = max(st.peak_r, st.cum_r)
        st.day_closed += 1
        st.day_wins += int(r_book > 0)
        st.last_exit[pos.symbol] = ts
        st.positions.pop(pos.symbol, None)
        slip = pos.side * (pos.entry - pos.signal_px) / pos.signal_px * 1e4 if pos.signal_px else 0.0
        self.jr.write({
            "cerrada_utc": iso(ts), "symbol": pos.symbol, "lado": "LONG" if pos.side == 1 else "SHORT",
            "abierta_utc": iso(pos.opened_ms + BAR_MS), "entrada": pos.entry, "salida": price, "motivo": reason,
            "r_bruto": round(res["r_bruto"], 4), "coste_r": round(res["coste_r"], 4),
            "r_neto": round(res["r_neto"], 4), "barras": pos.bars, "score": pos.score,
            "dist_htf": round(pos.dist, 3), "er_pct": round(pos.er_pct, 1), "rvol": round(pos.rvol, 2),
            "atr_pct": round(pos.atr_pct, 3), "mfe": round(pos.mfe, 3), "mae": round(pos.mae, 3),
            "pnl_usdt": round(pnl, 4), "modo": "REAL" if self.live else "PAPER",
            "slip_bps": round(slip, 2), "btc_reg": pos.btc_reg,
            "pnl_real_usdt": "" if pnl_real is None else round(pnl_real, 4),
            "r_real": "" if r_real is None else round(r_real, 4),
        })
        real_txt = f" · real {r_real:+.2f}R ({pnl_real:+.2f})" if r_real is not None else ""
        self.tg.send(f"{'✅' if r_book > 0 else '❌'} {pos.symbol} {'LONG' if pos.side == 1 else 'SHORT'} "
                     f"{reason} {res['r_neto']:+.2f}R est.{real_txt} · {pos.bars} velas · "
                     f"día {st.day_r:+.2f}R · total {st.cum_r:+.2f}R")
        dd = st.cum_r - st.peak_r
        if not st.paused and dd <= -self.cfg.max_dd_r:
            self.pause(f"drawdown {dd:.1f}R desde el máximo (límite {self.cfg.max_dd_r}R)")
        st.save()

    def pause(self, reason: str):
        self.st.paused, self.st.pause_reason = True, reason
        self.st.save()
        self.tg.send(f"⏸ PAUSA: {reason}. Sigue gestionando las abiertas. /reanudar para volver.")

    # ───────────────────────── comandos Telegram ─────────────────────────
    def handle_commands(self):
        for cmd in self.tg.poll():
            log.info("comando %s", cmd)
            try:
                with self.lock:
                    self.command(cmd)
            except Exception as e:  # noqa: BLE001
                log.exception("comando")
                self.tg.error(f"comando {cmd}: {e}", every_s=0)

    def command(self, cmd: str):
        st = self.st
        if cmd in ("/estado", "/status"):
            self.tg.send(self.status_text())
        elif cmd in ("/pausa", "/pause"):
            if st.paused:
                self.tg.send(f"Ya estaba en pausa: {st.pause_reason}")
            else:
                self.pause("manual")
        elif cmd in ("/reanudar", "/resume"):
            st.paused, st.pause_reason = False, ""
            st.peak_r = st.cum_r                          # nuevo pico de referencia para el DD
            st.save()
            self.tg.send(f"▶️ Reanudado. Referencia de drawdown reiniciada en {st.cum_r:+.2f}R.")
        elif cmd in ("/cerrar_todo", "/closeall"):
            n = len(st.positions)
            for pos in list(st.positions.values()):
                px = self.last_px.get(pos.symbol, pos.entry)
                if self.live:
                    self.close_live_market(pos)
                self.close_record(pos, px, "manual", now_ms())
            self.pause("cierre total manual")
            self.tg.send(f"🧹 {n} posiciones cerradas a mercado.")
        elif cmd in ("/ayuda", "/help", "/start"):
            self.tg.send(HELP)
        else:
            self.tg.send(f"Comando desconocido {cmd}\n{HELP}")

    def status_text(self) -> str:
        st = self.st
        lines = [f"{CODE_VERSION} · {'REAL' if self.live else 'PAPER'}"
                 + (f" · ⏸ {st.pause_reason}" if st.paused else " · ▶️ activo"),
                 f"día {st.day_r:+.2f}R ({st.day_closed} cerradas, {st.day_trades} abiertas hoy) · "
                 f"total {st.cum_r:+.2f}R · DD {st.cum_r - st.peak_r:+.2f}R",
                 f"BTC 1h {'alcista' if self.btc_now > 0 else 'bajista' if self.btc_now < 0 else 'neutro'} · "
                 f"universo {len(self.universe)}"]
        if not self.live:
            lines.append(f"equity paper {st.paper_equity:.2f}")
        for p in st.positions.values():
            px = self.last_px.get(p.symbol, p.entry)
            lines.append(f"• {p.symbol} {'L' if p.side == 1 else 'S'} {p.side * (px - p.entry) / p.r:+.2f}R · "
                         f"stop {p.sl_state} · {p.bars} velas")
        if not st.positions:
            lines.append("sin posiciones")
        return "\n".join(lines)

    def heartbeat(self):
        if self.cfg.heartbeat_h <= 0 or time.time() - self.last_hb < self.cfg.heartbeat_h * 3600:
            return
        self.last_hb = time.time()
        self.tg.send("💓 " + self.status_text())

    def try_(self, fn, *a):
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: %s", getattr(fn, "__name__", "call"), e)
            return None


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    Bot().run(once="--once" in sys.argv)


if __name__ == "__main__":
    main()
