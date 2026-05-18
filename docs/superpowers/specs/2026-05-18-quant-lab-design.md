# Quant Lab Design

## Background

MoneyManager already has a stock screening platform under `stock_screener`.
It includes FastAPI endpoints, MySQL persistence, Futu / AKShare / yfinance
market-data integrations, rule-chain screening, single-stock analysis, an
Option Lab, and a React/Vite frontend.

The current gap is a unified quantitative trading research framework for both
stocks and options. The framework must support backtesting, win-rate analysis,
performance attribution, and paper trading without placing real orders in the
first version.

The provided reference article compares common open-source quantitative
frameworks such as QuantConnect LEAN, Zipline, Backtrader, PyAlgoTrade,
vn.py, and Backtesting.py. Its most relevant takeaway for this project is that
event-driven frameworks with clean component boundaries are easier to extend
from research into simulation and live trading. For MoneyManager, the best fit
is not to replace the existing app with one external framework, but to add a
lightweight in-app Quant Lab while keeping a future boundary for external
executors if needed.

## Goals

- Add a dedicated `Quant Lab` inside `stock_screener`.
- Use one architecture for stocks, ETFs, and options.
- Support historical backtesting for stocks and ETFs in the first version.
- Support limited option backtesting and strategy replay using Option Lab
  snapshots and candidates.
- Support paper trading / simulated trading after a backtest passes.
- Provide win-rate, risk, return, drawdown, and trade-detail analysis.
- Add frontend and backend support.
- Preserve the existing Option Lab, stock screening, and shared signal-analysis
  behavior.

## Non-Goals

- Do not place real orders through Futu in the first version.
- Do not build a full historical option-chain warehouse before the first usable
  version.
- Do not run search or LLM analysis inside the default backtest loop.
- Do not replace the existing Option Lab or stock rule engine.
- Do not adopt QuantConnect LEAN, Backtrader, vectorbt, or another framework as
  the required execution core in the first version.

## Selected Approach

Use a lightweight in-app Quant Lab as the main path.

The design is:

- A new backend package: `stock_screener/quant_lab/`.
- A new FastAPI router: `stock_screener/web/quant.py`.
- New MySQL tables for backtests, paper trading, trades, positions, equity
  curves, metrics, and signal events.
- A new frontend page: `量化实验室`.
- A future executor boundary so an external engine such as QuantConnect LEAN can
  be evaluated later without changing the user-facing contract.

This keeps the implementation aligned with MoneyManager's current architecture
and avoids making the first version dependent on hard-to-source historical
option-chain data.

## Architecture

Add the following modules under `stock_screener/quant_lab/`:

- `models.py`
  - Defines normalized instruments, bars, signals, orders, trades, positions,
    equity points, metric snapshots, paper accounts, and error payloads.
- `data.py`
  - Reads historical market data.
  - Stocks and ETFs reuse the existing Futu / AKShare / yfinance K-line path.
  - Options read from Option Lab snapshots and future option snapshot storage.
- `strategies.py`
  - Wraps existing screening strategies into a backtestable strategy interface.
  - Initial candidates include ZuoYi, RSI, EMA, volume, and rule-chain based
    strategies.
- `rule_chain_adapter.py`
  - Treats existing rule chains and combinations of meta-rules as first-class
    Quant Lab strategies.
  - Evaluates rule chains on historical bars and emits normalized buy, sell, or
    hold signals.
  - Records which meta-rules passed, which meta-rules failed, and why the final
    chain result produced a trade signal.
- `option_adapter.py`
  - Converts Option Lab strategy candidates and `合约明细` into Quant Lab
    signals.
  - Marks option runs as limited when historical option-chain data is missing.
- `backtest.py`
  - Runs event-driven backtests.
  - Advances by bar timestamp, generates signals, creates simulated orders,
    applies fills, updates positions, and records equity.
- `broker.py`
  - Implements the simulated broker and paper account.
  - Applies commission, slippage, limit-order rules, max-position constraints,
    max-loss constraints, and rejected-order reasons.
- `metrics.py`
  - Calculates return, risk, win-rate, trade quality, drawdown, and holding
    period metrics.
- `service.py`
  - Orchestrates backtest submission, result loading, and paper-trading state.

The existing boundaries remain:

- `Option Lab` answers whether a current option strategy is worth considering.
- `Quant Lab` answers whether a strategy has historically worked and how it
  behaves in simulation.

## Rule Chain Backtesting

The first implementation priority is to reuse MoneyManager's existing rule
chains as quantitative strategies.

This means a rule chain is not only a screening condition. In Quant Lab it
becomes a historical strategy definition:

`meta-rules -> rule chain -> buy/sell/hold signal -> simulated order -> simulated trade -> position -> equity curve -> metrics`

Examples:

