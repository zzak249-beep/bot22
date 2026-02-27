"""
Bot de Telegram — BingX Trading Bot
Notifica: apertura, cierre (con PnL y motivo), señales detectadas
Comandos: /start /status /balance /trades /historial /stop
"""

import os
import asyncio
import logging
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application
from telegram.constants import ParseMode

from strategies.vwap_strategy import Signal, TradeSignal
from utils.logger import setup_logger

logger = setup_logger(__name__)


# ──────────────────────────────────────────────────────────────────
# FORMATOS DE MENSAJES
# ──────────────────────────────────────────────────────────────────

def fmt_signal(symbol: str, strategy: str, sig: TradeSignal) -> str:
    d = sig.signal.value
    arrow  = "🟢 LONG ▲" if d == "LONG" else "🔴 SHORT ▼"
    conf   = {"HIGH": "🔥 ALTA", "MEDIUM": "⚡ MEDIA"}.get(sig.confidence, "⚪")
    strat  = {"VWAP+SD": "📊 VWAP+SD", "BB+RSI": "📉 BB+RSI", "EMA Ribbon 5m": "⚡ EMA Scalp"}.get(strategy, strategy)
    ts     = datetime.utcnow().strftime("%H:%M:%S UTC")
    if sig.signal == Signal.LONG and sig.sl_price:
        rr = (sig.tp_price - sig.entry_price) / (sig.entry_price - sig.sl_price)
    elif sig.sl_price:
        rr = (sig.entry_price - sig.tp_price) / (sig.sl_price - sig.entry_price)
    else:
        rr = 0
    return (
        f"🔔 *SEÑAL DETECTADA*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{arrow}  `{symbol}`\n"
        f"{strat}\n"
        f"🎯 Confianza: {conf}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Entrada:  `{sig.entry_price}`\n"
        f"✅ TP:       `{sig.tp_price}`\n"
        f"🛑 SL:       `{sig.sl_price}`\n"
        f"⚖️ R:R:      `{rr:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 _{sig.reason}_\n"
        f"⏰ `{ts}`"
    )


def fmt_open(t: dict) -> str:
    arrow = "🟢 LONG ▲" if t["signal"] == "LONG" else "🔴 SHORT ▼"
    ts    = datetime.utcnow().strftime("%H:%M:%S UTC")
    if t.get("sl") and t.get("tp"):
        rr_n = abs(t["tp"] - t["entry"])
        rr_d = abs(t["sl"] - t["entry"])
        rr = rr_n / rr_d if rr_d else 0
    else:
        rr = 0
    return (
        f"🚀 *TRADE ABIERTO — BINGX*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{arrow}  `{t['symbol']}`\n"
        f"📊 `{t['strategy']}`\n"
        f"🎯 Confianza: `{t.get('confidence','?')}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Entrada:   `{t['entry']}`\n"
        f"✅ TP:        `{t['tp']}`\n"
        f"🛑 SL inicial:`{t['sl']}`\n"
        f"⚖️ R:R:       `{rr:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Margen:    `{t['margin']} USDT`\n"
        f"⚡ Apalancam: `{t['leverage']}x`\n"
        f"📦 Posición:  `{t['pos_value']} USDT`\n"
        f"📦 Contratos: `{t['qty']}`\n"
        f"🏦 Balance:   `${t.get('balance', 0):.2f} USDT`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 `{t.get('order_id','N/A')}`\n"
        f"⏰ `{ts}`\n\n"
        f"📈 _Trailing stop activo — deja correr si gana_"
    )


