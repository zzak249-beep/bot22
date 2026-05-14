"""
Bot Simple v5 — EMA puro, sin scanner, sin filtros
Entry point principal (railway.toml)
"""
import logging, os, sys, time
from typing import Optional
import pandas as pd
import numpy as np
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("bot.log", encoding="utf-8")],
)
logger = logging.getLogger("bot")

BINGX_API_KEY    = os.environ["BINGX_API_KEY"]
BINGX_API_SECRET = os.environ["BINGX_API_SECRET"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL    = os.getenv("SYMBOL", "BTC-USDT") or "BTC-USDT"
INTERVAL  = os.getenv("INTERVAL", "3m")
LEVERAGE  = int(os.getenv("LEVERAGE", "5"))
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"
RISK_PCT  = float(os.getenv("RISK_PCT", "1.0"))
SL_ATR    = float(os.getenv("ATR_SL_MULT", "1.5"))

from bingx_client import BingXClient

def tg(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10)
    except Exception as e:
        logger.error(f"TG: {e}")

def _ema(s, p):   return s.ewm(span=p, adjust=False).mean()
def _atr_series(df, p=14):
    h, lo, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h-lo, (h-pc).abs(), (lo-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(com=p-1, adjust=False).mean()


class SimpleBot:
    def __init__(self):
        self.client   = BingXClient(BINGX_API_KEY, BINGX_API_SECRET, demo=DEMO_MODE)
        self.pos_side: Optional[str]   = None
        self.entry:    Optional[float] = None
        self.qty:      Optional[float] = None
        self.qty_step  = 0.001
        self.p_prec    = 4
        self.candles   = 0

    def _fetch(self) -> pd.DataFrame:
        """Usa el cliente corregido — devuelve dicts normalizados"""
        klines = self.client.get_klines(SYMBOL, INTERVAL, limit=120)
        if not klines:
            raise ValueError("Sin datos")
        df = pd.DataFrame(klines)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        return df.sort_values("timestamp").reset_index(drop=True)

    def _balance(self) -> float:
        try:
            b = self.client.get_balance()
            return float(b.get("availableMargin", b.get("balance", b.get("equity", 0))))
        except Exception as e:
            logger.error(f"Balance: {e}")
            return 0.0

    def _signal(self, df: pd.DataFrame) -> str:
        if len(df) < 25:
            return "HOLD"
        c  = df["close"]
        e1 = _ema(c, 2); e2 = _ema(c, 4); e3 = _ema(c, 20)

        # Vela cerrada = iloc[-2]
        i = -2
        long_a  = c.iloc[i-1] >= e3.iloc[i-1] and c.iloc[i] < e3.iloc[i]
        long_b  = (c.iloc[i]-c.iloc[i-1] < 0 and
                   e1.iloc[i]-e1.iloc[i-1] < 0 and
                   c.iloc[i-1] >= e1.iloc[i-1] and
                   c.iloc[i] < e1.iloc[i] and
                   e2.iloc[i]-e2.iloc[i-1] > 0)
        short_a = c.iloc[i-1] <= e3.iloc[i-1] and c.iloc[i] > e3.iloc[i]
        short_b = (c.iloc[i]-c.iloc[i-1] > 0 and
                   e1.iloc[i]-e1.iloc[i-1] > 0 and
                   c.iloc[i-1] <= e1.iloc[i-1] and
                   c.iloc[i] > e1.iloc[i] and
                   e2.iloc[i]-e2.iloc[i-1] < 0)

        if long_a or long_b:   return "LONG"
        if short_a or short_b: return "SHORT"
        return "HOLD"

    def _open(self, side: str, df: pd.DataFrame):
        balance = self._balance()
        if balance <= 0:
            tg(f"⚠️ Balance cero — transfiere USDT a futuros BingX")
            return

        price   = float(df["close"].iloc[-2])
        atv     = float(_atr_series(df).iloc[-2])
        sl_dist = atv * SL_ATR
        sl_pct  = sl_dist / price
        risk    = balance * RISK_PCT / 100
        notional = (risk * LEVERAGE) / max(sl_pct, 0.0005)

        if notional < 5:
            tg(f"⚠️ Posición muy pequeña: ${notional:.2f}\n"
               f"Balance: ${balance:.2f} | ATR: {atv:.4f}")
            return

        qty = float(int((notional/price) / self.qty_step) * self.qty_step)
        if qty <= 0:
            tg(f"⚠️ qty=0 | qty_step={self.qty_step}")
            return

        if side == "LONG":
            sl_p  = round(price - sl_dist,       self.p_prec)
            tp1_p = round(price + sl_dist,        self.p_prec)
            tp2_p = round(price + sl_dist * 2.5,  self.p_prec)
        else:
            sl_p  = round(price + sl_dist,        self.p_prec)
            tp1_p = round(price - sl_dist,        self.p_prec)
            tp2_p = round(price - sl_dist * 2.5,  self.p_prec)

        e = "🟢 LARGO" if side == "LONG" else "🔴 CORTO"
        tg(f"<b>{e} — {SYMBOL}</b>\n\n"
           f"Precio:  <code>${price:,.4f}</code>\n"
           f"Qty:     <code>{qty}</code> ({LEVERAGE}x)\n"
           f"SL:      <code>${sl_p:,.4f}</code>\n"
           f"TP1:     <code>${tp1_p:,.4f}</code>\n"
           f"TP2:     <code>${tp2_p:,.4f}</code>\n"
           f"Riesgo:  <code>${risk:.2f}</code>")

        try:
            bs = "BUY" if side == "LONG" else "SELL"
            if not DEMO_MODE:
                try: self.client.cancel_all_orders(SYMBOL)
                except: pass
            order = self.client.place_market_order(SYMBOL, bs, side, qty)
            oid   = str(order.get("orderId","OK"))
            logger.info(f"Orden OK: {order}")
            try:
                ss = "SELL" if side == "LONG" else "BUY"
                self.client.place_stop_loss(SYMBOL, ss, side, sl_p, qty)
                q1 = float(int(qty*0.5/self.qty_step)*self.qty_step)
                q2 = round(qty - q1, 8)
                self.client.place_take_profit(SYMBOL, ss, side, tp1_p, q1)
                self.client.place_take_profit(SYMBOL, ss, side, tp2_p, q2)
            except Exception as ex:
                logger.warning(f"SL/TP: {ex}")
            self.pos_side = side
            self.entry    = price
            self.qty      = qty
            tg(f"✅ Ejecutado | ID: <code>{oid}</code>")
        except Exception as e:
            logger.error(f"Order: {e}")
            tg(f"❌ Error orden: <code>{str(e)[:400]}</code>")

    def _close(self, price: float, reason: str = ""):
        if not self.pos_side or not self.qty: return
        try:
            if not DEMO_MODE:
                try: self.client.cancel_all_orders(SYMBOL)
                except: pass
            self.client.close_position(SYMBOL, self.pos_side, self.qty)
            pnl = ((price-self.entry) if self.pos_side=="LONG"
                   else (self.entry-price)) * self.qty * LEVERAGE if self.entry else 0
            tg(f"{'💰' if pnl>=0 else '📉'} <b>Cerrado {self.pos_side}</b>\n"
               f"${self.entry:.4f} → ${price:.4f} | PnL: <b>{pnl:+.2f}$</b>"
               + (f"\n{reason}" if reason else ""))
        except Exception as e:
            tg(f"❌ Error cerrando: {e}")
        self.pos_side = self.entry = self.qty = None

    def setup(self):
        for s in ("LONG","SHORT"):
            try: self.client.set_leverage(SYMBOL, LEVERAGE, s)
            except: pass
        try: self.client.set_margin_mode(SYMBOL, "ISOLATED")
        except: pass
        try:
            info = self.client.get_symbol_info(SYMBOL)
            self.qty_step = float(info.get("tradeMinQuantity","0.001"))
        except: pass
        bal = self._balance()
        tg(f"🤖 <b>Bot {'PAPER 🟡' if DEMO_MODE else 'LIVE 🟢'}</b>\n\n"
           f"Par: <code>{SYMBOL}</code> | TF: <code>{INTERVAL}</code>\n"
           f"Leverage: <code>{LEVERAGE}x</code>\n"
           f"Balance: <code>${bal:,.2f} USDT</code>\n"
           f"qty_step: <code>{self.qty_step}</code>")

    def tick(self):
        self.candles += 1
        try:
            df     = self._fetch()
            action = self._signal(df)
            price  = float(df["close"].iloc[-1])
            logger.info(f"[{SYMBOL}] {price:.2f} | señal={action} | pos={self.pos_side}")

            if not self.pos_side:
                if action in ("LONG","SHORT"):
                    tg(f"📡 Señal: <b>{action}</b> @ ${price:,.2f}")
                    self._open(action, df)
            else:
                flip = ((action=="LONG" and self.pos_side=="SHORT") or
                        (action=="SHORT" and self.pos_side=="LONG"))
                if flip:
                    self._close(price, f"Flip → {action}")
                    time.sleep(0.5)
                    self._open(action, df)

            if self.candles % 20 == 0:
                self.tg_heartbeat(price)
        except Exception as e:
            logger.error(f"Tick: {e}", exc_info=True)
            tg(f"⚠️ Error: <code>{str(e)[:300]}</code>")

    def tg_heartbeat(self, price):
        try:
            bal = self._balance()
            tg(f"💓 {SYMBOL} ${price:,.2f} | "
               f"{'🟢' if self.pos_side=='LONG' else '🔴' if self.pos_side=='SHORT' else '⚪'}"
               f"{self.pos_side or 'FLAT'} | ${bal:.2f}")
        except: pass

    def run(self):
        self.setup()
        secs = int(INTERVAL[:-1]) * {"m":60,"h":3600}.get(INTERVAL[-1],60)
        logger.info(f"🚀 Bot activo | {INTERVAL} ({secs}s)")
        while True:
            try:
                now  = time.time()
                wait = max(0,(int(now/secs)+1)*secs + 2 - now)
                logger.info(f"⏳ {wait:.0f}s")
                time.sleep(wait)
                self.tick()
            except KeyboardInterrupt: break
            except Exception as e:
                logger.error(f"Loop: {e}")
                time.sleep(15)

if __name__ == "__main__":
    SimpleBot().run()
