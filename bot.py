"""
╔══════════════════════════════════════════════════════════════════╗
║     BINGX SPOT ARBITRAGE BOT v3.0 — Railway Ready               ║
║  Aprendizaje Adaptativo | Capital Compuesto | Telegram | Riesgo  ║
╠══════════════════════════════════════════════════════════════════╣
║  NUEVAS FUNCIONES v3.0:                                          ║
║  • Aprende de errores: penaliza triángulos que fallan            ║
║  • Aprende de éxitos: prioriza triángulos ganadores              ║
║  • Capital compuesto: reinvierte ganancias automáticamente       ║
║  • Blacklist temporal: bloquea rutas con alta tasa de fallo      ║
║  • Score dinámico: rankea triángulos por historial real          ║
║  • Memoria persistente: sobrevive reinicios de Railway           ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import asyncio
import logging
import json
import time
from datetime import datetime
from itertools import permutations
from collections import deque, defaultdict
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
MIN_PROFIT_PCT      = float(os.getenv("MIN_PROFIT_PCT", "0.25"))
LOOP_INTERVAL       = int(os.getenv("LOOP_INTERVAL", "3"))
MAX_DAILY_LOSS_USDT = float(os.getenv("MAX_DAILY_LOSS_USDT", "50"))
FEE_RATE            = float(os.getenv("FEE_RATE", "0.001"))
LEVERAGE            = int(os.getenv("LEVERAGE", "1"))

# ─── COMPOUNDING ──────────────────────────────────────────────────
COMPOUND_RATE  = float(os.getenv("COMPOUND_RATE", "0.8"))   # 80% de ganancias se reinvierte
MAX_TRADE_USDT = float(os.getenv("MAX_TRADE_USDT", "500"))  # techo máximo por trade
MIN_TRADE_USDT = float(os.getenv("MIN_TRADE_USDT", "10"))   # suelo mínimo por trade

# ─── APRENDIZAJE ──────────────────────────────────────────────────
BLACKLIST_THRESHOLD = float(os.getenv("BLACKLIST_THRESHOLD", "0.4"))  # >40% fallo → blacklist
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
    """
    Gestiona el capital y lo hace crecer con las ganancias.

    Tras cada trade ganador:
      capital += ganancia × COMPOUND_RATE
    El próximo trade usa:
      trade_amount = capital × COMPOUND_RATE
    """

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
    """
    Aprende qué triángulos son confiables y cuáles fallan.
    Score = win_rate × avg_profit_real
    Los triángulos con >40% de fallo quedan bloqueados 30 minutos.
    """

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

    def _key(self, path: list) -> str:
        return "→".join(path)

    def _get(self, path: list) -> dict:
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

    def is_blacklisted(self, path: list) -> bool:
        d   = self._get(path)
        now = time.time()
        if d["blacklisted"] and now < d["blacklist_until"]:
            return True
        if d["blacklisted"]:
            d["blacklisted"]     = False
            d["blacklist_until"] = 0.0
            log.info(f"🔓 {self._key(path)} salió de blacklist")
        return False

    def on_win(self, path: list, profit_usdt: float):
        d = self._get(path)
        d["wins"]          += 1
        d["total_profit"]  += profit_usdt
        d["profits"].append(profit_usdt)
        if len(d["profits"]) > 50:
            d["profits"] = d["profits"][-50:]
        log.info(f"  🧠 WIN {self._key(path)} | ✅={d['wins']} ❌={d['losses']}")

    def on_loss(self, path: list):
        d     = self._get(path)
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
                    f"fallo {fail_rate*100:.0f}% | "
                    f"bloqueado {BLACKLIST_MINUTES}min"
                )

    def score(self, path: list) -> float:
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


# ─── ARBITRAGE ENGINE ────────────────────────────────────────────────────────
class ArbitrageEngine:
    def __init__(self, prices: dict, brain: TriangleBrain, trade_amt: float):
        self.prices    = prices
        self.brain     = brain
        self.trade_amt = trade_amt

    def _convert(self, amount: float, frm: str, to: str) -> float:
        direct   = f"{to}/{frm}"
        inverted = f"{frm}/{to}"
        if direct in self.prices and self.prices[direct]:
            return (amount / self.prices[direct]) * (1 - FEE_RATE)
        if inverted in self.prices and self.prices[inverted]:
            return (amount * self.prices[inverted]) * (1 - FEE_RATE)
        return 0.0

    def calc(self, a: str, b: str, c: str) -> float:
        amt = self.trade_amt
        for frm, to in [(a, b), (b, c), (c, a)]:
            amt = self._convert(amt, frm, to)
            if not amt:
                return 0.0
        return round(((amt - self.trade_amt) / self.trade_amt) * 100, 6)

    def scan(self) -> list:
        found = []
        for b, c in permutations(BASE_CURRENCIES, 2):
            path = [QUOTE_CURRENCY, b, c, QUOTE_CURRENCY]
            if self.brain.is_blacklisted(path):
                continue
            pct = self.calc(QUOTE_CURRENCY, b, c)
            if pct >= MIN_PROFIT_PCT:
                score = self.brain.score(path)
                found.append(Opportunity(path, pct, self.trade_amt, score))
        # Ordenar por score × profit_pct
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
            f"📊 *Bot v3.0* {mode} — {h}h{m:02d}m\n"
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
        self.prices      : dict = {}
        self.compounder   = Compounder()
        self.brain        = TriangleBrain()
        self.risk         = RiskManager()
        self.stats        = Stats()
        self.tg           = Telegram()
        self._last_report = time.time()
        self._last_save   = time.time()

    async def load_prices(self):
        try:
            tickers = await self.ex.fetch_tickers()
            self.prices = {}
            relevant = set(BASE_CURRENCIES) | {QUOTE_CURRENCY}
            for s, d in tickers.items():
                if "/" not in s or ":" in s:
                    continue
                base, quote = s.split("/")
                if base in relevant and quote in relevant:
                    if d.get("last") and d["last"] > 0:
                        self.prices[s] = d["last"]
            log.debug(f"Precios cargados: {len(self.prices)} pares | {list(self.prices.keys())[:10]}")
        except Exception as e:
            log.error(f"fetch_tickers error: {e}")

    async def execute(self, opp: Opportunity) -> bool:
        if DRY_RUN:
            await asyncio.sleep(0.02)
            return True
        try:
            path = opp.path
            ex   = self.ex

            # BingX Spot: símbolos sin sufijo, ej: "BTC/USDT"
            def sym(base, quote):
                return f"{base}/{quote}"

            # Paso 1: USDT → B  (comprar B con USDT)
            p1   = sym(path[1], path[0])
            qty1 = opp.trade_amt / self.prices[p1]
            o1   = await ex.create_market_buy_order(p1, qty1)

            # Paso 2: B → C
            p2_direct   = sym(path[2], path[1])   # C/B existe?
            p2_inverted = sym(path[1], path[2])   # B/C existe?
            filled1 = float(o1.get("filled", qty1))
            if p2_direct in self.prices:
                qty2 = filled1 / self.prices[p2_direct]
                o2   = await ex.create_market_buy_order(p2_direct, qty2)
                qty_c = float(o2.get("filled", qty2))
            else:
                o2    = await ex.create_market_sell_order(p2_inverted, filled1)
                qty_c = float(o2.get("cost", filled1 * self.prices.get(p2_inverted, 1)))

            # Paso 3: C → USDT
            p3 = sym(path[2], path[0])
            o3 = await ex.create_market_sell_order(p3, qty_c)

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
            f"\n{'═'*58}\n"
            f"  BINGX SPOT ARB BOT v3.0 | "
            f"{'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"  Capital actual  : ${self.compounder.capital:.4f} USDT\n"
            f"  Primer trade    : ${self.compounder.trade_amount:.4f} USDT\n"
            f"  Compound rate   : {COMPOUND_RATE*100:.0f}% de ganancias\n"
            f"  Cap máx/trade   : ${MAX_TRADE_USDT} USDT\n"
            f"  Profit mín      : {MIN_PROFIT_PCT}%\n"
            f"  Blacklist >fallo: {BLACKLIST_THRESHOLD*100:.0f}% → {BLACKLIST_MINUTES}min\n"
            f"  Leverage        : {LEVERAGE}x\n"
            f"  Max loss/día    : ${MAX_DAILY_LOSS_USDT} USDT\n"
            f"  Telegram        : {'✅' if self.tg.enabled else '❌'}\n"
            f"{'═'*58}"
        )

        await self.tg.send(
            f"🤖 *Bot v3.0 iniciado* — BingX Spot\n"
            f"Modo      : {'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"Capital   : `${self.compounder.capital:.4f}` USDT\n"
            f"Próx trade: `${self.compounder.trade_amount:.4f}` USDT\n"
            f"Compound  : `{COMPOUND_RATE*100:.0f}%` se reinvierte\n"
            f"Aprendizaje: ✅ activo"
        )

        try:
            await self.ex.load_markets()
            log.info("✅ Mercados cargados — escaneando...")

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
