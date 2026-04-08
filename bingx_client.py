"""
BingX API Client - Perpetual Futures (Swap)
Async version compatible with bot_v2.py (aiohttp).
Fixed: retry logic, rate limiting, proper error handling, balance parsing.
"""
import hmac, hashlib, time, json, logging
import asyncio
import aiohttp
from urllib.parse import urlencode
from typing import Optional

log = logging.getLogger(__name__)

BASE_URL    = "https://open-api.bingx.com"
MAX_RETRIES = 3
RETRY_DELAY = 1.5


class BingXClient:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key    = api_key
        self.secret_key = secret_key
        self._session: Optional[aiohttp.ClientSession] = None

    # ── Async Context Manager ─────────────────────────────────────────────
    async def __aenter__(self):
        self._session = aiohttp.ClientSession(headers={
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json",
        })
        return self

    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()
            self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Obtener sesión activa o crear una nueva si no existe"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "X-BX-APIKEY": self.api_key,
                "Content-Type": "application/json",
            })
        return self._session

    # ── Auth ──────────────────────────────────────────────────────────────
    def _sign(self, params: dict) -> str:
        payload = urlencode(sorted(params.items()))
        return hmac.new(
            self.secret_key.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()

    async def _request(self, method: str, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        session = self._get_session()

        for attempt in range(MAX_RETRIES):
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                if method == "GET":
                    async with session.get(
                        BASE_URL + path, params=params, timeout=timeout
                    ) as r:
                        if r.status == 429:
                            wait = float(r.headers.get("Retry-After", 10))
                            log.warning(f"Rate limited, waiting {wait}s")
                            await asyncio.sleep(wait)
                            continue
                        r.raise_for_status()
                        data = await r.json()
                else:
                    async with session.post(
                        BASE_URL + path, params=params, timeout=timeout
                    ) as r:
                        if r.status == 429:
                            wait = float(r.headers.get("Retry-After", 10))
                            log.warning(f"Rate limited, waiting {wait}s")
                            await asyncio.sleep(wait)
                            continue
                        r.raise_for_status()
                        data = await r.json()

                code = data.get("code", 0)
                if code != 0:
                    msg = data.get("msg", "Unknown error")
                    log.error(f"BingX API code {code}: {msg} | {path}")
                return data

            except asyncio.TimeoutError:
                log.warning(f"Timeout {path}, attempt {attempt+1}/{MAX_RETRIES}")
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            except aiohttp.ClientError as e:
                log.error(f"Request error {path}: {e}")
                await asyncio.sleep(RETRY_DELAY)
        return {}

    async def _get(self, path: str, params: dict = None) -> dict:
        return await self._request("GET", path, params)

    async def _post(self, path: str, params: dict = None) -> dict:
        return await self._request("POST", path, params)

    # ── Helpers ───────────────────────────────────────────────────────────
    def _parse_balance_data(self, data: dict) -> Optional[dict]:
        raw = data.get("data", {})
        log.debug(f"Raw balance response: {raw}")
        balance = raw.get("balance", raw)

        if isinstance(balance, dict):
            if balance.get("asset") == "USDT":
                return balance
            for v in balance.values():
                if isinstance(v, dict) and v.get("asset") == "USDT":
                    return v
            if "availableMargin" in balance or "equity" in balance:
                return balance
        elif isinstance(balance, list):
            for a in balance:
                if isinstance(a, dict) and a.get("asset") == "USDT":
                    return a

        log.warning(f"Could not parse balance from response: {data}")
        return None

    # ── Market Data ───────────────────────────────────────────────────────
    async def get_all_symbols(self) -> list:
        data = await self._get("/openApi/swap/v2/quote/contracts")
        return [
            c["symbol"] for c in data.get("data", [])
            if c.get("currency") == "USDT" and c.get("status") == 1
        ]

    async def get_klines(self, symbol: str, interval: str = "15m", limit: int = 250) -> list:
        data = await self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        raw = data.get("data", [])
        candles = []
        for k in raw:
            try:
                candles.append({
                    "ts":     int(k[0]),
                    "open":   float(k[1]),
                    "high":   float(k[2]),
                    "low":    float(k[3]),
                    "close":  float(k[4]),
                    "volume": float(k[5]),
                })
            except (IndexError, ValueError, TypeError):
                continue
        return sorted(candles, key=lambda x: x["ts"])

    async def get_ticker(self, symbol: str) -> dict:
        data = await self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return data.get("data", {})

    async def get_24h_volume(self, symbol: str) -> float:
        t = await self.get_ticker(symbol)
        return float(t.get("quoteVolume", 0))

    async def get_orderbook(self, symbol: str, limit: int = 5) -> dict:
        data = await self._get("/openApi/swap/v2/quote/depth", {
            "symbol": symbol, "limit": limit
        })
        return data.get("data", {})

    # ── Account ───────────────────────────────────────────────────────────
    async def get_balance(self) -> float:
        data = await self._get("/openApi/swap/v2/user/balance")
        usdt = self._parse_balance_data(data)
        if usdt:
            return float(usdt.get("availableMargin", 0))
        return 0.0

    async def get_total_equity(self) -> float:
        data = await self._get("/openApi/swap/v2/user/balance")
        usdt = self._parse_balance_data(data)
        if usdt:
            return float(usdt.get("equity", 0))
        return 0.0

    async def get_positions(self, symbol: str = None) -> list:
        data = await self._get("/openApi/swap/v2/user/positions")
        positions = [
            p for p in data.get("data", [])
            if float(p.get("positionAmt", 0)) != 0
        ]
        if symbol:
            positions = [p for p in positions if p.get("symbol") == symbol]
        return positions

    async def get_open_orders(self, symbol: str) -> list:
        data = await self._get("/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
        return data.get("data", {}).get("orders", [])

    async def get_order_status(self, symbol: str, order_id: str) -> dict:
        data = await self._get("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "orderId": order_id
        })
        return data.get("data", {})

    # ── Trading ───────────────────────────────────────────────────────────
    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        for side in ("LONG", "SHORT"):
            await self._post("/openApi/swap/v2/trade/leverage", {
                "symbol": symbol, "side": side, "leverage": leverage
            })
        return {"status": "ok"}

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        return await self._post("/openApi/swap/v2/trade/marginType", {
            "symbol": symbol, "marginType": margin_type
        })

    async def place_order(
        self,
        symbol: str,
        side: str,
        position_side: str,
        order_type: str,
        quantity: float,
        price: float = None,
        stop_loss: float = None,
        take_profit: float = None,
        reduce_only: bool = False,
        client_order_id: str = None,
    ) -> dict:
        params = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         order_type,
            "quantity":     quantity,
        }
        if order_type == "LIMIT" and price:
            params["price"]       = price
            params["timeInForce"] = "GTC"
        if stop_loss:
            params["stopLoss"] = json.dumps({
                "type": "STOP_MARKET",
                "stopPrice": stop_loss,
                "workingType": "MARK_PRICE"
            })
        if take_profit:
            params["takeProfit"] = json.dumps({
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": take_profit,
                "workingType": "MARK_PRICE"
            })
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["clientOrderId"] = client_order_id

        return await self._post("/openApi/swap/v2/trade/order", params)

    async def place_market_order(
        self, symbol: str, direction: str, quantity: float
    ) -> dict:
        """Wrapper simplificado para órdenes de mercado (usado por bot_v2.py)."""
        position_side = "LONG" if direction == "BUY" else "SHORT"
        result = await self.place_order(
            symbol=symbol,
            side=direction,
            position_side=position_side,
            order_type="MARKET",
            quantity=quantity,
        )
        return result.get("data", {}).get("order", result.get("data", {}))

    async def place_tp_sl(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        sl: float,
        tps: list,
    ) -> None:
        """Colocar Stop Loss y Take Profits (usado por bot_v2.py)."""
        close_side    = "SELL" if direction == "BUY" else "BUY"
        position_side = "LONG" if direction == "BUY" else "SHORT"

        # Stop Loss
        try:
            await self._post("/openApi/swap/v2/trade/order", {
                "symbol":       symbol,
                "side":         close_side,
                "positionSide": position_side,
                "type":         "STOP_MARKET",
                "quantity":     quantity,
                "stopPrice":    sl,
                "workingType":  "MARK_PRICE",
                "reduceOnly":   "true",
            })
            log.info(f"SL colocado @ {sl}")
        except Exception as e:
            log.error(f"Error colocando SL: {e}")

        # Take Profits
        tp_qty = round(quantity / len(tps), 3) if tps else 0
        for i, tp in enumerate(tps, 1):
            try:
                await self._post("/openApi/swap/v2/trade/order", {
                    "symbol":       symbol,
                    "side":         close_side,
                    "positionSide": position_side,
                    "type":         "TAKE_PROFIT_MARKET",
                    "quantity":     tp_qty,
                    "stopPrice":    tp,
                    "workingType":  "MARK_PRICE",
                    "reduceOnly":   "true",
                })
                log.info(f"TP{i} colocado @ {tp}")
            except Exception as e:
                log.error(f"Error colocando TP{i}: {e}")

    async def cancel_all_orders(self, symbol: str) -> dict:
        return await self._post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})

    async def close_position(self, symbol: str, position_side: str, quantity: float) -> dict:
        side = "SELL" if position_side == "LONG" else "BUY"
        return await self.place_order(
            symbol, side, position_side, "MARKET", quantity, reduce_only=True
        )

    async def get_symbol_info(self, symbol: str) -> dict:
        data = await self._get("/openApi/swap/v2/quote/contracts")
        for c in data.get("data", []):
            if c["symbol"] == symbol:
                return c
        return {}

    async def get_income_history(
        self, income_type: str = "REALIZED_PNL", limit: int = 50
    ) -> list:
        data = await self._get("/openApi/swap/v2/user/income", {
            "incomeType": income_type, "limit": limit
        })
        return data.get("data", [])
