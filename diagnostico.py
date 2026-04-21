#!/usr/bin/env python3
"""
DIAGNÓSTICO — ejecuta esto y dime qué sale
Analiza 10 monedas y muestra exactamente qué filtro falla
"""
import os, requests, math, time
from datetime import datetime

BASE_URL = "https://open-api.bingx.com"
TG_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN','')
TG_CHAT  = os.getenv('TELEGRAM_CHAT_ID','')

def pub(path, params=None):
    try: return requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=10).json()
    except Exception as e: return {}

def tg(msg):
    if TG_TOKEN and TG_CHAT:
        try: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={'chat_id':TG_CHAT,'text':msg,'parse_mode':'HTML'},timeout=6)
        except: pass
    print(msg)

def ema(prices, n):
    if not prices: return 0
    k, e = 2/(n+1), prices[0]
    for p in prices[1:]: e = p*k + e*(1-k)
    return e

def rsi(prices, n=14):
    if len(prices) < n+1: return 50.0
    g = [max(prices[i]-prices[i-1],0) for i in range(1,len(prices))]
    l = [max(prices[i-1]-prices[i],0) for i in range(1,len(prices))]
    ag,al = sum(g[-n:])/n, sum(l[-n:])/n
    return 100.0 if al==0 else 100-100/(1+ag/al)

# ============================================================
print("=" * 60)
print("  DIAGNÓSTICO BOT — "+datetime.now().strftime('%H:%M:%S'))
print("=" * 60)

tg("🔍 <b>DIAGNÓSTICO INICIADO</b>\nAnalizando filtros...")

# 1. Conectividad
print("\n1. TEST API BINGX...")
d = pub('/openApi/swap/v2/quote/ticker', {'symbol':'BTC-USDT'})
if d.get('code') == 0:
    price = float(d['data'].get('lastPrice',0))
    chg   = float(d['data'].get('priceChangePercent',0))
    print(f"   ✅ BTC-USDT: ${price:,.2f} | {chg:+.2f}%")
    tg(f"✅ API OK | BTC: ${price:,.0f} ({chg:+.2f}%)")
else:
    print(f"   ❌ API ERROR: {d}")
    tg(f"❌ API ERROR: {d}")
    exit(1)

# 2. Hora actual UTC
hora = datetime.utcnow().hour
print(f"\n2. HORA UTC ACTUAL: {hora}h")
sesion_v71 = hora in {6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21}
sesion_v60 = hora in {7,8,9,10,11,12,13,14,15,16,17}
print(f"   v7.1 sesión (6-21h): {'✅ DENTRO' if sesion_v71 else '❌ FUERA'}")
print(f"   v6.0 sesión (7-17h): {'✅ DENTRO' if sesion_v60 else '❌ FUERA'}")
tg(f"🕐 Hora UTC: {hora}h\nSesión v7.1: {'✅' if sesion_v71 else '❌ BLOQUEADO'}")

# 3. BTC 1h
print("\n3. TEST BTC 1H...")
dk = pub('/openApi/swap/v3/quote/klines', {'symbol':'BTC-USDT','interval':'1h','limit':4})
btc_1h = 0.0
if dk.get('code')==0 and dk.get('data') and len(dk['data'])>=2:
    closes = [float(k['close']) for k in dk['data']]
    btc_1h = (closes[-1]-closes[-2])/closes[-2]*100
    print(f"   BTC 1h cambio: {btc_1h:+.2f}%")
    ok_v71 = btc_1h >= -1.5
    ok_v60  = btc_1h >= -0.3
    print(f"   v7.1 (BTC>=-1.5%): {'✅' if ok_v71 else '❌ BLOQUEADO'}")
    print(f"   v6.0 (BTC>=-0.3%): {'✅' if ok_v60 else '❌ BLOQUEADO'}")
    tg(f"₿ BTC 1h: {btc_1h:+.2f}%\nv7.1: {'✅' if ok_v71 else '❌ BLOQ'}")

# 4. Breadth
print("\n4. TEST BREADTH (EMA21)...")
COINS = ['BTC-USDT','ETH-USDT','BNB-USDT','SOL-USDT','XRP-USDT',
         'ADA-USDT','DOGE-USDT','LINK-USDT','AVAX-USDT','DOT-USDT']
