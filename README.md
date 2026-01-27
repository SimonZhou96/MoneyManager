# MoneyManager

MoneyManager is a personal finance manager to help track income, expenses, budgets, and generate simple reports.

Repository initialized on 2026-01-20.

## Features

- Track income and expenses
- Categorize transactions
- Set budgets and alerts
- Generate monthly reports

## HK EMA Crossover Screener (Streamlit)

This repository now includes a Streamlit app that screens Hong Kong stocks for an
EMA10/EMA150 bullish crossover and visualizes candlesticks with EMA/HMA overlays.
The data source is Futu OpenAPI (FutuOpenD running locally).

### Setup

```bash
python3 -m pip install -r requirements.txt
```

### Run

Make sure FutuOpenD is running on `127.0.0.1:11111` (default in the app).

```bash
python3 -m streamlit run app.py
```

## Getting started

1. Clone the repository:

   git clone https://github.com/SimonZhou96/MoneyManager.git

2. Open the project and follow the language-specific setup (this repo may contain multiple languages).

## Contributing

Contributions are welcome — please open an issue or submit a pull request describing your changes.

## License

This project is available under the MIT License.