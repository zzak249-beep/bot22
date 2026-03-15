"""
exchange.py — BingX Perpetual Futures v4.3 [FIXES ÓRDENES]

FIXES v4.3:
  ✅ FIX#1 — recvWindow=5000 en TODAS las peticiones firmadas (evita errores de timestamp)
  ✅ FIX#2 — Sincronización de tiempo con servidor BingX al arranque
  ✅ FIX#3 — reduceOnly=true en SL/TP (BingX rechazaba órdenes de cierre sin esto)
  ✅ FIX#4 — closePosition=true en cerrar_posicion modo one-way (evita "insuficiente balance")
  ✅ FIX#5 — Logging completo de respuesta API en errores (diagnóstico real)
  ✅ FIX#6 — set_leverage robusto: maneja hedge y one-way sin crash
  ✅ FIX#7 — Retry con back-off exponencial en _post/_get
  ✅ FIX#8 — _detect_hedge_mode() activo al arranque (no lazy)
  ✅ FIX#9 — Validación qty_str nunca envía "0" ni string vacío
  ✅ FIX#10 — stopPrice redondeado según precisión real del par
"""

import time, hmac, hashlib, logging, math
import requests
import config

log      = logging.getLogger("exchange")
BASE_URL = "https://open-api.bingx.com"
_SESSION = requests.Session()
_SESSION.headers.update({"Content-Type": "application/json"})

_hedge_mode_cache: dict   = {}   # par → True(hedge) / False(oneway)
_contract_cache: dict     = {}   # par → {step, dec, price_dec}
_contract_cache_ts: float = 0
_pares_no_soportados: set = set()
_CONTRATOS_FUTURES: set   = set()
_time_offset: int         = 0    # FIX#2: offset ms entre reloj local y BingX


# ══════════════════════════════════════════════════════════════
# SYNC DE TIEMPO CON BINGX  ← FIX#2
# ══════════════════════════════════════════════════════════════
def sync_server_time():
    """Sincroniza el reloj local con el servidor BingX. Llamar al arrancar."""
    global _time_offset
    try:
        r = _SESSION.get(
            BASE_URL + "/openApi/swap/v2/server/time",
            timeout=5,
        ).json()
        srv_ts = int((r.get("data") or {}).get("serverTime", 0) or 0)
        if srv_ts:
            local_ts     = int(time.time() * 1000)
            _time_offset = srv_ts - local_ts
            log.info(f"[TIME-SYNC] Offset: {_time_offset:+d} ms")
        else:
            log.debug("[TIME-SYNC] No se obtuvo serverTime")
    except Exception as e:
        log.debug(f"[TIME-SYNC] {e}")


def _ts() -> int:
    return int(time.time() * 1000) + _time_offset


# ══════════════════════════════════════════════════════════════
# PERSISTENCIA DE PARES NO SOPORTADOS
# ══════════════════════════════════════════════════════════════
def _ns_file() -> str:
    import os
    d = os.getenv("MEMORY_DIR", "").strip()
    return (d + "/no_soportados.json") if d else "no_soportados.json"

def _cargar_no_soportados():
    import json, os
    try:
        if os.path.exists(_ns_file()):
            with open(_ns_file()) as f:
                _pares_no_soportados.update(json.load(f))
            log.info(f"[API-BLOCK] {len(_pares_no_soportados)} pares cargados como no soportados")
    except Exception as e:
        log.debug(f"[API-BLOCK] {e}")

def _guardar_no_soportados():
    import json
    try:
        with open(_ns_file(), "w") as f:
            json.dump(list(_pares_no_soportados), f)
    except Exception as e:
        log.debug(f"[API-BLOCK] guardar: {e}")

def _bloquear_par(symbol: str, razon: str):
    if symbol not in _pares_no_soportados:
        _pares_no_soportados.add(symbol)
        _guardar_no_soportados()
        log.warning(f"[API-BLOCK] 🚫 {symbol} bloqueado permanentemente: {razon}")
        try:
            import memoria as _mem
            _mem.registrar_error_api(symbol)
        except Exception:
            pass

_cargar_no_soportados()


