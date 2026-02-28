"""
╔══════════════════════════════════════════════════════════════════════╗
║  SAIYAN AURA FUSION BOT  v4.0  ── MULTI-CONFIRMATION EDITION        ║
║  TradingView Webhook → BingX Perpetual Futures + Telegram            ║
║  Railway 24/7                                                        ║
╠══════════════════════════════════════════════════════════════════════╣
║  NUEVAS CONFIRMACIONES v4.0:                                         ║
║  ✦ MULTI-SEÑAL: requiere N confirmaciones en ventana de tiempo       ║
║  ✦ FILTRO EMA: solo opera a favor del trend (EMA20 vs EMA50)         ║
║  ✦ FILTRO RSI: evita entrar en extremos (sobrecompra/sobreventa)     ║
║  ✦ FILTRO VOLUMEN: el volumen debe superar media de N velas          ║
║  ✦ FILTRO SPREAD: rechaza si bid/ask spread es demasiado ancho       ║
║  ✦ FILTRO VOLATILIDAD ATR: evita mercados sin movimiento útil        ║
║  ✦ FILTRO FUNDING RATE: no va en contra de funding extremo           ║
║  ✦ FILTRO SESIÓN: evita horas de baja liquidez configurables         ║
║  ✦ CONFIRMACIÓN DE ORDEN: verifica fill real antes de registrar      ║
║  ✦ SIZING DINÁMICO ATR: ajusta tamaño de posición a volatilidad      ║
║  ✦ SCORE SYSTEM: puntuación de confirmaciones visible en Telegram    ║
║  ✦ MODO DRY-RUN: simula trades sin enviar órdenes reales             ║
╠══════════════════════════════════════════════════════════════════════╣
║  VARIABLES OBLIGATORIAS:                                             ║
║    BINGX_API_KEY  · BINGX_API_SECRET                                 ║
║    TELEGRAM_BOT_TOKEN  · TELEGRAM_CHAT_ID                            ║
║    WEBHOOK_SECRET                                                    ║
║                                                                      ║
║  VARIABLES OPCIONALES — CONFIRMACIONES:                              ║
║    MIN_CONFIRMATIONS   def: 2  (señales requeridas en ventana)       ║
║    CONFIRM_WINDOW_SEC  def: 60 (segundos de ventana multi-señal)     ║
║    EMA_FAST             def: 20 (EMA rápida para trend)              ║
║    EMA_SLOW             def: 50 (EMA lenta para trend)               ║
║    RSI_PERIOD           def: 14                                      ║
║    RSI_OB               def: 72 (evitar LONG si RSI > este valor)   ║
║    RSI_OS               def: 28 (evitar SHORT si RSI < este valor)  ║
║    VOL_MULT             def: 1.2 (volumen mínimo = media * mult)     ║
║    VOL_LOOKBACK         def: 20 (velas para media de volumen)        ║
║    MAX_SPREAD_PCT       def: 0.15 (% spread máximo aceptado)         ║
║    ATR_PERIOD           def: 14                                      ║
║    ATR_MIN_MULT         def: 0.3 (ATR mínimo = precio * mult / 100) ║
║    ATR_MAX_MULT         def: 5.0 (ATR máximo = precio * mult / 100) ║
║    FUNDING_LIMIT        def: 0.05 (% funding rate extremo)           ║
║    BLOCK_HOURS          def: ""  ("0,1,2" horas UTC a bloquear)      ║
║    ATR_SIZING           def: false (sizing dinámico por ATR)         ║
║    DRY_RUN              def: false (simular sin ejecutar)            ║
║                                                                      ║
║  VARIABLES OPCIONALES — TRADING:                                     ║
║    FIXED_USDT          def: 20                                       ║
║    LEVERAGE            def: 5                                        ║
║    MAX_OPEN_TRADES     def: 5                                        ║
║    MAX_DRAWDOWN        def: 15                                       ║
║    DAILY_LOSS_LIMIT    def: 8                                        ║
║    TP1_PCT             def: 1.0                                      ║
║    TP2_PCT             def: 1.8                                      ║
║    TP3_PCT             def: 3.0                                      ║
║    SL_PCT              def: 0.8                                      ║
║    TRAILING_PCT        def: 0.5                                      ║
║    TRAILING_ACTIVATE   def: 1.0                                      ║
║    HEARTBEAT_MIN       def: 60                                       ║
║    COOLDOWN_MIN        def: 5                                        ║
║    ANTI_SPIKE_PCT      def: 3.0                                      ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os, time, logging, csv, threading, json
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from collections import deque, defaultdict

import requests
import ccxt
import numpy as np
from flask import Flask, request, jsonify, Response

# ─────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("saiyan")

# ─────────────────────────────────────────────────────────────────
# CONFIG — TRADING
# ─────────────────────────────────────────────────────────────────
API_KEY           = os.environ.get("BINGX_API_KEY",       "")
API_SECRET        = os.environ.get("BINGX_API_SECRET",    "")
TG_TOKEN          = os.environ.get("TELEGRAM_BOT_TOKEN",  "")
TG_CHAT_ID        = os.environ.get("TELEGRAM_CHAT_ID",    "")
WEBHOOK_SECRET    = os.environ.get("WEBHOOK_SECRET",      "saiyan2024")

FIXED_USDT        = float(os.environ.get("FIXED_USDT",        "20.0"))
LEVERAGE          = int  (os.environ.get("LEVERAGE",           "5"))
MAX_OPEN_TRADES   = int  (os.environ.get("MAX_OPEN_TRADES",    "5"))
CB_DD             = float(os.environ.get("MAX_DRAWDOWN",       "15.0"))
DAILY_LOSS_PCT    = float(os.environ.get("DAILY_LOSS_LIMIT",   "8.0"))
TP1_PCT           = float(os.environ.get("TP1_PCT",            "1.0"))
TP2_PCT           = float(os.environ.get("TP2_PCT",            "1.8"))
TP3_PCT           = float(os.environ.get("TP3_PCT",            "3.0"))
SL_PCT            = float(os.environ.get("SL_PCT",             "0.8"))
TRAILING_PCT      = float(os.environ.get("TRAILING_PCT",       "0.5"))
TRAILING_ACTIVATE = float(os.environ.get("TRAILING_ACTIVATE",  "1.0"))
HEARTBEAT_MIN     = int  (os.environ.get("HEARTBEAT_MIN",      "60"))
COOLDOWN_MIN      = int  (os.environ.get("COOLDOWN_MIN",       "5"))
ANTI_SPIKE_PCT    = float(os.environ.get("ANTI_SPIKE_PCT",     "3.0"))
PORT              = int  (os.environ.get("PORT",               "8080"))

# ─────────────────────────────────────────────────────────────────
# CONFIG — CONFIRMACIONES
# ─────────────────────────────────────────────────────────────────
MIN_CONFIRMATIONS = int  (os.environ.get("MIN_CONFIRMATIONS",   "2"))
CONFIRM_WINDOW    = int  (os.environ.get("CONFIRM_WINDOW_SEC",  "60"))
EMA_FAST          = int  (os.environ.get("EMA_FAST",            "20"))
EMA_SLOW          = int  (os.environ.get("EMA_SLOW",            "50"))
RSI_PERIOD        = int  (os.environ.get("RSI_PERIOD",          "14"))
RSI_OB            = float(os.environ.get("RSI_OB",              "72.0"))
RSI_OS            = float(os.environ.get("RSI_OS",              "28.0"))
VOL_MULT          = float(os.environ.get("VOL_MULT",            "1.2"))
VOL_LOOKBACK      = int  (os.environ.get("VOL_LOOKBACK",        "20"))
MAX_SPREAD_PCT    = float(os.environ.get("MAX_SPREAD_PCT",      "0.15"))
ATR_PERIOD        = int  (os.environ.get("ATR_PERIOD",          "14"))
ATR_MIN_MULT      = float(os.environ.get("ATR_MIN_MULT",        "0.3"))
ATR_MAX_MULT      = float(os.environ.get("ATR_MAX_MULT",        "5.0"))
FUNDING_LIMIT     = float(os.environ.get("FUNDING_LIMIT",       "0.05"))
ATR_SIZING        = os.environ.get("ATR_SIZING",  "false").lower() == "true"
DRY_RUN           = os.environ.get("DRY_RUN",    "false").lower() == "true"

# Horas bloqueadas UTC (ej: "0,1,2,3" para madrugada)
_block_hours_raw  = os.environ.get("BLOCK_HOURS", "")
BLOCK_HOURS: List[int] = (
    [int(h.strip()) for h in _block_hours_raw.split(",") if h.strip().isdigit()]
    if _block_hours_raw.strip() else []
)

STATE_PATH = "/tmp/saiyan_state.json"
CSV_PATH   = "/tmp/saiyan_trades.csv"
_lock      = threading.Lock()

# ─────────────────────────────────────────────────────────────────
# SIGNAL BUFFER — ventana multi-confirmación
# ─────────────────────────────────────────────────────────────────
@dataclass
class PendingSignal:
    side:       str          # "long" | "short"
    timestamps: List[float]  = field(default_factory=list)

# symbol → PendingSignal
_signal_buffer: Dict[str, PendingSignal] = {}
_signal_lock   = threading.Lock()

def _register_signal(symbol: str, side: str) -> int:
    """
    Registra una señal entrante y retorna cuántas confirmaciones acumuladas hay
    dentro de la ventana de tiempo. Limpia señales del lado contrario.
    """
    now_ts = time.time()
    with _signal_lock:
        pending = _signal_buffer.get(symbol)
        # Si la señal es del lado opuesto, resetear
        if pending and pending.side != side:
            _signal_buffer[symbol] = PendingSignal(side=side, timestamps=[now_ts])
            return 1
        if pending is None:
            _signal_buffer[symbol] = PendingSignal(side=side, timestamps=[now_ts])
            return 1
        # Filtrar timestamps dentro de la ventana
        pending.timestamps = [
            ts for ts in pending.timestamps
            if now_ts - ts <= CONFIRM_WINDOW
        ]
        pending.timestamps.append(now_ts)
        return len(pending.timestamps)

def _clear_signal(symbol: str):
    with _signal_lock:
        _signal_buffer.pop(symbol, None)

# ─────────────────────────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────────────────────────
@dataclass
class Trade:
    symbol:        str
    side:          str
    entry_price:   float
    contracts:     float
    tp1:           float
    tp2:           float
    tp3:           float
    sl:            float
    entry_time:    str
    tp1_hit:       bool  = False
    tp2_hit:       bool  = False
    sl_at_be:      bool  = False
    trailing_on:   bool  = False
    trailing_high: float = 0.0
    confirm_score: int   = 0     # cuántas confirmaciones pasaron
    dry_run:       bool  = False # simulado

@dataclass
class State:
    trades:           Dict[str, Trade] = field(default_factory=dict)
    closed_history:   List[dict]       = field(default_factory=list)
    cooldowns:        Dict[str, float] = field(default_factory=dict)
    rejected_signals: int  = 0    # señales bloqueadas por filtros
    wins:             int   = 0
    losses:           int   = 0
    gross_profit:     float = 0.0
    gross_loss:       float = 0.0
    peak_equity:      float = 0.0
    total_pnl:        float = 0.0
    daily_pnl:        float = 0.0
    daily_reset_ts:   float = field(default_factory=time.time)
    start_time:       float = field(default_factory=time.time)
    total_trades:     int   = 0
    paused:           bool  = False
    max_dd_real:      float = 0.0
    best_trade:       float = 0.0
    worst_trade:      float = 0.0
    tg_update_offset: int   = 0

    def n(self): return len(self.trades)

    def wr(self):
        t = self.wins + self.losses
        return self.wins / t * 100 if t else 0.0

    def pf(self):
        return self.gross_profit / self.gross_loss if self.gross_loss else 0.0

    def avg_win(self):
        return self.gross_profit / self.wins if self.wins else 0.0

    def avg_loss(self):
        return self.gross_loss / self.losses if self.losses else 0.0

    def expectancy(self):
        wr = self.wr() / 100
        return wr * self.avg_win() - (1 - wr) * self.avg_loss()

    def cb(self):
        if self.peak_equity <= 0: return False
        dd = abs(self.total_pnl) / self.peak_equity * 100
        return self.total_pnl < 0 and dd >= CB_DD

    def daily_hit(self):
        if self.peak_equity <= 0: return False
        return self.daily_pnl < 0 and abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LOSS_PCT

    def in_cooldown(self, symbol: str) -> bool:
        last = self.cooldowns.get(symbol, 0)
        return (time.time() - last) < COOLDOWN_MIN * 60

    def reset_daily(self):
        now_ts = time.time()
        if now_ts - self.daily_reset_ts > 86400:
            self.daily_pnl      = 0.0
            self.daily_reset_ts = now_ts
            log.info("Daily PnL reset")

    def record_close(self, pnl: float, symbol: str):
        self.total_trades += 1
        if pnl >= 0:
            self.wins        += 1
            self.gross_profit += pnl
            self.best_trade   = max(self.best_trade, pnl)
        else:
            self.losses      += 1
            self.gross_loss  += abs(pnl)
            self.worst_trade  = min(self.worst_trade, pnl)
        self.total_pnl  += pnl
        self.daily_pnl  += pnl
        self.cooldowns[symbol] = time.time()
        if self.peak_equity > 0:
            dd = abs(self.total_pnl) / self.peak_equity * 100
            if self.total_pnl < 0 and dd > self.max_dd_real:
                self.max_dd_real = dd
        cur_equity = self.peak_equity + self.total_pnl
        if cur_equity > self.peak_equity:
            self.peak_equity = cur_equity

    def uptime(self) -> str:
        secs = int(time.time() - self.start_time)
        h, m = divmod(secs // 60, 60)
        d, h = divmod(h, 24)
        if d > 0: return f"{d}d {h}h {m}m"
        return f"{h}h {m}m"

    def to_persist(self) -> dict:
        return {
            "wins": self.wins, "losses": self.losses,
            "gross_profit": self.gross_profit, "gross_loss": self.gross_loss,
            "total_pnl": self.total_pnl, "peak_equity": self.peak_equity,
            "total_trades": self.total_trades, "best_trade": self.best_trade,
            "worst_trade": self.worst_trade, "max_dd_real": self.max_dd_real,
            "closed_history": self.closed_history[-50:],
            "cooldowns": self.cooldowns,
            "rejected_signals": self.rejected_signals,
        }

    def load_persist(self, d: dict):
        self.wins             = d.get("wins", 0)
        self.losses           = d.get("losses", 0)
        self.gross_profit     = d.get("gross_profit", 0.0)
        self.gross_loss       = d.get("gross_loss", 0.0)
        self.total_pnl        = d.get("total_pnl", 0.0)
        self.peak_equity      = d.get("peak_equity", 0.0)
        self.total_trades     = d.get("total_trades", 0)
        self.best_trade       = d.get("best_trade", 0.0)
        self.worst_trade      = d.get("worst_trade", 0.0)
        self.max_dd_real      = d.get("max_dd_real", 0.0)
        self.closed_history   = d.get("closed_history", [])
        self.cooldowns        = d.get("cooldowns", {})
        self.rejected_signals = d.get("rejected_signals", 0)

st = State()

# ─────────────────────────────────────────────────────────────────
# PERSISTENCIA
# ─────────────────────────────────────────────────────────────────
def save_state():
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(st.to_persist(), f)
    except Exception as e:
        log.warning(f"save_state: {e}")

def load_state():
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f:
                st.load_persist(json.load(f))
            log.info(f"Estado restaurado: {st.total_trades} trades")
    except Exception as e:
        log.warning(f"load_state: {e}")

# ─────────────────────────────────────────────────────────────────
# EXCHANGE
# ─────────────────────────────────────────────────────────────────
_ex: Optional[ccxt.Exchange] = None
_ex_lock = threading.Lock()

def ex() -> ccxt.Exchange:
    global _ex
    with _ex_lock:
        if _ex is None:
            _ex = ccxt.bingx({
                "apiKey":  API_KEY,
                "secret":  API_SECRET,
                "options": {"defaultType": "swap", "defaultMarginMode": "cross"},
                "enableRateLimit": True,
            })
            _ex.load_markets()
            log.info("BingX conectado ✓")
        return _ex

def ex_call(fn, *args, retries=3, **kwargs):
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except ccxt.NetworkError as e:
            wait = 2 ** attempt
            log.warning(f"NetworkError ({attempt+1}/{retries}): {e} — retry {wait}s")
            time.sleep(wait)
        except ccxt.RateLimitExceeded:
            log.warning("RateLimit — waiting 15s")
            time.sleep(15)
        except ccxt.AuthenticationError as e:
            log.error(f"AuthError: {e}")
            raise
        except Exception as e:
            if attempt == retries - 1:
                raise
            log.warning(f"Error ({attempt+1}/{retries}): {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed after {retries} retries")

def sym(raw: str) -> str:
    r = raw.upper().strip()
    if ":" in r: return r
    if "/" in r:
        b, q = r.split("/", 1)
        return f"{b}/{q.split(':')[0]}:{q.split(':')[0]}"
    if r.endswith("USDT"): return f"{r[:-4]}/USDT:USDT"
    return f"{r}/USDT:USDT"

def price_with_spread(symbol: str) -> Tuple[float, float, float]:
    """Retorna (last, bid, ask)."""
    ticker = ex_call(ex().fetch_ticker, symbol)
    last   = float(ticker["last"])
    bid    = float(ticker.get("bid") or last)
    ask    = float(ticker.get("ask") or last)
    return last, bid, ask

def price(symbol: str) -> float:
    last, _, _ = price_with_spread(symbol)
    return last

def price_validated(symbol: str) -> float:
    last, bid, ask = price_with_spread(symbol)
    mid = (bid + ask) / 2
    if mid > 0 and abs(last - mid) / mid * 100 > ANTI_SPIKE_PCT:
        raise ValueError(
            f"Anti-spike: last={last:.6g} vs mid={mid:.6g} "
            f"({abs(last-mid)/mid*100:.1f}%)"
        )
    return last

def balance() -> float:
    b    = ex_call(ex().fetch_balance)
    usdt = b.get("USDT", {})
    free = float(usdt.get("free", 0) or 0)
    if free == 0:
        for item in b.get("info", {}).get("data", {}).get("balance", []):
            if item.get("asset") == "USDT":
                free = float(item.get("availableMargin", 0) or 0)
                break
    return free

def position(symbol: str) -> Optional[dict]:
    try:
        for p in ex_call(ex().fetch_positions, [symbol]):
            contracts = abs(float(
                p.get("contracts") or
                p.get("info", {}).get("positionAmt", 0) or 0
            ))
            if contracts > 0:
                return p
    except Exception as e:
        log.warning(f"position({symbol}): {e}")
    return None

def set_lev(symbol: str):
    try:
        ex_call(ex().set_leverage, LEVERAGE, symbol, {"marginMode": "cross"})
        log.info(f"  [{symbol}] Leverage {LEVERAGE}x ✓")
    except Exception as e:
        log.warning(f"  [{symbol}] set_leverage: {e}")

def cancel_all_orders_safe(symbol: str):
    try:
        ex_call(ex().cancel_all_orders, symbol)
        return
    except Exception:
        pass
    try:
        for o in ex_call(ex().fetch_open_orders, symbol):
            try: ex_call(ex().cancel_order, o["id"], symbol)
            except Exception as e2: log.warning(f"cancel {o['id']}: {e2}")
    except Exception as e:
        log.warning(f"cancel_all_orders_safe({symbol}): {e}")

def place_tp_limit(e, symbol: str, close_side: str, qty: float, tp_price: float) -> bool:
    try:
        tp = float(e.price_to_precision(symbol, tp_price))
        q  = float(e.amount_to_precision(symbol, qty))
        ex_call(e.create_order, symbol, "limit", close_side, q, tp, {"reduceOnly": True})
        log.info(f"  TP limit @ {tp:.6g} qty={q} ✓")
        return True
    except Exception as err:
        log.warning(f"  place_tp_limit: {err}")
        return False

def place_sl_stop(e, symbol: str, close_side: str, qty: float, stop_price: float) -> bool:
    try:
        sp = float(e.price_to_precision(symbol, stop_price))
        q  = float(e.amount_to_precision(symbol, qty))
        params = {"reduceOnly": True, "stopPrice": sp}
        for order_type in ["stop_market", "stop"]:
            try:
                ex_call(e.create_order, symbol, order_type, close_side, q, None, params)
                log.info(f"  SL {order_type} @ {sp:.6g} qty={q} ✓")
                return True
            except Exception as te:
                log.warning(f"  SL {order_type}: {te}")
        return False
    except Exception as err:
        log.warning(f"  place_sl_stop: {err}")
        return False

def update_sl_order(t: Trade, new_sl: float):
    if t.dry_run: return
    try:
        close_side = "sell" if t.side == "long" else "buy"
        cancel_all_orders_safe(t.symbol)
        place_sl_stop(ex(), t.symbol, close_side, t.contracts, new_sl)
    except Exception as e:
        log.warning(f"update_sl_order({t.symbol}): {e}")

# ─────────────────────────────────────────────────────────────────
# ── INDICADORES TÉCNICOS ────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────
def _fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 100) -> Optional[np.ndarray]:
    """
    Descarga OHLCV. Retorna array shape (N, 6): ts,o,h,l,c,vol
    Retorna None si falla.
    """
    try:
        data = ex_call(ex().fetch_ohlcv, symbol, timeframe, limit=limit)
        if not data or len(data) < 20:
            return None
        return np.array(data, dtype=float)
    except Exception as e:
        log.warning(f"fetch_ohlcv({symbol}): {e}")
        return None

def _calc_ema(closes: np.ndarray, period: int) -> float:
    """EMA usando multiplicador estándar. Retorna último valor."""
    if len(closes) < period:
        return float(closes[-1])
    k   = 2.0 / (period + 1)
    ema = float(np.mean(closes[:period]))
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema

def _calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    """RSI Wilder. Retorna valor 0-100."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = float(np.mean(gains[:period]))
    avg_l  = float(np.mean(losses[:period]))
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i])  / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)

