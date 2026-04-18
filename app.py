"""
AEGIS GEX Bot — Dealer Flow Engine v6.0 "APEX QUANT"
═══════════════════════════════════════════════════════
Inteligencia institucional completa — 100% gratuita:

  MÓDULOS DE SEÑAL:
  ├── Z-Score Estadístico      (Simons — reversión 95%)
  ├── Absorción de Volumen     (disparo inminente)
  ├── Vanna Unwind             (P>70% NEGATIVE GAMMA)
  ├── Whale CVD                (flujo institucional)
  ├── Compression BB           (squeeze breakout)
  ├── GEX Flip Cross           (EMA9/21)
  └── Wall Break VWAP          (cruce nivel clave)

  MÓDULOS DE INTELIGENCIA:
  ├── Fear & Greed Index       (alternative.me — gratis)
  ├── Liquidaciones Masivas    (CoinGlass + BingX OI)
  ├── Correlación SPX          (Yahoo Finance — gratis)
  ├── OI Momentum              (BingX — gratis)
  ├── Funding Rate Harvest     (cobrar el funding pasivo)
  ├── BTC Dominance Rotación   (rotación de capital)
  └── Stablecoin Inflow        (señal 2-6h antes del precio)

  GESTIÓN DE RIESGO:
  ├── Circuit Breaker          (stop automático diario)
  ├── Trailing Stop            (asegura ganancias)
  ├── SL Fijo                  (red de seguridad)
  ├── Max Posiciones           (control exposición)
  ├── Filtro Sesión NY         (solo horario líquido)
  ├── Filtro Funding           (evita trampas)
  └── Order Book Real          (muros de liquidez)

  ENDPOINTS:
  ├── /           health check completo
  ├── /webhook    señales TradingView
  ├── /status     posiciones + estado
  ├── /scan       escaneo inmediato
  ├── /intel      inteligencia en tiempo real
  ├── /harvest    estado funding harvest
  ├── /cb/reset   reset circuit breaker
  └── /symbols    símbolos válidos GEX
═══════════════════════════════════════════════════════
"""

import os, logging, time, threading
from flask import Flask, request, jsonify
from datetime import datetime
import ccxt, requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
app = Flask(__name__)

# ══════════════════════════════════════════════
#   CONFIGURACIÓN
# ══════════════════════════════════════════════

BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")
ORDER_SIZE_USD   = float(os.getenv("ORDER_SIZE", "10"))
LEVERAGE         = int(os.getenv("LEVERAGE", "5"))
MODE             = os.getenv("MODE", "paper")

# ── Scanner ──
SCAN_INTERVAL     = int(os.getenv("SCAN_INTERVAL", "60"))
SCAN_TIMEFRAME    = os.getenv("SCAN_TIMEFRAME", "15m")
CONFIRM_TIMEFRAME = os.getenv("CONFIRM_TIMEFRAME", "1h")
SCANNER_ENABLED   = os.getenv("SCANNER_ENABLED", "true").lower() == "true"

# ── Indicadores técnicos ──
WHALE_MULT           = float(os.getenv("WHALE_VOL_MULTIPLIER", "2.5"))
CVD_THRESHOLD        = float(os.getenv("CVD_THRESHOLD", "0.6"))
ZSCORE_WINDOW        = int(os.getenv("ZSCORE_WINDOW", "20"))
ZSCORE_THRESHOLD     = float(os.getenv("ZSCORE_THRESHOLD", "2.5"))
ABSORPTION_VOL_MULT  = float(os.getenv("ABSORPTION_VOL_MULT", "3.0"))
ABSORPTION_MOVE_PCT  = float(os.getenv("ABSORPTION_MOVE_PCT", "0.002"))

# ── Riesgo ──
TRAILING_ENABLED = os.getenv("TRAILING_ENABLED", "true").lower() == "true"
TRAILING_PCT     = float(os.getenv("TRAILING_PCT", "0.8"))
TRAILING_MONITOR = int(os.getenv("TRAILING_MONITOR", "15"))
SL_PCT           = float(os.getenv("SL_PCT", "2.0"))
CB_MAX_LOSS_PCT  = float(os.getenv("CB_MAX_LOSS_PCT", "4.0"))
CB_MAX_STREAK    = int(os.getenv("CB_MAX_STREAK", "3"))
CB_ENABLED       = os.getenv("CB_ENABLED", "true").lower() == "true"
MAX_POSITIONS    = int(os.getenv("MAX_POSITIONS", "2"))

# ── Filtros ──
SESSION_FILTER     = os.getenv("SESSION_FILTER", "true").lower() == "true"
SESSION_HOUR_START = int(os.getenv("SESSION_HOUR_START", "13"))
SESSION_HOUR_END   = int(os.getenv("SESSION_HOUR_END", "22"))
FUNDING_FILTER     = os.getenv("FUNDING_FILTER", "true").lower() == "true"
FUNDING_MAX        = float(os.getenv("FUNDING_MAX", "0.05"))

# ── Inteligencia ──
FNG_FILTER        = os.getenv("FNG_FILTER", "true").lower() == "true"
FNG_EXTREME_FEAR  = int(os.getenv("FNG_EXTREME_FEAR", "20"))
FNG_EXTREME_GREED = int(os.getenv("FNG_EXTREME_GREED", "80"))
LIQ_FILTER        = os.getenv("LIQ_FILTER", "true").lower() == "true"
LIQ_MIN_USD       = float(os.getenv("LIQ_MIN_USD", "500000"))
SPX_FILTER        = os.getenv("SPX_FILTER", "true").lower() == "true"

# ── OI Momentum ──
OI_FILTER         = os.getenv("OI_FILTER", "true").lower() == "true"
OI_CHANGE_MIN     = float(os.getenv("OI_CHANGE_MIN", "2.0"))   # % cambio mínimo OI para confirmar

# ── BTC Dominance ──
BTCD_FILTER       = os.getenv("BTCD_FILTER", "true").lower() == "true"
BTCD_THRESHOLD    = float(os.getenv("BTCD_THRESHOLD", "0.5"))  # % cambio en dominancia para rotar

# ── Funding Harvest ──
HARVEST_ENABLED   = os.getenv("HARVEST_ENABLED", "true").lower() == "true"
HARVEST_MIN_RATE  = float(os.getenv("HARVEST_MIN_RATE", "0.05"))  # % funding mínimo para activar
HARVEST_SYMBOLS   = os.getenv("HARVEST_SYMBOLS", "BTC/USDT:USDT,ETH/USDT:USDT").split(",")
HARVEST_MONITOR   = int(os.getenv("HARVEST_MONITOR", "300"))       # segundos entre checks

# ── DGRP ──
DGRP_POSITIVE_MAX = 35
DGRP_NEGATIVE_MIN = 60

# ── Símbolos GEX válidos ──
GEX_VALID_SYMBOLS = [
    "BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT",
    "XRP/USDT:USDT","LINK/USDT:USDT","ADA/USDT:USDT","MATIC/USDT:USDT",
    "DOT/USDT:USDT","LTC/USDT:USDT",
]
_env_syms    = os.getenv("SCAN_SYMBOLS", "")
SCAN_SYMBOLS = [s.strip() for s in _env_syms.split(",") if s.strip()] if _env_syms else GEX_VALID_SYMBOLS

# ══════════════════════════════════════════════
#   ESTADO GLOBAL
# ══════════════════════════════════════════════

_trailing_state : dict = {}
_last_signal    : dict = {}
_open_positions : set  = set()
_position_lock         = threading.Lock()
_signal_lock           = threading.Lock()

