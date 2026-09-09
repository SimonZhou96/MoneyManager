# Option Lab Design

## Background

MoneyManager already has a stock screening platform under `stock_screener`.
It includes FastAPI endpoints, a React frontend, MySQL persistence, Futu
OpenD integration, yfinance/AKShare K-line fallback, Feishu notifications,
single-stock analysis and custom-list screening.

The new requirement is to evaluate whether options for a specific stock, ETF,
or index are worth trading, what price range is reasonable, how long to hold,
and how to monitor the position after the user manually places the trade.

The system must support:

- US stock options.
- HK stock options.
- A-share ETF and index options.
- A complete options strategy basket.
- Frontend and backend changes.
- Manual order execution by the user, not automatic Futu order placement.
- Position monitoring after the user reports fills.

## Goals

- Add an independent Option Lab inside the existing `stock_screener` platform.
- Support single-symbol and batch option evaluations.
- Support selectable risk profiles: `保守`, `均衡`, and `进取`.
- Provide an interactive Chinese CLI similar to the existing stock screening CLI.
- Generate structured order suggestions without placing orders.
- Let the user manually fill trade execution details and start monitoring.
- Support frontend monitoring status and Feishu alerts for important events.
- Preserve existing stock screening, single-stock analysis, and custom-list behavior.

## Non-Goals

- Do not place orders through Futu in this version.
- Do not merge options strategy logic into the existing stock screening rule engine.
- Do not require real-time streaming in the first version; polling and manual refresh are acceptable.
- Do not force a trade suggestion when market data is incomplete or stale.

## Architecture

Implement Option Lab inside `MoneyManager/stock_screener` as an independent
module that reuses the existing platform infrastructure.

Suggested backend modules:

- `option_lab/market_data.py`
  - Normalizes Futu, yfinance, and AKShare option data.
  - Provides expiration dates, option chains, contract quotes, IV, Greeks,
    volume, open interest, bid/ask, underlying price, and underlying K-lines.
- `option_lab/strategies.py`
  - Models the options strategy basket.
  - Produces strategy candidates and `合约明细`.
- `option_lab/risk.py`
  - Applies risk-profile gating, liquidity thresholds, max loss checks,
    capital usage checks, and warning generation.
- `option_lab/service.py`
  - Orchestrates single-symbol and batch evaluations.
  - Produces ranked strategy candidates and order suggestions.
- `option_lab/monitor.py`
  - Monitors filled positions and creates alert events.
- `web/main.py`
  - Adds `/api/options/*` endpoints.
- `web_frontend`
  - Adds an independent Option Lab page.
- `interactive_option_lab.py`
  - Adds the Python implementation for the Chinese command-line workflow.
  - Reuses the same backend services as the web API.
- `run_option_lab_shell.sh`
  - Adds the user-facing interactive shell entrypoint.
  - Running this file starts the persistent `option-lab>` command loop.

The option flow must stay separate from the existing database-driven stock rule
engine. Existing stock signals can be reused as evidence, but options strategy
evaluation has its own scoring, risk, persistence, and UI.

## User Flow

1. The user opens the Option Lab page.
2. The user chooses single-symbol or batch mode.
3. The user inputs market, symbol or symbols, risk profile, capital assumptions,
   holding assumptions, and strategy scope.
4. The backend fetches underlying market data and option chain data.
5. The backend ranks strategy candidates and creates order suggestions.
6. The user reviews the suggestion and manually places the trade in Futu.
7. The user fills execution details in the frontend.
8. The system creates a monitored position.
9. The monitoring job refreshes market data, updates position state, and creates
   frontend and Feishu alerts.

Future Futu read-only sync can import positions and fills automatically, but
manual fill entry must work first.

CLI flow:

1. The user runs `./run_option_lab_shell.sh`.
2. The CLI prints `MoneyManager 期权实验室` and enters the `option-lab>` shell.
3. The user runs `eval`, `batch`, `plans`, `fill`, `positions`, `refresh`,
   `help`, or `exit`.
