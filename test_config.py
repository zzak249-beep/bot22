#!/usr/bin/env python3
"""
Script de Test - Prueba el bot localmente antes de subir a Railway
"""

import os
from dotenv import load_dotenv

load_dotenv()

def test_config():
    """Verificar configuración"""
    print("="*80)
    print("🧪 TEST DE CONFIGURACIÓN")
    print("="*80)
    
    # API Keys
    api_key = os.getenv('BINGX_API_KEY', '')
    api_secret = os.getenv('BINGX_API_SECRET', '')
    
    print("\n1. API CREDENTIALS:")
    if api_key:
        print(f"   ✅ API Key: {api_key[:10]}...")
    else:
        print("   ❌ API Key: NO CONFIGURADA")
    
    if api_secret:
        print(f"   ✅ API Secret: ***configurada***")
    else:
        print("   ❌ API Secret: NO CONFIGURADA")
    
    # Parámetros
    print("\n2. PARÁMETROS:")
    params = {
        'AUTO_TRADING': os.getenv('AUTO_TRADING_ENABLED', 'false'),
        'MIN_CONFIDENCE': os.getenv('MIN_CONFIDENCE', '45'),
        'MIN_CHANGE_PCT': os.getenv('MIN_CHANGE_PCT', '0.3'),
        'CHECK_INTERVAL': os.getenv('CHECK_INTERVAL', '60'),
        'MAX_POSITION': os.getenv('MAX_POSITION_SIZE', '30'),
        'LEVERAGE': os.getenv('LEVERAGE', '3'),
        'MAX_TRADES': os.getenv('MAX_OPEN_TRADES', '5')
    }
    
    for key, value in params.items():
        print(f"   • {key}: {value}")
    
    # Telegram
    print("\n3. TELEGRAM:")
    tg_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    tg_chat = os.getenv('TELEGRAM_CHAT_ID', '')
    
    if tg_token and tg_chat:
        print("   ✅ Telegram configurado")
    else:
        print("   ⚪ Telegram no configurado (opcional)")
    
    # Resumen
    print("\n" + "="*80)
    print("📊 RESUMEN:")
    print("="*80)
    
    if api_key and api_secret:
        print("✅ API Keys: OK")
    else:
        print("❌ API Keys: FALTAN")
    
    min_conf = int(params['MIN_CONFIDENCE'])
    min_change = float(params['MIN_CHANGE_PCT'])
    
    if min_conf <= 50:
        print(f"✅ MIN_CONFIDENCE: {min_conf}% (BAJO - generará muchas señales)")
    else:
        print(f"⚠️ MIN_CONFIDENCE: {min_conf}% (ALTO - pocas señales)")
    
    if min_change <= 0.5:
        print(f"✅ MIN_CHANGE_PCT: {min_change}% (BAJO - detecta más movimientos)")
    else:
        print(f"⚠️ MIN_CHANGE_PCT: {min_change}% (ALTO - menos señales)")
    
    interval = int(params['CHECK_INTERVAL'])
    if interval <= 90:
        print(f"✅ CHECK_INTERVAL: {interval}s (RÁPIDO)")
    else:
        print(f"⚪ CHECK_INTERVAL: {interval}s (normal)")
    
    print("\n" + "="*80)
    print("💡 RECOMENDACIONES:")
    print("="*80)
    
    if min_conf > 50:
        print("⚠️ MIN_CONFIDENCE muy alto - Bájalo a 45 para más señales")
    
    if min_change > 0.5:
        print("⚠️ MIN_CHANGE_PCT muy alto - Bájalo a 0.3 para más señales")
    
    if interval > 90:
        print("💡 Baja CHECK_INTERVAL a 60s para más frecuencia")
    
    if not (api_key and api_secret):
        print("❌ CRÍTICO: Configura API keys en .env")
    
    print("\n" + "="*80)
    print("🎯 EXPECTATIVAS CON CONFIG ACTUAL:")
    print("="*80)
    
    # Calcular señales esperadas
    if min_conf <= 45 and min_change <= 0.3:
        print("📈 Señales esperadas: 20-30 por hora (MUY ACTIVO)")
    elif min_conf <= 50 and min_change <= 0.5:
        print("📈 Señales esperadas: 10-20 por hora (ACTIVO)")
    elif min_conf <= 60 and min_change <= 0.7:
        print("📈 Señales esperadas: 5-10 por hora (MODERADO)")
    else:
        print("📉 Señales esperadas: 0-5 por hora (CONSERVADOR)")
    
    print("\n✅ Test completado\n")


def test_connection():
    """Probar conexión a BingX"""
    print("="*80)
    print("🧪 TEST DE CONEXIÓN A BINGX")
    print("="*80)
    
    import requests
    
    print("\nProbando endpoint público...")
    
    try:
        url = "https://open-api.bingx.com/openApi/swap/v2/quote/ticker"
        params = {'symbol': 'BTC-USDT'}
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == 0:
                ticker = data['data']
                price = float(ticker.get('lastPrice', 0))
                change = float(ticker.get('priceChangePercent', 0))
                
                print(f"\n✅ Conexión OK")
                print(f"   BTC-USDT: ${price:,.2f}")
                print(f"   Cambio 24h: {change:+.2f}%")
                
                return True
            else:
                print(f"\n❌ Error API: {data.get('msg')}")
                return False
        else:
            print(f"\n❌ Error HTTP: {response.status_code}")
            return False
    
    except Exception as e:
        print(f"\n❌ Error de conexión: {e}")
        return False


if __name__ == "__main__":
    print("\n")
    test_config()
    test_connection()
    
    print("\n" + "="*80)
    print("¿TODO OK? Entonces puedes:")
    print("1. git add .")
    print("2. git commit -m 'Bot ultra-optimizado'")
    print("3. git push")
    print("="*80 + "\n")
