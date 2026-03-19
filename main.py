#!/usr/bin/env python3
"""
🚀 BOT ULTRA-OPTIMIZADO - CON TP/SL AUTOMÁTICOS EN BINGX
=========================================================
✅ GENERA SEÑALES
✅ ABRE TRADES
✅ COLOCA TP/SL COMO ÓRDENES EN BINGX ← NUEVO
✅ CIERRA AUTOMÁTICAMENTE
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


class BotConTPSL:
    """Bot con TP/SL reales en BingX"""
    
    def __init__(self):
        """Inicializar"""
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
        
        # Parámetros
        self.position_size = float(os.getenv('MAX_POSITION_SIZE', '50'))
        self.leverage = int(os.getenv('LEVERAGE', '3'))
        self.interval = int(os.getenv('CHECK_INTERVAL', '60'))
        
        # TP/SL
        self.take_profit_pct = float(os.getenv('TAKE_PROFIT_PCT', '2.5'))
        self.stop_loss_pct = float(os.getenv('STOP_LOSS_PCT', '1.2'))
        
        # Trailing
        self.trailing_activation = float(os.getenv('TRAILING_ACTIVATION', '0.8'))
        self.trailing_distance = float(os.getenv('TRAILING_DISTANCE', '0.4'))
        
        # Umbrales
        self.min_change_pct = float(os.getenv('MIN_CHANGE_PCT', '0.3'))
        self.min_confidence = float(os.getenv('MIN_CONFIDENCE', '45'))
        
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
        """Info de inicio"""
        logger.info("="*80)
        logger.info("🚀 BOT CON TP/SL AUTOMÁTICOS EN BINGX")
        logger.info("="*80)
        logger.info(f"✅ Modo: {'TRADING REAL' if self.auto_trading else 'SOLO SEÑALES'}")
        logger.info(f"📊 Pares: {len(self.symbols)}")
        logger.info(f"💰 Position: ${self.position_size} | Leverage: {self.leverage}x")
        logger.info(f"🎯 TP: {self.take_profit_pct}% | SL: {self.stop_loss_pct}%")
        logger.info(f"🔄 Trailing: {self.trailing_activation}% / {self.trailing_distance}%")
        logger.info(f"📈 Min Change: {self.min_change_pct}%")
        logger.info(f"📊 Min Confidence: {self.min_confidence}%")
        logger.info(f"⚡ Intervalo: {self.interval}s")
        logger.info("="*80)
        
        if self.auto_trading and (not self.bingx_api_key or not self.bingx_api_secret):
            logger.warning("⚠️ API keys faltantes - Solo modo señales")
            self.auto_trading = False
    
    def _sign_request(self, params: Dict) -> str:
        """Firmar petición"""
        query_string = urlencode(sorted(params.items()))
        signature = hmac.new(
            self.bingx_api_secret.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _get_price(self, symbol: str) -> Optional[Dict]:
        """Obtener precio"""
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
    
    def _analyze_signal(self, data: Dict) -> Dict:
        """Analizar señal"""
        symbol = data['symbol']
        price = data['price']
        change = data['change']
        
        if symbol in self.open_trades:
            return {'action': 'NEUTRAL', 'score': 0, 'change': change}
        
        prices = self.price_history[symbol]
        rsi = self._calculate_rsi(prices) if len(prices) >= 10 else 50
        
        score = 0
        reasons = []
        
        # Momentum
        if abs(change) >= self.min_change_pct:
            momentum_pts = min(35, abs(change) * 20)
            score += momentum_pts
            reasons.append(f"Move:{change:+.2f}%")
        
        # RSI
        if rsi < 40:
            score += 30
            reasons.append(f"RSI:{rsi:.0f}")
        elif rsi > 60:
            score += 30
            reasons.append(f"RSI:{rsi:.0f}")
        elif 40 <= rsi <= 50:
            score += 15
            reasons.append(f"RSI:{rsi:.0f}")
        elif 50 <= rsi <= 60:
            score += 15
            reasons.append(f"RSI:{rsi:.0f}")
        
        # Tendencia
        if len(prices) >= 5:
            trend = prices[-1] - prices[-5]
            if abs(trend / prices[-5] * 100) > 0.2:
                score += 15
                reasons.append("Trend")
        
        # Volatilidad
        if len(prices) >= 5:
            vol = (max(prices[-5:]) - min(prices[-5:])) / price * 100
            if vol > 0.3:
                score += 10
                reasons.append(f"Vol:{vol:.1f}%")
        
        # Decisión
        action = 'NEUTRAL'
        
        if score >= self.min_confidence:
            if change > 0 and rsi < 65:
                action = 'LONG'
            elif change < 0 and rsi > 35:
                action = 'SHORT'
            elif change > 0.1:
                action = 'LONG'
            elif change < -0.1:
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
        """Ejecutar trade CON TP/SL en BingX"""
        if not self.auto_trading:
            return False
        
        if len(self.open_trades) >= self.max_trades:
            logger.warning(f"⚠️ Max trades: {self.max_trades}")
            return False
        
        try:
            # Calcular TP/SL
            if direction == 'LONG':
                tp_price = price * (1 + self.take_profit_pct / 100)
                sl_price = price * (1 - self.stop_loss_pct / 100)
            else:
                tp_price = price * (1 - self.take_profit_pct / 100)
                sl_price = price * (1 + self.stop_loss_pct / 100)
            
            # Cantidad
            qty = (self.position_size / price) * self.leverage
            qty = max(qty, 10 / price)
            
            # 1. ABRIR POSICIÓN
            timestamp = int(time.time() * 1000)
            params = {
                'symbol': symbol,
                'side': 'BUY' if direction == 'LONG' else 'SELL',
                'positionSide': direction,
                'type': 'MARKET',
                'quantity': f"{qty:.6f}",
                'timestamp': timestamp
            }
            
            params['signature'] = self._sign_request(params)
            
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
                    
                    logger.info(f"✅ Posición abierta: {direction} {symbol}")
                    
                    # 2. COLOCAR TAKE PROFIT
                    time.sleep(0.5)
                    tp_result = self._place_tp_order(symbol, direction, tp_price, qty)
                    
                    # 3. COLOCAR STOP LOSS
                    time.sleep(0.5)
                    sl_result = self._place_sl_order(symbol, direction, sl_price, qty)
                    
                    if tp_result and sl_result:
                        logger.info(f"✅ TP/SL colocados correctamente")
                        logger.info(f"   TP: ${tp_price:.4f} | SL: ${sl_price:.4f}")
                    else:
                        logger.warning(f"⚠️ Error colocando TP/SL - Verificar en BingX")
                    
                    # Registrar
                    self.open_trades[symbol] = {
                        'direction': direction,
                        'entry': price,
                        'tp': tp_price,
                        'sl': sl_price,
                        'qty': qty,
                        'order_id': order_id,
                        'time': datetime.now().isoformat(),
                        'highest': price if direction == 'LONG' else 0,
                        'lowest': price if direction == 'SHORT' else float('inf'),
                        'trailing': False
                    }
                    
                    self.stats['trades_open'] += 1
                    
                    self._notify(
                        f"✅ <b>TRADE ABIERTO</b>\n"
                        f"{direction} {symbol}\n"
                        f"Entry: ${price:.4f}\n"
                        f"TP: ${tp_price:.4f} (+{self.take_profit_pct}%)\n"
                        f"SL: ${sl_price:.4f} (-{self.stop_loss_pct}%)\n"
                        f"Tamaño: ${price * qty:.2f}"
                    )
                    
                    return True
            
            return False
        except Exception as e:
            logger.error(f"❌ Error ejecutando: {e}")
            return False
    
    def _place_tp_order(self, symbol: str, direction: str, tp_price: float, qty: float) -> bool:
        """Colocar orden TAKE PROFIT en BingX"""
        try:
            timestamp = int(time.time() * 1000)
            
            # Orden TP es inversa a la posición
            side = 'SELL' if direction == 'LONG' else 'BUY'
            
            params = {
                'symbol': symbol,
                'side': side,
                'positionSide': direction,
                'type': 'TAKE_PROFIT_MARKET',
                'stopPrice': f"{tp_price:.4f}",
                'quantity': f"{qty:.6f}",
                'timestamp': timestamp
            }
            
            params['signature'] = self._sign_request(params)
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            headers = {
                'X-BX-APIKEY': self.bingx_api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            r = requests.post(url, params=params, headers=headers, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                if data.get('code') == 0:
                    logger.info(f"   ✅ TP colocado: ${tp_price:.4f}")
                    return True
                else:
                    logger.warning(f"   ⚠️ Error TP: {data.get('msg')}")
                    return False
            
            return False
        except Exception as e:
            logger.error(f"   ❌ Error TP: {e}")
            return False
    
    def _place_sl_order(self, symbol: str, direction: str, sl_price: float, qty: float) -> bool:
        """Colocar orden STOP LOSS en BingX"""
        try:
            timestamp = int(time.time() * 1000)
            
            # Orden SL es inversa a la posición
            side = 'SELL' if direction == 'LONG' else 'BUY'
            
            params = {
                'symbol': symbol,
                'side': side,
                'positionSide': direction,
                'type': 'STOP_MARKET',
                'stopPrice': f"{sl_price:.4f}",
                'quantity': f"{qty:.6f}",
                'timestamp': timestamp
            }
            
            params['signature'] = self._sign_request(params)
            
            url = f"{self.base_url}/openApi/swap/v2/trade/order"
            headers = {
                'X-BX-APIKEY': self.bingx_api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            r = requests.post(url, params=params, headers=headers, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                if data.get('code') == 0:
                    logger.info(f"   ✅ SL colocado: ${sl_price:.4f}")
                    return True
                else:
                    logger.warning(f"   ⚠️ Error SL: {data.get('msg')}")
                    return False
            
            return False
        except Exception as e:
            logger.error(f"   ❌ Error SL: {e}")
            return False
    
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
        logger.info("\n🚀 Bot iniciado - Con TP/SL automáticos\n")
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"⏱️ ITERACIÓN #{iteration} | {datetime.now().strftime('%H:%M:%S')}")
                logger.info(f"📊 Abiertos: {len(self.open_trades)}/{self.max_trades}")
                logger.info(f"💵 PnL: ${self.stats['pnl']:+.2f}")
                logger.info(f"{'='*80}\n")
                
                # Buscar señales
                signals = 0
                
                for i, symbol in enumerate(self.symbols, 1):
                    try:
                        if symbol in self.open_trades:
                            continue
                        
                        data = self._get_price(symbol)
                        if not data:
                            continue
                        
                        analysis = self._analyze_signal(data)
                        
                        if analysis['action'] == 'LONG':
                            signals += 1
                            self.stats['signals'] += 1
                            
                            logger.info(f"{i:2d}. 🟢 {symbol}: ${data['price']:.4f} | "
                                      f"{analysis['change']:+.2f}% | LONG ({analysis['score']:.0f}%)")
                            if analysis.get('reasons'):
                                logger.info(f"     {', '.join(analysis['reasons'])}")
                            
                            if self.auto_trading:
                                self._execute_trade(symbol, 'LONG', data['price'])
                        
                        elif analysis['action'] == 'SHORT':
                            signals += 1
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
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 RESUMEN #{iteration}:")
                logger.info(f"   🎯 Señales: {signals}")
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
        bot = BotConTPSL()
        await bot.run()
    except Exception as e:
        logger.error(f"❌ Fatal: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n👋 Terminado")