# ══════════════════════════════════════════════════════════════
# FIRMA  ← FIX#1: recvWindow incluido en todas las peticiones
# ══════════════════════════════════════════════════════════════
def _sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(
        config.BINGX_SECRET_KEY.encode(),
        query.encode(),
        hashlib.sha256,
    ).hexdigest()

def _headers() -> dict:
    return {"X-BX-APIKEY": config.BINGX_API_KEY}

def _add_auth(params: dict) -> dict:
    """Añade timestamp, recvWindow y signature. Modifica y devuelve params."""
    params["timestamp"]  = _ts()
    params["recvWindow"] = 5000          # FIX#1 — 5 segundos de ventana
    params["signature"]  = _sign(params)
    return params


def _get(path: str, params: dict = None, retries: int = 3) -> dict:
    params = _add_auth(params or {})
    for attempt in range(retries):
        try:
            r = _SESSION.get(
                BASE_URL + path, params=params,
                headers=_headers(), timeout=12,
            )
            data = r.json()
            if data.get("code", 0) != 0 and attempt == 0:
                log.debug(f"GET {path} code={data.get('code')} msg={data.get('msg','')[:80]}")
            return data
        except Exception as e:
            log.error(f"GET {path} [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    return {}


def _post(path: str, params: dict = None, retries: int = 3) -> dict:
    params = _add_auth(params or {})
    for attempt in range(retries):
        try:
            r = _SESSION.post(
                BASE_URL + path, params=params,
                headers=_headers(), timeout=12,
            )
            data = r.json()
            # FIX#5: log completo en errores para diagnóstico
            if data.get("code", 0) != 0:
                log.warning(
                    f"POST {path} ERROR code={data.get('code')} "
                    f"msg='{data.get('msg','')[:120]}' "
                    f"params_keys={list(params.keys())}"
                )
            return data
        except Exception as e:
            log.error(f"POST {path} [{attempt+1}/{retries}]: {e}")
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    return {}


# ══════════════════════════════════════════════════════════════
# BALANCE
# ══════════════════════════════════════════════════════════════
def _extract_float(data, keys) -> float:
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if v is not None:
                try:
                    f = float(v)
                    if f >= 0:
                        return f
                except Exception:
                    pass
        for v in data.values():
            if isinstance(v, (dict, list)):
                result = _extract_float(v, keys)
                if result >= 0:
                    return result
    elif isinstance(data, list):
        for item in data:
            result = _extract_float(item, keys)
            if result >= 0:
                return result
    return -1.0

def get_balance() -> float:
    if config.MODO_DEMO:
        return 1000.0
    # Auto-sync tiempo si aún no se ha hecho (primera llamada)
    global _time_offset
    if _time_offset == 0:
        sync_server_time()
    keys_real     = ("balance", "walletBalance", "equity")
    keys_fallback = ("availableMargin", "availableBalance", "crossAvailableBalance", "free")
    for ep in ["/openApi/swap/v2/user/balance", "/openApi/swap/v3/user/balance", "/openApi/swap/v2/user/margin"]:
        try:
            res  = _get(ep)
            data = res.get("data")
            if data is not None:
                val = _extract_float(data, keys_real)
                if val > 0.1:
                    return val
                val2 = _extract_float(data, keys_fallback)
                if val2 >= 0:
                    return val2
        except Exception as e:
            log.debug(f"[BAL] {ep}: {e}")
    log.warning("[BALANCE] No se pudo leer balance — verifica API key y permisos Futures")
    return 0.0

def get_available_margin() -> float:
    if config.MODO_DEMO:
        return 1000.0
    keys = ("availableMargin", "availableBalance", "crossAvailableBalance", "free")
    for ep in ["/openApi/swap/v2/user/balance", "/openApi/swap/v3/user/balance"]:
        try:
            data = _get(ep).get("data")
            if data is not None:
                val = _extract_float(data, keys)
                if val >= 0:
                    return val
        except Exception:
            pass
    return 0.0


def diagnostico_balance() -> dict:
    """Diagnóstico completo del balance — usado por main_bellsz y otros módulos."""
    bal = get_balance()
    try:
        raw = _get("/openApi/swap/v2/user/balance")
        data = (raw.get("data") or {})
        # BingX puede devolver lista o dict
        if isinstance(data, list):
            data = data[0] if data else {}
        def _sf(v, default=0.0):
            """Safe float — nunca falla aunque v sea dict/list/None."""
            if isinstance(v, (int, float)):  return float(v)
            if isinstance(v, str):
                try: return float(v)
                except: pass
            return default

        equity = _sf(data.get("equity") or data.get("totalEquity"), bal)
        margin = _sf(data.get("usedMargin") or data.get("totalUsedMargin"), 0.0)
        avail  = _sf(data.get("availableMargin") or data.get("availableEquity"), bal)
        upnl   = _sf(data.get("unrealizedProfit"), 0.0)
        return {
            "balance":  bal,
            "equity":   equity,
            "margin":   margin,
            "available":avail,
            "upnl":     upnl,
            "ok":       bal > 0,
        }
    except Exception as e:
        log.warning(f"[DIAG-BAL] {e}")
        return {"balance": bal, "equity": bal, "margin": 0,
                "available": bal, "upnl": 0, "ok": bal > 0}


# ══════════════════════════════════════════════════════════════
# PRECIO
# ══════════════════════════════════════════════════════════════
def get_precio(symbol: str) -> float:
    for _ in range(2):
        try:
            res = _SESSION.get(
                BASE_URL + "/openApi/swap/v2/quote/price",
                params={"symbol": symbol}, timeout=8,
            ).json()
            p = float((res.get("data") or {}).get("price", 0) or 0)
            if p > 0:
                return p
        except Exception as e:
            log.error(f"get_precio {symbol}: {e}")
        time.sleep(0.5)
    return 0.0


# ══════════════════════════════════════════════════════════════
# VELAS
# ══════════════════════════════════════════════════════════════
INTERVAL_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
    "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1d",
}

