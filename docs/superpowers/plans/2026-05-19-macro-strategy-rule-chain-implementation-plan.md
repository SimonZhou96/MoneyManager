# Macro Strategy Rule Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two macro strategies as first-class atomic `strategy` rules, introduce shared day-level `signal_analysis` caching for stock and option flows, and make the rule-chain page support create, edit, and delete.

**Architecture:** Keep one `expression_json` per rule chain and let the rule engine inspect referenced rules to decide whether it must prepare only technical inputs or both technical and macro inputs. Macro strategies reuse the existing `signal_analysis` pipeline through a shared cache keyed by `market + code + timeframe + analysis_profile + trade_date`, so the same symbol/day/timeframe is analyzed once and then reused by stock screening and option macro analysis.

**Tech Stack:** Python, FastAPI, PyMySQL/MySQL JSON columns, React + TypeScript, existing `signal_analysis` pipeline, existing stock screener rule engine.

---

### Task 1: Extend rule metadata and rule-chain persistence

**Files:**
- Modify: `stock_screener/db.py`
- Modify: `stock_screener/sql/001_screening_rules.sql`
- Modify: `stock_screener/rule_engine.py`
- Test: `stock_screener/tests/test_rule_engine.py`

- [ ] **Step 1: Write the failing database/metadata tests**

```python
def test_rule_metadata_supports_strategy_category():
    item = RuleMetadata.from_row({
        "market": "HK",
        "rule_key": "company_event_hot_news_link",
        "rule_name": "公司时事与热点新闻关联",
        "rule_type": "strategy",
        "strategy_category": "macro",
        "implementation": "CompanyEventHotNewsStrategizer",
        "params_json": {},
        "enabled": True,
        "display_order": 220,
        "description": "macro strategy",
    })
    assert item.rule_type == "strategy"
    assert item.strategy_category == "macro"


def test_rule_chain_sql_seeds_macro_strategy_rows():
    sql_path = Path(__file__).resolve().parents[1] / "sql" / "001_screening_rules.sql"
    content = sql_path.read_text(encoding="utf-8")
    assert "strategy_category VARCHAR(16)" in content
    assert "company_event_hot_sector_link" in content
    assert "company_event_hot_news_link" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest stock_screener/tests/test_rule_engine.py -k "strategy_category or macro_strategy_rows" -v
```

Expected: FAIL because `RuleMetadata` has no `strategy_category` field and deployment SQL does not seed the new macro strategies.

- [ ] **Step 3: Add minimal persistence support**

```python
@dataclass(frozen=True)
class RuleMetadata:
    market: str
    rule_key: str
    rule_name: str
    rule_type: str
    implementation: str
    strategy_category: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    display_order: int = 0
    description: str = ""
```

```sql
ALTER TABLE screening_rule_metadata
ADD COLUMN strategy_category VARCHAR(16) NULL COMMENT 'technical/macro';

INSERT IGNORE INTO screening_rule_metadata
    (market, rule_key, rule_name, rule_type, strategy_category, implementation, params_json, enabled, display_order, description)
VALUES
    ('HK', 'company_event_hot_sector_link', '公司时事与热点板块关联', 'strategy', 'macro', 'CompanyEventHotSectorStrategizer', '{}', 1, 210, '复用 AI 分析结果，判断公司时事与热点板块是否共振'),
    ('HK', 'company_event_hot_news_link', '公司时事与热点新闻关联', 'strategy', 'macro', 'CompanyEventHotNewsStrategizer', '{}', 1, 220, '复用 AI 分析结果，判断公司时事是否被热点新闻验证');
```

- [ ] **Step 4: Add rule-chain CRUD persistence methods**

```python
def create_screening_rule_chain(self, item: dict) -> None:
    ...

def update_screening_rule_chain(self, market: str, timeframe: str, chain_key: str, item: dict) -> bool:
    ...

def delete_screening_rule_chain(self, market: str, timeframe: str, chain_key: str) -> bool:
    ...
```

```python
def get_active_screening_rule_chain(self, market: str, timeframe: str = "*") -> Optional[dict]:
    # keep one expression_json model; no second expression field
    ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest stock_screener/tests/test_rule_engine.py -v
```

