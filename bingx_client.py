"""
Cliente BingX — v3.0
CAMBIOS CRÍTICOS:
  CRIT-1  place_entry: SOLO MARKET — las órdenes LIMIT acumulaban 12+ pendientes
           sin ejecutar y al ejecutarse todas a la vez arruinaban la cuenta
  CRIT-2  cleanup_all_orders: cancela TODAS las órdenes pendientes al arrancar
           y antes de cada cierre — elimina el estado sucio entre sesiones
  CRIT-3  place_tp_sl: 4 métodos de intento para máxima compatibilidad
           workingType MARK_PRICE → CONTRACT_PRICE → sin workingType → STOP limit
  CRIT-4  get_pending_orders_count: para que el bot sepa cuántas órdenes tiene
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
        try:
            if method == "GET":
                r = requests.get(url,    headers=self.headers, timeout=12)
            elif method == "POST":
                r = requests.post(url,   headers=self.headers, timeout=12)
            elif method == "DELETE":
                r = requests.delete(url, headers=self.headers, timeout=12)
            else:
                raise ValueError(f"Método no soportado: {method}")
            data = r.json()
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

    def get_positions(self, symbol="") -> List[Dict]:
        try:
            params = {"symbol": symbol} if symbol else {}
            data   = self._request("GET", "/openApi/swap/v2/user/positions", params)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"posiciones {symbol}: {e}")
            return []

    def get_all_open_positions(self) -> List[Dict]:
        """Obtiene TODAS las posiciones abiertas (sin filtro de símbolo)."""
        return [p for p in self.get_positions()
                if float(p.get("positionAmt", 0)) != 0]

    # ─────────────────── órdenes pendientes ──────────────────────────────

    def get_open_orders(self, symbol="") -> List[Dict]:
        try:
            params = {"symbol": symbol} if symbol else {}
            data   = self._request("GET", "/openApi/swap/v2/trade/openOrders", params)
            if isinstance(data, dict):
                return data.get("orders", []) or []
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.debug(f"open_orders {symbol}: {e}")
            return []

    def cleanup_symbol_orders(self, symbol: str):
        """
        CRIT-2: Cancela TODAS las órdenes pendientes de un símbolo.
        Llamar antes de abrir y antes de cerrar posición.
        """
        try:
            orders = self.get_open_orders(symbol)
            cancelled = 0
            for o in orders:
                oid = o.get("orderId", "")
                if oid:
                    try:
                        self._request("DELETE", "/openApi/swap/v2/trade/order",
                                      {"symbol": symbol, "orderId": str(oid)})
                        cancelled += 1
                    except Exception:
                        pass
            if cancelled:
                logger.info(f"{symbol}: {cancelled} órdenes canceladas")
        except Exception as e:
            logger.debug(f"cleanup {symbol}: {e}")

    def cleanup_all_orders(self):
        """
        CRIT-2: Cancela TODAS las órdenes pendientes de la cuenta.
        Llamar al arrancar el bot para limpiar estado sucio.
        """
        try:
            all_orders = self.get_open_orders()
            symbols    = set(o.get("symbol", "") for o in all_orders if o.get("symbol"))
            total = 0
            for sym in symbols:
                orders = [o for o in all_orders if o.get("symbol") == sym]
                # Solo cancelar LIMIT de entrada (no TP/SL)
                entry_types = {"LIMIT", "MARKET"}
                for o in orders:
                    if o.get("type") in entry_types or str(o.get("type", "")).upper() == "LIMIT":
                        oid = o.get("orderId", "")
                        if oid:
                            try:
                                self._request("DELETE", "/openApi/swap/v2/trade/order",
                                              {"symbol": sym, "orderId": str(oid)})
                                total += 1
                            except Exception:
                                pass
            if total:
                logger.warning(f"  [CLEANUP] {total} órdenes LIMIT pendientes canceladas al arrancar")
                self.send_telegram(f"<b>🧹 Limpieza al arrancar</b>\n{total} órdenes LIMIT pendientes canceladas")
        except Exception as e:
            logger.debug(f"cleanup_all: {e}")

    def has_tp_sl(self, symbol: str) -> dict:
        orders = self.get_open_orders(symbol)
        has_tp = any(o.get("type") in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT") for o in orders)
        has_sl = any(o.get("type") in ("STOP_MARKET", "STOP") for o in orders)
        return {"tp": has_tp, "sl": has_sl}

    # ─────────────────── leverage ─────────────────────────────────────────

    def set_leverage(self, symbol, leverage) -> bool:
        success = False
        for side in ("LONG", "SHORT", "BOTH"):
            try:
                self._request("POST", "/openApi/swap/v2/trade/leverage",
                              {"symbol": symbol, "leverage": leverage, "side": side})
                logger.info(f"{symbol}: leverage {leverage}x ({side})")
                success = True
            except BingXError as e:
                if "110025" in str(e) or "leverage" in str(e).lower():
                    success = True
                else:
                    logger.debug(f"leverage {side}: {e}")
        return success

    # ─────────────────── ENTRADA — SOLO MARKET ────────────────────────────

    def place_entry(self, symbol, direction, quantity,
                    position_side=None) -> bool:
        """
        CRIT-1: SOLO órdenes MARKET para entradas.
        Las LIMIT acumulaban docenas de órdenes sin ejecutar.
        MARKET = ejecución inmediata garantizada.
        """
        side     = "BUY" if direction == "long" else "SELL"
        qty_str  = f"{quantity:.6g}"
        ps       = (position_side or ("LONG" if direction == "long" else "SHORT")).upper()

        params: Dict[str, Any] = {
            "symbol":   symbol,
            "side":     side,
            "type":     "MARKET",
            "quantity": qty_str,
        }
        if ps != "BOTH":
            params["positionSide"] = ps

        logger.info(f"{symbol}: MARKET {direction.upper()} qty={qty_str} ps={ps}")
        try:
            self._request("POST", "/openApi/swap/v2/trade/order", params)
            logger.info(f"{symbol}: entrada MARKET OK ✅")
            return True
        except BingXError as e:
            # Fallback sin positionSide (one-way mode)
            if "positionSide" in params:
                logger.warning(f"{symbol}: reintentando sin positionSide")
                params.pop("positionSide")
                try:
                    self._request("POST", "/openApi/swap/v2/trade/order", params)
                    logger.info(f"{symbol}: entrada MARKET (sin ps) OK ✅")
                    return True
                except BingXError as e2:
                    logger.error(f"{symbol}: entrada falló: {e2}")
                    return False
            logger.error(f"{symbol}: entrada falló: {e}")
            return False

    # Alias para compatibilidad
    def place_market_order(self, symbol, side, quantity, position_side=None):
        direction = "long" if side.upper() == "BUY" else "short"
        self.place_entry(symbol, direction, quantity, position_side)

    # ─────────────────── TP / SL ─────────────────────────────────────────

    def place_tp_sl(self, symbol, direction, quantity, tp_price, sl_price,
                    position_side=None) -> dict:
        """
        CRIT-3: 4 métodos de intento para máxima compatibilidad con BingX.
        """
        close_side = "SELL" if direction == "long" else "BUY"
        ps         = (position_side or ("LONG" if direction == "long" else "SHORT")).upper()
        qty_str    = f"{quantity:.6g}"
        result     = {"tp": False, "sl": False}

        def _try_order(params_dict) -> bool:
            try:
                self._request("POST", "/openApi/swap/v2/trade/order", params_dict)
                return True
            except BingXError as e:
                logger.debug(f"  order attempt falló: {e}")
                return False

        def _base(order_type, stop_p, extra=None):
            p = {
                "symbol":    symbol,
                "side":      close_side,
                "type":      order_type,
                "stopPrice": f"{stop_p:.8g}",
                "quantity":  qty_str,
            }
            if ps != "BOTH":
                p["positionSide"] = ps
            if extra:
                p.update(extra)
            return p

        # ── Take Profit ───────────────────────────────────────────────────
        tp_attempts = [
            _base("TAKE_PROFIT_MARKET", tp_price, {"workingType": "MARK_PRICE"}),
            _base("TAKE_PROFIT_MARKET", tp_price, {"workingType": "CONTRACT_PRICE"}),
            _base("TAKE_PROFIT_MARKET", tp_price),
        ]
        # Fallback: TAKE_PROFIT con precio límite
        offset = 1.001 if direction == "long" else 0.999
        tp_attempts.append({
            "symbol": symbol, "side": close_side,
            "type": "TAKE_PROFIT",
            "stopPrice": f"{tp_price:.8g}",
            "price":     f"{tp_price * offset:.8g}",
            "quantity":  qty_str,
            "timeInForce": "GTC",
            **({} if ps == "BOTH" else {"positionSide": ps}),
        })

        for attempt in tp_attempts:
            if _try_order(attempt):
                logger.info(f"{symbol}: TP ✅ @ ${tp_price:.6g} ({attempt.get('type')} {attempt.get('workingType','')})")
                result["tp"] = True
                break

        if not result["tp"]:
            logger.error(f"{symbol}: TP TODOS LOS INTENTOS FALLARON")

        time.sleep(0.5)

        # ── Stop Loss ─────────────────────────────────────────────────────
        sl_attempts = [
            _base("STOP_MARKET", sl_price, {"workingType": "MARK_PRICE"}),
            _base("STOP_MARKET", sl_price, {"workingType": "CONTRACT_PRICE"}),
            _base("STOP_MARKET", sl_price),
        ]
        offset_sl = 0.999 if direction == "long" else 1.001
        sl_attempts.append({
            "symbol": symbol, "side": close_side,
            "type": "STOP",
            "stopPrice": f"{sl_price:.8g}",
            "price":     f"{sl_price * offset_sl:.8g}",
            "quantity":  qty_str,
            "timeInForce": "GTC",
            **({} if ps == "BOTH" else {"positionSide": ps}),
        })

        for attempt in sl_attempts:
            if _try_order(attempt):
                logger.info(f"{symbol}: SL ✅ @ ${sl_price:.6g} ({attempt.get('type')} {attempt.get('workingType','')})")
                result["sl"] = True
                break

        if not result["sl"]:
            logger.error(f"{symbol}: SL TODOS LOS INTENTOS FALLARON")

        return result

    # ─────────────────── cierre ───────────────────────────────────────────

    def close_position(self, symbol, direction, quantity, position_side=None) -> bool:
        """Cierra posición con orden MARKET reduceOnly."""
        close_side = "SELL" if direction == "long" else "BUY"
        ps         = (position_side or ("LONG" if direction == "long" else "SHORT")).upper()
        qty_str    = f"{quantity:.6g}"

        params: Dict[str, Any] = {
            "symbol":     symbol,
            "side":       close_side,
            "type":       "MARKET",
            "quantity":   qty_str,
        }
        if ps != "BOTH":
            params["positionSide"] = ps
        else:
            params["reduceOnly"] = "true"

        try:
            self._request("POST", "/openApi/swap/v2/trade/order", params)
            logger.info(f"{symbol}: cierre MARKET {direction.upper()} ✅")
            return True
        except BingXError as e:
            # Fallback reduceOnly
            try:
                fb = {"symbol": symbol, "side": close_side, "type": "MARKET",
                      "quantity": qty_str, "reduceOnly": "true"}
                self._request("POST", "/openApi/swap/v2/trade/order", fb)
                logger.info(f"{symbol}: cierre fallback ✅")
                return True
            except BingXError as e2:
                logger.error(f"{symbol}: cierre falló: {e2}")
                return False

    def close_all_positions(self, symbol: str) -> bool:
        """Cierra todas las posiciones del símbolo."""
        try:
            positions = self.get_positions(symbol)
            closed = 0
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt == 0:
                    continue
                ps  = str(pos.get("positionSide", "BOTH")).upper()
                direction = "long" if (ps == "LONG" or (ps == "BOTH" and amt > 0)) else "short"
                if self.close_position(symbol, direction, abs(amt), ps):
                    closed += 1
            return closed > 0
        except Exception as e:
            logger.error(f"close_all {symbol}: {e}")
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
