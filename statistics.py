"""
Seguimiento de Estadísticas y Análisis de Rentabilidad
Base de datos SQLite para histórico completo
"""
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

try:
    from config import Config
except ImportError:
    # Fallback si no está disponible
    class Config:
        DB_PATH = 'trading_bot.db'

logger = logging.getLogger(__name__)


class StatisticsTracker:
    """Rastreador de estadísticas y rentabilidad"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.DB_PATH
        self.conn = None
        self.cursor = None
        
        self._init_database()
        
        logger.info(f"📊 Statistics Tracker inicializado | DB: {self.db_path}")
    
    def _init_database(self):
        """Inicializar base de datos"""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.cursor = self.conn.cursor()
            
            # Tabla de trades
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    quantity REAL NOT NULL,
                    leverage INTEGER,
                    entry_time TIMESTAMP NOT NULL,
                    exit_time TIMESTAMP,
                    pnl REAL,
                    pnl_pct REAL,
                    exit_reason TEXT,
                    ml_confidence REAL,
                    technical_score REAL,
                    status TEXT DEFAULT 'OPEN'
                )
            ''')
            
            # Tabla de señales
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    price REAL,
                    technical_score REAL,
                    ml_confidence REAL,
                    executed BOOLEAN DEFAULT 0
                )
            ''')
            
            # Tabla de balance diario
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_balance (
                    date DATE PRIMARY KEY,
                    balance REAL,
                    pnl REAL,
                    trades_count INTEGER,
                    win_rate REAL
                )
            ''')
            
            self.conn.commit()
            logger.info("✅ Base de datos inicializada")
        
        except Exception as e:
            logger.error(f"❌ Error inicializando DB: {e}")
    
    def record_signal(self, symbol: str, direction: str, price: float,
                     technical_score: float = None, ml_confidence: float = None):
        """Registrar señal generada"""
        try:
            self.cursor.execute('''
                INSERT INTO signals (symbol, direction, timestamp, price, 
                                   technical_score, ml_confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (symbol, direction, datetime.now(), price, 
                  technical_score, ml_confidence))
            
            self.conn.commit()
        
        except Exception as e:
            logger.debug(f"Error registrando señal: {e}")
    
    def record_trade_open(self, symbol: str, direction: str, entry_price: float,
                         quantity: float, leverage: int = None,
                         ml_confidence: float = None, technical_score: float = None) -> int:
        """Registrar apertura de trade"""
        try:
            self.cursor.execute('''
                INSERT INTO trades (symbol, direction, entry_price, quantity, leverage,
                                  entry_time, ml_confidence, technical_score, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
            ''', (symbol, direction, entry_price, quantity, leverage,
                  datetime.now(), ml_confidence, technical_score))
            
            self.conn.commit()
            
            return self.cursor.lastrowid
        
        except Exception as e:
            logger.error(f"❌ Error registrando trade: {e}")
            return -1
    
    def record_trade_close(self, trade_id: int, exit_price: float, 
                          exit_reason: str = "MANUAL"):
        """Registrar cierre de trade"""
        try:
            # Obtener datos del trade
            self.cursor.execute('''
                SELECT entry_price, quantity, direction FROM trades WHERE id = ?
            ''', (trade_id,))
            
            result = self.cursor.fetchone()
            if not result:
                return
            
            entry_price, quantity, direction = result
            
            # Calcular PnL
            if direction == 'LONG':
                pnl = (exit_price - entry_price) * quantity
            else:
                pnl = (entry_price - exit_price) * quantity
            
            pnl_pct = (pnl / (entry_price * quantity)) * 100
            
            # Actualizar
            self.cursor.execute('''
                UPDATE trades 
                SET exit_price = ?, exit_time = ?, pnl = ?, pnl_pct = ?, 
                    exit_reason = ?, status = 'CLOSED'
                WHERE id = ?
            ''', (exit_price, datetime.now(), pnl, pnl_pct, exit_reason, trade_id))
            
            self.conn.commit()
            
            logger.info(f"💾 Trade #{trade_id} cerrado | PnL: ${pnl:+.2f}")
        
        except Exception as e:
            logger.error(f"❌ Error cerrando trade: {e}")
    
    def get_performance_summary(self, days: int = 30) -> Dict:
        """Obtener resumen de performance"""
        try:
            cutoff = datetime.now() - timedelta(days=days)
            
            self.cursor.execute('''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl) as total_pnl,
                    AVG(pnl) as avg_pnl,
                    MAX(pnl) as max_win,
                    MIN(pnl) as max_loss,
                    AVG(pnl_pct) as avg_pnl_pct
                FROM trades
                WHERE exit_time >= ? AND status = 'CLOSED'
            ''', (cutoff,))
            
            result = self.cursor.fetchone()
            
            if not result or not result[0]:
                return self._empty_summary()
            
            total, wins, losses, total_pnl, avg_pnl, max_win, max_loss, avg_pct = result
            
            win_rate = (wins / total * 100) if total > 0 else 0
            
            # Profit factor
            self.cursor.execute('''
                SELECT 
                    SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                    ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)) as gross_loss
                FROM trades
                WHERE exit_time >= ? AND status = 'CLOSED'
            ''', (cutoff,))
            
            pf_result = self.cursor.fetchone()
            gross_profit, gross_loss = pf_result if pf_result else (0, 0)
            
            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0
            
            return {
                'period_days': days,
                'total_trades': total,
                'wins': wins or 0,
                'losses': losses or 0,
                'win_rate': round(win_rate, 2),
                'total_pnl': round(total_pnl or 0, 2),
                'avg_pnl': round(avg_pnl or 0, 2),
                'avg_pnl_pct': round(avg_pct or 0, 2),
                'max_win': round(max_win or 0, 2),
                'max_loss': round(max_loss or 0, 2),
                'profit_factor': round(profit_factor, 2),
                'gross_profit': round(gross_profit or 0, 2),
                'gross_loss': round(gross_loss or 0, 2)
            }
        
        except Exception as e:
            logger.error(f"❌ Error performance: {e}")
            return self._empty_summary()
    
    def _empty_summary(self) -> Dict:
        """Summary vacío"""
        return {
            'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0,
            'total_pnl': 0, 'avg_pnl': 0, 'avg_pnl_pct': 0,
            'max_win': 0, 'max_loss': 0, 'profit_factor': 0
        }
    
    def get_performance_by_symbol(self, days: int = 30) -> List[Dict]:
        """Performance por símbolo"""
        try:
            cutoff = datetime.now() - timedelta(days=days)
            
            self.cursor.execute('''
                SELECT 
                    symbol,
                    COUNT(*) as trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(pnl) as total_pnl,
                    AVG(pnl_pct) as avg_pnl_pct
                FROM trades
                WHERE exit_time >= ? AND status = 'CLOSED'
                GROUP BY symbol
                ORDER BY total_pnl DESC
            ''', (cutoff,))
            
            results = []
            for row in self.cursor.fetchall():
                symbol, trades, wins, pnl, avg_pct = row
                win_rate = (wins / trades * 100) if trades > 0 else 0
                
                results.append({
                    'symbol': symbol,
                    'trades': trades,
                    'win_rate': round(win_rate, 2),
                    'total_pnl': round(pnl or 0, 2),
                    'avg_pnl_pct': round(avg_pct or 0, 2)
                })
            
            return results
        
        except Exception as e:
            logger.debug(f"Error performance by symbol: {e}")
            return []
    
    def get_recent_trades(self, limit: int = 10) -> List[Dict]:
        """Obtener trades recientes"""
        try:
            self.cursor.execute('''
                SELECT symbol, direction, entry_price, exit_price, pnl, 
                       pnl_pct, entry_time, exit_time, exit_reason
                FROM trades
                WHERE status = 'CLOSED'
                ORDER BY exit_time DESC
                LIMIT ?
            ''', (limit,))
            
            trades = []
            for row in self.cursor.fetchall():
                trades.append({
                    'symbol': row[0],
                    'direction': row[1],
                    'entry_price': round(row[2], 6),
                    'exit_price': round(row[3], 6),
                    'pnl': round(row[4], 2),
                    'pnl_pct': round(row[5], 2),
                    'entry_time': row[6],
                    'exit_time': row[7],
                    'exit_reason': row[8]
                })
            
            return trades
        
        except Exception as e:
            logger.debug(f"Error recent trades: {e}")
            return []
    
    def update_daily_balance(self, balance: float, pnl: float):
        """Actualizar balance diario"""
        try:
            today = datetime.now().date()
            
            # Contar trades del día
            self.cursor.execute('''
                SELECT COUNT(*) FROM trades 
                WHERE DATE(exit_time) = ? AND status = 'CLOSED'
            ''', (today,))
            
            trades_count = self.cursor.fetchone()[0]
            
            # Win rate del día
            self.cursor.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                FROM trades
                WHERE DATE(exit_time) = ? AND status = 'CLOSED'
            ''', (today,))
            
            result = self.cursor.fetchone()
            total, wins = result if result else (0, 0)
            win_rate = (wins / total * 100) if total > 0 else 0
            
            # Insertar o actualizar
            self.cursor.execute('''
                INSERT OR REPLACE INTO daily_balance (date, balance, pnl, trades_count, win_rate)
                VALUES (?, ?, ?, ?, ?)
            ''', (today, balance, pnl, trades_count, win_rate))
            
            self.conn.commit()
        
        except Exception as e:
            logger.debug(f"Error daily balance: {e}")
    
    def get_equity_curve(self, days: int = 30) -> List[Dict]:
        """Obtener curva de equity"""
        try:
            cutoff = datetime.now().date() - timedelta(days=days)
            
            self.cursor.execute('''
                SELECT date, balance, pnl
                FROM daily_balance
                WHERE date >= ?
                ORDER BY date ASC
            ''', (cutoff,))
            
            curve = []
            for row in self.cursor.fetchall():
                curve.append({
                    'date': str(row[0]),
                    'balance': round(row[1], 2),
                    'pnl': round(row[2], 2)
                })
            
            return curve
        
        except Exception as e:
            logger.debug(f"Error equity curve: {e}")
            return []
    
    def close(self):
        """Cerrar conexión"""
        if self.conn:
            self.conn.close()
