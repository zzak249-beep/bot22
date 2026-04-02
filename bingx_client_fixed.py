"""
Cliente BingX Mejorado - Correcciones para Error 109400
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
    """Excepción para errores de BingX"""
    pass


class BingXClient:
    """Cliente mejorado para BingX API con correcciones"""
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        demo: bool = False,
        telegram_token: str = "",
        telegram_chat: str = ""
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.demo = demo
        self.telegram_token = telegram_token
        self.telegram_chat = telegram_chat
        
        # Endpoints
        if demo:
            self.base_url = "https://open-api-vst.bingx.com"
        else:
            self.base_url = "https://open-api.bingx.com"
        
        self.session = requests.Session()
        self.session.headers.update({
            "X-BX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        })
    
    def _sign(self, params: Dict[str, Any]) -> str:
        """Generar firma HMAC"""
        query_string = urlencode(sorted(params.items()))
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        signed: bool = True
    ) -> Any:
        """Hacer request a la API"""
        if params is None:
            params = {}
        
        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['signature'] = self._sign(params)
        
        url = f"{self.base_url}{endpoint}"
        
        try:
            if method == "GET":
                response = self.session.get(url, params=params, timeout=10)
            elif method == "POST":
                response = self.session.post(url, params=params, timeout=10)
            elif method == "DELETE":
                response = self.session.delete(url, params=params, timeout=10)
            else:
                raise ValueError(f"Método HTTP no soportado: {method}")
            
            # Log de debugging
            logger.debug(f"{method} {endpoint} | Status: {response.status_code}")
            
            data = response.json()
            
            # Verificar errores
            if data.get('code') != 0:
                error_msg = f"API error {data.get('code')}: {data.get('msg', 'Unknown error')}"
                logger.error(f"{error_msg} | Endpoint: {endpoint} | Params: {params}")
                raise BingXError(error_msg)
            
            return data.get('data', data)
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {e}")
            raise BingXError(f"Network error: {e}")
        
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise BingXError(f"Error: {e}")
    
    def get_balance(self) -> float:
        """Obtener balance en USDT"""
        try:
            data = self._request("GET", "/openApi/swap/v2/user/balance")
            
            # Buscar balance de USDT
            if isinstance(data, dict):
                balance_info = data.get('balance', {})
                if isinstance(balance_info, dict):
                    return float(balance_info.get('balance', 0))
            
            # Si es una lista
            if isinstance(data, list):
                for item in data:
                    if item.get('asset') == 'USDT':
                        return float(item.get('balance', 0))
            
            logger.warning("No se encontró balance USDT en respuesta")
            return 0.0
        
        except Exception as e:
            logger.error(f"Error obteniendo balance: {e}")
            raise BingXError(f"Error obteniendo balance: {e}")
    
    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500
    ) -> List[Dict]:
        """Obtener velas"""
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000)
        }
        
        try:
            data = self._request("GET", "/openApi/swap/v3/quote/klines", params, signed=False)
            return data if isinstance(data, list) else []
        
        except Exception as e:
            logger.error(f"Error obteniendo klines {symbol}: {e}")
            return []
    
    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        """Obtener información del símbolo"""
        try:
            data = self._request("GET", "/openApi/swap/v2/quote/contracts", signed=False)
            
            if isinstance(data, list):
                for item in data:
                    if item.get('symbol') == symbol:
                        return {
                            'minQty': float(item.get('minTradeNum', 0.001)),
                            'qtyStep': float(item.get('quantityPrecision', 0.001)),
                            'pricePrecision': int(item.get('pricePrecision', 2))
                        }
            
            logger.warning(f"Símbolo {symbol} no encontrado")
            return {
                'minQty': 0.001,
                'qtyStep': 0.001,
                'pricePrecision': 2
            }
        
        except Exception as e:
            logger.error(f"Error obteniendo info de {symbol}: {e}")
            return None
    
    def get_positions(self, symbol: str) -> List[Dict]:
        """Obtener todas las posiciones"""
        params = {"symbol": symbol}
        
        try:
            data = self._request("GET", "/openApi/swap/v2/user/positions", params)
            return data if isinstance(data, list) else []
        
        except Exception as e:
            logger.error(f"Error obteniendo posiciones {symbol}: {e}")
            return []
    
    def get_open_position(self, symbol: str) -> Optional[Dict]:
        """Obtener posición abierta de un símbolo"""
        positions = self.get_positions(symbol)
        
        for pos in positions:
            amt = float(pos.get('positionAmt', 0))
            if amt != 0:
                # Detectar lado correctamente
                position_side = str(pos.get('positionSide', 'BOTH')).upper()
                
                if position_side == 'LONG':
                    detected_side = 'long'
                elif position_side == 'SHORT':
                    detected_side = 'short'
                else:
                    # One-way mode
                    detected_side = 'long' if amt > 0 else 'short'
                
                pos['detected_side'] = detected_side
                return pos
        
        return None
    
    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Configurar apalancamiento"""
        params = {
            "symbol": symbol,
            "leverage": leverage,
            "side": "BOTH"  # Configurar ambos lados
        }
        
        try:
            self._request("POST", "/openApi/swap/v2/trade/leverage", params)
            logger.info(f"{symbol}: Apalancamiento configurado a {leverage}x")
            return True
        
        except BingXError as e:
            # No es crítico si ya está configurado
            if "leverage" in str(e).lower():
                logger.debug(f"{symbol}: Apalancamiento ya configurado")
                return True
            logger.error(f"{symbol}: Error configurando leverage: {e}")
            return False
    
    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        position_side: Optional[str] = None
    ) -> Dict:
        """
        Abrir orden de mercado
        FIX: Parámetros simplificados para evitar error 109400
        """
        params = {
            "symbol": symbol,
            "side": side.upper(),  # BUY o SELL
            "type": "MARKET",
            "quantity": quantity,
        }
        
        # Solo agregar positionSide si se especifica y si es necesario
        # En algunas configuraciones de BingX, este parámetro causa error 109400
        # if position_side and position_side != "BOTH":
        #     params["positionSide"] = position_side
        
        try:
            logger.info(f"{symbol}: Orden MARKET {side} qty={quantity}")
            data = self._request("POST", "/openApi/swap/v2/trade/order", params)
            logger.info(f"{symbol}: Orden ejecutada exitosamente")
            return data
        
        except BingXError as e:
            logger.error(f"{symbol}: Error en orden: {e}")
            raise
    
    def close_position(self, symbol: str, side: str, quantity: float) -> bool:
        """
        Cerrar posición específica
        FIX: Usar orden simple sin positionSide
        """
        try:
            logger.info(f"{symbol}: Cerrando posición {side} qty={quantity}")
            
            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "MARKET",
                "quantity": quantity,
                "reduceOnly": True  # Importante: solo reducir posición
            }
            
            self._request("POST", "/openApi/swap/v2/trade/order", params)
            logger.info(f"{symbol}: Posición cerrada exitosamente")
            return True
        
        except BingXError as e:
            logger.error(f"{symbol}: Error cerrando posición: {e}")
            return False
    
    def close_all_positions(self, symbol: str) -> bool:
        """Cerrar todas las posiciones de un símbolo"""
        try:
            params = {"symbol": symbol}
            self._request("POST", "/openApi/swap/v2/trade/closeAllPositions", params)
            logger.info(f"{symbol}: Todas las posiciones cerradas")
            return True
        
        except BingXError as e:
            logger.error(f"{symbol}: Error cerrando todas las posiciones: {e}")
            return False
    
    def send_telegram(self, message: str):
        """Enviar notificación a Telegram"""
        if not self.telegram_token or not self.telegram_chat:
            return
        
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                "chat_id": self.telegram_chat,
                "text": message,
                "parse_mode": "HTML"
            }
            
            response = requests.post(url, json=data, timeout=5)
            
            if response.status_code != 200:
                logger.warning(f"Telegram error: {response.text}")
        
        except Exception as e:
            logger.debug(f"Error enviando Telegram: {e}")
