import os
import asyncio
import ccxt.pro as ccxt
import pandas as pd

# Configuración
API_KEY = os.getenv('BINGX_API_KEY')
SECRET_KEY = os.getenv('BINGX_SECRET_KEY')
SYMBOLS = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT'] # Monedas a vigilar
ORDER_SIZE = float(os.getenv('ORDER_SIZE', 0.001))

exchange = ccxt.bingx({
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

def calculate_gex_proxy(df):
    """
    Simula la lógica de AEGIS GEX: 
    Analiza la aceleración del precio vs volumen para detectar muros de dealers.
    """
    df['returns'] = df['close'].pct_change()
    df['volatility'] = df['returns'].rolling(window=20).std()
    # Si el volumen sube y el precio se detiene = Muro (Gamma Wall)
    df['pressure'] = df['volume'] * df['returns']
    return df['pressure'].iloc[-1], df['volatility'].iloc[-1]

async def monitor_market():
    print("🚀 Iniciando Escáner de Gamma Independiente...")
    while True:
        for symbol in SYMBOLS:
            try:
                # 1. Obtener datos históricos rápidos (OHLCV)
                ohlcv = await exchange.fetch_ohlcv(symbol, timeframe='1m', limit=50)
                df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
                
                pressure, vol = calculate_gex_proxy(df)
                
                # LÓGICA DE VENTAJA MATEMÁTICA: 
                # Si la presión es alta y la volatilidad baja = Compresión (Squeeze inminente)
                if pressure > df['volume'].mean() * 1.5:
                    print(f"🔥 Señal detectada en {symbol}: Presión de compra institucional")
                    # Aquí ejecutarías la orden de compra
                    # await exchange.create_market_order(symbol, 'buy', ORDER_SIZE)

                await asyncio.sleep(1) # No saturar la API
            except Exception as e:
                print(f"Error escaneando {symbol}: {e}")
        
        await asyncio.sleep(5) # Pausa entre escaneos completos

if __name__ == '__main__':
    asyncio.run(monitor_market())
