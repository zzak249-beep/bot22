"""
Quick backtest of the EMA strategy on historical BingX data.
Run: python backtest.py
"""

import os
import time
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from bingx_client import BingXClient
from strategy import EMAStrategy

load_dotenv()

SYMBOL   = os.getenv("SYMBOL",   "BTC-USDT")
INTERVAL = os.getenv("INTERVAL", "3m")
EMA1_LEN = int(os.getenv("EMA1_LEN", "2"))
EMA2_LEN = int(os.getenv("EMA2_LEN", "4"))
EMA3_LEN = int(os.getenv("EMA3_LEN", "20"))
SL_PCT   = float(os.getenv("SL_PCT",  "1.5")) / 100
TP_RATIO = float(os.getenv("TP_RATIO","2.0"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))


def fetch_history(symbol: str, interval: str, pages: int = 10) -> pd.DataFrame:
    """Fetch up to pages × 500 candles from BingX"""
    client = BingXClient(
        os.environ["BINGX_API_KEY"],
        os.environ["BINGX_API_SECRET"],
        demo=True,
    )
    all_candles = []
    end_time = None
    for _ in range(pages):
        params = {"symbol": symbol, "interval": interval, "limit": 500}
        if end_time:
            params["endTime"] = end_time
        try:
            raw = client._get("/openApi/swap/v3/quote/klines", params)
            candles = raw.get("data", [])
            if not candles:
                break
            all_candles = candles + all_candles
            end_time = int(candles[0][0]) - 1
            time.sleep(0.3)
        except Exception as e:
            print(f"Fetch error: {e}")
            break

    if not all_candles:
        raise ValueError("No candles fetched")

    df = pd.DataFrame(all_candles, columns=["timestamp","open","high","low","close","volume"])
    df = df.astype({"timestamp":"int64","open":"float64","high":"float64",
                    "low":"float64","close":"float64","volume":"float64"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def run_backtest(df: pd.DataFrame) -> dict:
    strat = EMAStrategy(EMA1_LEN, EMA2_LEN, EMA3_LEN)
    df = strat.compute(df.copy())

    balance      = 1000.0
    equity_curve = [balance]
    trades       = []
    position     = None   # {"side", "entry", "sl", "tp", "qty"}

    for i in range(len(df) - 1):
        row  = df.iloc[i]
        next = df.iloc[i + 1]
        price = float(row["close"])

        # Check SL/TP on open position
        if position:
            hi = float(next["high"])
            lo = float(next["low"])
            hit_sl = hit_tp = False

            if position["side"] == "LONG":
                if lo <= position["sl"]:  hit_sl = True
                if hi >= position["tp"]:  hit_tp = True
            else:
                if hi >= position["sl"]:  hit_sl = True
                if lo <= position["tp"]:  hit_tp = True

            if hit_tp or hit_sl:
                exit_price = position["tp"] if hit_tp else position["sl"]
                if position["side"] == "LONG":
                    pnl = (exit_price - position["entry"]) / position["entry"]
                else:
                    pnl = (position["entry"] - exit_price) / position["entry"]

                pnl_usdt = balance * 0.01 * LEVERAGE * (pnl / SL_PCT)
                balance += pnl_usdt
                equity_curve.append(balance)
                trades.append({
                    "ts":     str(row["timestamp"]),
                    "side":   position["side"],
                    "entry":  position["entry"],
                    "exit":   exit_price,
                    "result": "TP" if hit_tp else "SL",
                    "pnl":    pnl_usdt,
                })
                position = None

        # New signal
        if not position:
            if row["signal_long"]:
                sl = price * (1 - SL_PCT)
                tp = price * (1 + SL_PCT * TP_RATIO)
                position = {"side": "LONG", "entry": price, "sl": sl, "tp": tp}
            elif row["signal_short"]:
                sl = price * (1 + SL_PCT)
                tp = price * (1 - SL_PCT * TP_RATIO)
                position = {"side": "SHORT", "entry": price, "sl": sl, "tp": tp}

    # Results
    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        print("No trades generated.")
        return {}

    wins  = trades_df[trades_df["pnl"] > 0]
    loss  = trades_df[trades_df["pnl"] <= 0]
    eq    = pd.Series(equity_curve)
    peak  = eq.cummax()
    dd    = ((peak - eq) / peak).max()

    results = {
        "total_trades":  len(trades_df),
        "win_rate":      f"{len(wins)/len(trades_df)*100:.1f}%",
        "total_pnl":     f"${trades_df['pnl'].sum():.2f}",
        "avg_win":       f"${wins['pnl'].mean():.2f}" if len(wins) else "N/A",
        "avg_loss":      f"${loss['pnl'].mean():.2f}" if len(loss) else "N/A",
        "max_drawdown":  f"{dd*100:.1f}%",
        "final_balance": f"${balance:.2f}",
        "return":        f"{(balance-1000)/10:.1f}%",
    }

    print("\n" + "="*45)
    print(f"  BACKTEST RESULTS — {SYMBOL} {INTERVAL}")
    print("="*45)
    for k, v in results.items():
        print(f"  {k:<18}: {v}")
    print("="*45)

    return results


if __name__ == "__main__":
    print(f"Fetching {SYMBOL} {INTERVAL} history from BingX...")
    df = fetch_history(SYMBOL, INTERVAL, pages=5)
    print(f"Loaded {len(df)} candles from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    run_backtest(df)
