# Option Lab Macro Analysis Design

## Background

Option Lab already evaluates the option strategy basket and produces a ranked
candidate list for a stock, ETF, or index. The current candidate score is an
option strategy score: it measures whether the option structure, contract
selection, liquidity, risk, capital usage, and holding period fit the request.

The user also wants to see whether the broader environment supports the trade.
This should reuse the existing `signal_analysis` capability in
`stock_screener`, including search, LLM analysis, market hot news, company or
ETF theme context, hot sector matching, and main-force outflow risk. The new
analysis must be explicitly optional because it consumes Tavily credits and LLM
tokens.

## Goals

- Add optional macro-level analysis to Option Lab.
- Keep the existing option strategy score unchanged.
- Add a macro analysis score only when the user enables macro analysis.
- Add a composite score only when macro analysis is enabled and succeeds.
- Apply the same macro result to all strategy candidates for the same symbol.
- Avoid per-strategy Tavily or LLM calls.
- Expose the macro-analysis switch in the frontend and in the interactive shell.
- Keep Option Lab usable when search or LLM providers are unavailable.

## Non-Goals

- Do not let macro analysis place trades or trigger Futu orders.
- Do not replace the existing option strategy score.
- Do not ask the LLM to price individual option contracts.
- Do not call Tavily or the LLM once per strategy candidate.
- Do not require macro analysis for monitoring, order-plan saving, or manual fill
  entry in this version.

## Terminology

Use these user-facing Chinese terms:

- `期权评分`: the existing option strategy score. This is the current `评分` value
  and should remain available for compatibility.
- `宏观分析评分`: the new macro-only score, 0-100.
- `综合评分`: `期权评分 * 70% + 宏观分析评分 * 30%`.
- `宏观方向`: `偏多`, `偏空`, `中性`, `回避`, or `信息不足`.
- `新闻影响`: `利好`, `利空`, `中性`, `混合`, `无明显新闻`, or `信息不足`.
- `热点匹配`: `重点`, `相关`, `观察`, `无明确关联`, or `未知`.
- `主力资金风险`: `高`, `中`, `低`, or `数据不足`.

Backend developer fields can use English names, but frontend, shell output, and
saved reports should show Chinese labels.

## Score Boundaries

The existing candidate score is renamed conceptually as `期权评分`. It evaluates
the option strategy itself and must not be mutated by macro analysis.

Macro analysis evaluates only the broader environment for the underlying symbol:

- market and macro news;
- company events for stocks;
- ETF/index theme and macro context for ETFs;
- hot sector or hot theme matching;
- main-force outflow risk and other available capital-flow evidence;
- information gaps and source quality.

The LLM should not decide which option strategy is structurally best. It should
produce a symbol-level macro view. The service then attaches that same macro view
to all candidates for the symbol.

For every candidate, when macro analysis is enabled and successful:

```text
综合评分 = round(期权评分 * 0.7 + 宏观分析评分 * 0.3, 2)
```

When macro analysis is disabled or unavailable, `宏观分析评分` and `综合评分` are
empty. The candidate remains valid and continues to use `期权评分`.

## User Flow

### Frontend

The Option Lab form adds two controls:

- `开启宏观层面分析`, default off.
- `强制重新分析`, default off and only enabled when macro analysis is on.

If `开启宏观层面分析` is off:

- the frontend sends `enable_macro_analysis: false`;
- the backend must not call Tavily or any LLM provider;
- candidate tables show `期权评分` only;
- macro-analysis sections are hidden;
- sorting is only by `期权评分`.

If `开启宏观层面分析` is on:

- the frontend sends `enable_macro_analysis: true`;
- the backend fetches or reuses cached macro analysis;
- candidate tables show `期权评分`, `宏观分析评分`, and `综合评分`;
- the detail panel shows macro direction, news impact, hot-sector match,
  main-force risk, summary, positive factors, risk factors, macro factors,
  source URLs, cache status, analysis time, expiry time, and warnings;
- the frontend can sort by `期权评分` or `综合评分`.

### Interactive Shell

`run_option_lab_shell.sh` continues to start the `option-lab>` shell. Evaluation
prompts add:

