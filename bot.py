"""
╔══════════════════════════════════════════════════════════════════════╗
║  SAIYAN ADAPTIVE ENGINE v1.1  (fix: backslash en f-strings)         ║
║  Modulo de Auto-Aprendizaje + Grid Trading para mercado lateral      ║
║                                                                      ║
║  COMO USAR: importar en bot.py y llamar desde el startup:            ║
║    from adaptive_engine import AdaptiveEngine, GridEngine            ║
║    adaptive = AdaptiveEngine(st, log)                                ║
║    grid_engine = GridEngine(ex, st, log, tg)                         ║
║                                                                      ║
║  VARIABLES NUEVAS EN RAILWAY:                                        ║
║    ADAPTIVE_LEARNING  def: true  (auto-ajuste de parametros)         ║
║    GRID_MODE          def: true  (grid en mercado lateral)           ║
║    GRID_LEVELS        def: 6     (niveles del grid)                  ║
║    GRID_SPACING_PCT   def: 0.3   (separacion entre niveles en %)     ║
║    GRID_USDT          def: 5     (USDT por nivel del grid)           ║
║    GRID_SYMBOL        def: BTC/USDT:USDT (par para grid)             ║
║    REGIME_WINDOW      def: 20    (velas para detectar regimen)       ║
║    REGIME_ADX_THRESH  def: 25    (ADX > 25 = tendencia, < 25 = lat) ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os, time, logging, threading, json, math
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
import numpy as np

# ─────────────────────────────────────────────────────────────────
# CONFIG ADAPTIVE ENGINE
# ─────────────────────────────────────────────────────────────────
ADAPTIVE_LEARNING  = os.environ.get("ADAPTIVE_LEARNING", "true").lower() == "true"
GRID_MODE          = os.environ.get("GRID_MODE",          "true").lower() == "true"
GRID_LEVELS        = int  (os.environ.get("GRID_LEVELS",       "6"))
GRID_SPACING_PCT   = float(os.environ.get("GRID_SPACING_PCT",  "0.3"))
GRID_USDT          = float(os.environ.get("GRID_USDT",         "5.0"))
GRID_SYMBOL        = os.environ.get("GRID_SYMBOL", "BTC/USDT:USDT")
REGIME_WINDOW      = int  (os.environ.get("REGIME_WINDOW",     "20"))
REGIME_ADX_THRESH  = float(os.environ.get("REGIME_ADX_THRESH", "25.0"))
LEVERAGE           = int  (os.environ.get("LEVERAGE",          "5"))

# ─────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────
def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def esc(text) -> str:
    import html
    return html.escape(str(text), quote=False)


# ═══════════════════════════════════════════════════════════════════
#  PARTE 1: ADAPTIVE LEARNING ENGINE
# ═══════════════════════════════════════════════════════════════════
class AdaptiveEngine:
    """
    Motor de auto-aprendizaje.
    Analiza closed_history cada hora y ajusta los parametros del bot
    basandose en patrones de perdidas y ganancias.
    """

    PARAM_LIMITS = {
        "RSI_OB":            (60.0,  80.0),
        "RSI_OS":            (20.0,  40.0),
        "MAX_SPREAD_PCT":    (0.05,  0.5),
        "VOL_MULT":          (0.8,   2.5),
        "ATR_MIN_MULT":      (0.1,   1.0),
        "SL_PCT":            (0.3,   2.0),
        "MIN_CONFIRMATIONS": (1,     4),
        "COOLDOWN_MIN":      (2,     30),
    }

    def __init__(self, state, logger):
        self.st  = state
        self.log = logger

        self.params = {
            "RSI_OB":            float(os.environ.get("RSI_OB",            "72.0")),
            "RSI_OS":            float(os.environ.get("RSI_OS",            "28.0")),
            "MAX_SPREAD_PCT":    float(os.environ.get("MAX_SPREAD_PCT",    "0.15")),
            "VOL_MULT":          float(os.environ.get("VOL_MULT",          "1.2")),
            "ATR_MIN_MULT":      float(os.environ.get("ATR_MIN_MULT",      "0.3")),
            "SL_PCT":            float(os.environ.get("SL_PCT",            "0.8")),
            "MIN_CONFIRMATIONS": int  (os.environ.get("MIN_CONFIRMATIONS", "1")),
            "COOLDOWN_MIN":      int  (os.environ.get("COOLDOWN_MIN",      "5")),
        }

        self.adjustment_history: List[dict] = []
        self.last_analysis_ts    = 0.0
        self.analysis_interval   = 3600

        self.log.info("AdaptiveEngine iniciado - aprendizaje automatico activo")

    def get(self, param: str):
        return self.params.get(param)

    def _clamp(self, param: str, value) -> float:
        lo, hi = self.PARAM_LIMITS[param]
        return max(lo, min(hi, value))

    def _adjust(self, param: str, delta, reason: str):
        old = self.params[param]
        new = self._clamp(param, old + delta)
        if abs(new - old) < 1e-6:
            return
        self.params[param] = new
        entry = {
            "ts":     now(),
            "param":  param,
            "old":    round(old, 4),
            "new":    round(new, 4),
            "reason": reason,
        }
        self.adjustment_history.append(entry)
        if len(self.adjustment_history) > 200:
            self.adjustment_history = self.adjustment_history[-200:]
        self.log.info("[ADAPTIVE] %s: %s -> %s (%s)", param, old, new, reason)

    def analyze(self) -> List[dict]:
        if not ADAPTIVE_LEARNING:
            return []

        now_ts = time.time()
        if now_ts - self.last_analysis_ts < self.analysis_interval:
            return []
        self.last_analysis_ts = now_ts

        history = self.st.closed_history
        if len(history) < 5:
            self.log.info("[ADAPTIVE] Menos de 5 trades - esperando mas datos")
            return []

        adjustments_made = []
        recent           = history[-20:]
        wins             = [t for t in recent if t.get("pnl", 0) >= 0]
        losses           = [t for t in recent if t.get("pnl", 0) < 0]
        n_recent         = len(recent)
        wr_recent        = len(wins) / n_recent * 100 if n_recent > 0 else 0

        self.log.info("[ADAPTIVE] Analizando %d trades. WR: %.1f%%", n_recent, wr_recent)

        # REGLA 1: Muchas perdidas seguidas
        consecutive = self._count_consecutive_losses(recent)
        if consecutive >= 3:
            self._adjust("MIN_CONFIRMATIONS", +1,
                         "3+ perdidas seguidas (%d)" % consecutive)
            self._adjust("COOLDOWN_MIN", +2,
                         "3+ perdidas seguidas - mas cooldown")
            adjustments_made.append({"type": "safety", "reason": "consecutive_losses", "n": consecutive})

        # REGLA 2: WR bajo
        if wr_recent < 35 and n_recent >= 10:
            self._adjust("VOL_MULT",       +0.1,  "WR bajo %.1f%% - mas volumen requerido" % wr_recent)
            self._adjust("MAX_SPREAD_PCT", -0.02, "WR bajo %.1f%% - menos spread" % wr_recent)
            adjustments_made.append({"type": "filter", "reason": "low_wr", "wr": wr_recent})

        # REGLA 3: WR alto
        elif wr_recent > 70 and n_recent >= 10:
            self._adjust("VOL_MULT",     -0.05, "WR alto %.1f%% - relajando vol" % wr_recent)
            self._adjust("COOLDOWN_MIN", -1,    "WR alto - menos cooldown")
            adjustments_made.append({"type": "relax", "reason": "high_wr", "wr": wr_recent})

        # REGLA 4: Longs vs Shorts
        long_losses  = [t for t in losses if t.get("side") == "long"]
        short_losses = [t for t in losses if t.get("side") == "short"]

        if len(long_losses) > len(short_losses) * 2 and len(long_losses) >= 3:
            self._adjust("RSI_OB", -2.0,
                         "Longs perdiendo (%d vs %d shorts)" % (len(long_losses), len(short_losses)))
            adjustments_made.append({"type": "side_bias", "reason": "long_losses"})

        elif len(short_losses) > len(long_losses) * 2 and len(short_losses) >= 3:
            self._adjust("RSI_OS", +2.0,
                         "Shorts perdiendo (%d vs %d longs)" % (len(short_losses), len(long_losses)))
            adjustments_made.append({"type": "side_bias", "reason": "short_losses"})

        # REGLA 5: SL frecuente
        sl_closes = [t for t in recent if "STOP" in t.get("reason", "").upper()
                     or "SL" in t.get("reason", "").upper()]
        if len(sl_closes) > n_recent * 0.4 and n_recent >= 8:
            self._adjust("SL_PCT", +0.1,
                         "SL tocado %d/%d veces - ampliando SL" % (len(sl_closes), n_recent))
            adjustments_made.append({"type": "sl_adjust", "reason": "frequent_sl"})

        # REGLA 6: Ratio perdida/ganancia malo
        if losses:
            avg_loss_pnl = abs(sum(t.get("pnl", 0) for t in losses) / len(losses))
            avg_win_pnl  = sum(t.get("pnl", 0) for t in wins) / len(wins) if wins else 0
            if avg_loss_pnl > avg_win_pnl * 1.5 and len(losses) >= 3:
                self._adjust("ATR_MIN_MULT", +0.05,
                             "Perdida media (%.2f) > ganancia media (%.2f)" % (avg_loss_pnl, avg_win_pnl))
                adjustments_made.append({"type": "rr_adjust", "reason": "bad_rr"})

        if adjustments_made:
            self.log.info("[ADAPTIVE] %d ajustes aplicados", len(adjustments_made))
        else:
            self.log.info("[ADAPTIVE] Sin ajustes necesarios - bot funcionando bien")

        return adjustments_made

    def _count_consecutive_losses(self, trades: list) -> int:
        count = 0
        for t in reversed(trades):
            if t.get("pnl", 0) < 0:
                count += 1
            else:
                break
        return count

    def get_status(self) -> dict:
        return {
            "enabled":            ADAPTIVE_LEARNING,
            "current_params":     self.params,
            "adjustments_made":   len(self.adjustment_history),
            "last_adjustments":   self.adjustment_history[-5:],
            "next_analysis_in_s": max(0, int(self.analysis_interval - (time.time() - self.last_analysis_ts))),
        }

    def msg_adaptive_report(self, tg_func):
        if not self.adjustment_history:
            tg_func("El bot no ha necesitado ajustes aun. Todo en orden.")
            return

        recent_adj = self.adjustment_history[-8:]
        lines = ["<b>REPORTE AUTO-APRENDIZAJE</b>\n" + "=" * 30]
        for a in reversed(recent_adj):
            ts     = esc(a["ts"])
            param  = esc(a["param"])
            old    = esc(str(a["old"]))
            new    = esc(str(a["new"]))
            reason = esc(a["reason"])
            lines.append(
                "%s\n  %s: %s -> <b>%s</b>\n  Razon: %s" % (ts, param, old, new, reason)
            )
        lines.append("\nTotal ajustes historicos: %d" % len(self.adjustment_history))
        tg_func("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
#  PARTE 2: MARKET REGIME DETECTOR
# ═══════════════════════════════════════════════════════════════════
class RegimeDetector:
    """
    Detecta el regimen de mercado usando ADX + Bollinger Band Width.
    TENDENCIA: ADX > 25 y precio alejado de medias
    LATERAL:   ADX < 25 y precio oscilando en rango estrecho
    """

    def __init__(self, logger):
        self.log   = logger
        self._cache: Dict[str, Tuple[str, float]] = {}
        self._TTL  = 300

    def _calc_adx(self, highs: np.ndarray, lows: np.ndarray,
                  closes: np.ndarray, period: int = 14) -> float:
        if len(closes) < period * 2:
            return 25.0

        trs, plus_dm, minus_dm = [], [], []
        for i in range(1, len(closes)):
            hl  = highs[i] - lows[i]
            hc  = abs(highs[i] - closes[i - 1])
            lc  = abs(lows[i]  - closes[i - 1])
            trs.append(max(hl, hc, lc))

            up   = highs[i]  - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up   if up > down and up > 0   else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)

        def wilder_smooth(data, p):
            result = [sum(data[:p])]
            for v in data[p:]:
                result.append(result[-1] - result[-1] / p + v)
            return result

        atr14    = wilder_smooth(trs,      period)
        plus_14  = wilder_smooth(plus_dm,  period)
        minus_14 = wilder_smooth(minus_dm, period)

        dx_vals = []
        for a, p, m in zip(atr14, plus_14, minus_14):
            if a == 0:
                continue
            pdi   = 100 * p / a
            mdi   = 100 * m / a
            denom = pdi + mdi
            if denom == 0:
                continue
            dx_vals.append(100 * abs(pdi - mdi) / denom)

        if not dx_vals:
            return 25.0

        adx = sum(dx_vals[-period:]) / min(period, len(dx_vals))
        return round(adx, 2)

    def _calc_bb_width(self, closes: np.ndarray, period: int = 20) -> float:
        if len(closes) < period:
            return 0.05
        recent = closes[-period:]
        mid    = float(np.mean(recent))
        std    = float(np.std(recent))
        if mid == 0:
            return 0.05
        return round((4 * std) / mid, 4)

    def detect(self, symbol: str, exchange) -> str:
        cached = self._cache.get(symbol)
        if cached and (time.time() - cached[1]) < self._TTL:
            return cached[0]

        try:
            data = exchange.fetch_ohlcv(symbol, "1h", limit=60)
            if not data or len(data) < 30:
                return "unknown"

            arr    = np.array(data, dtype=float)
            highs  = arr[:, 2]
            lows   = arr[:, 3]
            closes = arr[:, 4]

            adx      = self._calc_adx(highs, lows, closes, 14)
            bb_width = self._calc_bb_width(closes, 20)

            if adx >= REGIME_ADX_THRESH and bb_width > 0.04:
                regime = "trending"
            elif adx < REGIME_ADX_THRESH:
                regime = "sideways"
            else:
                regime = "trending"

            self._cache[symbol] = (regime, time.time())
            self.log.info("[REGIME] %s: %s (ADX=%.1f, BBW=%.4f)", symbol, regime.upper(), adx, bb_width)
            return regime

        except Exception as e:
            self.log.warning("[REGIME] detect(%s): %s", symbol, e)
            return "unknown"


# ═══════════════════════════════════════════════════════════════════
#  PARTE 3: GRID ENGINE
# ═══════════════════════════════════════════════════════════════════
@dataclass
class GridLevel:
    price:    float
    side:     str
    order_id: str   = ""
    filled:   bool  = False
    pnl:      float = 0.0

@dataclass
class ActiveGrid:
    symbol:       str
    center_price: float
    spacing_pct:  float
    levels:       List[GridLevel] = field(default_factory=list)
    total_pnl:    float = 0.0
    trades_count: int   = 0
    created_at:   str   = field(default_factory=now)
    active:       bool  = True


class GridEngine:
    """
    Motor de Grid Trading para mercados laterales.

    Ejemplo con BTC a $95,000 y spacing 0.3%:
      Venta  @ $95,855 (precio + 3 niveles)
      Venta  @ $95,570 (precio + 2 niveles)
      Venta  @ $95,285 (precio + 1 nivel)
      [PRECIO ACTUAL: $95,000]
      Compra @ $94,715 (precio - 1 nivel)
      Compra @ $94,430 (precio - 2 niveles)
      Compra @ $94,145 (precio - 3 niveles)
    """

    def __init__(self, exchange_fn, state, logger, tg_fn):
        self.ex      = exchange_fn
        self.st      = state
        self.log     = logger
        self.tg      = tg_fn
        self.regime  = RegimeDetector(logger)
        self.grids:  Dict[str, ActiveGrid] = {}
        self._lock   = threading.Lock()
        self.dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    # ── CREAR GRID ────────────────────────────────────────────────
    def create_grid(self, symbol: str, center_price: Optional[float] = None) -> dict:
        if not GRID_MODE:
            return {"result": "grid_disabled"}

        with self._lock:
            if symbol in self.grids and self.grids[symbol].active:
                return {"result": "already_active", "symbol": symbol}

        try:
            e = self.ex()
            if symbol not in e.markets:
                e.load_markets()

            ticker = e.fetch_ticker(symbol)
            px     = center_price or float(ticker["last"])

            if px <= 0:
                return {"result": "error", "detail": "invalid price"}

            half   = GRID_LEVELS // 2
            levels = []

            for i in range(1, half + 1):
                buy_price  = round(px * (1 - i * GRID_SPACING_PCT / 100), 8)
                sell_price = round(px * (1 + i * GRID_SPACING_PCT / 100), 8)
                levels.append(GridLevel(price=buy_price,  side="buy"))
                levels.append(GridLevel(price=sell_price, side="sell"))

            levels.sort(key=lambda lv: lv.price)

            grid = ActiveGrid(
                symbol=symbol,
                center_price=px,
                spacing_pct=GRID_SPACING_PCT,
                levels=levels
            )

            if not self.dry_run:
                self._place_grid_orders(e, symbol, grid)
            else:
                self.log.info("[GRID DRY] Grid simulado para %s", symbol)

            with self._lock:
                self.grids[symbol] = grid

            dry_label = "[DRY-RUN]" if self.dry_run else "Ordenes colocadas"
            msg = (
                "<b>GRID ACTIVADO</b> - <code>%s</code>\n"
                "%s\n"
                "Centro: <code>%s</code>\n"
                "Niveles: %d (x%d buy + x%d sell)\n"
                "Espaciado: %s%% por nivel\n"
                "Capital: $%s x nivel\n"
                "%s\n"
                "%s"
            ) % (
                esc(symbol), "=" * 30,
                esc("%.6g" % px),
                GRID_LEVELS, half, half,
                esc(str(GRID_SPACING_PCT)),
                esc(str(GRID_USDT)),
                dry_label,
                now()
            )
            self.tg(msg)
            self.log.info("[GRID] Creado para %s @ %.6g", symbol, px)
            return {"result": "created", "symbol": symbol, "center": px, "levels": len(levels)}

        except Exception as ex:
            self.log.error("[GRID] create_grid(%s): %s", symbol, ex)
            return {"result": "error", "detail": str(ex)}

    def _place_grid_orders(self, e, symbol: str, grid: ActiveGrid):
        notional_per_level = GRID_USDT * LEVERAGE
        for level in grid.levels:
            try:
                qty = float(e.amount_to_precision(symbol, notional_per_level / level.price))
                if qty * level.price < 5:
                    continue
                order = e.create_order(
                    symbol, "limit", level.side, qty, level.price,
                    {"reduceOnly": False}
                )
                level.order_id = order.get("id", "")
                self.log.info("  [GRID] %s @ %.6g qty=%s", level.side, level.price, qty)
            except Exception as ex:
                self.log.warning("  [GRID] order %s@%.6g: %s", level.side, level.price, ex)

    # ── CANCELAR GRID ─────────────────────────────────────────────
    def cancel_grid(self, symbol: str) -> dict:
        with self._lock:
            if symbol not in self.grids:
                return {"result": "not_found"}
            grid = self.grids[symbol]
            grid.active = False

        try:
            if not self.dry_run:
                e = self.ex()
                e.cancel_all_orders(symbol)

            pnl = grid.total_pnl
            msg = (
                "<b>GRID CANCELADO</b> - <code>%s</code>\n"
                "Trades: %d\n"
                "PnL grid: $%s\n"
                "Activo desde: %s\n"
                "%s"
            ) % (
                esc(symbol),
                grid.trades_count,
                esc("%+.2f" % pnl),
                esc(grid.created_at),
                now()
            )
            self.tg(msg)
            self.log.info("[GRID] Cancelado %s. PnL=%.2f", symbol, pnl)

            with self._lock:
                del self.grids[symbol]

            return {"result": "cancelled", "pnl": pnl}

        except Exception as ex:
            self.log.error("[GRID] cancel_grid(%s): %s", symbol, ex)
            return {"result": "error", "detail": str(ex)}

    # ── MONITOR DEL GRID ─────────────────────────────────────────
    def monitor(self):
        with self._lock:
            symbols = list(self.grids.keys())

        for symbol in symbols:
            try:
                with self._lock:
                    if symbol not in self.grids:
                        continue
                    grid = self.grids[symbol]
                    if not grid.active:
                        continue

                if self.dry_run:
                    self._simulate_grid_fills(grid)
                    continue

                e = self.ex()
                for level in grid.levels:
                    if level.filled or not level.order_id:
                        continue
                    try:
                        order  = e.fetch_order(level.order_id, symbol)
                        status = str(order.get("status", "")).lower()
                        if status in ("closed", "filled"):
                            self._handle_grid_fill(e, symbol, grid, level, order)
                    except Exception as ex:
                        self.log.warning("[GRID] check order %s: %s", level.order_id, ex)

            except Exception as ex:
                self.log.warning("[GRID] monitor(%s): %s", symbol, ex)

    def _handle_grid_fill(self, e, symbol: str, grid: ActiveGrid,
                          level: GridLevel, order: dict):
        fill_price       = float(order.get("average") or order.get("price") or level.price)
        level.filled      = True
        grid.trades_count += 1

        profit_per_trade  = GRID_USDT * GRID_SPACING_PCT / 100 * LEVERAGE
        grid.total_pnl   += profit_per_trade
        level.pnl         = profit_per_trade

        self.log.info("[GRID] FILL: %s @ %.6g profit ~$%.3f",
                      level.side, fill_price, profit_per_trade)

        self.tg(
            "<b>GRID FILL</b> <code>%s</code>\n%s @ %s\nPnL grid acum: $%s\nTrades: %d" % (
                esc(symbol),
                esc(level.side.upper()),
                esc("%.6g" % fill_price),
                esc("%+.3f" % grid.total_pnl),
                grid.trades_count
            ),
            silent=True
        )

        try:
            notional = GRID_USDT * LEVERAGE
            qty      = float(e.amount_to_precision(symbol, notional / fill_price))

            if level.side == "buy":
                sell_price = round(fill_price * (1 + GRID_SPACING_PCT / 100), 8)
                sell_price = float(e.price_to_precision(symbol, sell_price))
                new_order  = e.create_order(symbol, "limit", "sell", qty, sell_price,
                                            {"reduceOnly": False})
                new_level  = GridLevel(price=sell_price, side="sell",
                                       order_id=new_order.get("id", ""))
            else:
                buy_price = round(fill_price * (1 - GRID_SPACING_PCT / 100), 8)
                buy_price = float(e.price_to_precision(symbol, buy_price))
                new_order = e.create_order(symbol, "limit", "buy", qty, buy_price,
                                           {"reduceOnly": False})
                new_level = GridLevel(price=buy_price, side="buy",
                                      order_id=new_order.get("id", ""))

            grid.levels.append(new_level)
            self.log.info("[GRID] Nueva orden contraria: %s @ %.6g",
                          new_level.side, new_level.price)

        except Exception as ex:
            self.log.warning("[GRID] orden contraria: %s", ex)

    def _simulate_grid_fills(self, grid: ActiveGrid):
        try:
            e  = self.ex()
            px = float(e.fetch_ticker(grid.symbol).get("last", 0))
            if px <= 0:
                return
            for level in grid.levels:
                if level.filled:
                    continue
                if level.side == "buy" and px <= level.price * 1.001:
                    profit = GRID_USDT * GRID_SPACING_PCT / 100
                    grid.total_pnl    += profit
                    grid.trades_count += 1
                    level.filled       = True
                    self.log.info("[GRID SIM] BUY fill @ %.6g +$%.4f", level.price, profit)
                elif level.side == "sell" and px >= level.price * 0.999:
                    profit = GRID_USDT * GRID_SPACING_PCT / 100
                    grid.total_pnl    += profit
                    grid.trades_count += 1
                    level.filled       = True
                    self.log.info("[GRID SIM] SELL fill @ %.6g +$%.4f", level.price, profit)
        except Exception as ex:
            self.log.warning("[GRID SIM] %s", ex)

    # ── AUTO-DETECCION DE REGIMEN ─────────────────────────────────
    def auto_manage(self, symbols: List[str]):
        if not GRID_MODE:
            return
        try:
            e = self.ex()
            for symbol in symbols:
                regime = self.regime.detect(symbol, e)
                with self._lock:
                    grid_active = symbol in self.grids and self.grids[symbol].active

                if regime == "sideways" and not grid_active:
                    self.log.info("[AUTO-GRID] %s: LATERAL detectado - activando grid", symbol)
                    self.create_grid(symbol)
                elif regime == "trending" and grid_active:
                    self.log.info("[AUTO-GRID] %s: TENDENCIA detectada - cancelando grid", symbol)
                    self.cancel_grid(symbol)

        except Exception as ex:
            self.log.warning("[AUTO-GRID] auto_manage: %s", ex)

    def get_status(self) -> dict:
        with self._lock:
            result = {}
            for sym, grid in self.grids.items():
                filled  = sum(1 for lv in grid.levels if lv.filled)
                pending = sum(1 for lv in grid.levels if not lv.filled)
                result[sym] = {
                    "active":         grid.active,
                    "center_price":   grid.center_price,
                    "spacing_pct":    grid.spacing_pct,
                    "total_pnl":      round(grid.total_pnl, 4),
                    "trades_count":   grid.trades_count,
                    "filled_levels":  filled,
                    "pending_levels": pending,
                    "created_at":     grid.created_at,
                    "dry_run":        self.dry_run,
                }
            return result

    def msg_grid_status(self, tg_func):
        status = self.get_status()
        if not status:
            tg_func("No hay grids activos.\nUsa /grid_start SYMBOL para crear uno.")
            return

        lines = ["<b>GRIDS ACTIVOS</b>\n" + "=" * 30]
        for sym, s in status.items():
            # Variables locales para evitar backslash dentro de expresiones
            centro  = esc("%.6g" % s["center_price"])
            pnl_str = esc("%+.4f" % s["total_pnl"])
            desde   = esc(s["created_at"])
            lines.append(
                "<code>%s</code>\n"
                "  Centro: %s\n"
                "  PnL: $%s\n"
                "  Trades: %d\n"
                "  Niveles: %d llenos / %d pendientes\n"
                "  Desde: %s" % (
                    esc(sym), centro, pnl_str,
                    s["trades_count"],
                    s["filled_levels"], s["pending_levels"],
                    desde
                )
            )
        tg_func("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
#  PARTE 4: WORKERS
# ═══════════════════════════════════════════════════════════════════
def start_adaptive_worker(adaptive_engine: AdaptiveEngine, tg_func, logger):
    def _worker():
        time.sleep(300)
        while True:
            try:
                adjustments = adaptive_engine.analyze()
                if adjustments:
                    details = "\n".join(
                        "  %s: %s" % (esc(a.get("type", "?")), esc(a.get("reason", "?")))
                        for a in adjustments
                    )
                    tg_func(
                        "<b>AUTO-APRENDIZAJE</b> - %d ajustes\n%s\nUsa /adaptive para ver detalles\n%s"
                        % (len(adjustments), details, now())
                    )
            except Exception as ex:
                logger.warning("adaptive_worker: %s", ex)
            time.sleep(3600)

    threading.Thread(target=_worker, daemon=True, name="adaptive").start()
    logger.info("Adaptive learning worker iniciado")


def start_grid_worker(grid_engine: GridEngine, logger):
    def _worker():
        time.sleep(60)
        while True:
            try:
                grid_engine.monitor()
            except Exception as ex:
                logger.warning("grid_worker monitor: %s", ex)
            time.sleep(30)

    threading.Thread(target=_worker, daemon=True, name="grid_monitor").start()
    logger.info("Grid monitor worker iniciado")


def start_regime_worker(grid_engine: GridEngine, symbols: List[str], logger):
    def _worker():
        time.sleep(120)
        while True:
            try:
                grid_engine.auto_manage(symbols)
            except Exception as ex:
                logger.warning("regime_worker: %s", ex)
            time.sleep(900)

    threading.Thread(target=_worker, daemon=True, name="regime").start()
    logger.info("Regime detection worker iniciado")


# ═══════════════════════════════════════════════════════════════════
#  PARTE 5: COMANDOS TELEGRAM NUEVOS
#  Agregar estos casos en el _tg_commands_worker de bot.py
# ═══════════════════════════════════════════════════════════════════
"""
COMANDOS NUEVOS A AGREGAR EN bot.py dentro de _tg_commands_worker:

