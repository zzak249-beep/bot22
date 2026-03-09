"""
╔══════════════════════════════════════════════════════════════╗
║        BINANCE ARBITRAGE BOT v2.0 — Railway Ready           ║
║  Triangular + Dynamic Pairs | Telegram | Risk Mgmt | Stats   ║
╚══════════════════════════════════════════════════════════════╝
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
API_KEY    = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
DRY_RUN    = os.getenv("DRY_RUN", "true").lower() == "true"

MIN_PROFIT_PCT      = float(os.getenv("MIN_PROFIT_PCT", "0.25"))
TRADE_AMOUNT_USDT   = float(os.getenv("TRADE_AMOUNT", "20"))
LOOP_INTERVAL       = int(os.getenv("LOOP_INTERVAL", "3"))
MAX_DAILY_LOSS_USDT = float(os.getenv("MAX_DAILY_LOSS_USDT", "50"))
FEE_RATE            = float(os.getenv("FEE_RATE", "0.001"))

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_CURRENCIES = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "MATIC"]
QUOTE_CURRENCY  = "USDT"


# ─── TELEGRAM ────────────────────────────────────────────────────────────────
class Telegram:
    def __init__(self):
        self.enabled = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

    async def send(self, msg: str):
        if not self.enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": msg,
                    "parse_mode": "Markdown",
                }, timeout=aiohttp.ClientTimeout(total=5))
        except Exception as e:
            log.warning(f"Telegram error: {e}")


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
            self.pause_reason = f"Pérdida diaria máx ${MAX_DAILY_LOSS_USDT} alcanzada"
            return False, self.pause_reason
        return True, ""

    def summary(self) -> str:
        return (
            f"PnL hoy: ${self.daily_pnl:+.4f} | "
            f"Trades hoy: {self.trades_total}"
        )


# ─── OPPORTUNITY ─────────────────────────────────────────────────────────────
class Opportunity:
    def __init__(self, path: list, profit_pct: float):
        self.path        = path
        self.profit_pct  = profit_pct
        self.profit_usdt = TRADE_AMOUNT_USDT * (profit_pct / 100)

    def __str__(self):
        return (
            f"{'→'.join(self.path)}  "
            f"{self.profit_pct:+.4f}%  "
            f"(${self.profit_usdt:.4f})"
        )


# ─── ARBITRAGE ENGINE ────────────────────────────────────────────────────────
class ArbitrageEngine:
    def __init__(self, prices: dict):
        self.prices = prices

    def _convert(self, amount: float, frm: str, to: str) -> float:
        direct   = f"{to}/{frm}"
        inverted = f"{frm}/{to}"
        if direct in self.prices and self.prices[direct]:
            return (amount / self.prices[direct]) * (1 - FEE_RATE)
        if inverted in self.prices and self.prices[inverted]:
            return (amount * self.prices[inverted]) * (1 - FEE_RATE)
        return 0.0

    def calc(self, a: str, b: str, c: str) -> float:
        amt = TRADE_AMOUNT_USDT
        amt = self._convert(amt, a, b)
        if not amt:
            return 0.0
        amt = self._convert(amt, b, c)
        if not amt:
            return 0.0
        amt = self._convert(amt, c, a)
        if not amt:
            return 0.0
        return round(((amt - TRADE_AMOUNT_USDT) / TRADE_AMOUNT_USDT) * 100, 6)

    def scan(self) -> list:
        found = []
        for b, c in permutations(BASE_CURRENCIES, 2):
            pct = self.calc(QUOTE_CURRENCY, b, c)
            if pct >= MIN_PROFIT_PCT:
                found.append(Opportunity([QUOTE_CURRENCY, b, c, QUOTE_CURRENCY], pct))
        found.sort(key=lambda x: x.profit_pct, reverse=True)
        return found


# ─── STATS ───────────────────────────────────────────────────────────────────
class Stats:
    def __init__(self):
        self.cycles  = 0
        self.seen    = 0
        self.ok      = 0
        self.fail    = 0
        self.pnl     = 0.0
        self.best    = None
        self.start   = datetime.now()
        self.history : deque = deque(maxlen=100)

    def add_opp(self, opp: Opportunity):
        self.seen += 1
        if not self.best or opp.profit_pct > self.best.profit_pct:
            self.best = opp

    def add_trade(self, opp: Opportunity, ok: bool):
        if ok:
            self.ok  += 1
            self.pnl += opp.profit_usdt
        else:
            self.fail += 1
        self.history.append({
            "t"    : datetime.now().strftime("%H:%M:%S"),
            "path" : "→".join(opp.path),
            "pct"  : opp.profit_pct,
            "usdt" : round(opp.profit_usdt, 6),
            "ok"   : ok,
        })

    def report(self) -> str:
        e = datetime.now() - self.start
        h, r = divmod(int(e.total_seconds()), 3600)
        m, _ = divmod(r, 60)
        b    = f"{self.best.profit_pct:.4f}%" if self.best else "—"
        mode = "🔵 DRY" if DRY_RUN else "🔴 LIVE"
        return (
            f"📊 *Stats* {mode} — {h}h{m:02d}m\n"
            f"  Ciclos : {self.cycles}\n"
            f"  Ops    : {self.seen}\n"
            f"  ✅/❌  : {self.ok}/{self.fail}\n"
            f"  PnL    : ${self.pnl:+.4f} USDT\n"
            f"  Mejor  : {b}"
        )

    def save(self):
        try:
            with open("stats.json", "w") as f:
                json.dump({
                    "updated": datetime.now().isoformat(),
                    "cycles" : self.cycles,
                    "seen"   : self.seen,
                    "ok"     : self.ok,
                    "fail"   : self.fail,
                    "pnl"    : self.pnl,
                    "history": list(self.history),
                }, f, indent=2)
        except Exception as e:
            log.warning(f"Stats save error: {e}")


# ─── BOT ─────────────────────────────────────────────────────────────────────
class Bot:
    def __init__(self):
        self.ex = ccxt.binance({
            "apiKey"         : API_KEY,
            "secret"         : API_SECRET,
            "enableRateLimit": True,
            "options"        : {"defaultType": "spot"},
        })
        self.prices       : dict = {}
        self.stats         = Stats()
        self.risk          = RiskManager()
        self.tg            = Telegram()
        self._last_report  = time.time()

    # ── Carga de precios ─────────────────────────────────────
    async def load_prices(self):
        try:
            tickers    = await self.ex.fetch_tickers()
            self.prices = {
                s: d["last"]
                for s, d in tickers.items()
                if d.get("last") and d["last"] > 0
            }
        except Exception as e:
            log.error(f"fetch_tickers error: {e}")

    # ── Ejecutar triángulo ───────────────────────────────────
    async def execute(self, opp: Opportunity) -> bool:
        if DRY_RUN:
            await asyncio.sleep(0.02)
            return True
        try:
            path = opp.path
            ex   = self.ex
            amt  = TRADE_AMOUNT_USDT

            # Orden 1: USDT → B
            p1 = f"{path[1]}/{path[0]}"
            o1 = await ex.create_market_buy_order(p1, amt / self.prices[p1])

            # Orden 2: B → C
            p2 = f"{path[2]}/{path[1]}"
            o2 = await ex.create_market_buy_order(p2, o1["filled"] / self.prices[p2])

            # Orden 3: C → USDT
            p3 = f"{path[2]}/{path[0]}"
            o3 = await ex.create_market_sell_order(p3, o2["filled"])

            log.info(f"  ✅ {o1['id']} | {o2['id']} | {o3['id']}")
            return True

        except Exception as e:
            log.error(f"  ❌ Execute error: {e}")
            return False

    # ── Ciclo principal ──────────────────────────────────────
    async def cycle(self):
        self.stats.cycles += 1
        await self.load_prices()
        if not self.prices:
            return

        opps = ArbitrageEngine(self.prices).scan()

        if not opps:
            if self.stats.cycles % 50 == 0:
                log.info(
                    f"Ciclo {self.stats.cycles} | "
                    f"Sin ops | {self.risk.summary()}"
                )
            return

        for opp in opps[:3]:
            self.stats.add_opp(opp)
            log.info(f"🚀 {opp}")

            can, reason = self.risk.can_trade()
            if not can:
                log.warning(f"⛔ {reason}")
                break

            ok = await self.execute(opp)
            self.stats.add_trade(opp, ok)
            self.risk.register(opp.profit_usdt if ok else 0)

            if ok:
                await self.tg.send(
                    f"✅ *Trade ejecutado*\n"
                    f"`{opp}`\n"
                    f"{self.risk.summary()}"
                )

    # ── Reporte periódico ────────────────────────────────────
    async def maybe_report(self):
        if time.time() - self._last_report >= 1800:
            self._last_report = time.time()
            rep = self.stats.report()
            log.info(rep.replace("*", "").replace("`", ""))
            await self.tg.send(rep)
            self.stats.save()

    # ── Entry point ──────────────────────────────────────────
    async def run(self):
        log.info(
            f"\n{'═'*54}\n"
            f"  BINANCE ARB BOT v2.0 | "
            f"{'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"  Profit mín   : {MIN_PROFIT_PCT}%\n"
            f"  Trade size   : ${TRADE_AMOUNT_USDT} USDT\n"
            f"  Max loss/día : ${MAX_DAILY_LOSS_USDT} USDT\n"
            f"  Fee por orden: {FEE_RATE*100}%\n"
            f"  Monedas      : {', '.join(BASE_CURRENCIES)}\n"
            f"  Combos       : {len(BASE_CURRENCIES)*(len(BASE_CURRENCIES)-1)} triángulos\n"
            f"  Telegram     : {'✅' if self.tg.enabled else '❌ no configurado'}\n"
            f"{'═'*54}"
        )

        await self.tg.send(
            f"🤖 *Arbitrage Bot v2.0 iniciado*\n"
            f"Modo   : {'DRY RUN 🔵' if DRY_RUN else 'LIVE 🔴'}\n"
            f"Profit : ≥{MIN_PROFIT_PCT}%\n"
            f"Tamaño : ${TRADE_AMOUNT_USDT} USDT\n"
            f"Combos : {len(BASE_CURRENCIES)*(len(BASE_CURRENCIES)-1)} triángulos"
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
            log.info("Bot detenido manualmente.")
        finally:
            rep = self.stats.report()
            log.info(rep.replace("*", "").replace("`", ""))
            await self.tg.send(f"🛑 *Bot detenido*\n{rep}")
            self.stats.save()
            await self.ex.close()


if __name__ == "__main__":
    asyncio.run(Bot().run())
