"""
exchange.py — Conexión BingX Futures v6
- Firma HMAC sin sorted() (orden de inserción = requerido por BingX)
- parsear_klines soporta dict Y array
- Actualizar SL en exchange (para trailing stop)
- Cierre parcial de posición
"""

import hmac
import hashlib
import time
import requests
from datetime import datetime
import config

BASE_URL = "https://open-api.bingx.com"


# ============================================================
# HELPERS DE FIRMA Y HTTP
# ============================================================

def _sign(query_string: str) -> str:
    return hmac.new(
        config.BINGX_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def _headers() -> dict:
    return {
        "X-BX-APIKEY": config.BINGX_API_KEY,
        "Content-Type": "application/json"
    }


def _build_query(params: dict) -> str:
    """Construye query string en orden de inserción (BingX requiere esto)."""
    return "&".join(f"{k}={v}" for k, v in params.items())


def _get(path: str, params: dict = None, auth: bool = True) -> dict:
    params = params or {}
    if auth:
        params["timestamp"] = int(time.time() * 1000)
        qs  = _build_query(params)
        sig = _sign(qs)
        url = f"{BASE_URL}{path}?{qs}&signature={sig}"
        try:
            r    = requests.get(url, headers=_headers(), timeout=10)
            data = r.json()
            if config.MODO_DEBUG and data.get("code", 0) != 0:
                print(f"[EXCHANGE] API error {path}: code={data.get('code')} | {data.get('msg','')[:100]}")
            return data
        except Exception as e:
            print(f"[EXCHANGE] GET(auth) error {path}: {e}")
            return {"code": -1, "data": None}
    else:
        try:
            r = requests.get(BASE_URL + path, params=params, headers=_headers(), timeout=10)
            return r.json()
        except Exception as e:
            print(f"[EXCHANGE] GET(pub) error {path}: {e}")
            return {"code": -1, "data": None}


def _post(path: str, params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    qs  = _build_query(params)
    sig = _sign(qs)
    url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    try:
        r    = requests.post(url, headers=_headers(), timeout=10)
        data = r.json()
        if config.MODO_DEBUG and data.get("code", 0) != 0:
            print(f"[EXCHANGE] POST error {path}: code={data.get('code')} | {data.get('msg','')[:100]}")
        return data
    except Exception as e:
        print(f"[EXCHANGE] POST error {path}: {e}")
        return {"code": -1}


# ============================================================
# BALANCE
# ============================================================

def get_balance() -> float:
    if config.MODO_DEMO:
        return _demo_balance()

    resp = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"}, auth=True)
    try:
        bal = resp.get("data", {}).get("balance", {})
        if isinstance(bal, dict):
            return float(bal.get("availableMargin", 0))
        if isinstance(bal, list):
            for item in bal:
                if item.get("asset") == "USDT":
                    return float(item.get("availableMargin", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error balance: {e}")
    return 0.0


def get_equity() -> float:
    if config.MODO_DEMO:
        return _demo_balance()

    resp = _get("/openApi/swap/v2/user/balance", {"currency": "USDT"}, auth=True)
    try:
        bal = resp.get("data", {}).get("balance", {})
        if isinstance(bal, dict):
            return float(bal.get("balance", 0))
        if isinstance(bal, list):
            for item in bal:
                if item.get("asset") == "USDT":
                    return float(item.get("balance", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error equity: {e}")
    return 0.0


# ============================================================
# PRECIO Y MERCADO (públicos)
# ============================================================

def get_precio(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/price", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, dict):
            return float(data.get("price", 0))
        if isinstance(data, list) and data:
            return float(data[0].get("price", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error precio {par}: {e}")
    return 0.0


def get_klines(par: str, intervalo: str = "5m", limit: int = 100) -> list:
    resp = _get("/openApi/swap/v3/quote/klines", {
        "symbol": par, "interval": intervalo, "limit": limit
    }, auth=False)
    try:
        data = resp.get("data", [])
        if isinstance(data, list):
            return data
    except Exception as e:
        print(f"[EXCHANGE] Error klines {par}: {e}")
    return []


def get_spread_pct(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/ticker", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, list) and data:
            data = data[0]
        bid = float(data.get("bidPrice", 0))
        ask = float(data.get("askPrice", 0))
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            return ((ask - bid) / mid) * 100
        vol = float(data.get("quoteVolume", 0))
        if vol > 500_000:
            return 0.1
        if vol > 0:
            return 0.5
    except Exception as e:
        print(f"[EXCHANGE] Error spread {par}: {e}")
    return 999.0


def get_volumen_24h(par: str) -> float:
    resp = _get("/openApi/swap/v2/quote/ticker", {"symbol": par}, auth=False)
    try:
        data = resp.get("data", {})
        if isinstance(data, list) and data:
            data = data[0]
        return float(data.get("quoteVolume", 0))
    except Exception as e:
        print(f"[EXCHANGE] Error volumen {par}: {e}")
    return 0.0


# ============================================================
# PARSEAR KLINES — soporta dict Y array (BingX v3)
# ============================================================

def parsear_klines(klines: list) -> dict:
    opens = []; highs = []; lows = []; closes = []; vols = []
    for k in klines:
        try:
            if isinstance(k, dict):
                opens.append( float(k.get("open",   k.get("o", 0))))
                highs.append( float(k.get("high",   k.get("h", 0))))
                lows.append(  float(k.get("low",    k.get("l", 0))))
                closes.append(float(k.get("close",  k.get("c", 0))))
                vols.append(  float(k.get("volume", k.get("v", 0))))
            elif isinstance(k, (list, tuple)) and len(k) >= 6:
                opens.append(float(k[1]))
                highs.append(float(k[2]))
                lows.append( float(k[3]))
                closes.append(float(k[4]))
                vols.append( float(k[5]))
        except (ValueError, TypeError, KeyError):
            continue
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes, "vols": vols}


# ============================================================
# APALANCAMIENTO
# ============================================================

def set_leverage(par: str, leverage: int) -> bool:
    if config.MODO_DEMO:
        return True
    resp = _post("/openApi/swap/v2/trade/leverage", {
        "symbol": par, "side": "LONG", "leverage": leverage
    })
    ok = resp.get("code") == 0
    if config.MODO_DEBUG:
        estado = "ok" if ok else resp.get("msg", "error")
        print(f"[EXCHANGE] Leverage {par} {leverage}x → {estado}")
    return ok


# ============================================================
# CALCULAR CANTIDAD
# ============================================================

def calcular_cantidad(par: str, balance: float, precio: float) -> float:
    if balance <= 0 or precio <= 0:
        return 0.0
    capital  = balance * config.RIESGO_POR_TRADE * config.LEVERAGE
    cantidad = capital / precio
    return round(cantidad, 4) if cantidad >= 0.0001 else 0.0


# ============================================================
# ÓRDENES
# ============================================================

def abrir_long(par: str, cantidad: float, precio_entrada: float,
               sl: float, tp: float) -> dict:
    if config.MODO_DEMO:
        return _demo_orden(par, "BUY", cantidad, precio_entrada, sl, tp)

    # Orden de mercado
    resp = _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "BUY",
        "positionSide": "LONG", "type": "MARKET",
        "quantity": str(cantidad),
    })

    if resp.get("code") != 0:
        print(f"[EXCHANGE] Error LONG {par}: {resp}")
        return {}

    order_id = str(resp.get("data", {}).get("orderId", ""))

    # Stop Loss
    _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "SELL", "positionSide": "LONG",
        "type": "STOP_MARKET", "quantity": str(cantidad),
        "stopPrice": str(round(sl, 6)), "workingType": "MARK_PRICE"
    })

    # Take Profit
    _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "SELL", "positionSide": "LONG",
        "type": "TAKE_PROFIT_MARKET", "quantity": str(cantidad),
        "stopPrice": str(round(tp, 6)), "workingType": "MARK_PRICE"
    })

    if config.MODO_DEBUG:
        print(f"[EXCHANGE] LONG {par} qty:{cantidad} SL:{sl:.6f} TP:{tp:.6f}")

    return {
        "order_id": order_id, "par": par, "lado": "LONG",
        "cantidad": cantidad, "precio_entrada": precio_entrada,
        "sl": sl, "tp": tp, "timestamp": datetime.now().isoformat()
    }


def actualizar_sl(par: str, cantidad: float, nuevo_sl: float) -> bool:
    """
    Cancela el SL existente y pone uno nuevo.
    Usado por trailing stop y breakeven.
    """
    if config.MODO_DEMO:
        if config.MODO_DEBUG:
            print(f"[DEMO] Actualizar SL {par} → {nuevo_sl:.6f}")
        return True

    # Cancelar órdenes abiertas del par (SL anterior)
    cancelar_ordenes_abiertas(par)

    # Poner nuevo SL
    resp = _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "SELL", "positionSide": "LONG",
        "type": "STOP_MARKET", "quantity": str(cantidad),
        "stopPrice": str(round(nuevo_sl, 6)), "workingType": "MARK_PRICE"
    })

    ok = resp.get("code") == 0
    if config.MODO_DEBUG:
        estado = "ok" if ok else resp.get("msg", "error")
        print(f"[EXCHANGE] SL actualizado {par} → {nuevo_sl:.6f} [{estado}]")
    return ok


def cerrar_parcial(par: str, cantidad_parcial: float) -> dict:
    """
    Cierra una fracción de la posición (para cierre parcial al TP50).
    """
    if config.MODO_DEMO:
        precio = get_precio(par)
        if config.MODO_DEBUG:
            print(f"[DEMO] Cierre parcial {par} qty:{cantidad_parcial} @ {precio:.6f}")
        return {"order_id": f"demo_parcial_{int(time.time())}", "precio_salida": precio}

    resp = _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "SELL",
        "positionSide": "LONG", "type": "MARKET",
        "quantity": str(round(cantidad_parcial, 4)),
    })

    if resp.get("code") != 0:
        print(f"[EXCHANGE] Error cierre parcial {par}: {resp}")
        return {}

    return {
        "order_id":     str(resp.get("data", {}).get("orderId", "")),
        "precio_salida": get_precio(par)
    }