Expected: PASS, including updated SQL assertions and metadata parsing.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/db.py stock_screener/sql/001_screening_rules.sql stock_screener/rule_engine.py stock_screener/tests/test_rule_engine.py
git commit -m "feat: add macro strategy metadata and rule chain persistence"
```

### Task 2: Add shared signal-analysis cache and macro strategy evaluators

**Files:**
- Create: `stock_screener/macro_strategies.py`
- Modify: `stock_screener/db.py`
- Modify: `stock_screener/signal_analysis/models.py`
- Modify: `stock_screener/signal_analysis/service.py`
- Modify: `stock_screener/option_lab/macro_analysis.py`
- Test: `stock_screener/tests/test_signal_analysis.py`
- Test: `stock_screener/tests/test_option_lab_macro_analysis.py`

- [ ] **Step 1: Write the failing cache and macro strategy tests**

```python
def test_company_event_hot_sector_strategy_passes_on_hot_sector_match():
    analysis = SignalAnalysisResult(
        code="HK.01810",
        company_events=["新品发布"],
        hot_sectors=["机器人"],
        matched_hot_sectors=["机器人"],
        hot_sector_mark="重点",
        hot_sector_reason="公司业务与热点板块直接匹配",
    )
    output = CompanyEventHotSectorStrategizer().apply_analysis(analysis)
    assert output.result.value == "pass"


def test_signal_analysis_cache_hit_skips_llm_call():
    repo = FakeRepository.with_cached_signal_analysis("HK", "HK.01810", "1d", "default", "2026-05-19")
    provider = ExplodingLLMProvider()
    result = run_signal_analysis_for_market(
        mysql_config=fake_mysql_config,
        task_id="task-1",
        market="HK",
        csv_path=csv_path,
        check_date=date(2026, 5, 19),
        timeframe="1d",
        enabled=True,
        repository=repo,
        llm_provider_override=provider,
    )
    assert result.results_by_code["HK.01810"].summary == "cached"
    assert provider.calls == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest stock_screener/tests/test_signal_analysis.py -k "cache_hit or hot_sector_strategy" -v
pytest stock_screener/tests/test_option_lab_macro_analysis.py -k "cache" -v
```

Expected: FAIL because there is no shared signal-analysis cache API and no macro strategy implementations.

- [ ] **Step 3: Add the shared cache table and repository methods**

```python
def upsert_signal_analysis_cache(self, item: dict) -> None:
    ...

def get_signal_analysis_cache(
    self,
    market: str,
    code: str,
    timeframe: str,
    analysis_profile: str,
    trade_date: date,
) -> Optional[dict]:
    ...
```

```sql
CREATE TABLE IF NOT EXISTS signal_analysis_cache (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    cache_key VARCHAR(200) NOT NULL,
    market VARCHAR(8) NOT NULL,
    code VARCHAR(32) NOT NULL,
    timeframe VARCHAR(16) NOT NULL,
    analysis_profile VARCHAR(32) NOT NULL DEFAULT 'default',
    trade_date DATE NOT NULL,
    analysis_status VARCHAR(32) NOT NULL DEFAULT 'success',
    company_events JSON NULL,
    company_hot_news JSON NULL,
    market_hot_news JSON NULL,
    news_impact VARCHAR(64) NULL,
    hot_sectors JSON NULL,
    matched_hot_sectors JSON NULL,
    hot_sector_mark VARCHAR(32) NULL,
    hot_sector_reason TEXT NULL,
    source_urls JSON NULL,
    evidence_links JSON NULL,
    factor_citations JSON NULL,
    data_gaps JSON NULL,
    raw_response JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uk_signal_analysis_cache_key (cache_key)
);
```

- [ ] **Step 4: Add macro strategy implementations**

```python
class CompanyEventHotSectorStrategizer:
    def apply_analysis(self, analysis: SignalAnalysisResult) -> FilterOutput:
        if not analysis.company_events:
            return FilterOutput(..., result=FilterResult.SKIP, reason="缺少公司时事，无法判断与热点板块的关联")
        if not analysis.hot_sectors:
            return FilterOutput(..., result=FilterResult.SKIP, reason="缺少热点板块数据，无法判断是否形成板块共振")
        if analysis.hot_sector_mark in {"重点", "相关"}:
            return FilterOutput(..., result=FilterResult.PASS, ...)
        return FilterOutput(..., result=FilterResult.FAIL, ...)
