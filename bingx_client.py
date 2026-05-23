"""
BingX Client — Hedge Mode compatible
positionSide se pasa explícitamente como LONG/SHORT
"""
import hashlib, hmac, time, logging
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
BASE = "https://open-api.bingx.com"


def _make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.3, status_forcelist=[429,500,502,503,504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    s.mount("https://", adapter); s.mount("http://", adapter)
    return s


class BingXClient:
    def __init__(self, api_key, api_secret, demo=False):
        self.api_key = api_key; self.api_secret = api_secret; self.demo = demo
        self.s = _make_session()
        self.s.headers.update({"X-BX-APIKEY": api_key})
        if demo: logger.warning("⚠️ DEMO mode")

    def _ts(self): return int(time.time() * 1000)

    def _sign(self, params):
        payload = "&".join(f"{k}={v}" for k, v in params.items())
        return hmac.new(self.api_secret.encode("utf-8"),
                        payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _req(self, method, path, params):
        params["timestamp"] = self._ts()
        params["signature"] = self._sign(params)
        url = BASE + path
        try:
            if method == "GET":     r = self.s.get(url,    params=params, timeout=10)
            elif method == "POST":  r = self.s.post(url,   params=params, timeout=10)
            elif method == "DELETE":r = self.s.delete(url, params=params, timeout=10)
            r.raise_for_status()
            d = r.json()
            if d.get("code", 0) != 0:
                raise RuntimeError(f"BingX [{d.get('code')}]: {d.get('msg')}")
            return d
        except RuntimeError: raise
        except Exception as e: raise RuntimeError(f"HTTP error: {e}")

    def get_klines(self, symbol, interval="3m", limit=200):
        d = self._req("GET", "/openApi/swap/v3/quote/klines",
                      {"symbol": symbol, "interval": interval, "limit": limit})
        raw = d.get("data", []); out = []
        if not raw: return out
        first = raw[0]
        if isinstance(first, (list, tuple)):
            n = len(first)
            for row in raw:
                try:
                    ts = int(row[0]); o = float(row[1])
                    if n >= 7: c=float(row[2]); h=float(row[3]); l=float(row[4]); v=float(row[5])
                    else:      h=float(row[2]); l=float(row[3]); c=float(row[4]); v=float(row[5])
                    out.append({"timestamp":ts,"open":o,"high":h,"low":l,"close":c,"volume":v})
                except: continue
        elif isinstance(first, dict):
            km = {"time":"timestamp","t":"timestamp","o":"open","h":"high",
                  "l":"low","c":"close","v":"volume"}
            for row in raw:
                try:
                    n = {km.get(k,k): v for k, v in row.items()}
                    out.append({"timestamp":int(n.get("timestamp",0)),
                                "open":float(n.get("open",0)),
                                "high":float(n.get("high",0)),
                                "low":float(n.get("low",0)),
                                "close":float(n.get("close",0)),
                                "volume":float(n.get("volume",0))})
                except: continue
        return sorted(out, key=lambda x: x["timestamp"])

    def get_ticker(self, symbol):
        return self._req("GET", "/openApi/swap/v2/quote/ticker",
                         {"symbol": symbol}).get("data", {})

    def get_balance(self):
        return self._req("GET", "/openApi/swap/v2/user/balance",
                         {}).get("data", {}).get("balance", {})

    def get_positions(self, symbol=""):
        p = {"symbol": symbol} if symbol else {}
        return self._req("GET", "/openApi/swap/v2/user/positions", p).get("data", [])

    def set_leverage(self, symbol, leverage, side="LONG"):
        return self._req("POST", "/openApi/swap/v2/trade/leverage",
                         {"symbol": symbol, "side": side, "leverage": leverage})

    def set_margin_mode(self, symbol, mode="ISOLATED"):
        return self._req("POST", "/openApi/swap/v2/trade/marginType",
                         {"symbol": symbol, "marginType": mode})

    def place_market_order(self, symbol, side, pos_side, quantity, reduce_only=False):
        if self.demo: return {"orderId": f"DEMO_{int(time.time())}"}
        p = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": pos_side,   # LONG o SHORT — nunca BOTH
            "type":         "MARKET",
            "quantity":     quantity,
        }
        if reduce_only: p["reduceOnly"] = "true"
        return self._req("POST", "/openApi/swap/v2/trade/order", p)

    def place_stop_loss(self, symbol, side, pos_side, stop_price, qty):
        if self.demo: return {}
        return self._req("POST", "/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": pos_side,
            "type":         "STOP_MARKET",
            "stopPrice":    stop_price,
            "quantity":     qty,
            "reduceOnly":   "true",
        })

    def place_take_profit(self, symbol, side, pos_side, stop_price, qty):
        if self.demo: return {}
        return self._req("POST", "/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": pos_side,
            "type":         "TAKE_PROFIT_MARKET",
            "stopPrice":    stop_price,
            "quantity":     qty,
            "reduceOnly":   "true",
        })

    def cancel_all_orders(self, symbol):
        if self.demo: return {}
        return self._req("DELETE", "/openApi/swap/v2/trade/allOpenOrders",
                         {"symbol": symbol})

    def close_position(self, symbol, pos_side, qty):
        side = "SELL" if pos_side == "LONG" else "BUY"
        return self.place_market_order(symbol, side, pos_side, qty, reduce_only=True)

    def get_symbol_info(self, symbol):
        data = self._req("GET", "/openApi/swap/v2/quote/contracts", {})
        for c in data.get("data", []):
            if c["symbol"] == symbol: return c
        raise ValueError(f"Symbol {symbol} not found")

    def get_all_symbols(self):
        data = self._req("GET", "/openApi/swap/v2/quote/contracts", {})
        return [c["symbol"] for c in data.get("data", []) if "-" in c.get("symbol", "")]
