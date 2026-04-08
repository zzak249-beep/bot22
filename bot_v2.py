"""
🚀 Advanced Trading Bot - BingX Edition V2
Estrategia dual: Sniper EMA + VWAP Volatility Bands [BOSWaves]
Gestión de comisiones, aprendizaje de errores y optimización
"""
import asyncio
import logging
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

from strategy_vwap import VWAPVolatilityBands
from strategy_sniper import SniperStrategy
from bingx_client import BingXClient
from telegram_bot import TelegramBot
from risk_manager import RiskManager
from trade_analyzer import TradeAnalyzer

# ══════════════════════════════════════════════════════════════════════════════
# LOGGING CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("/tmp/bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TradingBot")

# ══════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT VARIABLES WITH SAFE DEFAULTS
# ══════════════════════════════════════════════════════════════════════════════

def get_env(key: str, default: str = None, required: bool = False) -> str:
    """Obtener variable de entorno con validación"""
    value = os.environ.get(key, default)
    if required and not value:
        raise ValueError(f"❌ Variable de entorno requerida: {key}")
    return value

# API Keys (REQUERIDAS)
BINGX_API_KEY = get_env("BINGX_API_KEY", required=True)
BINGX_SECRET = get_env("BINGX_SECRET", required=True)
TG_TOKEN = get_env("TG_TOKEN", required=True)
TG_CHAT_ID = get_env("TG_CHAT_ID", required=True)

# Trading Parameters
SYMBOL = get_env("SYMBOL", "BTC-USDT")
TIMEFRAME = get_env("TIMEFRAME", "15m")
RISK_PCT = float(get_env("RISK_PCT", "1.0"))
LEVERAGE = int(get_env("LEVERAGE", "10"))
ATR_MULTIPLIER = float(get_env("ATR_MULTIPLIER", "1.5"))
POLL_SECONDS = int(get_env("POLL_SECONDS", "60"))
SIGNALS_ONLY = get_env("SIGNALS_ONLY", "false").lower() == "true"
HEARTBEAT_HOURS = int(get_env("HEARTBEAT_HOURS", "4"))

# Strategy Selection
STRATEGY = get_env("STRATEGY", "hybrid")  # "sniper", "vwap", or "hybrid"
MIN_SCORE_DIFF = float(get_env("MIN_SCORE_DIFF", "40.0"))  # Para señales STRONG

# Commission Settings (BingX fees)
MAKER_FEE = float(get_env("MAKER_FEE", "0.0002"))  # 0.02%
TAKER_FEE = float(get_env("TAKER_FEE", "0.0005"))  # 0.05%

# State files
STATE_FILE = "/tmp/bot_state.json"
TRADES_FILE = "/tmp/trades_history.json"
METRICS_FILE = "/tmp/performance_metrics.json"

# ══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def klines_to_df(klines: list) -> pd.DataFrame:
    """Convertir klines a DataFrame de pandas"""
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    return df

def load_json(filepath: str, default: dict) -> dict:
    """Cargar JSON con manejo de errores"""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"No se pudo cargar {filepath}: {e}")
        return default

def save_json(filepath: str, data: dict):
    """Guardar JSON con manejo de errores"""
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error guardando {filepath}: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# ADVANCED TRADING BOT
# ══════════════════════════════════════════════════════════════════════════════