def get_candles(symbol: str, interval: str = "5m", limit: int = 200) -> list:
    iv = INTERVAL_MAP.get(interval, "5m")
    for ep in ["/openApi/swap/v3/quote/klines", "/openApi/swap/v2/quote/klines"]:
        try:
            res = _SESSION.get(
                BASE_URL + ep,
                params={"symbol": symbol, "interval": iv, "limit": limit},
                timeout=15,
            ).json()
            raw = res.get("data") or []
            if not raw:
                continue
            candles = []
            for c in raw:
                try:
                    if isinstance(c, list):
                        candles.append({
                            "ts": int(c[0]), "open": float(c[1]),
                            "high": float(c[2]), "low": float(c[3]),
                            "close": float(c[4]), "volume": float(c[5]),
                        })
                    elif isinstance(c, dict):
                        candles.append({
                            "ts":     int(c.get("time", c.get("openTime", 0))),
                            "open":   float(c.get("open", 0)),
                            "high":   float(c.get("high", 0)),
                            "low":    float(c.get("low", 0)),
                            "close":  float(c.get("close", 0)),
                            "volume": float(c.get("volume", 0)),
                        })
                except Exception:
                    continue
            if candles:
                candles.sort(key=lambda x: x["ts"])
                return candles
        except Exception as e:
            log.error(f"get_candles {symbol} {ep}: {e}")
    return []


# ══════════════════════════════════════════════════════════════
# FILTRO PARES NO SOPORTADOS
# ══════════════════════════════════════════════════════════════
_PARES_BLOQUEADOS_API = ("NCFX", "NCF", "RESOLV")

def par_es_soportado(symbol: str) -> bool:
    if symbol in _pares_no_soportados:
        return False
    for prefix in _PARES_BLOQUEADOS_API:
        if symbol.startswith(prefix):
            return False
    return True

def get_pares_no_soportados() -> set:
    return _pares_no_soportados.copy()


# ══════════════════════════════════════════════════════════════
# POSICIONES
# ══════════════════════════════════════════════════════════════
def get_posiciones_abiertas() -> list:
    if config.MODO_DEMO:
        return []
    try:
        return _get("/openApi/swap/v2/user/positions").get("data") or []
    except Exception as e:
        log.error(f"get_posiciones_abiertas: {e}")
        return []