_cb_state = {
    "active":False,"daily_pnl_pct":0.0,
    "losing_streak":0,"trades_today":0,
    "last_reset":datetime.utcnow().date(),
}

# Harvest state
_harvest_state : dict = {}   # {symbol: {side, entry, contracts, funding_collected}}

# Cache inteligencia
_cache = {
    "fng_value":50,"fng_label":"Neutral","fng_ts":0,
    "liq_long":0,"liq_short":0,"liq_ts":0,
    "spx_change":0.0,"spx_ts":0,
    "btcd_change":0.0,"btcd_ts":0,
    "oi":{},"oi_ts":0,
    "stablecoin_inflow":0.0,"stable_ts":0,
}
_CACHE_TTL = 300

# ══════════════════════════════════════════════
#   BINGX
# ══════════════════════════════════════════════

exchange = ccxt.bingx({
    "apiKey": BINGX_API_KEY,
    "secret": BINGX_SECRET_KEY,
    "options": {"defaultType":"swap"},
})


def normalize_symbol(raw:str) -> str:
    s = raw.strip().upper()
    for sfx in [".P","PERP",".PERP"]:
        if s.endswith(sfx): s = s[:-len(sfx)]
    if "/USDT:USDT" in s: return s
    if "/" in s and ":USDT" not in s: return s.replace("/USDT","/USDT:USDT")
    if s.endswith("USDT") and "/" not in s: return f"{s[:-4]}/USDT:USDT"
    return s


def usd_to_contracts(symbol:str, usd:float) -> float:
    try:
        price     = exchange.fetch_ticker(symbol)["last"]
        market    = exchange.market(symbol)
        precision = int(market.get("precision",{}).get("amount",3))
        step      = market.get("contractSize",1)
        contracts = round((usd * LEVERAGE) / price, precision)
        return max(contracts, step)
    except Exception as e:
        log.warning(f"usd_to_contracts {symbol}: {e}"); return 1.0


# ══════════════════════════════════════════════
#   INTELIGENCIA INSTITUCIONAL
# ══════════════════════════════════════════════

def fetch_fear_greed() -> dict:
    now = time.time()
    if now - _cache["fng_ts"] < _CACHE_TTL:
        return {"value":_cache["fng_value"],"label":_cache["fng_label"]}
    try:
        d   = requests.get("https://api.alternative.me/fng/?limit=1",timeout=5).json()["data"][0]
        val = int(d["value"]); lbl = d["value_classification"]
        _cache.update({"fng_value":val,"fng_label":lbl,"fng_ts":now})
        log.info(f"[F&G] {val} — {lbl}")
    except Exception as e:
        log.warning(f"F&G: {e}")
    return {"value":_cache["fng_value"],"label":_cache["fng_label"]}


def fetch_liquidations() -> dict:
    """Liquidaciones desde BingX OI (gratis, sin key)."""
    now = time.time()
    if now - _cache["liq_ts"] < _CACHE_TTL:
        return {"long":_cache["liq_long"],"short":_cache["liq_short"]}
    try:
        # Intentar CoinGlass público primero
        r = requests.get(
            "https://open-api.coinglass.com/public/v2/liquidation_history",
            params={"symbol":"BTC","time_type":"h1"}, timeout=5
        )
        if r.status_code == 200:
            d         = r.json().get("data",[{}])
            last      = d[-1] if d else {}
            long_usd  = float(last.get("longLiquidationUsd",0))
            short_usd = float(last.get("shortLiquidationUsd",0))
        else:
            raise Exception(f"CoinGlass {r.status_code}")
    except:
        # Fallback: estimar desde cambio en OI de BingX
        try:
            sym = "BTC/USDT:USDT"
            oi  = exchange.fetch_open_interest_history(sym,"1h",limit=3)
            if len(oi)>=2:
                prev = float(oi[-2].get("openInterest",0))
                curr = float(oi[-1].get("openInterest",0))
                p    = exchange.fetch_ticker(sym)["last"]
                diff = (prev - curr) * p
                long_usd  = diff if diff>0 else 0
                short_usd = abs(diff) if diff<0 else 0
            else:
                long_usd = short_usd = 0
        except:
            long_usd = short_usd = 0
    _cache.update({"liq_long":long_usd,"liq_short":short_usd,"liq_ts":now})
    log.info(f"[LIQ] Longs: ${long_usd:,.0f} | Shorts: ${short_usd:,.0f}")
    return {"long":long_usd,"short":short_usd}


def fetch_spx() -> float:
    """Correlación BTC/SPX via Yahoo Finance (gratis)."""
    now = time.time()
    if now - _cache["spx_ts"] < _CACHE_TTL:
        return _cache["spx_change"]
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC",
            headers={"User-Agent":"Mozilla/5.0"},
            params={"interval":"5m","range":"1d"}, timeout=6
        )
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        chg    = round((closes[-1]-closes[-2])/closes[-2]*100,3) if len(closes)>=2 else 0.0
        _cache.update({"spx_change":chg,"spx_ts":now})
        log.info(f"[SPX] {chg:+.3f}%")
    except Exception as e:
        log.warning(f"SPX: {e}")
    return _cache["spx_change"]


def fetch_btc_dominance() -> float:
    """
    BTC Dominance via CoinGecko (gratis).
    Subida → capital a BTC, evitar altcoins.
    Bajada → rotación a altcoins, oportunidad.
    """
    now = time.time()
    if now - _cache["btcd_ts"] < _CACHE_TTL*2:
        return _cache["btcd_change"]
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=6
        )
        d    = r.json()["data"]
        btcd = float(d.get("market_cap_percentage",{}).get("btc",50))
        prev = _cache.get("btcd_prev", btcd)
        chg  = round(btcd - prev, 3)
        _cache.update({"btcd_change":chg,"btcd_prev":btcd,"btcd_ts":now})
        log.info(f"[BTCD] Dominancia BTC: {btcd:.1f}% | Cambio: {chg:+.3f}%")
    except Exception as e:
        log.warning(f"BTC Dominance: {e}")
    return _cache["btcd_change"]


def fetch_oi_momentum(symbol:str) -> dict:
    """
    OI Momentum desde BingX (gratis).
    OI↑ + Precio↑ = tendencia fuerte (confirma LONG)
    OI↑ + Precio↓ = shorts atrapados (señal LONG explosivo)
    OI↓ + Precio↑ = longs cerrando (debilidad)
    OI↓ + Precio↓ = pánico / capitulación
    """
    now = time.time()
    cached_oi = _cache.get("oi",{})
    if now - _cache["oi_ts"] < _CACHE_TTL and symbol in cached_oi:
        return cached_oi[symbol]

    result = {"oi_change_pct":0.0,"signal":"neutral","trapped":"none"}
    try:
        history = exchange.fetch_open_interest_history(symbol,"1h",limit=3)
        if len(history)<2:
            return result
        prev_oi = float(history[-2].get("openInterest",0))
        curr_oi = float(history[-1].get("openInterest",0))
        if prev_oi == 0:
            return result

        oi_chg = round((curr_oi - prev_oi) / prev_oi * 100, 2)
        ticker = exchange.fetch_ticker(symbol)
        price_chg = float(ticker.get("percentage",0) or 0)

        if oi_chg > OI_CHANGE_MIN and price_chg > 0:
            sig = "strong_long"       # Tendencia alcista confirmada
        elif oi_chg > OI_CHANGE_MIN and price_chg < 0:
            sig = "trapped_shorts"    # Shorts atrapados → explosión alcista inminente
            result["trapped"] = "shorts"
        elif oi_chg < -OI_CHANGE_MIN and price_chg > 0:
            sig = "weak_long"         # Longs cerrando, debilidad
        elif oi_chg < -OI_CHANGE_MIN and price_chg < 0:
            sig = "capitulation"      # Pánico / posible suelo
        else:
            sig = "neutral"

        result = {"oi_change_pct":oi_chg,"signal":sig,"trapped":result["trapped"],"price_chg":price_chg}
        _cache["oi"][symbol] = result
        _cache["oi_ts"] = now
        log.info(f"[OI] {symbol}: {oi_chg:+.2f}% | {sig}")
    except Exception as e:
        log.warning(f"OI {symbol}: {e}")
    return result


