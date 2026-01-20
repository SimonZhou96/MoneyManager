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

## Contributing

Contributions are welcome — please open an issue or submit a pull request describing your changes.

## License

This project is available under the MIT License.