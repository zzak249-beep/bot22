"""
exchange.py — BingX Perpetual Futures API
SMC Bot v5.0 — FIX balance + debug logging
"""
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional
from urllib.parse import urlencode

import requests

import config

log = logging.getLogger("exchange")

BASE_URL = "https://open-api.bingx.com"
_CONTRATOS_FUTURES: set = set()
_blocked_pairs: set = set()
_time_offset: int = 0
_QTY_PRECISION: dict = {}
_MIN_QTY: dict = {}


# ═══════════════════════════════════════════════════════
# FIRMA
# ═══════════════════════════════════════════════════════

def _ts() -> int:
    return int(time.time() * 1000) + _time_offset


def _api_key() -> str:
    return (os.getenv("BINGX_API_KEY", config.BINGX_API_KEY) or "").strip()

def _secret_key() -> str:
    return (os.getenv("BINGX_SECRET_KEY", config.BINGX_SECRET_KEY) or "").strip()


def _sign(query_string: str) -> str:
    """Firma el query string exacto (ya construido) con HMAC-SHA256."""
    secret = _secret_key()
    return hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _build_query(params: dict) -> str:
    """
    Construye query string SIN urlencode de valores —
    BingX firma y envía los valores raw (sin %xx encoding).
    Los parámetros se ordenan alfabéticamente por clave.
    """
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()))


def _headers() -> dict:
    return {
        "X-BX-APIKEY": _api_key(),
        "Content-Type": "application/json",
    }


def _get(path: str, params: Optional[dict] = None) -> dict:
    """GET firmado — construye URL manualmente para que firma == URL enviada."""
    p = dict(params or {})
    p["timestamp"] = _ts()
    query = _build_query(p)
    sig   = _sign(query)
    url   = f"{BASE_URL}{path}?{query}&signature={sig}"
    try:
        r = requests.get(url, headers=_headers(), timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"GET {path}: {e}")
        return {}


def _post(path: str, params: Optional[dict] = None) -> dict:
    """POST firmado — query en URL (BingX perpetual swap requiere params en query string)."""
    p = dict(params or {})
    p["timestamp"] = _ts()
    query = _build_query(p)
    sig   = _sign(query)
    url   = f"{BASE_URL}{path}?{query}&signature={sig}"
    try:
        r = requests.post(url, headers=_headers(), timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"POST {path}: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# TIEMPO
# ═══════════════════════════════════════════════════════

def sync_server_time():
    global _time_offset
    try:
        r = requests.get(f"{BASE_URL}/openApi/swap/v2/server/time", timeout=5)
        d = r.json()
        st = int(d.get("data", {}).get("serverTime", 0) or 0)
        if st > 0:
            _time_offset = st - int(time.time() * 1000)
            log.info(f"[TIME] offset={_time_offset}ms")
    except Exception as e:
        log.warning(f"sync_server_time: {e}")


# ═══════════════════════════════════════════════════════
# CONTRATOS
# ═══════════════════════════════════════════════════════

def _cargar_contratos():
    global _CONTRATOS_FUTURES
    try:
        r = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=12)
        contratos = r.json().get("data", []) or []
        _CONTRATOS_FUTURES = set()
        for c in contratos:
            sym = c.get("symbol", "")
            if not sym:
                continue
            _CONTRATOS_FUTURES.add(sym)
            _QTY_PRECISION[sym] = int(c.get("quantityPrecision", 4))
            _MIN_QTY[sym] = float(c.get("minQty", 0.0001) or 0.0001)
        log.info(f"[CONTRATOS] {len(_CONTRATOS_FUTURES)} pares")
    except Exception as e:
        log.warning(f"_cargar_contratos: {e}")


def par_es_soportado(par: str) -> bool:
    if par in _blocked_pairs:
        return False
    if _CONTRATOS_FUTURES and par not in _CONTRATOS_FUTURES:
        return False
    return True


# ═══════════════════════════════════════════════════════
# PRECIO Y VELAS
# ═══════════════════════════════════════════════════════