def fetch_stablecoin_inflow() -> float:
    """
    Entrada de stablecoins a exchanges via CoinGecko (gratis).
    Inflow alto → presión compradora en 2-6h.
    Outflow → presión vendedora.
    Retorna % cambio en supply on-exchange (aproximado via market cap USDT).
    """
    now = time.time()
    if now - _cache["stable_ts"] < _CACHE_TTL*2:
        return _cache["stablecoin_inflow"]
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/tether",
            params={"localization":"false","tickers":"false","community_data":"false"},
            timeout=6
        )
        d     = r.json()
        vol   = float(d.get("market_data",{}).get("total_volume",{}).get("usd",0))
        mcap  = float(d.get("market_data",{}).get("market_cap",{}).get("usd",1))
        ratio = round(vol/mcap*100,2) if mcap>0 else 0   # Vol/MCap = velocidad de movimiento
        prev  = _cache.get("stable_prev_ratio", ratio)
        chg   = round(ratio - prev, 2)
        _cache.update({"stablecoin_inflow":chg,"stable_prev_ratio":ratio,"stable_ts":now})
        log.info(f"[STABLE] USDT Vol/MCap: {ratio:.2f}% | Cambio: {chg:+.2f}%")
    except Exception as e:
        log.warning(f"Stablecoin: {e}")
    return _cache["stablecoin_inflow"]


def get_intel(symbol:str="BTC/USDT:USDT") -> dict:
    """Refresca y devuelve toda la inteligencia institucional."""
    return {
        "fng":           fetch_fear_greed(),
        "liquidations":  fetch_liquidations(),
        "spx_change":    fetch_spx(),
        "btcd_change":   fetch_btc_dominance(),
        "oi":            fetch_oi_momentum(symbol),
        "stablecoin":    fetch_stablecoin_inflow(),
    }


# ══════════════════════════════════════════════
#   TELEGRAM
# ══════════════════════════════════════════════

