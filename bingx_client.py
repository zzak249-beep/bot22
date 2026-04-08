"""
BingX API Client - Perpetual Futures (Swap)
Optimized for low fees using maker orders.
Fixed: retry logic, rate limiting, proper error handling, balance parsing.
"""
import hmac, hashlib, time, requests, json, logging
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
        self.session    = requests.Session()
        self.session.headers.update({
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json",
        })

    # ── Auth ──────────────────────────────────────────────────────────────
    def _sign(self, params: dict) -> str:
        payload = urlencode(sorted(params.items()))
        return hmac.new(
            self.secret_key.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()

    def _request(self, method: str, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)

        for attempt in range(MAX_RETRIES):
            try:
                if method == "GET":
                    r = self.session.get(BASE_URL + path, params=params, timeout=15)
                else:
                    r = self.session.post(BASE_URL + path, params=params, timeout=15)

                if r.status_code == 429:
                    wait = float(r.headers.get("Retry-After", 10))
                    log.warning(f"Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue

                r.raise_for_status()
                data = r.json()
                code = data.get("code", 0)
                if code != 0:
                    msg = data.get("msg", "Unknown error")
                    log.error(f"BingX API code {code}: {msg} | {path}")
                return data

            except requests.exceptions.Timeout:
                log.warning(f"Timeout {path}, attempt {attempt+1}/{MAX_RETRIES}")
                time.sleep(RETRY_DELAY * (attempt + 1))
            except requests.exceptions.RequestException as e:
                log.error(f"Request error {path}: {e}")
                time.sleep(RETRY_DELAY)
        return {}

    def _get(self, path: str, params: dict = None) -> dict:
        return self._request("GET", path, params)

    def _post(self, path: str, params: dict = None) -> dict:
        return self._request("POST", path, params)

    # ── Helpers ───────────────────────────────────────────────────────────
    def _parse_balance_data(self, data: dict) -> Optional[dict]:
        """
        Handles multiple BingX API response formats for balance:
        - Format A: data.balance = [ {"asset": "USDT", ...}, ... ]
        - Format B: data.balance = { "asset": "USDT", ... }
        - Format C: data = { "asset": "USDT", ... }
        Returns the USDT balance dict or None.
        """
        raw = data.get("data", {})
        log.debug(f"Raw balance response: {raw}")

        balance = raw.get("balance", raw)  # fallback to raw if no "balance" key

        if isinstance(balance, dict):
            # Format B or C — single dict
            if balance.get("asset") == "USDT":
                return balance
            # Maybe it's nested differently, search values
            for v in balance.values():
                if isinstance(v, dict) and v.get("asset") == "USDT":
                    return v
            # If no asset key, assume it's the USDT balance directly
            if "availableMargin" in balance or "equity" in balance:
                return balance

        elif isinstance(balance, list):
            # Format A — list of dicts or strings
            for a in balance:
                if isinstance(a, dict) and a.get("asset") == "USDT":
                    return a

        log.warning(f"Could not parse balance from response: {data}")
        return None

    # ── Market Data ───────────────────────────────────────────────────────
    def get_all_symbols(self) -> list:
        data = self._get("/openApi/swap/v2/quote/contracts")
        return [
            c["symbol"] for c in data.get("data", [])
            if c.get("currency") == "USDT" and c.get("status") == 1
        ]

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 250) -> list:
        data = self._get("/openApi/swap/v3/quote/klines", {
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

    def get_ticker(self, symbol: str) -> dict:
        data = self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return data.get("data", {})

    def get_24h_volume(self, symbol: str) -> float:
        t = self.get_ticker(symbol)
        return float(t.get("quoteVolume", 0))

    def get_orderbook(self, symbol: str, limit: int = 5) -> dict:
        data = self._get("/openApi/swap/v2/quote/depth", {
            "symbol": symbol, "limit": limit
        })
        return data.get("data", {})

    # ── Account ───────────────────────────────────────────────────────────
    def get_balance(self) -> float:
        data = self._get("/openApi/swap/v2/user/balance")
        usdt = self._parse_balance_data(data)
        if usdt:
            return float(usdt.get("availableMargin", 0))
        return 0.0

    def get_total_equity(self) -> float:
        data = self._get("/openApi/swap/v2/user/balance")
        usdt = self._parse_balance_data(data)
        if usdt:
            return float(usdt.get("equity", 0))
        return 0.0

    def get_positions(self) -> list:
        data = self._get("/openApi/swap/v2/user/positions")
        return [p for p in data.get("data", []) if float(p.get("positionAmt", 0)) != 0]

    def get_open_orders(self, symbol: str) -> list:
        data = self._get("/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
        return data.get("data", {}).get("orders", [])

    def get_order_status(self, symbol: str, order_id: str) -> dict:
        data = self._get("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "orderId": order_id
        })
        return data.get("data", {})

    # ── Trading ───────────────────────────────────────────────────────────
    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Set leverage for both LONG and SHORT sides."""
        for side in ("LONG", "SHORT"):
            self._post("/openApi/swap/v2/trade/leverage", {
                "symbol": symbol, "side": side, "leverage": leverage
            })
        return {"status": "ok"}

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        return self._post("/openApi/swap/v2/trade/marginType", {
            "symbol": symbol, "marginType": margin_type
        })

    def place_order(
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

        result = self._post("/openApi/swap/v2/trade/order", params)
        return result

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})

    def close_position(self, symbol: str, position_side: str, quantity: float) -> dict:
        side = "SELL" if position_side == "LONG" else "BUY"
        return self.place_order(
            symbol, side, position_side, "MARKET", quantity, reduce_only=True
        )

    def get_symbol_info(self, symbol: str) -> dict:
        data = self._get("/openApi/swap/v2/quote/contracts")
        for c in data.get("data", []):
            if c["symbol"] == symbol:
                return c
        return {}

    def get_income_history(self, income_type: str = "REALIZED_PNL", limit: int = 50) -> list:
        data = self._get("/openApi/swap/v2/user/income", {
            "incomeType": income_type, "limit": limit
        })
        return data.get("data", [])
