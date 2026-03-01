"""
backtest.py — Motor de Backtesting para SATY v14
══════════════════════════════════════════════════════════════
Usa datos históricos reales de BingX para simular exactamente
el comportamiento del bot: 6 módulos + TP1/TP2/TP3 + trailing.

USO:
  python backtest.py                      # top 20 pares, 90 días
  python backtest.py --symbol BTC/USDT:USDT
  python backtest.py --days 180 --tf 15m
  python backtest.py --minscore 6 --top 50
  python backtest.py --feedbrain          # pre-entrena el Brain

SALIDA:
  backtest_results.csv   → todos los trades
  backtest_report.html   → equity curve + métricas interactivas
  brain_data.json        → Brain actualizado (con --feedbrain)
"""

import os, sys, csv, argparse, logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import ccxt
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backtest")

API_KEY    = os.environ.get("BINGX_API_KEY",    "")
API_SECRET = os.environ.get("BINGX_API_SECRET", "")
CAPITAL    = float(os.environ.get("BT_CAPITAL",  "1000"))
FIXED_USDT = float(os.environ.get("BT_USDT",     "20"))
LEVERAGE   = float(os.environ.get("BT_LEVERAGE", "10"))
COMM       = 0.0005  # 0.05% taker
SLIP       = 0.0003  # slippage
TP1_M=1.0; TP2_M=2.0; TP3_M=4.0; SL_M=1.0

@dataclass
class BtTrade:
    symbol:str=""; side:str=""; entry_bar:int=0; exit_bar:int=0
    entry_price:float=0.0; exit_price:float=0.0
    tp1:float=0.0; tp2:float=0.0; tp3:float=0.0; sl:float=0.0
    pnl:float=0.0; pnl_pct:float=0.0; max_pct:float=0.0
    reason:str=""; score:int=0; modules:str=""; signals:str=""
    atr:float=0.0; rsi:float=0.0; adx:float=0.0
    bars:int=0; entry_time:str=""; exit_time:str=""
    tp1_hit:bool=False; tp2_hit:bool=False

