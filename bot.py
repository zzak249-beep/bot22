import os
import asyncio
from flask import Flask, request, jsonify
import ccxt.pro as ccxt  # Usamos la versión Pro/Asíncrona para máxima velocidad

app = Flask(__name__)

# Configuración de BingX asíncrona
exchange = ccxt.bingx({
    'apiKey': os.getenv('BINGX_API_KEY'),
    'secret': os.getenv('BINGX_SECRET_KEY'),
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

async def execute_trade(symbol, side, amount):
    try:
        # 1. CONSEGUIR POSICIÓN ACTUAL
        positions = await exchange.fetch_positions([symbol])
        current_pos = next((p for p in positions if p['symbol'] == symbol), None)
        
        # 2. LÓGICA DE REVERSIÓN (SIEMPRE EN EL MERCADO)
        # Si estamos largos y llega señal SHORT, cerramos y abrimos short.
        if current_pos and float(current_pos['contracts']) > 0:
            current_side = 'long' if float(current_pos['contracts']) > 0 else 'short'
            if current_side != side:
                print(f"Cerrando posición {current_side} previa...")
                # Cerramos posición actual
                await exchange.create_market_order(symbol, 'sell' if current_side == 'long' else 'buy', abs(float(current_pos['contracts'])))

        # 3. EJECUCIÓN DE NUEVA ORDEN
        print(f"Ejecutando entrada {side} en {symbol}...")
        order = await exchange.create_market_order(symbol, 'buy' if side == 'long' else 'sell', amount)
        return order
    except Exception as e:
        print(f"Error en ejecución: {e}")
        return None

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    symbol = data.get('ticker', 'BTC/USDT:USDT')
    signal = data.get('signal').lower() # 'long' o 'short'
    amount = float(os.getenv('ORDER_SIZE', 0.001))

    # Ejecutar de forma asíncrona sin bloquear el webhook
    asyncio.run(execute_trade(symbol, signal, amount))
    
    return jsonify({"status": "received"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