```

```python
class CompanyEventHotNewsStrategizer:
    def apply_analysis(self, analysis: SignalAnalysisResult) -> FilterOutput:
        company_items = [*analysis.company_events, *analysis.company_hot_news]
        if not company_items:
            return FilterOutput(..., result=FilterResult.SKIP, reason="缺少公司时事，无法判断与热点新闻的关联")
        if not analysis.company_hot_news and not analysis.market_hot_news:
            return FilterOutput(..., result=FilterResult.SKIP, reason="缺少热点新闻数据，无法判断新闻是否验证公司时事")
        if analysis.news_impact in {"利好", "利空", "偏利好", "偏利空"}:
            return FilterOutput(..., result=FilterResult.PASS, ...)
        return FilterOutput(..., result=FilterResult.FAIL, ...)
```

- [ ] **Step 5: Wire `signal_analysis` and option macro analysis to the shared cache**

```python
cache_key = cache_key_for_signal_analysis(market, code, timeframe, analysis_profile, check_date)
cached = repository.get_signal_analysis_cache(...)
if cached is not None and not force_refresh:
    context.results_by_code[code] = SignalAnalysisResult.from_llm_item(cached, model=str(cached.get("model") or "cache"))
    return AnalysisRunResult(...)
```

```python
shared = db.get_signal_analysis_cache(market=market, code=code, timeframe="1d", analysis_profile="default", trade_date=date.today())
if shared:
    result = SignalAnalysisResult.from_llm_item(shared, model=str(shared.get("model") or "cache"))
    return macro_analysis_from_signal_result(...)
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
pytest stock_screener/tests/test_signal_analysis.py -v
pytest stock_screener/tests/test_option_lab_macro_analysis.py -v
```

Expected: PASS, with explicit assertions that cache hits avoid provider calls.

- [ ] **Step 7: Commit**

```bash
git add stock_screener/macro_strategies.py stock_screener/db.py stock_screener/signal_analysis/models.py stock_screener/signal_analysis/service.py stock_screener/option_lab/macro_analysis.py stock_screener/tests/test_signal_analysis.py stock_screener/tests/test_option_lab_macro_analysis.py
git commit -m "feat: add shared signal analysis cache and macro strategies"
```

### Task 3: Upgrade the rule engine to execute mixed technical and macro strategy chains

**Files:**
- Modify: `stock_screener/rule_engine.py`
- Modify: `stock_screener/filters.py`
- Modify: `stock_screener/web/single_stock.py`
- Modify: `stock_screener/scheduled_daily_job.py`
- Modify: `stock_screener/local_agent.py`
- Test: `stock_screener/tests/test_rule_engine.py`
- Test: `stock_screener/tests/test_web_platform.py`

- [ ] **Step 1: Write the failing mixed-chain tests**

```python
def test_macro_only_chain_can_pass_without_technical_strategy():
    metadata_items = [
        metadata("company_event_hot_news_link", "strategy", "CompanyEventHotNewsStrategizer", params={"strategy_category": "macro"}),
    ]
    engine = RuleEngine(metadata_items, chain({"ref": "company_event_hot_news_link"}), registry=registry_with_macro())
    result = engine.evaluate_analysis(signal_analysis_result_with_news())
    assert result.passed is True


def test_rule_engine_detects_when_macro_analysis_is_required():
    engine = RuleEngine(
        [metadata("company_event_hot_sector_link", "strategy", "CompanyEventHotSectorStrategizer", params={"strategy_category": "macro"})],
        chain({"ref": "company_event_hot_sector_link"}),
        registry=registry_with_macro(),
    )
    assert engine.requires_macro_analysis() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest stock_screener/tests/test_rule_engine.py -k "macro_only_chain or requires_macro_analysis" -v
```

Expected: FAIL because the rule engine only knows technical `strategy` execution today.

- [ ] **Step 3: Extend the engine with strategy categories and mixed execution**

```python
@dataclass(frozen=True)
class RuleMetadata:
    ...
    strategy_category: str = ""
```

```python
def requires_macro_analysis(self) -> bool:
    for rule_key in self.referenced_rule_keys:
        metadata = self.metadata_by_key.get(rule_key)
        if metadata and metadata.rule_type == RULE_TYPE_STRATEGY and metadata.strategy_category == "macro":
            return True
    return False
