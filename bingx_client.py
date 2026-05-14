"""
BingX Perpetual Swap API Client — v2
Fix crítico: parseo correcto del formato de klines v3
BingX v3 devuelve: [timestamp, open, close, high, low, volume, amount]
(close y high estaban INTERCAMBIADOS en versiones anteriores)
"""

import hashlib, hmac, time, urllib.parse, logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)
BASE_URL = "https://open-api.bingx.com"


class BingXClient:
    def __init__(self, api_key: str, api_secret: str, demo: bool = False):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.demo       = demo
        self.session    = requests.Session()
        self.session.headers.update({"X-BX-APIKEY": self.api_key,
                                     "Content-Type": "application/json"})
        if demo:
            logger.warning("⚠️  DEMO mode — no real orders")

    def _sign(self, params: dict) -> str:
        q = urllib.parse.urlencode(sorted(params.items()))
        return hmac.new(self.api_secret.encode(), q.encode(), hashlib.sha256).hexdigest()

    def _ts(self) -> int:
        return int(time.time() * 1000)

    def _get(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)
        r = self.session.get(BASE_URL + path, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        if d.get("code", 0) != 0:
            raise RuntimeError(f"BingX [{d.get('code')}]: {d.get('msg')}")
        return d

    def _post(self, path: str, params: dict) -> dict:
        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)
        r = self.session.post(BASE_URL + path, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        if d.get("code", 0) != 0:
            raise RuntimeError(f"BingX [{d.get('code')}]: {d.get('msg')}")
        return d

    def _delete(self, path: str, params: dict) -> dict:
        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)
        r = self.session.delete(BASE_URL + path, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        if d.get("code", 0) != 0:
            raise RuntimeError(f"BingX [{d.get('code')}]: {d.get('msg')}")
        return d

    # ── Klines con parseo robusto ─────────────────────────────────────────
    def get_klines(self, symbol: str, interval: str = "3m", limit: int = 200) -> list:
        """
        Devuelve lista de dicts normalizados: {timestamp, open, high, low, close, volume}
        Maneja automáticamente el formato array de BingX v3.
        
        BingX v3 formato array: [timestamp, open, close, high, low, volume, amount]
        NOTA: close está en posición 2, high en posición 3 (NO es open/high/low/close)
        """
        data = self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": interval, "limit": limit,
        })
        raw = data.get("data", [])
        if not raw:
            return []

        result = []
        first  = raw[0]

        if isinstance(first, (list, tuple)):
            # Formato array v3: [ts, open, close, high, low, volume, amount]
            # Verificar longitud y reordenar a {ts, open, high, low, close, volume}
            n = len(first)
            for row in raw:
                try:
                    ts  = int(row[0])
                    o   = float(row[1])
                    # BingX v3: índice 2=close, 3=high, 4=low
                    if n >= 7:
                        c = float(row[2])
                        h = float(row[3])
                        l = float(row[4])
                        v = float(row[5])
                    elif n >= 6:
                        # Formato alternativo: [ts, open, high, low, close, volume]
                        h = float(row[2])
                        l = float(row[3])
                        c = float(row[4])
                        v = float(row[5])
                    else:
                        continue
                    result.append({"timestamp": ts, "open": o, "high": h,
                                   "low": l, "close": c, "volume": v})
                except (IndexError, ValueError, TypeError):
                    continue

        elif isinstance(first, dict):
            # Formato dict (v2 o algunos endpoints v3)
            key_map = {
                "time": "timestamp", "t": "timestamp",
                "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
            }
            for row in raw:
                norm = {}
                for k, val in row.items():
                    nk = key_map.get(k, k)
                    norm[nk] = val
                # Asegurar timestamp
                if "timestamp" not in norm and "time" in row:
                    norm["timestamp"] = row["time"]
                try:
                    result.append({
                        "timestamp": int(norm.get("timestamp", 0)),
                        "open":      float(norm.get("open",  0)),
                        "high":      float(norm.get("high",  0)),
                        "low":       float(norm.get("low",   0)),
                        "close":     float(norm.get("close", 0)),
                        "volume":    float(norm.get("volume",0)),
                    })
                except (ValueError, TypeError):
                    continue

        logger.debug(f"Klines {symbol} {interval}: {len(result)} velas parseadas")
        return sorted(result, key=lambda x: x["timestamp"])

    def get_ticker(self, symbol: str) -> dict:
        d = self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return d.get("data", {})

    def get_mark_price(self, symbol: str) -> float:
        d = self._get("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
        return float(d["data"]["markPrice"])

    def get_balance(self) -> dict:
        d = self._get("/openApi/swap/v2/user/balance")
        return d.get("data", {}).get("balance", {})

    def get_positions(self, symbol: str = "") -> list:
        p = {"symbol": symbol} if symbol else {}
        return self._get("/openApi/swap/v2/user/positions", p).get("data", [])

    def get_open_orders(self, symbol: str) -> list:
        d = self._get("/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
        return d.get("data", {}).get("orders", [])

    def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> dict:
        return self._post("/openApi/swap/v2/trade/leverage",
                          {"symbol": symbol, "side": side, "leverage": leverage})

    def set_margin_mode(self, symbol: str, mode: str = "ISOLATED") -> dict:
        return self._post("/openApi/swap/v2/trade/marginType",
                          {"symbol": symbol, "marginType": mode})

    def place_market_order(self, symbol, side, position_side, quantity,
                           reduce_only=False) -> dict:
        if self.demo:
            logger.info(f"[DEMO] {side}/{position_side} {quantity} {symbol}")
            return {"orderId": "DEMO_" + str(int(time.time()))}
        p = {"symbol": symbol, "side": side, "positionSide": position_side,
             "type": "MARKET", "quantity": quantity}
        if reduce_only:
            p["reduceOnly"] = "true"
        return self._post("/openApi/swap/v2/trade/order", p)

    def place_stop_loss(self, symbol, side, position_side, stop_price, quantity) -> dict:
        if self.demo:
            return {"demo": True}
        return self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": side, "positionSide": position_side,
            "type": "STOP_MARKET", "stopPrice": stop_price,
            "quantity": quantity, "reduceOnly": "true"})

    def place_take_profit(self, symbol, side, position_side, stop_price, quantity) -> dict:
        if self.demo:
            return {"demo": True}
        return self._post("/openApi/swap/v2/trade/order", {
            "symbol": symbol, "side": side, "positionSide": position_side,
            "type": "TAKE_PROFIT_MARKET", "stopPrice": stop_price,
            "quantity": quantity, "reduceOnly": "true"})

    def cancel_all_orders(self, symbol: str) -> dict:
        if self.demo:
            return {"demo": True}
        return self._delete("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})

    def close_position(self, symbol: str, position_side: str, quantity: float) -> dict:
        side = "SELL" if position_side == "LONG" else "BUY"
        return self.place_market_order(symbol, side, position_side, quantity, reduce_only=True)

    def get_symbol_info(self, symbol: str) -> dict:
        data = self._get("/openApi/swap/v2/quote/contracts")
        for c in data.get("data", []):
            if c["symbol"] == symbol:
                return c
        raise ValueError(f"Symbol {symbol} not found")
