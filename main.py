#!/usr/bin/env python3
"""
Trading Bot - Estrategia Liquidity Day + Linear Regression Channel
Ejecuta automáticamente con BingX API y notificaciones Telegram
"""

import asyncio
import logging
import numpy as np
from datetime import datetime
from config import (
    BINGX_API_KEY,
    BINGX_SECRET_KEY,
    SYMBOL,
    TIMEFRAME,
    POSITION_SIZE,
    TAKE_PROFIT_PERCENT,
    STOP_LOSS_PERCENT,
    LINREG_LENGTH,
    LINREG_MULT,
    CHECK_INTERVAL,
    DRY_RUN,
    ENABLE_TRADING,
)
from indicators import LinearRegressionChannel, LiquidityLevels
from bingx_api import BingXAPI
from telegram_bot import send_telegram_message

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TradingBot:
    """
    Bot de trading automático que combina dos estrategias:
    1. Liquidity Day: Detecta breakouts en PDH/PDL
    2. Linear Regression: Confirma cambios de tendencia
    """
    
    def __init__(self):
        self.bingx = BingXAPI(BINGX_API_KEY, BINGX_SECRET_KEY)
        self.linreg = LinearRegressionChannel(LINREG_LENGTH, LINREG_MULT)
        self.liquidity = LiquidityLevels()
        
        # Estado de posición
        self.position = None  # None, 'LONG', 'SHORT'
        self.entry_price = None
        self.last_signal_time = None
        
        logger.info(f"🤖 Bot inicializado")
        logger.info(f"   Símbolo: {SYMBOL}")
        logger.info(f"   Timeframe: {TIMEFRAME}")
        logger.info(f"   Position Size: {POSITION_SIZE}")
        logger.info(f"   TP: {TAKE_PROFIT_PERCENT}% | SL: {STOP_LOSS_PERCENT}%")
        logger.info(f"   DRY RUN: {DRY_RUN}")
    
    async def analyze_market(self) -> dict:
        """
        Analiza el mercado usando ambos indicadores
        """
        try:
            # Obtener datos
            ohlcv = await self.bingx.fetch_ohlcv(SYMBOL, TIMEFRAME, 200)
            
            if len(ohlcv) < max(LINREG_LENGTH, 50):
                logger.warning(f"⚠️ Datos insuficientes: {len(ohlcv)} velas")
                return None
            
            # Extraer precios
            closes = np.array([candle[4] for candle in ohlcv])
            price = float(closes[-1])
            
            # Calcular indicadores
            linreg_data = self.linreg.calculate(closes)
            liquidity_data = self.liquidity.calculate(ohlcv)
            
            if not linreg_data:
                return None
            
            # Extraer valores
            basis = linreg_data['basis'][0]
            upper = linreg_data['upper'][0]
            lower = linreg_data['lower'][0]
            is_bullish = linreg_data['is_bullish'][0]
            trend_up = linreg_data['trend_up'][0]
            trend_down = linreg_data['trend_down'][0]
            
            pdh = liquidity_data['pdh']
            pdl = liquidity_data['pdl']
            
            return {
                'price': price,
                'pdh': pdh,
                'pdl': pdl,
                'basis': basis,
                'upper': upper,
                'lower': lower,
                'is_bullish': is_bullish,
                'trend_up': trend_up,
                'trend_down': trend_down,
                'timestamp': datetime.now()
            }
        
        except Exception as e:
            logger.error(f"❌ Error analizando mercado: {e}")
            return None
    
    def generate_signal(self, analysis: dict) -> str:
        """
        Genera señales de trading basándose en el análisis
        
        REGLAS:
        BUY:  Precio > PDH AND Tendencia alcista AND Cambio a UP
        SELL: Precio < PDL AND Tendencia bajista AND Cambio a DOWN
        """
        price = analysis['price']
        pdh = analysis['pdh']
        pdl = analysis['pdl']
        basis = analysis['basis']
        upper = analysis['upper']
        lower = analysis['lower']
        is_bullish = analysis['is_bullish']
        trend_up = analysis['trend_up']
        trend_down = analysis['trend_down']
        
        signal = None
        
        # SEÑAL BUY
        if not self.position:
            # Condición 1: Breakout de PDH + Tendencia alcista
            if price > pdh and is_bullish and trend_up:
                logger.info(f"🟢 SEÑAL BUY: Precio {price:.4f} > PDH {pdh:.4f} + Tendencia UP")
                signal = 'BUY'
            
            # Condición 2: Bounce en lower band + Cambio a tendencia alcista
            elif price > lower and trend_up and price > basis:
                logger.info(f"🟢 SEÑAL BUY: Bounce en lower {lower:.4f} + Tendencia UP")
                signal = 'BUY'
        
        # SEÑAL SELL
        if not self.position:
            # Condición 1: Breakout de PDL + Tendencia bajista
            if price < pdl and not is_bullish and trend_down:
                logger.info(f"🔴 SEÑAL SELL: Precio {price:.4f} < PDL {pdl:.4f} + Tendencia DOWN")
                signal = 'SELL'
            
            # Condición 2: Rechazo en upper band + Cambio a tendencia bajista
            elif price < upper and trend_down and price < basis:
                logger.info(f"🔴 SEÑAL SELL: Rechazo en upper {upper:.4f} + Tendencia DOWN")
                signal = 'SELL'
        
        # CLOSE POSITIONS (TP/SL)
        if self.position and self.entry_price:
            tp_long = self.entry_price * (1 + TAKE_PROFIT_PERCENT / 100)
            sl_long = self.entry_price * (1 - STOP_LOSS_PERCENT / 100)
            
            tp_short = self.entry_price * (1 - TAKE_PROFIT_PERCENT / 100)
            sl_short = self.entry_price * (1 + STOP_LOSS_PERCENT / 100)
            
            if self.position == 'LONG':
                if price >= tp_long:
                    logger.info(f"💰 TP LONG alcanzado: {price:.4f} >= {tp_long:.4f}")
                    signal = 'CLOSE_LONG_TP'
                elif price <= sl_long:
                    logger.info(f"⛔ SL LONG tocado: {price:.4f} <= {sl_long:.4f}")
                    signal = 'CLOSE_LONG_SL'
            
            elif self.position == 'SHORT':
                if price <= tp_short:
                    logger.info(f"💰 TP SHORT alcanzado: {price:.4f} <= {tp_short:.4f}")
                    signal = 'CLOSE_SHORT_TP'
                elif price >= sl_short:
                    logger.info(f"⛔ SL SHORT tocado: {price:.4f} >= {sl_short:.4f}")
                    signal = 'CLOSE_SHORT_SL'
        
        return signal
    
    async def execute_signal(self, signal: str, analysis: dict) -> bool:
        """
        Ejecuta la orden según la señal
        """
        if not signal:
            return False
        
        price = analysis['price']
        
        try:
            if signal == 'BUY':
                if DRY_RUN:
                    logger.info(f"📊 [DRY RUN] BUY: {SYMBOL} @ {price:.4f} x {POSITION_SIZE}")
                else:
                    if not ENABLE_TRADING:
                        logger.warning("⚠️ Trading deshabilitado")
                        return False
                    
                    order = await self.bingx.create_order(SYMBOL, 'BUY', POSITION_SIZE)
                    if not order:
                        logger.error("❌ Error creando orden BUY")
                        return False
                
                self.position = 'LONG'
                self.entry_price = price
                
                tp_price = price * (1 + TAKE_PROFIT_PERCENT / 100)
                sl_price = price * (1 - STOP_LOSS_PERCENT / 100)
                
                message = (
                    f"🟢 <b>ENTRADA LONG</b>\n"
                    f"<b>Símbolo:</b> {SYMBOL}\n"
                    f"<b>Entrada:</b> ${price:.4f}\n"
                    f"<b>TP:</b> ${tp_price:.4f}\n"
                    f"<b>SL:</b> ${sl_price:.4f}\n"
                    f"<b>Cantidad:</b> {POSITION_SIZE}\n"
                    f"<b>Risk/Reward:</b> 1:{(tp_price - price) / (price - sl_price):.2f}"
                )
                await send_telegram_message(message)
                return True
            
            elif signal == 'SELL':
                if DRY_RUN:
                    logger.info(f"📊 [DRY RUN] SELL: {SYMBOL} @ {price:.4f} x {POSITION_SIZE}")
                else:
                    if not ENABLE_TRADING:
                        logger.warning("⚠️ Trading deshabilitado")
                        return False
                    
                    order = await self.bingx.create_order(SYMBOL, 'SELL', POSITION_SIZE)
                    if not order:
                        logger.error("❌ Error creando orden SELL")
                        return False
                
                self.position = 'SHORT'
                self.entry_price = price
                
                tp_price = price * (1 - TAKE_PROFIT_PERCENT / 100)
                sl_price = price * (1 + STOP_LOSS_PERCENT / 100)
                
                message = (
                    f"🔴 <b>ENTRADA SHORT</b>\n"
                    f"<b>Símbolo:</b> {SYMBOL}\n"
                    f"<b>Entrada:</b> ${price:.4f}\n"
                    f"<b>TP:</b> ${tp_price:.4f}\n"
                    f"<b>SL:</b> ${sl_price:.4f}\n"
                    f"<b>Cantidad:</b> {POSITION_SIZE}\n"
                    f"<b>Risk/Reward:</b> 1:{(price - tp_price) / (sl_price - price):.2f}"
                )
                await send_telegram_message(message)
                return True
            
            elif signal == 'CLOSE_LONG_TP':
                if DRY_RUN:
                    logger.info(f"📊 [DRY RUN] CLOSE LONG TP: {SYMBOL} @ {price:.4f}")
                else:
                    if not ENABLE_TRADING:
                        logger.warning("⚠️ Trading deshabilitado")
                        return False
                    
                    order = await self.bingx.create_order(SYMBOL, 'SELL', POSITION_SIZE)
                    if not order:
                        logger.error("❌ Error cerrando posición")
                        return False
                
                profit_pct = ((price - self.entry_price) / self.entry_price) * 100
                message = (
                    f"💰 <b>TAKE PROFIT LONG</b>\n"
                    f"<b>Entrada:</b> ${self.entry_price:.4f}\n"
                    f"<b>Salida:</b> ${price:.4f}\n"
                    f"<b>Ganancia:</b> {profit_pct:+.2f}%"
                )
                await send_telegram_message(message)
                self.position = None
                return True
            
            elif signal == 'CLOSE_LONG_SL':
                if DRY_RUN:
                    logger.info(f"📊 [DRY RUN] CLOSE LONG SL: {SYMBOL} @ {price:.4f}")
                else:
                    if not ENABLE_TRADING:
                        logger.warning("⚠️ Trading deshabilitado")
                        return False
                    
                    order = await self.bingx.create_order(SYMBOL, 'SELL', POSITION_SIZE)
                    if not order:
                        logger.error("❌ Error cerrando posición")
                        return False
                
                loss_pct = ((price - self.entry_price) / self.entry_price) * 100
                message = (
                    f"⛔ <b>STOP LOSS LONG</b>\n"
                    f"<b>Entrada:</b> ${self.entry_price:.4f}\n"
                    f"<b>Salida:</b> ${price:.4f}\n"
                    f"<b>Pérdida:</b> {loss_pct:.2f}%"
                )
                await send_telegram_message(message)
                self.position = None
                return True
            
            elif signal == 'CLOSE_SHORT_TP':
                if DRY_RUN:
                    logger.info(f"📊 [DRY RUN] CLOSE SHORT TP: {SYMBOL} @ {price:.4f}")
                else:
                    if not ENABLE_TRADING:
                        logger.warning("⚠️ Trading deshabilitado")
                        return False
                    
                    order = await self.bingx.create_order(SYMBOL, 'BUY', POSITION_SIZE)
                    if not order:
                        logger.error("❌ Error cerrando posición")
                        return False
                
                profit_pct = ((self.entry_price - price) / self.entry_price) * 100
                message = (
                    f"💰 <b>TAKE PROFIT SHORT</b>\n"
                    f"<b>Entrada:</b> ${self.entry_price:.4f}\n"
                    f"<b>Salida:</b> ${price:.4f}\n"
                    f"<b>Ganancia:</b> {profit_pct:+.2f}%"
                )
                await send_telegram_message(message)
                self.position = None
                return True
            
            elif signal == 'CLOSE_SHORT_SL':
                if DRY_RUN:
                    logger.info(f"📊 [DRY RUN] CLOSE SHORT SL: {SYMBOL} @ {price:.4f}")
                else:
                    if not ENABLE_TRADING:
                        logger.warning("⚠️ Trading deshabilitado")
                        return False
                    
                    order = await self.bingx.create_order(SYMBOL, 'BUY', POSITION_SIZE)
                    if not order:
                        logger.error("❌ Error cerrando posición")
                        return False
                
                loss_pct = ((price - self.entry_price) / self.entry_price) * 100
                message = (
                    f"⛔ <b>STOP LOSS SHORT</b>\n"
                    f"<b>Entrada:</b> ${self.entry_price:.4f}\n"
                    f"<b>Salida:</b> ${price:.4f}\n"
                    f"<b>Pérdida:</b> {loss_pct:.2f}%"
                )
                await send_telegram_message(message)
                self.position = None
                return True
        
        except Exception as e:
            logger.error(f"❌ Error ejecutando señal {signal}: {e}")
            await send_telegram_message(f"❌ Error ejecutando trade: {e}")
            return False
    
    async def run(self):
        """
        Loop principal del bot
        """
        logger.info("🤖 Bot iniciado - Esperando señales...")
        await send_telegram_message(
            f"🤖 <b>Bot iniciado correctamente</b>\n"
            f"<b>Símbolo:</b> {SYMBOL}\n"
            f"<b>Timeframe:</b> {TIMEFRAME}\n"
            f"<b>DRY RUN:</b> {DRY_RUN}"
        )
        
        try:
            while True:
                try:
                    # Analizar mercado
                    analysis = await self.analyze_market()
                    
                    if analysis:
                        # Generar señal
                        signal = self.generate_signal(analysis)
                        
                        # Ejecutar si hay señal
                        if signal:
                            await self.execute_signal(signal, analysis)
                    
                    # Esperar antes del siguiente check
                    await asyncio.sleep(CHECK_INTERVAL)
                
                except Exception as e:
                    logger.error(f"❌ Error en loop: {e}")
                    await send_telegram_message(f"⚠️ Error en loop: {e}")
                    await asyncio.sleep(CHECK_INTERVAL)
        
        except KeyboardInterrupt:
            logger.info("⛔ Bot detenido por usuario")
            await send_telegram_message("⛔ Bot detenido")
        except Exception as e:
            logger.error(f"❌ Error fatal: {e}")
            await send_telegram_message(f"❌ Error fatal: {e}")
        finally:
            await self.bingx.close()


async def main():
    """Punto de entrada principal"""
    bot = TradingBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
