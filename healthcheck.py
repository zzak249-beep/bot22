#!/usr/bin/env python3
"""
Health Check Script para Railway
Verifica que el bot está funcionando correctamente
"""

import os
import sys
import time
from pathlib import Path

def check_bot_running():
    """Verifica si el bot está ejecutándose"""
    # Buscar archivo de log
    log_file = Path('/tmp/bot.log')
    
    if not log_file.exists():
        print("❌ Log file not found")
        return False
    
    # Verificar que el log ha sido actualizado recientemente (últimos 5 minutos)
    last_modified = log_file.stat().st_mtime
    time_since_update = time.time() - last_modified
    
    if time_since_update > 300:  # 5 minutos
        print(f"⚠️ Log file not updated in {int(time_since_update)} seconds")
        return False
    
    # Leer últimas líneas del log
    try:
        with open(log_file, 'r') as f:
            lines = f.readlines()
            last_lines = lines[-20:] if len(lines) > 20 else lines
            
            # Buscar errores críticos
            critical_errors = [
                'KeyError',
                'ConnectionError',
                'API error 109400',
                'highest',
                'Bot stopped',
                'terminated'
            ]
            
            for line in last_lines:
                for error in critical_errors:
                    if error in line:
                        print(f"❌ Critical error found: {error}")
                        print(f"   Line: {line.strip()}")
                        return False
            
            # Buscar señales de vida
            alive_indicators = [
                'Scanning',
                'Positions:',
                'PnL:',
                'UTC'
            ]
            
            recent_activity = any(
                any(indicator in line for indicator in alive_indicators)
                for line in last_lines
            )
            
            if not recent_activity:
                print("⚠️ No recent activity detected")
                return False
    
    except Exception as e:
        print(f"❌ Error reading log: {e}")
        return False
    
    print("✅ Bot is running healthy")
    return True

def check_env_vars():
    """Verifica que las variables de entorno críticas están configuradas"""
    required_vars = [
        'BINGX_API_KEY',
        'BINGX_API_SECRET'
    ]
    
    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)
    
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        return False
    
    print("✅ Environment variables configured")
    return True

def main():
    """Main health check"""
    print("🔍 Running health check...")
    
    checks = [
        ("Environment Variables", check_env_vars),
        ("Bot Status", check_bot_running)
    ]
    
    results = []
    for name, check_func in checks:
        print(f"\n📋 Checking: {name}")
        result = check_func()
        results.append(result)
    
    print("\n" + "="*50)
    if all(results):
        print("✅ All health checks passed!")
        sys.exit(0)
    else:
        print("❌ Some health checks failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
