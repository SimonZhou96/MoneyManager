# 代码筛选工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old single-stock frontend entry with a unified “代码筛选” workbench that uses the existing custom-list task API for both one-code and multi-code screening.

**Architecture:** Keep backend execution unified through `POST /api/screening/custom-list-tasks`; do not introduce a new single-stock execution path. The React app owns input parsing, validation preview, task creation, polling custom-list results, and linking to existing task detail/artifact views.

**Tech Stack:** FastAPI backend already present, React 18 + TypeScript + Vite frontend, Python `unittest` for repository contract tests, existing CSS utility classes in `stock_screener/web_frontend/src/styles.css`.

---

## File Structure

- Modify `stock_screener/web_frontend/src/main.tsx`
  - Remove the old `SingleStock` page and `SingleRunDetail` frontend route.
  - Add a new `CodeScreening` page.
  - Submit code input to `/api/screening/custom-list-tasks`.
  - Poll `/api/screening/custom-list-tasks/{job_id}/results`.
  - Link completed jobs into existing `TaskDetail`.
- Modify `stock_screener/web_frontend/src/styles.css`
  - Add polished but restrained workbench styles for the code input textarea, preview metrics, and result preview.
  - Reuse existing `.panel`, `.form-grid`, `.toolbar`, `.table-wrap`, `.metric-grid`, and `.primary` patterns.
- Create `stock_screener/tests/test_code_screening_frontend.py`
  - Static frontend contract tests because the repo has no frontend test runner.
  - Verify old single-stock frontend API and label are removed.
  - Verify new page uses custom-list API and Chinese “代码筛选” copy.

No backend changes are required for the first implementation because `CustomListCodeParser`, `CustomListJobService`, and custom-list result APIs already exist.

## Task 1: Add Frontend Contract Tests

**Files:**
- Create: `stock_screener/tests/test_code_screening_frontend.py`

- [ ] **Step 1: Write the failing test**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN_TSX = ROOT / "web_frontend" / "src" / "main.tsx"