def cerrar_posicion(par: str, cantidad: float) -> dict:
    if config.MODO_DEMO:
        return {"order_id": f"demo_close_{int(time.time())}", "precio_salida": get_precio(par)}

    resp = _post("/openApi/swap/v2/trade/order", {
        "symbol": par, "side": "SELL",
        "positionSide": "LONG", "type": "MARKET",
        "quantity": str(cantidad),
    })

    if resp.get("code") != 0:
        print(f"[EXCHANGE] Error cerrando {par}: {resp}")
        return {}

    cancelar_ordenes_abiertas(par)
    return {
        "order_id":     str(resp.get("data", {}).get("orderId", "")),
        "precio_salida": get_precio(par)
    }


def cancelar_ordenes_abiertas(par: str):
    if config.MODO_DEMO:
        return
    _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": par})


def get_posiciones_abiertas() -> list:
    if config.MODO_DEMO:
        return _demo_posiciones()
    resp = _get("/openApi/swap/v2/user/positions", {}, auth=True)
    try:
        posiciones = resp.get("data", []) or []
        return [p for p in posiciones if float(p.get("positionAmt", 0)) != 0]
    except:
        return []


def get_posicion(par: str) -> dict:
    for p in get_posiciones_abiertas():
        if p.get("symbol") == par:
            return p
    return {}


