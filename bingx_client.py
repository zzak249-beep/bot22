"""
BingX Perpetual Futures API Client
Docs: https://bingx-api.github.io/docs/
"""
import hashlib
import hmac
import time
import urllib.parse
import aiohttp
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://open-api.bingx.com"


class BingXClient:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    def _sign(self, params: dict) -> str:
        query = urllib.parse.urlencode(sorted(params.items()))
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self) -> dict:
        return {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json",
        }

    def _build_params(self, extra: dict) -> dict:
        params = {**extra, "timestamp": int(time.time() * 1000)}
        params["signature"] = self._sign(params)
        return params

    # ── Market Data ────────────────────────────────────────────────────────────

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list:
        """
        interval: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w, 1M
        """
        url = f"{BASE_URL}/openApi/swap/v3/quote/klines"
        params = self._build_params({"symbol": symbol, "interval": interval, "limit": limit})
        async with self.session.get(url, params=params, headers=self._headers()) as r:
            data = await r.json()
            if data.get("code") != 0:
                raise Exception(f"BingX klines error: {data}")
            return data["data"]

    async def get_ticker(self, symbol: str) -> dict:
        url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
        params = self._build_params({"symbol": symbol})
        async with self.session.get(url, params=params, headers=self._headers()) as r:
            data = await r.json()
            if data.get("code") != 0:
                raise Exception(f"BingX ticker error: {data}")
            return data["data"]

    # ── Account ────────────────────────────────────────────────────────────────

    async def get_balance(self) -> dict:
        url = f"{BASE_URL}/openApi/swap/v2/user/balance"
        params = self._build_params({})
        async with self.session.get(url, params=params, headers=self._headers()) as r:
            data = await r.json()
            if data.get("code") != 0:
                raise Exception(f"BingX balance error: {data}")
            return data["data"]["balance"]

    async def get_positions(self, symbol: str = "") -> list:
        url = f"{BASE_URL}/openApi/swap/v2/user/positions"
        p = {} if not symbol else {"symbol": symbol}
        params = self._build_params(p)
        async with self.session.get(url, params=params, headers=self._headers()) as r:
            data = await r.json()
            if data.get("code") != 0:
                raise Exception(f"BingX positions error: {data}")
            return data["data"]

    # ── Orders ─────────────────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str,           # BUY / SELL
        position_side: str,  # LONG / SHORT
        order_type: str,     # MARKET / LIMIT
        quantity: float,
        price: float = None,
        stop_price: float = None,
        reduce_only: bool = False,
    ) -> dict:
        url = f"{BASE_URL}/openApi/swap/v2/trade/order"
        body = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "quantity": str(quantity),
        }
        if price:
            body["price"] = str(price)
        if stop_price:
            body["stopPrice"] = str(stop_price)
        if reduce_only:
            body["reduceOnly"] = "true"
        params = self._build_params(body)
        async with self.session.post(url, params=params, headers=self._headers()) as r:
            data = await r.json()
            if data.get("code") != 0:
                raise Exception(f"BingX order error: {data}")
            logger.info(f"Order placed: {data}")
            return data["data"]

    async def place_market_order(self, symbol: str, side: str, quantity: float) -> dict:
        position_side = "LONG" if side == "BUY" else "SHORT"
        return await self.place_order(symbol, side, position_side, "MARKET", quantity)

    async def place_tp_sl(
        self,
        symbol: str,
        direction: str,  # BUY or SELL (the original trade direction)
        quantity: float,
        sl_price: float,
        tp_prices: list[float],
    ) -> list:
        """Place SL + multiple partial TP orders."""
        results = []
        position_side = "LONG" if direction == "BUY" else "SHORT"
        close_side = "SELL" if direction == "BUY" else "BUY"

        # Stop-Loss
        sl = await self.place_order(
            symbol, close_side, position_side, "STOP_MARKET",
            quantity, stop_price=sl_price, reduce_only=True
        )
        results.append({"type": "SL", "order": sl})

        # TPs — split quantity equally (or weight toward early TPs)
        qty_per_tp = round(quantity / len(tp_prices), 4)
        for idx, tp in enumerate(tp_prices, 1):
            try:
                tp_order = await self.place_order(
                    symbol, close_side, position_side, "TAKE_PROFIT_MARKET",
                    qty_per_tp, stop_price=tp, reduce_only=True
                )
                results.append({"type": f"TP{idx}", "order": tp_order})
            except Exception as e:
                logger.warning(f"TP{idx} placement failed: {e}")

        return results

    async def close_position(self, symbol: str, direction: str, quantity: float) -> dict:
        close_side = "SELL" if direction == "BUY" else "BUY"
        position_side = "LONG" if direction == "BUY" else "SHORT"
        return await self.place_order(symbol, close_side, position_side, "MARKET", quantity, reduce_only=True)

    async def cancel_all_orders(self, symbol: str) -> dict:
        url = f"{BASE_URL}/openApi/swap/v2/trade/allOpenOrders"
        params = self._build_params({"symbol": symbol})
        async with self.session.delete(url, params=params, headers=self._headers()) as r:
            data = await r.json()
            return data

    async def set_leverage(self, symbol: str, leverage: int, margin_type: str = "ISOLATED") -> dict:
        url = f"{BASE_URL}/openApi/swap/v2/trade/leverage"
        params = self._build_params({"symbol": symbol, "leverage": leverage, "marginType": margin_type})
        async with self.session.post(url, params=params, headers=self._headers()) as r:
            data = await r.json()
            return data
