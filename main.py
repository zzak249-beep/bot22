"""
Sniper Bot V45 — ONE-WAY MODE (Compatible con cuentas BingX por defecto)
=========================================================================
FIX DEFINITIVO:
  - positionSide = "BOTH" siempre (ONE-WAY mode, default BingX)
  - side = "BUY" para abrir LONG o cerrar SHORT
  - side = "SELL" para abrir SHORT o cerrar LONG
  - No requiere activar Hedge Mode
  - SL/TP en la misma orden (formato correcto ONE-WAY)
"""

import asyncio, hashlib, hmac, logging, os, time, urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import httpx, numpy as np

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("logs/bot.log")],
)
log = logging.getLogger("SniperBot")

BINGX_API_KEY   = os.environ["BINGX_API_KEY"]
BINGX_API_SECRET= os.environ["BINGX_API_SECRET"]
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID= os.environ["TELEGRAM_CHAT_ID"]
TIMEFRAME       = os.getenv("TIMEFRAME",            "15m")
TF_HIGH         = os.getenv("TIMEFRAME_HIGH",       "1h")
LEVERAGE        = int(os.getenv("LEVERAGE",         "5"))
MAX_RISK_PCT    = float(os.getenv("MAX_RISK_PCT",   "1.0"))
MAX_POS_USDT    = float(os.getenv("MAX_POS_USDT",   "30"))
SCAN_TOP_N      = int(os.getenv("SCAN_TOP_N",       "10"))
MIN_VOL_USDT    = float(os.getenv("MIN_VOL_USDT",   "10000000"))
SCORE_ENTRY     = int(os.getenv("SCORE_ENTRY",      "55"))
SCAN_INTERVAL   = int(os.getenv("SCAN_INTERVAL_MIN","5")) * 60
MAX_POSITIONS   = int(os.getenv("MAX_POSITIONS",    "5"))
BLACKOUT_START  = int(os.getenv("BLACKOUT_START_UTC","0"))
BLACKOUT_END    = int(os.getenv("BLACKOUT_END_UTC",  "2"))
SLOPE_MIN       = float(os.getenv("SLOPE_MIN",      "25.0"))
POC_LOOKBACK    = int(os.getenv("POC_LOOKBACK",     "50"))
ADX_MAX         = float(os.getenv("ADX_MAX",        "35.0"))
RVOL_MIN        = float(os.getenv("RVOL_MIN",       "1.3"))
RR_RATIO        = float(os.getenv("RR_RATIO",       "2.5"))
ATR_SL_MULT     = float(os.getenv("ATR_SL_MULT",   "1.2"))
EMA_FAST        = int(os.getenv("EMA_FAST",         "7"))
EMA_SLOW        = int(os.getenv("EMA_SLOW",         "17"))
BASE_URL        = "https://open-api.bingx.com"

BLACKLIST = {
    "USDC-USDT","BUSD-USDT","DAI-USDT","TUSD-USDT","FRAX-USDT",
    "NCCOGOLD2USD-USDT","PAXG-USDT","XAUT-USDT","WBTC-USDT",
    "STETH-USDT","WETH-USDT","CBETH-USDT","ZEC-USDT","USDP-USDT",
}
WHITELIST = {
    "BTC-USDT","ETH-USDT","BNB-USDT","SOL-USDT","XRP-USDT",
    "DOGE-USDT","ADA-USDT","AVAX-USDT","DOT-USDT","LINK-USDT",
    "MATIC-USDT","UNI-USDT","ATOM-USDT","LTC-USDT","BCH-USDT",
    "NEAR-USDT","APT-USDT","ARB-USDT","OP-USDT","INJ-USDT",
    "SUI-USDT","TIA-USDT","WLD-USDT","AAVE-USDT","ONDO-USDT",
    "ENA-USDT","PEPE-USDT","WIF-USDT","SEI-USDT","JUP-USDT",
    "FIL-USDT","RENDER-USDT","FET-USDT","SHIB-USDT","BONK-USDT",
    "NOT-USDT","FLOKI-USDT","SAND-USDT","MANA-USDT","IMX-USDT",
    "BLUR-USDT","GALA-USDT","AXS-USDT","ALGO-USDT",
}
USE_WHITELIST = os.getenv("USE_WHITELIST","true").lower() == "true"


