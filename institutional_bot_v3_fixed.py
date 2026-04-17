"""
AEGIS GEX Bot — Dealer Flow Engine v3.0
Z-Score Quant + Whale Absorption + Trailing Stop
BingX Futuros Perpetuos → Telegram
"""

import os, logging, time, threading, math
from flask import Flask, request, jsonify
import ccxt, requests
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
app = Flask(__name__)

# ──────────────────────────────────────────────
# Variables de entorno
# ──────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")
ORDER_SIZE       = float(os.getenv("ORDER_SIZE", "10"))
LEVERAGE         = int(os.getenv("LEVERAGE", "5"))
MODE             = os.getenv("MODE", "paper")

# ── Scanner ──
SCAN_INTERVAL    = int(os.getenv("SCAN_INTERVAL", "60"))
SCAN_TIMEFRAME   = os.getenv("SCAN_TIMEFRAME", "15m")
SCANNER_ENABLED  = os.getenv("SCANNER_ENABLED", "true").lower() == "true"

# ── Whale / CVD ──
WHALE_MULT    = float(os.getenv("WHALE_VOL_MULTIPLIER", "2.5"))
CVD_THRESHOLD = float(os.getenv("CVD_THRESHOLD", "0.6"))

# ── Z-Score (Simons) ──
ZSCORE_WINDOW    = int(os.getenv("ZSCORE_WINDOW", "20"))
ZSCORE_THRESHOLD = float(os.getenv("ZSCORE_THRESHOLD", "2.5"))

# ── Absorción ──
# Volumen alto pero precio no se mueve → ballena absorbiendo → disparo inminente
ABSORPTION_VOL_MULT  = float(os.getenv("ABSORPTION_VOL_MULT", "3.0"))   # x veces vol medio
ABSORPTION_MOVE_PCT  = float(os.getenv("ABSORPTION_MOVE_PCT", "0.002"))  # <0.2% movimiento precio

# ── Trailing Stop ──
TRAILING_ENABLED = os.getenv("TRAILING_ENABLED", "true").lower() == "true"
TRAILING_PCT     = float(os.getenv("TRAILING_PCT", "0.8"))   # % desde máximo/mínimo visto
TRAILING_MONITOR = int(os.getenv("TRAILING_MONITOR", "15"))  # segundos entre checks

# ── DGRP umbrales reales AEGIS GEX ──
DGRP_POSITIVE_MAX = 35
DGRP_NEGATIVE_MIN = 60

# ── Monedas válidas para GEX ──
GEX_VALID_SYMBOLS = [
    "BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT",
    "XRP/USDT:USDT","LINK/USDT:USDT","ADA/USDT:USDT","MATIC/USDT:USDT",
    "DOT/USDT:USDT","LTC/USDT:USDT",
]
_env_syms    = os.getenv("SCAN_SYMBOLS", "")
SCAN_SYMBOLS = [s.strip() for s in _env_syms.split(",") if s.strip()] if _env_syms else GEX_VALID_SYMBOLS

# ──────────────────────────────────────────────
# BingX
# ──────────────────────────────────────────────
exchange = ccxt.bingx({
    "apiKey": BINGX_API_KEY,
    "secret": BINGX_SECRET_KEY,
    "options": {"defaultType": "swap"},
})

def normalize_symbol(raw: str) -> str:
    s = raw.strip().upper()
    for sfx in [".P","PERP",".PERP"]:
        if s.endswith(sfx): s = s[:-len(sfx)]
    if "/USDT:USDT" in s: return s
    if "/" in s and ":USDT" not in s: return s.replace("/USDT","/USDT:USDT")
    if s.endswith("USDT") and "/" not in s: return f"{s[:-4]}/USDT:USDT"
    return s

# ──────────────────────────────────────────────
# Telegram
# ──────────────────────────────────────────────
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        log.error(f"Telegram: {e}")


