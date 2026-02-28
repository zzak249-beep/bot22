"""
╔══════════════════════════════════════════════════════════════════════╗
║  SAIYAN ADAPTIVE ENGINE v1.0                                         ║
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
#  Analiza el historial de trades y ajusta parametros automaticamente
# ═══════════════════════════════════════════════════════════════════
class AdaptiveEngine:
    """
    Motor de auto-aprendizaje.
    Analiza closed_history cada hora y ajusta los parametros del bot
    basandose en patrones de perdidas y ganancias.
    """

    # Limites seguros para cada parametro (min, max)
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

        # Parametros actuales (se ajustan dinamicamente)
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
        self.analysis_interval   = 3600  # cada hora

        self.log.info("AdaptiveEngine iniciado — aprendizaje automatico activo")

    def get(self, param: str):
        """Obtiene el valor actual de un parametro (puede haber sido ajustado)."""
        return self.params.get(param)

    def _clamp(self, param: str, value) -> float:
        """Limita el valor dentro del rango seguro."""
        lo, hi = self.PARAM_LIMITS[param]
        return max(lo, min(hi, value))

    def _adjust(self, param: str, delta, reason: str):
        """Aplica un ajuste a un parametro y lo registra."""
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
        self.log.info(f"[ADAPTIVE] {param}: {old} -> {new} ({reason})")

    # ── ANALISIS PRINCIPAL ────────────────────────────────────────
    def analyze(self) -> List[dict]:
        """
        Analiza el historial de trades y retorna lista de ajustes realizados.
        Se llama automaticamente cada hora.
        """
        if not ADAPTIVE_LEARNING:
            return []

        now_ts = time.time()
        if now_ts - self.last_analysis_ts < self.analysis_interval:
            return []
        self.last_analysis_ts = now_ts

        history = self.st.closed_history
        if len(history) < 5:
            self.log.info("[ADAPTIVE] Menos de 5 trades — esperando mas datos")
            return []

        adjustments_made = []
        recent           = history[-20:]  # analizar ultimos 20 trades
        wins             = [t for t in recent if t.get("pnl", 0) >= 0]
        losses           = [t for t in recent if t.get("pnl", 0) < 0]
        n_recent         = len(recent)
        wr_recent        = len(wins) / n_recent * 100 if n_recent > 0 else 0

        self.log.info(f"[ADAPTIVE] Analizando {n_recent} trades recientes. WR: {wr_recent:.1f}%")

        # ── REGLA 1: Muchas perdidas seguidas → ser mas exigente ──
        consecutive = self._count_consecutive_losses(recent)
        if consecutive >= 3:
            self._adjust("MIN_CONFIRMATIONS", +1,
                         f"3+ perdidas seguidas ({consecutive})")
            self._adjust("COOLDOWN_MIN", +2,
                         f"3+ perdidas seguidas — mas cooldown")
            adjustments_made.append({"type": "safety", "reason": "consecutive_losses", "n": consecutive})

        # ── REGLA 2: WR bajo → filtros mas estrictos ─────────────
        if wr_recent < 35 and n_recent >= 10:
            self._adjust("VOL_MULT",       +0.1, f"WR bajo {wr_recent:.1f}% — mas volumen requerido")
            self._adjust("MAX_SPREAD_PCT", -0.02, f"WR bajo {wr_recent:.1f}% — menos spread")
            adjustments_made.append({"type": "filter", "reason": "low_wr", "wr": wr_recent})

        # ── REGLA 3: WR alto → relajar filtros un poco ───────────
        elif wr_recent > 70 and n_recent >= 10:
            self._adjust("VOL_MULT",   -0.05, f"WR alto {wr_recent:.1f}% — relajando vol")
            self._adjust("COOLDOWN_MIN", -1,  f"WR alto — menos cooldown")
            adjustments_made.append({"type": "relax", "reason": "high_wr", "wr": wr_recent})

        # ── REGLA 4: Analisis de longs vs shorts ─────────────────
        long_losses  = [t for t in losses if t.get("side") == "long"]
        short_losses = [t for t in losses if t.get("side") == "short"]

        if len(long_losses) > len(short_losses) * 2 and len(long_losses) >= 3:
            # Longs perdiendo mucho → mas exigente para longs
            self._adjust("RSI_OB", -2.0,
                         f"Longs perdiendo ({len(long_losses)} vs {len(short_losses)} shorts)")
            adjustments_made.append({"type": "side_bias", "reason": "long_losses"})

        elif len(short_losses) > len(long_losses) * 2 and len(short_losses) >= 3:
            self._adjust("RSI_OS", +2.0,
                         f"Shorts perdiendo ({len(short_losses)} vs {len(long_losses)} longs)")
            adjustments_made.append({"type": "side_bias", "reason": "short_losses"})

        # ── REGLA 5: SL tocandose muy frecuente → ajustar SL ─────
        sl_closes = [t for t in recent if "STOP" in t.get("reason", "").upper()
                     or "SL" in t.get("reason", "").upper()]
        if len(sl_closes) > n_recent * 0.4 and n_recent >= 8:
            self._adjust("SL_PCT", +0.1,
                         f"SL tocado {len(sl_closes)}/{n_recent} veces — ampliando SL")
            adjustments_made.append({"type": "sl_adjust", "reason": "frequent_sl"})

        # ── REGLA 6: Perdida promedio muy alta → reducir tamano ──
        if losses:
            avg_loss_pnl = abs(sum(t.get("pnl", 0) for t in losses) / len(losses))
            avg_win_pnl  = sum(t.get("pnl", 0) for t in wins) / len(wins) if wins else 0
            if avg_loss_pnl > avg_win_pnl * 1.5 and len(losses) >= 3:
                self._adjust("ATR_MIN_MULT", +0.05,
                             f"Perdida media ({avg_loss_pnl:.2f}) > ganancia media ({avg_win_pnl:.2f})")
                adjustments_made.append({"type": "rr_adjust", "reason": "bad_rr"})

        if adjustments_made:
            self.log.info(f"[ADAPTIVE] {len(adjustments_made)} ajustes aplicados")
        else:
            self.log.info("[ADAPTIVE] Sin ajustes necesarios — bot funcionando bien")

        return adjustments_made

    def _count_consecutive_losses(self, trades: list) -> int:
        """Cuenta perdidas consecutivas al final de la lista."""
        count = 0
        for t in reversed(trades):
            if t.get("pnl", 0) < 0:
                count += 1
            else:
                break
        return count

    def get_status(self) -> dict:
        """Retorna estado actual del motor adaptativo."""
        return {
            "enabled":             ADAPTIVE_LEARNING,
            "current_params":      self.params,
            "adjustments_made":    len(self.adjustment_history),
            "last_adjustments":    self.adjustment_history[-5:],
            "next_analysis_in_s":  max(0, int(self.analysis_interval - (time.time() - self.last_analysis_ts))),
        }

    def msg_adaptive_report(self, tg_func):
        """Envia resumen de ajustes por Telegram."""
        if not self.adjustment_history:
            tg_func("El bot no ha necesitado ajustes aun. Todo en orden.")
            return

        recent_adj = self.adjustment_history[-8:]
        lines = ["<b>REPORTE AUTO-APRENDIZAJE</b>\n" + "="*30]
        for a in reversed(recent_adj):
            lines.append(
                f"{esc(a['ts'])}\n"
                f"  {esc(a['param'])}: {esc(str(a['old']))} -> <b>{esc(str(a['new']))}</b>\n"
                f"  Razon: {esc(a['reason'])}"
            )
        lines.append(f"\nTotal ajustes historicos: {len(self.adjustment_history)}")
        tg_func("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
#  PARTE 2: MARKET REGIME DETECTOR
#  Detecta si el mercado esta en TENDENCIA o LATERAL
# ═══════════════════════════════════════════════════════════════════
class RegimeDetector:
    """
    Detecta el regimen de mercado usando ADX + Bollinger Band Width.

    TENDENCIA: ADX > 25 y precio alejado de medias
    LATERAL:   ADX < 25 y precio oscilando en rango estrecho
    """

    def __init__(self, logger):
        self.log    = logger
        self._cache: Dict[str, Tuple[str, float]] = {}  # symbol -> (regime, ts)
        self._TTL   = 300  # 5 minutos de cache

    def _calc_adx(self, highs: np.ndarray, lows: np.ndarray,
                  closes: np.ndarray, period: int = 14) -> float:
        """Calcula ADX (Average Directional Index). Rango 0-100."""
        if len(closes) < period * 2:
            return 25.0  # valor neutral

        # True Range
        trs, plus_dm, minus_dm = [], [], []
        for i in range(1, len(closes)):
            hl  = highs[i] - lows[i]
            hc  = abs(highs[i] - closes[i-1])
            lc  = abs(lows[i]  - closes[i-1])
            trs.append(max(hl, hc, lc))

            up   = highs[i]  - highs[i-1]
            down = lows[i-1] - lows[i]
            plus_dm.append(up   if up > down and up > 0   else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)

        def wilder_smooth(data, p):
            result = [sum(data[:p])]
            for v in data[p:]:
                result.append(result[-1] - result[-1]/p + v)
            return result

        atr14    = wilder_smooth(trs, period)
        plus_14  = wilder_smooth(plus_dm, period)
        minus_14 = wilder_smooth(minus_dm, period)

        dx_vals = []
        for a, p, m in zip(atr14, plus_14, minus_14):
            if a == 0: continue
            pdi = 100 * p / a
            mdi = 100 * m / a
            denom = pdi + mdi
            if denom == 0: continue
            dx_vals.append(100 * abs(pdi - mdi) / denom)

        if not dx_vals:
            return 25.0

        # ADX = media de DX
        adx = sum(dx_vals[-period:]) / min(period, len(dx_vals))
        return round(adx, 2)

    def _calc_bb_width(self, closes: np.ndarray, period: int = 20) -> float:
        """
        Bollinger Band Width = (BB_upper - BB_lower) / BB_mid
        Valores bajos = mercado comprimido = probable lateral
        """
        if len(closes) < period:
            return 0.05
        recent = closes[-period:]
        mid    = float(np.mean(recent))
        std    = float(np.std(recent))
        if mid == 0:
            return 0.05
        return round((4 * std) / mid, 4)

    def detect(self, symbol: str, exchange) -> str:
        """
        Detecta regimen de mercado para un simbolo.
        Retorna: 'trending' | 'sideways' | 'unknown'
        """
        # Cache
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

            # Logica de regimen:
            # ADX > threshold Y BB ancho → TENDENCIA
            # ADX < threshold Y BB estrecho → LATERAL
            if adx >= REGIME_ADX_THRESH and bb_width > 0.04:
                regime = "trending"
            elif adx < REGIME_ADX_THRESH and bb_width < 0.06:
                regime = "sideways"
            elif adx < REGIME_ADX_THRESH:
                regime = "sideways"
            else:
                regime = "trending"

            self._cache[symbol] = (regime, time.time())
            self.log.info(
                f"[REGIME] {symbol}: {regime.upper()} "
                f"(ADX={adx:.1f}, BBW={bb_width:.4f})"
            )
            return regime

        except Exception as e:
            self.log.warning(f"[REGIME] detect({symbol}): {e}")
            return "unknown"


# ═══════════════════════════════════════════════════════════════════
#  PARTE 3: GRID ENGINE
#  Opera en modo GRID cuando el mercado esta lateral
# ═══════════════════════════════════════════════════════════════════
@dataclass
class GridLevel:
    price:      float
    side:       str       # 'buy' | 'sell'
    order_id:   str  = ""
    filled:     bool = False
    pnl:        float = 0.0

@dataclass
class ActiveGrid:
    symbol:        str
    center_price:  float
    spacing_pct:   float
    levels:        List[GridLevel] = field(default_factory=list)
    total_pnl:     float = 0.0
    trades_count:  int   = 0
    created_at:    str   = field(default_factory=now)
    active:        bool  = True


class GridEngine:
    """
    Motor de Grid Trading para mercados laterales.

    Coloca ordenes de compra Y venta a intervalos regulares
    alrededor del precio actual. Cuando una se llena, coloca
    inmediatamente la contraria para capturar el rebote.

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
        self.ex      = exchange_fn   # funcion que retorna el exchange
        self.st      = state
        self.log     = logger
        self.tg      = tg_fn
        self.regime  = RegimeDetector(logger)
        self.grids:  Dict[str, ActiveGrid] = {}
        self._lock   = threading.Lock()
        self.dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    # ── CREAR GRID ────────────────────────────────────────────────
    def create_grid(self, symbol: str, center_price: Optional[float] = None) -> dict:
        """
        Crea un nuevo grid alrededor del precio actual.
        """
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

            # Crear niveles del grid
            half   = GRID_LEVELS // 2
            levels = []

            for i in range(1, half + 1):
                buy_price  = round(px * (1 - i * GRID_SPACING_PCT / 100), 8)
                sell_price = round(px * (1 + i * GRID_SPACING_PCT / 100), 8)
                levels.append(GridLevel(price=buy_price,  side="buy"))
                levels.append(GridLevel(price=sell_price, side="sell"))

            levels.sort(key=lambda l: l.price)

            grid = ActiveGrid(
                symbol=symbol,
                center_price=px,
                spacing_pct=GRID_SPACING_PCT,
                levels=levels
            )

            # Colocar ordenes reales
            if not self.dry_run:
                self._place_grid_orders(e, symbol, grid)
            else:
                self.log.info(f"[GRID DRY] Grid simulado para {symbol}")

            with self._lock:
                self.grids[symbol] = grid

            msg = (
                f"<b>GRID ACTIVADO</b> - <code>{esc(symbol)}</code>\n"
                f"{'='*30}\n"
                f"Centro: <code>{esc(f'{px:.6g}')}</code>\n"
                f"Niveles: {GRID_LEVELS} (x{half} buy + x{half} sell)\n"
                f"Espaciado: {GRID_SPACING_PCT}pct por nivel\n"
                f"Capital: ${GRID_USDT} x nivel\n"
                f"{'[DRY-RUN]' if self.dry_run else 'Ordenes colocadas'}\n"
                f"{now()}"
            )
            self.tg(msg)
            self.log.info(f"[GRID] Creado para {symbol} @ {px:.6g}")

            return {"result": "created", "symbol": symbol, "center": px, "levels": len(levels)}

        except Exception as ex:
            self.log.error(f"[GRID] create_grid({symbol}): {ex}")
            return {"result": "error", "detail": str(ex)}

    def _place_grid_orders(self, e, symbol: str, grid: ActiveGrid):
        """Coloca todas las ordenes del grid en el exchange."""
        notional_per_level = GRID_USDT * LEVERAGE
        for level in grid.levels:
            try:
                qty = float(e.amount_to_precision(symbol, notional_per_level / level.price))
                if qty * level.price < 5:
                    continue

                if level.side == "buy":
                    order = e.create_order(
                        symbol, "limit", "buy", qty, level.price,
                        {"reduceOnly": False}
                    )
                else:
                    order = e.create_order(
                        symbol, "limit", "sell", qty, level.price,
                        {"reduceOnly": False}
                    )
                level.order_id = order.get("id", "")
                self.log.info(f"  [GRID] {level.side} @ {level.price:.6g} qty={qty}")

            except Exception as ex:
                self.log.warning(f"  [GRID] order {level.side}@{level.price}: {ex}")

    # ── CANCELAR GRID ─────────────────────────────────────────────
    def cancel_grid(self, symbol: str) -> dict:
        """Cancela todas las ordenes del grid y cierra posiciones."""
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
                f"<b>GRID CANCELADO</b> - <code>{esc(symbol)}</code>\n"
                f"Trades: {grid.trades_count}\n"
                f"PnL grid: ${esc(f'{pnl:+.2f}')}\n"
                f"Activo desde: {esc(grid.created_at)}\n"
                f"{now()}"
            )
            self.tg(msg)
            self.log.info(f"[GRID] Cancelado {symbol}. PnL={pnl:.2f}")

            with self._lock:
                del self.grids[symbol]

            return {"result": "cancelled", "pnl": pnl}

        except Exception as ex:
            self.log.error(f"[GRID] cancel_grid({symbol}): {ex}")
            return {"result": "error", "detail": str(ex)}

    # ── MONITOR DEL GRID ─────────────────────────────────────────
    def monitor(self):
        """
        Monitorea ordenes del grid. Cuando una se llena,
        coloca inmediatamente la orden contraria para capturar el rebote.
        Llamar desde un thread periodicamente.
        """
        with self._lock:
            symbols = list(self.grids.keys())

        for symbol in symbols:
            try:
                with self._lock:
                    if symbol not in self.grids: continue
                    grid = self.grids[symbol]
                    if not grid.active: continue

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
                        self.log.warning(f"[GRID] check order {level.order_id}: {ex}")

            except Exception as ex:
                self.log.warning(f"[GRID] monitor({symbol}): {ex}")

    def _handle_grid_fill(self, e, symbol: str, grid: ActiveGrid,
                          level: GridLevel, order: dict):
        """Procesa un fill del grid y coloca la orden contraria."""
        fill_price  = float(order.get("average") or order.get("price") or level.price)
        level.filled = True
        grid.trades_count += 1

        # Calcular PnL estimado del rebote
        profit_per_trade = GRID_USDT * GRID_SPACING_PCT / 100 * LEVERAGE
        grid.total_pnl   += profit_per_trade
        level.pnl         = profit_per_trade

        self.log.info(
            f"[GRID] FILL: {level.side} @ {fill_price:.6g} "
            f"profit ~${profit_per_trade:.3f}"
        )

        # Notificar en Telegram (silencioso)
        self.tg(
            f"<b>GRID FILL</b> <code>{esc(symbol)}</code>\n"
            f"{esc(level.side.upper())} @ {esc(f'{fill_price:.6g}')}\n"
            f"PnL grid acum: ${esc(f'{grid.total_pnl:+.3f}')}\n"
            f"Trades: {grid.trades_count}",
            silent=True
        )

        # Colocar orden contraria
        try:
            notional = GRID_USDT * LEVERAGE
            qty      = float(e.amount_to_precision(symbol, notional / fill_price))
            if level.side == "buy":
                # Compra llenada → poner venta arriba
                sell_price = round(fill_price * (1 + GRID_SPACING_PCT / 100), 8)
                sell_price = float(e.price_to_precision(symbol, sell_price))
                new_order  = e.create_order(symbol, "limit", "sell", qty, sell_price,
                                             {"reduceOnly": False})
                new_level  = GridLevel(price=sell_price, side="sell",
                                       order_id=new_order.get("id", ""))
            else:
                # Venta llenada → poner compra abajo
                buy_price = round(fill_price * (1 - GRID_SPACING_PCT / 100), 8)
                buy_price = float(e.price_to_precision(symbol, buy_price))
                new_order = e.create_order(symbol, "limit", "buy", qty, buy_price,
                                            {"reduceOnly": False})
                new_level = GridLevel(price=buy_price, side="buy",
                                      order_id=new_order.get("id", ""))

            grid.levels.append(new_level)
            self.log.info(
                f"[GRID] Nueva orden contraria: {new_level.side} @ {new_level.price:.6g}"
            )

        except Exception as ex:
            self.log.warning(f"[GRID] orden contraria: {ex}")

    def _simulate_grid_fills(self, grid: ActiveGrid):
        """Simula fills en modo DRY-RUN para testing."""
        try:
            e  = self.ex()
            px = float(e.fetch_ticker(grid.symbol).get("last", 0))
            if px <= 0:
                return
            for level in grid.levels:
                if level.filled:
                    continue
                # Simular fill si el precio cruzo el nivel
                if level.side == "buy" and px <= level.price * 1.001:
                    profit = GRID_USDT * GRID_SPACING_PCT / 100
                    grid.total_pnl  += profit
                    grid.trades_count += 1
                    level.filled = True
                    self.log.info(f"[GRID SIM] BUY fill @ {level.price:.6g} +${profit:.4f}")
                elif level.side == "sell" and px >= level.price * 0.999:
                    profit = GRID_USDT * GRID_SPACING_PCT / 100
                    grid.total_pnl  += profit
                    grid.trades_count += 1
                    level.filled = True
                    self.log.info(f"[GRID SIM] SELL fill @ {level.price:.6g} +${profit:.4f}")
        except Exception as ex:
            self.log.warning(f"[GRID SIM] {ex}")

    # ── AUTO-DETECCION DE REGIMEN ─────────────────────────────────
    def auto_manage(self, symbols: List[str]):
        """
        Detecta automaticamente el regimen de mercado de cada simbolo
        y activa/desactiva el grid en consecuencia.

        Llamar periodicamente desde un worker.
        """
        if not GRID_MODE:
            return

        try:
            e = self.ex()
            for symbol in symbols:
                regime = self.regime.detect(symbol, e)
                with self._lock:
                    grid_active = symbol in self.grids and self.grids[symbol].active

                if regime == "sideways" and not grid_active:
                    self.log.info(f"[AUTO-GRID] {symbol}: LATERAL detectado → activando grid")
                    self.create_grid(symbol)

                elif regime == "trending" and grid_active:
                    self.log.info(f"[AUTO-GRID] {symbol}: TENDENCIA detectada → cancelando grid")
                    self.cancel_grid(symbol)

        except Exception as ex:
            self.log.warning(f"[AUTO-GRID] auto_manage: {ex}")

    def get_status(self) -> dict:
        """Retorna estado de todos los grids activos."""
        with self._lock:
            result = {}
            for sym, grid in self.grids.items():
                filled  = sum(1 for l in grid.levels if l.filled)
                pending = sum(1 for l in grid.levels if not l.filled)
                result[sym] = {
                    "active":        grid.active,
                    "center_price":  grid.center_price,
                    "spacing_pct":   grid.spacing_pct,
                    "total_pnl":     round(grid.total_pnl, 4),
                    "trades_count":  grid.trades_count,
                    "filled_levels": filled,
                    "pending_levels": pending,
                    "created_at":    grid.created_at,
                    "dry_run":       self.dry_run,
                }
            return result

    def msg_grid_status(self, tg_func):
        """Envia estado de los grids por Telegram."""
        status = self.get_status()
        if not status:
            tg_func("No hay grids activos.\nUsa /grid_start SYMBOL para crear uno.")
            return

        lines = ["<b>GRIDS ACTIVOS</b>\n" + "="*30]
        for sym, s in status.items():
            lines.append(
                f"<code>{esc(sym)}</code>\n"
                f"  Centro: {esc(f'{s[\"center_price\"]:.6g}')}\n"
                f"  PnL: ${esc(f'{s[\"total_pnl\"]:+.4f}')}\n"
                f"  Trades: {s['trades_count']}\n"
                f"  Niveles: {s['filled_levels']} llenos / {s['pending_levels']} pendientes\n"
                f"  Desde: {esc(s['created_at'])}"
            )
        tg_func("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
#  PARTE 4: WORKERS (threads para integrar en bot.py)
# ═══════════════════════════════════════════════════════════════════
def start_adaptive_worker(adaptive_engine: AdaptiveEngine, tg_func, logger):
    """
    Worker que ejecuta el analisis adaptativo cada hora.
    Agregar en bot.py:
        threading.Thread(target=start_adaptive_worker,
                        args=(adaptive, tg, log), daemon=True).start()
    """
    def _worker():
        time.sleep(300)  # esperar 5 min al inicio
        while True:
            try:
                adjustments = adaptive_engine.analyze()
                if adjustments:
                    # Notificar por Telegram los ajustes
                    details = "\n".join(
                        f"  {esc(a.get('type','?'))}: {esc(a.get('reason','?'))}"
                        for a in adjustments
                    )
                    tg_func(
                        f"<b>AUTO-APRENDIZAJE</b> - {len(adjustments)} ajustes\n"
                        f"{details}\n"
                        f"Usa /adaptive para ver detalles\n"
                        f"{now()}"
                    )
            except Exception as ex:
                logger.warning(f"adaptive_worker: {ex}")
            time.sleep(3600)  # cada hora

    threading.Thread(target=_worker, daemon=True, name="adaptive").start()
    logger.info("Adaptive learning worker iniciado")


def start_grid_worker(grid_engine: GridEngine, logger):
    """
    Worker que monitorea y gestiona los grids cada 30 segundos.
    Agregar en bot.py:
        threading.Thread(target=start_grid_worker,
                        args=(grid_engine, log), daemon=True).start()
    """
    def _worker():
        time.sleep(60)  # esperar 1 min al inicio
        while True:
            try:
                grid_engine.monitor()
            except Exception as ex:
                logger.warning(f"grid_worker monitor: {ex}")
            time.sleep(30)

    threading.Thread(target=_worker, daemon=True, name="grid_monitor").start()
    logger.info("Grid monitor worker iniciado")


def start_regime_worker(grid_engine: GridEngine, symbols: List[str], logger):
    """
    Worker que detecta el regimen de mercado cada 15 minutos
    y activa/desactiva grids automaticamente.
    """
    def _worker():
        time.sleep(120)  # esperar 2 min al inicio
        while True:
            try:
                grid_engine.auto_manage(symbols)
            except Exception as ex:
                logger.warning(f"regime_worker: {ex}")
            time.sleep(900)  # cada 15 min

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
    tg(f"Creando grid para {sym}...")
    res = grid_engine.create_grid(sym)
    tg(f"Grid: {str(res)}")

elif text.startswith("/grid_stop"):
    parts = text.split()
    sym   = parts[1].upper() if len(parts) > 1 else GRID_SYMBOL
    res   = grid_engine.cancel_grid(sym)
    tg(f"Grid cancelado: PnL={res.get('pnl',0):+.4f}")

elif text == "/regime":
    import ccxt
    # Mostrar regimen actual de los pares principales
    detector = RegimeDetector(log)
    for pair in ["BTC/USDT:USDT", "ETH/USDT:USDT"]:
        r = detector.detect(pair, ex())
        tg(f"{pair}: {r.upper()}")
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
    symbol  = raw_sym.upper()
    detector = RegimeDetector(log)
    regime  = detector.detect(symbol, ex())
    return jsonify({"symbol": symbol, "regime": regime})
"""


# ═══════════════════════════════════════════════════════════════════
#  PARTE 7: INSTRUCCIONES DE INTEGRACION CON bot.py
# ═══════════════════════════════════════════════════════════════════
INTEGRATION_GUIDE = """
╔══════════════════════════════════════════════════════╗
║  COMO INTEGRAR adaptive_engine.py EN bot.py          ║
╠══════════════════════════════════════════════════════╣
║                                                      ║
║  1. Subir adaptive_engine.py al mismo directorio     ║
║     que bot.py en GitHub                             ║
║                                                      ║
║  2. Al inicio de bot.py agregar:                     ║
║     from adaptive_engine import (                    ║
║         AdaptiveEngine, GridEngine,                  ║
║         start_adaptive_worker,                       ║
║         start_grid_worker,                           ║
║         start_regime_worker                          ║
║     )                                                ║
║                                                      ║
║  3. Despues de st = State() agregar:                 ║
║     adaptive     = AdaptiveEngine(st, log)           ║
║     grid_engine  = GridEngine(ex, st, log, tg)       ║
║                                                      ║
║  4. En la funcion startup() agregar:                 ║
║     start_adaptive_worker(adaptive, tg, log)         ║
║     start_grid_worker(grid_engine, log)              ║
║     start_regime_worker(grid_engine,                 ║
║         ["BTC/USDT:USDT"], log)                      ║
║                                                      ║
║  5. En _tg_commands_worker agregar los nuevos        ║
║     comandos (ver PARTE 5 de este archivo)           ║
║                                                      ║
║  6. En el Flask app agregar los endpoints            ║
║     (ver PARTE 6 de este archivo)                    ║
║                                                      ║
║  7. Variables nuevas en Railway:                     ║
║     ADAPTIVE_LEARNING = true                         ║
║     GRID_MODE         = true                         ║
║     GRID_LEVELS       = 6                            ║
║     GRID_SPACING_PCT  = 0.3                          ║
║     GRID_USDT         = 5                            ║
║     GRID_SYMBOL       = BTC/USDT:USDT               ║
║     REGIME_ADX_THRESH = 25                           ║
║                                                      ║
╚══════════════════════════════════════════════════════╝
"""

if __name__ == "__main__":
    print(INTEGRATION_GUIDE)
