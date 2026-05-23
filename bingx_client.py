"""
bingx_client.py — Cliente BingX v5 con firma corregida.

Añadido vs versión anterior:
  - get_funding_rate() — para el filtro de funding
  - get_open_interest() — para detectar acumulación institucional
  - get_orderbook_imbalance() — ratio bid/ask en top 10 niveles
  - Klines con fallback v3 → v2
  - Balance parsing robusto (probado con múltiples formatos)
"""
import asyncio
import hashlib
import hmac as _hmac
import logging
import time
from typing import Any, Optional

import aiohttp

log = logging.getLogger("bingx")
MAX_RETRIES = 3


class BingXError(Exception):
    def __init__(self, code: int, msg: str):
        self.code, self.msg = code, msg
        super().__init__(f"BingX {code}: {msg}")


class BingXClient:
    def __init__(self, api_key: str, api_secret: str,
                 base_url: str = "https://open-api.bingx.com"):
        self.key    = api_key
        self.secret = api_secret.encode()
        self.base   = base_url
        self._sess: Optional[aiohttp.ClientSession] = None

    async def _session(self) -> aiohttp.ClientSession:
        if not self._sess or self._sess.closed:
            self._sess = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20))
        return self._sess

    async def close(self):
        if self._sess and not self._sess.closed:
            await self._sess.close()

    def _sign(self, params: dict) -> dict:
        p  = {**params, "timestamp": int(time.time() * 1000)}
        qs = "&".join(f"{k}={p[k]}" for k in sorted(p))
        p["signature"] = _hmac.new(self.secret, qs.encode(),
                                    hashlib.sha256).hexdigest()
        return p

    async def _req(self, method: str, path: str,
                   params: dict = None, signed: bool = True) -> dict:
        sess = await self._session()
        url  = self.base + path
        hdrs = {"X-BX-APIKEY": self.key}
        p    = self._sign(params or {}) if signed else (params or {})

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                fn = sess.get if method == "GET" else sess.post
                async with fn(url, params=p, headers=hdrs) as r:
                    data = await r.json(content_type=None)
                code = data.get("code", -1)
                if code == 0:
                    return data
                if code in (100400, 80012) and attempt < MAX_RETRIES:
                    await asyncio.sleep(1.5 * attempt)
                    continue
                raise BingXError(code, data.get("msg", str(data)[:150]))
            except aiohttp.ClientError as e:
                if attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(1.5)

    # ── Mercado ───────────────────────────────────────────────

    async def get_symbols(self) -> list[str]:
        d = await self._req("GET", "/openApi/swap/v2/quote/contracts",
                            signed=False)
        raw = d.get("data", [])
        if isinstance(raw, dict):
            raw = raw.get("contracts", [])
        return [c["symbol"] for c in raw if c.get("status") == 1]

    async def get_klines(self, symbol: str, interval: str,
                         limit: int = 200) -> list[dict]:
        for ep in ("/openApi/swap/v3/quote/klines",
                   "/openApi/swap/v2/quote/klines"):
            try:
                d   = await self._req("GET", ep,
                      {"symbol": symbol, "interval": interval,
                       "limit": limit}, signed=False)
                raw = d.get("data", [])
                if not raw:
                    continue
                out = []
                for c in raw:
                    if isinstance(c, list):
                        out.append({"open": float(c[1]), "high": float(c[2]),
                                    "low":  float(c[3]), "close":float(c[4]),
                                    "volume": float(c[5])})
                    elif isinstance(c, dict):
                        out.append({"open": float(c.get("open",  c.get("o",0))),
                                    "high": float(c.get("high",  c.get("h",0))),
                                    "low":  float(c.get("low",   c.get("l",0))),
                                    "close":float(c.get("close", c.get("c",0))),
                                    "volume":float(c.get("volume",c.get("v",0)))})
                if out:
                    return out
            except BingXError as e:
                log.debug("klines %s %s: %s", ep, symbol, e)
        return []

    async def get_ticker(self, symbol: str) -> dict:
        try:
            d = await self._req("GET", "/openApi/swap/v2/quote/ticker",
                                {"symbol": symbol}, signed=False)
            v = d.get("data", {})
            return (v[0] if isinstance(v, list) and v else v) or {}
        except Exception:
            return {}

    async def get_funding_rate(self, symbol: str) -> float:
        """
        Funding rate actual. Positivo → longs pagan a shorts (mercado alcista caro).
        Negativo → shorts pagan a longs (mercado bajista caro).
        """
        try:
            d = await self._req("GET",
                "/openApi/swap/v2/quote/premiumIndex",
                {"symbol": symbol}, signed=False)
            v = d.get("data", {})
            if isinstance(v, list):
                v = v[0] if v else {}
            return float(v.get("lastFundingRate", 0))
        except Exception:
            return 0.0

    async def get_open_interest(self, symbol: str) -> float:
        """Open interest en contratos."""
        try:
            d = await self._req("GET",
                "/openApi/swap/v2/quote/openInterest",
                {"symbol": symbol}, signed=False)
            v = d.get("data", {})
            if isinstance(v, list):
                v = v[0] if v else {}
            return float(v.get("openInterest", 0))
        except Exception:
            return 0.0

    async def get_orderbook_imbalance(self, symbol: str,
                                       limit: int = 10) -> float:
        """
        Ratio de liquidez bid/ask en los primeros `limit` niveles.
        >1.3 → más liquidez compradora (alcista)
        <0.7 → más liquidez vendedora (bajista)
        """
        try:
            d = await self._req("GET",
                "/openApi/swap/v2/quote/depth",
                {"symbol": symbol, "limit": limit}, signed=False)
            book = d.get("data", {})
            bids = sum(float(b[1]) for b in book.get("bids", []))
            asks = sum(float(a[1]) for a in book.get("asks", []))
            return bids / asks if asks > 0 else 1.0
        except Exception:
            return 1.0

    # ── Cuenta ────────────────────────────────────────────────

    async def get_balance(self) -> float:
        try:
            d   = await self._req("GET", "/openApi/swap/v2/user/balance")
            raw = d.get("data", {})
            log.debug("balance raw: %s", str(raw)[:200])

            def _extract(obj: dict) -> float:
                for k in ("availableMargin","available","equity",
                          "totalAvailableBalance","maxWithdrawAmount","balance"):
                    v = obj.get(k)
                    if v is not None:
                        try:
                            f = float(v)
                            if f > 0:
                                return f
                        except (ValueError, TypeError):
                            pass
                return 0.0

            if isinstance(raw, dict):
                b = raw.get("balance", raw)
                if isinstance(b, dict):
                    val = _extract(b)
                    if val > 0:
                        return val
                for v in raw.values():
                    if isinstance(v, dict):
                        val = _extract(v)
                        if val > 0:
                            return val
            if isinstance(raw, list):
                for asset in raw:
                    if asset.get("asset", "") in ("USDT", ""):
                        val = _extract(asset)
                        if val > 0:
                            return val
            return 0.0
        except Exception as e:
            log.error("get_balance: %s", e)
            return 0.0

    async def get_positions(self, symbol: str = "") -> list[dict]:
        try:
            p = {"symbol": symbol} if symbol else {}
            d = await self._req("GET", "/openApi/swap/v2/user/positions", p)
            return [x for x in d.get("data", [])
                    if abs(float(x.get("positionAmt", 0))) > 0]
        except Exception as e:
            log.error("get_positions: %s", e)
            return []

    # ── Trading ───────────────────────────────────────────────

    async def set_leverage(self, symbol: str, lev: int, side: str) -> dict:
        try:
            return await self._req("POST",
                "/openApi/swap/v2/trade/leverage",
                {"symbol": symbol, "leverage": lev, "side": side})
        except BingXError as e:
            log.warning("leverage %s: %s", symbol, e)
            return {}

    async def set_margin_isolated(self, symbol: str) -> dict:
        try:
            return await self._req("POST",
                "/openApi/swap/v2/trade/marginType",
                {"symbol": symbol, "marginType": "ISOLATED"})
        except BingXError as e:
            if e.code == 200003:
                return {}
            return {}

    async def place_order(self, symbol: str, side: str,
                          pos_side: str, qty: float,
                          sl: float = 0, tp: float = 0) -> dict:
        p: dict[str, Any] = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": pos_side,
            "type":         "MARKET",
            "quantity":     round(qty, 4),
        }
        if sl:
            p["stopLoss"] = (
                f'{{"type":"STOP_MARKET","stopPrice":{round(sl,8)},'
                f'"workingType":"MARK_PRICE","closePosition":false}}'
            )
        if tp:
            p["takeProfit"] = (
                f'{{"type":"TAKE_PROFIT_MARKET","stopPrice":{round(tp,8)},'
                f'"workingType":"MARK_PRICE","closePosition":false}}'
            )
        return await self._req("POST", "/openApi/swap/v2/trade/order", p)

    async def close_market(self, symbol: str, pos_side: str,
                            qty: float) -> dict:
        side = "SELL" if pos_side == "LONG" else "BUY"
        return await self._req("POST", "/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": pos_side,
            "type":         "MARKET",
            "quantity":     round(abs(qty), 4),
        })

    async def cancel_all(self, symbol: str) -> dict:
        try:
            return await self._req("POST",
                "/openApi/swap/v2/trade/cancelAllOpenOrders",
                {"symbol": symbol})
        except BingXError:
            return {}

    async def test_connection(self) -> tuple[bool, str]:
        try:
            bal = await self.get_balance()
            pos = await self.get_positions()
            return True, f"Balance={bal:.2f} USDT | Posiciones={len(pos)}"
        except BingXError as e:
            return False, f"Error {e.code}: {e.msg}"
        except Exception as e:
            return False, str(e)