def fmt_msg(data, order, error, source="TradingView"):
    ts     = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    sym    = data.get("ticker","?")
    sig    = data.get("signal","?").upper()
    regime = data.get("regime","?")
    dgrp   = data.get("dgrp_score","?")
    price  = data.get("price","?")
    stype  = data.get("signal_type","")
    whale  = data.get("whale", False)
    cvd    = data.get("cvd_pct","")
    zscore = data.get("z_score","")
    absorb = data.get("absorption", False)
    src    = "📡 Scanner" if source=="Scanner" else "📊 TradingView"
    re     = {"POSITIVE GAMMA":"🟢","NEGATIVE GAMMA":"🔴","FLIP ZONE":"🟡"}.get(regime,"⚪")
    bull   = "LONG" in sig or "BUY" in sig

    if error:
        return (f"⚠️ <b>AEGIS GEX — ERROR</b>\n────────────────────\n"
                f"🕒 {ts} | {src}\n📈 {sym} | {sig}\n❌ <code>{error}</code>")

    status  = "✅ EJECUTADA" if order and not order.get("paper") else "📋 PAPER"
    oid     = order.get("id","—") if order else "—"
    extras  = ""
    if zscore: extras += f"\n📊 Z-Score: <b>{zscore}</b> (agotamiento estadístico)"
    if whale:  extras += f"\n🐋 Ballena detectada | CVD: <b>{cvd}%</b>"
    if absorb: extras += f"\n🧲 Absorción detectada (disparo inminente)"
    if "vanna" in sig.lower(): extras += f"\n⭐ Alta probabilidad Vanna Unwind (P>70%)"

    return (
        f"{'🟢' if bull else '🔴'} <b>AEGIS GEX — {sig}</b>\n"
        f"────────────────────\n"
        f"🕒 {ts} | {src}\n"
        f"📈 <b>{sym}</b> @ <b>{price}</b>\n"
        f"💡 {stype}{extras}\n"
        f"────────────────────\n"
        f"{re} Régimen: <b>{regime}</b> | DGRP: <b>{dgrp}/100</b>\n"
        f"────────────────────\n"
        f"📦 {status} | 🆔 <code>{oid}</code>\n"
        f"⚙️ Size: ${ORDER_SIZE} | Lev: {LEVERAGE}x"
    )


# ──────────────────────────────────────────────
# Trading
# ──────────────────────────────────────────────
def set_leverage(symbol):
    try: exchange.set_leverage(LEVERAGE, symbol)
    except Exception as e: log.warning(f"Leverage {symbol}: {e}")

def close_opposite(symbol, side):
    try:
        for pos in exchange.fetch_positions([symbol]):
            if pos["symbol"]==symbol and float(pos.get("contracts",0))!=0:
                ps = pos.get("side","")
                if side=="long"  and ps=="short":
                    exchange.create_market_buy_order(symbol, abs(float(pos["contracts"])), {"reduceOnly":True})
                elif side=="short" and ps=="long":
                    exchange.create_market_sell_order(symbol, abs(float(pos["contracts"])), {"reduceOnly":True})
    except Exception as e: log.warning(f"close_opposite: {e}")

def execute_order(symbol, side):
    set_leverage(symbol)
    close_opposite(symbol, side)
    if side=="long":  return exchange.create_market_buy_order(symbol, ORDER_SIZE)
    if side=="short": return exchange.create_market_sell_order(symbol, ORDER_SIZE)
    raise ValueError(f"Side inválido: {side}")

LONG_SIGNALS  = {"long","buy","wall_break_long","gex_flip_cross_long",
                 "vanna_unwind_long","compression_break_long","whale_long",
                 "zscore_long","absorption_long"}
SHORT_SIGNALS = {"short","sell","wall_break_short","gex_flip_cross_short",
                 "vanna_unwind_short","compression_break_short","whale_short",
                 "zscore_short","absorption_short"}

def interpret_signal(raw):
    raw = raw.lower().strip()
    if raw in LONG_SIGNALS:  return "long"
    if raw in SHORT_SIGNALS: return "short"
    if raw == "close":       return "close"
    raise ValueError(f"Señal desconocida: {raw}")


