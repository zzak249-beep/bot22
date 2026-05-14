"""
Bot v6 — EMA puro
- SYMBOL vacío → scanner dinámico con TODOS los pares de BingX
- SYMBOL="BTC-USDT" → opera solo ese par
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

SYMBOL     = os.getenv("SYMBOL", "")          # vacío = todos los pares
INTERVAL   = os.getenv("INTERVAL",   "3m")
HTF        = os.getenv("HTF_INTERVAL","15m")
LEVERAGE   = int(os.getenv("LEVERAGE",  "5"))
DEMO_MODE  = os.getenv("DEMO_MODE","false").lower() == "true"
RISK_PCT   = float(os.getenv("RISK_PCT",  "1.0"))
SL_ATR     = float(os.getenv("ATR_SL_MULT","1.5"))
SCORE_MIN  = float(os.getenv("SCORE_MIN", "30"))
MIN_VOL    = float(os.getenv("MIN_VOLUME","500000"))

from bingx_client import BingXClient
from scanner      import MultiSymbolScanner
from strategy     import Signal

def tg(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML"},
            timeout=10)
    except Exception as e:
        logger.error(f"TG: {e}")

def _ema(s,p):  return s.ewm(span=p,adjust=False).mean()

def _atr_val(df,p=14):
    h,lo,c = df["high"],df["low"],df["close"]
    pc = c.shift(1)
    tr = pd.concat([h-lo,(h-pc).abs(),(lo-pc).abs()],axis=1).max(axis=1)
    return float(tr.ewm(com=p-1,adjust=False).mean().iloc[-2])


class Bot:
    def __init__(self):
        self.client  = BingXClient(BINGX_API_KEY, BINGX_API_SECRET, demo=DEMO_MODE)
        self.scanner = MultiSymbolScanner(
            self.client, INTERVAL, HTF,
            top_n=1, min_volume=MIN_VOL, score_min=SCORE_MIN, workers=20,
        )
        self.active_sym: Optional[str]   = SYMBOL or None
        self.pos_side:   Optional[str]   = None
        self.entry:      Optional[float] = None
        self.qty:        Optional[float] = None
        self.qty_step    = 0.001
        self.p_prec      = 4
        self.candles     = 0
        self.total_pnl   = 0.0
        self._tg_offset  = 0

    def _fetch(self, symbol: str, interval=None, limit=120) -> pd.DataFrame:
        klines = self.client.get_klines(symbol, interval or INTERVAL, limit=limit)
        if not klines:
            raise ValueError(f"Sin datos {symbol}")
        df = pd.DataFrame(klines)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["close"]).sort_values("timestamp").reset_index(drop=True)

    def _balance(self) -> float:
        try:
            b = self.client.get_balance()
            return float(b.get("availableMargin", b.get("balance", b.get("equity",0))))
        except Exception as e:
            logger.error(f"Balance: {e}"); return 0.0

    def _signal(self, df: pd.DataFrame) -> str:
        if len(df) < 25: return "HOLD"
        c  = df["close"]
        e1 = _ema(c,2); e2 = _ema(c,4); e3 = _ema(c,20)
        i  = -2
        long_a  = c.iloc[i-1] >= e3.iloc[i-1] and c.iloc[i] < e3.iloc[i]
        long_b  = (c.iloc[i]<c.iloc[i-1] and e1.iloc[i]<e1.iloc[i-1] and
                   c.iloc[i-1]>=e1.iloc[i-1] and c.iloc[i]<e1.iloc[i] and
                   e2.iloc[i]>e2.iloc[i-1])
        short_a = c.iloc[i-1] <= e3.iloc[i-1] and c.iloc[i] > e3.iloc[i]
        short_b = (c.iloc[i]>c.iloc[i-1] and e1.iloc[i]>e1.iloc[i-1] and
                   c.iloc[i-1]<=e1.iloc[i-1] and c.iloc[i]>e1.iloc[i] and
                   e2.iloc[i]<e2.iloc[i-1])
        if long_a or long_b:   return "LONG"
        if short_a or short_b: return "SHORT"
        return "HOLD"

    def _init_sym(self, symbol: str):
        for s in ("LONG","SHORT"):
            try: self.client.set_leverage(symbol, LEVERAGE, s)
            except: pass
        try: self.client.set_margin_mode(symbol, "ISOLATED")
        except: pass
        try:
            info = self.client.get_symbol_info(symbol)
            self.qty_step = float(info.get("tradeMinQuantity","0.001"))
            self.p_prec   = int(info.get("pricePrecision", 4))
        except: pass

    def _open(self, symbol: str, side: str, df: pd.DataFrame):
        balance = self._balance()
        if balance <= 0:
            tg("⚠️ Balance cero — transfiere USDT a futuros BingX"); return

        price    = float(df["close"].iloc[-2])
        atv      = _atr_val(df)
        sl_dist  = atv * SL_ATR
        sl_pct   = sl_dist / price
        risk     = balance * RISK_PCT / 100
        notional = (risk * LEVERAGE) / max(sl_pct, 0.0005)

        if notional < 5:
            tg(f"⚠️ Notional pequeño ${notional:.1f} — balance ${balance:.2f}"); return

        qty = float(int((notional/price)/self.qty_step)*self.qty_step)
        if qty <= 0:
            tg(f"⚠️ qty=0 — qty_step={self.qty_step}"); return

        sl_p  = round(price - sl_dist if side=="LONG" else price + sl_dist, self.p_prec)
        tp1_p = round(price + sl_dist if side=="LONG" else price - sl_dist, self.p_prec)
        tp2_p = round(price + sl_dist*2.5 if side=="LONG" else price - sl_dist*2.5, self.p_prec)

        tg(f"{'🟢 LARGO' if side=='LONG' else '🔴 CORTO'} — <b>{symbol}</b>\n\n"
           f"Precio: <code>${price:,.4f}</code>\n"
           f"Qty: <code>{qty}</code> ({LEVERAGE}x)\n"
           f"SL:  <code>${sl_p:,.4f}</code>\n"
           f"TP1: <code>${tp1_p:,.4f}</code>\n"
           f"TP2: <code>${tp2_p:,.4f}</code>\n"
           f"Riesgo: <code>${risk:.2f}</code>")

        try:
            bs = "BUY" if side=="LONG" else "SELL"
            if not DEMO_MODE:
                try: self.client.cancel_all_orders(symbol)
                except: pass
            order = self.client.place_market_order(symbol, bs, side, qty)
            oid   = str(order.get("orderId","OK"))
            try:
                ss = "SELL" if side=="LONG" else "BUY"
                self.client.place_stop_loss(symbol, ss, side, sl_p, qty)
                q1 = float(int(qty*0.5/self.qty_step)*self.qty_step)
                self.client.place_take_profit(symbol, ss, side, tp1_p, q1)
                self.client.place_take_profit(symbol, ss, side, tp2_p, round(qty-q1,8))
            except Exception as ex:
                logger.warning(f"SL/TP: {ex}")
            self.pos_side  = side
            self.entry     = price
            self.qty       = qty
            self.active_sym = symbol
            tg(f"✅ Ejecutado | <code>{oid}</code>")
        except Exception as e:
            logger.error(f"Order: {e}")
            tg(f"❌ Error orden: <code>{str(e)[:400]}</code>")

    def _close(self, symbol: str, price: float, reason=""):
        if not self.pos_side or not self.qty: return
        try:
            if not DEMO_MODE:
                try: self.client.cancel_all_orders(symbol)
                except: pass
            self.client.close_position(symbol, self.pos_side, self.qty)
            pnl = ((price-self.entry) if self.pos_side=="LONG"
                   else (self.entry-price))*self.qty*LEVERAGE if self.entry else 0
            self.total_pnl += pnl
            tg(f"{'💰' if pnl>=0 else '📉'} <b>Cerrado {self.pos_side} {symbol}</b>\n"
               f"${self.entry:.4f} → ${price:.4f}\n"
               f"PnL: <b>{pnl:+.2f}$</b> | Total: <b>{self.total_pnl:+.2f}$</b>"
               +(f"\n{reason}" if reason else ""))
        except Exception as e:
            tg(f"❌ Error cerrando: {e}")
        self.pos_side = self.entry = self.qty = None

    def _commands(self):
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": self._tg_offset, "timeout": 1}, timeout=5)
            for upd in r.json().get("result", []):
                self._tg_offset = upd["update_id"] + 1
                msg = upd.get("message",{}).get("text","").lower().strip()
                cid = str(upd.get("message",{}).get("chat",{}).get("id",""))
                if cid != TELEGRAM_CHAT_ID: continue
                if msg == "/status":
                    bal = self._balance()
                    syms = self.scanner._get_all_symbols()
                    tg(f"ℹ️ <b>Estado</b>\n\n"
                       f"Par activo: <code>{self.active_sym or 'Scanner'}</code>\n"
                       f"Posición: <code>{self.pos_side or 'Sin posición'}</code>\n"
                       f"Balance: <code>${bal:.2f}</code>\n"
                       f"PnL sesión: <code>{self.total_pnl:+.2f}$</code>\n"
                       f"Pares escaneados: <code>{len(syms)}</code>")
                elif msg == "/balance":
                    tg(f"💼 Balance: <code>${self._balance():.2f} USDT</code>")
                elif msg == "/scan":
                    results = self.scanner.scan(force=True)
                    tg(self.scanner.format_report(results))
                elif msg == "/stop":
                    tg("⛔ Deteniendo...")
                    if self.active_sym and self.pos_side:
                        try:
                            p = float(self.client.get_ticker(self.active_sym).get("lastPrice",0))
                            self._close(self.active_sym, p, "Stop manual")
                        except: pass
                    raise SystemExit
        except SystemExit: raise
        except: pass

    def setup(self):
        symbols = self.scanner._get_all_symbols()
        bal     = self._balance()
        mode    = "PAPER 🟡" if DEMO_MODE else "LIVE 🟢"
        tg(f"🤖 <b>Bot {mode}</b>\n\n"
           f"Modo: <code>{'Par fijo: '+SYMBOL if SYMBOL else 'Scanner automático'}</code>\n"
           f"Pares disponibles: <code>{len(symbols)}</code>\n"
           f"Intervalo: <code>{INTERVAL}</code> | Leverage: <code>{LEVERAGE}x</code>\n"
           f"Balance: <code>${bal:.2f} USDT</code>\n"
           f"Vol mínimo: <code>${MIN_VOL/1e6:.1f}M</code>\n\n"
           f"Comandos: /status /balance /scan /stop")

    def tick(self):
        self.candles += 1
        self._commands()

        # ── Sin posición: buscar señal ──────────────────────────────────
        if not self.pos_side:
            if SYMBOL:
                # Par fijo
                try:
                    df     = self._fetch(SYMBOL)
                    action = self._signal(df)
                    price  = float(df["close"].iloc[-1])
                    logger.info(f"[{SYMBOL}] {price:.2f} | {action}")
                    if action in ("LONG","SHORT"):
                        self._init_sym(SYMBOL)
                        tg(f"📡 Señal <b>{action}</b> en {SYMBOL} @ ${price:,.2f}")
                        self._open(SYMBOL, action, df)
                except Exception as e:
                    logger.error(f"Tick fixed: {e}")
            else:
                # Scanner dinámico — TODOS los pares
                results = self.scanner.scan()
                if results:
                    best = results[0]
                    logger.info(f"Mejor: {best.symbol} {best.signal} score={best.score}")
                    tg(self.scanner.format_report(results))
                    self._init_sym(best.symbol)
                    df = self._fetch(best.symbol)
                    self._open(best.symbol, best.signal, df)
                else:
                    logger.info("Sin señales activas")
            return

        # ── Con posición: gestionar ─────────────────────────────────────
        symbol = self.active_sym
        try:
            df     = self._fetch(symbol)
            action = self._signal(df)
            price  = float(df["close"].iloc[-1])
            logger.info(f"[{symbol}] {price:.4f} | {action} | pos={self.pos_side}")

            flip = ((action=="LONG" and self.pos_side=="SHORT") or
                    (action=="SHORT" and self.pos_side=="LONG"))
            if flip:
                self._close(symbol, price, f"Flip → {action}")
                time.sleep(0.5)
                self._open(symbol, action, df)
        except Exception as e:
            logger.error(f"Tick pos: {e}", exc_info=True)
            tg(f"⚠️ Error: <code>{str(e)[:200]}</code>")

        if self.candles % 20 == 0:
            try:
                bal   = self._balance()
                price = float(self._fetch(symbol)["close"].iloc[-1])
                tg(f"💓 {symbol} ${price:,.2f} | "
                   f"{'🟢' if self.pos_side=='LONG' else '🔴' if self.pos_side=='SHORT' else '⚪'}"
                   f"{self.pos_side or 'FLAT'} | ${bal:.2f} | PnL:{self.total_pnl:+.2f}$")
            except: pass

    def run(self):
        self.setup()
        secs = int(INTERVAL[:-1])*{"m":60,"h":3600}.get(INTERVAL[-1],60)
        logger.info(f"🚀 Activo | {INTERVAL}")
        while True:
            try:
                now  = time.time()
                wait = max(0,(int(now/secs)+1)*secs+2-now)
                logger.info(f"⏳ {wait:.0f}s")
                time.sleep(wait)
                self.tick()
            except (KeyboardInterrupt, SystemExit): break
            except Exception as e:
                logger.error(f"Loop: {e}")
                time.sleep(15)


if __name__ == "__main__":
    Bot().run()