4. The CLI prompts for market, symbol or symbols, risk profile, capital
   assumptions, holding assumptions, and strategy scope.
5. The CLI prints Chinese summaries and can optionally save CSV/JSON reports.
6. If the user chooses a strategy candidate, the CLI can save an order plan.
7. After the user manually trades in Futu, the CLI can collect fill details and
   start monitoring.

## Data Model

Add these tables:

- `option_evaluation_runs`
  - One single-symbol or batch evaluation request.
  - Stores mode, request parameters, risk profile, status, warnings, and timestamps.
- `option_evaluation_items`
  - Per-symbol evaluation summary for batch mode.
  - Stores market, code, status, best strategy summary, data quality, and error reason.
- `option_strategy_candidates`
  - Ranked strategy candidates for each evaluated symbol.
  - Stores strategy name, score, recommendation status, `合约明细`,
    order suggestion, risk metrics, and warnings.
- `option_order_plans`
  - User-saved order suggestions.
  - Stores selected candidate, planned `合约明细`, planned limit price,
    allowed slippage, suggested quantity, max loss, target profit, stop loss,
    take profit, planned holding period, and exit conditions.
- `option_tracked_positions`
  - Actual positions after the user manually fills execution details.
  - Stores real filled `合约明细`, price, quantity, fill time, fees,
    linked order plan, monitoring state, and current recommendation.
- `option_monitor_events`
  - Monitoring events used by the frontend and Feishu.
  - Stores severity, event type, message, market snapshot reference, and timestamps.
- `option_market_snapshots`
  - Market data snapshots saved during evaluation and monitoring.
  - Stores provider, underlying quote, option chain summary, selected contract
    quotes, IV/Greeks when available, data timestamp, and raw normalized payload.

The market snapshot table is required, not optional, because it allows the user
to review why a specific strategy was recommended at that time.

## Strategy Basket

The first version should model the full strategy basket, grouped by use case:

- Directional long premium:
  - Long Call (`买入看涨期权`).
  - Long Put (`买入看跌期权`).
- Directional spreads:
  - Bull Call Spread (`牛市看涨价差`).
  - Bear Put Spread (`熊市看跌价差`).
  - Bull Put Spread (`牛市看跌价差`).
  - Bear Call Spread (`熊市看涨价差`).
- Income enhancement:
  - Covered Call (`备兑看涨`).
  - Cash Secured Put (`现金担保卖出看跌`).
- Long volatility:
  - Long Straddle (`买入跨式`).
  - Long Strangle (`买入宽跨式`).
  - Calendar Spread (`日历价差`).
- Range and short volatility:
  - Iron Condor (`铁鹰式`).
  - Iron Butterfly (`铁蝶式`).
  - Short Straddle (`卖出跨式`).
  - Short Strangle (`卖出宽跨式`).
- Complex leverage:
  - Butterfly (`蝶式价差`).
  - Ratio Spread (`比率价差`).
  - Backspread (`反向比率价差`).

Frontend and reports must use the term `合约明细`, not `leg`.
Frontend visible strategy names must use the Chinese labels above. English
strategy names can remain backend enum names, raw provider fields, or developer
diagnostic fields, but should not be shown as the primary user-facing name.

`合约明细` contains rows with:

- `买卖方向`.
- `期权类型`.
- `合约代码`.
- `到期日`.
- `行权价`.
- `建议价格`.
- `数量`.

## Scoring

Strategy scoring has four layers:

- Underlying signal score:
  - Reuses existing K-line and stock strategy evidence where practical, such as
    ZuoYi, EMA, RSI, volume, trend strength, and support/resistance.
- Option quality score:
  - Uses bid/ask spread, volume, open interest, Delta, IV, Theta, DTE, and event
    proximity when available.
- Strategy fit score:
  - Matches strategy type with bullish, bearish, range-bound, high-volatility,
    or low-volatility market assumptions.
- Risk score:
  - Uses max loss, capital usage, breakeven distance, liquidity, exit feasibility,
    and holding-period fit.

The final output is not just a buy/sell answer. The frontend must display these
fields in Chinese:

- `策略名称`.
- `合约明细`.
- `建议限价`.
- `允许滑点`.
- `建议数量`.
- `最大亏损`.
- `目标收益`.
- `盈亏平衡点`.
- `止损价`.
- `止盈价`.
- `计划持有期`.
- `退出条件`.

## Risk Profiles

Risk profile is a runtime option selected in the frontend.

`保守`:

- Prefer limited-loss strategies.
- Exclude or strongly de-prioritize unlimited-loss strategies.
- Require stronger liquidity, tighter spreads, and clearer data quality.
- Prefer DTE around 30-90 days unless the user explicitly changes assumptions.
- Use lower position-size limits.

`均衡`:

- Allow long premium, spreads, covered calls, cash secured puts, and iron condors.
- Keep naked short premium and complex leverage strategies low priority with
  explicit warnings.
- Use moderate liquidity and position-size thresholds.

`进取`:

- Let the full strategy basket participate in ranking.
- Allow shorter DTE and higher Gamma/Theta exposure.
- Require explicit risk display for unlimited-loss, high-margin, or low-liquidity
  strategies.
- Require clear stop-loss and monitoring rules.

## Market Data

Create a unified `OptionMarketDataProvider` abstraction. The rest of the system
must consume normalized data instead of provider-specific raw payloads.

Normalized data includes:

- Expiration dates.
- Option chain contracts.
- Contract quotes.
- IV and Greeks when available.
- Volume and open interest.
- Bid, ask, last, and quote timestamp.
- Underlying quote and K-lines.

Market support:

- US stock options:
  - Prefer Futu OpenD.
  - Fallback to yfinance for expiration dates, option chains, and quote fields.
  - Do not use AKShare as the main US options source.
- HK stock options:
  - Prefer Futu OpenD.
  - Use yfinance mainly for underlying quote or K-line fallback.
- A-share ETF and index options:
  - Prefer AKShare for ETF and index option interfaces.
  - Use Futu if the local OpenD account has data access.
  - Use yfinance only as a limited underlying quote fallback.

Standardization rules:

- Keep existing `market + code` identity, such as `US.AAPL`, `HK.00700`,
  and `SH.510050`.
- Store normalized `option_code`.
- Store provider-specific raw option code separately.
- Store `currency` for all money values.
- Do not mix USD, HKD, and CNY values without explicit conversion.

Data degradation rules:

- If the option chain is unavailable, mark the item as `数据不足` and do not
  create an order suggestion.
- If IV or Greeks are unavailable, allow basic evaluation only, lower the option
  quality score, and show a warning.
- If bid/ask is missing or spread is too wide, do not recommend real trading.
- If volume or open interest is missing, lower liquidity score; conservative
  mode should filter more aggressively.
- If quotes are stale, mark data as `行情过期` and do not create an order suggestion.

## Frontend

Add an independent Option Lab page. All frontend-visible copy, labels, table
headers, action buttons, empty states, warnings, event names, and report labels
must be Chinese. Backend API names, enum values, provider raw fields, and code
identifiers can remain English.

Main areas:

- `期权实验室`
  - Supports single-symbol and batch modes.
  - Frontend labels: `单标的评估`, `批量评估`, `市场`, `标的代码`,
    `风险偏好`, `资金规模`, `已有持仓`, `策略范围`, `最大可接受亏损`,
    `计划持有期`, `开始评估`.
  - Risk profile choices: `保守`, `均衡`, `进取`.
  - Outputs: `正股信号摘要`, `期权数据质量`, `推荐结论`, `策略候选排行`.
- `建议详情`
  - Shows `策略名称`, `评分`, `适用理由`, `合约明细`, `建议限价`,
    `允许滑点`, `建议数量`, `最大亏损`, `目标收益`, `盈亏平衡点`,
    `止损价`, `止盈价`, `计划持有期`, `退出条件`.
  - Provides actions: `保存订单建议`, `回填成交并加入监控`.
  - Clearly states: `系统不会自动下单，请在富途手动下单后回填成交信息`.
