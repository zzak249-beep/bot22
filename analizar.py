"""
analizar.py — SMC Bot v3.3 [SEÑALES OPTIMIZADAS]
=================================================
Cambios vs v3.1 basados en bt_v3.py:
  ✅ Choppiness filter: no entrar en laterales
  ✅ ATR mínimo: evitar mercados muertos
  ✅ HTF flexible (NEUTRAL = operar con 5m solo)
     La restricción estricta reducía trades sin mejorar WR
  ✅ RSI con rangos útiles: LONG 25-55, SHORT 45-75
  ✅ OB entry: precio dentro del OB +2 puntos
  ✅ SL desde OB bottom si disponible (más preciso)
  ✅ Volumen mínimo subido a 40%
  ✅ Debug logging para Railway
"""

import logging
from datetime import datetime, timezone
import concurrent.futures

import config
import exchange

log = logging.getLogger("analizar")


# ══════════════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════════════

def calc_ema(prices: list, period: int):
    if len(prices) < period: return None
    k = 2/(period+1); ema = sum(prices[:period])/period
    for p in prices[period:]: ema = p*k + ema*(1-k)
    return ema

def calc_rsi(prices: list, period: int = 14):
    if len(prices) < period+1: return None
    d = [prices[i]-prices[i-1] for i in range(1,len(prices))]
    ag = sum(max(x,0) for x in d[:period])/period
    al = sum(abs(min(x,0)) for x in d[:period])/period
    for x in d[period:]:
        ag = (ag*(period-1)+max(x,0))/period
        al = (al*(period-1)+abs(min(x,0)))/period
    return 100.0 if al==0 else round(100-100/(1+ag/al),2)

def calc_atr(highs, lows, closes, period=14):
    if len(highs) < period+1: return 0.0
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1]))
         for i in range(1,len(highs))]
    if not trs: return 0.0
    return sum(trs[-period:])/period if len(trs)>=period else sum(trs)/len(trs)

def calc_pivotes(ph,pl,pc):
    pp=(ph+pl+pc)/3
    return {"PP":pp,"R1":2*pp-pl,"R2":pp+(ph-pl),"S1":2*pp-ph,"S2":pp-(ph-pl)}


# ══════════════════════════════════════════════════════════════
# CHOPPINESS FILTER — evita mercados laterales
# Chop < 61.8 = trending → OK para operar
# Chop > 61.8 = choppy   → SKIP
# ══════════════════════════════════════════════════════════════

def es_trending(candles: list, n: int = 20) -> bool:
    if len(candles) < n+1: return True
    w = candles[-(n+1):]
    s = sum(max(w[i]["high"]-w[i]["low"],abs(w[i]["high"]-w[i-1]["close"]),
               abs(w[i]["low"]-w[i-1]["close"])) for i in range(1,len(w)))
    rng = max(c["high"] for c in w) - min(c["low"] for c in w)
    if rng <= 0: return False
    return s/rng/n*100 < 61.8


# ══════════════════════════════════════════════════════════════
# MTF — Tendencia 1h (FLEXIBLE)
# NEUTRAL permite operar (HTF no contradice)
# ══════════════════════════════════════════════════════════════

def tendencia_htf(par: str) -> str:
    if not config.MTF_ACTIVO: return "NEUTRAL"
    try:
        ch = exchange.get_candles(par, config.MTF_TIMEFRAME, config.MTF_CANDLES)
        if len(ch) < 50: return "NEUTRAL"
        cl = [c["close"] for c in ch]
        ef = calc_ema(cl, config.EMA_FAST)
        es = calc_ema(cl, config.EMA_SLOW)
        if ef is None or es is None: return "NEUTRAL"
        if ef > es*1.001: return "BULL"
        if ef < es*0.999: return "BEAR"
        return "NEUTRAL"
    except: return "NEUTRAL"


# ══════════════════════════════════════════════════════════════
# RANGO ASIA
# ══════════════════════════════════════════════════════════════

