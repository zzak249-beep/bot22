"""
brain.py — Motor de Aprendizaje Adaptativo
Aprende de cada trade cerrado y ajusta parámetros automáticamente.
"""
import json, os, time, logging
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from dataclasses import dataclass, field, asdict

log = logging.getLogger("brain")

BRAIN_FILE         = "brain_data.json"
MIN_SAMPLES        = 10
MAX_BOOST          = 2.0
MIN_BOOST          = 0.3
LEARNING_RATE      = 0.15
BLACKLIST_WR       = 0.30
BLACKLIST_H        = 24
WINDOW             = 30

@dataclass
class BrainData:
    version:             int   = 2
    total_trades:        int   = 0
    last_updated:        str   = ""
    last_review:         float = 0.0
    history:             List[dict] = field(default_factory=list)
    module_stats:        Dict[str,dict] = field(default_factory=dict)
    hour_stats:          Dict[str,dict] = field(default_factory=dict)
    min_score_overrides: Dict[str,int]  = field(default_factory=dict)
    effective_min_score: int   = 5
    effective_cd_min:    int   = 30
    consec_losses_today: int   = 0
    last_loss_ts:        float = 0.0
    insights:            List[str] = field(default_factory=list)

class Brain:
    def __init__(self):
        self.data = BrainData()
        self.load()

    def load(self):
        try:
            if os.path.exists(BRAIN_FILE):
                with open(BRAIN_FILE) as f: raw=json.load(f)
                self.data=BrainData(
                    version=raw.get("version",1), total_trades=raw.get("total_trades",0),
                    last_updated=raw.get("last_updated",""), last_review=raw.get("last_review",0.0),
                    history=raw.get("history",[])[-500:],
                    module_stats=raw.get("module_stats",{}),
                    hour_stats=raw.get("hour_stats",{}),
                    min_score_overrides=raw.get("min_score_overrides",{}),
                    effective_min_score=raw.get("effective_min_score",5),
                    effective_cd_min=raw.get("effective_cd_min",30),
                    consec_losses_today=raw.get("consec_losses_today",0),
                    last_loss_ts=raw.get("last_loss_ts",0.0),
                    insights=raw.get("insights",[])[-20:],
                )
                log.info(f"Brain: {self.data.total_trades} trades históricos cargados")
        except Exception as e:
            log.warning(f"Brain load: {e}"); self.data=BrainData()

    def save(self):
        try:
            self.data.last_updated=datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
            with open(BRAIN_FILE,"w") as f: json.dump(asdict(self.data),f,indent=2)
        except Exception as e: log.warning(f"Brain save: {e}")

    def record_trade(self, symbol,side,modules,signals,entry_score,pnl,pnl_pct,
                     max_profit,reason,duration_min,btc_bull,btc_bear,btc_adx,
                     rsi_entry=0.0,adx_entry=0.0):
        now_h=datetime.now(timezone.utc).hour
        win=pnl>0
        rec={"ts":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
             "symbol":symbol,"side":side,"modules":modules,"signals":signals[:80],
             "entry_score":entry_score,"pnl":pnl,"pnl_pct":pnl_pct,
             "max_profit":max_profit,"reason":reason,"duration_min":duration_min,
             "btc_bull":btc_bull,"btc_bear":btc_bear,"btc_adx":btc_adx,
             "rsi_entry":rsi_entry,"adx_entry":adx_entry,"hour_utc":now_h,"win":win}
        self.data.history.append(rec)
        if len(self.data.history)>500: self.data.history=self.data.history[-500:]
        self.data.total_trades+=1
        self._update_mods(modules,win,pnl)
        self._update_hours(now_h,win,pnl)
        if not win: self.data.consec_losses_today+=1; self.data.last_loss_ts=time.time()
        else: self.data.consec_losses_today=0
        if self.data.total_trades%10==0 or time.time()-self.data.last_review>86400:
            self._review()
        self.save()
        log.info(f"Brain: {'✅' if win else '❌'} {symbol} {pnl:+.2f} [{modules}] | total:{self.data.total_trades}")

    def _update_mods(self,modules,win,pnl):
        k=modules or "unknown"
        if k not in self.data.module_stats:
            self.data.module_stats[k]={"name":k,"trades":0,"wins":0,"total_pnl":0.0,"avg_pnl":0.0,"boost":1.0,"blacklisted_until":0.0}
        s=self.data.module_stats[k]
        s["trades"]+=1; s["wins"]+=1 if win else 0; s["total_pnl"]+=pnl
        s["avg_pnl"]=s["total_pnl"]/s["trades"]
        wr=s["wins"]/s["trades"]
        target=0.3+(wr/0.70)*1.7; target=max(MIN_BOOST,min(MAX_BOOST,target))
        if s["trades"]>=MIN_SAMPLES:
            s["boost"]=s.get("boost",1.0)+LEARNING_RATE*(target-s.get("boost",1.0))
        if s["trades"]>=15 and wr<BLACKLIST_WR:
            s["blacklisted_until"]=time.time()+BLACKLIST_H*3600
            log.warning(f"Brain: {k} BLACKLIST 24h (WR:{wr*100:.0f}%)")
        elif s["trades"]>=20 and wr>=0.50 and s.get("blacklisted_until",0)>0:
            s["blacklisted_until"]=0

    def _update_hours(self,hour,win,pnl):
        k=str(hour)
        if k not in self.data.hour_stats:
            self.data.hour_stats[k]={"hour":hour,"trades":0,"wins":0,"pnl":0.0}
        s=self.data.hour_stats[k]
        s["trades"]+=1; s["wins"]+=1 if win else 0; s["pnl"]+=pnl

    def _review(self):
        self.data.last_review=time.time(); insights=[]
        if self.data.total_trades<MIN_SAMPLES: return
        recent=self.data.history[-WINDOW:]
        if len(recent)>=10:
            rwr=sum(1 for t in recent if t["pnl"]>0)/len(recent)
            t5=[t for t in recent if t["entry_score"]>=5]
            if len(t5)>=5:
                wr5=sum(1 for t in t5 if t["pnl"]>0)/len(t5)
                if wr5<0.45 and self.data.effective_min_score<8:
                    self.data.effective_min_score+=1
                    insights.append(f"⬆️ Score mín → {self.data.effective_min_score} (WR@5={wr5*100:.0f}%)")
                elif wr5>0.65 and self.data.effective_min_score>4:
                    self.data.effective_min_score-=1
                    insights.append(f"⬇️ Score mín → {self.data.effective_min_score} (WR@5={wr5*100:.0f}%)")
        for combo,s in self.data.module_stats.items():
            if s["trades"]<MIN_SAMPLES: continue
            b=s.get("boost",1.0)
            if b<0.7:
                self.data.min_score_overrides[combo]=self.data.effective_min_score+2
                insights.append(f"🎯 {combo}: score local→{self.data.effective_min_score+2}")
            elif b>1.3 and combo in self.data.min_score_overrides:
                del self.data.min_score_overrides[combo]
        worst=[(int(h),s) for h,s in self.data.hour_stats.items() if s["trades"]>=5 and s["wins"]/s["trades"]<=0.35]
        best =[(int(h),s) for h,s in self.data.hour_stats.items() if s["trades"]>=5 and s["wins"]/s["trades"]>=0.70]
        if worst: insights.append(f"⚠️ Horas malas UTC: {sorted([h for h,_ in worst])} → pos. 50%")
        if best:  insights.append(f"⭐ Horas buenas UTC: {sorted([h for h,_ in best])} → pos. 120%")
        if insights:
            self.data.insights=insights+self.data.insights
            self.data.insights=self.data.insights[:20]
            for ins in insights: log.info(f"Brain: {ins}")

    def get_module_boost(self,modules) -> float:
        s=self.data.module_stats.get(modules,{})
        if not s: return 1.0
        if s.get("blacklisted_until",0)>time.time(): return 0.0
        return float(s.get("boost",1.0))

    def is_blacklisted(self,modules) -> bool:
        return self.data.module_stats.get(modules,{}).get("blacklisted_until",0)>time.time()

    def get_effective_min_score(self,modules) -> int:
        return self.data.min_score_overrides.get(modules,self.data.effective_min_score)

    def get_hour_mult(self,h=None) -> float:
        h=h if h is not None else datetime.now(timezone.utc).hour
        s=self.data.hour_stats.get(str(h),{})
        if not s or s.get("trades",0)<5: return 1.0
        wr=s["wins"]/s["trades"]
        if wr>=0.65: return 1.2
        if wr>=0.50: return 1.0
        if wr>=0.40: return 0.7
        return 0.5

    def adjusted_score(self,raw,modules) -> float:
        return raw*self.get_module_boost(modules)

    def should_enter(self,raw,modules) -> Tuple[bool,str]:
        if self.is_blacklisted(modules):
            s=self.data.module_stats.get(modules,{})
            wr=s["wins"]/s["trades"]*100 if s.get("trades",0)>0 else 0
            h=(s.get("blacklisted_until",0)-time.time())/3600
            return False,f"blacklisted WR:{wr:.0f}% ({h:.1f}h)"
        adj=self.adjusted_score(raw,modules)
        mn=self.get_effective_min_score(modules)
        if adj<mn: return False,f"score_adj {adj:.1f}<{mn}"
        return True,""

    def telegram_report(self) -> str:
        lines=["🧠 <b>BRAIN — Aprendizaje Adaptativo</b>",
               "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
               f"📊 Trades: <b>{self.data.total_trades}</b>",
               f"🎯 Score mín efectivo: <b>{self.data.effective_min_score}</b>"]
        if self.data.module_stats:
            sm=sorted([(k,v) for k,v in self.data.module_stats.items() if v.get("trades",0)>=3],
                      key=lambda x:x[1]["wins"]/max(x[1]["trades"],1),reverse=True)
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("📈 <b>Módulos:</b>")
            for k,v in sm[:6]:
                t=v.get("trades",0); w=v.get("wins",0)
                wr=w/t*100 if t>0 else 0; b=v.get("boost",1.0)
                bl=" ⛔BL" if v.get("blacklisted_until",0)>time.time() else ""
                ic="🟢" if wr>=55 else "🟡" if wr>=45 else "🔴"
                lines.append(f"  {ic} {k[:28]}: {t}t {wr:.0f}% b:{b:.2f}{bl}")
        if self.data.hour_stats:
            hd=[(int(h),v) for h,v in self.data.hour_stats.items() if v.get("trades",0)>=5]
            if hd:
                best=max(hd,key=lambda x:x[1]["wins"]/x[1]["trades"])
                worst=min(hd,key=lambda x:x[1]["wins"]/x[1]["trades"])
                bwr=best[1]["wins"]/best[1]["trades"]*100; wwr=worst[1]["wins"]/worst[1]["trades"]*100
                lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                lines.append(f"⏰ Mejor hora: {best[0]:02d}h UTC ({bwr:.0f}%) ×{self.get_hour_mult(best[0]):.1f}")
                lines.append(f"⏰ Peor hora:  {worst[0]:02d}h UTC ({wwr:.0f}%) ×{self.get_hour_mult(worst[0]):.1f}")
        if self.data.insights:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 <b>Insights:</b>")
            for ins in self.data.insights[:5]: lines.append(f"  {ins}")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🕐 {datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}")
        return "\n".join(lines)

    def telegram_summary_line(self) -> str:
        if self.data.total_trades<5: return "🧠 Brain: aprendiendo ..."
        recent=self.data.history[-20:]
        if not recent: return ""
        rwr=sum(1 for t in recent if t["pnl"]>0)/len(recent)*100
        bl=sum(1 for s in self.data.module_stats.values() if s.get("blacklisted_until",0)>time.time())
        return (f"🧠 {self.data.total_trades}t | WR:{rwr:.0f}% | "
                f"ScoreMín:{self.data.effective_min_score} | BL:{bl}")

    def score_distribution_bar(self) -> str:
        if len(self.data.history)<15: return ""
        buckets={}
        for t in self.data.history:
            sc=t.get("entry_score",0); b=f"{(sc//2)*2}-{(sc//2)*2+1}"
            if b not in buckets: buckets[b]={"w":0,"t":0}
            buckets[b]["t"]+=1
            if t["pnl"]>0: buckets[b]["w"]+=1
        lines=["📊 WR por score:"]
        for k in sorted(buckets):
            v=buckets[k]; wr=v["w"]/v["t"]*100 if v["t"]>0 else 0
            bar="█"*int(wr/10)+"░"*(10-int(wr/10))
            lines.append(f"  {k:>4}pt: {bar} {wr:.0f}% ({v['t']}t)")
        return "\n".join(lines)

