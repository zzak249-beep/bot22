#!/usr/bin/env python3
"""
BingX Trading Bot - Mean Reversion & Scalping
Estrategias: VWAP+SD, BB+RSI, EMA Ribbon 9/15
Señales en Telegram | Deploy en Railway
"""

import asyncio
import logging
import os
from bot.telegram_bot import TelegramSignalBot
from bot.trader import Trader
from utils.logger import setup_logger

logger = setup_logger(__name__)


async def main():
    logger.info("🚀 Iniciando BingX Trading Bot...")

    trader = Trader()
    telegram_bot = TelegramSignalBot(trader)

    # Ejecutar ambos en paralelo
    await asyncio.gather(
        trader.run_loop(),
        telegram_bot.run()
    )


if __name__ == "__main__":
    asyncio.run(main())
