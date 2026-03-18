"""
Cliente BingX optimizado con obtención dinámica de todas las monedas
"""
import requests
import hmac
import hashlib
import time
import logging
from urllib.parse import urlencode
from typing import Dict, List, Optional
from config import Config

logger = logging.getLogger(__name__)


class BingXClient:
    """Cliente optimizado para BingX API"""
    
    def __init__(self):
        self.api_key = Config.BINGX_API_KEY
        self.api_secret = Config.BINGX_API_SECRET
        self.base_url = Config.BASE_URL
        self.session = requests.Session()
        self.session.headers.update({'X-BX-APIKEY': self.api_key})
        
        # Cache para símbolos
        self.all_symbols_cache = None
        self.cache_timestamp = 0
        self.cache_ttl = 300  # 5 minutos
    
    def _sign_request(self, params: dict) -> str:
        """Firmar request"""
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def get_all_symbols(self, force_refresh: bool = False) -> List[Dict]:
        """
        Obtener TODAS las monedas disponibles en BingX
        
        Returns:
            Lista de símbolos con información detallada
        """
        # Usar cache si está disponible
        current_time = time.time()
        if not force_refresh and self.all_symbols_cache and (current_time - self.cache_timestamp) < self.cache_ttl:
            return self.all_symbols_cache
        
        try:
            url = f"{self.base_url}/openApi/swap/v2/quote/contracts"
            response = self.session.get(url, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    symbols_data = data.get('data', [])
                    
                    # Filtrar y enriquecer
                    filtered_symbols = []
                    for symbol_info in symbols_data:
                        symbol = symbol_info.get('symbol', '')
                        
                        # Solo USDT perpetuos
                        if not symbol.endswith('-USDT'):
                            continue
                        
                        filtered_symbols.append({
                            'symbol': symbol,
                            'base_asset': symbol.replace('-USDT', ''),
                            'size': float(symbol_info.get('size', 0)),
                            'min_qty': float(symbol_info.get('minQty', 0)),
                            'max_qty': float(symbol_info.get('maxQty', 0)),
                            'status': symbol_info.get('status', '')
                        })
                    
                    # Actualizar cache
                    self.all_symbols_cache = filtered_symbols
                    self.cache_timestamp = current_time
                    
                    logger.info(f"✅ Símbolos obtenidos: {len(filtered_symbols)} pares USDT")
                    return filtered_symbols
            
            logger.warning("⚠️ No se pudieron obtener símbolos")
            return []
        
        except Exception as e:
            logger.error(f"❌ Error obteniendo símbolos: {e}")
            return []
    
    def get_top_symbols_by_volume(self, limit: int = 50) -> List[str]:
        """
        Obtener top símbolos por volumen 24h
        
        Args:
            limit: Número máximo de símbolos
        
        Returns:
            Lista de símbolos ordenados por volumen
        """
        try:
            url = f"{self.base_url}/openApi/swap/v2/quote/ticker"
            response = self.session.get(url, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    tickers = data.get('data', [])
                    
                    # Procesar y filtrar
                    processed = []
                    for ticker in tickers:
                        symbol = ticker.get('symbol', '')
                        
                        if not symbol.endswith('-USDT'):
                            continue
                        
                        try:
                            volume = float(ticker.get('volume', 0))
                            last_price = float(ticker.get('lastPrice', 0))
                            volume_usd = volume * last_price
                            
                            # Filtros
                            if volume_usd < Config.MIN_VOLUME_24H:
                                continue
                            
                            if last_price < Config.MIN_PRICE:
                                continue
                            
                            processed.append({
                                'symbol': symbol,
                                'volume_usd': volume_usd,
                                'price': last_price,
                                'change_pct': float(ticker.get('priceChangePercent', 0))
                            })
                        
                        except (ValueError, TypeError):
                            continue
                    
                    # Ordenar por volumen
                    processed.sort(key=lambda x: x['volume_usd'], reverse=True)
                    
                    # Limitar
                    top_symbols = [item['symbol'] for item in processed[:limit]]
                    
                    logger.info(f"📊 Top {len(top_symbols)} símbolos por volumen obtenidos")
                    
                    # Log de los top 10
                    for i, item in enumerate(processed[:10], 1):
                        logger.info(f"   {i:2d}. {item['symbol']:15s} | ${item['volume_usd']:>15,.0f} | {item['change_pct']:>+6.2f}%")
                    
                    return top_symbols
            
            return []
        
        except Exception as e:
            logger.error(f"❌ Error obteniendo top símbolos: {e}")
            return []
    
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Obtener ticker de un símbolo"""
        try:
            url = f"{self.base_url}/openApi/swap/v2/quote/ticker"
            params = {'symbol': symbol}
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0 and data.get('data'):
                    ticker = data['data']
                    return {
                        'symbol': symbol,
                        'price': float(ticker.get('lastPrice', 0)),
                        'change': float(ticker.get('priceChangePercent', 0)),
                        'volume': float(ticker.get('volume', 0)),
                        'high': float(ticker.get('highPrice', 0)),
                        'low': float(ticker.get('lowPrice', 0)),
                        'open': float(ticker.get('openPrice', 0))
                    }
            return None
        
        except Exception as e:
            logger.debug(f"Error ticker {symbol}: {e}")
            return None
    
    def get_klines(self, symbol: str, interval: str = '5m', limit: int = 100) -> List[Dict]:
        """
        Obtener datos históricos (velas)
        
        Args:
            symbol: Par a consultar
            interval: Timeframe (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Número de velas
        
        Returns:
            Lista de velas con OHLCV
        """
        try:
            url = f"{self.base_url}/openApi/swap/v2/quote/klines"
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }
            
            response = self.session.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0 and data.get('data'):
                    klines = []
                    for k in data['data']:
                        klines.append({
                            'timestamp': int(k[0]),
                            'open': float(k[1]),
                            'high': float(k[2]),
                            'low': float(k[3]),
                            'close': float(k[4]),
                            'volume': float(k[5])
                        })
                    return klines
            
            return []
        
        except Exception as e:
            logger.debug(f"Error klines {symbol}: {e}")
            return []
    
    def get_balance(self) -> Dict:
        """Obtener balance de la cuenta"""
        try:
            timestamp = int(time.time() * 1000)
            params = {'timestamp': timestamp}
            params['signature'] = self._sign_request(params)
            
            url = f"{self.base_url}/openApi/swap/v2/user/balance"
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    balance_data = data.get('data', {}).get('balance', {})
                    return {
                        'total': float(balance_data.get('balance', 0)),
                        'available': float(balance_data.get('availableMargin', 0)),
                        'used': float(balance_data.get('usedMargin', 0)),
                        'unrealized_pnl': float(balance_data.get('unrealizedProfit', 0))
                    }
            
            return {'total': 0, 'available': 0, 'used': 0, 'unrealized_pnl': 0}
        
        except Exception as e:
            logger.error(f"❌ Error balance: {e}")
            return {'total': 0, 'available': 0, 'used': 0, 'unrealized_pnl': 0}
    
    def open_position(self, symbol: str, side: str, quantity: float, 
                     leverage: int = None) -> Optional[Dict]:
        """
        Abrir posición
        
        Args:
            symbol: Par
            side: 'LONG' o 'SHORT'
            quantity: Cantidad
            leverage: Apalancamiento (opcional)
        
        Returns:
            Datos de la orden o None si falla
        """
        try:
            # Configurar leverage si se especifica
            if leverage:
                self.set_leverage(symbol, leverage)
            
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': 'BUY' if side == 'LONG' else 'SELL',
                'positionSide': side,
                'type': 'MARKET',
                'quantity': str(quantity),
                'timestamp': timestamp
            }
            
            params['signature'] = self._sign_request(params)
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            response = self.session.post(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    order = data.get('data', {}).get('order', {})
                    return {
                        'order_id': order.get('orderId'),
                        'symbol': symbol,
                        'side': side,
                        'quantity': quantity,
                        'status': 'OPENED'
                    }
            
            logger.error(f"❌ Error abriendo: {data.get('msg')}")
            return None
        
        except Exception as e:
            logger.error(f"❌ Error open_position: {e}")
            return None
    
    def close_position(self, symbol: str, side: str, quantity: float) -> bool:
        """Cerrar posición"""
        try:
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': 'SELL' if side == 'LONG' else 'BUY',
                'positionSide': side,
                'type': 'MARKET',
                'quantity': str(quantity),
                'timestamp': timestamp
            }
            
            params['signature'] = self._sign_request(params)
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            response = self.session.post(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('code') == 0
            
            return False
        
        except Exception as e:
            logger.error(f"❌ Error close_position: {e}")
            return False
    
    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Configurar apalancamiento"""
        try:
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': 'BOTH',
                'leverage': leverage,
                'timestamp': timestamp
            }
            
            params['signature'] = self._sign_request(params)
            
            url = f"{self.base_url}/openApi/swap/v2/trade/leverage"
            response = self.session.post(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('code') == 0
            
            return False
        
        except Exception as e:
            logger.debug(f"Error leverage {symbol}: {e}")
            return False
    
    def get_open_positions(self) -> List[Dict]:
        """Obtener posiciones abiertas"""
        try:
            timestamp = int(time.time() * 1000)
            params = {'timestamp': timestamp}
            params['signature'] = self._sign_request(params)
            
            url = f"{self.base_url}/openApi/swap/v2/user/positions"
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    positions = []
                    for pos in data.get('data', []):
                        if float(pos.get('positionAmt', 0)) != 0:
                            positions.append({
                                'symbol': pos.get('symbol'),
                                'side': pos.get('positionSide'),
                                'size': float(pos.get('positionAmt', 0)),
                                'entry_price': float(pos.get('avgPrice', 0)),
                                'unrealized_pnl': float(pos.get('unrealizedProfit', 0)),
                                'leverage': int(pos.get('leverage', 1))
                            })
                    return positions
            
            return []
        
        except Exception as e:
            logger.debug(f"Error positions: {e}")
            return []
