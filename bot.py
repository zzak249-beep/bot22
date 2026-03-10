"""
╔══════════════════════════════════════════════════════════════════╗
║   BINGX FUTURES BOT v5.2 — Compounding + Brain Agresivo              ║
║   Estrategia: Multi-señal con confirmación                       ║
║   • RSI + EMA + Bollinger Bands + Volume Spike                   ║
║   • Leverage 10x-20x configurable                                ║
║   • Long Y Short — opera en ambas direcciones                    ║
║   • Take Profit escalonado (50% en TP1, 50% en TP2)             ║
║   • Trailing stop dinámico                                       ║
║   • Anti-liquidación: stop loss obligatorio siempre             ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, asyncio, logging, json, time
from datetime import datetime
from collections import deque
from dotenv import load_dotenv
import ccxt.async_support as ccxt
import aiohttp

load_dotenv()

# ─── LOGGING ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
log = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────
API_KEY    = os.getenv("BINGX_API_KEY", "")
API_SECRET = os.getenv("BINGX_API_SECRET", "")
DRY_RUN    = os.getenv("DRY_RUN", "true").lower() == "true"

LEVERAGE            = int(os.getenv("LEVERAGE", "10"))
TRADE_AMOUNT_USDT   = float(os.getenv("TRADE_AMOUNT_USDT", "5"))
MAX_OPEN_POSITIONS  = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
LOOP_INTERVAL       = int(os.getenv("LOOP_INTERVAL", "10"))
MAX_DAILY_LOSS_USDT = float(os.getenv("MAX_DAILY_LOSS_USDT", "20"))

# Take Profit / Stop Loss (% sobre precio, SIN apalancamiento)
TP1_PCT   = float(os.getenv("TP1_PCT",  "0.4"))   # cierra 50% posición
TP2_PCT   = float(os.getenv("TP2_PCT",  "0.9"))   # cierra resto
SL_PCT    = float(os.getenv("SL_PCT",   "0.5"))   # stop loss fijo
TRAIL_PCT = float(os.getenv("TRAIL_PCT","0.3"))   # trailing desde máximo

# ─── COMPOUNDING & APRENDIZAJE ────────────────────────────────────
COMPOUND_RATE       = float(os.getenv("COMPOUND_RATE", "0.5"))    # 50% ganancias → reinvierte
MIN_TRADE_USDT      = float(os.getenv("MIN_TRADE_USDT", "5"))     # mínimo por trade
MAX_TRADE_USDT      = float(os.getenv("MAX_TRADE_USDT", "50"))    # máximo por trade
BLACKLIST_FAILS     = int(os.getenv("BLACKLIST_FAILS", "3"))       # fallos para blacklist
BLACKLIST_MINUTES   = int(os.getenv("BLACKLIST_MINUTES", "60"))    # minutos bloqueado

# Indicadores
RSI_PERIOD      = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD    = float(os.getenv("RSI_OVERSOLD",  "35"))  # señal long
RSI_OVERBOUGHT  = float(os.getenv("RSI_OVERBOUGHT","65"))  # señal short
EMA_FAST        = int(os.getenv("EMA_FAST", "9"))
EMA_SLOW        = int(os.getenv("EMA_SLOW", "21"))
BB_PERIOD       = int(os.getenv("BB_PERIOD", "20"))
BB_STD          = float(os.getenv("BB_STD", "2.0"))
VOL_SPIKE_MULT  = float(os.getenv("VOL_SPIKE_MULT", "1.5"))  # volumen X veces la media
MIN_SIGNALS     = int(os.getenv("MIN_SIGNALS", "2"))  # señales mínimas para entrar

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
MEMORY_FILE      = "memory.json"

# Pares a monitorear — futuros perpetuos con más volumen en BingX
WATCHLIST = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
    "ADA/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT",
    "DOT/USDT:USDT", "ARB/USDT:USDT",  "APT/USDT:USDT",
    "MATIC/USDT:USDT", "LTC/USDT:USDT", "ATOM/USDT:USDT",
    "NEAR/USDT:USDT", "TRX/USDT:USDT",  "OP/USDT:USDT",
]


# ─── TELEGRAM ─────────────────────────────────────────────────────
class Telegram:
    def __init__(self):
        self.enabled = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

    async def send(self, msg: str, silent: bool = False):
        if not self.enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={
                    "chat_id": TELEGRAM_CHAT_ID, "text": msg,
                    "parse_mode": "Markdown", "disable_notification": silent,
                }, timeout=aiohttp.ClientTimeout(total=5))
        except Exception as e:
            log.warning(f"Telegram: {e}")


# ─── INDICADORES ──────────────────────────────────────────────────
def calc_rsi(prices: list, period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        d = prices[-period - 1 + i] - prices[-period - 2 + i]
        (gains if d >= 0 else losses).append(abs(d))
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.001
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calc_ema(prices: list, period: int) -> float | None:
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_bb(prices: list, period: int = 20, std_mult: float = 2.0):
    if len(prices) < period:
        return None, None, None
    window = prices[-period:]
    mean   = sum(window) / period
    std    = (sum((p - mean) ** 2 for p in window) / period) ** 0.5
    return mean - std_mult * std, mean, mean + std_mult * std


# ─── PRICE HISTORY ────────────────────────────────────────────────
class PriceHistory:
    def __init__(self, maxlen: int = 200):
        self.closes:  dict[str, deque] = {}
        self.volumes: dict[str, deque] = {}
        self.maxlen = maxlen

    def update(self, symbol: str, close: float, volume: float):
        for d in [self.closes, self.volumes]:
            if symbol not in d:
                d[symbol] = deque(maxlen=self.maxlen)
        self.closes[symbol].append(close)
        self.volumes[symbol].append(volume)

    def ready(self, symbol: str, n: int = 50) -> bool:
        return symbol in self.closes and len(self.closes[symbol]) >= n

    def get(self, symbol: str) -> tuple[list, list]:
        return list(self.closes.get(symbol, [])), list(self.volumes.get(symbol, []))


# ─── SEÑAL ────────────────────────────────────────────────────────
class Signal:
    def __init__(self, symbol, side, price, score, reasons):
        self.symbol  = symbol
        self.side    = side     # "long" | "short"
        self.price   = price
        self.score   = score    # 0-4 señales confirmadas
        self.reasons = reasons  # lista de indicadores que confirmaron

    def __str__(self):
        return (f"{'🟢 LONG' if self.side=='long' else '🔴 SHORT'} "
                f"{self.symbol} score={self.score} [{', '.join(self.reasons)}]")


# ─── MOTOR DE SEÑALES ─────────────────────────────────────────────
class SignalEngine:
    """
    Genera señales combinando 4 indicadores:
      1. RSI — sobrecompra/sobreventa
      2. EMA crossover — tendencia
      3. Bollinger Bands — precio en banda extrema
      4. Volume spike — confirmación de movimiento

    Necesita MIN_SIGNALS confirmaciones para disparar.
    """

    def __init__(self, history: PriceHistory):
        self.history = history

    def analyze(self, symbol: str, current_price: float, current_vol: float) -> Signal | None:
        closes, volumes = self.history.get(symbol)
        if len(closes) < 50:
            return None

        long_signals  = []
        short_signals = []

        # ── RSI ──────────────────────────────────────────────────
        rsi = calc_rsi(closes, RSI_PERIOD)
        if rsi is not None:
            if rsi <= RSI_OVERSOLD:
                long_signals.append(f"RSI={rsi:.1f}")
            elif rsi >= RSI_OVERBOUGHT:
                short_signals.append(f"RSI={rsi:.1f}")

        # ── EMA Crossover ────────────────────────────────────────
        ema_f = calc_ema(closes, EMA_FAST)
        ema_s = calc_ema(closes, EMA_SLOW)
        if ema_f and ema_s:
            if ema_f > ema_s * 1.001:   # EMA rápida por encima
                long_signals.append(f"EMA↑")
            elif ema_f < ema_s * 0.999: # EMA rápida por debajo
                short_signals.append(f"EMA↓")

        # ── Bollinger Bands ──────────────────────────────────────
        bb_low, bb_mid, bb_high = calc_bb(closes, BB_PERIOD, BB_STD)
        if bb_low and bb_high:
            if current_price <= bb_low:
                long_signals.append(f"BB_low")
            elif current_price >= bb_high:
                short_signals.append(f"BB_high")

        # ── Volume Spike ─────────────────────────────────────────
        if len(volumes) >= 10:
            avg_vol = sum(list(volumes)[-10:]) / 10
            if current_vol >= avg_vol * VOL_SPIKE_MULT:
                # Volumen alto confirma la dirección dominante
                long_signals.append(f"VOL×{current_vol/avg_vol:.1f}")
                short_signals.append(f"VOL×{current_vol/avg_vol:.1f}")

        # ── Momentum (precio vs EMA media) ───────────────────────
        if bb_mid:
            if current_price < bb_mid * 0.995:
                long_signals.append("MOM↓rev")
            elif current_price > bb_mid * 1.005:
                short_signals.append("MOM↑rev")

        # Decide dirección
        if len(long_signals) >= MIN_SIGNALS:
            return Signal(symbol, "long", current_price, len(long_signals), long_signals)
        if len(short_signals) >= MIN_SIGNALS:
            return Signal(symbol, "short", current_price, len(short_signals), short_signals)
        return None


# ─── POSICIÓN ─────────────────────────────────────────────────────
class Position:
    def __init__(self, symbol, side, entry, qty, usdt, leverage):
        self.symbol     = symbol
        self.side       = side       # "long" | "short"
        self.entry      = entry
        self.qty        = qty
        self.usdt       = usdt
        self.leverage   = leverage
        self.opened_at  = time.time()
        self.max_pnl    = 0.0
        self.tp1_done   = False      # ya cerró 50% en TP1

    def pnl_pct(self, price: float) -> float:
        if self.entry == 0:
            return 0.0
        raw = ((price - self.entry) / self.entry) * 100
        return raw * self.leverage if self.side == "long" else -raw * self.leverage

    def pnl_usdt(self, price: float) -> float:
        return self.usdt * (self.pnl_pct(price) / 100)

    def age_min(self) -> float:
        return (time.time() - self.opened_at) / 60


# ─── RISK MANAGER ─────────────────────────────────────────────────
class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.trades    = 0
        self.day_start = datetime.now().date()
        self.paused    = False
        self.reason    = ""

    def reset(self):
        if datetime.now().date() != self.day_start:
            self.daily_pnl = 0.0
            self.trades    = 0
            self.day_start = datetime.now().date()
            self.paused    = False
            log.info("🔄 Nuevo día — límites reseteados")

    def register(self, pnl: float):
        self.daily_pnl += pnl
        self.trades    += 1

    def can_trade(self) -> tuple:
        self.reset()
        if self.paused:
            return False, self.reason
        if self.daily_pnl <= -MAX_DAILY_LOSS_USDT:
            self.paused = True
            self.reason = f"Max pérdida diaria ${MAX_DAILY_LOSS_USDT}"
            return False, self.reason
        return True, ""

    def summary(self) -> str:
        return f"PnL hoy: ${self.daily_pnl:+.4f} | Trades: {self.trades}"



# ─── SYMBOL BRAIN ─────────────────────────────────────────────────
class SymbolBrain:
    """
    Aprende qué símbolos son rentables y cuáles fallan.
    - Penaliza símbolos con muchas pérdidas → blacklist temporal
    - Prioriza símbolos con historial ganador
    """
    def __init__(self):
        self.data: dict = {}   # symbol → {wins, losses, pnl, blacklist_until}

    def _get(self, sym: str) -> dict:
        if sym not in self.data:
            self.data[sym] = {"wins": 0, "losses": 0, "pnl": 0.0, "blacklist_until": 0.0}
        return self.data[sym]

    def is_blacklisted(self, sym: str) -> bool:
        d = self._get(sym)
        if time.time() < d["blacklist_until"]:
            return True
        d["blacklist_until"] = 0.0
        return False

    def on_win(self, sym: str, pnl: float):
        d = self._get(sym)
        d["wins"] += 1
        d["pnl"]  += pnl

    def on_loss(self, sym: str, pnl: float):
        d = self._get(sym)
        d["losses"] += 1
        d["pnl"]    += pnl
        total = d["wins"] + d["losses"]
        if total >= BLACKLIST_FAILS:
            fail_rate = d["losses"] / total
            if fail_rate >= 0.7:   # 70%+ fallos → blacklist
                d["blacklist_until"] = time.time() + BLACKLIST_MINUTES * 60
                log.warning(f"🚫 BLACKLIST {sym} | fallos={d['losses']}/{total} | {BLACKLIST_MINUTES}min")

    def score(self, sym: str) -> float:
        d = self._get(sym)
        total = d["wins"] + d["losses"]
        if total < 2:
            return 1.0
        return (d["wins"] / total) * max(d["pnl"], 0.001)

    def dump(self) -> dict:
        return dict(self.data)

    def load(self, data: dict):
        self.data = data


# ─── BOT ──────────────────────────────────────────────────────────
class Bot:
    def __init__(self):
        self.ex = ccxt.bingx({
            "apiKey"         : API_KEY,
            "secret"         : API_SECRET,
            "enableRateLimit": True,
            "options"        : {"defaultType": "swap"},  # FUTUROS PERPETUOS
        })
        self.positions : list[Position] = []
        self.history   = PriceHistory()
        self.engine    = SignalEngine(self.history)
        self.risk      = RiskManager()
        self.tg        = Telegram()
        self.capital   = TRADE_AMOUNT_USDT
        self.total_pnl = 0.0
        self.cycles    = 0
        self.wins      = 0
        self.losses    = 0
        self._last_report = time.time()
        self.brain     = SymbolBrain()
        self._load()

    def _load(self):
        try:
            with open(MEMORY_FILE) as f:
                d = json.load(f)
            self.capital   = float(d.get("capital",   TRADE_AMOUNT_USDT))
            self.total_pnl = float(d.get("total_pnl", 0.0))
            self.wins      = int(d.get("wins",   0))
            self.losses    = int(d.get("losses", 0))
            self.brain.load(d.get("brain", {}))
            log.info(
                f"💾 Capital: ${self.capital:.4f} | "
                f"PnL total: ${self.total_pnl:+.4f} | "
                f"W/L: {self.wins}/{self.losses} | "
                f"Brain: {len(self.brain.data)} símbolos"
            )
        except Exception:
            pass

    def _save(self):
        try:
            with open(MEMORY_FILE, "w") as f:
                json.dump({
                    "capital"  : round(self.capital,   6),
                    "total_pnl": round(self.total_pnl, 6),
                    "wins"     : self.wins,
                    "losses"   : self.losses,
                    "brain"    : self.brain.dump(),
                    "updated"  : datetime.now().isoformat(),
                }, f, indent=2)
        except Exception as e:
            log.warning(f"Save error: {e}")

    async def _set_leverage(self, symbol: str):
        if DRY_RUN:
            return
        try:
            await self.ex.set_leverage(LEVERAGE, symbol)
        except Exception:
            pass

    async def fetch_candles(self):
        """Descarga velas de 1m para cada símbolo del watchlist."""
        for symbol in WATCHLIST:
            try:
                ohlcv = await self.ex.fetch_ohlcv(symbol, "1m", limit=100)
                for candle in ohlcv:
                    _, _, _, _, close, volume = candle
                    self.history.update(symbol, float(close), float(volume))
            except Exception as e:
                log.debug(f"fetch_ohlcv {symbol}: {e}")

    async def fetch_live_prices(self) -> dict:
        """Precios en TIEMPO REAL — imprescindible para SL/TP."""
        prices = {}
        try:
            # Intenta todos a la vez
            syms = list({p.symbol for p in self.positions} | set(WATCHLIST))
            tickers = await self.ex.fetch_tickers(syms)
            for sym, t in tickers.items():
                p = t.get("last") or t.get("close")
                if p and float(p) > 0:
                    prices[sym] = float(p)
        except Exception:
            # Fallback: uno a uno solo las posiciones abiertas
            for pos in self.positions:
                try:
                    t = await self.ex.fetch_ticker(pos.symbol)
                    p = t.get("last") or t.get("close")
                    if p:
                        prices[pos.symbol] = float(p)
                except Exception as e:
                    log.warning(f"fetch_ticker {pos.symbol}: {e}")
        return prices

    async def sync_positions_with_exchange(self):
        """
        Al arrancar, sincroniza posiciones reales de BingX con el bot.
        Evita que el bot ignore posiciones ya abiertas.
        """
        if DRY_RUN:
            return
        try:
            real = await self.ex.fetch_positions()
            for p in real:
                if float(p.get("contracts", 0)) == 0:
                    continue
                sym    = p["symbol"]
                side   = "long" if p["side"] == "long" else "short"
                entry  = float(p.get("entryPrice") or p.get("averagePrice") or 0)
                qty    = float(p.get("contracts", 0))
                notional = float(p.get("notional") or TRADE_AMOUNT_USDT)
                lev    = int(p.get("leverage") or LEVERAGE)
                if entry > 0 and qty > 0:
                    already = any(x.symbol == sym and x.side == side for x in self.positions)
                    if not already:
                        pos = Position(sym, side, entry, qty, abs(notional) / lev, lev)
                        self.positions.append(pos)
                        log.info(f"📥 Posición sincronizada: {side.upper()} {sym} entry={entry} qty={qty}")
            if self.positions:
                log.info(f"✅ {len(self.positions)} posiciones cargadas desde BingX")
        except Exception as e:
            log.warning(f"sync_positions error: {e}")

    @property
    def trade_amount(self) -> float:
        """Capital por trade con compounding — reinvierte % de ganancias."""
        base = TRADE_AMOUNT_USDT
        if self.total_pnl > 0:
            base = base + self.total_pnl * COMPOUND_RATE
        return round(max(MIN_TRADE_USDT, min(MAX_TRADE_USDT, base)), 4)

    async def open_position(self, sig: Signal) -> bool:
        symbol = sig.symbol

        # Blacklist — no operar símbolos con mal historial
        if self.brain.is_blacklisted(symbol):
            log.info(f"🚫 {symbol} en blacklist — skip")
            return False

        # Evita hedging — no abrir si ya hay posición en este símbolo
        if any(p.symbol == symbol for p in self.positions):
            log.info(f"⛔ {symbol} ya tiene posición abierta — skip")
            return False

        price  = sig.price
        amount = self.trade_amount
        qty    = round((amount * LEVERAGE) / price, 4)

        log.info(f"🚀 {sig} | precio={price:.4f} qty={qty} lev={LEVERAGE}x")

        if DRY_RUN:
            pos = Position(symbol, sig.side, price, qty, amount, LEVERAGE)
            self.positions.append(pos)
            await self.tg.send(
                f"{'🟢' if sig.side=='long' else '🔴'} *{'LONG' if sig.side=='long' else 'SHORT'}* "
                f"`{symbol}`\n"
                f"Precio    : `{price:.4f}`\n"
                f"Score     : `{sig.score}/5` — {', '.join(sig.reasons)}\n"
                f"Cantidad  : `{qty}`\n"
                f"Leverage  : `{LEVERAGE}x`\n"
                f"Capital   : `${amount:.2f}` → expuesto `${amount*LEVERAGE:.2f}`\n"
                f"TP1={TP1_PCT*LEVERAGE:.1f}% | TP2={TP2_PCT*LEVERAGE:.1f}% | SL=-{SL_PCT*LEVERAGE:.1f}%"
            )
            return True

        try:
            await self._set_leverage(symbol)
            if sig.side == "long":
                order = await self.ex.create_market_buy_order(symbol, qty)
            else:
                order = await self.ex.create_market_sell_order(symbol, qty)

            filled = float(order.get("average") or order.get("price") or price)
            pos = Position(symbol, sig.side, filled, qty, amount, LEVERAGE)
            self.positions.append(pos)

            await self.tg.send(
                f"{'🟢' if sig.side=='long' else '🔴'} *{'LONG' if sig.side=='long' else 'SHORT'}* "
                f"`{symbol}`\n"
                f"Precio    : `{filled:.4f}`\n"
                f"Score     : `{sig.score}/5`\n"
                f"Leverage  : `{LEVERAGE}x`\n"
                f"TP1={TP1_PCT*LEVERAGE:.1f}% | TP2={TP2_PCT*LEVERAGE:.1f}% | SL=-{SL_PCT*LEVERAGE:.1f}%"
            )
            return True
        except Exception as e:
            log.error(f"open_position error {symbol}: {e}")
            return False

    async def close_position(self, pos: Position, price: float, reason: str, partial: float = 1.0):
        pnl_pct  = pos.pnl_pct(price)
        pnl_usdt = pos.pnl_usdt(price) * partial
        qty      = round(pos.qty * partial, 4)

        log.info(
            f"{'✅' if pnl_usdt >= 0 else '❌'} CIERRE {pos.symbol} [{reason}] | "
            f"entrada={pos.entry:.4f} salida={price:.4f} | "
            f"PnL={pnl_pct:+.2f}% (${pnl_usdt:+.4f}) | "
            f"partial={partial*100:.0f}%"
        )

        if not DRY_RUN:
            try:
                if pos.side == "long":
                    await self.ex.create_market_sell_order(pos.symbol, qty)
                else:
                    await self.ex.create_market_buy_order(pos.symbol, qty)
            except Exception as e:
                log.error(f"close error {pos.symbol}: {e}")

        # Actualiza capital y stats
        self.capital   += pnl_usdt
        self.total_pnl += pnl_usdt
        self.risk.register(pnl_usdt)
        if pnl_usdt >= 0:
            self.wins += 1
            self.brain.on_win(pos.symbol, pnl_usdt)
        else:
            self.losses += 1
            self.brain.on_loss(pos.symbol, pnl_usdt)
        self._save()

        # Log compounding
        log.info(
            f"  💰 Compounding | Capital: ${self.capital:.4f} | "
            f"Próx trade: ${self.trade_amount:.4f} | "
            f"PnL total: ${self.total_pnl:+.4f}"
        )

        await self.tg.send(
            f"{'✅' if pnl_usdt >= 0 else '❌'} *Cerrado* `{pos.symbol}`\n"
            f"Razón       : {reason}\n"
            f"PnL         : `{pnl_pct:+.2f}%` (${pnl_usdt:+.4f})\n"
            f"Capital     : `${self.capital:.4f}` USDT\n"
            f"Próx trade  : `${self.trade_amount:.4f}` USDT\n"
            f"PnL total   : `${self.total_pnl:+.4f}` USDT\n"
            f"{self.risk.summary()}"
        )

    async def manage_positions(self, current_prices: dict):
        """
        Gestión de posiciones con precios en tiempo real.
        Siempre loguea estado de cada posición para debug.
        """
        to_remove = []

        for pos in self.positions:
            # Precio en tiempo real — fetch individual si no está en current_prices
            price = current_prices.get(pos.symbol)
            if not price:
                try:
                    t = await self.ex.fetch_ticker(pos.symbol)
                    price = float(t.get("last") or t.get("close") or 0)
                    if price > 0:
                        current_prices[pos.symbol] = price
                except Exception as e:
                    log.warning(f"No precio para {pos.symbol}: {e}")
                    continue

            pnl = pos.pnl_pct(price)
            if pnl > pos.max_pnl:
                pos.max_pnl = pnl

            # Log estado de cada posición en cada ciclo
            log.info(
                f"📌 {pos.side.upper()} {pos.symbol} | "
                f"entry={pos.entry:.4f} now={price:.4f} | "
                f"PnL={pnl:+.2f}% max={pos.max_pnl:.2f}% | "
                f"age={pos.age_min():.0f}min | "
                f"TP1={'✅' if pos.tp1_done else f'{TP1_PCT*LEVERAGE:.1f}%'} "
                f"SL=-{SL_PCT*LEVERAGE:.1f}%"
            )

            reason = None

            # TP1 — cierra 50% al primer objetivo
            if not pos.tp1_done and pnl >= TP1_PCT * LEVERAGE:
                pos.tp1_done = True
                pos.qty      = round(pos.qty * 0.5, 6)
                pos.usdt     = pos.usdt * 0.5
                await self.close_position(pos, price, f"TP1 +{pnl:.2f}%")
                log.info(f"✅ TP1 ejecutado {pos.symbol} — queda 50%")
                continue

            # TP2 — cierra el resto
            if pos.tp1_done and pnl >= TP2_PCT * LEVERAGE:
                reason = f"TP2 +{pnl:.2f}%"

            # Stop Loss — prioridad máxima
            elif pnl <= -(SL_PCT * LEVERAGE):
                reason = f"SL {pnl:.2f}%"

            # Trailing Stop
            elif pos.max_pnl >= TP1_PCT * LEVERAGE * 0.5 and pnl < pos.max_pnl - TRAIL_PCT * LEVERAGE:
                reason = f"Trail max={pos.max_pnl:.2f}%→{pnl:.2f}%"

            # Timeout 30 minutos (reducido de 45)
            elif pos.age_min() > 30:
                reason = f"Timeout {pos.age_min():.0f}min PnL={pnl:+.2f}%"

            if reason:
                log.info(f"🔔 Cerrando {pos.symbol} — {reason}")
                await self.close_position(pos, price, reason)
                to_remove.append(pos)

        for p in to_remove:
            if p in self.positions:
                self.positions.remove(p)

        if to_remove:
            log.info(f"🔄 Posiciones cerradas este ciclo: {len(to_remove)}")

    async def cycle(self):
        self.cycles += 1

        # Descarga velas
        await self.fetch_candles()

        # Precios en TIEMPO REAL para gestión de posiciones (SL/TP)
        current_prices = await self.fetch_live_prices()

        # Fallback a último cierre de vela si no hay precio live
        for sym in WATCHLIST:
            if sym not in current_prices:
                closes, _ = self.history.get(sym)
                if closes:
                    current_prices[sym] = closes[-1]

        # Gestiona posiciones abiertas con precios reales
        await self.manage_positions(current_prices)

        # Log periódico
        if self.cycles % 12 == 0:  # cada ~2 minutos
            total = self.wins + self.losses
            wr    = f"{self.wins/total*100:.1f}%" if total else "—"
            log.info(
                f"Ciclo {self.cycles} | "
                f"Pos: {len(self.positions)}/{MAX_OPEN_POSITIONS} | "
                f"Capital: ${self.capital:.2f} | "
                f"PnL: ${self.total_pnl:+.4f} | "
                f"W/L: {self.wins}/{self.losses} ({wr}) | "
                f"{self.risk.summary()}"
            )

        # No abrir si estamos al límite
        if len(self.positions) >= MAX_OPEN_POSITIONS:
            return

        can, reason = self.risk.can_trade()
        if not can:
            if self.cycles % 12 == 0:
                log.warning(f"⛔ {reason}")
            return

        # Buscar señales
        # Evita abrir en símbolo que ya tiene posición (long O short)
        open_syms = {p.symbol for p in self.positions}
        for symbol in WATCHLIST:
            if symbol in open_syms:
                continue  # ya hay posición en este símbolo — no abrir la contraria
            if len(self.positions) >= MAX_OPEN_POSITIONS:
                break
            price = current_prices.get(symbol)
            if not price:
                continue
            _, vols = self.history.get(symbol)
            vol     = vols[-1] if vols else 0
            sig     = self.engine.analyze(symbol, price, vol)
            if sig:
                log.info(f"📡 {sig}")
                await self.open_position(sig)

    async def maybe_report(self):
        if time.time() - self._last_report >= 1800:
            self._last_report = time.time()
            total = self.wins + self.losses
            wr    = f"{self.wins/total*100:.1f}%" if total else "—"
            mode  = "🔵 DRY" if DRY_RUN else "🔴 LIVE"
            rep   = (
                f"📊 *Bot v5.2* {mode}\n"
                f"Capital   : `${self.capital:.4f}` USDT\n"
                f"PnL total : `${self.total_pnl:+.4f}` USDT\n"
                f"Trades    : {total} (✅{self.wins}/❌{self.losses}) {wr}\n"
                f"Posiciones: {len(self.positions)}/{MAX_OPEN_POSITIONS}\n"
                f"{self.risk.summary()}"
            )
            log.info(rep.replace("*","").replace("`",""))
            await self.tg.send(rep)

    async def run(self):
        log.info(
            f"\n{'═'*60}\n"
            f"  BINGX FUTURES BOT v5.1 | "
            f"{'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"  Capital/trade : ${TRADE_AMOUNT_USDT} USDT\n"
            f"  Leverage      : {LEVERAGE}x\n"
            f"  Exposición    : ${TRADE_AMOUNT_USDT * LEVERAGE} USDT/trade\n"
            f"  TP1           : +{TP1_PCT * LEVERAGE:.1f}% | TP2: +{TP2_PCT * LEVERAGE:.1f}%\n"
            f"  SL            : -{SL_PCT * LEVERAGE:.1f}%\n"
            f"  RSI umbrales  : {RSI_OVERSOLD}/{RSI_OVERBOUGHT}\n"
            f"  Señales min   : {MIN_SIGNALS}/5\n"
            f"  Watchlist     : {len(WATCHLIST)} pares\n"
            f"  Max pérd/día  : ${MAX_DAILY_LOSS_USDT}\n"
            f"  Telegram      : {'✅' if self.tg.enabled else '❌'}\n"
            f"{'═'*60}"
        )
        await self.tg.send(
            f"🤖 *Bot v5.2 iniciado* — BingX Futuros\n"
            f"Modo       : {'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"Leverage   : `{LEVERAGE}x`\n"
            f"Trade      : `${TRADE_AMOUNT_USDT}` → expuesto `${TRADE_AMOUNT_USDT*LEVERAGE}`\n"
            f"TP1/TP2/SL : `+{TP1_PCT*LEVERAGE:.1f}%` / `+{TP2_PCT*LEVERAGE:.1f}%` / `-{SL_PCT*LEVERAGE:.1f}%`\n"
            f"Pares      : `{len(WATCHLIST)}`\n"
            f"Calentando {50} velas..."
        )

        try:
            await self.ex.load_markets()
            log.info("✅ Mercados cargados")

            # Sincroniza posiciones reales existentes en BingX
            await self.sync_positions_with_exchange()

            # Calentamiento: descarga historial antes de operar
            log.info("⏳ Calentando indicadores (50 velas)...")
            for _ in range(3):
                await self.fetch_candles()
                await asyncio.sleep(5)
            log.info("✅ Indicadores listos — operando")

            while True:
                try:
                    await self.cycle()
                    await self.maybe_report()
                except Exception as e:
                    log.error(f"Cycle error: {e}", exc_info=True)
                await asyncio.sleep(LOOP_INTERVAL)

        except KeyboardInterrupt:
            log.info("Detenido.")
        finally:
            self._save()
            await self.tg.send(
                f"🛑 *Bot detenido*\n"
                f"PnL total: `${self.total_pnl:+.4f}` USDT\n"
                f"Trades: {self.wins+self.losses} (✅{self.wins}/❌{self.losses})"
            )
            await self.ex.close()


if __name__ == "__main__":
    asyncio.run(Bot().run())