def _calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """ATR Wilder."""
    if len(closes) < 2:
        return float(closes[-1]) * 0.01
    trs = []
    for i in range(1, len(closes)):
        hl  = highs[i]  - lows[i]
        hpc = abs(highs[i]  - closes[i-1])
        lpc = abs(lows[i]   - closes[i-1])
        trs.append(max(hl, hpc, lpc))
    trs = np.array(trs)
    if len(trs) < period:
        return float(np.mean(trs))
    atr = float(np.mean(trs[:period]))
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

@dataclass
class MarketData:
    """Snapshot de indicadores técnicos para un símbolo."""
    symbol:    str
    price:     float
    ema_fast:  float
    ema_slow:  float
    rsi:       float
    atr:       float
    vol_ratio: float   # volumen actual / media
    spread_pct: float
    funding:   float
    ts:        float = field(default_factory=time.time)

    def trend_ok(self, side: str) -> bool:
        """EMA fast > slow = alcista. fast < slow = bajista."""
        if side == "long":  return self.ema_fast >= self.ema_slow
        if side == "short": return self.ema_fast <= self.ema_slow
        return False

    def rsi_ok(self, side: str) -> bool:
        if side == "long":  return self.rsi <= RSI_OB
        if side == "short": return self.rsi >= RSI_OS
        return False

    def vol_ok(self) -> bool:
        return self.vol_ratio >= VOL_MULT

    def spread_ok(self) -> bool:
        return self.spread_pct <= MAX_SPREAD_PCT

    def atr_ok(self) -> bool:
        atr_pct = self.atr / self.price * 100 if self.price > 0 else 0
        return ATR_MIN_MULT <= atr_pct <= ATR_MAX_MULT

    def funding_ok(self, side: str) -> bool:
        """No abrir LONG si funding muy positivo, ni SHORT si muy negativo."""
        if side == "long":  return self.funding <= FUNDING_LIMIT
        if side == "short": return self.funding >= -FUNDING_LIMIT
        return True

    def score(self, side: str) -> Tuple[int, int, List[str]]:
        """
        Calcula puntuación de confirmaciones.
        Retorna (aprobadas, total, lista_detalle).
        """
        checks = [
            ("📈 Trend EMA",    self.trend_ok(side)),
            ("📊 RSI",          self.rsi_ok(side)),
            ("📦 Volumen",      self.vol_ok()),
            ("↔️ Spread",       self.spread_ok()),
            ("〰️ ATR volatilidad", self.atr_ok()),
            ("💸 Funding rate", self.funding_ok(side)),
        ]
        passed  = sum(1 for _, ok in checks if ok)
        details = [
            f"  {'✅' if ok else '❌'} {name}"
            for name, ok in checks
        ]
        return passed, len(checks), details


