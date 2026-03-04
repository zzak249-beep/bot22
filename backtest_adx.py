import requests, pandas as pd, numpy as np, time
from datetime import datetime

BB_PERIOD=30; BB_SIGMA=2.0; RSI_PERIOD=14; RSI_OB=45
SL_ATR=2.5; PARTIAL_TP_ATR=1.2; LEVERAGE=3; RISK_PCT=0.015
INITIAL_BAL=100.0; SCORE_MIN=45; COOLDOWN_BARS=4

SYMBOLS=["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
         "ADA-USDT","DOGE-USDT","AVAX-USDT","LINK-USDT","DOT-USDT"]

def fetch(sym):
    url="https://open-api.bingx.com/openApi/swap/v2/quote/klines"
    all_c=[]; end=None
    print(f"  {sym}...", end="", flush=True)
    for _ in range(2):
        p={"symbol":sym,"interval":"1h","limit":1000}
        if end: p["endTime"]=end
        try:
            r=requests.get(url,params=p,timeout=15).json()
            c=r.get("data",[])
            if not c: break
            all_c=c+all_c; end=int(c[0][0])-1; time.sleep(0.3)
        except Exception as e:
            print(f" ERR:{e}"); break
    if not all_c: print(" sin datos"); return pd.DataFrame()
    df=pd.DataFrame(all_c,columns=["ts","open","high","low","close","volume"])
    for col in ["open","high","low","close","volume"]:
        df[col]=pd.to_numeric(df[col],errors="coerce")
    df["ts"]=pd.to_datetime(pd.to_numeric(df["ts"],errors="coerce"),unit="ms")
    df=df.dropna().sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    print(f" {len(df)} velas ({str(df['ts'].iloc[0])[:10]} -> {str(df['ts'].iloc[-1])[:10]})")
    return df

def rsi(close):
    d=close.diff(); g=d.clip(lower=0).rolling(RSI_PERIOD).mean()
    l=(-d.clip(upper=0)).rolling(RSI_PERIOD).mean()
    return 100-100/(1+g/l.replace(0,float("nan")))

def atr(df,p=14):
    h,l,c=df["high"],df["low"],df["close"]
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(p).mean()

def macd(close):
    e12=close.ewm(span=12,adjust=False).mean(); e26=close.ewm(span=26,adjust=False).mean()
    lin=e12-e26; return lin-lin.ewm(span=9,adjust=False).mean()

def diverg(cls,rsi_s,lb=6):
    if len(rsi_s)<lb+2: return None
    r=rsi_s.iloc[-lb-1:]; p=cls.iloc[-lb-1:]
    rn=float(r.iloc[-1]); pn=float(p.iloc[-1])
    if pn<float(p.iloc[:-1].min()) and rn>float(r.iloc[:-1].min())+3: return "bull"
    if pn>float(p.iloc[:-1].max()) and rn<float(r.iloc[:-1].max())-3: return "bear"
    return None

def score(r,dv,mb):
    s=40
    if r<25: s+=25
    elif r<30: s+=20
    elif r<40: s+=12
    elif r<48: s+=6
    if dv=="bull": s+=15
    if mb: s+=8
    return min(s,100)

def backtest(df,sym,balance):
    if len(df)<BB_PERIOD+30: return [],balance
    df=df.copy()
    basis=df["close"].rolling(BB_PERIOD).mean(); std=df["close"].rolling(BB_PERIOD).std()
    df["upper"]=basis+BB_SIGMA*std; df["basis"]=basis; df["lower"]=basis-BB_SIGMA*std
    df["rsi"]=rsi(df["close"]); df["atr"]=atr(df); df["macd"]=macd(df["close"])
    trades=[]; pos=None; cool=0
    for i in range(BB_PERIOD+30,len(df)):
        cur=df.iloc[i]; prev=df.iloc[i-1]
        price=float(cur["close"])
        r=float(cur["rsi"]) if not np.isnan(cur["rsi"]) else 50.0
        a=float(cur["atr"]) if not np.isnan(cur["atr"]) else 0.0
        mb=float(cur["macd"])>0 if not np.isnan(cur["macd"]) else True
        if a<=0: continue
        blo=float(cur["lower"]); bhi=float(cur["upper"]); bmid=float(cur["basis"])
        if pos:
            side=pos["side"]; entry=pos["entry"]
            if not pos.get("pd"):
                hp=(side=="long" and price>=pos["tp_p"]) or (side=="short" and price<=pos["tp_p"])
                if hp: pos["qty"]*=0.5; pos["pd"]=True; pos["sl"]=entry
            sl_hit=(side=="long" and price<=pos["sl"]) or (side=="short" and price>=pos["sl"])
            tp_hit=(side=="long" and price>=pos["tp"])  or (side=="short" and price<=pos["tp"])
            ex=(side=="long" and float(prev["close"])<=float(prev["basis"]) and price>bmid) or \
               (side=="short" and float(prev["close"])>=float(prev["basis"]) and price<bmid)
            reason=None; exit_p=price
            if sl_hit: exit_p=pos["sl"]; reason="SL"
            elif tp_hit: exit_p=pos["tp"]; reason="TP"
            elif ex: reason="SIGNAL"
            if reason:
                pct=(exit_p-entry)/entry if side=="long" else (entry-exit_p)/entry
                pnl=pos["qty"]*entry*pct*LEVERAGE; balance+=pnl
                trades.append({"symbol":sym,"side":side,"entry":entry,"exit":exit_p,
                    "pnl":round(pnl,4),"result":"WIN" if pnl>0 else "LOSS",
                    "reason":reason,"sc":pos["sc"],"date":str(cur["ts"])[:10],"bars":i-pos["oi"]})
                pos=None; cool=COOLDOWN_BARS
            continue
        if cool>0: cool-=1; continue
        dv=diverg(df["close"].iloc[max(0,i-8):i+1],df["rsi"].iloc[max(0,i-8):i+1])
        touch=(price<=blo*1.002) or (dv=="bull" and r<48 and price<=blo*1.008)
        if touch and r<RSI_OB and balance>0:
            sc=score(r,dv,mb)
            if sc>=SCORE_MIN:
                qty=(balance*RISK_PCT*LEVERAGE)/price
                sl=price-SL_ATR*a; tp=bmid; tp_p=price+PARTIAL_TP_ATR*a
                if sl>0 and tp>price:
                    pos={"side":"long","entry":price,"sl":sl,"tp":tp,
                         "tp_p":tp_p,"qty":qty,"sc":sc,"oi":i,"pd":False}
    return trades,balance