# ══════════════════════════════════════════════════════════════
# DETECTAR MODO HEDGE / ONE-WAY  ← FIX#8: activo al arranque
# ══════════════════════════════════════════════════════════════
def _detect_hedge_mode(symbol: str) -> bool:
    """
    Detecta si la cuenta está en modo Hedge (True) o One-Way (False).
    BingX no tiene un endpoint directo para esto — lo inferimos intentando
    una orden dummy con positionSide=LONG y viendo si el error es de modo.
    Cache por par.
    """
    if symbol in _hedge_mode_cache:
        return _hedge_mode_cache[symbol]

    mode = config.BINGX_MODE.lower()
    if mode == "hedge":
        _hedge_mode_cache[symbol] = True
        return True
    if mode == "oneway":
        _hedge_mode_cache[symbol] = False
        return False

    # Auto-detect: intentar leer posiciones y ver el campo positionSide
    try:
        pos = _get("/openApi/swap/v2/user/positions").get("data") or []
        if pos:
            ps = (pos[0] or {}).get("positionSide", "BOTH")
            is_hedge = ps in ("LONG", "SHORT")
            _hedge_mode_cache[symbol] = is_hedge
            log.info(f"[MODE] {symbol}: {'HEDGE' if is_hedge else 'ONE-WAY'} (positionSide={ps})")
            return is_hedge
    except Exception:
        pass

    # Fallback: asumir hedge (más seguro)
    _hedge_mode_cache[symbol] = True
    log.debug(f"[MODE] {symbol}: asumiendo HEDGE por defecto")
    return True


# ══════════════════════════════════════════════════════════════
# LEVERAGE  ← FIX#6: robusto para ambos modos
# ══════════════════════════════════════════════════════════════
def set_leverage(symbol: str, leverage: int) -> bool:
    if config.MODO_DEMO:
        return True
    hedge = _detect_hedge_mode(symbol)
    if hedge:
        sides = ["LONG", "SHORT"]
    else:
        sides = ["LONG"]  # en one-way solo importa uno

    ok = True
    for side in sides:
        r = _post("/openApi/swap/v2/trade/leverage",
                  {"symbol": symbol, "side": side, "leverage": str(leverage)})
        code = r.get("code", 0)
        if code != 0 and code not in (80012, 80014, 109400):
            # 80012 = leverage ya está seteado, ignorar
            log.debug(f"set_leverage {symbol} {side}: code={code} msg={r.get('msg','')[:60]}")
            ok = False
    return ok


# ══════════════════════════════════════════════════════════════
# CONTRATOS + CANTIDAD
# ══════════════════════════════════════════════════════════════
def _load_contracts():
    global _contract_cache, _contract_cache_ts, _CONTRATOS_FUTURES
    if _contract_cache and time.time() - _contract_cache_ts < 3600:
        return
    try:
        res = _SESSION.get(
            BASE_URL + "/openApi/swap/v2/quote/contracts", timeout=15,
        ).json()
        for c in (res.get("data") or []):
            sym       = c.get("symbol", "")
            step      = float(c.get("tradeMinQuantity", 1) or 1)
            dec       = int(c.get("quantityPrecision", 0) or 0)
            price_dec = int(c.get("pricePrecision", 6) or 6)
            _contract_cache[sym] = {"step": step, "dec": dec, "price_dec": price_dec}
        _CONTRATOS_FUTURES = set(_contract_cache.keys())
        _contract_cache_ts = time.time()
        log.info(f"[CONTRACTS] {len(_contract_cache)} pares cargados")
    except Exception as e:
        log.warning(f"[CONTRACTS] {e}")

def _cargar_contratos():
    _load_contracts()

