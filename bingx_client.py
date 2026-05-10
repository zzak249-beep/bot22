"""
BingX Perpetual Swap (Futures) API Client
Docs: https://bingx-api.github.io/docs/#/en-us/swapV2/
"""

import hashlib
import hmac
import time
import urllib.parse
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://open-api.bingx.com"


class BingXClient:
    def __init__(self, api_key: str, api_secret: str, demo: bool = False):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.demo       = demo          # True → paper-trading endpoint
        self.session    = requests.Session()
        self.session.headers.update({
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json",
        })
        if demo:
            logger.warning("⚠️  DEMO mode active — no real orders will be placed")

    # ──────────────────────────────────────────────────────────────────────
    # Auth helpers
    # ──────────────────────────────────────────────────────────────────────
    def _sign(self, params: dict) -> str:
        query = urllib.parse.urlencode(sorted(params.items()))
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _timestamp(self) -> int:
        return int(time.time() * 1000)

    def _get(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = self._timestamp()
        params["signature"] = self._sign(params)
        url = BASE_URL + path
        r = self.session.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"BingX error [{data.get('code')}]: {data.get('msg')}")
        return data

    def _post(self, path: str, params: dict) -> dict:
        params["timestamp"] = self._timestamp()
        params["signature"] = self._sign(params)
        url = BASE_URL + path
        r = self.session.post(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"BingX error [{data.get('code')}]: {data.get('msg')}")
        return data

    def _delete(self, path: str, params: dict) -> dict:
        params["timestamp"] = self._timestamp()
        params["signature"] = self._sign(params)
        url = BASE_URL + path
        r = self.session.delete(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"BingX error [{data.get('code')}]: {data.get('msg')}")
        return data

    # ──────────────────────────────────────────────────────────────────────
    # Market data
    # ──────────────────────────────────────────────────────────────────────
    def get_klines(self, symbol: str, interval: str = "3m", limit: int = 200) -> list:
        """
        Returns list of OHLCV candles.
        interval: 1m 3m 5m 15m 30m 1h 2h 4h 6h 12h 1d 1w 1M
        """
        data = self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        return data.get("data", [])

    def get_ticker(self, symbol: str) -> dict:
        data = self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return data.get("data", {})

    def get_mark_price(self, symbol: str) -> float:
        data = self._get("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
        return float(data["data"]["markPrice"])

    # ──────────────────────────────────────────────────────────────────────
    # Account
    # ──────────────────────────────────────────────────────────────────────
    def get_balance(self) -> dict:
        data = self._get("/openApi/swap/v2/user/balance")
        return data.get("data", {}).get("balance", {})

    def get_positions(self, symbol: str = "") -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = self._get("/openApi/swap/v2/user/positions", params)
        return data.get("data", [])

    def get_open_orders(self, symbol: str) -> list:
        data = self._get("/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
        return data.get("data", {}).get("orders", [])

    # ──────────────────────────────────────────────────────────────────────
    # Trading
    # ──────────────────────────────────────────────────────────────────────
    def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> dict:
        """side: LONG or SHORT"""
        return self._post("/openApi/swap/v2/trade/leverage", {
            "symbol": symbol,
            "side": side,
            "leverage": leverage,
        })

    def set_margin_mode(self, symbol: str, mode: str = "ISOLATED") -> dict:
        """mode: ISOLATED or CROSSED"""
        return self._post("/openApi/swap/v2/trade/marginType", {
            "symbol": symbol,
            "marginType": mode,
        })

    def place_market_order(
        self,
        symbol: str,
        side: str,          # BUY / SELL
        position_side: str, # LONG / SHORT
        quantity: float,
        reduce_only: bool = False,
    ) -> dict:
        if self.demo:
            logger.info(f"[DEMO] Would place {side}/{position_side} {quantity} {symbol}")
            return {"demo": True, "orderId": "DEMO_" + str(int(time.time()))}

        params = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         "MARKET",
            "quantity":     quantity,
        }
        if reduce_only:
            params["reduceOnly"] = "true"

        return self._post("/openApi/swap/v2/trade/order", params)

    def place_stop_loss(
        self,
        symbol: str,
        side: str,
        position_side: str,
        stop_price: float,
        quantity: float,
    ) -> dict:
        if self.demo:
            return {"demo": True}
        return self._post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         "STOP_MARKET",
            "stopPrice":    stop_price,
            "quantity":     quantity,
            "reduceOnly":   "true",
        })

    def place_take_profit(
        self,
        symbol: str,
        side: str,
        position_side: str,
        stop_price: float,
        quantity: float,
    ) -> dict:
        if self.demo:
            return {"demo": True}
        return self._post("/openApi/swap/v2/trade/order", {
            "symbol":        symbol,
            "side":          side,
            "positionSide":  position_side,
            "type":          "TAKE_PROFIT_MARKET",
            "stopPrice":     stop_price,
            "quantity":      quantity,
            "reduceOnly":    "true",
        })

    def cancel_all_orders(self, symbol: str) -> dict:
        if self.demo:
            return {"demo": True}
        return self._delete("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})

    def close_position(self, symbol: str, position_side: str, quantity: float) -> dict:
        """Market-close an existing position"""
        side = "SELL" if position_side == "LONG" else "BUY"
        return self.place_market_order(symbol, side, position_side, quantity, reduce_only=True)

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────
    def get_symbol_info(self, symbol: str) -> dict:
        data = self._get("/openApi/swap/v2/quote/contracts")
        for c in data.get("data", []):
            if c["symbol"] == symbol:
                return c
        raise ValueError(f"Symbol {symbol} not found")

    def round_qty(self, qty: float, step: float) -> float:
        """Floor quantity to the nearest valid step size"""
        return float(int(qty / step) * step)
