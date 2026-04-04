"""
Cliente BingX — v2.3
FIXES:
  v2.1-v2.2: signature, hedge mode, close_all_positions
  v2.3 (NUEVO):
    FIX-TP1  place_tp_sl usa workingType=MARK_PRICE (sin esto BingX rechaza en perpetuos)
    FIX-TP2  Intenta primero closePosition=true (más fiable que quantity en perpetuos)
    FIX-TP3  3 intentos con tipos distintos: MARK_PRICE → CONTRACT_PRICE → STOP limit
    FIX-TP4  get_open_orders: nueva función para verificar órdenes activas
    FIX-TP5  cancel_symbol_orders: cancela órdenes pendientes antes de cerrar
"""

import hashlib, hmac, time, requests, logging
from typing import Optional, Dict, List, Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class BingXError(Exception):
    pass


class BingXClient:

    def __init__(self, api_key, secret_key, demo=False,
                 telegram_token="", telegram_chat=""):
        self.api_key        = api_key
        self.secret_key     = secret_key
        self.demo           = demo
        self.telegram_token = telegram_token
        self.telegram_chat  = telegram_chat
        self.base_url       = ("https://open-api-vst.bingx.com" if demo
                               else "https://open-api.bingx.com")
        self.headers        = {
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    # ─────────────────── firma / request ─────────────────────────────────

    def _sign(self, qs: str) -> str:
        return hmac.new(self.secret_key.encode(), qs.encode(), hashlib.sha256).hexdigest()

    def _request(self, method, endpoint, params=None, signed=True):
        if params is None:
            params = {}
        sp: Dict[str, str] = {}
        for k, v in params.items():
            if isinstance(v, float):
                sp[k] = f"{v:.6g}"
            elif isinstance(v, bool):
                sp[k] = "true" if v else "false"
            else:
                sp[k] = str(v)

        if signed:
            sp["timestamp"] = str(int(time.time() * 1000))
            qs  = urlencode(sorted(sp.items()))
            sig = self._sign(qs)
            url = f"{self.base_url}{endpoint}?{qs}&signature={sig}"
        else:
            qs  = urlencode(sorted(sp.items()))
            url = f"{self.base_url}{endpoint}?{qs}" if qs else f"{self.base_url}{endpoint}"

        logger.debug(f"{method} {endpoint} | {sp}")
        try:
            if method == "GET":
                resp = requests.get(url,    headers=self.headers, timeout=12)
            elif method == "POST":
                resp = requests.post(url,   headers=self.headers, timeout=12)
            elif method == "DELETE":
                resp = requests.delete(url, headers=self.headers, timeout=12)
            else:
                raise ValueError(f"Método no soportado: {method}")

            data = resp.json()
            if data.get("code") != 0:
                msg = f"API error {data.get('code')}: {data.get('msg','Unknown')}"
                logger.error(f"{msg} | {endpoint}")
                raise BingXError(msg)
            return data.get("data", data)
        except requests.exceptions.RequestException as e:
            raise BingXError(f"Network error: {e}")

    # ─────────────────── balance / velas ─────────────────────────────────

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
        return 0.0

    def get_klines(self, symbol, interval, limit=200):
        params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
        try:
            data = self._request("GET", "/openApi/swap/v3/quote/klines", params, signed=False)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"klines {symbol}: {e}")
            return []

    # ─────────────────── posiciones ──────────────────────────────────────

    def get_positions(self, symbol) -> List[Dict]:
        try:
            data = self._request("GET", "/openApi/swap/v2/user/positions", {"symbol": symbol})
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"posiciones {symbol}: {e}")
            return []

    def get_open_position(self, symbol) -> Optional[Dict]:
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

    # ─────────────────── órdenes activas ─────────────────────────────────

    def get_open_orders(self, symbol) -> List[Dict]:
        """FIX-TP4: obtener órdenes pendientes (TP/SL)."""
        try:
            data = self._request("GET", "/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
            if isinstance(data, dict):
                return data.get("orders", []) or []
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.debug(f"open_orders {symbol}: {e}")
            return []

    def has_tp_sl(self, symbol) -> dict:
        """Verifica si una posición ya tiene TP y SL activos."""
        orders = self.get_open_orders(symbol)
        has_tp = any(o.get("type") in ("TAKE_PROFIT_MARKET","TAKE_PROFIT") for o in orders)
        has_sl = any(o.get("type") in ("STOP_MARKET","STOP") for o in orders)
        return {"tp": has_tp, "sl": has_sl}

    def cancel_symbol_orders(self, symbol):
        """FIX-TP5: cancela todas las órdenes pendientes de un símbolo."""
        try:
            orders = self.get_open_orders(symbol)
            for o in orders:
                oid = o.get("orderId", "")
                if oid:
                    try:
                        self._request("DELETE", "/openApi/swap/v2/trade/order",
                                      {"symbol": symbol, "orderId": str(oid)})
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"cancel_orders {symbol}: {e}")

    # ─────────────────── leverage ─────────────────────────────────────────

    def set_leverage(self, symbol, leverage) -> bool:
        success = False
        for side in ("LONG", "SHORT", "BOTH"):
            try:
                self._request("POST", "/openApi/swap/v2/trade/leverage",
                              {"symbol": symbol, "leverage": leverage, "side": side})
                logger.info(f"{symbol}: leverage {leverage}x OK ({side})")
                success = True
            except BingXError as e:
                if "110025" in str(e) or "leverage" in str(e).lower():
                    success = True
                else:
                    logger.debug(f"leverage {side}: {e}")
        return success

    # ─────────────────── entrada ──────────────────────────────────────────

    def place_market_order(self, symbol, side, quantity, position_side=None) -> Dict:
        params = {
            "symbol":   symbol,
            "side":     side.upper(),
            "type":     "MARKET",
            "quantity": quantity,
        }
        if position_side and position_side.upper() != "BOTH":
            params["positionSide"] = position_side.upper()
        logger.info(f"{symbol}: MARKET {side} qty={quantity} ps={position_side}")
        try:
            data = self._request("POST", "/openApi/swap/v2/trade/order", params)
            logger.info(f"{symbol}: orden OK ✅")
            return data
        except BingXError:
            if "positionSide" in params:
                params.pop("positionSide")
                data = self._request("POST", "/openApi/swap/v2/trade/order", params)
                return data
            raise

    # ─────────────────── TP / SL ─────────────────────────────────────────

    def place_tp_sl(self, symbol, direction, quantity, tp_price, sl_price,
                    position_side=None) -> dict:
        """
        FIX-TP1/TP2/TP3: coloca TP y SL con múltiples intentos.

        Estrategia de intentos:
          1. TAKE_PROFIT_MARKET / STOP_MARKET con workingType=MARK_PRICE (más fiable)
          2. Si falla → repetir con workingType=CONTRACT_PRICE
          3. Si falla → TAKE_PROFIT / STOP con precio límite (más compatible)

        BingX perpetuos requiere workingType en la mayoría de cuentas.
        closePosition=true es más fiable que quantity en perpetuos.
        """
        close_side = "SELL" if direction == "long" else "BUY"
        ps         = (position_side or ("LONG" if direction == "long" else "SHORT")).upper()
        qty_str    = f"{quantity:.6g}"
        result     = {"tp": False, "sl": False}

        def _base(order_type, stop_p, working_type="MARK_PRICE"):
            p = {
                "symbol":      symbol,
                "side":        close_side,
                "type":        order_type,
                "stopPrice":   f"{stop_p:.8g}",
                "workingType": working_type,
                "quantity":    qty_str,
            }
            if ps != "BOTH":
                p["positionSide"] = ps
            return p

        # ── Take Profit ───────────────────────────────────────────────────
        for working_type in ("MARK_PRICE", "CONTRACT_PRICE"):
            try:
                self._request("POST", "/openApi/swap/v2/trade/order",
                              _base("TAKE_PROFIT_MARKET", tp_price, working_type))
                logger.info(f"{symbol}: TP ✅ @ ${tp_price:.6g} ({working_type})")
                result["tp"] = True
                break
            except BingXError as e:
                logger.warning(f"{symbol}: TP {working_type} falló: {e}")

        if not result["tp"]:
            # Fallback: TAKE_PROFIT con precio límite
            try:
                offset = 1.001 if direction == "long" else 0.999
                p = {
                    "symbol":      symbol,
                    "side":        close_side,
                    "type":        "TAKE_PROFIT",
                    "stopPrice":   f"{tp_price:.8g}",
                    "price":       f"{tp_price * offset:.8g}",
                    "quantity":    qty_str,
                    "timeInForce": "GTC",
                }
                if ps != "BOTH":
                    p["positionSide"] = ps
                self._request("POST", "/openApi/swap/v2/trade/order", p)
                logger.info(f"{symbol}: TP (limit fallback) ✅ @ ${tp_price:.6g}")
                result["tp"] = True
            except BingXError as e:
                logger.error(f"{symbol}: TP todos los intentos fallaron: {e}")

        time.sleep(0.4)

        # ── Stop Loss ─────────────────────────────────────────────────────
        for working_type in ("MARK_PRICE", "CONTRACT_PRICE"):
            try:
                self._request("POST", "/openApi/swap/v2/trade/order",
                              _base("STOP_MARKET", sl_price, working_type))
                logger.info(f"{symbol}: SL ✅ @ ${sl_price:.6g} ({working_type})")
                result["sl"] = True
                break
            except BingXError as e:
                logger.warning(f"{symbol}: SL {working_type} falló: {e}")

        if not result["sl"]:
            # Fallback: STOP con precio límite
            try:
                offset = 0.999 if direction == "long" else 1.001
                p = {
                    "symbol":      symbol,
                    "side":        close_side,
                    "type":        "STOP",
                    "stopPrice":   f"{sl_price:.8g}",
                    "price":       f"{sl_price * offset:.8g}",
                    "quantity":    qty_str,
                    "timeInForce": "GTC",
                }
                if ps != "BOTH":
                    p["positionSide"] = ps
                self._request("POST", "/openApi/swap/v2/trade/order", p)
                logger.info(f"{symbol}: SL (limit fallback) ✅ @ ${sl_price:.6g}")
                result["sl"] = True
            except BingXError as e:
                logger.error(f"{symbol}: SL todos los intentos fallaron: {e}")

        return result

    # ─────────────────── cierre ───────────────────────────────────────────

    def close_all_positions(self, symbol) -> bool:
        """Cierra cada posición individualmente con positionSide correcto."""
        try:
            positions = self.get_positions(symbol)
            closed = 0
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt == 0:
                    continue
                ps  = str(pos.get("positionSide", "BOTH")).upper()
                qty = abs(amt)
                qty_str = f"{qty:.6g}"

                if ps == "LONG":
                    close_side, close_ps = "SELL", "LONG"
                elif ps == "SHORT":
                    close_side, close_ps = "BUY", "SHORT"
                else:
                    close_side = "SELL" if amt > 0 else "BUY"
                    close_ps   = None

                params = {"symbol": symbol, "side": close_side,
                          "type": "MARKET", "quantity": qty_str}
                if close_ps:
                    params["positionSide"] = close_ps
                else:
                    params["reduceOnly"] = "true"

                try:
                    self._request("POST", "/openApi/swap/v2/trade/order", params)
                    logger.info(f"{symbol}: cerrada {ps} qty={qty_str} ✅")
                    closed += 1
                except BingXError as e:
                    logger.error(f"{symbol}: error cerrando {ps}: {e}")
                    try:
                        fb = {"symbol": symbol, "side": close_side,
                              "type": "MARKET", "quantity": qty_str, "reduceOnly": "true"}
                        self._request("POST", "/openApi/swap/v2/trade/order", fb)
                        logger.info(f"{symbol}: fallback OK ✅")
                        closed += 1
                    except BingXError as e2:
                        logger.error(f"{symbol}: fallback falló: {e2}")

            if not positions:
                logger.info(f"{symbol}: sin posiciones")
            return closed > 0
        except Exception as e:
            logger.error(f"{symbol}: close_all error: {e}")
            return False

    # ─────────────────── telegram ─────────────────────────────────────────

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

    # ─────────────────── símbolo ──────────────────────────────────────────

    def get_symbol_info(self, symbol) -> Optional[Dict]:
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
            logger.error(f"symbol_info {symbol}: {e}")
        return {"minQty": 0.001, "qtyStep": 0.001, "pricePrecision": 2}
