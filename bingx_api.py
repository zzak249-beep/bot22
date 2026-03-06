import os
import hmac
import hashlib
import time
import json
import requests
from urllib.parse import urlencode

# ══════════════════════════════════════════════════════
# bingx_api.py v13.2 — FIX DEFINITIVO error 100001
#
# Firma correcta para BingX Perpetual Swap:
# https://bingx-api.github.io/docs/#/en-us/swapV2/base-info.html
#
# Regla exacta de BingX:
#   payload = todos los params + timestamp (como query string)
#   signature = HMAC_SHA256(secretKey, payload)
#   La signature se añade SEPARADA al final de la URL
# ══════════════════════════════════════════════════════

BASE = "https://open-api.bingx.com"


def _key():
    return os.getenv("BINGX_API_KEY", "")

def _secret():
    return os.getenv("BINGX_API_SECRET", "")

def _lev():
    return int(os.getenv("LEVERAGE", 2))


def _sign(params: dict) -> str:
    """
    BingX firma: construir query string con todos los params
    (incluido timestamp), luego HMAC-SHA256.
    NO ordenar — mantener orden de inserción.
    """
    payload = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(
        _secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def _headers() -> dict:
    return {
        "X-BX-APIKEY": _key(),
        "Content-Type": "application/json",
    }


def _get(path: str, params: dict = None) -> dict:
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    sig = _sign(p)
    p["signature"] = sig
    r = requests.get(BASE + path, params=p, headers=_headers(), timeout=12)
    data = r.json()
    if data.get("code", 0) != 0:
        print(f"  [API GET] {path} → code={data.get('code')} {data.get('msg','')}")
    return data


def _post(path: str, params: dict = None) -> dict:
    """
    BingX POST para perpetual swap:
    - params van en query string (URL), NO en body
    - body queda vacío
    """
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    sig = _sign(p)
    p["signature"] = sig
    r = requests.post(BASE + path, params=p, headers=_headers(), timeout=12)
    data = r.json()
    if data.get("code", 0) != 0:
        print(f"  [API POST] {path} → code={data.get('code')} {data.get('msg','')}")
    return data


# ── Contratos ─────────────────────────────────────────
_contract_cache = {}

def get_contract_info(symbol: str) -> dict:
    if symbol in _contract_cache:
        return _contract_cache[symbol]
    try:
        r = requests.get(
            BASE + "/openApi/swap/v2/quote/contracts",
            timeout=10
        ).json()
        for c in (r.get("data") or []):
            if c.get("symbol") == symbol:
                info = {
                    "stepSize": float(c.get("tradeMinQuantity", 0.001)),
                    "minQty":   float(c.get("tradeMinQuantity", 0.001)),
                    "priceDec": int(c.get("pricePrecision", 4)),
                    "qtyDec":   int(c.get("quantityPrecision", 3)),
                }
                _contract_cache[symbol] = info
                return info
    except Exception as e:
        print(f"[API] contract_info {symbol}: {e}")
    return {"stepSize": 0.001, "minQty": 0.001, "priceDec": 4, "qtyDec": 3}


def _round_qty(symbol: str, qty: float) -> float:
    i = get_contract_info(symbol)
    s = i["stepSize"]
    return max(round(int(qty / s) * s, 8), i["minQty"])


def _round_price(symbol: str, price: float) -> float:
    return round(price, get_contract_info(symbol)["priceDec"])


# ── Balance ───────────────────────────────────────────
def get_balance() -> float:
    try:
        d = _get("/openApi/swap/v2/user/balance")
        b = ((d.get("data") or {}).get("balance") or {})
        return float(b.get("availableMargin", 0))
    except Exception as e:
        print(f"[API] balance: {e}")
        return 0.0


# ── Precio público (sin auth) ─────────────────────────
def get_price(symbol: str) -> float:
    try:
        r = requests.get(
            BASE + "/openApi/swap/v2/quote/price",
            params={"symbol": symbol}, timeout=8
        ).json()
        return float((r.get("data") or {}).get("price", 0))
    except Exception as e:
        print(f"[API] price {symbol}: {e}")
        return 0.0


# ── Apalancamiento ────────────────────────────────────
def set_leverage(symbol: str, leverage: int = None) -> bool:
    lev = leverage or _lev()
    ok = True
    for side in ("LONG", "SHORT"):
        try:
            r = _post("/openApi/swap/v2/trade/leverage",
                      {"symbol": symbol, "side": side, "leverage": lev})
            if r.get("code", 0) != 0:
                ok = False
        except Exception as e:
            print(f"[API] leverage {symbol} {side}: {e}")
            ok = False
    return ok


# ── Abrir orden con SL y TP ───────────────────────────
def open_order(symbol: str, side: str, qty: float,
               sl_price: float, tp_price: float) -> dict:
    """
    Abre orden market con SL y TP adjuntos.
    side: "long" | "short"
    """
    pos_side   = "LONG" if side == "long" else "SHORT"
    order_side = "BUY"  if side == "long" else "SELL"

    qty_r = _round_qty(symbol, qty)
    sl_r  = _round_price(symbol, sl_price)
    tp_r  = _round_price(symbol, tp_price)

    if qty_r <= 0:
        return {"code": -1, "msg": f"qty inválida: {qty} → {qty_r}"}

    # SL y TP como JSON strings (formato requerido por BingX)
    sl_obj = json.dumps({
        "type": "STOP_MARKET",
        "stopPrice": str(sl_r),
        "price": str(sl_r),
        "workingType": "MARK_PRICE"
    }, separators=(',', ':'))

    tp_obj = json.dumps({
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": str(tp_r),
        "price": str(tp_r),
        "workingType": "MARK_PRICE"
    }, separators=(',', ':'))

    params = {
        "symbol":       symbol,
        "side":         order_side,
        "positionSide": pos_side,
        "type":         "MARKET",
        "quantity":     qty_r,
        "stopLoss":     sl_obj,
        "takeProfit":   tp_obj,
    }

    print(f"  [API] open_order {symbol} {side} qty={qty_r} sl={sl_r} tp={tp_r}")

    try:
        result = _post("/openApi/swap/v2/trade/order", params)
        code = result.get("code", -1)
        if code == 0:
            oid = (result.get("data") or {}).get("order", {}).get("orderId", "?")
            print(f"  [API] ✅ ORDEN ABIERTA — orderId={oid}")
        else:
            print(f"  [API] ❌ Error: code={code} msg={result.get('msg','')}")
        return result
    except Exception as e:
        print(f"  [API] exception: {e}")
        return {"code": -1, "msg": str(e)}


# ── Cerrar posición ───────────────────────────────────
def close_position(symbol: str, side: str, qty: float) -> dict:
    pos_side   = "LONG" if side == "long" else "SHORT"
    order_side = "SELL" if side == "long" else "BUY"
    qty_r = _round_qty(symbol, qty)
    try:
        result = _post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         order_side,
            "positionSide": pos_side,
            "type":         "MARKET",
            "quantity":     qty_r,
        })
        if result.get("code") == 0:
            print(f"  [API] ✅ {symbol} cerrado")
        return result
    except Exception as e:
        return {"code": -1, "msg": str(e)}


