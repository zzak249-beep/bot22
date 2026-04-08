"""
Trade Analyzer - Sistema de aprendizaje y análisis de rendimiento
Permite al bot aprender de trades pasados y optimizar estrategia
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import statistics

logger = logging.getLogger("TradeAnalyzer")


class TradeAnalyzer:
    """
    Analizador de trades con métricas de rendimiento y aprendizaje
    """
    
    def __init__(self, trades_file: str, metrics_file: str):
        self.trades_file = trades_file
        self.metrics_file = metrics_file
        self.trades = self._load_trades()
        self.metrics = self._load_metrics()
    
    def _load_trades(self) -> List[Dict]:
        """Cargar historial de trades"""
        try:
            with open(self.trades_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
    
    def _save_trades(self):
        """Guardar trades"""
        try:
            with open(self.trades_file, 'w') as f:
                json.dump(self.trades, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando trades: {e}")
    
    def _load_metrics(self) -> Dict:
        """Cargar métricas"""
        try:
            with open(self.metrics_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return self._default_metrics()
    
    def _save_metrics(self):
        """Guardar métricas"""
        try:
            with open(self.metrics_file, 'w') as f:
                json.dump(self.metrics, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando métricas: {e}")
    
    def _default_metrics(self) -> Dict:
        """Métricas por defecto"""
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "total_commissions": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "avg_trade_duration_hours": 0.0,
            "strategy_performance": {},
            "tp_hit_distribution": {
                "1": 0, "2": 0, "3": 0, "4": 0, "5": 0
            },
            "last_updated": None
        }
    
    def record_trade(self, trade: Dict):
        """Registrar un nuevo trade y actualizar métricas"""
        # Agregar timestamp si no existe
        if "exit_time" not in trade:
            trade["exit_time"] = datetime.utcnow().isoformat()
        
        # Agregar a historial
        self.trades.append(trade)
        
        # Limitar a últimos 1000 trades
        if len(self.trades) > 1000:
            self.trades = self.trades[-1000:]
        
        self._save_trades()
        
        # Recalcular métricas
        self._recalculate_metrics()
        
        logger.info(f"✅ Trade registrado: {trade['direction']} | "
                   f"PnL: ${trade['pnl_net']:.2f}")
    
    def _recalculate_metrics(self):
        """Recalcular todas las métricas"""
        if not self.trades:
            self.metrics = self._default_metrics()
            return
        
        # Básicas
        total_trades = len(self.trades)
        winning_trades = sum(1 for t in self.trades if t['pnl_net'] > 0)
        losing_trades = sum(1 for t in self.trades if t['pnl_net'] <= 0)
        
        # Win rate
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        # PnL
        total_pnl = sum(t['pnl_net'] for t in self.trades)
        total_commissions = sum(t.get('commission_total', 0) for t in self.trades)
        
        # Averages
        wins = [t['pnl_net'] for t in self.trades if t['pnl_net'] > 0]
        losses = [t['pnl_net'] for t in self.trades if t['pnl_net'] <= 0]
        
        avg_win = statistics.mean(wins) if wins else 0
        avg_loss = abs(statistics.mean(losses)) if losses else 0
        
        # Profit factor
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0
        profit_factor = (total_wins / total_losses) if total_losses > 0 else 0
        
        # Best/Worst
        best_trade = max(t['pnl_net'] for t in self.trades)
        worst_trade = min(t['pnl_net'] for t in self.trades)
        
        # Duración promedio
        durations = []
        for t in self.trades:
            if 'entry_time' in t and 'exit_time' in t:
                try:
                    entry = datetime.fromisoformat(t['entry_time'])
                    exit = datetime.fromisoformat(t['exit_time'])
                    duration = (exit - entry).total_seconds() / 3600
                    durations.append(duration)
                except:
                    pass
        
        avg_duration = statistics.mean(durations) if durations else 0
        
        # Performance por estrategia
        strategy_perf = {}
        for t in self.trades:
            strategy = t.get('strategy', 'UNKNOWN')
            if strategy not in strategy_perf:
                strategy_perf[strategy] = {
                    'total': 0,
                    'wins': 0,
                    'pnl': 0.0
                }
            
            strategy_perf[strategy]['total'] += 1
            if t['pnl_net'] > 0:
                strategy_perf[strategy]['wins'] += 1
            strategy_perf[strategy]['pnl'] += t['pnl_net']
        
        # Calcular win rate por estrategia
        for strategy in strategy_perf:
            total = strategy_perf[strategy]['total']
            wins = strategy_perf[strategy]['wins']
            strategy_perf[strategy]['win_rate'] = (wins / total * 100) if total > 0 else 0
        
        # Distribución de TPs alcanzados
        tp_dist = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
        for t in self.trades:
            tp_hits = t.get('tp_hits', [])
            max_tp = max(tp_hits) if tp_hits else 0
            if max_tp > 0:
                tp_dist[str(max_tp)] += 1
        
        # Max Drawdown (simplificado)
        max_dd = self._calculate_max_drawdown()
        
        # Actualizar métricas
        self.metrics = {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "total_commissions": round(total_commissions, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "best_trade": round(best_trade, 2),
            "worst_trade": round(worst_trade, 2),
            "avg_trade_duration_hours": round(avg_duration, 2),
            "strategy_performance": strategy_perf,
            "tp_hit_distribution": tp_dist,
            "last_updated": datetime.utcnow().isoformat()
        }
        
        self._save_metrics()
    
    def _calculate_max_drawdown(self) -> float:
        """Calcular max drawdown"""
        if not self.trades:
            return 0.0
        
        # Equity curve
        equity = [0]
        for t in self.trades:
            equity.append(equity[-1] + t['pnl_net'])
        
        # Max drawdown
        peak = equity[0]
        max_dd = 0
        
        for value in equity:
            if value > peak:
                peak = value
            dd = peak - value
            if dd > max_dd:
                max_dd = dd
        
        return max_dd
    
    def get_metrics(self) -> Dict:
        """Obtener métricas actuales"""
        return self.metrics
    
    def get_recent_trades(self, count: int = 10) -> List[Dict]:
        """Obtener trades recientes"""
        return self.trades[-count:] if len(self.trades) >= count else self.trades
    
    def get_performance_report(self) -> str:
        """Generar reporte de rendimiento"""
        m = self.metrics
        
        report = f"""