# Cache de indicadores por símbolo (TTL 60s para no spamear el exchange)
_mdata_cache: Dict[str, MarketData] = {}
_mdata_lock  = threading.Lock()
_MDATA_TTL   = 60  # segundos

def get_market_data(symbol: str) -> Optional[MarketData]:
    """Obtiene indicadores técnicos con caché."""
    with _mdata_lock:
        cached = _mdata_cache.get(symbol)
        if cached and (time.time() - cached.ts) < _MDATA_TTL:
            return cached

    try:
        # OHLCV 15m — necesitamos al menos EMA_SLOW+ATR_PERIOD velas
        needed = max(EMA_SLOW, VOL_LOOKBACK, ATR_PERIOD) + 5
        ohlcv  = _fetch_ohlcv(symbol, "15m", limit=needed + 20)
        if ohlcv is None:
            return None

        closes = ohlcv[:, 4]
        highs  = ohlcv[:, 2]
        lows   = ohlcv[:, 3]
        vols   = ohlcv[:, 5]

        ema_fast  = _calc_ema(closes, EMA_FAST)
        ema_slow  = _calc_ema(closes, EMA_SLOW)
        rsi       = _calc_rsi(closes, RSI_PERIOD)
        atr       = _calc_atr(highs, lows, closes, ATR_PERIOD)
        cur_vol   = float(vols[-1])
        avg_vol   = float(np.mean(vols[-VOL_LOOKBACK-1:-1])) if len(vols) > VOL_LOOKBACK else float(np.mean(vols))
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0.0

        # Spread en %
        last, bid, ask = price_with_spread(symbol)
        spread_pct = abs(ask - bid) / last * 100 if last > 0 else 999.0

        # Funding rate
        funding = 0.0
        try:
            fr_data = ex_call(ex().fetch_funding_rate, symbol)
            funding = float(fr_data.get("fundingRate", 0) or 0) * 100  # → %
        except Exception:
            pass

        md = MarketData(
            symbol=symbol, price=last,
            ema_fast=ema_fast, ema_slow=ema_slow,
            rsi=rsi, atr=atr,
            vol_ratio=vol_ratio, spread_pct=spread_pct,
            funding=funding,
        )
        with _mdata_lock:
            _mdata_cache[symbol] = md
        return md

    except Exception as e:
        log.warning(f"get_market_data({symbol}): {e}")
        return None


def invalidate_market_cache(symbol: str):
    with _mdata_lock:
        _mdata_cache.pop(symbol, None)


# ─────────────────────────────────────────────────────────────────
# FILTRO DE SESIÓN
# ─────────────────────────────────────────────────────────────────
def session_ok() -> Tuple[bool, str]:
    if not BLOCK_HOURS:
        return True, ""
    hour_utc = datetime.now(timezone.utc).hour
    if hour_utc in BLOCK_HOURS:
        return False, f"Hora bloqueada: {hour_utc}:xx UTC"
    return True, ""


# ─────────────────────────────────────────────────────────────────
# SIZING DINÁMICO POR ATR
# ─────────────────────────────────────────────────────────────────
def calc_position_size(atr: float, px: float) -> float:
    """
    Si ATR_SIZING activo: ajusta FIXED_USDT según volatilidad.
    Mayor ATR → menor tamaño. Base: riesgo fijo de SL_PCT%.
    """
    if not ATR_SIZING or atr <= 0 or px <= 0:
        return FIXED_USDT
    # Riesgo base en USDT = FIXED_USDT * SL_PCT / 100
    risk_usdt  = FIXED_USDT * SL_PCT / 100
    # SL en USDT por contrato = ATR
    size_usdt  = (risk_usdt / atr) * px
    # Clamp entre 0.5x y 2x del FIXED_USDT
    size_usdt  = max(FIXED_USDT * 0.5, min(FIXED_USDT * 2.0, size_usdt))
    return round(size_usdt, 2)


# ─────────────────────────────────────────────────────────────────
# VERIFICACIÓN DE FILL
# ─────────────────────────────────────────────────────────────────
def verify_fill(symbol: str, order_id: str, max_wait: int = 10) -> Optional[dict]:
    """
    Espera hasta max_wait segundos a que la orden quede FILLED.
    Retorna la orden o None si no se confirma.
    """
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            order = ex_call(ex().fetch_order, order_id, symbol)
            status = str(order.get("status", "")).lower()
            if status in ("closed", "filled"):
                return order
            if status in ("canceled", "rejected", "expired"):
                log.warning(f"Orden {order_id} {status}")
                return None
        except Exception as e:
            log.warning(f"verify_fill({order_id}): {e}")
        time.sleep(1)
    log.warning(f"verify_fill timeout para {order_id}")
    return None


# ─────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────
_tg_queue:  deque      = deque(maxlen=50)
_tg_q_lock = threading.Lock()

def tg(msg: str, silent: bool = False):
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    payload = {
        "chat_id":              TG_CHAT_ID,
        "text":                 msg[:4096],
        "parse_mode":           "HTML",
        "disable_notification": silent,
    }
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload, timeout=15
        )
        if not r.ok:
            log.warning(f"TG {r.status_code}: {r.text[:200]}")
            with _tg_q_lock: _tg_queue.append(msg)
    except Exception as e:
        log.warning(f"TG error: {e}")
        with _tg_q_lock: _tg_queue.append(msg)

