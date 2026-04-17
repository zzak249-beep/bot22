import os
import asyncio
import ccxt.pro as ccxt
import pandas as pd
import numpy as np
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

class QuantBotSimons:
    def __init__(self):
        self.exchange = ccxt.bingx({
            'apiKey': os.getenv('BINGX_API_KEY'),
            'secret': os.getenv('BINGX_SECRET_KEY'),
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        self.symbol = 'BTC/USDT:USDT'
        self.order_size = float(os.getenv('ORDER_SIZE', 0.001))
        self.trailing_pct = 0.005  # 0.5% de Trailing Stop
        self.is_in_position = False
        self.entry_price = 0
        self.max_price_seen = 0

    async def get_statistical_edge(self):
        """
        Análisis de Frecuencias (Estilo Simons):
        Detección de agotamiento mediante Z-Score y Volumen.
        """
        try:
            ohlcv = await self.exchange.fetch_ohlcv(self.symbol, '1m', limit=30)
            df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
            
            # Cálculo de Z-Score (Desviación estadística)
            df['mean'] = df['close'].rolling(window=20).mean()
            df['std'] = df['close'].rolling(window=20).std()
            df['z_score'] = (df['close'] - df['mean']) / df['std']
            
            current_z = df['z_score'].iloc[-1]
            vol_surge = df['volume'].iloc[-1] > df['volume'].mean() * 3
            
            # PATRÓN: Z-Score extremo + Pico de volumen = Agotamiento de Ballena
            if abs(current_z) > 2.5 and vol_surge:
                side = 'buy' if current_z < -2.5 else 'sell'
                logger.info(f"📊 VENTAJA ESTADÍSTICA: Z-Score {current_z:.2f} detectado. Ballena agotada.")
                return side
            return None
        except Exception as e:
            logger.error(f"Error en análisis quant: {e}")
            return None

    async def manage_trailing_stop(self):
        """
        Lógica de Trailing Stop activa.
        """
        try:
            ticker = await self.exchange.fetch_ticker(self.symbol)
            current_price = ticker['last']

            if current_price > self.max_price_seen:
                self.max_price_seen = current_price
                logger.info(f"📈 Nuevo máximo visto: {self.max_price_seen}")

            # Si el precio cae un 0.5% desde el máximo visto, cerramos con ganancia
            stop_level = self.max_price_seen * (1 - self.trailing_pct)
            if current_price <= stop_level:
                logger.info(f"💰 TRAILING STOP ACTIVADO. Cerrando posición en {current_price}")
                await self.exchange.create_market_order(self.symbol, 'sell', self.order_size)
                self.is_in_position = False
                self.max_price_seen = 0
        except Exception as e:
            logger.error(f"Error en Trailing Stop: {e}")

    async def run(self):
        logger.info("🚀 Bot Quant 'Simons' activo en Railway")
        while True:
            if not self.is_in_position:
                signal = await self.get_statistical_edge()
                if signal == 'buy':
                    order = await self.exchange.create_market_order(self.symbol, 'buy', self.order_size)
                    self.entry_price = float(order['price'] if order['price'] else (await self.exchange.fetch_ticker(self.symbol))['last'])
                    self.max_price_seen = self.entry_price
                    self.is_in_position = True
            else:
                await self.manage_trailing_stop()
            
            await asyncio.sleep(5) # Escaneo rápido

if __name__ == '__main__':
    bot = QuantBotSimons()
    asyncio.run(bot.run())