╔════════════════════════════════════════════════════════════╗
║           REPORTE DE RENDIMIENTO DEL BOT                   ║
╚════════════════════════════════════════════════════════════╝

📊 ESTADÍSTICAS GENERALES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total de Trades:       {m['total_trades']}
Trades Ganadores:      {m['winning_trades']} ({m['win_rate']:.1f}%)
Trades Perdedores:     {m['losing_trades']}

💰 RENDIMIENTO FINANCIERO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PnL Total:            ${m['total_pnl']:.2f}
Comisiones Pagadas:   ${m['total_commissions']:.2f}
Profit Factor:         {m['profit_factor']:.2f}
Max Drawdown:         ${m['max_drawdown']:.2f}

📈 PROMEDIOS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ganancia Promedio:    ${m['avg_win']:.2f}
Pérdida Promedio:     ${m['avg_loss']:.2f}
Duración Promedio:     {m['avg_trade_duration_hours']:.1f}h

🏆 EXTREMOS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mejor Trade:          ${m['best_trade']:.2f}
Peor Trade:           ${m['worst_trade']:.2f}

🎯 DISTRIBUCIÓN DE TPs
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        
        for tp, count in m['tp_hit_distribution'].items():
            report += f"TP{tp}: {count} trades\n"
        
        report += "\n📊 RENDIMIENTO POR ESTRATEGIA\n"
        report += "━" * 60 + "\n"
        
        for strategy, perf in m['strategy_performance'].items():
            report += f"{strategy}: {perf['total']} trades | "
            report += f"Win Rate: {perf['win_rate']:.1f}% | "
            report += f"PnL: ${perf['pnl']:.2f}\n"
        
        report += "\n" + "=" * 60
        
        return report
    
    def analyze_errors(self) -> Dict:
        """
        Analizar errores comunes y patrones de pérdida
        """
        if not self.trades:
            return {"errors": [], "recommendations": []}
        
        errors = []
        recommendations = []
        
        # Análisis de trades perdedores
        losing_trades = [t for t in self.trades if t['pnl_net'] < 0]
        
        if losing_trades:
            # 1. Check si muchos trades no alcanzan TP1
            no_tp_hits = sum(1 for t in losing_trades if not t.get('tp_hits'))
            if no_tp_hits / len(losing_trades) > 0.5:
                errors.append("Más del 50% de trades perdedores no alcanzaron ningún TP")
                recommendations.append("Considera reducir ATR_MULTIPLIER para TPs más cercanos")
            
            # 2. Check win rate por estrategia
            if 'strategy_performance' in self.metrics:
                for strategy, perf in self.metrics['strategy_performance'].items():
                    if perf['win_rate'] < 40:
                        errors.append(f"Estrategia {strategy} tiene win rate bajo: {perf['win_rate']:.1f}%")
                        recommendations.append(f"Revisar parámetros de {strategy} o desactivarla")
            
            # 3. Check duración de trades
            long_losing_trades = [t for t in losing_trades 
                                 if 'entry_time' in t and 'exit_time' in t]
            
            if long_losing_trades:
                try:
                    avg_losing_duration = statistics.mean([
                        (datetime.fromisoformat(t['exit_time']) - 
                         datetime.fromisoformat(t['entry_time'])).total_seconds() / 3600
                        for t in long_losing_trades
                    ])
                    
                    if avg_losing_duration > 24:
                        errors.append(f"Trades perdedores duran demasiado: {avg_losing_duration:.1f}h")
                        recommendations.append("Implementar trailing stop o time-based exit")
                except:
                    pass
            
            # 4. Check comisiones excesivas
            avg_commission_pct = (self.metrics['total_commissions'] / 
                                 abs(self.metrics['total_pnl']) * 100 
                                 if self.metrics['total_pnl'] != 0 else 0)
            
            if avg_commission_pct > 30:
                errors.append(f"Comisiones son {avg_commission_pct:.1f}% del PnL")
                recommendations.append("Reducir frecuencia de trades o usar órdenes limit")
        
        return {
            "errors": errors,
            "recommendations": recommendations,
            "analysis_time": datetime.utcnow().isoformat()
        }


