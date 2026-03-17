import ccxt.async_support as ccxt
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

class BingXAPI:
    """
    Wrapper asíncrono para la API de BingX usando CCXT
    """
    def __init__(self, api_key: str, secret_key: str):
        self.exchange = ccxt.bingx({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap'
            }
        })
        logger.info("✅ BingX API inicializada")
    
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> List:
        """
        Obtiene datos OHLCV (Open, High, Low, Close, Volume)
        Returns: [[timestamp, open, high, low, close, volume], ...]
        """
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            logger.info(f"✅ Obtenidas {len(ohlcv)} velas de {symbol} {timeframe}")
            return ohlcv
        except Exception as e:
            logger.error(f"❌ Error fetching OHLCV: {e}")
            return []
    
    async def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Obtiene el precio actual del símbolo
        """
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            price = float(ticker['last'])
            logger.debug(f"Precio actual {symbol}: {price}")
            return price
        except Exception as e:
            logger.error(f"❌ Error fetching price: {e}")
            return None
    
    async def create_order(self, symbol: str, order_type: str, quantity: float, price: float = None) -> Optional[Dict]:
        """
        Crea una orden de mercado (BUY o SELL)
        
        Args:
            symbol: 'BTC/USDT'
            order_type: 'BUY' o 'SELL'
            quantity: Cantidad a tradear
            price: Precio (ignorado para órdenes de mercado)
        """
        try:
            if order_type == 'BUY':
                order = await self.exchange.create_market_buy_order(symbol, quantity)
                logger.info(f"✅ Orden BUY creada: {order['id']} - Qty: {quantity}")
            elif order_type == 'SELL':
                order = await self.exchange.create_market_sell_order(symbol, quantity)
                logger.info(f"✅ Orden SELL creada: {order['id']} - Qty: {quantity}")
            else:
                logger.error(f"❌ Tipo de orden inválido: {order_type}")
                return None
            
            return order
        except Exception as e:
            logger.error(f"❌ Error creating order: {e}")
            return None
    
    async def get_balance(self, symbol: str = None) -> Optional[Dict]:
        """
        Obtiene el balance de la cuenta
        """
        try:
            balance = await self.exchange.fetch_balance()
            logger.debug(f"Balance obtenido")
            return balance
        except Exception as e:
            logger.error(f"❌ Error fetching balance: {e}")
            return None
    
    async def close(self):
        """
        Cierra la conexión con la API
        """
        await self.exchange.close()
        logger.info("✅ API cerrada")