def fmt_close(c: dict) -> str:
    won    = c["won"]
    icon   = "✅" if won else "❌"
    pnl    = c["pnl_usdt"]
    pnl_p  = c["pnl_pct"]
    arrow  = "🟢 LONG ▲" if c["direction"] == "LONG" else "🔴 SHORT ▼"
    sign   = "+" if pnl >= 0 else ""
    ts     = datetime.utcnow().strftime("%H:%M:%S UTC")

    phase_info = ""
    if c.get("phase") == "Trailing 🎯":
        phase_info = f"📈 Pico alcanzado: `{c['best_price']}`\n🎯 Trailing SL:   `{c['trailing_sl']}`\n"

    return (
        f"{icon} *TRADE CERRADO — BINGX*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{arrow}  `{c['symbol']}`\n"
        f"📊 `{c['strategy']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Entrada:  `{c['entry']}`\n"
        f"🚪 Salida:   `{c['exit']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 PnL:      `{sign}{pnl:.4f} USDT ({sign}{pnl_p:.2f}%)`\n"
        f"🔄 Fase:     `{c['phase']}`\n"
        f"{phase_info}"
        f"📝 _{c['reason']}_\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 P&L hoy:  `{'+' if c['daily_pnl']>=0 else ''}{c['daily_pnl']:.4f} USDT`\n"
        f"🎯 Winrate:  `{c['winrate']}%`\n"
        f"⏰ `{ts}`"
    )


# ──────────────────────────────────────────────────────────────────
# BOT PRINCIPAL
# ──────────────────────────────────────────────────────────────────

