"""Cliente BingX perpetuos USDT-M (swap v2/v3).

Firma: query ordenada (sorted) + timestamp + recvWindow → HMAC-SHA256 hex → &signature=.
La MISMA cadena firmada es la que se envía (URL en GET/DELETE, body form-urlencoded en POST).
Los POST no se reintentan nunca (evita órdenes duplicadas).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import math
import time
from urllib.parse import urlencode

import pandas as pd
import requests

log = logging.getLogger("bingx")


class BingXError(Exception):
    def __init__(self, payload):
        self.payload = payload
        self.code = payload.get("code") if isinstance(payload, dict) else None
        super().__init__(str(payload)[:400])


def build_query(params: dict, secret: str | None = None, ts_ms: int | None = None) -> str:
    p = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    if secret is not None:
        p["timestamp"] = ts_ms if ts_ms is not None else int(time.time() * 1000)
        p.setdefault("recvWindow", 5000)
    qs = urlencode(sorted(p.items()))
    if secret is not None:
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        qs = f"{qs}&signature={sig}"
    return qs


def fnum(x: float, prec: int) -> str:
    return f"{x:.{max(0, int(prec))}f}"


def floor_to(x: float, prec: int) -> float:
    f = 10 ** max(0, int(prec))
    return math.floor(x * f + 1e-9) / f


class BingX:
    def __init__(self, api_key: str = "", api_secret: str = "", base: str = "https://open-api.bingx.com"):
        self.key, self.secret, self.base = api_key, api_secret, base.rstrip("/")
        self.s = requests.Session()

    # ───────────── núcleo ─────────────
    def _req(self, method: str, path: str, params: dict | None = None, signed: bool = False):
        url = self.base + path
        tries = 1 if method == "POST" else 3
        last = None
        for i in range(tries):
            qs = build_query(params or {}, self.secret if signed else None)
            headers = {"X-BX-APIKEY": self.key} if self.key else {}
            try:
                if method == "POST":
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                    r = self.s.post(url, data=qs, headers=headers, timeout=12)
                else:
                    r = self.s.request(method, f"{url}?{qs}" if qs else url, headers=headers, timeout=12)
                j = r.json()
            except (requests.RequestException, ValueError) as e:
                last = e
                log.warning("red %s %s intento %d: %s", method, path, i + 1, e)
                time.sleep(1.5 * (i + 1))
                continue
            if isinstance(j, dict) and j.get("code", 0) not in (0, "0"):
                if j.get("code") in (100410, 429) and i + 1 < tries:  # rate limit
                    time.sleep(3 * (i + 1))
                    continue
                raise BingXError(j)
            return j.get("data") if isinstance(j, dict) else j
        raise BingXError({"code": -1, "msg": f"sin respuesta {path}: {last}"})

    # ───────────── público ─────────────
    def contracts(self) -> dict:
        data = self._req("GET", "/openApi/swap/v2/quote/contracts") or []
        out = {}
        for c in data:
            sym = c.get("symbol")
            if not sym:
                continue
            out[sym] = {
                "qprec": int(c.get("quantityPrecision", 0) or 0),
                "pprec": int(c.get("pricePrecision", 4) or 4),
                "min_qty": float(c.get("tradeMinQuantity", 0) or 0),
                "min_usdt": float(c.get("tradeMinUSDT", 0) or 0),
                "status": int(c.get("status", 1) or 1),
            }
        return out

    def tickers(self) -> list[dict]:
        return self._req("GET", "/openApi/swap/v2/quote/ticker") or []

    def klines(self, symbol: str, interval: str = "5m", limit: int = 1000,
               start_ms: int | None = None, end_ms: int | None = None) -> pd.DataFrame:
        data = self._req("GET", "/openApi/swap/v3/quote/klines",
                         {"symbol": symbol, "interval": interval, "limit": limit,
                          "startTime": start_ms, "endTime": end_ms}) or []
        rows = []
        for k in data:
            if isinstance(k, dict):
                rows.append((int(k["time"]), float(k["open"]), float(k["high"]), float(k["low"]),
                             float(k["close"]), float(k.get("volume", 0) or 0)))
            else:
                rows.append((int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])))
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)

    def premium_index(self, symbol: str) -> dict:
        """lastFundingRate (fracción) y nextFundingTime (ms)."""
        d = self._req("GET", "/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol}) or {}
        if isinstance(d, list):
            d = d[0] if d else {}
        return {"rate": float(d.get("lastFundingRate", 0) or 0),
                "next_ms": int(float(d.get("nextFundingTime", 0) or 0))}

    # ───────────── cuenta ─────────────
    def income(self, symbol: str, start_ms: int, end_ms: int) -> float:
        """Suma de PnL realizado + comisiones + funding del símbolo en el intervalo (USDT)."""
        d = self._req("GET", "/openApi/swap/v2/user/income",
                      {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
                      signed=True) or []
        return float(sum(float(x.get("income", 0) or 0) for x in d
                         if str(x.get("asset", "USDT")).upper() in ("USDT", "")))

    def equity(self) -> float:
        d = self._req("GET", "/openApi/swap/v2/user/balance", signed=True)
        b = d.get("balance", d) if isinstance(d, dict) else d
        if isinstance(b, list):
            b = next((x for x in b if x.get("asset") == "USDT"), b[0] if b else {})
        return float(b.get("equity") or b.get("balance") or 0)

    def positions(self, symbol: str | None = None) -> list[dict]:
        d = self._req("GET", "/openApi/swap/v2/user/positions", {"symbol": symbol}, signed=True) or []
        return [x for x in d if abs(float(x.get("positionAmt", 0) or 0)) > 0]

    def hedge_mode(self) -> bool:
        d = self._req("GET", "/openApi/swap/v1/positionSide/dual", signed=True) or {}
        return str(d.get("dualSidePosition", "false")).lower() == "true"

    def set_leverage(self, symbol: str, side: str, lev: int):
        return self._req("POST", "/openApi/swap/v2/trade/leverage",
                         {"symbol": symbol, "side": side, "leverage": int(lev)}, signed=True)

    # ───────────── órdenes ─────────────
    def order(self, **params) -> str:
        d = self._req("POST", "/openApi/swap/v2/trade/order", params, signed=True) or {}
        o = d.get("order", d)
        return str(o.get("orderId") or o.get("orderID") or "")

    def cancel(self, symbol: str, order_id: str):
        if order_id:
            return self._req("DELETE", "/openApi/swap/v2/trade/order",
                             {"symbol": symbol, "orderId": order_id}, signed=True)

    def cancel_all(self, symbol: str):
        return self._req("DELETE", "/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol}, signed=True)

    def open_orders(self, symbol: str) -> list[dict]:
        d = self._req("GET", "/openApi/swap/v2/trade/openOrders", {"symbol": symbol}, signed=True) or {}
        return d.get("orders", d) if isinstance(d, dict) else d