# ============================================================
# MODO DEMO
# ============================================================

_demo_state = {"balance": None}
_demo_pos   = {}


def _demo_balance() -> float:
    if _demo_state["balance"] is None:
        _demo_state["balance"] = config.BALANCE_INICIAL
    return _demo_state["balance"]


def demo_actualizar_balance(pnl: float):
    _demo_state["balance"] = _demo_balance() + pnl
    print(f"[DEMO] Balance: ${_demo_state['balance']:.2f} (PnL: ${pnl:+.4f})")


def _demo_orden(par, lado, cantidad, precio, sl, tp) -> dict:
    oid = f"demo_{int(time.time())}"
    _demo_pos[par] = {
        "par": par, "lado": lado, "cantidad": cantidad,
        "precio_entrada": precio, "sl": sl, "tp": tp,
        "order_id": oid, "timestamp": datetime.now().isoformat()
    }
    print(f"[DEMO] {lado} {par} qty:{cantidad} entrada:{precio:.6f} SL:{sl:.6f} TP:{tp:.6f}")
    return _demo_pos[par].copy()


def _demo_posiciones() -> list:
    return list(_demo_pos.values())

import logging
log = logging.getLogger("exchange")

# ══════════════════════════════════════════════════════════
# WRAPPER CCXT-COMPATIBLE (main.py v6 usa fetch_ohlcv)
# ══════════════════════════════════════════════════════════