def _tg_retry_worker():
    while True:
        time.sleep(30)
        with _tg_q_lock:
            pending = list(_tg_queue)
            _tg_queue.clear()
        for msg in pending:
            try: tg(msg)
            except Exception: pass

def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def _bar(value: float, max_val: float, width: int = 10) -> str:
    filled = int(min(value / max_val, 1.0) * width) if max_val > 0 else 0
    return "█" * filled + "░" * (width - filled)

def _dur(entry_time_str: str) -> str:
    try:
        entry_dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        secs     = int((datetime.now(timezone.utc) - entry_dt).total_seconds())
        h, rem   = divmod(secs, 3600)
        m        = rem // 60
        return f"{h}h {m}m" if h > 0 else f"{m}m"
    except Exception:
        return "?"


# ─────────────────────────────────────────────────────────────────
# MENSAJES TELEGRAM
# ─────────────────────────────────────────────────────────────────
def msg_start(bal: float):
    dr = "🔸 <b>MODO DRY-RUN ACTIVO</b> — sin órdenes reales\n" if DRY_RUN else ""
    tg(
        f"<b>🚀 SAIYAN AURA FUSION v4.0 — ONLINE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{dr}"
        f"💰 Balance: <b>${bal:.2f} USDT</b>\n"
        f"⚙️ ${FIXED_USDT:.0f}/trade · {LEVERAGE}x · max {MAX_OPEN_TRADES}\n"
        f"🎯 TP1 +{TP1_PCT}% · TP2 +{TP2_PCT}% · TP3 +{TP3_PCT}%\n"
        f"🛑 SL -{SL_PCT}% · Trailing {TRAILING_PCT}% (activa +{TRAILING_ACTIVATE}%)\n"
        f"🛡 CB -{CB_DD}% · Diario -{DAILY_LOSS_PCT}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 <b>Confirmaciones:</b>\n"
        f"  Multi-señal: {MIN_CONFIRMATIONS}x en {CONFIRM_WINDOW}s\n"
        f"  EMA {EMA_FAST}/{EMA_SLOW} · RSI {RSI_OS}-{RSI_OB}\n"
        f"  Vol >{VOL_MULT}x · Spread <{MAX_SPREAD_PCT}%\n"
        f"  ATR {ATR_MIN_MULT}-{ATR_MAX_MULT}% · Funding <{FUNDING_LIMIT}%\n"
        f"  Sizing ATR: {'✅' if ATR_SIZING else '❌'}\n"
        f"  Horas bloqueadas: {BLOCK_HOURS if BLOCK_HOURS else 'ninguna'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 /status /pos /filters /pause /resume\n"
        f"⏰ {now()}"
    )

def msg_signal_pending(symbol: str, side: str, count: int):
    """Notifica cuando se acumula una señal pero aún falta confirmación."""
    tg(
        f"⏳ <b>SEÑAL PENDIENTE</b> — <code>{symbol}</code>\n"
        f"  {'▲ LONG' if side=='long' else '▼ SHORT'} · {count}/{MIN_CONFIRMATIONS} confirmaciones\n"
        f"  Ventana: {CONFIRM_WINDOW}s\n"
        f"⏰ {now()}",
        silent=True
    )

def msg_filtered(symbol: str, side: str, reason: str, md: Optional[MarketData]):
    """Informa señal bloqueada por filtros técnicos."""
    details_txt = ""
    score_txt   = ""
    if md:
        passed, total, details = md.score(side)
        score_txt   = f"  Score: {passed}/{total}\n"
        details_txt = "\n".join(details[:6]) + "\n"
    tg(
        f"🚫 <b>SEÑAL FILTRADA</b> — <code>{symbol}</code>\n"
        f"  {'▲ LONG' if side=='long' else '▼ SHORT'} · Razón: {reason}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{score_txt}"
        f"{details_txt}"
        f"⏰ {now()}",
        silent=True
    )
    with _lock:
        st.rejected_signals += 1

def msg_open(t: Trade, bal: float, md: Optional[MarketData]):
    e        = "🟢" if t.side == "long" else "🔴"
    d        = "▲ LONG" if t.side == "long" else "▼ SHORT"
    notional = (FIXED_USDT * LEVERAGE)
    dry_tag  = " 🔸DRY-RUN" if t.dry_run else ""
    score_txt = ""
    if md:
        passed, total, _ = md.score(t.side)
        score_txt = (
            f"🔍 Score: {passed}/{total} · "
            f"EMA {'✅' if md.trend_ok(t.side) else '❌'} "
            f"RSI {md.rsi:.0f} {'✅' if md.rsi_ok(t.side) else '❌'} "
            f"Vol {md.vol_ratio:.1f}x {'✅' if md.vol_ok() else '❌'}\n"
            f"  ATR {md.atr/md.price*100:.2f}% {'✅' if md.atr_ok() else '❌'} "
            f"Spread {md.spread_pct:.3f}% {'✅' if md.spread_ok() else '❌'} "
            f"Fund {md.funding:.3f}% {'✅' if md.funding_ok(t.side) else '❌'}\n"
        )
    tg(
        f"{e} <b>{d}{dry_tag}</b> — <code>{t.symbol}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Entrada: <code>{t.entry_price:.6g}</code>\n"
        f"🟡 TP1 50%: <code>{t.tp1:.6g}</code>  (+{TP1_PCT}%)\n"
        f"🟠 TP2 30%: <code>{t.tp2:.6g}</code>  (+{TP2_PCT}%)\n"
        f"🟢 TP3 20%: <code>{t.tp3:.6g}</code>  (+{TP3_PCT}%)\n"
        f"🛑 SL:      <code>{t.sl:.6g}</code>   (-{SL_PCT}%)\n"
        f"〽️ Trailing: +{TRAILING_ACTIVATE}% activa · paso {TRAILING_PCT}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{score_txt}"
        f"📦 {t.contracts} contratos · ${notional:.0f} notional\n"
        f"✅ Confirmaciones: {t.confirm_score}/{MIN_CONFIRMATIONS}\n"
        f"📊 {st.wins}W/{st.losses}L · WR:{st.wr():.1f}%\n"
        f"🔢 Abiertas: {st.n()}/{MAX_OPEN_TRADES} · Balance: ${bal:.2f}\n"
        f"⏰ {now()}"
    )

def msg_tp(t: Trade, label: str, pnl_est: float, remaining: str):
    extras = ""
    if label == "TP1":
        extras = "🛡 SL → Break-Even ✓\n〽️ Trailing activado ✓\n"
    tg(
        f"🏆 <b>{label} HIT</b> — <code>{t.symbol}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 ~${pnl_est:+.2f} parcial\n"
        f"📦 Restante: {remaining}\n"
        f"{extras}"
        f"💹 Hoy: ${st.daily_pnl:+.2f} · Total: ${st.total_pnl:+.2f}\n"
        f"⏰ {now()}"
    )

def msg_trailing_update(t: Trade, new_sl: float, gain_pct: float):
    tg(
        f"〽️ <b>TRAILING SL</b> — <code>{t.symbol}</code>\n"
        f"🛑 SL → <code>{new_sl:.6g}</code> · Ganancia: +{gain_pct:.2f}%\n"
        f"⏰ {now()}",
        silent=True
    )