brain = Brain()

def on_trade_closed(trade_state, pnl, reason, btc_bull, btc_bear, btc_adx, rsi_entry=0.0, adx_entry=0.0):
    try:
        pnl_pct=0.0
        if trade_state.contracts>0 and trade_state.entry_price>0:
            pnl_pct=pnl/(trade_state.entry_price*trade_state.contracts)*100
        dur=0
        try:
            from datetime import datetime
            et=datetime.strptime(trade_state.entry_time,"%d/%m/%Y %H:%M UTC")
            dur=int((datetime.utcnow()-et).total_seconds()/60)
        except Exception: pass
        brain.record_trade(
            symbol=trade_state.symbol, side=trade_state.side,
            modules=trade_state.modules, signals=trade_state.signals[:80],
            entry_score=trade_state.score, pnl=pnl, pnl_pct=pnl_pct,
            max_profit=trade_state.max_pct, reason=reason, duration_min=dur,
            btc_bull=btc_bull, btc_bear=btc_bear, btc_adx=btc_adx,
            rsi_entry=rsi_entry, adx_entry=adx_entry)
    except Exception as e: log.warning(f"on_trade_closed: {e}")

def check_entry(raw_score, modules, size_usdt):
    try:
        can,reason=brain.should_enter(raw_score,modules)
        if not can: return False,0.0,reason
        mult=brain.get_hour_mult()
        return True,size_usdt*mult,f"adj={brain.adjusted_score(raw_score,modules):.1f} h×{mult:.1f}"
    except Exception as e:
        log.warning(f"check_entry: {e}"); return True,size_usdt,"brain_err"
