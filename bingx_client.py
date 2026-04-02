"""
Cliente BingX Perpetual Futures (Swap V2) — v2 FIX
Docs: https://bingx-api.github.io/docs/#/swapV2/

FIXES v2:
  FIX-1  get_open_position() detecta correctamente LONG/SHORT en Hedge mode
         Antes: amt > 0 → "long" (incorrecto en Hedge mode)
         Ahora: usa positionSide explícito de la respuesta API
  FIX-2  close_all_positions() usa positionSide correcto (no BOTH fijo)
  FIX-3  place_market_order() acepta positionSide dinámico
  FIX-4  send_telegram() añadido para notificaciones
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
    def __init__(self, api_key: str, secret_key: str, demo: bool = False,
                 telegram_token: str = "", telegram_chat: str = ""):
        self.api_key = api_key
        self.secret_key = secret_key
        self.demo = demo
        self.telegram_token = telegram_token
        self.telegram_chat  = telegram_chat
        self.session = requests.Session()
        self.session.headers.update({
            "X-BX-APIKEY": api_key,
            "Content-Type": "application/json",
        })

    # ───────────────────────── AUTH ─────────────────────────

    def _sign(self, params: dict) -> str:
        query = "&".join(f"{k}={v}" for k, v in params.items())
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
        sig = self._sign(params)
        params["signature"] = sig
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
        sig = self._sign(params)
        params["signature"] = sig
        try:
            r = self.session.post(f"{BASE_URL}{path}", params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise BingXError(f"POST {path} falló: {e}") from e
        if data.get("code", 0) != 0:
            raise BingXError(f"API error {data.get('code')}: {data.get('msg')}")
        return data

    # ─────────────────────── TELEGRAM ─────────────────────────

    def send_telegram(self, msg: str):
        """
        FIX-4: Envía notificación a Telegram.
        Requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el .env
        """
        if not self.telegram_token or not self.telegram_chat:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat, "text": msg, "parse_mode": "HTML"},
                timeout=6
            )
        except Exception as e:
            logger.warning(f"Telegram error: {e}")

    # ─────────────────────── MARKET DATA ─────────────────────

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 500) -> list:
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
        data = self._get("/openApi/swap/v2/user/balance")
        balance_info = data.get("data", {}).get("balance", {})
        return float(balance_info.get("availableMargin", 0))

    def get_positions(self, symbol: str) -> list:
        data = self._get("/openApi/swap/v2/user/positions", {"symbol": symbol})
        return data.get("data", [])

    def get_open_position(self, symbol: str) -> Optional[dict]:
        """
        FIX-1: Detecta correctamente LONG/SHORT en Hedge mode.
        Antes: solo miraba positionAmt != 0 → podía devolver un SHORT
               y sync_position() lo interpretaba como LONG (amt > 0).
        Ahora: devuelve el primer registro con posición real,
               y añade campo 'detected_side' con LONG/SHORT explícito.
        """
        positions = self.get_positions(symbol)
        logger.debug(f"Posiciones raw de BingX ({symbol}): {positions}")
        for pos in positions:
            amt      = float(pos.get("positionAmt", 0) or 0)
            pos_side = str(pos.get("positionSide", "")).upper()

            if abs(amt) == 0:
                continue

            # Detectar lado real:
            # - Hedge mode: positionSide = "LONG" o "SHORT" (explícito)
            # - One-Way mode: positionSide = "BOTH", lado por signo de amt
            if pos_side == "LONG":
                detected_side = "long"
            elif pos_side == "SHORT":
                detected_side = "short"
            elif pos_side == "BOTH":
                detected_side = "long" if amt > 0 else "short"
            else:
                detected_side = "long" if amt > 0 else "short"

            pos["detected_side"] = detected_side
            logger.info(
                f"Posicion activa: amt={amt} positionSide={pos_side} "
                f"→ detected={detected_side} avgPrice={pos.get('avgPrice')}"
            )
            return pos
        return None

    # ─────────────────────── TRADING ─────────────────────────

    def set_leverage(self, symbol: str, leverage: int):
        for side in ("LONG", "SHORT"):
            try:
                self._post("/openApi/swap/v2/trade/leverage", {
                    "symbol": symbol,
                    "side": side,
                    "leverage": leverage,
                })
                logger.debug(f"Leverage {leverage}x configurado para {symbol} {side}")
            except BingXError as e:
                logger.warning(f"set_leverage {side}: {e}")

    def place_market_order(self, symbol: str, side: str, quantity: float,
                           position_side: str = "BOTH") -> dict:
        """
        FIX-3: position_side ahora se pasa explícitamente desde bot.py
        según el modo de la cuenta (One-Way=BOTH, Hedge=LONG/SHORT).
        """
        params = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         "MARKET",
            "quantity":     quantity,
        }
        data = self._post("/openApi/swap/v2/trade/order", params)
        order = data.get("data", {}).get("order", {})
        logger.info(f"Orden: {side} {quantity} {symbol} positionSide={position_side} orderId={order.get('orderId')}")
        return order

    def close_all_positions(self, symbol: str) -> dict:
        """
        FIX-2: Usa el positionSide correcto al cerrar.
        - Hedge mode LONG → SELL con positionSide=LONG
        - Hedge mode SHORT → BUY con positionSide=SHORT
        - One-Way mode → usa BOTH (comportamiento original)
        """
        pos = self.get_open_position(symbol)
        if pos is None:
            logger.info(f"Sin posicion abierta para {symbol}")
            return {}

        amt      = float(pos.get("positionAmt", 0) or 0)
        pos_side = str(pos.get("positionSide", "BOTH")).upper()
        detected = pos.get("detected_side", "long")

        if abs(amt) == 0:
            return {}

        quantity = abs(amt)

        if pos_side in ("LONG", "SHORT"):
            # Hedge mode: el positionSide debe coincidir con la posición a cerrar
            close_side      = "SELL" if pos_side == "LONG" else "BUY"
            close_pos_side  = pos_side  # LONG o SHORT
        else:
            # One-Way mode (BOTH): usar signo de amt
            close_side      = "SELL" if amt > 0 else "BUY"
            close_pos_side  = "BOTH"

        params = {
            "symbol":       symbol,
            "side":         close_side,
            "positionSide": close_pos_side,
            "type":         "MARKET",
            "quantity":     quantity,
        }
        data = self._post("/openApi/swap/v2/trade/order", params)
        order = data.get("data", {}).get("order", {})
        logger.info(
            f"Posicion cerrada: {close_side} {quantity} {symbol} "
            f"positionSide={close_pos_side} orderId={order.get('orderId')}"
        )
        return data

    def cancel_all_orders(self, symbol: str):
        try:
            self._post("/openApi/swap/v2/trade/cancelAllOpenOrders", {"symbol": symbol})
        except BingXError as e:
            logger.warning(f"cancel_all_orders: {e}")
