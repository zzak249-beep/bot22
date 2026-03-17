import os
from dotenv import load_dotenv

load_dotenv()

# ==================== API KEYS ====================
BINGX_API_KEY = os.getenv('BINGX_API_KEY')
BINGX_SECRET_KEY = os.getenv('BINGX_SECRET_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# ==================== TRADING PARAMETERS ====================
# Para multi-símbolo, SYMBOL puede ser una lista o "AUTO" para obtener todos
SYMBOL = os.getenv('SYMBOL', 'AUTO')  # 'AUTO' = obtiene todos de BingX
TIMEFRAME = os.getenv('TIMEFRAME', '15m')

# IMPORTANTE: Con multi-símbolo, reduce POSITION_SIZE
# Ya que tendrás múltiples posiciones abiertas
POSITION_SIZE = float(os.getenv('POSITION_SIZE', '0.005'))  # Reducido para multi-símbolo
TAKE_PROFIT_PERCENT = float(os.getenv('TAKE_PROFIT_PERCENT', '2.5'))
STOP_LOSS_PERCENT = float(os.getenv('STOP_LOSS_PERCENT', '1.5'))

# ==================== INDICATOR PARAMETERS ====================
LINREG_LENGTH = int(os.getenv('LINREG_LENGTH', '50'))
LINREG_MULT = float(os.getenv('LINREG_MULT', '2.0'))

# ==================== BOT SETTINGS ====================
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '300'))  # Segundos entre análisis
MAX_OPEN_POSITIONS = int(os.getenv('MAX_OPEN_POSITIONS', '5'))  # Max posiciones simultáneas
ENABLE_TRADING = os.getenv('ENABLE_TRADING', 'True').lower() == 'true'
DRY_RUN = os.getenv('DRY_RUN', 'False').lower() == 'true'

# ==================== MULTI-SÍMBOLO ====================
# Si quieres especificar símbolos en lugar de AUTO
# Descomenta y edita esto:
# CUSTOM_SYMBOLS = [
#     'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 
#     'ADA/USDT', 'DOGE/USDT', 'MATIC/USDT'
# ]
CUSTOM_SYMBOLS = None  # None = usar AUTO (todos los símbolos)

# ==================== LOGGING ====================
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