def get_precio(par: str) -> float:
    if config.MODO_DEMO:
        return 1.0
    try:
        r = requests.get(
            f"{BASE_URL}/openApi/swap/v2/quote/price",
            params={"symbol": par}, timeout=6,
        )
        return float(r.json().get("data", {}).get("price", 0) or 0)
    except Exception as e:
        log.debug(f"get_precio {par}: {e}")
        return 0.0


def get_candles(par: str, tf: str, limit: int = 200) -> list:
    try:
        r = requests.get(
            f"{BASE_URL}/openApi/swap/v2/quote/klines",
            params={"symbol": par, "interval": tf, "limit": min(limit, 1000)},
            timeout=10,
        )
        raw = r.json().get("data", []) or []
        result = []
        for c in raw:
            try:
                if isinstance(c, list):
                    result.append({
                        "ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                        "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]),
                    })
                elif isinstance(c, dict):
                    result.append({
                        "ts":     int(c.get("time", c.get("openTime", 0))),
                        "open":   float(c.get("open", 0)),
                        "high":   float(c.get("high", 0)),
                        "low":    float(c.get("low", 0)),
                        "close":  float(c.get("close", 0)),
                        "volume": float(c.get("volume", 0)),
                    })
            except Exception:
                pass
        result.sort(key=lambda x: x["ts"])
        return result
    except Exception as e:
        log.debug(f"get_candles {par}/{tf}: {e}")
        return []


# ═══════════════════════════════════════════════════════
# BALANCE — debug completo para diagnosticar
# ═══════════════════════════════════════════════════════

def _try_balance_endpoint(path: str, extra: dict = None) -> float:
    """Intenta un endpoint de balance y loguea la respuesta completa."""
    try:
        data = _get(path, extra or {})
        code = data.get("code", -1)
        msg  = data.get("msg", "")
        raw  = json.dumps(data)[:400]
        log.info(f"[BAL-RAW] {path} code={code} msg={msg} → {raw}")

        if code != 0:
            return -1.0

        d = data.get("data", {})

        # Caso 1: data.balance es dict
        bal = d.get("balance")
        if isinstance(bal, dict):
            for k in ("availableMargin", "freeMargin", "available", "crossWalletBalance",
                      "crossUnPnl", "balance", "equity", "maxWithdrawAmount"):
                v = bal.get(k)
                if v is not None:
                    try:
                        f = float(v)
                        log.info(f"[BAL] {path} campo '{k}' = {f}")
                        if f >= 0:
                            return f
                    except Exception:
                        pass

        # Caso 2: data es dict directo
        if isinstance(d, dict) and "availableMargin" in d:
            return float(d["availableMargin"])

        # Caso 3: data es lista
        if isinstance(d, list):
            for item in d:
                asset = str(item.get("asset", item.get("currency", ""))).upper()
                if asset in ("USDT", ""):
                    for k in ("availableMargin", "freeMargin", "available", "balance"):
                        v = item.get(k)
                        if v is not None:
                            try:
                                f = float(v)
                                if f >= 0:
                                    return f
                            except Exception:
                                pass

        # Caso 4: d.balance es lista (formato v3)
        if isinstance(d, dict):
            for key_outer in ("balance", "assets", "data"):
                inner = d.get(key_outer)
                if isinstance(inner, list):
                    for item in inner:
                        asset = str(item.get("asset", item.get("currency", ""))).upper()
                        if asset in ("USDT", ""):
                            for k in ("availableMargin", "freeMargin", "available", "balance"):
                                v = item.get(k)
                                if v is not None:
                                    try:
                                        f = float(v)
                                        if f >= 0:
                                            log.info(f"[BAL] {path} v-list campo '{k}' = {f}")
                                            return f
                                    except Exception:
                                        pass

        log.warning(f"[BAL] {path} no se encontró campo de balance en: {raw}")
        return -1.0

    except Exception as e:
        log.warning(f"[BAL] {path} excepción: {e}")
        return -1.0


