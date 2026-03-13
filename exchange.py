"""
exchange.py — BingX Perpetual Futures API v2/v3
SMC Bot v5.0 [MetaClaw Edition]
"""
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import requests

import config

log = logging.getLogger("exchange")

BASE_URL = "https://open-api.bingx.com"
_CONTRATOS_FUTURES: set = set()
_blocked_pairs: set = set()
_time_offset: int = 0
_QTY_PRECISION: dict = {}   # par → decimales
_MIN_QTY: dict = {}         # par → cantidad mínima


# ═══════════════════════════════════════════════════════
# FIRMA / AUTH
# ═══════════════════════════════════════════════════════

def _ts() -> int:
    return int(time.time() * 1000) + _time_offset


def _sign(params: dict) -> str:
    parts = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(
        config.BINGX_SECRET_KEY.encode("utf-8"),
        parts.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _headers() -> dict:
    return {
        "X-BX-APIKEY": config.BINGX_API_KEY,
        "Content-Type": "application/json",
    }


def _get(path: str, params: Optional[dict] = None) -> dict:
    p = dict(params or {})
    p["timestamp"] = _ts()
    p["signature"] = _sign(p)
    try:
        r = requests.get(f"{BASE_URL}{path}", params=p, headers=_headers(), timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"GET {path}: {e}")
        return {}


def _post(path: str, params: Optional[dict] = None) -> dict:
    p = dict(params or {})
    p["timestamp"] = _ts()
    p["signature"] = _sign(p)
    try:
        r = requests.post(f"{BASE_URL}{path}", params=p, headers=_headers(), timeout=12)
        return r.json()
    except Exception as e:
        log.error(f"POST {path}: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# SINCRONIZAR TIEMPO
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
        r = requests.get(
            f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=12
        )
        contratos = r.json().get("data", []) or []
        _CONTRATOS_FUTURES = set()
        for c in contratos:
            sym = c.get("symbol", "")
            if not sym:
                continue
            _CONTRATOS_FUTURES.add(sym)
            # Guardar precisión de cantidad
            qty_prec = int(c.get("quantityPrecision", 4))
            _QTY_PRECISION[sym] = qty_prec
            min_qty = float(c.get("minQty", 0.0001) or 0.0001)
            _MIN_QTY[sym] = min_qty
        log.info(f"[CONTRATOS] {len(_CONTRATOS_FUTURES)} pares perpetuos")
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
            params={"symbol": par},
            timeout=6,
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
                        "ts":     int(c[0]),
                        "open":   float(c[1]),
                        "high":   float(c[2]),
                        "low":    float(c[3]),
                        "close":  float(c[4]),
                        "volume": float(c[5]),
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
        # Ordenar por timestamp ascendente
        result.sort(key=lambda x: x["ts"])
        return result
    except Exception as e:
        log.debug(f"get_candles {par}/{tf}: {e}")
        return []


# ═══════════════════════════════════════════════════════
# BALANCE
# ═══════════════════════════════════════════════════════

def get_balance() -> float:
    if config.MODO_DEMO:
        return 200.0
    try:
        data = _get("/openApi/swap/v2/user/balance")
        bal = data.get("data", {}).get("balance", {})
        if isinstance(bal, dict):
            return float(
                bal.get("availableMargin",
                bal.get("freeMargin",
                bal.get("available",
                bal.get("balance", 0)))) or 0
            )
        if isinstance(bal, list):
            for b in bal:
                if b.get("asset", "").upper() == "USDT":
                    return float(b.get("availableMargin", b.get("balance", 0)) or 0)
        return 0.0
    except Exception as e:
        log.error(f"get_balance: {e}")
        return 0.0


# ═══════════════════════════════════════════════════════
# POSICIONES
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
            log.debug(f"leverage {par} {lado}: {res.get('msg', res)}")
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
            "symbol":       par,
            "side":         "BUY",
            "positionSide": "LONG",
            "type":         "MARKET",
            "quantity":     qty,
        }
        # SL y TP como JSON string
        if sl > 0:
            sl_price = round(sl, 8)
            params["stopLoss"] = json.dumps({
                "type":        "STOP_MARKET",
                "stopPrice":   sl_price,
                "price":       sl_price,
                "workingType": "MARK_PRICE",
            })
        if tp > 0:
            tp_price = round(tp, 8)
            params["takeProfit"] = json.dumps({
                "type":        "TAKE_PROFIT_MARKET",
                "stopPrice":   tp_price,
                "price":       tp_price,
                "workingType": "MARK_PRICE",
            })

        data = _post("/openApi/swap/v2/trade/order", params)
        if data.get("code", -1) != 0:
            err = data.get("msg", str(data))
            log.warning(f"abrir_long {par}: {err}")
            # Retry sin SL/TP si fue error de parámetros
            if "parameter" in err.lower() or "invalid" in err.lower():
                params.pop("stopLoss", None)
                params.pop("takeProfit", None)
                data = _post("/openApi/swap/v2/trade/order", params)
                if data.get("code", -1) != 0:
                    return {"error": data.get("msg", str(data))}
                # Poner SL/TP como órdenes separadas si la entrada funcionó
                _colocar_sl_tp_separados(par, sl, tp, "LONG", qty)
            else:
                return {"error": err}

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
            "symbol":       par,
            "side":         "SELL",
            "positionSide": "SHORT",
            "type":         "MARKET",
            "quantity":     qty,
        }
        if sl > 0:
            sl_price = round(sl, 8)
            params["stopLoss"] = json.dumps({
                "type":        "STOP_MARKET",
                "stopPrice":   sl_price,
                "price":       sl_price,
                "workingType": "MARK_PRICE",
            })
        if tp > 0:
            tp_price = round(tp, 8)
            params["takeProfit"] = json.dumps({
                "type":        "TAKE_PROFIT_MARKET",
                "stopPrice":   tp_price,
                "price":       tp_price,
                "workingType": "MARK_PRICE",
            })

        data = _post("/openApi/swap/v2/trade/order", params)
        if data.get("code", -1) != 0:
            err = data.get("msg", str(data))
            log.warning(f"abrir_short {par}: {err}")
            if "parameter" in err.lower() or "invalid" in err.lower():
                params.pop("stopLoss", None)
                params.pop("takeProfit", None)
                data = _post("/openApi/swap/v2/trade/order", params)
                if data.get("code", -1) != 0:
                    return {"error": data.get("msg", str(data))}
                _colocar_sl_tp_separados(par, sl, tp, "SHORT", qty)
            else:
                return {"error": err}

        order = data.get("data", {}).get("order", {})
        fill  = float(order.get("avgPrice", 0) or order.get("price", 0) or precio)
        qty_r = float(order.get("executedQty", qty) or qty)
        log.info(f"✅ SHORT {par} fill={fill:.6f} qty={qty_r}")
        return {"fill_price": fill or precio, "executedQty": qty_r or qty}
    except Exception as e:
        log.error(f"abrir_short {par}: {e}")
        return {"error": str(e)}


