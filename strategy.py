"""
Strategy FINAL — EMA Reversal (probado: WR 40%, PF 1.68)
SL=1.5xATR | TP1=1.5xATR (50%) | TP2=2.5xATR (50%)
Filtros: RSI + ADX + Volumen + HTF 15m + HTF 1h
"""
import pandas as pd, numpy as np
from dataclasses import dataclass
from typing import Optional
import logging
logger = logging.getLogger(__name__)

@dataclass
class Signal:
    action: str; price: float
    ema1: float; ema2: float; ema3: float
    rsi: float; adx: float; atr: float; atr_pct: float
    volume_ok: bool; reason: str; timestamp: str; score: float = 0.0

def _ema(s,p):   return s.ewm(span=p,adjust=False).mean()
def _co(a,b):    return (a.shift(1)<=b.shift(1))&(a>b)
def _cu(a,b):    return (a.shift(1)>=b.shift(1))&(a<b)
def _rsi(s,p=14):
    d=s.diff(); g=d.clip(lower=0).ewm(com=p-1,adjust=False).mean()
    l=(-d.clip(upper=0)).ewm(com=p-1,adjust=False).mean()
    return 100-100/(1+g/l.replace(0,np.nan))
def _atr(df,p=14):
    h,lo,c=df.high,df.low,df.close; pc=c.shift(1)
    return pd.concat([h-lo,(h-pc).abs(),(lo-pc).abs()],axis=1).max(axis=1).ewm(com=p-1,adjust=False).mean()
def _adx(df,p=14):
    h,lo,c=df.high,df.low,df.close; pc=c.shift(1)
    tr=pd.concat([h-lo,(h-pc).abs(),(lo-pc).abs()],axis=1).max(axis=1)
    dmp=(h-h.shift()).clip(lower=0); dmm=(lo.shift()-lo).clip(lower=0)
    dmp=dmp.where(dmp>dmm,0); dmm=dmm.where(dmm>dmp,0)
    a14=tr.ewm(com=p-1,adjust=False).mean().replace(0,np.nan)
    dip=100*dmp.ewm(com=p-1,adjust=False).mean()/a14
    dim=100*dmm.ewm(com=p-1,adjust=False).mean()/a14
    return (100*(dip-dim).abs()/(dip+dim).replace(0,np.nan)).ewm(com=p-1,adjust=False).mean()

class EMAStrategy:
    def __init__(self, ma1=2, ma2=4, ma3=20, score_min=35):
        self.ma1=ma1; self.ma2=ma2; self.ma3=ma3; self.score_min=score_min
        logger.info(f"Strategy FINAL | EMA{ma1}/{ma2}/{ma3} score_min={score_min}")

    def compute(self, df):
        df=df.copy(); p=df.close
        df["ema1"]=_ema(p,self.ma1); df["ema2"]=_ema(p,self.ma2); df["ema3"]=_ema(p,self.ma3)
        df["rsi"]=_rsi(p); df["adx"]=_adx(df); df["atr"]=_atr(df)
        df["vol_ma"]=df.volume.rolling(20).mean(); df["rsi_d"]=df.rsi.diff(2)
        dp=p.diff(); d1=df.ema1.diff(); d2=df.ema2.diff()
        df["raw_long"]  = _cu(p,df.ema3)|((dp<0)&(d1<0)&_cu(p,df.ema1)&(d2>0))
        df["raw_short"] = _co(p,df.ema3)|((dp>0)&(d1>0)&_co(p,df.ema1)&(d2<0))
        return df

    def _score(self, row, action, htf="NEUTRAL", h1="NEUTRAL"):
        s=40.0
        adx=float(row.get("adx",0) or 0)
        if adx>=25: s+=20
        elif adx>=18: s+=12
        elif adx>=12: s+=5
        rsi=float(row.get("rsi",50) or 50); rd=float(row.get("rsi_d",0) or 0)
        if action=="LONG":
            if 25<=rsi<=60: s+=18
            elif rsi<25: s+=10
            elif rsi>70: s-=15
            if rd>1: s+=8
        else:
            if 40<=rsi<=75: s+=18
            elif rsi>75: s+=10
            elif rsi<30: s-=15
            if rd<-1: s+=8
        vol=float(row.get("volume",0) or 0); vm=float(row.get("vol_ma",1) or 1)
        r=vol/max(vm,1)
        if r>=2.0: s+=15
        elif r>=1.5: s+=10
        elif r>=1.0: s+=5
        if action=="LONG"  and htf=="BULL": s+=12
        if action=="SHORT" and htf=="BEAR": s+=12
        if action=="LONG"  and htf=="BEAR": s-=8
        if action=="SHORT" and htf=="BULL": s-=8
        if action=="LONG"  and h1=="BULL":  s+=8
        if action=="SHORT" and h1=="BEAR":  s+=8
        if action=="LONG"  and h1=="BEAR":  s-=5
        if action=="SHORT" and h1=="BULL":  s-=5
        return round(min(100,max(0,s)),1)

    def _bias(self, df):
        if df is None or len(df)<self.ma3+5: return "NEUTRAL"
        try:
            e1=_ema(df.close,self.ma1).iloc[-2]; e3=_ema(df.close,self.ma3).iloc[-2]
            if e1>e3*1.0003: return "BULL"
            if e1<e3*0.9997: return "BEAR"
        except: pass
        return "NEUTRAL"

    def get_latest_signal(self, df, htf_df=None, h1_df=None):
        df=self.compute(df); row=df.iloc[-2]
        htf=self._bias(htf_df); h1=self._bias(h1_df)
        price=float(row.close)
        atr=float(row.atr) if not pd.isna(row.atr) else price*0.005
        base=dict(price=price,ema1=float(row.ema1),ema2=float(row.ema2),ema3=float(row.ema3),
                  rsi=float(row.rsi) if not pd.isna(row.rsi) else 50,
                  adx=float(row.adx) if not pd.isna(row.adx) else 0,
                  atr=atr,atr_pct=atr/price*100,
                  volume_ok=float(row.volume)>=float(row.get("vol_ma",0)),
                  timestamp=str(row.get("timestamp","")))
        for action,key in [("LONG","raw_long"),("SHORT","raw_short")]:
            if not row[key]: continue
            sc=self._score(row,action,htf,h1)
            if sc<self.score_min: continue
            htf_t=f" 15m:{htf}" if htf!="NEUTRAL" else ""
            h1_t =f" 1h:{h1}"  if h1!="NEUTRAL"  else ""
            return Signal(action=action,score=sc,
                         reason=f"EMA cross {'↑' if action=='LONG' else '↓'} RSI={base['rsi']:.0f} ADX={base['adx']:.0f}{htf_t}{h1_t}",**base)
        return Signal(action="HOLD",reason="Sin señal",score=0,**base)
