"""
VWAP Volatility Bands [BOSWaves] - Python Implementation
Traducción del indicador de TradingView a Python para trading automatizado
Fixed: pandas 2.x compatibility (fillna method removed, score vectorizado)
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
        self.vwap_anchor   = vwap_anchor
        self.t3_length     = t3_length
        self.t3_factor     = t3_factor
        self.atr_length    = atr_length
        self.min_score_diff = min_score_diff

        # Band multipliers
        self.m1 = 0.5
        self.m2 = 1.0
        self.m3 = 1.5
        self.m4 = 2.2

    def calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """Calcular VWAP acumulativo"""
        hlc3 = (df['high'] + df['low'] + df['close']) / 3
        cum_tpvol = (hlc3 * df['volume']).cumsum()
        cum_vol   = df['volume'].cumsum()

        vwap = cum_tpvol / cum_vol.replace(0, np.nan)
        # FIX: pandas 2.x — usar ffill() en lugar de fillna(method='ffill')
        return vwap.ffill()

    def calculate_t3(self, series: pd.Series, length: int, factor: float) -> pd.Series:
        """T3 Moving Average - 6-stage EMA cascade"""
        a  = factor
        c1 = -a ** 3
        c2 = 3 * a ** 2 + 3 * a ** 3
        c3 = -6 * a ** 2 - 3 * a - 3 * a ** 3
        c4 = 1 + 3 * a + a ** 3 + 3 * a ** 2

        e1 = series.ewm(span=length, adjust=False).mean()
        e2 = e1.ewm(span=length, adjust=False).mean()
        e3 = e2.ewm(span=length, adjust=False).mean()
        e4 = e3.ewm(span=length, adjust=False).mean()
        e5 = e4.ewm(span=length, adjust=False).mean()
        e6 = e5.ewm(span=length, adjust=False).mean()

        return c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3

    def calculate_atr(self, df: pd.DataFrame, length: int) -> pd.Series:
        """Calcular Average True Range"""
        high  = df['high']
        low   = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low  - close.shift(1)).abs()

        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=length).mean()

    def calculate_score(self, df: pd.DataFrame, t3: pd.Series) -> Tuple[pd.Series, pd.Series]:
        """
        Calcular score basado en pendiente de T3 — versión vectorizada
        (evita el bucle lento y el warning de pandas sobre .iloc en bucle)
        """
        t3_diff = t3.diff()

        # +1 sube, -1 baja, 0 igual → propagar último valor válido
        raw_score = pd.Series(np.where(t3_diff > 0, 1, np.where(t3_diff < 0, -1, np.nan)),
                              index=df.index)
        # FIX: forward-fill para propagar último estado, luego rellenar inicio con 0
        score = raw_score.ffill().fillna(0).astype(int)

        vwap_bull = df['close'] > t3
        return score, vwap_bull

    def analyze(self, df: pd.DataFrame) -> Dict:
        """Análisis completo de VWAP Volatility Bands"""
        if len(df) < max(self.t3_length, self.atr_length) + 10:
            return self._empty_signals(df)

        raw_vwap = self.calculate_vwap(df)
        t3       = self.calculate_t3(raw_vwap, self.t3_length, self.t3_factor)
        atr      = self.calculate_atr(df, self.atr_length)

        band_u1 = t3 + atr * self.m1
        band_u2 = t3 + atr * self.m2
        band_u3 = t3 + atr * self.m3
        band_u4 = t3 + atr * self.m4

        band_l1 = t3 - atr * self.m1
        band_l2 = t3 - atr * self.m2
        band_l3 = t3 - atr * self.m3
        band_l4 = t3 - atr * self.m4

        score, vwap_bull = self.calculate_score(df, t3)

        # Señales de entrada por cruce de score
        long_signal  = bool(len(score) >= 2 and score.iloc[-1] == 1  and score.iloc[-2] <= 0)
        short_signal = bool(len(score) >= 2 and score.iloc[-1] == -1 and score.iloc[-2] >= 0)

        # Señales de sobreextensión
        tp_bull = bool(score.iloc[-1] == -1 and df['close'].iloc[-1] < band_l4.iloc[-1])
        tp_bear = bool(score.iloc[-1] == 1  and df['close'].iloc[-1] > band_u4.iloc[-1])

        # Bull/Bear percentage
        current_price = float(df['close'].iloc[-1])
        t3_val        = float(t3.iloc[-1])
        atr_val       = float(atr.iloc[-1]) if not np.isnan(atr.iloc[-1]) else 1.0

        if score.iloc[-1] == 1:
            distance = (current_price - t3_val) / max(atr_val * self.m4, 1e-10)
            bull_pct = float(np.clip(50 + distance * 50, 0, 100))
            bear_pct = 100 - bull_pct
        else:
            distance = (t3_val - current_price) / max(atr_val * self.m4, 1e-10)
            bear_pct = float(np.clip(50 + distance * 50, 0, 100))
            bull_pct = 100 - bear_pct

        score_diff = abs(bull_pct - bear_pct)
        if score_diff >= self.min_score_diff:
            bias = "STRONG BULL" if bull_pct > bear_pct else "STRONG BEAR"
        else:
            bias = "MILD BULL" if bull_pct > bear_pct else "MILD BEAR"

        return {
            'buy_signal':  long_signal,
            'sell_signal': short_signal,
            'tp_bull':     tp_bull,
            'tp_bear':     tp_bear,
            'bull_pct':    bull_pct,
            'bear_pct':    bear_pct,
            'bias':        bias,
            'close':       current_price,
            'vwap':        float(raw_vwap.iloc[-1]),
            't3':          t3_val,
            'atr':         atr_val,
            'score':       int(score.iloc[-1]),
            'band_u4':     float(band_u4.iloc[-1]),
            'band_l4':     float(band_l4.iloc[-1]),
            'strategy':    'VWAP'
        }

    def _empty_signals(self, df: pd.DataFrame) -> Dict:
        close = float(df['close'].iloc[-1]) if len(df) > 0 else 0
        return {
            'buy_signal':  False,
            'sell_signal': False,
            'tp_bull':     False,
            'tp_bear':     False,
            'bull_pct':    50.0,
            'bear_pct':    50.0,
            'bias':        'NEUTRAL',
            'close':       close,
            'vwap':        close,
            't3':          close,
            'atr':         0,
            'score':       0,
            'band_u4':     0,
            'band_l4':     0,
            'strategy':    'VWAP'
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
        'open':   close - np.random.uniform(0, 100, 500),
        'high':   close + np.random.uniform(0, 200, 500),
        'low':    close - np.random.uniform(0, 200, 500),
        'close':  close,
        'volume': np.random.uniform(100, 1000, 500)
    }, index=dates)

    strategy = VWAPVolatilityBands()
    signals  = strategy.analyze(df_test)

    print("=" * 60)
    print("VWAP Volatility Bands - Test Results")
    print("=" * 60)
    for k, v in signals.items():
        print(f"{k:<15}: {v}")
    print("=" * 60)
