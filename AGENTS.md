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
