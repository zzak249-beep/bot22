"""
Cliente BingX Perpetual Futures (Swap V2)
Docs: https://bingx-api.github.io/docs/#/swapV2/
"""

import hmac
import hashlib
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://open-api.bingx.com"


class BingXError(Exception):
    pass


class BingXClient:
    def __init__(self, api_key: str, secret_key: str, demo: bool = False):
        self.api_key = api_key
        self.secret_key = secret_key
        self.demo = demo  # Si True, usa cuenta demo de BingX
        self.session = requests.Session()
        self.session.headers.update({
            "X-BX-APIKEY": api_key,
            "Content-Type": "application/json",
        })

    # ───────────────────────── AUTH ─────────────────────────

    def _sign(self, params: dict) -> str:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _ts(self) -> int:
        return int(time.time() * 1000)

    # ──────────────────────── REQUESTS ───────────────────────

    def _get(self, path: str, params: dict = None) -> dict:
        params = {**(params or {})}
        params["timestamp"] = self._ts()
        if self.demo:
            params["demoTradingFlag"] = "true"
        params["signature"] = self._sign(params)
        try:
            r = self.session.get(f"{BASE_URL}{path}", params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise BingXError(f"GET {path} falló: {e}") from e

        if data.get("code", 0) != 0:
            raise BingXError(f"API error {data.get('code')}: {data.get('msg')}")
        return data

    def _post(self, path: str, params: dict = None) -> dict:
        params = {**(params or {})}
        params["timestamp"] = self._ts()
        if self.demo:
            params["demoTradingFlag"] = "true"
        params["signature"] = self._sign(params)
        try:
            r = self.session.post(f"{BASE_URL}{path}", params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise BingXError(f"POST {path} falló: {e}") from e

        if data.get("code", 0) != 0:
            raise BingXError(f"API error {data.get('code')}: {data.get('msg')}")
        return data

    # ─────────────────────── MARKET DATA ─────────────────────

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 500) -> list:
        """
        Retorna velas OHLCV.
        interval: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w, 1M
        """
        data = self._get("/openApi/swap/v2/quote/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        return data.get("data", [])

    def get_ticker(self, symbol: str) -> dict:
        data = self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return data.get("data", {})

    def get_price(self, symbol: str) -> float:
        ticker = self.get_ticker(symbol)
        return float(ticker.get("lastPrice", 0))

    def get_exchange_info(self, symbol: str) -> dict:
        data = self._get("/openApi/swap/v2/quote/contracts")
        for contract in data.get("data", []):
            if contract.get("symbol") == symbol:
                return contract
        return {}

    # ─────────────────────── ACCOUNT ─────────────────────────

    def get_balance(self) -> float:
        """Retorna USDT disponible para margin."""
        data = self._get("/openApi/swap/v2/user/balance")
        balance_info = data.get("data", {}).get("balance", {})
        return float(balance_info.get("availableMargin", 0))

    def get_positions(self, symbol: str) -> list:
        data = self._get("/openApi/swap/v2/user/positions", {"symbol": symbol})
        return data.get("data", [])

    def get_open_position(self, symbol: str) -> Optional[dict]:
        """Retorna la posición abierta activa o None."""
        positions = self.get_positions(symbol)
        for pos in positions:
            amt = float(pos.get("positionAmt", 0))
            if abs(amt) > 0:
                return pos
        return None

    # ─────────────────────── TRADING ─────────────────────────

    def set_leverage(self, symbol: str, leverage: int):
        """Ajusta el apalancamiento para ambos lados."""
        for side in ("LONG", "SHORT"):
            try:
                self._post("/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol,
                    "side": side,
                    "leverage": leverage,
                })
                logger.debug(f"Leverage {leverage}x configurado para {symbol} {side}")
            except BingXError as e:
                logger.warning(f"set_leverage: {e}")

    def place_market_order(self, symbol: str, side: str, quantity: float,
                           position_side: str = "BOTH") -> dict:
        """
        side          : BUY | SELL
        position_side : BOTH (one-way) | LONG | SHORT (hedge mode)
        quantity      : Cantidad en moneda base (ej. 0.001 BTC)
        """
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": quantity,
        }
        data = self._post("/openApi/swap/v2/trade/order", params)
        order = data.get("data", {}).get("order", {})
        logger.info(f"✅ Orden ejecutada: {side} {quantity} {symbol} → orderId={order.get('orderId')}")
        return order

    def close_all_positions(self, symbol: str) -> dict:
        """
        Cierra la posicion abierta usando orden de mercado con reduceOnly.
        Mas fiable que el endpoint closeAllPositions (error 109400).
        """
        pos = self.get_open_position(symbol)
        if pos is None:
            logger.info(f"Sin posicion abierta para {symbol}, nada que cerrar")
            return {}

        amt = float(pos.get("positionAmt", 0))
        if amt == 0:
            return {}

        close_side = "SELL" if amt > 0 else "BUY"
        quantity = abs(amt)

        params = {
            "symbol":       symbol,
            "side":         close_side,
            "positionSide": "BOTH",
            "type":         "MARKET",
            "quantity":     quantity,
            "reduceOnly":   "true",
        }
        data = self._post("/openApi/swap/v2/trade/order", params)
        order = data.get("data", {}).get("order", {})
        logger.info(f"Posicion cerrada: {close_side} {quantity} {symbol} -> orderId={order.get('orderId')}")
        return data

    def cancel_all_orders(self, symbol: str):
        """Cancela todas las órdenes abiertas."""
        try:
            self._post("/openApi/swap/v2/trade/cancelAllOpenOrders", {
                "symbol": symbol,
            })
        except BingXError as e:
            logger.warning(f"cancel_all_orders: {e}")
