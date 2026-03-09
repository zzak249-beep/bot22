"""
╔══════════════════════════════════════════════════════════════════╗
║     BINGX SPOT ARBITRAGE BOT v3.1 — Railway Ready               ║
║  Aprendizaje Adaptativo | Capital Compuesto | Telegram | Riesgo  ║
╠══════════════════════════════════════════════════════════════════╣
║  FIX v3.1 — BUGS CORREGIDOS:                                     ║
║  • _convert() tenía lógica invertida en pares cruzados           ║
║  • Ahora carga TODOS los pares del exchange (no solo /USDT)      ║
║  • Motor triangular correcto: USDT→A→B→USDT con pares reales    ║
║  • Diagnóstico al inicio: muestra pares y triángulos posibles    ║
║  • MIN_PROFIT_PCT ajustado: 0.15% (antes 0.05%, imposible)       ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import asyncio
import logging
import json
import time
from datetime import datetime
from itertools import permutations
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
MIN_PROFIT_PCT      = float(os.getenv("MIN_PROFIT_PCT", "0.15"))   # FIX: era 0.05 — imposible con fees 0.3%
LOOP_INTERVAL       = int(os.getenv("LOOP_INTERVAL", "3"))
MAX_DAILY_LOSS_USDT = float(os.getenv("MAX_DAILY_LOSS_USDT", "50"))
FEE_RATE            = float(os.getenv("FEE_RATE", "0.001"))         # 0.1% × 3 legs = 0.3% total mínimo

# ─── COMPOUNDING ──────────────────────────────────────────────────
COMPOUND_RATE  = float(os.getenv("COMPOUND_RATE", "0.8"))
MAX_TRADE_USDT = float(os.getenv("MAX_TRADE_USDT", "500"))
MIN_TRADE_USDT = float(os.getenv("MIN_TRADE_USDT", "10"))

# ─── APRENDIZAJE ──────────────────────────────────────────────────
BLACKLIST_THRESHOLD = float(os.getenv("BLACKLIST_THRESHOLD", "0.4"))
BLACKLIST_MINUTES   = int(os.getenv("BLACKLIST_MINUTES", "30"))
MIN_TRADES_TO_SCORE = int(os.getenv("MIN_TRADES_TO_SCORE", "3"))

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_CURRENCIES = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "MATIC"]
QUOTE_CURRENCY  = "USDT"
MEMORY_FILE     = "memory.json"


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
                    "chat_id"             : TELEGRAM_CHAT_ID,
                    "text"                : msg,
                    "parse_mode"          : "Markdown",
                    "disable_notification": silent,
                }, timeout=aiohttp.ClientTimeout(total=5))
        except Exception as e:
            log.warning(f"Telegram error: {e}")


# ─── CAPITAL COMPOUNDER ───────────────────────────────────────────────────────
class Compounder:
    def __init__(self):
        self.capital      = INITIAL_CAPITAL
        self.total_profit = 0.0
        self.peak_capital = INITIAL_CAPITAL
        self._load()

    def _load(self):
        try:
            with open(MEMORY_FILE) as f:
                data = json.load(f)
            self.capital      = float(data.get("capital", INITIAL_CAPITAL))
            self.total_profit = float(data.get("total_profit", 0.0))
            self.peak_capital = float(data.get("peak_capital", INITIAL_CAPITAL))
            log.info(f"💾 Capital recuperado: ${self.capital:.4f} USDT")
        except Exception:
            log.info(f"💾 Capital inicial: ${self.capital:.4f} USDT")

    def save(self, extra: dict = None):
        data = {
            "capital"      : round(self.capital, 6),
            "total_profit" : round(self.total_profit, 6),
            "peak_capital" : round(self.peak_capital, 6),
            "updated"      : datetime.now().isoformat(),
        }
        if extra:
            data.update(extra)
        try:
            with open(MEMORY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning(f"Memory save error: {e}")

    @property
    def trade_amount(self) -> float:
        amt = self.capital * COMPOUND_RATE
        return round(max(MIN_TRADE_USDT, min(MAX_TRADE_USDT, amt)), 4)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_capital <= 0:
            return 0.0
        return ((self.peak_capital - self.capital) / self.peak_capital) * 100

    def on_win(self, profit_usdt: float):
        reinvest           = profit_usdt * COMPOUND_RATE
        self.capital      += reinvest
        self.total_profit += profit_usdt
        if self.capital > self.peak_capital:
            self.peak_capital = self.capital
        log.info(
            f"  💰 +${reinvest:.4f} reinvertido | "
            f"Capital: ${self.capital:.4f} | "
            f"Profit total: ${self.total_profit:.4f}"
        )

    def on_loss(self, loss_usdt: float):
        self.capital = max(MIN_TRADE_USDT, self.capital - loss_usdt)
        log.info(f"  📉 Capital: ${self.capital:.4f}")

    def summary(self) -> str:
        return (
            f"Capital : `${self.capital:.4f}` USDT\n"
            f"Trade   : `${self.trade_amount:.4f}` USDT\n"
            f"Profit  : `${self.total_profit:+.4f}` USDT\n"
            f"Drawdown: `{self.drawdown_pct:.2f}%`"
        )


# ─── TRIANGLE BRAIN ───────────────────────────────────────────────────────────
class TriangleBrain:
    def __init__(self):
        self.data: dict = {}
        self._load()

    def _default(self):
        return {
            "wins": 0, "losses": 0,
            "total_profit": 0.0,
            "blacklisted": False,
            "blacklist_until": 0.0,
            "profits": [],
        }

    def _key(self, path) -> str:
        if isinstance(path, list):
            return "→".join(path)
        return str(path)

    def _get(self, path) -> dict:
        k = self._key(path)
        if k not in self.data:
            self.data[k] = self._default()
        return self.data[k]

    def _load(self):
        try:
            with open(MEMORY_FILE) as f:
                saved = json.load(f)
            for k, v in saved.get("brain", {}).items():
                d = self._default()
                d.update(v)
                self.data[k] = d
            count = sum(1 for v in self.data.values() if v["wins"] + v["losses"] > 0)
            log.info(f"🧠 Memoria: {count} triángulos con historial")
        except Exception:
            pass

    def dump(self) -> dict:
        return {k: dict(v) for k, v in self.data.items()}

    def is_blacklisted(self, path) -> bool:
        d   = self._get(path)
        now = time.time()
        if d["blacklisted"] and now < d["blacklist_until"]:
            return True
        if d["blacklisted"]:
            d["blacklisted"]     = False
            d["blacklist_until"] = 0.0
            log.info(f"🔓 {self._key(path)} salió de blacklist")
        return False

    def on_win(self, path, profit_usdt: float):
        d = self._get(path)
        d["wins"]         += 1
        d["total_profit"] += profit_usdt
        d["profits"].append(profit_usdt)
        if len(d["profits"]) > 50:
            d["profits"] = d["profits"][-50:]
        log.info(f"  🧠 WIN {self._key(path)} | ✅={d['wins']} ❌={d['losses']}")

    def on_loss(self, path):
        d = self._get(path)
        d["losses"] += 1
        d["profits"].append(0.0)
        if len(d["profits"]) > 50:
            d["profits"] = d["profits"][-50:]
        total = d["wins"] + d["losses"]
        log.info(f"  🧠 LOSS {self._key(path)} | ✅={d['wins']} ❌={d['losses']}")
        if total >= MIN_TRADES_TO_SCORE:
            fail_rate = d["losses"] / total
            if fail_rate >= BLACKLIST_THRESHOLD:
                d["blacklisted"]     = True
                d["blacklist_until"] = time.time() + BLACKLIST_MINUTES * 60
                log.warning(
                    f"  🚫 BLACKLIST {self._key(path)} | "
                    f"fallo {fail_rate*100:.0f}% | bloqueado {BLACKLIST_MINUTES}min"
                )

    def score(self, path) -> float:
        d     = self._get(path)
        total = d["wins"] + d["losses"]
        if total < MIN_TRADES_TO_SCORE:
            return 1.0
        win_rate   = d["wins"] / total
        avg_profit = sum(d["profits"]) / len(d["profits"]) if d["profits"] else 0.0
        return win_rate * max(avg_profit, 0.001)

    def top_triangles(self, n: int = 3) -> list:
        scored = [
            (k, self.score(k.split("→")), v["wins"], v["losses"])
            for k, v in self.data.items()
            if v["wins"] + v["losses"] >= MIN_TRADES_TO_SCORE
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n]


# ─── OPPORTUNITY ─────────────────────────────────────────────────────────────
class Opportunity:
    def __init__(self, path: list, profit_pct: float, trade_amt: float, score: float = 1.0):
        self.path        = path
        self.profit_pct  = profit_pct
        self.trade_amt   = trade_amt
        self.profit_usdt = trade_amt * (profit_pct / 100)
        self.score       = score

    def __str__(self):
        return (
            f"{'→'.join(self.path)}  "
            f"{self.profit_pct:+.4f}%  "
            f"${self.profit_usdt:.4f}  "
            f"score={self.score:.3f}"
        )


# ─── ARBITRAGE ENGINE (CORREGIDO) ────────────────────────────────────────────
class ArbitrageEngine:
    """
    Motor de arbitraje triangular CORREGIDO.

    BUG ORIGINAL:
      load_prices() solo guardaba pares /USDT.
      _convert() buscaba pares cruzados (ETH/BTC, BNB/ETH...) → siempre 0.0
      → Nunca había oportunidades.

    SOLUCIÓN v3.1:
      1. load_prices() carga TODOS los pares del exchange.
      2. _step() busca el par en ambas direcciones:
         USDT→BTC: busca "BTC/USDT" → qty = usdt / price  ✅
         BTC→ETH:  busca "ETH/BTC"  → qty = btc / price  ✅
                   o "BTC/ETH"      → qty = btc * price  ✅
         ETH→USDT: busca "ETH/USDT" → usdt = eth * price ✅
    """

    def __init__(self, prices: dict, brain: TriangleBrain, trade_amt: float):
        self.prices    = prices
        self.brain     = brain
        self.trade_amt = trade_amt

    def _step(self, amount: float, frm: str, to: str) -> float:
        """
        Convierte `amount` de moneda `frm` a moneda `to` aplicando fee.

        Caso A — par TO/FRM existe (ej: BTC/USDT para USDT→BTC):
          El precio cotiza TO en términos de FRM.
          qty_to = amount / price_TO_FRM

        Caso B — par FRM/TO existe (ej: USDT/BTC — muy raro):
          El precio cotiza FRM en términos de TO.
          qty_to = amount * price_FRM_TO
        """
        sym_a = f"{to}/{frm}"
        if sym_a in self.prices and self.prices[sym_a] > 0:
            return (amount / self.prices[sym_a]) * (1 - FEE_RATE)

        sym_b = f"{frm}/{to}"
        if sym_b in self.prices and self.prices[sym_b] > 0:
            return (amount * self.prices[sym_b]) * (1 - FEE_RATE)

        return 0.0

    def _calc_triangle(self, a: str, b: str, c: str) -> float:
        """Calcula profit% del triángulo a→b→c→a."""
        amt = self.trade_amt
        amt = self._step(amt, a, b)
        if amt <= 0:
            return 0.0
        amt = self._step(amt, b, c)
        if amt <= 0:
            return 0.0
        amt = self._step(amt, c, a)
        if amt <= 0:
            return 0.0
        return round(((amt - self.trade_amt) / self.trade_amt) * 100, 6)

    def scan(self) -> list:
        found = []
        for b, c in permutations(BASE_CURRENCIES, 2):
            path = [QUOTE_CURRENCY, b, c, QUOTE_CURRENCY]
            if self.brain.is_blacklisted(path):
                continue
            pct = self._calc_triangle(QUOTE_CURRENCY, b, c)
            if pct >= MIN_PROFIT_PCT:
                score = self.brain.score(path)
                found.append(Opportunity(path, pct, self.trade_amt, score))
        found.sort(key=lambda x: x.score * x.profit_pct, reverse=True)
        return found


# ─── RISK MANAGER ────────────────────────────────────────────────────────────
class RiskManager:
    def __init__(self):
        self.daily_pnl    = 0.0
        self.trades_total = 0
        self.day_start    = datetime.now().date()
        self.paused       = False
        self.pause_reason = ""

    def _reset_if_new_day(self):
        if datetime.now().date() != self.day_start:
            log.info("🔄 Nuevo día — reseteando límites")
            self.daily_pnl    = 0.0
            self.trades_total = 0
            self.day_start    = datetime.now().date()
            self.paused       = False

    def register(self, pnl: float):
        self.daily_pnl    += pnl
        self.trades_total += 1

    def can_trade(self) -> tuple:
        self._reset_if_new_day()
        if self.paused:
            return False, self.pause_reason
        if self.daily_pnl <= -MAX_DAILY_LOSS_USDT:
            self.paused       = True
            self.pause_reason = f"Pérdida diaria máx ${MAX_DAILY_LOSS_USDT}"
            return False, self.pause_reason
        return True, ""

    def summary(self) -> str:
        return f"PnL hoy: ${self.daily_pnl:+.4f} | Trades: {self.trades_total}"


# ─── STATS ───────────────────────────────────────────────────────────────────
class Stats:
    def __init__(self):
        self.cycles  = 0
        self.ok      = 0
        self.fail    = 0
        self.start   = datetime.now()
        self.history : deque = deque(maxlen=100)

    def add_trade(self, opp: Opportunity, ok: bool):
        if ok:
            self.ok += 1
        else:
            self.fail += 1
        self.history.append({
            "t"    : datetime.now().strftime("%H:%M:%S"),
            "path" : "→".join(opp.path),
            "pct"  : opp.profit_pct,
            "usdt" : round(opp.profit_usdt, 6),
            "score": round(opp.score, 4),
            "ok"   : ok,
        })

    def report(self, compounder: Compounder, brain: TriangleBrain) -> str:
        e     = datetime.now() - self.start
        h, r  = divmod(int(e.total_seconds()), 3600)
        m, _  = divmod(r, 60)
        mode  = "🔵 DRY" if DRY_RUN else "🔴 LIVE"
        total = self.ok + self.fail
        wr    = f"{self.ok/total*100:.1f}%" if total > 0 else "—"

        top     = brain.top_triangles(3)
        top_str = "\n".join(
            f"    `{k}` ✅{w}/❌{l} score={s:.3f}"
            for k, s, w, l in top
        ) or "    (acumulando datos...)"

        return (
            f"📊 *Bot v3.1* {mode} — {h}h{m:02d}m\n"
            f"  ✅/❌    : {self.ok}/{self.fail} ({wr})\n"
            f"  {compounder.summary()}\n"
            f"  🏆 Mejores rutas:\n{top_str}"
        )


# ─── BOT ─────────────────────────────────────────────────────────────────────
class Bot:
    def __init__(self):
        self.ex = ccxt.bingx({
            "apiKey"         : API_KEY,
            "secret"         : API_SECRET,
            "enableRateLimit": True,
            "options"        : {"defaultType": "spot"},
        })
        self.prices       : dict = {}
        self.compounder   = Compounder()
        self.brain        = TriangleBrain()
        self.risk         = RiskManager()
        self.stats        = Stats()
        self.tg           = Telegram()
        self._last_report = time.time()
        self._last_save   = time.time()
        self._diag_done   = False

    async def load_prices(self):
        """
        FIX v3.1: Carga TODOS los tickers, no solo /USDT.
        Esto permite encontrar pares cruzados (ETH/BTC, BNB/BTC, etc.)
        que son necesarios para calcular los triángulos.
        """
        try:
            tickers = await self.ex.fetch_tickers()
            self.prices = {}
            for symbol, data in tickers.items():
                clean = symbol.split(":")[0]            # quita sufijo :USDT de futuros
                last  = data.get("last") or data.get("close")
                if last and float(last) > 0:
                    self.prices[clean] = float(last)

            # Diagnóstico solo la primera vez
            if not self._diag_done:
                self._diag_done = True
                usdt_p = sum(1 for s in self.prices if s.endswith("/USDT"))
                btc_p  = sum(1 for s in self.prices if s.endswith("/BTC"))
                eth_p  = sum(1 for s in self.prices if s.endswith("/ETH"))
                log.info(
                    f"📡 Precios: {len(self.prices)} pares totales | "
                    f"/USDT={usdt_p}  /BTC={btc_p}  /ETH={eth_p}"
                )
                engine = ArbitrageEngine(self.prices, self.brain, 20.0)
                viables = 0
                for b, c in permutations(BASE_CURRENCIES, 2):
                    if (engine._step(1.0, QUOTE_CURRENCY, b) > 0 and
                            engine._step(1.0, b, c) > 0 and
                            engine._step(1.0, c, QUOTE_CURRENCY) > 0):
                        viables += 1
                log.info(
                    f"🔺 Triángulos calculables: {viables} / "
                    f"{len(list(permutations(BASE_CURRENCIES, 2)))}"
                )
                if viables == 0:
                    log.warning(
                        "⚠️  CERO triángulos posibles. BingX Spot no tiene "
                        "suficientes pares cruzados entre las monedas configuradas. "
                        "Considera añadir DOT, LINK, UNI que sí tienen pares /BTC."
                    )
                    await self.tg.send(
                        "⚠️ *Advertencia*: Cero triángulos calculables.\n"
                        "BingX Spot no tiene pares cruzados suficientes.\n"
                        "Revisa BASE_CURRENCIES en bot.py."
                    )

        except Exception as e:
            log.error(f"fetch_tickers error: {e}")

    async def execute(self, opp: Opportunity) -> bool:
        if DRY_RUN:
            await asyncio.sleep(0.02)
            return True
        try:
            path = opp.path
            ex   = self.ex

            def find_sym(frm: str, to: str):
                """Retorna (symbol, 'buy'|'sell') para convertir frm→to."""
                sym_a = f"{to}/{frm}"
                if sym_a in self.prices:
                    return sym_a, "buy"
                sym_b = f"{frm}/{to}"
                if sym_b in self.prices:
                    return sym_b, "sell"
                return None, None

            # Leg 1: path[0] → path[1]  (USDT → A)
            sym1, side1 = find_sym(path[0], path[1])
            if not sym1:
                raise ValueError(f"No pair for {path[0]}→{path[1]}")
            qty1 = opp.trade_amt / self.prices[sym1]
            o1 = await (ex.create_market_buy_order(sym1, qty1) if side1 == "buy"
                        else ex.create_market_sell_order(sym1, opp.trade_amt))
            filled1 = float(o1.get("filled") or qty1)

            # Leg 2: path[1] → path[2]  (A → B)
            sym2, side2 = find_sym(path[1], path[2])
            if not sym2:
                raise ValueError(f"No pair for {path[1]}→{path[2]}")
            qty2 = filled1 / self.prices[sym2]
            o2 = await (ex.create_market_buy_order(sym2, qty2) if side2 == "buy"
                        else ex.create_market_sell_order(sym2, filled1))
            filled2 = float(o2.get("filled") or qty2)

            # Leg 3: path[2] → path[3]  (B → USDT)
            sym3, side3 = find_sym(path[2], path[3])
            if not sym3:
                raise ValueError(f"No pair for {path[2]}→{path[3]}")
            o3 = await (ex.create_market_sell_order(sym3, filled2) if side3 == "buy"
                        else ex.create_market_buy_order(sym3, filled2))

            log.info(f"  ✅ {o1['id']} | {o2['id']} | {o3['id']}")
            return True
        except Exception as e:
            log.error(f"  ❌ Execute error: {e}")
            return False

    def _save(self):
        self.compounder.save(extra={"brain": self.brain.dump()})

    async def cycle(self):
        self.stats.cycles += 1
        await self.load_prices()
        if not self.prices:
            return

        trade_amt = self.compounder.trade_amount
        opps      = ArbitrageEngine(self.prices, self.brain, trade_amt).scan()

        if not opps:
            if self.stats.cycles % 50 == 0:
                log.info(
                    f"Ciclo {self.stats.cycles} | Sin ops | "
                    f"Capital: ${self.compounder.capital:.2f} | "
                    f"{self.risk.summary()}"
                )
            return

        for opp in opps[:3]:
            log.info(f"🚀 {opp}")

            can, reason = self.risk.can_trade()
            if not can:
                log.warning(f"⛔ {reason}")
                break

            ok = await self.execute(opp)
            self.stats.add_trade(opp, ok)
            self.risk.register(opp.profit_usdt if ok else 0)

            if ok:
                self.compounder.on_win(opp.profit_usdt)
                self.brain.on_win(opp.path, opp.profit_usdt)
                await self.tg.send(
                    f"✅ *Trade ejecutado*\n"
                    f"`{'→'.join(opp.path)}`\n"
                    f"Profit  : `{opp.profit_pct:+.4f}%` (${opp.profit_usdt:.4f})\n"
                    f"Score   : `{opp.score:.3f}`\n"
                    f"Capital : `${self.compounder.capital:.4f}` USDT\n"
                    f"Próx    : `${self.compounder.trade_amount:.4f}` USDT\n"
                    f"{self.risk.summary()}"
                )
            else:
                self.brain.on_loss(opp.path)
                await self.tg.send(
                    f"❌ *Trade fallido* — aprendiendo\n"
                    f"`{'→'.join(opp.path)}`\n"
                    f"Esperado: `{opp.profit_pct:+.4f}%`\n"
                    f"⚠️ Ruta penalizada en memoria",
                    silent=True,
                )

        if time.time() - self._last_save >= 300:
            self._last_save = time.time()
            self._save()

    async def maybe_report(self):
        if time.time() - self._last_report >= 1800:
            self._last_report = time.time()
            rep = self.stats.report(self.compounder, self.brain)
            log.info(rep.replace("*", "").replace("`", ""))
            await self.tg.send(rep)
            self._save()

    async def run(self):
        log.info(
            f"\n{'═'*60}\n"
            f"  BINGX SPOT ARB BOT v3.1 | "
            f"{'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"  Capital actual  : ${self.compounder.capital:.4f} USDT\n"
            f"  Primer trade    : ${self.compounder.trade_amount:.4f} USDT\n"
            f"  Compound rate   : {COMPOUND_RATE*100:.0f}% de ganancias\n"
            f"  Cap máx/trade   : ${MAX_TRADE_USDT} USDT\n"
            f"  Profit mín      : {MIN_PROFIT_PCT}%\n"
            f"  Fee por orden   : {FEE_RATE*100:.2f}% (total 3 legs: {FEE_RATE*3*100:.2f}%)\n"
            f"  Blacklist >fallo: {BLACKLIST_THRESHOLD*100:.0f}% → {BLACKLIST_MINUTES}min\n"
            f"  Max loss/día    : ${MAX_DAILY_LOSS_USDT} USDT\n"
            f"  Telegram        : {'✅' if self.tg.enabled else '❌'}\n"
            f"{'═'*60}"
        )

        await self.tg.send(
            f"🤖 *Bot v3.1 iniciado* — BingX Spot\n"
            f"Modo      : {'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"Capital   : `${self.compounder.capital:.4f}` USDT\n"
            f"Próx trade: `${self.compounder.trade_amount:.4f}` USDT\n"
            f"Compound  : `{COMPOUND_RATE*100:.0f}%` se reinvierte\n"
            f"Fix v3.1  : motor triangular corregido ✅"
        )

        try:
            await self.ex.load_markets()
            log.info("✅ Mercados cargados — iniciando escaneo...")

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
            rep = self.stats.report(self.compounder, self.brain)
            log.info(rep.replace("*", "").replace("`", ""))
            await self.tg.send(f"🛑 *Bot detenido*\n{rep}")
            await self.ex.close()


if __name__ == "__main__":
    asyncio.run(Bot().run())