class TelegramSignalBot:
    def __init__(self, trader):
        self.token   = os.environ["TELEGRAM_TOKEN"]
        self.chat_id = os.environ["TELEGRAM_CHAT_ID"]
        self.trader  = trader
        self.app: Application | None = None
        self.bot: Bot | None = None

        # Registrar callbacks
        self.trader.register_signal_callback(self.on_signal)
        self.trader.register_trade_callback(self.on_open)
        self.trader.register_close_callback(self.on_close)

    async def send(self, text: str):
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

    # ── Callbacks ──────────────────────────────────────
    async def on_signal(self, symbol: str, strategy: str, sig: TradeSignal):
        await self.send(fmt_signal(symbol, strategy, sig))

    async def on_open(self, trade_info: dict):
        await self.send(fmt_open(trade_info))

    async def on_close(self, close_info: dict):
        await self.send(fmt_close(close_info))

    # ── Comandos ───────────────────────────────────────
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 *BingX Trading Bot*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📊 *Estrategias:*\n"
            "  • VWAP + Bandas SD (15m)\n"
            "  • Bollinger Bands + RSI (15m)\n"
            "  • EMA Ribbon 9/15 Scalping (5m)\n\n"
            "📈 *Gestión automática:*\n"
            "  • Entrada fija: 8 USDT × 7x\n"
            "  • Si gana → trailing stop (deja correr)\n"
            "  • Si pierde → cierre rápido al SL\n\n"
            "📌 *Comandos:*\n"
            "  /status → Estado del bot\n"
            "  /balance → Balance BingX\n"
            "  /trades → Posiciones abiertas\n"
            "  /config → Ver configuración actual\n"
            "  /stop → Detener análisis",
            parse_mode=ParseMode.MARKDOWN
        )

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        t = self.trader
        n = len(t.active_trades)
        wr = round((t.winning_trades / max(t.total_trades, 1)) * 100, 1)
        pnl_icon = "📈" if t.daily_pnl >= 0 else "📉"
        mode = "⚪ DEMO" if t.testnet else "🔴 DINERO REAL"
        await update.message.reply_text(
            f"📊 *Estado del Bot*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Estado: `ACTIVO` | {mode}\n"
            f"💰 Entrada: `{t.usdt_per_trade} USDT × {t.leverage}x`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 Posiciones: `{n}` abiertas\n"
            f"📈 Trades total: `{t.total_trades}`\n"
            f"🎯 Winrate: `{wr}%`\n"
            f"{pnl_icon} P&L hoy: `{'+' if t.daily_pnl >= 0 else ''}{t.daily_pnl:.4f} USDT`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💱 Pares: `{', '.join(t.pairs)}`",
            parse_mode=ParseMode.MARKDOWN
        )

    async def cmd_balance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("⏳ Consultando...")
        try:
            bal = await self.trader.get_balance()
            pos_val = self.trader.usdt_per_trade * self.trader.leverage
            trades_left = int(bal / self.trader.usdt_per_trade)
            await update.message.reply_text(
                f"💰 *Balance BingX Futuros*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Disponible: `${bal:.4f} USDT`\n"
                f"📦 Por trade:  `{self.trader.usdt_per_trade} USDT × {self.trader.leverage}x = {pos_val} USDT`\n"
                f"🔢 Trades posibles: `~{trades_left}`",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    async def cmd_trades(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        t = self.trader
        if not t.active_trades:
            await update.message.reply_text("📭 Sin posiciones abiertas.")
            return

        msg = "📋 *Posiciones Abiertas*\n━━━━━━━━━━━━━━━━━━━━\n"
        for sym, trade in t.active_trades.items():
            # Obtener precio actual
            try:
                price = await t.get_current_price(sym)
                trade.update_pnl(price)
            except:
                price = 0
            pnl_icon = "📈" if trade.pnl_usdt >= 0 else "📉"
            phase_name = {0: "🔒 SL Fijo", 1: "🔄 Breakeven", 2: "🎯 Trailing"}.get(trade.phase, "?")
            arr = "▲" if trade.direction == "LONG" else "▼"
            msg += (
                f"{arr} *{sym}* `{trade.direction}`\n"
                f"  Entrada: `{trade.entry}` | Precio: `{price:.4f}`\n"
                f"  {pnl_icon} PnL: `{trade.pnl_usdt:+.4f} USDT ({trade.pnl_pct():+.2f}%)`\n"
                f"  Fase: {phase_name} | SL: `{trade.current_sl:.4f}`\n"
                f"  TP: `{trade.tp}` | Pico: `{trade.best_price:.4f}`\n\n"
            )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def cmd_config(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from bot.trader import BE_TRIGGER_PCT, TRAIL_START_PCT, TRAIL_DISTANCE_PCT, LOSS_CUT_PCT, MONITOR_INTERVAL
        t = self.trader
        await update.message.reply_text(
            f"⚙️ *Configuración Actual*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Entrada: `{t.usdt_per_trade} USDT × {t.leverage}x`\n"
            f"📦 Posición: `{t.usdt_per_trade * t.leverage} USDT/trade`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 Breakeven a: `+{BE_TRIGGER_PCT}%`\n"
            f"🎯 Trailing inicia: `+{TRAIL_START_PCT}%`\n"
            f"📏 Distancia trailing: `{TRAIL_DISTANCE_PCT}%`\n"
            f"🛑 Corte pérdida: `-{LOSS_CUT_PCT}%`\n"
            f"⏱ Monitor cada: `{MONITOR_INTERVAL}s`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Max posiciones: `{t.risk_manager.max_open_positions}`\n"
            f"📉 Max pérdida diaria: `{t.risk_manager.max_daily_loss_pct}%`",
            parse_mode=ParseMode.MARKDOWN
        )

    async def cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🛑 Señal de stop recibida.\n"
            "El bot dejará de abrir nuevas posiciones.\n"
            "Las posiciones abiertas seguirán siendo monitoreadas."
        )

    # ── Run ────────────────────────────────────────────
    async def run(self):
        self.app = ApplicationBuilder().token(self.token).build()
        self.bot = self.app.bot

        self.app.add_handler(CommandHandler("start",    self.cmd_start))
        self.app.add_handler(CommandHandler("status",   self.cmd_status))
        self.app.add_handler(CommandHandler("balance",  self.cmd_balance))
        self.app.add_handler(CommandHandler("trades",   self.cmd_trades))
        self.app.add_handler(CommandHandler("config",   self.cmd_config))
        self.app.add_handler(CommandHandler("stop",     self.cmd_stop))

        modo = "⚪ DEMO" if self.trader.testnet else "🔴 DINERO REAL"
        pos_val = self.trader.usdt_per_trade * self.trader.leverage
        await self.send(
            f"🚀 *BingX Trading Bot iniciado* — {modo}\n"
            f"💰 Entrada fija: `{self.trader.usdt_per_trade} USDT × {self.trader.leverage}x = {pos_val} USDT`\n"
            f"📊 Pares: `{', '.join(self.trader.pairs)}`\n"
            f"📈 Trailing stop activo\n"
            f"Usa /start para ver comandos."
        )

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        while True:
            await asyncio.sleep(3600)
