# Market Intel Macro Scoring Rule Design

## 1. Background

`stock_screener` already has two macro-style atomic rules:

- `company_event_hot_sector_link`: 公司时事与热点板块关联
- `company_event_hot_news_link`: 公司时事与热点新闻关联

Those rules currently reuse fields from `signal_analysis`, such as
`company_events`, `hot_sectors`, `matched_hot_sectors`, `company_hot_news`,
`market_hot_news`, and `news_impact`. They are useful boolean rules, but they do
not make `market_intel` the primary evidence source and they do not express
macro information as a score.

The new requirement is to abstract current `market_intel`, company events,
hot-sector association, and hot-news association into one scoring rule. Technical
strategies remain 0/1 decisions. Macro strategies should integrate many pieces
of evidence, ask AI to score them, and then participate in the final stock score
with a configured weight.

## 2. Goals

- Add one canonical aggregate macro scoring rule for rule chains.
- Use `market_intel` stock and market evidence as the primary input.
- Allow the rule to refresh single-stock market intelligence on demand during
  rule execution.
- Convert macro evidence into a `-100` to `100` score through AI.
- Keep a rule-chain-compatible `pass` / `fail` result through a threshold.
- Always return score details when the macro rule executes, including failed or
  negative results.
- Support a final weighted score made from technical score and macro score.
- Preserve existing technical strategy semantics.
- Keep old macro rules available for backward compatibility.

## 3. Non-Goals

- Do not remove the existing `company_event_hot_sector_link` and
  `company_event_hot_news_link` rules in the first version.
- Do not make every technical filter become a score.
- Do not call AI once per macro dimension. The macro rule should make one AI
  scoring call per stock evidence package.
- Do not let AI invent data that is missing from `market_intel`.
- Do not place orders or turn the score into automatic trading execution.

## 4. Selected Approach

Use **structured preprocessing plus AI scoring**.

The new rule key should be:

- `market_intel_macro_score_link`

The implementation class should be:

- `MarketIntelMacroScoreStrategizer`

The rule is a `strategy` with `strategy_category = macro`. It behaves like an
atomic rule in rule chains, but its internals are score-based:

1. Fetch or refresh stock-level `market_intel`.
2. Fetch or refresh market-level `market_intel`.
3. Build a structured evidence package.
4. Ask AI to score the evidence with a strict JSON schema.
5. Convert the macro score to `pass` / `fail` using a threshold.
6. Store all score details in `details` for frontend display.

The old sector and news rules can keep working as separate boolean rules. The
new aggregate rule is the preferred macro rule for new chains.

## 5. Scoring Model

### 5.1 Technical Strategies

Technical strategies remain binary:

- hit: `1`
- miss: `0`

For scoring, technical hits are normalized into a technical score from `0` to
`100`. A weighted average should be used when a rule chain contains multiple
technical strategies.

Hard filters should remain gate conditions. They should not contribute positive
score unless a later design explicitly changes the filter model.

### 5.2 Macro Strategy

The aggregate macro rule returns a `macro_score` from `-100` to `100`:

- `100`: strong positive macro resonance
- `0`: neutral or insufficient directional evidence
- `-100`: strong negative macro risk

The first-version macro pass threshold is:

- `threshold = 60`

If `macro_score >= 60`, the macro rule returns `pass`.

If `macro_score < 60`, the macro rule returns `fail`, but still returns full
score details.

### 5.3 Final Weighted Score

The selected outer weighting is:

- technical score: `60%`
- macro score: `40%`

Formula:

```text
final_score = technical_score * 0.6 + macro_score * 0.4
```

Because technical score is `0` to `100` and macro score is `-100` to `100`,
`final_score` can be negative when macro information is strongly negative. This
is intentional. It lets negative market intelligence reduce ranking even when a
technical pattern is present.

## 6. Macro Subscores

The AI score is composed from six dimensions.

Default internal macro weights:

| Dimension | Weight | Meaning |
| --- | ---: | --- |
| `company_event_strength` | 20% | 公司事件本身是否具体、重要、可交易 |
| `sector_heat` | 20% | 相关板块是否处于热点或资金聚焦状态 |
| `news_validation` | 20% | 公司事件是否被公司新闻或市场热点新闻验证 |
| `impact_direction` | 20% | 信息整体偏利好、偏利空还是中性 |
| `source_credibility` | 10% | 来源是否可靠、是否有明确出处 |
| `freshness` | 10% | 信息是否足够新，是否已经过期 |

Each dimension should be scored from `-100` to `100`. The weighted macro score
is the weighted sum of the six subscores.

## 7. Execution Flow

```text
rule-chain execution
-> hard filters and technical strategies
-> MarketIntelMacroScoreStrategizer
-> MarketIntelService.get_stock_intel(market, code, refresh policy)
-> MarketIntelService.get_market_digest(market, refresh policy)
-> MacroEvidencePreprocessor
-> structured evidence package
-> AI macro scorer
-> schema validation
-> StrategizerOutput(result, satisfied, reason, details)
-> frontend score details and final ranking
```

The macro rule should support these rule params:

```json
{
  "threshold": 60,
  "refresh_policy": "cache_or_refresh",
  "technical_weight": 0.6,
  "macro_weight": 0.4,
  "sub_weights": {
    "company_event_strength": 0.2,
    "sector_heat": 0.2,
    "news_validation": 0.2,
    "impact_direction": 0.2,
    "source_credibility": 0.1,
    "freshness": 0.1
  }
}
```

`refresh_policy = cache_or_refresh` means:

1. Use valid cached `market_intel` when available.
2. Refresh stock and market intelligence when cache is missing, empty, or stale.
3. Share refreshed results through the rule execution context so the same stock
   does not repeat provider calls in one execution.

## 8. Structured Evidence Package

The preprocessor should turn raw `market_intel` items into compact structured
evidence before AI scoring.

Recommended evidence shape:

```json
{
  "market": "A",
  "code": "000001",
  "trade_date": "2026-05-26",
  "company_events": [
    {
      "title": "example",
      "summary": "example",
      "source": "eastmoney",
      "published_at": "2026-05-26T09:30:00",
      "url": "https://example.com"
    }
  ],
  "hot_sectors": [
    {
      "name": "AI 应用",
      "heat_reason": "资金流入和新闻热度同步上升",
      "source": "market_intel",
      "published_at": "2026-05-26T10:00:00"
    }
  ],
  "company_hot_news": [],
  "market_hot_news": [],
  "source_status": [],
  "data_gaps": []
}
```

The preprocessor should prefer concise evidence over dumping long raw text. It
should preserve title, source, publish time, URL, provider, item type, and stale
state so AI can cite evidence.

## 9. AI Output Schema

The AI macro scorer must return strict JSON:

```json
{
  "macro_score": 72,
  "passed": true,
  "threshold": 60,
  "sub_scores": {
    "company_event_strength": 68,
    "sector_heat": 80,
    "news_validation": 75,
    "impact_direction": 70,
    "source_credibility": 65,
    "freshness": 78
  },
  "weighted_contribution": {
    "company_event_strength": 13.6,
    "sector_heat": 16,
    "news_validation": 15,
    "impact_direction": 14,
    "source_credibility": 6.5,
    "freshness": 7.8
  },
  "summary": "公司事件与当前热点方向存在较强共振，新闻验证充分，整体偏利好。",
  "risks": ["新闻热度可能衰减", "部分来源可信度一般"],
  "evidence_refs": [
    {
      "dimension": "sector_heat",
      "title": "AI 应用板块持续活跃",
      "source": "market_intel",
      "url": "https://example.com"
    }
  ]
}
```

Validation rules:

- `macro_score` must be clamped to `-100` to `100`.
- Every subscore must be clamped to `-100` to `100`.
- `passed` must be recomputed by code from `macro_score >= threshold`.
- Missing optional arrays should become empty arrays.
- Invalid JSON should make the rule return `error`, not a fabricated score.

## 10. Missing Data and Error Handling

When `market_intel` succeeds but a dimension lacks evidence:

- The AI can score that dimension near `0`.
- The dimension explanation must say the evidence is insufficient.
- The rule still returns a total macro score.

When stock and market intelligence are both empty:

- The rule returns `skip`.
- AI is not called.
- `details.data_gaps` explains which evidence is missing.

When provider refresh partially fails:

- The rule can still score from remaining evidence.
- `details.source_status` and `details.data_gaps` must include failures.

When AI call fails or output schema is invalid:

- The rule returns `error`.
- `details` includes preprocessed evidence, provider status, and the error.
- Frontend shows scoring failure and can offer retry.

When `macro_score < threshold`:

- The rule returns `fail`.
- Frontend still shows subscore details, summary, risks, and evidence refs.

## 11. Score Cache

To avoid repeated AI cost, add or reuse a cache keyed by evidence identity.

Recommended cache identity:

```text
market + code + trade_date + rule_key + rule_version + evidence_digest + model
```

Recommended stored payload:

- `market`
- `code`
- `trade_date`
- `rule_key`
- `rule_version`
- `evidence_digest`
- `model`
- `status`: `success`, `skip`, or `error`
- `score_json`
- `error_message`
- `created_at`
- `expires_at`

The first version can store this in a dedicated macro-score table or in an
existing rule-execution result store if that store already preserves detailed
JSON. The implementation plan should choose the least invasive storage path
after checking the current persistence layer.

## 12. Frontend Behavior

The stock screener result page should treat the aggregate macro rule as a
scoreable macro block.

For `pass`:

- Show macro score, threshold, summary, and positive dimensions.

For `fail`:

- Still show macro score, threshold, summary, weak dimensions, risks, and
  evidence refs.

For negative score:

- Highlight negative macro direction and show the strongest risk dimensions.

For `skip`:

- Show missing evidence and provider status.

For `error`:

- Show scoring failure, preprocessed evidence availability, and retry option if
  the backend supports retry.

The UI should not hide score details simply because the macro rule failed the
threshold.

## 13. Testing Plan

Backend tests:

- Evidence preprocessor groups stock and market items into six dimensions.
- Missing evidence returns `skip` without calling AI when both stock and market
  intelligence are empty.
- Partial evidence still calls AI and returns score details.
- AI JSON parser clamps scores and recomputes `passed`.
- Invalid AI JSON returns `error`.
- `macro_score < threshold` returns `fail` with full score details.
- Score cache avoids repeated AI calls for the same evidence digest.
- Existing `company_event_hot_sector_link` and
  `company_event_hot_news_link` tests still pass.

Frontend tests:

- Macro details render for `pass`.
- Macro details render for `fail`.
- Negative score renders risk-oriented details.
- `skip` and `error` states are distinguishable.

Integration tests:

- A rule chain containing technical strategies plus
  `market_intel_macro_score_link` produces technical score, macro score, and
  final weighted score.
- A stale or missing market-intel cache triggers refresh according to
  `refresh_policy = cache_or_refresh`.

## 14. Rollout Notes

Recommended rollout order:

1. Add data models and score contracts.
2. Add evidence preprocessor tests.
3. Add AI scorer interface and parser tests.
4. Add `MarketIntelMacroScoreStrategizer`.
5. Register `market_intel_macro_score_link` in rule metadata for A/HK/US.
6. Add final weighted score aggregation.
7. Add frontend display for macro score details.
8. Run backend, frontend, and browser verification.

The implementation should keep changes scoped. Existing boolean macro rules can
remain unchanged until the new aggregate rule has been verified in live flows.
