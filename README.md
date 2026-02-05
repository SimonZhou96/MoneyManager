# MoneyManager

MoneyManager is a personal finance manager to help track income, expenses, budgets, and generate simple reports.

Repository initialized on 2026-01-20.

## Features

- Track income and expenses
- Categorize transactions
- Set budgets and alerts
- Generate monthly reports

## Getting started

1. Clone the repository:

   git clone https://github.com/SimonZhou96/MoneyManager.git

2. Open the project and follow the language-specific setup (this repo may contain multiple languages).

## Xiaomi EMA strategy example

This repo includes a simple Python example that downloads Xiaomi (1810.HK) data,
computes EMA10/EMA150, and applies the buy/sell rules described in the prompt.

1. Install dependencies:

   python -m pip install -r requirements.txt

2. Run the strategy:

   python xiaomi_strategy.py --ticker 1810.HK --lookback-days 220 --last-n 10

The script will save a candlestick chart with EMA10/EMA150 and buy/sell markers
to `xiaomi_chart.png` by default (use `--plot-file` to change the path).

## Futu daily buy/sell volume top10

This script uses Futu OpenD real-time ticks (ticker_direction) to compute daily
buy/sell volume and show the top 10 days. Run it during trading hours and save
ticks to CSV if you want to accumulate multi-day history.

1. Collect ticks (example: 5 minutes) and save:

   python futu_xiaomi_daily_volume_top10.py --code HK.01810 --duration-min 5 --save-csv ticks.csv

2. Show top10 from saved ticks:

   python futu_xiaomi_daily_volume_top10.py --ticks-csv ticks.csv --top-n 10

Use `--fetch` to merge the latest ticks with an existing CSV.

## Contributing

Contributions are welcome — please open an issue or submit a pull request describing your changes.

## License

This project is available under the MIT License.