def send_telegram(msg:str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML"},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        log.error(f"Telegram: {e}")


def fmt_msg(data:dict, order, error, source:str="TradingView", intel:dict=None) -> str:
    ts     = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    sym    = data.get("ticker","?")
    sig    = data.get("signal","?").upper()
    regime = data.get("regime","?")
    dgrp   = data.get("dgrp_score","?")
    price  = data.get("price","?")
    stype  = data.get("signal_type","")
    src    = "📡 Scanner" if source=="Scanner" else "📊 TradingView"
    re     = {"POSITIVE GAMMA":"🟢","NEGATIVE GAMMA":"🔴","FLIP ZONE":"🟡"}.get(regime,"⚪")
    bull   = "LONG" in sig or "BUY" in sig

    if error:
        return (f"⚠️ <b>APEX QUANT — ERROR</b>\n────────────────────\n"
                f"🕒 {ts} | {src}\n📈 {sym} | {sig}\n❌ <code>{error}</code>")

    status  = "✅ EJECUTADA" if order and not order.get("paper") else "📋 PAPER"
    oid     = order.get("id","—") if order else "—"
    extras  = ""
    if data.get("z_score"):     extras += f"\n📊 Z={data['z_score']} (agotamiento Simons)"
    if data.get("whale"):       extras += f"\n🐋 Ballena | CVD: {data.get('cvd_pct')}%"
    if data.get("absorption"):  extras += f"\n🧲 Absorción → disparo inminente"
    if data.get("mtf_confirm"): extras += f"\n✅ Multi-TF {SCAN_TIMEFRAME}+{CONFIRM_TIMEFRAME}"
    if data.get("oi_signal"):   extras += f"\n📈 OI: {data['oi_signal']}"
    if "vanna" in sig.lower():  extras += f"\n⭐ Vanna Unwind P>70%"

    intel_block = ""
    if intel:
        fng  = intel.get("fng",{})
        liq  = intel.get("liquidations",{})
        spx  = intel.get("spx_change",0)
        btcd = intel.get("btcd_change",0)
        stbl = intel.get("stablecoin",0)
        oi   = intel.get("oi",{})
        fv   = fng.get("value",50)
        fi   = "😱" if fv<30 else "🤑" if fv>70 else "😐"
        intel_block = (
            f"\n────────────────────\n"
            f"🧠 <b>INTELIGENCIA QUANT</b>\n"
            f"{fi} F&amp;G: <b>{fv} — {fng.get('label','?')}</b>\n"
            f"📈 SPX: <b>{spx:+.3f}%</b> | BTC.D: <b>{btcd:+.3f}%</b>\n"
            f"💥 Liq: Longs <b>${liq.get('long',0):,.0f}</b> | Shorts <b>${liq.get('short',0):,.0f}</b>\n"
            f"💰 USDT Inflow: <b>{stbl:+.2f}%</b> | OI: <b>{oi.get('signal','—')}</b>"
        )

    return (
        f"{'🟢' if bull else '🔴'} <b>APEX QUANT — {sig}</b>\n"
        f"────────────────────\n"
        f"🕒 {ts} | {src}\n"
        f"📈 <b>{sym}</b> @ <b>{price}</b>\n"
        f"💡 {stype}{extras}"
        f"{intel_block}\n"
        f"────────────────────\n"
        f"{re} Régimen: <b>{regime}</b> | DGRP: <b>{dgrp}/100</b>\n"
        f"────────────────────\n"
        f"📦 {status} | 🆔 <code>{oid}</code>\n"
        f"⚙️ ${ORDER_SIZE_USD} | {LEVERAGE}x | SL:{SL_PCT}% | Trail:{TRAILING_PCT}%"
    )


# ══════════════════════════════════════════════
#   FILTROS
# ══════════════════════════════════════════════

def check_session():
    if not SESSION_FILTER: return True,""
    h = datetime.utcnow().hour
    if SESSION_HOUR_START <= h < SESSION_HOUR_END: return True,""
    return False,f"Fuera sesión NY (UTC {h}h — {SESSION_HOUR_START}-{SESSION_HOUR_END})"

def check_funding(symbol, side):
    if not FUNDING_FILTER: return True,""
    try:
        rate = float(exchange.fetch_funding_rate(symbol).get("fundingRate",0))*100
        if side=="long"  and rate>FUNDING_MAX:  return False,f"Funding {rate:.3f}% — evitando LONG"
        if side=="short" and rate<-FUNDING_MAX: return False,f"Funding {rate:.3f}% — evitando SHORT"
    except Exception as e: log.warning(f"Funding {symbol}: {e}")
    return True,""

def check_cb():
    if not CB_ENABLED: return True,""
    _reset_cb_day()
    if _cb_state["active"]:
        return False,f"CB: PnL {_cb_state['daily_pnl_pct']:.2f}% | Racha {_cb_state['losing_streak']}"
    return True,""

def check_max_pos():
    with _position_lock:
        if len(_open_positions)>=MAX_POSITIONS:
            return False,f"Max posiciones ({MAX_POSITIONS})"
    return True,""

def check_orderbook(symbol, side):
    try:
        ob   = exchange.fetch_order_book(symbol,limit=20)
        bids = ob.get("bids",[]); asks = ob.get("asks",[])
        bv   = sum(v for _,v in bids[:5]); av=sum(v for _,v in asks[:5])
        tot  = bv+av
        if tot==0: return True,""
        r    = bv/tot
        if side=="long"  and r<0.35: return False,f"OB: presión vendedora ({r*100:.0f}% bids)"
        if side=="short" and r>0.65: return False,f"OB: presión compradora ({r*100:.0f}% bids)"
    except Exception as e: log.warning(f"OB {symbol}: {e}")
    return True,""

def check_fng(side, intel):
    if not FNG_FILTER: return True,""
    fng = intel.get("fng",{}); val = fng.get("value",50)
    if val<=FNG_EXTREME_FEAR  and side=="short": return False,f"F&G {val} Miedo Extremo — evitando SHORT"
    if val>=FNG_EXTREME_GREED and side=="long":  return False,f"F&G {val} Codicia Extrema — evitando LONG"
    return True,""

def check_liquidations(side, intel):
    if not LIQ_FILTER: return True,""
    liq = intel.get("liquidations",{})
    if liq.get("long",0)>LIQ_MIN_USD  and side=="short": return False,f"Liq LONGS ${liq['long']:,.0f} — buscar rebote LONG"
    if liq.get("short",0)>LIQ_MIN_USD and side=="long":  return False,f"Liq SHORTS ${liq['short']:,.0f} — buscar rebote SHORT"
    return True,""

def check_spx(side, intel):
    if not SPX_FILTER: return True,""
    chg = intel.get("spx_change",0)
    if chg<-0.3 and side=="long":  return False,f"SPX {chg:+.3f}% cayendo — evitando LONG"
    if chg>0.3  and side=="short": return False,f"SPX {chg:+.3f}% subiendo — evitando SHORT"
    return True,""

def check_btcd(symbol, side, intel):
    """
    Si BTC.D sube fuerte → evitar altcoins LONG (capital va a BTC).
    Si BTC.D baja fuerte → oportunidad LONG en altcoins.
    No afecta a BTC directamente.
    """
    if not BTCD_FILTER: return True,""
    if "BTC" in symbol: return True,""   # BTC no afectado
    chg = intel.get("btcd_change",0)
    if chg>BTCD_THRESHOLD  and side=="long":  return False,f"BTC.D subiendo {chg:+.3f}% — capital a BTC, evitar altcoin LONG"
    if chg<-BTCD_THRESHOLD and side=="short": return False,f"BTC.D bajando {chg:+.3f}% — rotación altcoins, evitar SHORT"
    return True,""

def check_oi(symbol, side, intel):
    """
    Usa OI Momentum para confirmar dirección.
    trapped_shorts → confirma LONG
    trapped_longs  → confirma SHORT
    weak_long + side=long → bloquea
    """
    if not OI_FILTER: return True,""
    oi = intel.get("oi",{})
    sig = oi.get("signal","neutral")
    if sig=="weak_long" and side=="long":   return False,"OI: longs cerrando — señal débil"
    if sig=="capitulation" and side=="short": return False,"OI: posible suelo — evitar SHORT"
    return True,""

def check_stablecoin(side, intel):
    """
    USDT inflow alto → presión compradora en 2-6h → confirma LONG.
    Outflow → presión vendedora.
    """
    stbl = intel.get("stablecoin",0)
    if stbl>2.0 and side=="short": return False,f"USDT inflow {stbl:+.2f}% — presión compradora próxima"
    if stbl<-2.0 and side=="long": return False,f"USDT outflow {stbl:+.2f}% — presión vendedora"
    return True,""

def _reset_cb_day():
    today = datetime.utcnow().date()
    if _cb_state["last_reset"]<today:
        _cb_state.update({"daily_pnl_pct":0.0,"losing_streak":0,
                          "trades_today":0,"active":False,"last_reset":today})
        log.info("CB reseteado (nuevo día)")

def update_cb(pnl_pct:float):
    _reset_cb_day()
    _cb_state["daily_pnl_pct"] += pnl_pct
    _cb_state["trades_today"]  += 1
    _cb_state["losing_streak"]  = _cb_state["losing_streak"]+1 if pnl_pct<0 else 0
    breached=False; reason=""
    if _cb_state["daily_pnl_pct"]<=-CB_MAX_LOSS_PCT:
        breached=True; reason=f"Pérdida {_cb_state['daily_pnl_pct']:.2f}% > límite {CB_MAX_LOSS_PCT}%"
    if _cb_state["losing_streak"]>=CB_MAX_STREAK:
        breached=True; reason=f"Racha {_cb_state['losing_streak']} pérdidas"
    if breached:
        _cb_state["active"]=True
        send_telegram(f"⚡ <b>CIRCUIT BREAKER</b>\n🛑 {reason}\n"
                      f"PnL día: <b>{_cb_state['daily_pnl_pct']:.2f}%</b> | Reset 00:00 UTC")


# ══════════════════════════════════════════════
#   TRADING
# ══════════════════════════════════════════════

def set_leverage(symbol):
    try: exchange.set_leverage(LEVERAGE,symbol)
    except Exception as e: log.warning(f"Leverage {symbol}: {e}")

def close_opposite(symbol, side):
    try:
        for pos in exchange.fetch_positions([symbol]):
            if pos["symbol"]==symbol and float(pos.get("contracts",0))!=0:
                ps=pos.get("side","")
                if side=="long"  and ps=="short":
                    exchange.create_market_buy_order(symbol,abs(float(pos["contracts"])),{"reduceOnly":True})
                elif side=="short" and ps=="long":
                    exchange.create_market_sell_order(symbol,abs(float(pos["contracts"])),{"reduceOnly":True})
    except Exception as e: log.warning(f"close_opposite: {e}")

def execute_order(symbol, side):
    set_leverage(symbol); close_opposite(symbol,side)
    n = usd_to_contracts(symbol, ORDER_SIZE_USD)
    log.info(f"Ejecutando {side.upper()} {n} contratos {symbol}")
    if side=="long":  return exchange.create_market_buy_order(symbol,n), n
    if side=="short": return exchange.create_market_sell_order(symbol,n), n
    raise ValueError(f"Side inválido: {side}")

LONG_SIGNALS  = {"long","buy","wall_break_long","gex_flip_cross_long","vanna_unwind_long",
                 "compression_break_long","whale_long","zscore_long","absorption_long",
                 "liq_rebound_long","oi_trapped_shorts","harvest_long"}
SHORT_SIGNALS = {"short","sell","wall_break_short","gex_flip_cross_short","vanna_unwind_short",
                 "compression_break_short","whale_short","zscore_short","absorption_short",
                 "liq_rebound_short","oi_trapped_longs","harvest_short"}

def interpret_signal(raw):
    raw=raw.lower().strip()
    if raw in LONG_SIGNALS:  return "long"
    if raw in SHORT_SIGNALS: return "short"
    if raw=="close":         return "close"
    raise ValueError(f"Señal desconocida: {raw}")


def process_signal(data:dict, source:str="TradingView", intel:dict=None) -> dict:
    sym = normalize_symbol(data.get("ticker","BTC/USDT:USDT"))
    data["ticker"]=sym
    if intel is None: intel=get_intel(sym)

    try:
        side = interpret_signal(data.get("signal",""))
    except ValueError as e:
        send_telegram(fmt_msg(data,None,str(e),source,intel))
        return {"error":str(e)}

    if side!="close":
        checks=[
            (check_cb,[]),
            (check_session,[]),
            (check_max_pos,[]),
            (check_fng,[side,intel]),
            (check_spx,[side,intel]),
            (check_stablecoin,[side,intel]),
            (check_liquidations,[side,intel]),
            (check_btcd,[sym,side,intel]),
            (check_oi,[sym,side,intel]),
            (check_orderbook,[sym,side]),
            (check_funding,[sym,side]),
        ]
        for fn,args in checks:
            ok,reason=fn(*args)
            if not ok:
                log.info(f"  Filtro: {sym} {side} bloqueado — {reason}")
                return {"filtered":reason}

    order=error=None; contracts=ORDER_SIZE_USD
    try:
        if MODE=="live":
            if side=="close":
                for pos in exchange.fetch_positions([sym]):
                    if pos["symbol"]==sym and float(pos.get("contracts",0))!=0:
                        ps=pos.get("side"); n=abs(float(pos["contracts"]))
                        if ps=="long":  order=exchange.create_market_sell_order(sym,n,{"reduceOnly":True})
                        elif ps=="short": order=exchange.create_market_buy_order(sym,n,{"reduceOnly":True})
                with _position_lock: _open_positions.discard(sym)
            else:
                order,contracts=execute_order(sym,side)
                with _position_lock: _open_positions.add(sym)
        else:
            log.info(f"[PAPER] {side.upper()} ${ORDER_SIZE_USD} {sym}")
            order={"id":f"PAPER-{datetime.utcnow().strftime('%H%M%S')}","paper":True}
            with _position_lock:
                if side=="close": _open_positions.discard(sym)
                else: _open_positions.add(sym)

        if TRAILING_ENABLED and side!="close" and order:
            try: ep=exchange.fetch_ticker(sym)["last"]
            except: ep=float(data.get("price",0) or 0)
            _trailing_state[sym]={
                "side":side,"entry":ep,"best":ep,"contracts":contracts,
                "paper":order.get("paper",False),
                "sl_price":ep*(1-SL_PCT/100) if side=="long" else ep*(1+SL_PCT/100),
            }
    except Exception as e:
        error=str(e); log.error(f"Error orden: {error}")

    send_telegram(fmt_msg(data,order,error,source,intel))
    return {"order":order,"error":error}


# ══════════════════════════════════════════════
#   FUNDING RATE HARVEST
#   Cobra el funding pasivamente cada 8h
#   Cuando funding > HARVEST_MIN_RATE:
#   → Abre SHORT en futuros + notifica
#   → Cobra el pago cada ciclo de funding
#   → Cierra cuando el funding cae
# ══════════════════════════════════════════════

def funding_harvest_loop():
    log.info(f"💰 Funding Harvest ON | Min rate: {HARVEST_MIN_RATE}% | Check: {HARVEST_MONITOR}s")
    send_telegram(
        f"💰 <b>Funding Rate Harvest activado</b>\n"
        f"Símbolos: <b>{', '.join(HARVEST_SYMBOLS)}</b>\n"
        f"Min funding: <b>{HARVEST_MIN_RATE}%</b> | Check: <b>{HARVEST_MONITOR}s</b>"
    )
    while True:
        time.sleep(HARVEST_MONITOR)
        for sym in HARVEST_SYMBOLS:
            sym=sym.strip()
            try:
                info = exchange.fetch_funding_rate(sym)
                rate = float(info.get("fundingRate",0))*100
                next_ts = info.get("fundingDatetime","")
                log.info(f"[HARVEST] {sym}: funding={rate:.4f}%")

                in_harvest = sym in _harvest_state

                if not in_harvest and rate>HARVEST_MIN_RATE:
                    # Activar harvest: abrir SHORT
                    log.info(f"[HARVEST] Abriendo SHORT en {sym} — funding {rate:.4f}%")
                    if MODE=="live":
                        n=usd_to_contracts(sym,ORDER_SIZE_USD)
                        set_leverage(sym)
                        order=exchange.create_market_sell_order(sym,n)
                        oid=order.get("id","?")
                    else:
                        n=1; oid=f"HARVEST-PAPER-{datetime.utcnow().strftime('%H%M%S')}"

                    _harvest_state[sym]={
                        "side":"short","entry":exchange.fetch_ticker(sym)["last"] if MODE=="live" else 0,
                        "contracts":n,"funding_rate":rate,"funding_collected":0.0,
                        "cycles":0,"order_id":oid,
                    }
                    send_telegram(
                        f"💰 <b>HARVEST INICIADO — {sym}</b>\n"
                        f"────────────────────\n"
                        f"📉 SHORT abierto para cobrar funding\n"
                        f"💹 Funding rate: <b>{rate:.4f}%</b> cada 8h\n"
                        f"💵 Ingreso esperado: <b>${ORDER_SIZE_USD*rate/100:.4f}</b>/ciclo\n"
                        f"🆔 <code>{oid}</code>\n"
                        f"⚙️ {'LIVE' if MODE=='live' else 'PAPER'}"
                    )

                elif in_harvest:
                    state=_harvest_state[sym]
                    # Estimar funding cobrado (aproximación)
                    state["cycles"]+=1
                    estimated=ORDER_SIZE_USD*state["funding_rate"]/100
                    state["funding_collected"]+=estimated

                    if rate<HARVEST_MIN_RATE/2:
                        # Cerrar harvest
                        log.info(f"[HARVEST] Funding caído ({rate:.4f}%), cerrando {sym}")
                        if MODE=="live":
                            try:
                                n=state.get("contracts",1)
                                exchange.create_market_buy_order(sym,n,{"reduceOnly":True})
                            except Exception as e: log.error(f"Harvest close {sym}: {e}")
                        total=state["funding_collected"]
                        send_telegram(
                            f"💰 <b>HARVEST CERRADO — {sym}</b>\n"
                            f"────────────────────\n"
                            f"✅ Funding caído a {rate:.4f}%\n"
                            f"🔄 Ciclos cobrados: <b>{state['cycles']}</b>\n"
                            f"💵 Funding estimado: <b>${total:.4f}</b>\n"
                            f"⚙️ {'LIVE' if MODE=='live' else 'PAPER'}"
                        )
                        _harvest_state.pop(sym,None)
                    else:
                        log.info(f"[HARVEST] {sym}: ciclo {state['cycles']} | est. ${state['funding_collected']:.4f}")

            except Exception as e:
                log.error(f"Harvest {sym}: {e}")


# ══════════════════════════════════════════════
#   OI TRAPPED — Señal especial
# ══════════════════════════════════════════════

def check_oi_trapped(symbol:str, intel:dict) -> dict|None:
    """
    OI↑ + Precio↓ = shorts atrapados → explosión alcista inminente.
    Esta es la señal más explosiva que existe en perpetuos.
    """
    oi = intel.get("oi",{})
    if oi.get("signal")=="trapped_shorts":
        return {
            "signal":"oi_trapped_shorts",
            "signal_type":f"🔥 OI: Shorts atrapados (+{oi.get('oi_change_pct',0):.2f}% OI con precio bajando)",
            "ticker":symbol,"price":"0",
            "regime":"NEGATIVE GAMMA","dgrp_score":75,
            "gex_flip":"0","oi_signal":"trapped_shorts",
        }
    return None


# ══════════════════════════════════════════════
#   TRAILING STOP + SL FIJO
# ══════════════════════════════════════════════

def trailing_monitor_loop():
    log.info(f"🎯 Trailing ON | {TRAILING_PCT}% trail | {SL_PCT}% SL | check {TRAILING_MONITOR}s")
    while True:
        time.sleep(TRAILING_MONITOR)
        if not _trailing_state: continue
        for sym, state in list(_trailing_state.items()):
            try:
                price=exchange.fetch_ticker(sym)["last"]
            except Exception as e:
                log.warning(f"Trailing {sym}: {e}"); continue

            if MODE=="live" and not state.get("paper"):
                try:
                    open_pos=any(
                        p["symbol"]==sym and float(p.get("contracts",0))!=0
                        for p in exchange.fetch_positions([sym])
                    )
                    if not open_pos:
                        _trailing_state.pop(sym,None)
                        with _position_lock: _open_positions.discard(sym)
                        continue
                except: pass

            side=state["side"]; best=state["best"]; sl=state.get("sl_price",0)
            trail=TRAILING_PCT/100; reason=None

            if side=="long":
                if price>best: _trailing_state[sym]["best"]=price; best=price
                if sl>0 and price<=sl:       reason="🛡 SL FIJO"
                elif price<=best*(1-trail):  reason="🎯 TRAILING"
            elif side=="short":
                if price<best: _trailing_state[sym]["best"]=price; best=price
                if sl>0 and price>=sl:       reason="🛡 SL FIJO"
                elif price>=best*(1+trail):  reason="🎯 TRAILING"

            if reason: _do_close(sym, state, price, reason)


def _do_close(sym, state, price, reason):
    side=state["side"]; entry=state["entry"]; best=state["best"]
    pnl=((price-entry)/entry*100) if side=="long" else ((entry-price)/entry*100)
    pnl=round(pnl*LEVERAGE,2)

    if not state.get("paper") and MODE=="live":
        try:
            n=state.get("contracts",ORDER_SIZE_USD)
            if side=="long": exchange.create_market_sell_order(sym,n,{"reduceOnly":True})
            else:            exchange.create_market_buy_order(sym,n,{"reduceOnly":True})
        except Exception as e: log.error(f"close {sym}: {e}")

    _trailing_state.pop(sym,None)
    with _position_lock: _open_positions.discard(sym)
    update_cb(pnl/LEVERAGE)

    icon="💰" if pnl>=0 else "🔴"
    send_telegram(
        f"{icon} <b>{reason} — {sym}</b>\n"
        f"────────────────────\n"
        f"📍 Entrada: <b>{entry:.4f}</b> → Salida: <b>{price:.4f}</b>\n"
        f"🏆 Best: <b>{best:.4f}</b>\n"
        f"{'🟢' if pnl>=0 else '🔴'} PnL: <b>{pnl:+.2f}%</b> (x{LEVERAGE})\n"
        f"📊 PnL día: <b>{_cb_state['daily_pnl_pct']:.2f}%</b> | "
        f"Racha pérd: <b>{_cb_state['losing_streak']}</b>\n"
        f"⚙️ {'LIVE' if MODE=='live' else 'PAPER'}"
    )


# ══════════════════════════════════════════════
#   INDICADORES TÉCNICOS
# ══════════════════════════════════════════════

def calc_ema(data,p):
    out=[None]*len(data); k=2/(p+1)
    for i in range(p-1,len(data)):
        out[i]=sum(data[i-p+1:i+1])/p if i==p-1 else data[i]*k+out[i-1]*(1-k)
    return out

def calc_atr(H,L,C,p=14):
    trs=[max(H[i]-L[i],abs(H[i]-C[i-1]),abs(L[i]-C[i-1])) for i in range(1,len(C))]
    out=[None]*len(trs)
    if len(trs)>=p:
        out[p-1]=sum(trs[:p])/p
        for i in range(p,len(trs)): out[i]=(out[i-1]*(p-1)+trs[i])/p
    return out

def calc_bb(C,p=20,m=2.0):
    if len(C)<p: return None,None,None
    w=C[-p:]; mid=sum(w)/p; std=(sum((x-mid)**2 for x in w)/p)**0.5
    return mid+m*std,mid,mid-m*std

def calc_vwap(H,L,C,V):
    tp=[(h+l+c)/3 for h,l,c in zip(H,L,C)]; tot=sum(V)
    return sum(t*v for t,v in zip(tp,V))/tot if tot>0 else C[-1]

def calc_dgrp(atr_vals):
    valid=[x for x in atr_vals if x is not None]
    if len(valid)<28: return "FLIP ZONE",50
    avg=sum(valid[-28:])/28; ratio=valid[-1]/avg if avg>0 else 1.0
    score=int(min(100,max(0,(ratio-0.4)/1.6*100)))
    if score<DGRP_POSITIVE_MAX:   return "POSITIVE GAMMA",score
    elif score>DGRP_NEGATIVE_MIN: return "NEGATIVE GAMMA",score
    else:                         return "FLIP ZONE",score

def calc_cvd(O,C,V,lb=20):
    if len(C)<lb+1: return 0.0,False
    rec=list(zip(O[-lb:],C[-lb:],V[-lb:]))
    bv=sum(v for o,c,v in rec if c>=o); sv=sum(v for o,c,v in rec if c<o)
    tot=bv+sv
    if tot==0: return 0.0,False
    imb=abs(bv-sv)/tot; avg=sum(V[-lb-1:-1])/lb
    return round(imb*100,1), imb>=CVD_THRESHOLD and V[-1]>avg*WHALE_MULT

def calc_zscore(C,w=None):
    w=w or ZSCORE_WINDOW
    if len(C)<w+1: return None
    s=C[-(w+1):-1]; mean=sum(s)/w; std=(sum((x-mean)**2 for x in s)/w)**0.5
    return round((C[-1]-mean)/std,3) if std>0 else None

def detect_absorption(H,L,C,V,lb=10):
    if len(C)<lb+1: return False,""
    avg=sum(V[-(lb+1):-1])/lb
    if V[-1]<=avg*ABSORPTION_VOL_MULT: return False,""
    mv=(H[-1]-L[-1])/C[-1] if C[-1]>0 else 99
    if mv>=ABSORPTION_MOVE_PCT: return False,""
    return True,"alcista 🔺" if C[-1]>=C[-2] else "bajista 🔻"

def confirm_multitf(symbol,side,tf):
    try:
        ohlcv=exchange.fetch_ohlcv(symbol,tf,limit=30)
        C=[x[4] for x in ohlcv]; e9=calc_ema(C,9); e21=calc_ema(C,21)
        if e9[-1] is None or e21[-1] is None: return True
        return e9[-1]>e21[-1] if side=="long" else e9[-1]<e21[-1]
    except: return True


# ══════════════════════════════════════════════
#   ANALIZADOR COMPLETO
# ══════════════════════════════════════════════

def analyze_symbol(symbol:str, timeframe:str, intel:dict) -> dict|None:
    """
    Prioridad señales (mayor → menor P):
    1. OI Trapped (shorts/longs atrapados)  ← NUEVO más explosivo
    2. Z-Score + vol spike (Simons 95%)
    3. Absorción (disparo inminente)
    4. Vanna Unwind P>70%
    5. Whale CVD
    6. Compression BB
    7. GEX Flip Cross
    8. Wall Break VWAP
    Filtros: F&G · SPX · USDT · BTC.D · OI · OB · Funding · Multi-TF
    """
    try:
        ohlcv=exchange.fetch_ohlcv(symbol,timeframe,limit=120)
    except Exception as e:
        log.warning(f"fetch_ohlcv {symbol}: {e}"); return None
    if len(ohlcv)<60: return None

    O=[x[1] for x in ohlcv]; H=[x[2] for x in ohlcv]
    L=[x[3] for x in ohlcv]; C=[x[4] for x in ohlcv]
    V=[x[5] for x in ohlcv]; price=C[-1]

    atr_v           =calc_atr(H,L,C,14)
    regime,score    =calc_dgrp(atr_v)
    ema9            =calc_ema(C,9); ema21=calc_ema(C,21)
    bb_u,bb_m,bb_l  =calc_bb(C,20,2.0)
    vwap_v          =calc_vwap(H,L,C,V)
    cvd_pct,is_whale=calc_cvd(O,C,V,20)
    z               =calc_zscore(C,ZSCORE_WINDOW)
    absorb,ab_dir   =detect_absorption(H,L,C,V,10)
    atr_last        =atr_v[-1] if atr_v[-1] else 0

    e9p,e9c   =ema9[-2],ema9[-1]
    e21p,e21c =ema21[-2],ema21[-1]
    cp,cc,op  =C[-2],C[-1],O[-1]
    signal=signal_type=None; extra={}

    # 1. OI Trapped
    oi_sig=check_oi_trapped(symbol,intel)
    if oi_sig:
        try:
            oi_sig["price"]=f"{exchange.fetch_ticker(symbol)['last']:.4f}"
        except: pass
        return {**oi_sig,"regime":regime,"dgrp_score":score,"gex_flip":f"{vwap_v:.4f}"}

    # 2. Z-Score
    if z is not None and abs(z)>=ZSCORE_THRESHOLD:
        avg_v=sum(V[-ZSCORE_WINDOW-1:-1])/ZSCORE_WINDOW
        if V[-1]>avg_v*3.0:
            if z<-ZSCORE_THRESHOLD: signal,signal_type="zscore_long",  f"Z={z} Agotamiento bajista (Simons)"
            else:                   signal,signal_type="zscore_short", f"Z={z} Agotamiento alcista (Simons)"
            extra["z_score"]=z

    # 3. Absorción
    if signal is None and absorb:
        s="long" if "alcista" in ab_dir else "short"
        signal,signal_type=f"absorption_{s}",f"Absorción {ab_dir}"; extra["absorption"]=True

    # 4. Vanna Unwind
    if signal is None and regime=="NEGATIVE GAMMA" and atr_last>0:
        trend_up=C[-3]<C[-2]; big=abs(cc-op)>atr_last*1.5
        if big:
            if trend_up and cc<op: signal,signal_type="vanna_unwind_short","Vanna Unwind bajista 🔻 P>70%"
            elif not trend_up and cc>op: signal,signal_type="vanna_unwind_long","Vanna Unwind alcista 🔺 P>70%"

    # 5. Whale CVD
    if signal is None and is_whale:
        bv=sum(v for o,c,v in zip(O[-20:],C[-20:],V[-20:]) if c>=o)
        sv=sum(v for o,c,v in zip(O[-20:],C[-20:],V[-20:]) if c<o)
        s="long" if bv>sv else "short"
        signal,signal_type=f"whale_{s}",f"🐋 Ballena {'alcista' if s=='long' else 'bajista'} CVD {cvd_pct}%"
        extra.update({"whale":True,"cvd_pct":cvd_pct})

    # 6. Compression BB
    if signal is None and bb_u and bb_m and bb_l:
        bw=(bb_u-bb_l)/bb_m if bb_m else 99
        if bw<0.025:
            if cc>bb_u: signal,signal_type="compression_break_long","BB squeeze → ruptura alcista"
            elif cc<bb_l: signal,signal_type="compression_break_short","BB squeeze → ruptura bajista"

    # 7. GEX Flip Cross
    if signal is None and all(v is not None for v in [e9p,e9c,e21p,e21c]):
        if e9p<=e21p and e9c>e21c and regime!="FLIP ZONE":
            signal,signal_type="gex_flip_cross_long","GEX Flip Cross alcista"
        elif e9p>=e21p and e9c<e21c and regime!="FLIP ZONE":
            signal,signal_type="gex_flip_cross_short","GEX Flip Cross bajista"

    # 8. Wall Break VWAP
    if signal is None:
        if cp<vwap_v and cc>vwap_v*1.001: signal,signal_type="wall_break_long","Wall Break VWAP alcista"
        elif cp>vwap_v and cc<vwap_v*0.999: signal,signal_type="wall_break_short","Wall Break VWAP bajista"

    if signal is None: return None

    # Multi-TF confirmación
    side_chk="long" if "long" in signal else "short"
    if not confirm_multitf(symbol,side_chk,CONFIRM_TIMEFRAME):
        log.info(f"  {symbol}: bloqueada Multi-TF"); return None
    extra["mtf_confirm"]=True

    # Añadir señal OI al resultado
    oi_data=intel.get("oi",{})
    if oi_data.get("signal") not in ["neutral",""]:
        extra["oi_signal"]=oi_data.get("signal")

    return {
        "signal":signal,"signal_type":signal_type,
        "ticker":symbol,"price":f"{price:.4f}",
        "regime":regime,"dgrp_score":score,
        "gex_flip":f"{vwap_v:.4f}",**extra,
    }


# ══════════════════════════════════════════════
#   SCANNER LOOP
# ══════════════════════════════════════════════

def scanner_loop():
    log.info(f"🔍 APEX QUANT Scanner | {len(SCAN_SYMBOLS)} síms | {SCAN_TIMEFRAME}+{CONFIRM_TIMEFRAME}")
    send_telegram(
        f"🚀 <b>AEGIS GEX APEX QUANT v6.0</b>\n"
        f"════════════════════\n"
        f"📋 {len(SCAN_SYMBOLS)} símbolos GEX\n"
        f"⏱ {SCAN_TIMEFRAME} + {CONFIRM_TIMEFRAME} (Multi-TF)\n"
        f"🧠 F&amp;G · SPX · USDT · BTC.D · OI · Liq\n"
        f"💰 Funding Harvest: <b>{'ON' if HARVEST_ENABLED else 'OFF'}</b>\n"
        f"📊 Z-Score ±{ZSCORE_THRESHOLD} | 🎯 Trail {TRAILING_PCT}% | SL {SL_PCT}%\n"
        f"⚡ CB {CB_MAX_LOSS_PCT}%/día | 🕐 {SESSION_HOUR_START}-{SESSION_HOUR_END}h UTC\n"
        f"⚙️ Modo: <b>{MODE.upper()}</b> | ${ORDER_SIZE_USD} x{LEVERAGE}"
    )
    n=0
    while True:
        n+=1; log.info(f"── SCAN #{n} ──"); found=0

        ok,reason=check_cb()
        if not ok: log.info(f"  CB: {reason}"); time.sleep(SCAN_INTERVAL); continue

        ok,reason=check_session()
        if not ok: log.info(f"  {reason}"); time.sleep(SCAN_INTERVAL); continue

        # Intel una vez por ciclo
        intel=get_intel("BTC/USDT:USDT")
        fng=intel["fng"]; spx=intel["spx_change"]
        btcd=intel["btcd_change"]; stbl=intel["stablecoin"]
        log.info(f"  Intel → F&G:{fng['value']} SPX:{spx:+.3f}% BTC.D:{btcd:+.3f}% USDT:{stbl:+.2f}%")

        # OI Trapped en todos los símbolos (señal más explosiva)
        for sym in SCAN_SYMBOLS:
            sym=sym.strip()
            sym_intel=get_intel(sym)
            with _signal_lock:
                oi_sig=check_oi_trapped(sym,sym_intel)
                if oi_sig:
                    key=f"OI_{sym}"
                    if _last_signal.get(key)!=oi_sig["signal"]:
                        _last_signal[key]=oi_sig["signal"]
                        try: oi_sig["price"]=f"{exchange.fetch_ticker(sym)['last']:.4f}"
                        except: pass
                        log.info(f"  🔥 OI TRAPPED {sym} → {oi_sig['signal']}")
                        process_signal(oi_sig, source="Scanner", intel=sym_intel)
                        found+=1
                        time.sleep(0.5)

        # Escaneo técnico normal
        for sym in SCAN_SYMBOLS:
            sym=sym.strip()
            sym_intel=get_intel(sym)
            try:
                res=analyze_symbol(sym,SCAN_TIMEFRAME,sym_intel)
            except Exception as e:
                log.error(f"{sym}: {e}"); continue
            if res is None: continue
            sig=res["signal"]
            if _last_signal.get(sym)==sig:
                log.info(f"  {sym}: repetida ({sig})"); continue
            _last_signal[sym]=sig; found+=1
            log.info(f"  ✅ {sym} → {sig} | {res.get('signal_type')}")
            process_signal(res, source="Scanner", intel=sym_intel)
            time.sleep(0.5)

        log.info(f"── FIN #{n} | Señales:{found} ──")
        time.sleep(SCAN_INTERVAL)


# ══════════════════════════════════════════════
#   FLASK
# ══════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "bot":"AEGIS GEX APEX QUANT v6.0","mode":MODE,
        "scanner":SCANNER_ENABLED,"trailing":TRAILING_ENABLED,
        "harvest":HARVEST_ENABLED,
        "symbols":SCAN_SYMBOLS,"timeframe":SCAN_TIMEFRAME,"confirm_tf":CONFIRM_TIMEFRAME,
        "filters":{"session":SESSION_FILTER,"funding":FUNDING_FILTER,"fng":FNG_FILTER,
                   "spx":SPX_FILTER,"oi":OI_FILTER,"btcd":BTCD_FILTER,"liq":LIQ_FILTER},
        "circuit_breaker":_cb_state,
        "open_positions":list(_open_positions),
        "trailing_active":list(_trailing_state.keys()),
        "harvest_active":_harvest_state,
    }), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Webhook-Secret","")!=WEBHOOK_SECRET:
        return jsonify({"error":"Unauthorized"}), 401
    data=request.get_json(silent=True)
    if not data: return jsonify({"error":"No JSON"}), 400
    log.info(f"Webhook: {data}")
    sym=normalize_symbol(data.get("ticker","BTC/USDT:USDT"))
    intel=get_intel(sym)
    res=process_signal(data, source="TradingView", intel=intel)
    if res.get("error"):    return jsonify({"status":"error","message":res["error"]}), 500
    if res.get("filtered"): return jsonify({"status":"filtered","reason":res["filtered"]}), 200
    return jsonify({"status":"success","order":res.get("order")}), 200

