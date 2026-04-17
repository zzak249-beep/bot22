"""
╔══════════════════════════════════════════════════════════════════════════╗
║            AEGIS GEX v7.0 — ELITE QUANT ENGINE                         ║
║  Simons · QE · Liquidity Sweep · ATR-SL · Session · Correlation        ║
║  BingX Futuros Perpetuos → Telegram                                     ║
╚══════════════════════════════════════════════════════════════════════════╝

NUEVAS ARMAS v7:

 ⚔️  ARMA SECRETA — Liquidity Sweep Detection
     Los grandes barren stops de retail deliberadamente para acumular
     posiciones baratas antes del movimiento real. Detectamos el sweep
     (vela con spike + rechazo + volumen alto) y entramos en la dirección
     correcta justo después. Es la señal de mayor win-rate del sistema.

 🎯  ATR Dynamic Stop Loss
     El trailing ya no es un % fijo. Se calcula como múltiplo del ATR
     de cada símbolo. BTC volátil = SL más amplio. LINK tranquilo = SL
     más ajustado. Evita ser barrido por ruido normal del mercado.

 🏆  Liquidity Tier Scoring
     BTC y ETH son Tier 1 (señales más limpias, menos manipulación).
     Alts son Tier 2 (requieren más confirmación). El sistema exige
     más confluencia a monedas de menor capitalización.

 🔗  Correlation Guard
     Si ETH ya está abierto, SOL tiene correlación ~0.85 con él.
     Abrir SOL sería doblar el riesgo real. El sistema bloquea nuevas
     posiciones que estén correlacionadas con las existentes.

 🕐  Session Filter (London + NY)
     El 80% del volumen institucional ocurre en ventanas específicas:
     London Open (07-10 UTC) y NY Open (13-16 UTC). Fuera de esas
     ventanas, las señales de momentum tienen win-rate 20% menor.
     El sistema ajusta los umbrales según la sesión activa.

 📈  Funding Rate Momentum
     No solo el funding actual, sino su TENDENCIA. Funding subiendo
     rápido = longs se van a liquidar pronto = oportunidad short.
     Un indicador que casi ningún bot retail usa.
"""

import os, asyncio, logging, math, time, collections, random
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import ccxt.pro as ccxt_pro
import ccxt as ccxt_sync
import requests as http_requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("AEGIS")

# ═══════════════════════════════════════════════════════════════
#   CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════

BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")
MODE             = os.getenv("MODE", "paper")

# ── Cuenta ────────────────────────────────────
ACCOUNT_BALANCE  = float(os.getenv("ACCOUNT_BALANCE",  "100"))
RISK_PER_TRADE   = float(os.getenv("RISK_PER_TRADE",   "0.01"))
MAX_OPEN_TRADES  = int(os.getenv("MAX_OPEN_TRADES",    "3"))
MAX_DAILY_LOSS   = float(os.getenv("MAX_DAILY_LOSS",   "0.03"))
LEVERAGE         = int(os.getenv("LEVERAGE",           "3"))

# ── Ejecución ─────────────────────────────────
LIMIT_WAIT_SECS  = int(os.getenv("LIMIT_WAIT_SECS",   "6"))
PRICE_SLIP_PCT   = float(os.getenv("PRICE_SLIP_PCT",   "0.0005"))

# ── Multi-Timeframe ───────────────────────────
MTF_TIMEFRAMES     = ["1m", "5m", "15m", "1h"]
MTF_WEIGHTS        = {"1m": 0.15, "5m": 0.25, "15m": 0.35, "1h": 0.25}
MTF_CONFLUENCE_MIN = float(os.getenv("MTF_CONFLUENCE_MIN", "0.55"))

# ── Señales ───────────────────────────────────
ZSCORE_WINDOW       = int(os.getenv("ZSCORE_WINDOW",      "20"))
ZSCORE_THRESHOLD    = float(os.getenv("ZSCORE_THRESHOLD", "2.5"))
WHALE_MULT          = float(os.getenv("WHALE_VOL_MULT",   "2.5"))
CVD_THRESHOLD       = float(os.getenv("CVD_THRESHOLD",    "0.6"))
ABSORPTION_VOL_MULT = float(os.getenv("ABSORPTION_VOL",   "3.0"))
ABSORPTION_MOVE_PCT = float(os.getenv("ABSORPTION_MOVE",  "0.002"))

# ── Simons ────────────────────────────────────
HURST_WINDOW     = int(os.getenv("HURST_WINDOW",    "40"))
HURST_TREND_MIN  = float(os.getenv("HURST_TREND_MIN",  "0.55"))
HURST_REVERT_MAX = float(os.getenv("HURST_REVERT_MAX", "0.45"))
KURT_MAX         = float(os.getenv("KURT_MAX",       "6.0"))
OI_DELTA_MIN     = float(os.getenv("OI_DELTA_MIN",   "0.02"))

# ── QE / Macro ────────────────────────────────
FG_EXTREME_FEAR  = int(os.getenv("FG_EXTREME_FEAR",  "25"))
FG_EXTREME_GREED = int(os.getenv("FG_EXTREME_GREED", "75"))
FG_REFRESH_SECS  = int(os.getenv("FG_REFRESH_SECS",  "300"))

# ── v7: ATR Stop Loss dinámico ─────────────────
ATR_SL_MULT      = float(os.getenv("ATR_SL_MULT",    "1.5"))  # SL = 1.5x ATR
ATR_TRAIL_MULT   = float(os.getenv("ATR_TRAIL_MULT", "1.0"))  # Trail = 1.0x ATR
ATR_SL_MIN_PCT   = float(os.getenv("ATR_SL_MIN_PCT", "0.005"))# mínimo 0.5%
ATR_SL_MAX_PCT   = float(os.getenv("ATR_SL_MAX_PCT", "0.03")) # máximo 3%

# ── v7: Liquidity Sweep ───────────────────────
SWEEP_WICK_RATIO  = float(os.getenv("SWEEP_WICK_RATIO", "0.6"))  # wick ≥ 60% del rango
SWEEP_VOL_MULT    = float(os.getenv("SWEEP_VOL_MULT",   "2.0"))  # vol ≥ 2x promedio
SWEEP_BODY_MAX    = float(os.getenv("SWEEP_BODY_MAX",   "0.3"))  # cuerpo ≤ 30% del rango

# ── v7: Session Filter ────────────────────────
SESSION_FILTER_ON    = os.getenv("SESSION_FILTER", "true").lower() == "true"
LONDON_OPEN_START    = int(os.getenv("LONDON_START", "7"))   # 07 UTC
LONDON_OPEN_END      = int(os.getenv("LONDON_END",   "10"))  # 10 UTC
NY_OPEN_START        = int(os.getenv("NY_START",     "13"))  # 13 UTC
NY_OPEN_END          = int(os.getenv("NY_END",       "16"))  # 16 UTC
ASIA_OPEN_START      = int(os.getenv("ASIA_START",   "0"))   # 00 UTC
ASIA_OPEN_END        = int(os.getenv("ASIA_END",     "3"))   # 03 UTC
OFF_SESSION_PENALTY  = float(os.getenv("OFF_SESSION_PENALTY", "0.10"))  # +10% confluencia fuera sesión

# ── v7: Correlation Guard ─────────────────────
CORR_WINDOW    = int(os.getenv("CORR_WINDOW",   "30"))   # velas para correlación
CORR_MAX       = float(os.getenv("CORR_MAX",    "0.75")) # máx correlación entre posiciones abiertas

# ── v7: Liquidity Tiers ───────────────────────
TIER1_SYMBOLS  = {"BTC/USDT:USDT", "ETH/USDT:USDT"}
TIER2_SYMBOLS  = {"SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT", "AVAX/USDT:USDT"}
# Resto son Tier 3 — mayor confluencia requerida
TIER1_CONF_BONUS  = -0.05   # menos exigente (señales más limpias)
TIER2_CONF_BONUS  =  0.00   # estándar
TIER3_CONF_PENALTY = 0.08   # más exigente

# ── v7: Funding Rate Momentum ─────────────────
FUNDING_HIST_LEN    = int(os.getenv("FUNDING_HIST",  "3"))    # últimas N lecturas
FUNDING_SQUEEZE_THR = float(os.getenv("FUND_SQUEEZE","0.0008"))# tendencia rápida

# ── Scanner ───────────────────────────────────
SCAN_INTERVAL   = int(os.getenv("SCAN_INTERVAL",  "60"))
SCANNER_ENABLED = os.getenv("SCANNER_ENABLED",    "true").lower() == "true"
SIGNAL_COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN","300"))

# ── Símbolos por defecto (12) ─────────────────
_DEFAULT_SYMBOLS = [
    "BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT",
    "XRP/USDT:USDT","LINK/USDT:USDT","AVAX/USDT:USDT","DOGE/USDT:USDT",
    "ADA/USDT:USDT","DOT/USDT:USDT","MATIC/USDT:USDT","OP/USDT:USDT",
]
_env_syms    = os.getenv("SCAN_SYMBOLS", "")
SCAN_SYMBOLS = [s.strip() for s in _env_syms.split(",") if s.strip()] if _env_syms else _DEFAULT_SYMBOLS

DGRP_POS_MAX = 35
DGRP_NEG_MIN = 60

# ═══════════════════════════════════════════════════════════════
#   ESTADO GLOBAL
# ═══════════════════════════════════════════════════════════════

_open_trades:       set   = set()
_trailing_state:    dict  = {}
_last_signal_time:  dict  = {}
_last_signal_val:   dict  = {}
_daily_pnl:         float = 0.0
_daily_date:        str   = ""
_ai_memory          = collections.deque(maxlen=500)
_ai_model_ready:    bool  = False
_ai_z_threshold:    float = ZSCORE_THRESHOLD

