#!/usr/bin/env python3
"""
BOT v8.1 — SIMPLE & EFFECTIVE (FIXED)
════════════════════════════════════════════════════════════════════════════════
FIXES vs v8.0:
  ✅ FIX1: opt_score cap bajado de 88 → 68 (era imposible llegar a 85)
  ✅ FIX2: Learn._adj() menos agresivo — sube solo +2/+1, baja -2/-1
  ✅ FIX3: analyze() loggea el motivo exacto de cada rechazo (DEBUG)
  ✅ FIX4: _score_min() nunca supera SCORE_MAX_CAP=70
  ✅ FIX5: daily_losers solo bloquea si pierdes >1% (no cualquier SL pequeño)
  ✅ FIX6: Comando /debug SYM — analiza un símbolo y muestra por qué pasa/falla
  ✅ FIX7: Comando /reset — resetea opt_score y streak sin borrar historial
  ✅ FIX8: Comando /why — muestra los últimos 5 rechazos con motivo
  ✅ FIX9: _analyze_parallel loggea top rechazos
  ✅ FIX10: Paper mode stats aunque AUTO=True (contador de señales perdidas)
"""

import os, sys, time, math, re, json, hmac, hashlib, logging, asyncio
import random, threading
from datetime import datetime, timedelta
from urllib.parse import urlencode
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# ============================================================================
# CONFIG
# ============================================================================

def _e(k, d, t='str'):
    v = os.getenv(k, str(d)).strip().strip('"').strip("'")
    if t in ('int','float'): v = re.sub(r'[^\d\.\-]','',v) or str(d)
    if t=='int':   return int(float(v))
    if t=='float': return float(v)
    if t=='bool':  return v.lower()=='true'
    return v

API_KEY    = os.getenv('BINGX_API_KEY','').strip().strip('"')
API_SECRET = os.getenv('BINGX_API_SECRET','').strip().strip('"')
TG_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN','')
TG_CHAT    = os.getenv('TELEGRAM_CHAT_ID','')
BASE_URL   = "https://open-api.bingx.com"

# Capital
AUTO      = _e('AUTO_TRADING_ENABLED','true','bool')
POS_SIZE  = _e('MAX_POSITION_SIZE','10','float')
MIN_TRADE = _e('MIN_TRADE_USDT','10','float')
LEVERAGE  = min(_e('LEVERAGE','2','int'), 3)
MAX_TRADES= _e('MAX_OPEN_TRADES','3','int')
MAX_DAILY = _e('MAX_DAILY_TRADES','6','int')
RISK_PCT  = _e('RISK_PCT','1.0','float')
EQUITY    = _e('ACCOUNT_EQUITY','100','float')

# TP/SL
TP1_PCT  = _e('TP1_PCT','40','float')
TP2_PCT  = _e('TP2_PCT','35','float')
TP1_R    = _e('TP1_RATIO','2.0','float')
TP2_R    = _e('TP2_RATIO','3.5','float')
SL_MAX   = _e('SL_MAX_PCT','3.5','float')
SL_MIN   = _e('SL_MIN_PCT','0.6','float')
SL_ATR_M = 1.5
MIN_RR   = _e('MIN_RR','1.5','float')

# Trailing
USE_TRAIL  = _e('USE_TRAILING_EXIT','true','bool')
TRAIL_RATE = _e('TRAIL_RATE_PCT','1.5','float')
TRAIL_ACT  = _e('TRAIL_ACTIVATION','0.8','float')

# Filtros
MIN_VOL       = _e('MIN_VOLUME_24H','300000','float')
MAX_SYMS      = _e('MAX_SYMBOLS','200','int')
MIN_SCORE     = _e('MIN_SCORE','52','float')
# ✅ FIX4: cap absoluto — opt_score NUNCA supera este valor
SCORE_MAX_CAP = _e('SCORE_MAX_CAP','70','float')
SCORE_BULL    = _e('SCORE_BULL','55','float')
SCORE_NEUTRAL = _e('SCORE_NEUTRAL','62','float')
VOL_R_MIN     = _e('VOL_RATIO_MIN','1.2','float')
AUROLO_MIN    = _e('AUROLO_MIN_PTS','2','int')
AUROLO_EMA    = _e('AUROLO_EMA_LEN','55','int')
BTC_CRASH     = _e('BTC_CRASH_PCT','2.5','float')
BREADTH_BEAR  = _e('BREADTH_BEAR_HARD','0.20','float')

# ✅ FIX5: pérdida mínima para que daily_losers bloquee el símbolo
SL_BLOCK_MIN_PCT = _e('SL_BLOCK_MIN_PCT','1.0','float')

# CB
CB_PCT    = _e('CIRCUIT_BREAKER_PCT','8.0','float')
CB_H      = _e('CB_PAUSE_HOURS','2','int')
DAILY_LOSS= _e('DAILY_LOSS_CAP_PCT','10.0','float')
MAX_STREAK= _e('MAX_LOSING_STREAK','4','int')

# Cooldowns
CD_TP     = _e('COOLDOWN_TP_MIN','10','int')
CD_SL     = _e('COOLDOWN_SL_MIN','120','int')
CD_SL_TODAY=_e('COOLDOWN_SL_TODAY','true','bool')

# Scanner
SCAN_INT  = _e('SCAN_INTERVAL','90','int')
MIN_CONF  = _e('SCANNER_MIN_CONF','45','int')
HOT_CONF  = _e('SCANNER_HOT_CONF','60','int')
SCAN_W    = _e('SCAN_WORKERS','8','int')

INTERVAL  = _e('CHECK_INTERVAL','60','int')

EXCL = {'USDC','BUSD','TUSD','FRAX','DAI','USDP','FDUSD','EUR','GBP','JPY','CHF','AUD','CAD','XAU','XAG'}
EXCL_PFX = ('NCS','NCB',)

BREADTH_COINS = ['BTC-USDT','ETH-USDT','BNB-USDT','SOL-USDT','XRP-USDT',
                 'ADA-USDT','DOGE-USDT','LINK-USDT','AVAX-USDT','MATIC-USDT']

FEE_TAKER = 0.001
FEE_COST  = FEE_TAKER * LEVERAGE * 2 * 100

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('V81')

# ============================================================================
# API
# ============================================================================

def api(method, ep, params=None, retries=3):
    params = params or {}
    for attempt in range(retries+1):
        try:
            p   = {**{k:str(v) for k,v in params.items()},
                   'timestamp':str(int(time.time()*1000))}
            qs  = urlencode(sorted(p.items()))
            sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f"{BASE_URL}{ep}?{qs}&signature={sig}"
            hdr = {'X-BX-APIKEY':API_KEY,'Content-Type':'application/x-www-form-urlencoded'}
            r   = getattr(requests,method.lower())(url,headers=hdr,timeout=15)
            return r.json()
        except Exception as e:
            if attempt<retries: time.sleep(2**attempt)
            else: log.error(f"API {ep}: {e}"); return {}

def pub(path, params=None):
    try: return requests.get(f"{BASE_URL}{path}",params=params or {},timeout=10).json()
    except: return {}

def _sf(v,d=0.0):
    if v is None: return d
    if isinstance(v,dict):
        for k in ('equity','balance','availableMargin','amount'):
            if k in v: return _sf(v[k],d)
        return d
    try: return float(v)
    except: return d

# ============================================================================
# INDICADORES
# ============================================================================

def ema(prices, n):
    if not prices: return 0.0
    if len(prices)<n: return sum(prices)/len(prices)
    k,e = 2/(n+1), prices[0]
    for p in prices[1:]: e=p*k+e*(1-k)
    return e

def rsi(prices, n=14):
    if len(prices)<n+1: return 50.0
    g=[max(prices[i]-prices[i-1],0) for i in range(1,len(prices))]
    l=[max(prices[i-1]-prices[i],0) for i in range(1,len(prices))]
    ag,al=sum(g[-n:])/n,sum(l[-n:])/n
    return 100.0 if al==0 else 100-100/(1+ag/al)

def atr_c(highs,lows,closes,n=14):
    if len(closes)<2: return 0.0
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1]))
         for i in range(1,min(len(closes),n+1))]
    return sum(trs)/len(trs) if trs else 0.0

def bollinger(closes,n=20,k=2.0):
    if len(closes)<n: return closes[-1],closes[-1],closes[-1],99.0
    w=closes[-n:]; mid=sum(w)/n
    std=math.sqrt(sum((x-mid)**2 for x in w)/n)
    u=mid+k*std; l=mid-k*std
    return u,mid,l,round((u-l)/mid*100 if mid>0 else 99.0,2)

def vwap_c(closes,highs,lows,volumes,n=50):
    n=min(n,len(closes))
    c=closes[-n:];h=highs[-n:];l=lows[-n:];v=volumes[-n:]
    tv=sum(((h[i]+l[i]+c[i])/3)*v[i] for i in range(len(c)))
    vs=sum(v)
    return tv/vs if vs>0 else closes[-1]

def z_vol(volumes,period=30):
    clean=[v for v in volumes if v>0]
    if len(clean)<period+1: return 0.0
    w=clean[-period-1:-1]
    if len(w)<8: return 0.0
    mean=sum(w)/len(w)
    if mean<=0: return 0.0
    std=math.sqrt(sum((v-mean)**2 for v in w)/len(w))
    if std<mean*0.03: return 0.0
    return round(min(max((clean[-1]-mean)/std,-8.0),8.0),2)

def vol_ratio(volumes,nr=3,nb=7):
    base=[v for v in volumes[-nb-nr:-nr] if v>0]
    if not base: return 1.0
    avg=sum(base)/len(base)
    if avg<=0: return 1.0
    rec=[v for v in volumes[-nr:] if v>=0]
    ra=sum(rec)/len(rec) if rec else 0
    return round(min(ra/avg,15.0),2)

def wt_series(closes,highs,lows,ch=10,avg=21):
    n=len(closes)
    if n<ch+avg+2: return [0.0]*n
    hlc3=[(highs[i]+lows[i]+closes[i])/3 for i in range(n)]
    k=2/(ch+1); esa=[hlc3[0]]*n
    for i in range(1,n): esa[i]=hlc3[i]*k+esa[i-1]*(1-k)
    d=[abs(hlc3[i]-esa[i]) for i in range(n)]
    de=[d[0]]*n
    for i in range(1,n): de[i]=d[i]*k+de[i-1]*(1-k)
    ci=[(hlc3[i]-esa[i])/(0.015*de[i]) if de[i]!=0 else 0 for i in range(n)]
    k2=2/(avg+1); wt1=[ci[0]]*n
    for i in range(1,n): wt1[i]=ci[i]*k2+wt1[i-1]*(1-k2)
    return wt1