# ══════════════════════════════════════════════════════════════════
# EXCHANGE — ONE-WAY MODE
# ══════════════════════════════════════════════════════════════════
class BingXClient:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=BASE_URL, timeout=20)

    def _sign(self, params: dict) -> str:
        q = urllib.parse.urlencode(sorted(params.items()))
        return hmac.new(BINGX_API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()

    def _h(self):
        return {"X-BX-APIKEY": BINGX_API_KEY, "Content-Type": "application/json"}

    async def _get(self, path, params=None) -> dict:
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = self._sign(p)
        r = await self.client.get(path, params=p, headers=self._h())
        r.raise_for_status()
        d = r.json()
        if d.get("code", 0) != 0:
            raise RuntimeError(f"BingX {d['code']}: {d.get('msg')}")
        return d

    async def _post(self, path, params: dict) -> dict:
        p = dict(params)
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = self._sign(p)
        r = await self.client.post(path, params=p, headers=self._h())
        r.raise_for_status()
        d = r.json()
        if d.get("code", 0) != 0:
            raise RuntimeError(f"BingX {d['code']}: {d.get('msg')}")
        return d

    async def get_klines(self, symbol, interval, limit=200) -> list:
        d = await self._get("/openApi/swap/v3/quote/klines",
                            {"symbol": symbol, "interval": interval, "limit": limit})
        out = []
        for c in d["data"]:
            try:
                if isinstance(c, list):
                    out.append({"time":int(c[0]),"open":float(c[1]),"high":float(c[2]),
                                "low":float(c[3]),"close":float(c[4]),"volume":float(c[5])})
                else:
                    out.append({"time":  int(c.get("time",   c.get("t",0))),
                                "open":  float(c.get("open",  c.get("o",0))),
                                "high":  float(c.get("high",  c.get("h",0))),
                                "low":   float(c.get("low",   c.get("l",0))),
                                "close": float(c.get("close", c.get("c",0))),
                                "volume":float(c.get("volume",c.get("v",c.get("quoteVolume",0))))})
            except: continue
        return out

    async def get_tickers(self) -> list:
        d = await self._get("/openApi/swap/v2/quote/ticker")
        out = []
        for t in d.get("data", []):
            sym = t.get("symbol","")
            if not sym.endswith("-USDT"): continue
            if sym in BLACKLIST: continue
            if USE_WHITELIST and sym not in WHITELIST: continue
            price = 0.0
            for f in ("lastPrice","last","price","close","markPrice"):
                v = t.get(f)
                if v:
                    try:
                        fv = float(v)
                        if fv > 0: price = fv; break
                    except: pass
            change = 0.0
            for f in ("priceChangePercent","change","changePercent"):
                v = t.get(f)
                if v is not None:
                    try: change = float(v); break
                    except: pass
            if price > 0:
                out.append({"symbol":sym,"price":price,"change_24h":change})
        return out

    async def get_funding_rate(self, symbol) -> float:
        try:
            d = await self._get("/openApi/swap/v2/quote/premiumIndex",{"symbol":symbol})
            data = d.get("data",{})
            if isinstance(data,list): data = data[0] if data else {}
            return float(data.get("lastFundingRate",data.get("fundingRate",0)))
        except: return 0.0

    async def get_balance(self) -> float:
        try:
            d = await self._get("/openApi/swap/v2/user/balance")
            raw = d.get("data", d)
            log.info(f"[BALANCE RAW]: {str(raw)[:400]}")

            def find(obj, keys, depth=0):
                if depth > 6: return None
                if isinstance(obj, dict):
                    for k in keys:
                        v = obj.get(k)
                        if v is not None:
                            try:
                                f = float(str(v).replace(",",""))
                                if f >= 0: return f
                            except: pass
                    for k, v in obj.items():
                        if isinstance(v, (dict,list)):
                            r = find(v, keys, depth+1)
                            if r is not None: return r
                if isinstance(obj, list):
                    for item in obj:
                        r = find(item, keys, depth+1)
                        if r is not None: return r
                return None

            bal = find(raw, ["availableMargin","available","free","equity",
                             "availableBalance","crossAvailableBalance","walletBalance"])
            if bal is not None and bal >= 0:
                log.info(f"Balance: {bal:.4f} USDT")
                return bal
            log.error(f"Balance no encontrado. Raw={str(raw)[:300]}")
            return 0.0
        except Exception as e:
            log.error(f"get_balance: {e}", exc_info=True)
            return 0.0

    def _parse_pos(self, data) -> list:
        if data is None: return []
        items = data if isinstance(data,list) else [data] if isinstance(data,dict) else []
        result = []
        for p in items:
            if not isinstance(p,dict): continue
            try:
                amt = float(p.get("positionAmt",p.get("positionAmount",p.get("size",0))))
                if abs(amt) > 0: result.append(p)
            except: continue
        return result

    async def get_position(self, symbol) -> Optional[dict]:
        try:
            d = await self._get("/openApi/swap/v2/user/positions",{"symbol":symbol})
            ps = self._parse_pos(d.get("data"))
            return ps[0] if ps else None
        except Exception as e:
            log.warning(f"get_position {symbol}: {e}")
            return None

    async def get_all_positions(self) -> list:
        try:
            d = await self._get("/openApi/swap/v2/user/positions")
            return self._parse_pos(d.get("data"))
        except Exception as e:
            log.warning(f"get_all_positions: {e}")
            return []

    async def set_leverage(self, symbol, leverage) -> bool:
        # ONE-WAY: solo un lado
        try:
            await self._post("/openApi/swap/v2/trade/leverage",
                             {"symbol":symbol,"side":"LONG","leverage":str(leverage)})
            log.info(f"Leverage {symbol} {leverage}x OK")
            return True
        except Exception as e:
            log.warning(f"Leverage {symbol}: {e}")
            return False

    async def open_long(self, symbol, qty, sl, tp) -> dict:
        """ONE-WAY LONG: side=BUY, positionSide=BOTH"""
        p = {
            "symbol":       symbol,
            "side":         "BUY",
            "positionSide": "BOTH",
            "type":         "MARKET",
            "quantity":     str(qty),
            "stopLoss":     str(round(sl, 6)),
            "takeProfit":   str(round(tp, 6)),
        }
        log.info(f"OPEN LONG {symbol} qty={qty} sl={sl:.6f} tp={tp:.6f}")
        r = await self._post("/openApi/swap/v2/trade/order", p)
        log.info(f"RESULT → {r}")
        return r

    async def open_short(self, symbol, qty, sl, tp) -> dict:
        """ONE-WAY SHORT: side=SELL, positionSide=BOTH"""
        p = {
            "symbol":       symbol,
            "side":         "SELL",
            "positionSide": "BOTH",
            "type":         "MARKET",
            "quantity":     str(qty),
            "stopLoss":     str(round(sl, 6)),
            "takeProfit":   str(round(tp, 6)),
        }
        log.info(f"OPEN SHORT {symbol} qty={qty} sl={sl:.6f} tp={tp:.6f}")
        r = await self._post("/openApi/swap/v2/trade/order", p)
        log.info(f"RESULT → {r}")
        return r

    async def close_all(self, symbol) -> dict:
        """Cierra toda la posición ONE-WAY."""
        pos = await self.get_position(symbol)
        if not pos: return {}
        amt = float(pos.get("positionAmt", pos.get("size", 0)))
        if amt == 0: return {}
        # Si long (amt>0) → SELL para cerrar; si short (amt<0) → BUY para cerrar
        close_side = "SELL" if amt > 0 else "BUY"
        p = {
            "symbol":       symbol,
            "side":         close_side,
            "positionSide": "BOTH",
            "type":         "MARKET",
            "quantity":     str(abs(amt)),
            "reduceOnly":   "true",
        }
        log.info(f"CLOSE {symbol} side={close_side} qty={abs(amt)}")
        return await self._post("/openApi/swap/v2/trade/order", p)

    async def close_half(self, symbol, pos) -> dict:
        amt  = float(pos.get("positionAmt", pos.get("size", 0)))
        half = round(abs(amt) / 2, 3)
        if half < 0.001: return {}
        close_side = "SELL" if amt > 0 else "BUY"
        p = {
            "symbol":       symbol,
            "side":         close_side,
            "positionSide": "BOTH",
            "type":         "MARKET",
            "quantity":     str(half),
            "reduceOnly":   "true",
        }
        log.info(f"CLOSE HALF {symbol} side={close_side} qty={half}")
        return await self._post("/openApi/swap/v2/trade/order", p)


# ══════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════
async def tg(text: str):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id":TELEGRAM_CHAT_ID,"text":text[:4000],"parse_mode":"Markdown"}
            )
    except Exception as e:
        log.error(f"Telegram: {e}")