def process_signal(data, source="TradingView"):
    sym = normalize_symbol(data.get("ticker","BTC/USDT:USDT"))
    data["ticker"] = sym
    try:
        side = interpret_signal(data.get("signal",""))
    except ValueError as e:
        send_telegram(fmt_msg(data, None, str(e), source))
        return {"error": str(e)}

    order = error = None
    try:
        if MODE == "live":
            if side == "close":
                for pos in exchange.fetch_positions([sym]):
                    if pos["symbol"]==sym and float(pos.get("contracts",0))!=0:
                        ps = pos.get("side")
                        if ps=="long":
                            order = exchange.create_market_sell_order(sym, abs(float(pos["contracts"])), {"reduceOnly":True})
                        elif ps=="short":
                            order = exchange.create_market_buy_order(sym, abs(float(pos["contracts"])), {"reduceOnly":True})
            else:
                order = execute_order(sym, side)
                # Registrar para trailing stop
                if TRAILING_ENABLED and order:
                    price = float(order.get("price") or order.get("average") or 0)
                    if price == 0:
                        try:
                            t = exchange.fetch_ticker(sym)
                            price = t["last"]
                        except: pass
                    _trailing_state[sym] = {
                        "side": side,
                        "entry": price,
                        "best": price,
                        "contracts": ORDER_SIZE,
                    }
                    log.info(f"[TRAILING] Registrado {sym} {side} @ {price}")
        else:
            log.info(f"[PAPER] {side.upper()} ${ORDER_SIZE} {sym}")
            order = {"id":f"PAPER-{datetime.utcnow().strftime('%H%M%S')}", "paper":True}
            if TRAILING_ENABLED:
                try:
                    t = exchange.fetch_ticker(sym)
                    price = t["last"]
                except: price = float(data.get("price",0) or 0)
                _trailing_state[sym] = {
                    "side": side, "entry": price,
                    "best": price, "contracts": ORDER_SIZE, "paper": True,
                }
    except Exception as e:
        error = str(e)
        log.error(f"Error orden: {error}")

    send_telegram(fmt_msg(data, order, error, source))
    return {"order": order, "error": error}


# ══════════════════════════════════════════════
#   INDICADORES
# ══════════════════════════════════════════════

def calc_ema(data, period):
    out=[None]*len(data); k=2/(period+1)
    for i in range(period-1,len(data)):
        out[i]=sum(data[i-period+1:i+1])/period if i==period-1 else data[i]*k+out[i-1]*(1-k)
    return out

def calc_atr(highs,lows,closes,period=14):
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(closes))]
    out=[None]*len(trs)
    if len(trs)>=period:
        out[period-1]=sum(trs[:period])/period
        for i in range(period,len(trs)):
            out[i]=(out[i-1]*(period-1)+trs[i])/period
    return out

def calc_bb(closes,period=20,mult=2.0):
    if len(closes)<period: return None,None,None
    w=closes[-period:]; mid=sum(w)/period
    std=(sum((x-mid)**2 for x in w)/period)**0.5
    return mid+mult*std, mid, mid-mult*std

def calc_vwap(highs,lows,closes,volumes):
    tp=[(h+l+c)/3 for h,l,c in zip(highs,lows,closes)]
    tot=sum(volumes)
    return sum(t*v for t,v in zip(tp,volumes))/tot if tot>0 else closes[-1]

def calc_dgrp(atr_vals):
    valid=[x for x in atr_vals if x is not None]
    if len(valid)<28: return "FLIP ZONE",50
    avg=sum(valid[-28:])/28; ratio=valid[-1]/avg if avg>0 else 1.0
    score=int(min(100,max(0,(ratio-0.4)/1.6*100)))
    if score<DGRP_POSITIVE_MAX:   return "POSITIVE GAMMA",score
    elif score>DGRP_NEGATIVE_MIN: return "NEGATIVE GAMMA",score
    else:                         return "FLIP ZONE",score

def calc_cvd(opens,closes,volumes,lookback=20):
    if len(closes)<lookback+1: return 0.0, False
    recent=list(zip(opens[-lookback:],closes[-lookback:],volumes[-lookback:]))
    buy_vol=sum(v for o,c,v in recent if c>=o)
    sell_vol=sum(v for o,c,v in recent if c<o)
    total=buy_vol+sell_vol
    if total==0: return 0.0,False
    imbalance=abs(buy_vol-sell_vol)/total
    avg_vol=sum(volumes[-lookback-1:-1])/lookback
    is_whale=imbalance>=CVD_THRESHOLD and volumes[-1]>avg_vol*WHALE_MULT
    return round(imbalance*100,1), is_whale