# ── Posiciones abiertas ───────────────────────────────
def get_open_positions() -> list:
    try:
        return _get("/openApi/swap/v2/user/positions").get("data") or []
    except Exception as e:
        print(f"[API] positions: {e}")
        return []


# ── Klines (sin auth) ─────────────────────────────────
def fetch_klines(symbol: str, interval: str = "15m", limit: int = 300) -> list:
    for path in ("/openApi/swap/v3/quote/klines", "/openApi/swap/v2/quote/klines"):
        try:
            r = requests.get(
                BASE + path,
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=15
            ).json()
            c = r if isinstance(r, list) else r.get("data", [])
            if c:
                return c
        except Exception:
            continue
    return []


# ── Test de firma (para diagnóstico) ─────────────────
def test_signature() -> dict:
    """
    Llama a /user/balance para verificar firma.
    Imprime resultado detallado.
    """
    print("\n[DIAG] Test de firma BingX...")
    print(f"[DIAG] API_KEY: {_key()[:8]}..." if _key() else "[DIAG] API_KEY: ⚠️ VACÍO")
    print(f"[DIAG] SECRET:  configurado" if _secret() else "[DIAG] SECRET:  ⚠️ VACÍO")

    try:
        result = _get("/openApi/swap/v2/user/balance")
        code = result.get("code", -1)
        if code == 0:
            bal = ((result.get("data") or {}).get("balance") or {})
            margin = bal.get("availableMargin", "?")
            print(f"[DIAG] ✅ Firma OK — Balance: ${margin}")
        else:
            print(f"[DIAG] ❌ Error {code}: {result.get('msg','')}")
        return result
    except Exception as e:
        print(f"[DIAG] Exception: {e}")
        return {"code": -1, "msg": str(e)}