def msg_close(t: Trade, exit_p: float, pnl: float, reason: str):
    e        = "✅" if pnl >= 0 else "❌"
    pct      = (exit_p - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
    dur      = _dur(t.entry_time)
    dry_tag  = " 🔸DR" if t.dry_run else ""
    daily_bar = _bar(
        abs(st.daily_pnl),
        st.peak_equity * DAILY_LOSS_PCT / 100 if st.peak_equity > 0 else 1
    )
    tg(
        f"{e} <b>CERRADO{dry_tag} · {reason}</b>\n"
        f"<code>{t.symbol}</code> {t.side.upper()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <code>{t.entry_price:.6g}</code> → <code>{exit_p:.6g}</code> ({pct:+.2f}%)\n"
        f"{'💰' if pnl>=0 else '💸'} PnL: <b>${pnl:+.2f}</b>  ⏱ {dur}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {st.wins}W/{st.losses}L · WR:{st.wr():.1f}% · PF:{st.pf():.2f}\n"
        f"💡 Avg: +${st.avg_win():.2f} / -${st.avg_loss():.2f}\n"
        f"🏆 Mejor: ${st.best_trade:+.2f} · Peor: ${st.worst_trade:+.2f}\n"
        f"💹 Hoy: ${st.daily_pnl:+.2f} [{daily_bar}]\n"
        f"💼 Total: ${st.total_pnl:+.2f} · MaxDD: {st.max_dd_real:.1f}%\n"
        f"⏰ {now()}"
    )

def msg_blocked(reason: str, action: str, symbol: str):
    tg(
        f"⛔ <b>BLOQUEADO</b> — {reason}\n"
        f"Acción: {action} {symbol}\n"
        f"💹 Hoy: ${st.daily_pnl:+.2f} · Total: ${st.total_pnl:+.2f}\n"
        f"⏰ {now()}"
    )

def msg_error(txt: str):
    tg(f"🔥 <b>ERROR:</b> <code>{txt[:400]}</code>\n⏰ {now()}")

def msg_status():
    try: bal = balance()
    except Exception: bal = 0.0
    paused_line = "⏸ <b>BOT EN PAUSA</b>\n" if st.paused else ""
    cb_line     = "🚨 <b>CIRCUIT BREAKER ACTIVO</b>\n" if st.cb() else ""
    dl_line     = "🚨 <b>LÍMITE DIARIO ALCANZADO</b>\n" if st.daily_hit() else ""
    daily_bar   = _bar(abs(st.daily_pnl), st.peak_equity * DAILY_LOSS_PCT / 100 if st.peak_equity > 0 else 1)
    cb_bar      = _bar(abs(st.total_pnl) if st.total_pnl < 0 else 0, st.peak_equity * CB_DD / 100 if st.peak_equity > 0 else 1)
    dr          = "🔸 DRY-RUN\n" if DRY_RUN else ""
    tg(
        f"📊 <b>STATUS — SAIYAN v4.0</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{dr}{paused_line}{cb_line}{dl_line}"
        f"💰 Balance: <b>${bal:.2f} USDT</b>\n"
        f"📦 Posiciones: {st.n()}/{MAX_OPEN_TRADES}\n"
        f"⏱ Uptime: {st.uptime()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 {st.wins}W / {st.losses}L · WR:{st.wr():.1f}%\n"
        f"🏦 PF:{st.pf():.2f} · E:${st.expectancy():.2f}/trade\n"
        f"🏆 Mejor: ${st.best_trade:+.2f} · Peor: ${st.worst_trade:+.2f}\n"
        f"💹 Hoy: ${st.daily_pnl:+.2f} [{daily_bar}]\n"
        f"🛡 DD: [{cb_bar}] Max:{st.max_dd_real:.1f}%\n"
        f"💼 Total PnL: ${st.total_pnl:+.2f}\n"
        f"🚫 Señales rechazadas: {st.rejected_signals}\n"
        f"⏰ {now()}"
    )

def msg_positions():
    if not st.trades:
        tg("📦 <b>Sin posiciones abiertas</b>")
        return
    lines = [f"📦 <b>POSICIONES ({st.n()})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for sym_, t in st.trades.items():
        try:
            px   = price(sym_)
            gain = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
            icon = "🟢" if gain >= 0 else "🔴"
            tags = ("〽️" if t.trailing_on else "") + (" 🛡BE" if t.sl_at_be else "") + (" 🔸DR" if t.dry_run else "")
            lines.append(
                f"{icon} <code>{sym_}</code> {t.side.upper()}{tags}\n"
                f"   {t.entry_price:.6g} → {px:.6g} ({gain:+.2f}%)\n"
                f"   SL:{t.sl:.6g}  ⏱{_dur(t.entry_time)}  Score:{t.confirm_score}"
            )
        except Exception:
            lines.append(f"<code>{sym_}</code> {t.side.upper()}")
    tg("\n".join(lines))

def msg_filters(symbol: str, side: str):
    """Muestra estado actual de todos los filtros para un símbolo."""
    tg(f"🔍 Analizando filtros para <code>{symbol}</code>...")
    md = get_market_data(symbol)
    if md is None:
        tg(f"❌ No se pudieron obtener datos para <code>{symbol}</code>")
        return
    passed, total, details = md.score(side)
    session_pass, session_msg = session_ok()
    with _signal_lock:
        pending = _signal_buffer.get(symbol)
        sig_count = len([
            ts for ts in (pending.timestamps if pending else [])
            if time.time() - ts <= CONFIRM_WINDOW
        ])
    tg(
        f"🔍 <b>FILTROS — <code>{symbol}</code> {'▲ LONG' if side=='long' else '▼ SHORT'}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  💵 Precio: {md.price:.6g}\n"
        f"  📈 EMA{EMA_FAST}: {md.ema_fast:.6g} · EMA{EMA_SLOW}: {md.ema_slow:.6g}\n"
        f"  📊 RSI({RSI_PERIOD}): {md.rsi:.1f}  [OS<{RSI_OS} OB>{RSI_OB}]\n"
        f"  〰️ ATR: {md.atr:.6g} ({md.atr/md.price*100:.2f}%)\n"
        f"  📦 Vol ratio: {md.vol_ratio:.2f}x  (min {VOL_MULT}x)\n"
        f"  ↔️ Spread: {md.spread_pct:.3f}%  (max {MAX_SPREAD_PCT}%)\n"
        f"  💸 Funding: {md.funding:.4f}%  (limit ±{FUNDING_LIMIT}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(details) + "\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {'✅' if session_pass else '❌'} Sesión horaria {session_msg}\n"
        f"  ⏳ Multi-señal: {sig_count}/{MIN_CONFIRMATIONS} en {CONFIRM_WINDOW}s\n"
        f"  🎯 Score total: {passed}/{total}\n"
        f"⏰ {now()}"
    )

def msg_daily_summary():
    try: bal = balance()
    except Exception: bal = 0.0
    emoji = "📈" if st.daily_pnl >= 0 else "📉"
    tg(
        f"{emoji} <b>RESUMEN DIARIO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance cierre: ${bal:.2f} USDT\n"
        f"💹 PnL del día: <b>${st.daily_pnl:+.2f}</b>\n"
        f"🚫 Señales rechazadas hoy: {st.rejected_signals}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {st.wins}W / {st.losses}L · WR:{st.wr():.1f}%\n"
        f"  PF:{st.pf():.2f} · E:${st.expectancy():.2f}/trade\n"
        f"  🏆 Mejor: ${st.best_trade:+.2f} · 💸 Peor: ${st.worst_trade:+.2f}\n"
        f"  MaxDD: {st.max_dd_real:.1f}% · Total: ${st.total_pnl:+.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {now()}"
    )

def msg_heartbeat():
    try:
        bal        = balance()
        open_lines = ""
        for sym_, t in list(st.trades.items()):
            try:
                px   = price(sym_)
                gain = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
                icon = "🟢" if gain >= 0 else "🔴"
                dr   = "🔸" if t.dry_run else ""
                open_lines += f"  {icon}{dr} <code>{sym_}</code> {t.side.upper()} {gain:+.2f}% ⏱{_dur(t.entry_time)}\n"
            except Exception:
                open_lines += f"  <code>{sym_}</code> {t.side.upper()}\n"
        if not open_lines:
            open_lines = "  (sin posiciones)\n"
        daily_bar = _bar(abs(st.daily_pnl), st.peak_equity * DAILY_LOSS_PCT / 100 if st.peak_equity > 0 else 1)
        cb_bar    = _bar(abs(st.total_pnl) if st.total_pnl < 0 else 0, st.peak_equity * CB_DD / 100 if st.peak_equity > 0 else 1)
        paused    = " ⏸PAUSA" if st.paused else ""
        dr        = " 🔸DRY" if DRY_RUN else ""
        tg(
            f"💓 <b>HEARTBEAT{paused}{dr}</b> · {now()}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: ${bal:.2f} USDT\n"
            f"📦 Posiciones ({st.n()}/{MAX_OPEN_TRADES}):\n{open_lines}"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {st.wins}W/{st.losses}L · WR:{st.wr():.1f}% · PF:{st.pf():.2f}\n"
            f"💹 Hoy: ${st.daily_pnl:+.2f} [{daily_bar}]\n"
            f"🛡 DD: [{cb_bar}] Max:{st.max_dd_real:.1f}%\n"
            f"🚫 Rechazadas: {st.rejected_signals} · ⏱ Uptime: {st.uptime()}",
            silent=True
        )
    except Exception as e:
        log.warning(f"Heartbeat: {e}")


# ─────────────────────────────────────────────────────────────────
# TELEGRAM COMMANDS (polling)
# ─────────────────────────────────────────────────────────────────
def _tg_commands_worker():
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    log.info("Telegram commands polling iniciado")
    while True:
        try:
            url    = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
            params = {"timeout": 30, "offset": st.tg_update_offset, "allowed_updates": ["message"]}
            r = requests.get(url, params=params, timeout=40)
            if not r.ok:
                time.sleep(5); continue
            for upd in r.json().get("result", []):
                st.tg_update_offset = upd["update_id"] + 1
                msg_obj = upd.get("message", {})
                chat_id = str(msg_obj.get("chat", {}).get("id", ""))
                text    = msg_obj.get("text", "").strip().lower()
                if chat_id != str(TG_CHAT_ID):
                    continue
                if text in ("/status", "/s"):
                    msg_status()
                elif text in ("/pos", "/positions", "/p"):
                    msg_positions()
                elif text == "/pause":
                    with _lock: st.paused = True
                    tg("⏸ <b>Bot en PAUSA.</b>\nUsa /resume para reanudar.")
                elif text == "/resume":
                    with _lock: st.paused = False
                    tg("▶️ <b>Bot REANUDADO.</b>")
                elif text.startswith("/filters"):
                    parts = text.split()
                    if len(parts) >= 2:
                        raw_sym = parts[1].upper()
                        side    = parts[2] if len(parts) >= 3 else "long"
                        msg_filters(raw_sym, side)
                    else:
                        tg("Uso: /filters BTCUSDT long")
                elif text.startswith("/close "):
                    raw_sym = text.split("/close ", 1)[1].strip().upper()
                    tg(f"🔄 Cerrando <code>{raw_sym}</code>...")
                    res = close_trade(raw_sym, "MANUAL /close")
                    if res.get("result") == "closed":
                        tg(f"✅ Cerrado. PnL: ${res.get('pnl', 0):+.2f}")
                    else:
                        tg(f"⚠️ {res}")
                elif text == "/help":
                    tg(
                        "📲 <b>Comandos v4.0:</b>\n"
                        "/status — resumen\n"
                        "/pos — posiciones\n"
                        "/filters SYMBOL long|short — análisis filtros\n"
                        "/pause — pausar entradas\n"
                        "/resume — reanudar\n"
                        "/close SYMBOL — cierre manual\n"
                        "/help — esta ayuda"
                    )
        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            log.warning(f"TG commands: {e}")
            time.sleep(10)


# ─────────────────────────────────────────────────────────────────
# CSV LOG
# ─────────────────────────────────────────────────────────────────
def csv_log(action: str, t: Trade, exit_p: float = 0.0, pnl: float = 0.0):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts", "action", "symbol", "side", "entry", "exit",
                            "pnl", "qty", "confirm_score", "dry_run", "trailing", "sl_at_be"])
            w.writerow([
                now(), action, t.symbol, t.side,
                t.entry_price, exit_p or t.entry_price,
                round(pnl, 4), t.contracts,
                t.confirm_score, t.dry_run,
                t.trailing_on, t.sl_at_be
            ])
    except Exception as e:
        log.warning(f"CSV: {e}")