- `是否开启宏观层面分析`, default `否`.
- `是否强制重新分析`, default `否`; ask this only when macro analysis is enabled.

When macro analysis is enabled, the shell output prints each candidate with:

- `期权评分`;
- `宏观分析评分`;
- `综合评分`;
- `宏观方向`;
- `新闻影响`;
- `热点匹配`;
- `主力资金风险`.

When macro analysis is disabled, the shell prints only the option score and
should not mention empty macro fields.

## Backend Data Flow

Single-symbol evaluation:

1. `OptionLabService.evaluate_single(...)` fetches the option market snapshot.
2. The existing strategy engine creates all strategy candidates and applies risk
   profile filters. Each candidate has `期权评分`.
3. If `enable_macro_analysis` is false, persist and return candidates without
   macro fields.
4. If `enable_macro_analysis` is true, call a new macro-analysis component once
   for the underlying symbol.
5. Attach the returned macro result to every candidate.
6. Compute `综合评分` for each candidate.
7. Persist candidates with macro fields and return the response.

Batch evaluation:

1. Generate option candidates per symbol as today.
2. For symbols where macro analysis is enabled, analyze per symbol with cache.
3. Market and hot-sector searches should be reusable across symbols in a batch
   where practical, but the first implementation can rely on symbol-level cache
   and avoid per-candidate calls.
4. Each batch row returns candidates with macro fields only when available.

## Backend Module Design

Add `stock_screener/option_lab/macro_analysis.py`.

Suggested responsibilities:

- Convert Option Lab inputs into `signal_analysis.models.ScreeningSignalRow`.
- Build provider instances using existing factories:
  - `signal_analysis.factories.SearchProviderFactory`;
  - `signal_analysis.factories.LLMProviderFactory`.
- Reuse existing signal-analysis search and normalization logic where possible.
- Produce a symbol-level `OptionMacroAnalysis` result.
- Cache macro results by symbol and analysis profile.
- Attach macro fields to `StrategyCandidate` objects or candidate response rows.

Suggested dataclass:

```python
@dataclass(frozen=True)
class OptionMacroAnalysis:
    macro_score: Optional[float]
    macro_direction: str
    news_impact: str
    hot_sector_mark: str
    main_force_risk_level: str
    summary: str
    positive_factors: list[str]
    risk_factors: list[str]
    macro_factors: list[str]
    source_urls: list[str]
    warnings: list[str]
    cached: bool
    provider: str
    analyzed_at: str
    expires_at: str
```

The first implementation may map `SignalAnalysisResult.reliability_score` to
`macro_score`, but the prompt and response labels must frame it as macro-only
for Option Lab. If the existing signal prompt is too stock-screening-specific,
add a small Option Lab prompt wrapper that reuses the same providers but asks for
macro support of the underlying symbol instead of re-evaluating option
contracts.

## API Contract

Extend evaluation requests:

- `enable_macro_analysis: boolean = false`.
- `force_macro_refresh: boolean = false`.
- `macro_cache_ttl_minutes: number = 60`.

Extend evaluation responses:

- top-level `macro_analysis`, present only when macro analysis was requested;
- per-candidate fields when macro analysis succeeds:
  - `期权评分`;
  - `宏观分析评分`;
  - `综合评分`;
  - `宏观方向`;
  - `新闻影响`;
  - `热点匹配`;
  - `主力资金风险`;
  - `宏观摘要`;
  - `关键利好因素`;
  - `关键风险因素`;
  - `宏观/政策因素`;
  - `信息来源`.

Compatibility rule:

- existing `评分` remains equal to `期权评分`;
- order-plan save APIs keep accepting existing candidate IDs;
- if macro fields exist on the saved candidate, the saved order plan should
  retain them for review and monitoring context.

## Persistence

Add a cache table, for example `option_macro_analysis_cache`:

- `cache_key`;
- `market`;
- `code`;
- `analysis_profile`;
- `macro_score`;
- `macro_direction`;
- `news_impact`;
- `hot_sector_mark`;
- `main_force_risk_level`;
- `summary`;
- `positive_factors` JSON;
- `risk_factors` JSON;
- `macro_factors` JSON;
- `source_urls` JSON;
- `warnings` JSON;
- `provider`;
- `created_at`;
- `expires_at`;
- `raw_payload` JSON.

