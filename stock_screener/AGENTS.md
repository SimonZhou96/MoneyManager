## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

### Chinese query mapping (must use)

When user asks in Chinese, do not query only with raw Chinese text.
Always map Chinese intent to English code terms, then run graphify query with English first.

Workflow:
1. Extract Chinese intent and key nouns.
2. Expand to 2-4 English query variants (symbol/file/domain wording).
3. Run `graphify query` for each variant, then merge results.
4. If still weak, run `graphify explain` on the best candidate symbol.
5. If relation question, run `graphify path "<A>" "<B>"`.

Common CN -> EN mapping for this repo:
- 规则链 -> rule chain, screening rule chain, expression_json, RuleExpressionEvaluator, `screening_rule_chains`
- 规则元数据 -> rule metadata, `screening_rule_metadata`, rule_key, params_json
- 前端页面 -> frontend page, React page, `web_frontend/src/main.tsx`, Screening
- 前端样式 -> frontend styles, `web_frontend/src/styles.css`
- 宏观分析 -> macro analysis, macro scoring, `market_intel`, MacroScoreResult, `macro_scoring.py`
- 信号分析 -> signal analysis, `signal_analysis`, SignalAnalysisChain
- 调度任务 -> scheduler, `scheduled_daily_job.py`, run_signal_analysis_for_market
- 数据库初始化 -> database init, `db.py`, init schema, SQL seed
- 默认SQL -> default sql, `sql/001_screening_rules.sql`, `sql/002_signal_analysis.sql`
- 筛选逻辑 -> screening logic, rule engine, `rule_engine.py`, evaluate
- 测试用例 -> tests, `tests/test_rule_engine.py`

Recommended query prompt templates:
- "Where is <EN_TERM> defined and wired?"
- "How does <EN_TERM> flow from scheduler to service to persistence?"
- "Which files implement <EN_TERM> and which tests cover it?"
- "What symbols are directly related to <EN_TERM>?"
