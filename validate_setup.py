#!/usr/bin/env python3
"""
Validador de Módulos - Verifica que todo está correctamente instalado
Uso: python validate_setup.py
"""
import sys
import importlib
from typing import Tuple, List

REQUIRED_MODULES = {
    # Core
    "fastapi": "FastAPI web framework",
    "uvicorn": "ASGI server",
    "requests": "HTTP client",
    "httpx": "Async HTTP client",
    
    # Data
    "pandas": "DataFrame library",
    "numpy": "Numerical computing",
    
    # Config
    "dotenv": "Environment variables",
    "pydantic": "Data validation",
    
    # Async
    "asyncio": "Async utilities (built-in)",
    "signal": "Signal handling (built-in)",
    
    # Custom modules (should be in project folder)
    "bingx_client": "BingX API client",
    "config": "Configuration module",
    "data_fetcher": "OHLCV data fetcher",
    "risk_manager": "Risk management",
    "strategy_engine": "Strategy evaluation",
    "telegram_bot_v2": "Telegram bot v2",
    "trade_executor": "Trade execution",
    "sniper_strategy": "Sniper strategy (NEW)",
    "telegram_diagnostics": "Telegram diagnostics (NEW)",
}

OPTIONAL_MODULES = {
    "prometheus_client": "Metrics export",
    "python_json_logger": "JSON logging",
}


def check_module(module_name: str) -> Tuple[bool, str]:
    """Intenta importar un módulo y retorna (éxito, mensaje)."""
    try:
        importlib.import_module(module_name)
        return True, f"✅ {module_name}"
    except ImportError as e:
        return False, f"❌ {module_name}: {str(e)[:60]}"
    except Exception as e:
        return False, f"⚠️  {module_name}: {str(e)[:60]}"


def main():
    print("\n" + "="*70)
    print("🔍 VALIDADOR DE MÓDULOS - Trading Bot")
    print("="*70)
    
    required_results = []
    optional_results = []
    
    # Check required
    print("\n📦 MÓDULOS REQUERIDOS:")
    print("-" * 70)
    for module_name, description in REQUIRED_MODULES.items():
        success, msg = check_module(module_name)
        required_results.append(success)
        status = "✅" if success else "❌"
        print(f"{msg:<45} ({description})")
    
    # Check optional
    print("\n📦 MÓDULOS OPCIONALES:")
    print("-" * 70)
    for module_name, description in OPTIONAL_MODULES.items():
        success, msg = check_module(module_name)
        optional_results.append(success)
        status = "✅" if success else "⚠️"
        print(f"{msg:<45} ({description})")
    
    # Summary
    required_ok = sum(required_results)
    required_total = len(required_results)
    optional_ok = sum(optional_results)
    optional_total = len(optional_results)
    
    print("\n" + "="*70)
    print("📊 RESUMEN:")
    print("="*70)
    print(f"Requeridos:  {required_ok}/{required_total} ✅" if required_ok == required_total else f"Requeridos:  {required_ok}/{required_total} ❌ CRÍTICO")
    print(f"Opcionales:  {optional_ok}/{optional_total} ✅" if optional_ok == optional_total else f"Opcionales:  {optional_ok}/{optional_total} ⚠️  (pueden ignorarse)")
    
    if required_ok == required_total:
        print("\n✅ TODO OK - Bot listo para ejecutar")
        return 0
    else:
        print("\n❌ HAY ERRORES - Ver abajo:")
        return 1


if __name__ == "__main__":
    sys.exit(main())
