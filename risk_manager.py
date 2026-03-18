"""
Gestión de Riesgo Avanzada
- Position sizing dinámico
- Trailing stop loss
- Max drawdown protection
- Kelly Criterion
"""
import logging
import numpy as np
from typing import Dict, Optional
from datetime import datetime, timedelta
from config import Config

logger = logging.getLogger(__name__)


class RiskManager:
    """Gestor de riesgo avanzado"""
    
    def __init__(self):
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_reset_time = datetime.now().date()
        
        self.peak_balance = 0.0
        self.current_drawdown = 0.0
        self.max_drawdown = 0.0
        
        self.trade_history = []
        
        logger.info("🛡️ Risk Manager inicializado")
    
    def reset_daily_stats(self):
        """Resetear estadísticas diarias"""
        today = datetime.now().date()
        
        if today != self.daily_reset_time:
            logger.info(f"📅 Nuevo día - Reset stats | PnL anterior: ${self.daily_pnl:+.2f}")
            
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.daily_reset_time = today
    
    def update_balance(self, balance: float):
        """Actualizar balance y calcular drawdown"""
        if balance > self.peak_balance:
            self.peak_balance = balance
            self.current_drawdown = 0.0
        else:
            self.current_drawdown = ((self.peak_balance - balance) / self.peak_balance) * 100
            
            if self.current_drawdown > self.max_drawdown:
                self.max_drawdown = self.current_drawdown
    
    def can_open_trade(self, balance: float, reason: str = "") -> tuple[bool, str]:
        """
        Verificar si se puede abrir un nuevo trade
        
        Returns:
            (can_trade, reason)
        """
        self.reset_daily_stats()
        
        # Verificar pérdida diaria máxima
        if self.daily_pnl <= -Config.MAX_DAILY_LOSS:
            return False, f"Max daily loss alcanzado (${self.daily_pnl:.2f})"
        
        # Verificar drawdown máximo
        self.update_balance(balance)
        if self.current_drawdown >= Config.MAX_DRAWDOWN_PCT:
            return False, f"Max drawdown alcanzado ({self.current_drawdown:.2f}%)"
        
        # Verificar número de trades abiertos (esto se maneja fuera, pero validamos)
        # En el bot principal se controla con len(open_trades)
        
        return True, "OK"
    
    def calculate_position_size(self, symbol: str, price: float, 
                               volatility: float = None, 
                               balance: float = None) -> float:
        """
        Calcular tamaño de posición dinámico
        
        Args:
            symbol: Par a tradear
            price: Precio actual
            volatility: Volatilidad (opcional)
            balance: Balance disponible (opcional)
        
        Returns:
            Cantidad a tradear
        """
        base_size = Config.MAX_POSITION_SIZE
        
        # Ajustar por volatilidad
        if volatility:
            # Menor volatilidad = mayor posición
            # Mayor volatilidad = menor posición
            vol_factor = max(0.5, min(1.5, 1 / (volatility / 100)))
            base_size *= vol_factor
        
        # Ajustar por drawdown actual
        if self.current_drawdown > 0:
            # Reducir tamaño si hay drawdown
            dd_factor = max(0.3, 1 - (self.current_drawdown / (Config.MAX_DRAWDOWN_PCT * 2)))
            base_size *= dd_factor
        
        # Calcular cantidad
        quantity = base_size / price
        
        return round(quantity, 6)
    
    def calculate_stop_loss(self, entry_price: float, direction: str, 
                           atr: float = None) -> float:
        """
        Calcular stop loss
        
        Args:
            entry_price: Precio de entrada
            direction: 'LONG' o 'SHORT'
            atr: Average True Range (opcional)
        
        Returns:
            Precio de stop loss
        """
        sl_pct = Config.STOP_LOSS_PCT
        
        # Ajustar por ATR si está disponible
        if atr:
            sl_pct = max(Config.STOP_LOSS_PCT, (atr / entry_price) * 100)
        
        if direction == 'LONG':
            sl_price = entry_price * (1 - sl_pct / 100)
        else:
            sl_price = entry_price * (1 + sl_pct / 100)
        
        return round(sl_price, 8)
    
    def calculate_take_profit(self, entry_price: float, direction: str) -> float:
        """Calcular take profit"""
        tp_pct = Config.TAKE_PROFIT_PCT
        
        if direction == 'LONG':
            tp_price = entry_price * (1 + tp_pct / 100)
        else:
            tp_price = entry_price * (1 - tp_pct / 100)
        
        return round(tp_price, 8)
    
    def update_trailing_stop(self, trade: Dict, current_price: float) -> Optional[float]:
        """
        Actualizar trailing stop loss
        
        Args:
            trade: Diccionario con info del trade
            current_price: Precio actual
        
        Returns:
            Nuevo SL o None si no cambió
        """
        if not Config.TRAILING_STOP_ENABLED:
            return None
        
        direction = trade.get('direction')
        entry_price = trade.get('entry_price')
        current_sl = trade.get('sl_price')
        
        # Calcular profit %
        if direction == 'LONG':
            profit_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            profit_pct = ((entry_price - current_price) / entry_price) * 100
        
        # Activar trailing stop si alcanzó threshold
        if profit_pct < Config.TRAILING_STOP_ACTIVATION:
            return None
        
        # Calcular nuevo SL
        if direction == 'LONG':
            new_sl = current_price * (1 - Config.TRAILING_STOP_DISTANCE / 100)
            
            # Solo actualizar si es mejor que el actual
            if new_sl > current_sl:
                logger.info(f"📈 Trailing SL actualizado: ${current_sl:.4f} → ${new_sl:.4f}")
                return round(new_sl, 8)
        
        else:  # SHORT
            new_sl = current_price * (1 + Config.TRAILING_STOP_DISTANCE / 100)
            
            if new_sl < current_sl:
                logger.info(f"📉 Trailing SL actualizado: ${current_sl:.4f} → ${new_sl:.4f}")
                return round(new_sl, 8)
        
        return None
    
    def record_trade(self, trade_data: Dict):
        """Registrar trade completado"""
        self.trade_history.append({
            **trade_data,
            'timestamp': datetime.now()
        })
        
        # Actualizar stats diarias
        pnl = trade_data.get('pnl', 0)
        self.daily_pnl += pnl
        self.daily_trades += 1
        
        # Limitar historial
        if len(self.trade_history) > 1000:
            self.trade_history = self.trade_history[-1000:]
    
    def calculate_win_rate(self, period_days: int = 7) -> Dict:
        """Calcular win rate"""
        if not self.trade_history:
            return {'win_rate': 0, 'total_trades': 0}
        
        cutoff = datetime.now() - timedelta(days=period_days)
        recent_trades = [t for t in self.trade_history if t['timestamp'] > cutoff]
        
        if not recent_trades:
            return {'win_rate': 0, 'total_trades': 0}
        
        wins = sum(1 for t in recent_trades if t.get('pnl', 0) > 0)
        total = len(recent_trades)
        
        win_rate = (wins / total) * 100 if total > 0 else 0
        
        return {
            'win_rate': round(win_rate, 2),
            'wins': wins,
            'losses': total - wins,
            'total_trades': total,
            'period_days': period_days
        }
    
    def calculate_profit_factor(self) -> float:
        """Calcular profit factor"""
        if not self.trade_history:
            return 0.0
        
        gross_profit = sum(t.get('pnl', 0) for t in self.trade_history if t.get('pnl', 0) > 0)
        gross_loss = abs(sum(t.get('pnl', 0) for t in self.trade_history if t.get('pnl', 0) < 0))
        
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0
        
        return round(gross_profit / gross_loss, 2)
    
    def calculate_sharpe_ratio(self, risk_free_rate: float = 0.02) -> float:
        """
        Calcular Sharpe Ratio
        
        Args:
            risk_free_rate: Tasa libre de riesgo anualizada
        
        Returns:
            Sharpe ratio
        """
        if len(self.trade_history) < 2:
            return 0.0
        
        returns = [t.get('pnl', 0) / Config.MAX_POSITION_SIZE for t in self.trade_history]
        
        avg_return = np.mean(returns)
        std_return = np.std(returns)
        
        if std_return == 0:
            return 0.0
        
        # Anualizar (asumiendo trades diarios)
        sharpe = (avg_return - risk_free_rate / 365) / std_return * np.sqrt(365)
        
        return round(sharpe, 2)
    
    def get_risk_metrics(self) -> Dict:
        """Obtener todas las métricas de riesgo"""
        win_rate_data = self.calculate_win_rate(7)
        
        return {
            'daily_pnl': round(self.daily_pnl, 2),
            'daily_trades': self.daily_trades,
            'daily_loss_limit': Config.MAX_DAILY_LOSS,
            'remaining_daily_loss': round(Config.MAX_DAILY_LOSS + self.daily_pnl, 2),
            'current_drawdown': round(self.current_drawdown, 2),
            'max_drawdown': round(self.max_drawdown, 2),
            'max_drawdown_limit': Config.MAX_DRAWDOWN_PCT,
            'win_rate': win_rate_data['win_rate'],
            'total_trades_7d': win_rate_data['total_trades'],
            'profit_factor': self.calculate_profit_factor(),
            'sharpe_ratio': self.calculate_sharpe_ratio(),
            'peak_balance': round(self.peak_balance, 2)
        }
    
    def calculate_kelly_criterion(self, win_rate: float, avg_win: float, 
                                  avg_loss: float) -> float:
        """
        Calcular Kelly Criterion para tamaño óptimo de posición
        
        Args:
            win_rate: Tasa de victorias (0-1)
            avg_win: Ganancia promedio
            avg_loss: Pérdida promedio
        
        Returns:
            Fracción óptima del capital (0-1)
        """
        if avg_loss == 0 or win_rate == 0:
            return 0.0
        
        win_loss_ratio = avg_win / avg_loss
        kelly = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
        
        # Kelly conservador (half-kelly)
        kelly_conservative = kelly * 0.5
        
        # Limitar entre 0 y 0.25 (máximo 25% del capital)
        kelly_safe = max(0.0, min(0.25, kelly_conservative))
        
        return round(kelly_safe, 4)
    
    def should_reduce_risk(self) -> bool:
        """Determinar si se debe reducir riesgo"""
        # Reducir si:
        # 1. Drawdown cercano al límite
        # 2. Pérdida diaria cercana al límite
        # 3. Win rate muy bajo
        
        if self.current_drawdown >= Config.MAX_DRAWDOWN_PCT * 0.7:
            return True
        
        if self.daily_pnl <= -Config.MAX_DAILY_LOSS * 0.7:
            return True
        
        win_rate_data = self.calculate_win_rate(7)
        if win_rate_data['total_trades'] >= 10 and win_rate_data['win_rate'] < 30:
            return True
        
        return False
