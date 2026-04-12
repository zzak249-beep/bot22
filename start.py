#!/usr/bin/env python3
"""
Start Script para Institutional Bot v3.1
Valida configuración antes de iniciar
"""

import os
import sys

def print_banner():
    print("\n" + "="*80)
    print("🏆 INSTITUTIONAL BOT v3.1 STARTUP")
    print("="*80 + "\n")

def validate_env():
    """Valida variables de entorno críticas"""
    print("🔍 Validating environment variables...")
    
    required = {
        'BINGX_API_KEY': 'BingX API Key',
        'BINGX_API_SECRET': 'BingX API Secret'
    }
    
    missing = []
    for var, description in required.items():
        if not os.getenv(var):
            missing.append(f"  ❌ {var} ({description})")
        else:
            value = os.getenv(var)
            masked = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
            print(f"  ✅ {var}: {masked}")
    
    if missing:
        print("\n❌ Missing required variables:")
        for m in missing:
            print(m)
        print("\nPlease configure these in Railway Variables tab")
        return False
    
    # Validate optional but important
    auto_trading = os.getenv('AUTO_TRADING_ENABLED', 'false').lower()
    if auto_trading == 'true':
        print("\n⚠️  WARNING: AUTO_TRADING_ENABLED=true")
        print("   Bot will trade with REAL MONEY!")
        print("   Make sure this is intentional.\n")
    else:
        print("\n📝 PAPER MODE: AUTO_TRADING_ENABLED=false")
        print("   Bot will simulate trades (no real money)")
    
    return True

def validate_files():
    """Valida que archivos necesarios existen"""
    print("\n🔍 Validating files...")
    
    required_files = [
        'institutional_bot_v3_fixed.py'
    ]
    
    missing = []
    for file in required_files:
        if os.path.exists(file):
            print(f"  ✅ {file}")
        else:
            print(f"  ❌ {file}")
            missing.append(file)
    
    if missing:
        print("\n❌ Missing files:")
        for m in missing:
            print(f"  - {m}")
        return False
    
    return True

def show_config():
    """Muestra configuración actual"""
    print("\n📊 Current Configuration:")
    
    config_vars = [
        ('POSITION_SIZE_USD', '10'),
        ('LEVERAGE', '2'),
        ('MAX_POSITIONS', '2'),
        ('CIRCUIT_BREAKER_PCT', '3.0'),
        ('MIN_ENTRY_SCORE', '75'),
        ('SL_ATR_MULTIPLIER', '1.5')
    ]
    
    for var, default in config_vars:
        value = os.getenv(var, default)
        print(f"  {var}: {value}")

def main():
    """Main startup validation"""
    print_banner()
    
    # Validaciones
    if not validate_env():
        sys.exit(1)
    
    if not validate_files():
        sys.exit(1)
    
    show_config()
    
    print("\n" + "="*80)
    print("✅ All validations passed!")
    print("🚀 Starting Institutional Bot v3.1...")
    print("="*80 + "\n")
    
    # Importar y ejecutar el bot
    try:
        import institutional_bot_v3_fixed
    except ImportError as e:
        print(f"❌ Failed to import bot: {e}")
        print("   Make sure institutional_bot_v3_fixed.py is in the same directory")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Startup interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error during startup: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