def get_rango_asia(candles: list) -> dict:
    r = {"high":0.0,"low":999_999_999.0,"valido":False}
    if not config.ASIA_RANGE_ACTIVO: return r
    ac = [c for c in candles
          if 0 <= (datetime.fromtimestamp(c["ts"]/1000,tz=timezone.utc).hour*60
                   +datetime.fromtimestamp(c["ts"]/1000,tz=timezone.utc).minute) < 240]
    if len(ac) >= 3:
        r.update({"high":max(c["high"] for c in ac),"low":min(c["low"] for c in ac),"valido":True})
    return r


# ══════════════════════════════════════════════════════════════
# ORDER BLOCKS
# ══════════════════════════════════════════════════════════════

def detectar_order_blocks(candles: list) -> dict:
    r = {"bull_ob":False,"bull_ob_top":0.0,"bull_ob_bottom":0.0,
         "bear_ob":False,"bear_ob_top":0.0,"bear_ob_bottom":0.0}
    if not config.OB_ACTIVO or len(candles) < 5: return r
    lb = min(config.OB_LOOKBACK, len(candles)-2)
    b  = candles[-(lb+2):-1]
    for i in range(len(b)-3,1,-1):
        c = b[i]
        if c["close"]<c["open"] and not r["bull_ob"] and i+2<len(b):
            c1,c2=b[i+1],b[i+2]
            if c1["close"]>c1["open"] and c2["close"]>c2["open"] and c2["high"]>c["high"]:
                r.update({"bull_ob":True,"bull_ob_top":max(c["open"],c["close"]),"bull_ob_bottom":c["low"]})
        if c["close"]>c["open"] and not r["bear_ob"] and i+2<len(b):
            c1,c2=b[i+1],b[i+2]
            if c1["close"]<c1["open"] and c2["close"]<c2["open"] and c2["low"]<c["low"]:
                r.update({"bear_ob":True,"bear_ob_top":c["high"],"bear_ob_bottom":min(c["open"],c["close"])})
        if r["bull_ob"] and r["bear_ob"]: break
    return r


# ══════════════════════════════════════════════════════════════
# BOS + CHoCH
# ══════════════════════════════════════════════════════════════

def detectar_bos_choch(candles: list) -> dict:
    r = {"bos_bull":False,"bos_bear":False,"choch_bull":False,"choch_bear":False}
    if not config.BOS_ACTIVO or len(candles) < 20: return r
    highs=[c["high"] for c in candles]; lows=[c["low"] for c in candles]
    closes=[c["close"] for c in candles]; precio=closes[-1]
    lb=min(50,len(candles)); sh=[]; sl=[]
    for i in range(2,lb-2):
        idx=len(candles)-lb+i
        if all(highs[idx]>highs[idx-k] and highs[idx]>highs[idx+k] for k in range(1,3)):
            sh.append(highs[idx])
        if all(lows[idx]<lows[idx-k] and lows[idx]<lows[idx+k] for k in range(1,3)):
            sl.append(lows[idx])
    if sh and precio>sh[-1]:
        r["bos_bull"]=True
        if len(sh)>=2 and sh[-1]<sh[-2]: r["choch_bull"]=True
    if sl and precio<sl[-1]:
        r["bos_bear"]=True
        if len(sl)>=2 and sl[-1]>sl[-2]: r["choch_bear"]=True
    return r


# ══════════════════════════════════════════════════════════════
# CONFIRMACIÓN DE VELA
# ══════════════════════════════════════════════════════════════

def confirmar_vela(candles: list, lado: str) -> bool:
    if not config.VELA_CONFIRMACION or len(candles) < 2: return False
    c=candles[-1]; rng=c["high"]-c["low"]
    if rng<=0: return False
    body=abs(c["close"]-c["open"]); bp=body/rng
    uw=c["high"]-max(c["open"],c["close"]); lw=min(c["open"],c["close"])-c["low"]
    prev=candles[-2]
    if lado=="LONG":
        return (c["close"]>c["open"] and bp>0.50) or (lw/rng>0.60) or (c["close"]>prev["open"] and c["open"]<=prev["close"])
    return (c["close"]<c["open"] and bp>0.50) or (uw/rng>0.60) or (c["close"]<prev["open"] and c["open"]>=prev["close"])


# ══════════════════════════════════════════════════════════════
# FAIR VALUE GAP
# ══════════════════════════════════════════════════════════════