# ══════════════════════════════════════════════════════════════════════════════
# TESTING
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    analyzer = TradeAnalyzer("/tmp/test_trades.json", "/tmp/test_metrics.json")
    
    # Simular algunos trades
    test_trades = [
        {
            "symbol": "BTC-USDT",
            "direction": "BUY",
            "entry": 40000,
            "exit": 40800,
            "qty": 0.1,
            "entry_time": "2024-01-01T10:00:00",
            "exit_time": "2024-01-01T14:00:00",
            "pnl_raw": 80,
            "pnl_net": 75,
            "commission_total": 5,
            "tp_hits": [1, 2],
            "reason": "TP2_HIT",
            "strategy": "HYBRID"
        },
        {
            "symbol": "BTC-USDT",
            "direction": "SELL",
            "entry": 40800,
            "exit": 40700,
            "qty": 0.1,
            "entry_time": "2024-01-01T15:00:00",
            "exit_time": "2024-01-01T16:00:00",
            "pnl_raw": 10,
            "pnl_net": 5,
            "commission_total": 5,
            "tp_hits": [1],
            "reason": "TP1_HIT",
            "strategy": "VWAP"
        }
    ]
    
    for trade in test_trades:
        analyzer.record_trade(trade)
    
    print(analyzer.get_performance_report())
    print("\n" + "=" * 60)
    
    errors_analysis = analyzer.analyze_errors()
    print("\n🔍 ANÁLISIS DE ERRORES:")
    for error in errors_analysis['errors']:
        print(f"  ❌ {error}")
    
    print("\n💡 RECOMENDACIONES:")
    for rec in errors_analysis['recommendations']:
        print(f"  ✅ {rec}")
