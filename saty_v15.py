"""
╔══════════════════════════════════════════════════════════════╗
║           SATY ELITE v15 — Proyecto independiente           ║
║  6 módulos · Brain opcional · BingX · Railway · Telegram    ║
╠══════════════════════════════════════════════════════════════╣
║  MEJORAS v15 vs v14:                                        ║
║  ✅ MODO SEÑAL MANUAL si no hay fondos (Telegram detallado) ║
║  ✅ Escanea TODOS los pares activos (hasta 500)             ║
║  ✅ Brain opcional → no crashea si no existe brain.py       ║
║  ✅ scipy opcional → no crashea si no está instalado        ║
║  ✅ ADX mínimo más estricto (20 vs 15) → menos ruido       ║
║  ✅ R/R mínimo 1.5 → solo entradas con ventaja real         ║
║  ✅ TP más amplio: TP1=1.2×ATR TP2=2.5×ATR TP3=4.5×ATR    ║
║  ✅ Cooldown doble tras rachas perdedoras                   ║
║  ✅ Variables de entorno limpias (strip de comillas)         ║
║  ✅ Señales manuales con pasos exactos para BingX           ║
║  ✅ Deduplicación de señales (no spam)                      ║
╠══════════════════════════════════════════════════════════════╣
║  MÓDULOS (Pine Script → Python):                            ║
║  1️⃣  ConfPRO      Bollinger + Squeeze + Volumen + EMA       ║
║  2️⃣  BollingerH   W/M · %B div · Breakout · Walking        ║
║  3️⃣  SMC          Order Blocks · Sweep · BOS multi-TF      ║
║  4️⃣  Powertrend   Volume Range Filter (wbburgin)           ║
║  5️⃣  BBPCT        Bollinger % · Percentrank volatilidad    ║
║  6️⃣  RSI+         Divergencias · OB/OS · Nivel 50          ║
╠══════════════════════════════════════════════════════════════╣
║  Variables Railway (añadir en dashboard):                   ║
║  BINGX_API_KEY        BINGX_API_SECRET                     ║
║  TELEGRAM_BOT_TOKEN   TELEGRAM_CHAT_ID                      ║
║  FIXED_USDT(8)        LEVERAGE(10)                          ║
║  MAX_OPEN_TRADES(10)  MIN_SCORE(5)   MIN_MODULES(2)        ║
║  COOLDOWN_MIN(30)     MAX_DRAWDOWN(15) DAILY_LOSS_LIMIT(8) ║
║  BTC_FILTER(true)     TOP_N_SYMBOLS(500)                   ║
║  MIN_VOLUME_USDT(30000)  MIN_RR(1.5)                       ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, sys, time, logging, csv
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

import requests, ccxt
import pandas as pd
import numpy as np

# ── scipy opcional ────────────────────────────────────────
try:
    from scipy.stats import linregress as _linreg
    def linregress(x, y): return _linreg(x, y)
except ImportError:
    def linregress(x, y):
        n=len(x); sx=sum(x); sy=sum(y)
        sxy=sum(xi*yi for xi,yi in zip(x,y)); sxx=sum(xi**2 for xi in x)
        sl=(n*sxy-sx*sy)/(n*sxx-sx**2+1e-12); ic=(sy-sl*sx)/n
        return sl,ic,None,None,None

# ── Brain opcional ────────────────────────────────────────
try:
    from brain import brain, on_trade_closed, check_entry
    HAS_BRAIN=True
except ImportError:
    HAS_BRAIN=False
    class _FB:
        class data: total_trades=0
        def get_effective_min_score(self,c):
            return int(os.environ.get("MIN_SCORE","5").strip().strip('"').strip("'"))
        def telegram_summary_line(self): return "🧠 Brain: no instalado"
        def telegram_report(self): return "🧠 Brain: no instalado"
        def score_distribution_bar(self): return ""
    brain=_FB()
    def on_trade_closed(*a,**k): pass
    def check_entry(score,mods,size): return True,size,""

# ══════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log=logging.getLogger("saty_v15")

# ══════════════════════════════════════════════════════════
# HELPERS de ENV (elimina comillas añadidas por Railway)
# ══════════════════════════════════════════════════════════
def _e(key,default=""):
    v=os.environ.get(key,default)
    return str(v).strip().strip('"').strip("'")

def _ef(key,default):  return float(_e(key,str(default)))
def _ei(key,default):  return int(_e(key,str(default)))
def _eb(key,default):  return _e(key,str(default)).lower()=="true"

# ══════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════
API_KEY    = _e("BINGX_API_KEY")
API_SECRET = _e("BINGX_API_SECRET")
TF         = _e("TIMEFRAME","5m")
HTF1       = _e("HTF1","15m")
HTF2       = _e("HTF2","1h")
POLL_SECS  = _ei("POLL_SECONDS",60)

def _tg_token():   return _e("TELEGRAM_BOT_TOKEN") or _e("TG_TOKEN")
def _tg_chat_id(): return _e("TELEGRAM_CHAT_ID")   or _e("TG_CHAT_ID")

BLACKLIST:List[str]=[s.strip() for s in _e("BLACKLIST").split(",") if s.strip()]

FIXED_USDT      = _ef("FIXED_USDT",8.0)
MAX_OPEN_TRADES = _ei("MAX_OPEN_TRADES",10)
MIN_SCORE       = _ei("MIN_SCORE",5)
MIN_MODULES     = _ei("MIN_MODULES",2)
CB_DD           = _ef("MAX_DRAWDOWN",15.0)
DAILY_LOSS_LIM  = _ef("DAILY_LOSS_LIMIT",8.0)
COOLDOWN_MIN    = _ei("COOLDOWN_MIN",30)
MAX_SPREAD_PCT  = _ef("MAX_SPREAD_PCT",0.5)      # v15: más estricto
MIN_VOL_USDT    = _ef("MIN_VOLUME_USDT",30000)   # v15: más pares
TOP_N           = _ei("TOP_N_SYMBOLS",500)        # v15: todos los pares
BTC_FILTER      = _eb("BTC_FILTER",True)
LEVERAGE        = _ef("LEVERAGE",10)
MIN_RR          = _ef("MIN_RR",1.5)              # v15: R/R mínimo

# ── Indicadores ──────────────────────────────────────────
BB_LEN=20; BB_MULT=2.0; VOL_MULT=1.5
SQZ_LEN=20; TREND_LEN=3; PIVOT_L=5; PIVOT_R=2
SMC_LB=10; PT_LEN=200; PT_ADX_LEN=14; PT_VWMA_LEN=200
BBPCT_LB=750; RSI_LEN=14; RSI_OB=70; RSI_OS=30
FAST=9; SLOW=21; BIAS=48; MA200=200
ADX_LEN=14; ADX_MIN=20; ATR_LEN=14   # v15: ADX_MIN=20 (más estricto)

# ── TP/SL v15: R/R mejorado ─────────────────────────────
TP1_M=1.2; TP2_M=2.5; TP3_M=4.5; SL_M=1.0
MAX_CONSEC=3; HEDGE_MODE=False
CSV_PATH="saty_v15_trades.csv"

# ── Balance mínimo para auto-trading ─────────────────────
MIN_BAL_AUTO=1.0  # USDT mínimo para operar real

# ══════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════
@dataclass
class MR:
    name:str; direction:str; score:int; signals:List[str]; reason:str=""

@dataclass
class TradeState:
    symbol:str=""; side:str=""; base:str=""; entry_price:float=0.0
    tp1:float=0.0; tp2:float=0.0; tp3:float=0.0; sl:float=0.0
    sl_be:bool=False; tp1_hit:bool=False; tp2_hit:bool=False
    peak:float=0.0; stall:int=0; phase:str="normal"
    max_pct:float=0.0; score:int=0; modules:str=""; signals:str=""
    entry_time:str=""; contracts:float=0.0; atr:float=0.0
    rsi:float=0.0; adx:float=0.0; rr:float=0.0
    trail_h:float=0.0; trail_l:float=0.0

@dataclass
class BotState:
    wins:int=0; losses:int=0; gross_p:float=0.0; gross_l:float=0.0
    consec:int=0; peak_eq:float=0.0; total_pnl:float=0.0; daily_pnl:float=0.0
    daily_reset:float=0.0; last_hb:float=0.0
    trades:Dict[str,TradeState]=field(default_factory=dict)
    cooldowns:Dict[str,float]=field(default_factory=dict)
    btc_bull:bool=True; btc_bear:bool=False; btc_rsi:float=50.0; btc_adx:float=0.0
    scan_n:int=0; sig_ok:int=0; sig_blk:int=0
    last_disc:List[dict]=field(default_factory=list)
    sent_sigs:set=field(default_factory=set)  # v15: anti-spam
    signal_only:bool=False                    # v15: modo sin fondos

    def open_n(self):  return len(self.trades)
    def bases(self):   return {t.base:t.side for t in self.trades.values()}
    def wr(self):      t=self.wins+self.losses; return (self.wins/t*100) if t else 0.0
    def pf(self):      return (self.gross_p/self.gross_l) if self.gross_l else 0.0
    def cb(self):
        if self.peak_eq<=0: return False
        return (self.peak_eq-(self.peak_eq+self.total_pnl))/self.peak_eq*100>=CB_DD
    def dl(self):
        if self.peak_eq<=0: return False
        return self.daily_pnl<0 and abs(self.daily_pnl)/self.peak_eq*100>=DAILY_LOSS_LIM
    def risk_m(self):  return 0.5 if self.consec>=MAX_CONSEC else 1.0
    def in_cd(self,s):
        mult=2.0 if self.consec>=MAX_CONSEC else 1.0
        return time.time()-self.cooldowns.get(s,0)<COOLDOWN_MIN*60*mult
    def set_cd(self,s): self.cooldowns[s]=time.time()
    def reset_daily(self):
        if time.time()-self.daily_reset>86400:
            self.daily_pnl=0.0; self.daily_reset=time.time()

state=BotState()

# ══════════════════════════════════════════════════════════
# CACHE OHLCV
# ══════════════════════════════════════════════════════════
_cache:Dict[str,Tuple[float,pd.DataFrame]]={}
CACHE_TTL=55

def fetch_df(ex,sym,tf,limit=500):
    key=f"{sym}|{tf}"; now=time.time()
    if key in _cache:
        ts,df=_cache[key]
        if now-ts<CACHE_TTL: return df
    raw=ex.fetch_ohlcv(sym,timeframe=tf,limit=limit)
    df=pd.DataFrame(raw,columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"]=pd.to_datetime(df["timestamp"],unit="ms")
    df.set_index("timestamp",inplace=True); df=df.astype(float)
    _cache[key]=(now,df); return df

def clear_cache(): _cache.clear()
def utcnow(): return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

# ══════════════════════════════════════════════════════════
# INDICADORES BASE
# ══════════════════════════════════════════════════════════
def ema(s,n):  return s.ewm(span=n,adjust=False).mean()
def sma(s,n):  return s.rolling(n).mean()

def atr(df,n=ATR_LEN):
    h,l,c=df["high"],df["low"],df["close"]
    tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(span=n,adjust=False).mean()

def rsi(s,n=RSI_LEN):
    d=s.diff(); g=d.clip(lower=0).ewm(span=n,adjust=False).mean()
    lo=(-d.clip(upper=0)).ewm(span=n,adjust=False).mean()
    return 100-(100/(1+g/lo.replace(0,np.nan)))

def adx(df,n=ADX_LEN):
    h,l=df["high"],df["low"]; up,dn=h.diff(),-l.diff()
    pdm=up.where((up>dn)&(up>0),0.0); mdm=dn.where((dn>up)&(dn>0),0.0)
    a=atr(df,n)
    dip=100*pdm.ewm(span=n,adjust=False).mean()/a
    dim=100*mdm.ewm(span=n,adjust=False).mean()/a
    dx=100*(dip-dim).abs()/(dip+dim).replace(0,np.nan)
    return dip,dim,dx.ewm(span=n,adjust=False).mean()

def bb(s,n=BB_LEN,m=BB_MULT):
    mid=sma(s,n); std=s.rolling(n).std()
    return mid,mid+m*std,mid-m*std

def sqz_mom(df,n=SQZ_LEN):
    c,h,l=df["close"],df["high"],df["low"]
    val=c-(h+l)/2; res=pd.Series(np.nan,index=df.index)
    for i in range(n-1,len(df)):
        y=val.iloc[i-n+1:i+1].values
        if len(y)==n and not np.isnan(y).any():
            x=np.arange(n); sl,ic,*_=linregress(x,y)
            res.iloc[i]=ic+sl*(n-1)
    return res

def pct_b(c,u,l): return (c-l)/(u-l).replace(0,np.nan)

def ph(s,L=PIVOT_L,R=PIVOT_R):
    r=pd.Series(np.nan,index=s.index)
    for i in range(L,len(s)-R):
        if s.iloc[i]>=s.iloc[i-L:i].max() and s.iloc[i]>=s.iloc[i+1:i+R+1].max():
            r.iloc[i]=s.iloc[i]
    return r

def pl(s,L=PIVOT_L,R=PIVOT_R):
    r=pd.Series(np.nan,index=s.index)
    for i in range(L,len(s)-R):
        if s.iloc[i]<=s.iloc[i-L:i].min() and s.iloc[i]<=s.iloc[i+1:i+R+1].min():
            r.iloc[i]=s.iloc[i]
    return r

def htf_bias(df):
    c=df["close"]; e48=ema(c,BIAS); e21=ema(c,SLOW)
    cl,e48v,e21v=float(c.iloc[-2]),float(e48.iloc[-2]),float(e21.iloc[-2])
    return (cl>e48v and e21v>e48v),(cl<e48v and e21v<e48v)

# ══════════════════════════════════════════════════════════
# MÓDULO 1 — CONFIRMACIÓN PRO
# ══════════════════════════════════════════════════════════
def mod_conf_pro(df,htf1b,htf1bear)->Tuple[MR,MR]:
    c=df["close"]; mid,u,l=bb(c)
    vs=df["volume"].fillna(1).replace(0,1); va=sma(vs,BB_LEN)
    sq=sqz_mom(df); ef=ema(c,FAST); es=ema(c,SLOW); _,_,ax=adx(df)
    i=len(df)-2
    cv,vv,vav=float(c.iloc[i]),float(vs.iloc[i]),float(va.iloc[i])
    sq_v=float(sq.iloc[i]) if not pd.isna(sq.iloc[i]) else 0.0
    uv,lv,efv,esv=float(u.iloc[i]),float(l.iloc[i]),float(ef.iloc[i]),float(es.iloc[i])
    axv=float(ax.iloc[i]) if not pd.isna(ax.iloc[i]) else 0.0
    vol_ok=vv>vav*VOL_MULT; adx_ok=axv>ADX_MIN
    pine_buy=cv<lv and vol_ok and sq_v>0 and efv>esv
    pine_sell=cv>uv and vol_ok and sq_v<0 and efv<esv
    ls=["BB_low"]*int(cv<lv)+["vol"]*int(vol_ok)+[f"sqz+{sq_v:.4f}"]*int(sq_v>0)+["EMA_up"]*int(efv>esv)+["HTF_b"]*int(htf1b)+["ADX"]*int(adx_ok)
    ss=["BB_hi"]*int(cv>uv)+["vol"]*int(vol_ok)+[f"sqz{sq_v:.4f}"]*int(sq_v<0)+["EMA_dn"]*int(efv<esv)+["HTF_bear"]*int(htf1bear)+["ADX"]*int(adx_ok)
    ld="long" if pine_buy else "none"; sd="short" if pine_sell else "none"
    return MR("ConfPRO",ld,len(ls) if pine_buy else max(0,len(ls)-2),ls), \
           MR("ConfPRO",sd,len(ss) if pine_sell else max(0,len(ss)-2),ss)

# ══════════════════════════════════════════════════════════
# MÓDULO 2 — BOLLINGER HUNTER PRO
# ══════════════════════════════════════════════════════════
def mod_bollinger_hunter(df,htf2b,htf2bear)->Tuple[MR,MR]:
    c,h,l,o=df["close"],df["high"],df["low"],df["open"]
    mid,u,lb=bb(c); bbw=((u-lb)/mid)*100; pb=pct_b(c,u,lb)
    va=sma(df["volume"].fillna(1).replace(0,1),BB_LEN)
    ef=ema(c,FAST); es=ema(c,SLOW); phs=ph(h); pls=pl(l)
    i=len(df)-2
    if i<50:
        e=MR("BollingerH","none",0,[]); return e,e
    cv,hv,lv,ov=float(c.iloc[i]),float(h.iloc[i]),float(l.iloc[i]),float(o.iloc[i])
    uv,lbv,mv=float(u.iloc[i]),float(lb.iloc[i]),float(mid.iloc[i])
    bbwv=float(bbw.iloc[i]); pbv=float(pb.iloc[i]) if not pd.isna(pb.iloc[i]) else 0.5
    vv,vav=float(df["volume"].iloc[i]),float(va.iloc[i])
    efv,esv=float(ef.iloc[i]),float(es.iloc[i])
    bbw_min=float(bbw.iloc[max(0,i-100):i+1].min())
    if bbwv<=bbw_min*1.10:
        e=MR("BollingerH","none",0,["squeeze"]); return e,e
    bbwp=float(bbw.iloc[i-1]); uvp=float(u.iloc[i-1]); lbvp=float(lb.iloc[i-1])
    vs=vv>vav*VOL_MULT
    bo_up=cv>uv and bbwv>bbwp and uv>uvp and lbv<lbvp and vs
    bo_dn=cv<lbv and bbwv>bbwp and uv>uvp and lbv<lbvp and vs
    above=sum(1 for j in range(i-TREND_LEN+1,i+1) if float(c.iloc[j])>=float(u.iloc[j]))
    walking=above>=TREND_LEN-1
    rev_l=not bo_up and lv<=lbv and cv>ov and pbv>0.1
    rev_s=not bo_dn and hv>=uv and cv<ov and pbv<0.9
    is_w=False; is_m=False; lp_o=False; hp_o=False
    for j in range(max(0,i-30),i+1):
        plv=pls.iloc[j]; phv=phs.iloc[j]
        lbj=float(lb.iloc[j]); ubj=float(u.iloc[j])
        if not pd.isna(plv):
            if plv>lbj and lp_o: is_w=True
            lp_o=plv<lbj
        if not pd.isna(phv):
            if phv<ubj and hp_o: is_m=True
            hp_o=phv>ubj
    bear_div=False; bull_div=False
    lp_ph=None; lpb_ph=None; lp_pl=None; lpb_pl=None
    for j in range(max(0,i-50),i+1):
        phv=phs.iloc[j]; plv=pls.iloc[j]
        pb_j=float(pb.iloc[j]) if not pd.isna(pb.iloc[j]) else 0.5
        if not pd.isna(phv):
            if lp_ph is not None and phv>lp_ph and pb_j<lpb_ph: bear_div=True
            lp_ph=phv; lpb_ph=pb_j
        if not pd.isna(plv):
            if lp_pl is not None and plv<lp_pl and pb_j>lpb_pl: bull_div=True
            lp_pl=plv; lpb_pl=pb_j
    cv_p=float(c.iloc[i-1]); mv_p=float(mid.iloc[i-1])
    mac_up=cv_p<=mv_p and cv>mv; mac_dn=cv_p>=mv_p and cv<mv
    lst="none"; ls=[]
    if not bear_div:
        if bull_div:  lst="bull_div"; ls=["bull_div"]+["vol"]*int(vs)+["HTF2"]*int(htf2b)
        elif bo_up:   lst="bo";       ls=["bo_up","vol"]+["HTF2"]*int(htf2b)+["EMA"]*int(efv>esv)
        elif walking: lst="walk";     ls=["walking"]+["HTF2"]*int(htf2b)+["EMA"]*int(efv>esv)
        elif mac_up:  lst="mac";      ls=["mac_up"]+["HTF2"]*int(htf2b)+["vol"]*int(vs)
        elif is_w:    lst="w";        ls=["W_pat"]+["HTF2"]*int(htf2b)+["vol"]*int(vs)
        elif rev_l:   lst="rev";      ls=["rev_l"]+["HTF2"]*int(htf2b)
    sst="none"; ss=[]
    if not bull_div:
        if bear_div:  sst="bear_div"; ss=["bear_div"]+["vol"]*int(vs)+["HTF2"]*int(htf2bear)
        elif bo_dn:   sst="bo";       ss=["bo_dn","vol"]+["HTF2"]*int(htf2bear)+["EMA"]*int(efv<esv)
        elif walking: sst="none"
        elif mac_dn:  sst="mac";      ss=["mac_dn"]+["HTF2"]*int(htf2bear)+["vol"]*int(vs)
        elif is_m:    sst="m";        ss=["M_pat"]+["HTF2"]*int(htf2bear)+["vol"]*int(vs)
        elif rev_s:   sst="rev";      ss=["rev_s"]+["HTF2"]*int(htf2bear)
    STRL={"bull_div","bo","walk"}; STRS={"bear_div","bo"}
    ld="long" if (lst in STRL and len(ls)>=1) or (lst and lst not in STRL and len(ls)>=2 and htf2b) else "none"
    sd="short" if (sst in STRS and len(ss)>=1) or (sst and sst not in STRS and len(ss)>=2 and htf2bear) else "none"
    return MR("BollingerH",ld,len(ls),ls),MR("BollingerH",sd,len(ss),ss)

# ══════════════════════════════════════════════════════════
# MÓDULO 3 — SMC
# ══════════════════════════════════════════════════════════
def mod_smc(df,df_htf)->Tuple[MR,MR]:
    c,h,l,o=df["close"],df["high"],df["low"],df["open"]
    i=len(df)-2
    if i<SMC_LB+5 or len(df_htf)<30:
        e=MR("SMC","none",0,[]); return e,e
    cv,hv,lv=float(c.iloc[i]),float(h.iloc[i]),float(l.iloc[i])
    atr_v=float(atr(df).iloc[i])
    bull_obs=[]; bear_obs=[]
    for k in range(2,min(11,i)):
        oh,ol,oc,oo=float(h.iloc[i-k]),float(l.iloc[i-k]),float(c.iloc[i-k]),float(o.iloc[i-k])
        if oc<oo and cv>oh: bull_obs.append({"h":oh,"l":ol})
        if oc>oo and cv<ol: bear_obs.append({"h":oh,"l":ol})
    br=any(ob["h"]<=cv<=ob["h"]+atr_v*1.5 for ob in bull_obs)
    brar=any(ob["l"]-atr_v*1.5<=cv<=ob["l"] for ob in bear_obs)
    lo_p=float(l.iloc[i-SMC_LB:i].min()); hi_p=float(h.iloc[i-SMC_LB:i].max())
    sw_dn=lv<lo_p; sw_up=hv>hi_p
    hi2=len(df_htf)-2
    htf_c=df_htf["close"]; htf_h=df_htf["high"]; htf_l=df_htf["low"]
    htf_ef=float(ema(htf_c,FAST).iloc[hi2]); htf_es=float(ema(htf_c,SLOW).iloc[hi2])
    htf_cl=float(htf_c.iloc[hi2])
    htf_hi=float(htf_h.iloc[max(0,hi2-20):hi2].max()); htf_lo=float(htf_l.iloc[max(0,hi2-20):hi2].min())
    bos_up=htf_cl>htf_hi; bos_dn=htf_cl<htf_lo
    htf_b=htf_ef>htf_es; htf_bear=htf_ef<htf_es
    lv_ok=bool(bull_obs) and br and sw_dn and (bos_up or htf_b)
    sv_ok=bool(bear_obs) and brar and sw_up and (bos_dn or htf_bear)
    ls=[f"bull_OB({len(bull_obs)})"]*int(bool(bull_obs))+["retest"]*int(br)+["sweep_lo"]*int(sw_dn)+["BOS"]*int(bos_up or htf_b)
    ss=[f"bear_OB({len(bear_obs)})"]*int(bool(bear_obs))+["retest"]*int(brar)+["sweep_hi"]*int(sw_up)+["BOS"]*int(bos_dn or htf_bear)
    return MR("SMC","long" if lv_ok else "none",len(ls),ls), \
           MR("SMC","short" if sv_ok else "none",len(ss),ss)

# ══════════════════════════════════════════════════════════
# MÓDULO 4 — POWERTREND
# ══════════════════════════════════════════════════════════
def mod_powertrend(df,htf1b,htf1bear)->Tuple[MR,MR]:
    c=df["close"]; vs=df["volume"].fillna(1).replace(0,1)
    i=len(df)-2
    if i<PT_LEN+10:
        e=MR("Powertrend","none",0,[]); return e,e
    wper=PT_LEN//2+1
    avrng=abs(c.diff()).ewm(span=wper,adjust=False).mean(); srng=avrng*3.0
    rngfilt=pd.Series(np.nan,index=df.index)
    for j in range(1,len(df)):
        prev=rngfilt.iloc[j-1] if not pd.isna(rngfilt.iloc[j-1]) else float(c.iloc[j])
        sv=float(srng.iloc[j]); cv2=float(c.iloc[j])
        vv2=float(vs.iloc[j]); vp2=float(vs.iloc[j-1])
        if vv2>vp2: rngfilt.iloc[j]=prev if (cv2-sv)<prev else (cv2-sv)
        else:       rngfilt.iloc[j]=prev if (cv2+sv)>prev else (cv2+sv)
    hband=rngfilt+srng; lband=rngfilt-srng; uprng=rngfilt>rngfilt.shift(1)
    _,_,adx_s=adx(df,PT_ADX_LEN)
    adx_vwma=(adx_s*vs).rolling(PT_ADX_LEN).sum()/vs.rolling(PT_ADX_LEN).sum()
    adx_ok=float(adx_s.iloc[i])>float(adx_vwma.iloc[i]) if not pd.isna(adx_vwma.iloc[i]) else False
    vwma200=(c*vs).rolling(PT_VWMA_LEN).sum()/vs.rolling(PT_VWMA_LEN).sum()
    vwma_b=float(c.iloc[i])>float(vwma200.iloc[i]) if not pd.isna(vwma200.iloc[i]) else True
    hl_len=14; hb_tf=hband.rolling(hl_len).max(); lb_tf=lband.rolling(hl_len).min()
    bs_up=0; bs_dn=0
    for j in range(max(0,i-50),i+1):
        if float(c.iloc[j])>float(hb_tf.iloc[j]): bs_up=i-j
        if float(c.iloc[j])<float(lb_tf.iloc[j]): bs_dn=i-j
    hl_up=bs_up<bs_dn
    cv=float(c.iloc[i]); cvp=float(c.iloc[i-1])
    hbv=float(hband.iloc[i]); hbvp=float(hband.iloc[i-1])
    lbv=float(lband.iloc[i]); lbvp=float(lband.iloc[i-1])
    up=bool(uprng.iloc[i])
    cross_up=cvp<=hbvp and cv>hbv; cross_dn=cvp>=lbvp and cv<lbv
    pine_b=up and cross_up and hl_up and adx_ok and vwma_b and htf1b
    pine_s=not up and cross_dn and not hl_up and adx_ok and htf1bear
    ls=["uprng"]*int(up)+["cross_hb"]*int(cross_up)+["HL_up"]*int(hl_up)+["ADX"]*int(adx_ok)+["VWMA_b"]*int(vwma_b)+["HTF1b"]*int(htf1b)
    ss=["dn"]*int(not up)+["cross_lb"]*int(cross_dn)+["HL_dn"]*int(not hl_up)+["ADX"]*int(adx_ok)+["HTF1bear"]*int(htf1bear)
    ld="long" if pine_b else "none"; sd="short" if pine_s else "none"
    return MR("Powertrend",ld,len(ls) if pine_b else max(0,len(ls)-3),ls), \
           MR("Powertrend",sd,len(ss) if pine_s else max(0,len(ss)-3),ss)

# ══════════════════════════════════════════════════════════
# MÓDULO 5 — BBPCT%
# ══════════════════════════════════════════════════════════
def mod_bbpct(df,htf1b,htf1bear)->Tuple[MR,MR]:
    c=df["close"]; mid,u,lb=bb(c); pb100=pct_b(c,u,lb)*100
    dev_pct=(u-lb)/c; lb_s=min(BBPCT_LB,len(df)-1)
    dev_arr=dev_pct.iloc[-lb_s:]; curr_dev=float(dev_pct.iloc[-2])
    vol_rank=float((dev_arr<curr_dev).sum())/len(dev_arr)*100 if len(dev_arr)>=10 else 50.0
    i=len(df)-2
    if i<25:
        e=MR("BBPCT","none",0,[]); return e,e
    pbv=float(pb100.iloc[i]); pbvp=float(pb100.iloc[i-1])
    uv=float(u.iloc[i]); lbv=float(lb.iloc[i]); cv=float(c.iloc[i])
    stdL=cv>lbv*0.95; stdS=cv<uv*1.05
    co_m8=pbvp<=-8 and pbv>-8; cu_108=pbvp>=108 and pbv<108
    co_m10=pbvp<=-10 and pbv>-10; cu_110=pbvp>=110 and pbv<110
    co_50=pbvp<=50 and pbv>50; cu_50=pbvp>=50 and pbv<50
    low_vol=vol_rank<20; hi_vol=vol_rank>60
    pine_b=(co_m8 or co_m10) and stdL; pine_s=(cu_108 or cu_110) and stdS
    zone=("xOS" if pbv<-10 else "OS" if pbv<0 else "b_mid" if pbv<50 else
          "a_mid" if pbv<100 else "OB" if pbv<110 else "xOB")
    ls=["co_m8"]*int(co_m8)+["co_m10"]*int(co_m10)+["co_50"]*int(co_50)+[zone]*int(zone in("xOS","OS"))+["hi_vol"]*int(hi_vol)+["HTF1b"]*int(htf1b)
    ss=["cu_108"]*int(cu_108)+["cu_110"]*int(cu_110)+["cu_50"]*int(cu_50)+[zone]*int(zone in("xOB","OB"))+["hi_vol"]*int(hi_vol)+["HTF1bear"]*int(htf1bear)
    ld="long" if pine_b and not low_vol else "none"
    sd="short" if pine_s and not low_vol else "none"
    return MR("BBPCT",ld,len(ls) if ld=="long" else max(0,len(ls)-2),ls), \
           MR("BBPCT",sd,len(ss) if sd=="short" else max(0,len(ss)-2),ss)

# ══════════════════════════════════════════════════════════
# MÓDULO 6 — RSI+
# ══════════════════════════════════════════════════════════
def mod_rsi_plus(df,htf1b,htf1bear)->Tuple[MR,MR]:
    c=df["close"]; rs=rsi(c,RSI_LEN); phs2=ph(c); pls2=pl(c)
    i=len(df)-2
    if i<20:
        e=MR("RSI+","none",0,[]); return e,e
    rv=float(rs.iloc[i]); rvp=float(rs.iloc[i-1])
    co_os=rvp<=RSI_OS and rv>RSI_OS; co_ob=rvp>=RSI_OB and rv<RSI_OB
    co_50=rvp<=50 and rv>50; cu_50=rvp>=50 and rv<50
    b50b=40<rv<60 and rvp<rv and htf1b; b50s=40<rv<60 and rvp>rv and htf1bear
    bull_div=False; bear_div=False
    lp_c_pl=None; lp_r_pl=None; lp_c_ph=None; lp_r_ph=None
    for j in range(max(0,i-40),i+1):
        plv=pls2.iloc[j]; phv=phs2.iloc[j]
        rj=float(rs.iloc[j]) if not pd.isna(rs.iloc[j]) else 50.0
        if not pd.isna(plv):
            if lp_c_pl is not None and plv<lp_c_pl and rj>lp_r_pl: bull_div=True
            lp_c_pl=plv; lp_r_pl=rj
        if not pd.isna(phv):
            if lp_c_ph is not None and phv>lp_c_ph and rj<lp_r_ph: bear_div=True
            lp_c_ph=phv; lp_r_ph=rj
    ls=["bull_div"]*int(bull_div)+["co_OS"]*int(co_os)+["co_50"]*int(co_50)+["b50"]*int(b50b)+[f"RSI{rv:.0f}"]*int(rv<35)+["HTF1b"]*int(htf1b)
    ss=["bear_div"]*int(bear_div)+["co_OB"]*int(co_ob)+["cu_50"]*int(cu_50)+["b50s"]*int(b50s)+[f"RSI{rv:.0f}"]*int(rv>65)+["HTF1bear"]*int(htf1bear)
    ls_ok=bull_div or (co_os and htf1b); ss_ok=bear_div or (co_ob and htf1bear)
    ld="long" if ls_ok and len(ls)>=2 else "none"
    sd="short" if ss_ok and len(ss)>=2 else "none"
    return MR("RSI+",ld,len(ls) if ld=="long" else max(0,len(ls)-2),ls), \
           MR("RSI+",sd,len(ss) if sd=="short" else max(0,len(ss)-2),ss)

# ══════════════════════════════════════════════════════════
# CONSENSO
# ══════════════════════════════════════════════════════════
def consensus(*results)->Tuple[Optional[str],int,str,str]:
    longs=[r for r in results[0::2] if r.direction=="long"]
    shorts=[r for r in results[1::2] if r.direction=="short"]
    lt=sum(r.score for r in longs); st=sum(r.score for r in shorts)
    if len(longs)>=2 and len(shorts)>=2 and abs(lt-st)<3:
        return None,0,"","contradicting"
    combo_l="+".join(r.name for r in longs)
    combo_s="+".join(r.name for r in shorts)
    eff_l=brain.get_effective_min_score(combo_l)
    eff_s=brain.get_effective_min_score(combo_s)
    best_d=None; best_sc=0; best_combo=""; best_sigs=""
    if len(longs)>=MIN_MODULES and lt>=eff_l:
        best_d="long"; best_sc=lt; best_combo=combo_l
        best_sigs="; ".join(f"{r.name}:[{','.join(r.signals[:3])}]" for r in longs)
    if len(shorts)>=MIN_MODULES and st>=eff_s and st>best_sc:
        best_d="short"; best_sc=st; best_combo=combo_s
        best_sigs="; ".join(f"{r.name}:[{','.join(r.signals[:3])}]" for r in shorts)
    return (best_d,best_sc,best_combo,best_sigs) if best_d else (None,0,"","")

# ══════════════════════════════════════════════════════════
# BTC BIAS
# ══════════════════════════════════════════════════════════
def update_btc(ex):
    prev_b=state.btc_bull; prev_bear=state.btc_bear
    try:
        df=fetch_df(ex,"BTC/USDT:USDT","1h",250)
        e48=ema(df["close"],BIAS); e200=ema(df["close"],MA200)
        ax=adx(df)[2]; rs=rsi(df["close"]); r=df.iloc[-2]
        state.btc_bull=bool(float(r["close"])>float(e48.iloc[-2]) and float(e48.iloc[-2])>float(e200.iloc[-2]))
        state.btc_bear=bool(float(r["close"])<float(e48.iloc[-2]) and float(e48.iloc[-2])<float(e200.iloc[-2]))
        state.btc_rsi=float(rs.iloc[-2]); state.btc_adx=float(ax.iloc[-2])
        log.info(f"BTC: {'BULL' if state.btc_bull else 'BEAR' if state.btc_bear else 'NEUTRAL'} RSI:{state.btc_rsi:.1f} ADX:{state.btc_adx:.1f}")
        if prev_b!=state.btc_bull or prev_bear!=state.btc_bear:
            s2="🟢 ALCISTA" if state.btc_bull else "🔴 BAJISTA" if state.btc_bear else "⚪ NEUTRO"
            tg(f"₿ BTC cambió → <b>{s2}</b> RSI:{state.btc_rsi:.0f} ADX:{state.btc_adx:.0f}\n⏰ {utcnow()}")
    except Exception as e: log.warning(f"BTC: {e}")

def btc_allows(direction,score):
    if not BTC_FILTER: return True,""
    if direction=="long"  and state.btc_bear and state.btc_adx>22 and score<9:
        return False,f"BTC bajista ADX:{state.btc_adx:.0f}"
    if direction=="short" and state.btc_bull and state.btc_adx>22 and score<9:
        return False,f"BTC alcista ADX:{state.btc_adx:.0f}"
    return True,""

# ══════════════════════════════════════════════════════════
# SCAN SÍMBOLO
# ══════════════════════════════════════════════════════════
def scan(ex,sym):
    try:
        df =fetch_df(ex,sym,TF,500)
        df1=fetch_df(ex,sym,HTF1,200)
        df2=fetch_df(ex,sym,HTF2,300)
        if len(df)<150 or len(df1)<50 or len(df2)<50: return None
        rs=rsi(df["close"]); at=atr(df); _,_,ax=adx(df)
        rv=float(rs.iloc[-2]); axv=float(ax.iloc[-2])
        if pd.isna(axv) or pd.isna(rv): return None
        htf1b,htf1bear=htf_bias(df1); htf2b,htf2bear=htf_bias(df2)
        m1l,m1s=mod_conf_pro(df,htf1b,htf1bear)
        m2l,m2s=mod_bollinger_hunter(df,htf2b,htf2bear)
        m3l,m3s=mod_smc(df,df1)
        m4l,m4s=mod_powertrend(df,htf1b,htf1bear)
        m5l,m5s=mod_bbpct(df,htf1b,htf1bear)
        m6l,m6s=mod_rsi_plus(df,htf1b,htf1bear)
        d,sc,mods,sigs=consensus(m1l,m1s,m2l,m2s,m3l,m3s,m4l,m4s,m5l,m5s,m6l,m6s)
        atr_v=float(at.iloc[-2]); price=float(df["close"].iloc[-2])
        # v15: calcular R/R antes de devolver
        if d and atr_v>0:
            sl_dist=atr_v*SL_M; tp_dist=atr_v*TP3_M
            rr=tp_dist/max(sl_dist,1e-9)
            if rr<MIN_RR: return None  # filtro R/R mínimo
        else: rr=0.0
        return {"sym":sym,"base":sym.split("/")[0],"direction":d,"score":sc,
                "modules":mods,"signals":sigs,"rsi":rv,"adx":axv,
                "atr":atr_v,"price":price,"rr":round(rr,2)}
    except Exception as e: log.debug(f"[{sym}] scan: {e}"); return None

# ══════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════
def tg(msg,mode="HTML"):
    tok=_tg_token(); cid=_tg_chat_id()
    if not tok or not cid: return
    try:
        r=requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                        json={"chat_id":cid,"text":msg,"parse_mode":mode},timeout=10)
        if not r.ok: log.warning(f"TG {r.status_code}: {r.text[:100]}")
    except Exception as e: log.warning(f"TG: {e}")

def tg_startup(balance,n):
    modo="🤖 AUTO TRADING" if not state.signal_only else "📱 SEÑALES MANUALES"
    tg(f"🚀 <b>SATY ELITE v15 — ONLINE</b>\n"
       f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
       f"💰 Balance: <b>${balance:.4f} USDT</b>\n"
       f"🔔 Modo: <b>{modo}</b>\n"
       f"📊 {n} pares | TF:{TF}·{HTF1}·{HTF2}\n"
       f"🎯 Score≥{MIN_SCORE} | Mód≥{MIN_MODULES}/6 | R/R≥{MIN_RR}\n"
       f"💵 ${FIXED_USDT:.0f}×{int(LEVERAGE)}x | CD:{COOLDOWN_MIN}min\n"
       f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
       f"📦 6 módulos:\n"
       f"  1️⃣ ConfPRO  2️⃣ BollingerH  3️⃣ SMC\n"
       f"  4️⃣ Powertrend  5️⃣ BBPCT%  6️⃣ RSI+\n"
       f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
       f"{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'} BTC "
       f"RSI:{state.btc_rsi:.0f} ADX:{state.btc_adx:.0f}\n⏰ {utcnow()}")

def tg_signal(t:TradeState,manual:bool=False):
    emoji="🟢" if t.side=="long" else "🔴"
    act="LONG ▲" if t.side=="long" else "SHORT ▼"
    sl_d=abs(t.sl-t.entry_price)
    def pct(p): return abs(p-t.entry_price)/t.entry_price*100
    rr=abs(t.tp3-t.entry_price)/max(sl_d,1e-9)
    head=f"{'👆 EJECUTAR MANUAL' if manual else '🤖 AUTO-EJECUTADO'}"
    msg=(f"{emoji} <b>{act} — {t.symbol}</b>\n"
         f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
         f"🧠 <b>{t.modules}</b> | Score:{t.score} | R/R:{rr:.1f}\n"
         f"📋 <code>{t.signals[:100]}</code>\n"
         f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
         f"💵 Entrada: <code>{t.entry_price:.6g}</code>\n"
         f"🟡 TP1: <code>{t.tp1:.6g}</code> +{pct(t.tp1):.2f}%\n"
         f"🟠 TP2: <code>{t.tp2:.6g}</code> +{pct(t.tp2):.2f}%\n"
         f"🟢 TP3: <code>{t.tp3:.6g}</code> +{pct(t.tp3):.2f}%\n"
         f"🛑 SL:  <code>{t.sl:.6g}</code> -{pct(t.sl):.2f}%\n"
         f"📊 RSI:{t.rsi:.0f} ADX:{t.adx:.0f}\n"
         f"{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'} BTC:{state.btc_rsi:.0f}\n"
         f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
         f"🔔 {head}\n")
    if manual:
        msg+=(f"📲 Pasos en BingX:\n"
              f"1️⃣ Futuros → {t.symbol}\n"
              f"2️⃣ {act} · Apalancamiento {int(LEVERAGE)}x\n"
              f"3️⃣ Entrada: <code>{t.entry_price:.6g}</code>\n"
              f"4️⃣ TP: <code>{t.tp1:.6g}</code> / <code>{t.tp2:.6g}</code> / <code>{t.tp3:.6g}</code>\n"
              f"5️⃣ SL: <code>{t.sl:.6g}</code>\n")
    msg+=f"⏰ {utcnow()}"
    tg(msg)

def tg_close(reason,t:TradeState,ep,pnl):
    w=pnl>0; emoji="✅" if w else "❌"
    pct=(pnl/(t.entry_price*t.contracts)*100) if t.contracts>0 else 0
    tg(f"{emoji} <b>CERRADO — {t.symbol}</b>\n"
       f"📋 {t.side.upper()} | {t.modules} | {reason}\n"
       f"💵 {t.entry_price:.6g} → {ep:.6g}\n"
       f"{'📈' if w else '📉'} <b>{pct:+.2f}% ${pnl:+.2f}</b>\n"
       f"🏔 Máx: +{t.max_pct:.2f}%\n"
       f"📊 {state.wins}W/{state.losses}L WR:{state.wr():.1f}%\n"
       f"💹 Hoy:${state.daily_pnl:+.2f} Total:${state.total_pnl:+.2f}\n⏰ {utcnow()}")

def tg_hb(balance):
    ol="\n".join(f"  {'🟢' if ts.side=='long' else '🔴'} {sym} [{ts.modules}] "
                 f"R/R:{ts.rr} {'🛡' if ts.sl_be else ''}+{ts.max_pct:.1f}%"
                 for sym,ts in state.trades.items()) or "  (ninguna)"
    modo="🤖 AUTO" if not state.signal_only else "📱 MANUAL"
    tg(f"💓 <b>HEARTBEAT — SATY v15</b>\n"
       f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
       f"🔔 {modo} | 💰 ${balance:.4f}\n"
       f"💹 Hoy:${state.daily_pnl:+.2f} | Total:${state.total_pnl:+.2f}\n"
       f"📊 {state.wins}W/{state.losses}L WR:{state.wr():.1f}% PF:{state.pf():.2f}\n"
       f"🔍 Ok:{state.sig_ok} Bloq:{state.sig_blk}\n"
       f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
       f"📦 Posiciones ({state.open_n()}/{MAX_OPEN_TRADES}):\n{ol}\n"
       f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
       f"{brain.telegram_summary_line()}\n"
       f"{'🟢' if state.btc_bull else '🔴' if state.btc_bear else '⚪'} BTC RSI:{state.btc_rsi:.0f} ADX:{state.btc_adx:.0f}\n"
       f"⏰ {utcnow()}")

def tg_summary(new_sigs,n_scan):
    top="\n".join(f"  {'🟢' if s['direction']=='long' else '🔴'} {s['sym']} "
                  f"[{s['modules']}] s:{s['score']} R/R:{s.get('rr',0)}" for s in new_sigs[:5]) or "  (ninguna)"
    blk="\n".join(f"  ⚫ {d['sym']} → {d['reason']}" for d in state.last_disc[:3]) or "  (ninguna)"
    tg(f"📡 <b>SCAN #{state.scan_n}</b> | {n_scan} pares\n"
       f"📶 Entradas:\n{top}\n🚫 Bloqueadas:\n{blk}\n"
       f"📊 {state.wins}W/{state.losses}L ${state.total_pnl:+.2f}\n⏰ {utcnow()}")

def tg_error(msg):
    tg(f"🔥 <b>ERROR</b>\n<code>{str(msg)[:400]}</code>\n⏰ {utcnow()}")

# ══════════════════════════════════════════════════════════
# CSV LOG
# ══════════════════════════════════════════════════════════
def log_csv(action,t:TradeState,price,pnl=0.0):
    try:
        exists=os.path.exists(CSV_PATH)
        with open(CSV_PATH,"a",newline="") as f:
            w=csv.writer(f)
            if not exists:
                w.writerow(["ts","action","symbol","side","modules","score","rr","entry","exit","pnl","contracts","rsi","adx"])
            w.writerow([utcnow(),action,t.symbol,t.side,t.modules,t.score,t.rr,
                        t.entry_price,price,round(pnl,4),t.contracts,t.rsi,t.adx])
    except Exception as e: log.warning(f"CSV: {e}")

# ══════════════════════════════════════════════════════════
# EXCHANGE
# ══════════════════════════════════════════════════════════
def build_ex():
    ex=ccxt.bingx({"apiKey":API_KEY,"secret":API_SECRET,
                   "options":{"defaultType":"swap"},"enableRateLimit":True})
    ex.load_markets(); return ex

def detect_hedge(ex):
    try:
        for p in ex.fetch_positions()[:5]:
            if p.get("info",{}).get("positionSide","") in ("LONG","SHORT"): return True
    except Exception: pass
    return False

def get_bal(ex):
    try: return float(ex.fetch_balance()["USDT"]["free"])
    except: return 0.0

def get_pos(ex,sym):
    try:
        for p in ex.fetch_positions([sym]):
            if abs(float(p.get("contracts",0) or 0))>0: return p
    except Exception: pass
    return None

def get_all_pos(ex):
    res={}
    try:
        for p in ex.fetch_positions():
            if abs(float(p.get("contracts",0) or 0))>0: res[p["symbol"]]=p
    except Exception as e: log.warning(f"positions: {e}")
    return res

def spread_pct(ex,sym):
    try:
        ob=ex.fetch_order_book(sym,limit=1)
        bid=ob["bids"][0][0] if ob["bids"] else 0
        ask=ob["asks"][0][0] if ob["asks"] else 0
        mid=(bid+ask)/2; return ((ask-bid)/mid*100) if mid>0 else 999.0
    except Exception: return 0.0

def ep(side):  return {"positionSide":"LONG" if side=="buy" else "SHORT"} if HEDGE_MODE else {}
def xp(ts):
    if HEDGE_MODE: return {"positionSide":"LONG" if ts=="long" else "SHORT","reduceOnly":True}
    return {"reduceOnly":True}

def get_syms(ex):
    cands=[s for s,m in ex.markets.items()
           if m.get("swap") and m.get("quote")=="USDT" and m.get("active",True) and s not in BLACKLIST]
    if not cands: return []
    try: tickers=ex.fetch_tickers(cands)
    except Exception: return cands[:TOP_N]
    ranked=[(s,float(tickers.get(s,{}).get("quoteVolume",0) or 0)) for s in cands]
    ranked=sorted([(s,v) for s,v in ranked if v>=MIN_VOL_USDT],key=lambda x:-x[1])
    res=[s for s,_ in ranked[:TOP_N]]
    log.info(f"Universo v15: {len(res)} pares (vol≥${MIN_VOL_USDT:.0f}, top {TOP_N})")
    return res

# ══════════════════════════════════════════════════════════
# APERTURA / CIERRE REAL
# ══════════════════════════════════════════════════════════
def open_trade(ex,sym,base,side,score,mods,sigs,atr_v,swing_l,swing_h,rv,axv,rr_val):
    try:
        sp=spread_pct(ex,sym)
        if sp>MAX_SPREAD_PCT: log.warning(f"[{sym}] spread {sp:.3f}%"); return None
        can,bsize,breason=check_entry(score,mods,FIXED_USDT*state.risk_m())
        if not can: log.info(f"[{sym}] Brain bloqueó: {breason}"); return None
        price=float(ex.fetch_ticker(sym)["last"])
        mkt=ex.markets.get(sym,{})
        cs=float(mkt.get("contractSize") or mkt.get("info",{}).get("contractSize") or 1.0)
        notional=bsize*LEVERAGE; amount=float(ex.amount_to_precision(sym,notional/(price*cs)))
        min_a=float((mkt.get("limits",{}).get("amount",{}) or {}).get("min",0) or 0)
        if amount<=0 or amount<min_a or amount*price*cs<3:
            log.warning(f"[{sym}] amount {amount:.6f} inválido"); return None
        try:
            lev=int(LEVERAGE)
            if HEDGE_MODE:
                ex.set_leverage(lev,sym,params={"positionSide":"LONG"})
                ex.set_leverage(lev,sym,params={"positionSide":"SHORT"})
            else: ex.set_leverage(lev,sym)
        except Exception as e: log.warning(f"[{sym}] lev: {e}")
        order=ex.create_order(sym,"market",side,amount,params=ep(side))
        entry=float(order.get("average") or price); ts="long" if side=="buy" else "short"
        if side=="buy":
            sl=min(swing_l-atr_v*0.2,entry-atr_v*SL_M)
            tp1=entry+atr_v*TP1_M; tp2=entry+atr_v*TP2_M; tp3=entry+atr_v*TP3_M
        else:
            sl=max(swing_h+atr_v*0.2,entry+atr_v*SL_M)
            tp1=entry-atr_v*TP1_M; tp2=entry-atr_v*TP2_M; tp3=entry-atr_v*TP3_M
        tp1=float(ex.price_to_precision(sym,tp1)); tp2=float(ex.price_to_precision(sym,tp2))
        tp3=float(ex.price_to_precision(sym,tp3)); sl=float(ex.price_to_precision(sym,sl))
        x=xp(ts); cside="sell" if side=="buy" else "buy"
        q1=float(ex.amount_to_precision(sym,amount*0.25))
        q2=float(ex.amount_to_precision(sym,amount*0.25))
        q3=float(ex.amount_to_precision(sym,amount*0.50))
        for lbl,qty,px2 in [("TP1",q1,tp1),("TP2",q2,tp2),("TP3",q3,tp3)]:
            try: ex.create_order(sym,"limit",cside,qty,px2,x)
            except Exception as e: log.warning(f"[{sym}] {lbl}: {e}")
        try: ex.create_order(sym,"stop_market",cside,amount,None,{**x,"stopPrice":sl})
        except Exception as e: log.warning(f"[{sym}] SL: {e}")
        t=TradeState(symbol=sym,base=base,side=ts,entry_price=entry,
                     tp1=tp1,tp2=tp2,tp3=tp3,sl=sl,score=score,modules=mods,
                     signals=sigs[:150],entry_time=utcnow(),contracts=amount,
                     atr=atr_v,rsi=rv,adx=axv,rr=rr_val)
        t.trail_h=t.trail_l=t.peak=entry
        log_csv("OPEN",t,entry); tg_signal(t,manual=False)
        log.info(f"[OPEN] {sym} {ts.upper()} score={score} R/R={rr_val} [{mods}]")
        return t
    except Exception as e:
        log.error(f"[{sym}] open: {e}"); tg_error(f"open {sym}: {e}"); return None

def send_manual_signal(res,atr_v,swing_l,swing_h,balance):
    """v15: Envía señal detallada a Telegram para ejecución manual."""
    sym=res["sym"]; side=res["direction"]; price=res["price"]
    rv=res["rsi"]; axv=res["adx"]; rr_val=res.get("rr",0)
    # Evitar señales duplicadas (anti-spam 30 min)
    sig_key=f"{sym}_{side}_{round(price,4)}"
    if sig_key in state.sent_sigs: return
    state.sent_sigs.add(sig_key)
    if len(state.sent_sigs)>200: state.sent_sigs=set(list(state.sent_sigs)[-100:])
    if side=="long":
        sl=min(swing_l-atr_v*0.2,price-atr_v*SL_M)
        tp1=price+atr_v*TP1_M; tp2=price+atr_v*TP2_M; tp3=price+atr_v*TP3_M
    else:
        sl=max(swing_h+atr_v*0.2,price+atr_v*SL_M)
        tp1=price-atr_v*TP1_M; tp2=price-atr_v*TP2_M; tp3=price-atr_v*TP3_M
    t=TradeState(symbol=sym,base=sym.split("/")[0],side=side,entry_price=price,
                 tp1=tp1,tp2=tp2,tp3=tp3,sl=sl,score=res["score"],
                 modules=res["modules"],signals=res["signals"][:150],
                 entry_time=utcnow(),contracts=0.0,atr=atr_v,rsi=rv,adx=axv,rr=rr_val)
    tg_signal(t,manual=True)
    log.info(f"[SEÑAL MANUAL] {sym} {side.upper()} score={res['score']} R/R={rr_val}")

def move_sl(ex,sym,new_sl):
    if sym not in state.trades: return
    t=state.trades[sym]
    try: ex.cancel_all_orders(sym)
    except Exception as e: log.warning(f"[{sym}] cancel: {e}")
    sl_px=float(ex.price_to_precision(sym,new_sl)); cside="sell" if t.side=="long" else "buy"
    try:
        ex.create_order(sym,"stop_market",cside,t.contracts,None,{**xp(t.side),"stopPrice":sl_px})
        t.sl=sl_px
    except Exception as e: log.warning(f"[{sym}] move_sl: {e}")

def close_trade(ex,sym,reason,price):
    if sym not in state.trades: return
    t=state.trades[sym]
    try: ex.cancel_all_orders(sym)
    except Exception as e: log.warning(f"[{sym}] cancel: {e}")
    pos=get_pos(ex,sym); pnl=0.0
    if pos:
        contracts=abs(float(pos.get("contracts",0)))
        cside="sell" if t.side=="long" else "buy"
        try:
            ex.create_order(sym,"market",cside,contracts,params=xp(t.side))
            pnl=((price-t.entry_price) if t.side=="long" else (t.entry_price-price))*contracts
        except Exception as e: log.error(f"[{sym}] close: {e}"); return
    if pnl>0: state.wins+=1; state.gross_p+=pnl; state.consec=0
    else:      state.losses+=1; state.gross_l+=abs(pnl); state.consec+=1
    state.total_pnl+=pnl; state.daily_pnl+=pnl
    state.peak_eq=max(state.peak_eq,state.peak_eq+pnl)
    state.set_cd(sym); log_csv("CLOSE",t,price,pnl); tg_close(reason,t,price,pnl)
    on_trade_closed(t,pnl,reason,state.btc_bull,state.btc_bear,state.btc_adx,t.rsi,t.adx)
    del state.trades[sym]

# ══════════════════════════════════════════════════════════
# GESTIÓN DEL TRADE
# ══════════════════════════════════════════════════════════
def manage(ex,sym,lp,atr_v,res,live_pos):
    if sym not in state.trades: return
    t=state.trades[sym]
    if live_pos is None:
        pnl=((lp-t.entry_price) if t.side=="long" else (t.entry_price-lp))*t.contracts
        reason=("TP3" if (t.side=="long" and lp>=t.tp3) or (t.side=="short" and lp<=t.tp3) else "SL")
        if pnl>0: state.wins+=1; state.gross_p+=pnl; state.consec=0
        else:      state.losses+=1; state.gross_l+=abs(pnl); state.consec+=1
        state.total_pnl+=pnl; state.daily_pnl+=pnl; state.set_cd(sym)
        log_csv("CLOSE_EXT",t,lp,pnl); tg_close(reason,t,lp,pnl)
        on_trade_closed(t,pnl,reason,state.btc_bull,state.btc_bear,state.btc_adx,t.rsi,t.adx)
        del state.trades[sym]; return
    if not t.tp1_hit:
        if (t.side=="long" and lp>=t.tp1) or (t.side=="short" and lp<=t.tp1):
            t.tp1_hit=True; t.sl_be=True; t.peak=lp
            pnl_e=abs(t.tp1-t.entry_price)*float(live_pos.get("contracts",0))*0.25
            move_sl(ex,sym,t.entry_price)
            tg(f"🟡 <b>TP1+BE</b> — {t.symbol}\n💰+${pnl_e:.2f}|SL→entrada\n🎯TP2:<code>{t.tp2:.6g}</code>\n⏰{utcnow()}")
    if t.tp1_hit and not t.tp2_hit:
        if (t.side=="long" and lp>=t.tp2) or (t.side=="short" and lp<=t.tp2):
            t.tp2_hit=True
            pnl_e=abs(t.tp2-t.entry_price)*float(live_pos.get("contracts",0))*0.25
            move_sl(ex,sym,t.tp1)
            tg(f"🟠 <b>TP2</b> — {t.symbol}\n💰+${pnl_e:.2f}|SL→TP1\n🎯TP3:<code>{t.tp3:.6g}</code>\n⏰{utcnow()}")
    if t.tp1_hit and sym in state.trades:
        atr_t=atr_v if atr_v>0 else t.atr
        cur_pct=((lp-t.entry_price)/t.entry_price*100 if t.side=="long" else (t.entry_price-lp)/t.entry_price*100)
        t.max_pct=max(t.max_pct,cur_pct)
        new_peak=(lp>t.peak if t.side=="long" else lp<t.peak)
        if new_peak: t.peak=lp; t.stall=0
        else: t.stall+=1
        denom=abs(t.peak-t.entry_price)
        retrace=((t.peak-lp)/max(denom,1e-9)*100 if t.side=="long" else (lp-t.peak)/max(denom,1e-9)*100)
        if   cur_pct>5.0:  t.phase="ultra"
        elif retrace>30:   t.phase="locked"
        elif t.stall>=3:   t.phase="tight"
        else:              t.phase="normal"
        tm={"normal":0.8,"tight":0.4,"locked":0.2,"ultra":0.15}[t.phase]
        if t.side=="long":
            t.trail_h=max(t.trail_h,lp)
            if lp<=t.trail_h-atr_t*tm: close_trade(ex,sym,f"TRAIL_{t.phase.upper()}",lp); return
        else:
            t.trail_l=min(t.trail_l,lp)
            if lp>=t.trail_l+atr_t*tm: close_trade(ex,sym,f"TRAIL_{t.phase.upper()}",lp); return
    if not t.tp1_hit and sym in state.trades:
        loss_d=(t.entry_price-lp if t.side=="long" else lp-t.entry_price)
        if loss_d>=(atr_v if atr_v>0 else t.atr)*0.8:
            close_trade(ex,sym,"PÉRDIDA_DIN",lp); return
    if res and sym in state.trades:
        d=res.get("direction"); sc=res.get("score",0)
        if t.side=="long"  and d=="short" and sc>=MIN_SCORE+2: close_trade(ex,sym,"FLIP_SHORT",lp)
        elif t.side=="short" and d=="long"  and sc>=MIN_SCORE+2: close_trade(ex,sym,"FLIP_LONG",lp)

# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
def main():
    global HEDGE_MODE
    log.info("="*60)
    log.info("  SATY ELITE v15 — 6 módulos · Brain opcional · Railway")
    log.info(f"  Brain: {'✅' if HAS_BRAIN else '❌ no instalado (ok)'}")
    log.info(f"  scipy: {'✅' if 'scipy' in sys.modules else '❌ usando fallback (ok)'}")
    log.info("="*60)
    if not API_KEY or not API_SECRET:
        log.error("Faltan API Keys"); tg_error("Sin API Keys"); sys.exit(1)

    ex=None
    for attempt in range(10):
        try: ex=build_ex(); log.info("BingX ✓"); break
        except Exception as e:
            wait=min(2**attempt,120)
            log.warning(f"Conexión {attempt+1}/10 retry {wait}s: {e}"); time.sleep(wait)
    if ex is None: tg_error("Sin conexión BingX"); raise RuntimeError("Sin conexión")

    HEDGE_MODE=detect_hedge(ex)
    log.info(f"Modo: {'HEDGE' if HEDGE_MODE else 'ONE-WAY'}")

    # ── Detectar balance y modo ──────────────────────────
    balance=0.0
    for _ in range(10):
        try: balance=get_bal(ex); break
        except: time.sleep(5)

    state.signal_only = balance < MIN_BAL_AUTO
    if state.signal_only:
        log.warning(f"⚠️ Balance ${balance:.6f} < ${MIN_BAL_AUTO} → MODO SEÑALES MANUALES")
    else:
        log.info(f"✅ Balance ${balance:.2f} → MODO AUTO TRADING")

    state.peak_eq=max(balance,0.01); state.daily_reset=time.time(); state.last_hb=time.time()

    symbols=[]
    while not symbols:
        try: ex.load_markets(); symbols=get_syms(ex)
        except Exception as e: log.error(f"get_syms: {e}"); time.sleep(60)

    update_btc(ex); tg_startup(balance,len(symbols))

    scan_n=0
    REFRESH=max(1,3600//max(POLL_SECS,1))
    BTC_REF=max(1,900//max(POLL_SECS,1))
    SUMMARY=20; prev_cb=False; prev_dl=False

    while True:
        ts_start=time.time()
        try:
            scan_n+=1; state.scan_n=scan_n
            state.reset_daily(); clear_cache()
            log.info(f"SCAN #{scan_n} | {datetime.now(timezone.utc):%H:%M:%S} | "
                     f"{'MANUAL' if state.signal_only else 'AUTO'} | "
                     f"{state.open_n()}/{MAX_OPEN_TRADES} | ok:{state.sig_ok} blk:{state.sig_blk}")
            if scan_n%REFRESH==0:
                try: ex.load_markets(); symbols=get_syms(ex)
                except Exception as e: log.warning(f"Refresh: {e}")
            if scan_n%BTC_REF==0: update_btc(ex)

            # Re-evaluar balance cada 10 ciclos
            if scan_n%10==0:
                try:
                    new_bal=get_bal(ex)
                    was_manual=state.signal_only
                    state.signal_only=new_bal<MIN_BAL_AUTO
                    if was_manual and not state.signal_only:
                        tg(f"🎉 <b>Fondos detectados ${new_bal:.2f}!</b>\n🤖 Activando AUTO TRADING\n⏰{utcnow()}")
                    elif not was_manual and state.signal_only:
                        tg(f"⚠️ <b>Sin fondos — Modo SEÑALES MANUALES</b>\n⏰{utcnow()}")
                except: pass

            if time.time()-state.last_hb>3600:
                try: tg_hb(get_bal(ex)); state.last_hb=time.time()
                except Exception: pass

            # Circuit breaker
            cb_now=state.cb()
            if cb_now and not prev_cb:
                dd=(state.peak_eq-(state.peak_eq+state.total_pnl))/state.peak_eq*100
                tg(f"🚨 <b>CIRCUIT BREAKER</b> DD:{dd:.2f}%>{CB_DD}%\n⏰ {utcnow()}")
            prev_cb=cb_now
            if cb_now: time.sleep(POLL_SECS); continue

            # Daily limit
            dl_now=state.dl()
            if dl_now and not prev_dl:
                tg(f"🚨 <b>LÍMITE DIARIO</b> ${state.daily_pnl:+.2f}\n⏰ {utcnow()}")
            prev_dl=dl_now
            if dl_now: time.sleep(POLL_SECS); continue

            # Gestionar trades activos (solo modo AUTO)
            if not state.signal_only:
                live_pos=get_all_pos(ex)
                for sym in list(state.trades.keys()):
                    try:
                        lp_d=live_pos.get(sym)
                        lp_p=float(lp_d["markPrice"]) if lp_d else float(ex.fetch_ticker(sym)["last"])
                        res=scan(ex,sym); atr_v=res["atr"] if res else state.trades[sym].atr
                        manage(ex,sym,lp_p,atr_v,res,lp_d)
                    except Exception as e: log.warning(f"[{sym}] manage: {e}")

            # Buscar nuevas entradas / señales
            new_sigs=[]; state.last_disc=[]
            to_scan=[]
            bases_open=state.bases()
            to_scan=[s for s in symbols
                     if s not in state.trades
                     and not state.in_cd(s)
                     and s.split("/")[0] not in bases_open]

            # Si modo MANUAL, escanear siempre (no hay límite de posiciones abiertas)
            if state.signal_only or state.open_n()<MAX_OPEN_TRADES:
                log.info(f"Escaneando {len(to_scan)} pares con 6 módulos ...")
                with ThreadPoolExecutor(max_workers=8) as pool:
                    futures={pool.submit(scan,ex,s):s for s in to_scan}
                    results=[f.result() for f in as_completed(futures) if f.result()]
                for res in results:
                    d=res.get("direction"); sc=res.get("score",0)
                    if d is None: continue
                    ok,breason=btc_allows(d,sc)
                    if not ok:
                        state.sig_blk+=1
                        state.last_disc.append({"sym":res["sym"],"score":sc,"reason":breason}); continue
                    state.sig_ok+=1; new_sigs.append(res)
                new_sigs.sort(key=lambda x:x["score"],reverse=True)

                for res in new_sigs:
                    sym=res["sym"]; base=res["base"]
                    if state.in_cd(sym): continue
                    try:
                        df_tmp=fetch_df(ex,sym,TF,50)
                        sl_l=float(df_tmp["low"].rolling(10).min().iloc[-2])
                        sl_h=float(df_tmp["high"].rolling(10).max().iloc[-2])
                    except Exception: sl_l=res["price"]*0.99; sl_h=res["price"]*1.01

                    if state.signal_only:
                        # ── Modo MANUAL: enviar señal Telegram ──────────
                        send_manual_signal(res,res["atr"],sl_l,sl_h,balance)
                    else:
                        # ── Modo AUTO: ejecutar real ─────────────────────
                        if state.open_n()>=MAX_OPEN_TRADES: break
                        if sym in state.trades or base in state.bases(): continue
                        t=open_trade(ex,sym,base,"buy" if res["direction"]=="long" else "sell",
                                     res["score"],res["modules"],res["signals"],
                                     res["atr"],sl_l,sl_h,res["rsi"],res["adx"],res.get("rr",0))
                        if t: state.trades[sym]=t

            if scan_n%SUMMARY==0: tg_summary(new_sigs,len(to_scan))
            if scan_n%50==0 and brain.data.total_trades>=5:
                tg(brain.telegram_report())
                dist=brain.score_distribution_bar()
                if dist: tg(f"<code>{dist}</code>")

            elapsed=time.time()-ts_start
            log.info(f"Ciclo {elapsed:.1f}s | {state.wins}W/{state.losses}L | "
                     f"hoy:${state.daily_pnl:+.2f} | total:${state.total_pnl:+.2f}")

        except ccxt.NetworkError as e: log.warning(f"Network: {e}"); time.sleep(15)
        except ccxt.ExchangeError as e: log.error(f"Exchange: {e}"); tg_error(str(e))
        except KeyboardInterrupt: tg("🛑 Bot detenido."); break
        except Exception as e: log.exception(f"Error: {e}"); tg_error(str(e))

        elapsed=time.time()-ts_start
        time.sleep(max(0,POLL_SECS-elapsed))

# ══════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════
if __name__=="__main__":
    while True:
        try: main()
        except KeyboardInterrupt: break
        except Exception as e:
            log.exception(f"CRASH: {e}")
            try: tg_error(f"CRASH restart 30s: {str(e)[:200]}")
            except Exception: pass
            time.sleep(30)