def detectar_fvg(candles: list) -> dict:
    r={"bull_fvg":False,"bear_fvg":False,"fvg_top":0.0,"fvg_bottom":0.0}
    if len(candles)<3: return r
    for i in range(len(candles)-1, max(len(candles)-20,2)-1, -1):
        c0,c2=candles[i],candles[i-2]
        if c0["low"]-c2["high"]>config.FVG_MIN_PIPS:
            r.update({"bull_fvg":True,"fvg_top":c0["low"],"fvg_bottom":c2["high"]}); break
        if c2["low"]-c0["high"]>config.FVG_MIN_PIPS:
            r.update({"bear_fvg":True,"fvg_top":c2["low"],"fvg_bottom":c0["high"]}); break
    return r


# ══════════════════════════════════════════════════════════════
# EQUAL HIGHS / LOWS
# ══════════════════════════════════════════════════════════════

def _phigh(highs,l,i):
    if i<l or i+l>=len(highs): return None
    v=highs[i]
    return v if all(highs[j]<v for j in range(i-l,i+l+1) if j!=i) else None

def _plow(lows,l,i):
    if i<l or i+l>=len(lows): return None
    v=lows[i]
    return v if all(lows[j]>v for j in range(i-l,i+l+1) if j!=i) else None

def detectar_eqh_eql(candles: list) -> dict:
    r={"is_eqh":False,"eqh_price":0.0,"is_eql":False,"eql_price":0.0}
    if len(candles)<config.EQ_LOOKBACK: return r
    H=[c["high"] for c in candles]; L=[c["low"] for c in candles]
    ln=config.EQ_PIVOT_LEN; t=config.EQ_THRESHOLD; n=len(H); lb=config.EQ_LOOKBACK
    ph=[]; pl=[]
    for i in range(max(ln,n-lb-ln),n-ln):
        p=_phigh(H,ln,i)
        if p: ph.append(p)
        p=_plow(L,ln,i)
        if p: pl.append(p)
    for lst,key,pkey in [(ph,"is_eqh","eqh_price"),(pl,"is_eql","eql_price")]:
        if len(lst)>=2:
            for i in range(len(lst)-1,0,-1):
                for j in range(i-1,max(i-10,-1),-1):
                    if abs(lst[i]-lst[j])/lst[i]*100<=t:
                        r[key]=True; r[pkey]=lst[i]; break
                if r[key]: break
    return r


# ══════════════════════════════════════════════════════════════
# KILLZONES
# ══════════════════════════════════════════════════════════════

def en_killzone() -> dict:
    ahora=datetime.now(timezone.utc); tim=ahora.hour*60+ahora.minute
    asia=config.KZ_ASIA_START<=tim<config.KZ_ASIA_END
    lon =config.KZ_LONDON_START<=tim<config.KZ_LONDON_END
    ny  =config.KZ_NY_START<=tim<config.KZ_NY_END
    return {"in_asia":asia,"in_london":lon,"in_ny":ny,"in_kz":asia or lon or ny,
            "nombre":"ASIA" if asia else ("LONDON" if lon else ("NY" if ny else "FUERA"))}


# ══════════════════════════════════════════════════════════════
# PIVOTES DIARIOS
# ══════════════════════════════════════════════════════════════

def calcular_pivotes_diarios(candles_d):
    if len(candles_d)<2: return None
    p=candles_d[-2]
    return calc_pivotes(p["high"],p["low"],p["close"])


# ══════════════════════════════════════════════════════════════
# FILTRO VOLUMEN (40% del promedio — subido de 30%)
# ══════════════════════════════════════════════════════════════

def volumen_ok(candles: list) -> bool:
    if len(candles)<21: return True
    vols=[c["volume"] for c in candles[-21:-1]]
    avg=sum(vols)/len(vols) if vols else 0
    return avg<=0 or candles[-1]["volume"]/avg>=0.40


# ══════════════════════════════════════════════════════════════
# SEÑAL PRINCIPAL v3.3
# ══════════════════════════════════════════════════════════════

