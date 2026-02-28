"""
╔══════════════════════════════════════════════════════════════════╗
║         SATY ELITE v18 — DCA + ANTI-LOSS                          ║
║         BingX Perpetual Futures · 12 Trades · 24/7             ║
╠══════════════════════════════════════════════════════════════════╣
║  NUEVO v15 — 4 Pine Scripts + Whale Signals:                         ║
║                                                                  ║
║  1. UTBot (HPotter/Yo_adriiiiaan)                               ║
║     · ATR Trailing Stop line con Key Value configurable         ║
║     · Señal: EMA cruza ATR Trailing Stop → punto score 13       ║
║     · UTBot trailing como 2ª capa de protección                 ║
║                                                                  ║
║  2. Instrument-Z (OscillateMatrix)                              ║
║     · WaveTrend (TCI) oscillator → puntos score 14             ║
║     · Divergencias WaveTrend                                    ║
║     · TP/SL diferenciados UpTrend vs DownTrend                  ║
║     · Trade Expiration (cierre por barras máximas)              ║
║     · Mínimo profit para salidas de señal                       ║
║                                                                  ║
║  3. Bj Bot (3Commas framework)                                  ║
║     · Stops basados en Swing H/L + ATR buffer                  ║
║     · R:R ratio configurable (Risk to Reward)                   ║
║     · Trail trigger a X% del reward (rrExit)                   ║
║     · MA cross signal → punto score 15                          ║
║                                                                  ║
║  4. BB+RSI (rouxam)                                             ║
║     · Bollinger Bands oversold/overbought                       ║
║     · BB signal filtrada por RSI → punto score 16              ║
║                                                                  ║
║  Score total: 16 puntos (antes 12)                              ║
║  Score mínimo recomendado: 5 (ajustar según perfil)             ║
╚══════════════════════════════════════════════════════════════════╝

VARIABLES OBLIGATORIAS:
    BINGX_API_KEY  BINGX_API_SECRET
    TELEGRAM_BOT_TOKEN  TELEGRAM_CHAT_ID

VARIABLES OPCIONALES — GENERALES:
    MAX_OPEN_TRADES   def:12    FIXED_USDT      def:8
    MIN_SCORE         def:5     MAX_DRAWDOWN    def:15
    DAILY_LOSS_LIMIT  def:8     MIN_VOLUME_USDT def:100000
    TOP_N_SYMBOLS     def:300   POLL_SECONDS    def:60
    TIMEFRAME         def:5m    HTF1            def:15m
    HTF2              def:1h    BTC_FILTER      def:true
    COOLDOWN_MIN      def:20    MAX_SPREAD_PCT  def:1.0
    BLACKLIST

VARIABLES — SMI (Stochastic Momentum Index):
    SMI_K_LEN  def:10   SMI_D_LEN  def:3
    SMI_EMA_LEN def:10  SMI_SMOOTH def:5
    SMI_OB     def:40   SMI_OS     def:-40

VARIABLES — UTBOT (ATR Trailing Stop):
    UTBOT_KEY_VALUE   def:10   sensibilidad (+ bajo = + sensible)
    UTBOT_ATR_PERIOD  def:10   periodo ATR del trailing stop

VARIABLES — WAVETREND (Instrument-Z):
    WT_CHAN_LEN   def:9    Canal EMA
    WT_AVG_LEN    def:12   Media EMA
    WT_OB         def:60   Sobrecompra
    WT_OS         def:-60  Sobreventa

VARIABLES — BB+RSI (Bollinger Bands):
    BB_PERIOD  def:20   periodo de la BB
    BB_STD     def:2.0  desviaciones estándar
    BB_RSI_OB  def:65   RSI máximo para señal long

VARIABLES — BJ BOT (Risk Management):
    RNR        def:2.0  Risk to Reward ratio (TP = RnR × Risk)
    RISK_MULT  def:1.0  Buffer ATR para stop (detrás del swing)
    RR_EXIT    def:0.5  % del reward para activar trailing (0=inmediato)
    SWING_LB   def:10   Lookback swing high/low (redefinible)
    MIN_PROFIT_PCT def:0.0  Mínimo profit % para cerrar por señal
    TRADE_EXPIRE_BARS def:0 Barras máx por trade (0=desactivado)
"""

import os, time, logging, csv, json, collections
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import ccxt
import pandas as pd
import numpy as np

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("saty_v18")

# ══════════════════════════════════════════════════════════
# CONFIG — variables de entorno
# ══════════════════════════════════════════════════════════
API_KEY    = os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
TF         = os.environ.get("TIMEFRAME",  "5m")
HTF1       = os.environ.get("HTF1",       "15m")
HTF2       = os.environ.get("HTF2",       "1h")
POLL_SECS  = int(os.environ.get("POLL_SECONDS", "60"))
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")

_bl = os.environ.get("BLACKLIST", "")
BLACKLIST: List[str] = [s.strip() for s in _bl.split(",") if s.strip()]

# ── Capital ──
FIXED_USDT       = 8.0  # Fijo: 8 USDT por trade con 12× apalancamiento
LEVERAGE         = 12   # Apalancamiento fijo 12×
MAX_OPEN_TRADES  = int(os.environ.get("MAX_OPEN_TRADES",    "2"))    # FIX#4: Máx 2 con $50 — 6 trades = 96% capital = cuenta muerta
MIN_SCORE        = int(os.environ.get("MIN_SCORE",          "15"))   # FIX#4: 15/25 — APE con 19/25 perdió igual, necesitamos calidad no cantidad
CB_DD            = float(os.environ.get("MAX_DRAWDOWN",     "8.0"))  # 8% — para antes de que sea tarde
DAILY_LOSS_LIMIT = float(os.environ.get("DAILY_LOSS_LIMIT", "4.0"))  # 4% diario máx = ~$2 con $50
COOLDOWN_MIN     = int(os.environ.get("COOLDOWN_MIN",       "30"))   # FIX#2: 30 min — STX se abrió y cerró en el mismo minuto
MAX_SPREAD_PCT   = float(os.environ.get("MAX_SPREAD_PCT",   "0.8"))  # Spread estricto
MIN_VOLUME_USDT  = float(os.environ.get("MIN_VOLUME_USDT",  "1000000")) # FIX#4: 1M vol — solo pares top líquidos
TOP_N_SYMBOLS    = int(os.environ.get("TOP_N_SYMBOLS",      "300"))
BTC_FILTER       = os.environ.get("BTC_FILTER", "false").lower() == "true"  # OFF — captura long y short libremente

# ── SMI ──
SMI_K_LEN   = int(os.environ.get("SMI_K_LEN",   "10"))
SMI_D_LEN   = int(os.environ.get("SMI_D_LEN",   "3"))
SMI_EMA_LEN = int(os.environ.get("SMI_EMA_LEN", "10"))
SMI_SMOOTH  = int(os.environ.get("SMI_SMOOTH",  "5"))
SMI_OB      = float(os.environ.get("SMI_OB",    "40.0"))
SMI_OS      = float(os.environ.get("SMI_OS",    "-40.0"))

# ── UTBot (ATR Trailing Stop) ──
UTBOT_KEY    = float(os.environ.get("UTBOT_KEY_VALUE",  "10.0"))
UTBOT_ATR    = int(os.environ.get("UTBOT_ATR_PERIOD",  "10"))

# ── WaveTrend (Instrument-Z) ──
WT_CHAN_LEN = int(os.environ.get("WT_CHAN_LEN", "9"))
WT_AVG_LEN  = int(os.environ.get("WT_AVG_LEN", "12"))
WT_OB       = float(os.environ.get("WT_OB",    "60.0"))
WT_OS       = float(os.environ.get("WT_OS",    "-60.0"))

# ── Bollinger Bands + RSI ──
BB_PERIOD  = int(os.environ.get("BB_PERIOD", "20"))
BB_STD     = float(os.environ.get("BB_STD",  "2.0"))
BB_RSI_OB  = float(os.environ.get("BB_RSI_OB", "65.0"))

# ── Bj Bot Risk Management ──
RNR              = float(os.environ.get("RNR",               "2.5"))  # TP2 = 2.5× el riesgo — más ganancia por winner
RISK_MULT        = float(os.environ.get("RISK_MULT",         "0.9"))  # SL ligeramente más ajustado — menos pérdida
RR_EXIT          = float(os.environ.get("RR_EXIT",           "0.4"))  # Trail activo al 40% del TP2 — protege antes
MIN_PROFIT_PCT   = float(os.environ.get("MIN_PROFIT_PCT",    "0.0"))
TRADE_EXPIRE_BARS= int(os.environ.get("TRADE_EXPIRE_BARS",  "0"))

# ── Indicadores clásicos ──
FAST_LEN  = 8;   PIVOT_LEN = 21; BIAS_LEN  = 48; SLOW_LEN  = 200
ADX_LEN   = 14;  ADX_MIN   = 16; RSI_LEN   = 14; ATR_LEN   = 14
VOL_LEN   = 20;  OSC_LEN   = 3;  SWING_LB  = int(os.environ.get("SWING_LB", "10"))
MACD_FAST = 12;  MACD_SLOW = 26; MACD_SIG  = 9

# ── Exits ──
TP1_ATR_MULT = 1.2
SL_ATR_MULT  = 1.0

# ── RSI extremo ──
RSI_OB_LOW = 10; RSI_OB_HIGH = 25
RSI_OS_LOW = 78; RSI_OS_HIGH = 90

# ── Risk ──
MAX_CONSEC_LOSS = 2   # Tras 2 pérdidas seguidas → reduce tamaño a la mitad
USE_CB          = True
HEDGE_MODE: bool = False
CSV_PATH = "/tmp/saty_v18_trades.csv"


# ══════════════════════════════════════════════════════════
# CACHE OHLCV
# ══════════════════════════════════════════════════════════
_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 55

def fetch_df(ex: ccxt.Exchange, symbol: str, tf: str, limit: int = 400) -> pd.DataFrame:
    key = f"{symbol}|{tf}"
    now = time.time()
    if key in _cache:
        ts, df = _cache[key]
        if now - ts < CACHE_TTL:
            return df
    raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df  = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    _cache[key] = (now, df)
    return df

def clear_cache():
    _cache.clear()


# ══════════════════════════════════════════════════════════
# TRADING BRAIN — Aprendizaje de errores (v16)
# Registra cada trade cerrado y analiza patrones para mejorar
# Sin ML, sin GPU — estadística pura sobre trades reales
# ══════════════════════════════════════════════════════════
BRAIN_PATH = "/tmp/saty_v18_brain.json"

class TradingBrain:
    """
    Aprende de cada trade cerrado:
    - Qué scores realmente ganan vs pierden
    - Qué indicadores correlacionan con wins
    - Qué pares están en racha positiva o negativa
    - Qué horas del día tienen mejor win rate
    - Ajusta MIN_SCORE dinámicamente si hay datos suficientes
    """
    def __init__(self):
        self.trades: List[dict] = []          # historial completo
        self.pair_stats: Dict[str, dict] = {} # stats por par
        self.score_stats: Dict[int, dict] = {}# stats por score
        self.hour_stats:  Dict[int, dict] = {}# stats por hora UTC
        self.factor_wins: Dict[str, list] = collections.defaultdict(list)  # factor→[1/0]
        self.blacklist:   Dict[str, float] = {}  # par→timestamp_expiry
        self.adaptive_min_score: int = MIN_SCORE # score ajustado dinámicamente
        self.total_trades: int = 0
        self.load()

    def load(self):
        try:
            if os.path.exists(BRAIN_PATH):
                with open(BRAIN_PATH) as f:
                    data = json.load(f)
                self.trades       = data.get("trades", [])[-500:]  # últimos 500
                self.pair_stats   = data.get("pair_stats", {})
                self.score_stats  = {int(k): v for k, v in data.get("score_stats", {}).items()}
                self.hour_stats   = {int(k): v for k, v in data.get("hour_stats", {}).items()}
                self.factor_wins  = collections.defaultdict(list, data.get("factor_wins", {}))
                self.blacklist    = data.get("blacklist", {})
                self.adaptive_min_score = data.get("adaptive_min_score", MIN_SCORE)
                self.total_trades = data.get("total_trades", 0)
                log.info(f"🧠 TradingBrain cargado: {self.total_trades} trades históricos")
        except Exception as e:
            log.warning(f"TradingBrain load error: {e}")

    def save(self):
        try:
            with open(BRAIN_PATH, "w") as f:
                json.dump({
                    "trades":             self.trades[-500:],
                    "pair_stats":         self.pair_stats,
                    "score_stats":        self.score_stats,
                    "hour_stats":         self.hour_stats,
                    "factor_wins":        dict(self.factor_wins),
                    "blacklist":          self.blacklist,
                    "adaptive_min_score": self.adaptive_min_score,
                    "total_trades":       self.total_trades,
                }, f, indent=2)
        except Exception as e:
            log.warning(f"TradingBrain save error: {e}")

    def is_blacklisted(self, symbol: str) -> bool:
        """True si el par está temporalmente bloqueado por malas rachas."""
        expiry = self.blacklist.get(symbol, 0)
        if time.time() < expiry:
            return True
        elif symbol in self.blacklist:
            del self.blacklist[symbol]
        return False

    def record_trade(self, symbol: str, side: str, score: int,
                     pnl: float, reason: str, row: Optional[pd.Series] = None):
        """Registra un trade cerrado y actualiza todas las estadísticas."""
        win = 1 if pnl > 0 else 0
        hour = datetime.now(timezone.utc).hour
        self.total_trades += 1

        # ── Registro completo ──
        record = {
            "ts": time.time(), "symbol": symbol, "side": side,
            "score": score, "pnl": pnl, "win": win, "reason": reason,
            "hour": hour,
        }
        # Registrar qué indicadores estaban activos
        if row is not None:
            factors = {}
            for f in ["st_bull","st_bear","above_vwap","below_vwap","bos_bull",
                      "ob_bull","ob_bear","cvd_bull_div","cvd_bear_div",
                      "utbot_buy","utbot_sell","wt_cross_up","wt_cross_dn",
                      "macd_cross_up","macd_cross_down","regime_trend"]:
                try: factors[f] = bool(row.get(f, False))
                except: pass
            record["factors"] = factors
            for f, active in factors.items():
                if active:
                    self.factor_wins[f].append(win)

        self.trades.append(record)

        # ── Stats por par ──
        if symbol not in self.pair_stats:
            self.pair_stats[symbol] = {"wins": 0, "losses": 0, "pnl": 0.0, "streak": 0}
        ps = self.pair_stats[symbol]
        ps["pnl"] += pnl
        if win:
            ps["wins"] += 1
            ps["streak"] = max(0, ps["streak"]) + 1
        else:
            ps["losses"] += 1
            ps["streak"] = min(0, ps["streak"]) - 1
            # 3 pérdidas seguidas en un par → blacklist 4 horas
            if ps["streak"] <= -3:
                self.blacklist[symbol] = time.time() + 4 * 3600
                log.warning(f"🧠 {symbol} blacklisted 4h (3 losses streak)")

        # ── Stats por score ──
        sc = self.score_stats.setdefault(score, {"wins": 0, "losses": 0})
        if win: sc["wins"] += 1
        else:   sc["losses"] += 1

        # ── Stats por hora ──
        hr = self.hour_stats.setdefault(hour, {"wins": 0, "losses": 0})
        if win: hr["wins"] += 1
        else:   hr["losses"] += 1

        # ── Adaptar MIN_SCORE cada 30 trades ──
        if self.total_trades % 30 == 0:
            self._adapt_min_score()

        self.save()

    def _adapt_min_score(self):
        """Ajusta MIN_SCORE basándose en datos reales de win rate por score."""
        global MIN_SCORE
        if len(self.trades) < 20:
            return

        best_score = MIN_SCORE
        best_wr    = 0.0

        for sc, data in self.score_stats.items():
            total = data["wins"] + data["losses"]
            if total < 5:
                continue
            wr = data["wins"] / total
            if wr > best_wr:
                best_wr    = wr
                best_score = sc

        # Solo ajustar si hay diferencia significativa y datos suficientes
        if best_wr > 0.55 and abs(best_score - self.adaptive_min_score) <= 2:
            old = self.adaptive_min_score
            # Encontrar el score mínimo con win rate > 50%
            for sc in sorted(self.score_stats.keys()):
                d = self.score_stats[sc]
                total = d["wins"] + d["losses"]
                if total >= 5 and d["wins"] / total < 0.45:
                    new_min = sc + 1
                    if new_min != old:
                        self.adaptive_min_score = new_min
                        MIN_SCORE = new_min
                        log.info(f"🧠 MIN_SCORE ajustado: {old}→{new_min} "
                                 f"(WR análisis de {self.total_trades} trades)")
                    break

    def get_report(self) -> str:
        """Genera un resumen del aprendizaje para Telegram."""
        if self.total_trades < 5:
            return f"🧠 Brain: {self.total_trades} trades — datos insuficientes aún"

        recent = [t for t in self.trades[-30:]]
        if not recent:
            return "🧠 Sin datos recientes"

        wins    = sum(1 for t in recent if t["win"])
        wr      = wins / len(recent) * 100
        avg_pnl = sum(t["pnl"] for t in recent) / len(recent)

        # Mejor y peor score
        score_report = []
        for sc in sorted(self.score_stats.keys()):
            d = self.score_stats[sc]
            total = d["wins"] + d["losses"]
            if total >= 3:
                score_report.append(f"  {sc}/25: {d['wins']}W/{d['losses']}L "
                                    f"({d['wins']/total*100:.0f}%)")

        # Mejor factor
        best_factors = []
        for f, results in self.factor_wins.items():
            if len(results) >= 5:
                best_factors.append((f, sum(results)/len(results), len(results)))
        best_factors.sort(key=lambda x: -x[1])

        bl_active = sum(1 for exp in self.blacklist.values() if time.time() < exp)

        lines = [
            f"🧠 <b>TRADING BRAIN REPORT</b>",
            f"Total: {self.total_trades} trades | MIN_SCORE adaptado: {self.adaptive_min_score}/25",
            f"Últimos 30: {wins}W/{len(recent)-wins}L = {wr:.0f}% | Avg: ${avg_pnl:+.3f}",
            f"🚫 Pares bloqueados: {bl_active}",
        ]
        if score_report:
            lines.append("📊 Win rate por score:")
            lines.extend(score_report[:6])
        if best_factors:
            lines.append("⭐ Mejores indicadores:")
            for name, wr_f, n in best_factors[:3]:
                lines.append(f"  {name}: {wr_f*100:.0f}% ({n} trades)")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# GRID STATE — Estado de operaciones grid en lateral (v16)