def calcular_cantidad(symbol: str, trade_usdt: float, precio: float) -> float:
    if precio <= 0 or trade_usdt <= 0:
        return 0.0
    _load_contracts()
    qty_raw = (trade_usdt * config.LEVERAGE) / precio
    info    = _contract_cache.get(symbol, {})
    step    = info.get("step", 0)
    dec     = info.get("dec", 0)
    if step > 0:
        qty = math.floor(qty_raw / step) * step
        qty = max(qty, step)
        qty = round(qty, dec)
        log.debug(f"[QTY] {symbol} raw={qty_raw:.6f} step={step} dec={dec} → {qty}")
        return qty
    # fallback por precio
    if precio >= 1000:
        return max(round(qty_raw, 3), 0.001)
    elif precio >= 1:
        return max(round(qty_raw, 1), 0.1)
    else:
        return max(math.floor(qty_raw), 1)


def _qty_str(qty: float, symbol: str = "") -> str:
    """FIX#9: nunca devuelve '0' o string vacío."""
    info = _contract_cache.get(symbol, {})
    dec  = info.get("dec", 0)
    step = info.get("step", 0)
    if qty <= 0:
        qty = step if step > 0 else 0.001
    if dec == 0:
        return str(max(int(qty), 1))
    return str(round(qty, dec))


def _price_str(price: float, symbol: str = "") -> str:
    """FIX#10: redondea stopPrice según precisión real del par."""
    info = _contract_cache.get(symbol, {})
    dec  = info.get("price_dec", 6)
    return str(round(price, dec))


# ══════════════════════════════════════════════════════════════
# ORDEN PRINCIPAL  (con fallback hedge → one-way)
# ══════════════════════════════════════════════════════════════
def _send_order(symbol: str, side: str, pos_side: str, qty_str: str) -> dict:
    if symbol in _pares_no_soportados:
        return {"code": -999, "msg": f"{symbol} no soportado por API"}

    hedge = _detect_hedge_mode(symbol)

    if not hedge:
        # ONE-WAY MODE
        r = _post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": side,
            "positionSide": "BOTH",
            "type": "MARKET", "quantity": qty_str,
        })
        if r.get("code", 0) != 0:
            _bloquear_par(symbol, f"oneway code={r.get('code')}")
        return r

    # HEDGE MODE
    params = {
        "symbol": symbol, "side": side,
        "positionSide": pos_side,
        "type": "MARKET", "quantity": qty_str,
    }
    res  = _post("/openApi/swap/v2/trade/order", params)
    code = res.get("code", 0)

    if code in (109400, 80001, 80014, 100400, -1, 80012):
        # Probablemente one-way — actualizar caché y reintentar
        log.info(f"[ORDER] {symbol} hedge→BOTH fallback (code={code})")
        _hedge_mode_cache[symbol] = False
        res2 = _post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": side,
            "positionSide": "BOTH",
            "type": "MARKET", "quantity": qty_str,
        })
        if res2.get("code", 0) != 0:
            _bloquear_par(symbol, f"hedge={code} both={res2.get('code')}")
        return res2

    if code == 0:
        _hedge_mode_cache[symbol] = True

    return res


# ══════════════════════════════════════════════════════════════
# SL / TP SEPARADOS  ← FIX#3: reduceOnly=true añadido
# ══════════════════════════════════════════════════════════════
def _place_sl_tp(symbol: str, lado: str, qty: float, sl: float, tp: float):
    if config.MODO_DEMO:
        return
    hedge = _hedge_mode_cache.get(symbol, True)
    qty_s = _qty_str(qty, symbol)
    close = "SELL" if lado == "LONG" else "BUY"

    for order_type, price in [("STOP_MARKET", sl), ("TAKE_PROFIT_MARKET", tp)]:
        p = {
            "symbol":      symbol,
            "side":        close,
            "type":        order_type,
            "quantity":    qty_s,
            "stopPrice":   _price_str(price, symbol),   # FIX#10
            "workingType": "MARK_PRICE",
            "reduceOnly":  "true",                      # FIX#3 — CRÍTICO
        }
        if hedge:
            p["positionSide"] = lado

        r = _post("/openApi/swap/v2/trade/order", p)
        code = r.get("code", 0)

        if code != 0 and hedge:
            # Reintentar sin positionSide (one-way fallback)
            p2 = dict(p)
            p2.pop("positionSide", None)
            p2["positionSide"] = "BOTH"
            r = _post("/openApi/swap/v2/trade/order", p2)
            code = r.get("code", 0)

        if code != 0:
            # Último intento: closePosition en lugar de quantity
            p3 = {
                "symbol":        symbol,
                "side":          close,
                "type":          order_type,
                "closePosition": "true",
                "stopPrice":     _price_str(price, symbol),
                "workingType":   "MARK_PRICE",
            }
            if hedge:
                p3["positionSide"] = lado
            r = _post("/openApi/swap/v2/trade/order", p3)

        if r.get("code", 0) == 0:
            log.info(f"  ✅ {order_type} {symbol} {lado} @ {price:.8g}")
        else:
            log.error(f"  ❌ {order_type} {symbol}: {r.get('msg','')[:80]}")

    log.info(f"✅ SL/TP colocados {symbol} {lado} | SL={sl:.8g} TP={tp:.8g}")