def get_balance() -> float:
    if config.MODO_DEMO:
        return 200.0

    # Intentar v3 y v2 con y sin recvWindow
    endpoints = [
        "/openApi/swap/v3/user/balance",
        "/openApi/swap/v2/user/balance",
    ]

    for ep in endpoints:
        # Intento 1: sin recvWindow
        bal = _try_balance_endpoint(ep)
        if bal >= 0:
            log.info(f"[BAL] ✅ Balance: ${bal:.2f} desde {ep}")
            return bal
        # Intento 2: con recvWindow=5000
        bal2 = _try_balance_endpoint(ep, extra={"recvWindow": 5000})
        if bal2 >= 0:
            log.info(f"[BAL] ✅ Balance (recvWindow): ${bal2:.2f} desde {ep}")
            return bal2

    log.warning("[BAL] Todos los endpoints fallaron — retornando 0")
    return 0.0


# ═══════════════════════════════════════════════════════
# POSICIONES ABIERTAS
# ═══════════════════════════════════════════════════════

def get_posiciones_abiertas() -> list:
    if config.MODO_DEMO:
        return []
    try:
        data = _get("/openApi/swap/v2/trade/allOpenPositions")
        pos = data.get("data", []) or []
        return pos if isinstance(pos, list) else []
    except Exception as e:
        log.error(f"get_posiciones_abiertas: {e}")
        return []


# ═══════════════════════════════════════════════════════
# CANTIDAD Y LEVERAGE
# ═══════════════════════════════════════════════════════

def calcular_cantidad(par: str, usdt: float, precio: float) -> float:
    if precio <= 0:
        return 0.0
    try:
        raw_qty = (usdt * config.LEVERAGE) / precio
        prec    = _QTY_PRECISION.get(par, 4)
        factor  = 10 ** prec
        qty     = float(int(raw_qty * factor)) / factor
        min_q   = _MIN_QTY.get(par, 0.0001)
        return qty if qty >= min_q else 0.0
    except Exception as e:
        log.error(f"calcular_cantidad {par}: {e}")
        return 0.0


def _set_leverage(par: str, lado: str):
    try:
        res = _post("/openApi/swap/v2/trade/leverage", {
            "symbol":   par,
            "side":     "LONG" if lado == "LONG" else "SHORT",
            "leverage": config.LEVERAGE,
        })
        if res.get("code", -1) != 0:
            log.debug(f"leverage {par}: {res.get('msg')}")
    except Exception as e:
        log.debug(f"_set_leverage {par}: {e}")


# ═══════════════════════════════════════════════════════
# ABRIR LONG
# ═══════════════════════════════════════════════════════

