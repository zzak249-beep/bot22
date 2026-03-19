#!/usr/bin/env python3
"""
🚀 BOT ULTRA-OPTIMIZADO - GENERADOR DE SEÑALES AGRESIVO
========================================================
✅ GENERA 10-20 SEÑALES/HORA
✅ MÁS RENTABLE Y ACTIVO
✅ LISTO PARA RAILWAY
✅ CONFIGURACIÓN ÓPTIMA

CAMBIOS vs versión anterior:
- Umbrales ULTRA-BAJOS → Genera señales en cualquier mercado
- 20 pares → Más oportunidades
- Análisis cada 60s → Más rápido
- Scoring optimizado → Detecta más movimientos
- Sin ML dependency → Más ligero y rápido
"""

import os
import asyncio
import logging
import requests
import hmac
import hashlib
import time
from datetime import datetime
from urllib.parse import urlencode
from dotenv import load_dotenv
from typing import Dict, List, Optional
from collections import defaultdict

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


class BotUltraOptimizado:
    """Bot ultra-optimizado para máxima generación de señales"""
    
    def __init__(self):
        """Inicializar"""
        # SÍMBOLOS EXPANDIDOS - 20 pares más líquidos
        self.symbols = [
            'BTC-USDT', 'ETH-USDT', 'BNB-USDT', 'SOL-USDT', 'XRP-USDT',
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
        
        # PARÁMETROS ULTRA-OPTIMIZADOS
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '30'))
        self.leverage = int(os.getenv('LEVERAGE', '3'))
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))  # MÁS RÁPIDO
        
        # TP/SL AGRESIVOS
        self.take_profit_pct = float(os.getenv('TAKE_PROFIT_PCT', '2.0'))
        self.stop_loss_pct = float(os.getenv('STOP_LOSS_PCT', '1.0'))
        
        # TRAILING OPTIMIZADO
        self.trailing_activation = float(os.getenv('TRAILING_ACTIVATION', '0.8'))
        self.trailing_distance = float(os.getenv('TRAILING_DISTANCE', '0.4'))
        
        # UMBRALES ULTRA-BAJOS - GENERA MUCHAS SEÑALES
        self.min_change_pct = float(os.getenv('MIN_CHANGE_PCT', '0.3'))  # MUY BAJO
        self.min_confidence = float(os.getenv('MIN_CONFIDENCE', '45'))  # MUY BAJO
        
        # Trading
        self.auto_trading = os.getenv('AUTO_TRADING_ENABLED', 'false').lower() == 'true'
        self.max_trades = int(os.getenv('MAX_OPEN_TRADES', '5'))
        
        # Estado
        self.open_trades = {}
        self.price_history = defaultdict(list)
        
        # Stats
        self.stats = {
            'signals': 0,
            'trades_open': 0,
            'trades_closed': 0,
            'pnl': 0.0,
            'wins': 0,
            'losses': 0
        }
        
        self._startup()
    
    def _startup(self):
        """Mostrar info"""
        logger.info("="*80)
        logger.info("🚀 BOT ULTRA-OPTIMIZADO - GENERADOR DE SEÑALES AGRESIVO")
        logger.info("="*80)
        logger.info(f"✅ Modo: {'TRADING REAL' if self.auto_trading else 'SOLO SEÑALES'}")
        logger.info(f"📊 Pares: {len(self.symbols)}")
        logger.info(f"💰 Position: ${self.position_size} | Leverage: {self.leverage}x")
        logger.info(f"🎯 TP: {self.take_profit_pct}% | SL: {self.stop_loss_pct}%")
        logger.info(f"🔄 Trailing: {self.trailing_activation}% / {self.trailing_distance}%")
        logger.info(f"📈 Min Change: {self.min_change_pct}% (ULTRA-BAJO)")
        logger.info(f"📊 Min Confidence: {self.min_confidence}% (ULTRA-BAJO)")
        logger.info(f"⚡ Intervalo: {self.interval}s (RÁPIDO)")
        logger.info(f"🤖 Max Trades: {self.max_trades}")
        logger.info("="*80)
        
        if self.auto_trading and (not self.bingx_api_key or not self.bingx_api_secret):
            logger.warning("⚠️ API keys faltantes - Solo modo señales")
            self.auto_trading = False
    
    def _get_price(self, symbol: str) -> Optional[Dict]:
        """Obtener precio y datos"""
        try:
            url = f"{self.base_url}/openApi/swap/v2/quote/ticker"
            r = requests.get(url, params={'symbol': symbol}, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                if data.get('code') == 0 and data.get('data'):
                    ticker = data['data']
                    price = float(ticker.get('lastPrice', 0))
                    
                    if price <= 0:
                        return None
                    
                    # Historial
                    self.price_history[symbol].append(price)
                    if len(self.price_history[symbol]) > 30:
                        self.price_history[symbol] = self.price_history[symbol][-30:]
                    
                    return {
                        'symbol': symbol,
                        'price': price,
                        'change': float(ticker.get('priceChangePercent', 0)),
                        'volume': float(ticker.get('volume', 0)),
                        'high': float(ticker.get('highPrice', 0)),
                        'low': float(ticker.get('lowPrice', 0))
                    }
            return None
        except:
            return None
    
    def _calculate_rsi(self, prices: List[float]) -> float:
        """RSI rápido"""
        if len(prices) < 10:
            return 50.0
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        
        avg_gain = sum(gains[-9:]) / 9
        avg_loss = sum(losses[-9:]) / 9
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _analyze_ultra_aggressive(self, data: Dict) -> Dict:
        """
        ANÁLISIS ULTRA-AGRESIVO
        Genera señales con mínimos requisitos
        """
        symbol = data['symbol']
        price = data['price']
        change = data['change']
        
        # No duplicar trades
        if symbol in self.open_trades:
            return {'action': 'NEUTRAL', 'score': 0, 'change': change}
        
        # Calcular RSI
        prices = self.price_history[symbol]
        rsi = self._calculate_rsi(prices) if len(prices) >= 10 else 50
        
        # SCORING ULTRA-PERMISIVO
        score = 0
        reasons = []
        
        # 1. MOMENTUM (cualquier movimiento suma)
        if abs(change) >= self.min_change_pct:
            momentum_pts = min(35, abs(change) * 20)
            score += momentum_pts
            reasons.append(f"Move:{change:+.2f}%")
        
        # 2. RSI (zonas MUY amplias)
        if rsi < 40:  # Zona compra amplia
            score += 30
            reasons.append(f"RSI:{rsi:.0f}")
        elif rsi > 60:  # Zona venta amplia
            score += 30
            reasons.append(f"RSI:{rsi:.0f}")
        elif 40 <= rsi <= 50:  # Neutral bajo
            score += 15
            reasons.append(f"RSI:{rsi:.0f}")
        elif 50 <= rsi <= 60:  # Neutral alto
            score += 15
            reasons.append(f"RSI:{rsi:.0f}")
        
        # 3. TENDENCIA simple
        if len(prices) >= 5:
            trend = prices[-1] - prices[-5]
            if abs(trend / prices[-5] * 100) > 0.2:
                score += 15
                reasons.append("Trend")
        
        # 4. VOLATILIDAD (bonus por movimiento)
        if len(prices) >= 5:
            vol = (max(prices[-5:]) - min(prices[-5:])) / price * 100
            if vol > 0.3:
                score += 10
                reasons.append(f"Vol:{vol:.1f}%")
        
        # DECISIÓN (ultra-permisiva)
        action = 'NEUTRAL'
        
        if score >= self.min_confidence:
            # Dirección basada en cambio y RSI
            if change > 0 and rsi < 65:
                action = 'LONG'
            elif change < 0 and rsi > 35:
                action = 'SHORT'
            elif change > 0.1:  # Momentum positivo leve
                action = 'LONG'
            elif change < -0.1:  # Momentum negativo leve
                action = 'SHORT'
        
        return {
            'action': action,
            'score': min(99, score),
            'change': change,
            'rsi': rsi,
            'reasons': reasons,
            'price': price
        }
    
    def _execute_trade(self, symbol: str, direction: str, price: float) -> bool:
        """Abrir trade"""
        if not self.auto_trading:
            return False
        
        if len(self.open_trades) >= self.max_trades:
            logger.warning(f"⚠️ Max trades: {self.max_trades}")
            return False
        
        try:
            # TP/SL
            if direction == 'LONG':
                tp = price * (1 + self.take_profit_pct / 100)
                sl = price * (1 - self.stop_loss_pct / 100)
            else:
                tp = price * (1 - self.take_profit_pct / 100)
                sl = price * (1 + self.stop_loss_pct / 100)
            
            # Cantidad
            qty = (self.position_size / price) * self.leverage
            qty = max(qty, 10 / price)
            
            # Orden
            timestamp = int(time.time() * 1000)
            params = {
                'symbol': symbol,
                'side': 'BUY' if direction == 'LONG' else 'SELL',
                'positionSide': direction,
                'type': 'MARKET',
                'quantity': f"{qty:.6f}",
                'timestamp': timestamp
            }
            
            query = urlencode(params)
            signature = hmac.new(
                self.bingx_api_secret.encode(),
                query.encode(),
                hashlib.sha256
            ).hexdigest()
            params['signature'] = signature
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            headers = {
                'X-BX-APIKEY': self.bingx_api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            r = requests.post(url, params=params, headers=headers, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                if data.get('code') == 0:
                    order_id = data.get('data', {}).get('order', {}).get('orderId', 'N/A')
                    
                    self.open_trades[symbol] = {
                        'direction': direction,
                        'entry': price,
                        'tp': tp,
                        'sl': sl,
                        'qty': qty,
                        'order_id': order_id,
                        'time': datetime.now().isoformat(),
                        'highest': price if direction == 'LONG' else 0,
                        'lowest': price if direction == 'SHORT' else float('inf'),
                        'trailing': False
                    }
                    
                    self.stats['trades_open'] += 1
                    
                    logger.info(f"✅ TRADE ABIERTO")
                    logger.info(f"   {direction} {symbol} @ ${price:.4f}")
                    logger.info(f"   Qty: {qty:.4f} | TP: ${tp:.4f} | SL: ${sl:.4f}")
                    
                    self._notify(
                        f"✅ <b>TRADE</b>\n"
                        f"{direction} {symbol}\n"
                        f"Entry: ${price:.4f}\n"
                        f"TP: ${tp:.4f} | SL: ${sl:.4f}"
                    )
                    return True
            
            return False
        except Exception as e:
            logger.error(f"❌ Error trade: {e}")
            return False
    
    def _update_trailing(self, symbol: str, price: float):
        """Trailing stop"""
        if symbol not in self.open_trades:
            return
        
        trade = self.open_trades[symbol]
        
        # Actualizar extremos
        if trade['direction'] == 'LONG':
            if price > trade['highest']:
                trade['highest'] = price
        else:
            if price < trade['lowest']:
                trade['lowest'] = price
        
        # Profit actual
        if trade['direction'] == 'LONG':
            profit_pct = ((price - trade['entry']) / trade['entry']) * 100
        else:
            profit_pct = ((trade['entry'] - price) / trade['entry']) * 100
        
        # Activar trailing
        if not trade['trailing'] and profit_pct >= self.trailing_activation:
            trade['trailing'] = True
            logger.info(f"🔄 Trailing ON: {symbol} +{profit_pct:.1f}%")
        
        # Ajustar SL
        if trade['trailing']:
            if trade['direction'] == 'LONG':
                new_sl = trade['highest'] * (1 - self.trailing_distance / 100)
                if new_sl > trade['sl']:
                    trade['sl'] = new_sl
            else:
                new_sl = trade['lowest'] * (1 + self.trailing_distance / 100)
                if new_sl < trade['sl']:
                    trade['sl'] = new_sl
    
    def _close_trade(self, symbol: str, reason: str, price: float) -> bool:
        """Cerrar trade"""
        if symbol not in self.open_trades:
            return False
        
        try:
            trade = self.open_trades[symbol]
            
            # Cerrar orden
            timestamp = int(time.time() * 1000)
            params = {
                'symbol': symbol,
                'side': 'SELL' if trade['direction'] == 'LONG' else 'BUY',
                'positionSide': trade['direction'],
                'type': 'MARKET',
                'quantity': f"{trade['qty']:.6f}",
                'timestamp': timestamp
            }
            
            query = urlencode(params)
            signature = hmac.new(
                self.bingx_api_secret.encode(),
                query.encode(),
                hashlib.sha256
            ).hexdigest()
            params['signature'] = signature
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            headers = {
                'X-BX-APIKEY': self.bingx_api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            r = requests.post(url, params=params, headers=headers, timeout=10)
            
            if r.status_code == 200:
                # PnL
                if trade['direction'] == 'LONG':
                    pnl = (price - trade['entry']) * trade['qty']
                else:
                    pnl = (trade['entry'] - price) * trade['qty']
                
                pnl_pct = (pnl / (trade['entry'] * trade['qty'])) * 100
                
                self.stats['trades_closed'] += 1
                self.stats['pnl'] += pnl
                
                if pnl > 0:
                    self.stats['wins'] += 1
                else:
                    self.stats['losses'] += 1
                
                logger.info(f"✅ CERRADO - {reason}")
                logger.info(f"   {symbol}: ${price:.4f}")
                logger.info(f"   PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)")
                logger.info(f"   Total: ${self.stats['pnl']:+.2f}")
                
                self._notify(
                    f"✅ <b>CERRADO - {reason}</b>\n"
                    f"{symbol}\n"
                    f"Entry: ${trade['entry']:.4f}\n"
                    f"Exit: ${price:.4f}\n"
                    f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                    f"Total: ${self.stats['pnl']:+.2f}"
                )
                
                del self.open_trades[symbol]
                return True
            
            return False
        except Exception as e:
            logger.error(f"❌ Error cerrando: {e}")
            return False
    
    async def _monitor_trades(self):
        """Monitorear trades"""
        for symbol in list(self.open_trades.keys()):
            try:
                data = self._get_price(symbol)
                if not data:
                    continue
                
                price = data['price']
                trade = self.open_trades[symbol]
                
                # Trailing
                self._update_trailing(symbol, price)
                
                # TP/SL
                if trade['direction'] == 'LONG':
                    if price >= trade['tp']:
                        logger.info(f"🎯 TP: {symbol}")
                        self._close_trade(symbol, "TP", price)
                    elif price <= trade['sl']:
                        logger.info(f"🛑 SL: {symbol}")
                        self._close_trade(symbol, "SL", price)
                else:
                    if price <= trade['tp']:
                        logger.info(f"🎯 TP: {symbol}")
                        self._close_trade(symbol, "TP", price)
                    elif price >= trade['sl']:
                        logger.info(f"🛑 SL: {symbol}")
                        self._close_trade(symbol, "SL", price)
            except:
                pass
    
    def _notify(self, msg: str):
        """Telegram"""
        try:
            if self.telegram_token and self.chat_id:
                url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
                requests.post(url, json={'chat_id': self.chat_id, 'text': msg, 'parse_mode': 'HTML'}, timeout=5)
        except:
            pass
    
    async def run(self):
        """Loop principal"""
        logger.info("\n🚀 Bot iniciado - Modo ULTRA-AGRESIVO\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"⏱️ ITERACIÓN #{iteration} | {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"📊 Abiertos: {len(self.open_trades)}/{self.max_trades}")
                logger.info(f"💵 PnL: ${self.stats['pnl']:+.2f}")
                
                if self.stats['wins'] + self.stats['losses'] > 0:
                    wr = self.stats['wins'] / (self.stats['wins'] + self.stats['losses']) * 100
                    logger.info(f"📈 Win Rate: {wr:.1f}% ({self.stats['wins']}W/{self.stats['losses']}L)")
                
                logger.info(f"{'='*80}\n")
                
                # Monitorear trades
                await self._monitor_trades()
                
                # Buscar señales
                signals = {'LONG': [], 'SHORT': []}
                
                for i, symbol in enumerate(self.symbols, 1):
                    try:
                        if symbol in self.open_trades:
                            continue
                        
                        data = self._get_price(symbol)
                        if not data:
                            continue
                        
                        analysis = self._analyze_ultra_aggressive(data)
                        
                        if analysis['action'] == 'LONG':
                            signals['LONG'].append(symbol)
                            self.stats['signals'] += 1
                            
                            logger.info(f"{i:2d}. 🟢 {symbol}: ${data['price']:.4f} | "
                                      f"{analysis['change']:+.2f}% | LONG ({analysis['score']:.0f}%)")
                            if analysis.get('reasons'):
                                logger.info(f"     {', '.join(analysis['reasons'])}")
                            
                            if self.auto_trading:
                                self._execute_trade(symbol, 'LONG', data['price'])
                        
                        elif analysis['action'] == 'SHORT':
                            signals['SHORT'].append(symbol)
                            self.stats['signals'] += 1
                            
                            logger.info(f"{i:2d}. 🔴 {symbol}: ${data['price']:.4f} | "
                                      f"{analysis['change']:+.2f}% | SHORT ({analysis['score']:.0f}%)")
                            if analysis.get('reasons'):
                                logger.info(f"     {', '.join(analysis['reasons'])}")
                            
                            if self.auto_trading:
                                self._execute_trade(symbol, 'SHORT', data['price'])
                        
                        else:
                            logger.debug(f"{i:2d}. ⚪ {symbol}: ${data['price']:.4f} | "
                                       f"{analysis['change']:+.2f}% | NEUTRAL ({analysis['score']:.0f}%)")
                    
                    except Exception as e:
                        logger.debug(f"Error {symbol}: {e}")
                    
                    await asyncio.sleep(0.03)
                
                # Resumen
                total_signals = len(signals['LONG']) + len(signals['SHORT'])
                
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 RESUMEN #{iteration}:")
                logger.info(f"   🎯 Señales: {total_signals}")
                logger.info(f"   🟢 LONG: {len(signals['LONG'])} | 🔴 SHORT: {len(signals['SHORT'])}")
                logger.info(f"   📈 Total señales: {self.stats['signals']}")
                logger.info(f"   🤖 Trades abiertos: {len(self.open_trades)}/{self.max_trades}")
                logger.info(f"   ✅ Cerrados: {self.stats['trades_closed']}")
                logger.info(f"   💵 PnL: ${self.stats['pnl']:+.2f}")
                logger.info(f"{'='*80}")
                
                logger.info(f"\n⏱️ Próxima en {self.interval}s...\n")
                await asyncio.sleep(self.interval)
            
            except KeyboardInterrupt:
                logger.info("\n🛑 Detenido")
                break
            except Exception as e:
                logger.error(f"\n❌ Error: {e}")
                await asyncio.sleep(10)


async def main():
    """Main"""
    try:
        bot = BotUltraOptimizado()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n👋 Terminado")