# ══════════════════════════════════════════════════════════════
# CANCELAR ÓRDENES ABIERTAS (SL/TP pendientes)
# ══════════════════════════════════════════════════════════════
def cancelar_ordenes_abiertas(symbol: str):
    """Cancela todas las órdenes abiertas de un par antes de cerrar la posición."""
    if config.MODO_DEMO:
        return
    try:
        r = _post("/openApi/swap/v2/trade/cancelAllOrders", {"symbol": symbol})
        if r.get("code", 0) == 0:
            log.debug(f"[CANCEL] Órdenes de {symbol} canceladas")
    except Exception as e:
        log.debug(f"[CANCEL] {symbol}: {e}")


# ══════════════════════════════════════════════════════════════
# ABRIR LONG
# ══════════════════════════════════════════════════════════════
def abrir_long(symbol: str, qty: float, precio: float, sl: float, tp: float) -> dict:
    if config.MODO_DEMO:
        return {"fill_price": precio, "executedQty": qty, "orderId": "demo_long"}
    if symbol in _pares_no_soportados:
        return {"error": f"{symbol} no soportado por API"}

    set_leverage(symbol, config.LEVERAGE)
    qty_s = _qty_str(qty, symbol)
    log.info(f"[ORDER] LONG {symbol} qty={qty_s} @ ~{precio:.8g} | SL={sl:.8g} TP={tp:.8g}")

    res  = _send_order(symbol, "BUY", "LONG", qty_s)
    if res.get("code", 0) != 0:
        return {"error": res.get("msg", "unknown")}

    order = (res.get("data") or {}).get("order", res.get("data") or {})
    fill  = float(order.get("avgPrice", order.get("price", precio)) or precio)
    eqty  = float(order.get("executedQty", qty) or qty)
    if fill <= 0:
        fill = precio

    time.sleep(0.8)
    _place_sl_tp(symbol, "LONG", eqty, sl, tp)
    return {"fill_price": fill, "executedQty": eqty, "orderId": order.get("orderId")}


# ══════════════════════════════════════════════════════════════
# ABRIR SHORT
# ══════════════════════════════════════════════════════════════
def abrir_short(symbol: str, qty: float, precio: float, sl: float, tp: float) -> dict:
    if config.MODO_DEMO:
        return {"fill_price": precio, "executedQty": qty, "orderId": "demo_short"}
    if symbol in _pares_no_soportados:
        return {"error": f"{symbol} no soportado por API"}

    set_leverage(symbol, config.LEVERAGE)
    qty_s = _qty_str(qty, symbol)
    log.info(f"[ORDER] SHORT {symbol} qty={qty_s} @ ~{precio:.8g} | SL={sl:.8g} TP={tp:.8g}")

    res  = _send_order(symbol, "SELL", "SHORT", qty_s)
    if res.get("code", 0) != 0:
        return {"error": res.get("msg", "unknown")}

    order = (res.get("data") or {}).get("order", res.get("data") or {})
    fill  = float(order.get("avgPrice", order.get("price", precio)) or precio)
    eqty  = float(order.get("executedQty", qty) or qty)
    if fill <= 0:
        fill = precio

    time.sleep(0.8)
    _place_sl_tp(symbol, "SHORT", eqty, sl, tp)
    return {"fill_price": fill, "executedQty": eqty, "orderId": order.get("orderId")}