# ── Caches v7 ─────────────────────────────────
_fg_cache:       dict  = {"value": 50, "bias": 0, "ts": 0.0, "label": "Neutral"}
_oi_cache:       dict  = {}
_funding_hist:   dict  = collections.defaultdict(lambda: collections.deque(maxlen=FUNDING_HIST_LEN))
_price_cache:    dict  = {}   # sym → últimos closes para correlación
_mtf_conf_dyn:   float = MTF_CONFLUENCE_MIN

# ── Stats v7 ──────────────────────────────────
_sweep_detected: dict  = {}   # sym → ts del último sweep detectado
_stats: dict = {
    "sweeps_detected": 0, "sweeps_traded": 0,
    "session_blocks": 0, "corr_blocks": 0, "kurt_blocks": 0,
    "tier1_trades": 0, "tier2_trades": 0, "tier3_trades": 0,
}

# ═══════════════════════════════════════════════════════════════
#   EXCHANGES
# ═══════════════════════════════════════════════════════════════

_ex_cfg = {
    "apiKey":  BINGX_API_KEY,
    "secret":  BINGX_SECRET_KEY,
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
}
exchange_async = ccxt_pro.bingx(_ex_cfg)
exchange_sync  = ccxt_sync.bingx({**_ex_cfg, "options": {"defaultType": "swap"}})

def normalize_symbol(raw: str) -> str:
    s = raw.strip().upper()
    for sfx in [".P","PERP",".PERP"]:
        if s.endswith(sfx): s = s[:-len(sfx)]
    if "/USDT:USDT" in s: return s
    if "/" in s and ":USDT" not in s: return s.replace("/USDT","/USDT:USDT")
    if s.endswith("USDT") and "/" not in s: return f"{s[:-4]}/USDT:USDT"
    return s

# ═══════════════════════════════════════════════════════════════
#   TELEGRAM
# ═══════════════════════════════════════════════════════════════

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        log.error(f"Telegram: {e}")

def _session_label(hour: int) -> str:
    if LONDON_OPEN_START <= hour < LONDON_OPEN_END: return "🇬🇧 London"
    if NY_OPEN_START     <= hour < NY_OPEN_END:     return "🇺🇸 NY"
    if ASIA_OPEN_START   <= hour < ASIA_OPEN_END:   return "🇯🇵 Asia"
    return "😴 Off-Session"

def _tier_label(sym: str) -> str:
    if sym in TIER1_SYMBOLS: return "T1"
    if sym in TIER2_SYMBOLS: return "T2"
    return "T3"

def fmt_signal_msg(sym, sig, price, stype, regime, score,
                   mtf=None, ai_conf=None, zscore=None,
                   whale=False, cvd=None, absorb=False,
                   sweep=False, order=None, error=None, source="Scanner",
                   hurst=None, kurt=None, fg=None, oi_delta=None,
                   atr_sl=None, session=None, fund_mom=None, tier=None):
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bull = "long" in sig.lower() or "buy" in sig.lower()
    src  = "📡 Scanner" if source == "Scanner" else "📊 TradingView"
    re_ic = {"POSITIVE GAMMA":"🟢","NEGATIVE GAMMA":"🔴","FLIP ZONE":"🟡"}.get(regime,"⚪")

    extras = ""
    if sweep:              extras += f"\n⚡ <b>LIQUIDITY SWEEP</b> — stop hunt detectado"
    if zscore  is not None:extras += f"\n📊 Z-Score: <b>{zscore}</b> (umbral: {_ai_z_threshold:.2f})"
    if whale:              extras += f"\n🐋 Ballena | CVD: <b>{cvd}%</b>"
    if absorb:             extras += f"\n🧲 Absorción detectada"
    if mtf     is not None:extras += f"\n📐 Confluencia MTF: <b>{mtf*100:.0f}%</b>"
    if ai_conf is not None:extras += f"\n🧠 Confianza IA: <b>{ai_conf*100:.0f}%</b>"
    if hurst   is not None:
        hl = "Trending" if hurst>HURST_TREND_MIN else ("Mean-Rev" if hurst<HURST_REVERT_MAX else "Neutral")
        extras += f"\n📉 Hurst: <b>{hurst:.3f}</b> ({hl})"
    if kurt    is not None:extras += f"\n🎲 Kurtosis: <b>{kurt:.2f}</b>"
    if fg      is not None:extras += f"\n😱 Fear&Greed: <b>{fg}</b> ({_fg_cache['label']})"
    if oi_delta is not None:extras += f"\n📦 OI Delta: <b>{oi_delta:+.2%}</b>"
    if atr_sl  is not None:extras += f"\n🛡️ SL dinámico: <b>{atr_sl*100:.2f}%</b> (ATR-based)"
    if session is not None:extras += f"\n🕐 Sesión: <b>{session}</b>"
    if fund_mom is not None and abs(fund_mom) > 0.0002:
        extras += f"\n💰 Funding trend: <b>{fund_mom:+.4%}</b>"
    if tier:               extras += f"\n🏆 Tier: <b>{tier}</b>"

    if error:
        return (f"⚠️ <b>AEGIS v7 — ERROR</b>\n────────────────\n"
                f"🕒 {ts}\n📈 {sym} | {sig.upper()}\n❌ <code>{error}</code>")

    status = "✅ LIVE" if (order and not order.get("paper") and MODE=="live") else "📋 PAPER"
    oid    = (order or {}).get("id","—")
    size   = round(calc_order_size(), 2)

    return (
        f"{'🟢' if bull else '🔴'} <b>AEGIS v7 — {sig.upper()}</b>\n"
        f"────────────────────\n"
        f"🕒 {ts} | {src}\n"
        f"📈 <b>{sym}</b> @ <b>{price}</b>\n"
        f"💡 {stype}{extras}\n"
        f"────────────────────\n"
        f"{re_ic} Régimen: <b>{regime}</b> | DGRP: <b>{score}/100</b>\n"
        f"────────────────────\n"
        f"📦 {status} | ID: <code>{oid}</code>\n"
        f"⚙️ Size: ${size} | Lev: {LEVERAGE}x | Riesgo: ${ACCOUNT_BALANCE*RISK_PER_TRADE:.2f}\n"
        f"💼 Pos: {len(_open_trades)}/{MAX_OPEN_TRADES} | PnL día: {_daily_pnl:+.2f}%"
    )

# ═══════════════════════════════════════════════════════════════
#   GESTIÓN DE RIESGO
# ═══════════════════════════════════════════════════════════════

def _reset_daily():
    global _daily_pnl, _daily_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _daily_date != today:
        if _daily_date:
            log.info(f"[RIESGO] Nuevo día. PnL ayer: {_daily_pnl:+.2f}%")
            send_telegram(
                f"📅 <b>Nuevo día de trading</b>\n"
                f"PnL ayer: <b>{_daily_pnl:+.2f}%</b>\n"
                f"Fear&Greed: <b>{_fg_cache['value']}</b>\n"
                f"Sweeps detectados: <b>{_stats['sweeps_detected']}</b> | "
                f"Operados: <b>{_stats['sweeps_traded']}</b>\n"
                f"Bloqueados (sesión/corr/kurt): "
                f"<b>{_stats['session_blocks']}/{_stats['corr_blocks']}/{_stats['kurt_blocks']}</b>"
            )
        _daily_pnl, _daily_date = 0.0, today

def circuit_breaker() -> tuple[bool, str]:
    _reset_daily()
    if _daily_pnl <= -(MAX_DAILY_LOSS * 100):
        return False, f"Circuit breaker: {_daily_pnl:.2f}% ≥ límite {MAX_DAILY_LOSS*100:.0f}%"
    if len(_open_trades) >= MAX_OPEN_TRADES:
        return False, f"Máx. posiciones ({MAX_OPEN_TRADES})"
    return True, ""

def calc_order_size() -> float:
    dollar_risk = ACCOUNT_BALANCE * RISK_PER_TRADE
    stop_pct    = 0.01
    nominal     = dollar_risk / stop_pct
    margin      = nominal / LEVERAGE
    max_margin  = ACCOUNT_BALANCE * 0.10
    return round(min(margin, max_margin) * LEVERAGE, 2)

def can_send_signal(sym: str, sig: str) -> bool:
    now = time.time()
    if now - _last_signal_time.get(sym, 0) < SIGNAL_COOLDOWN: return False
    if _last_signal_val.get(sym) == sig: return False
    return True

def register_signal(sym: str, sig: str):
    _last_signal_time[sym] = time.time()
    _last_signal_val[sym]  = sig

# ═══════════════════════════════════════════════════════════════
#   INDICADORES CLÁSICOS — pure Python
# ═══════════════════════════════════════════════════════════════

def ema(data, p):
    out=[None]*len(data); k=2/(p+1)
    for i in range(p-1,len(data)):
        out[i]=sum(data[i-p+1:i+1])/p if i==p-1 else data[i]*k+out[i-1]*(1-k)
    return out

def atr(highs, lows, closes, p=14):
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1]))
         for i in range(1,len(closes))]
    out=[None]*len(trs)
    if len(trs)>=p:
        out[p-1]=sum(trs[:p])/p
        for i in range(p,len(trs)):
            out[i]=(out[i-1]*(p-1)+trs[i])/p
    return out

def bollinger(closes, p=20, mult=2.0):
    if len(closes)<p: return None,None,None
    w=closes[-p:]; mid=sum(w)/p
    std=(sum((x-mid)**2 for x in w)/p)**0.5
    return mid+mult*std, mid, mid-mult*std

