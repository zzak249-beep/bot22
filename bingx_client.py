"""
Cliente BingX — v2.1
FIXES:
  FIX-A  close_all_positions: cierra posición a posición con positionSide correcto
         El endpoint /closeAllPositions da 109400 en hedge mode — sustituido
         por órdenes individuales SELL/BUY con positionSide=LONG/SHORT
  FIX-B  set_leverage: en hedge mode hay que llamarlo para LONG y SHORT por separado
         "side": "BOTH" solo funciona en one-way mode
  FIX-C  place_market_order: positionSide siempre incluido (era comentado)
         Sin positionSide BingX no sabe qué lado abrir en hedge mode → 109400
"""

import hashlib
import hmac
import time
import requests
import logging
from typing import Optional, Dict, List, Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class BingXError(Exception):
    pass


class BingXClient:

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        demo: bool = False,
        telegram_token: str = "",
        telegram_chat: str = "",
    ):
        self.api_key        = api_key
        self.secret_key     = secret_key
        self.demo           = demo
        self.telegram_token = telegram_token
        self.telegram_chat  = telegram_chat

        self.base_url = (
            "https://open-api-vst.bingx.com" if demo else "https://open-api.bingx.com"
        )

        self.session = requests.Session()
        self.session.headers.update({
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json",
        })

    # ─────────────────── firma / request ────────────────────────────────────

    def _sign(self, params: Dict[str, Any]) -> str:
        qs = urlencode(sorted(params.items()))
        return hmac.new(
            self.secret_key.encode("utf-8"),
            qs.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        signed: bool = True,
    ) -> Any:
        if params is None:
            params = {}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign(params)

        url = f"{self.base_url}{endpoint}"

        try:
            if method == "GET":
                resp = self.session.get(url, params=params, timeout=10)
            elif method == "POST":
                resp = self.session.post(url, params=params, timeout=10)
            elif method == "DELETE":
                resp = self.session.delete(url, params=params, timeout=10)
            else:
                raise ValueError(f"Método no soportado: {method}")

            data = resp.json()
            logger.debug(f"{method} {endpoint} → code:{data.get('code')}")

            if data.get("code") != 0:
                msg = f"API error {data.get('code')}: {data.get('msg', 'Unknown error')}"
                logger.error(f"{msg} | endpoint:{endpoint} | params:{params}")
                raise BingXError(msg)

            return data.get("data", data)

        except requests.exceptions.RequestException as e:
            raise BingXError(f"Network error: {e}")

    # ─────────────────── balance / velas ────────────────────────────────────

    def get_balance(self) -> float:
        data = self._request("GET", "/openApi/swap/v2/user/balance")
        if isinstance(data, dict):
            balance_info = data.get("balance", {})
            if isinstance(balance_info, dict):
                return float(balance_info.get("balance", 0))
        if isinstance(data, list):
            for item in data:
                if item.get("asset") == "USDT":
                    return float(item.get("balance", 0))
        logger.warning("No se encontró balance USDT")
        return 0.0

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> List[Dict]:
        params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
        try:
            data = self._request("GET", "/openApi/swap/v3/quote/klines", params, signed=False)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error klines {symbol}: {e}")
            return []

    # ─────────────────── posiciones ─────────────────────────────────────────

    def get_positions(self, symbol: str) -> List[Dict]:
        try:
            data = self._request("GET", "/openApi/swap/v2/user/positions", {"symbol": symbol})
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error posiciones {symbol}: {e}")
            return []

    def get_open_position(self, symbol: str) -> Optional[Dict]:
        for pos in self.get_positions(symbol):
            amt = float(pos.get("positionAmt", 0))
            if amt != 0:
                ps = str(pos.get("positionSide", "BOTH")).upper()
                if ps == "LONG":
                    pos["detected_side"] = "long"
                elif ps == "SHORT":
                    pos["detected_side"] = "short"
                else:
                    pos["detected_side"] = "long" if amt > 0 else "short"
                return pos
        return None

    # ─────────────────── leverage ────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        FIX-B: En hedge mode hay que configurar leverage para LONG y SHORT
        por separado. "side": "BOTH" solo funciona en one-way.
        Intentamos los tres por si acaso.
        """
        success = False
        for side in ("LONG", "SHORT", "BOTH"):
            try:
                self._request(
                    "POST",
                    "/openApi/swap/v2/trade/leverage",
                    {"symbol": symbol, "leverage": leverage, "side": side},
                )
                logger.info(f"{symbol}: leverage {leverage}x configurado (side={side})")
                success = True
            except BingXError as e:
                # Ignorar errores de "ya configurado" o side no válido
                if "leverage" in str(e).lower() or "110025" in str(e):
                    success = True
                else:
                    logger.debug(f"set_leverage side={side}: {e}")
        return success

    # ─────────────────── órdenes ─────────────────────────────────────────────

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        position_side: Optional[str] = None,
    ) -> Dict:
        """
        FIX-C: positionSide siempre incluido en hedge mode.
        Sin él BingX no sabe qué lado abrir → error 109400.
        """
        params: Dict[str, Any] = {
            "symbol":   symbol,
            "side":     side.upper(),   # BUY | SELL
            "type":     "MARKET",
            "quantity": quantity,
        }

        # En hedge mode positionSide es obligatorio
        if position_side and position_side.upper() != "BOTH":
            params["positionSide"] = position_side.upper()

        logger.info(f"{symbol}: orden MARKET {side} qty={quantity} posSide={position_side}")
        try:
            data = self._request("POST", "/openApi/swap/v2/trade/order", params)
            logger.info(f"{symbol}: orden ejecutada OK")
            return data
        except BingXError:
            # Si falla con positionSide, intentar sin él (one-way)
            if position_side and "positionSide" in params:
                logger.warning(f"{symbol}: reintentando sin positionSide...")
                params.pop("positionSide")
                data = self._request("POST", "/openApi/swap/v2/trade/order", params)
                logger.info(f"{symbol}: orden ejecutada OK (sin positionSide)")
                return data
            raise

    def close_all_positions(self, symbol: str) -> bool:
        """
        FIX-A (crítico): El endpoint /closeAllPositions da error 109400 en
        hedge mode porque necesita positionSide por posición.

        Solución: obtener posiciones abiertas y cerrar cada una individualmente
        con la orden correcta:
          - LONG → side=SELL, positionSide=LONG
          - SHORT → side=BUY, positionSide=SHORT
          - BOTH (one-way) → lado contrario + reduceOnly=true
        """
        try:
            positions = self.get_positions(symbol)
            closed = 0

            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt == 0:
                    continue

                ps   = str(pos.get("positionSide", "BOTH")).upper()
                qty  = abs(amt)

                if ps == "LONG":
                    close_side = "SELL"
                    close_ps   = "LONG"
                elif ps == "SHORT":
                    close_side = "BUY"
                    close_ps   = "SHORT"
                else:
                    # One-way mode
                    close_side = "SELL" if amt > 0 else "BUY"
                    close_ps   = None

                params: Dict[str, Any] = {
                    "symbol":   symbol,
                    "side":     close_side,
                    "type":     "MARKET",
                    "quantity": qty,
                }

                if close_ps:
                    params["positionSide"] = close_ps
                else:
                    params["reduceOnly"] = "true"

                try:
                    self._request("POST", "/openApi/swap/v2/trade/order", params)
                    logger.info(f"{symbol}: cerrada posición {ps} qty={qty} ✅")
                    closed += 1
                except BingXError as e:
                    logger.error(f"{symbol}: error cerrando {ps}: {e}")
                    # Último recurso: reduceOnly sin positionSide
                    try:
                        fallback: Dict[str, Any] = {
                            "symbol":     symbol,
                            "side":       close_side,
                            "type":       "MARKET",
                            "quantity":   qty,
                            "reduceOnly": "true",
                        }
                        self._request("POST", "/openApi/swap/v2/trade/order", fallback)
                        logger.info(f"{symbol}: cerrada con fallback reduceOnly ✅")
                        closed += 1
                    except BingXError as e2:
                        logger.error(f"{symbol}: fallback también falló: {e2}")

            if closed == 0 and not positions:
                logger.info(f"{symbol}: sin posiciones abiertas que cerrar")
            return closed > 0

        except Exception as e:
            logger.error(f"{symbol}: close_all_positions error inesperado: {e}")
            return False

    # ─────────────────── telegram ────────────────────────────────────────────

    def send_telegram(self, message: str):
        if not self.telegram_token or not self.telegram_chat:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat, "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception as e:
            logger.debug(f"Telegram error: {e}")

    # ─────────────────── símbolo ────────────────────────────────────────────

    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        try:
            data = self._request("GET", "/openApi/swap/v2/quote/contracts", signed=False)
            if isinstance(data, list):
                for item in data:
                    if item.get("symbol") == symbol:
                        return {
                            "minQty":          float(item.get("minTradeNum", 0.001)),
                            "qtyStep":         float(item.get("quantityPrecision", 0.001)),
                            "pricePrecision":  int(item.get("pricePrecision", 2)),
                        }
        except Exception as e:
            logger.error(f"get_symbol_info {symbol}: {e}")
        return {"minQty": 0.001, "qtyStep": 0.001, "pricePrecision": 2}