# ─────────────────────────────────────────────────────────────────
# TRAILING STOP WORKER
# ─────────────────────────────────────────────────────────────────
def _trailing_worker():
    log.info("Trailing worker iniciado")
    while True:
        time.sleep(15)
        with _lock:
            symbols = list(st.trades.keys())
        for symbol in symbols:
            try:
                with _lock:
                    if symbol not in st.trades: continue
                    t = st.trades[symbol]
                px       = price(symbol)
                gain_pct = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
                with _lock:
                    if symbol not in st.trades: continue
                    t = st.trades[symbol]
                    if not t.trailing_on and gain_pct >= TRAILING_ACTIVATE:
                        t.trailing_on   = True
                        t.trailing_high = px
                        log.info(f"[{symbol}] Trailing ON @ {px:.6g} (+{gain_pct:.2f}%)")
                    if not t.trailing_on: continue
                    if t.side == "long":
                        if px > t.trailing_high: t.trailing_high = px
                        new_sl = t.trailing_high * (1 - TRAILING_PCT / 100)
                        if new_sl > t.sl:
                            t.sl = new_sl
                            msg_trailing_update(t, new_sl, gain_pct)
                            update_sl_order(t, new_sl)
                    else:
                        if t.trailing_high == 0 or px < t.trailing_high: t.trailing_high = px
                        new_sl = t.trailing_high * (1 + TRAILING_PCT / 100)
                        if new_sl < t.sl:
                            t.sl = new_sl
                            msg_trailing_update(t, new_sl, gain_pct)
                            update_sl_order(t, new_sl)
            except Exception as e:
                log.warning(f"Trailing [{symbol}]: {e}")


# ─────────────────────────────────────────────────────────────────
# ══ CORE LÓGICA ══════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────

def _run_confirmations(symbol: str, side: str) -> Tuple[bool, str, Optional[MarketData]]:
    """
    Ejecuta todas las capas de confirmación.
    Retorna (ok, motivo_rechazo, market_data).
    """
    # 1. Filtro de sesión horaria
    sess_ok, sess_msg = session_ok()
    if not sess_ok:
        return False, f"Sesión bloqueada: {sess_msg}", None

    # 2. Obtener datos de mercado
    md = get_market_data(symbol)
    if md is None:
        # Sin datos no bloqueamos (falla suave)
        log.warning(f"[{symbol}] Sin datos de mercado — pasando sin filtros técnicos")
        return True, "", None

    # 3. Spread
    if not md.spread_ok():
        return False, f"Spread {md.spread_pct:.3f}% > {MAX_SPREAD_PCT}%", md

    # 4. Anti-spike (ya en price_validated, pero doble check)
    # ya cubierto en open_trade

    # 5. Volatilidad ATR
    if not md.atr_ok():
        atr_pct = md.atr / md.price * 100
        return False, f"ATR {atr_pct:.2f}% fuera de [{ATR_MIN_MULT},{ATR_MAX_MULT}]%", md

    # 6. Volumen
    if not md.vol_ok():
        return False, f"Volumen bajo: {md.vol_ratio:.2f}x < {VOL_MULT}x media", md

    # 7. Trend EMA
    if not md.trend_ok(side):
        dir_msg = f"EMA{EMA_FAST}({md.ema_fast:.4g}) {'<' if side=='long' else '>'} EMA{EMA_SLOW}({md.ema_slow:.4g})"
        return False, f"Contra-trend: {dir_msg}", md

    # 8. RSI
    if not md.rsi_ok(side):
        return False, f"RSI {md.rsi:.1f} {'sobrecomprado' if side=='long' else 'sobrevendido'}", md

    # 9. Funding rate
    if not md.funding_ok(side):
        return False, f"Funding extremo {md.funding:.4f}% (limit ±{FUNDING_LIMIT}%)", md

    return True, "", md


def open_trade(raw_symbol: str, side: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)

        # ── Protecciones básicas ──────────────────────────────────
        if st.paused:
            return {"result": "paused"}
        if st.n() >= MAX_OPEN_TRADES:
            msg_blocked("Máx posiciones", side, symbol)
            return {"result": "blocked_max_trades"}
        if symbol in st.trades:
            return {"result": "already_open"}
        if st.cb():
            msg_blocked(f"Circuit Breaker ≥{CB_DD}%", side, symbol)
            return {"result": "blocked_circuit_breaker"}
        if st.daily_hit():
            msg_blocked(f"Límite diario ≥{DAILY_LOSS_PCT}%", side, symbol)
            return {"result": "blocked_daily_limit"}
        if st.in_cooldown(symbol):
            remaining = int(COOLDOWN_MIN * 60 - (time.time() - st.cooldowns.get(symbol, 0)))
            return {"result": "cooldown", "remaining_sec": remaining}

    # ── Multi-señal (fuera del lock para no bloquearlo) ──────────
    confirm_count = _register_signal(symbol, side)
    log.info(f"[{symbol}] {side.upper()} señal #{confirm_count}/{MIN_CONFIRMATIONS}")

    if confirm_count < MIN_CONFIRMATIONS:
        msg_signal_pending(symbol, side, confirm_count)
        return {"result": "waiting_confirmations", "count": confirm_count, "needed": MIN_CONFIRMATIONS}

    # ── Filtros técnicos ─────────────────────────────────────────
    filters_ok, filter_reason, md = _run_confirmations(symbol, side)
    if not filters_ok:
        log.info(f"[{symbol}] Filtrado: {filter_reason}")
        msg_filtered(symbol, side, filter_reason, md)
        _clear_signal(symbol)   # resetear buffer tras rechazo técnico
        return {"result": "filtered", "reason": filter_reason}

    # ── Abrir posición ───────────────────────────────────────────
    _clear_signal(symbol)

    with _lock:
        # Re-check con lock (otra señal pudo haber abierto ya)
        if symbol in st.trades:
            return {"result": "already_open"}

        try:
            e = ex()
            if symbol not in e.markets:
                ex_call(e.load_markets)
            if symbol not in e.markets:
                raise ValueError(f"Símbolo no encontrado: {symbol}")

            set_lev(symbol)
            px    = price_validated(symbol)
            bal   = balance()

            # Sizing: dinámico por ATR o fijo
            usdt_size = calc_position_size(md.atr if md else 0, px) if ATR_SIZING and md else FIXED_USDT
            notl  = usdt_size * LEVERAGE
            qty   = float(e.amount_to_precision(symbol, notl / px))

            if qty * px < 5:
                raise ValueError(f"Notional demasiado pequeño: ${qty*px:.2f}")

            order_side = "buy" if side == "long" else "sell"
            log.info(f"[OPEN] {symbol} {side.upper()} qty={qty} @~{px:.6g} usdt={usdt_size:.1f}")

            if DRY_RUN:
                # Simular sin enviar orden real
                entry_p = px
                log.info(f"  [DRY-RUN] Simulando entrada @ {entry_p:.6g}")
            else:
                order = ex_call(e.create_order, symbol, "market", order_side, qty,
                                params={"reduceOnly": False})
                order_id = order.get("id", "")
                # Verificar fill
                filled = verify_fill(symbol, order_id, max_wait=15) if order_id else None
                entry_p = float(
                    (filled or order).get("average") or
                    (filled or order).get("price") or px
                )
                if entry_p == 0:
                    entry_p = px
                log.info(f"  Fill confirmado @ {entry_p:.6g}")

            mult       = 1 if side == "long" else -1
            tp1        = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP1_PCT / 100)))
            tp2        = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP2_PCT / 100)))
            tp3        = float(e.price_to_precision(symbol, entry_p * (1 + mult * TP3_PCT / 100)))
            sl         = float(e.price_to_precision(symbol, entry_p * (1 - mult * SL_PCT / 100)))
            close_side = "sell" if side == "long" else "buy"

            if not DRY_RUN:
                place_tp_limit(e, symbol, close_side, qty * 0.50, tp1)
                place_tp_limit(e, symbol, close_side, qty * 0.30, tp2)
                place_tp_limit(e, symbol, close_side, qty * 0.20, tp3)
                place_sl_stop(e, symbol, close_side, qty, sl)

            t = Trade(
                symbol=symbol, side=side, entry_price=entry_p,
                contracts=qty, tp1=tp1, tp2=tp2, tp3=tp3, sl=sl,
                entry_time=now(), trailing_high=entry_p,
                confirm_score=confirm_count,
                dry_run=DRY_RUN,
            )
            st.trades[symbol] = t
            csv_log("OPEN", t)
            msg_open(t, bal, md)
            # Invalidar cache para que próximas consultas sean frescas
            invalidate_market_cache(symbol)
            return {"result": "opened", "symbol": symbol, "side": side,
                    "entry": entry_p, "qty": qty, "dry_run": DRY_RUN,
                    "confirm_score": confirm_count}

        except Exception as ex_err:
            log.error(f"open_trade {symbol}: {ex_err}")
            msg_error(f"open_trade {symbol}: {ex_err}")
            return {"result": "error", "detail": str(ex_err)}


def close_trade(raw_symbol: str, reason: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)
        if symbol not in st.trades:
            return {"result": "not_found"}
        t = st.trades[symbol]

        try:
            e = ex()
            cancel_all_orders_safe(symbol)

            pos    = position(symbol)
            exit_p = price(symbol)
            pnl    = 0.0

            if t.dry_run:
                pnl    = ((exit_p - t.entry_price) if t.side=="long"
                          else (t.entry_price - exit_p)) * t.contracts
                log.info(f"[CLOSE DRY] {symbol} {reason} @ {exit_p:.6g} pnl={pnl:.2f}")
            elif pos:
                qty_pos    = abs(float(
                    pos.get("contracts") or
                    pos.get("info", {}).get("positionAmt", 0) or 0
                ))
                close_side = "sell" if t.side=="long" else "buy"
                if qty_pos > 0:
                    ord_ = ex_call(e.create_order, symbol, "market", close_side, qty_pos,
                                   params={"reduceOnly": True})
                    exit_p = float(ord_.get("average") or ord_.get("price") or exit_p)
                    if exit_p == 0: exit_p = price(symbol)
                    pnl = ((exit_p - t.entry_price) if t.side=="long"
                           else (t.entry_price - exit_p)) * qty_pos
            else:
                pnl = ((exit_p - t.entry_price) if t.side=="long"
                       else (t.entry_price - exit_p)) * t.contracts

            dur = _dur(t.entry_time)
            st.closed_history.append({
                "symbol": t.symbol, "side": t.side,
                "entry": t.entry_price, "exit": exit_p,
                "pnl": round(pnl, 4), "reason": reason,
                "duration": dur, "ts": now(),
                "confirm_score": t.confirm_score,
                "dry_run": t.dry_run,
            })
            if len(st.closed_history) > 50:
                st.closed_history = st.closed_history[-50:]

            st.record_close(pnl, symbol)
            csv_log("CLOSE", t, exit_p, pnl)
            msg_close(t, exit_p, pnl, reason)
            del st.trades[symbol]
            save_state()
            invalidate_market_cache(symbol)
            return {"result": "closed", "pnl": round(pnl, 4)}

        except Exception as ex_err:
            log.error(f"close_trade {symbol}: {ex_err}")
            msg_error(f"close_trade {symbol}: {ex_err}")
            return {"result": "error", "detail": str(ex_err)}


