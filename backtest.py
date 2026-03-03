"""
backtest.py — Run backtests for the BingX trading bot
Usage: python backtest.py 2024-01-01 2024-12-31
"""
import sys
import os
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from investing_algorithm_framework import (
    create_app,
    PortfolioConfiguration,
    pretty_print_backtest,
    download_data,
    PandasOHLCVDataProvider,
)
from strategies.ema_crossover import EMACrossoverStrategy
from strategies.rsi_strategy import RSIStrategy
from app.telegram_notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger   = logging.getLogger(__name__)
notifier = TelegramNotifier()


def convert_to_datetime(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        print(f"Error: invalid date '{s}'. Use YYYY-MM-DD.")
        sys.exit(1)


def main():
    if len(sys.argv) < 3:
        print("Usage: python backtest.py <start_date> <end_date>")
        print("  e.g. python backtest.py 2024-01-01 2024-12-31")
        sys.exit(1)

    start_date = convert_to_datetime(sys.argv[1])
    end_date   = convert_to_datetime(sys.argv[2])

    print(f"\n Descargando datos historicos...")
    print(f"   Periodo: {start_date.date()} -> {end_date.date()}")

    symbols  = ["BTC/USDT", "ETH/USDT", "BNB/USDT"]
    data_dir = "./data"
    os.makedirs(data_dir, exist_ok=True)

    for symbol in symbols:
        print(f"   Descargando {symbol} 1h...")
        try:
            download_data(
                symbol=symbol,
                market="BINGX",
                timeframe="1h",
                start_date=start_date,
                end_date=end_date,
                storage_path=data_dir,
            )
        except Exception as e:
            print(f"   BingX fallo ({e}), intentando Binance...")
            try:
                download_data(
                    symbol=symbol,
                    market="BINANCE",
                    timeframe="1h",
                    start_date=start_date,
                    end_date=end_date,
                    storage_path=data_dir,
                )
            except Exception as e2:
                print(f"   ERROR descargando {symbol}: {e2}")

    print(f"\n Ejecutando backtest: {start_date.date()} -> {end_date.date()}\n")

    app = create_app(name="BingX-Backtest")

    for symbol in symbols:
        app.add_market_data_source(PandasOHLCVDataProvider(
            identifier=f"{symbol}-ohlcv-1h",
            market="BINGX",
            symbol=symbol,
            timeframe="1h",
            storage_path=data_dir,
        ))

    app.add_strategy(EMACrossoverStrategy)
    app.add_strategy(RSIStrategy)
    app.add_portfolio_configuration(
        PortfolioConfiguration(
            market="BINGX",
            trading_symbol="USDT",
            initial_balance=1000,
        )
    )

    report = app.backtest(
        start_date=start_date,
        end_date=end_date,
        pending_order_check_interval="1h",
    )

    pretty_print_backtest(report)

    try:
        overview     = report.get_overview()
        trades       = report.get_trades_overview()
        total_trades = trades.get("number_of_trades_closed", 0)
        win_rate     = trades.get("percentage_of_positive_trades", 0)
        initial_bal  = overview.get("initial_balance", 1000)
        final_bal    = overview.get("final_balance", initial_bal)
    except Exception:
        initial_bal = final_bal = 1000
        total_trades = win_rate = 0

    notifier.backtest_complete(
        start=str(start_date.date()),
        end=str(end_date.date()),
        initial=initial_bal,
        final=final_bal,
        total_trades=total_trades,
        win_rate=win_rate,
    )


if __name__ == "__main__":
    main()