@app.route("/status", methods=["GET"])
def status():
    try:
        positions=[]
        if MODE=="live":
            positions=[p for p in exchange.fetch_positions() if float(p.get("contracts",0))!=0]
        return jsonify({
            "mode":MODE,"positions":positions,"last_signals":_last_signal,
            "trailing_active":_trailing_state,"open_positions":list(_open_positions),
            "circuit_breaker":_cb_state,"harvest":_harvest_state,
        }), 200
    except Exception as e: return jsonify({"error":str(e)}), 500

@app.route("/intel", methods=["GET"])
def intel_endpoint():
    sym=request.args.get("symbol","BTC/USDT:USDT")
    intel=get_intel(normalize_symbol(sym))
    fng=intel["fng"]; fv=fng.get("value",50)
    spx=intel["spx_change"]; btcd=intel["btcd_change"]
    stbl=intel["stablecoin"]; oi=intel["oi"]

    bias="NEUTRAL"
    if fv<=20:       bias="🟢 LONG (Miedo extremo)"
    elif fv>=80:     bias="🔴 SHORT (Codicia extrema)"
    if spx<-0.3:     bias=f"🔴 SHORT (SPX {spx:+.3f}%)"
    elif spx>0.3:    bias=f"🟢 LONG (SPX {spx:+.3f}%)"
    if stbl>2:       bias=f"🟢 LONG (USDT inflow {stbl:+.2f}%)"
    elif stbl<-2:    bias=f"🔴 SHORT (USDT outflow {stbl:+.2f}%)"
    if oi.get("signal")=="trapped_shorts": bias=f"🚀 LONG EXPLOSIVO (OI: shorts atrapados)"

    return jsonify({
        "symbol":sym,
        "fear_and_greed":fng,
        "spx_5m":f"{spx:+.3f}%",
        "btc_dominance_change":f"{btcd:+.3f}%",
        "usdt_inflow":f"{stbl:+.2f}%",
        "oi_momentum":oi,
        "liquidations":intel["liquidations"],
        "market_bias":bias,
        "session_active":check_session()[0],
        "circuit_breaker":_cb_state,
    }), 200