- `持仓监控`
  - Shows filled or synced option positions.
  - Displays `策略名称`, `标的`, `合约明细`, `成交价`, `当前价`,
    `浮动盈亏`, `剩余到期天数`, `止损状态`, `止盈状态`, `最近提醒`.

Batch mode should first show `机会排行`. Users can drill into a symbol to view
`策略详情`.

## Interactive CLI

Add `stock_screener/interactive_option_lab.py` and
`stock_screener/run_option_lab_shell.sh`. The shell file is the user-facing
entrypoint and should start a persistent `option-lab>` command loop. The Python
implementation should follow the same interaction style as
`stock_screener/interactive_screening.py`: Chinese title, numbered choices,
sensible defaults, explicit input examples, and clear summary before execution.

All CLI prompts, menu items, output summaries, warnings, and report labels must
be Chinese.

Required CLI modes:

- `单标的评估`
  - Prompts: `市场`, `标的代码`, `风险偏好`, `资金规模`, `已有持仓`,
    `策略范围`, `最大可接受亏损`, `计划持有期`, `是否保存报告`.
  - Output: `正股信号摘要`, `期权数据质量`, `推荐结论`, `策略候选排行`.
- `批量评估`
  - Prompts: `市场`, `标的代码列表`, optional `从筛选结果导入`,
    `风险偏好`, `资金规模`, `计划持有期`, `是否保存报告`.
  - Output: `机会排行`, per-symbol `未生成建议原因`, and best candidate summary.
- `保存订单建议`
  - Lets the user select a candidate from the latest run and saves an order plan.
  - Prints `系统不会自动下单，请在富途手动下单后回填成交信息`.
- `回填成交并加入监控`
  - Prompts: `订单建议ID`, `合约代码`, `买卖方向`, `期权类型`, `成交价`,
    `数量`, `成交时间`, optional `手续费`.
  - Creates a monitored position.
- `查看持仓监控`
  - Lists monitored positions with `策略名称`, `标的`, `浮动盈亏`,
    `剩余到期天数`, `止损状态`, `止盈状态`, and `最近提醒`.
- `手动刷新监控`
  - Refreshes one position or all active positions.
  - Prints generated `紧急`, `重要`, and `提示` events.

Report output:

- CSV and JSON report paths should be configurable.
- Defaults should be under `logs/`, consistent with the existing screening CLI.
- Reports must use Chinese column names for user-facing fields.

The CLI should call the same service layer as `/api/options/*`; it should not
duplicate option strategy, risk, or monitoring logic.

## API

Add `/api/options/*` endpoints:

- `POST /api/options/evaluate`
  - Runs a single-symbol evaluation.
- `POST /api/options/evaluate-batch`
  - Runs a batch evaluation from symbols or imported screening results.
- `GET /api/options/evaluations/{run_id}`
  - Returns evaluation results and strategy candidate details.
- `POST /api/options/order-plans`
  - Saves a selected strategy candidate as an order plan.
- `POST /api/options/order-plans/{plan_id}/fills`
  - Records user-entered fill details and starts monitoring.
- `GET /api/options/positions`
  - Lists monitored option positions.
- `GET /api/options/positions/{position_id}`
  - Shows position detail, events, and current suggested action.
- `POST /api/options/positions/{position_id}/refresh`
  - Refreshes one monitored position.
- `POST /api/options/monitor/run`
  - Runs one monitoring pass for background jobs or admin use.

Future endpoint:

- `POST /api/options/futu/sync-positions`
  - Read-only Futu position and fill sync.
  - Matches positions to order plans when possible.
  - Falls back to manual confirmation when matching is unclear.

All APIs should return `warnings` and `data_quality` so the frontend can explain
why no recommendation was generated. The frontend display for these values must
use Chinese labels such as `风险提示`, `数据质量`, and `未生成建议原因`.

## Monitoring

Monitoring sources:

- Manual fill records entered in the frontend.
- Future Futu read-only synced positions and fills.

Monitoring inputs:

- Underlying price.
- Option bid, ask, and last price.
- IV and Greeks when available.
- Volume and open interest.
- DTE.
- Filled price, quantity, fill time, strategy name, `合约明细`, planned
  holding period, stop loss, take profit, and original order-plan risk metrics.
  The frontend display labels are `剩余到期天数`, `成交价`, `数量`,
  `成交时间`, `策略名称`, `计划持有期`, `止损价`, `止盈价`, and `原始风控指标`.

Event severities:

- `紧急`
  - Stop loss triggered.
  - Expiration is near and no action has been recorded.
  - Max loss or capital risk exceeds limit.
  - Quote abnormality makes normal exit difficult.
- `重要`
  - Take-profit area reached.
  - Strategy thesis invalidated.
  - Underlying breaks a key price level.
  - IV changes materially.
  - Liquidity worsens materially.
- `提示`
  - Holding period is over halfway through.
  - DTE warning zone entered.
  - Price approaches stop loss or take profit.
  - Real fill differs materially from the planned order.

Feishu should send only `紧急` and `重要` events by default. `提示` events should
be visible in the frontend but not pushed by default.

Suggested refresh cadence:

- `保守`: every 30-60 minutes, with higher frequency near stop loss or expiration.
- `均衡`: every 15-30 minutes.
- `进取`: every 5-15 minutes, especially for short-DTE or high-Gamma positions.

The first version can support manual refresh plus a background scheduled monitor.
Real-time subscriptions can be added later.

## Testing

Backend tests:

- Provider normalization with fake Futu, yfinance, and AKShare payloads.
- Missing data cases: no chain, missing IV, missing Greeks, missing bid/ask,
  stale quote, missing volume or open interest.
- Strategy basket generation for each strategy group.
- Risk-profile behavior for `保守`, `均衡`, and `进取` profiles.
- API behavior for evaluation, batch evaluation, order plan saving, manual fill,
  position listing, and monitor refresh.
- Interactive CLI prompt flow using fake input and fake services.
- Monitoring event generation for stop loss, take profit, expiration warning,
  liquidity degradation, quote abnormality, and strategy invalidation.
- Regression tests proving stock screening, single-stock analysis, and custom-list
  flows are not affected.

Frontend tests:

- `期权实验室` accepts single-symbol input and renders strategy candidates.
- `批量评估` renders `机会排行` and links to details.
- `风险偏好` selection changes request payload and displayed assumptions.
- Strategy detail uses `合约明细` terminology.
- Users can `保存订单建议`, `回填成交并加入监控`, and see a monitored position.
- Monitoring page renders `提醒级别` and `当前建议动作`.
- Frontend-visible labels and user-facing messages are Chinese.

CLI tests:

- `单标的评估` collects inputs and calls the shared evaluation service.
- `批量评估` accepts comma-separated symbols and renders `机会排行`.
- `保存订单建议` and `回填成交并加入监控` call the shared order-plan and monitoring services.
- CLI prompts and output summaries are Chinese.

## Acceptance Criteria

- US, HK, and A-share ETF/index options have explicit data adapter paths.
- The frontend provides `保守`, `均衡`, and `进取` risk-profile choices.
- Single-symbol and batch option evaluations are supported.
- Strategy candidates include structured `合约明细`, risk metrics, and exit conditions.
- The system never places orders automatically.
- Manual fill entry creates monitored positions.
- Frontend monitoring and Feishu important-event alerts are supported.
- The interactive CLI supports evaluation, order-plan saving, manual fill entry,
  monitoring list, and manual monitor refresh.
- Incomplete or stale market data blocks trade recommendations and explains why.
- `option_market_snapshots` records the data context used for recommendations.
- Existing stock screening and single-stock workflows continue to pass regression tests.

## Self-Review Notes

- No placeholder requirements remain.
- The design keeps Option Lab independent from the stock screening rule engine.
- The term `合约明细` is used instead of `leg`.
- Frontend-visible text is explicitly required to be Chinese.
- Automatic Futu order placement is explicitly out of scope.
- Futu read-only sync is designed as a future enhancement while manual fill entry
  remains the first working monitoring path.
- Interactive CLI is included and must reuse the same service layer as the API.