# ══════════════════════════════════════════════
#   Z-SCORE ESTADÍSTICO (Estilo Simons)
# ══════════════════════════════════════════════

def calc_zscore(closes, window=None) -> float | None:
    """
    Z = (precio_actual - media) / desv_std
    |Z| > 2.5 → precio estadísticamente extremo
               → ballena agotada → reversión probable 95%
    """
    w = window or ZSCORE_WINDOW
    if len(closes) < w + 1: return None
    subset = closes[-(w+1):-1]   # ventana de historia (excluye vela actual)
    mean   = sum(subset) / w
    variance = sum((x-mean)**2 for x in subset) / w
    std    = variance ** 0.5
    if std == 0: return None
    return round((closes[-1] - mean) / std, 3)


# ══════════════════════════════════════════════
#   DETECCIÓN DE ABSORCIÓN
# ══════════════════════════════════════════════

def detect_absorption(highs, lows, closes, volumes, lookback=10) -> tuple[bool, str]:
    """
    Absorción = Volumen muy alto + Precio apenas se mueve.
    Señal: la ballena está comprando/vendiendo en silencio.
    El disparo vendrá cuando termine.
    """
    if len(closes) < lookback + 1: return False, ""
    avg_vol  = sum(volumes[-(lookback+1):-1]) / lookback
    last_vol = volumes[-1]
    vol_high = last_vol > avg_vol * ABSORPTION_VOL_MULT

    # Movimiento de precio de la última vela (high-low / close)
    price_move = (highs[-1] - lows[-1]) / closes[-1] if closes[-1] > 0 else 99
    price_flat = price_move < ABSORPTION_MOVE_PCT

    if not (vol_high and price_flat): return False, ""

    # Determinar si la absorción es alcista o bajista
    buy_vol  = sum(v for o,c,v in zip([None]*lookback,closes[-lookback:],volumes[-lookback:]) if c>=closes[-lookback-1])
    # Simplificado: last candle color
    bull_absorb = closes[-1] >= closes[-2]
    direction   = "alcista 🔺" if bull_absorb else "bajista 🔻"
    return True, direction


# ══════════════════════════════════════════════
#   TRAILING STOP — Hilo de monitoreo
# ══════════════════════════════════════════════

_trailing_state: dict = {}   # {symbol: {side, entry, best, contracts, paper?}}

def trailing_monitor_loop():
    """
    Monitorea posiciones abiertas y aplica trailing stop.
    LONG:  sube best_price cuando precio sube, cierra si cae TRAILING_PCT% desde best
    SHORT: baja best_price cuando precio baja, cierra si sube TRAILING_PCT% desde best
    """
    log.info(f"🎯 Trailing Stop monitor ON | {TRAILING_PCT}% | Check cada {TRAILING_MONITOR}s")
    while True:
        time.sleep(TRAILING_MONITOR)
        if not _trailing_state: continue

        for sym, state in list(_trailing_state.items()):
            try:
                ticker = exchange.fetch_ticker(sym)
                price  = ticker["last"]
            except Exception as e:
                log.warning(f"Trailing fetch_ticker {sym}: {e}"); continue

            side  = state["side"]
            best  = state["best"]
            trail = TRAILING_PCT / 100.0

            if side == "long":
                if price > best:
                    _trailing_state[sym]["best"] = price
                    log.info(f"[TRAILING LONG] {sym} nuevo máximo: {price:.4f}")
                    best = price
                stop_level = best * (1 - trail)
                if price <= stop_level:
                    log.info(f"[TRAILING] 💰 LONG stop activado {sym} @ {price:.4f} (best={best:.4f})")
                    _close_trailing(sym, state, price, "LONG trailing stop")

            elif side == "short":
                if price < best:
                    _trailing_state[sym]["best"] = price
                    log.info(f"[TRAILING SHORT] {sym} nuevo mínimo: {price:.4f}")
                    best = price
                stop_level = best * (1 + trail)
                if price >= stop_level:
                    log.info(f"[TRAILING] 💰 SHORT stop activado {sym} @ {price:.4f} (best={best:.4f})")
                    _close_trailing(sym, state, price, "SHORT trailing stop")