def abrir_long(par: str, qty: float, precio: float, sl: float, tp: float) -> Optional[dict]:
    if config.MODO_DEMO:
        log.info(f"[DEMO] LONG {par} qty={qty:.6f} @ {precio:.6f}")
        return {"fill_price": precio, "executedQty": qty}
    try:
        _set_leverage(par, "LONG")
        params: dict = {
            "symbol": par, "side": "BUY",
            "positionSide": "LONG", "type": "MARKET", "quantity": qty,
        }
        if sl > 0:
            params["stopLoss"] = json.dumps({
                "type": "STOP_MARKET", "stopPrice": round(sl, 8),
                "price": round(sl, 8), "workingType": "MARK_PRICE",
            })
        if tp > 0:
            params["takeProfit"] = json.dumps({
                "type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, 8),
                "price": round(tp, 8), "workingType": "MARK_PRICE",
            })

        data = _post("/openApi/swap/v2/trade/order", params)
        if data.get("code", -1) != 0:
            err = data.get("msg", str(data))
            log.warning(f"abrir_long {par}: {err}")
            # Retry sin SL/TP
            params.pop("stopLoss", None); params.pop("takeProfit", None)
            data = _post("/openApi/swap/v2/trade/order", params)
            if data.get("code", -1) != 0:
                return {"error": data.get("msg", str(data))}
            _colocar_sl_tp_separados(par, sl, tp, "LONG", qty)

        order = data.get("data", {}).get("order", {})
        fill  = float(order.get("avgPrice", 0) or order.get("price", 0) or precio)
        qty_r = float(order.get("executedQty", qty) or qty)
        log.info(f"✅ LONG {par} fill={fill:.6f} qty={qty_r}")
        return {"fill_price": fill or precio, "executedQty": qty_r or qty}
    except Exception as e:
        log.error(f"abrir_long {par}: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════
# ABRIR SHORT
# ═══════════════════════════════════════════════════════

def abrir_short(par: str, qty: float, precio: float, sl: float, tp: float) -> Optional[dict]:
    if config.MODO_DEMO:
        log.info(f"[DEMO] SHORT {par} qty={qty:.6f} @ {precio:.6f}")
        return {"fill_price": precio, "executedQty": qty}
    try:
        _set_leverage(par, "SHORT")
        params: dict = {
            "symbol": par, "side": "SELL",
            "positionSide": "SHORT", "type": "MARKET", "quantity": qty,
        }
        if sl > 0:
            params["stopLoss"] = json.dumps({
                "type": "STOP_MARKET", "stopPrice": round(sl, 8),
                "price": round(sl, 8), "workingType": "MARK_PRICE",
            })
        if tp > 0:
            params["takeProfit"] = json.dumps({
                "type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, 8),
                "price": round(tp, 8), "workingType": "MARK_PRICE",
            })

        data = _post("/openApi/swap/v2/trade/order", params)
        if data.get("code", -1) != 0:
            err = data.get("msg", str(data))
            log.warning(f"abrir_short {par}: {err}")
            params.pop("stopLoss", None); params.pop("takeProfit", None)
            data = _post("/openApi/swap/v2/trade/order", params)
            if data.get("code", -1) != 0:
                return {"error": data.get("msg", str(data))}
            _colocar_sl_tp_separados(par, sl, tp, "SHORT", qty)

        order = data.get("data", {}).get("order", {})
        fill  = float(order.get("avgPrice", 0) or order.get("price", 0) or precio)
        qty_r = float(order.get("executedQty", qty) or qty)
        log.info(f"✅ SHORT {par} fill={fill:.6f} qty={qty_r}")
        return {"fill_price": fill or precio, "executedQty": qty_r or qty}
    except Exception as e:
        log.error(f"abrir_short {par}: {e}")
        return {"error": str(e)}


def _colocar_sl_tp_separados(par: str, sl: float, tp: float, lado: str, qty: float):
    try:
        pos_side = "LONG" if lado == "LONG" else "SHORT"
        cl_side  = "SELL" if lado == "LONG" else "BUY"
        if sl > 0:
            _post("/openApi/swap/v2/trade/order", {
                "symbol": par, "side": cl_side, "positionSide": pos_side,
                "type": "STOP_MARKET", "quantity": qty,
                "stopPrice": round(sl, 8), "workingType": "MARK_PRICE", "reduceOnly": "true",
            })
        if tp > 0:
            _post("/openApi/swap/v2/trade/order", {
                "symbol": par, "side": cl_side, "positionSide": pos_side,
                "type": "TAKE_PROFIT_MARKET", "quantity": qty,
                "stopPrice": round(tp, 8), "workingType": "MARK_PRICE", "reduceOnly": "true",
            })
    except Exception as e:
        log.debug(f"_colocar_sl_tp_separados {par}: {e}")


# ═══════════════════════════════════════════════════════
# CERRAR POSICIÓN
# ═══════════════════════════════════════════════════════

def cerrar_posicion(par: str, qty: float, lado: str) -> Optional[dict]:
    precio_actual = get_precio(par)
    if config.MODO_DEMO:
        return {"precio_salida": precio_actual}
    try:
        cl_side  = "SELL" if lado == "LONG" else "BUY"
        pos_side = "LONG" if lado == "LONG" else "SHORT"
        data = _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": cl_side, "positionSide": pos_side,
            "type": "MARKET", "quantity": qty, "reduceOnly": "true",
        })
        if data.get("code", -1) != 0:
            log.warning(f"cerrar {par}: {data.get('msg')} — intentando closePosition")
            data2 = _post("/openApi/swap/v2/trade/closePosition", {
                "symbol": par, "positionSide": pos_side,
            })
            if data2.get("code", -1) == 0:
                return {"precio_salida": precio_actual}
            return {"precio_salida": precio_actual, "error": data.get("msg")}

        order = data.get("data", {}).get("order", {})
        fill  = float(order.get("avgPrice", 0) or order.get("price", 0) or precio_actual)
        return {"precio_salida": fill or precio_actual}
    except Exception as e:
        log.error(f"cerrar_posicion {par}: {e}")
        return {"precio_salida": precio_actual}