Cache key:

```text
market + normalized_code + yyyy-mm-dd + analysis_profile
```

Default TTL: 60 minutes.

`force_macro_refresh` skips cache lookup. If refresh fails and a non-expired
cache exists, return the cached result with a warning: `刷新失败，使用缓存`.

Candidate persistence should store the macro fields in the existing candidate
payload JSON. A later migration can normalize candidate macro fields if reporting
needs grow.

## Cost Control

Macro analysis is default off. When off, there is zero Tavily and LLM cost.

When on, use one macro analysis per symbol, not per candidate. With the current
Tavily implementation, the expected per-symbol search cost is about three basic
search requests:

- market/macro context;
- hot-sector context;
- company or ETF theme context.

All candidates for the symbol reuse that one result. This keeps a full 18
strategy basket close to the cost of analyzing one symbol, not 18 symbols.

Batch evaluation should also reuse the existing search provider's batch company
search behavior and the macro cache. The service should emit warnings when
providers are unavailable instead of silently spending retries.

## Failure Behavior

- Search unavailable:
  - continue with LLM using available local symbol and main-force context;
  - add warning `联网检索不可用`.
- LLM unavailable:
  - return option candidates normally;
  - leave `宏观分析评分` and `综合评分` empty;
  - add warning `未配置 LLM provider，跳过宏观分析`.
- Macro analysis failure:
  - return option candidates normally;
  - leave macro fields empty;
  - add warning with the failure type and message.
- Cache hit:
  - return cached macro result;
  - mark `cached: true`.
- Forced refresh failure:
  - use a valid cache if present;
  - mark warning `刷新失败，使用缓存`.

Option evaluation must not fail solely because macro analysis fails.

## Frontend Display

Form controls:

- Add a switch or checkbox labeled `开启宏观层面分析`.
- Add a secondary checkbox labeled `强制重新分析`; disabled unless macro analysis
  is on.

Candidate table:

- Without macro analysis: show `策略名称`, `期权评分`, `适用理由`, and existing order
  columns.
- With macro analysis: additionally show `宏观分析评分`, `综合评分`, `宏观方向`,
  `新闻影响`, `热点匹配`, and `主力资金风险`.

Candidate detail:

- Add a `宏观分析` section only when macro analysis was requested.
- Show summary, positive factors, risk factors, macro factors, source URLs,
  cache status, analyzed time, expiry time, and warnings.

Sorting:

- Default sort remains `期权评分`.
- When macro analysis is enabled and at least one candidate has `综合评分`, allow
  sorting by `综合评分`.

## CLI Display

Evaluation prompts:

- `是否开启宏观层面分析 [y/N]`.
- `是否强制重新分析 [y/N]`, only when enabled.

Candidate output:

- Always show `期权评分`.
- Show `宏观分析评分` and `综合评分` only when macro analysis exists.
- Print macro summary and warning text once per symbol, not once per strategy,
  to avoid noisy output.

## Testing

Backend tests:

- Macro analysis disabled does not instantiate or call search/LLM providers.
- Macro analysis enabled calls providers once per symbol and applies the same
  macro score to all candidates.
- Composite score is `期权评分 * 0.7 + 宏观分析评分 * 0.3` rounded to two decimals.
- LLM unavailable returns candidates without macro fields and with warnings.
- Cache hit avoids provider calls.
- Forced refresh failure can fall back to valid cache.
- Batch evaluation does not call macro analysis per candidate.

API tests:

- `enable_macro_analysis=false` response has no macro fields.
- `enable_macro_analysis=true` response includes top-level `macro_analysis` and
  candidate macro fields.
- Existing candidate `评分` remains equal to `期权评分`.

Frontend tests or build checks:

- The form contains `开启宏观层面分析`.
- Macro columns are hidden when disabled.
- Macro columns and detail section appear when enabled.
- `npm run build` passes.

CLI tests:

- Shell prompts for macro analysis enablement.
- Disabled mode does not print empty macro fields.
- Enabled mode prints `期权评分`, `宏观分析评分`, and `综合评分`.