def adx_vals(highs,lows,closes,di=14,sm=14):
    n=len(closes)
    if n<di+sm+2: return [20.0]*n,[20.0]*n,[20.0]*n
    tr=[0.0]*n; pdm=[0.0]*n; ndm=[0.0]*n
    for i in range(1,n):
        h,l,pc=highs[i],lows[i],closes[i-1]
        tr[i]=max(h-l,abs(h-pc),abs(l-pc))
        up,dn=highs[i]-highs[i-1],lows[i-1]-lows[i]
        pdm[i]=max(up,0) if up>dn else 0
        ndm[i]=max(dn,0) if dn>up else 0
    def wld(d,n):
        s=[0.0]*len(d)
        if n<len(d):
            s[n]=sum(d[1:n+1])
            for i in range(n+1,len(d)): s[i]=s[i-1]-s[i-1]/n+d[i]
        return s
    at=wld(tr,di); pt=wld(pdm,di); nt=wld(ndm,di)
    dip=[100*pt[i]/at[i] if at[i]>0 else 0 for i in range(n)]
    din=[100*nt[i]/at[i] if at[i]>0 else 0 for i in range(n)]
    dx=[abs(dip[i]-din[i])/(dip[i]+din[i])*100 if (dip[i]+din[i])>0 else 0 for i in range(n)]
    adxv=[0.0]*n; st=di+sm
    if st<n:
        adxv[st]=sum(dx[di:st+1])/sm
        for i in range(st+1,n): adxv[i]=(adxv[i-1]*(sm-1)+dx[i])/sm
    return adxv,dip,din

# ============================================================================
# AUROLO
# ============================================================================

def aurolo(closes,highs,lows,volumes,opens,atr_v=None):
    res={'puntos':0,'señal':'NO','p1':False,'p2':False,'p3':False,
         'ema55':0,'sl_price':0,'sl_pct':0,'wt_now':0,'adx_now':0,
         'dip':0,'din':0,'debilidad':False,'cambio_tend':False,'vol_ratio':1}
    if len(closes)<AUROLO_EMA+20: return res
    price=closes[-1]; e55=ema(closes,AUROLO_EMA); res['ema55']=e55
    e55p=ema(closes[:-1],AUROLO_EMA)
    res['cambio_tend']=(price>e55)!=(closes[-2]>e55p if len(closes)>=2 else price>e55)
    if price<=e55: return res

    av=atr_v or atr_c(highs,lows,closes,14)
    zp=max(min((av/price*100 if av>0 else 0.8)*1.2,2.5),0.4)
    zi=e55*(1-zp/100); zs=e55*(1+zp/100)
    toco=any(zi<=closes[i]<=zs for i in range(-min(8,len(closes)-1),0))
    res['p1']=toco and closes[-1]>e55*0.998

    wt1=wt_series(closes,highs,lows,10,21)
    wn=wt1[-1]; wp=wt1[-2] if len(wt1)>=2 else wn; wp2=wt1[-3] if len(wt1)>=3 else wp
    res['wt_now']=wn
    res['p2']=(wn>wp and (wp<=-15 or wp2<=-42)) or (wn<=-42 and wn>wp)

    adxv,dip,din=adx_vals(highs,lows,closes,14,14)
    res['adx_now']=adxv[-1]; res['dip']=dip[-1]; res['din']=din[-1]
    res['p3']=adxv[-1]>=18 and dip[-1]>din[-1]

    pts=int(res['p1'])+int(res['p2'])+int(res['p3']); res['puntos']=pts

    mr=min(lows[-8:-1]) if len(lows)>=8 else lows[-1]
    sl_c=min(mr-av*SL_ATR_M, e55*(1-0.15/100))
    sl=max(min(sl_c,price*(1-SL_MIN/100)),price*(1-SL_MAX/100))
    if sl>=price: sl=price*(1-SL_MIN/100)
    slp=(price-sl)/price*100
    if slp<SL_MIN: sl=price*(1-SL_MIN/100); slp=SL_MIN
    res['sl_price']=round(sl,8); res['sl_pct']=round(slp,3)

    adxp=adxv[-2] if len(adxv)>=2 else adxv[-1]
    res['debilidad']=bool(adxv[-1]<adxp and wn<wp and wn>=55 and din[-1]>dip[-1]*0.80)

    va=sum(volumes[-6:-1])/5 if len(volumes)>=6 else volumes[-1]
    res['vol_ratio']=volumes[-1]/va if va>0 else 1

    if pts>=3: res['señal']='LONG_3/3'
    elif pts==2: res['señal']='LONG_2/3'
    elif pts==1: res['señal']='LONG_1/3'
    return res

def _sym_ok(sym):
    if not sym.endswith('-USDT'): return False
    b=sym.replace('-USDT','').upper()
    if b in EXCL: return False
    if any(b.startswith(p) for p in EXCL_PFX): return False
    if re.search(r'[A-Z]{2,}\d{3,}',b): return False
    return True

# ============================================================================
# SCANNER
# ============================================================================

class Scanner:
    def __init__(self, tg_fn):
        self._tg=tg_fn; self.oi_cache={}; self.alerted={}
        self.daily_log=[]; self.daily_date=datetime.utcnow().date()
        self.hot=[]; self._lock=threading.Lock(); self._running=True

    def stop(self): self._running=False
    def get_hot(self,mc=HOT_CONF,n=50):
        with self._lock: return [s for s,c in self.hot if c>=mc][:n]
    def get_conf(self,sym):
        with self._lock:
            for s,c in self.hot:
                if s==sym: return c
        return 0

    def _kl(self,sym,tf='5m',lim=100):
        d=pub('/openApi/swap/v3/quote/klines',{'symbol':sym,'interval':tf,'limit':lim})
        if d.get('code')==0 and d.get('data') and len(d['data'])>=15:
            kl=d['data']
            return {'c':[float(k['close'])for k in kl],'h':[float(k['high'])for k in kl],
                    'l':[float(k['low'])for k in kl],'v':[float(k['volume'])for k in kl],
                    'o':[float(k['open'])for k in kl]}
        return None

    def _scan1(self, sym):
        if not _sym_ok(sym): return None
        try:
            d=pub('/openApi/swap/v2/quote/ticker',{'symbol':sym})
            if d.get('code')!=0 or not d.get('data'): return None
            t=d['data']; price=float(t.get('lastPrice',0)); chg=float(t.get('priceChangePercent',0))
            if price<=0 or chg>35 or chg<-25: return None

            k5=self._kl(sym,'5m',100)
            if not k5 or len(k5['c'])<30: return None
            c5=k5['c'];h5=k5['h'];l5=k5['l'];v5=k5['v'];o5=k5['o']

            conf=0; sigs=[]

            _,_,_,bbw=bollinger(c5)
            if bbw<2.0: pts=min(int(25*(2.0-bbw)/2.0+10),25); conf+=pts; sigs.append(f"🎯 BB {bbw:.1f}% (+{pts})")
            elif bbw<3.5: conf+=8; sigs.append(f"📊 BB {bbw:.1f}% (+8)")

            zv=z_vol(v5,30)
            if zv>=4.0: conf+=20; sigs.append(f"🐳 Z={zv:.1f} (+20)")
            elif zv>=2.5: conf+=13; sigs.append(f"⚡ Z={zv:.1f} (+13)")
            elif zv>=1.5: conf+=6; sigs.append(f"📈 Z={zv:.1f} (+6)")

            vr=vol_ratio(v5,3,7)
            if vr>=3.0: conf+=12; sigs.append(f"🔥 Vol {vr:.1f}x (+12)")
            elif vr>=2.0: conf+=7; sigs.append(f"📊 Vol {vr:.1f}x (+7)")
            elif vr>=1.3: conf+=3

            oic=0.0
            try:
                do=pub('/openApi/swap/v2/quote/openInterest',{'symbol':sym})
                oi_c=float((do.get('data') or {}).get('openInterest',0) or 0)
                oi_p=self.oi_cache.get(sym,{}).get('oi',oi_c)
                oic=(oi_c-oi_p)/oi_p*100 if oi_p>0 else 0
                self.oi_cache[sym]={'oi':oi_c}
                if oic>=5 and abs(chg)<3: conf+=20; sigs.append(f"🐳 OI +{oic:.1f}% plano (+20)")
                elif oic>=2: conf+=10; sigs.append(f"📈 OI +{oic:.1f}% (+10)")
                elif oic<=-3: conf-=5
            except: pass

            fund=0.0
            try:
                df=pub('/openApi/swap/v2/quote/premiumIndex',{'symbol':sym})
                fund=float((df.get('data') or {}).get('lastFundingRate',0) or 0)*100
                if fund<=-0.04: conf+=15; sigs.append(f"💰 Fund {fund:.3f}% (+15)")
                elif fund<=-0.01: conf+=8; sigs.append(f"💰 Fund {fund:.3f}% (+8)")
                elif fund<=0: conf+=3
                elif fund>=0.06: conf-=7
            except: pass

            bull=bear=0.0
            for i in range(-min(20,len(c5)),0):
                c=c5[i]; o=o5[i] if o5 else c5[i-1]; v=v5[i]
                if v>0:
                    if c>o: bull+=v
                    elif c<o: bear+=v
            cv=bull/(bull+bear) if (bull+bear)>0 else 0.5
            if cv>=0.65: pts=min(int((cv-0.5)*60),20); conf+=pts; sigs.append(f"🌊 CVD {int(cv*100)}% (+{pts})")
            elif cv>=0.52: conf+=5
            elif cv<=0.40: conf-=8
            elif cv<=0.48: conf-=4

            rsiv=rsi(c5,14)
            if rsiv<35 and rsiv>rsi(c5[:-1],14): conf+=12; sigs.append(f"📈 RSI {rsiv:.0f} rebota (+12)")
            elif rsiv<45: conf+=6; sigs.append(f"📈 RSI {rsiv:.0f} OS (+6)")
            elif rsiv>75: conf-=10

            broke=False
            if len(h5)>=22:
                res=max(h5[-21:-1]); broke=c5[-1]>res and c5[-2]<=res
                if broke:
                    bs=(c5[-1]/res-1)*100
                    conf+=(18 if bs>0.5 else 12); sigs.append(f"🚀 BREAKOUT {bs:.2f}%")

            tf_b=0
            if ema(c5,9)>ema(c5,21): tf_b+=1
            try:
                k15=self._kl(sym,'15m',40)
                if k15 and len(k15['c'])>=20 and ema(k15['c'],9)>ema(k15['c'],21): tf_b+=1
            except: pass
            try:
                k1h=self._kl(sym,'1h',35)
                if k1h and len(k1h['c'])>=20 and ema(k1h['c'],9)>ema(k1h['c'],21): tf_b+=1
            except: pass
            if tf_b>=3: conf+=20; sigs.append("✅ MTF 3/3 (+20)")
            elif tf_b==2: conf+=12; sigs.append("✅ MTF 2/3 (+12)")
            elif tf_b==1: conf+=5

            if bbw<2.0 and broke: conf+=15; sigs.append("💥 SQUEEZE+BREAK (+15)")
            if zv>=2.5 and oic>=2 and cv>=0.55: conf+=10; sigs.append("🐳 BALLENA+OI (+10)")

            conf=min(max(conf,0),100)
            if conf<MIN_CONF: return None
            return {'symbol':sym,'confidence':conf,'price':price,'change':chg,
                    'signals':sigs,'tf':tf_b,'zv':zv,'cvd':cv,'rsi':round(rsiv,1),
                    'bbw':bbw,'oi_chg':round(oic,2),'fund':round(fund,4),'vr':vr,'broke':broke}
        except Exception as e: log.debug(f"[SCAN] {sym}: {e}"); return None

    def _syms(self):
        d=pub('/openApi/swap/v2/quote/ticker')
        if d.get('code')!=0: return []
        items=[]
        for t in d.get('data',[]):
            sym=t.get('symbol','')
            if not _sym_ok(sym): continue
            try:
                price=float(t.get('lastPrice',0)); vol=float(t.get('volume',0))*price
                if vol>=MIN_VOL and price>0: items.append((sym,vol))
            except: continue
        items.sort(key=lambda x:x[1],reverse=True)
        return [s for s,_ in items[:MAX_SYMS]]

    def scan_once(self):
        syms=self._syms()
        if not syms: log.warning("[SCAN] Sin símbolos"); return []
        results=[]
        with ThreadPoolExecutor(max_workers=SCAN_W) as ex:
            futs={ex.submit(self._scan1,s):s for s in syms}
            for fut in as_completed(futs):
                try:
                    r=fut.result()
                    if r: results.append(r)
                except: pass
        results.sort(key=lambda x:x['confidence'],reverse=True)
        with self._lock: self.hot=[(r['symbol'],r['confidence']) for r in results]
        log.info(f"  [SCAN] {len(results)} señales | Top5: "+" ".join(f"{r['symbol']}({r['confidence']}%)" for r in results[:5]))
        for r in results:
            if r['confidence']<HOT_CONF: continue
            prev=self.alerted.get(r['symbol'])
            if prev and time.time()-prev[1]<2700 and r['confidence']<prev[0]+15: continue
            lvl="🔴 CRÍTICO" if r['confidence']>=80 else "🟠 ALTO" if r['confidence']>=65 else "🟡 SEÑAL"
            sigs_txt="\n".join(f"  {s}" for s in r['signals'][:5])
            self._tg(
                f"{lvl} <b>{r['symbol']}</b> — <b>{r['confidence']}%</b>\n"
                f"💲 ${r['price']:.6f} | 24h: {r['change']:+.2f}%\n"
                f"BB:{r['bbw']:.1f}% | Z:{r['zv']:.1f} | CVD:{int(r['cvd']*100)}%\n"
                f"OI:{r['oi_chg']:+.1f}% | MTF:{r['tf']}/3 | RSI:{r['rsi']:.0f}\n"
                f"Señales:\n{sigs_txt}\n"
                f"⏰ {datetime.utcnow().strftime('%H:%M UTC')}"
                +("\n🚀 <b>BREAKOUT</b>" if r.get('broke') else "")
            )
            self.alerted[r['symbol']]=(r['confidence'],time.time())
            self.daily_log.append(r)
        return results

    def daily_summary(self):
        if not self.daily_log: return
        n=len(self.daily_log)
        top=sorted(self.daily_log,key=lambda x:x['confidence'],reverse=True)[:5]
        self._tg(
            f"<b>📊 Scanner diario — {self.daily_date}</b>\n"
            f"Alertas: {n}\n"
            f"Top: "+", ".join(f"{a['symbol']}({a['confidence']}%)" for a in top)
        )
        self.daily_log=[]; self.daily_date=datetime.utcnow().date()

    def run_loop(self):
        log.info("[SCAN] Thread arrancado")
        while self._running:
            try:
                today=datetime.utcnow().date()
                if today!=self.daily_date: self.daily_summary()
                self.scan_once()
            except Exception as e: log.error(f"[SCAN] {e}")
            time.sleep(SCAN_INT)