# ═══════════════════════════════════════════════════════
# DIAGNÓSTICO DE BALANCE AL ARRANQUE
# ═══════════════════════════════════════════════════════

def diagnostico_balance():
    """Diagnóstico profundo: hex de claves, test spot, test futuros."""
    import json as _json
    log.info("=" * 60)
    log.info("[DIAG] ====== DIAGNÓSTICO COMPLETO BingX ======")

    ak = _api_key()
    sk = _secret_key()

    # ── 1. Verificar chars invisibles en las claves ──────────────
    if not ak:
        log.error("[DIAG] ❌ BINGX_API_KEY está VACÍA")
        return
    if not sk:
        log.error("[DIAG] ❌ BINGX_SECRET_KEY está VACÍA")
        return

    # Mostrar hex de primeros y últimos 4 bytes para detectar \n \r \x00
    ak_hex_start = ak[:4].encode().hex()
    ak_hex_end   = ak[-4:].encode().hex()
    sk_hex_start = sk[:4].encode().hex()
    sk_hex_end   = sk[-4:].encode().hex()
    log.info(f"[DIAG] API_KEY  len={len(ak)} inicio_hex={ak_hex_start} fin_hex={ak_hex_end}")
    log.info(f"[DIAG] SECRET   len={len(sk)} inicio_hex={sk_hex_start} fin_hex={sk_hex_end}")

    # ── 2. Mostrar query string + firma de prueba ────────────────
    ts_test = _ts()
    q_test  = f"timestamp={ts_test}"
    sig_test = _sign(q_test)
    log.info(f"[DIAG] Query ejemplo: '{q_test}'")
    log.info(f"[DIAG] Firma ejemplo: {sig_test}")

    # ── 3. Test SPOT (sin permisos de futuros) ───────────────────
    # Si esto funciona → API key válida pero sin permiso futuros
    try:
        p = {"timestamp": _ts()}
        q = _build_query(p)
        sig = _sign(q)
        url = f"{BASE_URL}/openApi/spot/v1/account/balance?{q}&signature={sig}"
        r_spot = requests.get(url, headers=_headers(), timeout=10)
        d_spot = r_spot.json()
        log.info(f"[DIAG] SPOT balance code={d_spot.get('code')} msg={d_spot.get('msg','')[:80]}")
    except Exception as e:
        log.warning(f"[DIAG] SPOT test error: {e}")

    # ── 4. Test endpoints FUTUROS ────────────────────────────────
    endpoints = [
        "/openApi/swap/v3/user/balance",
        "/openApi/swap/v2/user/balance",
    ]
    for ep in endpoints:
        try:
            data = _get(ep)
            code = data.get("code", "?")
            msg  = data.get("msg", "")[:100]
            log.info(f"[DIAG] {ep} → code={code} msg={msg}")
            if code == 0:
                log.info(f"[DIAG] ✅ ÉXITO en {ep}: {_json.dumps(data)[:300]}")
        except Exception as e:
            log.info(f"[DIAG] {ep} → ERROR: {e}")

    log.info("[DIAG] ====== FIN DIAGNÓSTICO ======")
    log.info("=" * 60)