class _BingXCCXT:
    def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 200) -> list:
        par    = symbol.replace("/", "-")
        klines = get_klines(par, intervalo=timeframe, limit=limit)
        result = []
        for k in klines:
            try:
                if isinstance(k, dict):
                    ts = int(k.get("time", k.get("t", 0)))
                    o  = float(k.get("open",   k.get("o", 0)))
                    h  = float(k.get("high",   k.get("h", 0)))
                    l  = float(k.get("low",    k.get("l", 0)))
                    c  = float(k.get("close",  k.get("c", 0)))
                    v  = float(k.get("volume", k.get("v", 0)))
                elif isinstance(k, (list, tuple)) and len(k) >= 6:
                    ts, o, h, l, c, v = int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
                else:
                    continue
                result.append([ts, o, h, l, c, v])
            except Exception:
                continue
        return result

    def load_markets(self):
        return {}

_exchange_instance = None

def get_exchange() -> _BingXCCXT:
    global _exchange_instance
    if _exchange_instance is None:
        _exchange_instance = _BingXCCXT()
    return _exchange_instance


# ══════════════════════════════════════════════════════════
# get_open_positions — formato que espera main.py
# ══════════════════════════════════════════════════════════

def get_open_positions() -> list:
    raw    = get_posiciones_abiertas()
    result = []
    for p in raw:
        sym      = p.get("symbol", "")
        sym_ccxt = sym.replace("-", "/")
        side_raw = p.get("positionSide", p.get("side", "LONG")).upper()
        side     = "long" if side_raw == "LONG" else "short"
        result.append({
            "symbol":  sym_ccxt,
            "current": float(p.get("markPrice", p.get("avgPrice", 0))),
            "side":    side,
            "qty":     abs(float(p.get("positionAmt", 0))),
        })
    return result


# ══════════════════════════════════════════════════════════
# open_long — abre LONG real en BingX Futuros
# ══════════════════════════════════════════════════════════

def open_long(symbol: str, sig: dict) -> dict | None:
    try:
        par     = symbol.replace("/", "-")
        balance = get_balance()
        precio  = sig.get("entry", get_precio(par))
        cant    = calcular_cantidad(par, balance, precio)
        if cant <= 0:
            log.warning(f"open_long {symbol}: cantidad=0 balance=${balance:.2f}")
            return None

        # Configurar apalancamiento para LONG
        _post("/openApi/swap/v2/trade/leverage", {
            "symbol": par, "side": "LONG", "leverage": str(config.LEVERAGE)
        })

        # Orden de mercado LONG
        resp = _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": "BUY",
            "positionSide": "LONG", "type": "MARKET",
            "quantity": str(cant),
        })

        if resp.get("code") != 0:
            log.error(f"open_long {symbol}: {resp.get('msg', resp)}")
            return None

        order_id = str(resp.get("data", {}).get("orderId", ""))

        # SL automático
        _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": "SELL", "positionSide": "LONG",
            "type": "STOP_MARKET", "quantity": str(cant),
            "stopPrice": str(round(sig["sl"], 8)),
            "workingType": "MARK_PRICE"
        })

        # TP automático
        _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": "SELL", "positionSide": "LONG",
            "type": "TAKE_PROFIT_MARKET", "quantity": str(cant),
            "stopPrice": str(round(sig["tp"], 8)),
            "workingType": "MARK_PRICE"
        })

        log.info(f"LONG ABIERTO {par} qty:{cant} entrada:{precio} SL:{sig['sl']:.8f} TP:{sig['tp']:.8f}")
        return {"side": "long", "qty": cant, "order_id": order_id}

    except Exception as e:
        log.error(f"open_long {symbol}: {e}")
        return None