# ══════════════════════════════════════════════════════════
@dataclass
class GridLevel:
    price:     float = 0.0
    side:      str   = ""    # "buy" o "sell"
    order_id:  str   = ""
    filled:    bool  = False
    pnl:       float = 0.0

@dataclass
class GridTrade:
    symbol:    str   = ""
    center:    float = 0.0    # precio central del grid
    spacing:   float = 0.0    # separación entre niveles (ATR-based)
    levels:    list  = field(default_factory=list)  # List[GridLevel]
    ts_open:   float = 0.0
    total_pnl: float = 0.0
    n_trades:  int   = 0


# ══════════════════════════════════════════════════════════
# ESTADO
# ══════════════════════════════════════════════════════════
@dataclass
class TradeState:
    symbol:           str   = ""
    side:             str   = ""
    base:             str   = ""
    entry_price:      float = 0.0
    tp1_price:        float = 0.0
    tp2_price:        float = 0.0
    sl_price:         float = 0.0
    sl_moved_be:      bool  = False
    tp1_hit:          bool  = False
    trail_high:       float = 0.0
    trail_low:        float = 0.0
    peak_price:       float = 0.0
    prev_price:       float = 0.0
    stall_count:      int   = 0
    trail_phase:      str   = "normal"
    max_profit_pct:   float = 0.0
    entry_score:      int   = 0
    entry_time:       str   = ""
    contracts:        float = 0.0
    atr_entry:        float = 0.0
    smi_entry:        float = 0.0
    wt_entry:         float = 0.0
    utbot_stop:       float = 0.0   # UTBot ATR trailing stop at entry
    bar_count:        int   = 0     # barras desde entrada (trade expiry)
    uptrend_entry:    bool  = True  # era uptrend en la entrada
    whale_desc:       str   = ""    # whale + saint grail signals v15
    rr_trail_active:  bool  = False # R:R trail trigger activado (Bj Bot)
    rr_trail_stop:    float = 0.0   # nivel del trailing Bj Bot
    # ── DCA Averaging (v18) ──────────────────────────────────
    dca_count:        int   = 0     # cuántas órdenes DCA ejecutadas
    dca_avg_price:    float = 0.0   # precio promedio ponderado tras DCA
    dca_total_usdt:   float = 0.0   # capital total comprometido (base + DCAs)
    dca_total_contr:  float = 0.0   # contratos totales acumulados
    dca_next_price:   float = 0.0   # precio al que se dispara la próxima orden DCA
    dca_sl_price:     float = 0.0   # SL duro tras el último DCA


@dataclass
class BotState:
    wins:           int   = 0
    losses:         int   = 0
    gross_profit:   float = 0.0
    gross_loss:     float = 0.0
    consec_losses:  int   = 0
    peak_equity:    float = 0.0
    total_pnl:      float = 0.0
    daily_pnl:      float = 0.0
    daily_reset_ts: float = 0.0
    last_heartbeat: float = 0.0
    trades:       Dict[str, TradeState] = field(default_factory=dict)
    grid_trades:  Dict[str, GridTrade]  = field(default_factory=dict)  # v16 grid
    cooldowns:    Dict[str, float]      = field(default_factory=dict)
    rsi_alerts:   Dict[str, float]      = field(default_factory=dict)
    btc_bull: bool  = True
    btc_bear: bool  = False
    btc_rsi:  float = 50.0

    def open_count(self) -> int: return len(self.trades)
    def bases_open(self) -> Dict[str, str]:
        return {t.base: t.side for t in self.trades.values()}
    def base_has_trade(self, base: str) -> bool:
        return base in self.bases_open()
    def win_rate(self) -> float:
        t = self.wins + self.losses
        return (self.wins / t * 100) if t else 0.0
    def profit_factor(self) -> float:
        return (self.gross_profit / self.gross_loss) if self.gross_loss else 0.0
    def score_bar(self, score: int, mx: int = 25) -> str:
        return "█" * min(score, mx) + "░" * (mx - min(score, mx))
    def cb_active(self) -> bool:
        if not USE_CB or self.peak_equity <= 0: return False
        dd = (self.peak_equity - (self.peak_equity + self.total_pnl)) / self.peak_equity * 100
        return dd >= CB_DD
    def daily_limit_hit(self) -> bool:
        if self.peak_equity <= 0: return False
        return self.daily_pnl < 0 and abs(self.daily_pnl) / self.peak_equity * 100 >= DAILY_LOSS_LIMIT
    def risk_mult(self) -> float:
        return 0.5 if self.consec_losses >= MAX_CONSEC_LOSS else 1.0
    def in_cooldown(self, symbol: str) -> bool:
        return time.time() - self.cooldowns.get(symbol, 0) < COOLDOWN_MIN * 60
    def set_cooldown(self, symbol: str):
        self.cooldowns[symbol] = time.time()
    def reset_daily(self):
        now = time.time()
        if now - self.daily_reset_ts > 86400:
            self.daily_pnl = 0.0; self.daily_reset_ts = now
            log.info("Daily PnL reseteado")


state = BotState()
brain = TradingBrain()  # 🧠 v16 — aprende de cada trade


# ══════════════════════════════════════════════════════════
# CSV LOG
# ══════════════════════════════════════════════════════════
def log_csv(action: str, t: TradeState, price: float, pnl: float = 0.0):
    try:
        exists = os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts","action","symbol","base","side","score",
                            "smi","wt","entry","exit","pnl","contracts","bars"])
            w.writerow([utcnow(), action, t.symbol, t.base, t.side,
                        t.entry_score, round(t.smi_entry,2), round(t.wt_entry,2),
                        t.entry_price, price, round(pnl,4), t.contracts, t.bar_count])
    except Exception as e:
        log.warning(f"CSV: {e}")


# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════
def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def smi_label(smi: float) -> str:
    if smi >= SMI_OB:  return f"🔴 SMI OB {smi:.1f}"
    if smi <= SMI_OS:  return f"🟢 SMI OS {smi:.1f}"
    if smi > 0:        return f"⚪ SMI {smi:.1f}↑"
    return                    f"⚪ SMI {smi:.1f}↓"

def wt_label(wt: float) -> str:
    if wt >= WT_OB:  return f"🔴 WT OB {wt:.1f}"
    if wt <= WT_OS:  return f"🟢 WT OS {wt:.1f}"
    if wt > 0:       return f"⚪ WT {wt:.1f}↑"
    return                  f"⚪ WT {wt:.1f}↓"

def rsi_extreme_long(rsi: float) -> bool:
    return RSI_OB_LOW <= rsi <= RSI_OB_HIGH

def rsi_extreme_short(rsi: float) -> bool:
    return RSI_OS_LOW <= rsi <= RSI_OS_HIGH

def rsi_zone_label(rsi: float) -> str:
    if rsi < RSI_OB_LOW:   return f"⚠️ RSI HIPERVENTA {rsi:.1f}"
    if rsi <= RSI_OB_HIGH: return f"🔥 RSI SOBREVENTA {rsi:.1f}"
    if rsi < 42:            return f"🟢 RSI bajo {rsi:.1f}"
    if rsi <= 58:           return f"⚪ RSI neutral {rsi:.1f}"
    if rsi < RSI_OS_LOW:   return f"🟡 RSI alto {rsi:.1f}"
    if rsi <= RSI_OS_HIGH: return f"🔥 RSI SOBRECOMPRA {rsi:.1f}"
    return                        f"⚠️ RSI HIPERCOMPRA {rsi:.1f}"


# ══════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════
def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.warning(f"TG: {e}")

def tg_startup(balance: float, n: int):
    tg(
        f"<b>🚀 SATY ELITE v18 — DCA + ANTI-LOSS</b>\n"
        f"══════════════════════════════\n"
        f"🌍 Universo: {n} pares | Vol≥${MIN_VOLUME_USDT/1000:.0f}K\n"
        f"⚙️ Modo: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'} | 24/7\n"
        f"⏱ {TF} · {HTF1} · {HTF2} | Leverage: {LEVERAGE}×\n"
        f"🎯 Score min: {MIN_SCORE}/25 | Max trades: {MAX_OPEN_TRADES}\n"
        f"💰 Balance: ${balance:.2f} | ${FIXED_USDT:.0f} × {LEVERAGE}× = ${FIXED_USDT*LEVERAGE:.0f} notional/trade\n"
        f"🛡 CB: -{CB_DD}% | Límite diario: -{DAILY_LOSS_LIMIT}% | Consec: {MAX_CONSEC_LOSS}\n"
        f"📐 R:R={RNR} | Trail activo al {RR_EXIT*100:.0f}% | SL mult={RISK_MULT}\n"
        f"⏳ Cooldown: {COOLDOWN_MIN}min | Spread máx: {MAX_SPREAD_PCT}%\n"
        f"₿ Filtro BTC: {'✅' if BTC_FILTER else '❌ (long+short libre)'}\n"
        f"🐋 Whale: FR + OI + L/S ratio + Sesión\n"
        f"⚜️ Saint Grail: VWAP + Supertrend + CVD + SMC + Regime\n"
        f"🚫 Regime Chop Filter: activo (bloquea mercados laterales)\n"
        f"📉 DCA Averaging: {'✅' if DCA_ENABLED else '❌'} | Máx {DCA_MAX_ORDERS} órdenes | Paso {DCA_STEP_PCT}% | TP +{DCA_TP_PCT}%\n"
        f"🚫 Macro Override: BTC RSI>75=no shorts | RSI<25=no longs\n"
        f"══════════════════════════════\n"
        f"⏰ {utcnow()}"
    )

def tg_signal(t: TradeState, row: pd.Series):
    e      = "🟢" if t.side == "long" else "🔴"
    sl_d   = abs(t.sl_price - t.entry_price)
    rr1    = abs(t.tp1_price - t.entry_price) / max(sl_d, 1e-9)
    rr2    = abs(t.tp2_price - t.entry_price) / max(sl_d, 1e-9)
    smi_v  = float(row.get("smi", 0.0))
    wt_v   = float(row.get("wt1", 0.0))
    ut_stop= float(row.get("utbot_stop", 0.0))
    trend  = "📈 UpTrend" if t.uptrend_entry else "📉 DownTrend"
    tg(
        f"{e} <b>{'LONG' if t.side=='long' else 'SHORT'}</b> — {t.symbol}\n"
        f"══════════════════════════════\n"
        f"🎯 Score: {t.entry_score}/25  {state.score_bar(t.entry_score)}\n"
        f"📊 {trend}\n"
        f"💵 Entrada: <code>{t.entry_price:.6g}</code>\n"
        f"🟡 TP1: <code>{t.tp1_price:.6g}</code> R:R 1:{rr1:.1f}\n"
        f"🟢 TP2: <code>{t.tp2_price:.6g}</code> R:R 1:{rr2:.1f}\n"
        f"🛑 SL: <code>{t.sl_price:.6g}</code> → BE tras TP1\n"
        f"🤖 UTBot Stop: <code>{ut_stop:.6g}</code>\n"
        f"══════════════════════════════\n"
        f"{smi_label(smi_v)} | {wt_label(wt_v)}\n"
        f"{rsi_zone_label(float(row['rsi']))} | ADX:{row['adx']:.1f}\n"
        f"MACD:{row['macd_hist']:.5f} | Vol:{row['volume']/row['vol_ma']:.2f}x\n"
        f"ATR:{t.atr_entry:.5f} | ${FIXED_USDT:.0f} × {LEVERAGE}×\n"
        f"🐋 {t.whale_desc}\n"
        f"⚜️ VWAP:{'✅' if row.get('above_vwap' if t.side=='long' else 'below_vwap') else '❌'} "
        f"ST:{'✅' if row.get('st_bull' if t.side=='long' else 'st_bear') else '❌'} "
        f"CVD:{'✅' if row.get('cvd_bull_div' if t.side=='long' else 'cvd_bear_div') else '❌'} "
        f"SMC:{'✅' if row.get('ob_bull' if t.side=='long' else 'ob_bear') else '❌'} "
        f"Régimen:{'✅' if row.get('regime_trend') else '❌'}\n"
        f"₿{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'} "
        f"RSI:{state.btc_rsi:.0f}\n"
        f"📊 {state.open_count()}/{MAX_OPEN_TRADES} trades\n"
        f"⏰ {utcnow()}"
    )

def tg_tp1_be(t: TradeState, price: float, pnl: float):
    tg(
        f"🟡 <b>TP1 + BREAK-EVEN</b> — {t.symbol}\n"
        f"💵 <code>{price:.6g}</code> | PnL parcial: ~${pnl:+.2f}\n"
        f"SMI:{t.smi_entry:.1f} | WT:{t.wt_entry:.1f}\n"
        f"🛡 SL → entrada <code>{t.entry_price:.6g}</code>\n"
        f"⏰ {utcnow()}"
    )