elif text == "/adaptive":
    adaptive.msg_adaptive_report(tg)

elif text == "/grid":
    grid_engine.msg_grid_status(tg)

elif text.startswith("/grid_start"):
    parts = text.split()
    sym   = parts[1].upper() if len(parts) > 1 else GRID_SYMBOL
    tg("Creando grid para %s..." % sym)
    res = grid_engine.create_grid(sym)
    tg("Grid: %s" % str(res))

elif text.startswith("/grid_stop"):
    parts = text.split()
    sym   = parts[1].upper() if len(parts) > 1 else GRID_SYMBOL
    res   = grid_engine.cancel_grid(sym)
    tg("Grid cancelado: PnL=%+.4f" % res.get("pnl", 0))

elif text == "/regime":
    detector = RegimeDetector(log)
    for pair in ["BTC/USDT:USDT", "ETH/USDT:USDT"]:
        r = detector.detect(pair, ex())
        tg("%s: %s" % (pair, r.upper()))
"""


# ═══════════════════════════════════════════════════════════════════
#  PARTE 6: ENDPOINTS REST NUEVOS
#  Agregar en el Flask app de bot.py
# ═══════════════════════════════════════════════════════════════════
"""
ENDPOINTS NUEVOS A AGREGAR EN bot.py:

@app.route("/adaptive", methods=["GET"])
def adaptive_endpoint():
    return jsonify(adaptive.get_status())

