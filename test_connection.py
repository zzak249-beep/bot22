"""
Script de Testing - Verificar Conexión con BingX
Ejecutar antes de iniciar el bot para verificar configuración
"""

import os
import sys
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

print("=" * 70)
print("🔍 VERIFICACIÓN DE CONFIGURACIÓN - Bot Trading Multi-Moneda")
print("=" * 70)
print()

# ═══════════════════════════════════════════════════════════════
# 1. VERIFICAR VARIABLES DE ENTORNO
# ═══════════════════════════════════════════════════════════════

print("📋 1. Verificando variables de entorno...")
print("-" * 70)

required_vars = {
    'BINGX_API_KEY': 'API Key de BingX',
    'BINGX_SECRET_KEY': 'Secret Key de BingX',
}

optional_vars = {
    'TELEGRAM_BOT_TOKEN': 'Token del bot de Telegram',
    'TELEGRAM_CHAT_ID': 'Chat ID de Telegram',
    'SYMBOLS': 'Símbolos a tradear',
}

all_ok = True

for var, desc in required_vars.items():
    value = os.getenv(var)
    if value:
        masked = value[:8] + "..." if len(value) > 8 else "***"
        print(f"  ✅ {desc}: {masked}")
    else:
        print(f"  ❌ {desc}: NO CONFIGURADO")
        all_ok = False

print()
for var, desc in optional_vars.items():
    value = os.getenv(var)
    if value:
        if var == 'SYMBOLS':
            print(f"  ✅ {desc}: {value}")
        else:
            masked = value[:8] + "..." if len(value) > 8 else "***"
            print(f"  ✅ {desc}: {masked}")
    else:
        print(f"  ⚠️  {desc}: No configurado (opcional)")

if not all_ok:
    print()
    print("❌ ERROR: Faltan variables de entorno requeridas")
    print("   Edita el archivo .env con tus credenciales")
    sys.exit(1)

print()

# ═══════════════════════════════════════════════════════════════
# 2. VERIFICAR DEPENDENCIAS
# ═══════════════════════════════════════════════════════════════

print("📦 2. Verificando dependencias de Python...")
print("-" * 70)

required_packages = [
    ('pandas', 'pandas'),
    ('numpy', 'numpy'),
    ('requests', 'requests'),
    ('dotenv', 'python-dotenv'),
]

missing_packages = []

for package_name, pip_name in required_packages:
    try:
        __import__(package_name)
        print(f"  ✅ {package_name}")
    except ImportError:
        print(f"  ❌ {package_name} - NO INSTALADO")
        missing_packages.append(pip_name)

if missing_packages:
    print()
    print("❌ ERROR: Faltan paquetes de Python")
    print(f"   Instalar con: pip install {' '.join(missing_packages)}")
    sys.exit(1)

print()

# ═══════════════════════════════════════════════════════════════
# 3. PROBAR CONEXIÓN CON BINGX
# ═══════════════════════════════════════════════════════════════

print("🔗 3. Probando conexión con BingX...")
print("-" * 70)

try:
    from bingx_client_fixed import BingXClient, BingXError
    
    API_KEY = os.getenv("BINGX_API_KEY")
    SECRET_KEY = os.getenv("BINGX_SECRET_KEY")
    DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"
    
    mode_str = "DEMO (Testnet)" if DEMO_MODE else "REAL (Producción)"
    print(f"  Modo: {mode_str}")
    print()
    
    client = BingXClient(API_KEY, SECRET_KEY, demo=DEMO_MODE)
    
    # Test 1: Obtener balance
    print("  Test 1: Obtener balance...")
    try:
        balance = client.get_balance()
        print(f"    ✅ Balance disponible: {balance:.2f} USDT")
    except BingXError as e:
        print(f"    ❌ Error obteniendo balance: {e}")
        all_ok = False
    
    # Test 2: Obtener velas
    print("  Test 2: Obtener datos de mercado...")
    try:
        symbols_str = os.getenv("SYMBOLS", "BTC-USDT")
        test_symbol = symbols_str.split(",")[0].strip()
        
        klines = client.get_klines(test_symbol, "15m", limit=10)
        if klines and len(klines) > 0:
            print(f"    ✅ Datos de {test_symbol} obtenidos correctamente")
            last_price = float(klines[-1].get('c', 0))
            print(f"    ℹ️  Último precio: ${last_price:.4f}")
        else:
            print(f"    ❌ No se obtuvieron datos de {test_symbol}")
            all_ok = False
    except Exception as e:
        print(f"    ❌ Error obteniendo velas: {e}")
        all_ok = False
    
    # Test 3: Obtener info de símbolo
    print("  Test 3: Verificar configuración de símbolos...")
    try:
        symbol_info = client.get_symbol_info(test_symbol)
        if symbol_info:
            print(f"    ✅ Información de {test_symbol}:")
            print(f"       - Cantidad mínima: {symbol_info.get('minQty', 'N/A')}")
            print(f"       - Step de cantidad: {symbol_info.get('qtyStep', 'N/A')}")
            print(f"       - Precisión de precio: {symbol_info.get('pricePrecision', 'N/A')}")
        else:
            print(f"    ⚠️  No se pudo obtener info de {test_symbol}")
    except Exception as e:
        print(f"    ❌ Error: {e}")
    
    # Test 4: Verificar posiciones actuales
    print("  Test 4: Verificar posiciones actuales...")
    try:
        positions = client.get_positions(test_symbol)
        open_positions = [p for p in positions if float(p.get('positionAmt', 0)) != 0]
        
        if open_positions:
            print(f"    ⚠️  Hay {len(open_positions)} posición(es) abierta(s) en {test_symbol}")
            for pos in open_positions:
                side = pos.get('positionSide', 'UNKNOWN')
                amt = pos.get('positionAmt', 0)
                price = pos.get('avgPrice', 0)
                print(f"       - {side}: {amt} @ ${price}")
        else:
            print(f"    ✅ No hay posiciones abiertas en {test_symbol}")
    except Exception as e:
        print(f"    ⚠️  No se pudo verificar posiciones: {e}")

