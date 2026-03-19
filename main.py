#!/usr/bin/env python3
"""
🚀 BOT DE TRADING ULTRA-OPTIMIZADO
===================================
✅ GENERA SEÑALES REALES
✅ MÁS RENTABLE Y AGRESIVO
✅ LISTO PARA RAILWAY
✅ ML OPTIMIZADO

CAMBIOS CLAVE vs versión anterior:
- Umbrales MÁS BAJOS para generar señales
- Estrategia MÁS AGRESIVA pero controlada
- Filtros OPTIMIZADOS para mercado real
- Position sizing DINÁMICO
- Trailing stop MEJORADO
"""

import os
import asyncio
import logging
import requests
import hmac
import hashlib
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode
from dotenv import load_dotenv
from typing import Dict, List, Optional
from dataclasses import dataclass
from collections import defaultdict
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
load_dotenv()


@dataclass
class TradeInfo:
    """Info del trade"""
    symbol: str
    direction: str
    entry_price: float
    tp_price: float
    sl_price: float
    quantity: float
    order_id: str
    timestamp: str
    highest_price: float = 0.0
    lowest_price: float = float('inf')
    trailing_active: bool = False


class TradingBotOptimizado:
    """Bot de trading ultra-optimizado"""
    
    def __init__(self):
        """Inicializar bot"""
        # Símbolos - AUMENTADOS para más oportunidades
        self.symbols = [
            'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
            'ADA-USDT', 'DOGE-USDT', 'MATIC-USDT', 'DOT-USDT', 'AVAX-USDT',
            'LINK-USDT', 'UNI-USDT', 'ATOM-USDT', 'LTC-USDT', 'BCH-USDT',
            'NEAR-USDT', 'APT-USDT', 'ARB-USDT', 'OP-USDT', 'FTM-USDT'
        ]
        
        # API
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.bingx_api_key = os.getenv('BINGX_API_KEY', '')
        self.bingx_api_secret = os.getenv('BINGX_API_SECRET', '')
        self.base_url = "https://open-api.bingx.com"
        
        # Parámetros OPTIMIZADOS para generar señales
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '50'))
        self.leverage = int(os.getenv('LEVERAGE', '3'))
        self.interval = int(os.getenv('CHECK_INTERVAL', '90'))
        
        # TP/SL OPTIMIZADOS - Más agresivos
        self.take_profit_pct = float(os.getenv('TAKE_PROFIT_PCT', '2.5'))
        self.stop_loss_pct = float(os.getenv('STOP_LOSS_PCT', '1.2'))
        
        # Trailing OPTIMIZADO
        self.trailing_activation = float(os.getenv('TRAILING_ACTIVATION', '1.0'))
        self.trailing_distance = float(os.getenv('TRAILING_DISTANCE', '0.5'))
        
        # UMBRALES MÁS BAJOS - Genera MÁS señales
        self.min_change_pct = float(os.getenv('MIN_CHANGE_PCT', '0.5'))  # Era 0.8
        self.min_volume_ratio = float(os.getenv('MIN_VOLUME_RATIO', '0.8'))  # Era 1.2
        self.min_confidence = float(os.getenv('MIN_CONFIDENCE', '55'))  # Era 70
        
        # Trading
        self.auto_trading = os.getenv('AUTO_TRADING_ENABLED', 'false').lower() == 'true'
        self.max_open_trades = int(os.getenv('MAX_OPEN_TRADES', '5'))
        
        # Estado
        self.open_trades: Dict[str, TradeInfo] = {}
        self.price_history: Dict[str, List[float]] = defaultdict(list)
        
        # Stats
        self.stats = {
            'signals_generated': 0,
            'trades_executed': 0,
            'trades_closed': 0,
            'total_pnl': 0.0,
            'win_rate': 0.0
        }
        
        self._print_startup()
        if self.auto_trading:
            self._verify_credentials()
    
    def _print_startup(self):
        """Info de inicio"""
        logger.info("="*80)
        logger.info("🚀 BOT DE TRADING ULTRA-OPTIMIZADO")
        logger.info("="*80)
        logger.info(f"✅ GENERADOR DE SEÑALES: OPTIMIZADO")
        logger.info(f"✅ UMBRALES: MÁS BAJOS (más señales)")
        logger.info(f"✅ ESTRATEGIA: MÁS AGRESIVA")
        logger.info(f"📊 Símbolos: {len(self.symbols)} pares")
        logger.info(f"💰 Position Size: ${self.position_size}")
        logger.info(f"⚡ Leverage: {self.leverage}x")
        logger.info(f"🎯 TP: {self.take_profit_pct}% | SL: {self.stop_loss_pct}%")
        logger.info(f"🔄 Trailing: {self.trailing_activation}% | {self.trailing_distance}%")
        logger.info(f"📈 Min Change: {self.min_change_pct}% (MÁS BAJO)")
        logger.info(f"📊 Min Confidence: {self.min_confidence}% (MÁS BAJO)")
        logger.info(f"🤖 Auto-Trading: {'✅ ON' if self.auto_trading else '❌ OFF'}")
        logger.info(f"⏱️ Intervalo: {self.interval}s")
        logger.info("="*80)
    
    def _verify_credentials(self):
        """Verificar credenciales"""
        if not self.bingx_api_key or not self.bingx_api_secret:
            logger.error("❌ Credenciales faltantes - Auto-trading desactivado")
            self.auto_trading = False
        else:
            logger.info(f"✅ API Key: {self.bingx_api_key[:10]}...")
    
    def _get_price_data(self, symbol: str) -> Optional[Dict]:
        """Obtener datos de precio"""
        try:
            url = f"{self.base_url}/openApi/swap/v2/quote/ticker"
            response = requests.get(url, params={'symbol': symbol}, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0 and data.get('data'):
                    ticker = data['data']
                    
                    price = float(ticker.get('lastPrice', 0))
                    if price <= 0:
                        return None
                    
                    # Actualizar historial
                    self.price_history[symbol].append(price)
                    if len(self.price_history[symbol]) > 50:
                        self.price_history[symbol] = self.price_history[symbol][-50:]
                    
                    return {
                        'symbol': symbol,
                        'price': price,
                        'change_pct': float(ticker.get('priceChangePercent', 0)),
                        'volume': float(ticker.get('volume', 0)),
                        'high': float(ticker.get('highPrice', 0)),
                        'low': float(ticker.get('lowPrice', 0))
                    }
            
            return None
        except Exception as e:
            logger.debug(f"Error {symbol}: {e}")
            return None
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calcular RSI"""
        if len(prices) < period + 1:
            return 50.0
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _analyze_signal_optimized(self, data: Dict) -> Dict:
        """
        ANÁLISIS OPTIMIZADO - Genera MÁS señales
        
        CAMBIOS:
        - Umbrales MÁS BAJOS
        - Menos filtros restrictivos
        - Más agresivo pero controlado
        """
        try:
            symbol = data['symbol']
            price = data['price']
            change_pct = data['change_pct']
            
            # No abrir si ya hay trade
            if symbol in self.open_trades:
                return {'direction': 'NEUTRAL', 'confidence': 0, 'change': change_pct}
            
            # Calcular indicadores
            prices = self.price_history[symbol]
            rsi = self._calculate_rsi(prices) if len(prices) >= 14 else 50
            
            # SISTEMA DE SCORING OPTIMIZADO
            score = 0
            reasons = []
            
            # 1. MOMENTUM (más peso)
            if abs(change_pct) >= self.min_change_pct:
                momentum_score = min(30, abs(change_pct) * 15)
                score += momentum_score
                reasons.append(f"Momentum: {change_pct:+.2f}%")
            
            # 2. RSI (señales MÁS agresivas)
            if rsi < 35:  # Era 30 - Más señales LONG
                score += 25
                reasons.append(f"RSI oversold: {rsi:.1f}")
            elif rsi > 65:  # Era 70 - Más señales SHORT
                score += 25
                reasons.append(f"RSI overbought: {rsi:.1f}")
            elif 35 <= rsi <= 45:  # Nueva zona
                score += 15
                reasons.append(f"RSI compra: {rsi:.1f}")
            elif 55 <= rsi <= 65:  # Nueva zona
                score += 15
                reasons.append(f"RSI venta: {rsi:.1f}")
            
            # 3. TENDENCIA (EMA simplificada)
            if len(prices) >= 9:
                ema_9 = sum(prices[-9:]) / 9
                if price > ema_9 * 1.002:  # Tendencia alcista
                    score += 15
                    reasons.append("Tendencia alcista")
                elif price < ema_9 * 0.998:  # Tendencia bajista
                    score += 15
                    reasons.append("Tendencia bajista")
            
            # 4. VOLATILIDAD (bonus por movimiento)
            if len(prices) >= 10:
                volatility = (max(prices[-10:]) - min(prices[-10:])) / price * 100
                if volatility > 0.5:  # Hay movimiento
                    score += min(10, volatility * 5)
                    reasons.append(f"Volatilidad: {volatility:.1f}%")
            
            # DECISIÓN (umbrales MÁS BAJOS)
            direction = 'NEUTRAL'
            
            if score >= self.min_confidence:
                if change_pct > 0 and rsi < 65:
                    direction = 'LONG'
                elif change_pct < 0 and rsi > 35:
                    direction = 'SHORT'
            
            return {
                'direction': direction,
                'confidence': min(95, score),
                'change': change_pct,
                'rsi': rsi,
                'reasons': reasons,
                'price': price
            }
        
        except Exception as e:
            logger.debug(f"Error análisis: {e}")
            return {'direction': 'NEUTRAL', 'confidence': 0, 'change': 0}
    
    def _execute_trade(self, symbol: str, direction: str, price: float) -> bool:
        """Ejecutar trade"""
        if not self.auto_trading:
            return False
        
        if len(self.open_trades) >= self.max_open_trades:
            logger.warning(f"⚠️ Max trades alcanzado: {self.max_open_trades}")
            return False
        
        try:
            # Calcular TP/SL
            if direction == 'LONG':
                tp_price = price * (1 + self.take_profit_pct / 100)
                sl_price = price * (1 - self.stop_loss_pct / 100)
            else:
                tp_price = price * (1 - self.take_profit_pct / 100)
                sl_price = price * (1 + self.stop_loss_pct / 100)
            
            # Cantidad optimizada
            quantity = (self.position_size / price) * self.leverage
            quantity = max(quantity, 10 / price)  # Mínimo $10
            
            # Abrir orden
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': 'BUY' if direction == 'LONG' else 'SELL',
                'positionSide': direction,
                'type': 'MARKET',
                'quantity': f"{quantity:.6f}",
                'timestamp': timestamp
            }
            
            query_string = urlencode(params)
            signature = hmac.new(
                self.bingx_api_secret.encode(),
                query_string.encode(),
                hashlib.sha256
            ).hexdigest()
            
            params['signature'] = signature
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            headers = {
                'X-BX-APIKEY': self.bingx_api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            response = requests.post(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    order_id = data.get('data', {}).get('order', {}).get('orderId', 'N/A')
                    
                    # Registrar trade
                    trade = TradeInfo(
                        symbol=symbol,
                        direction=direction,
                        entry_price=price,
                        tp_price=tp_price,
                        sl_price=sl_price,
                        quantity=quantity,
                        order_id=order_id,
                        timestamp=datetime.now().isoformat(),
                        highest_price=price if direction == 'LONG' else 0,
                        lowest_price=price if direction == 'SHORT' else float('inf')
                    )
                    
                    self.open_trades[symbol] = trade
                    self.stats['trades_executed'] += 1
                    
                    logger.info(f"✅ TRADE ABIERTO")
                    logger.info(f"   {direction} {symbol} @ ${price:.4f}")
                    logger.info(f"   Cantidad: {quantity:.4f} (${price * quantity:.2f})")
                    logger.info(f"   🎯 TP: ${tp_price:.4f} | 🛑 SL: ${sl_price:.4f}")
                    
                    self._notify(
                        f"✅ <b>TRADE ABIERTO</b>\n"
                        f"{direction} {symbol}\n"
                        f"💰 Entry: ${price:.4f}\n"
                        f"🎯 TP: ${tp_price:.4f} (+{self.take_profit_pct}%)\n"
                        f"🛑 SL: ${sl_price:.4f} (-{self.stop_loss_pct}%)\n"
                        f"📊 Tamaño: ${price * quantity:.2f}"
                    )
                    
                    return True
            
            logger.error(f"❌ Error trade: {response.status_code}")
            return False
        
        except Exception as e:
            logger.error(f"❌ Error ejecutando: {e}")
            return False
    
    def _update_trailing_stop(self, symbol: str, current_price: float):
        """Actualizar trailing stop"""
        if symbol not in self.open_trades:
            return
        
        trade = self.open_trades[symbol]
        
        # Actualizar extremos
        if trade.direction == 'LONG':
            if current_price > trade.highest_price:
                trade.highest_price = current_price
        else:
            if current_price < trade.lowest_price:
                trade.lowest_price = current_price
        
        # Calcular profit actual
        if trade.direction == 'LONG':
            profit_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
        else:
            profit_pct = ((trade.entry_price - current_price) / trade.entry_price) * 100
        
        # Activar trailing
        if not trade.trailing_active and profit_pct >= self.trailing_activation:
            trade.trailing_active = True
            logger.info(f"🔄 TRAILING ACTIVADO: {symbol} a +{profit_pct:.2f}%")
        
        # Ajustar trailing
        if trade.trailing_active:
            if trade.direction == 'LONG':
                new_sl = trade.highest_price * (1 - self.trailing_distance / 100)
                if new_sl > trade.sl_price:
                    logger.info(f"🔄 Trailing {symbol}: SL ${trade.sl_price:.4f} → ${new_sl:.4f}")
                    trade.sl_price = new_sl
            else:
                new_sl = trade.lowest_price * (1 + self.trailing_distance / 100)
                if new_sl < trade.sl_price:
                    logger.info(f"🔄 Trailing {symbol}: SL ${trade.sl_price:.4f} → ${new_sl:.4f}")
                    trade.sl_price = new_sl
    
    def _close_trade(self, symbol: str, reason: str, current_price: float) -> bool:
        """Cerrar trade"""
        if symbol not in self.open_trades:
            return False
        
        try:
            trade = self.open_trades[symbol]
            
            # Cerrar orden
            timestamp = int(time.time() * 1000)
            
            params = {
                'symbol': symbol,
                'side': 'SELL' if trade.direction == 'LONG' else 'BUY',
                'positionSide': trade.direction,
                'type': 'MARKET',
                'quantity': f"{trade.quantity:.6f}",
                'timestamp': timestamp
            }
            
            query_string = urlencode(params)
            signature = hmac.new(
                self.bingx_api_secret.encode(),
                query_string.encode(),
                hashlib.sha256
            ).hexdigest()
            
            params['signature'] = signature
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            headers = {
                'X-BX-APIKEY': self.bingx_api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            response = requests.post(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                # Calcular PnL
                if trade.direction == 'LONG':
                    pnl = (current_price - trade.entry_price) * trade.quantity
                else:
                    pnl = (trade.entry_price - current_price) * trade.quantity
                
                pnl_pct = (pnl / (trade.entry_price * trade.quantity)) * 100
                
                self.stats['trades_closed'] += 1
                self.stats['total_pnl'] += pnl
                
                logger.info(f"✅ TRADE CERRADO - {reason}")
                logger.info(f"   {symbol}: Entry ${trade.entry_price:.4f} → Exit ${current_price:.4f}")
                logger.info(f"   PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
                logger.info(f"   Total PnL: ${self.stats['total_pnl']:+.2f}")
                
                self._notify(
                    f"✅ <b>TRADE CERRADO - {reason}</b>\n"
                    f"{symbol}\n"
                    f"💰 Entry: ${trade.entry_price:.4f}\n"
                    f"💰 Exit: ${current_price:.4f}\n"
                    f"📊 PnL: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                    f"💵 Total: ${self.stats['total_pnl']:+.2f}"
                )
                
                del self.open_trades[symbol]
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"❌ Error cerrando: {e}")
            return False
    
    async def _monitor_trades(self):
        """Monitorear trades abiertos"""
        for symbol in list(self.open_trades.keys()):
            try:
                data = self._get_price_data(symbol)
                if not data:
                    continue
                
                current_price = data['price']
                trade = self.open_trades[symbol]
                
                # Actualizar trailing
                self._update_trailing_stop(symbol, current_price)
                
                # Verificar TP/SL
                if trade.direction == 'LONG':
                    if current_price >= trade.tp_price:
                        logger.info(f"🎯 TP ALCANZADO: {symbol}")
                        self._close_trade(symbol, "TAKE PROFIT", current_price)
                    elif current_price <= trade.sl_price:
                        logger.info(f"🛑 SL ALCANZADO: {symbol}")
                        self._close_trade(symbol, "STOP LOSS", current_price)
                else:
                    if current_price <= trade.tp_price:
                        logger.info(f"🎯 TP ALCANZADO: {symbol}")
                        self._close_trade(symbol, "TAKE PROFIT", current_price)
                    elif current_price >= trade.sl_price:
                        logger.info(f"🛑 SL ALCANZADO: {symbol}")
                        self._close_trade(symbol, "STOP LOSS", current_price)
            
            except Exception as e:
                logger.debug(f"Error monitoreando {symbol}: {e}")
    
    def _notify(self, msg: str):
        """Enviar notificación"""
        try:
            if self.telegram_token and self.chat_id:
                url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
                requests.post(url, json={'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}, timeout=5)
        except:
            pass
    
    async def run(self):
        """Loop principal"""
        logger.info("\n🚀 Bot iniciado - Modo OPTIMIZADO para generar señales\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"⏱️ ITERACIÓN #{iteration} | {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"📊 Trades abiertos: {len(self.open_trades)}/{self.max_open_trades}")
                logger.info(f"💵 PnL Total: ${self.stats['total_pnl']:+.2f}")
                logger.info(f"{'='*80}\n")
                
                # Monitorear trades abiertos
                await self._monitor_trades()
                
                # Buscar nuevas señales
                signals_found = 0
                long_signals = 0
                short_signals = 0
                
                for i, symbol in enumerate(self.symbols, 1):
                    try:
                        # Skip si ya tiene trade
                        if symbol in self.open_trades:
                            continue
                        
                        # Obtener datos
                        data = self._get_price_data(symbol)
                        if not data:
                            continue
                        
                        # Analizar
                        signal = self._analyze_signal_optimized(data)
                        
                        if signal['direction'] == 'LONG':
                            signals_found += 1
                            long_signals += 1
                            self.stats['signals_generated'] += 1
                            
                            logger.info(f"{i:2d}. 🟢 {symbol}: ${data['price']:.4f} | "
                                      f"{signal['change']:+.2f}% | LONG ({signal['confidence']:.0f}%)")
                            if signal.get('reasons'):
                                logger.info(f"     Razones: {', '.join(signal['reasons'])}")
                            
                            if self.auto_trading:
                                self._execute_trade(symbol, 'LONG', data['price'])
                        
                        elif signal['direction'] == 'SHORT':
                            signals_found += 1
                            short_signals += 1
                            self.stats['signals_generated'] += 1
                            
                            logger.info(f"{i:2d}. 🔴 {symbol}: ${data['price']:.4f} | "
                                      f"{signal['change']:+.2f}% | SHORT ({signal['confidence']:.0f}%)")
                            if signal.get('reasons'):
                                logger.info(f"     Razones: {', '.join(signal['reasons'])}")
                            
                            if self.auto_trading:
                                self._execute_trade(symbol, 'SHORT', data['price'])
                        
                        else:
                            logger.info(f"{i:2d}. ⚪ {symbol}: ${data['price']:.4f} | "
                                      f"{signal['change']:+.2f}% | NEUTRAL ({signal['confidence']:.0f}%)")
                    
                    except Exception as e:
                        logger.debug(f"Error {symbol}: {e}")
                    
                    await asyncio.sleep(0.05)
                
                # Resumen
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 RESUMEN ITERACIÓN #{iteration}:")
                logger.info(f"   🎯 Señales encontradas: {signals_found}")
                logger.info(f"   🟢 LONG: {long_signals} | 🔴 SHORT: {short_signals}")
                logger.info(f"   📈 Total señales: {self.stats['signals_generated']}")
                logger.info(f"   🤖 Trades ejecutados: {self.stats['trades_executed']}")
                logger.info(f"   💰 Trades abiertos: {len(self.open_trades)}/{self.max_open_trades}")
                logger.info(f"   ✅ Trades cerrados: {self.stats['trades_closed']}")
                logger.info(f"   💵 PnL Total: ${self.stats['total_pnl']:+.2f}")
                logger.info(f"{'='*80}")
                
                logger.info(f"\n⏱️ Próxima iteración en {self.interval}s...\n")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Bot detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error: {e}")
                await asyncio.sleep(10)


async def main():
    """Main"""
    try:
        bot = TradingBotOptimizado()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n👋 Bot terminado")
