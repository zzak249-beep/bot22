"""
╔══════════════════════════════════════════════════════════════════════════╗
║            AEGIS GEX v8.0 — PRECISION OVER QUANTITY                    ║
║  MEJORAS CRÍTICAS vs v7:                                                ║
║  ✅ Solo pares Tier1/Tier2 líquidos (sin micro-caps)                   ║
║  ✅ Filtro macro BTC obligatorio                                        ║
║  ✅ Anti-overtrading (máx 4 trades/día)                                ║
║  ✅ Auto-blacklist de pares perdedores consecutivos                    ║
║  ✅ R:R mínimo 2.0 verificado con ATR antes de entrar                  ║
║  ✅ Fee-aware: no entrar si ganancia esperada < 3x fees                ║
║  ✅ Circuit breaker de racha negativa (3 pérdidas seguidas)            ║
║  ✅ Confluencia mínima subida a 0.68                                   ║
║  ✅ Cooldown 10min (antes 5min)                                        ║
║  ✅ Tendencia 1h + 4h obligatoria para momentum signals                ║
╚══════════════════════════════════════════════════════════════════════════╝
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
log = logging.getLogger("AEGIS_v8")

# ═══════════════════════════════════════════════════════════════
#   CONFIGURACIÓN v8 — Más conservadora y rentable
# ═══════════════════════════════════════════════════════════════

BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "")
MODE             = os.getenv("MODE", "paper")

# ── Cuenta ────────────────────────────────────
ACCOUNT_BALANCE  = float(os.getenv("ACCOUNT_BALANCE",  "100"))
RISK_PER_TRADE   = float(os.getenv("RISK_PER_TRADE",   "0.015"))  # 1.5% riesgo por trade
MAX_OPEN_TRADES  = int(os.getenv("MAX_OPEN_TRADES",    "2"))       # REDUCIDO: máx 2 simultáneos
MAX_DAILY_LOSS   = float(os.getenv("MAX_DAILY_LOSS",   "0.04"))    # 4% pérdida máxima día
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES",   "4"))       # NUEVO: máx 4 trades/día
LEVERAGE         = int(os.getenv("LEVERAGE",           "3"))

# ── Ejecución ─────────────────────────────────
LIMIT_WAIT_SECS  = int(os.getenv("LIMIT_WAIT_SECS",   "8"))
PRICE_SLIP_PCT   = float(os.getenv("PRICE_SLIP_PCT",   "0.0003"))

# ── Multi-Timeframe ───────────────────────────
MTF_TIMEFRAMES     = ["5m", "15m", "1h", "4h"]   # v8: incluye 4h para tendencia mayor
MTF_WEIGHTS        = {"5m": 0.15, "15m": 0.30, "1h": 0.35, "4h": 0.20}
MTF_CONFLUENCE_MIN = float(os.getenv("MTF_CONFLUENCE_MIN", "0.68"))  # SUBIDO de 0.55

# ── Señales ───────────────────────────────────
ZSCORE_WINDOW       = int(os.getenv("ZSCORE_WINDOW",      "20"))
ZSCORE_THRESHOLD    = float(os.getenv("ZSCORE_THRESHOLD", "2.8"))  # más estricto
WHALE_MULT          = float(os.getenv("WHALE_VOL_MULT",   "3.0"))  # más estricto
CVD_THRESHOLD       = float(os.getenv("CVD_THRESHOLD",    "0.65"))
ABSORPTION_VOL_MULT = float(os.getenv("ABSORPTION_VOL",   "3.5"))
ABSORPTION_MOVE_PCT = float(os.getenv("ABSORPTION_MOVE",  "0.0015"))

# ── Simons ────────────────────────────────────
HURST_WINDOW     = int(os.getenv("HURST_WINDOW",    "40"))
HURST_TREND_MIN  = float(os.getenv("HURST_TREND_MIN",  "0.58"))  # más exigente
HURST_REVERT_MAX = float(os.getenv("HURST_REVERT_MAX", "0.43"))
KURT_MAX         = float(os.getenv("KURT_MAX",       "5.0"))     # más estricto
OI_DELTA_MIN     = float(os.getenv("OI_DELTA_MIN",   "0.015"))

# ── QE / Macro ────────────────────────────────
FG_EXTREME_FEAR  = int(os.getenv("FG_EXTREME_FEAR",  "20"))
FG_EXTREME_GREED = int(os.getenv("FG_EXTREME_GREED", "80"))
FG_REFRESH_SECS  = int(os.getenv("FG_REFRESH_SECS",  "300"))

# ── ATR Stop Loss dinámico ─────────────────────
ATR_SL_MULT      = float(os.getenv("ATR_SL_MULT",    "1.8"))   # SL más amplio = menos falsos stops
ATR_TRAIL_MULT   = float(os.getenv("ATR_TRAIL_MULT", "1.2"))
ATR_SL_MIN_PCT   = float(os.getenv("ATR_SL_MIN_PCT", "0.006"))
ATR_SL_MAX_PCT   = float(os.getenv("ATR_SL_MAX_PCT", "0.025"))

# ── R:R Mínimo — CRÍTICO PARA RENTABILIDAD ────
MIN_RR_RATIO     = float(os.getenv("MIN_RR_RATIO",   "2.0"))   # NUEVO: R:R ≥ 2:1 obligatorio
MIN_TP_PCT       = float(os.getenv("MIN_TP_PCT",     "0.012")) # TP mínimo 1.2% (cubre fees)
FEE_PCT          = float(os.getenv("FEE_PCT",        "0.0005"))# comisión BingX ~0.05% maker
FEE_MULTIPLIER   = float(os.getenv("FEE_MULTIPLIER", "3.0"))   # ganancia mínima = 3x fees

# ── Liquidity Sweep ───────────────────────────
SWEEP_WICK_RATIO  = float(os.getenv("SWEEP_WICK_RATIO", "0.65"))  # más estricto
SWEEP_VOL_MULT    = float(os.getenv("SWEEP_VOL_MULT",   "2.5"))   # más estricto
SWEEP_BODY_MAX    = float(os.getenv("SWEEP_BODY_MAX",   "0.25"))

# ── Session Filter ────────────────────────────
SESSION_FILTER_ON    = os.getenv("SESSION_FILTER", "true").lower() == "true"
LONDON_OPEN_START    = int(os.getenv("LONDON_START", "7"))
LONDON_OPEN_END      = int(os.getenv("LONDON_END",   "10"))
NY_OPEN_START        = int(os.getenv("NY_START",     "13"))
NY_OPEN_END          = int(os.getenv("NY_END",       "16"))
ASIA_OPEN_START      = int(os.getenv("ASIA_START",   "0"))
ASIA_OPEN_END        = int(os.getenv("ASIA_END",     "3"))
OFF_SESSION_PENALTY  = float(os.getenv("OFF_SESSION_PENALTY", "0.15"))  # penalización mayor

# ── Correlation Guard ─────────────────────────
CORR_WINDOW    = int(os.getenv("CORR_WINDOW",   "30"))
CORR_MAX       = float(os.getenv("CORR_MAX",    "0.70"))

# ── Liquidity Tiers — v8: SOLO Tier1/Tier2 ────
# CRÍTICO: Tier3 desactivado por defecto → quita pares micro-cap
TIER1_SYMBOLS  = {"BTC/USDT:USDT", "ETH/USDT:USDT"}
TIER2_SYMBOLS  = {"SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT"}
TIER1_CONF_BONUS   = -0.05
TIER2_CONF_BONUS   =  0.00
TIER3_CONF_PENALTY =  0.99   # efectivamente deshabilita Tier3 (requiere 167% confluencia)
ALLOW_TIER3        = os.getenv("ALLOW_TIER3", "false").lower() == "true"  # NUEVO: off por defecto

# ── Funding Rate Momentum ─────────────────────
FUNDING_HIST_LEN    = int(os.getenv("FUNDING_HIST",  "4"))
FUNDING_SQUEEZE_THR = float(os.getenv("FUND_SQUEEZE","0.0006"))

# ── Scanner ───────────────────────────────────
SCAN_INTERVAL   = int(os.getenv("SCAN_INTERVAL",  "90"))   # cada 90s en vez de 60s
SCANNER_ENABLED = os.getenv("SCANNER_ENABLED",    "true").lower() == "true"
SIGNAL_COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN","600"))  # 10 min (antes 5 min)

# ── Símbolos v8: SOLO top-5 líquidos ─────────
# Eliminados: LINK, AVAX, DOGE, ADA, DOT, MATIC, OP (manipulables, altos fees)
_DEFAULT_SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT",
]
_env_syms    = os.getenv("SCAN_SYMBOLS", "")
_raw_symbols = [s.strip() for s in _env_syms.split(",") if s.strip()] if _env_syms else _DEFAULT_SYMBOLS
# Filtrar automáticamente símbolos no-Tier1/Tier2 si ALLOW_TIER3=false
SCAN_SYMBOLS = [
    s for s in _raw_symbols
    if ALLOW_TIER3 or s in TIER1_SYMBOLS or s in TIER2_SYMBOLS
] or _DEFAULT_SYMBOLS

# ── BTC Macro Filter ──────────────────────────
BTC_MACRO_FILTER = os.getenv("BTC_MACRO_FILTER", "true").lower() == "true"  # NUEVO
BTC_TREND_TF     = "1h"   # timeframe para tendencia BTC

# ── Auto-Blacklist ────────────────────────────
BLACKLIST_MAX_CONSEC_LOSSES = int(os.getenv("BLACKLIST_LOSSES", "3"))   # NUEVO
BLACKLIST_DURATION_SECS     = int(os.getenv("BLACKLIST_SECS",   "3600"))# 1h ban

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
_daily_trade_count: int   = 0   # NUEVO: contador diario
_ai_memory          = collections.deque(maxlen=500)
_ai_model_ready:    bool  = False
_ai_z_threshold:    float = ZSCORE_THRESHOLD

# ── Caches ────────────────────────────────────
_fg_cache:       dict  = {"value": 50, "bias": 0, "ts": 0.0, "label": "Neutral"}
_oi_cache:       dict  = {}
_funding_hist:   dict  = collections.defaultdict(lambda: collections.deque(maxlen=FUNDING_HIST_LEN))
_price_cache:    dict  = {}
_mtf_conf_dyn:   float = MTF_CONFLUENCE_MIN

# ── BTC Macro State ───────────────────────────
_btc_trend:      dict  = {"dir": 0, "strength": 0.0, "ts": 0.0}  # NUEVO

# ── Auto-Blacklist ────────────────────────────
_symbol_consec_losses: dict = collections.defaultdict(int)   # NUEVO
_symbol_blacklist:     dict = {}  # sym → timestamp hasta cuando está baneado

# ── Stats ─────────────────────────────────────
_sweep_detected: dict = {}
_consecutive_losses: int = 0   # NUEVO: racha de pérdidas
_stats: dict = {
    "sweeps_detected": 0, "sweeps_traded": 0,
    "session_blocks": 0, "corr_blocks": 0, "kurt_blocks": 0,
    "tier_blocks": 0, "rr_blocks": 0, "btc_macro_blocks": 0,
    "fee_blocks": 0, "daily_limit_blocks": 0,
    "consec_loss_blocks": 0, "blacklist_blocks": 0,
    "tier1_trades": 0, "tier2_trades": 0,
    "total_wins": 0, "total_losses": 0,
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
    for sfx in [".P", "PERP", ".PERP"]:
        if s.endswith(sfx): s = s[:-len(sfx)]
    if "/USDT:USDT" in s: return s
    if "/" in s and ":USDT" not in s: return s.replace("/USDT", "/USDT:USDT")
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
                   atr_sl=None, session=None, fund_mom=None, tier=None,
                   rr_ratio=None, tp_pct=None, btc_trend=None):
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bull = "long" in sig.lower() or "buy" in sig.lower()
    src  = "📡 Scanner" if source == "Scanner" else "📊 TradingView"
    re_ic = {"POSITIVE GAMMA": "🟢", "NEGATIVE GAMMA": "🔴", "FLIP ZONE": "🟡"}.get(regime, "⚪")

    extras = ""
    if sweep:               extras += f"\n⚡ <b>LIQUIDITY SWEEP</b> — stop hunt detectado"
    if zscore is not None:  extras += f"\n📊 Z-Score: <b>{zscore}</b>"
    if whale:               extras += f"\n🐋 Ballena | CVD: <b>{cvd}%</b>"
    if absorb:              extras += f"\n🧲 Absorción detectada"
    if mtf is not None:     extras += f"\n📐 Confluencia MTF: <b>{mtf*100:.0f}%</b>"
    if ai_conf is not None: extras += f"\n🧠 Confianza IA: <b>{ai_conf*100:.0f}%</b>"
    if hurst is not None:
        hl = "Trending" if hurst > HURST_TREND_MIN else ("Mean-Rev" if hurst < HURST_REVERT_MAX else "Neutral")
        extras += f"\n📉 Hurst: <b>{hurst:.3f}</b> ({hl})"
    if fg is not None:      extras += f"\n😱 Fear&Greed: <b>{fg}</b> ({_fg_cache['label']})"
    if oi_delta is not None:extras += f"\n📦 OI Delta: <b>{oi_delta:+.2%}</b>"
    if atr_sl is not None:  extras += f"\n🛡️ SL: <b>{atr_sl*100:.2f}%</b> (ATR-based)"
    if session is not None: extras += f"\n🕐 Sesión: <b>{session}</b>"
    if rr_ratio is not None:extras += f"\n⚖️ R:R ratio: <b>{rr_ratio:.1f}:1</b>"
    if tp_pct is not None:  extras += f"\n🎯 TP objetivo: <b>{tp_pct*100:.2f}%</b>"
    if btc_trend is not None:
        bt_icon = "🟢" if btc_trend > 0 else ("🔴" if btc_trend < 0 else "⚪")
        extras += f"\n{bt_icon} BTC trend: <b>{'ALCISTA' if btc_trend > 0 else ('BAJISTA' if btc_trend < 0 else 'LATERAL')}</b>"
    if tier:               extras += f"\n🏆 Tier: <b>{tier}</b>"
    if fund_mom is not None and abs(fund_mom) > 0.0002:
        extras += f"\n💰 Funding trend: <b>{fund_mom:+.4%}</b>"

    if error:
        return (f"⚠️ <b>AEGIS v8 — ERROR</b>\n────────────────\n"
                f"🕒 {ts}\n📈 {sym} | {sig.upper()}\n❌ <code>{error}</code>")

    status = "✅ LIVE" if (order and not order.get("paper") and MODE == "live") else "📋 PAPER"
    oid    = (order or {}).get("id", "—")
    size   = round(calc_order_size(), 2)
    wr_pct = (_stats["total_wins"] / max(1, _stats["total_wins"] + _stats["total_losses"])) * 100

    return (
        f"{'🟢' if bull else '🔴'} <b>AEGIS v8 — {sig.upper()}</b>\n"
        f"────────────────────\n"
        f"🕒 {ts} | {src}\n"
        f"📈 <b>{sym}</b> @ <b>{price}</b>\n"
        f"💡 {stype}{extras}\n"
        f"────────────────────\n"
        f"{re_ic} Régimen: <b>{regime}</b> | DGRP: <b>{score}/100</b>\n"
        f"────────────────────\n"
        f"📦 {status} | ID: <code>{oid}</code>\n"
        f"⚙️ Size: ${size} | Lev: {LEVERAGE}x | Riesgo: ${ACCOUNT_BALANCE*RISK_PER_TRADE:.2f}\n"
        f"💼 Pos: {len(_open_trades)}/{MAX_OPEN_TRADES} | Hoy: {_daily_trade_count}/{MAX_DAILY_TRADES} trades\n"
        f"📊 PnL día: {_daily_pnl:+.2f}% | WR sesión: {wr_pct:.0f}%"
    )

# ═══════════════════════════════════════════════════════════════
#   GESTIÓN DE RIESGO v8 — Múltiples capas
# ═══════════════════════════════════════════════════════════════

def _reset_daily():
    global _daily_pnl, _daily_date, _daily_trade_count, _consecutive_losses
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _daily_date != today:
        if _daily_date:
            log.info(f"[RIESGO] Nuevo día. PnL ayer: {_daily_pnl:+.2f}%")
            wr = (_stats["total_wins"] / max(1, _stats["total_wins"] + _stats["total_losses"])) * 100
            send_telegram(
                f"📅 <b>Nuevo día AEGIS v8</b>\n"
                f"PnL ayer: <b>{_daily_pnl:+.2f}%</b>\n"
                f"Trades ayer: <b>{_daily_trade_count}</b> | WR sesión: <b>{wr:.0f}%</b>\n"
                f"Bloqueados: rr={_stats['rr_blocks']} btc={_stats['btc_macro_blocks']} "
                f"fees={_stats['fee_blocks']} lim={_stats['daily_limit_blocks']}"
            )
        _daily_pnl       = 0.0
        _daily_date      = today
        _daily_trade_count = 0
        _consecutive_losses = 0  # reset al nuevo día

def circuit_breaker() -> tuple[bool, str]:
    _reset_daily()
    if _daily_pnl <= -(MAX_DAILY_LOSS * 100):
        return False, f"Circuit breaker: pérdida {_daily_pnl:.2f}%"
    if len(_open_trades) >= MAX_OPEN_TRADES:
        return False, f"Máx posiciones ({MAX_OPEN_TRADES})"
    if _daily_trade_count >= MAX_DAILY_TRADES:
        _stats["daily_limit_blocks"] += 1
        return False, f"Límite diario ({MAX_DAILY_TRADES} trades/día)"
    if _consecutive_losses >= 3:
        _stats["consec_loss_blocks"] += 1
        return False, f"Racha negativa: {_consecutive_losses} pérdidas seguidas — pausa automática"
    return True, ""

def is_blacklisted(sym: str) -> bool:
    """Comprueba si el símbolo está en lista negra por pérdidas consecutivas."""
    if sym not in _symbol_blacklist:
        return False
    if time.time() > _symbol_blacklist[sym]:
        del _symbol_blacklist[sym]
        _symbol_consec_losses[sym] = 0
        log.info(f"[BLACKLIST] {sym}: ban expirado, rehabilitado")
        return False
    _stats["blacklist_blocks"] += 1
    return True

def register_trade_result(sym: str, won: bool):
    """Actualiza estadísticas de victorias/pérdidas y blacklist."""
    global _consecutive_losses
    if won:
        _stats["total_wins"] += 1
        _symbol_consec_losses[sym] = 0
        _consecutive_losses = 0
    else:
        _stats["total_losses"] += 1
        _symbol_consec_losses[sym] = _symbol_consec_losses.get(sym, 0) + 1
        _consecutive_losses += 1
        if _symbol_consec_losses[sym] >= BLACKLIST_MAX_CONSEC_LOSSES:
            _symbol_blacklist[sym] = time.time() + BLACKLIST_DURATION_SECS
            log.warning(f"[BLACKLIST] {sym}: {BLACKLIST_MAX_CONSEC_LOSSES} pérdidas consecutivas → baneado {BLACKLIST_DURATION_SECS//60}min")
            send_telegram(
                f"⛔ <b>AUTO-BLACKLIST</b>\n"
                f"<b>{sym}</b> baneado por {BLACKLIST_DURATION_SECS//60} min\n"
                f"Motivo: {_symbol_consec_losses[sym]} pérdidas consecutivas"
            )

def calc_order_size() -> float:
    dollar_risk = ACCOUNT_BALANCE * RISK_PER_TRADE
    stop_pct    = ATR_SL_MIN_PCT
    nominal     = dollar_risk / stop_pct
    margin      = nominal / LEVERAGE
    max_margin  = ACCOUNT_BALANCE * 0.08  # máx 8% del balance como margen
    return round(min(margin, max_margin) * LEVERAGE, 2)

def check_fee_worthiness(atr_sl_pct: float, price: float) -> tuple[bool, float]:
    """
    Verifica que el trade sea rentable después de fees.
    Fee BingX ≈ 0.05% por lado → 0.10% ida+vuelta (sin contar liquidaciones)
    Con leverage 3x, el coste real es mayor en términos de margen.
    Retorna (es_rentable, ratio_ganancia_esperada_vs_fees)
    """
    tp_pct = atr_sl_pct * MIN_RR_RATIO  # TP esperado
    round_trip_fee = FEE_PCT * 2  # entrada + salida
    effective_fee  = round_trip_fee / LEVERAGE  # en términos de precio
    ratio = tp_pct / (effective_fee + 1e-10)
    return ratio >= FEE_MULTIPLIER, ratio

def can_send_signal(sym: str, sig: str) -> bool:
    now = time.time()
    if now - _last_signal_time.get(sym, 0) < SIGNAL_COOLDOWN: return False
    if _last_signal_val.get(sym) == sig: return False
    return True

def register_signal(sym: str, sig: str):
    global _daily_trade_count
    _last_signal_time[sym] = time.time()
    _last_signal_val[sym]  = sig
    _daily_trade_count += 1

# ═══════════════════════════════════════════════════════════════
#   BTC MACRO TREND FILTER — NUEVO v8
# ═══════════════════════════════════════════════════════════════

async def update_btc_trend():
    """
    Determina la tendencia macro de BTC en 1h.
    Solo operamos alts EN DIRECCIÓN de BTC o en BTC/ETH directamente.
    Sin esto, los alts sufren "muerte por correlación": caen más que BTC en bajadas.
    """
    global _btc_trend
    try:
        ohlcv = await exchange_async.fetch_ohlcv("BTC/USDT:USDT", "1h", limit=50)
        if len(ohlcv) < 30:
            return
        closes = [x[4] for x in ohlcv]
        highs  = [x[2] for x in ohlcv]
        lows   = [x[3] for x in ohlcv]

        # EMA 20 vs EMA 50
        e20 = ema(closes, 20)
        e50 = ema(closes, 50)
        e20_val = e20[-1] or 0
        e50_val = e50[-1] or 0

        # ADX simplificado para fuerza de tendencia
        atr_v = atr(highs, lows, closes, 14)
        atr_last = next((v for v in reversed(atr_v) if v is not None), 0)

        price = closes[-1]
        trend_dir = 0
        trend_strength = 0.0

        if e20_val > e50_val * 1.002 and price > e20_val:
            trend_dir = 1   # alcista
            trend_strength = (e20_val - e50_val) / e50_val
        elif e20_val < e50_val * 0.998 and price < e20_val:
            trend_dir = -1  # bajista
            trend_strength = (e50_val - e20_val) / e50_val
        else:
            trend_dir = 0   # lateral — evitar operar alts
            trend_strength = 0.0

        _btc_trend = {
            "dir": trend_dir,
            "strength": round(trend_strength, 5),
            "ts": time.time(),
            "price": price,
        }
        log.info(f"[BTC_MACRO] trend={'BULL' if trend_dir==1 else ('BEAR' if trend_dir==-1 else 'FLAT')} "
                 f"strength={trend_strength:.4%} price={price:.2f}")
    except Exception as e:
        log.warning(f"[BTC_MACRO] {e}")

def check_btc_macro(sym: str, sig_dir: int) -> tuple[bool, str]:
    """
    Para alts (no BTC/ETH), requiere que la señal vaya en dirección de BTC macro.
    BTC/ETH pueden operar en cualquier dirección.
    """
    if not BTC_MACRO_FILTER:
        return True, ""
    if sym in TIER1_SYMBOLS:
        return True, ""  # BTC y ETH operan libremente
    if time.time() - _btc_trend.get("ts", 0) > 3600:
        return True, ""  # datos viejos → no bloquear
    btc_dir = _btc_trend.get("dir", 0)
    if btc_dir == 0:
        _stats["btc_macro_blocks"] += 1
        return False, "BTC lateral — esperar tendencia clara"
    if btc_dir != sig_dir:
        _stats["btc_macro_blocks"] += 1
        return False, f"BTC {'ALCISTA' if btc_dir==1 else 'BAJISTA'} pero señal va en contra"
    return True, ""

# ═══════════════════════════════════════════════════════════════
#   INDICADORES CLÁSICOS
# ═══════════════════════════════════════════════════════════════

def ema(data, p):
    out = [None] * len(data)
    k = 2 / (p + 1)
    for i in range(p - 1, len(data)):
        out[i] = sum(data[i - p + 1:i + 1]) / p if i == p - 1 else data[i] * k + out[i - 1] * (1 - k)
    return out

def atr(highs, lows, closes, p=14):
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
           for i in range(1, len(closes))]
    out = [None] * len(trs)
    if len(trs) >= p:
        out[p - 1] = sum(trs[:p]) / p
        for i in range(p, len(trs)):
            out[i] = (out[i-1] * (p - 1) + trs[i]) / p
    return out

def bollinger(closes, p=20, mult=2.0):
    if len(closes) < p: return None, None, None
    w = closes[-p:]; mid = sum(w) / p
    std = (sum((x - mid) ** 2 for x in w) / p) ** 0.5
    return mid + mult * std, mid, mid - mult * std

def vwap(highs, lows, closes, volumes):
    tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    tot = sum(volumes)
    return sum(t * v for t, v in zip(tp, volumes)) / tot if tot > 0 else closes[-1]

def rsi(closes, p=14):
    if len(closes) < p + 1: return 50.0
    gs, ls = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]; gs.append(max(d, 0)); ls.append(max(-d, 0))
    ag = sum(gs[-p:]) / p; al = sum(ls[-p:]) / p
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 2)

def adx_simple(highs, lows, closes, p=14) -> float:
    """ADX simplificado para medir fuerza de tendencia."""
    if len(closes) < p + 2: return 0.0
    atr_v = atr(highs, lows, closes, p)
    if not any(v is not None for v in atr_v): return 0.0
    dm_plus  = [max(highs[i] - highs[i-1], 0) for i in range(1, len(highs))]
    dm_minus = [max(lows[i-1] - lows[i], 0) for i in range(1, len(lows))]
    di_plus_raw  = [dp / (av + 1e-10) for dp, av in zip(dm_plus, atr_v) if av is not None]
    di_minus_raw = [dm / (av + 1e-10) for dm, av in zip(dm_minus, atr_v) if av is not None]
    if not di_plus_raw: return 0.0
    di_plus  = sum(di_plus_raw[-p:])  / p * 100
    di_minus = sum(di_minus_raw[-p:]) / p * 100
    dx = abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10) * 100
    return round(dx, 1)

def dgrp(atr_vals):
    valid = [x for x in atr_vals if x is not None]
    if len(valid) < 28: return "FLIP ZONE", 50
    avg = sum(valid[-28:]) / 28; ratio = valid[-1] / avg if avg > 0 else 1.0
    score = int(min(100, max(0, (ratio - 0.4) / 1.6 * 100)))
    if score < DGRP_POS_MAX:   return "POSITIVE GAMMA", score
    elif score > DGRP_NEG_MIN: return "NEGATIVE GAMMA", score
    return "FLIP ZONE", score

def cvd_whale(opens, closes, volumes, lb=20):
    if len(closes) < lb + 1: return 0.0, False
    rec = list(zip(opens[-lb:], closes[-lb:], volumes[-lb:]))
    bv  = sum(v for o, c, v in rec if c >= o)
    sv  = sum(v for o, c, v in rec if c < o)
    tot = bv + sv
    if tot == 0: return 0.0, False
    imb = abs(bv - sv) / tot
    avgv = sum(volumes[-lb-1:-1]) / lb
    return round(imb * 100, 1), imb >= CVD_THRESHOLD and volumes[-1] > avgv * WHALE_MULT

def zscore_val(closes, w=None):
    w = w or ZSCORE_WINDOW
    if len(closes) < w + 1: return None
    sub = closes[-(w+1):-1]; mean = sum(sub) / w
    std = (sum((x - mean) ** 2 for x in sub) / w) ** 0.5
    return None if std == 0 else round((closes[-1] - mean) / std, 3)

def absorption(highs, lows, closes, volumes, lb=10):
    if len(closes) < lb + 1: return False, ""
    avgv = sum(volumes[-(lb+1):-1]) / lb
    if volumes[-1] < avgv * ABSORPTION_VOL_MULT: return False, ""
    move = (highs[-1] - lows[-1]) / closes[-1] if closes[-1] > 0 else 99
    if move >= ABSORPTION_MOVE_PCT: return False, ""
    return True, "alcista" if closes[-1] >= closes[-2] else "bajista"

# ═══════════════════════════════════════════════════════════════
#   SIMONS — Indicadores estadísticos
# ═══════════════════════════════════════════════════════════════

def hurst_exponent(closes, max_lag=None) -> float:
    max_lag = max_lag or min(HURST_WINDOW, len(closes) // 2)
    if len(closes) < max_lag * 2: return 0.5
    lags = range(2, max_lag)
    tau = [max(1e-10, (sum((closes[i] - closes[i-lag]) ** 2 for i in range(lag, len(closes)))
                       / (len(closes) - lag)) ** 0.5) for lag in lags]
    if not tau: return 0.5
    llags = [math.log(l) for l in lags]; ltau = [math.log(t) for t in tau]
    n = len(lags); mx = sum(llags) / n; my = sum(ltau) / n
    num = sum((x - mx) * (y - my) for x, y in zip(llags, ltau))
    den = sum((x - mx) ** 2 for x in llags)
    return round((num / den) / 2, 4) if den > 0 else 0.5

def autocorr_lag1(closes, n=20) -> float:
    if len(closes) < n + 2: return 0.0
    r = [(closes[i] - closes[i-1]) / (closes[i-1] + 1e-10) for i in range(-n-1, 0)]
    r0 = r[:-1]; r1 = r[1:]
    m0 = sum(r0) / len(r0); m1 = sum(r1) / len(r1)
    num = sum((a - m0) * (b - m1) for a, b in zip(r0, r1))
    d0  = (sum((a - m0) ** 2 for a in r0)) ** 0.5
    d1  = (sum((b - m1) ** 2 for b in r1)) ** 0.5
    return round(num / (d0 * d1 + 1e-9), 4)

def returns_moments(closes, n=30) -> tuple[float, float, float]:
    if len(closes) < n + 1: return 0.0, 0.0, 3.0
    r = [(closes[i] - closes[i-1]) / (closes[i-1] + 1e-10) for i in range(-n, 0)]
    m = sum(r) / len(r)
    std = (sum((x - m) ** 2 for x in r) / len(r)) ** 0.5
    if std < 1e-9: return std, 0.0, 3.0
    skew = sum((x - m) ** 3 for x in r) / (len(r) * std ** 3)
    kurt = sum((x - m) ** 4 for x in r) / (len(r) * std ** 4)
    return round(std, 6), round(skew, 3), round(kurt, 3)

# ═══════════════════════════════════════════════════════════════
#   ⚡ LIQUIDITY SWEEP DETECTOR — v8 más estricto
# ═══════════════════════════════════════════════════════════════

def detect_liquidity_sweep(opens, highs, lows, closes, volumes, lb=20) -> tuple[bool, str, float]:
    if len(closes) < lb + 3: return False, "", 0.0
    candle_range = highs[-1] - lows[-1]
    if candle_range < 1e-8: return False, "", 0.0

    body       = abs(closes[-1] - opens[-1])
    upper_wick = highs[-1] - max(closes[-1], opens[-1])
    lower_wick = min(closes[-1], opens[-1]) - lows[-1]
    body_ratio = body / candle_range

    avg_vol   = sum(volumes[-(lb+1):-1]) / lb
    vol_ratio = volumes[-1] / (avg_vol + 1e-10)

    recent_low  = min(lows[-(lb+1):-1])
    recent_high = max(highs[-(lb+1):-1])

    # v8: Requiere que TAMBIÉN la vela anterior confirme (no viene de caída libre)
    prev_body    = abs(closes[-2] - opens[-2])
    prev_range   = highs[-2] - lows[-2]
    prev_healthy = prev_body > prev_range * 0.1 if prev_range > 0 else True

    strength  = 0.0
    direction = ""

    lower_wick_ratio = lower_wick / candle_range
    if (lower_wick_ratio >= SWEEP_WICK_RATIO and
            body_ratio <= SWEEP_BODY_MAX and
            vol_ratio  >= SWEEP_VOL_MULT and
            lows[-1]   <= recent_low * 1.0005 and
            closes[-1] > (highs[-1] + lows[-1]) / 2 and
            prev_healthy):
        strength  = min(1.0, (lower_wick_ratio - SWEEP_WICK_RATIO) * 3 + (vol_ratio - SWEEP_VOL_MULT) * 0.2)
        direction = "alcista"

    upper_wick_ratio = upper_wick / candle_range
    if (upper_wick_ratio >= SWEEP_WICK_RATIO and
            body_ratio <= SWEEP_BODY_MAX and
            vol_ratio  >= SWEEP_VOL_MULT and
            highs[-1]  >= recent_high * 0.9995 and
            closes[-1] < (highs[-1] + lows[-1]) / 2 and
            prev_healthy):
        strength  = min(1.0, (upper_wick_ratio - SWEEP_WICK_RATIO) * 3 + (vol_ratio - SWEEP_VOL_MULT) * 0.2)
        direction = "bajista"

    if direction:
        log.info(f"[SWEEP] ⚡ {direction.upper()} | wick={lower_wick_ratio if direction=='alcista' else upper_wick_ratio:.2%} "
                 f"| body={body_ratio:.2%} | vol={vol_ratio:.1f}x | strength={strength:.2f}")
    return bool(direction), direction, round(strength, 3)

# ═══════════════════════════════════════════════════════════════
#   ATR DYNAMIC STOP LOSS + R:R CALCULATOR — v8
# ═══════════════════════════════════════════════════════════════

def calc_atr_sl(atr_vals, price: float) -> float:
    valid = [x for x in atr_vals if x is not None]
    if not valid or price <= 0: return 0.015
    atr_val = valid[-1]
    sl_dist = (atr_val * ATR_SL_MULT) / price
    return round(max(ATR_SL_MIN_PCT, min(ATR_SL_MAX_PCT, sl_dist)), 5)

def calc_atr_trail(atr_vals, price: float) -> float:
    valid = [x for x in atr_vals if x is not None]
    if not valid or price <= 0: return 0.010
    return round(max(0.004, min(0.025, (valid[-1] * ATR_TRAIL_MULT) / price)), 5)

def calc_rr_ratio(atr_sl_pct: float, target_mult: float = 2.0) -> tuple[float, float]:
    """Calcula TP y R:R ratio dado el SL en ATR."""
    tp_pct = atr_sl_pct * target_mult
    return round(tp_pct, 5), round(target_mult, 2)

def verify_rr_viable(atr_sl_pct: float, closes: list, highs: list, lows: list,
                     sig_dir: int) -> tuple[bool, float, float]:
    """
    Verifica que el R:R sea viable dado el contexto del mercado.
    Comprueba que no haya resistencia/soporte fuerte bloqueando el TP.
    Retorna (viable, tp_pct, rr_ratio)
    """
    tp_pct = atr_sl_pct * MIN_RR_RATIO
    # Verificación básica: el TP mínimo supera los fees
    fee_ok, fee_ratio = check_fee_worthiness(atr_sl_pct, closes[-1])
    if not fee_ok:
        _stats["fee_blocks"] += 1
        return False, tp_pct, 0.0
    # Si hay nivel de resistencia cercano (< 50% del TP), reducir ratio
    # Simplificado: verificar max/min de las últimas 20 velas
    if len(closes) >= 20:
        if sig_dir == 1:  # long → resistencia en máximos recientes
            resistance = max(highs[-20:-1])
            dist_to_res = (resistance - closes[-1]) / closes[-1]
            if dist_to_res < tp_pct * 0.5:
                return False, tp_pct, dist_to_res / atr_sl_pct
        else:  # short → soporte en mínimos recientes
            support = min(lows[-20:-1])
            dist_to_sup = (closes[-1] - support) / closes[-1]
            if dist_to_sup < tp_pct * 0.5:
                return False, tp_pct, dist_to_sup / atr_sl_pct
    return True, tp_pct, MIN_RR_RATIO

# ═══════════════════════════════════════════════════════════════
#   SESSION FILTER
# ═══════════════════════════════════════════════════════════════

def get_session_info() -> tuple[str, float]:
    hour = datetime.now(timezone.utc).hour
    if LONDON_OPEN_START <= hour < LONDON_OPEN_END:
        return f"🇬🇧 London Open ({hour}h UTC)", -0.05
    if NY_OPEN_START <= hour < NY_OPEN_END:
        return f"🇺🇸 NY Open ({hour}h UTC)", -0.05
    if ASIA_OPEN_START <= hour < ASIA_OPEN_END:
        return f"🇯🇵 Asia Open ({hour}h UTC)", 0.0
    return f"😴 Off-Session ({hour}h UTC)", OFF_SESSION_PENALTY

# ═══════════════════════════════════════════════════════════════
#   CORRELATION GUARD
# ═══════════════════════════════════════════════════════════════

def pearson_corr(a: list, b: list) -> float:
    n = min(len(a), len(b))
    if n < 5: return 0.0
    a, b = a[-n:], b[-n:]
    ma = sum(a) / n; mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da  = (sum((x - ma) ** 2 for x in a)) ** 0.5
    db  = (sum((y - mb) ** 2 for y in b)) ** 0.5
    return round(num / (da * db + 1e-9), 3)

def check_correlation_guard(sym: str, sig_dir: int) -> tuple[bool, str]:
    if not _open_trades: return True, ""
    new_closes = _price_cache.get(sym, [])
    if not new_closes: return True, ""
    for open_sym in _open_trades:
        open_closes = _price_cache.get(open_sym, [])
        if not open_closes: continue
        n = min(len(new_closes), len(open_closes), CORR_WINDOW)
        if n < 5: continue
        r_new  = [(new_closes[i] - new_closes[i-1]) / (new_closes[i-1] + 1e-10) for i in range(-n, 0)]
        r_open = [(open_closes[i] - open_closes[i-1]) / (open_closes[i-1] + 1e-10) for i in range(-n, 0)]
        corr   = pearson_corr(r_new, r_open)
        open_side = _trailing_state.get(open_sym, {}).get("side", "")
        open_dir  = 1 if open_side == "long" else -1
        if corr > CORR_MAX and open_dir == sig_dir:
            _stats["corr_blocks"] += 1
            return False, f"Correlación {corr:.2f} con {open_sym}"
    return True, ""

# ═══════════════════════════════════════════════════════════════
#   FUNDING RATE MOMENTUM
# ═══════════════════════════════════════════════════════════════

def funding_momentum(sym: str) -> float:
    hist = list(_funding_hist[sym])
    if len(hist) < 2: return 0.0
    n = len(hist); x = list(range(n))
    mx = sum(x) / n; my = sum(hist) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, hist))
    den = sum((xi - mx) ** 2 for xi in x)
    return round(num / (den + 1e-12), 6)

async def update_funding_hist(sym: str):
    try:
        fr   = await exchange_async.fetch_funding_rate(sym)
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
        fg_label = resp["data"][0]["value_classification"]
        bias    = 1 if fg_val < FG_EXTREME_FEAR else (-1 if fg_val > FG_EXTREME_GREED else 0)
        if fg_val < FG_EXTREME_FEAR:
            _mtf_conf_dyn = max(0.55, MTF_CONFLUENCE_MIN - 0.08)
        elif fg_val > FG_EXTREME_GREED:
            _mtf_conf_dyn = min(0.85, MTF_CONFLUENCE_MIN + 0.12)
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
#   LSTM MINI — v8 con 10 features
# ═══════════════════════════════════════════════════════════════

class MiniLSTM:
    def __init__(self, inp=10, hid=14):
        self.hid = hid; self.inp = inp; self._init()

    def _init(self):
        def X(r, c):
            sc = (2 / (r + c)) ** 0.5
            return [[random.gauss(0, sc) for _ in range(c)] for _ in range(r)]
        h, i = self.hid, self.inp
        self.Wf = X(h, i+h); self.bf = [0.1] * h
        self.Wi = X(h, i+h); self.bi = [0.0] * h
        self.Wg = X(h, i+h); self.bg = [0.0] * h
        self.Wo = X(h, i+h); self.bo = [0.5] * h
        self.Wy = X(1, h);   self.by = [0.0]

    @staticmethod
    def _s(x): return 1 / (1 + math.exp(-max(-20, min(20, x))))
    @staticmethod
    def _t(x): return math.tanh(max(-20, min(20, x)))

    def _mm(self, W, x, b):
        return [b[i] + sum(W[i][j] * x[j] for j in range(len(x))) for i in range(len(b))]

    def forward(self, seq):
        h = [0.0] * self.hid; c = [0.0] * self.hid
        for x in seq:
            xh = x + h
            f  = [self._s(v) for v in self._mm(self.Wf, xh, self.bf)]
            ig = [self._s(v) for v in self._mm(self.Wi, xh, self.bi)]
            g  = [self._t(v) for v in self._mm(self.Wg, xh, self.bg)]
            o  = [self._s(v) for v in self._mm(self.Wo, xh, self.bo)]
            c  = [f[j] * c[j] + ig[j] * g[j] for j in range(self.hid)]
            h  = [o[j] * self._t(c[j]) for j in range(self.hid)]
        return self._s(self.by[0] + sum(self.Wy[0][j] * h[j] for j in range(self.hid)))

    def train(self, seq, target, lr=0.01):
        p = self.forward(seq); e = p - target
        for j in range(self.hid): self.Wy[0][j] -= lr * e * p * (1 - p)
        self.by[0] -= lr * e * p * (1 - p)
        return abs(e)

class MiniRF:
    class MiniTree:
        def __init__(self, depth=4): self.d = depth; self.tree = None
        def fit(self, X, y): self.tree = self._b(X, y, 0)
        def _b(self, X, y, dep):
            if not X or dep >= self.d or len(set(str(v) for v in y)) == 1:
                return sum(y) / len(y) if y else 0.5
            bf = bv = None; bs = float("inf")
            for f in range(len(X[0])):
                for v in sorted(set(r[f] for r in X))[1:]:
                    ly = [y[i] for i, r in enumerate(X) if r[f] < v]
                    ry = [y[i] for i, r in enumerate(X) if r[f] >= v]
                    if not ly or not ry: continue
                    sc = self._g(ly) * len(ly) + self._g(ry) * len(ry)
                    if sc < bs: bs, bf, bv = sc, f, v
            if bf is None: return sum(y) / len(y)
            lX = [r for r in X if r[bf] < bv]; rX = [r for r in X if r[bf] >= bv]
            lY = [y[i] for i, r in enumerate(X) if r[bf] < bv]; rY = [y[i] for i, r in enumerate(X) if r[bf] >= bv]
            return {"f": bf, "v": bv, "l": self._b(lX, lY, dep+1), "r": self._b(rX, rY, dep+1)}
        def _g(self, y):
            if not y: return 0; p = sum(y) / len(y); return 1 - p*p - (1-p)**2
        def predict(self, x):
            n = self.tree
            while isinstance(n, dict): n = n["l"] if x[n["f"]] < n["v"] else n["r"]
            return n

    def __init__(self, n=12, d=4): self.trees = [self.MiniTree(d) for _ in range(n)]; self.ok = False
    def fit(self, X, y):
        n = len(X)
        for t in self.trees:
            idx = [random.randint(0, n-1) for _ in range(n)]
            t.fit([X[i] for i in idx], [y[i] for i in idx])
        self.ok = True
    def predict(self, x):
        if not self.ok: return ZSCORE_THRESHOLD
        ps = [t.predict(x) for t in self.trees if t.tree is not None]
        return max(2.0, min(4.0, sum(ps) / len(ps))) if ps else ZSCORE_THRESHOLD

_lstm = MiniLSTM(inp=10, hid=14)
_rf   = MiniRF(n=12, d=4)

def _features(closes, volumes, hour, hurst_val=0.5, kurt_val=3.0,
              sweep_strength=0.0, fund_mom=0.0, session_bonus=0.0, btc_dir=0):
    if len(closes) < 10 or len(volumes) < 10: return None
    pc  = [(closes[i] - closes[i-1]) / (closes[i-1] + 1e-10) for i in range(-5, 0)]
    vm  = sum(volumes[-10:]) / 10
    vn  = [volumes[i] / (vm + 1e-10) for i in range(-5, 0)]
    fg_norm   = _fg_cache["value"] / 100.0
    kurt_norm = min(1.0, kurt_val / 10.0)
    btc_norm  = (btc_dir + 1) / 2.0  # -1→0, 0→0.5, 1→1
    # 10 features por timestep
    return [[p, v, hour / 23.0, fg_norm, hurst_val, kurt_norm,
             sweep_strength, min(1.0, abs(fund_mom) * 1000),
             -session_bonus, btc_norm]
            for p, v in zip(pc, vn)]

def lstm_confidence(closes, volumes, hour, hurst_val=0.5, kurt_val=3.0,
                    sweep_strength=0.0, fund_mom=0.0, session_bonus=0.0, btc_dir=0) -> float:
    seq = _features(closes, volumes, hour, hurst_val, kurt_val, sweep_strength, fund_mom, session_bonus, btc_dir)
    if seq is None: return 0.5
    try: return _lstm.forward(seq)
    except: return 0.5

def update_ai(success, z_entry, vol_mult, hour, mtf_score, funding, ob_imb,
              hurst_val=0.5, kurt_val=3.0, sweep_strength=0.0, fund_mom=0.0, btc_dir=0):
    global _ai_z_threshold, _ai_model_ready
    _ai_memory.append({
        "ok": 1 if success else 0, "z": z_entry, "vol": vol_mult,
        "hr": hour, "mtf": mtf_score, "fund": funding, "ob": ob_imb,
        "hurst": hurst_val, "kurt": min(kurt_val, 10.0),
        "sweep": sweep_strength, "fmom": fund_mom, "btc": btc_dir,
    })
    closes_p = [1.0 + random.gauss(0, 0.001) for _ in range(10)]
    vols_p   = [vol_mult] * 10
    seq = _features(closes_p, vols_p, hour, hurst_val, kurt_val, sweep_strength, fund_mom, btc_dir=btc_dir)
    if seq: _lstm.train(seq, float(success))
    if len(_ai_memory) < 20: return
    data = list(_ai_memory)
    X = [[d["hr"]/23.0, d["vol"]/5.0, d["mtf"], d["fund"]*100, d["ob"],
          d.get("hurst", 0.5), min(d.get("kurt", 3.0), 10.0)/10.0,
          d.get("sweep", 0.0), min(abs(d.get("fmom", 0.0))*1000, 1.0),
          (d.get("btc", 0)+1)/2.0] for d in data]
    y = [d["z"] * d["ok"] for d in data]
    try:
        _rf.fit(X, y)
        hr   = datetime.now(timezone.utc).hour
        last = data[-1]
        _ai_z_threshold = _rf.predict([
            hr/23.0, last["vol"]/5.0, last["mtf"], last["fund"]*100, last["ob"],
            last.get("hurst", 0.5), min(last.get("kurt", 3.0), 10.0)/10.0,
            last.get("sweep", 0.0), min(abs(last.get("fmom", 0.0))*1000, 1.0),
            (last.get("btc", 0)+1)/2.0,
        ])
        _ai_model_ready = True
        log.info(f"[IA] Retrain OK. Z_thr={_ai_z_threshold:.3f}")
    except Exception as e:
        log.error(f"[IA] {e}")

# ═══════════════════════════════════════════════════════════════
#   EJECUCIÓN SMART v8
# ═══════════════════════════════════════════════════════════════

async def smart_order(sym: str, side: str, size: float, atr_sl_pct: float = 0.015) -> dict | None:
    if MODE != "live":
        try:
            t = await exchange_async.fetch_ticker(sym); p = t["last"]
        except: p = 0.0
        log.info(f"[PAPER] {side.upper()} ${size} {sym} @ {p:.4f} | SL={atr_sl_pct*100:.2f}%")
        return {"id": f"PAPER-{datetime.now().strftime('%H%M%S')}", "paper": True,
                "average": p, "price": p, "atr_sl_pct": atr_sl_pct}
    try:
        lev_task = exchange_async.set_leverage(LEVERAGE, sym)
        ob_task  = exchange_async.fetch_order_book(sym, limit=5)
        res      = await asyncio.gather(lev_task, ob_task, return_exceptions=True)
        ob = res[1] if not isinstance(res[1], Exception) else await exchange_async.fetch_order_book(sym, limit=5)

        best_bid = ob["bids"][0][0]; best_ask = ob["asks"][0][0]
        spread   = (best_ask - best_bid) / best_bid

        if spread > 0.001:
            log.warning(f"[ORDER] {sym}: spread {spread:.4%} → MARKET directo")
            morder = await exchange_async.create_market_order(sym, side, size)
            morder["atr_sl_pct"] = atr_sl_pct
            return morder

        limit_price = (round(best_bid + (best_ask - best_bid) * 0.35, 4) if side == "buy"
                       else round(best_ask - (best_ask - best_bid) * 0.35, 4))

        log.info(f"[LIMIT] {side.upper()} {sym} @ {limit_price} | SL={atr_sl_pct*100:.2f}%")
        order = await exchange_async.create_limit_order(sym, side, size, limit_price)
        oid   = order["id"]

        for tick in range(LIMIT_WAIT_SECS):
            await asyncio.sleep(1)
            try:
                check = await exchange_async.fetch_order(oid, sym)
                if check["status"] == "closed":
                    check["atr_sl_pct"] = atr_sl_pct
                    return check
            except: pass

        try: await exchange_async.cancel_order(oid, sym)
        except: pass
        morder = await exchange_async.create_market_order(sym, side, size)
        morder["atr_sl_pct"] = atr_sl_pct
        return morder
    except Exception as e:
        log.error(f"[ORDER] {sym} {side}: {e}")
        return None

async def close_position(sym: str, side: str, contracts: float):
    if MODE != "live":
        return {"id": f"PAPER-CLOSE-{datetime.now().strftime('%H%M%S')}", "paper": True}
    try:
        ob = await exchange_async.fetch_order_book(sym, limit=5)
        cs = "sell" if side == "long" else "buy"
        lp = ob["asks"][0][0] if cs == "sell" else ob["bids"][0][0]
        order = await exchange_async.create_limit_order(sym, cs, contracts, round(lp, 4), {"reduceOnly": True})
        await asyncio.sleep(LIMIT_WAIT_SECS)
        check = await exchange_async.fetch_order(order["id"], sym)
        if check["status"] == "closed": return check
        await exchange_async.cancel_order(order["id"], sym)
        return await exchange_async.create_market_order(sym, cs, contracts, {"reduceOnly": True})
    except Exception as e:
        log.error(f"[CLOSE] {sym}: {e}"); return None

# ═══════════════════════════════════════════════════════════════
#   ANÁLISIS POR TIMEFRAME — v8
# ═══════════════════════════════════════════════════════════════

async def analyze_tf(sym: str, tf: str) -> dict | None:
    try:
        ohlcv = await exchange_async.fetch_ohlcv(sym, tf, limit=120)
    except Exception as e:
        log.warning(f"fetch_ohlcv {sym} {tf}: {e}"); return None
    if len(ohlcv) < 60: return None

    opens   = [x[1] for x in ohlcv]
    highs   = [x[2] for x in ohlcv]
    lows    = [x[3] for x in ohlcv]
    closes  = [x[4] for x in ohlcv]
    volumes = [x[5] for x in ohlcv]
    price   = closes[-1]

    if tf == "5m":
        _price_cache[sym] = closes[-CORR_WINDOW-5:]

    atr_v             = atr(highs, lows, closes, 14)
    regime, score     = dgrp(atr_v)
    e9                = ema(closes, 9)
    e21               = ema(closes, 21)
    e50               = ema(closes, 50)  # v8: añadimos EMA50
    bb_up, bb_mid, bb_lo = bollinger(closes, 20, 2.0)
    vw                = vwap(highs, lows, closes, volumes)
    cvd_pct, is_whale = cvd_whale(opens, closes, volumes, 20)
    z                 = zscore_val(closes, ZSCORE_WINDOW)
    abs_ok, abs_dir   = absorption(highs, lows, closes, volumes, 10)
    rsi_v             = rsi(closes, 14)
    adx_v             = adx_simple(highs, lows, closes, 14)  # v8: ADX para fuerza
    atr_sl_pct        = calc_atr_sl(atr_v, price)
    atr_trail_pct     = calc_atr_trail(atr_v, price)

    h_exp             = hurst_exponent(closes, HURST_WINDOW)
    ac                = autocorr_lag1(closes, 20)
    _, skew, kurt     = returns_moments(closes, 30)

    sweep_ok, sweep_dir, sweep_str = detect_liquidity_sweep(opens, highs, lows, closes, volumes, 20)
    if sweep_ok: _stats["sweeps_detected"] += 1

    # Filtro kurtosis (distribución fat-tail = mercado errático)
    if kurt > KURT_MAX:
        _stats["kurt_blocks"] += 1
        return None

    e9p,  e9c  = e9[-2],  e9[-1]
    e21p, e21c = e21[-2], e21[-1]
    e50_val    = e50[-1] or 0
    cp, cc, op = closes[-2], closes[-1], opens[-1]
    atr_last   = atr_v[-1] or 0

    thr = _ai_z_threshold if _ai_model_ready else ZSCORE_THRESHOLD
    signal = stype = None
    extra  = {}
    is_reverting = h_exp < HURST_REVERT_MAX
    is_trending  = h_exp > HURST_TREND_MIN

    # v8: Filtro ADX — momentum signals requieren ADX > 20 (tendencia real)
    trend_strong = adx_v > 20

    # ── PRIORIDAD 0: Liquidity Sweep ─────────────
    if sweep_ok:
        if sweep_dir == "alcista":
            signal, stype = ("sweep_long",
                             f"⚡ Sweep alcista | Fuerza: {sweep_str:.2f} | Stop hunt")
        else:
            signal, stype = ("sweep_short",
                             f"⚡ Sweep bajista | Fuerza: {sweep_str:.2f} | Stop hunt")
        extra.update({"sweep": True, "sweep_strength": sweep_str, "sweep_dir": sweep_dir})

    # ── 1. Z-Score (mean-reverting) ───────────────
    if signal is None and z is not None and abs(z) >= thr and (is_reverting or not is_trending):
        avgv = sum(volumes[-ZSCORE_WINDOW-1:-1]) / ZSCORE_WINDOW
        if volumes[-1] > avgv * 3.5:  # v8: más volumen requerido
            if z < -thr and rsi_v < 38:  # v8: RSI más extremo
                signal, stype = "zscore_long", f"Z-Score {z} | RSI={rsi_v} H={h_exp:.3f}"
            elif z > thr and rsi_v > 62:
                signal, stype = "zscore_short", f"Z-Score {z} | RSI={rsi_v} H={h_exp:.3f}"
            extra["z_score"] = z

    # ── 2. Whale CVD (trending + ADX) ─────────────
    if signal is None and is_whale and trend_strong and (is_trending or not is_reverting):
        bv = sum(v for o, c, v in zip(opens[-20:], closes[-20:], volumes[-20:]) if c >= o)
        sv = sum(v for o, c, v in zip(opens[-20:], closes[-20:], volumes[-20:]) if c < o)
        if bv > sv and rsi_v < 65:  # v8: no entrar en sobrecompra
            signal, stype = "whale_long", f"Ballena alcista CVD={cvd_pct}% ADX={adx_v:.0f}"
        elif sv > bv and rsi_v > 35:
            signal, stype = "whale_short", f"Ballena bajista CVD={cvd_pct}% ADX={adx_v:.0f}"
        extra.update({"whale": True, "cvd_pct": cvd_pct})

    # ── 3. GEX Flip (trending + EMA50 confirma) ───
    if signal is None and all(v is not None for v in [e9p, e9c, e21p, e21c]) and trend_strong:
        if not is_reverting:
            if e9p <= e21p and e9c > e21c and cc > e50_val:
                signal, stype = "gex_flip_cross_long", f"GEX Flip alcista | ADX={adx_v:.0f} H={h_exp:.3f}"
            elif e9p >= e21p and e9c < e21c and cc < e50_val:
                signal, stype = "gex_flip_cross_short", f"GEX Flip bajista | ADX={adx_v:.0f} H={h_exp:.3f}"

    # ── 4. Absorción (solo mean-reverting) ────────
    if signal is None and abs_ok and is_reverting:
        d = "long" if "alcista" in abs_dir else "short"
        signal, stype = f"absorption_{d}", f"Absorción {abs_dir} H={h_exp:.3f}"
        extra["absorption"] = True

    # ── 5. Wall Break VWAP (trending fuerte) ──────
    if signal is None and is_trending and trend_strong:
        if cp < vw and cc > vw * 1.0015 and rsi_v < 65:
            signal, stype = "wall_break_long", f"Wall Break VWAP={vw:.4f}"
        elif cp > vw and cc < vw * 0.9985 and rsi_v > 35:
            signal, stype = "wall_break_short", f"Wall Break VWAP={vw:.4f}"

    sig_is_momentum  = (signal or "") in ("gex_flip_cross_long", "gex_flip_cross_short",
                                          "wall_break_long", "wall_break_short",
                                          "whale_long", "whale_short",
                                          "sweep_long", "sweep_short")
    sig_is_reversion = (signal or "") in ("zscore_long", "zscore_short",
                                          "absorption_long", "absorption_short")
    ac_confirmed = (sig_is_momentum and ac > 0.1) or (sig_is_reversion and ac < -0.1)

    extra.update({
        "hurst": h_exp, "kurt": kurt, "ac_confirmed": ac_confirmed,
        "atr_sl_pct": atr_sl_pct, "atr_trail_pct": atr_trail_pct, "adx": adx_v,
    })

    # v8: bias requiere también EMA50
    bull_bias = (e9c or 0) > (e21c or 0) and cc > vw and cc > e50_val
    bear_bias = (e9c or 0) < (e21c or 0) and cc < vw and cc < e50_val

    return {
        "signal": signal, "stype": stype, "price": price,
        "regime": regime, "score": score, "rsi": rsi_v, "z": z,
        "dir": 1 if bull_bias else (-1 if bear_bias else 0),
        "extra": extra, "hurst": h_exp, "kurt": kurt,
        "atr_sl_pct": atr_sl_pct, "atr_trail_pct": atr_trail_pct,
        "highs": highs, "lows": lows, "closes": closes,
    }

# ═══════════════════════════════════════════════════════════════
#   MULTI-TIMEFRAME ENGINE — v8
# ═══════════════════════════════════════════════════════════════

async def analyze_mtf(sym: str) -> dict | None:
    # v8: 5m, 15m, 1h, 4h en paralelo
    coros   = [analyze_tf(sym, tf) for tf in MTF_TIMEFRAMES]
    raw     = await asyncio.gather(*coros, return_exceptions=True)
    results = {}
    for tf, r in zip(MTF_TIMEFRAMES, raw):
        if r and not isinstance(r, Exception): results[tf] = r

    base = next((results[tf] for tf in MTF_TIMEFRAMES if results.get(tf, {}).get("signal")), None)
    if base is None: return None

    # v8: Verificar R:R ANTES de calcular confluencia (falla rápido)
    atr_sl = base.get("atr_sl_pct", 0.015)
    closes = base.get("closes", [])
    highs  = base.get("highs", [])
    lows   = base.get("lows", [])
    sig_dir = 1 if "long" in base["signal"] else -1
    rr_ok, tp_pct, rr_ratio = verify_rr_viable(atr_sl, closes, highs, lows, sig_dir)
    if not rr_ok:
        _stats["rr_blocks"] += 1
        log.info(f"[RR] {sym}: R:R insuficiente (atr_sl={atr_sl:.3%}, tp={tp_pct:.3%})")
        return None

    wtd = 0.0
    for tf, r in results.items():
        w = MTF_WEIGHTS.get(tf, 0.25)
        if r["dir"] == sig_dir: wtd += w
        elif r.get("signal") and ("long" in r["signal"]) == (sig_dir == 1): wtd += w * 0.7

    # Boosts
    if base["extra"].get("ac_confirmed"):   wtd = min(1.0, wtd * 1.10)
    if base["extra"].get("sweep"):          wtd = min(1.0, wtd * 1.12)
    if base["extra"].get("adx", 0) > 25:   wtd = min(1.0, wtd * 1.05)  # v8: boost ADX fuerte

    # Session penalty/bonus
    session_label, session_penalty = get_session_info()
    conf_min = _mtf_conf_dyn + session_penalty

    # Tier adjustment — v8: Tier3 prácticamente imposible
    tier = _tier_label(sym)
    if tier == "T1":   conf_min += TIER1_CONF_BONUS
    elif tier == "T2":  conf_min += TIER2_CONF_BONUS
    elif tier == "T3":
        if not ALLOW_TIER3:
            _stats["tier_blocks"] += 1
            return None  # bloqueo directo
        conf_min += TIER3_CONF_PENALTY

    if wtd < conf_min:
        if session_penalty > 0: _stats["session_blocks"] += 1
        log.info(f"[MTF] {sym}: {wtd:.2f} < {conf_min:.2f} ({tier}, {session_label})")
        return None

    # Filtro RSI 1h
    rsi_1h = results.get("1h", {}).get("rsi", 50)
    if sig_dir == 1  and rsi_1h > 72: return None
    if sig_dir == -1 and rsi_1h < 28: return None

    # v8: Filtro 4h — alineación obligatoria para momentum
    r4h = results.get("4h", {})
    if r4h and base["signal"] in ("gex_flip_cross_long", "wall_break_long", "whale_long", "sweep_long"):
        if r4h.get("dir", 0) == -1:  # 4h bajista → no abrir long
            log.info(f"[4H] {sym}: 4h bajista, skip long momentum")
            return None
    if r4h and base["signal"] in ("gex_flip_cross_short", "wall_break_short", "whale_short", "sweep_short"):
        if r4h.get("dir", 0) == 1:
            log.info(f"[4H] {sym}: 4h alcista, skip short momentum")
            return None

    # Mejor ATR de 15m o 5m
    atr_sl_final = atr_sl
    atr_tr_final = base.get("atr_trail_pct", 0.010)
    for tf in ["15m", "5m"]:
        if results.get(tf, {}).get("extra", {}).get("atr_sl_pct"):
            atr_sl_final = results[tf]["extra"]["atr_sl_pct"]
            atr_tr_final = results[tf]["extra"]["atr_trail_pct"]
            break

    return {
        "signal": base["signal"], "stype": base["stype"],
        "ticker": sym, "price": f"{base['price']:.4f}",
        "regime": base["regime"], "score": base["score"],
        "mtf_score": wtd, "session": session_label, "tier": tier,
        "hurst": base.get("hurst", 0.5), "kurt": base.get("kurt", 3.0),
        "atr_sl_pct": atr_sl_final, "atr_trail_pct": atr_tr_final,
        "tp_pct": tp_pct, "rr_ratio": rr_ratio,
        **base["extra"],
    }

# ═══════════════════════════════════════════════════════════════
#   PROCESS SIGNAL — v8 con todas las capas de filtrado
# ═══════════════════════════════════════════════════════════════

LONG_SIGS  = {"long", "buy", "wall_break_long", "gex_flip_cross_long", "vanna_unwind_long",
              "compression_break_long", "whale_long", "zscore_long", "absorption_long", "sweep_long"}
SHORT_SIGS = {"short", "sell", "wall_break_short", "gex_flip_cross_short", "vanna_unwind_short",
              "compression_break_short", "whale_short", "zscore_short", "absorption_short", "sweep_short"}

def interp(raw):
    r = raw.lower().strip()
    if r in LONG_SIGS:  return "long"
    if r in SHORT_SIGS: return "short"
    if r == "close":    return "close"
    raise ValueError(f"Señal desconocida: {raw}")

async def process_signal(data: dict, source="Scanner", mtf_score=None) -> dict:
    sym = normalize_symbol(data.get("ticker", "BTC/USDT:USDT"))
    data["ticker"] = sym

    # ── Capa 0: Tier check ────────────────────────
    tier = _tier_label(sym)
    if tier == "T3" and not ALLOW_TIER3:
        _stats["tier_blocks"] += 1
        return {"skipped": "tier3_disabled"}

    # ── Capa 1: Blacklist check ───────────────────
    if is_blacklisted(sym):
        return {"skipped": f"blacklisted until {datetime.fromtimestamp(_symbol_blacklist.get(sym,0)).strftime('%H:%M')}"}

    try: side = interp(data.get("signal", ""))
    except ValueError as e:
        send_telegram(fmt_signal_msg(sym, data.get("signal", "?"), data.get("price", "?"),
                                     str(e), "?", "?", error=str(e), source=source))
        return {"error": str(e)}

    # ── Capa 2: Circuit breaker ───────────────────
    ok, reason = circuit_breaker()
    if not ok and side != "close":
        send_telegram(f"🚫 <b>Circuit Breaker</b>\n{sym}: {reason}")
        return {"error": reason}

    if side != "close" and not can_send_signal(sym, side):
        return {"skipped": "cooldown"}

    sig_dir      = 1 if side == "long" else (-1 if side == "short" else 0)
    hurst_val    = data.get("hurst", 0.5)
    kurt_val     = data.get("kurt", 3.0)
    atr_sl_pct   = data.get("atr_sl_pct", 0.015)
    atr_tr_pct   = data.get("atr_trail_pct", 0.010)
    sweep_str    = data.get("sweep_strength", 0.0)
    session      = data.get("session", "")
    tier         = data.get("tier", "T2")
    tp_pct       = data.get("tp_pct", atr_sl_pct * MIN_RR_RATIO)
    rr_ratio_val = data.get("rr_ratio", MIN_RR_RATIO)

    # ── Capa 3: BTC Macro filter ──────────────────
    if side != "close":
        btc_ok, btc_reason = check_btc_macro(sym, sig_dir)
        if not btc_ok:
            log.info(f"[BTC_MACRO] {sym}: {btc_reason}")
            return {"skipped": f"btc_macro: {btc_reason}"}

    # ── Capa 4: Fee worthiness ────────────────────
    if side != "close":
        fee_ok, fee_ratio = check_fee_worthiness(atr_sl_pct, float(data.get("price", 1)))
        if not fee_ok:
            log.info(f"[FEE] {sym}: ratio={fee_ratio:.1f} < {FEE_MULTIPLIER}")
            return {"skipped": "fee_not_worth"}

    # ── Capa 5: Fear & Greed ──────────────────────
    fg_bias = _fg_cache["bias"]
    if fg_bias != 0 and sig_dir != 0 and fg_bias != sig_dir:
        if (mtf_score or 0) < _mtf_conf_dyn + 0.12:
            return {"skipped": "fg_macro_filter"}

    # ── Capa 6: OI Delta ──────────────────────────
    oi_delta = 0.0
    if side != "close":
        oi_delta = await get_oi_delta(sym)
        if sig_dir == 1  and oi_delta < -OI_DELTA_MIN: return {"skipped": "oi_divergence"}
        if sig_dir == -1 and oi_delta > OI_DELTA_MIN:  return {"skipped": "oi_divergence"}

    # ── Capa 7: Funding Momentum ──────────────────
    await update_funding_hist(sym)
    fund_mom = funding_momentum(sym)
    if sig_dir == 1  and fund_mom > FUNDING_SQUEEZE_THR:  return {"skipped": "funding_squeeze"}
    if sig_dir == -1 and fund_mom < -FUNDING_SQUEEZE_THR: return {"skipped": "funding_squeeze"}

    # ── Capa 8: Correlation Guard ─────────────────
    if side != "close":
        corr_ok, corr_reason = check_correlation_guard(sym, sig_dir)
        if not corr_ok:
            return {"skipped": f"correlation: {corr_reason}"}

    # ── Capa 9: LSTM confidence ───────────────────
    btc_dir = _btc_trend.get("dir", 0)
    ai_conf = None
    try:
        ohlcv = await exchange_async.fetch_ohlcv(sym, "5m", limit=25)
        if len(ohlcv) >= 15:
            cl = [x[4] for x in ohlcv]; vl = [x[5] for x in ohlcv]
            _, session_pen = get_session_info()
            ai_conf = lstm_confidence(cl, vl, datetime.now(timezone.utc).hour,
                                      hurst_val, kurt_val, sweep_str, fund_mom,
                                      session_pen, btc_dir)
            thresh_adj = 0.40 if data.get("sweep") else 0.43
            if side == "long"  and ai_conf < thresh_adj:  return {"skipped": "lstm_low"}
            if side == "short" and ai_conf > 1 - thresh_adj: return {"skipped": "lstm_low"}
    except Exception as e:
        log.warning(f"LSTM: {e}")

    # ── Precio y filtros de mercado ───────────────
    try:
        t = await exchange_async.fetch_ticker(sym); cur_price = t["last"]
    except: cur_price = float(data.get("price", 0) or 0)

    ob_imb = funding = 0.0
    try:
        ob = await exchange_async.fetch_order_book(sym, limit=10)
        bv = sum(b[1] for b in ob["bids"][:5]); av = sum(a[1] for a in ob["asks"][:5])
        ob_imb = (bv - av) / (bv + av) if bv + av > 0 else 0.0
    except: pass
    try:
        fr = await exchange_async.fetch_funding_rate(sym)
        funding = float(fr.get("fundingRate", 0) or 0)
    except: pass

    # v8: filtros de mercado más estrictos
    if sig_dir == 1  and (funding > 0.0008 or ob_imb < -0.25): return {"skipped": "market_filter"}
    if sig_dir == -1 and (funding < -0.0008 or ob_imb > 0.25): return {"skipped": "market_filter"}

    # ── Estadísticas de tier ──────────────────────
    if tier == "T1": _stats["tier1_trades"] += 1
    elif tier == "T2": _stats["tier2_trades"] += 1
    if data.get("sweep"): _stats["sweeps_traded"] += 1

    size  = calc_order_size()
    order = error = None

    if side == "close":
        state = _trailing_state.get(sym)
        if state:
            order = await close_position(sym, state["side"], state["contracts"])
        _open_trades.discard(sym); _trailing_state.pop(sym, None)
    else:
        order = await smart_order(sym, "buy" if side == "long" else "sell", size, atr_sl_pct)
        if order:
            entry = float(order.get("average") or order.get("price") or cur_price)
            _open_trades.add(sym)
            _trailing_state[sym] = {
                "side": side, "entry": entry, "best": entry,
                "contracts": size, "ai_conf": ai_conf,
                "mtf_score": mtf_score or 0.0, "paper": order.get("paper", False),
                "hurst": hurst_val, "kurt": kurt_val,
                "atr_sl_pct": atr_sl_pct, "atr_trail_pct": atr_tr_pct,
                "sweep_strength": sweep_str, "fund_mom": fund_mom,
                "tp_pct": tp_pct, "rr_ratio": rr_ratio_val,
            }
            register_signal(sym, side)
        else:
            error = "Order failed"

    send_telegram(fmt_signal_msg(
        sym, data.get("signal", "?"), f"{cur_price:.4f}",
        data.get("stype", data.get("signal_type", "")),
        data.get("regime", "?"), data.get("score", "?"),
        mtf=mtf_score, ai_conf=ai_conf,
        zscore=data.get("z_score"), whale=data.get("whale", False),
        cvd=data.get("cvd_pct"), absorb=data.get("absorption", False),
        sweep=data.get("sweep", False),
        order=order, error=error, source=source,
        hurst=hurst_val if hurst_val != 0.5 else None,
        kurt=kurt_val if kurt_val != 3.0 else None,
        fg=_fg_cache["value"],
        oi_delta=oi_delta if oi_delta != 0.0 else None,
        atr_sl=atr_sl_pct, session=session,
        fund_mom=fund_mom if abs(fund_mom) > 0.0001 else None,
        tier=tier, rr_ratio=rr_ratio_val, tp_pct=tp_pct,
        btc_trend=btc_dir,
    ))
    return {"order": order, "error": error}

# ═══════════════════════════════════════════════════════════════
#   TRAILING STOP — v8 con TP automático
# ═══════════════════════════════════════════════════════════════

async def trailing_loop():
    log.info("Trailing loop ON | ATR-based + TP auto | check cada 10s")
    while True:
        await asyncio.sleep(10)
        for sym, state in list(_trailing_state.items()):
            try:
                t = await exchange_async.fetch_ticker(sym); price = t["last"]
            except: continue

            side   = state["side"]
            best   = state["best"]
            entry  = state["entry"]
            trail  = state.get("atr_trail_pct", 0.010)
            tp_pct = state.get("tp_pct", 0.025)

            # v8: TP automático — cerrar si alcanza objetivo de beneficio
            if side == "long":
                pnl_pct = (price - entry) / entry
                if pnl_pct >= tp_pct:
                    log.info(f"[TP] {sym} long: {pnl_pct:.2%} ≥ TP {tp_pct:.2%}")
                    await _fire_trailing(sym, state, price, reason="TP")
                    continue
            else:
                pnl_pct = (entry - price) / entry
                if pnl_pct >= tp_pct:
                    log.info(f"[TP] {sym} short: {pnl_pct:.2%} ≥ TP {tp_pct:.2%}")
                    await _fire_trailing(sym, state, price, reason="TP")
                    continue

            moved = False
            if side == "long"  and price > best: state["best"] = best = price; moved = True
            if side == "short" and price < best: state["best"] = best = price; moved = True
            if moved: log.info(f"[TRAIL] {sym} best={best:.4f}")

            hit = ((side == "long"  and price <= best * (1 - trail)) or
                   (side == "short" and price >= best * (1 + trail)))
            if hit:
                await _fire_trailing(sym, state, price, reason="TRAIL")

async def _fire_trailing(sym, state, price, reason="TRAIL"):
    global _daily_pnl
    side  = state["side"]; entry = state["entry"]; best = state["best"]
    pnl   = ((price - entry) / entry * 100) if side == "long" else ((entry - price) / entry * 100)
    pnl_l = round(pnl * LEVERAGE, 2)
    _daily_pnl += pnl_l
    won = pnl_l > 0

    register_trade_result(sym, won)
    await close_position(sym, side, state["contracts"])
    _trailing_state.pop(sym, None); _open_trades.discard(sym)

    update_ai(won, state.get("z_score", ZSCORE_THRESHOLD), 1.0,
              datetime.now(timezone.utc).hour, state.get("mtf_score", 0.5),
              funding=0.0, ob_imb=0.0,
              hurst_val=state.get("hurst", 0.5), kurt_val=state.get("kurt", 3.0),
              sweep_strength=state.get("sweep_strength", 0.0),
              fund_mom=state.get("fund_mom", 0.0),
              btc_dir=_btc_trend.get("dir", 0))

    _reset_daily()
    wr = (_stats["total_wins"] / max(1, _stats["total_wins"] + _stats["total_losses"])) * 100
    send_telegram(
        f"{'🟢' if pnl_l >= 0 else '🔴'} <b>CERRADO — {reason}</b>\n"
        f"────────────────────\n"
        f"📈 <b>{sym}</b>\n"
        f"📍 {entry:.4f} → {price:.4f} | Best: {best:.4f}\n"
        f"🛡️ Trail: <b>{state.get('atr_trail_pct',0.01)*100:.2f}%</b> | "
        f"🎯 TP: <b>{state.get('tp_pct',0.025)*100:.2f}%</b>\n"
        f"{'🟢' if pnl_l >= 0 else '🔴'} PnL: <b>{pnl_l:+.2f}%</b> (x{LEVERAGE})\n"
        f"📊 PnL hoy: <b>{_daily_pnl:+.2f}%</b>\n"
        f"📈 WR acumulado: <b>{wr:.0f}%</b> "
        f"({_stats['total_wins']}W/{_stats['total_losses']}L)\n"
        f"🔴 Racha pérdidas: <b>{_consecutive_losses}</b>"
    )

# ═══════════════════════════════════════════════════════════════
#   SCANNER LOOP — v8 con BTC macro update
# ═══════════════════════════════════════════════════════════════

async def scanner_loop():
    log.info(f"Scanner v8 ON | {len(SCAN_SYMBOLS)} syms | {SCAN_INTERVAL}s")
    log.info(f"Símbolos permitidos: {SCAN_SYMBOLS}")
    send_telegram(
        f"🚀 <b>AEGIS GEX v8.0 — PRECISION EDITION</b>\n"
        f"────────────────────\n"
        f"Modo: <b>{MODE.upper()}</b> | Lev: <b>{LEVERAGE}x</b>\n"
        f"Balance: <b>${ACCOUNT_BALANCE}</b> | Riesgo/trade: <b>{RISK_PER_TRADE*100:.1f}%</b>\n"
        f"Símbolos: <b>{', '.join(SCAN_SYMBOLS)}</b>\n"
        f"────────────────────\n"
        f"🆕 MEJORAS v8:\n"
        f"⚫ Tier3 desactivado (sin micro-caps)\n"
        f"📉 Max {MAX_DAILY_TRADES} trades/día (anti-overtrading)\n"
        f"⚖️ R:R mínimo {MIN_RR_RATIO}:1 verificado\n"
        f"🪙 Fee-aware: mín {FEE_MULTIPLIER}x fees\n"
        f"📊 BTC macro filter obligatorio\n"
        f"⛔ Auto-blacklist tras {BLACKLIST_MAX_CONSEC_LOSSES} pérdidas\n"
        f"🛑 Pausa tras 3 pérdidas seguidas\n"
        f"⏱️ Cooldown 10min | ADX filter\n"
        f"📐 Confluencia mín {MTF_CONFLUENCE_MIN:.0%}"
    )
    n = 0
    while True:
        n += 1
        ok, reason = circuit_breaker()
        if not ok:
            log.warning(f"[CB] {reason}"); await asyncio.sleep(SCAN_INTERVAL); continue

        try: await asyncio.get_event_loop().run_in_executor(None, refresh_fear_greed)
        except: pass

        # v8: actualizar tendencia BTC cada ciclo
        await update_btc_trend()

        session_label, session_pen = get_session_info()
        btc_str = f"BTC={'↑' if _btc_trend.get('dir')==1 else ('↓' if _btc_trend.get('dir')==-1 else '→')}"
        log.info(f"── SCAN #{n} | {session_label} | {btc_str} | "
                 f"pos={len(_open_trades)}/{MAX_OPEN_TRADES} | "
                 f"trades_hoy={_daily_trade_count}/{MAX_DAILY_TRADES} | "
                 f"PnL={_daily_pnl:+.2f}% ──")

        async def _scan_one(sym):
            if is_blacklisted(sym):
                return sym, None
            try: return sym, await analyze_mtf(sym)
            except Exception as e:
                log.error(f"[SCAN] {sym}: {e}")
                return sym, None

        scan_results = await asyncio.gather(*[_scan_one(s) for s in SCAN_SYMBOLS])
        found = 0
        for sym, res in scan_results:
            if res is None: continue
            found += 1
            log.info(f"  ✅ {sym} [{res.get('tier','?')}] → {res['signal']} "
                     f"| MTF={res['mtf_score']:.2f} "
                     f"| RR={res.get('rr_ratio',0):.1f} "
                     f"| {'⚡SWEEP' if res.get('sweep') else ''}")
            await process_signal(res, source="Scanner", mtf_score=res["mtf_score"])

        log.info(f"── FIN #{n} | señales={found} | WR={(_stats['total_wins']/max(1,_stats['total_wins']+_stats['total_losses']))*100:.0f}% ──")
        await asyncio.sleep(SCAN_INTERVAL)

# ═══════════════════════════════════════════════════════════════
#   FLASK — API REST
# ═══════════════════════════════════════════════════════════════

flask_app = Flask(__name__)
_loop: asyncio.AbstractEventLoop | None = None

def _run_async(coro):
    if _loop and _loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=30)
    return {"error": "Loop no disponible"}

@flask_app.route("/", methods=["GET"])
def health():
    ok, reason = circuit_breaker()
    session_label, session_pen = get_session_info()
    wr = (_stats["total_wins"] / max(1, _stats["total_wins"] + _stats["total_losses"])) * 100
    return jsonify({
        "status": "online", "bot": "AEGIS GEX v8.0 PRECISION", "mode": MODE,
        "scanner": SCANNER_ENABLED, "symbols": SCAN_SYMBOLS,
        "open_trades": list(_open_trades), "daily_pnl_pct": round(_daily_pnl, 2),
        "daily_trades": f"{_daily_trade_count}/{MAX_DAILY_TRADES}",
        "consecutive_losses": _consecutive_losses,
        "circuit_breaker": not ok, "block_reason": reason,
        "win_rate_pct": round(wr, 1),
        "ai": {"trained": _ai_model_ready, "z_threshold": round(_ai_z_threshold, 3),
               "memory": len(_ai_memory), "features": 10},
        "session": {"current": session_label, "penalty": session_pen,
                    "mtf_effective": round(_mtf_conf_dyn + session_pen, 3)},
        "btc_macro": _btc_trend,
        "qe": {"fear_greed": _fg_cache["value"], "label": _fg_cache["label"],
               "bias": _fg_cache["bias"]},
        "blacklist": {k: datetime.fromtimestamp(v).strftime("%H:%M") for k, v in _symbol_blacklist.items()},
        "v8_stats": _stats,
        "trailing": list(_trailing_state.keys()),
    }), 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Webhook-Secret", "") != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True)
    if not data: return jsonify({"error": "No JSON"}), 400
    res = _run_async(process_signal(data, source="TradingView"))
    if res.get("error"): return jsonify({"status": "error", "message": res["error"]}), 500
    return jsonify({"status": "success"}), 200

@flask_app.route("/status", methods=["GET"])
def status():
    ok, reason = circuit_breaker()
    wr = (_stats["total_wins"] / max(1, _stats["total_wins"] + _stats["total_losses"])) * 100
    return jsonify({
        "mode": MODE, "open_trades": list(_open_trades),
        "trailing": {k: {kk: vv for kk, vv in v.items() if kk != "paper"}
                     for k, v in _trailing_state.items()},
        "daily_pnl_pct": round(_daily_pnl, 2),
        "daily_trades": _daily_trade_count,
        "can_trade": ok, "block_reason": reason,
        "consecutive_losses": _consecutive_losses,
        "win_rate_pct": round(wr, 1),
        "btc_trend": _btc_trend,
        "blacklist": list(_symbol_blacklist.keys()),
        "stats": _stats,
    }), 200

@flask_app.route("/macro", methods=["GET"])
def macro():
    refresh_fear_greed()
    session_label, pen = get_session_info()
    return jsonify({
        "fear_greed": _fg_cache["value"], "label": _fg_cache["label"],
        "bias": "LONG" if _fg_cache["bias"] == 1 else ("SHORT" if _fg_cache["bias"] == -1 else "NEUTRO"),
        "session": session_label, "session_penalty": pen,
        "mtf_effective": round(_mtf_conf_dyn + pen, 3),
        "btc_macro": _btc_trend,
        "blacklist": {k: datetime.fromtimestamp(v).strftime("%H:%M") for k, v in _symbol_blacklist.items()},
        "consec_losses": {k: v for k, v in _symbol_consec_losses.items() if v > 0},
    }), 200

@flask_app.route("/risk", methods=["GET"])
def risk():
    ok, reason = circuit_breaker()
    return jsonify({
        "balance": ACCOUNT_BALANCE, "risk_pct": RISK_PER_TRADE * 100,
        "max_trades": MAX_OPEN_TRADES, "current_open": len(_open_trades),
        "max_daily_trades": MAX_DAILY_TRADES, "daily_trades": _daily_trade_count,
        "daily_loss_limit_pct": MAX_DAILY_LOSS * 100, "daily_pnl_pct": round(_daily_pnl, 2),
        "can_trade": ok, "reason": reason, "leverage": LEVERAGE,
        "order_size_usdt": calc_order_size(),
        "min_rr_ratio": MIN_RR_RATIO, "min_tp_pct": MIN_TP_PCT * 100,
        "consecutive_losses": _consecutive_losses,
    }), 200

@flask_app.route("/scan", methods=["GET"])
def scan_now():
    results = []
    for sym in SCAN_SYMBOLS:
        if is_blacklisted(sym):
            results.append({"symbol": sym, "status": "blacklisted"})
            continue
        try:
            res = _run_async(analyze_mtf(sym.strip()))
            if res: results.append(res)
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return jsonify({"scanned": len(SCAN_SYMBOLS), "found": len(results), "signals": results}), 200

# ═══════════════════════════════════════════════════════════════
#   ARRANQUE
# ═══════════════════════════════════════════════════════════════

import threading

def run_flask(port):
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)

async def main():
    global _loop
    _loop = asyncio.get_running_loop()
    # Cargar tendencia BTC al arrancar
    await update_btc_trend()
    tasks = [trailing_loop()]
    if SCANNER_ENABLED: tasks.append(scanner_loop())
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    send_telegram(
        f"🔌 <b>AEGIS GEX v8.0 PRECISION arrancando...</b>\n"
        f"Modo: <b>{MODE.upper()}</b> | Puerto: <b>{port}</b>\n"
        f"Símbolos: <b>{', '.join(SCAN_SYMBOLS)}</b>\n"
        f"Max trades/día: <b>{MAX_DAILY_TRADES}</b> | "
        f"Confluencia: <b>{MTF_CONFLUENCE_MIN:.0%}</b> | "
        f"R:R min: <b>{MIN_RR_RATIO}:1</b>"
    )
    t = threading.Thread(target=run_flask, args=(port,), daemon=True)
    t.start()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Detenido manualmente.")
    finally:
        asyncio.run(exchange_async.close())