class CodeScreeningFrontendTest(unittest.TestCase):
    def read_main(self) -> str:
        return MAIN_TSX.read_text(encoding="utf-8")

    def test_navigation_replaces_single_stock_with_code_screening(self):
        source = self.read_main()

        self.assertIn("代码筛选", source)
        self.assertNotIn(">单股选股<", source)
        self.assertNotIn("page === 'single'", source)
        self.assertNotIn("setPage('single')", source)

    def test_code_screening_uses_custom_list_api_not_single_stock_api(self):
        source = self.read_main()

        self.assertIn("/api/screening/custom-list-tasks", source)
        self.assertIn("/api/screening/custom-list-tasks/${jobId}/results", source)
        self.assertNotIn("/api/screening/single-stock", source)

    def test_dashboard_does_not_link_to_single_stock_runs(self):
        source = self.read_main()

        self.assertNotIn("single_stock_runs", source)
        self.assertNotIn("SingleRunDetail", source)
        self.assertNotIn("openSingle", source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_code_screening_frontend -v
```

Expected: FAIL because `main.tsx` still contains `单股选股`, `SingleStock`, `SingleRunDetail`, and `/api/screening/single-stock`.

- [ ] **Step 3: Commit the failing test**

```bash
git add stock_screener/tests/test_code_screening_frontend.py
git commit -m "test: define code screening frontend contract"
```

## Task 2: Replace Navigation and Dashboard Single-Run Links

**Files:**
- Modify: `stock_screener/web_frontend/src/main.tsx`

- [ ] **Step 1: Remove single-run state from `App`**

Replace this state:

```tsx
const [selectedSingleRunId, setSelectedSingleRunId] = useState('')
```

with nothing. Keep `selectedTaskId`.

- [ ] **Step 2: Replace sidebar navigation item**

Replace:

```tsx
<button className={page === 'single' ? 'active' : ''} onClick={() => setPage('single')}>单股选股</button>
```

with:

```tsx
<button className={page === 'codeScreening' ? 'active' : ''} onClick={() => setPage('codeScreening')}>代码筛选</button>
```

- [ ] **Step 3: Replace page render targets**

Replace:

```tsx
{page === 'dashboard' && <Dashboard
  openTask={(taskId) => { setSelectedTaskId(taskId); setPage('task') }}
  openSingle={(runId) => { setSelectedSingleRunId(runId); setPage('singleRun') }}
/>}
{page === 'screening' && <Screening />}
{page === 'single' && <SingleStock />}
{page === 'options' && <OptionLab />}
{page === 'quant' && <QuantLab />}
{page === 'rules' && <Rules />}
{page === 'task' && <TaskDetail taskId={selectedTaskId} />}
{page === 'singleRun' && <SingleRunDetail runId={selectedSingleRunId} />}
```

with:

```tsx
{page === 'dashboard' && <Dashboard
  openTask={(taskId) => { setSelectedTaskId(taskId); setPage('task') }}
/>}
{page === 'screening' && <Screening />}
{page === 'codeScreening' && <CodeScreening openTask={(taskId) => { setSelectedTaskId(taskId); setPage('task') }} />}
{page === 'options' && <OptionLab />}
{page === 'quant' && <QuantLab />}
{page === 'rules' && <Rules />}
{page === 'task' && <TaskDetail taskId={selectedTaskId} />}
```

- [ ] **Step 4: Update `Dashboard` signature and data**

Replace:

```tsx
function Dashboard({ openTask, openSingle }: { openTask: (taskId: string) => void; openSingle: (runId: string) => void }) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [singleRuns, setSingleRuns] = useState<SingleStockRun[]>([])
  const [syncRuns, setSyncRuns] = useState<any[]>([])

  async function refresh() {
    const taskData = await api<{ jobs: Job[]; tasks: Task[]; single_stock_runs?: SingleStockRun[] }>('/api/screening/tasks')
    setJobs(taskData.jobs)
    setTasks(taskData.tasks)
    setSingleRuns(taskData.single_stock_runs || [])
```

with:

```tsx
function Dashboard({ openTask }: { openTask: (taskId: string) => void }) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [syncRuns, setSyncRuns] = useState<any[]>([])

  async function refresh() {
    const taskData = await api<{ jobs: Job[]; tasks: Task[] }>('/api/screening/tasks')
    setJobs(taskData.jobs)
    setTasks(taskData.tasks)
```

Then replace:

```tsx
<Metric label="筛选任务" value={tasks.length + singleRuns.length} />
```

with:

```tsx
<Metric label="筛选任务" value={tasks.length} />
```

Replace:

```tsx
<TaskTable rows={mergeRecentTasks(tasks, singleRuns, jobs)} openTask={openTask} openSingle={openSingle} />
```

with:

```tsx
<TaskTable rows={mergeRecentTasks(tasks, jobs)} openTask={openTask} />
```

- [ ] **Step 5: Update `mergeRecentTasks`**

Replace:

```tsx
function mergeRecentTasks(tasks: Task[], singleRuns: SingleStockRun[], jobs: Job[]): Task[] {
  const marketTasks = (tasks || []).map(item => ({ ...item, job_type: 'screening' }))
  const singleTasks = (singleRuns || []).map(item => ({
    ...item,
    task_id: item.run_id,
    job_type: 'single_stock',
    total_count: 1,
    completed_count: item.status === 'completed' || item.status === 'failed' ? 1 : 0,
    current_stock_code: item.normalized_code || item.code,
    current_stock_name: item.name,
    chain_key: item.chain_key,
    chain_name: item.chain_name
  }))
```

with:

```tsx
function mergeRecentTasks(tasks: Task[], jobs: Job[]): Task[] {
  const marketTasks = (tasks || []).map(item => ({ ...item, job_type: 'screening' }))
```

Then replace:

```tsx
return [...webJobs, ...marketTasks, ...singleTasks]
```

with:

```tsx
return [...webJobs, ...marketTasks]
```

- [ ] **Step 6: Update `TaskTable`**

Replace the signature:

```tsx
function TaskTable({ rows, openTask, openSingle }: { rows: Task[]; openTask: (taskId: string) => void; openSingle: (runId: string) => void }) {
```

with:

```tsx
function TaskTable({ rows, openTask }: { rows: Task[]; openTask: (taskId: string) => void }) {
```

Inside the row render, replace single-specific logic:

```tsx
const isSingle = row.job_type === 'single_stock'
const isWebJob = row.job_type === 'web_job'
const id = isSingle ? (row.run_id || row.task_id) : row.task_id
const stockText = isSingle
  ? formatSingleStockText(row)
  : (row.current_stock_code || '未补齐')
const progressText = isSingle
  ? (row.status === 'completed' ? (row.passed ? '通过' : '未通过') : statusLabel(row.status))
  : isWebJob
    ? (row.task_ids && row.task_ids.length > 0 ? `${row.task_ids.length} 个市场任务` : statusLabel(row.status))
  : `${row.completed_count}/${row.total_count}`
```

with:

```tsx
const isWebJob = row.job_type === 'web_job'
const id = row.task_id
const stockText = row.current_stock_code || '未补齐'
const progressText = isWebJob
  ? (row.task_ids && row.task_ids.length > 0 ? `${row.task_ids.length} 个市场任务` : statusLabel(row.status))
  : `${row.completed_count}/${row.total_count}`
```

Replace:

```tsx
<td>{isSingle ? '单股' : isWebJob ? '任务组' : '全市场'}</td>
```

with:

```tsx
<td>{isWebJob ? '任务组' : '筛选任务'}</td>
```

Replace:

```tsx
onClick={() => isSingle ? openSingle(id) : openTask(isWebJob ? firstTaskId : id)}
```

with:

```tsx
onClick={() => openTask(isWebJob ? firstTaskId : id)}
```

- [ ] **Step 7: Run the contract test**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_code_screening_frontend -v
```

Expected: still FAIL because `SingleStock`, `SingleRunDetail`, and the single-stock API are not removed yet.

- [ ] **Step 8: Commit navigation cleanup**

```bash
git add stock_screener/web_frontend/src/main.tsx
git commit -m "refactor: remove single stock frontend entry"
```

## Task 3: Add `CodeScreening` Page Backed by Custom List API

**Files:**
- Modify: `stock_screener/web_frontend/src/main.tsx`

- [ ] **Step 1: Add custom-list response types near existing frontend types**

Add after `RulesResponse`:

```tsx
type CustomListStatusRow = {
  index: number
  input?: string
  code?: string
  name?: string
  status?: string
  status_text?: string
  状态?: string
  reason?: string
  is_passed?: boolean
  filter_summary?: string
}

type CustomListTaskResponse = {
  job_id: string
  status: string
  runner?: string
  market: string
  timeframe: string
  chain_key?: string
  chain_timeframe?: string
  chain_name?: string
  task_id?: string
  input_summary?: Record<string, unknown>
  input_status_rows?: CustomListStatusRow[]
  rows?: CustomListStatusRow[]
}
```

- [ ] **Step 2: Add input parsing helper near `chainDisplay`**

```tsx
function parseCodeInput(value: string): string[] {
  return value
    .split(/[\s,，;；]+/)
    .map(item => item.trim())
    .filter(Boolean)
}
```

- [ ] **Step 3: Replace `SingleStock` component with `CodeScreening`**

Delete the old component that starts with `function SingleStock() {` and ends immediately before the next top-level component definition. Add:

```tsx
function CodeScreening({ openTask }: { openTask: (taskId: string) => void }) {
  const [market, setMarket] = useState('US')
  const [timeframe, setTimeframe] = useState('1d')
  const [rules, setRules] = useState<RulesResponse | null>(null)
  const [chainKey, setChainKey] = useState('')
  const [codeText, setCodeText] = useState('AAPL')
  const [enableAiAnalysis, setEnableAiAnalysis] = useState(true)
  const [sendFeishu, setSendFeishu] = useState(false)
  const [jobId, setJobId] = useState('')
  const [result, setResult] = useState<CustomListTaskResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const codes = parseCodeInput(codeText)
  const uniqueCount = new Set(codes.map(item => item.toUpperCase())).size
  const duplicateCount = Math.max(0, codes.length - uniqueCount)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setError('')
    setResult(null)
    setJobId('')
    if (codes.length === 0) {
      setError('至少输入一个有效股票代码')
      return
    }
    setLoading(true)
    try {
      const data = await api<CustomListTaskResponse>('/api/screening/custom-list-tasks', {
        method: 'POST',
        body: JSON.stringify({
          market,
          codes,
          timeframe,
          chain_key: chainKey || undefined,
          enable_ai_analysis: enableAiAnalysis,
          send_feishu: sendFeishu
        })
      })
      setResult(data)
      setJobId(data.job_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建代码筛选任务失败')
      setLoading(false)
    }
  }

  async function refreshJob(id: string) {
    try {
      const data = await api<CustomListTaskResponse>(`/api/screening/custom-list-tasks/${id}/results`)
      setResult(data)
      if (data.status === 'completed' || data.status === 'failed') {
        setLoading(false)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载代码筛选结果失败')
      setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    api<RulesResponse>(`/api/rules?market=${market}&timeframe=${timeframe}`)
      .then(data => {
        if (cancelled) return
        setRules(data)
        const activeKey = data.chain?.chain_key
        if (!chainKey || !(data.chains || []).some(item => item.chain_key === chainKey)) {
          setChainKey(activeKey || data.chains?.[0]?.chain_key || '')
        }
      })
      .catch(err => setError(err instanceof Error ? err.message : '加载规则链失败'))
    return () => { cancelled = true }
  }, [market, timeframe])

  useEffect(() => {
    if (!jobId) return
    refreshJob(jobId).catch(console.error)
    const timer = window.setInterval(() => refreshJob(jobId).catch(console.error), 5000)
    return () => window.clearInterval(timer)
  }, [jobId])

  return (
    <section>
      <Header title="代码筛选" subtitle="输入一个代码即单股筛选；输入多个代码即自选列表筛选，统一走自选列表任务流程。" />
      <form className="code-screening-layout" onSubmit={submit}>
        <Panel title="任务参数">
          <div className="form-grid compact-form-grid">
            <Field label="市场">
              <select value={market} onChange={event => setMarket(event.target.value)}>
                {MARKET_OPTIONS.map(item => <option key={item}>{item}</option>)}
              </select>
            </Field>
            <Field label="周期">
              <select value={timeframe} onChange={event => setTimeframe(event.target.value)}>
                {TIMEFRAME_OPTIONS.map(item => <option key={item}>{item}</option>)}
              </select>
            </Field>
            <Field label="规则链">
              <select value={chainKey} onChange={event => setChainKey(event.target.value)}>
                {(rules?.chains || []).map(item => (
                  <option key={`${item.chain_key}:${item.timeframe}`} value={item.chain_key}>
                    {item.chain_name} {item.enabled ? '默认候选' : '可试跑'}
                  </option>
                ))}
              </select>
            </Field>
            <label className="check"><input type="checkbox" checked={enableAiAnalysis} onChange={event => setEnableAiAnalysis(event.target.checked)} /> 联网 AI 分析</label>
            <label className="check"><input type="checkbox" checked={sendFeishu} onChange={event => setSendFeishu(event.target.checked)} /> 发送飞书</label>
          </div>
        </Panel>

        <Panel title="股票代码">
          <textarea
            className="code-input"
            rows={8}
            value={codeText}
            onChange={event => setCodeText(event.target.value)}
            placeholder={"AAPL\\nMSFT\\nTSLA"}
          />
          <div className="code-preview-grid">
            <Metric label="输入数量" value={codes.length} />
            <Metric label="去重后" value={uniqueCount} />
            <Metric label="本地重复" value={duplicateCount} />
            <Metric label="运行方式" value={codes.length <= 1 ? '单只代码' : '自选列表'} />
          </div>
          <div className="toolbar toolbar-row">
            <button className="primary" disabled={loading || codes.length === 0}>{loading ? '等待本地 Agent...' : '创建筛选任务'}</button>
          </div>
        </Panel>
      </form>

      {error && <div className="error">{error}</div>}
      {result && <CodeScreeningResult result={result} openTask={openTask} />}
    </section>
  )
}
```

- [ ] **Step 4: Add result component after `CodeScreening`**

```tsx
function CodeScreeningResult({ result, openTask }: { result: CustomListTaskResponse; openTask: (taskId: string) => void }) {
  const rows = result.rows || result.input_status_rows || []
  return (
    <Panel title="代码筛选结果">
      <div className="metric-grid">
        <Metric label="任务状态" value={<StatusBadge value={result.status} />} />
        <Metric label="市场" value={result.market} />
        <Metric label="周期" value={result.timeframe} />
        <Metric label="规则链" value={result.chain_name || result.chain_key || '默认链'} />
      </div>
      <div className="toolbar toolbar-row">
        {result.task_id ? (
          <button type="button" className="primary" onClick={() => openTask(result.task_id || '')}>查看任务详情</button>
        ) : (
          <span className="muted">任务已创建，等待本地 Agent 执行。</span>
        )}
      </div>
      <Table rows={rows} columns={['index', 'input', 'code', '状态', 'name', 'filter_summary', 'reason']} />
    </Panel>
  )
}
```

- [ ] **Step 5: Run tests and build**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_code_screening_frontend -v
npm run build
```

Expected: contract test may still fail if old components remain; build should pass only after type errors are resolved.

- [ ] **Step 6: Commit page implementation**

```bash
git add stock_screener/web_frontend/src/main.tsx
git commit -m "feat: add code screening workbench"
```

## Task 4: Remove Old Single-Stock Components and Types

**Files:**
- Modify: `stock_screener/web_frontend/src/main.tsx`

- [ ] **Step 1: Delete unused `SingleStockRun` type**

Delete the entire `type SingleStockRun` block near the top of the file. The block starts with `type SingleStockRun = {` and ends at the matching closing brace before the next type definition.

- [ ] **Step 2: Delete old `SingleResult` component**

Delete the full function whose signature is:

```tsx
function SingleResult({ result }: { result: any })
```

Do not keep any `/api/screening/single-stock` fetch in the frontend.

- [ ] **Step 3: Delete old `SingleRunDetail` component**

Delete the full function whose signature is:

```tsx
function SingleRunDetail({ runId }: { runId: string })
```

Do not keep any `singleRun` page state or route.

- [ ] **Step 4: Delete unused single-stock helper usage**

Search:

```bash
rg -n "SingleStock|SingleResult|SingleRunDetail|single-stock|singleRun|single_stock_runs|openSingle|单股选股" stock_screener/web_frontend/src/main.tsx
```

Expected: no matches.

- [ ] **Step 5: Run contract test**

Run:

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_code_screening_frontend -v
```

Expected: PASS.

- [ ] **Step 6: Commit removal**

```bash
git add stock_screener/web_frontend/src/main.tsx stock_screener/tests/test_code_screening_frontend.py
git commit -m "refactor: remove old single stock frontend flow"
```

## Task 5: Polish Code Screening Styling

**Files:**
- Modify: `stock_screener/web_frontend/src/styles.css`

- [ ] **Step 1: Add workbench styles**

Append near existing form/table styles:

```css
.code-screening-layout {
  display: grid;
  gap: 18px;
}

.compact-form-grid {
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border: 0;
  padding: 0;
}

textarea.code-input {
  width: 100%;
  min-height: 180px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 12px;
  resize: vertical;
  background: white;
  color: #172033;
  line-height: 1.5;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
}

.code-preview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 14px 0;
}
```

- [ ] **Step 2: Extend mobile responsiveness**

Update the existing media query so `.compact-form-grid` and `.code-preview-grid` collapse:

```css
@media (max-width: 900px) {
  .app-shell { grid-template-columns: 1fr; }
  .sidebar { position: static; }
  .metric-grid, .form-grid, .result-layout, .option-form, .option-fill-form, .option-layout, .option-summary-grid, .quant-grid, .metric-row, .rule-list-toolbar, .compact-form-grid, .code-preview-grid { grid-template-columns: 1fr; }
  .option-detail-heading { display: grid; }
}
```

- [ ] **Step 3: Build frontend**

Run:

```bash
npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 4: Commit styling**

```bash
git add stock_screener/web_frontend/src/styles.css
git commit -m "style: polish code screening workbench"
```

## Task 6: Full Verification

**Files:**
- Verify only; no intended file edits.

- [ ] **Step 1: Run frontend contract test**

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_code_screening_frontend -v
```

Expected: all tests pass.

- [ ] **Step 2: Run custom-list backend tests**

```bash
PYTHONPATH=stock_screener python3 -m unittest stock_screener.tests.test_custom_list -v
```

Expected: all tests pass.

- [ ] **Step 3: Run related web platform tests**

Use explicit `unittest.mock` import for Python 3.14 compatibility:

```bash
PYTHONPATH=stock_screener python3 - <<'PY'
import unittest
import unittest.mock
suite = unittest.defaultTestLoader.loadTestsFromName('stock_screener.tests.test_web_platform')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
```

Expected: all tests pass.

- [ ] **Step 4: Run frontend build**

```bash
npm run build
```

Expected: build passes.

- [ ] **Step 5: Manual browser check**

Start the frontend dev server if needed:

```bash
npm run dev
```

Manual checks:

- Sidebar shows `代码筛选`, not `单股选股`.
- Code screening page uses the same visual language as the rest of the app.
- One-code input creates a custom-list task request with `codes.length === 1`.
- Multi-code input creates a custom-list task request with all parsed codes.
- Completed custom-list task shows result rows and links to existing task detail.
- No horizontal overflow at mobile width.

- [ ] **Step 6: Final commit if verification required fixes**

Only if verification required additional changes:

```bash
git add stock_screener/web_frontend/src/main.tsx stock_screener/web_frontend/src/styles.css stock_screener/tests/test_code_screening_frontend.py
git commit -m "fix: verify code screening workbench"
```

## Self-Review

- Spec coverage: The plan deletes the old single-stock frontend entry, adds the unified code screening workbench, uses only custom-list APIs, keeps old backend single-stock APIs untouched, and routes results to existing task detail/artifact surfaces.
- Placeholder scan: No unresolved planning placeholders are present.
- Type consistency: `CustomListTaskResponse`, `CustomListStatusRow`, `CodeScreening`, and `CodeScreeningResult` names are defined before use and reused consistently.
