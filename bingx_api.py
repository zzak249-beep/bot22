"""
bingx_api.py — API BingX Futuros Perpetuos v14.0
Firma HMAC-SHA256, orden de parámetros exacto (BingX lo requiere).
"""
import hmac
import hashlib
import time
import logging
import requests

log = logging.getLogger("bingx_api")

try:
    import config as cfg
    API_KEY    = cfg.BINGX_API_KEY
    API_SECRET = cfg.BINGX_API_SECRET
    LEVERAGE   = cfg.LEVERAGE
    MODO_DEBUG = cfg.MODO_DEBUG
except Exception:
    API_KEY = ""; API_SECRET = ""; LEVERAGE = 3; MODO_DEBUG = False

BASE = "https://open-api.bingx.com"


def _sign(qs: str) -> str:
    return hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()


def _headers() -> dict:
    return {"X-BX-APIKEY": API_KEY, "Content-Type": "application/json"}


def _qs(params: dict) -> str:
    return "&".join(f"{k}={v}" for k, v in params.items())


def _get(path: str, params: dict = None, auth: bool = True) -> dict:
    p = dict(params or {})
    if auth:
        p["timestamp"] = int(time.time() * 1000)
        qs  = _qs(p)
        url = f"{BASE}{path}?{qs}&signature={_sign(qs)}"
        try:
            r = requests.get(url, headers=_headers(), timeout=12)
            return r.json()
        except Exception as e:
            log.warning(f"GET {path}: {e}")
            return {}
    try:
        r = requests.get(BASE + path, params=p, timeout=12)
        return r.json()
    except Exception as e:
        log.warning(f"GET(pub) {path}: {e}")
        return {}


def _post(path: str, params: dict) -> dict:
    p = dict(params)
    p["timestamp"] = int(time.time() * 1000)
    qs  = _qs(p)
    url = f"{BASE}{path}?{qs}&signature={_sign(qs)}"
    try:
        r = requests.post(url, headers=_headers(), timeout=12)
        d = r.json()
        if MODO_DEBUG and d.get("code", 0) != 0:
            log.warning(f"POST {path} code={d.get('code')} msg={d.get('msg','')[:80]}")
        return d
    except Exception as e:
        log.warning(f"POST {path}: {e}")
        return {"code": -1}


# ── Balance ───────────────────────────────────────────

def get_balance() -> float:
    resp = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"})
    try:
        bal = resp.get("data", {}).get("balance", {})
        if isinstance(bal, dict):
            return float(bal.get("availableMargin", 0))
        if isinstance(bal, list):
            for item in bal:
                if item.get("asset") == "USDT":
                    return float(item.get("availableMargin", 0))
    except Exception as e:
        log.warning(f"get_balance: {e}")
    return 0.0


# ── Leverage ──────────────────────────────────────────

def set_leverage(symbol: str, leverage: int):
    par = symbol.replace("/", "-")
    for side in ("LONG", "SHORT"):
        _post("/openApi/swap/v2/trade/leverage",
              {"symbol": par, "side": side, "leverage": str(leverage)})


# ── Órdenes ───────────────────────────────────────────

def open_order(symbol: str, side: str, qty: float, sl: float, tp: float) -> dict:
    """
    Abre posición de mercado + SL + TP.
    side: "long" | "short"
    Retorna respuesta de BingX (code==0 → éxito).
    """
    par       = symbol.replace("/", "-")
    bx_side   = "BUY"  if side == "long"  else "SELL"
    pos_side  = "LONG" if side == "long"  else "SHORT"
    sl_side   = "SELL" if side == "long"  else "BUY"

    # Orden principal de mercado
    resp = _post("/openApi/swap/v2/trade/order", {
        "symbol":       par,
        "side":         bx_side,
        "positionSide": pos_side,
        "type":         "MARKET",
        "quantity":     str(qty),
    })

    if resp.get("code", -1) != 0:
        return resp

    # Stop Loss
    _post("/openApi/swap/v2/trade/order", {
        "symbol":       par,
        "side":         sl_side,
        "positionSide": pos_side,
        "type":         "STOP_MARKET",
        "quantity":     str(qty),
        "stopPrice":    str(round(sl, 8)),
        "workingType":  "MARK_PRICE",
    })

    # Take Profit
    _post("/openApi/swap/v2/trade/order", {
        "symbol":       par,
        "side":         sl_side,
        "positionSide": pos_side,
        "type":         "TAKE_PROFIT_MARKET",
        "quantity":     str(qty),
        "stopPrice":    str(round(tp, 8)),
        "workingType":  "MARK_PRICE",
    })

    log.info(f"ABIERTO {side.upper()} {par} qty={qty} SL={sl:.6f} TP={tp:.6f}")
    return resp


def close_position(symbol: str, side: str, qty: float) -> dict:
    par      = symbol.replace("/", "-")
    bx_side  = "SELL" if side == "long" else "BUY"
    pos_side = "LONG" if side == "long" else "SHORT"
    resp = _post("/openApi/swap/v2/trade/order", {
        "symbol":       par,
        "side":         bx_side,
        "positionSide": pos_side,
        "type":         "MARKET",
        "quantity":     str(round(qty, 4)),
    })
    # Cancelar SL/TP pendientes
    _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": par})
    return resp