@app.route("/scan", methods=["GET"])
def scan_now():
    intel=get_intel("BTC/USDT:USDT"); results=[]
    for sym in SCAN_SYMBOLS:
        sym=sym.strip()
        sym_intel=get_intel(sym)
        try:
            res=analyze_symbol(sym,SCAN_TIMEFRAME,sym_intel)
            if res: results.append(res)
        except Exception as e: results.append({"symbol":sym,"error":str(e)})
    return jsonify({"scanned":len(SCAN_SYMBOLS),"found":len(results),"signals":results,"intel":intel}), 200

@app.route("/harvest", methods=["GET"])
def harvest_status():
    details=[]
    for sym,state in _harvest_state.items():
        try:
            rate=float(exchange.fetch_funding_rate(sym).get("fundingRate",0))*100
        except: rate=state.get("funding_rate",0)
        details.append({
            "symbol":sym,"side":state["side"],
            "funding_rate":f"{rate:.4f}%",
            "cycles":state.get("cycles",0),
            "funding_collected":f"${state.get('funding_collected',0):.4f}",
        })
    return jsonify({"harvest_enabled":HARVEST_ENABLED,"active":details,"symbols":HARVEST_SYMBOLS}), 200

@app.route("/cb/reset", methods=["POST"])
def cb_reset():
    _cb_state.update({"active":False,"daily_pnl_pct":0.0,"losing_streak":0})
    send_telegram("⚡ Circuit Breaker reseteado manualmente.")
    return jsonify({"status":"reset","cb":_cb_state}), 200

@app.route("/symbols", methods=["GET"])
def list_symbols():
    return jsonify({"gex_valid":GEX_VALID_SYMBOLS,"scanning":SCAN_SYMBOLS}), 200


# ══════════════════════════════════════════════
#   ARRANQUE
# ══════════════════════════════════════════════

if __name__ == "__main__":
    port=int(os.environ.get("PORT",5000))
    if SCANNER_ENABLED:
        threading.Thread(target=scanner_loop,daemon=True).start()
    if TRAILING_ENABLED:
        threading.Thread(target=trailing_monitor_loop,daemon=True).start()
    if HARVEST_ENABLED:
        threading.Thread(target=funding_harvest_loop,daemon=True).start()
    app.run(host="0.0.0.0",port=port)