- A ZuoYi rule chain can be replayed over historical bars to find every date
  where the chain would have emitted a buy or bearish signal.
- A combined chain such as `ZuoYi + RSI + volume` can be replayed as a stricter
  strategy.
- Multiple existing meta-rules can be assembled into different rule chains and
  compared by return, drawdown, win rate, expected value, and trade count.

The rule-chain adapter must distinguish between two concepts:

- **Entry signal**: a rule-chain pass that opens or increases a simulated
  position.
- **Exit signal**: a rule-chain pass, inverse signal, or configured risk rule
  that closes or reduces a simulated position.

If a rule chain only defines entry conditions, the backtest request must specify
an exit policy. Supported first-version exit policies are:

- inverse signal exit;
- fixed holding period exit;
- take-profit exit;
- stop-loss exit;
- trailing drawdown exit;
- end-of-backtest forced close.

Quant Lab must not report a complete trade-level backtest from entry signals
alone. If no exit policy exists, the system can report signal-forward returns
over fixed horizons, but the UI must label that as signal analysis rather than
full simulated trading.

## Data Flow

### Backtest Flow

1. The user submits a backtest request from the frontend.
2. The backend validates market, symbols, strategy, date range, cash, fees,
   slippage, and risk parameters.
3. A row is created in `quant_backtest_runs`.
4. The data layer loads historical bars and optional option snapshots.
5. The strategy layer emits normalized signals.
6. The broker layer creates simulated orders and trades.
7. The position layer updates holdings and cash.
8. The equity layer records portfolio value and drawdown.
9. The metrics layer writes a metric snapshot.
10. The frontend displays charts, metrics, trade details, and data-quality
    warnings.

### Rule Chain Backtest Flow

1. The user selects an existing rule chain or creates a backtest configuration
   from selected meta-rules.
2. The backend snapshots the rule-chain definition and stores it with the
   backtest run so future rule edits do not rewrite historical results.
3. The data layer loads historical bars for the selected symbol pool.
4. The rule-chain adapter evaluates the chain on each bar.
5. Passing entry rules emit normalized entry signals.
6. Exit rules or the configured exit policy emit normalized exit signals.
7. Signals create simulated orders through the broker layer.
8. Orders become simulated trades only if price, liquidity, slippage, and risk
   checks allow the fill.
9. Every signal event stores the rule-chain key, rule-chain snapshot, triggered
   meta-rules, failed meta-rules, signal direction, and signal reason.

### Limited Option Backtest Flow

1. Option strategies are generated through Option Lab or loaded from stored
   Option Lab candidates.
2. Quant Lab converts candidates into normalized option signals.
3. If historical option snapshots are available, Quant Lab replays contract
   details, suggested prices, DTE, risk limits, and exits.
4. If required option-chain or quote fields are missing, the run is marked as
   limited and the frontend shows the exact missing reason.

The system must not present limited option replay as a full historical option
backtest.

### Paper Trading Flow

1. The user enables paper trading for an approved strategy configuration.
2. The system reads current market data on schedule or manual refresh.
3. Signals create simulated orders only.
4. Simulated fills update paper positions and account equity.
5. Paper events are shown in the frontend.
6. No Futu real-order API is called in version one.

## Database Design

Add a new migration such as `sql/014_quant_lab.sql` with these tables:

- `quant_strategy_configs`
  - Stores strategy type, params JSON, market scope, symbol scope, risk config,
    entry rule-chain key, exit policy, and paper-trading enabled state.
- `quant_backtest_runs`
  - Stores one backtest request, status, data source, request JSON, warning JSON,
    error message, start/end timestamps, rule-chain snapshot JSON, and
    ownership.
- `quant_signal_events`
  - Stores normalized signal events, rule-chain key, meta-rule results, signal
    direction, signal strength, and the reason each signal fired.
- `quant_backtest_orders`
  - Stores simulated orders, order status, limit price, rejected reason, and
    linked signal.
- `quant_backtest_trades`
  - Stores simulated fills, fill price, quantity, fees, slippage, and linked
    order.
- `quant_positions`
  - Stores position snapshots for stocks, ETFs, and option combinations.
- `quant_equity_curve`
  - Stores portfolio value, cash, exposure, drawdown, and benchmark value by
    timestamp.
- `quant_metric_snapshots`
  - Stores return, risk, win-rate, trade quality, drawdown, and holding-period
    metrics.
- `quant_paper_accounts`
  - Stores paper account cash, equity, status, and risk limits.
- `quant_paper_orders`
  - Stores simulated live orders created by paper trading.
- `quant_paper_positions`
  - Stores current paper positions and paper-trading state.

Every displayed metric must be traceable through:

`signal -> order -> trade -> position -> equity -> metric`.

## Metrics

The first version must calculate:

- Return metrics:
  - Total return.
  - Annualized return.
  - Benchmark return.
  - Excess return.