```

```python
def evaluate_analysis(self, analysis: SignalAnalysisResult) -> StockFilterResult:
    execution = MacroRuleExecutionContext(...)
    passed = self.evaluator.evaluate(self.chain_config.expression, execution)
    return StockFilterResult(
        stock=StockInfo(market=analysis.raw_response.get("market", ""), code=analysis.code, name=analysis.name),
        passed=passed,
        filter_outputs=execution.ordered_outputs,
    )
```

- [ ] **Step 4: Integrate the mixed execution path into single-stock and scheduled screening**

```python
result = rule_engine.evaluate_stock(stock, context)
if rule_engine.requires_macro_analysis():
    analysis = load_or_run_signal_analysis(...)
    macro_result = rule_engine.evaluate_analysis(analysis)
    result.filter_outputs.extend(macro_result.filter_outputs)
    result.passed = bool(result.passed and macro_result.passed)
```

```python
def _rule_details(rule_engine, outputs) -> List[dict]:
    return [{
        "rule_key": metadata.rule_key,
        "rule_name": metadata.rule_name,
        "rule_type": metadata.rule_type,
        "strategy_category": metadata.strategy_category,
        "result": output.result.value,
        ...
    }]
```

- [ ] **Step 5: Run the updated tests**

Run:

```bash
pytest stock_screener/tests/test_rule_engine.py -v
pytest stock_screener/tests/test_web_platform.py -v
```

Expected: PASS, including macro-only chains and mixed evaluation behavior.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/rule_engine.py stock_screener/filters.py stock_screener/web/single_stock.py stock_screener/scheduled_daily_job.py stock_screener/local_agent.py stock_screener/tests/test_rule_engine.py stock_screener/tests/test_web_platform.py
git commit -m "feat: execute mixed technical and macro strategy chains"
```

### Task 4: Add rule-chain CRUD APIs and validation

**Files:**
- Modify: `stock_screener/web/main.py`
- Modify: `stock_screener/web/rule_chains.py`
- Test: `stock_screener/tests/test_web_platform.py`

- [ ] **Step 1: Write the failing API/validation tests**

```python
def test_create_rule_chain_rejects_unknown_rule_key(client):
    payload = {
        "market": "HK",
        "timeframe": "1d",
        "chain_key": "macro_only",
        "chain_name": "宏观策略链",
        "expression_json": {"ref": "missing_rule"},
        "enabled": False,
        "priority": 300,
    }
    response = client.post("/api/rules/chains", json=payload)
    assert response.status_code == 400
    assert response.json()["message"] == "规则链引用了不存在或未启用的原子规则"
```

```python
def test_delete_active_rule_chain_is_blocked():
    with pytest.raises(BusinessError, match="默认生效链不允许直接删除"):
        delete_rule_chain(...)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest stock_screener/tests/test_web_platform.py -k "rule_chain" -v
```

Expected: FAIL because CRUD APIs and validators do not exist.

- [ ] **Step 3: Add validation helpers**

```python
def validate_rule_chain_expression(metadata_items: List[RuleMetadata], expression: dict) -> None:
    referenced = RuleExpressionEvaluator({item.rule_key: item for item in metadata_items}).collect_rule_keys(expression)
    missing = [key for key in referenced if key not in {item.rule_key for item in metadata_items if item.enabled}]
    if missing:
        raise BusinessError("INVALID_RULE_CHAIN", "规则链引用了不存在或未启用的原子规则")
```

```python
def validate_chain_key(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9_]+", value):
        raise BusinessError("INVALID_RULE_CHAIN", "规则链 Key 只允许小写字母、数字和下划线")
    return value
```

- [ ] **Step 4: Add CRUD endpoints**

```python
@app.post("/api/rules/chains")
def create_rule_chain(payload: RuleChainUpsertRequest, _: CurrentUser = Depends(require_user), db: MarketDatabase = Depends(get_db)):
    ...

@app.put("/api/rules/chains/{market}/{timeframe}/{chain_key}")
def update_rule_chain(...):
    ...

@app.delete("/api/rules/chains/{market}/{timeframe}/{chain_key}")
def delete_rule_chain(...):
    ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest stock_screener/tests/test_web_platform.py -v
```