def tg_trail_phase(t: TradeState, phase: str, price: float,
                   retrace: float, trail_m: float):
    icons = {"normal": "🏃", "tight": "⚡", "locked": "🔒", "utbot": "🤖", "rr": "📐"}
    tg(
        f"{icons.get(phase,'⚡')} <b>TRAILING {phase.upper()}</b> — {t.symbol}\n"
        f"Precio: <code>{price:.6g}</code> | Peak: <code>{t.peak_price:.6g}</code>\n"
        f"Retroceso: {retrace:.1f}% | Mult: {trail_m}\n"
        f"Ganancia max: {t.max_profit_pct:.2f}%\n"
        f"⏰ {utcnow()}"
    )

def tg_close(reason: str, t: TradeState, exit_p: float, pnl: float):
    e   = "✅" if pnl > 0 else "❌"
    pct = (pnl / (t.entry_price * t.contracts) * 100) if t.contracts > 0 else 0
    dca_line = f"📉 DCA usado: {t.dca_count}× | Avg precio: {t.dca_avg_price:.6g}\n" if t.dca_count > 0 else ""
    tg(
        f"{e} <b>CERRADO</b> — {t.symbol}\n"
        f"📋 {t.side.upper()} · {t.entry_score}/25 · {reason}\n"
        f"💵 <code>{t.entry_price:.6g}</code> → <code>{exit_p:.6g}</code> ({pct:+.2f}%)\n"
        f"{dca_line}"
        f"{'💰' if pnl>0 else '💸'} PnL: ${pnl:+.2f} | Barras: {t.bar_count}\n"
        f"📊 {state.wins}W/{state.losses}L · WR:{state.win_rate():.1f}% · PF:{state.profit_factor():.2f}\n"
        f"💹 Hoy:${state.daily_pnl:+.2f} · Total:${state.total_pnl:+.2f}\n"
        f"⏰ {utcnow()}"
    )

def tg_rsi_alert(symbol: str, rsi: float, smi: float, wt: float,
                 ls: int, ss: int, price: float):
    direction = "📉 LONG rebote" if rsi_extreme_long(rsi) else "📈 SHORT caída"
    tg(
        f"🔔 <b>RSI EXTREMO</b> — {symbol}\n"
        f"{rsi_zone_label(rsi)}\n"
        f"{smi_label(smi)} | {wt_label(wt)}\n"
        f"💵 <code>{price:.6g}</code> | {direction}\n"
        f"Score: L:{ls}/25 S:{ss}/25\n"
        f"⏰ {utcnow()}"
    )

def tg_summary(signals: List[dict], n_scanned: int):
    open_lines = "\n".join(
        f"  {'🟢' if ts.side=='long' else '🔴'} {sym} E:{ts.entry_price:.5g} "
        f"WT:{ts.wt_entry:.1f} {'🛡' if ts.sl_moved_be else ''}"
        for sym, ts in state.trades.items()
    ) or "  (ninguna)"
    top = "\n".join(
        f"  {'🟢' if s['side']=='long' else '🔴'} {s['symbol']} "
        f"{s['score']}/25 {wt_label(s['wt'])}"
        for s in signals[:5]
    ) or "  (ninguna)"
    tg(
        f"📡 <b>RESUMEN</b> — {n_scanned} pares · {utcnow()}\n"
        f"Top señales:\n{top}\n"
        f"══════════════════════════════\n"
        f"Posiciones ({state.open_count()}/{MAX_OPEN_TRADES}):\n{open_lines}\n"
        f"══════════════════════════════\n"
        f"CB:{'⛔' if state.cb_active() else '✅'} Hoy:${state.daily_pnl:+.2f}\n"
        f"₿{'🟢' if state.btc_bull else '🔴'} {state.wins}W/{state.losses}L PF:{state.profit_factor():.2f}"
    )

def tg_heartbeat(balance: float):
    bases    = state.bases_open()
    open_str = ", ".join(f"{b}({'L' if s=='long' else 'S'})"
                         for b, s in bases.items()) or "ninguna"
    grids_str = ", ".join(state.grid_trades.keys()) or "ninguno"
    tg(
        f"💓 <b>HEARTBEAT</b> — {utcnow()}\n"
        f"Balance: ${balance:.2f} | Hoy: ${state.daily_pnl:+.2f}\n"
        f"Trades tendencia: {state.open_count()}/{MAX_OPEN_TRADES} | {open_str}\n"
        f"🔲 Grids activos: {len(state.grid_trades)} | {grids_str}\n"
        f"₿ {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'} "
        f"RSI:{state.btc_rsi:.0f}\n"
        f"\n{brain.get_report()}"
    )

def tg_error(msg: str):
    tg(f"🔥 <b>ERROR:</b> <code>{msg[:300]}</code>\n⏰ {utcnow()}")

def tg_manual_signal(symbol: str, side: str, score: int,
                     entry: float, tp1: float, tp2: float, sl: float,
                     atr: float, whale_desc: str, reason: str):
    """Alerta de operación manual cuando el bot no puede abrir por fondos insuficientes."""
    e = "🟢" if side == "long" else "🔴"
    sl_dist = abs(entry - sl)
    rr1 = abs(tp1 - entry) / max(sl_dist, 1e-9)
    rr2 = abs(tp2 - entry) / max(sl_dist, 1e-9)
    tg(
        f"👤 <b>SEÑAL MANUAL — {side.upper()}</b> {e} — {symbol}\n"
        f"══════════════════════════════\n"
        f"⚠️ <i>Bot no pudo abrir: {reason[:80]}</i>\n"
        f"══════════════════════════════\n"
        f"🎯 Score: {score}/25  |  12× apalancamiento\n"
        f"💵 Entrada: <code>{entry:.6g}</code>\n"
        f"🟡 TP1: <code>{tp1:.6g}</code>  (R:R 1:{rr1:.1f})\n"
        f"🟢 TP2: <code>{tp2:.6g}</code>  (R:R 1:{rr2:.1f})\n"
        f"🛑 SL:  <code>{sl:.6g}</code>\n"
        f"══════════════════════════════\n"
        f"📐 Margen necesario: ~${FIXED_USDT:.0f} USDT\n"
        f"📊 Contratos aprox: {(FIXED_USDT * LEVERAGE / entry):.4f}\n"
        f"🐋 {whale_desc}\n"
        f"⏰ {utcnow()}"
    )


# ══════════════════════════════════════════════════════════
# INDICADORES BASE
# ══════════════════════════════════════════════════════════
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()

def calc_atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def calc_rsi(s: pd.Series, n: int) -> pd.Series:
    d  = s.diff()
    g  = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    lo = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - (100 / (1 + g / lo.replace(0, np.nan)))

def calc_adx(df: pd.DataFrame, n: int) -> Tuple[pd.Series, pd.Series, pd.Series]:
    h, l   = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    pdm    = up.where((up > dn) & (up > 0), 0.0)
    mdm    = dn.where((dn > up) & (dn > 0), 0.0)
    atr_s  = calc_atr(df, n)
    dip    = 100 * pdm.ewm(span=n, adjust=False).mean() / atr_s
    dim    = 100 * mdm.ewm(span=n, adjust=False).mean() / atr_s
    dx     = 100 * (dip - dim).abs() / (dip + dim).replace(0, np.nan)
    return dip, dim, dx.ewm(span=n, adjust=False).mean()

def calc_macd(s: pd.Series):
    m  = ema(s, MACD_FAST) - ema(s, MACD_SLOW)
    sg = ema(m, MACD_SIG)
    return m, sg, m - sg