except ImportError as e:
    print(f"  ❌ Error importando módulos del bot: {e}")
    print(f"     Verifica que bingx_client_fixed.py existe")
    all_ok = False
except Exception as e:
    print(f"  ❌ Error inesperado: {e}")
    all_ok = False

print()

# ═══════════════════════════════════════════════════════════════
# 4. PROBAR TELEGRAM (OPCIONAL)
# ═══════════════════════════════════════════════════════════════

telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
telegram_chat = os.getenv("TELEGRAM_CHAT_ID")

if telegram_token and telegram_chat:
    print("📱 4. Probando notificaciones de Telegram...")
    print("-" * 70)
    
    try:
        import requests
        
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        data = {
            "chat_id": telegram_chat,
            "text": "✅ Test de conexión exitoso - Bot configurado correctamente",
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=data, timeout=5)
        
        if response.status_code == 200:
            print("  ✅ Mensaje de prueba enviado a Telegram")
            print("     Verifica tu chat para confirmar")
        else:
            print(f"  ❌ Error enviando mensaje: {response.status_code}")
            print(f"     {response.text}")
    except Exception as e:
        print(f"  ❌ Error probando Telegram: {e}")
    
    print()
else:
    print("📱 4. Telegram no configurado (opcional)")
    print("-" * 70)
    print("  ℹ️  Para recibir notificaciones, configura:")
    print("     - TELEGRAM_BOT_TOKEN")
    print("     - TELEGRAM_CHAT_ID")
    print()

# ═══════════════════════════════════════════════════════════════
# 5. VERIFICAR CONFIGURACIÓN DEL BOT
# ═══════════════════════════════════════════════════════════════

print("⚙️  5. Verificando configuración del bot...")
print("-" * 70)

config_params = {
    'SYMBOLS': os.getenv('SYMBOLS', 'BTC-USDT'),
    'TIMEFRAME': os.getenv('TIMEFRAME', '15m'),
    'LEVERAGE': os.getenv('LEVERAGE', '5'),
    'RISK_PCT': os.getenv('RISK_PCT', '0.05'),
    'MAX_POSITIONS': os.getenv('MAX_POSITIONS', '3'),
    'ENTRY_MAX_PROB': os.getenv('ENTRY_MAX_PROB', '0.65'),
    'EXIT_PROB': os.getenv('EXIT_PROB', '0.84'),
    'MIN_BALANCE': os.getenv('MIN_BALANCE', '10'),
}

for param, value in config_params.items():
    print(f"  • {param}: {value}")

print()

# Advertencias
symbols_list = config_params['SYMBOLS'].split(',')
max_pos = int(config_params['MAX_POSITIONS'])
min_bal = float(config_params['MIN_BALANCE'])
leverage = int(config_params['LEVERAGE'])
risk_pct = float(config_params['RISK_PCT'])

print("⚠️  Advertencias:")

if len(symbols_list) < max_pos:
    print(f"  • Símbolos ({len(symbols_list)}) < MAX_POSITIONS ({max_pos})")
    print(f"    Considera reducir MAX_POSITIONS a {len(symbols_list)}")

capital_needed = min_bal * max_pos
print(f"  • Capital mínimo recomendado: {capital_needed:.0f} USDT")

exposure_per_trade = risk_pct * leverage * 100
total_exposure = exposure_per_trade * max_pos
print(f"  • Exposición por trade: {exposure_per_trade:.0f}%")
print(f"  • Exposición total máxima: {total_exposure:.0f}%")

if total_exposure > 100:
    print(f"    ⚠️  ADVERTENCIA: Exposición muy alta!")

if leverage > 10:
    print(f"  • Apalancamiento alto ({leverage}x) - Mayor riesgo de liquidación")

print()

# ═══════════════════════════════════════════════════════════════
# RESUMEN FINAL
# ═══════════════════════════════════════════════════════════════

print("=" * 70)
if all_ok:
    print("✅ VERIFICACIÓN COMPLETADA - Todo configurado correctamente")
    print()
    print("Siguiente paso:")
    print("  python bot_fixed.py")
    print()
    if not DEMO_MODE:
        print("⚠️  ADVERTENCIA: Modo REAL activado - Se usará dinero real")
        print("   Para probar primero, configura: DEMO_MODE=true")
else:
    print("❌ VERIFICACIÓN FALLIDA - Hay errores que corregir")
    print()
    print("Revisa los errores anteriores y corrige antes de continuar")

print("=" * 70)