# ============================================================================
# ✅ FIX1+FIX2+FIX3: APRENDIZAJE REPARADO
# ============================================================================

class Learn:
    def __init__(self):
        self.history=[];self.sym_stats=defaultdict(lambda:{'w':0,'l':0,'pnl':0.0,'n':0})
        # ✅ FIX1: Arranca en MIN_SCORE, no puede superar SCORE_MAX_CAP
        self.opt_score=MIN_SCORE
        self.blacklist=set();self.streak=0;self.last10=[]
        self.fwin=defaultdict(int);self.floss=defaultdict(int)
        self.sboost={};self.daily_losers=set();self._day=datetime.utcnow().date()

    def _dr(self):
        today=datetime.utcnow().date()
        if today!=self._day: self.daily_losers=set();self._day=today

    # ✅ FIX5: solo bloquea si la pérdida fue significativa
    def record(self,sym,score,pnl,win,hora=None,pts=0,reason='?',factors=None):
        self._dr()
        rec={'sym':sym,'score':score,'pnl':pnl,'win':win,
             'hora':hora or datetime.utcnow().hour,'pts':pts,'reason':reason,'factors':factors or []}
        self.history.append(rec);self.last10.append(rec)
        if len(self.last10)>10: self.last10.pop(0)
        s=self.sym_stats[sym];s['n']+=1;s['pnl']+=pnl
        if win: s['w']+=1;self.streak=0
        else:   s['l']+=1;self.streak+=1
        # ✅ FIX5: solo bloquea si pérdida > SL_BLOCK_MIN_PCT del capital
        loss_pct=abs(pnl)/POS_SIZE*100 if POS_SIZE>0 else 0
        if CD_SL_TODAY and not win and 'SL' in reason.upper() and loss_pct>=SL_BLOCK_MIN_PCT:
            self.daily_losers.add(sym)
            log.info(f"  [LEARN] {sym} bloqueado hoy (pérdida {loss_pct:.1f}%)")
        for f in (factors or []):
            if win: self.fwin[f]+=1
            else:   self.floss[f]+=1
        self._adj()

    def _adj(self):
        # ✅ FIX2: Cap máximo SCORE_MAX_CAP (no 88)
        cap = SCORE_MAX_CAP
        if len(self.history)>=10:
            wr=sum(1 for t in self.last10 if t['win'])/len(self.last10)
            # ✅ FIX2: ajustes más suaves — +1/+2 max, nunca +4
            if   wr<0.30: self.opt_score=min(self.opt_score+2,cap)
            elif wr<0.40: self.opt_score=min(self.opt_score+1,cap)
            elif wr>0.60: self.opt_score=max(self.opt_score-2,MIN_SCORE)
            elif wr>0.70: self.opt_score=max(self.opt_score-3,MIN_SCORE)
        # ✅ FIX1: doble garantía — NUNCA supera SCORE_MAX_CAP
        self.opt_score=max(min(self.opt_score, cap), MIN_SCORE)

        for sym,s in self.sym_stats.items():
            tot=s['w']+s['l']
            if tot>=5 and s['pnl']<-1.5 and s['w']/tot<0.25 and sym not in self.blacklist:
                self.blacklist.add(sym)
                log.info(f"  [LEARN] {sym} → blacklist (WR {s['w']/tot*100:.0f}%, pnl ${s['pnl']:.2f})")
        if len(self.history)>=15:
            for f in set(list(self.fwin)+list(self.floss)):
                w=self.fwin.get(f,0);l=self.floss.get(f,0)
                if w+l<5: continue
                wr=w/(w+l)
                if wr<0.30: self.sboost[f]=-6
                elif wr>0.70: self.sboost[f]=+5
                else: self.sboost.pop(f,None)

    def ok(self,sym,score):
        self._dr()
        if sym in self.blacklist:     return False,"blacklist"
        if sym in self.daily_losers:  return False,"sl_hoy"
        thr=max(self.opt_score,MIN_SCORE)
        if score<thr:                 return False,f"score_{int(score)}<{int(thr)}"
        if self.streak>=MAX_STREAK:   return False,f"streak_{self.streak}"
        return True,"ok"

    def adj(self,factors): return sum(self.sboost.get(f,0) for f in factors)

    def reset_score(self):
        """✅ FIX7: reset manual del score sin borrar historial"""
        old=self.opt_score
        self.opt_score=MIN_SCORE
        self.streak=0
        self.daily_losers=set()
        log.info(f"  [LEARN] Reset: {old:.0f} → {MIN_SCORE}")
        return old

    def save(self,fp='/tmp/v81.json'):
        try: json.dump({'history':self.history[-200:],'sym_stats':dict(self.sym_stats),
                        'opt_score':self.opt_score,'blacklist':list(self.blacklist),
                        'fwin':dict(self.fwin),'floss':dict(self.floss),
                        'sboost':self.sboost,'daily_losers':list(self.daily_losers)},
                       open(fp,'w'),indent=2)
        except: pass

    def load(self,fp='/tmp/v81.json'):
        # ✅ también intenta cargar v8.0
        for path in [fp,'/tmp/v8.json','/tmp/bot_v71.json','/tmp/bot_learn.json']:
            try:
                if not os.path.exists(path): continue
                d=json.load(open(path))
                self.history=d.get('history',[])
                self.sym_stats=defaultdict(lambda:{'w':0,'l':0,'pnl':0.0,'n':0},d.get('sym_stats',{}))
                self.blacklist=set(d.get('blacklist',[]))
                self.fwin=defaultdict(int,d.get('fwin',{}))
                self.floss=defaultdict(int,d.get('floss',{}))
                self.sboost=d.get('sboost',{})
                self.daily_losers=set(d.get('daily_losers',[]))
                # ✅ FIX1: al cargar, fuerza el cap
                raw_score=d.get('opt_score',MIN_SCORE)
                self.opt_score=max(min(raw_score, SCORE_MAX_CAP), MIN_SCORE)
                if raw_score > SCORE_MAX_CAP:
                    log.warning(f"  [LEARN] opt_score corregido: {raw_score:.0f} → {self.opt_score:.0f} (cap={SCORE_MAX_CAP})")
                log.info(f"  [LEARN] {len(self.history)} trades | Score:{int(self.opt_score)} | BL:{len(self.blacklist)} | DailyLosers:{len(self.daily_losers)}")
                return
            except Exception as e:
                log.debug(f"  [LEARN] no cargó {path}: {e}")
                continue
        log.info("  [LEARN] Sin historial previo — arrancando fresh")

# ============================================================================
# ✅ FIX3+FIX8: REJECTION TRACKER
# ============================================================================

class RejectionTracker:
    """Registra por qué se rechazan señales — para /why command"""
    def __init__(self,maxlen=100):
        self._q=deque(maxlen=maxlen)
        self._counts=defaultdict(int)

    def add(self,sym,reason):
        self._q.append({'sym':sym,'reason':reason,'t':datetime.utcnow()})
        self._counts[reason]+=1

    def last_n(self,n=5):
        items=list(self._q)[-n:]
        return list(reversed(items))

    def top_reasons(self,n=8):
        return sorted(self._counts.items(),key=lambda x:x[1],reverse=True)[:n]

    def summary_text(self):
        top=self.top_reasons()
        if not top: return "Sin rechazos registrados"
        return "\n".join(f"  {r}: {c}x" for r,c in top)

# ============================================================================
# BOT v8.1
# ============================================================================