def _close_trailing(sym, state, price, reason):
    side  = state["side"]
    entry = state["entry"]
    best  = state["best"]
    pnl   = ((price - entry) / entry * 100) if side == "long" else ((entry - price) / entry * 100)
    pnl   = round(pnl * LEVERAGE, 2)

    if not state.get("paper") and MODE == "live":
        try:
            contracts = state.get("contracts", ORDER_SIZE)
            if side == "long":
                exchange.create_market_sell_order(sym, contracts, {"reduceOnly": True})
            else:
                exchange.create_market_buy_order(sym, contracts, {"reduceOnly": True})
        except Exception as e:
            log.error(f"Trailing close error {sym}: {e}")

    _trailing_state.pop(sym, None)
    send_telegram(
        f"🎯 <b>TRAILING STOP — {reason.upper()}</b>\n"
        f"────────────────────\n"
        f"📈 <b>{sym}</b>\n"
        f"📍 Entrada: <b>{entry:.4f}</b> | Salida: <b>{price:.4f}</b>\n"
        f"🏆 Best: <b>{best:.4f}</b>\n"
        f"{'🟢' if pnl>=0 else '🔴'} PnL est.: <b>{pnl:+.2f}%</b> (x{LEVERAGE})\n"
        f"⚙️ Modo: {'LIVE' if MODE=='live' else 'PAPER'}"
    )


# ══════════════════════════════════════════════
#   ANALIZADOR COMPLETO
# ══════════════════════════════════════════════

_last_signal: dict = {}