- Risk metrics:
  - Maximum drawdown.
  - Drawdown duration.
  - Volatility.
  - Return-to-drawdown ratio.
- Trade metrics:
  - Trade count.
  - Win rate.
  - Average win.
  - Average loss.
  - Win/loss ratio.
  - Profit factor.
  - Expected value.
  - Maximum consecutive losses.
- Position metrics:
  - Average holding period.
  - Capital utilization.
  - Maximum position weight.
- Option-specific metrics:
  - DTE distribution.
  - Delta bucket win rate when available.
  - IV availability rate.
  - Max loss.
  - Breakeven points.
  - Missing-data reasons.

Win rate must not be used alone as the quality signal. The UI should show it
next to average win, average loss, expected value, and drawdown.

## Frontend Design

Add a `量化实验室` page next to the existing `期权实验室`.

The page is a workbench, not a landing page.

Layout:

- Left configuration panel:
  - Market.
  - Symbol pool.
  - Strategy source: existing rule chain, selected meta-rules, or Option Lab.
  - Entry rule-chain selection.
  - Exit policy selection.
  - Date range.
  - Initial cash.
  - Commission.
  - Slippage.
  - Risk limits.
  - Start backtest button.
- Top result cards:
  - Total return.
  - Annualized return.
  - Maximum drawdown.
  - Win rate.
  - Win/loss ratio.
  - Trade count.
- Main charts:
  - Equity curve.
  - Drawdown curve.
  - Benchmark comparison.
  - Optional monthly return or parameter comparison view.
- Detail sections:
  - Trade table.
  - Signal-event table.
  - Rule-chain trigger details.
  - Rejected-order table.
  - Paper orders and positions.
- Option-only section:
  - `合约明细`.
  - DTE.
  - Delta / IV fields when available.
  - Max loss and breakeven points.
  - Limited-backtest warning and missing-data reasons.

The current `web_frontend/src/main.tsx` is already large. Implementation should
prefer extracting Quant Lab frontend code into focused feature files instead of
adding another large block to the same file.

## Data Source Capability

Futu OpenD, AKShare, and yfinance should be treated as data and trading
connectors, not complete quantitative frameworks.

- Futu OpenD:
  - Good for quote, K-line, option-chain, paper/live account integration, and
    future order placement.
  - Version one must not call real order-placement APIs.
  - Suitable for future read-only paper sync and optional trade gateway work.
- AKShare:
  - Good for A-share, ETF, index, and option research data.
  - No trading capability.
  - Useful as a supplemental research source, but not enough alone for reliable
    production backtesting.
- yfinance:
  - Good for low-cost US stock and option research prototypes.
  - No trading capability.
  - Useful for stocks and current option-chain data, but not a full historical
    option-chain backtest source.

## Error Handling

The backend must store and expose explicit error reasons for:

- Missing K-line data.
- Missing option-chain snapshots.
- Missing bid/ask.
- Missing Greeks.
- Missing IV.
- Invalid strategy parameters.
- Invalid date range.
- Insufficient capital.
- Max-position violation.
- Max-loss violation.
- Limit price not reached.
- Suspended or unavailable market data.
- Paper-trading data refresh failure.

Paper trading must preserve the previous state when market data refresh fails
and create a risk event instead of silently modifying positions.

## Testing

Backend tests:

- `metrics.py` unit tests for return, drawdown, win rate, expected value, profit
  factor, and consecutive-loss calculations.
- `broker.py` unit tests for commissions, slippage, rejected orders, limit
  orders, and risk limits.
- `backtest.py` integration tests for signal, order, trade, position, equity,
  and metric generation.
- `rule_chain_adapter.py` tests for historical rule evaluation, meta-rule
  trigger recording, buy/sell/hold signal generation, and missing exit-policy
  handling.
- `option_adapter.py` tests for Option Lab candidate conversion and limited
  backtest warnings.
- API tests for backtest submission, result loading, and paper-trading enable /
  disable.

Frontend tests/build:

- Validate that the Quant Lab page renders configuration, metrics, charts, trade
  details, and paper-trading state.
- Run `npm run build` in `stock_screener/web_frontend`.

Cost-control tests:

- Default backtests must not invoke search providers.
- Default backtests must not invoke LLM providers.
- Search and LLM analysis can be added later as explicit explanation features,
  not as default backtest dependencies.

## Rollout Plan

The implementation should be split into phases:

1. Backend data model, metrics, broker, rule-chain adapter, and stock/ETF
   backtest engine.
2. FastAPI endpoints and database persistence.
3. Quant Lab frontend page.
4. Paper-trading simulation.
5. Option Lab adapter and limited option replay.
6. Optional future external executor boundary for LEAN or another engine.

This keeps the first deliverable useful without blocking on full option-chain
history.