def fetch_hist(ex, sym, tf, days):
    tf_m={"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,"1h":60,"4h":240}.get(tf,5)
    since=int((datetime.now(timezone.utc)-timedelta(days=days)).timestamp()*1000)
    bars=[]; limit=500
    while True:
        try:
            raw=ex.fetch_ohlcv(sym,tf,since=since,limit=limit)
            if not raw: break
            bars.extend(raw); since=raw[-1][0]+1
            if len(raw)<limit: break
        except Exception as e: log.warning(f"{sym}: {e}"); break
    if not bars: return pd.DataFrame()
    df=pd.DataFrame(bars,columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"]=pd.to_datetime(df["timestamp"],unit="ms"); df.set_index("timestamp",inplace=True)
    return df.astype(float).drop_duplicates()

def resamp(df,tf):
    r={"15m":"15T","1h":"1H","4h":"4H","1d":"1D"}.get(tf,"15T")
    return df.resample(r).agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()

def run_signal(df_full, dh1, dh2, bar, min_score, min_mods):
    """Ejecuta los 6 módulos en la barra `bar` sin look-ahead."""
    from saty_v14 import (mod_conf_pro,mod_bollinger_hunter,mod_smc,
                           mod_powertrend,mod_bbpct,mod_rsi_plus,
                           consensus,htf_bias,rsi,atr,adx)
    df  = df_full.iloc[:bar+1]
    d1  = dh1[dh1.index<=df_full.index[bar]]
    d2  = dh2[dh2.index<=df_full.index[bar]]
    if len(df)<200 or len(d1)<30 or len(d2)<30: return None
    try:
        rv=float(rsi(df["close"]).iloc[-2]); av=float(atr(df).iloc[-2])
        _,_,ax=adx(df); axv=float(ax.iloc[-2])
        if pd.isna(rv) or pd.isna(axv): return None
        htf1b,htf1bear=htf_bias(d1); htf2b,htf2bear=htf_bias(d2)
        m1l,m1s=mod_conf_pro(df,htf1b,htf1bear)
        m2l,m2s=mod_bollinger_hunter(df,htf2b,htf2bear)
        m3l,m3s=mod_smc(df,d1)
        m4l,m4s=mod_powertrend(df,htf1b,htf1bear)
        m5l,m5s=mod_bbpct(df,htf1b,htf1bear)
        m6l,m6s=mod_rsi_plus(df,htf1b,htf1bear)
        d,sc,mods,sigs=consensus(m1l,m1s,m2l,m2s,m3l,m3s,m4l,m4s,m5l,m5s,m6l,m6s)
        if d is None or sc<min_score: return None
        return {"direction":d,"score":sc,"modules":mods,"signals":sigs,"atr":av,"rsi":rv,"adx":axv}
    except Exception as e: log.debug(f"signal bar {bar}: {e}"); return None

def simulate(df, entry_bar, sig, sym):
    """Simula el trade con TP1/TP2/TP3 + trailing exactamente como el bot."""
    side=sig["direction"]; ep=float(df["close"].iloc[entry_bar])
    slip=ep*SLIP; ep=ep+slip if side=="long" else ep-slip
    atr_v=sig["atr"]; notional=FIXED_USDT*LEVERAGE; contracts=notional/ep
    if side=="long":
        sl=ep-atr_v*SL_M; tp1=ep+atr_v*TP1_M; tp2=ep+atr_v*TP2_M; tp3=ep+atr_v*TP3_M
    else:
        sl=ep+atr_v*SL_M; tp1=ep-atr_v*TP1_M; tp2=ep-atr_v*TP2_M; tp3=ep-atr_v*TP3_M
    t=BtTrade(symbol=sym,side=side,entry_bar=entry_bar,entry_price=ep,
              sl=sl,tp1=tp1,tp2=tp2,tp3=tp3,score=sig["score"],
              modules=sig["modules"],signals=sig["signals"][:60],
              atr=atr_v,rsi=sig["rsi"],adx=sig["adx"],
              entry_time=str(df.index[entry_bar]))
    rem=1.0; pnl=-notional*COMM; tp1h=False; tp2h=False
    tr_h=tr_l=peak=ep; stall=0; phase="normal"; sl_now=sl; max_p=0.0
    for bar in range(entry_bar+1, min(entry_bar+600,len(df))):
        h=float(df["high"].iloc[bar]); l=float(df["low"].iloc[bar]); c=float(df["close"].iloc[bar])
        cp=((c-ep)/ep*100 if side=="long" else (ep-c)/ep*100); max_p=max(max_p,cp)
        if not tp1h:
            if (side=="long" and h>=tp1) or (side=="short" and l<=tp1):
                pnl+=((tp1-ep if side=="long" else ep-tp1)*contracts*0.25)-notional*0.25*COMM
                rem-=0.25; tp1h=True; sl_now=ep; t.tp1_hit=True; peak=tp1
        if tp1h and not tp2h:
            if (side=="long" and h>=tp2) or (side=="short" and l<=tp2):
                pnl+=((tp2-ep if side=="long" else ep-tp2)*contracts*0.25)-notional*0.25*COMM
                rem-=0.25; tp2h=True; sl_now=tp1; t.tp2_hit=True
        if tp1h and rem>0:
            np2=(c>peak if side=="long" else c<peak)
            if np2: peak=c; stall=0
            else: stall+=1
            ret=((peak-c)/max(abs(peak-ep),1e-9)*100 if side=="long" else (c-peak)/max(abs(peak-ep),1e-9)*100)
            if   cp>5.0:   phase="ultra"
            elif ret>30:   phase="locked"
            elif stall>=3: phase="tight"
            else:          phase="normal"
            tm={"normal":0.8,"tight":0.4,"locked":0.2,"ultra":0.15}[phase]
            if side=="long":
                tr_h=max(tr_h,c); ts=tr_h-atr_v*tm
                if l<=ts:
                    xp=max(ts,l); pnl+=((xp-ep)*contracts*rem)-notional*rem*COMM
                    t.exit_bar=bar; t.exit_price=xp; t.reason=f"TRAIL_{phase.upper()}"
                    t.exit_time=str(df.index[bar]); break
            else:
                tr_l=min(tr_l,c); ts=tr_l+atr_v*tm
                if h>=ts:
                    xp=min(ts,h); pnl+=((ep-xp)*contracts*rem)-notional*rem*COMM
                    t.exit_bar=bar; t.exit_price=xp; t.reason=f"TRAIL_{phase.upper()}"
                    t.exit_time=str(df.index[bar]); break
        if (side=="long" and l<=sl_now) or (side=="short" and h>=sl_now):
            xp=sl_now; pnl+=((xp-ep if side=="long" else ep-xp)*contracts*rem)-notional*rem*COMM
            t.exit_bar=bar; t.exit_price=xp
            t.reason="TP3" if ((side=="long" and h>=tp3) or (side=="short" and l<=tp3)) else "SL"
            t.exit_time=str(df.index[bar]); break
        if tp2h and rem>0:
            if (side=="long" and h>=tp3) or (side=="short" and l<=tp3):
                xp=tp3; pnl+=((xp-ep if side=="long" else ep-xp)*contracts*rem)-notional*rem*COMM
                t.exit_bar=bar; t.exit_price=xp; t.reason="TP3_COMP"
                t.exit_time=str(df.index[bar]); break
    else:
        if rem>0:
            xp=float(df["close"].iloc[-1])
            pnl+=((xp-ep if side=="long" else ep-xp)*contracts*rem)-notional*rem*COMM
            t.exit_bar=len(df)-1; t.exit_price=xp; t.reason="TIMEOUT"
            t.exit_time=str(df.index[-1])
    t.pnl=round(pnl,4); t.pnl_pct=round(pnl/FIXED_USDT*100,2)
    t.max_pct=round(max_p,2); t.bars=t.exit_bar-entry_bar
    return t

def bt_symbol(ex,sym,tf,htf1,htf2,days,min_score,min_mods):
    log.info(f"  BT {sym} {tf} {days}d ...")
    df=fetch_hist(ex,sym,tf,days); dh1=fetch_hist(ex,sym,htf1,days); dh2=fetch_hist(ex,sym,htf2,days)
    if len(df)<300: log.warning(f"  {sym}: insuf ({len(df)}b)"); return []
    if len(dh1)<50: dh1=resamp(df,htf1)
    if len(dh2)<50: dh2=resamp(df,htf2)
    tf_m={"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,"1h":60}.get(tf,5)
    cd_bars=max(5,30*60//tf_m)
    trades=[]; last_exit=-cd_bars; bar=200
    while bar<len(df)-1:
        if bar-last_exit<cd_bars: bar+=1; continue
        sig=run_signal(df,dh1,dh2,bar,min_score,min_mods)
        if sig:
            t=simulate(df,bar,sig,sym); trades.append(t)
            last_exit=t.exit_bar; bar=t.exit_bar+1
        else: bar+=1
    return trades

def print_report(trades):
    if not trades: print("Sin trades."); return
    wins=sum(1 for t in trades if t.pnl>0); tot=len(trades); losses=tot-wins
    wr=wins/tot*100; gp=sum(t.pnl for t in trades if t.pnl>0)
    gl=abs(sum(t.pnl for t in trades if t.pnl<=0)); tp=sum(t.pnl for t in trades)
    pf=gp/gl if gl>0 else 0; aw=gp/wins if wins>0 else 0; al=gl/losses if losses>0 else 0
    rr=aw/al if al>0 else 0; exp=(wr/100)*aw-(1-wr/100)*al
    eq=CAPITAL; peak=eq; mdd=0.0
    for t in trades:
        eq+=t.pnl
        if eq>peak: peak=eq
        dd=(peak-eq)/peak*100
        if dd>mdd: mdd=dd
    print(f"\n{'='*60}\n  SATY v14 — BACKTEST\n{'='*60}")
    for k,v in [("Trades",tot),("Win Rate",f"{wr:.1f}%"),("PF",f"{pf:.2f}"),
                ("PnL",f"${tp:+.2f}"),("ROI",f"{tp/CAPITAL*100:+.1f}%"),
                ("Max DD",f"{mdd:.1f}%"),("R:R",f"{rr:.2f}"),
                ("Expectancy",f"${exp:+.2f}"),("Capital final",f"${CAPITAL+tp:.2f}")]:
        print(f"  {k:<18} {v:>15}")
    ms={}
    for t in trades:
        k=t.modules or "?"
        if k not in ms: ms[k]={"t":0,"w":0,"pnl":0.0}
        ms[k]["t"]+=1; ms[k]["w"]+=1 if t.pnl>0 else 0; ms[k]["pnl"]+=t.pnl
    print(f"\n  MÓDULOS:")
    for k,v in sorted(ms.items(),key=lambda x:-x[1]["pnl"])[:8]:
        wr_m=v["w"]/v["t"]*100; ic="🟢" if wr_m>=55 else "🟡" if wr_m>=45 else "🔴"
        print(f"  {ic} {k[:38]:<38} {v['t']:>4}t {wr_m:>5.1f}% ${v['pnl']:>+8.2f}")
    print(f"\n  WR POR SCORE:")
    sc_s={}
    for t in trades:
        if t.score not in sc_s: sc_s[t.score]={"t":0,"w":0}
        sc_s[t.score]["t"]+=1; sc_s[t.score]["w"]+=1 if t.pnl>0 else 0
    for s in sorted(sc_s):
        v=sc_s[s]; wr_s=v["w"]/v["t"]*100
        bar2="█"*int(wr_s/10)+"░"*(10-int(wr_s/10))
        print(f"  Score {s:>2}: {bar2} {wr_s:>5.1f}% ({v['t']}t)")
    print("="*60)

def save_csv(trades,path="backtest_results.csv"):
    with open(path,"w",newline="") as f:
        w=csv.writer(f)
        w.writerow(["symbol","side","entry_time","exit_time","entry","exit","pnl","pnl%","max%","reason","score","modules","rsi","adx","tp1","tp2","bars"])
        for t in trades:
            w.writerow([t.symbol,t.side,t.entry_time,t.exit_time,
                        round(t.entry_price,8),round(t.exit_price,8),
                        round(t.pnl,4),round(t.pnl_pct,2),round(t.max_pct,2),
                        t.reason,t.score,t.modules,round(t.rsi,1),round(t.adx,1),t.tp1_hit,t.tp2_hit,t.bars])
    log.info(f"✅ CSV: {path}")

def save_html(trades,path="backtest_report.html"):
    trades=sorted(trades,key=lambda t:t.entry_time)
    eq=[CAPITAL]; ec=CAPITAL
    for t in trades: ec+=t.pnl; eq.append(round(ec,2))
    wins=sum(1 for t in trades if t.pnl>0); tot=len(trades)
    wr=wins/tot*100 if tot else 0; tp=sum(t.pnl for t in trades)
    gp=sum(t.pnl for t in trades if t.pnl>0); gl=abs(sum(t.pnl for t in trades if t.pnl<=0))
    pf=gp/gl if gl>0 else 0; roi=tp/CAPITAL*100
    ms={}
    for t in trades:
        k=t.modules or "?"
        if k not in ms: ms[k]={"t":0,"w":0,"pnl":0.0}
        ms[k]["t"]+=1; ms[k]["w"]+=1 if t.pnl>0 else 0; ms[k]["pnl"]+=t.pnl
    mod_rows="".join(
        f'<tr><td>{k[:42]}</td><td>{v["t"]}</td>'
        f'<td style="color:{"#3fb950" if v["w"]/max(v["t"],1)*100>=55 else "#d29922" if v["w"]/max(v["t"],1)*100>=45 else "#f85149"}">'
        f'{v["w"]/max(v["t"],1)*100:.1f}%</td><td>${v["pnl"]:+.2f}</td></tr>'
        for k,v in sorted(ms.items(),key=lambda x:-x[1]["pnl"])[:15])
    trade_rows="".join(
        f'<tr style="color:{"#3fb950" if t.pnl>0 else "#f85149"}">'
        f'<td>{t.symbol}</td><td>{t.side}</td><td>{t.entry_time[:16]}</td><td>{t.exit_time[:16]}</td>'
        f'<td>{t.entry_price:.6g}</td><td>{t.exit_price:.6g}</td><td>${t.pnl:+.2f}</td>'
        f'<td>{t.pnl_pct:+.1f}%</td><td>{t.reason}</td><td>{t.score}</td><td>{t.modules[:28]}</td></tr>'
        for t in trades[-60:])
    dates_js=",".join(f'"{t.entry_time[:10]}"' for t in trades)+(',"end"' if trades else '')
    html=f"""<!DOCTYPE html><html lang="es">
<head><meta charset="UTF-8"><title>SATY v14 Backtest</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:monospace;margin:24px}}
h1{{color:#58a6ff}} h2{{color:#79c0ff;border-bottom:1px solid #30363d;padding-bottom:6px;margin-top:28px}}
.g{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}}
.c{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;text-align:center}}
.v{{font-size:1.7em;font-weight:bold}} .l{{font-size:0.78em;color:#8b949e;margin-top:4px}}
.green{{color:#3fb950}} .red{{color:#f85149}} .yellow{{color:#d29922}}
table{{width:100%;border-collapse:collapse;font-size:0.83em}} th{{background:#21262d;padding:8px;text-align:left;border:1px solid #30363d}} td{{padding:5px 8px;border:1px solid #161b22}} tr:hover{{background:#21262d}}
canvas{{max-height:300px}}
</style></head><body>
<h1>📊 SATY ELITE v14 — Backtest Report</h1>
<p style="color:#8b949e">Generado: {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')} | Capital: ${CAPITAL:.0f} | Trades: {tot}</p>
<div class="g">
  <div class="c"><div class="v {'green' if wr>=50 else 'red'}">{wr:.1f}%</div><div class="l">Win Rate</div></div>
  <div class="c"><div class="v {'green' if pf>=1.5 else 'yellow' if pf>=1 else 'red'}">{pf:.2f}</div><div class="l">Profit Factor</div></div>
  <div class="c"><div class="v {'green' if tp>0 else 'red'}">${tp:+.2f}</div><div class="l">PnL Total</div></div>
  <div class="c"><div class="v {'green' if roi>0 else 'red'}">{roi:+.1f}%</div><div class="l">ROI</div></div>
  <div class="c"><div class="v green">{wins}</div><div class="l">Ganadores</div></div>
  <div class="c"><div class="v red">{tot-wins}</div><div class="l">Perdedores</div></div>
  <div class="c"><div class="v green">${gp:.2f}</div><div class="l">Ganancia bruta</div></div>
  <div class="c"><div class="v red">${gl:.2f}</div><div class="l">Pérdida bruta</div></div>
</div>
<h2>📈 Equity Curve</h2>
<canvas id="ec"></canvas>
<script>
new Chart(document.getElementById('ec').getContext('2d'),{{
  type:'line',data:{{labels:[{dates_js}],datasets:[{{label:'Equity ($)',
    data:[{','.join(str(e) for e in eq)}],borderColor:'#58a6ff',
    backgroundColor:'rgba(88,166,255,0.07)',fill:true,tension:0.3,pointRadius:0,borderWidth:2}}]}},
  options:{{responsive:true,plugins:{{legend:{{labels:{{color:'#c9d1d9'}}}}}},
    scales:{{y:{{grid:{{color:'#21262d'}},ticks:{{color:'#8b949e'}}}},
             x:{{ticks:{{maxTicksLimit:12,color:'#8b949e'}},grid:{{color:'#21262d'}}}}}}}}
}});
</script>
<h2>🧠 Módulos</h2>
<table><tr><th>Combinación</th><th>Trades</th><th>Win Rate</th><th>PnL</th></tr>{mod_rows}</table>
<h2>📋 Últimos 60 Trades</h2>
<table><tr><th>Sym</th><th>Side</th><th>Entrada</th><th>Salida</th><th>E.P</th><th>S.P</th><th>PnL</th><th>%</th><th>Razón</th><th>Score</th><th>Módulos</th></tr>{trade_rows}</table>
</body></html>"""
    with open(path,"w",encoding="utf-8") as f: f.write(html)
    log.info(f"✅ HTML: {path}")

def feed_brain(trades):
    try:
        from brain import brain
        log.info(f"Alimentando Brain con {len(trades)} trades ...")
        for t in sorted(trades,key=lambda x:x.entry_time):
            brain.record_trade(symbol=t.symbol,side=t.side,modules=t.modules,signals=t.signals,
                               entry_score=t.score,pnl=t.pnl,pnl_pct=t.pnl_pct,
                               max_profit=t.max_pct,reason=t.reason,duration_min=t.bars*5,
                               btc_bull=True,btc_bear=False,btc_adx=20.0,rsi_entry=t.rsi,adx_entry=t.adx)
        log.info(f"Brain: {brain.data.total_trades}t | score_min={brain.data.effective_min_score}")
        for ins in brain.data.insights[:8]: log.info(f"  💡 {ins}")
    except Exception as e: log.warning(f"feed_brain: {e}")

def main():
    p=argparse.ArgumentParser(description="SATY v14 Backtester")
    p.add_argument("--symbol",   default=None)
    p.add_argument("--days",     type=int,   default=90)
    p.add_argument("--tf",       default="5m")
    p.add_argument("--htf1",     default="15m")
    p.add_argument("--htf2",     default="1h")
    p.add_argument("--top",      type=int,   default=20)
    p.add_argument("--minscore", type=int,   default=5)
    p.add_argument("--minmods",  type=int,   default=2)
    p.add_argument("--capital",  type=float, default=1000)
    p.add_argument("--feedbrain",action="store_true")
    p.add_argument("--no-html",  action="store_true")
    args=p.parse_args()

    global CAPITAL; CAPITAL=args.capital
    log.info(f"SATY v14 BACKTESTER | {args.days}d | {args.tf} | score≥{args.minscore}")

    if not (API_KEY and API_SECRET):
        log.error("Faltan BINGX_API_KEY / BINGX_API_SECRET"); sys.exit(1)
    ex=ccxt.bingx({"apiKey":API_KEY,"secret":API_SECRET,"options":{"defaultType":"swap"},"enableRateLimit":True})
    ex.load_markets()

    if args.symbol:
        symbols=[args.symbol]
    else:
        cands=[s for s,m in ex.markets.items() if m.get("swap") and m.get("quote")=="USDT" and m.get("active",True)]
        try:
            tickers=ex.fetch_tickers(cands)
            ranked=sorted([(s,float(tickers.get(s,{}).get("quoteVolume",0) or 0)) for s in cands],key=lambda x:-x[1])
            symbols=[s for s,v in ranked if v>0][:args.top]
        except Exception: symbols=cands[:args.top]

    log.info(f"Backtesting {len(symbols)} pares: {symbols[:5]} ...")
    all_trades=[]
    for sym in symbols:
        try:
            ts=bt_symbol(ex,sym,args.tf,args.htf1,args.htf2,args.days,args.minscore,args.minmods)
            if ts:
                all_trades.extend(ts)
                wins_s=sum(1 for t in ts if t.pnl>0)
                log.info(f"  {sym}: {len(ts)}t WR:{wins_s/len(ts)*100:.1f}% PnL:${sum(t.pnl for t in ts):+.2f}")
        except Exception as e: log.error(f"{sym}: {e}")

    if not all_trades: log.warning("Sin trades."); return
    all_trades.sort(key=lambda t:t.entry_time)
    print_report(all_trades)
    save_csv(all_trades)
    if not args.no_html: save_html(all_trades)
    if args.feedbrain: feed_brain(all_trades)
    log.info(f"\n✅ {len(all_trades)} trades | backtest_results.csv | backtest_report.html")

if __name__=="__main__":
    main()
