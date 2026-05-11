import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

type User = { id: number; username: string; role: string }
type Task = {
  task_id: string
  market: string
  timeframe: string
  status: string
  total_count: number
  completed_count: number
  current_stock_code?: string
  current_stock_name?: string
  created_at?: string
  job_type?: string
  run_id?: string
  job_id?: string
  task_ids?: string[]
  markets?: string[]
  normalized_code?: string
  code?: string
  name?: string
  passed?: boolean
}
type SingleStockRun = {
  job_type: 'single_stock'
  run_id: string
  market: string
  code: string
  normalized_code: string
  timeframe: string
  status: string
  passed: boolean
  name?: string
  created_at?: string
  error_message?: string
}
type ScreeningResult = {
  market: string
  code: string
  name?: string
  is_passed: boolean
  sector?: string
  industry?: string
  filter_summary?: string
  close_price?: number
}
type TaskResultsResponse = {
  rows: ScreeningResult[]
  total_count: number
  passed_count: number
  uploaded_result_scope?: string
  limit: number
  offset: number
}
type Artifact = {
  artifact_id: string
  artifact_type: string
  file_name: string
  file_size?: number
}
type Job = {
  job_id: string
  markets: string[]
  timeframe: string
  status: string
  task_ids: string[]
  created_at?: string
  error_message?: string
  execution_mode?: string
  summary?: Record<string, unknown>
}

const MARKET_OPTIONS = ['HK', 'US', 'A']
const TIMEFRAME_OPTIONS = ['1d', '1wk', '1mo', '3mo', '1m', '3m', '5m', '15m', '30m', '60m']

const COLUMN_LABELS: Record<string, string> = {
  artifact_type: '文件类型',
  close_price: '最新收盘价',
  code: '股票代码',
  created_at: '创建时间',
  current_stock_code: '当前股票',
  current_stock_name: '当前股票名',
  display_order: '排序',
  enabled: '启用状态',
  error_message: '错误信息',
  file_name: '文件名',
  file_size: '文件大小',
  filter_summary: '筛选摘要',
  implementation: '实现类',
  industry: '行业',
  is_passed: '通过状态',
  job_id: '任务组 ID',
  market: '市场',
  markets: '市场',
  name: '名称',
  reason: '原因',
  result: '结果',
  rule_key: '规则 Key',
  rule_name: '规则名',
  rule_type: '规则类型',
  sector: '所属板块',
  status: '状态',
  task_id: '任务 ID',
  timeframe: '周期'
}

const STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  error: '错误',
  fail: '失败',
  failed: '失败',
  false: '未通过',
  expired: '已过期',
  pass: '通过',
  queued: '排队中',
  running: '运行中',
  skip: '跳过',
  true: '通过'
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  })
  const payload = await response.json().catch(() => null)
  if (payload?.ok === false) {
    throw new Error(payload.message || '请求失败，请稍后重试')
  }
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || `${response.status} ${response.statusText}`)
  }
  return payload as T
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      const result = await api<{ user: User }>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password })
      })
      onLogin(result.user)
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="login-shell">
      <form className="login-panel" onSubmit={submit}>
        <div>
          <p className="eyebrow">MoneyManager</p>
          <h1>选股器登录</h1>
          <p className="muted">仅固定账号可访问，未登录无法查看任何筛选页面。</p>
        </div>
        <label>
          用户名
          <input value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" />
        </label>
        <label>
          密码
          <input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="primary" disabled={loading}>{loading ? '登录中...' : '登录'}</button>
      </form>
    </main>
  )
}

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState('dashboard')
  const [selectedTaskId, setSelectedTaskId] = useState('')
  const [selectedSingleRunId, setSelectedSingleRunId] = useState('')

  useEffect(() => {
    api<User>('/api/auth/me')
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  async function logout() {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => null)
    setUser(null)
  }

  if (loading) return <div className="loading">加载中...</div>
  if (!user) return <Login onLogin={setUser} />

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <strong>MoneyManager</strong>
          <span>Stock Screener</span>
        </div>
        <nav>
          <button className={page === 'dashboard' ? 'active' : ''} onClick={() => setPage('dashboard')}>总览</button>
          <button className={page === 'screening' ? 'active' : ''} onClick={() => setPage('screening')}>全市场筛选</button>
          <button className={page === 'single' ? 'active' : ''} onClick={() => setPage('single')}>单股选股</button>
          <button className={page === 'rules' ? 'active' : ''} onClick={() => setPage('rules')}>规则链</button>
        </nav>
        <div className="sidebar-footer">
          <span>{user.username}</span>
          <button onClick={logout}>退出</button>
        </div>
      </aside>
      <main className="content">
        {page === 'dashboard' && <Dashboard
          openTask={(taskId) => { setSelectedTaskId(taskId); setPage('task') }}
          openSingle={(runId) => { setSelectedSingleRunId(runId); setPage('singleRun') }}
        />}
        {page === 'screening' && <Screening />}
        {page === 'single' && <SingleStock />}
        {page === 'rules' && <Rules />}
        {page === 'task' && <TaskDetail taskId={selectedTaskId} />}
        {page === 'singleRun' && <SingleRunDetail runId={selectedSingleRunId} />}
      </main>
    </div>
  )
}

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
    const fresh = await api<{ sync_runs: any[] }>('/api/system/data-freshness')
    setSyncRuns(fresh.sync_runs)
  }

  useEffect(() => {
    refresh().catch(console.error)
    const timer = window.setInterval(() => refresh().catch(console.error), 10000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <section>
      <Header title="总览" subtitle="任务进度、数据同步和最近筛选结果" />
      <div className="metric-grid">
        <Metric label="Web 任务" value={jobs.length} />
        <Metric label="筛选任务" value={tasks.length + singleRuns.length} />
        <Metric label="最近同步" value={statusLabel(syncRuns[0]?.status || '无')} />
      </div>
      <Panel title="最近 Web 任务">
        <Table rows={jobs} columns={['job_id', 'markets', 'timeframe', 'status', 'created_at', 'error_message']} />
      </Panel>
      <Panel title="最近筛选任务">
        <TaskTable rows={mergeRecentTasks(tasks, singleRuns, jobs)} openTask={openTask} openSingle={openSingle} />
      </Panel>
    </section>
  )
}

function mergeRecentTasks(tasks: Task[], singleRuns: SingleStockRun[], jobs: Job[]): Task[] {
  const marketTasks = (tasks || []).map(item => ({ ...item, job_type: 'screening' }))
  const singleTasks = (singleRuns || []).map(item => ({
    ...item,
    task_id: item.run_id,
    job_type: 'single_stock',
    total_count: 1,
    completed_count: item.status === 'completed' || item.status === 'failed' ? 1 : 0,
    current_stock_code: item.normalized_code || item.code,
    current_stock_name: item.name
  }))
  const webJobs = (jobs || []).map(item => ({
    ...item,
    task_id: item.job_id,
    job_type: 'web_job',
    market: (item.markets || []).join(', '),
    total_count: item.task_ids?.length || 0,
    completed_count: item.status === 'completed' ? (item.task_ids?.length || 0) : 0,
    current_stock_code: item.task_ids && item.task_ids.length > 0 ? item.task_ids[0] : '等待本地 Agent'
  }))
  return [...webJobs, ...marketTasks, ...singleTasks]
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
    .slice(0, 50)
}

function Screening() {
  const [markets, setMarkets] = useState(['HK', 'US', 'A'])
  const [timeframe, setTimeframe] = useState('1d')
  const [enableAi, setEnableAi] = useState(true)
  const [sendFeishu, setSendFeishu] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  function toggleMarket(market: string) {
    setError('')
    setMarkets(current => current.includes(market) ? current.filter(item => item !== market) : [...current, market])
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setMessage('')
    setError('')
    if (markets.length === 0) {
      setError('至少选择一个市场')
      return
    }
    setSubmitting(true)
    try {
      const result = await api<any>('/api/screening/tasks', {
        method: 'POST',
        body: JSON.stringify({ markets, timeframe, enable_ai_analysis: enableAi, send_feishu: sendFeishu })
      })
      setMessage(result.reused
        ? `已有任务运行中，已复用任务组 ${result.job_id}。`
        : `已创建任务组 ${result.job_id}，等待本地 Agent 领取执行。`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section>
      <Header title="全市场筛选" subtitle="启动 HK / US / A 批量筛选，后台生成 CSV 和报告" />
      <form className="form-grid" onSubmit={submit}>
        <Field label="市场">
          <div className="segmented">
            {MARKET_OPTIONS.map(market => (
              <button type="button" className={markets.includes(market) ? 'selected' : ''} onClick={() => toggleMarket(market)} key={market}>{market}</button>
            ))}
          </div>
        </Field>
        <Field label="周期">
          <select value={timeframe} onChange={event => setTimeframe(event.target.value)}>
            {TIMEFRAME_OPTIONS.map(item => <option key={item}>{item}</option>)}
          </select>
        </Field>
        <label className="check"><input type="checkbox" checked={enableAi} onChange={event => setEnableAi(event.target.checked)} /> AI 分析</label>
        <label className="check"><input type="checkbox" checked={sendFeishu} onChange={event => setSendFeishu(event.target.checked)} /> 发送飞书</label>
        <button className="primary" disabled={submitting || markets.length === 0}>{submitting ? '创建中...' : '启动筛选'}</button>
      </form>
      {markets.length === 0 && <div className="inline-error">至少选择一个市场后才能启动筛选。</div>}
      {error && <div className="error">{error}</div>}
      {message && <div className="notice">{message}</div>}
    </section>
  )
}

function SingleStock() {
  const [market, setMarket] = useState('US')
  const [timeframe, setTimeframe] = useState('1d')
  const [code, setCode] = useState('AAPL')
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [runId, setRunId] = useState('')

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setLoading(true)
    setResult(null)
    setRunId('')
    try {
      const data = await api('/api/screening/single-stock', {
        method: 'POST',
        body: JSON.stringify({ market, code, timeframe })
      })
      setResult(data)
      setRunId((data as any).run_id || '')
    } catch (err) {
      setResult({ error: err instanceof Error ? err.message : '分析失败' })
      setLoading(false)
    }
  }

  async function refreshRun(id: string) {
    try {
      const data = await api(`/api/screening/single-stock/${id}`)
      setResult(data)
      const status = (data as any).status
      if (status === 'completed' || status === 'failed') setLoading(false)
    } catch (err) {
      setResult({ error: err instanceof Error ? err.message : '加载单股任务失败' })
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!runId) return
    refreshRun(runId).catch(console.error)
    const timer = window.setInterval(() => refreshRun(runId).catch(console.error), 5000)
    return () => window.clearInterval(timer)
  }, [runId])

  useEffect(() => {
    if (!result || !runId) return
    if (result.status === 'completed' || result.status === 'failed') {
      setLoading(false)
      setRunId('')
    }
  }, [result, runId])

  return (
    <section>
      <Header title="单股选股" subtitle="输入股票代码，跑完整规则链；未通过只展示失败原因" />
      <form className="form-grid" onSubmit={submit}>
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
        <Field label="股票代码">
          <input value={code} onChange={event => setCode(event.target.value)} />
        </Field>
        <button className="primary" disabled={loading}>{loading ? '等待本地 Agent...' : '开始分析'}</button>
      </form>
      {result && <SingleResult result={result} />}
    </section>
  )
}

function SingleResult({ result }: { result: any }) {
  if (result.error) return <div className="error">{result.error}</div>
  if (result.status && result.status !== 'completed' && result.status !== 'failed') {
    return <div className="notice">单股任务 {result.run_id} 当前状态：{statusLabel(result.status)}，等待本地 Agent 执行。</div>
  }
  const details = result.rule_chain?.details || result.rule_details || []
  const displayCode = result.code || result.normalized_code || result.run_id
  const displayName = result.name || result.normalized_code || ''
  return (
    <div className="result-layout">
      <Panel title={`${displayCode} ${displayName}`}>
        <StatusBadge value={Boolean(result.passed)} />
        <dl className="info-list">
          <div><dt>板块</dt><dd>{displayMissing(result.sector)}</dd></div>
          <div><dt>行业</dt><dd>{displayMissing(result.industry)}</dd></div>
          <div><dt>数据源</dt><dd>{displayMissing(result.data_source)}</dd></div>
          <div><dt>市值</dt><dd>{displayMissing(result.market_cap)}</dd></div>
          <div><dt>PE</dt><dd>{displayMissing(result.pe_ratio)}</dd></div>
        </dl>
        <p>命中条件：{(result.conditions_met || []).join(' | ') || '无'}</p>
        {result.ai_analysis && <pre>{JSON.stringify(result.ai_analysis, null, 2)}</pre>}
      </Panel>
      <Panel title="规则明细">
        <Table rows={details} columns={['rule_name', 'rule_type', 'result', 'reason']} />
      </Panel>
    </div>
  )
}

function SingleRunDetail({ runId }: { runId: string }) {
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')

  async function refresh() {
    if (!runId) return
    setError('')
    try {
      const data = await api(`/api/screening/single-stock/${runId}`)
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载单股任务失败')
    }
  }

  useEffect(() => {
    refresh().catch(console.error)
    if (!runId) return
    const timer = window.setInterval(() => refresh().catch(console.error), 5000)
    return () => window.clearInterval(timer)
  }, [runId])

  return (
    <section>
      <Header title={`单股任务 ${runId ? runId.slice(0, 8) : ''}`} subtitle={runId || '请选择一个单股任务'} />
      <div className="toolbar toolbar-row">
        <button className="primary" onClick={() => refresh().catch(console.error)}>刷新</button>
      </div>
      {error && <div className="error">{error}</div>}
      {result && <SingleResult result={result} />}
    </section>
  )
}

function Rules() {
  const [market, setMarket] = useState('HK')
  const [rules, setRules] = useState<any>(null)
  useEffect(() => {
    api(`/api/rules?market=${market}`).then(setRules).catch(console.error)
  }, [market])
  return (
    <section>
      <Header title="规则链" subtitle="只读展示当前数据库规则配置" />
      <div className="toolbar">
        <select value={market} onChange={event => setMarket(event.target.value)}>
          {MARKET_OPTIONS.map(item => <option key={item}>{item}</option>)}
        </select>
      </div>
      <div className="table-note">SKIP 表示该规则启用但当前参数为空，不阻断通过。</div>
      <Panel title="生效规则链">
        <pre>{JSON.stringify(rules?.chain || {}, null, 2)}</pre>
      </Panel>
      <Panel title="原子规则">
        <Table rows={rules?.metadata || []} columns={['rule_key', 'rule_name', 'rule_type', 'implementation', 'enabled', 'display_order']} />
      </Panel>
    </section>
  )
}

function TaskDetail({ taskId }: { taskId: string }) {
  const [task, setTask] = useState<Task | null>(null)
  const [results, setResults] = useState<ScreeningResult[]>([])
  const [totalCount, setTotalCount] = useState(0)
  const [passedCount, setPassedCount] = useState(0)
  const [limit, setLimit] = useState(100)
  const [offset, setOffset] = useState(0)
  const [passedOnly, setPassedOnly] = useState(false)
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [uploadedResultScope, setUploadedResultScope] = useState('')
  const [error, setError] = useState('')

  async function refresh() {
    if (!taskId) return
    setError('')
    try {
      const params = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
        passed_only: passedOnly ? 'true' : 'false'
      })
      const [taskData, resultData, artifactData] = await Promise.all([
        api<Task>(`/api/screening/tasks/${taskId}`),
        api<TaskResultsResponse>(`/api/screening/tasks/${taskId}/results?${params.toString()}`),
        api<{ artifacts: Artifact[] }>(`/api/screening/tasks/${taskId}/artifacts`)
      ])
      setTask(taskData)
      setResults(resultData.rows || [])
      setTotalCount(resultData.total_count || 0)
      setPassedCount(resultData.passed_count || 0)
      setUploadedResultScope(resultData.uploaded_result_scope || '')
      setArtifacts(artifactData.artifacts || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载任务失败')
    }
  }

  useEffect(() => {
    refresh().catch(console.error)
    if (!taskId) return
    const timer = window.setInterval(() => refresh().catch(console.error), 8000)
    return () => window.clearInterval(timer)
  }, [taskId, limit, offset, passedOnly])

  if (!taskId) {
    return <section><Header title="任务详情" subtitle="请选择一个筛选任务" /></section>
  }

  const filteredTotal = passedOnly ? passedCount : totalCount
  const currentStart = filteredTotal === 0 ? 0 : offset + 1
  const currentEnd = Math.min(offset + results.length, filteredTotal)
  const canPrev = offset > 0
  const canNext = offset + limit < filteredTotal
  return (
    <section>
      <Header title={`任务 ${taskId.slice(0, 8)}`} subtitle={taskId} />
      <div className="toolbar toolbar-row">
        <button className="primary" onClick={() => refresh().catch(console.error)}>刷新</button>
        <label className="check"><input type="checkbox" checked={passedOnly} onChange={event => { setPassedOnly(event.target.checked); setOffset(0) }} /> 只看通过</label>
        <Field label="每页数量">
          <select value={limit} onChange={event => { setLimit(Number(event.target.value)); setOffset(0) }}>
            {[50, 100, 200, 500].map(item => <option key={item} value={item}>{item}</option>)}
          </select>
        </Field>
      </div>
      {error && <div className="error">{error}</div>}
      {task && (
        <div className="metric-grid">
          <Metric label="市场" value={task.market} />
          <Metric label="状态" value={<StatusBadge value={task.status} />} />
          <Metric label="进度" value={`${task.completed_count}/${task.total_count}`} />
          <Metric label="通过" value={`${passedCount}/${totalCount}`} />
          <Metric label="当前展示" value={`${currentStart}-${currentEnd}/${filteredTotal}`} />
        </div>
      )}
      <Panel title="导出文件">
        {artifacts.length === 0 ? <div className="empty">该任务未登记 Web 导出文件；历史脚本任务可能没有 artifact 记录。</div> : (
          <div className="artifact-list">
            {artifacts.map(item => (
              <a key={item.artifact_id} href={`/api/artifacts/${item.artifact_id}/download`} target="_blank" rel="noreferrer">
                <strong>{item.file_name}</strong>
                <span>{item.artifact_type} · {formatFileSize(item.file_size)}</span>
              </a>
            ))}
          </div>
        )}
      </Panel>
      <Panel title="结果表格">
        {uploadedResultScope === 'passed_only' && (
          <div className="table-note">该任务为本地 Agent 轻量上传模式：云端只保存通过股票明细，失败股票只计入统计。</div>
        )}
        <div className="pager">
          <button type="button" disabled={!canPrev} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</button>
          <span>第 {filteredTotal === 0 ? 0 : Math.floor(offset / limit) + 1} 页</span>
          <button type="button" disabled={!canNext} onClick={() => setOffset(offset + limit)}>下一页</button>
        </div>
        <Table rows={results} columns={['is_passed', 'code', 'name', 'sector', 'industry', 'close_price', 'filter_summary']} />
      </Panel>
    </section>
  )
}

