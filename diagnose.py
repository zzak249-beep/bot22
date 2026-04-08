#!/usr/bin/env python3
"""
Diagnostic Script - Advanced Trading Bot V2
Verifica configuración, dependencias y conexiones
"""
import sys
import os
import importlib
from datetime import datetime


class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'
    BOLD = '\033[1m'


def print_header(text):
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'='*70}{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}{text.center(70)}{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}{'='*70}{Colors.END}\n")


def check_ok(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")


def check_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.END}")


def check_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")


def check_python_version():
    """Verificar versión de Python"""
    print_header("VERIFICACIÓN DE SISTEMA")
    
    version = sys.version_info
    print(f"Python: {version.major}.{version.minor}.{version.micro}")
    
    if version.major >= 3 and version.minor >= 9:
        check_ok(f"Python {version.major}.{version.minor} es compatible")
        return True
    else:
        check_error(f"Python {version.major}.{version.minor} es demasiado antiguo")
        check_warning("Se requiere Python 3.9 o superior")
        return False


def check_dependencies():
    """Verificar dependencias instaladas"""
    print_header("VERIFICACIÓN DE DEPENDENCIAS")
    
    required = {
        'pandas': 'pandas',
        'numpy': 'numpy',
        'aiohttp': 'aiohttp',
        'dotenv': 'python-dotenv'
    }
    
    all_ok = True
    
    for module, package in required.items():
        try:
            importlib.import_module(module)
            check_ok(f"{package} instalado")
        except ImportError:
            check_error(f"{package} NO instalado")
            all_ok = False
    
    if not all_ok:
        print(f"\n{Colors.YELLOW}Instalar dependencias:{Colors.END}")
        print(f"  pip install -r requirements.txt")
    
    return all_ok


def check_env_file():
    """Verificar archivo .env"""
    print_header("VERIFICACIÓN DE CONFIGURACIÓN")
    
    if not os.path.exists('.env'):
        check_error("Archivo .env no encontrado")
        check_warning("Copiar .env.example a .env y configurar")
        print(f"\n{Colors.YELLOW}Crear archivo:{Colors.END}")
        print(f"  cp .env.example .env")
        print(f"  nano .env  # Editar con tus credenciales")
        return False
    
    check_ok("Archivo .env encontrado")
    
    # Verificar variables críticas
    from dotenv import load_dotenv
    load_dotenv()
    
    required_vars = [
        'BINGX_API_KEY',
        'BINGX_SECRET',
        'TG_TOKEN',
        'TG_CHAT_ID'
    ]
    
    all_ok = True
    for var in required_vars:
        value = os.getenv(var)
        if value and value != f'tu_{var.lower()}_aqui':
            check_ok(f"{var} configurado")
        else:
            check_error(f"{var} NO configurado")
            all_ok = False
    
    return all_ok


def check_files():
    """Verificar archivos del bot"""
    print_header("VERIFICACIÓN DE ARCHIVOS")
    
    required_files = [
        'bot_v2.py',
        'strategy_vwap.py',
        'strategy_sniper.py',
        'trade_analyzer.py',
        'bingx_client.py',
        'telegram_bot.py',
        'risk_manager.py'
    ]
    
    all_ok = True
    
    for file in required_files:
        if os.path.exists(file):
            check_ok(f"{file}")
        else:
            check_error(f"{file} NO encontrado")
            all_ok = False
    
    return all_ok


def check_permissions():
    """Verificar permisos de escritura"""
    print_header("VERIFICACIÓN DE PERMISOS")
    
    test_dirs = ['/tmp']
    
    all_ok = True
    
    for dir_path in test_dirs:
        test_file = os.path.join(dir_path, f'test_write_{datetime.now().timestamp()}.txt')
        try:
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            check_ok(f"Escritura en {dir_path}")
        except Exception as e:
            check_error(f"No se puede escribir en {dir_path}: {e}")
            all_ok = False
    
    return all_ok


def test_imports():
    """Probar importar módulos del bot"""
    print_header("VERIFICACIÓN DE MÓDULOS")
    
    modules = [
        ('strategy_vwap', 'VWAPVolatilityBands'),
        ('strategy_sniper', 'SniperStrategy'),
        ('trade_analyzer', 'TradeAnalyzer')
    ]
    
    all_ok = True
    
    for module_name, class_name in modules:
        try:
            module = importlib.import_module(module_name)
            getattr(module, class_name)
            check_ok(f"{module_name}.{class_name}")
        except Exception as e:
            check_error(f"{module_name}: {e}")
            all_ok = False
    
    return all_ok


def show_current_config():
    """Mostrar configuración actual"""
    print_header("CONFIGURACIÓN ACTUAL")
    
    from dotenv import load_dotenv
    load_dotenv()
    
    config = {
        'SYMBOL': os.getenv('SYMBOL', 'N/A'),
        'TIMEFRAME': os.getenv('TIMEFRAME', 'N/A'),
        'LEVERAGE': os.getenv('LEVERAGE', 'N/A'),
        'RISK_PCT': os.getenv('RISK_PCT', 'N/A'),
        'STRATEGY': os.getenv('STRATEGY', 'N/A'),
        'SIGNALS_ONLY': os.getenv('SIGNALS_ONLY', 'N/A'),
        'ATR_MULTIPLIER': os.getenv('ATR_MULTIPLIER', 'N/A'),
        'POLL_SECONDS': os.getenv('POLL_SECONDS', 'N/A')
    }
    
    for key, value in config.items():
        if value == 'N/A':
            check_warning(f"{key}: {value}")
        else:
            print(f"  {key}: {value}")


def main():
    """Ejecutar todos los diagnósticos"""
    print(f"\n{Colors.BOLD}🔍 DIAGNÓSTICO DEL SISTEMA{Colors.END}")
    print(f"{Colors.BOLD}Advanced Trading Bot V2{Colors.END}")
    
    checks = [
        ("Sistema", check_python_version),
        ("Dependencias", check_dependencies),
        ("Configuración", check_env_file),
        ("Archivos", check_files),
        ("Permisos", check_permissions),
        ("Módulos", test_imports)
    ]
    
    results = {}
    
    for name, check_func in checks:
        results[name] = check_func()
    
    # Mostrar configuración
    if results["Configuración"]:
        show_current_config()
    
    # Resumen
    print_header("RESUMEN")
    
    all_passed = all(results.values())
    
    for name, passed in results.items():
        if passed:
            check_ok(f"{name}: OK")
        else:
            check_error(f"{name}: FALLÓ")
    
    if all_passed:
        print(f"\n{Colors.GREEN}{Colors.BOLD}✅ ¡Todos los checks pasaron!{Colors.END}")
        print(f"\n{Colors.GREEN}El bot está listo para ejecutarse:{Colors.END}")
        print(f"  python bot_v2.py")
    else:
        print(f"\n{Colors.RED}{Colors.BOLD}❌ Algunos checks fallaron{Colors.END}")
        print(f"\n{Colors.YELLOW}Revisa los errores arriba y corrige antes de ejecutar el bot{Colors.END}")
    
    print(f"\n{Colors.BLUE}{'='*70}{Colors.END}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Diagnóstico interrumpido{Colors.END}")
    except Exception as e:
        print(f"\n{Colors.RED}Error en diagnóstico: {e}{Colors.END}")
        import traceback
        traceback.print_exc()
