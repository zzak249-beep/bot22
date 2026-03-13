"""
exchange.py — BingX Perpetual Futures API
SMC Bot v5.0 — FIX balance + debug logging
"""
import hashlib
import hmac
import json
import logging
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


def _praseParam(params: dict) -> str:
    """
    Construye el query string para firmar — patrón oficial BingX.
    stopLoss/takeProfit se serializan como JSON string dentro de la query.
    """
    parts = []
    for k, v in params.items():
        if isinstance(v, dict):
            # Objetos como stopLoss/takeProfit → JSON string en la URL
            parts.append(f"{k}={json.dumps(v, separators=(',', ':'))}")
        else:
            parts.append(f"{k}={v}")
    return "&".join(parts)


def _sign(query_string: str) -> str:
    """Firma HMAC-SHA256 sobre el query string completo."""
    return hmac.new(
        config.BINGX_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _headers() -> dict:
    return {
        "X-BX-APIKEY": config.BINGX_API_KEY,
    }


def _get(path: str, params: Optional[dict] = None) -> dict:
    p = dict(params or {})
    p["timestamp"] = _ts()
    qs = _praseParam(p)
    sig = _sign(qs)
    url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    try:
        r = requests.get(url, headers=_headers(), timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"GET {path}: {e}")
        return {}


def _post(path: str, params: Optional[dict] = None) -> dict:
    """
    Patrón oficial BingX: todos los params van en la URL como query string.
    El body está vacío. La firma se hace sobre el query string completo.
    """
    p = dict(params or {})
    p["timestamp"] = _ts()
    qs = _praseParam(p)
    sig = _sign(qs)
    url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    try:
        r = requests.post(url, headers=_headers(), data={}, timeout=12)
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

def _try_balance_endpoint(path: str) -> tuple:
    """
    Retorna (balance_total, margen_disponible) desde el endpoint.
    balance_total  = campo 'balance' o 'equity' (dinero total en la cuenta)
    margen_disponible = campo 'availableMargin' (libre para nuevas órdenes)
    """
    try:
        data = _get(path)
        code = data.get("code", -1)
        msg  = data.get("msg", "")
        raw  = json.dumps(data)[:400]
        log.info(f"[BAL-RAW] {path} code={code} msg={msg} → {raw}")

        if code != 0:
            return -1.0, -1.0

        d   = data.get("data", {})
        bal = d.get("balance") if isinstance(d, dict) else None

        total_val = -1.0
        avail_val = -1.0

        if isinstance(bal, dict):
            # balance total: preferir 'balance' → 'equity'
            for k in ("balance", "equity"):
                v = bal.get(k)
                if v is not None:
                    try:
                        f = float(v)
                        if f >= 0:
                            total_val = f
                            break
                    except Exception:
                        pass
            # margen disponible
            for k in ("availableMargin", "freeMargin", "available", "maxWithdrawAmount"):
                v = bal.get(k)
                if v is not None:
                    try:
                        f = float(v)
                        if f >= 0:
                            avail_val = f
                            break
                    except Exception:
                        pass
            if total_val >= 0:
                log.info(f"[BAL] {path} total={total_val:.2f} disponible={avail_val:.2f}")
                return total_val, avail_val

        # Caso 2: data es dict directo
        if isinstance(d, dict) and "availableMargin" in d:
            avail = float(d.get("availableMargin", 0))
            total = float(d.get("balance", d.get("equity", avail)))
            return total, avail

        # Caso 3: data es lista
        if isinstance(d, list):
            for item in d:
                asset = str(item.get("asset", item.get("currency", ""))).upper()
                if asset in ("USDT", ""):
                    t = item.get("balance", item.get("equity"))
                    a = item.get("availableMargin", item.get("freeMargin", item.get("available")))
                    if t is not None:
                        return float(t), float(a) if a is not None else float(t)

        log.warning(f"[BAL] {path} no se encontró campo de balance en: {raw}")
        return -1.0, -1.0

    except Exception as e:
        log.warning(f"[BAL] {path} excepción: {e}")
        return -1.0, -1.0


def get_balance() -> float:
    """Balance TOTAL de la cuenta (para display y reporte). Incluye margen en uso."""
    if config.MODO_DEMO:
        return 200.0

    endpoints = [
        "/openApi/swap/v2/user/balance",
        "/openApi/swap/v3/user/balance",
        "/openApi/account/v1/balance",
    ]

    for ep in endpoints:
        total, avail = _try_balance_endpoint(ep)
        if total >= 0:
            log.info(f"[BAL] ✅ Balance total: ${total:.2f} | Disponible: ${avail:.2f} desde {ep}")
            return total

    log.warning("[BAL] Todos los endpoints fallaron — retornando 0")
    return 0.0


def get_available_margin() -> float:
    """
    Margen DISPONIBLE para abrir nuevas posiciones.
    Usar SOLO en ejecutar_senal() para verificar si hay fondos libres.
    """
    if config.MODO_DEMO:
        return 200.0

    endpoints = [
        "/openApi/swap/v2/user/balance",
        "/openApi/swap/v3/user/balance",
        "/openApi/account/v1/balance",
    ]

    for ep in endpoints:
        total, avail = _try_balance_endpoint(ep)
        if total >= 0:
            # Si no se pudo leer availableMargin, asumir conservadoramente
            return avail if avail >= 0 else total
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
            params["stopLoss"] = {
                "type": "STOP_MARKET", "stopPrice": round(sl, 8),
                "price": round(sl, 8), "workingType": "MARK_PRICE",
            }
        if tp > 0:
            params["takeProfit"] = {
                "type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, 8),
                "price": round(tp, 8), "workingType": "MARK_PRICE",
            }

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
            params["stopLoss"] = {
                "type": "STOP_MARKET", "stopPrice": round(sl, 8),
                "price": round(sl, 8), "workingType": "MARK_PRICE",
            }
        if tp > 0:
            params["takeProfit"] = {
                "type": "TAKE_PROFIT_MARKET", "stopPrice": round(tp, 8),
                "price": round(tp, 8), "workingType": "MARK_PRICE",
            }

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
    """Llama a todos los endpoints y loguea la respuesta RAW completa.
    Llamar una vez al arrancar para identificar qué endpoint funciona."""
    import json as _json
    log.info("=" * 60)
    log.info("[DIAG-BAL] Iniciando diagnóstico de balance BingX...")
    endpoints = [
        "/openApi/swap/v2/user/balance",
        "/openApi/swap/v3/user/balance",
        "/openApi/account/v1/balance",
        "/openApi/swap/v2/user/margin",
    ]
    for ep in endpoints:
        try:
            data = _get(ep)
            log.info(f"[DIAG-BAL] {ep} → {_json.dumps(data)[:500]}")
        except Exception as e:
            log.info(f"[DIAG-BAL] {ep} → ERROR: {e}")
    log.info("=" * 60)
