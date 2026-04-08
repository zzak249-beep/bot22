"""
Sniper Strategy - Improved Version
Estrategia basada en cruces de EMA con scoring multi-indicador
"""
import pandas as pd
import numpy as np
from typing import Dict, Tuple


class SniperStrategy:
    """
    Estrategia Sniper con cruces EMA 9/21 y scoring system
    """
    
    def __init__(self, atr_multiplier: float = 1.5):
        self.atr_multiplier = atr_multiplier
    
    def calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """Calcular EMA"""
        return series.ewm(span=period, adjust=False).mean()
    
    def calculate_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        """Calcular RSI"""
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)
    
    def calculate_macd(self, series: pd.Series) -> Tuple[pd.Series, pd.Series]:
        """Calcular MACD"""
        ema12 = series.ewm(span=12, adjust=False).mean()
        ema26 = series.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        return macd_line, signal_line
    
    def calculate_adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calcular ADX (Average Directional Index)"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Plus/Minus Directional Movement
        plus_dm = high.diff()
        minus_dm = -low.diff()
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Smoothed values
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        
        # ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.rolling(window=period).mean()
        
        return adx.fillna(0)
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calcular ATR"""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        return atr
    
    def calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """Calcular VWAP simple"""
        hlc3 = (df['high'] + df['low'] + df['close']) / 3
        return (hlc3 * df['volume']).cumsum() / df['volume'].cumsum()
    
    def calculate_dual_score(self, df: pd.DataFrame, rsi_5m: pd.Series = None) -> Dict:
        """
        Calcular scoring system dual (bull/bear)
        Basado en el indicador original de TradingView
        """
        # Indicadores
        ema9 = self.calculate_ema(df['close'], 9)
        ema21 = self.calculate_ema(df['close'], 21)
        vwap = self.calculate_vwap(df)
        rsi = self.calculate_rsi(df['close'], 14)
        macd, macd_signal = self.calculate_macd(df['close'])
        adx = self.calculate_adx(df, 14)
        vol_avg = df['volume'].rolling(window=20).mean()
        
        # RSI 5m (si está disponible)
        if rsi_5m is None:
            rsi_5m = pd.Series(50, index=df.index)
        
        # Bull Score (7 componentes)
        bull_score = 0
        close = df['close'].iloc[-1]
        open_price = df['open'].iloc[-1]
        volume = df['volume'].iloc[-1]
        
        bull_score += 1 if close > vwap.iloc[-1] else 0
        bull_score += 1 if rsi.iloc[-1] > 50 else 0
        bull_score += 1 if macd.iloc[-1] > macd_signal.iloc[-1] else 0
        bull_score += 1 if ema9.iloc[-1] > ema21.iloc[-1] else 0
        bull_score += 1 if (adx.iloc[-1] > 25 and close > ema9.iloc[-1]) else 0
        bull_score += 1 if (volume > vol_avg.iloc[-1] and close > open_price) else 0
        bull_score += 1 if rsi_5m.iloc[-1] > 50 else 0
        
        bull_pct = (bull_score / 7) * 100
        
        # Bear Score (7 componentes)
        bear_score = 0
        bear_score += 1 if close < vwap.iloc[-1] else 0
        bear_score += 1 if rsi.iloc[-1] < 50 else 0
        bear_score += 1 if macd.iloc[-1] < macd_signal.iloc[-1] else 0
        bear_score += 1 if ema9.iloc[-1] < ema21.iloc[-1] else 0
        bear_score += 1 if (adx.iloc[-1] > 25 and close < ema9.iloc[-1]) else 0
        bear_score += 1 if (volume > vol_avg.iloc[-1] and close < open_price) else 0
        bear_score += 1 if rsi_5m.iloc[-1] < 50 else 0
        
        bear_pct = (bear_score / 7) * 100
        
        # Bias
        score_diff = abs(bull_pct - bear_pct)
        if score_diff >= 40:
            bias = "STRONG BULL" if bull_pct > bear_pct else "STRONG BEAR"
        else:
            bias = "MILD BULL" if bull_pct > bear_pct else "MILD BEAR"
        
        return {
            'bull_pct': bull_pct,
            'bear_pct': bear_pct,
            'bias': bias,
            'close': close,
            'vwap': vwap.iloc[-1],
            'rsi': rsi.iloc[-1],
            'macd': macd.iloc[-1],
            'macd_signal': macd_signal.iloc[-1],
            'adx': adx.iloc[-1],
            'ema9': ema9.iloc[-1],
            'ema21': ema21.iloc[-1],
            'rsi_5m': rsi_5m.iloc[-1]
        }
    
    def detect_signals(self, df: pd.DataFrame) -> Dict:
        """Detectar señales de entrada basadas en cruces de EMA"""
        if len(df) < 30:
            return {'buy_signal': False, 'sell_signal': False}
        
        ema9 = self.calculate_ema(df['close'], 9)
        ema21 = self.calculate_ema(df['close'], 21)
        
        # Cruces
        buy_cross = (ema9.iloc[-1] > ema21.iloc[-1] and 
                     ema9.iloc[-2] <= ema21.iloc[-2])
        
        sell_cross = (ema9.iloc[-1] < ema21.iloc[-1] and 
                      ema9.iloc[-2] >= ema21.iloc[-2])
        
        return {
            'buy_signal': buy_cross,
            'sell_signal': sell_cross
        }
    
    def analyze(self, df: pd.DataFrame, df_5m: pd.DataFrame = None) -> Dict:
        """
        Análisis completo de la estrategia Sniper
        """
        if len(df) < 30:
            return self._empty_signals(df)
        
        # Calcular RSI 5m si está disponible
        rsi_5m = None
        if df_5m is not None and len(df_5m) >= 14:
            rsi_5m_series = self.calculate_rsi(df_5m['close'], 14)
            rsi_5m = pd.Series(rsi_5m_series.iloc[-1], index=df.index)
        
        # Scoring system
        scores = self.calculate_dual_score(df, rsi_5m)
        
        # Detectar señales
        signals = self.detect_signals(df)
        
        # Calcular ATR para niveles
        atr = self.calculate_atr(df, 14)
        
        # Combinar resultados
        result = {
            **scores,
            **signals,
            'atr': atr.iloc[-1],
            'strategy': 'SNIPER'
        }
        
        return result
    
    def _empty_signals(self, df: pd.DataFrame) -> Dict:
        """Señales vacías cuando no hay datos suficientes"""
        return {
            'buy_signal': False,
            'sell_signal': False,
            'bull_pct': 50.0,
            'bear_pct': 50.0,
            'bias': 'NEUTRAL',
            'close': df['close'].iloc[-1] if len(df) > 0 else 0,
            'vwap': 0,
            'rsi': 50,
            'macd': 0,
            'macd_signal': 0,
            'adx': 0,
            'ema9': 0,
            'ema21': 0,
            'rsi_5m': 50,
            'atr': 0,
            'strategy': 'SNIPER'
        }


# ══════════════════════════════════════════════════════════════════════════════
# TESTING
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=500, freq='15min')
    
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
    
    strategy = SniperStrategy()
    signals = strategy.analyze(df_test)
    
    print("=" * 60)
    print("Sniper Strategy - Test Results")
    print("=" * 60)
    print(f"Buy Signal:  {signals['buy_signal']}")
    print(f"Sell Signal: {signals['sell_signal']}")
    print(f"Bull %:      {signals['bull_pct']:.1f}%")
    print(f"Bear %:      {signals['bear_pct']:.1f}%")
    print(f"Bias:        {signals['bias']}")
    print(f"RSI:         {signals['rsi']:.1f}")
    print(f"ADX:         {signals['adx']:.1f}")
    print(f"ATR:         {signals['atr']:.2f}")
    print("=" * 60)