bulls=0; total=0
for coin in COINS:
    try:
        dk2 = pub('/openApi/swap/v3/quote/klines',{'symbol':coin,'interval':'1h','limit':25})
        if dk2.get('code')==0 and dk2.get('data') and len(dk2['data'])>=21:
            c = [float(k['close']) for k in dk2['data']]
            if c[-1] > ema(c,21): bulls+=1
            total+=1
    except: pass
breadth = bulls/total if total>0 else 0.5
print(f"   Breadth: {bulls}/{total} = {int(breadth*100)}%")
ok_b35 = breadth >= 0.35
ok_b50 = breadth >= 0.50
print(f"   v7.1 (>=35%): {'✅' if ok_b35 else '❌ BLOQUEADO'}")
print(f"   v6.0 (>=50%): {'✅' if ok_b50 else '❌ BLOQUEADO'}")
tg(f"📊 Breadth: {int(breadth*100)}%\nv7.1(35%): {'✅' if ok_b35 else '❌ BLOQ'}\nv6.0(50%): {'✅' if ok_b50 else '❌ BLOQ'}")

# 5. Análisis de 10 monedas concretas
print("\n5. ANÁLISIS DETALLADO DE 10 MONEDAS...")
TEST_COINS = ['BTC-USDT','ETH-USDT','SOL-USDT','XRP-USDT','DOGE-USDT',
              'LINK-USDT','AVAX-USDT','ADA-USDT','BNB-USDT','TRX-USDT']