def handle_tp(raw_symbol: str, tp_label: str) -> dict:
    with _lock:
        symbol = sym(raw_symbol)
        if symbol not in st.trades:
            return {"result": "not_found"}
        t = st.trades[symbol]
        try:
            e   = ex()
            pos = position(symbol)
            rem = str(round(abs(float(
                pos.get("contracts") or pos.get("info", {}).get("positionAmt", 0) or 0
            )), 4)) if pos else "~restante"

            if tp_label == "TP1" and not t.tp1_hit:
                t.tp1_hit = True
                pnl_est   = abs(t.tp1 - t.entry_price) * t.contracts * 0.50
                if not t.dry_run:
                    try:
                        be         = float(e.price_to_precision(symbol, t.entry_price))
                        close_side = "sell" if t.side=="long" else "buy"
                        cancel_all_orders_safe(symbol)
                        qty_rem = t.contracts * 0.50
                        place_tp_limit(e, symbol, close_side, qty_rem * 0.60, t.tp2)
                        place_tp_limit(e, symbol, close_side, qty_rem * 0.40, t.tp3)
                        place_sl_stop(e, symbol, close_side, qty_rem, be)
                        t.sl         = be
                        t.sl_at_be   = True
                        t.trailing_on = True
                        t.trailing_high = price(symbol)
                        log.info(f"[{symbol}] SL→BE={be:.6g}, trailing ON")
                    except Exception as be_err:
                        log.warning(f"  BE after TP1: {be_err}")
                else:
                    t.sl_at_be  = True
                    t.trailing_on = True
                msg_tp(t, "TP1", pnl_est, rem)

            elif tp_label == "TP2" and not t.tp2_hit:
                t.tp2_hit = True
                pnl_est   = abs(t.tp2 - t.entry_price) * t.contracts * 0.30
                msg_tp(t, "TP2", pnl_est, rem)

            elif tp_label == "TP3":
                pnl_est = abs(t.tp3 - t.entry_price) * t.contracts * 0.20
                msg_tp(t, "TP3", pnl_est, "0")
                st.record_close(pnl_est, symbol)
                csv_log("CLOSE", t, t.tp3, pnl_est)
                if symbol in st.trades:
                    del st.trades[symbol]
                save_state()

            return {"result": f"{tp_label}_handled"}

        except Exception as ex_err:
            log.error(f"handle_tp {symbol} {tp_label}: {ex_err}")
            return {"result": "error", "detail": str(ex_err)}


# ─────────────────────────────────────────────────────────────────
# FLASK APP
# ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":             "alive",
        "bot":                "SAIYAN AURA FUSION v4.0",
        "dry_run":            DRY_RUN,
        "paused":             st.paused,
        "uptime":             st.uptime(),
        "open_trades":        st.n(),
        "total_trades":       st.total_trades,
        "wins":               st.wins, "losses": st.losses,
        "win_rate":           round(st.wr(), 1),
        "profit_factor":      round(st.pf(), 2),
        "expectancy":         round(st.expectancy(), 2),
        "avg_win":            round(st.avg_win(), 2),
        "avg_loss":           round(st.avg_loss(), 2),
        "best_trade":         round(st.best_trade, 2),
        "worst_trade":        round(st.worst_trade, 2),
        "max_drawdown":       round(st.max_dd_real, 2),
        "total_pnl":          round(st.total_pnl, 2),
        "daily_pnl":          round(st.daily_pnl, 2),
        "circuit_breaker":    st.cb(),
        "daily_limit":        st.daily_hit(),
        "rejected_signals":   st.rejected_signals,
        "confirmations_cfg":  {
            "min_signals":    MIN_CONFIRMATIONS,
            "window_sec":     CONFIRM_WINDOW,
            "ema":            f"{EMA_FAST}/{EMA_SLOW}",
            "rsi_range":      f"{RSI_OS}-{RSI_OB}",
            "vol_mult":       VOL_MULT,
            "max_spread_pct": MAX_SPREAD_PCT,
            "atr_range":      f"{ATR_MIN_MULT}-{ATR_MAX_MULT}%",
            "funding_limit":  FUNDING_LIMIT,
            "atr_sizing":     ATR_SIZING,
            "block_hours":    BLOCK_HOURS,
        },
        "time": now()
    })

@app.route("/positions", methods=["GET"])
def positions_endpoint():
    result = {}
    for sym_, t in st.trades.items():
        try:
            px     = price(sym_)
            unreal = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
        except Exception:
            px, unreal = 0.0, 0.0
        result[sym_] = {
            "side": t.side, "entry": t.entry_price,
            "current_price": px, "unrealized_pct": round(unreal, 2),
            "tp1": t.tp1, "tp2": t.tp2, "tp3": t.tp3, "sl": t.sl,
            "sl_at_be": t.sl_at_be, "trailing_on": t.trailing_on,
            "tp1_hit": t.tp1_hit, "tp2_hit": t.tp2_hit,
            "confirm_score": t.confirm_score,
            "dry_run": t.dry_run,
            "duration": _dur(t.entry_time), "since": t.entry_time,
        }
    return jsonify(result)

@app.route("/history", methods=["GET"])
def history_endpoint():
    return jsonify({"trades": st.closed_history[-20:]})

@app.route("/filters/<raw_sym>", methods=["GET"])
def filters_endpoint(raw_sym: str):
    """Endpoint REST para ver filtros de un símbolo."""
    side   = request.args.get("side", "long")
    symbol = sym(raw_sym)
    md     = get_market_data(symbol)
    if md is None:
        return jsonify({"error": "no market data"}), 503
    passed, total, details = md.score(side)
    sess_ok, sess_msg       = session_ok()
    return jsonify({
        "symbol":     symbol,
        "side":       side,
        "price":      md.price,
        "ema_fast":   round(md.ema_fast, 6),
        "ema_slow":   round(md.ema_slow, 6),
        "rsi":        round(md.rsi, 2),
        "atr":        round(md.atr, 6),
        "atr_pct":    round(md.atr / md.price * 100, 3) if md.price > 0 else 0,
        "vol_ratio":  round(md.vol_ratio, 3),
        "spread_pct": round(md.spread_pct, 4),
        "funding":    round(md.funding, 5),
        "checks": {
            "trend_ema":   md.trend_ok(side),
            "rsi":         md.rsi_ok(side),
            "volume":      md.vol_ok(),
            "spread":      md.spread_ok(),
            "atr":         md.atr_ok(),
            "funding":     md.funding_ok(side),
            "session":     sess_ok,
        },
        "score":       f"{passed}/{total}",
        "ready":       passed == total and sess_ok,
    })

