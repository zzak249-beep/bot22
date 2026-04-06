"""
Sniper Bot — Main orchestrator
Runs on asyncio loop, polls BingX, evaluates strategy, manages trades.
"""
import asyncio
import logging
import os
import json
from datetime import datetime

import pandas as pd

from strategy import compute_scores, compute_trade_levels
from bingx_client import BingXClient
from telegram_bot import TelegramBot
from risk_manager import RiskManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("SniperBot")

# ── Config from environment ───────────────────────────────────────────────────
BINGX_API_KEY   = os.environ["BINGX_API_KEY"]
BINGX_SECRET    = os.environ["BINGX_SECRET"]
TG_TOKEN        = os.environ["TG_TOKEN"]
TG_CHAT_ID      = os.environ["TG_CHAT_ID"]
SYMBOL          = os.environ.get("SYMBOL", "BTC-USDT")
TIMEFRAME       = os.environ.get("TIMEFRAME", "15m")
RISK_PCT        = float(os.environ.get("RISK_PCT", "1.0"))
LEVERAGE        = int(os.environ.get("LEVERAGE", "10"))
ATR_MULTIPLIER  = float(os.environ.get("ATR_MULTIPLIER", "1.5"))
POLL_SECONDS    = int(os.environ.get("POLL_SECONDS", "60"))
SIGNALS_ONLY    = os.environ.get("SIGNALS_ONLY", "false").lower() == "true"
HEARTBEAT_HOURS = int(os.environ.get("HEARTBEAT_HOURS", "4"))

# State file for persistence across restarts
STATE_FILE = "/tmp/bot_state.json"


def tf_to_minutes(tf: str) -> int:
    map_ = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "2h": 120, "4h": 240}
    return map_.get(tf, 15)


def klines_to_df(klines: list) -> pd.DataFrame:
    df = pd.DataFrame(klines, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    return df


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"last_signal": None, "trade_direction": None, "entry": None,
                "sl": None, "tps": [], "qty": 0.0, "tp_hits": []}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