function Header({ title, subtitle }: { title: string; subtitle: string }) {
  return <header className="page-header"><h1>{title}</h1><p>{subtitle}</p></header>
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="panel"><h2>{title}</h2>{children}</section>
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>
}

function Table({ rows, columns }: { rows: any[]; columns: string[] }) {
  if (!rows || rows.length === 0) return <div className="empty">暂无数据</div>
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{columns.map(column => <th key={column}>{columnLabel(column)}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>{columns.map(column => <td key={column}>{formatCell(row[column], column)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TaskTable({ rows, openTask, openSingle }: { rows: Task[]; openTask: (taskId: string) => void; openSingle: (runId: string) => void }) {
  if (!rows || rows.length === 0) return <div className="empty">暂无数据</div>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>类型</th>
            <th>任务 ID</th>
            <th>市场</th>
            <th>周期</th>
            <th>状态</th>
            <th>进度</th>
            <th>当前股票</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
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
            const firstTaskId = row.task_ids && row.task_ids.length > 0 ? row.task_ids[0] : ''
            return (
              <tr key={`${row.job_type || 'screening'}-${id}`}>
                <td>{isSingle ? '单股' : isWebJob ? '任务组' : '全市场'}</td>
                <td>
                  {isWebJob && !firstTaskId ? (
                    <span title={id}>{id.slice(0, 8)}</span>
                  ) : (
                    <button
                      className="link-button"
                      title={id}
                      onClick={() => isSingle ? openSingle(id) : openTask(isWebJob ? firstTaskId : id)}
                    >
                      {id.slice(0, 8)}
                    </button>
                  )}
                </td>
                <td>{row.market}</td>
                <td>{row.timeframe}</td>
                <td><StatusBadge value={row.status} /></td>
                <td>{progressText}</td>
                <td>{stockText}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function formatSingleStockText(row: Task): string {
  const code = row.normalized_code || row.code || ''
  const name = row.name || row.current_stock_name || ''
  if (!name) return code || '未补齐'
  const normalized = (value: string) => value.trim().toLowerCase()
  const duplicateNames = new Set([row.normalized_code, row.code, code].filter(Boolean).map(value => normalized(String(value))))
  if (duplicateNames.has(normalized(name))) return code || name
  return [code, name].filter(Boolean).join(' ')
}

function StatusBadge({ value }: { value: string | boolean }) {
  const key = String(value).toLowerCase()
  const label = statusLabel(value)
  const className = key === 'true' || key === 'pass' || key === 'completed'
    ? 'pass'
    : key === 'false' || key === 'fail' || key === 'failed' || key === 'error'
      ? 'fail'
      : key === 'running'
        ? 'running'
        : key === 'queued'
          ? 'queued'
          : 'neutral'
  return <span className={`status ${className}`}>{label}</span>
}

function statusLabel(value: string | boolean) {
  const key = String(value).toLowerCase()
  return STATUS_LABELS[key] || String(value || '未补齐')
}

function columnLabel(column: string) {
  return COLUMN_LABELS[column] || column
}

function formatCell(value: any, column?: string): React.ReactNode {
  if (column === 'status' || column === 'is_passed' || column === 'result') return <StatusBadge value={value} />
  if (column === 'enabled') return value ? '启用' : '停用'
  if ((column === 'sector' || column === 'industry') && (value === undefined || value === null || value === '')) return '未补齐'
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'object' && value !== null) return JSON.stringify(value)
  if (value === undefined || value === null || value === '') return '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function displayMissing(value: any) {
  if (value === undefined || value === null || value === '') return '未补齐'
  return String(value)
}

function formatFileSize(value?: number) {
  if (!value) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

createRoot(document.getElementById('root')!).render(<App />)
