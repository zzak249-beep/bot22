"""
Scanner FINAL — Todos los pares BingX
- 8 workers (evita connection pool lleno)
- Caché 55s para no re-escanear
- Score: señal + ATR + volumen + hora
"""
import logging,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import dataclass
from datetime import datetime,timezone
from typing import List,Optional
import pandas as pd
from bingx_client import BingXClient
from strategy import EMAStrategy

logger=logging.getLogger(__name__)

@dataclass
class SymbolScore:
    symbol:str; score:float; signal:str; price:float
    ema1:float; ema2:float; ema3:float
    rsi:float; adx:float; atr_pct:float
    volume24h:float; vol_spike:float
    reason:str; sig_score:float

def _to_df(klines):
    if not klines or len(klines)<30: return None
    df=pd.DataFrame(klines)
    df["timestamp"]=pd.to_datetime(df["timestamp"],unit="ms",utc=True)
    for c in ["open","high","low","close","volume"]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.dropna(subset=["close"]).sort_values("timestamp").reset_index(drop=True)

class MultiSymbolScanner:
    def __init__(self,client:BingXClient,interval="3m",htf="15m",h1="1h",
                 top_n=1,min_volume=300_000,score_min=35,workers=8):
        self.client=client; self.iv=interval; self.htf=htf; self.h1=h1
        self.top_n=top_n; self.min_vol=min_volume; self.score_min=score_min
        self.workers=workers  # 8 max para no saturar connection pool
        self.strategy=EMAStrategy(score_min=score_min)
        self._cache:List[SymbolScore]=[]; self._cache_ts=0.0
        self._syms:List[str]=[]; self._syms_ts=0.0

    def _all_symbols(self):
        now=time.time()
        if self._syms and now-self._syms_ts<3600: return self._syms
        try:
            self._syms=self.client.get_all_symbols()
            self._syms_ts=now
            logger.info(f"Pares BingX: {len(self._syms)}")
        except Exception as e:
            logger.warning(f"Symbols error: {e}")
            if not self._syms:
                self._syms=["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
                             "DOGE-USDT","AVAX-USDT","LINK-USDT","ARB-USDT","SUI-USDT"]
        return self._syms

    def _hour_bonus(self):
        h=datetime.now(timezone.utc).hour
        if 13<=h<=17: return 1.3
        if 8<=h<=12:  return 1.15
        return 1.0

    def _one(self,symbol):
        try:
            k=self.client.get_klines(symbol,self.iv,150)
            df=_to_df(k)
            if df is None: return None
            try: vol24h=float(self.client.get_ticker(symbol).get("quoteVolume",0))
            except: vol24h=float(df.volume.sum()*df.close.iloc[-1])
            if vol24h<self.min_vol: return None
            htf_df=_to_df(self.client.get_klines(symbol,self.htf,80))
            h1_df =_to_df(self.client.get_klines(symbol,self.h1, 60))
            sig=self.strategy.get_latest_signal(df,htf_df,h1_df)
            if sig.action=="HOLD": return None
            vm=float(df.volume.rolling(20).mean().iloc[-2])
            vs=float(df.volume.iloc[-2])/max(vm,1)
            comp=(sig.score*0.55+min(25,sig.atr_pct*8)*0.25+min(20,(vs-1)*8)*0.20)*self._hour_bonus()
            return SymbolScore(symbol=symbol,score=round(comp,1),signal=sig.action,price=sig.price,
                ema1=sig.ema1,ema2=sig.ema2,ema3=sig.ema3,rsi=sig.rsi,adx=sig.adx,
                atr_pct=sig.atr_pct,volume24h=vol24h,vol_spike=round(vs,2),
                reason=sig.reason,sig_score=sig.score)
        except Exception as e:
            logger.debug(f"{symbol}:{e}"); return None

    def scan(self,force=False):
        now=time.time()
        if not force and self._cache and now-self._cache_ts<55: return self._cache
        syms=self._all_symbols(); t0=time.time(); results=[]
        logger.info(f"Escaneando {len(syms)} pares ({self.workers} hilos)...")
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for fut in as_completed({ex.submit(self._one,s):s for s in syms}):
                r=fut.result()
                if r: results.append(r)
        results.sort(key=lambda x:x.score,reverse=True)
        logger.info(f"Scan {time.time()-t0:.1f}s | señales={len(results)} | top={[s.symbol for s in results[:3]]}")
        self._cache=results; self._cache_ts=now
        return results

    def format_report(self,res):
        if not res: return "🔍 Sin señales ahora."
        lines=[f"📊 <b>{len(res)} señales — top 5</b>\n"]
        for i,s in enumerate(res[:5],1):
            e="🟢" if s.signal=="LONG" else "🔴"
            lines.append(f"{i}. {e} <b>{s.symbol}</b>  Score:<b>{s.score}</b>\n"
                        f"   ${s.price:,.6g} | RSI {s.rsi:.0f} | ADX {s.adx:.0f} | Vol×{s.vol_spike}\n"
                        f"   {s.reason}")
        return "\n".join(lines)