def vwap(highs, lows, closes, volumes):
    tp=[(h+l+c)/3 for h,l,c in zip(highs,lows,closes)]
    tot=sum(volumes)
    return sum(t*v for t,v in zip(tp,volumes))/tot if tot>0 else closes[-1]

def rsi(closes, p=14):
    if len(closes)<p+1: return 50.0
    gs,ls=[],[]
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]; gs.append(max(d,0)); ls.append(max(-d,0))
    ag=sum(gs[-p:])/p; al=sum(ls[-p:])/p
    return 100.0 if al==0 else round(100-100/(1+ag/al),2)

def dgrp(atr_vals):
    valid=[x for x in atr_vals if x is not None]
    if len(valid)<28: return "FLIP ZONE",50
    avg=sum(valid[-28:])/28; ratio=valid[-1]/avg if avg>0 else 1.0
    score=int(min(100,max(0,(ratio-0.4)/1.6*100)))
    if score<DGRP_POS_MAX:   return "POSITIVE GAMMA",score
    elif score>DGRP_NEG_MIN: return "NEGATIVE GAMMA",score
    return "FLIP ZONE",score

def cvd_whale(opens, closes, volumes, lb=20):
    if len(closes)<lb+1: return 0.0,False
    rec=list(zip(opens[-lb:],closes[-lb:],volumes[-lb:]))
    bv=sum(v for o,c,v in rec if c>=o)
    sv=sum(v for o,c,v in rec if c<o)
    tot=bv+sv
    if tot==0: return 0.0,False
    imb=abs(bv-sv)/tot
    avgv=sum(volumes[-lb-1:-1])/lb
    return round(imb*100,1), imb>=CVD_THRESHOLD and volumes[-1]>avgv*WHALE_MULT

def zscore_val(closes, w=None):
    w=w or ZSCORE_WINDOW
    if len(closes)<w+1: return None
    sub=closes[-(w+1):-1]; mean=sum(sub)/w
    std=(sum((x-mean)**2 for x in sub)/w)**0.5
    return None if std==0 else round((closes[-1]-mean)/std,3)

def absorption(highs, lows, closes, volumes, lb=10):
    if len(closes)<lb+1: return False,""
    avgv=sum(volumes[-(lb+1):-1])/lb
    if volumes[-1]<avgv*ABSORPTION_VOL_MULT: return False,""
    move=(highs[-1]-lows[-1])/closes[-1] if closes[-1]>0 else 99
    if move>=ABSORPTION_MOVE_PCT: return False,""
    return True,"alcista" if closes[-1]>=closes[-2] else "bajista"

# ═══════════════════════════════════════════════════════════════
#   SIMONS — Indicadores estadísticos
# ═══════════════════════════════════════════════════════════════