@app.route("/dashboard", methods=["GET"])
def dashboard():
    try: bal = balance()
    except Exception: bal = 0.0
    wr    = st.wr()
    color = "#00ff88" if st.total_pnl >= 0 else "#ff4444"
    rows  = ""
    for t in reversed(st.closed_history[-10:]):
        pnl_c = "#00ff88" if t["pnl"] >= 0 else "#ff4444"
        dr    = " 🔸" if t.get("dry_run") else ""
        rows += (
            f"<tr><td>{t['ts']}</td><td>{t['symbol']}</td>"
            f"<td>{t['side'].upper()}</td>"
            f"<td style='color:{pnl_c}'>${t['pnl']:+.2f}</td>"
            f"<td>{t['reason']}{dr}</td>"
            f"<td>{t.get('confirm_score','?')}</td>"
            f"<td>{t['duration']}</td></tr>"
        )
    open_rows = ""
    for sym_, t in st.trades.items():
        try:
            px = price(sym_)
            g  = (px - t.entry_price) / t.entry_price * 100 * (1 if t.side=="long" else -1)
        except Exception:
            px, g = 0.0, 0.0
        gc    = "#00ff88" if g >= 0 else "#ff4444"
        trail = "〽️" if t.trailing_on else ""
        dr    = " 🔸" if t.dry_run else ""
        open_rows += (
            f"<tr><td>{sym_}</td><td>{t.side.upper()}</td>"
            f"<td>{t.entry_price:.6g}</td><td>{px:.6g}</td>"
            f"<td style='color:{gc}'>{g:+.2f}%</td>"
            f"<td>{trail}</td><td>{t.confirm_score}</td>"
            f"<td>{_dur(t.entry_time)}{dr}</td></tr>"
        )
    paused_banner = (
        '<div style="background:#ff8800;color:#000;padding:10px;text-align:center;font-weight:bold">'
        '⏸ BOT EN PAUSA</div>' if st.paused else ""
    )
    dry_banner = (
        '<div style="background:#3366ff;color:#fff;padding:8px;text-align:center">'
        '🔸 MODO DRY-RUN — sin órdenes reales</div>' if DRY_RUN else ""
    )
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<title>SAIYAN v4.0</title>
<style>
  body{{background:#0d0d0d;color:#e0e0e0;font-family:monospace;margin:20px}}
  h1{{color:#ff6600}} h2{{color:#ff9900;margin-top:30px}}
  .card{{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:15px;
         display:inline-block;margin:8px;min-width:140px;text-align:center}}
  .big{{font-size:1.8em;font-weight:bold}}
  table{{width:100%;border-collapse:collapse;margin-top:10px}}
  th{{background:#222;padding:8px;text-align:left;color:#ff9900}}
  td{{padding:6px 8px;border-bottom:1px solid #222}}
  tr:hover{{background:#1a1a1a}}
  .cfg{{background:#111;border:1px solid #333;padding:10px;border-radius:6px;font-size:0.85em;margin-top:10px}}
</style></head><body>
{paused_banner}{dry_banner}
<h1>🔥 SAIYAN AURA FUSION v4.0 — MULTI-CONFIRMATION</h1>
<div style="color:#888">Auto-refresh 30s · {now()}</div>
<div>
  <div class="card"><div style="color:#888">Balance</div><div class="big">${bal:.2f}</div><div style="color:#888">USDT</div></div>
  <div class="card"><div style="color:#888">PnL Total</div><div class="big" style="color:{color}">${st.total_pnl:+.2f}</div></div>
  <div class="card"><div style="color:#888">Hoy</div><div class="big" style="color:{'#00ff88' if st.daily_pnl>=0 else '#ff4444'}">${st.daily_pnl:+.2f}</div></div>
  <div class="card"><div style="color:#888">Win Rate</div><div class="big">{wr:.1f}%</div><div style="color:#888">{st.wins}W/{st.losses}L</div></div>
  <div class="card"><div style="color:#888">Profit Factor</div><div class="big">{st.pf():.2f}</div></div>
  <div class="card"><div style="color:#888">Expectancy</div><div class="big" style="color:{'#00ff88' if st.expectancy()>=0 else '#ff4444'}">${st.expectancy():.2f}</div></div>
  <div class="card"><div style="color:#888">Max DD</div><div class="big" style="color:#ff4444">{st.max_dd_real:.1f}%</div></div>
  <div class="card"><div style="color:#888">Rechazadas</div><div class="big" style="color:#ff8800">{st.rejected_signals}</div></div>
  <div class="card"><div style="color:#888">Uptime</div><div class="big" style="font-size:1.1em">{st.uptime()}</div></div>
</div>
<div class="cfg">
  <b style="color:#ff9900">🔍 Confirmaciones activas:</b>
  Multi-señal: <b>{MIN_CONFIRMATIONS}x</b> en <b>{CONFIRM_WINDOW}s</b> ·
  EMA <b>{EMA_FAST}/{EMA_SLOW}</b> ·
  RSI <b>{RSI_OS}–{RSI_OB}</b> ·
  Vol ><b>{VOL_MULT}x</b> ·
  Spread <<b>{MAX_SPREAD_PCT}%</b> ·
  ATR <b>{ATR_MIN_MULT}–{ATR_MAX_MULT}%</b> ·
  Funding <<b>{FUNDING_LIMIT}%</b> ·
  ATR-sizing: <b>{'ON' if ATR_SIZING else 'OFF'}</b> ·
  Horas bloq: <b>{BLOCK_HOURS if BLOCK_HOURS else 'ninguna'}</b>
</div>
<h2>📦 Posiciones abiertas ({st.n()}/{MAX_OPEN_TRADES})</h2>
<table>
  <tr><th>Símbolo</th><th>Lado</th><th>Entrada</th><th>Precio</th><th>PnL%</th><th>Trail</th><th>Score</th><th>Tiempo</th></tr>
  {open_rows or "<tr><td colspan='8' style='color:#666;text-align:center'>Sin posiciones</td></tr>"}
</table>
<h2>📜 Últimas 10 operaciones</h2>
<table>
  <tr><th>Fecha</th><th>Símbolo</th><th>Lado</th><th>PnL</th><th>Razón</th><th>Score</th><th>Duración</th></tr>
  {rows or "<tr><td colspan='7' style='color:#666;text-align:center'>Sin historial</td></tr>"}
</table>
</body></html>"""
    return Response(html, mimetype="text/html")

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        log.info(f"Webhook: {data}")

        incoming_secret = data.get("secret", data.get("passphrase", ""))
        if incoming_secret != WEBHOOK_SECRET:
            log.warning(f"Webhook no autorizado")
            return jsonify({"error": "unauthorized"}), 401

        action = str(data.get("action", data.get("signal", ""))).strip().lower()
        symbol = str(data.get("symbol", data.get("ticker", ""))).strip()

        if not action or not symbol:
            return jsonify({"error": "missing action or symbol"}), 400

        st.reset_daily()

        if   action in ("buy", "long entry", "long_entry"):
            res = open_trade(symbol, "long")
        elif action in ("sell", "short entry", "short_entry"):
            res = open_trade(symbol, "short")
        elif action in ("long_exit", "long exit"):
            res = close_trade(symbol, "LONG EXIT")
        elif action in ("short_exit", "short exit"):
            res = close_trade(symbol, "SHORT EXIT")
        elif action in ("stop_loss", "stop loss"):
            res = close_trade(symbol, "STOP LOSS")
        elif "tp1" in action:
            res = handle_tp(symbol, "TP1")
        elif "tp2" in action:
            res = handle_tp(symbol, "TP2")
        elif "tp3" in action or "take profit" in action:
            res = handle_tp(symbol, "TP3")
        elif "close" in action:
            res = close_trade(symbol, "CLOSE SIGNAL")
        else:
            return jsonify({"error": f"unknown action: {action}"}), 400

        return jsonify(res), 200

    except Exception as e:
        log.exception(f"Webhook crash: {e}")
        msg_error(f"Webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/test_telegram", methods=["GET"])
def test_telegram():
    try:
        tg(f"🧪 <b>Test Telegram OK</b> — SAIYAN v4.0\n⏰ {now()}")
        return jsonify({"result": "ok"})
    except Exception as e:
        return jsonify({"result": "error", "detail": str(e)}), 500

@app.route("/test_exchange", methods=["GET"])
def test_exchange():
    try:
        bal = balance()
        return jsonify({"result": "ok", "balance_usdt": bal})
    except Exception as e:
        return jsonify({"result": "error", "detail": str(e)}), 500


# ─────────────────────────────────────────────────────────────────
# WORKERS
# ─────────────────────────────────────────────────────────────────
def _heartbeat_worker():
    time.sleep(90)
    while True:
        msg_heartbeat()
        time.sleep(HEARTBEAT_MIN * 60)

def _daily_summary_worker():
    while True:
        now_utc  = datetime.now(timezone.utc)
        tomorrow = (now_utc + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        time.sleep((tomorrow - now_utc).total_seconds())
        msg_daily_summary()
        with _lock:
            st.daily_pnl      = 0.0
            st.daily_reset_ts = time.time()
        save_state()

def _autosave_worker():
    while True:
        time.sleep(300)
        save_state()

def _cache_warmer():
    """Pre-calienta cache de indicadores para posiciones abiertas."""
    time.sleep(120)
    while True:
        with _lock:
            symbols = list(st.trades.keys())
        for sym_ in symbols:
            try:
                invalidate_market_cache(sym_)
                get_market_data(sym_)
            except Exception:
                pass
        time.sleep(300)  # refrescar cada 5min

# ─────────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────────
def startup():
    load_state()
    log.info("━" * 62)
    log.info("  SAIYAN AURA FUSION BOT v4.0 — Starting...")
    log.info("━" * 62)
    log.info(f"  USDT/trade: ${FIXED_USDT} | Leverage: {LEVERAGE}x | Max: {MAX_OPEN_TRADES}")
    log.info(f"  TP1:{TP1_PCT}% TP2:{TP2_PCT}% TP3:{TP3_PCT}% SL:{SL_PCT}%")
    log.info(f"  Trailing: +{TRAILING_ACTIVATE}% activa | step {TRAILING_PCT}%")
    log.info(f"  CB:{CB_DD}% | Daily:{DAILY_LOSS_PCT}% | Cooldown:{COOLDOWN_MIN}min")
    log.info(f"  DRY-RUN: {'YES ⚠️' if DRY_RUN else 'NO'}")
    log.info(f"  Confirmaciones: multi={MIN_CONFIRMATIONS}x/{CONFIRM_WINDOW}s EMA{EMA_FAST}/{EMA_SLOW} RSI{RSI_OS}-{RSI_OB}")
    log.info(f"  Filtros: Vol>{VOL_MULT}x Spread<{MAX_SPREAD_PCT}% ATR{ATR_MIN_MULT}-{ATR_MAX_MULT}% Fund<{FUNDING_LIMIT}%")
    log.info(f"  ATR-sizing: {ATR_SIZING} | Horas bloq: {BLOCK_HOURS}")
    log.info("━" * 62)

    if not (API_KEY and API_SECRET):
        log.warning("⚠ BINGX_API_KEY/SECRET no configurados")
    if not (TG_TOKEN and TG_CHAT_ID):
        log.warning("⚠ TELEGRAM_BOT_TOKEN/CHAT_ID no configurados")
    if WEBHOOK_SECRET == "saiyan2024":
        log.warning("⚠ Usando WEBHOOK_SECRET por defecto — cámbialo en producción")

    for attempt in range(10):
        try:
            bal = balance()
            if st.peak_equity == 0:
                st.peak_equity = bal
            st.daily_reset_ts = time.time()
            log.info(f"✓ Balance: ${bal:.2f} USDT")
            log.info(f"✓ Historial: {st.total_trades} trades")
            msg_start(bal)
            break
        except Exception as e:
            wait = min(2 ** attempt, 60)
            log.warning(f"Startup {attempt+1}/10: {e} — retry {wait}s")
            time.sleep(wait)
    else:
        log.error("❌ No se pudo conectar con BingX")
        tg(f"❌ <b>SAIYAN v4.0 — ERROR INICIO</b>\nNo conectó con BingX\n⏰ {now()}")


# ─────────────────────────────────────────────────────────────────
# LAUNCH
# ─────────────────────────────────────────────────────────────────
threading.Thread(target=startup,               daemon=True, name="startup").start()
threading.Thread(target=_trailing_worker,      daemon=True, name="trailing").start()
threading.Thread(target=_heartbeat_worker,     daemon=True, name="heartbeat").start()
threading.Thread(target=_daily_summary_worker, daemon=True, name="daily_summary").start()
threading.Thread(target=_autosave_worker,      daemon=True, name="autosave").start()
threading.Thread(target=_tg_retry_worker,      daemon=True, name="tg_retry").start()
threading.Thread(target=_tg_commands_worker,   daemon=True, name="tg_commands").start()
threading.Thread(target=_cache_warmer,         daemon=True, name="cache_warmer").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