# ══════════════════════════════════════════════════════════
# open_short — abre SHORT real en BingX Futuros
# ══════════════════════════════════════════════════════════

def open_short(symbol: str, sig: dict) -> dict | None:
    try:
        par     = symbol.replace("/", "-")
        balance = get_balance()
        precio  = sig.get("entry", get_precio(par))
        cant    = calcular_cantidad(par, balance, precio)
        if cant <= 0:
            log.warning(f"open_short {symbol}: cantidad=0 balance=${balance:.2f}")
            return None

        # Configurar apalancamiento para SHORT
        _post("/openApi/swap/v2/trade/leverage", {
            "symbol": par, "side": "SHORT", "leverage": str(config.LEVERAGE)
        })

        # Orden de mercado SHORT
        resp = _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": "SELL",
            "positionSide": "SHORT", "type": "MARKET",
            "quantity": str(cant),
        })

        if resp.get("code") != 0:
            log.error(f"open_short {symbol}: {resp.get('msg', resp)}")
            return None

        order_id = str(resp.get("data", {}).get("orderId", ""))

        # SL automático SHORT
        _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": "BUY", "positionSide": "SHORT",
            "type": "STOP_MARKET", "quantity": str(cant),
            "stopPrice": str(round(sig["sl"], 8)),
            "workingType": "MARK_PRICE"
        })

        # TP automático SHORT
        _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": "BUY", "positionSide": "SHORT",
            "type": "TAKE_PROFIT_MARKET", "quantity": str(cant),
            "stopPrice": str(round(sig["tp"], 8)),
            "workingType": "MARK_PRICE"
        })

        log.info(f"SHORT ABIERTO {par} qty:{cant} entrada:{precio} SL:{sig['sl']:.8f} TP:{sig['tp']:.8f}")
        return {"side": "short", "qty": cant, "order_id": order_id}

    except Exception as e:
        log.error(f"open_short {symbol}: {e}")
        return None


# ══════════════════════════════════════════════════════════
# close_long / close_short — cierran posición real
# ══════════════════════════════════════════════════════════

def close_long(symbol: str, qty: float) -> bool:
    try:
        par  = symbol.replace("/", "-")
        resp = _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": "SELL",
            "positionSide": "LONG", "type": "MARKET",
            "quantity": str(round(qty, 4)),
        })
        ok = resp.get("code") == 0
        if ok:
            # Cancelar SL/TP pendientes del LONG
            _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": par})
            log.info(f"LONG CERRADO {par} qty:{qty}")
        else:
            log.error(f"close_long {symbol}: {resp.get('msg', resp)}")
        return ok
    except Exception as e:
        log.error(f"close_long {symbol}: {e}")
        return False


def close_short(symbol: str, qty: float) -> bool:
    try:
        par  = symbol.replace("/", "-")
        resp = _post("/openApi/swap/v2/trade/order", {
            "symbol": par, "side": "BUY",
            "positionSide": "SHORT", "type": "MARKET",
            "quantity": str(round(qty, 4)),
        })
        ok = resp.get("code") == 0
        if ok:
            _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": par})
            log.info(f"SHORT CERRADO {par} qty:{qty}")
        else:
            log.error(f"close_short {symbol}: {resp.get('msg', resp)}")
        return ok
    except Exception as e:
        log.error(f"close_short {symbol}: {e}")
        return False
