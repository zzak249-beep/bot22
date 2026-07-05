"""
Cliente BingX Perpetual Futures (Swap V2) — asíncrono
========================================================
Implementa lo mínimo necesario para este bot:
  - listado de símbolos + volumen 24h
  - klines (velas)
  - balance de cuenta
  - set leverage
  - abrir/cerrar posición con SL/TP

Firma HMAC-SHA256 según especificación estándar de BingX Swap V2 API.
Requiere BINGX_API_KEY / BINGX_API_SECRET como variables de entorno.

NOTA (añadida en revisión): la firma se calcula sobre
urlencode(sorted(params.items())) — el orden alfabético importa para
el HASH en sí, pero NO para el orden en que aiohttp serializa params=
en la petición real. Esto es seguro solo si BingX reordena los
parámetros recibidos antes de recalcular su propia firma (estándar en
este esquema, y consistente con cómo lo hace el resto del fleet) — no
confirmado con tráfico real contra BingX todavía. Si el primer
POST/GET autenticado falla con error de firma, revisar esto primero.
"""
import asyncio
import hashlib
import hmac
import logging
import random
import re
import time
from urllib.parse import urlencode

import aiohttp

log = logging.getLogger("exchange_client")

RATE_LIMIT_CODE = 100410
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_MAX_WAIT_S = 20.0  # techo de espera por intento, aunque el mensaje pida más


def _parse_unblock_wait_s(msg):
    """
    Extrae el epoch en ms de mensajes tipo:
    "code:100410:The endpoint trigger frequency limit rule is currently in
    the disabled period and will be unblocked after 1783232851826"
    Devuelve segundos a esperar (>=0), o un default conservador si no
    puede parsear el mensaje (BingX podría cambiar el formato del texto).
    """
    match = re.search(r"unblocked after (\d+)", msg or "")
    if not match:
        return 3.0
    unblock_ms = int(match.group(1))
    wait_s = (unblock_ms / 1000) - time.time()
    return max(0.0, wait_s)