def analizar_par(par: str):
    try:
        candles = exchange.get_candles(par, config.TIMEFRAME, config.CANDLES_LIMIT)
        if len(candles) < 80: return None

        # ✅ Choppiness filter
        if not es_trending(candles, 20):
            return None

        if not volumen_ok(candles): return None

        cl=[c["close"] for c in candles]
        hi=[c["high"]  for c in candles]
        lo=[c["low"]   for c in candles]
        precio=cl[-1]
        if precio<=0: return None

        atr=calc_atr(hi,lo,cl,config.ATR_PERIOD)
        if atr<=0: return None

        # ✅ ATR mínimo — mercados muertos
        if atr/precio*100<0.03:
            log.debug(f"[ATR_PLANO] {par} {atr/precio*100:.4f}% skip")
            return None

        # ── EMA 5m ──
        ef=calc_ema(cl,config.EMA_FAST); es=calc_ema(cl,config.EMA_SLOW)
        bull5=ef is not None and es is not None and ef>es*1.001
        bear5=ef is not None and es is not None and ef<es*0.999

        # ── RSI con rangos útiles ──
        rsi=calc_rsi(cl,config.RSI_PERIOD) or 50.0
        rsi_l=(25<=rsi<=config.RSI_BUY_MAX)
        rsi_s=(config.RSI_SELL_MIN<=rsi<=75)

        # ── MTF 1h (FLEXIBLE) ──
        htf=tendencia_htf(par)
        # ✅ NEUTRAL permite operar — solo bloquea si va en CONTRA
        trend_ok_l=bull5 and htf!="BEAR"
        trend_ok_s=bear5 and htf!="BULL"

        # ── Señales SMC ──
        fvg=detectar_fvg(candles)
        eq =detectar_eqh_eql(candles)
        ob =detectar_order_blocks(candles)
        bos=detectar_bos_choch(candles)
        asia=get_rango_asia(candles)
        kz =en_killzone()

        candles_d=exchange.get_candles(par,"1d",5)
        pivotes=calcular_pivotes_diarios(candles_d)
        ns1=ns2=nr1=nr2=False
        if pivotes:
            pct=config.PIVOT_NEAR_PCT/100
            ns1=abs(precio-pivotes["S1"])/precio<pct
            ns2=abs(precio-pivotes["S2"])/precio<pct
            nr1=abs(precio-pivotes["R1"])/precio<pct
            nr2=abs(precio-pivotes["R2"])/precio<pct

        nal=nah=False
        if asia["valido"]:
            pct=config.PIVOT_NEAR_PCT/100
            nal=abs(precio-asia["low"])/precio<pct
            nah=abs(precio-asia["high"])/precio<pct

        vcl=confirmar_vela(candles,"LONG")
        vcs=confirmar_vela(candles,"SHORT")

        iob_b=(ob["bull_ob"] and ob["bull_ob_bottom"]<=precio<=ob["bull_ob_top"]*1.005)
        iob_r=(ob["bear_ob"] and ob["bear_ob_bottom"]*0.995<=precio<=ob["bear_ob_top"])

        # ── SCORE v3.3 (máx 12) ──
        sl=ss=0; ml=[]; ms=[]
        def add(cond,pts,lbl,side):
            nonlocal sl,ss,ml,ms
            if cond:
                if side in ("L","B"): sl+=pts; ml.append(lbl)
                if side in ("S","B"): ss+=pts; ms.append(lbl)

        add(fvg["bull_fvg"],2,"FVG","L")
        add(fvg["bear_fvg"],2,"FVG","S")
        add(kz["in_kz"],1,f"KZ_{kz['nombre']}","B")
        add(ns1 or ns2,1,"S1/S2","L")
        add(eq["is_eql"],1,"EQL","L")
        add(nal,1,"ASIA_L","L")
        add(nr1 or nr2,1,"R1/R2","S")
        add(eq["is_eqh"],1,"EQH","S")
        add(nah,1,"ASIA_H","S")
        add(iob_b,2,"OB+","L")
        add(iob_r,2,"OB-","S")
        add(bos["bos_bull"],1,"CHoCH" if bos["choch_bull"] else "BOS","L")
        add(bos["bos_bear"],1,"CHoCH" if bos["choch_bear"] else "BOS","S")
        add(htf=="BULL",1,"MTF_1H","L")
        add(htf=="BEAR",1,"MTF_1H","S")
        add(bull5,1,"EMA","L")
        add(bear5,1,"EMA","S")
        add(rsi_l,1,f"RSI{rsi:.0f}","L")
        add(rsi_s,1,f"RSI{rsi:.0f}","S")
        add(vcl,1,"VELA","L")
        add(vcs,1,"VELA","S")

        zona_l=ns1 or ns2 or eq["is_eql"] or nal or iob_b
        zona_s=nr1 or nr2 or eq["is_eqh"] or nah or iob_r
        base_l=fvg["bull_fvg"] and kz["in_kz"] and zona_l
        base_s=fvg["bear_fvg"] and kz["in_kz"] and zona_s

        lado=score=None; motivos=[]
        if not config.SOLO_LONG:
            if base_s and ss>=config.SCORE_MIN and trend_ok_s and rsi_s:
                if ss>sl: lado,score,motivos="SHORT",ss,ms
        if base_l and sl>=config.SCORE_MIN and trend_ok_l and rsi_l:
            if lado is None or sl>=ss: lado,score,motivos="LONG",sl,ml

        if lado is None:
            if sl>=3 or ss>=3:
                log.debug(f"[NO-SEÑAL] {par} L:{sl}({','.join(ml)}) S:{ss}({','.join(ms)}) "
                          f"base_L={base_l} base_S={base_s} "
                          f"trend_L={trend_ok_l}(htf={htf},5m={bull5}) "
                          f"rsi={rsi:.0f} chop=ok")
            return None

        # ── SL / TP ──
        # ✅ SL_ATR_MULT=0.6 (optimizado por backtest: mejor PnL)
        # ✅ PARTIAL_TP desactivado — R:R real fue 0.5x con partial
        if lado=="LONG":
            sl_ob=ob["bull_ob_bottom"]*0.997 if iob_b else 0
            sl_atr=precio-atr*config.SL_ATR_MULT
            sl_p=max(sl_ob,sl_atr) if sl_ob>0 else sl_atr
            tp_p=precio+atr*config.TP_ATR_MULT
            tp1_p=precio+atr*config.PARTIAL_TP1_MULT
        else:
            sl_ob=ob["bear_ob_top"]*1.003 if iob_r else 0
            sl_atr=precio+atr*config.SL_ATR_MULT
            sl_p=min(sl_ob,sl_atr) if sl_ob>0 else sl_atr
            tp_p=precio-atr*config.TP_ATR_MULT
            tp1_p=precio-atr*config.PARTIAL_TP1_MULT

        rr=abs(tp_p-precio)/abs(precio-sl_p) if abs(precio-sl_p)>0 else 0
        if rr<config.MIN_RR:
            log.debug(f"[NO-SEÑAL] {par} R:R={rr:.2f} < {config.MIN_RR}")
            return None

        return {"par":par,"lado":lado,"precio":precio,
                "sl":round(sl_p,8),"tp":round(tp_p,8),"tp1":round(tp1_p,8),
                "atr":round(atr,8),"score":score,"rsi":rsi,"rr":round(rr,2),
                "motivos":motivos,"kz":kz["nombre"],
                "fvg_top":fvg.get("fvg_top",0),"fvg_bottom":fvg.get("fvg_bottom",0),
                "pivotes":pivotes,"htf":htf,
                "ob_bull":ob["bull_ob"],"ob_bear":ob["bear_ob"],
                "bos_bull":bos["bos_bull"],"bos_bear":bos["bos_bear"],
                "choch_bull":bos["choch_bull"],"choch_bear":bos["choch_bear"],
                "vela_conf":vcl or vcs,"asia_valido":asia["valido"]}

    except Exception as e:
        log.error(f"analizar_par {par}: {e}")
        return None


def analizar_todos(pares: list, workers: int = 4) -> list:
    senales=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futuros={ex.submit(analizar_par,p):p for p in pares}
        for fut in concurrent.futures.as_completed(futuros):
            try:
                r=fut.result()
                if r: senales.append(r)
            except Exception as e:
                log.error(f"thread: {e}")
    senales.sort(key=lambda x:x["score"],reverse=True)
    return senales