def _colocar_sl_tp_separados(par: str, sl: float, tp: float, lado: str, qty: float):
    """Fallback: coloca SL y TP como órdenes separadas."""
    try:
        pos_side = "LONG" if lado == "LONG" else "SHORT"
        cl_side  = "SELL" if lado == "LONG" else "BUY"
        if sl > 0:
            _post("/openApi/swap/v2/trade/order", {
                "symbol":       par,
                "side":         cl_side,
                "positionSide": pos_side,
                "type":         "STOP_MARKET",
                "quantity":     qty,
                "stopPrice":    round(sl, 8),
                "workingType":  "MARK_PRICE",
                "reduceOnly":   "true",
            })
        if tp > 0:
            _post("/openApi/swap/v2/trade/order", {
                "symbol":       par,
                "side":         cl_side,
                "positionSide": pos_side,
                "type":         "TAKE_PROFIT_MARKET",
                "quantity":     qty,
                "stopPrice":    round(tp, 8),
                "workingType":  "MARK_PRICE",
                "reduceOnly":   "true",
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
            "symbol":       par,
            "side":         cl_side,
            "positionSide": pos_side,
            "type":         "MARKET",
            "quantity":     qty,
            "reduceOnly":   "true",
        })
        if data.get("code", -1) != 0:
            # Intentar closePosition
            log.warning(f"cerrar_posicion {par} market fail: {data.get('msg')} — intentando closePosition")
            data2 = _post("/openApi/swap/v2/trade/closePosition", {
                "symbol":       par,
                "positionSide": pos_side,
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