@app.route("/grid", methods=["GET"])
def grid_status_endpoint():
    return jsonify(grid_engine.get_status())

@app.route("/grid/start/<raw_sym>", methods=["POST"])
def grid_start_endpoint(raw_sym: str):
    result = grid_engine.create_grid(raw_sym.upper())
    return jsonify(result)

@app.route("/grid/stop/<raw_sym>", methods=["POST"])
def grid_stop_endpoint(raw_sym: str):
    result = grid_engine.cancel_grid(raw_sym.upper())
    return jsonify(result)

@app.route("/regime/<raw_sym>", methods=["GET"])
def regime_endpoint(raw_sym: str):
    symbol   = raw_sym.upper()
    detector = RegimeDetector(log)
    regime   = detector.detect(symbol, ex())
    return jsonify({"symbol": symbol, "regime": regime})
"""


# ═══════════════════════════════════════════════════════════════════
#  PARTE 7: INSTRUCCIONES DE INTEGRACION
# ═══════════════════════════════════════════════════════════════════
INTEGRATION_GUIDE = """
╔══════════════════════════════════════════════════════╗
║  COMO INTEGRAR adaptive_engine.py EN bot.py          ║
╠══════════════════════════════════════════════════════╣
║  1. Subir adaptive_engine.py al mismo directorio     ║
║     que bot.py en GitHub                             ║
║  2. Al inicio de bot.py agregar:                     ║
║     from adaptive_engine import (                    ║
║         AdaptiveEngine, GridEngine,                  ║
║         start_adaptive_worker,                       ║
║         start_grid_worker,                           ║
║         start_regime_worker                          ║
║     )                                                ║
║  3. Despues de st = State() agregar:                 ║
║     adaptive     = AdaptiveEngine(st, log)           ║
║     grid_engine  = GridEngine(ex, st, log, tg)       ║
║  4. En la funcion startup() agregar:                 ║
║     start_adaptive_worker(adaptive, tg, log)         ║
║     start_grid_worker(grid_engine, log)              ║
║     start_regime_worker(grid_engine,                 ║
║         ["BTC/USDT:USDT"], log)                      ║
║  5. En _tg_commands_worker agregar los nuevos        ║
║     comandos (ver PARTE 5 de este archivo)           ║
║  6. En el Flask app agregar los endpoints            ║
║     (ver PARTE 6 de este archivo)                    ║
║  7. Variables nuevas en Railway:                     ║
║     ADAPTIVE_LEARNING = true                         ║
║     GRID_MODE         = true                         ║
║     GRID_LEVELS       = 6                            ║
║     GRID_SPACING_PCT  = 0.3                          ║
║     GRID_USDT         = 5                            ║
║     GRID_SYMBOL       = BTC/USDT:USDT               ║
║     REGIME_ADX_THRESH = 25                           ║
╚══════════════════════════════════════════════════════╝
"""

if __name__ == "__main__":
    print(INTEGRATION_GUIDE)
