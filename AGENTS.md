<claude-mem-context>
# Memory Context

# [SimonZhou96] recent context, 2026-05-09 2:48pm GMT+8

No previous sessions found.
</claude-mem-context>

# Stock Screener Rule Engine

This project uses a database-driven rule engine for `MoneyManager/stock_screener`.
When the user asks to add, modify, explain, or debug screening rules, use this
section as the source of truth for the rule-chain syntax and summarize the
needed database syntax before making changes.

## Rule Storage

- Deployment SQL lives at `MoneyManager/stock_screener/sql/001_screening_rules.sql`.
- Runtime schema and default seeds are also created from
  `MoneyManager/stock_screener/db.py`.
- Keep the SQL file and the `db.py` default constants in sync whenever default
  rules or default chains change.
- Default inserts use `INSERT IGNORE`; they initialize missing rows and must not
  overwrite user-edited database rules.

Two tables define the engine:

- `screening_rule_metadata`: atomic rule metadata. Important fields:
  `market`, `rule_key`, `rule_name`, `rule_type`, `implementation`,
  `params_json`, `enabled`, `display_order`, `description`.
- `screening_rule_chains`: market-level rule chains. Important fields:
  `market`, `chain_key`, `chain_name`, `expression_json`, `enabled`,
  `priority`, `description`.

The active chain for a market is the enabled row in `screening_rule_chains` with
the lowest `priority`, then lowest `id`.

## Rule Chain JSON DSL

`expression_json` is a JSON object interpreted by `RuleExpressionEvaluator`.
Supported operators:

```json
{ "ref": "rule_key" }
```

Execute one atomic rule by `rule_key`.
If the rule does not exist or `enabled=0`, it evaluates to `false`.

```json
{ "and": [expr1, expr2] }
```

Evaluate all child expressions. Empty `and` evaluates to `true`.

```json
{ "any": [expr1, expr2] }
```

Evaluate any child expression. Empty `any` evaluates to `false`.

```json
{ "all_enabled": ["rule_key_a", "rule_key_b"] }
```

Filter the listed keys to rules that exist and have `enabled=1`, then require
all of those enabled rules to pass. If no listed rule is enabled, this evaluates
to `true`. This is the preferred operator for optional hard filters.

```json
{ "any_enabled": ["rule_key_a", "rule_key_b"] }
```

Filter the listed keys to rules that exist and have `enabled=1`, then require
at least one enabled rule to pass. If no listed rule is enabled, this evaluates
to `false`. This is the preferred operator for optional alternative strategies.

Rule truth semantics:

- `rule_type="filter"`: `FilterResult.PASS` and `FilterResult.SKIP` are truthy;
  `FAIL` and `ERROR` are false.
- `rule_type="strategy"`: `StrategizerOutput.satisfied=True` is truthy.
- Database `implementation` values are allowlisted in `RuleRegistry.default()`.
  Do not store or execute arbitrary code in the database.

## Default Chain

The default HK/US/A chain is:

```json
{
  "and": [
    {
      "all_enabled": [
        "market_cap_range",
        "avg_daily_volume_range",
        "price_range",
        "pe_range",
        "profitability"
      ]
    },
    { "ref": "zuoyi_signal" },
    {
      "any_enabled": [
        "ema_breakout",
        "rsi_oversold",
        "rsi_overbought",
        "volume_spike_prior3",
        "daily_drop_6_65",
        "daily_rise_4_45"
      ]
    }
  ]
}
```

Meaning:

```text
all enabled hard filters pass && zuoyi_signal passes && at least one enabled
non-zuoyi strategy passes
```

## Built-In Atomic Rules

Hard filters:

- `market_cap_range`: `filter`, `MarketCapFilter`, params
  `{"min_cap": null, "max_cap": null}`.
- `avg_daily_volume_range`: `filter`, `AvgDailyVolumeFilter`, params
  `{"min_volume": null, "max_volume": null}`.
- `price_range`: `filter`, `PriceFilter`, params
  `{"min_price": null, "max_price": null}`.
- `pe_range`: `filter`, `PEFilter`, params
  `{"min_pe": null, "max_pe": null, "allow_negative": false}`.