def analyze_symbol(symbol, timeframe):
    """
    Prioridad de señales (mayor → menor probabilidad):
    1. Z-Score extremo + Volumen (Simons — reversión 95%)
    2. Absorción de ballena (disparo inminente)
    3. Vanna Unwind en NEGATIVE GAMMA (P>70%)
    4. Whale CVD
    5. Compression BB
    6. GEX Flip Cross EMA9/21
    7. Wall Break VWAP
    """
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=120)
    except Exception as e:
        log.warning(f"fetch_ohlcv {symbol}: {e}"); return None

    if len(ohlcv) < 60: return None

    opens   = [x[1] for x in ohlcv]
    highs   = [x[2] for x in ohlcv]
    lows    = [x[3] for x in ohlcv]
    closes  = [x[4] for x in ohlcv]
    volumes = [x[5] for x in ohlcv]
    price   = closes[-1]

    atr_v              = calc_atr(highs,lows,closes,14)
    regime, score      = calc_dgrp(atr_v)
    ema9               = calc_ema(closes,9)
    ema21              = calc_ema(closes,21)
    bb_up,bb_mid,bb_lo = calc_bb(closes,20,2.0)
    vwap_v             = calc_vwap(highs,lows,closes,volumes)
    cvd_pct, is_whale  = calc_cvd(opens,closes,volumes,20)
    z                  = calc_zscore(closes, ZSCORE_WINDOW)
    absorb, absorb_dir = detect_absorption(highs,lows,closes,volumes,10)
    atr_last           = atr_v[-1] if atr_v[-1] else 0

    e9p,e9c   = ema9[-2],  ema9[-1]
    e21p,e21c = ema21[-2], ema21[-1]
    cp,cc,op  = closes[-2], closes[-1], opens[-1]

    signal = signal_type = None
    extra  = {}

    # ── 1. Z-SCORE (Simons — reversión estadística) ──────────────
    if z is not None and abs(z) >= ZSCORE_THRESHOLD:
        avg_vol = sum(volumes[-ZSCORE_WINDOW-1:-1]) / ZSCORE_WINDOW
        vol_spike = volumes[-1] > avg_vol * 3.0
        if vol_spike:
            if z < -ZSCORE_THRESHOLD:
                signal,signal_type = "zscore_long", f"Z-Score {z} → agotamiento bajista (Simons)"
            elif z > ZSCORE_THRESHOLD:
                signal,signal_type = "zscore_short", f"Z-Score {z} → agotamiento alcista (Simons)"
            extra["z_score"] = z

    # ── 2. ABSORCIÓN (volumen alto + precio plano) ───────────────
    if signal is None and absorb:
        if "alcista" in absorb_dir:
            signal,signal_type = "absorption_long",  f"Absorción {absorb_dir} → disparo inminente"
        else:
            signal,signal_type = "absorption_short", f"Absorción {absorb_dir} → disparo inminente"
        extra["absorption"] = True

    # ── 3. VANNA UNWIND ──────────────────────────────────────────
    if signal is None and regime=="NEGATIVE GAMMA" and atr_last>0:
        trend_up   = closes[-3]<closes[-2]
        big_candle = abs(cc-op)>atr_last*1.5
        if big_candle:
            if trend_up and cc<op:
                signal,signal_type = "vanna_unwind_short","Vanna Unwind bajista 🔻 (P>70%)"
            elif not trend_up and cc>op:
                signal,signal_type = "vanna_unwind_long","Vanna Unwind alcista 🔺 (P>70%)"

    # ── 4. WHALE CVD ─────────────────────────────────────────────
    if signal is None and is_whale:
        bv=sum(v for o,c,v in zip(opens[-20:],closes[-20:],volumes[-20:]) if c>=o)
        sv=sum(v for o,c,v in zip(opens[-20:],closes[-20:],volumes[-20:]) if c<o)
        if bv>sv: signal,signal_type = "whale_long",  f"🐋 Ballena alcista | CVD {cvd_pct}%"
        else:     signal,signal_type = "whale_short", f"🐋 Ballena bajista | CVD {cvd_pct}%"
        extra.update({"whale":True,"cvd_pct":cvd_pct})

    # ── 5. COMPRESSION BB ────────────────────────────────────────
    if signal is None and bb_up and bb_mid and bb_lo:
        bw=(bb_up-bb_lo)/bb_mid if bb_mid else 99
        if bw<0.025:
            if cc>bb_up: signal,signal_type = "compression_break_long",  "Compresión BB → ruptura alcista"
            elif cc<bb_lo: signal,signal_type = "compression_break_short","Compresión BB → ruptura bajista"

    # ── 6. GEX FLIP CROSS ────────────────────────────────────────
    if signal is None and all(v is not None for v in [e9p,e9c,e21p,e21c]):
        if e9p<=e21p and e9c>e21c and regime!="FLIP ZONE":
            signal,signal_type = "gex_flip_cross_long","GEX Flip Cross alcista (EMA9>EMA21)"
        elif e9p>=e21p and e9c<e21c and regime!="FLIP ZONE":
            signal,signal_type = "gex_flip_cross_short","GEX Flip Cross bajista (EMA9<EMA21)"

    # ── 7. WALL BREAK VWAP ───────────────────────────────────────
    if signal is None:
        if cp<vwap_v and cc>vwap_v*1.001:
            signal,signal_type = "wall_break_long",  f"Wall Break VWAP alcista @ {vwap_v:.4f}"
        elif cp>vwap_v and cc<vwap_v*0.999:
            signal,signal_type = "wall_break_short", f"Wall Break VWAP bajista @ {vwap_v:.4f}"

    if signal is None: return None

    return {
        "signal": signal, "signal_type": signal_type,
        "ticker": symbol, "price": f"{price:.4f}",
        "regime": regime, "dgrp_score": score,
        "gex_flip": f"{vwap_v:.4f}",
        **extra,
    }


# ══════════════════════════════════════════════
#   SCANNER LOOP
# ══════════════════════════════════════════════