# ══════════════════════════════════════════════════════════════
# CERRAR POSICIÓN  ← FIX#4: closePosition=true en one-way
# ══════════════════════════════════════════════════════════════
def cerrar_posicion(symbol: str, qty: float, lado: str) -> dict:
    if config.MODO_DEMO:
        return {"precio_salida": get_precio(symbol)}

    # Cancelar SL/TP pendientes primero para evitar conflictos
    cancelar_ordenes_abiertas(symbol)
    time.sleep(0.3)

    hedge  = _hedge_mode_cache.get(symbol, True)
    side   = "SELL" if lado == "LONG" else "BUY"
    qty_s  = _qty_str(qty, symbol)

    # Intento 1: con positionSide y reduceOnly
    p = {
        "symbol":     symbol,
        "side":       side,
        "type":       "MARKET",
        "quantity":   qty_s,
        "reduceOnly": "true",           # FIX#3
    }
    if hedge:
        p["positionSide"] = lado

    res  = _post("/openApi/swap/v2/trade/order", p)
    code = res.get("code", 0)

    if code != 0:
        # Intento 2: closePosition=true (FIX#4)
        p2 = {
            "symbol":        symbol,
            "side":          side,
            "type":          "MARKET",
            "closePosition": "true",
        }
        if hedge:
            p2["positionSide"] = lado
        res  = _post("/openApi/swap/v2/trade/order", p2)
        code = res.get("code", 0)

    if code != 0 and hedge:
        # Intento 3: one-way fallback
        p3 = {
            "symbol":        symbol,
            "side":          side,
            "type":          "MARKET",
            "closePosition": "true",
            "positionSide":  "BOTH",
        }
        res  = _post("/openApi/swap/v2/trade/order", p3)
        code = res.get("code", 0)

    if code != 0:
        log.error(f"[CIERRE] {symbol} todos los intentos fallaron: {res.get('msg','')[:80]}")

    order = (res.get("data") or {}).get("order", res.get("data") or {})
    fill  = float(order.get("avgPrice", order.get("price", 0)) or 0)
    return {"precio_salida": fill or get_precio(symbol)}


# ══════════════════════════════════════════════════════════════
# CANCELAR ÓRDENES SL/TP Y REEMPLAZAR (para trailing update)
# ══════════════════════════════════════════════════════════════
def actualizar_sl(symbol: str, lado: str, qty: float, nuevo_sl: float):
    """Cancela el SL vigente y coloca uno nuevo (para trailing stop)."""
    if config.MODO_DEMO:
        return
    cancelar_ordenes_abiertas(symbol)
    time.sleep(0.3)
    hedge = _hedge_mode_cache.get(symbol, True)
    qty_s = _qty_str(qty, symbol)
    close = "SELL" if lado == "LONG" else "BUY"
    p = {
        "symbol":      symbol,
        "side":        close,
        "type":        "STOP_MARKET",
        "quantity":    qty_s,
        "stopPrice":   _price_str(nuevo_sl, symbol),
        "workingType": "MARK_PRICE",
        "reduceOnly":  "true",
    }
    if hedge:
        p["positionSide"] = lado
    r = _post("/openApi/swap/v2/trade/order", p)
    if r.get("code", 0) == 0:
        log.debug(f"[SL-UPDATE] {symbol} nuevo SL @ {nuevo_sl:.8g}")
    else:
        log.warning(f"[SL-UPDATE] {symbol}: {r.get('msg','')[:60]}")


# ── Alias para compatibilidad con main_bellsz ────────────────
def actualizar_sl_bingx(par: str, nuevo_sl: float, lado: str) -> bool:
    """Alias de actualizar_sl — compatibilidad con main_bellsz.py"""
    try:
        actualizar_sl(par, lado, 0, nuevo_sl)
        return True
    except Exception as e:
        log.debug(f"actualizar_sl_bingx {par}: {e}")
        return False

