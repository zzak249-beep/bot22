import os
from dotenv import load_dotenv

load_dotenv()

# ==================== API KEYS ====================
BINGX_API_KEY = os.getenv('BINGX_API_KEY')
BINGX_SECRET_KEY = os.getenv('BINGX_SECRET_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# ==================== TRADING PARAMETERS ====================
SYMBOL = os.getenv('SYMBOL', 'BTC/USDT')
TIMEFRAME = os.getenv('TIMEFRAME', '15m')
POSITION_SIZE = float(os.getenv('POSITION_SIZE', '0.01'))
TAKE_PROFIT_PERCENT = float(os.getenv('TAKE_PROFIT_PERCENT', '2.5'))
STOP_LOSS_PERCENT = float(os.getenv('STOP_LOSS_PERCENT', '1.5'))

# ==================== INDICATOR PARAMETERS ====================
LINREG_LENGTH = int(os.getenv('LINREG_LENGTH', '50'))
LINREG_MULT = float(os.getenv('LINREG_MULT', '2.0'))

# ==================== BOT SETTINGS ====================
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '300'))
ENABLE_TRADING = os.getenv('ENABLE_TRADING', 'True').lower() == 'true'
DRY_RUN = os.getenv('DRY_RUN', 'False').lower() == 'true'