Expected: PASS with create, update, delete, and delete-protection coverage.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/web/main.py stock_screener/web/rule_chains.py stock_screener/tests/test_web_platform.py
git commit -m "feat: add rule chain CRUD api and validation"
```

### Task 5: Build the rule-chain editor UI

**Files:**
- Modify: `stock_screener/web_frontend/src/main.tsx`
- Modify: `stock_screener/web_frontend/src/api.ts`
- Modify: `stock_screener/web_frontend/src/styles.css`
- Test: `stock_screener/web_frontend/package.json`

- [ ] **Step 1: Add failing UI behavior checks through a build-first gate**

```tsx
type RuleMetadataRow = {
  market: string
  rule_key: string
  rule_name: string
  rule_type: string
  strategy_category?: string
  implementation: string
  enabled: boolean
  display_order: number
}
```

```tsx
type RuleChainForm = {
  market: string
  timeframe: string
  chain_key: string
  chain_name: string
  expression_json: Record<string, unknown>
  enabled: boolean
  priority: number
  description: string
}
```

- [ ] **Step 2: Run the frontend build to establish the current baseline**

Run:

```bash
npm --prefix stock_screener/web_frontend run build
```

Expected: PASS before UI changes; after adding new types and state hooks, transient type errors are acceptable until the implementation is complete.

- [ ] **Step 3: Implement the editor UI**

```tsx
function Rules() {
  const [editing, setEditing] = useState<RuleChain | null>(null)
  const [draft, setDraft] = useState<RuleChainForm>(emptyDraft("HK"))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  async function saveChain(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    const method = editing ? "PUT" : "POST"
    const path = editing
      ? `/api/rules/chains/${editing.market}/${editing.timeframe}/${editing.chain_key}`
      : "/api/rules/chains"
    await api(path, { method, body: JSON.stringify(draft) })
    await refresh()
    setEditing(null)
    setDraft(emptyDraft(market))
    setSaving(false)
  }
}
```

```tsx
<Panel title="规则链编辑器">
  <form onSubmit={saveChain} className="rule-chain-form">
    <Field label="规则链 Key"><input value={draft.chain_key} ... /></Field>
    <Field label="规则链名称"><input value={draft.chain_name} ... /></Field>
    <Field label="表达式 JSON"><textarea value={JSON.stringify(draft.expression_json, null, 2)} ... /></Field>
    <button className="primary" disabled={saving}>{editing ? "保存修改" : "新增规则链"}</button>
  </form>
</Panel>
```

- [ ] **Step 4: Add list actions**

```tsx
<Table
  rows={(rules?.chains || []).map(item => ({
    ...item,
    actions: (
      <>
        <button onClick={() => beginEdit(item)}>编辑</button>
        <button onClick={() => removeChain(item)}>删除</button>
      </>
    ),
  }))}
  columns={["chain_name", "chain_key", "enabled", "priority", "description", "actions"]} 
/>
```

- [ ] **Step 5: Run the frontend build**

Run:

```bash
npm --prefix stock_screener/web_frontend run build
```

Expected: PASS, with the rule-chain page compiling cleanly and the new form state typed.

- [ ] **Step 6: Commit**

```bash
git add stock_screener/web_frontend/src/main.tsx stock_screener/web_frontend/src/api.ts stock_screener/web_frontend/src/styles.css
git commit -m "feat: add rule chain editor ui"
```

### Task 6: Final integration verification

**Files:**
- Modify: `docs/superpowers/specs/2026-05-19-macro-strategy-rule-chain-design.md`
- Test: `stock_screener/tests/test_rule_engine.py`
- Test: `stock_screener/tests/test_signal_analysis.py`
- Test: `stock_screener/tests/test_option_lab_macro_analysis.py`
- Test: `stock_screener/tests/test_web_platform.py`

- [ ] **Step 1: Re-run the targeted Python test suite**

Run:

```bash
pytest \
  stock_screener/tests/test_rule_engine.py \
  stock_screener/tests/test_signal_analysis.py \
  stock_screener/tests/test_option_lab_macro_analysis.py \
  stock_screener/tests/test_web_platform.py -v
```

Expected: PASS.

- [ ] **Step 2: Re-run the frontend build**

Run:

```bash
npm --prefix stock_screener/web_frontend run build
```

Expected: PASS.

- [ ] **Step 3: Update the spec if implementation drifted**

```md
## Implementation Notes

- The cache key is `market + code + timeframe + analysis_profile + trade_date`.
- Macro strategies remain `rule_type='strategy'` and are distinguished by `strategy_category='macro'`.
- Rule chains keep a single `expression_json`; execution is phase-aware internally.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-05-19-macro-strategy-rule-chain-design.md
git commit -m "docs: sync macro strategy rule chain spec with implementation"
```