- `profitability`: `filter`, `ProfitabilityFilter`, params
  `{"require_profitable": true}`.

Strategies:

- `zuoyi_signal`: `strategy`, `ZuoYiStrategizer`, params
  `{"signal_window": 3, "include_bullish": true, "include_bearish": true}`.
- `ema_breakout`: `strategy`, `EMABreakoutStrategizer`, params
  `{"ema_short": 10, "ema_long": 150}`.
- `rsi_oversold`: `strategy`, `RSIOversoldStrategizer`, params
  `{"period": 14, "threshold": 30.0}`.
- `rsi_overbought`: `strategy`, `RSIOverboughtStrategizer`, params
  `{"period": 14, "threshold": 70.0}`.
- `volume_spike_prior3`: `strategy`,
  `TodayVolumeExceedsPrior3MaxStrategizer`, params `{}`.
- `daily_drop_6_65`: `strategy`, `DailyDrop6To65Strategizer`, params
  `{"pct_min": -6.5, "pct_max": -6.0}`.
- `daily_rise_4_45`: `strategy`, `DailyRise4To45Strategizer`, params
  `{"pct_min": 4.0, "pct_max": 4.5}`.

Hard filters are seeded with `enabled=0` by default to preserve the current
behavior. Enable and parameterize them in `screening_rule_metadata` when they
should participate in `all_enabled`.

## Rule Modification Guidelines

When the user asks to modify rules:

1. Summarize the intended DSL expression in plain language and JSON.
2. Identify which `screening_rule_metadata` rows need `enabled` or
   `params_json` changes.
3. Identify whether `screening_rule_chains.expression_json` must change.
4. If the change should become a deployment default, update both
   `sql/001_screening_rules.sql` and the default constants in `db.py`.
5. If a new `implementation` is needed, add it to `RuleRegistry.default()` and
   add tests before referencing it from database rows.
6. Prefer validating with a small CSV-based full-flow regression first: use the
   listed stocks, pull K lines, run `use_db_rule_engine=True`, generate temporary
   CSVs, and compare them to the expected CSV files.

Common SQL examples:

```sql
UPDATE screening_rule_metadata
SET enabled = 1,
    params_json = JSON_OBJECT('min_price', 5, 'max_price', 100)
WHERE market = 'HK' AND rule_key = 'price_range';
```

```sql
UPDATE screening_rule_chains
SET expression_json = CAST('{"and":[{"ref":"zuoyi_signal"},{"ref":"ema_breakout"}]}' AS JSON)
WHERE market = 'HK' AND chain_key = 'default_zuoyi_and_other';
```

# Skill Abstraction Guidance

During project conversations, watch for workflows that are reusable enough to
become a skill or improvements to an existing skill.

If a conversation reveals a repeatable process, domain-specific procedure,
validation workflow, code-generation pattern, or project convention that would
help future agents, ask the user whether they want to create a new skill or
iterate an existing one. Do not create or modify skills silently.

When the user agrees:

1. Summarize the recent relevant conversation:
   - User goal and trigger phrases.
   - Files, commands, schemas, APIs, or workflows involved.
   - Decisions made and constraints discovered.
   - Validation steps that proved the workflow.
2. Decide whether this is a new skill or an update to an existing skill.
3. Prefer project-level skills under `.agents/skills/<skill-name>` unless the
   user explicitly requests a global skill.
4. Use the `skill-creator` skill when creating or materially updating a skill.
5. Keep `SKILL.md` concise and move detailed examples or long checklists into
   `references/`.
6. Run the skill validator after changes when possible. If validation is
   blocked by missing local dependencies, state the blocker and perform a
   manual structure/content check.
7. Report the skill path, what changed, and how future agents should use it.

# Stock Screener Signal Analysis Chain

`MoneyManager/stock_screener` can optionally run a best-effort search + LLM
analysis pass after a market's screening CSV files are generated. This pass is
only an auxiliary judgement layer: it must never decide whether a stock passed
screening and must never block the original CSV or Feishu delivery path.

## Runtime Flow

- Scheduler integration lives in `scheduled_daily_job.py`, immediately after
  the market CSV, `_no_etf.csv`, and `_etf_only.csv` files are written.
