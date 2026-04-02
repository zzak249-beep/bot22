"""
Estrategia Zero Lag EMA + Trend Reversal Probability
"""

import numpy as np
import pandas as pd
from typing import Dict


def zlema(series: pd.Series, length: int) -> pd.Series:
    """
    Zero Lag Exponential Moving Average
    Más reactivo que EMA tradicional, menos lag
    """
    lag = (length - 1) // 2
    ema_data = series + (series.diff(lag))
    zlema_values = ema_data.ewm(span=length, adjust=False).mean()
    return zlema_values


def calculate_bands(zlema_series: pd.Series, mult: float, period: int) -> tuple:
    """
    Calcular bandas superior e inferior basadas en desviación estándar
    """
    std = zlema_series.rolling(window=period).std()
    upper = zlema_series + (std * mult)
    lower = zlema_series - (std * mult)
    return upper, lower


def stochastic_oscillator(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """
    Oscilador estocástico para detectar sobrecompra/sobreventa
    """
    lowest_low = low.rolling(window=period).min()
    highest_high = high.rolling(window=period).max()
    
    k = 100 * (close - lowest_low) / (highest_high - lowest_low)
    k = k.fillna(50)  # Valor neutro por defecto
    
    return k


def calculate_trend(close: pd.Series, zlema_series: pd.Series) -> int:
    """
    Determinar tendencia actual
    1 = alcista, -1 = bajista
    """
    current_price = close.iloc[-1]
    current_zlema = zlema_series.iloc[-1]
    
    # Tendencia basada en precio vs ZLEMA
    if current_price > current_zlema:
        return 1  # Alcista
    else:
        return -1  # Bajista


def calculate_reversal_probability(
    close: pd.Series,
    zlema_series: pd.Series,
    upper_band: pd.Series,
    lower_band: pd.Series,
    osc: pd.Series
) -> float:
    """
    Calcular probabilidad de reversión basada en múltiples factores
    
    Factores considerados:
    - Distancia del precio a las bandas
    - Nivel del oscilador
    - Momentum del precio
    
    Retorna: 0.0 - 1.0 (0% - 100%)
    """
    current_price = close.iloc[-1]
    current_zlema = zlema_series.iloc[-1]
    current_upper = upper_band.iloc[-1]
    current_lower = lower_band.iloc[-1]
    current_osc = osc.iloc[-1]
    
    # Factor 1: Distancia a las bandas (0-40%)
    band_range = current_upper - current_lower
    if band_range > 0:
        if current_price > current_zlema:
            # Precio arriba de ZLEMA
            distance_to_band = (current_upper - current_price) / band_range
            band_factor = max(0, min(0.4, 0.4 * (1 - distance_to_band)))
        else:
            # Precio abajo de ZLEMA
            distance_to_band = (current_price - current_lower) / band_range
            band_factor = max(0, min(0.4, 0.4 * (1 - distance_to_band)))
    else:
        band_factor = 0
    
    # Factor 2: Oscilador (0-35%)
    if current_osc > 80:
        osc_factor = 0.35  # Sobrecompra fuerte
    elif current_osc > 70:
        osc_factor = 0.25  # Sobrecompra
    elif current_osc < 20:
        osc_factor = 0.35  # Sobreventa fuerte
    elif current_osc < 30:
        osc_factor = 0.25  # Sobreventa
    else:
        osc_factor = 0  # Zona neutral
    
    # Factor 3: Momentum (0-25%)
    if len(close) >= 5:
        recent_change = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]
        momentum_factor = min(0.25, abs(recent_change) * 100)
    else:
        momentum_factor = 0
    
    # Probabilidad total (suma de factores)
    probability = band_factor + osc_factor + momentum_factor
    
    return min(1.0, max(0.0, probability))


def detect_entry_signals(
    close: pd.Series,
    zlema_series: pd.Series,
    upper_band: pd.Series,
    lower_band: pd.Series,
    osc: pd.Series,
    trend: int
) -> Dict[str, bool]:
    """
    Detectar señales de entrada alcistas y bajistas
    """
    current_price = close.iloc[-1]
    prev_price = close.iloc[-2] if len(close) >= 2 else current_price
    
    current_zlema = zlema_series.iloc[-1]
    current_lower = lower_band.iloc[-1]
    current_upper = upper_band.iloc[-1]
    current_osc = osc.iloc[-1]
    
    # Señal alcista (LONG)
    bullish_entry = (
        trend == 1 and  # Tendencia alcista
        current_price > current_zlema and  # Precio sobre ZLEMA
        prev_price <= current_zlema and  # Cruce reciente
        current_osc < 80  # No sobrecomprado
    )
    
    # O rebote en banda inferior
    bullish_bounce = (
        current_price <= current_lower and  # Toca banda inferior
        current_osc < 30 and  # Sobreventa
        close.iloc[-1] > close.iloc[-2]  # Empezando a subir
    )
    
    # Señal bajista (SHORT)
    bearish_entry = (
        trend == -1 and  # Tendencia bajista
        current_price < current_zlema and  # Precio bajo ZLEMA
        prev_price >= current_zlema and  # Cruce reciente
        current_osc > 20  # No sobrevendido
    )
    
    # O rechazo en banda superior
    bearish_rejection = (
        current_price >= current_upper and  # Toca banda superior
        current_osc > 70 and  # Sobrecompra
        close.iloc[-1] < close.iloc[-2]  # Empezando a bajar
    )
    
    return {
        "bullish_entry": bullish_entry or bullish_bounce,
        "bearish_entry": bearish_entry or bearish_rejection
    }


