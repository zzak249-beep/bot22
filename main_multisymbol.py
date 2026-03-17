#!/usr/bin/env python3
"""
Trading Bot Multi-Símbolo - Analiza TODAS las monedas de BingX
Estrategia: Liquidity Day + Linear Regression Channel
"""

import asyncio
import logging
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from config import (
    BINGX_API_KEY,
    BINGX_SECRET_KEY,
    TIMEFRAME,
    POSITION_SIZE,
    TAKE_PROFIT_PERCENT,
    STOP_LOSS_PERCENT,
    LINREG_LENGTH,
    LINREG_MULT,
    CHECK_INTERVAL,
    DRY_RUN,
    ENABLE_TRADING,
    MAX_OPEN_POSITIONS,
)
from indicators import LinearRegressionChannel, LiquidityLevels
from bingx_api import BingXAPI
from telegram_bot import send_telegram_message

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MultiSymbolTradingBot:
    """
    Bot que analiza múltiples símbolos en paralelo
    """
    
    def __init__(self):
        self.bingx = BingXAPI(BINGX_API_KEY, BINGX_SECRET_KEY)
        self.symbols = []  # Lista de símbolos a tradear
        self.indicators = {}  # {symbol: LinearRegressionChannel}
        self.liquidity = {}  # {symbol: LiquidityLevels}
        self.positions = {}  # {symbol: {'position': 'LONG/SHORT', 'entry_price': X}}
        self.signals_history = {}  # {symbol: [signal1, signal2, ...]}
        
        logger.info("🤖 Bot Multi-símbolo inicializado")
    
    async def fetch_all_symbols(self) -> List[str]:
        """
        Obtiene todos los símbolos disponibles en BingX
        Filtra solo los pares USDT principales
        """
        try:
            # Obtener mercados
            markets = await self.bingx.exchange.fetch_markets()
            
            # Filtrar símbolos USDT
            usdt_symbols = []
            for market in markets:
                symbol = market['symbol']
                
                # Criterios de filtro
                if symbol.endswith('/USDT'):
                    # Excluir stablecoins, tokens sin volumen, etc.
                    excluded = ['USDT/USDT', 'BUSD/USDT', 'USDC/USDT', 'DAI/USDT']
                    if symbol not in excluded:
                        usdt_symbols.append(symbol)
            
            # Ordenar alfabéticamente
            usdt_symbols.sort()
            
            logger.info(f"✅ Obtenidos {len(usdt_symbols)} símbolos USDT")
            logger.info(f"   Primeros 10: {', '.join(usdt_symbols[:10])}")
            logger.info(f"   Total: {', '.join(usdt_symbols)}")
            
            return usdt_symbols
        
        except Exception as e:
            logger.error(f"❌ Error obteniendo símbolos: {e}")
            # Retornar los principales por defecto
            return [
                'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
                'ADA/USDT', 'DOGE/USDT', 'MATIC/USDT', 'LTC/USDT', 'DOT/USDT',
                'UNI/USDT', 'LINK/USDT', 'AVAX/USDT', 'FTT/USDT', 'CRO/USDT',
                'NEAR/USDT', 'FIL/USDT', 'ATOM/USDT', 'SAND/USDT', 'MANA/USDT'
            ]
    
    async def initialize_symbols(self):
        """
        Inicializa los símbolos a analizar
        """
        self.symbols = await self.fetch_all_symbols()
        
        # Crear indicadores para cada símbolo
        for symbol in self.symbols:
            self.indicators[symbol] = LinearRegressionChannel(
                LINREG_LENGTH, 
                LINREG_MULT
            )
            self.liquidity[symbol] = LiquidityLevels()
            self.signals_history[symbol] = []
        
        logger.info(f"🎯 Analizando {len(self.symbols)} símbolos")
        await send_telegram_message(
            f"🎯 Bot iniciado analizando {len(self.symbols)} símbolos USDT\n"
            f"Timeframe: {TIMEFRAME}\n"
            f"TP: {TAKE_PROFIT_PERCENT}% | SL: {STOP_LOSS_PERCENT}%"
        )
    
    async def analyze_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Analiza un símbolo individual
        """
        try:
            # Obtener datos
            ohlcv = await self.bingx.fetch_ohlcv(symbol, TIMEFRAME, 200)
            
            if len(ohlcv) < max(LINREG_LENGTH, 50):
                return None
            
            # Extraer precios
            closes = np.array([candle[4] for candle in ohlcv])
            price = float(closes[-1])
            
            # Calcular indicadores
            linreg_data = self.indicators[symbol].calculate(closes)
            liquidity_data = self.liquidity[symbol].calculate(ohlcv)
            
            if not linreg_data:
                return None
            
            return {
                'symbol': symbol,
                'price': price,
                'pdh': liquidity_data['pdh'],
                'pdl': liquidity_data['pdl'],
                'basis': linreg_data['basis'][0],
                'upper': linreg_data['upper'][0],
                'lower': linreg_data['lower'][0],
                'is_bullish': linreg_data['is_bullish'][0],
                'trend_up': linreg_data['trend_up'][0],
                'trend_down': linreg_data['trend_down'][0],
            }
        
        except Exception as e:
            logger.debug(f"Error analizando {symbol}: {e}")
            return None
    
    def generate_signal(self, analysis: Dict) -> Optional[str]:
        """
        Genera señal para un símbolo
        """
        symbol = analysis['symbol']
        
        # Si ya hay posición abierta, no generar nueva señal
        if symbol in self.positions:
            return None
        
        price = analysis['price']
        pdh = analysis['pdh']
        pdl = analysis['pdl']
        basis = analysis['basis']
        is_bullish = analysis['is_bullish']
        trend_up = analysis['trend_up']
        trend_down = analysis['trend_down']
        
        signal = None
        
        # BUY: Precio > PDH + Tendencia alcista
        if price > pdh and is_bullish and trend_up:
            logger.info(f"🟢 {symbol}: BUY signal - Price {price:.4f} > PDH {pdh:.4f}")
            signal = 'BUY'
        
        # SELL: Precio < PDL + Tendencia bajista
        elif price < pdl and not is_bullish and trend_down:
            logger.info(f"🔴 {symbol}: SELL signal - Price {price:.4f} < PDL {pdl:.4f}")
            signal = 'SELL'
        
        if signal:
            self.signals_history[symbol].append({
                'signal': signal,
                'price': price,
                'time': datetime.now()
            })
        
        return signal
    
    async def execute_signal(self, signal: str, analysis: Dict) -> bool:
        """
        Ejecuta la orden
        """
        symbol = analysis['symbol']
        price = analysis['price']
        
        # Verificar límite de posiciones abiertas
        open_positions = len(self.positions)
        if open_positions >= MAX_OPEN_POSITIONS:
            logger.warning(f"⚠️ Límite de posiciones alcanzado ({MAX_OPEN_POSITIONS})")
            return False
        
        try:
            if signal == 'BUY':
                if DRY_RUN:
                    logger.info(f"📊 [DRY RUN] BUY {symbol} @ {price:.4f}")
                else:
                    if not ENABLE_TRADING:
                        return False
                    
                    order = await self.bingx.create_order(symbol, 'BUY', POSITION_SIZE)
                    if not order:
                        return False
                
                self.positions[symbol] = {
                    'position': 'LONG',
                    'entry_price': price,
                    'entry_time': datetime.now()
                }
                
                tp_price = price * (1 + TAKE_PROFIT_PERCENT / 100)
                sl_price = price * (1 - STOP_LOSS_PERCENT / 100)
                
                message = (
                    f"🟢 <b>{symbol} LONG</b>\n"
                    f"Entrada: ${price:.4f}\n"
                    f"TP: ${tp_price:.4f}\n"
                    f"SL: ${sl_price:.4f}\n"
                    f"Qty: {POSITION_SIZE}"
                )
                await send_telegram_message(message)
                return True
            
            elif signal == 'SELL':
                if DRY_RUN:
                    logger.info(f"📊 [DRY RUN] SELL {symbol} @ {price:.4f}")
                else:
                    if not ENABLE_TRADING:
                        return False
                    
                    order = await self.bingx.create_order(symbol, 'SELL', POSITION_SIZE)
                    if not order:
                        return False
                
                self.positions[symbol] = {
                    'position': 'SHORT',
                    'entry_price': price,
                    'entry_time': datetime.now()
                }
                
                tp_price = price * (1 - TAKE_PROFIT_PERCENT / 100)
                sl_price = price * (1 + STOP_LOSS_PERCENT / 100)
                
                message = (
                    f"🔴 <b>{symbol} SHORT</b>\n"
                    f"Entrada: ${price:.4f}\n"
                    f"TP: ${tp_price:.4f}\n"
                    f"SL: ${sl_price:.4f}\n"
                    f"Qty: {POSITION_SIZE}"
                )
                await send_telegram_message(message)
                return True
        
        except Exception as e:
            logger.error(f"❌ Error ejecutando trade en {symbol}: {e}")
            return False
    
    async def check_positions(self):
        """
        Verifica y cierra posiciones con TP/SL
        """
        symbols_to_close = []
        
        for symbol, pos in self.positions.items():
            try:
                price = await self.bingx.get_current_price(symbol)
                if not price:
                    continue
                
                entry = pos['entry_price']
                tp = entry * (1 + TAKE_PROFIT_PERCENT / 100) if pos['position'] == 'LONG' else entry * (1 - TAKE_PROFIT_PERCENT / 100)
                sl = entry * (1 - STOP_LOSS_PERCENT / 100) if pos['position'] == 'LONG' else entry * (1 + STOP_LOSS_PERCENT / 100)
                
                close_reason = None
                
                if pos['position'] == 'LONG':
                    if price >= tp:
                        close_reason = 'TP'
                    elif price <= sl:
                        close_reason = 'SL'
                
                elif pos['position'] == 'SHORT':
                    if price <= tp:
                        close_reason = 'TP'
                    elif price >= sl:
                        close_reason = 'SL'
                
                if close_reason:
                    symbols_to_close.append((symbol, pos['position'], price, close_reason))
            
            except Exception as e:
                logger.error(f"Error checking position {symbol}: {e}")
        
        # Cerrar posiciones
        for symbol, position_type, price, reason in symbols_to_close:
            try:
                entry = self.positions[symbol]['entry_price']
                
                if DRY_RUN:
                    logger.info(f"📊 [DRY RUN] CLOSE {symbol} {reason} @ {price:.4f}")
                else:
                    if position_type == 'LONG':
                        await self.bingx.create_order(symbol, 'SELL', POSITION_SIZE)
                    else:
                        await self.bingx.create_order(symbol, 'BUY', POSITION_SIZE)
                
                profit_pct = ((price - entry) / entry * 100) if position_type == 'LONG' else ((entry - price) / entry * 100)
                
                message = (
                    f"✅ {symbol} {reason}\n"
                    f"Entrada: ${entry:.4f}\n"
                    f"Salida: ${price:.4f}\n"
                    f"Ganancia: {profit_pct:+.2f}%"
                )
                await send_telegram_message(message)
                
                del self.positions[symbol]
            
            except Exception as e:
                logger.error(f"Error cerrando {symbol}: {e}")
    
    async def analyze_all_symbols(self) -> List[Dict]:
        """
        Analiza todos los símbolos en paralelo
        """
        tasks = [self.analyze_symbol(symbol) for symbol in self.symbols]
        results = await asyncio.gather(*tasks)
        
        # Filtrar None
        valid_results = [r for r in results if r is not None]
        
        logger.info(f"📊 Analizados {len(valid_results)}/{len(self.symbols)} símbolos exitosamente")
        
        return valid_results
    
    async def run(self):
        """
        Loop principal
        """
        logger.info("🚀 Inicializando símbolos...")
        await self.initialize_symbols()
        
        logger.info("🤖 Bot multi-símbolo iniciado - Analizando mercados...")
        
        try:
            cycle = 0
            while True:
                cycle += 1
                logger.info(f"\n📈 Ciclo #{cycle} - {datetime.now().strftime('%H:%M:%S')}")
                
                # Analizar todos los símbolos
                analyses = await self.analyze_all_symbols()
                
                # Generar y ejecutar señales
                for analysis in analyses:
                    signal = self.generate_signal(analysis)
                    if signal:
                        await self.execute_signal(signal, analysis)
                
                # Verificar posiciones abiertas
                await self.check_positions()
                
                # Resumen
                logger.info(
                    f"📊 Estado: {len(self.positions)} posición(es) abierta(s) "
                    f"| Siguientes análisis en {CHECK_INTERVAL}s"
                )
                
                # Esperar
                await asyncio.sleep(CHECK_INTERVAL)
        
        except KeyboardInterrupt:
            logger.info("⛔ Bot detenido")
            await send_telegram_message("⛔ Bot detenido por usuario")
        except Exception as e:
            logger.error(f"❌ Error fatal: {e}")
            await send_telegram_message(f"❌ Error fatal: {e}")


async def main():
    bot = MultiSymbolTradingBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
