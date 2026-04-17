import os
import asyncio
import ccxt.pro as ccxt
import pandas as pd
import logging

# Configuración de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

class WhaleHunter:
    def __init__(self):
        self.exchange = ccxt.bingx({
            'apiKey': os.getenv('BINGX_API_KEY'),
            'secret': os.getenv('BINGX_SECRET_KEY'),
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        self.symbol = 'BTC/USDT:USDT'
        self.order_size = float(os.getenv('ORDER_SIZE', 0.001))
        self.is_in_position = False

    async def detect_whale_trace(self):
        """
        Lógica de Ventaja Matemática:
        Detecta 'Absorción'. Volumen > 300% de la media con poco movimiento de precio.
        """
        try:
            # Obtener velas de 1 minuto
            ohlcv = await self.exchange.fetch_ohlcv(self.symbol, '1m', limit=20)
            df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
            
            recent_vol = df['volume'].iloc[-1]
            avg_vol = df['volume'].iloc[:-1].mean()
            price_change = abs((df['close'].iloc[-1] - df['open'].iloc[-1]) / df['open'].iloc[-1]) * 100

            # CRITERIO DE BALLENA:
            # Mucho volumen (3x la media) y el precio se mueve menos de 0.05% (Absorción)
            if recent_vol > (avg_vol * 3) and price_change < 0.05:
                logger.info(f"🐋 HUELLA DETECTADA: Volumen {recent_vol:.2f} (Media: {avg_vol:.2f})")
                return True
            return False
        except Exception as e:
            logger.error(f"Error analizando huellas: {e}")
            return False

    async def trade_logic(self):
        logger.info("🤖 Bot 'Cazador de Ballenas' activado...")
        while True:
            whale_active = await self.detect_whale_trace()
            
            if whale_active and not self.is_in_position:
                # Determinamos dirección basada en el flujo anterior
                logger.info("🚀 Ejecutando entrada rápida - Front-run a la ballena")
                try:
                    # Ejemplo de orden de compra (puedes ajustar a Long/Short según tendencia)
                    order = await self.exchange.create_market_order(self.symbol, 'buy', self.order_size)
                    logger.info(f"✅ Orden ejecutada: {order['id']}")
                    self.is_in_position = True
                    # Esperar 5 minutos o hasta objetivo para resetear posición (simplificado)
                    await asyncio.sleep(300) 
                    self.is_in_position = False
                except Exception as e:
                    logger.error(f"Error al ejecutar orden: {e}")

            await asyncio.sleep(10) # Escaneo cada 10 segundos para máxima velocidad

async def main():
    bot = WhaleHunter()
    await bot.trade_logic()

if __name__ == '__main__':
    asyncio.run(main())