class AdvancedTradingBot:
    """Bot de trading avanzado con estrategias duales y análisis de rendimiento"""
    
    def __init__(self):
        logger.info("🚀 Inicializando Advanced Trading Bot V2...")
        
        # Clients & Managers
        self.bingx = BingXClient(BINGX_API_KEY, BINGX_SECRET)
        self.tg = TelegramBot(TG_TOKEN, TG_CHAT_ID)
        self.risk = RiskManager(risk_pct=RISK_PCT, max_leverage=LEVERAGE)
        self.analyzer = TradeAnalyzer(TRADES_FILE, METRICS_FILE)
        
        # Strategies
        self.vwap_strategy = VWAPVolatilityBands()
        self.sniper_strategy = SniperStrategy(atr_multiplier=ATR_MULTIPLIER)
        
        # State
        self.state = load_json(STATE_FILE, {
            "last_signal": None,
            "trade_direction": None,
            "entry": None,
            "sl": None,
            "tps": [],
            "qty": 0.0,
            "tp_hits": [],
            "entry_time": None,
            "commission_paid": 0.0,
            "strategy_used": None
        })
        
        # Metrics
        self._last_heartbeat = datetime.utcnow()
        self._loop_count = 0
        self._errors_count = 0
        
    # ──────────────────────────────────────────────────────────────────────────
    # DATA FETCHING
    # ──────────────────────────────────────────────────────────────────────────
    
    async def fetch_df(self, interval: str, limit: int = 200) -> pd.DataFrame:
        """Obtener datos de velas con retry"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                klines = await self.bingx.get_klines(SYMBOL, interval, limit)
                return klines_to_df(klines)
            except Exception as e:
                logger.warning(f"Error fetching data (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    raise
    
    # ──────────────────────────────────────────────────────────────────────────
    # STRATEGY EVALUATION
    # ──────────────────────────────────────────────────────────────────────────
    
    async def evaluate_signals(self) -> Dict:
        """Evaluar ambas estrategias y combinar señales"""
        df_main = await self.fetch_df(TIMEFRAME)
        df_5m = await self.fetch_df("5m", limit=100)
        
        # Evaluación de estrategias
        vwap_signals = self.vwap_strategy.analyze(df_main)
        sniper_signals = self.sniper_strategy.analyze(df_main, df_5m)
        
        # Combinar señales según configuración
        if STRATEGY == "vwap":
            return vwap_signals
        elif STRATEGY == "sniper":
            return sniper_signals
        else:  # hybrid
            return self._hybrid_signal(vwap_signals, sniper_signals)
    
    def _hybrid_signal(self, vwap: Dict, sniper: Dict) -> Dict:
        """Combinar señales de ambas estrategias (confluencia)"""
        # Señal BUY: ambas alcistas
        buy_signal = (vwap.get('buy_signal', False) and 
                      sniper.get('buy_signal', False))
        
        # Señal SELL: ambas bajistas
        sell_signal = (vwap.get('sell_signal', False) and 
                       sniper.get('sell_signal', False))
        
        # Usar score más fuerte
        bull_pct = max(vwap.get('bull_pct', 0), sniper.get('bull_pct', 0))
        bear_pct = max(vwap.get('bear_pct', 0), sniper.get('bear_pct', 0))
        
        # Determinar bias
        score_diff = abs(bull_pct - bear_pct)
        if score_diff >= MIN_SCORE_DIFF:
            bias = "STRONG BULL" if bull_pct > bear_pct else "STRONG BEAR"
        else:
            bias = "MILD BULL" if bull_pct > bear_pct else "MILD BEAR"
        
        return {
            'buy_signal': buy_signal,
            'sell_signal': sell_signal,
            'bull_pct': bull_pct,
            'bear_pct': bear_pct,
            'bias': bias,
            'close': vwap['close'],
            'atr': max(vwap['atr'], sniper['atr']),
            'rsi': sniper.get('rsi', 50),
            'adx': sniper.get('adx', 0),
            'vwap_t3': vwap.get('t3', vwap['close']),
            'strategy': 'HYBRID'
        }
    
    # ──────────────────────────────────────────────────────────────────────────
    # COMMISSION OPTIMIZATION
    # ──────────────────────────────────────────────────────────────────────────
    
    def calculate_breakeven_with_fees(self, entry: float, direction: str) -> float:
        """Calcular precio de breakeven incluyendo comisiones"""
        total_fee = TAKER_FEE * 2 * LEVERAGE  # Entry + Exit con leverage
        
        if direction == "BUY":
            return entry * (1 + total_fee)
        else:
            return entry * (1 - total_fee)
    
    def adjust_tp_for_fees(self, entry: float, tp: float, direction: str) -> float:
        """Ajustar TP para garantizar profit después de fees"""
        min_profit_pct = 0.003  # 0.3% mínimo después de fees
        
        if direction == "BUY":
            min_tp = entry * (1 + (TAKER_FEE * 2 * LEVERAGE) + min_profit_pct)
            return max(tp, min_tp)
        else:
            min_tp = entry * (1 - (TAKER_FEE * 2 * LEVERAGE) - min_profit_pct)
            return min(tp, min_tp)
    
    # ──────────────────────────────────────────────────────────────────────────
    # TRADE EXECUTION
    # ──────────────────────────────────────────────────────────────────────────
    
    async def handle_new_signal(self, direction: str, signals: Dict):
        """Manejar nueva señal de trading"""
        entry = signals['close']
        atr = signals['atr']
        
        # Calcular niveles con ATR
        risk = atr * ATR_MULTIPLIER
        
        if direction == "BUY":
            sl = entry - risk
            raw_tps = [
                entry + risk,
                entry + (risk * 2),
                entry + (risk * 3),
                entry + (risk * 4),
                entry + (risk * 5)
            ]
        else:
            sl = entry + risk
            raw_tps = [
                entry - risk,
                entry - (risk * 2),
                entry - (risk * 3),
                entry - (risk * 4),
                entry - (risk * 5)
            ]
        
        # Ajustar TPs para cubrir comisiones
        tps = [self.adjust_tp_for_fees(entry, tp, direction) for tp in raw_tps]
        
        breakeven = self.calculate_breakeven_with_fees(entry, direction)
        
        levels = {
            'entry': entry,
            'sl': sl,
            'tp1': tps[0],
            'tp2': tps[1],
            'tp3': tps[2],
            'tp4': tps[3],
            'tp5': tps[4],
            'breakeven': breakeven
        }
        
        logger.info(f"📊 NEW SIGNAL: {direction} @ {entry:.4f}")
        logger.info(f"   SL: {sl:.4f} | Breakeven: {breakeven:.4f}")
        logger.info(f"   TPs: {[f'{tp:.4f}' for tp in tps]}")
        
        # Enviar señal a Telegram
        await self.tg.signal(direction, SYMBOL, levels, signals, TIMEFRAME)
        
        if SIGNALS_ONLY:
            logger.info("📡 SIGNALS_ONLY mode — No se ejecuta trade")
            return
        
        # Obtener balance y calcular cantidad
        balance_data = await self.bingx.get_balance()
        balance = float(balance_data.get("availableMargin", 0))
        
        if balance <= 0:
            await self.tg.error_alert(f"❌ Balance insuficiente: ${balance:.2f}")
            return
        
        # Calcular position size
        qty = self.risk.position_size(balance, entry, sl, LEVERAGE)
        
        if qty <= 0:
            await self.tg.error_alert(f"⚠️ Position size = 0 (balance: ${balance:.2f})")
            return
        
        # Verificar si trade está permitido
        positions = await self.bingx.get_positions(SYMBOL)
        open_count = sum(1 for p in positions if float(p.get("positionAmt", 0)) != 0)
        
        allowed, reason = self.risk.check_trade_allowed(open_count, balance)
        if not allowed:
            logger.warning(f"🚫 Trade bloqueado: {reason}")
            await self.tg.error_alert(f"🚫 {reason}")
            return
        
        try:
            # Set leverage
            await self.bingx.set_leverage(SYMBOL, LEVERAGE)
            
            # Ejecutar entrada (Market order)
            order = await self.bingx.place_market_order(SYMBOL, direction, qty)
            filled_price = float(order.get("price", entry))
            
            # Calcular comisión de entrada
            entry_commission = filled_price * qty * TAKER_FEE * LEVERAGE
            
            await self.tg.order_filled(SYMBOL, direction, qty, filled_price)
            
            # Colocar SL y TPs
            await self.bingx.place_tp_sl(SYMBOL, direction, qty, sl, tps)
            
            # Guardar estado
            self.state = {
                "last_signal": direction,
                "trade_direction": direction,
                "entry": filled_price,
                "sl": sl,
                "tps": tps,
                "qty": qty,
                "tp_hits": [],
                "entry_time": datetime.utcnow().isoformat(),
                "commission_paid": entry_commission,
                "strategy_used": signals.get('strategy', STRATEGY),
                "breakeven": breakeven
            }
            save_json(STATE_FILE, self.state)
            
            logger.info(f"✅ Trade ejecutado - Comisión: ${entry_commission:.4f}")
            
        except Exception as e:
            logger.error(f"❌ Error ejecutando trade: {e}", exc_info=True)
            await self.tg.error_alert(f"❌ Error en ejecución: {str(e)[:200]}")
            self._errors_count += 1
    
    # ──────────────────────────────────────────────────────────────────────────
    # POSITION MONITORING
    # ──────────────────────────────────────────────────────────────────────────
    
    async def monitor_position(self, signals: Dict):
        """Monitorear posición activa y detectar TPs alcanzados"""
        if not self.state.get("trade_direction"):
            return
        
        direction = self.state["trade_direction"]
        tps = self.state.get("tps", [])
        tp_hits = self.state.get("tp_hits", [])
        entry = self.state.get("entry", 0)
        current = signals["close"]
        
        new_hits = []
        
        for i, tp in enumerate(tps, 1):
            if i in tp_hits:
                continue
            
            hit = (current >= tp) if direction == "BUY" else (current <= tp)
            
            if hit:
                tp_hits.append(i)
                new_hits.append(i)
                
                # Calcular PnL con leverage y comisión
                pnl_raw = abs(tp - entry) / entry * 100 * LEVERAGE
                exit_commission_pct = TAKER_FEE * LEVERAGE * 100
                pnl_net = pnl_raw - exit_commission_pct
                
                await self.tg.tp_hit(SYMBOL, i, current, pnl_net)
                logger.info(f"🎯 TP{i} HIT @ {current:.4f} | PnL: {pnl_net:.2f}%")
                
                self.state["tp_hits"] = tp_hits
                save_json(STATE_FILE, self.state)
        
        # Si TP5 alcanzado, cerrar trade y registrar
        if 5 in tp_hits and len(new_hits) > 0:
            await self.close_trade_and_log(current, "TP5_HIT")
    
    async def close_trade_and_log(self, exit_price: float, reason: str):
        """Cerrar trade y registrar métricas"""
        if not self.state.get("trade_direction"):
            return
        
        try:
            direction = self.state["trade_direction"]
            qty = self.state["qty"]
            
            # Cancelar órdenes pendientes
            await self.bingx.cancel_all_orders(SYMBOL)
            
            # Cerrar posición
            await self.bingx.close_position(SYMBOL, direction, qty)
            
            # Calcular métricas finales
            entry = self.state["entry"]
            entry_commission = self.state.get("commission_paid", 0)
            exit_commission = exit_price * qty * TAKER_FEE * LEVERAGE
            
            pnl_raw = ((exit_price - entry) / entry if direction == "BUY" 
                      else (entry - exit_price) / entry) * qty * LEVERAGE
            pnl_net = pnl_raw - entry_commission - exit_commission
            
            # Registrar trade
            trade_record = {
                "symbol": SYMBOL,
                "direction": direction,
                "entry": entry,
                "exit": exit_price,
                "qty": qty,
                "entry_time": self.state.get("entry_time"),
                "exit_time": datetime.utcnow().isoformat(),
                "pnl_raw": pnl_raw,
                "pnl_net": pnl_net,
                "commission_total": entry_commission + exit_commission,
                "tp_hits": self.state.get("tp_hits", []),
                "reason": reason,
                "strategy": self.state.get("strategy_used", STRATEGY)
            }
            
            self.analyzer.record_trade(trade_record)
            
            await self.tg.send(
                f"🏁 <b>Trade Cerrado</b>\n"
                f"Razón: {reason}\n"
                f"PnL Bruto: ${pnl_raw:.2f}\n"
                f"Comisiones: ${entry_commission + exit_commission:.2f}\n"
                f"<b>PnL Neto: ${pnl_net:.2f}</b>"
            )
            
            # Resetear estado
            self.state = {
                "last_signal": self.state.get("last_signal"),
                "trade_direction": None,
                "entry": None,
                "sl": None,
                "tps": [],
                "qty": 0.0,
                "tp_hits": [],
                "entry_time": None,
                "commission_paid": 0.0,
                "strategy_used": None
            }
            save_json(STATE_FILE, self.state)
            
            logger.info(f"✅ Trade cerrado - PnL neto: ${pnl_net:.2f}")
            
        except Exception as e:
            logger.error(f"❌ Error cerrando trade: {e}", exc_info=True)
            await self.tg.error_alert(f"❌ Error cerrando: {str(e)[:200]}")
    
    # ──────────────────────────────────────────────────────────────────────────
    # HEARTBEAT & METRICS
    # ──────────────────────────────────────────────────────────────────────────
    
    async def heartbeat(self, signals: Dict):
        """Enviar heartbeat periódico con métricas"""
        now = datetime.utcnow()
        hours_since = (now - self._last_heartbeat).total_seconds() / 3600
        
        if hours_since < HEARTBEAT_HOURS:
            return
        
        self._last_heartbeat = now
        
        try:
            # Balance actual
            balance_data = await self.bingx.get_balance()
            equity = float(balance_data.get("equity", 0))
            unrealized_pnl = float(balance_data.get("unrealizedProfit", 0))
            
            # Métricas de rendimiento
            metrics = self.analyzer.get_metrics()
            
            # Trade activo
            trade_str = self.state.get("trade_direction") or "None"
            tp_hits_str = str(self.state.get("tp_hits", []))
            
            msg = (
                f"💓 <b>Heartbeat</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 {SYMBOL} | {TIMEFRAME}\n"
                f"💰 Equity: ${equity:.2f}\n"
                f"📈 PnL Unrealized: ${unrealized_pnl:.2f}\n"
                f"🎯 Trade Activo: {trade_str}\n"
                f"✅ TPs Hit: {tp_hits_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Win Rate: {metrics.get('win_rate', 0):.1f}%\n"
                f"💵 Total PnL: ${metrics.get('total_pnl', 0):.2f}\n"
                f"💸 Comisiones: ${metrics.get('total_commissions', 0):.2f}\n"
                f"📊 Trades: {metrics.get('total_trades', 0)}\n"
                f"🔄 Loops: {self._loop_count}\n"
                f"❌ Errors: {self._errors_count}"
            )
            
            await self.tg.send(msg)
            
        except Exception as e:
            logger.warning(f"⚠️ Heartbeat error: {e}")
    
    # ──────────────────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ──────────────────────────────────────────────────────────────────────────
    
    async def run(self):
        """Loop principal del bot"""
        logger.info("=" * 80)
        logger.info(f"🚀 ADVANCED TRADING BOT V2 - STARTING")
        logger.info(f"📊 Symbol: {SYMBOL} | Timeframe: {TIMEFRAME}")
        logger.info(f"⚡ Leverage: {LEVERAGE}x | Risk: {RISK_PCT}%")
        logger.info(f"🎯 Strategy: {STRATEGY.upper()}")
        logger.info(f"💰 Mode: {'SIGNALS ONLY 📡' if SIGNALS_ONLY else 'LIVE TRADING 💸'}")
        logger.info("=" * 80)
        
        await self.tg.send(
            f"🚀 <b>Advanced Trading Bot V2</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {SYMBOL} | {TIMEFRAME}\n"
            f"⚡ {LEVERAGE}x | Risk: {RISK_PCT}%\n"
            f"🎯 Strategy: <b>{STRATEGY.upper()}</b>\n"
            f"💰 Mode: {'📡 SIGNALS ONLY' if SIGNALS_ONLY else '💸 LIVE TRADING'}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Bot iniciado correctamente"
        )
        
        async with self.bingx:
            prev_signal_state = self.state.get("last_signal")
            
            while True:
                try:
                    self._loop_count += 1
                    
                    # Evaluar estrategias
                    signals = await self.evaluate_signals()
                    
                    logger.info(
                        f"[{SYMBOL}] Bull:{signals['bull_pct']:.0f}% "
                        f"Bear:{signals['bear_pct']:.0f}% "
                        f"Bias:{signals['bias']}"
                    )
                    
                    # Señal de compra
                    if signals["buy_signal"] and prev_signal_state != "BUY":
                        prev_signal_state = "BUY"
                        
                        # Cerrar posición SHORT si existe
                        if self.state.get("trade_direction") == "SELL" and not SIGNALS_ONLY:
                            await self.close_trade_and_log(signals['close'], "REVERSAL_BUY")
                        
                        await self.handle_new_signal("BUY", signals)
                    
                    # Señal de venta
                    elif signals["sell_signal"] and prev_signal_state != "SELL":
                        prev_signal_state = "SELL"
                        
                        # Cerrar posición LONG si existe
                        if self.state.get("trade_direction") == "BUY" and not SIGNALS_ONLY:
                            await self.close_trade_and_log(signals['close'], "REVERSAL_SELL")
                        
                        await self.handle_new_signal("SELL", signals)
                    
                    # Monitorear posición activa
                    else:
                        await self.monitor_position(signals)
                    
                    # Heartbeat periódico
                    await self.heartbeat(signals)
                    
                except Exception as e:
                    self._errors_count += 1
                    logger.error(f"❌ Loop error: {e}", exc_info=True)
                    await self.tg.error_alert(f"❌ Error: {str(e)[:300]}")
                    
                    # Si hay muchos errores consecutivos, esperar más
                    if self._errors_count > 5:
                        logger.warning("⚠️ Demasiados errores, esperando 5 minutos...")
                        await asyncio.sleep(300)
                        self._errors_count = 0
                
                await asyncio.sleep(POLL_SECONDS)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        bot = AdvancedTradingBot()
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("🛑 Bot detenido por usuario")
    except Exception as e:
        logger.error(f"💥 Error fatal: {e}", exc_info=True)