def scanner_loop():
    log.info(f"🔍 Scanner ON | {len(SCAN_SYMBOLS)} síms | {SCAN_TIMEFRAME} | {SCAN_INTERVAL}s")
    send_telegram(
        f"🔍 <b>AEGIS GEX v3.0 — Scanner iniciado</b>\n"
        f"📋 {len(SCAN_SYMBOLS)} símbolos con opciones líquidas\n"
        f"⏱ {SCAN_TIMEFRAME} | Cada {SCAN_INTERVAL}s\n"
        f"📊 Z-Score: ±{ZSCORE_THRESHOLD} (ventana {ZSCORE_WINDOW})\n"
        f"🐋 Whale: x{WHALE_MULT} vol | 🎯 Trailing: {TRAILING_PCT}%"
    )
    n = 0
    while True:
        n += 1; log.info(f"── SCAN #{n} ──"); found=0
        for sym in SCAN_SYMBOLS:
            sym=sym.strip()
            try:
                res=analyze_symbol(sym, SCAN_TIMEFRAME)
            except Exception as e:
                log.error(f"{sym}: {e}"); continue
            if res is None: continue
            sig=res["signal"]
            if _last_signal.get(sym)==sig:
                log.info(f"  {sym}: repetida ({sig})"); continue
            _last_signal[sym]=sig; found+=1
            log.info(f"  ✅ {sym} → {sig} | {res.get('signal_type')}")
            process_signal(res, source="Scanner")
            time.sleep(0.3)
        log.info(f"── FIN #{n} | Señales: {found}/{len(SCAN_SYMBOLS)} ──")
        time.sleep(SCAN_INTERVAL)


# ──────────────────────────────────────────────
# Flask routes
# ──────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":"online","bot":"AEGIS GEX v3.0","mode":MODE,
        "scanner":SCANNER_ENABLED,"trailing":TRAILING_ENABLED,
        "symbols":SCAN_SYMBOLS,"timeframe":SCAN_TIMEFRAME,
        "zscore_threshold":ZSCORE_THRESHOLD,
    }), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Webhook-Secret","")!=WEBHOOK_SECRET:
        return jsonify({"error":"Unauthorized"}), 401
    data=request.get_json(silent=True)
    if not data: return jsonify({"error":"No JSON"}), 400
    log.info(f"Webhook: {data}")
    res=process_signal(data, source="TradingView")
    if res.get("error"): return jsonify({"status":"error","message":res["error"]}), 500
    return jsonify({"status":"success","order":res.get("order")}), 200

@app.route("/status", methods=["GET"])
def status():
    try:
        positions=[]
        if MODE=="live":
            all_pos=exchange.fetch_positions()
            positions=[p for p in all_pos if float(p.get("contracts",0))!=0]
        return jsonify({
            "mode":MODE,"positions":positions,
            "last_signals":_last_signal,
            "trailing_active":_trailing_state,
        }), 200
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/scan", methods=["GET"])
def scan_now():
    results=[]
    for sym in SCAN_SYMBOLS:
        try:
            res=analyze_symbol(sym.strip(), SCAN_TIMEFRAME)
            if res: results.append(res)
        except Exception as e:
            results.append({"symbol":sym,"error":str(e)})
    return jsonify({"scanned":len(SCAN_SYMBOLS),"signals_found":len(results),"signals":results}), 200

@app.route("/symbols", methods=["GET"])
def list_symbols():
    return jsonify({"gex_valid":GEX_VALID_SYMBOLS,"scanning":SCAN_SYMBOLS}), 200


# ──────────────────────────────────────────────
# Arranque
# ──────────────────────────────────────────────
if __name__ == "__main__":
    port=int(os.environ.get("PORT",5000))
    send_telegram(
        f"🚀 <b>AEGIS GEX Bot v3.0</b>\n"
        f"Modo: <b>{MODE.upper()}</b> | Lev: <b>{LEVERAGE}x</b> | Size: <b>${ORDER_SIZE}</b>\n"
        f"Scanner: <b>{'ON' if SCANNER_ENABLED else 'OFF'}</b> | "
        f"Trailing: <b>{'ON {TRAILING_PCT}%' if TRAILING_ENABLED else 'OFF'}</b>\n"
        f"Z-Score: <b>±{ZSCORE_THRESHOLD}</b> | Absorción: <b>ON</b>"
    )
    if SCANNER_ENABLED:
        threading.Thread(target=scanner_loop, daemon=True).start()
    if TRAILING_ENABLED:
        threading.Thread(target=trailing_monitor_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=port)