# ══════════════════════════════════════════════════════════
# SMI — Stochastic Momentum Index (Pine Script original)
# ══════════════════════════════════════════════════════════
def calc_smi(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    h, l, c = df["high"], df["low"], df["close"]
    ll      = l.rolling(SMI_K_LEN).min()
    hh      = h.rolling(SMI_K_LEN).max()
    diff    = hh - ll
    rdiff   = c - (hh + ll) / 2
    avgrel  = rdiff.ewm(span=SMI_D_LEN,  adjust=False).mean()
    avgdiff = diff.ewm(span=SMI_D_LEN,   adjust=False).mean()
    smi_raw = pd.Series(
        np.where(avgdiff.abs() > 1e-10, (avgrel / (avgdiff / 2)) * 100, 0.0),
        index=df.index
    )
    smoothed = smi_raw.rolling(SMI_SMOOTH).mean()
    signal   = smoothed.ewm(span=SMI_EMA_LEN, adjust=False).mean()
    return smoothed, signal


# ══════════════════════════════════════════════════════════
# UTBOT — ATR Trailing Stop (HPotter / Yo_adriiiiaan)
# Traducción exacta del Pine Script v2
# ══════════════════════════════════════════════════════════
def calc_utbot(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    xATR  = atr(ATR_PERIOD)
    nLoss = KEY_VALUE * xATR
    xATRTrailingStop logic (iff cascade):
      if close > prev_stop AND close[1] > prev_stop: max(prev_stop, close-nLoss)
      elif close < prev_stop AND close[1] < prev_stop: min(prev_stop, close+nLoss)
      elif close > prev_stop: close - nLoss
      else: close + nLoss
    buy  = close > stop AND ema(close,1) crosses above stop
    sell = close < stop AND ema(close,1) crosses below stop
    """
    atr_vals = calc_atr(df, UTBOT_ATR)
    n_loss   = UTBOT_KEY * atr_vals
    c        = df["close"]

    stop = pd.Series(0.0, index=df.index)
    c_arr    = c.values
    nl_arr   = n_loss.values
    st_arr   = stop.values

    for i in range(1, len(df)):
        prev = st_arr[i - 1]
        curr = c_arr[i]
        prev_c = c_arr[i - 1]
        loss   = nl_arr[i]
        if curr > prev and prev_c > prev:
            st_arr[i] = max(prev, curr - loss)
        elif curr < prev and prev_c < prev:
            st_arr[i] = min(prev, curr + loss)
        elif curr > prev:
            st_arr[i] = curr - loss
        else:
            st_arr[i] = curr + loss

    stop     = pd.Series(st_arr, index=df.index)
    ema1     = c.ewm(span=1, adjust=False).mean()
    buy_sig  = (c > stop) & (ema1 > stop) & (ema1.shift() <= stop.shift())
    sell_sig = (c < stop) & (ema1 < stop) & (ema1.shift() >= stop.shift())
    return stop, buy_sig, sell_sig


# ══════════════════════════════════════════════════════════
# WAVETREND — TCI (Instrument-Z / OscillateMatrix)
# ══════════════════════════════════════════════════════════
def calc_wavetrend(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    ap   = hlc3
    esa  = ema(ap, CHAN_LEN)
    d    = ema(abs(ap - esa), CHAN_LEN)
    ci   = (ap - esa) / (0.015 * d)
    tci  = ema(ci, AVG_LEN)
    wt1  = tci
    wt2  = sma(wt1, 4)
    cross_up: wt1 > wt2 AND wt1[1] <= wt2[1] AND wt1 < 0  (cross from below zero)
    cross_dn: wt1 < wt2 AND wt1[1] >= wt2[1] AND wt1 > 0  (cross from above zero)
    """
    ap  = (df["high"] + df["low"] + df["close"]) / 3
    esa = ap.ewm(span=WT_CHAN_LEN, adjust=False).mean()
    d   = (ap - esa).abs().ewm(span=WT_CHAN_LEN, adjust=False).mean()
    ci  = (ap - esa) / (0.015 * d.replace(0, np.nan))
    tci = ci.ewm(span=WT_AVG_LEN, adjust=False).mean()
    wt1 = tci
    wt2 = wt1.rolling(4).mean()

    cross_up = (wt1 > wt2) & (wt1.shift() <= wt2.shift()) & (wt1 < 0)
    cross_dn = (wt1 < wt2) & (wt1.shift() >= wt2.shift()) & (wt1 > 0)
    return wt1, wt2, cross_up, cross_dn


# ══════════════════════════════════════════════════════════
# BOLLINGER BANDS — BB+RSI (rouxam / 3commas DCA)
# ══════════════════════════════════════════════════════════
def calc_bb(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    basis = sma(close, BB_PERIOD)
    upper = basis + BB_STD * stdev(close, BB_PERIOD)
    lower = basis - BB_STD * stdev(close, BB_PERIOD)
    buy  = close < lower AND rsi < BB_RSI_OB   (oversold at lower band)
    sell = close > upper AND rsi > (100-BB_RSI_OB)  (overbought at upper band)
    """
    c     = df["close"]
    basis = c.rolling(BB_PERIOD).mean()
    dev   = c.rolling(BB_PERIOD).std()
    upper = basis + BB_STD * dev
    lower = basis - BB_STD * dev
    return upper, lower, basis


# ══════════════════════════════════════════════════════════
# WHALE & INSTITUTIONAL INDICATORS — v14
# ══════════════════════════════════════════════════════════

# Cache para datos de derivados (funding, OI, L/S ratio)
_deriv_cache: Dict[str, Tuple[float, dict]] = {}
DERIV_TTL = 300  # 5 minutos (estos datos cambian lento)

def fetch_funding_rate(ex: ccxt.Exchange, symbol: str) -> float:
    """Devuelve funding rate actual. Positivo = longs pagan (mercado alcista/sobrepoblado)."""
    key = f"fr|{symbol}"
    now = time.time()
    if key in _deriv_cache:
        ts, val = _deriv_cache[key]
        if now - ts < DERIV_TTL:
            return val.get("rate", 0.0)
    try:
        fr = ex.fetch_funding_rate(symbol)
        rate = float(fr.get("fundingRate") or fr.get("nextFundingRate") or 0.0)
        _deriv_cache[key] = (now, {"rate": rate})
        return rate
    except Exception:
        return 0.0

def fetch_open_interest(ex: ccxt.Exchange, symbol: str) -> Tuple[float, float]:
    """Devuelve (OI_actual, OI_anterior). OI creciente = ballenas entrando."""
    key = f"oi|{symbol}"
    now = time.time()
    if key in _deriv_cache:
        ts, val = _deriv_cache[key]
        if now - ts < DERIV_TTL:
            return val.get("oi_now", 0.0), val.get("oi_prev", 0.0)
    try:
        hist = ex.fetch_open_interest_history(symbol, timeframe="5m", limit=3)
        if hist and len(hist) >= 2:
            oi_now  = float(hist[-1].get("openInterestAmount") or hist[-1].get("openInterest") or 0)
            oi_prev = float(hist[-2].get("openInterestAmount") or hist[-2].get("openInterest") or 0)
            _deriv_cache[key] = (now, {"oi_now": oi_now, "oi_prev": oi_prev})
            return oi_now, oi_prev
    except Exception:
        pass
    return 0.0, 0.0

def fetch_long_short_ratio(ex: ccxt.Exchange, symbol: str) -> float:
    """Devuelve ratio L/S global. >1 = mayoría long, <1 = mayoría short."""
    key = f"ls|{symbol}"
    now = time.time()
    if key in _deriv_cache:
        ts, val = _deriv_cache[key]
        if now - ts < DERIV_TTL:
            return val.get("ratio", 1.0)
    try:
        # Intentar via ccxt unificado primero
        hist = ex.fetch_long_short_ratio_history(symbol, timeframe="5m", limit=2)
        if hist and len(hist) >= 1:
            ls = hist[-1]
            ratio = float(ls.get("longShortRatio") or ls.get("ratio") or 1.0)
            _deriv_cache[key] = (now, {"ratio": ratio})
            return ratio
    except Exception:
        pass
    # Fallback: BingX API directa
    try:
        base = symbol.split("/")[0].replace(":USDT", "")
        url  = f"https://open-api.bingx.com/openApi/swap/v2/quote/longShortRatio"
        params = {"symbol": f"{base}-USDT", "period": "5m", "limit": 2}
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json().get("data", [])
        if data:
            ratio = float(data[-1].get("longShortRatio", 1.0))
            _deriv_cache[key] = (now, {"ratio": ratio})
            return ratio
    except Exception:
        pass
    return 1.0  # neutral si falla

def is_institutional_session() -> bool:
    """
    True si estamos en horario de alta liquidez institucional.
    Basado en investigación: ballenas reales operan en sesión London-NY overlap (13-16 UTC)
    y durante las sesiones principales. Evitar 23:00-06:00 UTC (bots dominan, señales falsas).
    """
    hour = datetime.now(timezone.utc).hour
    # Horario institucional activo: Asia tarde (06-08), London (08-16), NY (13-22)
    # Evitar: 23:00-05:59 UTC = madrugada US/Europa, solo bots asiáticos de baja calidad
    return 6 <= hour <= 22

def whale_score_bonus(ex: ccxt.Exchange, symbol: str, side: str) -> Tuple[int, int, str]:
    """
    Calcula puntos adicionales (0-4) basados en señales de ballenas/institucionales.
    Retorna (puntos_long, puntos_short, descripcion)
    
    Puntos posibles:
    +1 P17: Funding rate favorable (contrarian — mercado no sobrepoblado en tu dirección)
    +1 P18: Open Interest creciente (dinero nuevo entrando, confirma movimiento)
    +1 P19: Long/Short ratio a favor (posicionamiento institucional correcto)
    +1 P20: Sesión institucional activa (London/NY = ballenas reales operando)
    """
    long_pts = 0
    short_pts = 0
    desc_parts = []

    try:
        fr = fetch_funding_rate(ex, symbol)
        fr_pct = fr * 100

        # P17: Funding rate — contrarian es mejor
        # FR muy positivo = longs masivos = peligroso para LONG, bueno para SHORT
        # FR negativo = shorts masivos = bueno para LONG
        # FR neutral (-0.03% a +0.03%) = ok para ambos
        FR_EXTREME = 0.05   # >0.05% cada 8h = mercado sobrepoblado (bloquear esa dirección)
        FR_FAVORABLE = -0.01  # FR negativo = beneficioso para LONG (shorts pagando)

        if fr < FR_FAVORABLE:    # FR negativo: favorable para LONG
            long_pts += 1
            desc_parts.append(f"FR:{fr_pct:.3f}%🟢L")
        elif fr > FR_EXTREME:    # FR muy positivo: favorable para SHORT (longs sobrecargados)
            short_pts += 1
            desc_parts.append(f"FR:{fr_pct:.3f}%🔴S")
        elif -FR_EXTREME < fr < FR_EXTREME:  # FR neutral: punto para ambos (mercado equilibrado)
            long_pts += 1
            short_pts += 1
            desc_parts.append(f"FR:{fr_pct:.3f}%⚪")
    except Exception:
        long_pts += 1; short_pts += 1  # neutral si falla

    try:
        oi_now, oi_prev = fetch_open_interest(ex, symbol)
        if oi_now > 0 and oi_prev > 0:
            oi_change_pct = (oi_now - oi_prev) / oi_prev * 100
            # OI creciente con precio subiendo = ballenas comprando (LONG)
            # OI creciente con precio bajando = ballenas vendiendo (SHORT)
            if oi_change_pct > 0.5:  # OI crece >0.5%
                long_pts += 1   # dinero nuevo entrando, puede ser en cualquier dirección
                short_pts += 1  # ambos se benefician del aumento de OI
                desc_parts.append(f"OI:+{oi_change_pct:.1f}%📈")
            else:
                desc_parts.append(f"OI:{oi_change_pct:+.1f}%")
        else:
            long_pts += 1; short_pts += 1
    except Exception:
        long_pts += 1; short_pts += 1

    try:
        ls_ratio = fetch_long_short_ratio(ex, symbol)
        # L/S ratio: si mayoría está LONG (>1.3), seguir a las ballenas para LONG
        # Si mayoría está SHORT (<0.7), seguir para SHORT
        # Si ratio extremo (>2.0 o <0.5) = trampa, contrarian
        if 1.1 <= ls_ratio <= 1.8:      # mayoría institucional en LONG moderado
            long_pts += 1
            desc_parts.append(f"L/S:{ls_ratio:.2f}🟢")
        elif 0.6 <= ls_ratio < 0.9:     # mayoría en SHORT moderado
            short_pts += 1
            desc_parts.append(f"L/S:{ls_ratio:.2f}🔴")
        elif 0.9 <= ls_ratio < 1.1:     # equilibrado = neutral
            long_pts += 1; short_pts += 1
            desc_parts.append(f"L/S:{ls_ratio:.2f}⚪")
        else:                            # extremo = trampa, no puntúa
            desc_parts.append(f"L/S:{ls_ratio:.2f}⚠️")
    except Exception:
        long_pts += 1; short_pts += 1

    # P20: Sesión institucional
    if is_institutional_session():
        long_pts += 1
        short_pts += 1
        hour = datetime.now(timezone.utc).hour
        session = "🏦London" if 8 <= hour < 13 else "🗽NY" if 13 <= hour < 22 else "🌏Asia"
        desc_parts.append(f"Sesión:{session}")
    else:
        desc_parts.append("Sesión:🌙OFF-hrs")

    return long_pts, short_pts, " | ".join(desc_parts)

# ══════════════════════════════════════════════════════════
# SUPERTREND — El filtro de tendencia más fiable (v15)
# Parámetros probados: period=10, multiplier=3.0
# Señal: precio cruza por encima → BULL | precio cruza por debajo → BEAR
# Usado por HaasOnline, Cryptohopper y los mejores bots 2025
# ══════════════════════════════════════════════════════════
def calc_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    """
    Retorna (supertrend_line, direction):
      direction = +1 uptrend (alcista), -1 downtrend (bajista)
    """
    h = df["high"]; l = df["low"]; c = df["close"]
    hl2 = (h + l) / 2.0
    atr = calc_atr(df, period)

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    upper = basic_upper.copy()
    lower = basic_lower.copy()

    for i in range(1, len(df)):
        # Final upper band
        if basic_upper.iloc[i] < upper.iloc[i-1] or c.iloc[i-1] > upper.iloc[i-1]:
            upper.iloc[i] = basic_upper.iloc[i]
        else:
            upper.iloc[i] = upper.iloc[i-1]
        # Final lower band
        if basic_lower.iloc[i] > lower.iloc[i-1] or c.iloc[i-1] < lower.iloc[i-1]:
            lower.iloc[i] = basic_lower.iloc[i]
        else:
            lower.iloc[i] = lower.iloc[i-1]

    direction = pd.Series(index=df.index, dtype=float)
    supertrend = pd.Series(index=df.index, dtype=float)
    direction.iloc[0] = 1.0
    supertrend.iloc[0] = lower.iloc[0]

    for i in range(1, len(df)):
        prev_dir = direction.iloc[i-1]
        if prev_dir == -1:
            direction.iloc[i] = 1.0 if c.iloc[i] > upper.iloc[i-1] else -1.0
        else:
            direction.iloc[i] = -1.0 if c.iloc[i] < lower.iloc[i-1] else 1.0
        supertrend.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]

    return supertrend, direction


# ══════════════════════════════════════════════════════════
# CVD — Cumulative Volume Delta (orden de flujo real)
# La diferencia entre volumen comprador y vendedor acumulado.
# Divergencia CVD vs precio = señal de reversal / trampa
# Usado por traders institucionales para confirmar breakouts
# ══════════════════════════════════════════════════════════
def calc_cvd(df: pd.DataFrame, period: int = 20) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    CVD aproximado desde OHLCV (sin datos de tape):
    - delta_bar = buy_vol - sell_vol (estimación desde velas)
    - cvd       = delta acumulado rolling period barras
    - cvd_bull  = CVD sube mientras precio también sube (confluencia)
    - cvd_bear  = CVD baja mientras precio también baja (confluencia)
    - cvd_bull_div = precio baja pero CVD sube → absorción (señal LONG fuerte)
    - cvd_bear_div = precio sube pero CVD baja → distribución (señal SHORT fuerte)
    """
    h = df["high"]; l = df["low"]; c = df["close"]; o = df["open"]; v = df["volume"]
    rng = (h - l).replace(0, np.nan)

    # Estimación delta por vela (buy vol - sell vol)
    buy_vol  = v * (c - l) / rng
    sell_vol = v * (h - c) / rng
    delta    = (buy_vol - sell_vol).fillna(0)

    # CVD rolling (últimas `period` barras)
    cvd = delta.rolling(period).sum()

    # Divergencia: precio hace mínimo más bajo pero CVD hace mínimo más alto → bullish
    cvd_bull_div = (
        (c < c.shift(3)) &           # precio bajó
        (cvd > cvd.shift(3)) &       # pero CVD subió
        (cvd < 0)                    # y CVD aún negativo (zona sobreventa)
    )
    # Divergencia bajista: precio hace máximo más alto pero CVD hace máximo más bajo
    cvd_bear_div = (
        (c > c.shift(3)) &
        (cvd < cvd.shift(3)) &
        (cvd > 0)
    )

    return cvd, cvd_bull_div, cvd_bear_div


# ══════════════════════════════════════════════════════════
# SMC — Smart Money Concepts (Order Blocks + BOS/CHoCH)
# Lo que usan los hedge funds e instituciones
# Order Block = zona donde las instituciones colocaron órdenes masivas
# BOS = Break of Structure (continuación de tendencia)
# CHoCH = Change of Character (reversión inminente)
# ══════════════════════════════════════════════════════════
def calc_smc(df: pd.DataFrame, swing_len: int = 10) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Retorna:
    - ob_bull: True si precio está sobre un bullish order block (soporte institucional)
    - ob_bear: True si precio está bajo un bearish order block (resistencia institucional)
    - bos_bull: Break of Structure alcista (precio rompió máximo estructural → tendencia LONG)
    - choch:    Change of Character (posible reversión, cuidado)
    """
    h = df["high"]; l = df["low"]; c = df["close"]

    # Swing highs y lows estructurales
    swing_high = h.rolling(swing_len * 2 + 1, center=True).max() == h
    swing_low  = l.rolling(swing_len * 2 + 1, center=True).min() == l

    # BOS alcista: precio cierra por encima del swing high anterior
    prev_swing_high = h.where(swing_high).ffill().shift(1)
    bos_bull = (c > prev_swing_high) & (c.shift(1) <= prev_swing_high)

    # BOS bajista: precio cierra por debajo del swing low anterior
    prev_swing_low = l.where(swing_low).ffill().shift(1)
    bos_bear = (c < prev_swing_low) & (c.shift(1) >= prev_swing_low)

    # CHoCH: después de BOS alcista, si rompe el último swing low = posible reversión
    choch = bos_bull.shift(1).fillna(False) & bos_bear

    # Order Block alcista: última vela bajista antes de un BOS alcista (zona de compra institucional)
    # Es la vela roja que precede al movimiento alcista fuerte
    ob_bull_level = l.where(bos_bull.shift(1).fillna(False)).ffill()
    ob_bull_top   = h.where(bos_bull.shift(1).fillna(False)).ffill()
    ob_bull = (c >= ob_bull_level) & (c <= ob_bull_top * 1.005)  # precio dentro o cerca del OB

    # Order Block bajista: última vela alcista antes de un BOS bajista
    ob_bear_level = h.where(bos_bear.shift(1).fillna(False)).ffill()
    ob_bear_bottom= l.where(bos_bear.shift(1).fillna(False)).ffill()
    ob_bear = (c <= ob_bear_level) & (c >= ob_bear_bottom * 0.995)

    return ob_bull.fillna(False), ob_bear.fillna(False), bos_bull.fillna(False), choch.fillna(False)


# ══════════════════════════════════════════════════════════
# BJ BOT — R:R Targets (3Commas framework)
# ══════════════════════════════════════════════════════════
def calc_rr_targets(entry: float, side: str,
                    swing_low: float, swing_high: float,
                    atr: float) -> Tuple[float, float, float]:
    """
    Traducción directa de Bj Bot:
      longStop  = lowestLow  - atr * RiskM
      shortStop = highestHigh + atr * RiskM
      longRisk  = entry - longStop
      longlimit = entry + RnR * longRisk     ← TP2 basado en R:R
      TP1       = entry + (longlimit - entry) * 0.5  ← 50% del camino a TP2
    """
    if side == "long":
        stop   = min(swing_low  - atr * RISK_MULT, entry - atr * SL_ATR_MULT)
        risk   = entry - stop
        tp2    = entry + RNR * risk
        tp1    = entry + (tp2 - entry) * 0.5
    else:
        stop   = max(swing_high + atr * RISK_MULT, entry + atr * SL_ATR_MULT)
        risk   = stop - entry
        tp2    = entry - RNR * risk
        tp1    = entry - (entry - tp2) * 0.5
    return tp1, tp2, stop


# ══════════════════════════════════════════════════════════
# COMPUTE — todos los indicadores
# ══════════════════════════════════════════════════════════
def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v, o = df["close"], df["high"], df["low"], df["volume"], df["open"]

    # ── EMAs ──
    df["ema8"]   = ema(c, FAST_LEN)
    df["ema21"]  = ema(c, PIVOT_LEN)
    df["ema48"]  = ema(c, BIAS_LEN)
    df["ema200"] = ema(c, SLOW_LEN)
    df["atr"]    = calc_atr(df, ATR_LEN)
    df["rsi"]    = calc_rsi(c, RSI_LEN)

    # ── ADX ──
    dip, dim, adx = calc_adx(df, ADX_LEN)
    df["dip"] = dip; df["dim"] = dim; df["adx"] = adx

    # ── MACD ──
    macd, macd_sg, macd_h = calc_macd(c)
    df["macd_hist"]       = macd_h
    df["macd_bull"]       = (macd_h > 0) & (macd_h > macd_h.shift())
    df["macd_bear"]       = (macd_h < 0) & (macd_h < macd_h.shift())
    df["macd_cross_up"]   = (macd > macd_sg) & (macd.shift() <= macd_sg.shift())
    df["macd_cross_down"] = (macd < macd_sg) & (macd.shift() >= macd_sg.shift())

    # ── SMI ──
    smi_s, smi_sig = calc_smi(df)
    df["smi"]          = smi_s
    df["smi_signal"]   = smi_sig
    df["smi_cross_up"]   = (smi_s > smi_sig) & (smi_s.shift() <= smi_sig.shift())
    df["smi_cross_down"] = (smi_s < smi_sig) & (smi_s.shift() >= smi_sig.shift())
    df["smi_bull"]     = (smi_s > smi_sig) & (smi_s < SMI_OB)
    df["smi_bear"]     = (smi_s < smi_sig) & (smi_s > SMI_OS)
    df["smi_ob"]       = smi_s >= SMI_OB
    df["smi_os"]       = smi_s <= SMI_OS
    df["smi_exit_ob"]  = (smi_s < SMI_OB) & (smi_s.shift() >= SMI_OB)
    df["smi_exit_os"]  = (smi_s > SMI_OS) & (smi_s.shift() <= SMI_OS)

    # ── UTBot ──
    ut_stop, ut_buy, ut_sell = calc_utbot(df)
    df["utbot_stop"] = ut_stop
    df["utbot_buy"]  = ut_buy
    df["utbot_sell"] = ut_sell

    # ── WaveTrend ──
    wt1, wt2, wt_cross_up, wt_cross_dn = calc_wavetrend(df)
    df["wt1"]          = wt1
    df["wt2"]          = wt2
    df["wt_cross_up"]  = wt_cross_up
    df["wt_cross_dn"]  = wt_cross_dn
    df["wt_bull"]      = (wt1 > wt2) & (wt1 < WT_OB)
    df["wt_bear"]      = (wt1 < wt2) & (wt1 > WT_OS)
    df["wt_ob"]        = wt1 >= WT_OB
    df["wt_os"]        = wt1 <= WT_OS

    # ── Bollinger Bands ──
    bb_up, bb_lo, bb_basis = calc_bb(df)
    df["bb_upper"] = bb_up
    df["bb_lower"] = bb_lo
    df["bb_basis"] = bb_basis
    # BB signal: precio toca banda inferior con RSI no sobrecomprado
    df["bb_buy"]  = (c < bb_lo) & (df["rsi"] < BB_RSI_OB)
    df["bb_sell"] = (c > bb_up) & (df["rsi"] > (100 - BB_RSI_OB))
    # Squeeze: BB dentro de Keltner
    kc_up         = df["ema21"] + 2.0 * df["atr"]
    df["squeeze"] = bb_up < kc_up
    bb_w          = (bb_up - bb_lo) / df["ema21"].replace(0, np.nan)
    df["bb_width"]    = bb_w

    # ── MA cross (Bj Bot) — usa ema8 vs ema21 ──
    df["ma_cross_up"]  = (df["ema8"] > df["ema21"]) & (df["ema8"].shift() <= df["ema21"].shift())
    df["ma_cross_down"]= (df["ema8"] < df["ema21"]) & (df["ema8"].shift() >= df["ema21"].shift())

    # ── Oscilador ──
    df["osc"]    = ema(((c - df["ema21"]) / (3.0 * df["atr"].replace(0,np.nan))) * 100, OSC_LEN)
    df["osc_up"] = (df["osc"] > 0) & (df["osc"].shift() <= 0)
    df["osc_dn"] = (df["osc"] < 0) & (df["osc"].shift() >= 0)

    # ── Tendencia ──
    df["is_trending"] = (adx > ADX_MIN) & (bb_w > sma(bb_w, 20) * 0.8)

    # ── Volumen ──
    rng            = (h - l).replace(0, np.nan)
    df["buy_vol"]  = v * (c - l) / rng
    df["sell_vol"] = v * (h - c) / rng
    df["vol_ma"]   = sma(v, VOL_LEN)
    df["vol_spike"]= v > df["vol_ma"] * 1.05
    df["vol_bull"] = df["buy_vol"] > df["sell_vol"]
    df["vol_bear"] = df["sell_vol"] > df["buy_vol"]

    # ── Velas ──
    body              = (c - o).abs()
    body_pct          = body / rng.replace(0, np.nan)
    df["bull_candle"] = (c > o) & (body_pct >= 0.30)
    df["bear_candle"] = (c < o) & (body_pct >= 0.30)
    prev_body = (o.shift() - c.shift()).abs()
    df["bull_engulf"] = (c > o) & (o <= c.shift()) & (c >= o.shift()) & (body > prev_body * 0.8)
    df["bear_engulf"] = (c < o) & (o >= c.shift()) & (c <= o.shift()) & (body > prev_body * 0.8)

    # ── Swing H/L ──
    df["swing_low"]  = l.rolling(SWING_LB).min()
    df["swing_high"] = h.rolling(SWING_LB).max()

    # ── Divergencias RSI ──
    rsi = df["rsi"]
    df["bull_div"] = (
        (l < l.shift(1)) & (l.shift(1) < l.shift(2)) &
        (rsi > rsi.shift(1)) & (rsi.shift(1) > rsi.shift(2)) & (rsi < 42)
    )
    df["bear_div"] = (
        (h > h.shift(1)) & (h.shift(1) > h.shift(2)) &
        (rsi < rsi.shift(1)) & (rsi.shift(1) < rsi.shift(2)) & (rsi > 58)
    )

    # ══════════════════════════════════════════════════════
    # v15 SAINT GRAIL — Indicadores avanzados
    # ══════════════════════════════════════════════════════

    # ── P21: VWAP diario (288 velas × 5m = 1 día) ──
    vwap_period   = min(288, len(df))
    typical_price = (h + l + c) / 3.0
    cum_vol       = v.rolling(vwap_period).sum()
    cum_tpvol     = (typical_price * v).rolling(vwap_period).sum()
    df["vwap"]           = cum_tpvol / cum_vol.replace(0, np.nan)
    df["above_vwap"]     = c > df["vwap"]
    df["below_vwap"]     = c < df["vwap"]
    df["vwap_cross_up"]  = (c > df["vwap"]) & (c.shift(1) <= df["vwap"].shift(1))
    df["vwap_cross_down"]= (c < df["vwap"]) & (c.shift(1) >= df["vwap"].shift(1))

    # ── P22: Supertrend (10, 3.0) ──
    try:
        st_line, st_dir = calc_supertrend(df, period=10, multiplier=3.0)
        df["st_bull"]       = (st_dir == 1.0)
        df["st_bear"]       = (st_dir == -1.0)
        df["st_cross_bull"] = (st_dir == 1.0) & (st_dir.shift(1).fillna(-1.0) == -1.0)
        df["st_cross_bear"] = (st_dir == -1.0) & (st_dir.shift(1).fillna(1.0) == 1.0)
    except Exception:
        for k in ["st_bull","st_bear","st_cross_bull","st_cross_bear"]:
            df[k] = False

    # ── P23: CVD Divergence ──
    try:
        cvd, cvd_bull_div, cvd_bear_div = calc_cvd(df, period=20)
        df["cvd"]          = cvd
        df["cvd_bull_div"] = cvd_bull_div
        df["cvd_bear_div"] = cvd_bear_div
        df["cvd_rising"]   = cvd > cvd.shift(3)
        df["cvd_falling"]  = cvd < cvd.shift(3)
    except Exception:
        for k in ["cvd_bull_div","cvd_bear_div","cvd_rising","cvd_falling"]:
            df[k] = False

    # ── P24: SMC — Order Blocks + BOS ──
    try:
        ob_bull, ob_bear, bos_bull, choch = calc_smc(df, swing_len=SWING_LB)
        df["ob_bull"]  = ob_bull
        df["ob_bear"]  = ob_bear
        df["bos_bull"] = bos_bull
        df["choch"]    = choch
    except Exception:
        for k in ["ob_bull","ob_bear","bos_bull","choch"]:
            df[k] = False

    # ── Market Regime Filter ──
    atr_pct    = df["atr"] / c.replace(0, np.nan) * 100
    atr_pct_ma = sma(atr_pct, 50)
    bb_w_ma    = sma(df["bb_width"], 50)
    df["regime_trend"] = (adx > 20) & (df["bb_width"] > bb_w_ma * 0.9) & (atr_pct > atr_pct_ma * 0.7)
    df["regime_chop"]  = (adx < 18) & (df["bb_width"] < bb_w_ma * 0.8)

    return df


def htf_bias(df: pd.DataFrame) -> Tuple[bool, bool]:
    df  = compute(df)
    row = df.iloc[-2]
    bull = bool(row["close"] > row["ema48"] and row["ema21"] > row["ema48"])
    bear = bool(row["close"] < row["ema48"] and row["ema21"] < row["ema48"])
    return bull, bear

def htf2_macro(df: pd.DataFrame) -> Tuple[bool, bool]:
    df  = compute(df)
    row = df.iloc[-2]
    bull = bool(row["close"] > row["ema48"] and row["ema48"] > row["ema200"])
    bear = bool(row["close"] < row["ema48"] and row["ema48"] < row["ema200"])
    return bull, bear


# ══════════════════════════════════════════════════════════
# SCORE 25 PUNTOS — v15 Saint Grail Edition
# ══════════════════════════════════════════════════════════
def confluence_score(row: pd.Series,
                     htf1_bull: bool, htf1_bear: bool,
                     htf2_bull: bool, htf2_bear: bool,
                     uptrend: bool,
                     whale_long: int = 0, whale_short: int = 0) -> Tuple[int, int]:
    """
    25 puntos por dirección (v15 Saint Grail Edition):

    LONG (P1-P16 base + P17-P20 whale + P21-P25 saint grail):
     1. EMA trend alcista
     2. Oscilador cruza al alza
     3. HTF1 bias alcista
     4. HTF2 macro alcista
     5. ADX con DI+ > DI-
     6. RSI en zona sana
     7. Volumen comprador + spike
     8. Vela alcista + close > ema21
     9. MACD alcista o cruce
    10. SMI cross up / bull
    11. SMI en OS o saliendo
    12. Bull engulf / div RSI
    13. UTBot BUY signal
    14. WaveTrend cross up / OS
    15. MA cross alcista
    16. BB buy signal
    17. Funding Rate favorable     ← WHALE v14
    18. Open Interest creciente    ← WHALE v14
    19. Long/Short ratio a favor   ← WHALE v14
    20. Sesión institucional       ← WHALE v14
    21. Precio sobre VWAP diario   ← SAINT GRAIL v15
    22. Supertrend alcista         ← SAINT GRAIL v15
    23. CVD bull divergence        ← SAINT GRAIL v15
    24. SMC Order Block bull / BOS ← SAINT GRAIL v15
    25. Régimen de tendencia activo← SAINT GRAIL v15

    SHORT: lógica espejada
    """
    # HARD BLOCK: si el mercado está en chop/lateral, score = 0
    if bool(row.get("regime_chop", False)):
        return 0, 0

    rsi = float(row["rsi"])

    # ─── LONG ─────────────────────────────────────────────
    l1  = bool(row["close"] > row["ema48"] and row["ema8"] > row["ema21"])
    l2  = bool(row["osc_up"])
    l3  = htf1_bull
    l4  = htf2_bull
    l5  = bool(row["adx"] > ADX_MIN and row["dip"] > row["dim"])
    l6  = bool(42 <= rsi <= 78)
    l7  = bool(row["vol_bull"] and row["vol_spike"] and not row["squeeze"])
    l8  = bool(row["bull_candle"] and row["close"] > row["ema21"])
    l9  = bool(row["macd_bull"] or row["macd_cross_up"])
    l10 = bool(row.get("smi_cross_up") or row.get("smi_bull"))
    l11 = bool(row.get("smi_os")       or row.get("smi_exit_os"))
    l12 = bool(row["bull_engulf"]      or row["bull_div"])
    l13 = bool(row.get("utbot_buy"))
    l14 = bool(row.get("wt_cross_up")  or row.get("wt_os") or
               (row.get("wt_bull") and not row.get("wt_ob")))
    l15 = bool(row.get("ma_cross_up"))
    l16 = bool(row.get("bb_buy") and not row.get("squeeze"))
    # Whale points (P17-P20)
    whale_l_pts = min(whale_long, 4)
    # Saint Grail (P21-P25)
    l21 = bool(row.get("above_vwap") or row.get("vwap_cross_up"))         # VWAP
    l22 = bool(row.get("st_bull") or row.get("st_cross_bull"))            # Supertrend
    l23 = bool(row.get("cvd_bull_div") or row.get("cvd_rising"))          # CVD
    l24 = bool(row.get("ob_bull") or row.get("bos_bull"))                 # SMC
    l25 = bool(row.get("regime_trend", False))                            # Régimen tendencia

    # ─── SHORT ────────────────────────────────────────────
    s1  = bool(row["close"] < row["ema48"] and row["ema8"] < row["ema21"])
    s2  = bool(row["osc_dn"])
    s3  = htf1_bear
    s4  = htf2_bear
    s5  = bool(row["adx"] > ADX_MIN and row["dim"] > row["dip"])
    s6  = bool(22 <= rsi <= 58)
    s7  = bool(row["vol_bear"] and row["vol_spike"] and not row["squeeze"])
    s8  = bool(row["bear_candle"] and row["close"] < row["ema21"])
    s9  = bool(row["macd_bear"]   or row["macd_cross_down"])
    s10 = bool(row.get("smi_cross_down") or row.get("smi_bear"))
    s11 = bool(row.get("smi_ob")         or row.get("smi_exit_ob"))
    s12 = bool(row["bear_engulf"]        or row["bear_div"])
    s13 = bool(row.get("utbot_sell"))
    s14 = bool(row.get("wt_cross_dn")    or row.get("wt_ob") or
               (row.get("wt_bear") and not row.get("wt_os")))
    s15 = bool(row.get("ma_cross_down"))
    s16 = bool(row.get("bb_sell") and not row.get("squeeze"))
    whale_s_pts = min(whale_short, 4)
    # Saint Grail (P21-P25)
    s21 = bool(row.get("below_vwap") or row.get("vwap_cross_down"))      # VWAP
    s22 = bool(row.get("st_bear") or row.get("st_cross_bear"))           # Supertrend
    s23 = bool(row.get("cvd_bear_div") or row.get("cvd_falling"))        # CVD
    s24 = bool(row.get("ob_bear") or row.get("choch"))                   # SMC
    s25 = bool(row.get("regime_trend", False))                           # Régimen tendencia

    long_score  = (sum([l1,l2,l3,l4,l5,l6,l7,l8,l9,l10,l11,l12,l13,l14,l15,l16])
                   + whale_l_pts + sum([l21,l22,l23,l24,l25]))
    short_score = (sum([s1,s2,s3,s4,s5,s6,s7,s8,s9,s10,s11,s12,s13,s14,s15,s16])
                   + whale_s_pts + sum([s21,s22,s23,s24,s25]))
    return long_score, short_score



# ══════════════════════════════════════════════════════════
# BTC BIAS
# ══════════════════════════════════════════════════════════
def update_btc_bias(ex: ccxt.Exchange):
    try:
        df  = fetch_df(ex, "BTC/USDT:USDT", "1h", limit=250)
        df  = compute(df)
        row = df.iloc[-2]
        state.btc_bull = bool(row["close"] > row["ema48"] and row["ema48"] > row["ema200"])
        state.btc_bear = bool(row["close"] < row["ema48"] and row["ema48"] < row["ema200"])
        state.btc_rsi  = float(row["rsi"])
        log.info(
            f"BTC: {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'} "
            f"RSI:{state.btc_rsi:.1f} "
            f"SMI:{float(row.get('smi',0)):.1f} "
            f"WT:{float(row.get('wt1',0)):.1f} "
            f"UTBot:{'BUY' if row.get('utbot_buy') else 'SELL' if row.get('utbot_sell') else '-'}"
        )
    except Exception as e:
        log.warning(f"BTC bias: {e}")


# ══════════════════════════════════════════════════════════
# EXCHANGE
# ══════════════════════════════════════════════════════════
def build_exchange() -> ccxt.Exchange:
    ex = ccxt.bingx({
        "apiKey": API_KEY, "secret": API_SECRET,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

def detect_hedge_mode(ex: ccxt.Exchange) -> bool:
    try:
        for p in ex.fetch_positions()[:5]:
            if p.get("info", {}).get("positionSide", "") in ("LONG", "SHORT"):
                return True
    except Exception:
        pass
    return False

def get_balance(ex: ccxt.Exchange) -> float:
    return float(ex.fetch_balance()["USDT"]["free"])

def get_position(ex: ccxt.Exchange, symbol: str) -> Optional[dict]:
    try:
        for p in ex.fetch_positions([symbol]):
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                return p
    except Exception:
        pass
    return None

def get_all_positions(ex: ccxt.Exchange) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    try:
        for p in ex.fetch_positions():
            if abs(float(p.get("contracts", 0) or 0)) > 0:
                result[p["symbol"]] = p
    except Exception as e:
        log.warning(f"fetch_positions: {e}")
    return result

def get_last_price(ex: ccxt.Exchange, symbol: str) -> float:
    return float(ex.fetch_ticker(symbol)["last"])

def get_spread_pct(ex: ccxt.Exchange, symbol: str) -> float:
    try:
        ob  = ex.fetch_order_book(symbol, limit=1)
        bid = ob["bids"][0][0] if ob["bids"] else 0
        ask = ob["asks"][0][0] if ob["asks"] else 0
        mid = (bid + ask) / 2
        return ((ask - bid) / mid * 100) if mid > 0 else 999.0
    except Exception:
        return 0.0

def get_min_amount(ex: ccxt.Exchange, symbol: str) -> float:
    try:
        mkt = ex.markets.get(symbol, {})
        return float(mkt.get("limits", {}).get("amount", {}).get("min", 0) or 0)
    except Exception:
        return 0.0

def entry_params(side: str) -> dict:
    if HEDGE_MODE:
        return {"positionSide": "LONG" if side == "buy" else "SHORT"}
    return {}

def exit_params(trade_side: str) -> dict:
    if HEDGE_MODE:
        return {"positionSide": "LONG" if trade_side == "long" else "SHORT",
                "reduceOnly": True}
    return {"reduceOnly": True}


# ══════════════════════════════════════════════════════════
# UNIVERSO
# ══════════════════════════════════════════════════════════
def get_symbols(ex: ccxt.Exchange) -> List[str]:
    candidates = []
    for sym, mkt in ex.markets.items():
        if not (mkt.get("swap") and mkt.get("quote") == "USDT"
                and mkt.get("active", True)):
            continue
        if sym in BLACKLIST: continue
        candidates.append(sym)

    if not candidates:
        log.warning("Sin candidatos de mercado")
        return []

    log.info(f"Obteniendo tickers para {len(candidates)} pares...")
    try:
        tickers = ex.fetch_tickers(candidates)
    except Exception as e:
        log.warning(f"fetch_tickers: {e}")
        return candidates[:TOP_N_SYMBOLS]

    ranked = []
    for sym in candidates:
        tk  = tickers.get(sym, {})
        vol = float(tk.get("quoteVolume", 0) or 0)
        if vol >= MIN_VOLUME_USDT:
            info    = ex.markets.get(sym, {}).get("info", {})
            created = info.get("onboardDate", 0) or info.get("deliveryDate", 0)
            is_new  = False
            if created:
                try:
                    age_days = (time.time() - float(created) / 1000) / 86400
                    is_new   = age_days < 30
                except Exception:
                    pass
            ranked.append((sym, vol, is_new))

    ranked.sort(key=lambda x: (not x[2], -x[1]))
    result = [s for s, _, _ in ranked]
    if TOP_N_SYMBOLS > 0:
        result = result[:TOP_N_SYMBOLS]

    new_count = sum(1 for _, _, n in ranked[:len(result)] if n)
    log.info(f"Universo: {len(result)} pares "
             f"(vol≥${MIN_VOLUME_USDT/1000:.0f}K, {new_count} nuevos primero)")
    return result


# ══════════════════════════════════════════════════════════
# APERTURA DE POSICIÓN — con targets Bj Bot (R:R)
# ══════════════════════════════════════════════════════════
def open_trade(ex: ccxt.Exchange, symbol: str, base: str,
               side: str, score: int, row: pd.Series,
               uptrend: bool, whale_desc: str = "") -> Optional[TradeState]:
    try:
        spread = get_spread_pct(ex, symbol)
        if spread > MAX_SPREAD_PCT:
            log.warning(f"[{symbol}] spread {spread:.3f}% > {MAX_SPREAD_PCT}% — skip")
            return None

        # Establecer apalancamiento 12x antes de abrir
        try:
            lv_params = {"hedged": True} if HEDGE_MODE else {}
            ex.set_leverage(LEVERAGE, symbol, params=lv_params)
        except Exception as lv_err:
            log.warning(f"[{symbol}] set_leverage {LEVERAGE}x: {lv_err} (continuando)")

        price   = get_last_price(ex, symbol)
        atr     = float(row["atr"])
        smi_v   = float(row.get("smi", 0.0))
        wt_v    = float(row.get("wt1", 0.0))
        ut_stop = float(row.get("utbot_stop", 0.0))
        usdt    = FIXED_USDT * state.risk_mult()
        raw_amt = (usdt * LEVERAGE) / price  # 8 USDT x 12 = 96 USDT notional
        min_amt = get_min_amount(ex, symbol)
        # Usar el máximo entre el importe calculado y el mínimo del exchange
        raw_amt = max(raw_amt, min_amt) if min_amt > 0 else raw_amt
        amount  = float(ex.amount_to_precision(symbol, raw_amt))

        # Verificar que el notional no excede demasiado FIXED_USDT (máx 3×)
        if amount <= 0:
            log.warning(f"[{symbol}] amount calculado es 0")
            return None
        if amount * price < 3:
            log.warning(f"[{symbol}] notional ${amount*price:.2f} < $3")
            return None
        if amount * price > FIXED_USDT * LEVERAGE * 3:
            log.warning(f"[{symbol}] notional ${amount*price:.2f} excede 3× FIXED_USDT, skipping")
            return None

        log.info(f"[OPEN] {symbol} {side.upper()} score={score}/25 "
                 f"SMI={smi_v:.1f} WT={wt_v:.1f} ${usdt:.1f} @ {price:.6g}")

        order       = ex.create_order(symbol, "market", side, amount,
                                      params=entry_params(side))
        entry_price = float(order.get("average") or price)
        trade_side  = "long" if side == "buy" else "short"

        # ── Targets Bj Bot (R:R) ──
        tp1_p, tp2_p, sl_p = calc_rr_targets(
            entry_price, trade_side,
            float(row["swing_low"]), float(row["swing_high"]), atr
        )

        tp1_p = float(ex.price_to_precision(symbol, tp1_p))
        tp2_p = float(ex.price_to_precision(symbol, tp2_p))
        sl_p  = float(ex.price_to_precision(symbol, sl_p))

        # R:R trail trigger (Bj Bot rrExit)
        if trade_side == "long":
            rr_trigger = entry_price + (tp2_p - entry_price) * RR_EXIT
        else:
            rr_trigger = entry_price - (entry_price - tp2_p) * RR_EXIT

        close_side = "sell" if side == "buy" else "buy"
        half       = float(ex.amount_to_precision(symbol, amount * 0.5))
        ep         = exit_params(trade_side)

        for lbl, qty, px in [("TP1", half, tp1_p), ("TP2", half, tp2_p)]:
            try:
                ex.create_order(symbol, "limit", close_side, qty, px, ep)
                log.info(f"[{symbol}] {lbl} @ {px:.6g}")
            except Exception as e:
                log.warning(f"[{symbol}] {lbl}: {e}")

        try:
            sl_ep = {**ep, "stopPrice": sl_p}
            ex.create_order(symbol, "stop_market", close_side, amount, None, sl_ep)
            log.info(f"[{symbol}] SL @ {sl_p:.6g}")
        except Exception as e:
            log.warning(f"[{symbol}] SL: {e}")

        t = TradeState(
            symbol=symbol,       base=base,        side=trade_side,
            entry_price=entry_price,               tp1_price=tp1_p,
            tp2_price=tp2_p,     sl_price=sl_p,
            entry_score=score,   entry_time=utcnow(),
            contracts=amount,    atr_entry=atr,
            smi_entry=smi_v,     wt_entry=wt_v,
            utbot_stop=ut_stop,
            uptrend_entry=uptrend,
            whale_desc=whale_desc,
            rr_trail_stop=rr_trigger,
        )
        if side == "buy":
            t.trail_high = entry_price
            t.rr_trail_stop = rr_trigger
        else:
            t.trail_low  = entry_price
            t.rr_trail_stop = rr_trigger

        # ── Inicializar DCA (v18) ──
        if DCA_ENABLED and DCA_MAX_ORDERS > 0:
            t.dca_avg_price  = entry_price
            t.dca_total_usdt = usdt
            t.dca_total_contr = amount
            # Precio que dispara la 1ª orden DCA
            if trade_side == "long":
                t.dca_next_price = entry_price * (1 - DCA_STEP_PCT / 100)
            else:
                t.dca_next_price = entry_price * (1 + DCA_STEP_PCT / 100)
            t.dca_sl_price = sl_p  # SL inicial (se actualiza tras cada DCA)

        log_csv("OPEN", t, entry_price)
        tg_signal(t, row)
        return t

    except Exception as e:
        err_str = str(e).lower()
        log.error(f"[{symbol}] open_trade: {e}")
        # Detectar error de margen insuficiente → enviar señal manual
        if any(k in err_str for k in ["insufficient margin", "insufficient balance",
                                       "not enough", "margin", "balance"]):
            try:
                # Calcular targets para el trader manual
                _price = get_last_price(ex, symbol)
                _atr   = float(row["atr"])
                _side  = "long" if side == "buy" else "short"
                _tp1, _tp2, _sl = calc_rr_targets(
                    _price, _side,
                    float(row["swing_low"]), float(row["swing_high"]), _atr
                )
                tg_manual_signal(
                    symbol=symbol, side=_side, score=score,
                    entry=_price, tp1=_tp1, tp2=_tp2, sl=_sl,
                    atr=_atr, whale_desc=whale_desc,
                    reason=str(e)[:120]
                )
            except Exception as me:
                log.warning(f"[{symbol}] no pude enviar señal manual: {me}")
                tg_error(f"open_trade {symbol}: {e}")
        else:
            tg_error(f"open_trade {symbol}: {e}")
        return None


def move_be(ex: ccxt.Exchange, symbol: str):
    if symbol not in state.trades: return
    t = state.trades[symbol]
    if t.sl_moved_be: return
    try:
        ex.cancel_all_orders(symbol)
    except Exception as e:
        log.warning(f"[{symbol}] cancel for BE: {e}")
    be    = float(ex.price_to_precision(symbol, t.entry_price))
    ep    = {**exit_params(t.side), "stopPrice": be}
    cside = "sell" if t.side == "long" else "buy"
    try:
        ex.create_order(symbol, "stop_market", cside, t.contracts, None, ep)
        t.sl_price    = be
        t.sl_moved_be = True
        log.info(f"[{symbol}] BE @ {be:.6g}")
    except Exception as e:
        log.warning(f"[{symbol}] BE failed: {e}")


def close_trade(ex: ccxt.Exchange, symbol: str, reason: str, price: float):
    if symbol not in state.trades: return
    t = state.trades[symbol]
    try: ex.cancel_all_orders(symbol)
    except Exception as e: log.warning(f"[{symbol}] cancel: {e}")

    pos = get_position(ex, symbol)
    pnl = 0.0
    if pos:
        contracts  = abs(float(pos.get("contracts", 0)))
        close_side = "sell" if t.side == "long" else "buy"
        try:
            ex.create_order(symbol, "market", close_side, contracts,
                            params=exit_params(t.side))
            pnl = ((price - t.entry_price) if t.side == "long"
                   else (t.entry_price - price)) * contracts
        except Exception as e:
            log.error(f"[{symbol}] close: {e}")
            tg_error(f"close {symbol}: {e}")
            return

    if pnl > 0:
        state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
    elif pnl < 0:
        state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1

    state.total_pnl   += pnl
    state.daily_pnl   += pnl
    state.peak_equity  = max(state.peak_equity, state.peak_equity + pnl)
    state.set_cooldown(symbol)

    log_csv("CLOSE", t, price, pnl)
    tg_close(reason, t, price, pnl)
    brain.record_trade(symbol, t.side, t.entry_score, pnl, reason)
    del state.trades[symbol]


# ══════════════════════════════════════════════════════════
# GESTIÓN DEL TRADE — v14 con todas las capas de salida
# ══════════════════════════════════════════════════════════
def execute_dca_order(ex: ccxt.Exchange, symbol: str, live_price: float, atr: float):
    """
    DCA Averaging — modelo 3Commas/Bitsgap.
    Cuando el precio va -DCA_STEP_PCT% contra la posición:
      1. Abre una nueva orden market al precio actual (tamaño escalonado)
      2. Recalcula el precio promedio ponderado
      3. Actualiza el TP al nuevo precio promedio + DCA_TP_PCT%
      4. Actualiza el SL duro a avg_price - DCA_SL_ATR_MULT × ATR
      5. Programa el próximo nivel DCA
    Máximo DCA_MAX_ORDERS niveles para evitar sobre-exposición.
    """
    if symbol not in state.trades:
        return
    t = state.trades[symbol]

    if not DCA_ENABLED or t.dca_count >= DCA_MAX_ORDERS:
        return

    try:
        # Calcular tamaño de la orden DCA (escalonado × DCA_SIZE_MULT)
        usdt_base  = FIXED_USDT * state.risk_mult()
        dca_usdt   = usdt_base * (DCA_SIZE_MULT ** t.dca_count)  # 1ª: 8×1.2=9.6, 2ª: 9.6×1.2=11.52
        raw_amt    = (dca_usdt * LEVERAGE) / live_price
        min_amt    = get_min_amount(ex, symbol)
        raw_amt    = max(raw_amt, min_amt) if min_amt > 0 else raw_amt
        dca_amount = float(ex.amount_to_precision(symbol, raw_amt))

        if dca_amount <= 0:
            return

        # Verificar margen disponible antes de abrir
        try:
            bal = get_balance(ex)
            if bal < dca_usdt * 0.8:
                log.warning(f"[DCA] {symbol}: sin margen suficiente (${bal:.2f} < ${dca_usdt:.2f})")
                return
        except Exception:
            pass

        side_order = "buy" if t.side == "long" else "sell"
        order = ex.create_order(symbol, "market", side_order, dca_amount,
                                params=entry_params(side_order))
        fill_price = float(order.get("average") or live_price)

        # Recalcular precio promedio ponderado
        total_cost   = t.dca_avg_price * t.dca_total_contr + fill_price * dca_amount
        t.dca_total_contr += dca_amount
        t.dca_total_usdt  += dca_usdt
        t.dca_avg_price    = total_cost / t.dca_total_contr
        t.dca_count       += 1

        # Nuevo TP al precio promedio + DCA_TP_PCT%
        if t.side == "long":
            new_tp2 = t.dca_avg_price * (1 + DCA_TP_PCT / 100)
            new_tp1 = t.dca_avg_price * (1 + DCA_TP_PCT / 200)  # TP1 = mitad del camino
            # Nuevo SL duro debajo del promedio
            new_sl  = t.dca_avg_price - atr * DCA_SL_ATR_MULT
            # Siguiente nivel DCA más abajo (distancia × DCA_STEP_MULT)
            step_pct = DCA_STEP_PCT * (DCA_STEP_MULT ** t.dca_count)
            t.dca_next_price = t.dca_avg_price * (1 - step_pct / 100)
        else:
            new_tp2 = t.dca_avg_price * (1 - DCA_TP_PCT / 100)
            new_tp1 = t.dca_avg_price * (1 - DCA_TP_PCT / 200)
            new_sl  = t.dca_avg_price + atr * DCA_SL_ATR_MULT
            step_pct = DCA_STEP_PCT * (DCA_STEP_MULT ** t.dca_count)
            t.dca_next_price = t.dca_avg_price * (1 + step_pct / 100)

        new_tp2   = float(ex.price_to_precision(symbol, new_tp2))
        new_tp1   = float(ex.price_to_precision(symbol, new_tp1))
        new_sl    = float(ex.price_to_precision(symbol, new_sl))

        t.tp2_price   = new_tp2
        t.tp1_price   = new_tp1
        t.dca_sl_price = new_sl
        t.sl_price     = new_sl   # el SL del estado se actualiza
        t.contracts    = t.dca_total_contr  # para cálculo de PnL correcto

        # Cancelar TP/SL anteriores y colocar los nuevos
        close_side = "sell" if t.side == "long" else "buy"
        ep = exit_params(t.side)
        try:
            ex.cancel_all_orders(symbol)
        except Exception:
            pass
        half = float(ex.amount_to_precision(symbol, t.dca_total_contr * 0.5))
        for lbl, qty, px in [("TP1", half, new_tp1), ("TP2", half, new_tp2)]:
            try:
                ex.create_order(symbol, "limit", close_side, qty, px, ep)
            except Exception as e:
                log.warning(f"[DCA-TP] {symbol} {lbl}: {e}")
        try:
            sl_ep = {**ep, "stopPrice": new_sl}
            ex.create_order(symbol, "stop_market", close_side, t.dca_total_contr, None, sl_ep)
        except Exception as e:
            log.warning(f"[DCA-SL] {symbol}: {e}")

        log.info(f"[DCA #{t.dca_count}] {symbol} fill={fill_price:.6g} "
                 f"avg={t.dca_avg_price:.6g} TP2={new_tp2:.6g} SL={new_sl:.6g}")

        pnl_est = ((t.dca_avg_price - live_price) if t.side == "long"
                   else (live_price - t.dca_avg_price)) * t.dca_total_contr

        tg(f"📉 DCA #{t.dca_count} — {symbol}\n"
           f"{'LONG' if t.side=='long' else 'SHORT'} · Orden {t.dca_count}/{DCA_MAX_ORDERS}\n"
           f"💵 Fill: {fill_price:.6g} | Prom: {t.dca_avg_price:.6g}\n"
           f"🟢 Nuevo TP2: {new_tp2:.6g} (+{DCA_TP_PCT:.1f}%)\n"
           f"🛑 Nuevo SL:  {new_sl:.6g}\n"
           f"📊 Capital DCA: ${t.dca_total_usdt:.2f} | Contratos: {t.dca_total_contr:.4f}\n"
           f"⏰ {utcnow()}")

    except Exception as e:
        log.error(f"[DCA] {symbol}: {e}")


def manage_trade(ex: ccxt.Exchange, symbol: str,
                 live_price: float, atr: float,
                 long_score: int, short_score: int,
                 live_pos: Optional[dict],
                 result: Optional[dict] = None):

    if symbol not in state.trades: return
    t = state.trades[symbol]
    t.bar_count += 1

    # ── Posición cerrada externamente (SL/TP ejecutado) ──
    if live_pos is None:
        pnl = ((live_price - t.entry_price) if t.side == "long"
               else (t.entry_price - live_price)) * t.contracts
        reason = ("TP2 ALCANZADO"
                  if (t.side=="long"  and live_price >= t.tp2_price) or
                     (t.side=="short" and live_price <= t.tp2_price)
                  else "SL ALCANZADO")
        if pnl > 0:
            state.wins += 1; state.gross_profit += pnl; state.consec_losses = 0
        else:
            state.losses += 1; state.gross_loss += abs(pnl); state.consec_losses += 1
        state.total_pnl += pnl; state.daily_pnl += pnl
        state.set_cooldown(symbol)
        log_csv("CLOSE_EXT", t, live_price, pnl)
        tg_close(reason, t, live_price, pnl)
        brain.record_trade(symbol, t.side, t.entry_score, pnl, reason)
        del state.trades[symbol]
        return

    # ── Trade Expiration (Instrument-Z) ──
    if TRADE_EXPIRE_BARS > 0 and t.bar_count >= TRADE_EXPIRE_BARS:
        close_trade(ex, symbol, f"EXPIRADO ({t.bar_count} barras)", live_price)
        return

    # ── DCA SL DURO: si precio supera el SL del promedio → cierre inmediato ──
    if t.dca_count > 0 and t.dca_sl_price > 0:
        sl_hit = (
            (t.side == "long"  and live_price <= t.dca_sl_price) or
            (t.side == "short" and live_price >= t.dca_sl_price)
        )
        if sl_hit:
            close_trade(ex, symbol, f"DCA SL DURO (tras {t.dca_count} avg)", live_price)
            return

    # ── UTBot trailing stop como 2ª línea de defensa ──
    # Si el precio cruza la línea UTBot en dirección contraria → cierre
    if result is not None and symbol in state.trades:
        row = result.get("row")
        if row is not None:
            ut_stop_now = float(row.get("utbot_stop", 0.0))
            if t.side == "long" and ut_stop_now > 0:
                # UTBot sell signal activo Y precio bajo el stop
                if bool(row.get("utbot_sell")) and live_price < ut_stop_now:
                    if t.tp1_hit:  # solo si ya está en profit
                        close_trade(ex, symbol, "UTBOT TRAILING STOP", live_price)
                        return
            elif t.side == "short" and ut_stop_now > 0:
                if bool(row.get("utbot_buy")) and live_price > ut_stop_now:
                    if t.tp1_hit:
                        close_trade(ex, symbol, "UTBOT TRAILING STOP", live_price)
                        return

    # ── Cierre por pérdida dinámica (antes de TP1) ──
    if not t.tp1_hit:
        atr_now   = atr if atr > 0 else t.atr_entry
        loss_dist = (t.entry_price - live_price if t.side == "long"
                     else live_price - t.entry_price)
        if loss_dist >= atr_now * 1.0:
            # ── DCA AVERAGING (v18): antes de cerrar, intentar promediar ──
            if (DCA_ENABLED and t.dca_count < DCA_MAX_ORDERS
                    and t.dca_next_price > 0):
                trigger_hit = (
                    (t.side == "long"  and live_price <= t.dca_next_price) or
                    (t.side == "short" and live_price >= t.dca_next_price)
                )
                if trigger_hit:
                    execute_dca_order(ex, symbol, live_price, atr_now)
                    return  # No cerrar — promediamos
            # Sin DCA disponible → cerrar
            close_trade(ex, symbol, "PÉRDIDA DINÁMICA", live_price)
            return

    # ── Agotamiento (7 señales incluyendo SMI, WT, UTBot) ──
    if result is not None and symbol in state.trades:
        row = result.get("row")
        if row is not None:
            try:
                in_profit = ((t.side == "long"  and live_price > t.entry_price) or
                             (t.side == "short" and live_price < t.entry_price))
                if in_profit:
                    rsi_v     = float(row["rsi"])
                    adx_v     = float(row["adx"])
                    vol_ratio = float(row["volume"]) / max(float(row["vol_ma"]), 1)
                    smi_now   = float(row.get("smi", 0.0))
                    wt_now    = float(row.get("wt1", 0.0))
                    if t.side == "long":
                        e1 = bool(row["macd_bear"])
                        e2 = adx_v < 20
                        e3 = vol_ratio < 0.7
                        e4 = bool(row["bear_div"])
                        e5 = bool(row["osc_dn"])
                        e6 = rsi_v > 72
                        e7 = bool(row.get("smi_ob") or row.get("smi_cross_down"))
                        e8 = bool(row.get("wt_ob") or row.get("wt_cross_dn"))
                        e9 = bool(row.get("utbot_sell"))
                    else:
                        e1 = bool(row["macd_bull"])
                        e2 = adx_v < 20
                        e3 = vol_ratio < 0.7
                        e4 = bool(row["bull_div"])
                        e5 = bool(row["osc_up"])
                        e6 = rsi_v < 28
                        e7 = bool(row.get("smi_os") or row.get("smi_cross_up"))
                        e8 = bool(row.get("wt_os") or row.get("wt_cross_up"))
                        e9 = bool(row.get("utbot_buy"))
                    exh = sum([e1,e2,e3,e4,e5,e6,e7,e8,e9])
                    if exh >= 6:  # 6/9 señales — más exigente, deja correr los winners
                        profit = ((live_price - t.entry_price) if t.side == "long"
                                  else (t.entry_price - live_price)) * t.contracts

                        # Minimum profit check (Instrument-Z)
                        min_profit_usdt = t.entry_price * t.contracts * MIN_PROFIT_PCT
                        if profit < min_profit_usdt:
                            pass  # no cerrar si no alcanza mínimo
                        else:
                            tg(
                                f"🏁 <b>AGOTAMIENTO</b> — {symbol}\n"
                                f"Señales: {exh}/9 | ${profit:+.2f}\n"
                                f"{'✅' if e1 else '❌'} MACD  {'✅' if e2 else '❌'} ADX\n"
                                f"{'✅' if e3 else '❌'} Vol↓  {'✅' if e4 else '❌'} DivRSI\n"
                                f"{'✅' if e5 else '❌'} OSC   {'✅' if e6 else '❌'} RSIext\n"
                                f"{'✅' if e7 else '❌'} SMI{smi_now:.1f} "
                                f"{'✅' if e8 else '❌'} WT{wt_now:.1f} "
                                f"{'✅' if e9 else '❌'} UTBot\n"
                                f"⏰ {utcnow()}"
                            )
                            close_trade(ex, symbol, "AGOTAMIENTO", live_price)
                            return
            except Exception as e:
                log.debug(f"[{symbol}] agotamiento: {e}")

    # ── TP1 → Break-Even ──
    if not t.tp1_hit:
        hit = ((t.side == "long"  and live_price >= t.tp1_price) or
               (t.side == "short" and live_price <= t.tp1_price))
        if hit:
            t.tp1_hit    = True
            t.peak_price = live_price
            t.prev_price = live_price
            contracts    = float(live_pos.get("contracts", 0))
            pnl_est      = abs(t.tp1_price - t.entry_price) * contracts * 0.5
            move_be(ex, symbol)
            tg_tp1_be(t, live_price, pnl_est)

    # ── R:R Trail trigger (Bj Bot rrExit) ──
    if t.tp1_hit and symbol in state.trades and not t.rr_trail_active:
        triggered = ((t.side == "long"  and live_price >= t.rr_trail_stop) or
                     (t.side == "short" and live_price <= t.rr_trail_stop))
        if triggered:
            t.rr_trail_active = True
            tg(
                f"📐 <b>R:R TRAIL ACTIVADO</b> — {symbol}\n"
                f"Precio trigger: <code>{t.rr_trail_stop:.6g}</code>\n"
                f"R:R={RNR} | {RR_EXIT*100:.0f}% del camino al TP2\n"
                f"⏰ {utcnow()}"
            )

    # ── Trailing stop (3 fases + R:R) ──
    if t.tp1_hit and symbol in state.trades:
        atr_t = atr if atr > 0 else t.atr_entry

        if t.side == "long":
            cur_pct = (live_price - t.entry_price) / t.entry_price * 100
        else:
            cur_pct = (t.entry_price - live_price) / t.entry_price * 100
        t.max_profit_pct = max(t.max_profit_pct, cur_pct)

        new_peak = (live_price > t.peak_price if t.side == "long"
                    else live_price < t.peak_price)
        if new_peak:
            t.peak_price  = live_price
            t.stall_count = 0
        else:
            t.stall_count += 1

        denom = abs(t.peak_price - t.entry_price)
        if t.side == "long":
            retrace = (t.peak_price - live_price) / max(denom, 1e-9) * 100
        else:
            retrace = (live_price - t.peak_price) / max(denom, 1e-9) * 100

        prev_phase = t.trail_phase
        # R:R trail activo → fase más agresiva
        if t.rr_trail_active and retrace > 12:
            t.trail_phase = "locked"
        elif retrace > 25:
            t.trail_phase = "locked"
        elif t.stall_count >= 2:
            t.trail_phase = "tight"
        else:
            t.trail_phase = "normal"

        trail_m = {"normal": 0.7, "tight": 0.35, "locked": 0.15}[t.trail_phase]

        if t.trail_phase != prev_phase:
            tg_trail_phase(t, t.trail_phase, live_price, retrace, trail_m)

        if t.side == "long":
            t.trail_high = max(t.trail_high, live_price)
            if live_price <= t.trail_high - atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price)
                return
        else:
            t.trail_low = min(t.trail_low, live_price)
            if live_price >= t.trail_low + atr_t * trail_m:
                close_trade(ex, symbol, f"TRAILING {t.trail_phase.upper()}", live_price)
                return

        t.prev_price = live_price

    # ── Flip de dirección ──
    if symbol in state.trades:
        if t.side == "long"  and short_score >= MIN_SCORE + 2:
            close_trade(ex, symbol, "FLIP LONG→SHORT", live_price)
        elif t.side == "short" and long_score >= MIN_SCORE + 2:
            close_trade(ex, symbol, "FLIP SHORT→LONG", live_price)


# ══════════════════════════════════════════════════════════
# SCAN DE UN SÍMBOLO
# ══════════════════════════════════════════════════════════
def scan_symbol(ex: ccxt.Exchange, symbol: str) -> Optional[dict]:
    try:
        df  = fetch_df(ex, symbol, TF,   400)
        df1 = fetch_df(ex, symbol, HTF1, 200)
        df2 = fetch_df(ex, symbol, HTF2, 300)

        df  = compute(df)
        row = df.iloc[-2]

        # Validar que los indicadores están disponibles
        for col in ["adx", "rsi", "macd_hist", "smi", "wt1", "utbot_stop"]:
            if pd.isna(row.get(col, np.nan)):
                return None

        htf1_bull, htf1_bear = htf_bias(df1)
        htf2_bull, htf2_bear = htf2_macro(df2)

        # UpTrend: precio sobre EMA200
        uptrend = bool(row["close"] > row["ema200"])

        # ── Whale & Institutional signals (v14) ──
        wl, ws, whale_desc = whale_score_bonus(ex, symbol, "both")
        ls, ss = confluence_score(row, htf1_bull, htf1_bear, htf2_bull, htf2_bear,
                                  uptrend, whale_long=wl, whale_short=ws)

        rsi_v = float(row["rsi"])
        smi_v = float(row.get("smi", 0.0))
        wt_v  = float(row.get("wt1", 0.0))

        if rsi_extreme_long(rsi_v) or rsi_extreme_short(rsi_v):
            now  = time.time()
            last = state.rsi_alerts.get(symbol, 0)
            if now - last > 1800:
                state.rsi_alerts[symbol] = now
                tg_rsi_alert(symbol, rsi_v, smi_v, wt_v, ls, ss, float(row["close"]))

        return {
            "symbol":      symbol,
            "base":        symbol.split("/")[0],
            "long_score":  ls,
            "short_score": ss,
            "row":         row,
            "atr":         float(row["atr"]),
            "live_price":  float(row["close"]),
            "is_trending": bool(row["is_trending"]),
            "rsi":         rsi_v,
            "smi":         smi_v,
            "wt":          wt_v,
            "uptrend":     uptrend,
            "whale_desc":  whale_desc,
        }
    except Exception as e:
        log.debug(f"[{symbol}] scan: {e}")
        return None


# ══════════════════════════════════════════════════════════
# GRID MODE — Gana dinero cuando el mercado está lateral (v16)
# Cuando regime_chop=True, activa un mini-grid de 5 niveles
# Usa ATR para espaciar niveles, leverage reducido (3×)
# ══════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
# DCA AVERAGING (v18) — Modelo 3Commas/Bitsgap
# En vez de SL inmediato, promedia cuando el precio va en contra.
# Solo activa si el trade está en -1.5% y hay presupuesto disponible.
# ══════════════════════════════════════════════════════════════
DCA_ENABLED       = os.environ.get("DCA_ENABLED", "true").lower() == "true"
DCA_MAX_ORDERS    = int(os.environ.get("DCA_MAX_ORDERS",   "2"))    # máx 2 órdenes de promediado (seguro con $50)
DCA_STEP_PCT      = float(os.environ.get("DCA_STEP_PCT",   "1.5"))  # 1ª orden DCA si precio baja 1.5%
DCA_STEP_MULT     = float(os.environ.get("DCA_STEP_MULT",  "1.5"))  # cada paso multiplica distancia × 1.5 (3Commas default)
DCA_SIZE_MULT     = float(os.environ.get("DCA_SIZE_MULT",  "1.2"))  # cada orden DCA es 1.2× la anterior
DCA_SL_ATR_MULT   = float(os.environ.get("DCA_SL_ATR_MULT","3.0"))  # SL duro tras último DCA = 3× ATR del promedio
DCA_TP_PCT        = float(os.environ.get("DCA_TP_PCT",     "1.0"))  # TP cuando el precio vuelve al avg_entry + 1%

GRID_LEVELS       = 3      # REDUCIDO: 3 niveles (antes 5 = demasiada exposición)
GRID_ATR_MULT     = 0.7    # spacing ligeramente mayor = más margen entre niveles
GRID_LEVERAGE     = 2      # REDUCIDO: 2× (antes 3× — con $50 es demasiado)
GRID_USDT         = 2.0    # REDUCIDO: $2 por nivel (antes $4 = 360% exposición con $50)
GRID_MAX_ACTIVE   = 1      # REDUCIDO: máx 1 grid a la vez (antes 3 = colapsaba margen)
GRID_EXPIRE_H     = 4      # Cierre más rápido si no funciona (antes 6h)
GRID_MIN_VOLUME   = 1_000_000  # Volumen mínimo para grid


def open_grid(ex: ccxt.Exchange, symbol: str, price: float, atr: float):
    """Abre un mini-grid de 5 niveles cuando el mercado está lateral."""
    if symbol in state.grid_trades:
        return
    if len(state.grid_trades) >= GRID_MAX_ACTIVE:
        return

    spacing = atr * GRID_ATR_MULT
    if spacing <= 0 or price <= 0:
        return

    # Comprueba volumen mínimo
    try:
        ticker = ex.fetch_ticker(symbol)
        vol_24h = float(ticker.get("quoteVolume", 0) or 0)
        if vol_24h < GRID_MIN_VOLUME:
            return
    except Exception:
        return

    levels = []
    # Niveles de COMPRA: por debajo del precio actual
    for i in range(1, GRID_LEVELS + 1):
        buy_price = price - i * spacing
        if buy_price <= 0:
            continue
        try:
            ex.set_leverage(GRID_LEVERAGE, symbol, params={})
        except Exception:
            pass
        min_amt = get_min_amount(ex, symbol)
        amount = max((GRID_USDT * GRID_LEVERAGE) / buy_price, min_amt)
        try:
            amount = float(ex.amount_to_precision(symbol, amount))
            buy_price_p = float(ex.price_to_precision(symbol, buy_price))
            order = ex.create_order(symbol, "limit", "buy", amount,
                                    price=buy_price_p,
                                    params={"reduceOnly": False})
            levels.append(GridLevel(price=buy_price_p, side="buy",
                                    order_id=order.get("id", ""), filled=False))
        except Exception as e:
            log.warning(f"[GRID] {symbol} buy L{i}: {e}")

    if not levels:
        return

    gt = GridTrade(
        symbol=symbol, center=price, spacing=spacing,
        levels=levels, ts_open=time.time()
    )
    state.grid_trades[symbol] = gt
    tg(f"🔲 <b>GRID ABIERTO</b> — {symbol}\n"
       f"Precio: <code>{price:.6g}</code> | ATR: {atr:.5f}\n"
       f"Spacing: {spacing:.5f} | Niveles: {len(levels)}\n"
       f"Capital: ${GRID_USDT:.0f} × {GRID_LEVERAGE}× por nivel\n"
       f"⏰ {utcnow()}")
    log.info(f"[GRID OPEN] {symbol} center={price:.6g} spacing={spacing:.5f} "
             f"levels={len(levels)}")


def manage_grid(ex: ccxt.Exchange, symbol: str, live_price: float):
    """Gestiona un grid activo: detecta fills y coloca sell en el nivel superior."""
    if symbol not in state.grid_trades:
        return
    gt = state.grid_trades[symbol]

    # ── Expiración ──
    if time.time() - gt.ts_open > GRID_EXPIRE_H * 3600:
        close_grid(ex, symbol, "EXPIRADO")
        return

    # ── Verificar fills ──
    for lv in gt.levels:
        if lv.filled or not lv.order_id:
            continue
        try:
            order = ex.fetch_order(lv.order_id, symbol)
            status = order.get("status", "")
            if status == "closed":
                lv.filled = True
                fill_price = float(order.get("average") or order.get("price") or live_price)
                tp_price   = fill_price + gt.spacing  # TP en el nivel superior
                amount     = float(order.get("filled", 0))
                if amount > 0:
                    try:
                        tp_price_p = float(ex.price_to_precision(symbol, tp_price))
                        sell_ord   = ex.create_order(symbol, "limit", "sell", amount,
                                                     price=tp_price_p,
                                                     params={"reduceOnly": True})
                        pnl_est    = (tp_price - fill_price) * amount
                        lv.pnl     = pnl_est
                        gt.total_pnl += pnl_est
                        gt.n_trades  += 1
                        tg(f"✅ <b>GRID FILL</b> — {symbol}\n"
                           f"Compra: <code>{fill_price:.6g}</code> → "
                           f"TP: <code>{tp_price_p:.6g}</code>\n"
                           f"PnL est: ~${pnl_est:+.3f} | Total: ${gt.total_pnl:+.3f}\n"
                           f"⏰ {utcnow()}")
                    except Exception as e:
                        log.warning(f"[GRID] {symbol} TP order: {e}")
        except Exception as e:
            log.debug(f"[GRID] {symbol} check order: {e}")

    # ── Si el precio sale del rango del grid → cerrar ──
    lower_bound = gt.center - (GRID_LEVELS + 1) * gt.spacing
    upper_bound = gt.center + (GRID_LEVELS + 1) * gt.spacing
    if live_price < lower_bound * 0.995 or live_price > upper_bound * 1.005:
        close_grid(ex, symbol, "PRECIO FUERA DE RANGO")


def close_grid(ex: ccxt.Exchange, symbol: str, reason: str):
    """Cierra todos los pedidos del grid y lo elimina."""
    if symbol not in state.grid_trades:
        return
    gt = state.grid_trades[symbol]
    try:
        ex.cancel_all_orders(symbol)
    except Exception as e:
        log.warning(f"[GRID] cancel {symbol}: {e}")
    tg(f"🔲 <b>GRID CERRADO</b> — {symbol} ({reason})\n"
       f"Trades: {gt.n_trades} | PnL total: ${gt.total_pnl:+.3f}\n"
       f"⏰ {utcnow()}")
    del state.grid_trades[symbol]


# ══════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════
def main():
    global HEDGE_MODE

    log.info("=" * 65)
    log.info("  SATY ELITE v18 — DCA + ANTI-LOSS · 24/7")
    log.info("  UTBot + WaveTrend + Bj Bot R:R + BB+RSI + SMI")
    log.info("=" * 65)

    if not (API_KEY and API_SECRET):
        log.warning("DRY-RUN: sin claves API")
        while True: log.info("DRY-RUN..."); time.sleep(POLL_SECS)

    ex = None
    for attempt in range(10):
        try:
            ex = build_exchange()
            log.info("Exchange conectado ✓")
            break
        except Exception as e:
            wait = min(2 ** attempt, 120)
            log.warning(f"Conexión {attempt+1}/10: {e} — retry {wait}s")
            time.sleep(wait)

    if ex is None:
        raise RuntimeError("No se pudo conectar al exchange")

    HEDGE_MODE = detect_hedge_mode(ex)
    log.info(f"Modo cuenta: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'}")

    balance = 0.0
    for i in range(10):
        try:
            balance = get_balance(ex)
            break
        except Exception as e:
            log.warning(f"get_balance {i+1}/10: {e}")
            time.sleep(5)

    state.peak_equity    = balance
    state.daily_reset_ts = time.time()
    log.info(f"Balance: ${balance:.2f} USDT")

    symbols: List[str] = []
    while not symbols:
        try:
            ex.load_markets()
            symbols = get_symbols(ex)
        except Exception as e:
            log.error(f"get_symbols: {e} — reintento 60s")
            time.sleep(60)

    update_btc_bias(ex)
    tg_startup(balance, len(symbols))

    scan_count    = 0
    REFRESH_EVERY = max(1, 3600 // max(POLL_SECS, 1))
    BTC_REFRESH   = max(1, 900  // max(POLL_SECS, 1))
    HB_INTERVAL   = 3600

    while True:
        ts_start = time.time()
        try:
            scan_count += 1
            state.reset_daily()
            clear_cache()

            log.info(
                f"━━━ SCAN #{scan_count} "
                f"{datetime.now(timezone.utc):%H:%M:%S} "
                f"| {len(symbols)} pares "
                f"| {state.open_count()}/{MAX_OPEN_TRADES} trades "
                f"| bases: {list(state.bases_open().keys())} ━━━"
            )

            if scan_count % REFRESH_EVERY == 0:
                try:
                    ex.load_markets(); symbols = get_symbols(ex)
                except Exception as e:
                    log.warning(f"Refresh: {e}")

            if scan_count % BTC_REFRESH == 0:
                update_btc_bias(ex)

            if time.time() - state.last_heartbeat > HB_INTERVAL:
                try:
                    tg_heartbeat(get_balance(ex))
                    state.last_heartbeat = time.time()
                except Exception:
                    pass

            if state.cb_active():
                log.warning(f"CIRCUIT BREAKER >= {CB_DD}%")
                time.sleep(POLL_SECS); continue

            if state.daily_limit_hit():
                log.warning(f"LÍMITE DIARIO >= {DAILY_LOSS_LIMIT}%")
                time.sleep(POLL_SECS); continue

            live_positions = get_all_positions(ex)

            # ── Gestionar trades de tendencia abiertos ──
            for sym in list(state.trades.keys()):
                try:
                    lp  = live_positions.get(sym)
                    lp_ = float(lp["markPrice"]) if lp else get_last_price(ex, sym)
                    res = scan_symbol(ex, sym)
                    ls  = res["long_score"]  if res else 0
                    ss  = res["short_score"] if res else 0
                    atr = res["atr"]         if res else state.trades[sym].atr_entry
                    manage_trade(ex, sym, lp_, atr, ls, ss, lp, res)
                except Exception as e:
                    log.warning(f"[{sym}] manage: {e}")

            # ── Gestionar grids activos (mercado lateral) ──
            for sym in list(state.grid_trades.keys()):
                try:
                    lp_ = get_last_price(ex, sym)
                    manage_grid(ex, sym, lp_)
                except Exception as e:
                    log.warning(f"[GRID {sym}] manage: {e}")

            # ── Buscar nuevas entradas ──
            new_signals: List[dict] = []

            if state.open_count() < MAX_OPEN_TRADES:
                bases_open = state.bases_open()
                to_scan    = [
                    s for s in symbols
                    if s not in state.trades
                    and not state.in_cooldown(s)
                    and not brain.is_blacklisted(s)      # 🧠 Brain blacklist
                    and s.split("/")[0] not in bases_open
                ]

                log.info(f"Escaneando {len(to_scan)} pares "
                         f"(excluidas bases: {list(bases_open.keys())})")

                with ThreadPoolExecutor(max_workers=12) as pool:
                    futures = {pool.submit(scan_symbol, ex, s): s for s in to_scan}
                    results = [f.result() for f in as_completed(futures)
                               if f.result() is not None]

                for res in results:
                    base       = res["base"]
                    best_side  = None
                    best_score = 0
                    uptrend    = res["uptrend"]

                    can_long  = (res["long_score"]  >= MIN_SCORE and res["is_trending"])
                    can_short = (res["short_score"] >= MIN_SCORE and res["is_trending"])

                    # ── FIX #1 — MACRO OVERRIDE (el más importante) ──
                    # Si BTC RSI > 75 = rally extremo → CERO shorts (APE/STX lección)
                    # Si BTC RSI < 25 = crash extremo → CERO longs
                    # Ningún score, por alto que sea, supera el contexto macro
                    if state.btc_rsi > 75:
                        can_short = False   # BLOQUEADO: rally extremo, no contraría
                    if state.btc_rsi < 25:
                        can_long  = False   # BLOQUEADO: crash extremo, no contraría

                    if BTC_FILTER:
                        if state.btc_bear: can_long  = False
                        if state.btc_bull: can_short = False

                    if state.base_has_trade(base):
                        continue

                    # ── FIX #2 — FLIP COOLDOWN: ignorar pares con score inestable ──
                    # STX: SHORT→LONG en 1 barra = ruido puro, no operar
                    row = res.get("row")
                    long_s  = res["long_score"]
                    short_s = res["short_score"]
                    score_diff = abs(long_s - short_s)
                    if score_diff < 5:  # FIX#2: STX L:13 S:13 → diff=0 → skip (ruido)
                        # Scores demasiado parecidos = par indeciso = skip
                        continue

                    # ── FIX #3 — GRID MODE con RSI filter ──
                    # FARTCOIN grid lección: no abrir grid si RSI > 70 (está subiendo, no lateral)
                    if (row is not None
                            and bool(row.get("regime_chop", False))
                            and res["symbol"] not in state.grid_trades
                            and res["symbol"] not in state.trades
                            and len(state.grid_trades) < GRID_MAX_ACTIVE):
                        rsi_now = float(row.get("rsi", 50))
                        if 35 <= rsi_now <= 65:  # FIX#3: FARTCOIN RSI 85 = no lateral. Grid solo si RSI neutral real
                            try:
                                open_grid(ex, res["symbol"],
                                          float(row["close"]), float(row["atr"]))
                            except Exception as ge:
                                log.debug(f"[GRID] {res['symbol']}: {ge}")
                        continue  # No abrir trade de tendencia en mercado chop

                    if can_long  and res["long_score"]  > best_score:
                        best_score = res["long_score"];  best_side = "long"
                    if can_short and res["short_score"] > best_score:
                        best_score = res["short_score"]; best_side = "short"

                    if best_side:
                        new_signals.append({
                            "symbol":     res["symbol"],
                            "base":       base,
                            "side":       best_side,
                            "score":      best_score,
                            "row":        res["row"],
                            "rsi":        res["rsi"],
                            "smi":        res["smi"],
                            "wt":         res["wt"],
                            "uptrend":    uptrend,
                            "whale_desc": res.get("whale_desc", ""),
                        })

                new_signals.sort(key=lambda x: x["score"], reverse=True)

                for sig in new_signals:
                    if state.open_count() >= MAX_OPEN_TRADES: break
                    sym  = sig["symbol"]
                    base = sig["base"]
                    if sym in state.trades:        continue
                    if state.base_has_trade(base): continue
                    if state.in_cooldown(sym):      continue

                    order_side = "buy" if sig["side"] == "long" else "sell"
                    t = open_trade(ex, sym, base, order_side,
                                   sig["score"], sig["row"], sig["uptrend"],
                                   whale_desc=sig.get("whale_desc", ""))
                    if t:
                        state.trades[sym] = t

            else:
                log.info(f"Max trades alcanzado ({MAX_OPEN_TRADES})")

            elapsed = time.time() - ts_start
            log.info(
                f"✓ {elapsed:.1f}s | señales:{len(new_signals)} | "
                f"{state.wins}W/{state.losses}L | "
                f"hoy:${state.daily_pnl:+.2f} | total:${state.total_pnl:+.2f}"
            )

            if scan_count % 20 == 0:
                tg_summary(new_signals, len(symbols))

        except ccxt.NetworkError as e:
            log.warning(f"Network: {e} — 10s")
            time.sleep(10)
        except ccxt.ExchangeError as e:
            log.error(f"Exchange: {e}")
            tg(f"❌ Exchange: <code>{str(e)[:200]}</code>")
        except KeyboardInterrupt:
            log.info("Detenido.")
            tg("🛑 <b>Bot detenido.</b>")
            break
        except Exception as e:
            log.exception(f"Error: {e}")
            tg_error(str(e))

        elapsed = time.time() - ts_start
        time.sleep(max(0, POLL_SECS - elapsed))


if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            log.info("Detenido por usuario.")
            break
        except Exception as e:
            log.exception(f"CRASH: {e}")
            try: tg_error(f"CRASH — reinicio en 30s:\n{e}")
            except Exception: pass
            log.info("Reiniciando en 30s...")
            time.sleep(30)