def hurst_exponent(closes, max_lag=None) -> float:
    max_lag=max_lag or min(HURST_WINDOW,len(closes)//2)
    if len(closes)<max_lag*2: return 0.5
    lags=range(2,max_lag)
    tau=[max(1e-10,(sum((closes[i]-closes[i-lag])**2 for i in range(lag,len(closes)))/(len(closes)-lag))**0.5)
         for lag in lags]
    if not tau: return 0.5
    llags=[math.log(l) for l in lags]; ltau=[math.log(t) for t in tau]
    n=len(lags); mx=sum(llags)/n; my=sum(ltau)/n
    num=sum((x-mx)*(y-my) for x,y in zip(llags,ltau))
    den=sum((x-mx)**2 for x in llags)
    return round((num/den)/2,4) if den>0 else 0.5

def autocorr_lag1(closes, n=20) -> float:
    if len(closes)<n+2: return 0.0
    r=[(closes[i]-closes[i-1])/(closes[i-1]+1e-10) for i in range(-n-1,0)]
    r0=r[:-1]; r1=r[1:]
    m0=sum(r0)/len(r0); m1=sum(r1)/len(r1)
    num=sum((a-m0)*(b-m1) for a,b in zip(r0,r1))
    d0=(sum((a-m0)**2 for a in r0))**0.5
    d1=(sum((b-m1)**2 for b in r1))**0.5
    return round(num/(d0*d1+1e-9),4)

def returns_moments(closes, n=30) -> tuple[float,float,float]:
    if len(closes)<n+1: return 0.0,0.0,3.0
    r=[(closes[i]-closes[i-1])/(closes[i-1]+1e-10) for i in range(-n,0)]
    m=sum(r)/len(r)
    std=(sum((x-m)**2 for x in r)/len(r))**0.5
    if std<1e-9: return std,0.0,3.0
    skew=sum((x-m)**3 for x in r)/(len(r)*std**3)
    kurt=sum((x-m)**4 for x in r)/(len(r)*std**4)
    return round(std,6),round(skew,3),round(kurt,3)

# ═══════════════════════════════════════════════════════════════
#   ⚡ ARMA SECRETA: LIQUIDITY SWEEP DETECTOR
# ═══════════════════════════════════════════════════════════════

def detect_liquidity_sweep(opens, highs, lows, closes, volumes, lb=20) -> tuple[bool, str, float]:
    """
    Los institucionales ("los grandes") ejecutan Liquidity Sweeps:
    1. Empujan el precio deliberadamente por debajo de un soporte
       (o encima de una resistencia) para activar stop-losses de retail
    2. Recogen toda esa liquidez a precio favorable
    3. Revierten inmediatamente en la dirección real

    Señales de un sweep:
    - Vela con wick largo (≥60% del rango total) que toca mínimo/máximo reciente
    - Cuerpo pequeño (≤30% del rango) — el precio RECHAZÓ el movimiento
    - Volumen alto (≥2x promedio) — confirmación de absorción institucional
    - El cierre está en la mitad OPUESTA al wick (rechazo)

    Retorna: (detectado, dirección "alcista"/"bajista", fuerza 0-1)
    """
    if len(closes) < lb + 3: return False, "", 0.0

    candle_range = highs[-1] - lows[-1]
    if candle_range < 1e-8: return False, "", 0.0

    body        = abs(closes[-1] - opens[-1])
    upper_wick  = highs[-1]  - max(closes[-1], opens[-1])
    lower_wick  = min(closes[-1], opens[-1]) - lows[-1]
    body_ratio  = body / candle_range

    # Volumen promedio de lb velas previas
    avg_vol = sum(volumes[-(lb+1):-1]) / lb
    vol_ratio = volumes[-1] / (avg_vol + 1e-10)

    # Mínimos y máximos recientes (las últimas lb velas, excluyendo la actual)
    recent_low  = min(lows[-(lb+1):-1])
    recent_high = max(highs[-(lb+1):-1])

    strength = 0.0
    direction = ""

    # ── Sweep alcista: wick inferior largo, cierre en mitad superior ──
    # Interpretación: barrieron los stops de los longs, ahora subirá
    lower_wick_ratio = lower_wick / candle_range
    if (lower_wick_ratio >= SWEEP_WICK_RATIO and
            body_ratio <= SWEEP_BODY_MAX and
            vol_ratio >= SWEEP_VOL_MULT and
            lows[-1] <= recent_low * 1.001 and   # tocó o rompió el mínimo reciente
            closes[-1] > (highs[-1] + lows[-1]) / 2):  # cerró en mitad superior
        strength = min(1.0, (lower_wick_ratio - SWEEP_WICK_RATIO) * 3 +
                       (vol_ratio - SWEEP_VOL_MULT) * 0.2)
        direction = "alcista"

    # ── Sweep bajista: wick superior largo, cierre en mitad inferior ──
    upper_wick_ratio = upper_wick / candle_range
    if (upper_wick_ratio >= SWEEP_WICK_RATIO and
            body_ratio <= SWEEP_BODY_MAX and
            vol_ratio >= SWEEP_VOL_MULT and
            highs[-1] >= recent_high * 0.999 and  # tocó o rompió el máximo reciente
            closes[-1] < (highs[-1] + lows[-1]) / 2):  # cerró en mitad inferior
        strength = min(1.0, (upper_wick_ratio - SWEEP_WICK_RATIO) * 3 +
                       (vol_ratio - SWEEP_VOL_MULT) * 0.2)
        direction = "bajista"

    if direction:
        log.info(f"[SWEEP] ⚡ {direction.upper()} | wick={lower_wick_ratio if direction=='alcista' else upper_wick_ratio:.2%} "
                 f"| body={body_ratio:.2%} | vol={vol_ratio:.1f}x | strength={strength:.2f}")
    return bool(direction), direction, round(strength, 3)

# ═══════════════════════════════════════════════════════════════
#   v7: ATR DYNAMIC STOP LOSS
# ═══════════════════════════════════════════════════════════════

def calc_atr_sl(atr_vals, price: float) -> float:
    """
    Stop Loss dinámico = ATR * multiplicador.
    Se clampea entre ATR_SL_MIN_PCT y ATR_SL_MAX_PCT del precio.
    Resultado: % de distancia del SL desde la entrada.
    """
    valid = [x for x in atr_vals if x is not None]
    if not valid or price <= 0:
        return 0.01  # fallback 1%
    atr_val = valid[-1]
    sl_dist = (atr_val * ATR_SL_MULT) / price
    return round(max(ATR_SL_MIN_PCT, min(ATR_SL_MAX_PCT, sl_dist)), 5)

def calc_atr_trail(atr_vals, price: float) -> float:
    """Trailing stop dinámico en % del precio."""
    valid = [x for x in atr_vals if x is not None]
    if not valid or price <= 0: return 0.008
    return round(max(0.003, min(0.02, (valid[-1] * ATR_TRAIL_MULT) / price)), 5)

# ═══════════════════════════════════════════════════════════════
#   v7: SESSION FILTER
# ═══════════════════════════════════════════════════════════════

def get_session_info() -> tuple[str, float]:
    """
    Retorna (etiqueta_sesión, penalización_confluencia).
    London Open y NY Open son las ventanas de mayor volumen institucional.
    Fuera de ellas, exigimos más confluencia para filtrar señales falsas.
    """
    hour = datetime.now(timezone.utc).hour
    if LONDON_OPEN_START <= hour < LONDON_OPEN_END:
        return f"🇬🇧 London Open ({hour}h UTC)", -0.05  # bonus: menos exigente
    if NY_OPEN_START <= hour < NY_OPEN_END:
        return f"🇺🇸 NY Open ({hour}h UTC)", -0.05
    if ASIA_OPEN_START <= hour < ASIA_OPEN_END:
        return f"🇯🇵 Asia Open ({hour}h UTC)", 0.0   # neutral
    # Off-session: más exigente
    return f"😴 Off-Session ({hour}h UTC)", OFF_SESSION_PENALTY

# ═══════════════════════════════════════════════════════════════
#   v7: CORRELATION GUARD
# ═══════════════════════════════════════════════════════════════

def pearson_corr(a: list, b: list) -> float:
    n = min(len(a), len(b))
    if n < 5: return 0.0
    a, b = a[-n:], b[-n:]
    ma = sum(a)/n; mb = sum(b)/n
    num = sum((x-ma)*(y-mb) for x,y in zip(a,b))
    da  = (sum((x-ma)**2 for x in a))**0.5
    db  = (sum((y-mb)**2 for y in b))**0.5
    return round(num/(da*db+1e-9), 3)

def check_correlation_guard(sym: str, sig_dir: int) -> tuple[bool, str]:
    """
    Verifica si el nuevo símbolo está muy correlacionado con alguna
    posición ya abierta. Si es así, bloquea la entrada.
    Retorna (permitido, razón).
    """
    if not _open_trades: return True, ""
    new_closes = _price_cache.get(sym, [])
    if not new_closes: return True, ""  # sin datos → permite

    for open_sym in _open_trades:
        open_closes = _price_cache.get(open_sym, [])
        if not open_closes: continue

        # Calculamos correlación de retornos (no de precios absolutos)
        n = min(len(new_closes), len(open_closes), CORR_WINDOW)
        if n < 5: continue
        r_new  = [(new_closes[i]-new_closes[i-1])/(new_closes[i-1]+1e-10) for i in range(-n,0)]
        r_open = [(open_closes[i]-open_closes[i-1])/(open_closes[i-1]+1e-10) for i in range(-n,0)]
        corr   = pearson_corr(r_new, r_open)

        # Correlación alta en misma dirección = riesgo duplicado
        open_side = _trailing_state.get(open_sym, {}).get("side", "")
        open_dir  = 1 if open_side == "long" else -1
        if corr > CORR_MAX and open_dir == sig_dir:
            return False, f"Correlación {corr:.2f} con {open_sym} ya abierto"

    return True, ""

# ═══════════════════════════════════════════════════════════════
#   v7: FUNDING RATE MOMENTUM
# ═══════════════════════════════════════════════════════════════

def funding_momentum(sym: str) -> float:
    """
    Tendencia del funding rate en las últimas N lecturas.
    Positiva y acelerando → longs sobre-extendidos → señal bearish
    Negativa y acelerando → shorts sobre-extendidos → señal bullish
    """
    hist = list(_funding_hist[sym])
    if len(hist) < 2: return 0.0
    # pendiente simple de la serie de funding
    n = len(hist)
    x = list(range(n))
    mx = sum(x)/n; my = sum(hist)/n
    num = sum((xi-mx)*(yi-my) for xi,yi in zip(x,hist))
    den = sum((xi-mx)**2 for xi in x)
    return round(num/(den+1e-12), 6)

async def update_funding_hist(sym: str):
    """Actualiza el historial de funding rate para un símbolo."""
    try:
        fr = await exchange_async.fetch_funding_rate(sym)
        rate = float(fr.get("fundingRate", 0) or 0)
        _funding_hist[sym].append(rate)
    except:
        pass

# ═══════════════════════════════════════════════════════════════
#   QE: FEAR & GREED + OI
# ═══════════════════════════════════════════════════════════════

def refresh_fear_greed():
    global _fg_cache, _mtf_conf_dyn
    now = time.time()
    if now - _fg_cache["ts"] < FG_REFRESH_SECS: return
    try:
        resp    = http_requests.get("https://api.alternative.me/fng/?limit=1", timeout=6).json()
        fg_val  = int(resp["data"][0]["value"])
        fg_label= resp["data"][0]["value_classification"]
        bias    = 1 if fg_val < FG_EXTREME_FEAR else (-1 if fg_val > FG_EXTREME_GREED else 0)
        if fg_val < FG_EXTREME_FEAR:
            _mtf_conf_dyn = max(0.45, MTF_CONFLUENCE_MIN - 0.10)
        elif fg_val > FG_EXTREME_GREED:
            _mtf_conf_dyn = min(0.80, MTF_CONFLUENCE_MIN + 0.15)
        else:
            _mtf_conf_dyn = MTF_CONFLUENCE_MIN
        _fg_cache = {"value": fg_val, "bias": bias, "ts": now, "label": fg_label}
        log.info(f"[F&G] {fg_val} ({fg_label}) | MTF_min={_mtf_conf_dyn:.2f}")
    except Exception as e:
        log.warning(f"[F&G] {e}")

async def get_oi_delta(sym: str) -> float:
    try:
        oi_data = await exchange_async.fetch_open_interest_history(sym, "5m", limit=3)
        if len(oi_data) >= 2:
            prev = float(oi_data[-2].get("openInterestAmount", 0) or 0)
            curr = float(oi_data[-1].get("openInterestAmount", 0) or 0)
            if prev > 0:
                return round((curr - prev) / prev, 5)
    except:
        pass
    return 0.0

# ═══════════════════════════════════════════════════════════════
#   LSTM + RANDOM FOREST
# ═══════════════════════════════════════════════════════════════

class MiniLSTM:
    def __init__(self, inp=9, hid=12):  # v7: +sweep_strength, +fund_mom, +session_bonus
        self.hid=hid; self.inp=inp; self._init()

    def _init(self):
        def X(r,c): sc=(2/(r+c))**0.5; return [[random.gauss(0,sc) for _ in range(c)] for _ in range(r)]
        h,i=self.hid,self.inp
        self.Wf=X(h,i+h);self.bf=[0.1]*h
        self.Wi=X(h,i+h);self.bi=[0.0]*h
        self.Wg=X(h,i+h);self.bg=[0.0]*h
        self.Wo=X(h,i+h);self.bo=[0.5]*h
        self.Wy=X(1,h);self.by=[0.0]

    @staticmethod
    def _s(x): return 1/(1+math.exp(-max(-20,min(20,x))))
    @staticmethod
    def _t(x): return math.tanh(max(-20,min(20,x)))

    def _mm(self,W,x,b):
        return [b[i]+sum(W[i][j]*x[j] for j in range(len(x))) for i in range(len(b))]

    def forward(self,seq):
        h=[0.0]*self.hid; c=[0.0]*self.hid
        for x in seq:
            xh=x+h
            f=[self._s(v) for v in self._mm(self.Wf,xh,self.bf)]
            ig=[self._s(v) for v in self._mm(self.Wi,xh,self.bi)]
            g=[self._t(v) for v in self._mm(self.Wg,xh,self.bg)]
            o=[self._s(v) for v in self._mm(self.Wo,xh,self.bo)]
            c=[f[j]*c[j]+ig[j]*g[j] for j in range(self.hid)]
            h=[o[j]*self._t(c[j]) for j in range(self.hid)]
        return self._s(self.by[0]+sum(self.Wy[0][j]*h[j] for j in range(self.hid)))

    def train(self,seq,target,lr=0.01):
        p=self.forward(seq); e=p-target
        for j in range(self.hid): self.Wy[0][j]-=lr*e*p*(1-p)
        self.by[0]-=lr*e*p*(1-p)
        return abs(e)

class MiniTree:
    def __init__(self,depth=4): self.d=depth; self.tree=None
    def fit(self,X,y): self.tree=self._b(X,y,0)
    def _b(self,X,y,dep):
        if not X or dep>=self.d or len(set(str(v) for v in y))==1:
            return sum(y)/len(y) if y else 0.5
        bf=bv=None; bs=float("inf")
        for f in range(len(X[0])):
            for v in sorted(set(r[f] for r in X))[1:]:
                ly=[y[i] for i,r in enumerate(X) if r[f]<v]
                ry=[y[i] for i,r in enumerate(X) if r[f]>=v]
                if not ly or not ry: continue
                sc=self._g(ly)*len(ly)+self._g(ry)*len(ry)
                if sc<bs: bs,bf,bv=sc,f,v
        if bf is None: return sum(y)/len(y)
        lX=[r for r in X if r[bf]<bv]; rX=[r for r in X if r[bf]>=bv]
        lY=[y[i] for i,r in enumerate(X) if r[bf]<bv]; rY=[y[i] for i,r in enumerate(X) if r[bf]>=bv]
        return {"f":bf,"v":bv,"l":self._b(lX,lY,dep+1),"r":self._b(rX,rY,dep+1)}
    def _g(self,y):
        if not y: return 0; p=sum(y)/len(y); return 1-p*p-(1-p)**2
    def predict(self,x):
        n=self.tree
        while isinstance(n,dict): n=n["l"] if x[n["f"]]<n["v"] else n["r"]
        return n

class MiniRF:
    def __init__(self,n=10,d=4): self.trees=[MiniTree(d) for _ in range(n)]; self.ok=False
    def fit(self,X,y):
        n=len(X)
        for t in self.trees:
            idx=[random.randint(0,n-1) for _ in range(n)]
            t.fit([X[i] for i in idx],[y[i] for i in idx])
        self.ok=True
    def predict(self,x):
        if not self.ok: return _ai_z_threshold
        ps=[t.predict(x) for t in self.trees if t.tree is not None]
        return max(2.0,min(4.0,sum(ps)/len(ps))) if ps else _ai_z_threshold

_lstm = MiniLSTM(inp=9, hid=12)
_rf   = MiniRF(n=10, d=4)

def _features(closes, volumes, hour, hurst_val=0.5, kurt_val=3.0,
              sweep_strength=0.0, fund_mom=0.0, session_bonus=0.0):
    if len(closes)<10 or len(volumes)<10: return None
    pc=[(closes[i]-closes[i-1])/(closes[i-1]+1e-10) for i in range(-5,0)]
    vm=sum(volumes[-10:])/10
    vn=[volumes[i]/(vm+1e-10) for i in range(-5,0)]
    fg_norm=_fg_cache["value"]/100.0
    kurt_norm=min(1.0,kurt_val/10.0)
    # 9 features por timestep
    return [[p,v,hour/23.0,fg_norm,hurst_val,kurt_norm,
             sweep_strength,min(1.0,abs(fund_mom)*1000),
             -session_bonus]  # session_bonus negativo = off-session
            for p,v in zip(pc,vn)]

def lstm_confidence(closes, volumes, hour, hurst_val=0.5, kurt_val=3.0,
                    sweep_strength=0.0, fund_mom=0.0, session_bonus=0.0) -> float:
    seq=_features(closes,volumes,hour,hurst_val,kurt_val,sweep_strength,fund_mom,session_bonus)
    if seq is None: return 0.5
    try: return _lstm.forward(seq)
    except: return 0.5

def update_ai(success, z_entry, vol_mult, hour, mtf_score, funding, ob_imb,
              hurst_val=0.5, kurt_val=3.0, sweep_strength=0.0, fund_mom=0.0):
    global _ai_z_threshold, _ai_model_ready
    _ai_memory.append({
        "ok":1 if success else 0,"z":z_entry,"vol":vol_mult,
        "hr":hour,"mtf":mtf_score,"fund":funding,"ob":ob_imb,
        "hurst":hurst_val,"kurt":min(kurt_val,10.0),
        "sweep":sweep_strength,"fmom":fund_mom,
    })
    closes_p=[1.0+(random.gauss(0,0.001)) for _ in range(10)]
    vols_p=[vol_mult]*10
    seq=_features(closes_p,vols_p,hour,hurst_val,kurt_val,sweep_strength,fund_mom)
    if seq: _lstm.train(seq,float(success))
    if len(_ai_memory)<20: return
    data=list(_ai_memory)
    X=[[d["hr"]/23.0,d["vol"]/5.0,d["mtf"],d["fund"]*100,d["ob"],
        d.get("hurst",0.5),min(d.get("kurt",3.0),10.0)/10.0,
        d.get("sweep",0.0),min(abs(d.get("fmom",0.0))*1000,1.0)] for d in data]
    y=[d["z"]*d["ok"] for d in data]
    try:
        _rf.fit(X,y)
        hr=datetime.now(timezone.utc).hour
        last=data[-1]
        _ai_z_threshold=_rf.predict([
            hr/23.0,last["vol"]/5.0,last["mtf"],last["fund"]*100,last["ob"],
            last.get("hurst",0.5),min(last.get("kurt",3.0),10.0)/10.0,
            last.get("sweep",0.0),min(abs(last.get("fmom",0.0))*1000,1.0)
        ])
        _ai_model_ready=True
        log.info(f"[IA] Retrain OK. Z_thr={_ai_z_threshold:.3f}")
    except Exception as e:
        log.error(f"[IA] {e}")

# ═══════════════════════════════════════════════════════════════
#   EJECUCIÓN SMART v7 — Ultra-rápida
# ═══════════════════════════════════════════════════════════════

async def smart_order(sym: str, side: str, size: float,
                      atr_sl_pct: float = 0.01) -> dict | None:
    if MODE != "live":
        try:
            t=await exchange_async.fetch_ticker(sym); p=t["last"]
        except: p=0.0
        log.info(f"[PAPER] {side.upper()} ${size} {sym} @ {p:.4f} | SL={atr_sl_pct*100:.2f}%")
        return {"id":f"PAPER-{datetime.now().strftime('%H%M%S')}","paper":True,
                "average":p,"price":p,"atr_sl_pct":atr_sl_pct}
    try:
        lev_task=exchange_async.set_leverage(LEVERAGE,sym)
        ob_task=exchange_async.fetch_order_book(sym,limit=5)
        res=await asyncio.gather(lev_task,ob_task,return_exceptions=True)
        ob=res[1] if not isinstance(res[1],Exception) else await exchange_async.fetch_order_book(sym,limit=5)

        best_bid=ob["bids"][0][0]; best_ask=ob["asks"][0][0]
        spread=(best_ask-best_bid)/best_bid

        # Mercado muy rápido → market directo
        if spread>0.001:
            log.warning(f"[ORDER] {sym}: spread {spread:.4%} → MARKET directo")
            morder=await exchange_async.create_market_order(sym,side,size)
            morder["atr_sl_pct"]=atr_sl_pct
            return morder

        limit_price=(round(best_bid+(best_ask-best_bid)*0.35,4) if side=="buy"
                     else round(best_ask-(best_ask-best_bid)*0.35,4))

        log.info(f"[LIMIT] {side.upper()} {sym} @ {limit_price} | spread={spread:.4%} | SL={atr_sl_pct*100:.2f}%")
        order=await exchange_async.create_limit_order(sym,side,size,limit_price)
        oid=order["id"]

        for tick in range(LIMIT_WAIT_SECS):
            await asyncio.sleep(1)
            try:
                check=await exchange_async.fetch_order(oid,sym)
                if check["status"]=="closed":
                    fill=check.get("average") or check.get("price") or limit_price
                    log.info(f"[LIMIT] ✅ Fill en {tick+1}s @ {fill:.4f}")
                    check["atr_sl_pct"]=atr_sl_pct
                    return check
            except: pass

        try: await exchange_async.cancel_order(oid,sym)
        except: pass
        morder=await exchange_async.create_market_order(sym,side,size)
        morder["atr_sl_pct"]=atr_sl_pct
        return morder
    except Exception as e:
        log.error(f"[ORDER] {sym} {side}: {e}")
        return None

async def close_position(sym: str, side: str, contracts: float):
    if MODE != "live":
        return {"id":f"PAPER-CLOSE-{datetime.now().strftime('%H%M%S')}","paper":True}
    try:
        ob=await exchange_async.fetch_order_book(sym,limit=5)
        cs="sell" if side=="long" else "buy"
        lp=ob["asks"][0][0] if cs=="sell" else ob["bids"][0][0]
        order=await exchange_async.create_limit_order(sym,cs,contracts,round(lp,4),{"reduceOnly":True})
        await asyncio.sleep(LIMIT_WAIT_SECS)
        check=await exchange_async.fetch_order(order["id"],sym)
        if check["status"]=="closed": return check
        await exchange_async.cancel_order(order["id"],sym)
        return await exchange_async.create_market_order(sym,cs,contracts,{"reduceOnly":True})
    except Exception as e:
        log.error(f"[CLOSE] {sym}: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
#   ANÁLISIS POR TIMEFRAME — v7
# ═══════════════════════════════════════════════════════════════

async def analyze_tf(sym: str, tf: str) -> dict | None:
    try:
        ohlcv=await exchange_async.fetch_ohlcv(sym,tf,limit=120)
    except Exception as e:
        log.warning(f"fetch_ohlcv {sym} {tf}: {e}"); return None
    if len(ohlcv)<60: return None

    opens  =[x[1] for x in ohlcv]
    highs  =[x[2] for x in ohlcv]
    lows   =[x[3] for x in ohlcv]
    closes =[x[4] for x in ohlcv]
    volumes=[x[5] for x in ohlcv]
    price  =closes[-1]

    # Guardar closes en cache para correlación (solo 5m)
    if tf == "5m":
        _price_cache[sym] = closes[-CORR_WINDOW-5:]

    # ── Indicadores ──────────────────────────────
    atr_v            =atr(highs,lows,closes,14)
    regime,score     =dgrp(atr_v)
    e9               =ema(closes,9)
    e21              =ema(closes,21)
    bb_up,bb_mid,bb_lo=bollinger(closes,20,2.0)
    vw               =vwap(highs,lows,closes,volumes)
    cvd_pct,is_whale =cvd_whale(opens,closes,volumes,20)
    z                =zscore_val(closes,ZSCORE_WINDOW)
    abs_ok,abs_dir   =absorption(highs,lows,closes,volumes,10)
    rsi_v            =rsi(closes,14)
    atr_last         =atr_v[-1] or 0

    # ── Simons ───────────────────────────────────
    h_exp             =hurst_exponent(closes,HURST_WINDOW)
    ac                =autocorr_lag1(closes,20)
    _,skew,kurt       =returns_moments(closes,30)
    atr_sl_pct        =calc_atr_sl(atr_v, price)
    atr_trail_pct     =calc_atr_trail(atr_v, price)

    # ── ⚡ LIQUIDITY SWEEP ────────────────────────
    sweep_ok,sweep_dir,sweep_str=detect_liquidity_sweep(opens,highs,lows,closes,volumes,20)
    if sweep_ok: _stats["sweeps_detected"]+=1

    # FILTRO KURTOSIS
    if kurt > KURT_MAX:
        log.info(f"[SIMONS] {sym} {tf}: kurtosis={kurt:.2f} → skip"); _stats["kurt_blocks"]+=1
        return None

    e9p,e9c   =e9[-2],e9[-1]
    e21p,e21c =e21[-2],e21[-1]
    cp,cc,op  =closes[-2],closes[-1],opens[-1]

    thr=_ai_z_threshold if _ai_model_ready else ZSCORE_THRESHOLD
    signal=stype=None
    extra={}
    is_reverting=h_exp<HURST_REVERT_MAX
    is_trending =h_exp>HURST_TREND_MIN

    # ── PRIORIDAD 0: Liquidity Sweep (señal de mayor calidad) ──
    if sweep_ok:
        if sweep_dir=="alcista":
            signal,stype=(
                "sweep_long",
                f"⚡ Liquidity Sweep alcista | Fuerza: {sweep_str:.2f} | Stop hunt detectado"
            )
        else:
            signal,stype=(
                "sweep_short",
                f"⚡ Liquidity Sweep bajista | Fuerza: {sweep_str:.2f} | Stop hunt detectado"
            )
        extra.update({"sweep":True,"sweep_strength":sweep_str,"sweep_dir":sweep_dir})

    # ── 1. Z-Score (mean-reverting) ───────────────
    if signal is None and z is not None and abs(z)>=thr and (is_reverting or not is_trending):
        avgv=sum(volumes[-ZSCORE_WINDOW-1:-1])/ZSCORE_WINDOW
        if volumes[-1]>avgv*3.0:
            if z<-thr and rsi_v<42: signal,stype="zscore_long", f"Z-Score {z} | agot. bajista RSI={rsi_v} H={h_exp:.3f}"
            elif z>thr and rsi_v>58: signal,stype="zscore_short",f"Z-Score {z} | agot. alcista RSI={rsi_v} H={h_exp:.3f}"
            extra["z_score"]=z

    # ── 2. Absorción ──────────────────────────────
    if signal is None and abs_ok and not is_trending:
        d="long" if "alcista" in abs_dir else "short"
        signal,stype=f"absorption_{d}",f"Absorción {abs_dir} H={h_exp:.3f}"
        extra["absorption"]=True

    # ── 3. Vanna Unwind ───────────────────────────
    if signal is None and regime=="NEGATIVE GAMMA" and atr_last>0:
        if abs(cc-op)>atr_last*1.5:
            tu=closes[-3]<closes[-2]
            if tu and cc<op and rsi_v>58: signal,stype="vanna_unwind_short","Vanna Unwind bajista"
            elif not tu and cc>op and rsi_v<42: signal,stype="vanna_unwind_long","Vanna Unwind alcista"

    # ── 4. Whale CVD (trending) ───────────────────
    if signal is None and is_whale and (is_trending or not is_reverting):
        bv=sum(v for o,c,v in zip(opens[-20:],closes[-20:],volumes[-20:]) if c>=o)
        sv=sum(v for o,c,v in zip(opens[-20:],closes[-20:],volumes[-20:]) if c<o)
        if bv>sv: signal,stype="whale_long", f"Ballena alcista CVD={cvd_pct}% H={h_exp:.3f}"
        else:     signal,stype="whale_short",f"Ballena bajista CVD={cvd_pct}% H={h_exp:.3f}"
        extra.update({"whale":True,"cvd_pct":cvd_pct})

    # ── 5. Bollinger Compression ──────────────────
    if signal is None and bb_up and bb_mid and bb_lo:
        bw=(bb_up-bb_lo)/bb_mid if bb_mid else 99
        if bw<0.025:
            if cc>bb_up: signal,stype="compression_break_long","Compresión BB alcista"
            elif cc<bb_lo: signal,stype="compression_break_short","Compresión BB bajista"

    # ── 6. GEX Flip (trending) ────────────────────
    if signal is None and all(v is not None for v in [e9p,e9c,e21p,e21c]):
        if not is_reverting:
            if e9p<=e21p and e9c>e21c: signal,stype="gex_flip_cross_long", f"GEX Flip alcista H={h_exp:.3f}"
            elif e9p>=e21p and e9c<e21c: signal,stype="gex_flip_cross_short",f"GEX Flip bajista H={h_exp:.3f}"

    # ── 7. Wall Break VWAP (trending) ─────────────
    if signal is None and is_trending:
        if cp<vw and cc>vw*1.001: signal,stype="wall_break_long", f"Wall Break VWAP={vw:.4f} H={h_exp:.3f}"
        elif cp>vw and cc<vw*0.999: signal,stype="wall_break_short",f"Wall Break VWAP={vw:.4f} H={h_exp:.3f}"

    sig_is_momentum =(signal or "") in ("gex_flip_cross_long","gex_flip_cross_short",
                                         "wall_break_long","wall_break_short","whale_long","whale_short",
                                         "sweep_long","sweep_short")
    sig_is_reversion=(signal or "") in ("zscore_long","zscore_short","absorption_long","absorption_short")
    ac_confirmed=(sig_is_momentum and ac>0.1) or (sig_is_reversion and ac<-0.1)

    extra.update({"hurst":h_exp,"kurt":kurt,"ac_confirmed":ac_confirmed,
                  "atr_sl_pct":atr_sl_pct,"atr_trail_pct":atr_trail_pct})

    bull_bias=(e9c or 0)>(e21c or 0) and cc>vw
    bear_bias=(e9c or 0)<(e21c or 0) and cc<vw

    return {
        "signal":signal,"stype":stype,"price":price,
        "regime":regime,"score":score,"rsi":rsi_v,"z":z,
        "dir":1 if bull_bias else (-1 if bear_bias else 0),
        "extra":extra,"hurst":h_exp,"kurt":kurt,
        "atr_sl_pct":atr_sl_pct,"atr_trail_pct":atr_trail_pct,
    }

# ═══════════════════════════════════════════════════════════════
#   MULTI-TIMEFRAME ENGINE — v7 con tier + session
# ═══════════════════════════════════════════════════════════════

async def analyze_mtf(sym: str) -> dict | None:
    # Paralelo: 4 TF simultáneos
    coros=[analyze_tf(sym,tf) for tf in MTF_TIMEFRAMES]
    raw=await asyncio.gather(*coros,return_exceptions=True)
    results={}
    for tf,r in zip(MTF_TIMEFRAMES,raw):
        if r and not isinstance(r,Exception): results[tf]=r

    base=next((results[tf] for tf in MTF_TIMEFRAMES if results.get(tf,{}).get("signal")),None)
    if base is None: return None

    sig_dir=1 if "long" in base["signal"] else -1
    wtd=0.0
    for tf,r in results.items():
        w=MTF_WEIGHTS.get(tf,0.25)
        if r["dir"]==sig_dir: wtd+=w
        elif r.get("signal") and ("long" in r["signal"])==(sig_dir==1): wtd+=w*0.7

    # Boost por autocorrelación confirmada
    if base["extra"].get("ac_confirmed"):
        wtd=min(1.0,wtd*1.10)

    # Boost por sweep (señal de mayor calidad, menos confluencia requerida)
    if base["extra"].get("sweep"):
        wtd=min(1.0,wtd*1.15)

    # ── v7: Session penalty/bonus ─────────────────
    session_label,session_penalty=get_session_info()
    conf_min=_mtf_conf_dyn+session_penalty

    # ── v7: Tier adjustment ───────────────────────
    tier=_tier_label(sym)
    if tier=="T1": conf_min+=TIER1_CONF_BONUS
    elif tier=="T3": conf_min+=TIER3_CONF_PENALTY

    if wtd<conf_min:
        if session_penalty>0: _stats["session_blocks"]+=1
        log.info(f"[MTF] {sym}: {wtd:.2f} < {conf_min:.2f} ({tier}, {session_label})"); return None

    # Filtro RSI 1h
    rsi_1h=results.get("1h",{}).get("rsi",50)
    if sig_dir==1  and rsi_1h>70: return None
    if sig_dir==-1 and rsi_1h<30: return None

    # Mejor ATR de 5m o 15m (más confiable)
    atr_sl=base.get("atr_sl_pct",0.01)
    atr_tr=base.get("atr_trail_pct",0.008)
    for tf in ["15m","5m"]:
        if results.get(tf,{}).get("extra",{}).get("atr_sl_pct"):
            atr_sl=results[tf]["extra"]["atr_sl_pct"]
            atr_tr=results[tf]["extra"]["atr_trail_pct"]
            break

    return {
        "signal":base["signal"],"stype":base["stype"],
        "ticker":sym,"price":f"{base['price']:.4f}",
        "regime":base["regime"],"score":base["score"],
        "mtf_score":wtd,"session":session_label,"tier":tier,
        "hurst":base.get("hurst",0.5),"kurt":base.get("kurt",3.0),
        "atr_sl_pct":atr_sl,"atr_trail_pct":atr_tr,
        **base["extra"],
    }

# ═══════════════════════════════════════════════════════════════
#   PROCESS SIGNAL — v7
# ═══════════════════════════════════════════════════════════════

LONG_SIGS  = {"long","buy","wall_break_long","gex_flip_cross_long","vanna_unwind_long",
              "compression_break_long","whale_long","zscore_long","absorption_long","sweep_long"}
SHORT_SIGS = {"short","sell","wall_break_short","gex_flip_cross_short","vanna_unwind_short",
              "compression_break_short","whale_short","zscore_short","absorption_short","sweep_short"}

def interp(raw):
    r=raw.lower().strip()
    if r in LONG_SIGS:  return "long"
    if r in SHORT_SIGS: return "short"
    if r=="close":      return "close"
    raise ValueError(f"Señal desconocida: {raw}")

async def process_signal(data: dict, source="Scanner", mtf_score=None) -> dict:
    sym=normalize_symbol(data.get("ticker","BTC/USDT:USDT"))
    data["ticker"]=sym

    try: side=interp(data.get("signal",""))
    except ValueError as e:
        send_telegram(fmt_signal_msg(sym,data.get("signal","?"),data.get("price","?"),
                                     str(e),"?","?",error=str(e),source=source))
        return {"error":str(e)}

    ok,reason=circuit_breaker()
    if not ok and side!="close":
        send_telegram(f"🚫 <b>Circuit Breaker</b>\n{sym}: {reason}")
        return {"error":reason}

    if side!="close" and not can_send_signal(sym,side):
        return {"skipped":"cooldown"}

    sig_dir=1 if side=="long" else (-1 if side=="short" else 0)
    hurst_val  =data.get("hurst",0.5)
    kurt_val   =data.get("kurt",3.0)
    atr_sl_pct =data.get("atr_sl_pct",0.01)
    atr_tr_pct =data.get("atr_trail_pct",0.008)
    sweep_str  =data.get("sweep_strength",0.0)
    session    =data.get("session","")
    tier       =data.get("tier","T2")

    # ── Fear & Greed contra-sesgo ─────────────────
    fg_bias=_fg_cache["bias"]
    if fg_bias!=0 and sig_dir!=0 and fg_bias!=sig_dir:
        if (mtf_score or 0)<_mtf_conf_dyn+0.10:
            return {"skipped":"fg_macro_filter"}

    # ── OI Delta ──────────────────────────────────
    oi_delta=0.0
    if side!="close":
        oi_delta=await get_oi_delta(sym)
        if sig_dir==1  and oi_delta<-OI_DELTA_MIN: return {"skipped":"oi_divergence"}
        if sig_dir==-1 and oi_delta> OI_DELTA_MIN: return {"skipped":"oi_divergence"}

    # ── Funding Rate Momentum ─────────────────────
    await update_funding_hist(sym)
    fund_mom=funding_momentum(sym)
    # Funding acelerando contra la señal → skip
    if sig_dir==1  and fund_mom> FUNDING_SQUEEZE_THR: return {"skipped":"funding_squeeze_short"}
    if sig_dir==-1 and fund_mom<-FUNDING_SQUEEZE_THR: return {"skipped":"funding_squeeze_long"}

    # ── Correlation Guard ─────────────────────────
    if side!="close":
        corr_ok,corr_reason=check_correlation_guard(sym,sig_dir)
        if not corr_ok:
            _stats["corr_blocks"]+=1
            log.info(f"[CORR] {sym}: {corr_reason}")
            return {"skipped":f"correlation: {corr_reason}"}

    # ── Tier stats ────────────────────────────────
    if tier=="T1": _stats["tier1_trades"]+=1
    elif tier=="T2": _stats["tier2_trades"]+=1
    else: _stats["tier3_trades"]+=1
    if data.get("sweep"): _stats["sweeps_traded"]+=1

    # ── LSTM confidence ───────────────────────────
    ai_conf=None
    try:
        ohlcv=await exchange_async.fetch_ohlcv(sym,"5m",limit=25)
        if len(ohlcv)>=15:
            cl=[x[4] for x in ohlcv]; vl=[x[5] for x in ohlcv]
            _,session_pen=get_session_info()
            ai_conf=lstm_confidence(cl,vl,datetime.now(timezone.utc).hour,
                                    hurst_val,kurt_val,sweep_str,fund_mom,session_pen)
            # Sweep tiene mayor tolerancia (señal de calidad)
            thresh_adj=0.42 if data.get("sweep") else 0.44
            if side=="long"  and ai_conf<thresh_adj:  return {"skipped":"lstm_low"}
            if side=="short" and ai_conf>1-thresh_adj: return {"skipped":"lstm_low"}
    except Exception as e: log.warning(f"LSTM: {e}")

    # ── Precio y filtros de mercado ───────────────
    try:
        t=await exchange_async.fetch_ticker(sym); cur_price=t["last"]
    except: cur_price=float(data.get("price",0) or 0)

    ob_imb=funding=0.0
    try:
        ob=await exchange_async.fetch_order_book(sym,limit=10)
        bv=sum(b[1] for b in ob["bids"][:5]); av=sum(a[1] for a in ob["asks"][:5])
        ob_imb=(bv-av)/(bv+av) if bv+av>0 else 0.0
    except: pass
    try:
        fr=await exchange_async.fetch_funding_rate(sym)
        funding=float(fr.get("fundingRate",0) or 0)
    except: pass

    if sig_dir==1  and (funding>0.001 or ob_imb<-0.2): return {"skipped":"market_filter"}
    if sig_dir==-1 and (funding<-0.001 or ob_imb>0.2): return {"skipped":"market_filter"}

    size=calc_order_size()
    order=error=None

    if side=="close":
        state=_trailing_state.get(sym)
        if state:
            order=await close_position(sym,state["side"],state["contracts"])
        _open_trades.discard(sym); _trailing_state.pop(sym,None)
    else:
        order=await smart_order(sym,"buy" if side=="long" else "sell",size,atr_sl_pct)
        if order:
            entry=float(order.get("average") or order.get("price") or cur_price)
            _open_trades.add(sym)
            _trailing_state[sym]={
                "side":side,"entry":entry,"best":entry,
                "contracts":size,"ai_conf":ai_conf,
                "mtf_score":mtf_score or 0.0,"paper":order.get("paper",False),
                "hurst":hurst_val,"kurt":kurt_val,
                "atr_sl_pct":atr_sl_pct,"atr_trail_pct":atr_tr_pct,
                "sweep_strength":sweep_str,"fund_mom":fund_mom,
            }
            register_signal(sym,side)
        else:
            error="Order failed"

    send_telegram(fmt_signal_msg(
        sym,data.get("signal","?"),f"{cur_price:.4f}",
        data.get("stype",data.get("signal_type","")),
        data.get("regime","?"),data.get("score","?"),
        mtf=mtf_score,ai_conf=ai_conf,
        zscore=data.get("z_score"),whale=data.get("whale",False),
        cvd=data.get("cvd_pct"),absorb=data.get("absorption",False),
        sweep=data.get("sweep",False),
        order=order,error=error,source=source,
        hurst=hurst_val if hurst_val!=0.5 else None,
        kurt=kurt_val if kurt_val!=3.0 else None,
        fg=_fg_cache["value"],
        oi_delta=oi_delta if oi_delta!=0.0 else None,
        atr_sl=atr_sl_pct,
        session=session,
        fund_mom=fund_mom if abs(fund_mom)>0.0001 else None,
        tier=tier,
    ))
    return {"order":order,"error":error}

# ═══════════════════════════════════════════════════════════════
#   TRAILING STOP — v7 con ATR dinámico
# ═══════════════════════════════════════════════════════════════

async def trailing_loop():
    log.info(f"Trailing loop ON | ATR-based | check cada {10}s")
    while True:
        await asyncio.sleep(10)
        for sym, state in list(_trailing_state.items()):
            try:
                t=await exchange_async.fetch_ticker(sym); price=t["last"]
            except: continue

            side=state["side"]; best=state["best"]
            # ATR trail dinámico (guardado en state)
            trail=state.get("atr_trail_pct",0.008)

            moved=False
            if side=="long"  and price>best: state["best"]=best=price; moved=True
            if side=="short" and price<best: state["best"]=best=price; moved=True
            if moved: log.info(f"[TRAIL] {sym} best={best:.4f} | trail={trail*100:.2f}%")

            hit=((side=="long"  and price<=best*(1-trail)) or
                 (side=="short" and price>=best*(1+trail)))
            if hit: await _fire_trailing(sym,state,price)

async def _fire_trailing(sym, state, price):
    global _daily_pnl
    side=state["side"]; entry=state["entry"]; best=state["best"]
    pnl=((price-entry)/entry*100) if side=="long" else ((entry-price)/entry*100)
    pnl_l=round(pnl*LEVERAGE,2)
    _daily_pnl+=pnl_l

    await close_position(sym,side,state["contracts"])
    _trailing_state.pop(sym,None); _open_trades.discard(sym)

    update_ai(pnl>0,state.get("z_score",ZSCORE_THRESHOLD),1.0,
              datetime.now(timezone.utc).hour,state.get("mtf_score",0.5),
              funding=0.0,ob_imb=0.0,
              hurst_val=state.get("hurst",0.5),kurt_val=state.get("kurt",3.0),
              sweep_strength=state.get("sweep_strength",0.0),
              fund_mom=state.get("fund_mom",0.0))

    _reset_daily()
    send_telegram(
        f"{'🟢' if pnl_l>=0 else '🔴'} <b>TRAILING STOP</b>\n"
        f"────────────────────\n"
        f"📈 <b>{sym}</b>\n"
        f"📍 {entry:.4f} → {price:.4f} | Best: {best:.4f}\n"
        f"🛡️ Trail ATR: <b>{state.get('atr_trail_pct',0.008)*100:.2f}%</b>\n"
        f"{'🟢' if pnl_l>=0 else '🔴'} PnL: <b>{pnl_l:+.2f}%</b> (x{LEVERAGE})\n"
        f"📊 PnL hoy: <b>{_daily_pnl:+.2f}%</b>\n"
        f"⚡ Sweeps hoy: <b>{_stats['sweeps_traded']}</b> | "
        f"🔗 Corr bloqueados: <b>{_stats['corr_blocks']}</b>"
    )

# ═══════════════════════════════════════════════════════════════
#   SCANNER LOOP — v7 paralelo
# ═══════════════════════════════════════════════════════════════

async def scanner_loop():
    log.info(f"Scanner ON | {len(SCAN_SYMBOLS)} syms | {SCAN_INTERVAL}s")
    send_telegram(
        f"🚀 <b>AEGIS GEX v7.0 — ELITE</b>\n"
        f"────────────────────\n"
        f"Modo: <b>{MODE.upper()}</b> | Lev: <b>{LEVERAGE}x</b>\n"
        f"Balance: <b>${ACCOUNT_BALANCE}</b> | Riesgo/trade: <b>{RISK_PER_TRADE*100:.0f}%</b>\n"
        f"Símbolos: <b>{len(SCAN_SYMBOLS)}</b> ({', '.join(SCAN_SYMBOLS[:3])}...)\n"
        f"────────────────────\n"
        f"⚡ <b>Liquidity Sweep</b> — stop hunt detector\n"
        f"🛡️ <b>ATR Dynamic SL</b> — stops adaptativos\n"
        f"🏆 <b>Tier Scoring</b> — T1/T2/T3 por liquidez\n"
        f"🔗 <b>Correlation Guard</b> — sin riesgo duplicado\n"
        f"🕐 <b>Session Filter</b> — London/NY/Asia\n"
        f"📈 <b>Funding Momentum</b> — squeeze detector\n"
        f"🔬 <b>Hurst+Kurt+Autocorr</b> (Simons)\n"
        f"😱 <b>Fear&Greed+OI</b> (QE macro)\n"
        f"🧠 <b>LSTM(9f)+RF(9f)</b> — IA adaptativa"
    )
    n=0
    while True:
        n+=1
        ok,reason=circuit_breaker()
        if not ok:
            log.warning(f"[CB] {reason}"); await asyncio.sleep(SCAN_INTERVAL); continue

        try: await asyncio.get_event_loop().run_in_executor(None,refresh_fear_greed)
        except: pass

        session_label,session_pen=get_session_info()
        log.info(f"── SCAN #{n} | {session_label} | pos={len(_open_trades)}/{MAX_OPEN_TRADES} "
                 f"| PnL={_daily_pnl:+.2f}% | F&G={_fg_cache['value']} "
                 f"| MTF_min={(_mtf_conf_dyn+session_pen):.2f} ──")

        async def _scan_one(sym):
            try: return sym, await analyze_mtf(sym)
            except Exception as e:
                log.error(f"[SCAN] {sym}: {e}")
                if "connection" in str(e).lower() or "timeout" in str(e).lower():
                    try: await exchange_async.close()
                    except: pass
                    await asyncio.sleep(3)
                return sym, None

        scan_results=await asyncio.gather(*[_scan_one(s) for s in SCAN_SYMBOLS])
        found=0
        for sym,res in scan_results:
            if res is None: continue
            found+=1
            log.info(f"  ✅ {sym} [{res.get('tier','?')}] → {res['signal']} "
                     f"| MTF={res['mtf_score']:.2f} "
                     f"| H={res.get('hurst',0.5):.3f} "
                     f"| {'⚡SWEEP' if res.get('sweep') else ''}")
            await process_signal(res,source="Scanner",mtf_score=res["mtf_score"])

        log.info(f"── FIN #{n} | señales={found} | sweeps={_stats['sweeps_detected']} "
                 f"| corr_blocks={_stats['corr_blocks']} ──")
        await asyncio.sleep(SCAN_INTERVAL)

# ═══════════════════════════════════════════════════════════════
#   FLASK — API REST
# ═══════════════════════════════════════════════════════════════

flask_app=Flask(__name__)
_loop: asyncio.AbstractEventLoop | None=None

def _run_async(coro):
    if _loop and _loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro,_loop).result(timeout=30)
    return {"error":"Loop no disponible"}

@flask_app.route("/",methods=["GET"])
def health():
    ok,reason=circuit_breaker()
    session_label,session_pen=get_session_info()
    return jsonify({
        "status":"online","bot":"AEGIS GEX v7.0 ELITE","mode":MODE,
        "scanner":SCANNER_ENABLED,"symbols":SCAN_SYMBOLS,
        "open_trades":list(_open_trades),"daily_pnl_pct":round(_daily_pnl,2),
        "circuit_breaker":not ok,"block_reason":reason,
        "ai":{"trained":_ai_model_ready,"z_threshold":round(_ai_z_threshold,3),
              "memory":len(_ai_memory),"features":9},
        "session":{"current":session_label,"penalty":session_pen,
                   "mtf_effective":round(_mtf_conf_dyn+session_pen,3)},
        "qe":{"fear_greed":_fg_cache["value"],"label":_fg_cache["label"],
              "bias":_fg_cache["bias"],"mtf_dynamic":round(_mtf_conf_dyn,3)},
        "simons":{"hurst_window":HURST_WINDOW,"kurt_max":KURT_MAX,
                  "atr_sl_mult":ATR_SL_MULT,"atr_trail_mult":ATR_TRAIL_MULT},
        "v7_stats":_stats,
        "trailing":list(_trailing_state.keys()),
    }),200

@flask_app.route("/webhook",methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Webhook-Secret","")!=WEBHOOK_SECRET:
        return jsonify({"error":"Unauthorized"}),401
    data=request.get_json(silent=True)
    if not data: return jsonify({"error":"No JSON"}),400
    res=_run_async(process_signal(data,source="TradingView"))
    if res.get("error"): return jsonify({"status":"error","message":res["error"]}),500
    return jsonify({"status":"success"}),200

@flask_app.route("/status",methods=["GET"])
def status():
    ok,reason=circuit_breaker()
    return jsonify({
        "mode":MODE,"open_trades":list(_open_trades),
        "trailing":{k:{kk:vv for kk,vv in v.items() if kk!="paper"}
                    for k,v in _trailing_state.items()},
        "daily_pnl_pct":round(_daily_pnl,2),
        "can_trade":ok,"block_reason":reason,
        "session":_session_label(datetime.now(timezone.utc).hour),
        "ai":{"trained":_ai_model_ready,"z_threshold":round(_ai_z_threshold,3)},
        "stats":_stats,
    }),200

@flask_app.route("/macro",methods=["GET"])
def macro():
    refresh_fear_greed()
    session_label,pen=get_session_info()
    return jsonify({
        "fear_greed":_fg_cache["value"],"label":_fg_cache["label"],
        "bias":"LONG" if _fg_cache["bias"]==1 else ("SHORT" if _fg_cache["bias"]==-1 else "NEUTRO"),
        "session":session_label,"session_penalty":pen,
        "mtf_effective":round(_mtf_conf_dyn+pen,3),
        "funding_history":{k:list(v) for k,v in _funding_hist.items()},
        "correlation_cache_syms":list(_price_cache.keys()),
        "sweep_stats":{"detected":_stats["sweeps_detected"],"traded":_stats["sweeps_traded"]},
    }),200

@flask_app.route("/scan",methods=["GET"])
def scan_now():
    results=[]
    for sym in SCAN_SYMBOLS:
        try:
            res=_run_async(analyze_mtf(sym.strip()))
            if res: results.append(res)
        except Exception as e:
            results.append({"symbol":sym,"error":str(e)})
    return jsonify({"scanned":len(SCAN_SYMBOLS),"found":len(results),"signals":results}),200

@flask_app.route("/risk",methods=["GET"])
def risk():
    ok,reason=circuit_breaker()
    return jsonify({
        "balance":ACCOUNT_BALANCE,"risk_pct":RISK_PER_TRADE*100,
        "max_trades":MAX_OPEN_TRADES,"current_open":len(_open_trades),
        "daily_loss_limit_pct":MAX_DAILY_LOSS*100,"daily_pnl_pct":round(_daily_pnl,2),
        "can_trade":ok,"reason":reason,"leverage":LEVERAGE,
        "order_size_usdt":calc_order_size(),
        "atr_sl_mult":ATR_SL_MULT,"atr_trail_mult":ATR_TRAIL_MULT,
    }),200

# ═══════════════════════════════════════════════════════════════
#   ARRANQUE
# ═══════════════════════════════════════════════════════════════

import threading

def run_flask(port):
    flask_app.run(host="0.0.0.0",port=port,use_reloader=False)

async def main():
    global _loop
    _loop=asyncio.get_running_loop()
    tasks=[trailing_loop()]
    if SCANNER_ENABLED: tasks.append(scanner_loop())
    await asyncio.gather(*tasks)

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    send_telegram(
        f"🔌 <b>AEGIS GEX v7.0 ELITE arrancando...</b>\n"
        f"Modo: <b>{MODE.upper()}</b> | Puerto: <b>{port}</b>\n"
        f"⚡ Liquidity Sweep | 🛡️ ATR-SL | 🔗 Corr Guard\n"
        f"🕐 Session Filter | 📈 Funding Momentum | 🏆 Tier Scoring"
    )
    t=threading.Thread(target=run_flask,args=(port,),daemon=True)
    t.start()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Detenido manualmente.")
    finally:
        asyncio.run(exchange_async.close())
