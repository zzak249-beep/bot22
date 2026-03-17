import aiohttp
import logging
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

async def send_telegram_message(message: str) -> bool:
    """
    Envía un mensaje a Telegram de forma asíncrona
    
    Args:
        message: Texto del mensaje
    
    Returns:
        bool: True si se envió exitosamente, False si falló
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Telegram no configurado - saltando notificación")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    try:
        async with aiohttp.ClientSession() as session:
            data = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            async with session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    logger.info(f"✅ Mensaje Telegram enviado: {message[:50]}...")
                    return True
                else:
                    logger.error(f"❌ Error Telegram status {response.status}")
                    return False
    except aiohttp.ClientError as e:
        logger.error(f"❌ Error conectando a Telegram: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Error en Telegram: {e}")
        return False