class SniperBot:
    def __init__(self):
        self.bingx = BingXClient(BINGX_API_KEY, BINGX_SECRET)
        self.tg = TelegramBot(TG_TOKEN, TG_CHAT_ID)
        self.risk = RiskManager(risk_pct=RISK_PCT, max_leverage=LEVERAGE)
        self.state = load_state()
        self._last_heartbeat = datetime.utcnow()
        self._loop_count = 0

    # ── Data ──────────────────────────────────────────────────────────────────

    async def fetch_df(self, interval: str, limit: int = 200) -> pd.DataFrame:
        klines = await self.bingx.get_klines(SYMBOL, interval, limit)
        return klines_to_df(klines)

    # ── Core logic ────────────────────────────────────────────────────────────

    async def evaluate(self):
        df_main = await self.fetch_df(TIMEFRAME)
        df_5m   = await self.fetch_df("5m")

        rsi_5m = None
        try:
            from strategy import rsi as compute_rsi
            rsi_5m_series = compute_rsi(df_5m["close"], 14)
            # Align index to main df (use last value only)
            rsi_5m = pd.Series([rsi_5m_series.iloc[-1]] * len(df_main), index=df_main.index)
        except Exception as e:
            logger.warning(f"5m RSI fetch failed: {e}")

        scores = compute_scores(df_main, rsi_5m)
        logger.info(
            f"[{SYMBOL}] Bull:{scores['bull_pct']:.0f}% Bear:{scores['bear_pct']:.0f}% "
            f"Bias:{scores['bias']} RSI:{scores['rsi']:.1f} ADX:{scores['adx']:.1f}"
        )
        return scores

    async def handle_new_signal(self, direction: str, scores: dict):
        entry = scores["close"]
        levels = compute_trade_levels(entry, scores["atr"], direction, ATR_MULTIPLIER)

        logger.info(f"NEW SIGNAL: {direction} | Entry:{entry:.4f} SL:{levels['sl']:.4f}")

        # Send Telegram signal regardless of mode
        await self.tg.signal(direction, SYMBOL, levels, scores, TIMEFRAME)

        if SIGNALS_ONLY:
            logger.info("SIGNALS_ONLY mode — skipping order placement.")
            return

        # Get balance and compute qty
        balance_data = await self.bingx.get_balance()
        balance = float(balance_data.get("availableMargin", 0))
        qty = self.risk.position_size(balance, entry, levels["sl"], LEVERAGE)

        if qty <= 0:
            await self.tg.error_alert(f"Position size 0 — balance: ${balance:.2f}")
            return

        # Check if existing position
        positions = await self.bingx.get_positions(SYMBOL)
        open_count = sum(1 for p in positions if float(p.get("positionAmt", 0)) != 0)
        allowed, reason = self.risk.check_trade_allowed(open_count, balance)
        if not allowed:
            logger.warning(f"Trade blocked: {reason}")
            await self.tg.error_alert(f"Trade blocked: {reason}")
            return

        # Set leverage
        await self.bingx.set_leverage(SYMBOL, LEVERAGE)

        # Place market entry
        order = await self.bingx.place_market_order(SYMBOL, direction, qty)
        filled_price = float(order.get("price", entry))
        await self.tg.order_filled(SYMBOL, direction, qty, filled_price)

        # Place TP/SL bracket
        tp_list = [levels[f"tp{i}"] for i in range(1, 6)]
        await self.bingx.place_tp_sl(SYMBOL, direction, qty, levels["sl"], tp_list)

        # Save state
        self.state = {
            "last_signal": direction,
            "trade_direction": direction,
            "entry": filled_price,
            "sl": levels["sl"],
            "tps": tp_list,
            "qty": qty,
            "tp_hits": [],
        }
        save_state(self.state)

    async def monitor_position(self, scores: dict):
        """Check if TPs have been hit based on current price."""
        if not self.state.get("trade_direction"):
            return

        direction = self.state["trade_direction"]
        tps = self.state.get("tps", [])
        tp_hits = self.state.get("tp_hits", [])
        entry = self.state.get("entry", 0)
        current = scores["close"]

        for i, tp in enumerate(tps, 1):
            if i in tp_hits:
                continue
            hit = (current >= tp) if direction == "BUY" else (current <= tp)
            if hit:
                tp_hits.append(i)
                pnl_pct = abs(tp - entry) / entry * 100 * LEVERAGE
                await self.tg.tp_hit(SYMBOL, i, current, pnl_pct)
                logger.info(f"TP{i} HIT at {current:.4f}")
                self.state["tp_hits"] = tp_hits
                save_state(self.state)

    async def heartbeat(self, scores: dict):
        now = datetime.utcnow()
        hours_since = (now - self._last_heartbeat).total_seconds() / 3600
        if hours_since < HEARTBEAT_HOURS:
            return
        self._last_heartbeat = now
        try:
            balance_data = await self.bingx.get_balance()
            balance = float(balance_data.get("equity", 0))
            pnl = float(balance_data.get("unrealizedProfit", 0))
            trade_str = self.state.get("trade_direction") or "None"
            await self.tg.heartbeat(SYMBOL, balance, pnl, trade_str)
        except Exception as e:
            logger.warning(f"Heartbeat failed: {e}")

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        logger.info(f"🚀 Sniper Bot starting — {SYMBOL} {TIMEFRAME} leverage:{LEVERAGE}x")
        await self.tg.send(
            f"🚀 <b>Sniper Bot STARTED</b>\n"
            f"Symbol: {SYMBOL}  |  TF: {TIMEFRAME}\n"
            f"Leverage: {LEVERAGE}x  |  Risk: {RISK_PCT}%\n"
            f"Mode: {'SIGNALS ONLY 📡' if SIGNALS_ONLY else 'LIVE TRADING 💸'}"
        )

        async with self.bingx:
            prev_signal_state = self.state.get("last_signal")

            while True:
                try:
                    self._loop_count += 1
                    scores = await self.evaluate()

                    if scores["buy_signal"] and prev_signal_state != "BUY":
                        prev_signal_state = "BUY"
                        # Close any existing SHORT
                        if self.state.get("trade_direction") == "SELL" and not SIGNALS_ONLY:
                            await self.bingx.cancel_all_orders(SYMBOL)
                            await self.bingx.close_position(SYMBOL, "SELL", self.state["qty"])
                            self.state = load_state()
                            self.state["trade_direction"] = None
                            save_state(self.state)
                        await self.handle_new_signal("BUY", scores)

                    elif scores["sell_signal"] and prev_signal_state != "SELL":
                        prev_signal_state = "SELL"
                        if self.state.get("trade_direction") == "BUY" and not SIGNALS_ONLY:
                            await self.bingx.cancel_all_orders(SYMBOL)
                            await self.bingx.close_position(SYMBOL, "BUY", self.state["qty"])
                            self.state = load_state()
                            self.state["trade_direction"] = None
                            save_state(self.state)
                        await self.handle_new_signal("SELL", scores)

                    else:
                        await self.monitor_position(scores)

                    await self.heartbeat(scores)

                except Exception as e:
                    logger.error(f"Loop error: {e}", exc_info=True)
                    await self.tg.error_alert(str(e)[:300])

                await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    bot = SniperBot()
    asyncio.run(bot.run())