# ══════════════════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════════════════
def ema(v, p):
    v = np.asarray(v,float); k = 2/(p+1)
    r = np.zeros(len(v)); r[0] = v[0]
    for i in range(1,len(v)): r[i] = v[i]*k + r[i-1]*(1-k)
    return r

def hma(v, p):
    return ema(2*ema(v,max(p//2,1))-ema(v,p), max(int(np.sqrt(p)),1))

def sma(v, p):
    return np.convolve(np.asarray(v,float), np.ones(p)/p, mode="same")

def stoch_s(src, p):
    src = np.asarray(src,float); r = np.zeros(len(src))
    for i in range(p-1,len(src)):
        w = src[i-p+1:i+1]; lo,hi = w.min(),w.max()
        r[i] = (src[i]-lo)/(hi-lo+1e-10)
    return r

def stc_ind(c):
    return ema(stoch_s(ema(c,23)-ema(c,50), 10), 3)

def calc_atr(h,l,c,p=14):
    h,l,c = map(lambda x:np.asarray(x,float),[h,l,c])
    tr = np.maximum(h-l,np.maximum(np.abs(h-np.roll(c,1)),np.abs(l-np.roll(c,1))))
    r = np.zeros(len(tr)); r[0]=tr[0]
    for i in range(1,len(tr)): r[i]=(r[i-1]*(p-1)+tr[i])/p
    return r

def calc_vwap(h,l,c,v):
    tp=(np.asarray(h,float)+np.asarray(l,float)+np.asarray(c,float))/3
    return np.cumsum(tp*np.asarray(v,float))/(np.cumsum(np.asarray(v,float))+1e-10)

def calc_poc(closes,volumes,lookback):
    n=min(lookback,len(closes))
    v=np.asarray(volumes[-n:],float)
    return float(np.asarray(closes[-n:],float)[int(np.argmax(v))])

def calc_adx(h,l,c,p=14):
    h,l,c=map(lambda x:np.asarray(x,float),[h,l,c])
    ph,pl,pc=np.roll(h,1),np.roll(l,1),np.roll(c,1)
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    dmp=np.where((h-ph)>(pl-l),np.maximum(h-ph,0),0).astype(float)
    dmm=np.where((pl-l)>(h-ph),np.maximum(pl-l,0),0).astype(float)
    aw=np.zeros(len(tr)); dp=np.zeros(len(tr)); dm=np.zeros(len(tr))
    if p<len(tr):
        aw[p]=np.sum(tr[1:p+1]); dp[p]=np.sum(dmp[1:p+1]); dm[p]=np.sum(dmm[1:p+1])
    for i in range(p+1,len(tr)):
        aw[i]=aw[i-1]-aw[i-1]/p+tr[i]
        dp[i]=dp[i-1]-dp[i-1]/p+dmp[i]
        dm[i]=dm[i-1]-dm[i-1]/p+dmm[i]
    dip=100*dp/(aw+1e-10); dim=100*dm/(aw+1e-10)
    dx=100*np.abs(dip-dim)/(dip+dim+1e-10)
    adx=np.zeros(len(dx))
    if 2*p<len(dx): adx[2*p]=np.mean(dx[p:2*p+1])
    for i in range(2*p+1,len(dx)): adx[i]=(adx[i-1]*(p-1)+dx[i])/p
    return adx

def calc_slope(closes,p=7):
    e=ema(closes,p); a=calc_atr(closes,closes,closes,p)
    s=np.zeros(len(e))
    for i in range(1,len(e)): s[i]=((e[i]-e[i-1])/(a[i]+1e-10))*100
    return s

def calc_rvol(volumes,p=50):
    v=np.asarray(volumes,float)
    return v/(sma(v,p)+1e-10)

def pivot_hi(h,n):
    h=np.asarray(h,float); r=np.full(len(h),np.nan)
    for i in range(n,len(h)-n):
        if h[i]==h[i-n:i+n+1].max(): r[i]=h[i]
    return r

def pivot_lo(l,n):
    l=np.asarray(l,float); r=np.full(len(l),np.nan)
    for i in range(n,len(l)-n):
        if l[i]==l[i-n:i+n+1].min(): r[i]=l[i]
    return r

def vol24h(candles):
    last=candles[-96:] if len(candles)>=96 else candles
    return sum(c["close"]*c["volume"] for c in last)


# ══════════════════════════════════════════════════════════════════
# ANALYZE
# ══════════════════════════════════════════════════════════════════
@dataclass
class CoinResult:
    symbol:     str
    direction:  str
    score:      int
    entry:      float
    sl:         float
    tp:         float
    tp_half:    float
    vol_usd:    float
    atr_val:    float
    signals:    list  = field(default_factory=list)
    change_24h: float = 0.0
    funding:    float = 0.0
    slope:      float = 0.0
    adx:        float = 0.0
    rvol:       float = 0.0

def analyze(ticker, candles, candles_1h, funding) -> Optional[CoinResult]:
    sym = ticker["symbol"]
    if len(candles) < 80: return None

    closes  = np.array([c["close"]  for c in candles], float)
    highs   = np.array([c["high"]   for c in candles], float)
    lows    = np.array([c["low"]    for c in candles], float)
    volumes = np.array([c["volume"] for c in candles], float)

    if closes[-1] <= 0: return None
    vusd = vol24h(candles)
    if vusd < MIN_VOL_USDT: return None

    atr_arr  = calc_atr(highs,lows,closes)
    atr_now  = float(atr_arr[-1])
    vwap_now = float(calc_vwap(highs,lows,closes,volumes)[-1])
    poc      = calc_poc(closes,volumes,POC_LOOKBACK)
    stc_arr  = stc_ind(closes)
    adx_arr  = calc_adx(highs,lows,closes)
    adx_now  = float(adx_arr[-1])
    slp_arr  = calc_slope(closes,EMA_FAST)
    slp_now  = float(slp_arr[-1])
    rvol_arr = calc_rvol(volumes,50)
    rvol_now = float(rvol_arr[-1])
    e_fast   = ema(closes,EMA_FAST)
    e_slow   = ema(closes,EMA_SLOW)
    h50      = hma(closes,50)

    ph_v=pivot_hi(highs,4); pl_v=pivot_lo(lows,4)
    vph=ph_v[~np.isnan(ph_v)]; vpl=pl_v[~np.isnan(pl_v)]
    peak   = float(vph[-1]) if len(vph)>0 else float(highs[-1])
    valley = float(vpl[-1]) if len(vpl)>0 else float(lows[-1])

    htf_bull=htf_bear=False
    if len(candles_1h)>=20:
        c1=np.array([c["close"] for c in candles_1h],float)
        e7_1=ema(c1,EMA_FAST); e17_1=ema(c1,EMA_SLOW); h50_1=hma(c1,50)
        htf_bull=bool(c1[-1]>h50_1[-1] and e7_1[-1]>e17_1[-1])
        htf_bear=bool(c1[-1]<h50_1[-1] and e7_1[-1]<e17_1[-1])

    i=-1; cn=float(closes[i]); hi=float(highs[i]); lo=float(lows[i])

    dist_poc = abs(cn-poc)>(atr_now*1.5)
    cond_vol = rvol_now>RVOL_MIN
    stc_up   = stc_arr[i]>stc_arr[i-1]
    stc_down = stc_arr[i]<stc_arr[i-1]
    adx_ok   = adx_now<ADX_MAX

    long_cond  = (lo<valley and cn<vwap_now and slp_now>SLOPE_MIN
                  and stc_up and adx_ok and dist_poc and cond_vol)
    short_cond = (hi>peak and cn>vwap_now and slp_now<-SLOPE_MIN
                  and stc_down and adx_ok and dist_poc and cond_vol)

    score=0; signals=[]; direction="NEUTRAL"
    hull_bull=cn>float(h50[-1]); hull_bear=not hull_bull

    if long_cond:
        direction="LONG"; score=85; signals=["V45🟢"]
        if htf_bull:              score+=10; signals.append("1H🟢")
        if e_fast[i]>e_slow[i]:  score+=5;  signals.append("EMA✅")
    elif short_cond:
        direction="SHORT"; score=85; signals=["V45🔴"]
        if htf_bear:              score+=10; signals.append("1H🔴")
        if e_fast[i]<e_slow[i]:  score+=5;  signals.append("EMA✅")
    else:
        direction="LONG" if hull_bull else "SHORT"
        if hull_bull or hull_bear:           score+=20; signals.append("Hull✅")
        if (hull_bull and e_fast[i]>e_slow[i]) or (hull_bear and e_fast[i]<e_slow[i]):
            score+=15; signals.append("EMA✅")
        if cond_vol:  score+=15; signals.append(f"RVOL{rvol_now:.1f}✅")
        else:         signals.append(f"RVOL{rvol_now:.1f}·")
        if (stc_up and hull_bull) or (stc_down and hull_bear):
            score+=12; signals.append("STC✅")
        if adx_ok:    score+=8;  signals.append(f"ADX{adx_now:.0f}✅")
        else:         signals.append(f"ADX{adx_now:.0f}⚠️")
        if dist_poc:  score+=8;  signals.append("POC✅")
        if (htf_bull and hull_bull) or (htf_bear and hull_bear):
            score+=12; signals.append("1H✅")
        score=min(score,84)

    signals+=[f"Slp{slp_now:+.0f}",f"R{rvol_now:.1f}",f"ADX{adx_now:.0f}"]
    score=min(max(score,0),100)

    log.info(f"{sym}: score={score} dir={direction} "
             f"long={long_cond} short={short_cond} "
             f"slp={slp_now:.1f} adx={adx_now:.1f} "
             f"rvol={rvol_now:.2f} poc={dist_poc} vwap_ok="
             f"{cn<vwap_now if direction=='LONG' else cn>vwap_now}")

    if long_cond:
        sl=lo-atr_now*ATR_SL_MULT; risk=abs(cn-sl)
        tp=cn+risk*RR_RATIO; tp_half=cn+risk
    elif short_cond:
        sl=hi+atr_now*ATR_SL_MULT; risk=abs(sl-cn)
        tp=cn-risk*RR_RATIO; tp_half=cn-risk
    else:
        sl_d=atr_now*ATR_SL_MULT
        sl=cn-sl_d if direction=="LONG" else cn+sl_d
        risk=abs(cn-sl)
        tp=cn+risk*RR_RATIO if direction=="LONG" else cn-risk*RR_RATIO
        tp_half=cn+risk if direction=="LONG" else cn-risk

    return CoinResult(symbol=sym, direction=direction, score=score,
                      entry=cn, sl=sl, tp=tp, tp_half=tp_half,
                      vol_usd=vusd, atr_val=atr_now, signals=signals,
                      change_24h=ticker.get("change_24h",0),
                      funding=funding, slope=slp_now, adx=adx_now, rvol=rvol_now)


# ══════════════════════════════════════════════════════════════════
# RISK
# ══════════════════════════════════════════════════════════════════
def calc_qty(balance, entry, sl) -> float:
    dist=abs(entry-sl)
    if dist<1e-10: return 0.0
    qty_risk=(balance*MAX_RISK_PCT/100)/dist
    qty_max=MAX_POS_USDT/max(entry,1e-10)
    qty=min(qty_risk,qty_max)
    if   entry>=1000: qty=round(qty,4)
    elif entry>=100:  qty=round(qty,3)
    elif entry>=10:   qty=round(qty,2)
    elif entry>=1:    qty=round(qty,1)
    else:             qty=round(qty,0)
    qty=max(qty,0.001)
    log.info(f"qty: bal={balance:.2f} entry={entry:.4f} dist={dist:.6f} → {qty}")
    return qty

def is_blackout():
    return BLACKOUT_START<=datetime.now(timezone.utc).hour<BLACKOUT_END


# ══════════════════════════════════════════════════════════════════
# STATE
# ══════════════════════════════════════════════════════════════════
exchange     = BingXClient()
watchlist:   list[CoinResult] = []
last_dir:    dict[str,str]    = {}
half_closed: set[str]         = set()


# ══════════════════════════════════════════════════════════════════
# DIAGNOSTICS
# ══════════════════════════════════════════════════════════════════
async def run_diagnostics():
    lines=["🔧 *DIAGNÓSTICO V45 ONE-WAY*\n━━━━━━━━━━━━━━━━━━━━\n"]
    try:
        d=await exchange._get("/openApi/swap/v2/user/balance")
        lines.append("✅ *API Key:* OK")
        raw=d.get("data",{})
        lines.append(f"📦 *RAW:* `{str(raw)[:200]}`\n")
    except Exception as e:
        lines.append(f"❌ *API Error:* `{e}`")
        await tg("\n".join(lines)); return False

    balance=await exchange.get_balance()
    if balance>0: lines.append(f"✅ *Balance:* `{balance:.4f} USDT`")
    else:         lines.append("❌ *Balance=0* — transfiere a BingX Futuros")

    try:
        positions=await exchange.get_all_positions()
        n=len(positions)
        icon="🔴" if n>=MAX_POSITIONS else "✅"
        lines.append(f"{icon} *Posiciones:* `{n}/{MAX_POSITIONS}`")
        if n>=MAX_POSITIONS: lines.append("⛔ BLOQUEADO — sube MAX_POSITIONS")
        total_unr=0.0
        for pos in positions:
            sym=pos.get("symbol","?")
            amt=float(pos.get("positionAmt",0))
            avg=float(pos.get("avgPrice",pos.get("entryPrice",0)))
            cur=float(pos.get("markPrice",pos.get("currentPrice",0)))
            unr=float(pos.get("unrealizedProfit",pos.get("unRealizedProfit",0)))
            total_unr+=unr
            direction="LONG" if amt>0 else "SHORT"
            pnl=((cur-avg)/avg*100) if amt>0 else ((avg-cur)/avg*100)
            e2="🟢" if unr>=0 else "🔴"
            lines.append(f"  {e2} `{sym}` {direction} {pnl:+.1f}% ({unr:+.2f}$)")
        if positions: lines.append(f"  PnL total: `{total_unr:+.2f} USDT`")
    except Exception as e:
        lines.append(f"❌ *Posiciones:* `{e}`")

    try:
        k=await exchange.get_klines("BTC-USDT","15m",5)
        lines.append(f"✅ *Klines BTC:* `{k[-1]['close']:.0f}`")
    except Exception as e:
        lines.append(f"❌ *Klines:* `{e}`")

    try:
        ok=await exchange.set_leverage("BTC-USDT",LEVERAGE)
        lines.append(f"{'✅' if ok else '⚠️'} *Leverage {LEVERAGE}x*")
    except Exception as e:
        lines.append(f"❌ *Leverage:* `{e}`")

    lines.append(f"\n⚙️ TF:`{TIMEFRAME}` Score:`{SCORE_ENTRY}` MaxPos:`{MAX_POSITIONS}`")
    lines.append(f"MaxUSDT:`{MAX_POS_USDT}` Riesgo:`{MAX_RISK_PCT}%` Lev:`{LEVERAGE}x`")
    lines.append(f"RVOL≥`{RVOL_MIN}` Slope≥`±{SLOPE_MIN}` ADX<`{ADX_MAX}` TP=`{RR_RATIO}R`")
    lines.append(f"*Modo: ONE-WAY (positionSide=BOTH)*")

    if balance>0:
        try:
            k=await exchange.get_klines("BTC-USDT","15m",5)
            entry=k[-1]["close"]
            qty_sim=calc_qty(balance,entry,entry-entry*0.01)
            lines.append(f"\n📐 Sim BTC entry=`{entry:.0f}` → qty=`{qty_sim}`")
        except: pass

    lines.append(f"\n{'✅ *Listo para operar*' if balance>0 else '❌ *Sin balance*'}")
    await tg("\n".join(lines))
    return balance>0


# ══════════════════════════════════════════════════════════════════
# OPEN TRADE
# ══════════════════════════════════════════════════════════════════
async def open_trade(cr: CoinResult) -> bool:
    sym=cr.symbol
    try:
        log.info(f"⚡ INTENTANDO {cr.direction} {sym} score={cr.score}")

        if last_dir.get(sym)==cr.direction:
            log.info(f"  {sym}: señal ya activa"); return False

        pos=await exchange.get_position(sym)
        if pos:
            log.info(f"  {sym}: ya tiene posición"); return False

        balance=await exchange.get_balance()
        if balance<5:
            await tg(f"⚠️ Balance bajo: `{balance:.2f} USDT`"); return False

        qty=calc_qty(balance,cr.entry,cr.sl)
        if qty<=0:
            log.warning(f"  {sym}: qty=0"); return False

        await exchange.set_leverage(sym,LEVERAGE)

        # ONE-WAY MODE — la clave del fix
        if cr.direction=="LONG":
            await exchange.open_long(sym, qty, cr.sl, cr.tp)
        else:
            await exchange.open_short(sym, qty, cr.sl, cr.tp)

        risk_usd=abs(cr.entry-cr.sl)*qty
        pos_value=cr.entry*qty
        emoji="🟢" if cr.direction=="LONG" else "🔴"
        await tg(
            f"{emoji} *{cr.direction} ABIERTO — V45*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Par:    `{sym}`\n"
            f"Entry:  `{cr.entry:.6f}`\n"
            f"SL:     `{cr.sl:.6f}`\n"
            f"TP:     `{cr.tp:.6f}` *({RR_RATIO}R)*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Qty:    `{qty}` | Lev:`{LEVERAGE}x`\n"
            f"Valor:  `≈{pos_value:.2f} USDT`\n"
            f"Riesgo: `≈{risk_usd:.2f} USDT`\n"
            f"Score:  `{cr.score}/100`\n"
            f"Slp:`{cr.slope:+.0f}` ADX:`{cr.adx:.0f}` RVOL:`{cr.rvol:.1f}x`\n"
            f"{' '.join(cr.signals[:6])}"
        )
        last_dir[sym]=cr.direction
        log.info(f"✅ TRADE ABIERTO {cr.direction} {sym} qty={qty} val={pos_value:.2f}$")
        return True

    except Exception as e:
        log.error(f"open_trade {sym}: {e}", exc_info=True)
        await tg(f"❌ *Error orden* `{sym}`:\n`{str(e)[:300]}`")
        return False


# ══════════════════════════════════════════════════════════════════
# MANAGE POSITIONS
# ══════════════════════════════════════════════════════════════════
async def manage_positions():
    try:
        positions=await exchange.get_all_positions()
        if not positions: return
        for pos in positions:
            sym=pos.get("symbol","")
            amt=float(pos.get("positionAmt",pos.get("size",0)))
            avg=float(pos.get("avgPrice",   pos.get("entryPrice",0)))
            cur=float(pos.get("markPrice",  pos.get("currentPrice",0)))
            unr=float(pos.get("unrealizedProfit",pos.get("unRealizedProfit",0)))
            if avg<=0 or cur<=0: continue
            is_long=amt>0
            pnl_pct=((cur-avg)/avg*100) if is_long else ((avg-cur)/avg*100)
            direction="LONG" if is_long else "SHORT"
            log.info(f"POS {sym} {direction} pnl={pnl_pct:+.2f}% unreal={unr:+.2f}")
            if sym not in half_closed and pnl_pct>=0.6:
                try:
                    await exchange.close_half(sym,pos)
                    half_closed.add(sym)
                    await tg(f"🔒 *Cierre 50%* `{sym}` {direction}\n"
                             f"PnL: `+{pnl_pct:.2f}%` | `+{unr:.2f} USDT`\n"
                             f"Resto hasta {RR_RATIO}R")
                except Exception as e:
                    log.error(f"close_half {sym}: {e}")
    except Exception as e:
        log.error(f"manage_positions: {e}")


# ══════════════════════════════════════════════════════════════════
# SCANNER
# ══════════════════════════════════════════════════════════════════
async def scanner_loop():
    global watchlist
    while True:
        try:
            if is_blackout():
                await asyncio.sleep(SCAN_INTERVAL); continue

            log.info("🔍 Escaneando V45 ONE-WAY...")
            tickers=await exchange.get_tickers()
            if not tickers:
                await tg("⚠️ Sin tickers.")
                await asyncio.sleep(SCAN_INTERVAL); continue

            syms=[t["symbol"] for t in tickers]
            lim=max(200,POC_LOOKBACK+80)
            r15,r1h,rfr=await asyncio.gather(
                asyncio.gather(*[exchange.get_klines(s,TIMEFRAME,lim) for s in syms],return_exceptions=True),
                asyncio.gather(*[exchange.get_klines(s,TF_HIGH,  100) for s in syms],return_exceptions=True),
                asyncio.gather(*[exchange.get_funding_rate(s)         for s in syms],return_exceptions=True),
            )

            results=[]
            for t,c15,c1h,fr in zip(tickers,r15,r1h,rfr):
                if isinstance(c15,Exception): continue
                cr=analyze(t,c15,
                    c1h if not isinstance(c1h,Exception) else [],
                    fr  if not isinstance(fr, Exception) else 0.0)
                if cr: results.append(cr)

            results.sort(key=lambda x:x.score,reverse=True)
            top=results[:SCAN_TOP_N]

            wl=[r for r in top if r.score>=SCORE_ENTRY and r.direction!="NEUTRAL"]
            watchlist=wl if wl else top[:5]
            log.info(f"Watchlist: {[(r.symbol,r.score,r.direction) for r in watchlist]}")

            lines=[f"🔍 *V45 ONE-WAY — {len(top)} coins*\n"]
            for n,r in enumerate(top,1):
                e="🟢" if r.direction=="LONG" else "🔴"
                bar="█"*(r.score//10)+"░"*(10-r.score//10)
                tag=" ⚡*ENTRA*" if r.score>=SCORE_ENTRY else ""
                lines.append(
                    f"*#{n}* {e} `{r.symbol}` `{r.score}/100`{tag}\n"
                    f"`{bar}`\n"
                    f"Vol:`${r.vol_usd/1e6:.0f}M` RVOL:`{r.rvol:.1f}x`"
                    f" ADX:`{r.adx:.0f}` Slp:`{r.slope:+.0f}`\n"
                    f"{' '.join(r.signals[:5])}\n"
                )
            lines.append(f"\n🎯 *Watchlist ({len(watchlist)}):*")
            for r in watchlist:
                e="🟢" if r.direction=="LONG" else "🔴"
                lines.append(f"  {e} `{r.symbol}` `{r.score}` → {r.direction}")
            await tg("\n".join(lines)[:3900])

        except Exception as e:
            log.error(f"Scanner: {e}",exc_info=True)
            await tg(f"⚠️ *Error escáner:* `{str(e)[:200]}`")

        await asyncio.sleep(SCAN_INTERVAL)


# ══════════════════════════════════════════════════════════════════
# TRADING LOOP
# ══════════════════════════════════════════════════════════════════
async def trading_loop():
    log.info("Trading: esperando primer escaneo (50s)...")
    await asyncio.sleep(50)
    while True:
        try:
            if is_blackout():
                await asyncio.sleep(60); continue

            await manage_positions()

            open_pos=await exchange.get_all_positions()
            n_open=len(open_pos)
            open_syms={p.get("symbol","") for p in open_pos}
            log.info(f"Trading: {n_open}/{MAX_POSITIONS} pos | watchlist={len(watchlist)}")

            if n_open<MAX_POSITIONS and watchlist:
                for cr in list(watchlist):
                    if n_open>=MAX_POSITIONS: break
                    if cr.symbol in open_syms: continue
                    if cr.score<SCORE_ENTRY:
                        log.info(f"  {cr.symbol}: {cr.score}<{SCORE_ENTRY}"); continue
                    opened=await open_trade(cr)
                    if opened:
                        n_open+=1; open_syms.add(cr.symbol)
                    await asyncio.sleep(3)

        except Exception as e:
            log.error(f"Trading loop: {e}",exc_info=True)
        await asyncio.sleep(60)


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
async def main():
    log.info("🚀 Sniper Bot V45 ONE-WAY MODE — Arrancando...")
    await tg(f"🔄 *V45 ONE-WAY arrancando...*\nTF:`{TIMEFRAME}` Lev:`{LEVERAGE}x`")

    ok=await run_diagnostics()
    if not ok:
        await tg("⛔ Sin balance en Futuros\n1. BingX → Activos → Futuros → Transferir\n2. Railway → Redeploy")
        while True:
            await asyncio.sleep(300)
            bal=await exchange.get_balance()
            if bal>5:
                await tg(f"✅ Balance: `{bal:.2f} USDT` — arrancando!")
                break

    await asyncio.gather(scanner_loop(), trading_loop())

if __name__ == "__main__":
    asyncio.run(main())