class Bot:
    _opening=False

    def __init__(self):
        log.info("="*68)
        log.info("  BINGX BOT v8.1 — FIXED")
        log.info(f"  ${POS_SIZE} | {LEVERAGE}x | Max:{MAX_TRADES} | {MAX_DAILY}/día")
        log.info(f"  Score: bull≥{SCORE_BULL} neutral≥{SCORE_NEUTRAL} | CAP={SCORE_MAX_CAP}")
        log.info(f"  Aurolo≥{AUROLO_MIN}/3 | BTC crash>{BTC_CRASH}% bloquea")
        log.info(f"  Vol mín: ${MIN_VOL/1e3:.0f}K | {MAX_SYMS} símbolos")
        log.info("="*68)

        self.symbols=[];self.trades={}
        self._contracts={};self._cooldowns={};self._pending={}
        self._last_report=datetime.now()-timedelta(hours=3)
        self._last_zombie=0
        self._btc_1h=0.0;self._btc_4h=0.0;self._btc_ok=True
        self._regime='neutral';self._regime_until=None;self._breadth=0.5
        self._mode='hedge';self._daily_pnl=0.0;self._daily_trades=0
        self._daily_date=datetime.utcnow().date()
        self._equity_start=EQUITY
        self._cb_active=False;self._cb_until=None
        self._paused=False
        # ✅ FIX8: contador de señales rechazadas
        self._rejected = RejectionTracker()
        self._signals_seen=0;self._signals_blocked=0

        self.learn=Learn();self.learn.load()
        self.stats={'exec':0,'closed':0,'wins':0,'losses':0,'pnl':0.0,'fees':0.0}

        self.scanner=Scanner(self._tg)
        threading.Thread(target=self.scanner.run_loop,daemon=True).start()
        self._tg_offset=0
        threading.Thread(target=self._cmd_loop,daemon=True).start()

        if not self._connect(): sys.exit(1)
        self._detect_mode()
        self._load_contracts()
        self._refresh_symbols()
        nk=self._nuke_zombies()
        self._recover()

        self._tg(
            f"<b>🤖 BOT v8.1 — FIXED</b>\n"
            f"{len(self.symbols)} símbolos | Max {MAX_TRADES} pos | {MAX_DAILY}/día\n"
            f"Score bull≥{SCORE_BULL} | neutral≥{SCORE_NEUTRAL} | CAP={SCORE_MAX_CAP}\n"
            f"opt_score actual: {int(self.learn.opt_score)}\n"
            f"🧟 Zombies: {nk} | ♻️ {len(self.trades)} recuperadas\n"
            f"/status /top /trades /why /debug SYM /reset /pause /resume"
        )

    # ============================================================================
    # ✅ FIX6+FIX7+FIX8: COMANDOS AMPLIADOS
    # ============================================================================

    def _cmd_loop(self):
        while True:
            try:
                r=requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                               params={'offset':self._tg_offset,'timeout':30},timeout=35)
                if r.status_code!=200: time.sleep(5); continue
                for upd in r.json().get('result',[]):
                    self._tg_offset=upd['update_id']+1
                    msg=upd.get('message',{})
                    txt=msg.get('text','').strip()
                    cid=str(msg.get('chat',{}).get('id',''))
                    if not txt or cid!=str(TG_CHAT): continue
                    self._cmd(txt.lower(), txt)
            except Exception as e: log.debug(f"[CMD] {e}"); time.sleep(10)

    def _cmd(self, cmd, raw=''):
        total=self.stats['wins']+self.stats['losses']
        wr=self.stats['wins']/total*100 if total else 0

        if '/status' in cmd:
            hot=self.scanner.get_hot(HOT_CONF,5)
            self._tg(
                f"<b>📊 STATUS v8.1</b>\n"
                f"Pos: {len(self.trades)}/{MAX_TRADES} | Hoy: {self._daily_trades}/{MAX_DAILY}\n"
                f"PnL hoy: ${self._daily_pnl:+.4f} | WR: {wr:.0f}% ({total}t)\n"
                f"Régimen: {self._regime} | Breadth: {int(self._breadth*100)}%\n"
                f"BTC 1h: {self._btc_1h:+.2f}% 4h: {self._btc_4h:+.2f}%\n"
                f"opt_score: {int(self.learn.opt_score)} (cap={SCORE_MAX_CAP})\n"
                f"Score mín: {int(self._score_min())}\n"
                f"Señales vistas: {self._signals_seen} | Bloqueadas: {self._signals_blocked}\n"
                f"BL: {len(self.learn.blacklist)} | DL: {len(self.learn.daily_losers)}\n"
                f"Estado: {'⏸️ PAUSADO' if self._paused else '✅ ACTIVO'}\n"
                f"🔥 Hot: {', '.join(hot) if hot else 'buscando...'}"
            )

        elif '/top' in cmd:
            with self.scanner._lock: top=self.scanner.hot[:10]
            if not top: self._tg("⏳ Scanner buscando...")
            else: self._tg("<b>🔥 TOP 10</b>\n"+"\n".join(f"  {'🔴' if c>=80 else '🟠' if c>=65 else '🟡'} {s} — {c}%" for s,c in top))

        elif '/trades' in cmd:
            if not self.trades: self._tg("Sin posiciones")
            else:
                lines=[]
                for sym,t in self.trades.items():
                    tk=self._ticker(sym); cur=tk['price'] if tk else t['entry']
                    pct=(cur-t['entry'])/t['entry']*100
                    lines.append(f"  {'✅' if pct>0 else '📌'} {sym}: {pct:+.2f}%")
                self._tg("<b>📍 POSICIONES</b>\n"+"\n".join(lines))

        # ✅ FIX8: /why — últimos rechazos y motivos más frecuentes
        elif '/why' in cmd:
            last=self._rejected.last_n(5)
            top=self._rejected.top_reasons(8)
            lines=[]
            if last:
                lines.append("<b>Últimos rechazos:</b>")
                for r in last:
                    lines.append(f"  {r['sym']}: {r['reason']} ({r['t'].strftime('%H:%M')})")
            if top:
                lines.append("<b>Motivos más frecuentes:</b>")
                for reason,cnt in top:
                    lines.append(f"  {reason}: {cnt}x")
            if not lines: lines=["Sin rechazos registrados aún"]
            self._tg("\n".join(lines))

        # ✅ FIX7: /reset — resetea opt_score y streak
        elif '/reset' in cmd:
            old=self.learn.reset_score()
            self._tg(
                f"<b>🔄 RESET LEARNING</b>\n"
                f"opt_score: {old:.0f} → {MIN_SCORE:.0f}\n"
                f"streak: 0\n"
                f"daily_losers: limpiado\n"
                f"Score mín ahora: {int(self._score_min())}"
            )

        # ✅ FIX6: /debug SYM — análisis detallado de por qué pasa/falla
        elif '/debug' in cmd:
            parts=raw.strip().split()
            sym=parts[1].upper() if len(parts)>1 else ''
            if not sym.endswith('-USDT') and sym: sym=sym+'-USDT'
            if not sym:
                self._tg("Uso: /debug SYMBOL (ej: /debug BTC-USDT)")
            else:
                threading.Thread(target=self._debug_sym,args=(sym,),daemon=True).start()

        elif '/blacklist' in cmd:
            bl=list(self.learn.blacklist)
            dl=list(self.learn.daily_losers)
            self._tg(
                f"<b>🚫 Blacklist</b> ({len(bl)}): {', '.join(bl[:15]) or 'vacía'}\n"
                f"<b>🔒 Hoy bloqueados</b> ({len(dl)}): {', '.join(dl[:15]) or 'ninguno'}"
            )

        elif '/pause' in cmd: self._paused=True; self._tg("⏸️ PAUSADO")
        elif '/resume' in cmd: self._paused=False; self._tg("▶️ REANUDADO")
        elif '/help' in cmd or '/start' in cmd:
            self._tg(
                "<b>Comandos v8.1</b>\n"
                "/status — estado general\n"
                "/top — top 10 scanner\n"
                "/trades — posiciones abiertas\n"
                "/why — por qué no entra\n"
                "/debug SYM — análisis de símbolo\n"
                "/reset — resetea opt_score\n"
                "/blacklist — símbolos bloqueados\n"
                "/pause /resume"
            )

    def _debug_sym(self, sym):
        """✅ FIX6: análisis detallado de un símbolo para Telegram"""
        try:
            self._tg(f"🔍 Analizando {sym}...")
            result = self._analyze_verbose(sym)
            self._tg(result)
        except Exception as e:
            self._tg(f"❌ Error debug {sym}: {e}")

    def _analyze_verbose(self, sym):
        """✅ FIX3: versión de analyze() que devuelve texto en vez de None"""
        lines=[f"<b>🔬 DEBUG {sym}</b>"]
        checks=[]

        # Cooldown
        if not self._cd_ok(sym):
            ts=self._cooldowns.get(sym)
            resume=ts[0] if isinstance(ts,tuple) else ts
            mins=int((resume-time.time())/60)
            checks.append(f"❌ Cooldown: {mins}min restantes")
        else:
            checks.append("✅ Cooldown OK")

        # En trades
        if sym in self.trades:
            checks.append(f"❌ Ya en posición")
        else:
            checks.append("✅ Sin posición abierta")

        # Pending
        if sym in self._pending:
            checks.append(f"❌ Orden pendiente")

        # Blacklist / daily losers
        ok,reason=self.learn.ok(sym, 999)  # score=999 para saltarse ese check
        if not ok and reason != f"score_999<{int(max(self.learn.opt_score,MIN_SCORE))}":
            checks.append(f"❌ Learn.ok: {reason}")
        else:
            checks.append("✅ No en blacklist/daily_losers")

        # Régimen
        ro,rr=self._regime_ok()
        checks.append(f"{'✅' if ro else '❌'} Régimen: {self._regime} ({rr}) Breadth:{int(self._breadth*100)}%")

        # BTC
        checks.append(f"{'✅' if self._btc_ok else '❌'} BTC 1h: {self._btc_1h:+.2f}% (crash>{BTC_CRASH}% bloquea)")

        # CB / daily
        checks.append(f"{'✅' if not self._cb_active else '❌'} CB: {'activo' if self._cb_active else 'inactivo'}")
        checks.append(f"{'✅' if self._daily_trades<MAX_DAILY else '❌'} Daily: {self._daily_trades}/{MAX_DAILY}")
        checks.append(f"{'✅' if len(self.trades)<MAX_TRADES else '❌'} MaxTrades: {len(self.trades)}/{MAX_TRADES}")
        checks.append(f"{'✅' if not self._paused else '❌'} Bot: {'PAUSADO' if self._paused else 'activo'}")

        # Klines
        c5,h5,l5,v5,o5=self._klines(sym,'5m',130)
        if not c5 or len(c5)<AUROLO_EMA+20:
            checks.append(f"❌ Datos 5m insuficientes: {len(c5) if c5 else 0} velas (necesita {AUROLO_EMA+20})")
            lines.extend(checks)
            return "\n".join(lines)
        checks.append(f"✅ Datos 5m: {len(c5)} velas")

        tk=self._ticker(sym)
        price=tk['price'] if tk else 0
        chg=tk['change'] if tk else 0
        if not tk or price<=0:
            checks.append(f"❌ Ticker inválido")
            lines.extend(checks)
            return "\n".join(lines)
        checks.append(f"✅ Precio: ${price:.6f} ({chg:+.2f}%)")
        if chg>30 or chg<-20:
            checks.append(f"❌ Cambio extremo: {chg:.1f}%")

        # Trend 1h
        c1h,h1h,l1h,v1h,_=self._klines(sym,'1h',35)
        if c1h and len(c1h)>=20:
            e9_1h=ema(c1h,9); e21_1h=ema(c1h,21)
            rsi_1h=rsi(c1h,14)
            trend_ok=e9_1h>e21_1h
            checks.append(f"{'✅' if trend_ok else '❌'} 1h trend: EMA9({e9_1h:.4f}) {'>' if trend_ok else '<='} EMA21({e21_1h:.4f})")
            checks.append(f"{'✅' if rsi_1h<=75 else '❌'} RSI 1h: {rsi_1h:.1f}")
        else:
            checks.append("❌ Sin datos 1h")

        # 4h
        c4h,*_=self._klines(sym,'4h',25)
        if c4h and len(c4h)>=21:
            e9_4h=ema(c4h,9); e21_4h=ema(c4h,21)
            trend_4h=1 if e9_4h>e21_4h else (-1 if e9_4h<e21_4h*0.99 else 0)
            if trend_4h==-1 and self._regime not in ('bull',):
                checks.append(f"❌ 4h bajista ({e9_4h:.4f}<{e21_4h:.4f}*0.99) y régimen={self._regime}")
            else:
                checks.append(f"✅ 4h OK (trend={trend_4h})")

        # Aurolo
        atr_v=atr_c(h5,l5,c5,14)
        atr_pct=atr_v/price*100 if price>0 else 0
        sig=aurolo(c5,h5,l5,v5,o5,atr_v)
        checks.append(f"{'✅' if sig['puntos']>=AUROLO_MIN else '❌'} Aurolo: {sig['puntos']}/3 [{sig['señal']}] (P1={sig['p1']} P2={sig['p2']} P3={sig['p3']})")
        checks.append(f"{'❌' if sig['cambio_tend'] else '✅'} Cambio tendencia: {sig['cambio_tend']}")
        checks.append(f"{'✅' if sig['vol_ratio']>=VOL_R_MIN else '❌'} Vol ratio: {sig['vol_ratio']:.2f} (min={VOL_R_MIN})")
        checks.append(f"  ATR: {atr_pct:.2f}% | EMA55: {sig['ema55']:.6f}")
        checks.append(f"  WT: {sig['wt_now']:.1f} | ADX: {sig['adx_now']:.1f} | SL: -{sig['sl_pct']:.2f}%")

        # Score estimado
        score=0
        if sig['puntos']==3: score+=55
        elif sig['puntos']==2: score+=35
        if sig['p1']: score+=10
        if sig['p2']: score+=10
        if sig['p3']: score+=10
        if c1h and len(c1h)>=20 and ema(c1h,9)>ema(c1h,21): score+=12
        score_min=self._score_min()
        checks.append(f"{'✅' if score>=score_min else '❌'} Score estimado: ~{score} (necesita {int(score_min)}) [sin bonuses]")
        checks.append(f"  opt_score={int(self.learn.opt_score)} | cap={SCORE_MAX_CAP}")

        # Scanner
        sc=self.scanner.get_conf(sym)
        checks.append(f"  Scanner conf: {sc}%")

        lines.extend(checks)
        return "\n".join(lines)

    # ============================================================================
    # BOT CORE (igual que v8.0 pero con logging de rechazos)
    # ============================================================================

    def _connect(self):
        global AUTO, EQUITY
        if not AUTO: return True
        if not API_KEY or not API_SECRET: AUTO=False; return False
        d=api('GET','/openApi/swap/v2/user/balance')
        if d.get('code')==0:
            b=d.get('data',{})
            if isinstance(b,list):
                for item in b:
                    v=_sf(item)
                    if v>0: EQUITY=v; break
            else:
                eq=_sf(b.get('equity',b.get('balance',0)))
                if eq<=0:
                    for _,val in b.items():
                        v=_sf(val)
                        if v>0: eq=v; break
                if eq>0: EQUITY=eq
            self._equity_start=EQUITY
            log.info(f"✅ BingX | ${EQUITY:.2f} USDT"); return True
        AUTO=False; return False

    def _detect_mode(self):
        try:
            d=api('GET','/openApi/swap/v2/user/positions',{'symbol':'BTC-USDT'})
            for p in (d.get('data') or []):
                s=str(p.get('positionSide','')).upper()
                if s in ('LONG','SHORT'): self._mode='hedge'; log.info("  HEDGE"); return
        except: pass
        log.info("  HEDGE (default)")

    def _load_contracts(self):
        d=pub('/openApi/swap/v2/quote/contracts')
        if d.get('code')==0:
            for c in d.get('data',[]):
                s=c.get('symbol','')
                if s: self._contracts[s]={'step':float(c.get('tradeMinQuantity',1)),
                                          'prec':int(c.get('quantityPrecision',2)),
                                          'ctval':float(c.get('contractSize',1))}
            log.info(f"  {len(self._contracts)} contratos")

    def _refresh_symbols(self):
        d=pub('/openApi/swap/v2/quote/ticker')
        if d.get('code')!=0: return
        items=[]
        for t in d.get('data',[]):
            sym=t.get('symbol','')
            if not _sym_ok(sym): continue
            try:
                price=float(t.get('lastPrice',0)); vol=float(t.get('volume',0))*price
                if vol>=MIN_VOL and price>0: items.append((sym,vol))
            except: continue
        items.sort(key=lambda x:x[1],reverse=True)
        self.symbols=[s for s,_ in items[:MAX_SYMS]]
        log.info(f"  {len(self.symbols)} símbolos (vol≥${MIN_VOL/1e3:.0f}K)")

    def _scan_order(self):
        hot=self.scanner.get_hot(HOT_CONF,60)
        rest=[s for s in self.symbols if s not in hot]
        return hot+rest

    def _nuke_zombies(self):
        if not AUTO: return 0
        protected=set()
        for sym in list(self.trades.keys()):
            d=api('GET','/openApi/swap/v2/trade/openOrders',{'symbol':sym})
            for o in (d.get('data',{}).get('orders') or []):
                otype=str(o.get('type','')).upper()
                if 'STOP' in otype or 'TRAILING' in otype:
                    oid=o.get('orderId')
                    if oid: protected.add(str(oid))
        killed=0; now_ms=int(time.time()*1000)
        all_syms=set(self.symbols or [])
        try:
            dp=api('GET','/openApi/swap/v2/user/positions',{})
            for p in (dp.get('data') or []):
                s=p.get('symbol','')
                if s: all_syms.add(s)
        except: pass
        for sym in list(all_syms)[:80]:
            try:
                d=api('GET','/openApi/swap/v2/trade/openOrders',{'symbol':sym})
                for o in (d.get('data',{}).get('orders') or []):
                    oid=str(o.get('orderId','')); otype=str(o.get('type','')).upper()
                    otime=int(o.get('time',now_ms) or now_ms); age=(now_ms-otime)/60000
                    if oid in protected: continue
                    if otype in ('LIMIT','TRIGGER','STOP','TAKE_PROFIT') and (sym not in self.trades or age>20):
                        r=api('DELETE','/openApi/swap/v2/trade/order',{'symbol':sym,'orderId':oid})
                        if r.get('code')==0: killed+=1
                        time.sleep(0.12)
            except: pass
        if killed: log.info(f"  🧟 {killed} zombies")
        self._last_zombie=time.time(); return killed

    def _update_regime(self):
        c4h,*_=self._klines('BTC-USDT','4h',10)
        if c4h and len(c4h)>=4:
            self._btc_4h=(c4h[-1]-c4h[-4])/c4h[-4]*100
            if self._btc_4h<-3.0:
                if not self._regime_until or datetime.utcnow()>self._regime_until:
                    self._regime_until=datetime.utcnow()+timedelta(hours=2)
                    self._tg(f"<b>🚨 CRASH GUARD</b>\nBTC {self._btc_4h:.1f}% → Pausa 2h")
        bulls=0;total=0
        for coin in BREADTH_COINS[:10]:
            try:
                c,*_=self._klines(coin,'1h',25)
                if c and len(c)>=21:
                    if c[-1]>ema(c,21): bulls+=1
                    total+=1
            except: pass
        if total>0: self._breadth=bulls/total
        if self._breadth<BREADTH_BEAR:
            if self._regime!='bear': self._tg(f"<b>🛑 BEAR</b> Breadth {int(self._breadth*100)}%")
            self._regime='bear'; return
        btc_bear=self._btc_4h<-2.0 or self._btc_1h<-2.0
        low_b=self._breadth<0.35
        if btc_bear and low_b: nuevo='bear'
        elif btc_bear or low_b: nuevo='caution'
        elif self._btc_4h>1.0 and self._breadth>0.60: nuevo='bull'
        else: nuevo='neutral'
        if nuevo!=self._regime: log.info(f"  📊 {self._regime}→{nuevo}")
        self._regime=nuevo

    def _regime_ok(self):
        if self._regime_until and datetime.utcnow()<self._regime_until:
            return False,"crash_guard"
        if self._regime=='bear': return False,"bear"
        return True,"ok"

    def _score_min(self):
        base=SCORE_BULL if self._regime=='bull' else SCORE_NEUTRAL
        # ✅ FIX4: opt_score ya tiene cap, pero double-check
        base=max(base, min(self.learn.opt_score, SCORE_MAX_CAP))
        if self._regime=='caution': base=int(base*1.06)
        return min(base, SCORE_MAX_CAP)

    def _get_positions(self,sym=None):
        params={}
        if sym: params['symbol']=sym
        d=api('GET','/openApi/swap/v2/user/positions',params)
        res=defaultdict(lambda:{'long':0.0,'short':0.0})
        for p in (d.get('data') or []):
            try:
                amt=float(p.get('positionAmt',0) or 0);s=p.get('symbol','')
                side=str(p.get('positionSide','')).upper()
                if not s or abs(amt)==0: continue
                if side=='LONG' or (side=='BOTH' and amt>0): res[s]['long']=abs(amt)
                elif side=='SHORT' or (side=='BOTH' and amt<0): res[s]['short']=abs(amt)
            except: continue
        return res

    def _has_pos(self,sym):
        p=self._get_positions(sym); return p[sym]['long']>0 or p[sym]['short']>0

    def _close_short(self,sym,qty):
        params={'symbol':sym,'side':'BUY','type':'MARKET','quantity':str(qty)}
        if self._mode=='hedge': params['positionSide']='SHORT'
        else: params['reduceOnly']='true'
        return api('POST','/openApi/swap/v2/trade/order',params)

    def _recover(self):
        if not AUTO: return
        all_pos=self._get_positions(); n=0
        for sym,sides in all_pos.items():
            if sides['short']>0: self._close_short(sym,sides['short']); time.sleep(0.5)
            if sides['long']>0 and sym not in self.trades:
                d2=api('GET','/openApi/swap/v2/user/positions',{'symbol':sym})
                entry=0.0;lv=1.0
                for p in (d2.get('data') or []):
                    s2=str(p.get('positionSide','')).upper();a2=float(p.get('positionAmt',0) or 0)
                    if (s2=='LONG' and abs(a2)>0) or (s2=='BOTH' and a2>0):
                        entry=float(p.get('avgPrice') or p.get('entryPrice') or 0)
                        lv=float(p.get('leverage',LEVERAGE) or LEVERAGE); break
                if lv>LEVERAGE+1 or entry<=0: continue
                sl_r=entry*(1-SL_MAX/100)
                self.trades[sym]={
                    'entry':entry,'qty_total':sides['long'],'qty_runner':sides['long'],
                    'qty_tp1':round(sides['long']*TP1_PCT/100,6),'qty_tp2':round(sides['long']*TP2_PCT/100,6),
                    'tp1_hit':False,'tp2_hit':False,
                    'tp1_price':entry*(1+TP1_R*SL_MAX/100),'tp2_price':entry*(1+TP2_R*SL_MAX/100),
                    'sl':sl_r,'sl_orig':sl_r,'sl_pct':SL_MAX,'trail_sl':sl_r,
                    'highest':entry,'opened':datetime.now(),'score':0,'aurolo_pts':0,
                    'entrada_label':'recovered','usdt':POS_SIZE,'pnl_parcial':0.0,
                    'factors':[],'hora_utc':datetime.utcnow().hour,'btc_dir':'flat',
                    'debilidad_alertada':False,'trail_placed':False,'scanner_conf':0
                }
                n+=1; log.info(f"  ♻️ {sym} @ ${entry:.6f}")
        log.info(f"  Recuperadas: {n}")

    def _klines(self,sym,tf='5m',lim=130):
        d=pub('/openApi/swap/v3/quote/klines',{'symbol':sym,'interval':tf,'limit':lim})
        if d.get('code')==0 and d.get('data'):
            kl=d['data']
            return ([float(k['close'])for k in kl],[float(k['high'])for k in kl],
                    [float(k['low'])for k in kl],[float(k['volume'])for k in kl],
                    [float(k['open'])for k in kl])
        return None,None,None,None,None

    def _ticker(self,sym):
        d=pub('/openApi/swap/v2/quote/ticker',{'symbol':sym})
        if d.get('code')==0 and d.get('data'):
            t=d['data']
            return {'price':float(t.get('lastPrice',0)),'change':float(t.get('priceChangePercent',0))}
        return None

    def _update_btc(self):
        c,*_=self._klines('BTC-USDT','1h',4)
        if c and len(c)>=2:
            self._btc_1h=(c[-1]-c[-2])/c[-2]*100
            self._btc_ok=self._btc_1h>=(-BTC_CRASH)
        else: self._btc_ok=True

    def _btc_dir(self):
        if self._btc_1h>0.5: return 'up'
        if self._btc_1h<-0.5: return 'down'
        return 'flat'

    def _update_equity(self):
        global EQUITY
        d=api('GET','/openApi/swap/v2/user/balance')
        if d.get('code')==0:
            b=d.get('data',{})
            if isinstance(b,list):
                for item in b:
                    v=_sf(item)
                    if v>0: EQUITY=v; break
            else:
                eq=_sf(b.get('equity',b.get('balance',0)))
                if eq>0: EQUITY=eq

    # ============================================================================
    # ✅ FIX3: analyze() con logging de rechazos en cada punto
    # ============================================================================

    def _rej(self, sym, reason):
        """Registra rechazo"""
        self._signals_blocked+=1
        self._rejected.add(sym, reason)
        log.debug(f"  [REJ] {sym}: {reason}")

    def analyze(self, sym):
        if sym in self.trades:
            return None  # silencioso, es normal

        if not self._cd_ok(sym):
            self._rej(sym,"cooldown"); return None
        if sym in self._pending:
            return None
        if not self._regime_ok()[0]:
            self._rej(sym,f"regime_{self._regime}"); return None
        if not self._btc_ok:
            self._rej(sym,f"btc_crash_{self._btc_1h:.1f}%"); return None
        if self._cb_active:
            self._rej(sym,"circuit_breaker"); return None
        if self._breadth<BREADTH_BEAR:
            self._rej(sym,f"breadth_{int(self._breadth*100)}%"); return None
        if self._daily_trades>=MAX_DAILY:
            self._rej(sym,"daily_max"); return None

        c5,h5,l5,v5,o5=self._klines(sym,'5m',130)
        if not c5 or len(c5)<AUROLO_EMA+20:
            self._rej(sym,f"klines_insuf_{len(c5) if c5 else 0}"); return None

        tk=self._ticker(sym)
        if not tk or tk['price']<=0:
            self._rej(sym,"ticker_fail"); return None
        price=tk['price']; chg=tk['change']
        if chg>30 or chg<-20:
            self._rej(sym,f"chg_extremo_{chg:.0f}%"); return None

        # 1h trend — requerido
        c1h,h1h,l1h,v1h,_=self._klines(sym,'1h',35)
        if not (c1h and len(c1h)>=20):
            self._rej(sym,"1h_sin_datos"); return None
        e9_1h=ema(c1h,9); e21_1h=ema(c1h,21)
        if e9_1h<=e21_1h:
            self._rej(sym,f"1h_bajista({e9_1h:.4f}<={e21_1h:.4f})"); return None
        rsi_1h=rsi(c1h,14)
        if rsi_1h>75:
            self._rej(sym,f"rsi1h_OB_{rsi_1h:.0f}"); return None

        # 4h
        c4h,*_=self._klines(sym,'4h',25)
        trend_4h=0
        if c4h and len(c4h)>=21:
            e9_4h=ema(c4h,9); e21_4h=ema(c4h,21)
            if e9_4h>e21_4h: trend_4h=1
            elif e9_4h<e21_4h*0.99: trend_4h=-1
        if trend_4h==-1 and self._regime not in ('bull',):
            self._rej(sym,f"4h_bajista_regime_{self._regime}"); return None

        atr_v=atr_c(h5,l5,c5,14)
        atr_pct=atr_v/price*100 if price>0 else 0
        if atr_pct<0.05 or atr_pct>8.0:
            self._rej(sym,f"atr_{atr_pct:.2f}%"); return None

        sig=aurolo(c5,h5,l5,v5,o5,atr_v)
        if sig['puntos']<AUROLO_MIN:
            self._rej(sym,f"aurolo_{sig['puntos']}/3_<{AUROLO_MIN}"); return None
        if sig['cambio_tend']:
            self._rej(sym,"cambio_tend"); return None
        if sig['vol_ratio']<VOL_R_MIN:
            self._rej(sym,f"vol_ratio_{sig['vol_ratio']:.2f}<{VOL_R_MIN}"); return None

        sl_price=sig['sl_price']; sl_pct=sig['sl_pct']
        if sl_pct<SL_MIN*0.7 or sl_pct>SL_MAX*1.1:
            self._rej(sym,f"sl_pct_{sl_pct:.2f}%_oor"); return None

        tp1=price*(1+sl_pct*TP1_R/100); tp2=price*(1+sl_pct*TP2_R/100)
        rr=max(sl_pct*MIN_RR,1.2,atr_pct*2.0)/sl_pct if sl_pct>0 else 0
        if rr<MIN_RR*0.65:
            self._rej(sym,f"rr_{rr:.2f}<{MIN_RR*0.65:.2f}"); return None
        if sl_pct*TP1_R-FEE_COST<0.15:
            self._rej(sym,f"tp1_neto_{sl_pct*TP1_R-FEE_COST:.2f}%"); return None

        # ── SCORING ─────────────────────────────────────────────
        score=0; factors=[]
        pts=sig['puntos']
        if pts==3: score+=55; factors.append("a3")
        elif pts==2: score+=35; factors.append("a2")
        if sig['p1']: score+=10; factors.append("p1")
        if sig['p2']: score+=10; factors.append("p2")
        if sig['p3']: score+=10; factors.append("p3")
        if sig['wt_now']<=-60: score+=8; factors.append("wt_d")
        elif sig['wt_now']<=-42: score+=4
        vr=sig['vol_ratio']
        if vr>=2.5: score+=12; factors.append("vf")
        elif vr>=1.8: score+=7
        elif vr>=1.2: score+=3

        score+=12; factors.append("t1h")
        if trend_4h==1: score+=10; factors.append("t4h")

        c15,*_=self._klines(sym,'15m',35)
        t15=0
        if c15 and len(c15)>=20:
            t15=1 if ema(c15,9)>ema(c15,21) else -1
        if t15==1: score+=10; factors.append("t15m")
        elif t15==-1: score-=5

        bull=bear=0.0
        for i in range(-min(15,len(c5)),0):
            c=c5[i];o=o5[i] if o5 else c5[i-1];v=v5[i]
            if v>0:
                if c>o: bull+=v
                elif c<o: bear+=v
        ofi_r=bull/(bull+bear) if (bull+bear)>0 else 0.5
        if ofi_r>=0.62: score+=10; factors.append("ofi")
        elif ofi_r>=0.52: score+=5
        elif ofi_r<=0.40: score-=8

        vwap=vwap_c(c5,h5,l5,v5,50)
        if price>=vwap: score+=8; factors.append("vwap")
        else: score-=4

        if self._regime=='bull': score+=12; factors.append("bull")
        elif self._regime=='neutral': score+=5
        elif self._regime=='caution': score-=5

        if self._btc_1h>1.0: score+=8; factors.append("btc_up")
        elif self._btc_1h>0.3: score+=4
        elif self._btc_1h<-1.0: score-=6
        if self._btc_4h>1.5: score+=6; factors.append("btc4h")
        elif self._btc_4h<-1.5: score-=8

        if rsi_1h<45: score+=8; factors.append("rsi_os")
        elif rsi_1h<58: score+=4
        elif rsi_1h>68: score-=5

        if self._breadth>0.65: score+=8; factors.append("br_good")
        elif self._breadth>0.50: score+=4
        elif self._breadth<0.35: score-=8

        sc=self.scanner.get_conf(sym)
        if sc>=80: score+=20; factors.append("sc_c")
        elif sc>=65: score+=12; factors.append("sc_a")
        elif sc>=50: score+=6; factors.append("sc_m")

        hora=datetime.utcnow().hour
        if hora in {7,8,9,10,11,12,13,14,15,16,17,18,19,20}: score+=5; factors.append("ses_ok")

        score+=self.learn.adj(factors)
        self._signals_seen+=1

        score_min=self._score_min()
        if score<score_min:
            self._rej(sym,f"score_{int(score)}<{int(score_min)}"); return None

        ok,rej_reason=self.learn.ok(sym,score)
        if not ok:
            self._rej(sym,f"learn_{rej_reason}"); return None

        off=random.uniform(-0.07,0.02)
        sl_adj=round(max(sl_price*(1-SL_MAX*1.1/100),min(sl_price*(1+off/100),sl_price*(1-SL_MIN*0.7/100))),8)
        sl_pct=(price-sl_adj)/price*100

        size_mult=0.65 if atr_pct>5.0 else (1.2 if sc>=80 else 1.0)

        log.info(f"  ✅ SEÑAL {sym} score={int(score)}/{int(score_min)} aurolo={pts}/3 sc={sc}%")
        return {
            'price':price,'change':chg,'score':score,'score_min':score_min,
            'aurolo_pts':pts,'aurolo_p1':sig['p1'],'aurolo_p2':sig['p2'],'aurolo_p3':sig['p3'],
            'aurolo_wt':sig['wt_now'],'aurolo_adx':sig['adx_now'],
            'aurolo_señal':sig['señal'],
            'sl_price':sl_adj,'sl_pct':round(sl_pct,3),
            'tp1_price':round(tp1,8),'tp2_price':round(tp2,8),'rr':round(rr,2),
            'tp1_neto':round(sl_pct*TP1_R-FEE_COST,3),'vwap':vwap,
            'ema25':ema(c5,25),'ema55':sig['ema55'],
            'trend_15m':t15,'rsi_1h':rsi_1h,'ofi':ofi_r,'atr_pct':atr_pct,
            'factors':factors,'hora_utc':hora,'btc_dir':self._btc_dir(),
            'regime':self._regime,'breadth':self._breadth,'size_mult':size_mult,'scanner_conf':sc
        }

    def _analyze_parallel(self, symbols):
        results=[]
        with ThreadPoolExecutor(max_workers=SCAN_W) as ex:
            futs={ex.submit(self.analyze,s):s for s in symbols}
            for fut in as_completed(futs):
                try:
                    sig=fut.result()
                    if sig: results.append((futs[fut],sig))
                except: pass
        results.sort(key=lambda x:x[1]['score'],reverse=True)
        # ✅ FIX9: loggea top rechazos tras análisis
        top_rej=self._rejected.top_reasons(5)
        if top_rej and not results:
            log.info("  [REJ TOP] "+" | ".join(f"{r}:{c}" for r,c in top_rej))
        return results

    def _set_lev(self,sym):
        for s in ('LONG','SHORT'):
            try: api('POST','/openApi/swap/v2/trade/leverage',{'symbol':sym,'side':s,'leverage':str(LEVERAGE)})
            except: pass

    def _calc_qty(self,sym,price,sl_price,mult=1.0):
        info=self._contracts.get(sym,{'step':1,'prec':2,'ctval':1})
        step=max(float(info.get('step',1)),1e-6);prec=int(info.get('prec',2))
        ctval=max(float(info.get('ctval',1)),1e-9);ppc=price*ctval
        if ppc<=0: return None,0
        dist=(price-sl_price)/price*100 if sl_price<price else SL_MIN
        notional=min(EQUITY*(RISK_PCT/100)/(dist/100),POS_SIZE*LEVERAGE)*mult
        notional=max(notional,MIN_TRADE)
        qty=math.ceil((notional/ppc)/step)*step;qty=round(qty,prec);val=qty*ppc
        for _ in range(200):
            if val>=MIN_TRADE: break
            qty+=step;qty=round(qty,prec);val=qty*ppc
        return (qty,round(val,4)) if val>=MIN_TRADE else (None,0)

    def _order(self,sym,side,qty,otype='MARKET',price=None,stop_price=None,act=None,rate=None):
        params={'symbol':sym,'side':side.upper(),'type':otype,'quantity':str(qty)}
        if self._mode=='hedge': params['positionSide']='LONG'
        else:
            if side.upper()=='SELL': params['reduceOnly']='true'
        if price: params['price']=str(round(price,8));params['timeInForce']='GTC'
        if stop_price: params['stopPrice']=str(round(stop_price,8))
        if act: params['activationPrice']=str(round(act,8))
        if rate: params['priceRate']=str(rate)
        return api('POST','/openApi/swap/v2/trade/order',params)

    def _confirm_pos(self,sym,timeout=15):
        for _ in range(timeout):
            d=api('GET','/openApi/swap/v2/user/positions',{'symbol':sym})
            for p in (d.get('data') or []):
                amt=float(p.get('positionAmt',0) or 0);side=str(p.get('positionSide','')).upper()
                if (side=='LONG' and abs(amt)>0) or (side=='BOTH' and amt>0):
                    return abs(amt),float(p.get('avgPrice') or p.get('entryPrice') or 0)
            time.sleep(1)
        return None,None

    def _cancel_open(self,sym):
        d=api('GET','/openApi/swap/v2/trade/openOrders',{'symbol':sym})
        for o in (d.get('data',{}).get('orders') or []):
            oid=o.get('orderId')
            if oid: api('DELETE','/openApi/swap/v2/trade/order',{'symbol':sym,'orderId':str(oid)});time.sleep(0.1)

    def _place_sl(self,sym,qty,sl):
        d=self._order(sym,'SELL',qty,'STOP_MARKET',stop_price=sl)
        if d.get('code')==0: return True
        d=self._order(sym,'SELL',qty,'STOP',price=sl*0.999,stop_price=sl)
        if d.get('code')==0: return True
        return self._order(sym,'SELL',qty,'STOP_MARKET',stop_price=sl*0.998).get('code')==0

    def _place_trail(self,sym,qty,act,rate):
        params={'symbol':sym,'side':'SELL','type':'TRAILING_STOP_MARKET',
                'quantity':str(qty),'activationPrice':str(round(act,8)),'priceRate':str(rate)}
        if self._mode=='hedge': params['positionSide']='LONG'
        else: params['reduceOnly']='true'
        return api('POST','/openApi/swap/v2/trade/order',params).get('code')==0

    def _entry(self,sym,qty):
        d=pub('/openApi/swap/v2/quote/bookTicker',{'symbol':sym})
        ask=None
        if d.get('code')==0 and d.get('data'): ask=float(d['data'].get('askPrice',0) or 0)
        if not ask or ask<=0:
            tk=self._ticker(sym)
            if tk: ask=tk['price']*1.0002
        if not ask: return None,None
        d=self._order(sym,'BUY',qty,'LIMIT',price=round(ask*1.0005,8))
        if d.get('code')!=0: d=self._order(sym,'BUY',qty,'MARKET')
        if d.get('code')!=0: return None,None
        for _ in range(12):
            time.sleep(1)
            fq,fp=self._confirm_pos(sym,1)
            if fq and fp: return fq,fp
        self._cancel_open(sym);time.sleep(0.5)
        fq,fp=self._confirm_pos(sym,2)
        if fq: return fq,fp
        dm=self._order(sym,'BUY',qty,'MARKET')
        if dm.get('code')==0: return self._confirm_pos(sym,10)
        return None,None

    def open_trade(self,sym,sig):
        if not AUTO or sym in self.trades: return False
        if Bot._opening or len(self.trades)>=MAX_TRADES: return False
        if self._daily_trades>=MAX_DAILY: return False
        if sym in self._pending: return False
        if self._has_pos(sym): return False
        Bot._opening=True
        try: return self._open(sym,sig)
        finally: Bot._opening=False

    def _open(self,sym,sig):
        price=sig['price'];sl_price=sig['sl_price']
        pts=sig['aurolo_pts'];label=sig['aurolo_señal']
        mult=sig.get('size_mult',1.0);sc=sig.get('scanner_conf',0)
        log.info(f"\n  🎯 LONG {sym} [{label}] Score:{int(sig['score'])}/{int(sig['score_min'])} Sc:{sc}% RR:{sig['rr']:.2f}:1")
        self._set_lev(sym);time.sleep(0.2)
        qty,notional=self._calc_qty(sym,price,sl_price,mult)
        if not qty: return False
        self._pending[sym]='p'
        fq,fp=self._entry(sym,qty)
        if not fq or not fp: self._pending.pop(sym,None); return False
        sl_pct=sig['sl_pct']
        sl_r=max(fp*(1-SL_MAX/100),min(fp*(1-SL_MIN*0.7/100),fp*(1-sl_pct/100)))
        tp1=fp*(1+sl_pct*TP1_R/100);tp2=fp*(1+sl_pct*TP2_R/100)
        sl_ok=self._place_sl(sym,fq,sl_r)
        if not sl_ok: time.sleep(2);sl_ok=self._place_sl(sym,fq,sl_r)
        if not sl_ok:
            self._order(sym,'SELL',fq,'MARKET');self._pending.pop(sym,None);return False
        trail_placed=False
        if USE_TRAIL: trail_placed=self._place_trail(sym,fq,fp*(1+TRAIL_ACT/100),TRAIL_RATE)
        trade={
            'entry':fp,'qty_total':fq,'qty_runner':fq,
            'qty_tp1':round(fq*TP1_PCT/100,6),'qty_tp2':round(fq*TP2_PCT/100,6),
            'tp1_hit':False,'tp2_hit':False,'tp1_price':tp1,'tp2_price':tp2,
            'sl':sl_r,'sl_orig':sl_r,'sl_pct':sl_pct,'trail_sl':sl_r,
            'highest':fp,'opened':datetime.now(),'score':sig['score'],
            'aurolo_pts':pts,'entrada_label':label,'vwap':sig['vwap'],
            'usdt':POS_SIZE,'pnl_parcial':0.0,'factors':sig['factors'],
            'hora_utc':sig['hora_utc'],'btc_dir':sig['btc_dir'],
            'debilidad_alertada':False,'trail_placed':trail_placed,'scanner_conf':sc
        }
        self.trades[sym]=trade;self._pending.pop(sym,None)
        self.stats['exec']+=1;self.stats['fees']+=notional*FEE_TAKER
        self._daily_trades+=1
        p1="✅" if sig['aurolo_p1'] else "❌";p2="✅" if sig['aurolo_p2'] else "❌";p3="✅" if sig['aurolo_p3'] else "❌"
        sc_tag=f"🔴{sc}%" if sc>=80 else f"🟠{sc}%" if sc>=65 else f"🟡{sc}%" if sc>0 else "—"
        self._tg(
            f"<b>🟢 LONG [{label}]</b> — <b>{sym}</b>{'[x'+str(mult)+']' if mult!=1.0 else ''}\n"
            f"Score: {int(sig['score'])}/{int(sig['score_min'])} | Scanner: {sc_tag}\n"
            f"RR: {sig['rr']:.2f}:1 | {sig['regime']}\n"
            f"{p1}P1 {p2}P2 WT:{sig['aurolo_wt']:.1f} {p3}P3 ADX:{sig['aurolo_adx']:.1f}\n"
            f"📍 ${fp:.6f} | SL: ${sl_r:.6f} (-{sl_pct:.2f}%)\n"
            f"TP1: +{sl_pct*TP1_R:.2f}% | TP2: +{sl_pct*TP2_R:.2f}%\n"
            f"Trail: {'✅' if trail_placed else '🔧'} | BTC1h: {self._btc_1h:+.2f}%"
        )
        return True

    def _close_partial(self,sym,qty,exit_price,label):
        if qty<=0: return 0
        if self._order(sym,'SELL',qty,'MARKET').get('code')!=0: return 0
        t=self.trades[sym];chg=(exit_price-t['entry'])/t['entry']
        frac=qty/t['qty_total']
        net=POS_SIZE*LEVERAGE*chg*frac-POS_SIZE*LEVERAGE*FEE_TAKER*2*frac
        t['pnl_parcial']+=net;t['qty_runner']-=qty
        self.stats['fees']+=POS_SIZE*LEVERAGE*FEE_TAKER*2*frac
        self._daily_pnl+=net;self.stats['pnl']+=net
        log.info(f"  💰 {label} {sym}: ${net:+.4f}")
        self._tg(f"<b>💰 {label}</b> — {sym}\n${exit_price:.6f} | ${net:+.4f}")
        return net

    def _close_all(self,sym,exit_price,reason):
        if sym not in self.trades: return False
        t=self.trades[sym];qr=t['qty_runner']
        if qr>0: self._order(sym,'SELL',qr,'MARKET')
        fr=qr/t['qty_total'] if t['qty_total']>0 else 0
        chg=(exit_price-t['entry'])/t['entry']
        net_r=POS_SIZE*LEVERAGE*chg*fr-POS_SIZE*LEVERAGE*FEE_TAKER*2*fr
        net_total=t['pnl_parcial']+net_r;win=net_total>0
        self.stats['closed']+=1;self.stats['pnl']+=net_r
        self.stats['fees']+=POS_SIZE*LEVERAGE*FEE_TAKER*2*fr
        self._daily_pnl+=net_r
        if win: self.stats['wins']+=1
        else:   self.stats['losses']+=1
        total=self.stats['wins']+self.stats['losses']
        wr=self.stats['wins']/total*100 if total else 0
        mins=int((datetime.now()-t['opened']).total_seconds()/60)
        log.info(f"  {'✅' if win else '❌'} {reason} | ${net_total:+.4f} | {mins}min | WR:{wr:.0f}%")
        self.learn.record(sym,t['score'],net_total,win,t.get('hora_utc'),
                          t.get('aurolo_pts',0),reason,t.get('factors',[]))
        if 'SL' in reason or 'STOP' in reason:
            self._cooldowns[sym]=(time.time()+CD_SL*60,'SL')
        else: self._cooldowns[sym]=(time.time()+CD_TP*60,'TP')
        self._tg(
            f"<b>{'✅' if win else '❌'} {reason}</b> — {sym} | {mins}min\n"
            f"${t['entry']:.6f}→${exit_price:.6f}\n"
            f"<b>PnL: ${net_total:+.4f} | WR: {wr:.0f}%</b>"
        )
        if self.stats['closed']%3==0: self.learn.save()
        del self.trades[sym];self._cancel_open(sym);return True

    async def monitor(self):
        for sym in list(self.trades.keys()):
            try:
                t=self.trades[sym];tk=self._ticker(sym)
                if not tk: continue
                cur=tk['price']
                if cur>t['highest']: t['highest']=cur
                c5,h5,l5,v5,_=self._klines(sym,'5m',80)
                if c5 and not t.get('debilidad_alertada'):
                    av=atr_c(h5,l5,c5,14)
                    sl=aurolo(c5,h5,l5,v5 or [1]*len(c5),c5,av)
                    if sl['debilidad']: t['debilidad_alertada']=True;self._tg(f"<b>⚠️ DEBILIDAD {sym}</b>")
                    if sl['cambio_tend'] and (cur-t['entry'])/t['entry']*100>0.3:
                        self._close_all(sym,cur,"CAMBIO TENDENCIA");continue
                if t.get('trail_placed') and USE_TRAIL:
                    if cur<=t['sl']: self._close_all(sym,cur,"STOP LOSS");continue
                    continue
                prof=(cur-t['entry'])/t['entry']*100
                if USE_TRAIL and prof>=TRAIL_ACT:
                    new_sl=cur*(1-TRAIL_RATE/100)
                    if new_sl>t['trail_sl']:
                        old=t['trail_sl'];t['trail_sl']=new_sl;t['sl']=new_sl
                        self._cancel_open(sym);self._place_sl(sym,t['qty_runner'],new_sl)
                        log.info(f"  🔧 Trail {sym}: ${old:.6f}→${new_sl:.6f}")
                if not t['tp1_hit'] and cur>=t['tp1_price']:
                    self._close_partial(sym,t['qty_tp1'],cur,f"TP1({int(TP1_PCT)}%)")
                    t['tp1_hit']=True;be=t['entry']*1.001
                    if be>t['sl']:
                        t['sl']=be;t['trail_sl']=be
                        self._cancel_open(sym);self._place_sl(sym,t['qty_runner'],be)
                    continue
                if t['tp1_hit'] and not t['tp2_hit'] and cur>=t['tp2_price']:
                    self._close_partial(sym,t['qty_tp2'],cur,f"TP2({int(TP2_PCT)}%)");t['tp2_hit']=True;continue
                if cur<=t['sl']: self._close_all(sym,cur,"STOP LOSS")
            except Exception as e: log.debug(f"monitor {sym}: {e}")

    def _cd_ok(self,sym):
        ts=self._cooldowns.get(sym)
        if not ts: return True
        resume=ts[0] if isinstance(ts,tuple) else ts
        if time.time()>=resume: del self._cooldowns[sym];return True
        return False

    def _daily_reset(self):
        today=datetime.utcnow().date()
        if today!=self._daily_date:
            self.scanner.daily_summary()
            self._daily_pnl=0.0;self._daily_date=today;self._daily_trades=0
            self._cb_active=False;self._cb_until=None;self.learn.streak=0
            self._update_equity();self._equity_start=EQUITY
            self._signals_seen=0;self._signals_blocked=0
            log.info("📅 Reset diario")

    def _circuit_check(self):
        self._daily_reset()
        if self._cb_active:
            if self._cb_until and datetime.utcnow()>self._cb_until:
                self._cb_active=False
            return self._cb_active
        if self._equity_start>0:
            ep=abs(self._daily_pnl)/self._equity_start*100
            if self._daily_pnl<0 and ep>DAILY_LOSS:
                self._cb_active=True;self._cb_until=datetime.utcnow()+timedelta(hours=CB_H)
                self._tg(f"<b>🔒 DAILY LOSS {ep:.1f}%</b> | Pausa {CB_H}h");return True
        if self._daily_pnl<-(EQUITY*(CB_PCT/100)):
            self._cb_active=True;self._cb_until=datetime.utcnow()+timedelta(hours=CB_H)
            self._tg(f"<b>🔒 CB ${self._daily_pnl:.3f}</b> | Pausa {CB_H}h")
        return self._cb_active

    def _report(self):
        if datetime.now()-self._last_report<timedelta(hours=2): return
        self._last_report=datetime.now()
        total=self.stats['wins']+self.stats['losses']
        wr=self.stats['wins']/total*100 if total else 0
        pos=""
        for sym,t in self.trades.items():
            tk=self._ticker(sym);cur=tk['price'] if tk else t['entry']
            pct=(cur-t['entry'])/t['entry']*100
            pos+=f"  {'✅' if pct>0 else '📌'} {sym}[{t['aurolo_pts']}/3]: {pct:+.2f}%\n"
        hot=self.scanner.get_hot(HOT_CONF,5)
        top_rej=self._rejected.top_reasons(4)
        rej_txt="\n".join(f"  {r}:{c}" for r,c in top_rej) if top_rej else "  ninguno"
        self._tg(
            f"<b>📊 BOT v8.1</b>\n"
            f"PnL: ${self.stats['pnl']:+.4f} | WR: {wr:.0f}% ({total}t)\n"
            f"Hoy: {self._daily_trades}/{MAX_DAILY} | Fees: ${self.stats['fees']:.4f}\n"
            f"Régimen: {self._regime} | Breadth: {int(self._breadth*100)}% | BTC1h: {self._btc_1h:+.2f}%\n"
            f"opt_score: {int(self.learn.opt_score)} | Score mín: {int(self._score_min())}\n"
            f"Señales: vistas={self._signals_seen} bloq={self._signals_blocked}\n"
            f"🔥 Hot: {', '.join(hot) if hot else 'buscando...'}\n"
            f"Top rechazos:\n{rej_txt}\n"
            +(pos if pos else "  Sin posiciones\n")
        )

    def _tg(self,msg):
        try:
            if TG_TOKEN and TG_CHAT:
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              json={'chat_id':TG_CHAT,'text':msg,'parse_mode':'HTML'},timeout=6)
        except: pass

    def _check_ltv(self):
        if not AUTO: return
        d=api('GET','/openApi/swap/v2/user/balance')
        if d.get('code')!=0: return
        try:
            b=d.get('data',{});eq=_sf(b.get('equity',b.get('balance',0)))
            mg=_sf(b.get('usedMargin',b.get('initialMargin',0)))
            if eq>0 and mg/eq*100>=75:
                self._tg("<b>⚠️ LTV ALTO</b>")
                for sym in list(self.trades):
                    tk=self._ticker(sym)
                    if tk: self._close_all(sym,tk['price'],"LTV")
        except: pass

    async def run(self):
        log.info(f"\n🚀 Bot v8.1 FIXED | {len(self.symbols)} símbolos\n")
        iteration=0; last_sym=last_ltv=last_hedge=last_eq=last_regime=0
        while True:
            try:
                iteration+=1;self._daily_reset()
                now=time.time()
                if now-last_sym >600: self._refresh_symbols();last_sym=now
                if now-last_ltv >300: self._check_ltv();last_ltv=now
                if now-last_eq  >1800: self._update_equity();last_eq=now
                if now-last_regime>300: self._update_regime();last_regime=now
                if now-last_hedge>600:
                    for sym,sides in self._get_positions().items():
                        if sides['short']>0: self._close_short(sym,sides['short']);time.sleep(0.3)
                    last_hedge=now
                if now-self._last_zombie>600: self._nuke_zombies()

                self._update_btc()
                if self._circuit_check(): await asyncio.sleep(INTERVAL);continue
                if self._paused: log.info("  ⏸️ PAUSADO");await asyncio.sleep(INTERVAL);continue

                total=self.stats['wins']+self.stats['losses']
                wr=self.stats['wins']/total*100 if total else 0
                sm=self._score_min()
                with self.scanner._lock: nh=sum(1 for _,c in self.scanner.hot if c>=HOT_CONF)

                log.info(f"\n{'='*68}")
                log.info(f"  #{iteration} {datetime.now().strftime('%H:%M:%S')} | "
                         f"Pos:{len(self.trades)}/{MAX_TRADES} | Hoy:{self._daily_trades}/{MAX_DAILY} | "
                         f"PnL:${self.stats['pnl']:+.4f} | WR:{wr:.0f}%")
                log.info(f"  BTC:{self._btc_1h:+.2f}% | {self._regime} | Breadth:{int(self._breadth*100)}% | "
                         f"Score≥{int(sm)}(opt={int(self.learn.opt_score)}) | Hot:{nh} | "
                         f"Vistas:{self._signals_seen} Bloq:{self._signals_blocked}")
                log.info(f"{'='*68}\n")

                await self.monitor()
                self._report()

                can=len(self.trades)<MAX_TRADES and self._daily_trades<MAX_DAILY
                if can:
                    ro,rr=self._regime_ok()
                    if not ro: log.info(f"  ⏸️ {rr}");await asyncio.sleep(INTERVAL);continue
                    scan_order=self._scan_order()
                    log.info(f"  🔍 {len(scan_order)} símbolos (hot primero)...")
                    signals=self._analyze_parallel(scan_order)
                    log.info(f"  ✅ {len(signals)} señales")
                    for sym,sig in signals:
                        if len(self.trades)>=MAX_TRADES or self._daily_trades>=MAX_DAILY: break
                        sc=sig.get('scanner_conf',0)
                        log.info(f"  💡 {sym} [{sig['aurolo_señal']}] Score:{int(sig['score'])}/{int(sig['score_min'])} RR:{sig['rr']:.2f} Sc:{sc}%")
                        if self.open_trade(sym,sig): await asyncio.sleep(3)
                else:
                    log.info("  ⏸️ Max trades o CB")
                await asyncio.sleep(INTERVAL)
            except KeyboardInterrupt: log.info("⏹️ Detenido");break
            except Exception as e:
                log.error(f"❌ #{iteration}: {e}",exc_info=True);await asyncio.sleep(20)
        self.learn.save()

async def main():
    bot=Bot()
    await bot.run()

if __name__=="__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("👋")
