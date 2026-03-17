import numpy as np
from typing import Dict, List, Optional

class LinearRegressionChannel:
    """
    Calcula el canal de regresión lineal con desviación estándar
    Detecta cambios de tendencia basándose en la pendiente
    """
    def __init__(self, length: int = 50, multiplier: float = 2.0):
        self.length = length
        self.multiplier = multiplier
        self.prev_bullish = None
    
    def calculate(self, closes: np.ndarray) -> Optional[Dict]:
        """
        Calcula:
        - Línea de regresión lineal (basis)
        - Bandas superior e inferior (desviación estándar)
        - Pendiente (slope)
        - Detección de cambio de tendencia
        """
        if len(closes) < self.length:
            return None
        
        # Últimas N velas
        recent = closes[-self.length:]
        
        # Regresión lineal polinomial grado 1
        x = np.arange(len(recent))
        z = np.polyfit(x, recent, 1)
        linreg = np.polyval(z, x)
        
        # Valores clave
        basis = float(linreg[-1])
        deviation = float(np.std(recent))
        upper = basis + (deviation * self.multiplier)
        lower = basis - (deviation * self.multiplier)
        
        # Calcular pendiente
        slope = linreg[-1] - linreg[-2]
        is_bullish = slope > 0
        
        # Detectar cambio de tendencia
        if self.prev_bullish is not None:
            trend_up = (self.prev_bullish == False) and is_bullish
            trend_down = (self.prev_bullish == True) and not is_bullish
        else:
            trend_up = False
            trend_down = False
        
        self.prev_bullish = is_bullish
        
        return {
            'basis': [basis],
            'upper': [upper],
            'lower': [lower],
            'is_bullish': [is_bullish],
            'trend_up': [trend_up],
            'trend_down': [trend_down],
            'slope': [slope]
        }


class LiquidityLevels:
    """
    Calcula los niveles de liquidez del día anterior
    PDH = Previous Day High
    PDL = Previous Day Low
    """
    def calculate(self, ohlcv: List) -> Dict:
        """
        Extrae el High y Low de la barra anterior
        """
        if len(ohlcv) < 2:
            return {'pdh': 0, 'pdl': 0}
        
        # Barra anterior (-2 porque -1 es la barra actual incompleta)
        pdh = float(ohlcv[-2][2])  # High
        pdl = float(ohlcv[-2][3])  # Low
        
        return {
            'pdh': pdh,
            'pdl': pdl
        }