def report(all_trades,final_bal):
    if not all_trades: print("Sin trades generados."); return
    wins=[t for t in all_trades if t["result"]=="WIN"]
    losses=[t for t in all_trades if t["result"]=="LOSS"]
    total=len(all_trades); wr=len(wins)/total*100
    tp=sum(t["pnl"] for t in all_trades)
    aw=sum(t["pnl"] for t in wins)/len(wins) if wins else 0
    al=sum(t["pnl"] for t in losses)/len(losses) if losses else 0
    pf=abs(sum(t["pnl"] for t in wins))/abs(sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses)!=0 else 999
    roi=(final_bal-INITIAL_BAL)/INITIAL_BAL*100
    sep="="*55
    lines=[sep,"  BB+RSI ELITE v5 - BACKTEST REAL BINGX",sep,
        f"  Balance inicial : ${INITIAL_BAL:.2f}",
        f"  Balance final   : ${final_bal:.2f}  ROI: {roi:+.1f}%",
        f"  Total trades    : {total}",
        f"  Ganados/Perdidos: {len(wins)}/{len(losses)}  WR: {wr:.1f}%",
        f"  PnL total       : ${tp:+.2f}",
        f"  Ganancia media  : ${aw:+.4f}",
        f"  Perdida media   : ${al:+.4f}",
        f"  Profit Factor   : {pf:.2f}",
        f"  Expectativa/trade: ${(wr/100*aw+(1-wr/100)*al):+.4f}",""]
    by_sym={}
    for t in all_trades:
        s=t["symbol"]
        if s not in by_sym: by_sym[s]={"n":0,"w":0,"pnl":0}
        by_sym[s]["n"]+=1; by_sym[s]["pnl"]+=t["pnl"]
        if t["result"]=="WIN": by_sym[s]["w"]+=1
    lines.append("  POR PAR:")
    for s,v in sorted(by_sym.items(),key=lambda x:-x[1]["pnl"]):
        wr_s=v["w"]/v["n"]*100 if v["n"] else 0
        e="+" if v["pnl"]>=0 else "-"
        lines.append(f"  {e} {s:12s}  {v['n']:3d}tr  WR:{wr_s:.0f}%  ${v['pnl']:+.4f}")
    lines.append("")
    by_r={}
    for t in all_trades:
        r2=t["reason"]
        if r2 not in by_r: by_r[r2]={"n":0,"w":0,"pnl":0}
        by_r[r2]["n"]+=1; by_r[r2]["pnl"]+=t["pnl"]
        if t["result"]=="WIN": by_r[r2]["w"]+=1
    lines.append("  POR RAZON CIERRE:")
    for r2,v in sorted(by_r.items(),key=lambda x:-x[1]["n"]):
        wr_r=v["w"]/v["n"]*100 if v["n"] else 0
        lines.append(f"  {r2:8s}  {v['n']:3d}tr  WR:{wr_r:.0f}%  ${v['pnl']:+.4f}")
    lines.append("")
    monthly={}
    for t in sorted(all_trades,key=lambda x:x["date"]):
        m=t["date"][:7]
        if m not in monthly: monthly[m]=0
        monthly[m]+=t["pnl"]
    lines.append("  CURVA MENSUAL:")
    bal2=INITIAL_BAL
    for m,pnl in sorted(monthly.items()):
        bal2+=pnl; e="+" if pnl>=0 else "-"
        lines.append(f"  {e} {m}  ${pnl:>+7.4f}  Balance:${bal2:.2f}")
    lines.append("")
    if pf>=1.5 and wr>=50: verdict="RENTABLE ✓"; tag="OK"
    elif pf>=1.2 and wr>=45: verdict="MARGINAL ~"; tag="!!"
    else: verdict="NO RENTABLE ✗"; tag="XX"
    lines.append(f"  [{tag}] {verdict} - PF={pf:.2f} WR={wr:.1f}% ROI={roi:+.1f}%")
    lines.append(sep)
    out="\n".join(lines)
    print(out)
    with open("backtest_resultado.txt","w",encoding="utf-8") as f:
        f.write(out+f"\n\nGenerado: {datetime.now()}\n")
    print("\n  Guardado en: backtest_resultado.txt")

def main():
    print("="*55)
    print("  BACKTEST REAL - datos de BingX 1h (~83 dias)")
    print("="*55)
    all_trades=[]; balance=INITIAL_BAL
    for sym in SYMBOLS:
        df=fetch(sym)
        if df.empty: continue
        trades,balance=backtest(df,sym,balance)
        all_trades.extend(trades)
        w=sum(1 for t in trades if t["result"]=="WIN")
        p=sum(t["pnl"] for t in trades)
        print(f"  -> {len(trades)} trades {w}G {len(trades)-w}P  PnL:${p:+.4f}")
        time.sleep(0.5)
    print(f"\nTotal: {len(all_trades)} trades")
    report(all_trades,balance)

main()
