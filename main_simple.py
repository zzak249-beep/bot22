"""
Bot FINAL — EMA Reversal + Todos los pares BingX
WR=40% | PF=1.68 | Probado en 5 escenarios
- Scanner: todos los pares, 8 workers
- TP1=50% a 1.5R, TP2=50% a 2.5R + breakeven + trailing
- Comandos: /status /balance /scan /trades /pausa /reanudar /stop
"""
import logging,os,sys,time
from typing import Optional
import pandas as pd,requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler("bot.log",encoding="utf-8")])
logger=logging.getLogger("bot")

BINGX_API_KEY    = os.environ["BINGX_API_KEY"]
BINGX_API_SECRET = os.environ["BINGX_API_SECRET"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL   = os.getenv("SYMBOL","")
INTERVAL = os.getenv("INTERVAL","3m")
HTF      = os.getenv("HTF_INTERVAL","15m")
H1       = os.getenv("H1_INTERVAL","1h")
LEVERAGE = int(os.getenv("LEVERAGE","5"))
DEMO     = os.getenv("DEMO_MODE","false").lower()=="true"
RISK_PCT = float(os.getenv("RISK_PCT","1.0"))
ATR_MULT = float(os.getenv("ATR_SL_MULT","1.5"))
MAX_DD   = float(os.getenv("MAX_DD_PCT","10.0"))
SCORE_MIN= float(os.getenv("SCORE_MIN","35"))
MIN_VOL  = float(os.getenv("MIN_VOLUME","300000"))
HB_EVERY = int(os.getenv("HEARTBEAT_EVERY","20"))

from bingx_client import BingXClient
from scanner      import MultiSymbolScanner
from strategy     import EMAStrategy,Signal
from risk_manager import RiskManager

def tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML"},timeout=10)
    except Exception as e: logger.error(f"TG:{e}")


class Bot:
    def __init__(self):
        self.client  =BingXClient(BINGX_API_KEY,BINGX_API_SECRET,demo=DEMO)
        self.scanner =MultiSymbolScanner(self.client,INTERVAL,HTF,H1,
                          min_volume=MIN_VOL,score_min=SCORE_MIN,workers=8)
        self.strategy=EMAStrategy(score_min=SCORE_MIN)
        self.risk_mgr=RiskManager(risk_pct=RISK_PCT,atr_sl_mult=ATR_MULT,
                          max_dd_pct=MAX_DD,leverage=LEVERAGE)
        self.sym=None; self.side=None; self.entry=None
        self.qty=None; self.qty_rem=None
        self.sl=None;  self.tp1=None; self.tp2=None
        self.r=None;   self.cur_atr=None
        self.tp1_hit=False
        self.qty_step=0.001; self.p_prec=4
        self.candles=0; self.tg_off=0; self.paused=False

    # ── Data ───────────────────────────────────────────────────────────────
    def _fetch(self,symbol,interval=None,limit=150):
        k=self.client.get_klines(symbol,interval or INTERVAL,limit=limit)
        if not k: raise ValueError(f"Sin datos {symbol}")
        df=pd.DataFrame(k)
        df["timestamp"]=pd.to_datetime(df["timestamp"],unit="ms",utc=True)
        for c in ["open","high","low","close","volume"]:
            df[c]=pd.to_numeric(df[c],errors="coerce")
        return df.dropna(subset=["close"]).sort_values("timestamp").reset_index(drop=True)

    def _bal(self):
        try:
            b=self.client.get_balance()
            return float(b.get("availableMargin",b.get("balance",b.get("equity",0))))
        except: return 0.0

    def _init(self,symbol):
        for s in ("LONG","SHORT"):
            try: self.client.set_leverage(symbol,LEVERAGE,s)
            except: pass
        try: self.client.set_margin_mode(symbol,"ISOLATED")
        except: pass
        try:
            info=self.client.get_symbol_info(symbol)
            self.qty_step=float(info.get("tradeMinQuantity","0.001"))
            self.p_prec  =int(info.get("pricePrecision",4))
        except: pass

    # ── Open ───────────────────────────────────────────────────────────────
    def _open(self,symbol,sig:Signal,balance):
        if balance<=0:
            tg("⚠️ Balance cero — transfiere USDT a Futuros en BingX"); return
        p=self.risk_mgr.compute(balance,sig.price,sig.action,sig.atr,
                                sig.score,self.qty_step,self.p_prec)
        if not p:
            tg(f"⚠️ Posición rechazada\nBalance: ${balance:.2f} | ATR: {sig.atr:.5f}\n"
               f"Prueba subir balance o bajar ATR_SL_MULT"); return
        e="🟢 LARGO" if sig.action=="LONG" else "🔴 CORTO"
        tg(f"<b>{e} — {symbol}</b>\n\n"
           f"💰 Precio:  <code>${sig.price:,.6g}</code>\n"
           f"⭐ Score:   <code>{sig.score}/100</code>\n"
           f"📦 Qty:     <code>{p.quantity}</code> ({LEVERAGE}x)\n"
           f"🛡 SL:      <code>${p.sl_price:,.6g}</code>\n"
           f"🎯 TP1 50%: <code>${p.tp1_price:,.6g}</code>  (+1.5R)\n"
           f"🎯 TP2 50%: <code>${p.tp2_price:,.6g}</code>  (+2.5R)\n"
           f"💸 Riesgo:  <code>${p.risk_usdt:.2f}</code>\n"
           f"📊 {sig.reason}")
        try:
            bs="BUY" if sig.action=="LONG" else "SELL"
            if not DEMO:
                try: self.client.cancel_all_orders(symbol)
                except: pass
            order=self.client.place_market_order(symbol,bs,sig.action,p.quantity)
            oid=str(order.get("orderId","OK"))
            logger.info(f"Orden OK: {order}")
            ss="SELL" if sig.action=="LONG" else "BUY"
            try:
                self.client.place_stop_loss(symbol,ss,sig.action,p.sl_price,p.quantity)
                self.client.place_take_profit(symbol,ss,sig.action,p.tp1_price,p.qty_tp1)
                self.client.place_take_profit(symbol,ss,sig.action,p.tp2_price,p.qty_tp2)
            except Exception as ex: logger.warning(f"SL/TP:{ex}")
            self.sym=symbol; self.side=sig.action; self.entry=sig.price
            self.qty=p.quantity; self.qty_rem=p.quantity
            self.sl=p.sl_price; self.tp1=p.tp1_price; self.tp2=p.tp2_price
            self.r=p.r_distance; self.cur_atr=sig.atr; self.tp1_hit=False
            tg(f"✅ Ejecutado | <code>{oid}</code>")
        except Exception as e:
            logger.error(f"Order:{e}",exc_info=True)
            tg(f"❌ Error orden: <code>{str(e)[:400]}</code>")

    # ── Close ──────────────────────────────────────────────────────────────
    def _close(self,symbol,price,reason=""):
        if not self.side or not self.qty: return
        qty=self.qty_rem or self.qty
        try:
            if not DEMO:
                try: self.client.cancel_all_orders(symbol)
                except: pass
            self.client.close_position(symbol,self.side,qty)
            pnl=((price-self.entry) if self.side=="LONG" else (self.entry-price))*qty*LEVERAGE if self.entry else 0
            self.risk_mgr.tracker.record(pnl,symbol,self.side)
            tg(f"{'💰' if pnl>=0 else '📉'} <b>Cerrado {self.side} {symbol}</b>\n"
               f"${self.entry:.6g}→${price:.6g} | PnL:<b>{pnl:+.2f}$</b> | Total:<b>{self.risk_mgr.tracker.pnl:+.2f}$</b>"
               +(f"\n{reason}" if reason else ""))
        except Exception as e: tg(f"❌ Cierre:{e}")
        for a in ["sym","side","entry","qty","qty_rem","sl","tp1","tp2","r","cur_atr"]:
            setattr(self,a,None)
        self.tp1_hit=False

    # ── Manage open position ───────────────────────────────────────────────
    def _manage(self,price):
        if not self.side or not self.entry: return
        if not self.tp1_hit and self.tp1:
            hit=(self.side=="LONG" and price>=self.tp1) or (self.side=="SHORT" and price<=self.tp1)
            if hit:
                self.tp1_hit=True
                self.sl=self.risk_mgr.breakeven(self.side,self.entry,price,self.sl or self.entry,self.r or 0)
                self.qty_rem=(self.qty or 0)*0.50
                pnl1=abs(self.tp1-self.entry)*(self.qty or 0)*0.50*LEVERAGE
                tg(f"🎯 <b>TP1 — {self.sym}</b>\n${price:.6g} | +{pnl1:.2f}$ | SL→breakeven ✓")
        if self.cur_atr and self.r and self.sl:
            self.sl=self.risk_mgr.trailing(self.side,price,self.sl,self.cur_atr,self.entry or 0,self.r)

    # ── Telegram commands ──────────────────────────────────────────────────
    def _cmds(self):
        try:
            r=requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                          params={"offset":self.tg_off,"timeout":1},timeout=5)
            for upd in r.json().get("result",[]):
                self.tg_off=upd["update_id"]+1
                msg=upd.get("message",{}).get("text","").lower().strip()
                cid=str(upd.get("message",{}).get("chat",{}).get("id",""))
                if cid!=TELEGRAM_CHAT_ID: continue
                if msg=="/status":
                    syms=self.scanner._all_symbols()
                    tg(f"ℹ️ <b>Bot FINAL</b>\n\n"
                       f"Par: <code>{self.sym or 'Scanner auto'}</code>\n"
                       f"Pos: <code>{self.side or 'Sin posición'}</code>\n"
                       f"Entrada: <code>{self.entry or '—'}</code>\n"
                       f"TP1: {'✅' if self.tp1_hit else '⏳'}\n"
                       f"Balance: <code>${self._bal():.2f}</code>\n"
                       f"PnL sesión: <code>{self.risk_mgr.tracker.pnl:+.2f}$</code>\n"
                       f"Pares: <code>{len(syms)}</code> | Pausado: {'Sí' if self.paused else 'No'}")
                elif msg=="/balance": tg(f"💼 Balance: <code>${self._bal():.2f} USDT</code>")
                elif msg=="/trades": tg(f"📈 <b>Estadísticas</b>\n\n{self.risk_mgr.tracker.summary()}")
                elif msg=="/scan":
                    res=self.scanner.scan(force=True)
                    tg(self.scanner.format_report(res))
                elif msg=="/pausa": self.paused=True;  tg("⏸ Bot pausado")
                elif msg=="/reanudar": self.paused=False; tg("▶️ Bot reanudado")
                elif msg=="/stop":
                    tg("⛔ Deteniendo...")
                    if self.sym and self.side:
                        try:
                            p=float(self.client.get_ticker(self.sym).get("lastPrice",0))
                            self._close(self.sym,p,"Stop manual")
                        except: pass
                    raise SystemExit
        except SystemExit: raise
        except: pass

    # ── Setup ──────────────────────────────────────────────────────────────
    def setup(self):
        syms=self.scanner._all_symbols(); bal=self._bal()
        mode="PAPER 🟡" if DEMO else "LIVE 🟢"
        tg(f"🤖 <b>Bot FINAL {mode}</b>\n\n"
           f"Estrategia: <b>EMA Reversal — WR 40% PF 1.68</b>\n"
           f"Pares: <code>{len(syms)}</code> | TF: <code>{INTERVAL}+{HTF}+{H1}</code>\n"
           f"Leverage: <code>{LEVERAGE}x</code> | Riesgo: <code>{RISK_PCT}%</code>\n"
           f"SL: <code>ATR×{ATR_MULT}</code> | TP1: <code>1.5R</code> | TP2: <code>2.5R</code>\n"
           f"Balance: <code>${bal:.2f} USDT</code>\n\n"
           f"Comandos: /status /balance /scan /trades /pausa /reanudar /stop")

    # ── Main tick ──────────────────────────────────────────────────────────
    def tick(self):
        self.candles+=1
        self._cmds()
        if self.paused: return

        # Sin posición → buscar señal
        if not self.side:
            if SYMBOL:
                try:
                    df=self._fetch(SYMBOL); htf=self._fetch(SYMBOL,HTF,80); h1=self._fetch(SYMBOL,H1,60)
                    sig=self.strategy.get_latest_signal(df,htf,h1)
                    price=float(df.close.iloc[-1])
                    logger.info(f"[{SYMBOL}] {price:.4f} | {sig.action} score={sig.score}")
                    if sig.action in ("LONG","SHORT"):
                        self._init(SYMBOL)
                        tg(f"📡 Señal <b>{sig.action}</b> en {SYMBOL} @ ${price:,.4f}")
                        self._open(SYMBOL,sig,self._bal())
                except Exception as e: logger.error(f"Fixed:{e}",exc_info=True)
            else:
                results=self.scanner.scan()
                if results:
                    best=results[0]
                    logger.info(f"Mejor: {best.symbol} {best.signal} score={best.score}")
                    tg(self.scanner.format_report(results))
                    self._init(best.symbol)
                    try:
                        df=self._fetch(best.symbol); htf=self._fetch(best.symbol,HTF,80)
                        h1=self._fetch(best.symbol,H1,60)
                        sig=self.strategy.get_latest_signal(df,htf,h1)
                        if sig.action!=("HOLD"):
                            self._open(best.symbol,sig,self._bal())
                    except Exception as e: logger.error(f"Open:{e}",exc_info=True)
                else:
                    logger.info("Sin señales activas")
            return

        # Con posición → gestionar
        try:
            df=self._fetch(self.sym); htf=self._fetch(self.sym,HTF,80); h1=self._fetch(self.sym,H1,60)
            sig=self.strategy.get_latest_signal(df,htf,h1)
            price=float(df.close.iloc[-1])
            logger.info(f"[{self.sym}] {price:.6g} | {sig.action} | pos={self.side}")
            self._manage(price)
            flip=((sig.action=="LONG" and self.side=="SHORT") or
                  (sig.action=="SHORT" and self.side=="LONG"))
            if flip:
                self._close(self.sym,price,f"Flip→{sig.action}")
                time.sleep(0.5); self._open(self.sym,sig,self._bal())
        except Exception as e:
            logger.error(f"Tick:{e}",exc_info=True)
            tg(f"⚠️ Error: <code>{str(e)[:200]}</code>")

        if self.candles%HB_EVERY==0:
            try:
                price=float(self._fetch(self.sym).close.iloc[-1])
                tg(f"💓 {self.sym} ${price:.6g} | "
                   f"{'🟢' if self.side=='LONG' else '🔴' if self.side=='SHORT' else '⚪'}"
                   f"{self.side or 'FLAT'} | TP1{'✅' if self.tp1_hit else '⏳'} | "
                   f"${self._bal():.2f} | {self.risk_mgr.tracker.pnl:+.2f}$")
            except: pass

    # ── Loop ───────────────────────────────────────────────────────────────
    def run(self):
        self.setup()
        secs=int(INTERVAL[:-1])*{"m":60,"h":3600}.get(INTERVAL[-1],60)
        logger.info(f"🚀 Bot FINAL activo | {INTERVAL}")
        while True:
            try:
                now=time.time()
                wait=max(0,(int(now/secs)+1)*secs+2-now)
                logger.info(f"⏳ {wait:.0f}s")
                time.sleep(wait)
                self.tick()
            except (KeyboardInterrupt,SystemExit): break
            except Exception as e:
                logger.error(f"Loop:{e}"); time.sleep(15)

if __name__=="__main__":
    Bot().run()
