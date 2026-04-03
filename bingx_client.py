"""
Cliente BingX — v2.2
FIXES:
  FIX-A  close_all_positions: cierra posición a posición con positionSide correcto
  FIX-B  set_leverage: llama para LONG, SHORT y BOTH por separado (hedge mode)
  FIX-C  place_market_order: positionSide siempre incluido en hedge mode
  FIX-D  _request (CRÍTICO — error 100001): construye URL manualmente igual
         que wyckoff_bot.py que SÍ funciona. Antes params=params hacía que
         requests recodificara floats/bools diferente → signature mismatch.
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
        self.headers = {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    # ─────────────────── firma / request ─────────────────────────────────────

    def _sign(self, qs: str) -> str:
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
        """
        FIX-D: URL construida manualmente — mismo patrón que wyckoff_bot.py.

        Problema anterior: requests.post(url, params=dict) recodifica valores
        (float 0.0044 → '0.0044' pero a veces con precisión extra, bool True →
        'True' en lugar de 'true') creando un query string distinto al que
        se firmó → error 100001 signature mismatch.

        Solución: convertir todos los valores a str explícitamente, construir
        el query string una sola vez, firmarlo, y pasar la URL completa a
        requests sin ningún parámetro extra.
        """
        if params is None:
            params = {}

        # Todos los valores a string antes de firmar
        str_params: Dict[str, str] = {}
        for k, v in params.items():
            if isinstance(v, float):
                str_params[k] = f"{v:.6g}"      # evita notación científica
            elif isinstance(v, bool):
                str_params[k] = "true" if v else "false"
            else:
                str_params[k] = str(v)

        if signed:
            str_params["timestamp"] = str(int(time.time() * 1000))
            qs  = urlencode(sorted(str_params.items()))
            sig = self._sign(qs)
            url = f"{self.base_url}{endpoint}?{qs}&signature={sig}"
        else:
            qs  = urlencode(sorted(str_params.items()))
            url = f"{self.base_url}{endpoint}?{qs}" if qs else f"{self.base_url}{endpoint}"

        logger.debug(f"{method} {endpoint} | {str_params}")

        try:
            if method == "GET":
                resp = requests.get(url, headers=self.headers, timeout=10)
            elif method == "POST":
                resp = requests.post(url, headers=self.headers, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(url, headers=self.headers, timeout=10)
            else:
                raise ValueError(f"Método no soportado: {method}")

            data = resp.json()

            if data.get("code") != 0:
                msg = f"API error {data.get('code')}: {data.get('msg', 'Unknown')}"
                logger.error(f"{msg} | {endpoint} | {str_params}")
                raise BingXError(msg)

            return data.get("data", data)

        except requests.exceptions.RequestException as e:
            raise BingXError(f"Network error: {e}")

    # ─────────────────── balance / velas ─────────────────────────────────────

    def get_balance(self) -> float:
        data = self._request("GET", "/openApi/swap/v2/user/balance")
        if isinstance(data, dict):
            bal = data.get("balance", {})
            if isinstance(bal, dict):
                return float(bal.get("balance", 0))
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

    # ─────────────────── posiciones ──────────────────────────────────────────

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

    # ─────────────────── leverage ─────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """FIX-B: hedge mode requiere LONG y SHORT por separado."""
        success = False
        for side in ("LONG", "SHORT", "BOTH"):
            try:
                self._request(
                    "POST",
                    "/openApi/swap/v2/trade/leverage",
                    {"symbol": symbol, "leverage": leverage, "side": side},
                )
                logger.info(f"{symbol}: leverage {leverage}x OK (side={side})")
                success = True
            except BingXError as e:
                err = str(e)
                if "110025" in err or "leverage" in err.lower():
                    success = True
                else:
                    logger.debug(f"leverage side={side}: {e}")
        return success

    # ─────────────────── órdenes ──────────────────────────────────────────────

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        position_side: Optional[str] = None,
    ) -> Dict:
        """FIX-C + FIX-D: positionSide correcto, cantidad como string seguro."""
        params: Dict[str, Any] = {
            "symbol":   symbol,
            "side":     side.upper(),
            "type":     "MARKET",
            "quantity": quantity,
        }
        if position_side and position_side.upper() != "BOTH":
            params["positionSide"] = position_side.upper()

        logger.info(f"{symbol}: MARKET {side} qty={quantity} posSide={position_side}")
        try:
            data = self._request("POST", "/openApi/swap/v2/trade/order", params)
            logger.info(f"{symbol}: orden OK ✅")
            return data
        except BingXError:
            if "positionSide" in params:
                logger.warning(f"{symbol}: reintentando sin positionSide…")
                params.pop("positionSide")
                data = self._request("POST", "/openApi/swap/v2/trade/order", params)
                logger.info(f"{symbol}: orden OK (sin positionSide) ✅")
                return data
            raise

    def close_all_positions(self, symbol: str) -> bool:
        """
        FIX-A: cierra cada posición individualmente con positionSide correcto.
        El endpoint /closeAllPositions da error 109400 en hedge mode.
        """
        try:
            positions = self.get_positions(symbol)
            closed = 0

            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt == 0:
                    continue

                ps  = str(pos.get("positionSide", "BOTH")).upper()
                qty = abs(amt)

                if ps == "LONG":
                    close_side, close_ps = "SELL", "LONG"
                elif ps == "SHORT":
                    close_side, close_ps = "BUY", "SHORT"
                else:
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
                    logger.info(f"{symbol}: cerrada {ps} qty={qty} ✅")
                    closed += 1
                except BingXError as e:
                    logger.error(f"{symbol}: error cerrando {ps}: {e}")
                    # Último recurso
                    try:
                        fallback: Dict[str, Any] = {
                            "symbol":     symbol,
                            "side":       close_side,
                            "type":       "MARKET",
                            "quantity":   qty,
                            "reduceOnly": "true",
                        }
                        self._request("POST", "/openApi/swap/v2/trade/order", fallback)
                        logger.info(f"{symbol}: fallback OK ✅")
                        closed += 1
                    except BingXError as e2:
                        logger.error(f"{symbol}: fallback falló: {e2}")

            if not positions:
                logger.info(f"{symbol}: sin posiciones abiertas")
            return closed > 0

        except Exception as e:
            logger.error(f"{symbol}: close_all_positions error: {e}")
            return False


    # ─────────────────── TP / SL ─────────────────────────────────────────────

    def place_tp_sl(self, symbol, direction, quantity, tp_price, sl_price, position_side=None):
        """
        Coloca TP y SL tras abrir posición.
        direction: "long" o "short"
        Retorna {"tp": bool, "sl": bool}
        """
        close_side = "SELL" if direction == "long" else "BUY"
        ps = (position_side or ("LONG" if direction == "long" else "SHORT")).upper()
        qty_str = f"{quantity:.6g}"
        result = {"tp": False, "sl": False}
        # TP
        tp_p = {"symbol": symbol, "side": close_side, "type": "TAKE_PROFIT_MARKET",
                "quantity": qty_str, "stopPrice": f"{tp_price:.8g}"}
        if ps != "BOTH":
            tp_p["positionSide"] = ps
        try:
            self._request("POST", "/openApi/swap/v2/trade/order", tp_p)
            logger.info(f"{symbol}: TP OK @ ${tp_price:.6g}")
            result["tp"] = True
        except BingXError as e:
            logger.error(f"{symbol}: TP falló: {e}")
        import time as _t; _t.sleep(0.3)
        # SL
        sl_p = {"symbol": symbol, "side": close_side, "type": "STOP_MARKET",
                "quantity": qty_str, "stopPrice": f"{sl_price:.8g}"}
        if ps != "BOTH":
            sl_p["positionSide"] = ps
        try:
            self._request("POST", "/openApi/swap/v2/trade/order", sl_p)
            logger.info(f"{symbol}: SL OK @ ${sl_price:.6g}")
            result["sl"] = True
        except BingXError as e:
            logger.warning(f"{symbol}: SL STOP_MARKET falló, reintentando STOP: {e}")
            offset = 0.999 if direction == "long" else 1.001
            sl_p2 = {"symbol": symbol, "side": close_side, "type": "STOP",
                     "quantity": qty_str, "stopPrice": f"{sl_price:.8g}",
                     "price": f"{sl_price*offset:.8g}", "timeInForce": "GTC"}
            if ps != "BOTH":
                sl_p2["positionSide"] = ps
            try:
                self._request("POST", "/openApi/swap/v2/trade/order", sl_p2)
                logger.info(f"{symbol}: SL(STOP) OK @ ${sl_price:.6g}")
                result["sl"] = True
            except BingXError as e2:
                logger.error(f"{symbol}: SL también falló: {e2}")
        return result

    # ─────────────────── telegram ─────────────────────────────────────────────

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
            logger.debug(f"Telegram: {e}")

    # ─────────────────── símbolo ──────────────────────────────────────────────

    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        try:
            data = self._request("GET", "/openApi/swap/v2/quote/contracts", signed=False)
            if isinstance(data, list):
                for item in data:
                    if item.get("symbol") == symbol:
                        return {
                            "minQty":         float(item.get("minTradeNum", 0.001)),
                            "qtyStep":        float(item.get("quantityPrecision", 0.001)),
                            "pricePrecision": int(item.get("pricePrecision", 2)),
                        }
        except Exception as e:
            logger.error(f"get_symbol_info {symbol}: {e}")
        return {"minQty": 0.001, "qtyStep": 0.001, "pricePrecision": 2}