class BingXClient:
    def __init__(self, api_key, api_secret, base_url, dry_run=True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self._session = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()

    def _sign(self, params: dict) -> str:
        qs = urlencode(sorted(params.items()))
        return hmac.new(
            self.api_secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    async def _request(self, method, path, params=None, signed=False, _retry=0):
        original_params = dict(params or {})  # sin timestamp/signature — se re-firma en cada intento
        params = dict(original_params)
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)
        headers = {"X-BX-APIKEY": self.api_key} if signed else {}
        url = f"{self.base_url}{path}"
        try:
            async with self._session.request(method, url, params=params, headers=headers, timeout=15) as resp:
                data = await resp.json(content_type=None)
                code = data.get("code")
                if code == RATE_LIMIT_CODE and _retry < RATE_LIMIT_MAX_RETRIES:
                    wait_s = min(_parse_unblock_wait_s(data.get("msg", "")), RATE_LIMIT_MAX_WAIT_S)
                    wait_s += random.uniform(0, 1.5)  # jitter — evita que todas las corutinas reintenten a la vez
                    log.warning(
                        "Rate limit BingX (100410) en [%s %s], esperando %.1fs antes de reintentar (%d/%d)",
                        method, path, wait_s, _retry + 1, RATE_LIMIT_MAX_RETRIES,
                    )
                    await asyncio.sleep(wait_s)
                    return await self._request(method, path, params=original_params,
                                                signed=signed, _retry=_retry + 1)
                if code not in (0, None):
                    log.warning("BingX API error [%s %s]: %s", method, path, data)
                return data
        except Exception as e:
            log.error("Error en request BingX [%s %s]: %s", method, path, e)
            return {"code": -1, "msg": str(e)}

    # ── Datos públicos ────────────────────────────────────────────────
    async def get_all_symbols_with_volume(self):
        """Devuelve [{symbol, volume_24h_usdt}, ...] para todos los perpetuos."""
        data = await self._request("GET", "/openApi/swap/v2/quote/ticker", signed=False)
        items = data.get("data", []) if isinstance(data, dict) else []
        out = []
        for it in items:
            try:
                out.append({
                    "symbol": it["symbol"],
                    "volume_24h_usdt": float(it.get("quoteVolume", 0)),
                })
            except (KeyError, ValueError, TypeError):
                continue
        return out

    async def get_klines(self, symbol, interval, limit=200):
        """interval: '3m','5m','15m','30m','1h','4h','1d' (formato BingX)."""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        data = await self._request("GET", "/openApi/swap/v3/quote/klines", params, signed=False)
        raw = data.get("data", []) if isinstance(data, dict) else []
        candles = []
        for k in raw:
            try:
                candles.append({
                    "open": float(k["open"]), "high": float(k["high"]),
                    "low": float(k["low"]), "close": float(k["close"]),
                    "volume": float(k["volume"]), "time": int(k["time"]),
                })
            except (KeyError, ValueError, TypeError):
                continue
        candles.sort(key=lambda c: c["time"])
        return candles

    # ── Cuenta / trading ──────────────────────────────────────────────
    async def get_balance_usdt(self):
        data = await self._request("GET", "/openApi/swap/v2/user/balance", signed=True)
        try:
            return float(data["data"]["balance"]["balance"])
        except (KeyError, TypeError, ValueError):
            log.warning("No se pudo leer balance: %s", data)
            return 0.0

    async def set_leverage(self, symbol, leverage, side="LONG"):
        if self.dry_run:
            log.info("[DRY_RUN] set_leverage %s x%s (%s)", symbol, leverage, side)
            return True
        params = {"symbol": symbol, "side": side, "leverage": leverage}
        data = await self._request("POST", "/openApi/swap/v2/trade/leverage", params, signed=True)
        ok = data.get("code") == 0
        if not ok:
            log.error("set_leverage falló para %s: %s", symbol, data)
        return ok

    async def open_position(self, symbol, side, quantity, sl_price=None, tp_price=None):
        """side: 'LONG' o 'SHORT'."""
        if self.dry_run:
            log.info(
                "[DRY_RUN] open_position %s %s qty=%s SL=%s TP=%s",
                symbol, side, quantity, sl_price, tp_price,
            )
            return {"code": 0, "dry_run": True}

        order_side = "BUY" if side == "LONG" else "SELL"
        params = {
            "symbol": symbol, "side": order_side, "positionSide": side,
            "type": "MARKET", "quantity": quantity,
        }
        data = await self._request("POST", "/openApi/swap/v2/trade/order", params, signed=True)
        if data.get("code") != 0:
            log.error("Error abriendo posición %s %s: %s", symbol, side, data)
            return data

        if sl_price:
            await self._place_stop(symbol, side, "STOP_MARKET", sl_price, quantity)
        if tp_price:
            await self._place_stop(symbol, side, "TAKE_PROFIT_MARKET", tp_price, quantity)
        return data

    async def _place_stop(self, symbol, position_side, order_type, stop_price, quantity):
        close_side = "SELL" if position_side == "LONG" else "BUY"
        params = {
            "symbol": symbol, "side": close_side, "positionSide": position_side,
            "type": order_type, "stopPrice": stop_price, "quantity": quantity,
            "workingType": "MARK_PRICE",
        }
        data = await self._request("POST", "/openApi/swap/v2/trade/order", params, signed=True)
        if data.get("code") != 0:
            log.error("Error colocando %s para %s: %s", order_type, symbol, data)
        return data

    async def get_recent_trades(self, symbol, limit=1000):
        """
        Trades públicos recientes (se agregan client-side en order_flow.py).
        Devuelve lista de {price, qty, time, is_buyer_maker}.

        NOTA: este endpoint devuelve los N trades MÁS RECIENTES, no permite
        rango de tiempo arbitrario — por eso el filtro de order flow solo es
        útil para validar el sweep/breaker MÁS RECIENTE (señal en vivo), no
        para backtesting histórico. Verificar el nombre exacto del endpoint
        contra la documentación vigente de BingX antes de operar en real.
        """
        params = {"symbol": symbol, "limit": limit}
        data = await self._request("GET", "/openApi/swap/v2/quote/trades", params, signed=False)
        raw = data.get("data", []) if isinstance(data, dict) else []
        trades = []
        for t in raw:
            try:
                trades.append({
                    "price": float(t["price"]),
                    "qty": float(t.get("qty", t.get("volume", 0))),
                    "time": int(t["time"]),
                    "is_buyer_maker": bool(t.get("buyerMaker", t.get("isBuyerMaker", False))),
                })
            except (KeyError, ValueError, TypeError):
                continue
        trades.sort(key=lambda x: x["time"])
        return trades

    async def get_funding_rate(self, symbol):
        """Funding rate actual del símbolo. Devuelve float (ej. 0.0001 = 0.01%)."""
        params = {"symbol": symbol}
        data = await self._request("GET", "/openApi/swap/v2/quote/premiumIndex", params, signed=False)
        try:
            return float(data["data"]["lastFundingRate"])
        except (KeyError, TypeError, ValueError):
            return None

    async def get_open_interest(self, symbol):
        """Open Interest actual en unidades del contrato. Devuelve float o None."""
        params = {"symbol": symbol}
        data = await self._request("GET", "/openApi/swap/v2/quote/openInterest", params, signed=False)
        try:
            return float(data["data"]["openInterest"])
        except (KeyError, TypeError, ValueError):
            return None

    async def get_income_history(self, symbol, limit=20):
        """Historial de PnL realizado (para el position_monitor)."""
        params = {"symbol": symbol, "limit": limit, "incomeType": "REALIZED_PNL"}
        data = await self._request("GET", "/openApi/swap/v2/user/income", params, signed=True)
        raw = data.get("data", []) if isinstance(data, dict) else []
        out = []
        for it in raw:
            try:
                out.append({"symbol": it["symbol"], "income": float(it["income"]), "time": int(it["time"])})
            except (KeyError, ValueError, TypeError):
                continue
        return out

    async def get_open_positions(self):
        data = await self._request("GET", "/openApi/swap/v2/user/positions", signed=True)
        items = data.get("data", []) if isinstance(data, dict) else []
        return [p for p in items if float(p.get("positionAmt", 0)) != 0]
