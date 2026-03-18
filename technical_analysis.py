"""
Análisis técnico avanzado
Indicadores: RSI, MACD, Bollinger Bands, Volume Analysis
"""
import numpy as np
import logging
from typing import Dict, List, Optional
from config import Config

logger = logging.getLogger(__name__)


class TechnicalAnalysis:
    """Análisis técnico con múltiples indicadores"""
    
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        """
        Calcular RSI (Relative Strength Index)
        
        Args:
            prices: Lista de precios de cierre
            period: Período (default 14)
        
        Returns:
            Valor RSI (0-100)
        """
        if len(prices) < period + 1:
            return 50.0
        
        try:
            prices_arr = np.array(prices)
            deltas = np.diff(prices_arr)
            
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            
            avg_gain = np.mean(gains[-period:])
            avg_loss = np.mean(losses[-period:])
            
            if avg_loss == 0:
                return 100.0
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            return round(rsi, 2)
        
        except Exception as e:
            logger.debug(f"Error RSI: {e}")
            return 50.0
    
    @staticmethod
    def calculate_macd(prices: List[float], fast: int = 12, slow: int = 26, 
                       signal: int = 9) -> Dict:
        """
        Calcular MACD (Moving Average Convergence Divergence)
        
        Returns:
            {'macd': float, 'signal': float, 'histogram': float}
        """
        if len(prices) < slow + signal:
            return {'macd': 0, 'signal': 0, 'histogram': 0}
        
        try:
            prices_arr = np.array(prices)
            
            # EMA rápida
            ema_fast = TechnicalAnalysis._ema(prices_arr, fast)
            
            # EMA lenta
            ema_slow = TechnicalAnalysis._ema(prices_arr, slow)
            
            # MACD line
            macd_line = ema_fast - ema_slow
            
            # Signal line
            signal_line = TechnicalAnalysis._ema(macd_line, signal)
            
            # Histogram
            histogram = macd_line[-1] - signal_line[-1]
            
            return {
                'macd': round(macd_line[-1], 6),
                'signal': round(signal_line[-1], 6),
                'histogram': round(histogram, 6)
            }
        
        except Exception as e:
            logger.debug(f"Error MACD: {e}")
            return {'macd': 0, 'signal': 0, 'histogram': 0}
    
    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        """Calcular EMA (Exponential Moving Average)"""
        ema = np.zeros_like(data)
        ema[0] = data[0]
        multiplier = 2 / (period + 1)
        
        for i in range(1, len(data)):
            ema[i] = (data[i] - ema[i-1]) * multiplier + ema[i-1]
        
        return ema
    
    @staticmethod
    def calculate_bollinger_bands(prices: List[float], period: int = 20, 
                                  std_dev: float = 2.0) -> Dict:
        """
        Calcular Bollinger Bands
        
        Returns:
            {'upper': float, 'middle': float, 'lower': float, 'bandwidth': float}
        """
        if len(prices) < period:
            avg = np.mean(prices)
            return {'upper': avg, 'middle': avg, 'lower': avg, 'bandwidth': 0}
        
        try:
            prices_arr = np.array(prices[-period:])
            
            middle = np.mean(prices_arr)
            std = np.std(prices_arr)
            
            upper = middle + (std_dev * std)
            lower = middle - (std_dev * std)
            bandwidth = ((upper - lower) / middle) * 100
            
            return {
                'upper': round(upper, 6),
                'middle': round(middle, 6),
                'lower': round(lower, 6),
                'bandwidth': round(bandwidth, 2)
            }
        
        except Exception as e:
            logger.debug(f"Error BB: {e}")
            avg = np.mean(prices)
            return {'upper': avg, 'middle': avg, 'lower': avg, 'bandwidth': 0}
    
    @staticmethod
    def calculate_volume_profile(volumes: List[float], prices: List[float]) -> Dict:
        """
        Análisis de volumen
        
        Returns:
            {'avg_volume': float, 'volume_trend': str, 'volume_spike': bool}
        """
        if len(volumes) < 10:
            return {'avg_volume': 0, 'volume_trend': 'NEUTRAL', 'volume_spike': False}
        
        try:
            volumes_arr = np.array(volumes)
            
            avg_volume = np.mean(volumes_arr[-20:]) if len(volumes_arr) >= 20 else np.mean(volumes_arr)
            recent_volume = np.mean(volumes_arr[-5:])
            
            # Tendencia de volumen
            if recent_volume > avg_volume * 1.5:
                volume_trend = 'INCREASING'
                volume_spike = True
            elif recent_volume < avg_volume * 0.5:
                volume_trend = 'DECREASING'
                volume_spike = False
            else:
                volume_trend = 'NEUTRAL'
                volume_spike = False
            
            return {
                'avg_volume': round(avg_volume, 2),
                'recent_volume': round(recent_volume, 2),
                'volume_trend': volume_trend,
                'volume_spike': volume_spike
            }
        
        except Exception as e:
            logger.debug(f"Error volume: {e}")
            return {'avg_volume': 0, 'volume_trend': 'NEUTRAL', 'volume_spike': False}
    
    @staticmethod
    def calculate_sma(prices: List[float], period: int) -> float:
        """Calcular SMA (Simple Moving Average)"""
        if len(prices) < period:
            return np.mean(prices)
        
        return round(np.mean(prices[-period:]), 6)
    
    @staticmethod
    def analyze_price_action(klines: List[Dict]) -> Dict:
        """
        Análisis de price action
        
        Args:
            klines: Lista de velas OHLCV
        
        Returns:
            Análisis completo de price action
        """
        if len(klines) < 20:
            return {'trend': 'UNKNOWN', 'strength': 0}
        
        try:
            closes = [k['close'] for k in klines]
            highs = [k['high'] for k in klines]
            lows = [k['low'] for k in klines]
            volumes = [k['volume'] for k in klines]
            
            current_price = closes[-1]
            
            # SMAs
            sma_20 = TechnicalAnalysis.calculate_sma(closes, 20)
            sma_50 = TechnicalAnalysis.calculate_sma(closes, 50) if len(closes) >= 50 else sma_20
            
            # Tendencia
            if current_price > sma_20 > sma_50:
                trend = 'UPTREND'
                trend_strength = min(100, ((current_price - sma_50) / sma_50) * 100)
            elif current_price < sma_20 < sma_50:
                trend = 'DOWNTREND'
                trend_strength = min(100, ((sma_50 - current_price) / sma_50) * 100)
            else:
                trend = 'SIDEWAYS'
                trend_strength = 0
            
            # Support/Resistance
            recent_lows = sorted(lows[-20:])[:5]
            recent_highs = sorted(highs[-20:], reverse=True)[:5]
            
            support = np.mean(recent_lows)
            resistance = np.mean(recent_highs)
            
            return {
                'trend': trend,
                'strength': round(abs(trend_strength), 2),
                'sma_20': round(sma_20, 6),
                'sma_50': round(sma_50, 6),
                'support': round(support, 6),
                'resistance': round(resistance, 6),
                'current_price': current_price
            }
        
        except Exception as e:
            logger.debug(f"Error price action: {e}")
            return {'trend': 'UNKNOWN', 'strength': 0}
    
    @staticmethod
    def generate_features(klines: List[Dict]) -> Optional[Dict]:
        """
        Generar features para ML
        
        Returns:
            Diccionario con todos los indicadores calculados
        """
        if len(klines) < 50:
            return None
        
        try:
            closes = [k['close'] for k in klines]
            volumes = [k['volume'] for k in klines]
            
            # Indicadores
            rsi = TechnicalAnalysis.calculate_rsi(closes, Config.RSI_PERIOD)
            macd = TechnicalAnalysis.calculate_macd(closes, Config.MACD_FAST, 
                                                   Config.MACD_SLOW, Config.MACD_SIGNAL)
            bb = TechnicalAnalysis.calculate_bollinger_bands(closes, Config.BB_PERIOD, 
                                                             Config.BB_STD_DEV)
            volume_prof = TechnicalAnalysis.calculate_volume_profile(volumes, closes)
            price_action = TechnicalAnalysis.analyze_price_action(klines)
            
            # Features adicionales
            current_price = closes[-1]
            price_change_1h = ((closes[-1] - closes[-12]) / closes[-12]) * 100 if len(closes) >= 12 else 0
            price_change_4h = ((closes[-1] - closes[-48]) / closes[-48]) * 100 if len(closes) >= 48 else 0
            
            # Posición respecto a BB
            bb_position = ((current_price - bb['lower']) / (bb['upper'] - bb['lower'])) * 100 if bb['upper'] != bb['lower'] else 50
            
            return {
                'rsi': rsi,
                'macd': macd['macd'],
                'macd_signal': macd['signal'],
                'macd_histogram': macd['histogram'],
                'bb_upper': bb['upper'],
                'bb_middle': bb['middle'],
                'bb_lower': bb['lower'],
                'bb_bandwidth': bb['bandwidth'],
                'bb_position': round(bb_position, 2),
                'volume_trend': volume_prof['volume_trend'],
                'volume_spike': 1 if volume_prof['volume_spike'] else 0,
                'trend': price_action['trend'],
                'trend_strength': price_action['strength'],
                'price_change_1h': round(price_change_1h, 2),
                'price_change_4h': round(price_change_4h, 2),
                'current_price': current_price
            }
        
        except Exception as e:
            logger.error(f"❌ Error generando features: {e}")
            return None
    
    @staticmethod
    def get_signal_strength(features: Dict) -> Dict:
        """
        Calcular fuerza de señal basada en indicadores
        
        Returns:
            {'direction': 'LONG'|'SHORT'|'NEUTRAL', 'strength': 0-100, 'reasons': []}
        """
        if not features:
            return {'direction': 'NEUTRAL', 'strength': 0, 'reasons': []}
        
        score = 0
        reasons = []
        
        # RSI
        if features['rsi'] < Config.RSI_OVERSOLD:
            score += 20
            reasons.append(f"RSI oversold ({features['rsi']:.1f})")
        elif features['rsi'] > Config.RSI_OVERBOUGHT:
            score -= 20
            reasons.append(f"RSI overbought ({features['rsi']:.1f})")
        
        # MACD
        if features['macd_histogram'] > 0:
            score += 15
            reasons.append("MACD bullish")
        else:
            score -= 15
            reasons.append("MACD bearish")
        
        # Bollinger Bands
        if features['bb_position'] < 20:
            score += 15
            reasons.append("Near lower BB")
        elif features['bb_position'] > 80:
            score -= 15
            reasons.append("Near upper BB")
        
        # Trend
        if features['trend'] == 'UPTREND':
            score += features['trend_strength'] * 0.2
            reasons.append(f"Uptrend ({features['trend_strength']:.1f}%)")
        elif features['trend'] == 'DOWNTREND':
            score -= features['trend_strength'] * 0.2
            reasons.append(f"Downtrend ({features['trend_strength']:.1f}%)")
        
        # Volume
        if features['volume_spike']:
            score += 10
            reasons.append("Volume spike")
        
        # Determinar dirección
        if score > 20:
            direction = 'LONG'
            strength = min(100, score)
        elif score < -20:
            direction = 'SHORT'
            strength = min(100, abs(score))
        else:
            direction = 'NEUTRAL'
            strength = 0
        
        return {
            'direction': direction,
            'strength': round(strength, 2),
            'score': round(score, 2),
            'reasons': reasons
        }
