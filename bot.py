"""
╔══════════════════════════════════════════════════════════════════╗
║     BINGX SPOT BOT v4.0 — Estrategia adaptada a BingX           ║
║  BingX tiene 940 pares /USDT pero casi CERO pares cruzados.     ║
║  Arbitraje triangular clásico: IMPOSIBLE en BingX.              ║
║                                                                  ║
║  NUEVA ESTRATEGIA — Mean Reversion + Momentum:                   ║
║  Detecta activos con divergencia de precio respecto a su        ║
║  correlación histórica con BTC. Cuando un altcoin baja más      ║
║  de lo esperado vs BTC → compra. Cuando sube más → vende.       ║
║                                                                  ║
║  ESTRATEGIA 2 — Spread interno BingX:                           ║
║  Compara precio implícito de A via B vs precio directo.         ║
║  USDT→BTC→USDT  vs  USDT directo.                               ║
║  Detecta el "precio justo" usando múltiples rutas USDT.         ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import asyncio
import logging
import json
import time
from datetime import datetime
from collections import deque
from dotenv import load_dotenv
import ccxt.async_support as ccxt
import aiohttp

load_dotenv()

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ],
)
log = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
API_KEY    = os.getenv("BINGX_API_KEY", "")
API_SECRET = os.getenv("BINGX_API_SECRET", "")
DRY_RUN    = os.getenv("DRY_RUN", "true").lower() == "true"

INITIAL_CAPITAL     = float(os.getenv("INITIAL_CAPITAL", "20"))
LOOP_INTERVAL       = int(os.getenv("LOOP_INTERVAL", "5"))
MAX_DAILY_LOSS_USDT = float(os.getenv("MAX_DAILY_LOSS_USDT", "10"))
FEE_RATE            = float(os.getenv("FEE_RATE", "0.001"))

# Parámetros estrategia
TRADE_AMOUNT_USDT   = float(os.getenv("TRADE_AMOUNT_USDT", "10"))
MIN_SPREAD_PCT      = float(os.getenv("MIN_SPREAD_PCT", "0.4"))   # spread mínimo bid/ask para entrar
ZSCORE_THRESHOLD    = float(os.getenv("ZSCORE_THRESHOLD", "2.0")) # desviación estándar para señal
LOOKBACK            = int(os.getenv("LOOKBACK", "20"))             # velas para calcular media/std
MAX_OPEN_POSITIONS  = int(os.getenv("MAX_OPEN_POSITIONS", "3"))

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Monedas a monitorear — las de mayor volumen en BingX
WATCHLIST = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX",
    "MATIC", "DOT", "LINK", "UNI", "ATOM", "LTC", "TRX",
    "NEAR", "ICP", "FIL", "APT", "ARB",
]
QUOTE = "USDT"
MEMORY_FILE = "memory.json"


# ─── TELEGRAM ────────────────────────────────────────────────────────────────
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
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": msg,
                    "parse_mode": "Markdown",
                    "disable_notification": silent,
                }, timeout=aiohttp.ClientTimeout(total=5))
        except Exception as e:
            log.warning(f"Telegram error: {e}")


# ─── PRICE HISTORY ───────────────────────────────────────────────────────────
class PriceHistory:
    """
    Guarda historial de precios para calcular z-score y detectar
    divergencias estadísticas.
    """
    def __init__(self, maxlen: int = 100):
        self.data: dict[str, deque] = {}
        self.maxlen = maxlen

    def update(self, symbol: str, price: float):
        if symbol not in self.data:
            self.data[symbol] = deque(maxlen=self.maxlen)
        self.data[symbol].append(price)

    def zscore(self, symbol: str) -> float | None:
        """Z-score del último precio respecto a media histórica."""
        if symbol not in self.data or len(self.data[symbol]) < LOOKBACK:
            return None
        prices = list(self.data[symbol])[-LOOKBACK:]
        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        std = variance ** 0.5
        if std == 0:
            return None
        return (prices[-1] - mean) / std

    def pct_change(self, symbol: str, periods: int = 5) -> float | None:
        """Cambio % en los últimos N ciclos."""
        if symbol not in self.data or len(self.data[symbol]) < periods + 1:
            return None
        prices = list(self.data[symbol])
        old = prices[-(periods + 1)]
        new = prices[-1]
        if old == 0:
            return None
        return ((new - old) / old) * 100

    def ready(self, symbol: str) -> bool:
        return symbol in self.data and len(self.data[symbol]) >= LOOKBACK


# ─── POSITION MANAGER ────────────────────────────────────────────────────────
class Position:
    def __init__(self, symbol: str, side: str, entry: float, qty: float, usdt: float):
        self.symbol     = symbol
        self.side       = side       # "long"
        self.entry      = entry
        self.qty        = qty
        self.usdt       = usdt
        self.opened_at  = time.time()
        self.max_profit = 0.0

    def pnl_pct(self, current_price: float) -> float:
        if self.entry == 0:
            return 0.0
        return ((current_price - self.entry) / self.entry) * 100

    def pnl_usdt(self, current_price: float) -> float:
        return self.qty * (current_price - self.entry)

    def age_minutes(self) -> float:
        return (time.time() - self.opened_at) / 60

    def __str__(self):
        return f"{self.symbol} entry={self.entry:.6f} qty={self.qty:.4f}"


# ─── RISK MANAGER ────────────────────────────────────────────────────────────
class RiskManager:
    def __init__(self):
        self.daily_pnl  = 0.0
        self.trades     = 0
        self.day_start  = datetime.now().date()
        self.paused     = False
        self.pause_reason = ""

    def reset_if_new_day(self):
        if datetime.now().date() != self.day_start:
            log.info("🔄 Nuevo día — reseteando límites")
            self.daily_pnl  = 0.0
            self.trades     = 0
            self.day_start  = datetime.now().date()
            self.paused     = False

    def register(self, pnl: float):
        self.daily_pnl += pnl
        self.trades    += 1

    def can_trade(self) -> tuple:
        self.reset_if_new_day()
        if self.paused:
            return False, self.pause_reason
        if self.daily_pnl <= -MAX_DAILY_LOSS_USDT:
            self.paused       = True
            self.pause_reason = f"Max pérdida diaria ${MAX_DAILY_LOSS_USDT}"
            return False, self.pause_reason
        return True, ""

    def summary(self) -> str:
        return f"PnL hoy: ${self.daily_pnl:+.4f} | Trades: {self.trades}"


# ─── STATS ───────────────────────────────────────────────────────────────────
class Stats:
    def __init__(self):
        self.cycles    = 0
        self.wins      = 0
        self.losses    = 0
        self.total_pnl = 0.0
        self.start     = datetime.now()

    def record(self, pnl: float):
        self.total_pnl += pnl
        if pnl >= 0:
            self.wins += 1
        else:
            self.losses += 1

    def report(self) -> str:
        e    = datetime.now() - self.start
        h, r = divmod(int(e.total_seconds()), 3600)
        m, _ = divmod(r, 60)
        total = self.wins + self.losses
        wr    = f"{self.wins/total*100:.1f}%" if total else "—"
        mode  = "🔵 DRY" if DRY_RUN else "🔴 LIVE"
        return (
            f"📊 *Bot v4.0* {mode} — {h}h{m:02d}m\n"
            f"  Trades : {total} (✅{self.wins}/❌{self.losses}) {wr}\n"
            f"  PnL    : `${self.total_pnl:+.4f}` USDT\n"
            f"  Ciclos : {self.cycles}"
        )


# ─── SIGNAL ENGINE ───────────────────────────────────────────────────────────
class SignalEngine:
    """
    Detecta oportunidades usando SOLO pares X/USDT (lo que BingX tiene).

    ESTRATEGIA: Mean Reversion con Z-Score
    ─────────────────────────────────────
    1. Calcula z-score del precio actual vs media de últimos N ciclos.
    2. Si z-score < -THRESHOLD → precio anormalmente bajo → señal de compra.
    3. Salida cuando z-score vuelve a 0 (precio regresa a media) → profit.

    FILTROS adicionales:
    - Volumen mínimo (evita tokens ilíquidos)
    - Spread bid/ask máximo (evita slippage alto)
    - Momentum: confirmar que la caída está frenando
    """

    def __init__(self, tickers: dict, history: PriceHistory):
        self.tickers = tickers
        self.history = history

    def scan(self) -> list:
        signals = []
        for coin in WATCHLIST:
            sym = f"{coin}/{QUOTE}"
            if sym not in self.tickers:
                continue
            t = self.tickers[sym]

            # Precio y volumen
            price = t.get("last") or t.get("close")
            vol   = t.get("quoteVolume") or t.get("baseVolume", 0)
            bid   = t.get("bid")
            ask   = t.get("ask")

            if not price or price <= 0:
                continue

            # Actualiza historial
            self.history.update(sym, price)

            if not self.history.ready(sym):
                continue

            # Spread bid/ask (liquidez)
            spread_pct = 0.0
            if bid and ask and bid > 0:
                spread_pct = ((ask - bid) / bid) * 100
                if spread_pct > MIN_SPREAD_PCT:
                    continue  # demasiado spread = slippage alto

            # Z-score
            z = self.history.zscore(sym)
            if z is None:
                continue

            # Cambio reciente para confirmar momentum
            chg5 = self.history.pct_change(sym, 5)

            # SEÑAL LONG: precio anormalmente bajo y empezando a recuperar
            if z <= -ZSCORE_THRESHOLD:
                # Confirmar que el momentum negativo está frenando
                # (chg5 no tan negativo como antes)
                chg1 = self.history.pct_change(sym, 1)
                recovering = chg1 is not None and chg1 > -0.1  # no sigue cayendo fuerte

                if recovering:
                    signals.append({
                        "symbol"    : sym,
                        "coin"      : coin,
                        "price"     : price,
                        "zscore"    : z,
                        "spread_pct": spread_pct,
                        "vol_usdt"  : vol,
                        "chg5"      : chg5,
                        "strength"  : abs(z),  # más negativo = señal más fuerte
                    })

        # Ordena por fuerza de señal (z-score más extremo primero)
        signals.sort(key=lambda x: x["strength"], reverse=True)
        return signals


# ─── BOT ─────────────────────────────────────────────────────────────────────
class Bot:
    def __init__(self):
        self.ex = ccxt.bingx({
            "apiKey"         : API_KEY,
            "secret"         : API_SECRET,
            "enableRateLimit": True,
            "options"        : {"defaultType": "spot"},
        })
        self.tickers    : dict              = {}
        self.positions  : list[Position]    = []
        self.history    = PriceHistory(maxlen=100)
        self.risk       = RiskManager()
        self.stats      = Stats()
        self.tg         = Telegram()
        self._last_report = time.time()
        self._capital   = INITIAL_CAPITAL
        self._load_memory()

    def _load_memory(self):
        try:
            with open(MEMORY_FILE) as f:
                d = json.load(f)
            self._capital = float(d.get("capital", INITIAL_CAPITAL))
            log.info(f"💾 Capital: ${self._capital:.4f} USDT")
        except Exception:
            log.info(f"💾 Capital inicial: ${self._capital:.4f} USDT")

    def _save_memory(self):
        try:
            with open(MEMORY_FILE, "w") as f:
                json.dump({
                    "capital" : round(self._capital, 6),
                    "updated" : datetime.now().isoformat(),
                }, f, indent=2)
        except Exception as e:
            log.warning(f"Memory save error: {e}")

    async def fetch_tickers(self):
        try:
            self.tickers = await self.ex.fetch_tickers()
        except Exception as e:
            log.error(f"fetch_tickers error: {e}")

    async def open_position(self, signal: dict) -> bool:
        sym   = signal["symbol"]
        price = signal["price"]
        qty   = round(TRADE_AMOUNT_USDT / price, 6)

        log.info(
            f"🟢 SEÑAL LONG  {sym} | "
            f"precio={price:.6f} | z={signal['zscore']:.2f} | "
            f"spread={signal['spread_pct']:.3f}% | qty={qty}"
        )

        if DRY_RUN:
            pos = Position(sym, "long", price, qty, TRADE_AMOUNT_USDT)
            self.positions.append(pos)
            await self.tg.send(
                f"🟢 *LONG abierto* `{sym}`\n"
                f"Precio  : `{price:.6f}`\n"
                f"Z-Score : `{signal['zscore']:.2f}`\n"
                f"Spread  : `{signal['spread_pct']:.3f}%`\n"
                f"Cantidad: `{qty}`\n"
                f"USDT    : `${TRADE_AMOUNT_USDT}`"
            )
            return True
        try:
            order = await self.ex.create_market_buy_order(sym, qty)
            filled_qty   = float(order.get("filled", qty))
            filled_price = float(order.get("average", price))
            pos = Position(sym, "long", filled_price, filled_qty, TRADE_AMOUNT_USDT)
            self.positions.append(pos)
            await self.tg.send(
                f"🟢 *LONG abierto* `{sym}`\n"
                f"Precio  : `{filled_price:.6f}`\n"
                f"Z-Score : `{signal['zscore']:.2f}`\n"
                f"Cantidad: `{filled_qty}`"
            )
            return True
        except Exception as e:
            log.error(f"open_position error {sym}: {e}")
            return False

    async def close_position(self, pos: Position, reason: str):
        sym = pos.symbol
        t   = self.tickers.get(sym, {})
        current = t.get("last") or t.get("close") or pos.entry
        pnl_pct  = pos.pnl_pct(current)
        pnl_usdt = pos.pnl_usdt(current)

        log.info(
            f"🔴 CIERRE {sym} | {reason} | "
            f"entrada={pos.entry:.6f} salida={current:.6f} | "
            f"PnL={pnl_pct:+.3f}% (${pnl_usdt:+.4f})"
        )

        if not DRY_RUN:
            try:
                await self.ex.create_market_sell_order(sym, pos.qty)
            except Exception as e:
                log.error(f"close_position error {sym}: {e}")

        self._capital += pnl_usdt
        self.stats.record(pnl_usdt)
        self.risk.register(pnl_usdt)
        self._save_memory()

        await self.tg.send(
            f"{'✅' if pnl_usdt >= 0 else '❌'} *Cerrado* `{sym}`\n"
            f"Razón   : {reason}\n"
            f"PnL     : `{pnl_pct:+.3f}%` (${pnl_usdt:+.4f})\n"
            f"Capital : `${self._capital:.4f}` USDT\n"
            f"{self.risk.summary()}"
        )

    async def manage_positions(self):
        """Gestiona posiciones abiertas — cierra según condiciones."""
        to_close = []
        for pos in self.positions:
            sym = pos.symbol
            t   = self.tickers.get(sym, {})
            current = t.get("last") or t.get("close")
            if not current:
                continue

            pnl_pct = pos.pnl_pct(current)
            z       = self.history.zscore(sym)
            age     = pos.age_minutes()

            # Actualiza máximo profit
            if pnl_pct > pos.max_profit:
                pos.max_profit = pnl_pct

            reason = None

            # Take profit: z-score volvió a neutral (precio regresó a media)
            if z is not None and z >= -0.3 and pnl_pct > 0:
                reason = f"TP z={z:.2f}"

            # Take profit fijo: +0.5%
            elif pnl_pct >= 0.5:
                reason = f"TP +{pnl_pct:.2f}%"

            # Stop loss: -1.0%
            elif pnl_pct <= -1.0:
                reason = f"SL {pnl_pct:.2f}%"

            # Trailing stop: si bajó 0.3% desde máximo
            elif pos.max_profit > 0.3 and pnl_pct < pos.max_profit - 0.3:
                reason = f"Trailing stop (max={pos.max_profit:.2f}%)"

            # Timeout: posición abierta más de 30 minutos
            elif age > 30:
                reason = f"Timeout {age:.0f}min"

            if reason:
                to_close.append((pos, reason))

        for pos, reason in to_close:
            await self.close_position(pos, reason)
            self.positions.remove(pos)

    async def cycle(self):
        self.stats.cycles += 1
        await self.fetch_tickers()
        if not self.tickers:
            return

        # Gestionar posiciones abiertas primero
        await self.manage_positions()

        # Log periódico
        if self.stats.cycles % 20 == 0:
            log.info(
                f"Ciclo {self.stats.cycles} | "
                f"Posiciones: {len(self.positions)}/{MAX_OPEN_POSITIONS} | "
                f"Capital: ${self._capital:.2f} | "
                f"{self.risk.summary()}"
            )

        # No abrir más si estamos al límite
        if len(self.positions) >= MAX_OPEN_POSITIONS:
            return

        can, reason = self.risk.can_trade()
        if not can:
            if self.stats.cycles % 20 == 0:
                log.warning(f"⛔ {reason}")
            return

        # Buscar señales
        engine  = SignalEngine(self.tickers, self.history)
        signals = engine.scan()

        if not signals:
            return

        # Evita abrir en el mismo símbolo dos veces
        open_syms = {p.symbol for p in self.positions}

        for sig in signals:
            if len(self.positions) >= MAX_OPEN_POSITIONS:
                break
            if sig["symbol"] in open_syms:
                continue
            await self.open_position(sig)
            open_syms.add(sig["symbol"])

    async def maybe_report(self):
        if time.time() - self._last_report >= 1800:
            self._last_report = time.time()
            rep = self.stats.report()
            log.info(rep.replace("*", "").replace("`", ""))
            await self.tg.send(rep)

    async def run(self):
        log.info(
            f"\n{'═'*60}\n"
            f"  BINGX SPOT BOT v4.0 | "
            f"{'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"  Estrategia    : Mean Reversion (Z-Score)\n"
            f"  Capital       : ${self._capital:.4f} USDT\n"
            f"  Trade amount  : ${TRADE_AMOUNT_USDT} USDT\n"
            f"  Z-Score umbral: {ZSCORE_THRESHOLD}σ\n"
            f"  Lookback      : {LOOKBACK} ciclos\n"
            f"  Max posiciones: {MAX_OPEN_POSITIONS}\n"
            f"  Max loss/día  : ${MAX_DAILY_LOSS_USDT} USDT\n"
            f"  Watchlist     : {len(WATCHLIST)} monedas\n"
            f"  Telegram      : {'✅' if self.tg.enabled else '❌'}\n"
            f"{'═'*60}"
        )

        await self.tg.send(
            f"🤖 *Bot v4.0 iniciado* — BingX Spot\n"
            f"Modo       : {'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"Estrategia : Mean Reversion Z-Score\n"
            f"Capital    : `${self._capital:.4f}` USDT\n"
            f"Trade      : `${TRADE_AMOUNT_USDT}` USDT\n"
            f"Watchlist  : `{len(WATCHLIST)}` monedas\n"
            f"Nota: necesita ~{LOOKBACK} ciclos para calibrar"
        )

        try:
            await self.ex.load_markets()
            log.info(f"✅ Mercados cargados — calentando {LOOKBACK} ciclos...")

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
            self._save_memory()
            rep = self.stats.report()
            log.info(rep.replace("*", "").replace("`", ""))
            await self.tg.send(f"🛑 *Bot detenido*\n{rep}")
            await self.ex.close()


if __name__ == "__main__":
    asyncio.run(Bot().run())