def calculate_signals(
    df: pd.DataFrame,
    zlema_length: int = 70,
    band_mult: float = 1.2,
    osc_period: int = 20
) -> Dict:
    """
    Función principal: calcular todas las señales
    
    Args:
        df: DataFrame con columnas ['open', 'high', 'low', 'close', 'volume']
        zlema_length: Longitud del Zero Lag EMA
        band_mult: Multiplicador de bandas
        osc_period: Período del oscilador
    
    Returns:
        Dictionary con señales:
        - close: precio actual
        - trend: 1 (alcista) o -1 (bajista)
        - probability: probabilidad de reversión (0.0-1.0)
        - bullish_entry: señal de entrada long
        - bearish_entry: señal de entrada short
    """
    # Validar datos
    if df is None or len(df) < max(zlema_length, osc_period) + 10:
        raise ValueError(f"Datos insuficientes: se requieren al menos {max(zlema_length, osc_period) + 10} velas")
    
    required_cols = ['open', 'high', 'low', 'close']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Columna requerida faltante: {col}")
    
    # Calcular indicadores
    zlema_values = zlema(df['close'], zlema_length)
    upper_band, lower_band = calculate_bands(zlema_values, band_mult, zlema_length)
    osc = stochastic_oscillator(df['high'], df['low'], df['close'], osc_period)
    
    # Determinar tendencia
    trend = calculate_trend(df['close'], zlema_values)
    
    # Calcular probabilidad de reversión
    probability = calculate_reversal_probability(
        df['close'],
        zlema_values,
        upper_band,
        lower_band,
        osc
    )
    
    # Detectar señales de entrada
    entry_signals = detect_entry_signals(
        df['close'],
        zlema_values,
        upper_band,
        lower_band,
        osc,
        trend
    )
    
    return {
        "close": float(df['close'].iloc[-1]),
        "zlema": float(zlema_values.iloc[-1]),
        "upper_band": float(upper_band.iloc[-1]),
        "lower_band": float(lower_band.iloc[-1]),
        "oscillator": float(osc.iloc[-1]),
        "trend": trend,
        "probability": probability,
        "bullish_entry": entry_signals["bullish_entry"],
        "bearish_entry": entry_signals["bearish_entry"]
    }


def backtest_strategy(
    df: pd.DataFrame,
    zlema_length: int = 70,
    band_mult: float = 1.2,
    osc_period: int = 20,
    entry_max_prob: float = 0.65,
    exit_prob: float = 0.84
) -> Dict:
    """
    Backtesting simple de la estrategia
    
    Returns:
        Estadísticas del backtest
    """
    if len(df) < max(zlema_length, osc_period) + 50:
        raise ValueError("Datos insuficientes para backtesting")
    
    trades = []
    in_position = False
    position_type = None
    entry_price = 0
    
    # Iterar por cada vela (empezando después de tener suficientes datos)
    start_idx = max(zlema_length, osc_period) + 10
    
    for i in range(start_idx, len(df)):
        current_df = df.iloc[:i+1]
        
        try:
            signals = calculate_signals(current_df, zlema_length, band_mult, osc_period)
        except Exception:
            continue
        
        current_price = signals['close']
        
        # Lógica de salida
        if in_position:
            exit_triggered = False
            
            if signals['probability'] >= exit_prob:
                exit_triggered = True
                reason = "High reversal probability"
            elif position_type == "long" and signals['trend'] == -1:
                exit_triggered = True
                reason = "Trend reversal"
            elif position_type == "short" and signals['trend'] == 1:
                exit_triggered = True
                reason = "Trend reversal"
            
            if exit_triggered:
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                if position_type == "short":
                    pnl_pct = -pnl_pct
                
                trades.append({
                    'type': position_type,
                    'entry': entry_price,
                    'exit': current_price,
                    'pnl_pct': pnl_pct,
                    'reason': reason
                })
                
                in_position = False
                position_type = None
        
        # Lógica de entrada
        if not in_position:
            if signals['probability'] < entry_max_prob:
                if signals['bullish_entry']:
                    in_position = True
                    position_type = "long"
                    entry_price = current_price
                elif signals['bearish_entry']:
                    in_position = True
                    position_type = "short"
                    entry_price = current_price
    
    # Calcular estadísticas
    if not trades:
        return {
            'total_trades': 0,
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'profit_factor': 0,
            'total_pnl': 0
        }
    
    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]
    
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0
    
    gross_profit = sum(t['pnl_pct'] for t in wins)
    gross_loss = abs(sum(t['pnl_pct'] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
    
    return {
        'total_trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'total_pnl': sum(t['pnl_pct'] for t in trades),
        'trades': trades
    }