- The scheduler calls `signal_analysis.service.run_signal_analysis_for_market`
  only for the base market CSV. Original CSV paths must always remain in
  `MarketScreeningResult.csv_paths`; AI artifacts may only be appended.
- If analysis fails, log a warning and continue. Do not set
  `MarketScreeningResult.error` for AI-analysis failures.

## Object Model

- `signal_analysis.search_providers.SearchProvider` is the search interface.
  Use `TavilySearchProvider` for Tavily and `NullSearchProvider` when no search
  key is configured.
- `signal_analysis.llm_providers.LLMProvider` is the model interface. Use
  `OpenAICompatibleLLMProvider` for `/v1/chat/completions` compatible services
  and `NullLLMProvider` when model credentials are missing.
- `signal_analysis.factories.SearchProviderFactory` and
  `LLMProviderFactory` are the only places that should read provider-specific
  environment variables.
- `signal_analysis.chain.SignalAnalysisChain` runs ordered `AnalysisStep`
  objects. Add new behavior by adding a small step instead of expanding the
  scheduler or service.

## Environment Variables

- `ENABLE_LLM_ANALYSIS=1` enables automatic post-screening analysis. Set `0`,
  `false`, `no`, or `off` to disable it.
- `TAVILY_API_KEY` enables Tavily search. Missing key means CSV-only model
  analysis if an LLM is configured.
- `LLM_API_BASE` defaults to `https://api.openai.com`.
- `LLM_API_KEY` and `LLM_MODEL` are required for model analysis. If either is
  missing, analysis is skipped and no AI artifacts are generated.
- `LLM_ANALYSIS_BATCH_SIZE`, `LLM_ANALYSIS_TIMEOUT_SEC`, and
  `SIGNAL_SEARCH_MAX_RESULTS` tune batching, request timeout, and search depth.
- `SIGNAL_MARKET_CONTEXT_LIMIT`, `SIGNAL_COMPANY_CONTEXT_LIMIT`, and
  `SIGNAL_SEARCH_CONTENT_CHARS` limit how much search text enters each model
  prompt. Defaults are conservative (`2`, `2`, `300`) for low TPM/RPM model
  plans.
- Manual hot news can be configured with `SIGNAL_MANUAL_HOT_NEWS_FILE`,
  `SIGNAL_MANUAL_MARKET_HOT_NEWS`, `SIGNAL_MANUAL_MARKET_HOT_NEWS_<MARKET>`,
  `SIGNAL_MANUAL_NEWS_SOURCES`, `SIGNAL_MANUAL_NEWS_SOURCES_<MARKET>`,
  `SIGNAL_MANUAL_COMPANY_HOT_NEWS_JSON`, and
  `SIGNAL_MANUAL_COMPANY_NEWS_SOURCES_JSON`. If manual hot news is present, it
  overrides searched hot-news context for that market or stock code.

## Artifacts and Persistence

- The screening CSV must be generated before AI analysis starts. After a
  successful analysis, append or refresh AI columns in that existing CSV.
  Failures must preserve the original CSV for Feishu sending.
- Successful analysis appends or refreshes AI columns in the existing market
  CSV and split CSV files; it must not create `<base>_ai.csv`.
- Successful analysis may write `<base>_ai_report.md` as an extra Feishu
  attachment.
- Enhanced CSV columns are: `AI分析状态`, `信号可靠性评分`, `模型置信度`,
  `辅助方向判断`, plus the corresponding rubric columns
  `信号可靠性评分口径`, `模型置信度口径`, `辅助方向判断口径`, and the factor
  columns `关键利好因素`, `关键风险因素`, `宏观/政策因素`, `公司事件`,
  `市场热点新闻`, `公司热点新闻`, `新闻影响判断`, `新闻来源`, `信息来源`.
- Persistence DDL lives in
  `MoneyManager/stock_screener/sql/002_signal_analysis.sql`; keep it in sync
  with `MarketDatabase.init_signal_analysis_schema`.
- `screening_signal_analysis` is keyed by `(task_id, market, code)` and stores
  model output for audit, not for screening decisions.