results = []
for sym in TEST_COINS:
    print(f"\n   [{sym}]")
    issues = []

    # Ticker
    dt = pub('/openApi/swap/v2/quote/ticker',{'symbol':sym})
    if dt.get('code')!=0: print("     ❌ Ticker fallido"); continue
    t = dt['data']
    price = float(t.get('lastPrice',0)); chg = float(t.get('priceChangePercent',0))
    print(f"     Precio: ${price:.4f} | 24h: {chg:+.2f}%")

    # Klines 5m
    dk5 = pub('/openApi/swap/v3/quote/klines',{'symbol':sym,'interval':'5m','limit':130})
    if dk5.get('code')!=0 or not dk5.get('data') or len(dk5['data'])<60:
        print("     ❌ Sin klines 5m"); issues.append("sin_klines"); continue
    kl = dk5['data']
    c5 = [float(k['close'])for k in kl]; h5=[float(k['high'])for k in kl]
    l5 = [float(k['low'])for k in kl];  v5=[float(k['volume'])for k in kl]
    o5 = [float(k['open'])for k in kl]

    # EMA55
    e55 = ema(c5, 55)
    over_ema55 = price > e55
    print(f"     EMA55: ${e55:.4f} | Precio {'>' if over_ema55 else '<'} EMA55: {'✅' if over_ema55 else '❌'}")
    if not over_ema55: issues.append("bajo_ema55")

    # Trend 1h
    dk1h = pub('/openApi/swap/v3/quote/klines',{'symbol':sym,'interval':'1h','limit':30})
    if dk1h.get('code')==0 and dk1h.get('data') and len(dk1h['data'])>=25:
        c1h = [float(k['close']) for k in dk1h['data']]
        e9_1h = ema(c1h,9); e21_1h = ema(c1h,21)
        trend_1h = e9_1h > e21_1h
        rsi_1h = rsi(c1h,14)
        print(f"     1H: EMA9={'✅' if trend_1h else '❌'} alcista | RSI={rsi_1h:.0f}")
        if not trend_1h: issues.append("1h_bajista")
        if rsi_1h > 72: issues.append(f"rsi_1h_alto_{rsi_1h:.0f}")
    else:
        print("     ❌ Sin klines 1h")
        issues.append("sin_1h")

    # Trend 4h
    dk4h = pub('/openApi/swap/v3/quote/klines',{'symbol':sym,'interval':'4h','limit':25})
    if dk4h.get('code')==0 and dk4h.get('data') and len(dk4h['data'])>=21:
        c4h = [float(k['close']) for k in dk4h['data']]
        trend_4h = ema(c4h,9) > ema(c4h,21)
        print(f"     4H: EMA9={'✅' if trend_4h else '❌'} alcista")
        if not trend_4h: issues.append("4h_bajista")
    else:
        issues.append("sin_4h")

    # Vol ratio
    va = sum(v5[-10:-3])/7 if len(v5)>=10 else v5[-1]
    vr = sum(v5[-3:])/3/va if va>0 else 1.0
    vr = min(vr, 15.0)
    print(f"     Vol ratio: {vr:.1f}x (mín 1.3)")
    if vr < 1.3: issues.append(f"vol_bajo_{vr:.1f}x")

    # VWAP
    n_v=min(50,len(c5)); cv=c5[-n_v:]; hv=h5[-n_v:]; lv=l5[-n_v:]; vv=v5[-n_v:]
    tv=sum(((hv[i]+lv[i]+cv[i])/3)*vv[i] for i in range(len(cv))); vs=sum(vv)
    vwap=tv/vs if vs>0 else price
    sobre_vwap = price >= vwap
    print(f"     VWAP: ${vwap:.4f} | {'✅ Sobre' if sobre_vwap else '❌ Bajo'}")
    if not sobre_vwap: issues.append("bajo_vwap")

    # MTF 15m
    dk15 = pub('/openApi/swap/v3/quote/klines',{'symbol':sym,'interval':'15m','limit':40})
    tf_b = 1 if ema(c5,9)>ema(c5,21) else 0
    if dk15.get('code')==0 and dk15.get('data') and len(dk15['data'])>=25:
        c15=[float(k['close'])for k in dk15['data']]
        if ema(c15,9)>ema(c15,21): tf_b+=1
    print(f"     MTF alcista: {tf_b}/2")

    # CVD
    bull=bear=0.0
    for i in range(-20,0):
        c=c5[i]; o=o5[i] if o5 else c5[i-1]; v=v5[i]
        if v>0:
            if c>o: bull+=v
            elif c<o: bear+=v
    cv_r = bull/(bull+bear) if (bull+bear)>0 else 0.5
    print(f"     CVD: {int(cv_r*100)}% compradores")
    if cv_r < 0.45: issues.append(f"cvd_bajo_{int(cv_r*100)}%")

    # Score estimado
    score = 0
    if not issues or (len(issues)==1 and 'bajo_vwap' in issues): score = 65
    elif len(issues)==0: score = 80
    else: score = max(20, 65 - len(issues)*15)

    status = "✅ PASARÍA" if len([i for i in issues if 'vol_bajo' not in i])==0 else f"❌ BLOQUEADO: {', '.join(issues[:3])}"
    print(f"     {status}")
    results.append({'sym':sym,'issues':issues,'score':score})

# 6. Resumen
print("\n" + "="*60)
print("RESUMEN:")
ok = [r for r in results if len(r['issues'])==0]
bloq = [r for r in results if len(r['issues'])>0]
print(f"  Pasarían filtros: {len(ok)}/{len(results)}")
if ok: print(f"  OK: {[r['sym'] for r in ok]}")
print(f"  Bloqueados: {len(bloq)}/{len(results)}")
for r in bloq:
    print(f"  {r['sym']}: {r['issues']}")

# Causa principal
all_issues = []
for r in bloq:
    all_issues.extend(r['issues'])
from collections import Counter
cnt = Counter(all_issues)
print(f"\n  Filtro que más bloquea: {cnt.most_common(3)}")

msg = (
    f"<b>📊 DIAGNÓSTICO COMPLETO</b>\n"
    f"Hora: {hora}h UTC | Sesión: {'✅' if sesion_v71 else '❌'}\n"
    f"BTC 1h: {btc_1h:+.2f}% | Breadth: {int(breadth*100)}%\n\n"
    f"Monedas que pasarían: {len(ok)}/10\n"
    f"{chr(10).join('✅ '+r['sym'] for r in ok) if ok else '❌ Ninguna'}\n\n"
    f"Filtros más bloqueantes:\n"
    + "\n".join(f"  {k}: {v}x" for k,v in cnt.most_common(5))
    + f"\n\nDime este resultado para que ajuste el bot"
)
tg(msg)
print("\n" + msg.replace('<b>','').replace('</b>',''))
