#!/usr/bin/env python3
"""
exchange.py — BingX Perpetual Futures API
✅ Balance fix: lee walletBalance (no availableMargin)
✅ Compound activado: usa % del balance real
"""

import hashlib
import hmac
import time
import requests
from urllib.parse import urlencode
import config

BASE_URL = "https://open-api.bingx.com"


def _sign(params: dict) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(
        config.BINGX_SECRET_KEY.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()


def _get(path: str, params: dict = None) -> dict:
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    headers = {"X-BX-APIKEY": config.BINGX_API_KEY}
    try:
        r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        print(f"[GET ERROR] {path}: {e}")
        return {}


def _post(path: str, params: dict = None) -> dict:
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    headers = {"X-BX-APIKEY": config.BINGX_API_KEY}
    try:
        r = requests.post(BASE_URL + path, params=params, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        print(f"[POST ERROR] {path}: {e}")
        return {}


def get_balance() -> float:
    """
    Obtiene balance real de la cuenta.
    ✅ Lee walletBalance (balance total), NO availableMargin
    """
    if config.MODO_DEMO:
        return config.BALANCE_INICIAL

    data = _get("/openApi/swap/v2/user/balance")

    try:
        balance_data = data.get("data", {}).get("balance", {})

        # Prioridad de campos - balance total primero
        for campo in ["balance", "walletBalance", "equity"]:
            val = balance_data.get(campo)
            if val is not None:
                b = float(val)
                if b > 0:
                    print(f"[BALANCE] ✓ ${b:.2f} (campo: {campo})")
                    return b

        # Solo como último recurso
        val = balance_data.get("availableMargin")
        if val is not None:
            b = float(val)
            print(f"[BALANCE] ⚠️ ${b:.2f} (availableMargin - puede ser incorrecto)")
            return b

    except Exception as e:
        print(f"[BALANCE ERROR] {e} | raw: {data}")

    return 0.0


def get_precio(symbol: str) -> float:
    """Precio actual del par"""
    data = _get("/openApi/swap/v2/quote/price", {"symbol": symbol})
    try:
        return float(data["data"]["price"])
    except Exception:
        return 0.0


def get_klines(symbol: str, interval: str = "15m", limit: int = 100) -> list:
    """Velas OHLCV"""
    data = _get("/openApi/swap/v2/quote/klines", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    })
    try:
        return data.get("data", [])
    except Exception:
        return []


def set_leverage(symbol: str, leverage: int) -> bool:
    """Configura leverage del par"""
    r = _post("/openApi/swap/v2/trade/leverage", {
        "symbol": symbol,
        "side": "LONG",
        "leverage": leverage
    })
    r2 = _post("/openApi/swap/v2/trade/leverage", {
        "symbol": symbol,
        "side": "SHORT",
        "leverage": leverage
    })
    return True


def calcular_cantidad(symbol: str, precio: float, balance: float, riesgo_pct: float = None) -> float:
    """
    Calcula cantidad con COMPOUND activado.
    Usa % del balance real en cada trade.
    """
    if riesgo_pct is None:
        riesgo_pct = config.RIESGO_POR_TRADE

    # COMPOUND: usa balance real * % riesgo
    margen_usd = balance * riesgo_pct  # COMPOUND
    valor_posicion = margen_usd * config.LEVERAGE

    if precio <= 0:
        return 0.0

    cantidad = valor_posicion / precio

    # Redondear a 1 decimal (mayoría de pares BingX)
    cantidad = round(cantidad, 1)

    return max(cantidad, 0.1)


def abrir_long(symbol: str, cantidad: float, sl: float, tp: float) -> dict:
    """Abre posición LONG"""
    if config.MODO_DEMO:
        print(f"[DEMO] LONG {symbol} qty={cantidad} sl={sl:.4f} tp={tp:.4f}")
        return {"demo": True, "orderId": "demo_long"}

    set_leverage(symbol, config.LEVERAGE)

    params = {
        "symbol": symbol,
        "side": "BUY",
        "positionSide": "LONG",
        "type": "MARKET",
        "quantity": cantidad,
        "stopLoss": f'{{"type":"MARK_PRICE","stopPrice":{sl:.4f},"price":{sl:.4f},"workingType":"MARK_PRICE"}}',
        "takeProfit": f'{{"type":"MARK_PRICE","stopPrice":{tp:.4f},"price":{tp:.4f},"workingType":"MARK_PRICE"}}',
    }
    return _post("/openApi/swap/v2/trade/order", params)


def abrir_short(symbol: str, cantidad: float, sl: float, tp: float) -> dict:
    """Abre posición SHORT"""
    if config.MODO_DEMO:
        print(f"[DEMO] SHORT {symbol} qty={cantidad} sl={sl:.4f} tp={tp:.4f}")
        return {"demo": True, "orderId": "demo_short"}

    set_leverage(symbol, config.LEVERAGE)

    params = {
        "symbol": symbol,
        "side": "SELL",
        "positionSide": "SHORT",
        "type": "MARKET",
        "quantity": cantidad,
        "stopLoss": f'{{"type":"MARK_PRICE","stopPrice":{sl:.4f},"price":{sl:.4f},"workingType":"MARK_PRICE"}}',
        "takeProfit": f'{{"type":"MARK_PRICE","stopPrice":{tp:.4f},"price":{tp:.4f},"workingType":"MARK_PRICE"}}',
    }
    return _post("/openApi/swap/v2/trade/order", params)


def cerrar_posicion(symbol: str, lado: str) -> dict:
    """Cierra posición abierta"""
    if config.MODO_DEMO:
        print(f"[DEMO] CERRAR {symbol} {lado}")
        return {"demo": True}

    position_side = "LONG" if lado == "LONG" else "SHORT"
    side = "SELL" if lado == "LONG" else "BUY"

    params = {
        "symbol": symbol,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "closePosition": "true"
    }
    return _post("/openApi/swap/v2/trade/order", params)


def get_posiciones_abiertas() -> list:
    """Lista posiciones abiertas"""
    if config.MODO_DEMO:
        return []
    data = _get("/openApi/swap/v2/user/positions")
    try:
        posiciones = data.get("data", [])
        return [p for p in posiciones if float(p.get("positionAmt", 0)) != 0]
    except Exception:
        return []


def get_info_par(symbol: str) -> dict:
    """Info del par: precio, volumen, spread"""
    data = _get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
    try:
        d = data.get("data", {})
        return {
            "precio": float(d.get("lastPrice", 0)),
            "volumen_24h": float(d.get("volume", 0)) * float(d.get("lastPrice", 1)),
            "bid": float(d.get("bidPrice", 0)),
            "ask": float(d.get("askPrice", 0)),
        }
    except Exception:
        return {}


def get_todos_los_pares() -> list:
    """Lista todos los pares disponibles en BingX"""
    data = _get("/openApi/swap/v2/quote/contracts")
    try:
        contratos = data.get("data", [])
        return [c["symbol"] for c in contratos if c.get("symbol", "").endswith("-USDT")]
    except Exception:
        return []
