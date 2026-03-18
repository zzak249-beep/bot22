"""
Configuración centralizada del bot de trading
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Configuración global del bot"""
    
    # ========== CREDENCIALES ==========
    BINGX_API_KEY = os.getenv('BINGX_API_KEY', '')
    BINGX_API_SECRET = os.getenv('BINGX_API_SECRET', '')
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
    
    # ========== TRADING PARAMETERS ==========
    AUTO_TRADING_ENABLED = os.getenv('AUTO_TRADING_ENABLED', 'false').lower() == 'true'
    MAX_POSITION_SIZE = float(os.getenv('MAX_POSITION_SIZE', '100'))
    LEVERAGE = int(os.getenv('LEVERAGE', '2'))
    
    # ========== RISK MANAGEMENT ==========
    TAKE_PROFIT_PCT = float(os.getenv('TAKE_PROFIT_PCT', '2.0'))
    STOP_LOSS_PCT = float(os.getenv('STOP_LOSS_PCT', '1.0'))
    TRAILING_STOP_ENABLED = os.getenv('TRAILING_STOP_ENABLED', 'true').lower() == 'true'
    TRAILING_STOP_ACTIVATION = float(os.getenv('TRAILING_STOP_ACTIVATION', '1.0'))
    TRAILING_STOP_DISTANCE = float(os.getenv('TRAILING_STOP_DISTANCE', '0.5'))
    
    MAX_OPEN_TRADES = int(os.getenv('MAX_OPEN_TRADES', '5'))
    MAX_DAILY_LOSS = float(os.getenv('MAX_DAILY_LOSS', '500'))
    MAX_DRAWDOWN_PCT = float(os.getenv('MAX_DRAWDOWN_PCT', '10'))
    
    # ========== MARKET SCANNING ==========
    MIN_VOLUME_24H = float(os.getenv('MIN_VOLUME_24H', '1000000'))  # $1M
    MIN_PRICE = float(os.getenv('MIN_PRICE', '0.0001'))
    MAX_SYMBOLS_TO_TRADE = int(os.getenv('MAX_SYMBOLS_TO_TRADE', '50'))
    
    # ========== ML/AI SETTINGS ==========
    ML_ENABLED = os.getenv('ML_ENABLED', 'true').lower() == 'true'
    ML_CONFIDENCE_THRESHOLD = float(os.getenv('ML_CONFIDENCE_THRESHOLD', '0.65'))
    ML_RETRAIN_INTERVAL = int(os.getenv('ML_RETRAIN_INTERVAL', '3600'))  # segundos
    
    # ========== TECHNICAL ANALYSIS ==========
    RSI_PERIOD = int(os.getenv('RSI_PERIOD', '14'))
    RSI_OVERBOUGHT = int(os.getenv('RSI_OVERBOUGHT', '70'))
    RSI_OVERSOLD = int(os.getenv('RSI_OVERSOLD', '30'))
    
    MACD_FAST = int(os.getenv('MACD_FAST', '12'))
    MACD_SLOW = int(os.getenv('MACD_SLOW', '26'))
    MACD_SIGNAL = int(os.getenv('MACD_SIGNAL', '9'))
    
    BB_PERIOD = int(os.getenv('BB_PERIOD', '20'))
    BB_STD_DEV = float(os.getenv('BB_STD_DEV', '2.0'))
    
    # ========== TIMEFRAMES ==========
    TIMEFRAMES = ['1m', '5m', '15m', '1h']
    PRIMARY_TIMEFRAME = os.getenv('PRIMARY_TIMEFRAME', '5m')
    
    # ========== INTERVALS ==========
    CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '60'))  # segundos
    MARKET_SCAN_INTERVAL = int(os.getenv('MARKET_SCAN_INTERVAL', '300'))  # 5 min
    
    # ========== DATABASE ==========
    DB_PATH = os.getenv('DB_PATH', 'trading_bot.db')
    
    # ========== DASHBOARD ==========
    DASHBOARD_ENABLED = os.getenv('DASHBOARD_ENABLED', 'true').lower() == 'true'
    DASHBOARD_PORT = int(os.getenv('DASHBOARD_PORT', '8080'))
    DASHBOARD_UPDATE_INTERVAL = int(os.getenv('DASHBOARD_UPDATE_INTERVAL', '10'))
    
    # ========== LOGGING ==========
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'trading_bot.log')
    
    # ========== BINGX API ==========
    BASE_URL = "https://open-api.bingx.com"
    
    @classmethod
    def validate(cls):
        """Validar configuración"""
        errors = []
        
        if cls.AUTO_TRADING_ENABLED:
            if not cls.BINGX_API_KEY:
                errors.append("BINGX_API_KEY no configurada")
            if not cls.BINGX_API_SECRET:
                errors.append("BINGX_API_SECRET no configurada")
        
        if cls.MAX_POSITION_SIZE <= 0:
            errors.append("MAX_POSITION_SIZE debe ser > 0")
        
        if cls.TAKE_PROFIT_PCT <= 0 or cls.STOP_LOSS_PCT <= 0:
            errors.append("TP y SL deben ser > 0")
        
        return errors
    
    @classmethod
    def get_summary(cls):
        """Resumen de configuración"""
        return f"""
╔══════════════════════════════════════════════════════════════╗
║           🤖 CONFIGURACIÓN DEL BOT DE TRADING 🤖            ║
╠══════════════════════════════════════════════════════════════╣
║ TRADING                                                      ║
║  • Auto-Trading: {'✅ ACTIVADO' if cls.AUTO_TRADING_ENABLED else '❌ DESACTIVADO'}                              ║
║  • Position Size: ${cls.MAX_POSITION_SIZE}                   ║
║  • Leverage: {cls.LEVERAGE}x                                 ║
║  • Max Trades: {cls.MAX_OPEN_TRADES}                         ║
║                                                              ║
║ RISK MANAGEMENT                                              ║
║  • Take Profit: {cls.TAKE_PROFIT_PCT}%                       ║
║  • Stop Loss: {cls.STOP_LOSS_PCT}%                           ║
║  • Trailing Stop: {'✅ ON' if cls.TRAILING_STOP_ENABLED else '❌ OFF'}                                ║
║  • Max Daily Loss: ${cls.MAX_DAILY_LOSS}                     ║
║  • Max Drawdown: {cls.MAX_DRAWDOWN_PCT}%                     ║
║                                                              ║
║ ML/AI                                                        ║
║  • ML Enabled: {'✅ ON' if cls.ML_ENABLED else '❌ OFF'}                                    ║
║  • Confidence: {cls.ML_CONFIDENCE_THRESHOLD}                 ║
║  • Retrain: {cls.ML_RETRAIN_INTERVAL}s                       ║
║                                                              ║
║ MARKET SCANNING                                              ║
║  • Min Volume 24h: ${cls.MIN_VOLUME_24H:,.0f}                ║
║  • Max Symbols: {cls.MAX_SYMBOLS_TO_TRADE}                   ║
║  • Check Interval: {cls.CHECK_INTERVAL}s                     ║
║                                                              ║
║ DASHBOARD                                                    ║
║  • Enabled: {'✅ ON' if cls.DASHBOARD_ENABLED else '❌ OFF'}                                    ║
║  • Port: {cls.DASHBOARD_PORT}                                ║
╚══════════════════════════════════════════════════════════════╝
"""
