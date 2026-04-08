"""
VWAP Volatility Bands [BOSWaves] - Python Implementation
Traducción del indicador de TradingView a Python para trading automatizado
"""
import pandas as pd
import numpy as np
from typing import Dict, Tuple


class VWAPVolatilityBands:
    """
    Implementación de VWAP Volatility Bands con T3 smoothing
    Basado en el indicador de BOSWaves
    """
    
    def __init__(
        self,
        vwap_anchor: str = "Session",
        t3_length: int = 28,
        t3_factor: float = 0.7,
        atr_length: int = 14,
        min_score_diff: float = 40.0
    ):
        self.vwap_anchor = vwap_anchor
        self.t3_length = t3_length
        self.t3_factor = t3_factor
        self.atr_length = atr_length
        self.min_score_diff = min_score_diff
        
        # Band multipliers (fixed as per TradingView code)
        self.m1 = 0.5
        self.m2 = 1.0
        self.m3 = 1.5
        self.m4 = 2.2
    
    def calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """
        Calcular VWAP acumulativo
        Simplificado: usa todo el dataframe como período
        """
        hlc3 = (df['high'] + df['low'] + df['close']) / 3
        cum_tpvol = (hlc3 * df['volume']).cumsum()
        cum_vol = df['volume'].cumsum()
        
        # Evitar división por cero
        vwap = cum_tpvol / cum_vol.replace(0, np.nan)
        return vwap.fillna(method='ffill')
    
    def calculate_t3(self, series: pd.Series, length: int, factor: float) -> pd.Series:
        """
        T3 Moving Average - 6-stage EMA cascade
        """
        a = factor
        c1 = -a * a * a
        c2 = 3 * a * a + 3 * a * a * a
        c3 = -6 * a * a - 3 * a - 3 * a * a * a
        c4 = 1 + 3 * a + a * a * a + 3 * a * a
        
        e1 = series.ewm(span=length, adjust=False).mean()
        e2 = e1.ewm(span=length, adjust=False).mean()
        e3 = e2.ewm(span=length, adjust=False).mean()
        e4 = e3.ewm(span=length, adjust=False).mean()
        e5 = e4.ewm(span=length, adjust=False).mean()
        e6 = e5.ewm(span=length, adjust=False).mean()
        
        t3 = c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3
        return t3
    
    def calculate_atr(self, df: pd.DataFrame, length: int) -> pd.Series:
        """Calcular Average True Range"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=length).mean()
        
        return atr
    
    def calculate_score(self, df: pd.DataFrame, t3: pd.Series) -> Tuple[pd.Series, pd.Series]:
        """
        Calcular score basado en pendiente de T3
        Similar a la lógica del Pine Script
        """
        # Score: 1 si T3 sube, -1 si baja
        score = pd.Series(0, index=df.index)
        
        for i in range(1, len(df)):
            if t3.iloc[i] > t3.iloc[i-1]:
                score.iloc[i] = 1
            elif t3.iloc[i] < t3.iloc[i-1]:
                score.iloc[i] = -1
            else:
                score.iloc[i] = score.iloc[i-1]
        
        # VWAP position score
        vwap_bull = df['close'] > t3
        vwap_bear = df['close'] < t3
        
        return score, vwap_bull
    
    def analyze(self, df: pd.DataFrame) -> Dict:
        """
        Análisis completo de VWAP Volatility Bands
        Retorna señales y métricas
        """
        if len(df) < max(self.t3_length, self.atr_length) + 10:
            # Datos insuficientes
            return self._empty_signals(df)
        
        # Calcular componentes
        raw_vwap = self.calculate_vwap(df)
        t3 = self.calculate_t3(raw_vwap, self.t3_length, self.t3_factor)
        atr = self.calculate_atr(df, self.atr_length)
        
        # Calcular bandas
        band_u1 = t3 + atr * self.m1
        band_u2 = t3 + atr * self.m2
        band_u3 = t3 + atr * self.m3
        band_u4 = t3 + atr * self.m4
        
        band_l1 = t3 - atr * self.m1
        band_l2 = t3 - atr * self.m2
        band_l3 = t3 - atr * self.m3
        band_l4 = t3 - atr * self.m4
        
        # Score y señales
        score, vwap_bull = self.calculate_score(df, t3)
        
        # Detectar cruces (señales de entrada)
        long_signal = False
        short_signal = False
        
        if len(score) >= 2:
            # Long: T3 cruza hacia arriba
            if score.iloc[-1] == 1 and score.iloc[-2] <= 0:
                long_signal = True
            
            # Short: T3 cruza hacia abajo
            if score.iloc[-1] == -1 and score.iloc[-2] >= 0:
                short_signal = True
        
        # Detectar señales TP (sobreextensión)
        tp_bull = False
        tp_bear = False
        
        if len(df) >= 2:
            # TP Bull: precio cruza debajo de banda inferior durante tendencia bajista
            if score.iloc[-1] == -1 and df['close'].iloc[-1] < band_l4.iloc[-1]:
                tp_bull = True
            
            # TP Bear: precio cruza arriba de banda superior durante tendencia alcista
            if score.iloc[-1] == 1 and df['close'].iloc[-1] > band_u4.iloc[-1]:
                tp_bear = True
        
        # Calcular bull/bear percentage (simplified)
        # En este caso, basamos en posición relativa dentro de las bandas
        current_price = df['close'].iloc[-1]
        t3_val = t3.iloc[-1]
        atr_val = atr.iloc[-1]
        
        if score.iloc[-1] == 1:
            # Tendencia alcista
            distance_from_center = (current_price - t3_val) / (atr_val * self.m4)
            bull_pct = min(100, 50 + (distance_from_center * 50))
            bear_pct = 100 - bull_pct
        else:
            # Tendencia bajista
            distance_from_center = (t3_val - current_price) / (atr_val * self.m4)
            bear_pct = min(100, 50 + (distance_from_center * 50))
            bull_pct = 100 - bear_pct
        
        # Determinar bias
        score_diff = abs(bull_pct - bear_pct)
        if score_diff >= self.min_score_diff:
            bias = "STRONG BULL" if bull_pct > bear_pct else "STRONG BEAR"
        else:
            bias = "MILD BULL" if bull_pct > bear_pct else "MILD BEAR"
        
        return {
            'buy_signal': long_signal,
            'sell_signal': short_signal,
            'tp_bull': tp_bull,
            'tp_bear': tp_bear,
            'bull_pct': bull_pct,
            'bear_pct': bear_pct,
            'bias': bias,
            'close': current_price,
            'vwap': raw_vwap.iloc[-1],
            't3': t3_val,
            'atr': atr_val,
            'score': score.iloc[-1],
            'band_u4': band_u4.iloc[-1],
            'band_l4': band_l4.iloc[-1],
            'strategy': 'VWAP'
        }
    
    def _empty_signals(self, df: pd.DataFrame) -> Dict:
        """Retornar señales vacías cuando no hay suficientes datos"""
        return {
            'buy_signal': False,
            'sell_signal': False,
            'tp_bull': False,
            'tp_bear': False,
            'bull_pct': 50.0,
            'bear_pct': 50.0,
            'bias': 'NEUTRAL',
            'close': df['close'].iloc[-1] if len(df) > 0 else 0,
            'vwap': df['close'].iloc[-1] if len(df) > 0 else 0,
            't3': df['close'].iloc[-1] if len(df) > 0 else 0,
            'atr': 0,
            'score': 0,
            'band_u4': 0,
            'band_l4': 0,
            'strategy': 'VWAP'
        }


# ══════════════════════════════════════════════════════════════════════════════
# TESTING
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Test con datos simulados
    import matplotlib.pyplot as plt
    
    # Generar datos de prueba
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=500, freq='15min')
    
    # Simular precio con tendencia
    trend = np.linspace(40000, 45000, 500)
    noise = np.random.normal(0, 200, 500)
    close = trend + noise
    
    df_test = pd.DataFrame({
        'open': close - np.random.uniform(0, 100, 500),
        'high': close + np.random.uniform(0, 200, 500),
        'low': close - np.random.uniform(0, 200, 500),
        'close': close,
        'volume': np.random.uniform(100, 1000, 500)
    }, index=dates)
    
    # Ejecutar estrategia
    strategy = VWAPVolatilityBands()
    signals = strategy.analyze(df_test)
    
    print("=" * 60)
    print("VWAP Volatility Bands - Test Results")
    print("=" * 60)
    print(f"Buy Signal:  {signals['buy_signal']}")
    print(f"Sell Signal: {signals['sell_signal']}")
    print(f"Bull %:      {signals['bull_pct']:.1f}%")
    print(f"Bear %:      {signals['bear_pct']:.1f}%")
    print(f"Bias:        {signals['bias']}")
    print(f"Close:       {signals['close']:.2f}")
    print(f"VWAP:        {signals['vwap']:.2f}")
    print(f"T3:          {signals['t3']:.2f}")
    print(f"ATR:         {signals['atr']:.2f}")
    print(f"Score:       {signals['score']}")
    print("=" * 60)
