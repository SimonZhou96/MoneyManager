import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import CodeMirror from '@uiw/react-codemirror'
import { json } from '@codemirror/lang-json'
import { api } from './api'
import { QuantLab } from './features/quant/QuantLab'
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
  chain_key?: string
  chain_name?: string
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
type CustomListResultRow = ScreeningResult & {
  input?: string
  status?: string
  status_text?: string
  状态?: string
  task_id?: string
  reason?: string
}
type CustomListResultsResponse = {
  job_id: string
  status: string
  market: string
  timeframe: string
  chain_key?: string
  chain_name?: string
  task_id?: string
  input_summary?: Record<string, unknown>
  rows: CustomListResultRow[]
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
  chain_key?: string
  chain_name?: string
  pool_types?: string[]
  summary?: Record<string, unknown>
}
type RuleChain = {
  market: string
  timeframe: string
  chain_key: string
  chain_name: string
  expression: Record<string, unknown>
  expression_json?: Record<string, unknown>
  enabled: boolean
  priority: number
  description?: string
}
type AtomicRule = {
  market: string
  rule_key: string
  rule_name: string
  rule_type: string
  strategy_category?: string
  implementation: string
  params?: Record<string, unknown>
  signal_direction?: string | null
  signal_direction_label?: string
  enabled: boolean
  display_order: number
}
type RulesResponse = {
  market: string
  timeframe?: string
  metadata: AtomicRule[]
  chain: RuleChain
  chains: RuleChain[]
}

const jsonEditorExtensions = [json()]

type EvidenceLink = {
  label?: string
  url?: string
  title?: string
  domain?: string
  source_type?: string
}
type FactorCitations = Record<string, EvidenceLink[]>
type OptionCandidate = {
  candidate_id: string
  策略名称: string
  评分: number
  期权评分?: number
  宏观分析评分?: number
  综合评分?: number
  宏观方向?: string
  新闻影响?: string
  热点匹配?: string
  主力资金风险?: string
  宏观摘要?: string
  关键利好因素?: string[]
  关键风险因素?: string[]
  '宏观/政策因素'?: string[]
  信息来源?: string[]
  数据缺失原因?: string[]
  引用来源?: EvidenceLink[]
  因素引用?: FactorCitations
  适用理由?: string
  合约明细?: Record<string, unknown>[]
  risk_metrics?: Record<string, unknown>
  order_suggestion?: Record<string, unknown>
  warnings?: string[]
  数据质量?: Record<string, unknown>
}
type OptionEvaluationResponse = {
  run_id: string
  market: string
  code: string
  status: string
  risk_profile?: string
  risk_profile_label?: string
  风险偏好?: string
  warnings?: string[]
  data_quality?: Record<string, unknown>
  candidates?: OptionCandidate[]
  macro_analysis?: Record<string, unknown>
}
type OptionBatchRow = {
  market?: string
  code?: string
  status?: string
  run_id?: string
  最佳策略?: string
  评分?: number
  期权评分?: number
  宏观分析评分?: number
  综合评分?: number
  candidates?: OptionCandidate[]
  macro_analysis?: Record<string, unknown>
  data_quality?: Record<string, unknown>
  warnings?: string[]
}
type OptionPosition = {
  position_id: string
  market?: string
  code?: string
  strategy_name?: string
  filled_price?: number
  quantity?: number
  status?: string
  current_action?: string
}
type OptionMonitorEvent = {
  event_id?: string
  position_id?: string
  提醒级别?: string
  事件类型?: string
  提醒内容?: string
  created_at?: string
}

const MARKET_OPTIONS = ['HK', 'US', 'A']
const OPTION_MARKET_OPTIONS = [
  { value: 'US', label: '美股' },
  { value: 'HK', label: '港股' },
  { value: 'A', label: 'A股' }
]
const TIMEFRAME_OPTIONS = ['1d', '1wk', '1mo', '3mo', '1m', '3m', '5m', '15m', '30m', '60m']
const POOL_OPTIONS = [
  { value: 'best', label: '优选池' },
  { value: 'major_index', label: '核心指数' },
  { value: 'industry_top5', label: '行业前五' },
  { value: 'recent_ipo_2y', label: '两年新股' },
  { value: 'all_etf', label: '全部ETF' }
]

const COLUMN_LABELS: Record<string, string> = {
  artifact_type: '文件类型',
  close_price: '最新收盘价',
  chain_key: '规则链 Key',
  chain_name: '规则链',
  code: '股票代码',
  created_at: '创建时间',
  current_stock_code: '当前股票',
  current_stock_name: '当前股票名',
  current_action: '当前动作',
  display_order: '排序',
  enabled: '启用状态',
  error_message: '错误信息',
  file_name: '文件名',
  file_size: '文件大小',
  filter_summary: '筛选摘要',
  implementation: '实现类',
  industry: '行业',
  input: '原始输入',
  is_passed: '通过状态',
  job_id: '任务组 ID',
  market: '市场',
  markets: '市场',
  name: '名称',
  reason: '原因',
  risk_profile_label: '风险偏好',
  result: '结果',
  run_id: '评估编号',
  rule_key: '规则 Key',
  rule_name: '规则名',
  rule_type: '规则类型',
  signal_direction_label: '方向',
  sector: '所属板块',
  status: '状态',
  task_id: '任务 ID',
  timeframe: '周期',
  position_id: '持仓编号',
  strategy_name: '策略名称',
  filled_price: '成交价格',
  quantity: '数量',
  event_id: '提醒编号'
}

const STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  error: '错误',
  fail: '失败',
  failed: '失败',
  false: '未通过',
  expired: '已过期',
  pass: '通过',
  passed: '通过',
  queued: '排队中',
  running: '运行中',
  skip: '跳过',
  valid: '待筛选',
  invalid: '无效输入',
  duplicate: '重复输入',
  true: '通过'
}

function chainDisplay(chain?: Partial<RuleChain> | null) {
  if (!chain?.chain_key) return '默认链/历史任务'
  return chain.chain_name ? `${chain.chain_name} (${chain.chain_key})` : chain.chain_key
}

function ruleDirection(rule: AtomicRule) {
  const fromField = String(rule.signal_direction || '').trim()
  if (fromField) return fromField
  const fromParams = String(rule.params?.direction || '').trim()
  return fromParams || 'unknown'
}

function ruleDirectionLabel(direction: string) {
  if (direction === 'bullish') return '看涨'
  if (direction === 'bearish') return '看跌'
  if (direction === 'neutral') return '中性'
  return '未标明'
}

function ruleGroupLabel(direction: string) {
  if (direction === 'bullish') return '看涨规则'
  if (direction === 'bearish') return '看跌规则'
  if (direction === 'neutral') return '中性规则'
  return '其他规则'
}

function ruleDirectionOrder(direction: string) {
  if (direction === 'bullish') return 1
  if (direction === 'bearish') return 2
  if (direction === 'neutral') return 3
  return 4
}

function commonRuleChains(markets: string[], rulesByMarket: Record<string, RulesResponse | undefined>): RuleChain[] {
  if (markets.length === 0) return []
  const firstRules = rulesByMarket[markets[0]]
  if (!firstRules?.chains) return []
  return firstRules.chains
    .filter(chain => markets.every(market => (rulesByMarket[market]?.chains || []).some(item => item.chain_key === chain.chain_key && item.timeframe === chain.timeframe)))
    .sort((a, b) => Number(b.enabled) - Number(a.enabled) || a.priority - b.priority || a.chain_key.localeCompare(b.chain_key))
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
          <button className={page === 'codeScreening' ? 'active' : ''} onClick={() => setPage('codeScreening')}>代码筛选</button>
          <button className={page === 'options' ? 'active' : ''} onClick={() => setPage('options')}>期权实验室</button>
          <button className={page === 'quant' ? 'active' : ''} onClick={() => setPage('quant')}>量化实验室</button>
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
        />}
        {page === 'screening' && <Screening />}
        {page === 'codeScreening' && <CodeScreening openTask={(taskId) => { setSelectedTaskId(taskId); setPage('task') }} />}
        {page === 'options' && <OptionLab />}
        {page === 'quant' && <QuantLab />}
        {page === 'rules' && <Rules />}
        {page === 'task' && <TaskDetail taskId={selectedTaskId} />}
      </main>
    </div>
  )
}

function OptionLab() {
  const [mode, setMode] = useState<'single' | 'batch'>('single')
  const [market, setMarket] = useState('US')
  const [code, setCode] = useState('AAPL')
  const [codes, setCodes] = useState('AAPL,MSFT')
  const [riskProfile, setRiskProfile] = useState('均衡')
  const [capital, setCapital] = useState('')
  const [maxLoss, setMaxLoss] = useState('')
  const [holdingDays, setHoldingDays] = useState('30')
  const [enableMacroAnalysis, setEnableMacroAnalysis] = useState(false)
  const [forceMacroRefresh, setForceMacroRefresh] = useState(false)
  const [result, setResult] = useState<OptionEvaluationResponse | null>(null)
  const [batchRows, setBatchRows] = useState<OptionBatchRow[]>([])
  const [selected, setSelected] = useState<OptionCandidate | null>(null)
  const [savedPlanId, setSavedPlanId] = useState('')
  const [fillPrice, setFillPrice] = useState('')
  const [fillQuantity, setFillQuantity] = useState('1')
  const [fillFee, setFillFee] = useState('')
  const [fillAt, setFillAt] = useState(defaultDateTimeInput())
  const [positions, setPositions] = useState<OptionPosition[]>([])
  const [events, setEvents] = useState<OptionMonitorEvent[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    loadPositions().catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!enableMacroAnalysis) setForceMacroRefresh(false)
  }, [enableMacroAnalysis])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setError('')
    setMessage('')
    setLoading(true)
    selectCandidate(null)
    try {
      const base = {
        market,
        risk_profile: riskProfile,
        capital: parseOptionalNumber(capital),
        max_loss: parseOptionalNumber(maxLoss),
        planned_holding_days: parseOptionalNumber(holdingDays),
        enable_macro_analysis: enableMacroAnalysis,
        force_macro_refresh: enableMacroAnalysis ? forceMacroRefresh : false,
        macro_cache_ttl_minutes: 60
      }
      if (mode === 'single') {
        const data = await api<OptionEvaluationResponse>('/api/options/evaluate', {
          method: 'POST',
          body: JSON.stringify({ ...base, code })
        })
        const candidates = data.candidates || []
        setResult(data)
        setBatchRows([])
        selectCandidate(candidates[0] || null)
      } else {
        const data = await api<any>('/api/options/evaluate-batch', {
          method: 'POST',
          body: JSON.stringify({ ...base, codes: splitCodes(codes) })
        })
        const rows = normalizeOptionBatchRows(data)
        setBatchRows(rows)
        setResult(null)
        selectCandidate(firstBatchCandidate(rows))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '期权评估失败')
    } finally {
      setLoading(false)
    }
  }

  async function savePlan(candidate: OptionCandidate) {
    setError('')
    setMessage('')
    try {
      const data = await api<any>('/api/options/order-plans', {
        method: 'POST',
        body: JSON.stringify({ candidate_id: candidate.candidate_id })
      })
      setSavedPlanId(data.plan_id || '')
      setMessage(`已保存订单建议 ${data.plan_id || '成功'}。系统不会自动下单，请在富途手动下单后回填成交信息。`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存订单建议失败')
    }
  }

  async function recordFill() {
    setError('')
    setMessage('')
    if (!savedPlanId) {
      setError('请先保存订单建议，或输入订单建议编号')
      return
    }
    if (!fillPrice || Number(fillPrice) <= 0) {
      setError('请输入有效的成交价格')
      return
    }
    try {
      const position = await api<OptionPosition>(`/api/options/order-plans/${savedPlanId}/fills`, {
        method: 'POST',
        body: JSON.stringify({
          filled_price: Number(fillPrice),
          quantity: Number(fillQuantity || '1'),
          filled_at: fillAt.replace('T', ' '),
          fee: parseOptionalNumber(fillFee)
        })
      })
      setMessage(`已回填成交并加入监控 ${position.position_id}`)
      await loadPositions()
    } catch (err) {
      setError(err instanceof Error ? err.message : '回填成交失败')
    }
  }

  async function loadPositions() {
    const data = await api<{ positions: OptionPosition[] }>('/api/options/positions')
    setPositions(data.positions || [])
  }

  async function refreshMonitor(positionId: string) {
    setError('')
    setMessage('')
    try {
      const data = await api<{ events: OptionMonitorEvent[] }>(`/api/options/positions/${positionId}/refresh`, { method: 'POST' })
      setEvents(data.events || [])
      setMessage('监控已刷新')
      await loadPositions()
    } catch (err) {
      setError(err instanceof Error ? err.message : '刷新监控失败')
    }
  }

  function selectCandidate(candidate: OptionCandidate | null) {
    setSelected(candidate)
    setSavedPlanId('')
    setFillPrice('')
    setFillFee('')
    setFillQuantity('1')
    setFillAt(defaultDateTimeInput())
  }

  const selectedOrderSuggestion = selected?.order_suggestion || {}
  const selectedRiskMetrics = selected?.risk_metrics || {}
  const selectedRows = selected ? [optionDetailSummary(selected)] : []
  const batchCandidateRows = batchRows.flatMap(row => row.candidates || [])
  const resultCandidateRows = result?.candidates || []
  const macroColumnsVisible = resultCandidateRows.some(hasOptionMacroFields) || batchCandidateRows.some(hasOptionMacroFields)
  const candidateColumns = macroColumnsVisible
    ? ['策略名称', '期权评分', '宏观分析评分', '综合评分', '宏观方向', '新闻影响', '热点匹配', '主力资金风险', '适用理由']
    : ['策略名称', '期权评分', '适用理由']
  const batchCandidateColumns = macroColumnsVisible
    ? ['market', 'code', '策略名称', '期权评分', '宏观分析评分', '综合评分', '宏观方向', '新闻影响', '热点匹配', '主力资金风险', '适用理由']
    : ['market', 'code', '策略名称', '期权评分', '适用理由']
  const batchResultColumns = macroColumnsVisible
    ? ['market', 'code', 'status', 'run_id', '最佳策略', '期权评分', '宏观分析评分', '综合评分']
    : ['market', 'code', 'status', 'run_id', '最佳策略', '评分']
  const detailColumns = selected && hasOptionMacroFields(selected)
    ? ['策略名称', '期权评分', '宏观分析评分', '综合评分', '宏观方向', '新闻影响', '热点匹配', '主力资金风险', '建议限价', '允许滑点', '建议数量', '最大亏损', '目标收益', '盈亏平衡点', '止损价', '止盈价', '计划持有期', '退出条件']
    : ['策略名称', '期权评分', '建议限价', '允许滑点', '建议数量', '最大亏损', '目标收益', '盈亏平衡点', '止损价', '止盈价', '计划持有期', '退出条件']

  return (
    <section>
      <Header title="期权实验室" subtitle="评估期权策略、生成订单建议、回填成交并监控风险" />
      <form className="form-grid option-form" onSubmit={submit}>
        <Field label="评估模式">
          <div className="segmented">
            <button type="button" className={mode === 'single' ? 'selected' : ''} onClick={() => setMode('single')}>单标的评估</button>
            <button type="button" className={mode === 'batch' ? 'selected' : ''} onClick={() => setMode('batch')}>批量评估</button>
          </div>
        </Field>
        <Field label="市场">
          <select value={market} onChange={event => setMarket(event.target.value)}>
            {OPTION_MARKET_OPTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </Field>
        {mode === 'single' ? (
          <Field label="标的代码"><input value={code} onChange={event => setCode(event.target.value)} /></Field>
        ) : (
          <Field label="标的代码列表"><input value={codes} onChange={event => setCodes(event.target.value)} /></Field>
        )}
        <Field label="风险偏好">
          <select value={riskProfile} onChange={event => setRiskProfile(event.target.value)}>
            <option>保守</option>
            <option>均衡</option>
            <option>进取</option>
          </select>
        </Field>
        <Field label="资金规模"><input value={capital} onChange={event => setCapital(event.target.value)} placeholder="可留空" inputMode="decimal" /></Field>
        <Field label="最大可接受亏损"><input value={maxLoss} onChange={event => setMaxLoss(event.target.value)} placeholder="可留空" inputMode="decimal" /></Field>
        <Field label="计划持有期"><input value={holdingDays} onChange={event => setHoldingDays(event.target.value)} inputMode="numeric" /></Field>
        <Field label="宏观层面分析">
          <label className="check">
            <input type="checkbox" checked={enableMacroAnalysis} onChange={event => setEnableMacroAnalysis(event.target.checked)} />
            开启宏观层面分析
          </label>
        </Field>
        <Field label="宏观缓存">
          <label className="check">
            <input
              type="checkbox"
              checked={forceMacroRefresh}
              disabled={!enableMacroAnalysis}
              onChange={event => setForceMacroRefresh(event.target.checked)}
            />
            强制重新分析
          </label>
        </Field>
        <button className="primary" disabled={loading}>{loading ? '评估中...' : '开始评估'}</button>
      </form>
      {error && <div className="error">{error}</div>}
      {message && <div className="notice">{message}</div>}
      {result && (
        <>
          <div className="option-layout">
            <Panel title="推荐结论">
              <dl className="info-list">
                <div><dt>评估编号</dt><dd>{result.run_id}</dd></div>
                <div><dt>市场</dt><dd>{optionMarketLabel(result.market)}</dd></div>
                <div><dt>标的代码</dt><dd>{result.code}</dd></div>
                <div><dt>状态</dt><dd><StatusBadge value={result.status} /></dd></div>
                <div><dt>风险偏好</dt><dd>{result.风险偏好 || result.risk_profile_label || riskProfile}</dd></div>
              </dl>
            </Panel>
            <Panel title="期权数据质量">
              <dl className="info-list">
                <div><dt>状态</dt><dd>{displayMissing(result.data_quality?.status)}</dd></div>
                <div><dt>报价时间</dt><dd>{displayMissing(result.data_quality?.quote_time)}</dd></div>
                <div><dt>风险提示</dt><dd>{displayWarnings(result.warnings || result.data_quality?.warnings)}</dd></div>
              </dl>
            </Panel>
          </div>
          {result.macro_analysis && (
            <Panel title="宏观层面分析">
              <dl className="info-list option-macro-info">
                <div><dt>宏观分析评分</dt><dd>{displayMissing(result.macro_analysis['宏观分析评分'])}</dd></div>
                <div><dt>宏观方向</dt><dd>{displayMissing(result.macro_analysis['宏观方向'])}</dd></div>
                <div><dt>新闻影响</dt><dd>{displayMissing(result.macro_analysis['新闻影响'])}</dd></div>
                <div><dt>热点匹配</dt><dd>{displayMissing(result.macro_analysis['热点匹配'])}</dd></div>
                <div><dt>主力资金风险</dt><dd>{displayMissing(result.macro_analysis['主力资金风险'])}</dd></div>
                <div><dt>宏观摘要</dt><dd>{displayMissing(result.macro_analysis['宏观摘要'])}</dd></div>
                <div><dt>数据缺失原因</dt><dd>{displayWarnings(result.macro_analysis['数据缺失原因'])}</dd></div>
                <div><dt>关键利好因素</dt><dd><FactorList factors={asStringList(result.macro_analysis['关键利好因素'])} citations={asFactorCitations(result.macro_analysis['因素引用'])} /></dd></div>
                <div><dt>关键风险因素</dt><dd><FactorList factors={asStringList(result.macro_analysis['关键风险因素'])} citations={asFactorCitations(result.macro_analysis['因素引用'])} /></dd></div>
                <div><dt>宏观/政策因素</dt><dd><FactorList factors={asStringList(result.macro_analysis['宏观/政策因素'])} citations={asFactorCitations(result.macro_analysis['因素引用'])} /></dd></div>
                <div><dt>信息来源</dt><dd><CitationTags links={asEvidenceLinks(result.macro_analysis['引用来源'], asStringList(result.macro_analysis['信息来源']))} /></dd></div>
              </dl>
            </Panel>
          )}
          <Panel title="策略候选排行">
            <Table rows={result.candidates || []} columns={candidateColumns} onRowClick={(row) => selectCandidate(row as OptionCandidate)} />
          </Panel>
        </>
      )}
      {batchRows.length > 0 && (
        <Panel title="批量评估结果">
          <Table rows={batchRows} columns={batchResultColumns} onRowClick={(row) => selectCandidate(firstBatchCandidate([row as OptionBatchRow]))} />
        </Panel>
      )}
      {!result && batchCandidateRows.length > 0 && (
        <Panel title="策略候选排行">
          <Table rows={batchCandidateRows} columns={batchCandidateColumns} onRowClick={(row) => selectCandidate(row as OptionCandidate)} />
        </Panel>
      )}
      {selected && (
        <Panel title="建议详情">
          <div className="option-detail">
            <div className="option-detail-heading">
              <div>
                <h3>{selected.策略名称}</h3>
                <p>{selected.适用理由 || '暂无适用理由'}</p>
              </div>
              <button className="primary" onClick={() => savePlan(selected)}>保存订单建议</button>
            </div>
            <Table rows={selectedRows} columns={detailColumns} />
            {hasOptionMacroFields(selected) && (
              <section className="option-subsection">
                <h4>宏观分析</h4>
                <dl className="info-list option-macro-info">
                  <div><dt>宏观摘要</dt><dd>{displayMissing(selected.宏观摘要)}</dd></div>
                  <div><dt>数据缺失原因</dt><dd>{displayWarnings(selected.数据缺失原因)}</dd></div>
                  <div><dt>关键利好因素</dt><dd><FactorList factors={asStringList(selected.关键利好因素)} citations={selected.因素引用 || {}} /></dd></div>
                  <div><dt>关键风险因素</dt><dd><FactorList factors={asStringList(selected.关键风险因素)} citations={selected.因素引用 || {}} /></dd></div>
                  <div><dt>宏观/政策因素</dt><dd><FactorList factors={asStringList(selected['宏观/政策因素'])} citations={selected.因素引用 || {}} /></dd></div>
                  <div><dt>信息来源</dt><dd><CitationTags links={asEvidenceLinks(selected.引用来源, selected.信息来源)} /></dd></div>
                </dl>
              </section>
            )}
            <section className="option-subsection">
              <h4>合约明细</h4>
              <Table rows={selected.合约明细 || []} columns={['买卖方向', '期权类型', '合约代码', '到期日', '行权价', '建议价格', '数量']} />
            </section>
            <div className="option-summary-grid">
              <div>
                <h4>订单建议</h4>
                <pre>{JSON.stringify(selectedOrderSuggestion, null, 2)}</pre>
              </div>
              <div>
                <h4>风险指标</h4>
                <pre>{JSON.stringify(selectedRiskMetrics, null, 2)}</pre>
              </div>
            </div>
            <div className="table-note">系统不会自动下单，请在富途手动下单后回填成交信息。</div>
            <section className="option-subsection">
              <h4>成交回填</h4>
              <div className="form-grid option-fill-form">
                <Field label="订单建议编号"><input value={savedPlanId} onChange={event => setSavedPlanId(event.target.value)} placeholder="保存订单建议后自动填入" /></Field>
                <Field label="成交价格"><input value={fillPrice} onChange={event => setFillPrice(event.target.value)} inputMode="decimal" /></Field>
                <Field label="成交数量"><input value={fillQuantity} onChange={event => setFillQuantity(event.target.value)} inputMode="numeric" /></Field>
                <Field label="成交时间"><input type="datetime-local" value={fillAt} onChange={event => setFillAt(event.target.value)} /></Field>
                <Field label="手续费"><input value={fillFee} onChange={event => setFillFee(event.target.value)} placeholder="可留空" inputMode="decimal" /></Field>
                <button className="primary" type="button" onClick={recordFill}>回填成交并加入监控</button>
              </div>
            </section>
            {selected.warnings && selected.warnings.length > 0 && <div className="notice">{displayWarnings(selected.warnings)}</div>}
          </div>
        </Panel>
      )}
      <Panel title="持仓监控">
        <Table rows={positions} columns={['position_id', 'market', 'code', 'strategy_name', 'filled_price', 'quantity', 'status', 'current_action']} onRowClick={(row) => refreshMonitor((row as OptionPosition).position_id)} />
        {events.length > 0 && (
          <div className="option-subsection">
            <h4>监控提醒</h4>
            <Table rows={events} columns={['提醒级别', '事件类型', '提醒内容', 'created_at']} />
          </div>
        )}
      </Panel>
    </section>
  )
}

function Dashboard({ openTask }: { openTask: (taskId: string) => void }) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [syncRuns, setSyncRuns] = useState<any[]>([])

  async function refresh() {
    const taskData = await api<{ jobs: Job[]; tasks: Task[] }>('/api/screening/tasks')
    setJobs(taskData.jobs)
    setTasks(taskData.tasks)
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
        <Metric label="筛选任务" value={tasks.length} />
        <Metric label="最近同步" value={statusLabel(syncRuns[0]?.status || '无')} />
      </div>
      <Panel title="最近 Web 任务">
        <Table rows={jobs} columns={['job_id', 'markets', 'timeframe', 'chain_name', 'status', 'created_at', 'error_message']} />
      </Panel>
      <Panel title="最近筛选任务">
        <TaskTable rows={mergeRecentTasks(tasks, jobs)} openTask={openTask} />
      </Panel>
    </section>
  )
}

function mergeRecentTasks(tasks: Task[], jobs: Job[]): Task[] {
  const marketTasks = (tasks || []).map(item => ({ ...item, job_type: 'screening' }))
  const webJobs = (jobs || []).map(item => ({
    ...item,
    task_id: item.job_id,
    job_type: 'web_job',
    market: (item.markets || []).join(', '),
    total_count: item.task_ids?.length || 0,
    completed_count: item.status === 'completed' ? (item.task_ids?.length || 0) : 0,
    current_stock_code: item.task_ids && item.task_ids.length > 0 ? item.task_ids[0] : '等待本地 Agent',
    chain_key: item.chain_key,
    chain_name: item.chain_name
  }))
  return [...webJobs, ...marketTasks]
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
    .slice(0, 50)
}

function Screening() {
  const [markets, setMarkets] = useState(['HK', 'US', 'A'])
  const [timeframe, setTimeframe] = useState('1d')
  const [poolTypes, setPoolTypes] = useState(POOL_OPTIONS.map(item => item.value))
  const [rulesByMarket, setRulesByMarket] = useState<Record<string, RulesResponse>>({})
  const [chainKey, setChainKey] = useState('')
  const [enableAi, setEnableAi] = useState(true)
  const [sendFeishu, setSendFeishu] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  function toggleMarket(market: string) {
    setError('')
    setMarkets(current => current.includes(market) ? current.filter(item => item !== market) : [...current, market])
  }

  function togglePoolType(poolType: string) {
    setError('')
    setPoolTypes(current => current.includes(poolType) ? current.filter(item => item !== poolType) : [...current, poolType])
  }

  useEffect(() => {
    let cancelled = false
    async function loadRules() {
      const entries = await Promise.all(markets.map(async market => {
        const rules = await api<RulesResponse>(`/api/rules?market=${market}&timeframe=${timeframe}`)
        return [market, rules] as const
      }))
      if (!cancelled) {
        setRulesByMarket(current => ({ ...current, ...Object.fromEntries(entries) }))
      }
    }
    if (markets.length > 0) loadRules().catch(err => setError(err instanceof Error ? err.message : '加载规则链失败'))
    return () => { cancelled = true }
  }, [markets.join('|'), timeframe])

  const chainOptions = commonRuleChains(markets, rulesByMarket)
  const chainOptionKey = chainOptions.map(item => item.chain_key).join('|')

  useEffect(() => {
    if (chainOptions.length === 0) {
      setChainKey('')
      return
    }
    const activeKey = rulesByMarket[markets[0]]?.chain?.chain_key
    if (!chainKey || !chainOptions.some(item => item.chain_key === chainKey)) {
      setChainKey(activeKey && chainOptions.some(item => item.chain_key === activeKey) ? activeKey : chainOptions[0].chain_key)
    }
  }, [chainOptionKey, markets.join('|')])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setMessage('')
    setError('')
    if (markets.length === 0) {
      setError('至少选择一个市场')
      return
    }
    if (poolTypes.length === 0) {
      setError('至少选择一个股票池类型')
      return
    }
    setSubmitting(true)
    try {
      const result = await api<any>('/api/screening/tasks', {
        method: 'POST',
        body: JSON.stringify({
          markets,
          timeframe,
          pool_types: poolTypes,
          chain_key: chainKey || undefined,
          enable_ai_analysis: enableAi,
          send_feishu: sendFeishu
        })
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
        <div className="field field-wide">
          <span>股票池类型</span>
          <div className="segmented segmented-wrap">
            {POOL_OPTIONS.map(item => (
              <button type="button" className={poolTypes.includes(item.value) ? 'selected' : ''} onClick={() => togglePoolType(item.value)} key={item.value}>{item.label}</button>
            ))}
          </div>
        </div>
        <Field label="规则链">
          <select value={chainKey} onChange={event => setChainKey(event.target.value)} disabled={chainOptions.length === 0}>
            {chainOptions.length === 0 ? <option value="">暂无共同规则链</option> : chainOptions.map(item => (
              <option key={item.chain_key} value={item.chain_key}>
                {item.chain_name} {item.enabled ? '默认候选' : '可试跑'}
              </option>
            ))}
          </select>
        </Field>
        <label className="check"><input type="checkbox" checked={enableAi} onChange={event => setEnableAi(event.target.checked)} /> AI 分析</label>
        <label className="check"><input type="checkbox" checked={sendFeishu} onChange={event => setSendFeishu(event.target.checked)} /> 发送飞书</label>
        <button className="primary" disabled={submitting || markets.length === 0 || poolTypes.length === 0 || chainOptions.length === 0}>{submitting ? '创建中...' : '启动筛选'}</button>
      </form>
      {markets.length === 0 && <div className="inline-error">至少选择一个市场后才能启动筛选。</div>}
      {poolTypes.length === 0 && <div className="inline-error">至少选择一个股票池类型后才能启动筛选。</div>}
      {markets.length > 0 && chainOptions.length === 0 && <div className="inline-error">所选市场没有共同规则链，无法创建多市场任务。</div>}
      {error && <div className="error">{error}</div>}
      {message && <div className="notice">{message}</div>}
    </section>
  )
}

function CodeScreening({ openTask }: { openTask: (taskId: string) => void }) {
  const [market, setMarket] = useState('US')
  const [timeframe, setTimeframe] = useState('1d')
  const [rules, setRules] = useState<RulesResponse | null>(null)
  const [chainKey, setChainKey] = useState('')
  const [codes, setCodes] = useState('AAPL, MSFT')
  const [enableAi, setEnableAi] = useState(true)
  const [sendFeishu, setSendFeishu] = useState(false)
  const [jobId, setJobId] = useState('')
  const [result, setResult] = useState<CustomListResultsResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const parsedCodes = useMemo(() => splitCodes(codes), [codes])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setError('')
    setResult(null)
    setJobId('')
    if (parsedCodes.length === 0) {
      setError('至少输入一个代码')
      return
    }
    setLoading(true)
    try {
      const data = await api<{ job_id: string }>('/api/screening/custom-list-tasks', {
        method: 'POST',
        body: JSON.stringify({
          market,
          codes: parsedCodes,
          timeframe,
          chain_key: chainKey || undefined,
          enable_ai_analysis: enableAi,
          send_feishu: sendFeishu
        })
      })
      const nextJobId = data.job_id || ''
      setJobId(nextJobId)
      if (nextJobId) await refreshJob(nextJobId)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建代码筛选任务失败')
      setLoading(false)
    }
  }

  async function refreshJob(jobId: string) {
    try {
      const data = await api<CustomListResultsResponse>(`/api/screening/custom-list-tasks/${jobId}/results`)
      setResult(data)
      if (data.status === 'completed' || data.status === 'failed' || data.status === 'error') setLoading(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载代码筛选结果失败')
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!jobId || !loading) return
    refreshJob(jobId).catch(console.error)
    const timer = window.setInterval(() => refreshJob(jobId).catch(console.error), 5000)
    return () => window.clearInterval(timer)
  }, [jobId, loading])

  useEffect(() => {
    if (!result || !jobId) return
    if (result.status === 'completed' || result.status === 'failed' || result.status === 'error') {
      setLoading(false)
    }
  }, [result, jobId])

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

  const selectedChain = (rules?.chains || []).find(item => item.chain_key === chainKey) || rules?.chain

  return (
    <section className="code-screening-layout">
      <Header title="代码筛选" subtitle="输入一个或多个代码，使用自定义列表任务按原始顺序返回筛选结果" />
      <form className="compact-form-grid" onSubmit={submit}>
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
              <option key={item.chain_key} value={item.chain_key}>
                {item.chain_name} {item.enabled ? '默认候选' : '可试跑'}
              </option>
            ))}
          </select>
        </Field>
        <label className="check"><input type="checkbox" checked={enableAi} onChange={event => setEnableAi(event.target.checked)} /> AI 分析</label>
        <label className="check"><input type="checkbox" checked={sendFeishu} onChange={event => setSendFeishu(event.target.checked)} /> 发送飞书</label>
        <Field label="代码列表" className="field-wide">
          <textarea className="code-input" rows={5} value={codes} onChange={event => setCodes(event.target.value)} placeholder="AAPL, MSFT 或每行一个代码" />
        </Field>
        <div className="code-preview-actions">
          <button className="primary" disabled={loading || parsedCodes.length === 0 || (rules?.chains || []).length === 0}>{loading ? '等待本地 Agent...' : '启动代码筛选'}</button>
        </div>
      </form>
      <Panel title="输入预览">
        {parsedCodes.length === 0 ? (
          <div className="empty">暂无可提交代码</div>
        ) : (
          <div className="code-preview-grid">
            {parsedCodes.slice(0, 12).map((code, index) => (
              <article className="code-preview-card" key={`${code}-${index}`}>
                <header>
                  <h3>{code}</h3>
                  <span className="status-badge">待提交</span>
                </header>
                <dl>
                  <dt>市场</dt><dd>{optionMarketLabel(market)}</dd>
                  <dt>周期</dt><dd>{timeframe}</dd>
                  <dt>规则链</dt><dd>{selectedChain ? chainDisplay(selectedChain) : '未加载'}</dd>
                </dl>
              </article>
            ))}
            {parsedCodes.length > 12 && (
              <article className="code-preview-card">
                <header>
                  <h3>其余代码</h3>
                  <span className="status-badge">+{parsedCodes.length - 12}</span>
                </header>
                <p>提交时会按输入顺序一起进入自定义列表筛选。</p>
              </article>
            )}
          </div>
        )}
      </Panel>
      {error && <div className="error">{error}</div>}
      {result && (
        <>
          <div className="metric-grid">
            <Metric label="任务组" value={result.job_id ? result.job_id.slice(0, 8) : '-'} />
            <Metric label="市场" value={optionMarketLabel(result.market)} />
            <Metric label="周期" value={result.timeframe} />
            <Metric label="规则链" value={chainDisplay({ chain_key: result.chain_key || selectedChain?.chain_key, chain_name: result.chain_name || selectedChain?.chain_name })} />
            <Metric label="状态" value={<StatusBadge value={result.status} />} />
            <Metric label="通过" value={displayMissing(result.input_summary?.['通过数量'])} />
          </div>
          <Panel title="任务摘要">
            <dl className="info-list">
              <div><dt>输入数量</dt><dd>{displayMissing(result.input_summary?.['输入数量'] || result.input_summary?.input_count)}</dd></div>
              <div><dt>有效数量</dt><dd>{displayMissing(result.input_summary?.['有效代码数'] || result.input_summary?.valid_count)}</dd></div>
              <div><dt>无效代码</dt><dd>{displayMissing(result.input_summary?.['无效代码数'])}</dd></div>
              <div><dt>重复代码</dt><dd>{displayMissing(result.input_summary?.['重复代码数'])}</dd></div>
              <div><dt>未通过数量</dt><dd>{displayMissing(result.input_summary?.['未通过数量'])}</dd></div>
              <div><dt>筛选任务</dt><dd>{result.task_id ? <button className="link-button" onClick={() => openTask(result.task_id || '')}>{result.task_id.slice(0, 8)}</button> : '等待本地 Agent 创建'}</dd></div>
            </dl>
          </Panel>
          <Panel title="筛选结果">
            <CodeScreeningResultTable rows={result.rows || []} taskId={result.task_id} openTask={openTask} />
          </Panel>
        </>
      )}
    </section>
  )
}

function CodeScreeningResultTable({ rows, taskId, openTask }: { rows: CustomListResultRow[]; taskId?: string; openTask: (taskId: string) => void }) {
  if (!rows || rows.length === 0) return <div className="empty">暂无数据</div>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>原始输入</th>
            <th>股票代码</th>
            <th>名称</th>
            <th>状态</th>
            <th>结果</th>
            <th>所属板块</th>
            <th>行业</th>
            <th>最新收盘价</th>
            <th>筛选摘要</th>
            <th>原因</th>
            <th>任务详情</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const rowTaskId = row.task_id || taskId || ''
            return (
              <tr key={`${row.input || row.code || 'row'}-${index}`}>
                <td>{displayMissing(row.input || row.code)}</td>
                <td>{displayMissing(row.code)}</td>
                <td>{displayMissing(row.name)}</td>
                <td><span className={`status-badge ${codeScreeningStatusClass(row)}`}>{codeScreeningStatusLabel(row)}</span></td>
                <td><StatusBadge value={codeScreeningResultValue(row)} /></td>
                <td>{displayMissing(row.sector)}</td>
                <td>{displayMissing(row.industry)}</td>
                <td>{displayMissing(row.close_price)}</td>
                <td>{displayMissing(row.filter_summary)}</td>
                <td>{displayMissing(row.reason)}</td>
                <td>{rowTaskId ? <button className="link-button" onClick={() => openTask(rowTaskId)}>{rowTaskId.slice(0, 8)}</button> : '等待创建'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function codeScreeningStatusLabel(row: CustomListResultRow) {
  return row.状态 || row.status_text || statusLabel(row.status || 'queued')
}

function codeScreeningStatusClass(row: CustomListResultRow) {
  if (row.is_passed === true || row.status === 'passed') return 'pass'
  if (row.is_passed === false || row.status === 'failed') return 'fail'
  if (row.status === 'invalid') return 'invalid'
  if (row.status === 'duplicate') return 'duplicate'
  return ''
}

function codeScreeningResultValue(row: CustomListResultRow) {
  if (row.is_passed !== undefined && row.is_passed !== null) return row.is_passed
  return row.status || row.status_text || 'queued'
}

function Rules() {
  const [market, setMarket] = useState('HK')
  const [timeframe, setTimeframe] = useState('1d')
  const [rules, setRules] = useState<RulesResponse | null>(null)
  const [chainKey, setChainKey] = useState('')
  const [ruleSearch, setRuleSearch] = useState('')
  const [ruleTypeFilter, setRuleTypeFilter] = useState('all')
  const [ruleDirectionFilter, setRuleDirectionFilter] = useState('all')
  const [rulePage, setRulePage] = useState(0)
  const rulePageSize = 12
  const [editor, setEditor] = useState({
    timeframe: '1d',
    chain_key: '',
    chain_name: '',
    enabled: true,
    priority: 100,
    description: '',
    expression_text: '{\n  "ref": "zuoyi_signal"\n}'
  })
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  async function loadRules() {
    const data = await api<RulesResponse>(`/api/rules?market=${market}&timeframe=${timeframe}`)
    setRules(data)
    const activeKey = data.chain?.chain_key
    if (!chainKey || !(data.chains || []).some(item => item.chain_key === chainKey)) {
      setChainKey(activeKey || data.chains?.[0]?.chain_key || '')
    }
    return data
  }

  useEffect(() => {
    loadRules().catch(err => setError(err instanceof Error ? err.message : '加载规则链失败'))
  }, [market, timeframe])
  const selectedChain = (rules?.chains || []).find(item => item.chain_key === chainKey) || rules?.chain

  useEffect(() => {
    setRulePage(0)
  }, [ruleSearch, ruleTypeFilter, ruleDirectionFilter, market, timeframe])

  const ruleRows = useMemo(() => {
    const keyword = ruleSearch.trim().toLowerCase()
    return (rules?.metadata || [])
      .map(item => {
        const direction = ruleDirection(item)
        return {
          ...item,
          enabled: item.enabled ? '启用' : '停用',
          signal_direction: direction,
          signal_direction_label: ruleDirectionLabel(direction),
          direction_group: ruleGroupLabel(direction),
        }
      })
      .filter(item => {
        if (ruleTypeFilter !== 'all' && item.rule_type !== ruleTypeFilter) return false
        if (ruleDirectionFilter !== 'all' && item.signal_direction !== ruleDirectionFilter) return false
        if (!keyword) return true
        return [
          item.rule_key,
          item.rule_name,
          item.rule_type,
          item.strategy_category || '',
          item.implementation,
          item.signal_direction_label,
        ].some(value => String(value).toLowerCase().includes(keyword))
      })
      .sort((a, b) => {
        const directionDelta = ruleDirectionOrder(String(a.signal_direction)) - ruleDirectionOrder(String(b.signal_direction))
        if (directionDelta !== 0) return directionDelta
        return Number(a.display_order || 0) - Number(b.display_order || 0)
      })
  }, [rules?.metadata, ruleSearch, ruleTypeFilter, ruleDirectionFilter])

  const rulePageCount = Math.max(1, Math.ceil(ruleRows.length / rulePageSize))
  const visibleRuleRows = ruleRows.slice(rulePage * rulePageSize, (rulePage + 1) * rulePageSize)
  const groupedVisibleRuleRows = visibleRuleRows.reduce<Record<string, typeof visibleRuleRows>>((groups, row) => {
    const group = String(row.direction_group || '其他规则')
    groups[group] = groups[group] || []
    groups[group].push(row)
    return groups
  }, {})
  const jsonStatus = useMemo(() => {
    if (!editor.expression_text.trim()) {
      return { ok: false, message: 'expression_json 不能为空' }
    }
    try {
      JSON.parse(editor.expression_text)
      return { ok: true, message: 'JSON 格式有效' }
    } catch (err) {
      return {
        ok: false,
        message: `JSON 格式错误：${err instanceof Error ? err.message : '无法解析'}`
      }
    }
  }, [editor.expression_text])

  useEffect(() => {
    if (!selectedChain) return
    setEditor({
      timeframe: selectedChain.timeframe || timeframe,
      chain_key: selectedChain.chain_key,
      chain_name: selectedChain.chain_name,
      enabled: !!selectedChain.enabled,
      priority: selectedChain.priority || 100,
      description: selectedChain.description || '',
      expression_text: JSON.stringify(selectedChain.expression_json || selectedChain.expression || {}, null, 2)
    })
  }, [selectedChain?.chain_key, selectedChain?.timeframe])

  function startNewChain() {
    setEditor({
      timeframe,
      chain_key: '',
      chain_name: '',
      enabled: false,
      priority: 100,
      description: '',
      expression_text: '{\n  "ref": "zuoyi_signal"\n}'
    })
  }

  function insertRuleRef(ruleKey: string) {
    navigator.clipboard?.writeText(`{"ref":"${ruleKey}"}`).catch(() => undefined)
    setNotice(`已复制 {"ref":"${ruleKey}"} 到剪贴板`)
  }

  function formatExpressionJson() {
    setNotice('')
    setError('')
    try {
      const expression_json = JSON.parse(editor.expression_text)
      setEditor(current => ({
        ...current,
        expression_text: JSON.stringify(expression_json, null, 2)
      }))
      setNotice('expression_json 已格式化')
    } catch (err) {
      setError(`expression_json 不是合法 JSON：${err instanceof Error ? err.message : '无法解析'}`)
    }
  }

  async function saveChain() {
    setSaving(true)
    setNotice('')
    setError('')
    try {
      const expression_json = JSON.parse(editor.expression_text)
      const payload = {
        market,
        timeframe: editor.timeframe,
        chain_key: editor.chain_key,
        chain_name: editor.chain_name,
        expression_json,
        enabled: editor.enabled,
        priority: Number(editor.priority || 100),
        description: editor.description
      }
      const method = (rules?.chains || []).some(item => item.chain_key === editor.chain_key && item.timeframe === editor.timeframe) ? 'PUT' : 'POST'
      const path = method === 'PUT'
        ? `/api/rules/chains/${market}/${encodeURIComponent(editor.timeframe)}/${editor.chain_key}`
        : '/api/rules/chains'
      await api(path, { method, body: JSON.stringify(payload) })
      setNotice('规则链已保存')
      await loadRules()
      setChainKey(editor.chain_key)
    } catch (err) {
      if (err instanceof SyntaxError) {
        setError(`expression_json 不是合法 JSON：${err.message}`)
      } else {
        setError(err instanceof Error ? err.message : '保存规则链失败')
      }
    } finally {
      setSaving(false)
    }
  }

  async function deleteChain() {
    if (!editor.chain_key) return
    setSaving(true)
    setNotice('')
    setError('')
    try {
      await api(`/api/rules/chains/${market}/${encodeURIComponent(editor.timeframe)}/${editor.chain_key}`, { method: 'DELETE' })
      setNotice('规则链已删除')
      setChainKey('')
      startNewChain()
      await loadRules()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除规则链失败')
    } finally {
      setSaving(false)
    }
  }
  return (
    <section>
      <Header title="规则链" subtitle="查看原子规则，并以 JSON DSL 新增、编辑、删除规则链" />
      <div className="toolbar toolbar-row">
        <select value={market} onChange={event => setMarket(event.target.value)}>
          {MARKET_OPTIONS.map(item => <option key={item}>{item}</option>)}
        </select>
        <select value={timeframe} onChange={event => setTimeframe(event.target.value)}>
          {TIMEFRAME_OPTIONS.map(item => <option key={item}>{item}</option>)}
        </select>
        <Field label="规则链">
          <select value={chainKey} onChange={event => setChainKey(event.target.value)}>
            {(rules?.chains || []).map(item => (
              <option key={`${item.chain_key}:${item.timeframe}`} value={item.chain_key}>{item.chain_name}</option>
            ))}
          </select>
        </Field>
        <button type="button" onClick={startNewChain}>新建</button>
      </div>
      <div className="table-note">规则链表达式使用 JSON DSL。可从下方原子规则复制 `ref` 节点；当前版本不做可视化编排器。</div>
      {error && <div className="error">{error}</div>}
      {notice && <div className="notice">{notice}</div>}
      <Panel title="规则链列表">
        <Table rows={(rules?.chains || []).map(item => ({
          ...item,
          timeframe: item.timeframe,
          chain_name: item.chain_key === rules?.chain?.chain_key ? `${item.chain_name}（默认生效）` : item.chain_name,
          enabled: item.enabled ? '默认候选' : '可试跑',
        }))} columns={['chain_name', 'chain_key', 'timeframe', 'enabled', 'priority', 'description']} onRowClick={(row) => setChainKey(row.chain_key)} />
      </Panel>
      <Panel title="规则链编辑">
        <div className="rule-editor-layout">
          <div className="rule-editor-fields">
            <Field label="规则链 Key"><input value={editor.chain_key} onChange={event => setEditor(current => ({ ...current, chain_key: event.target.value }))} /></Field>
            <Field label="适用周期">
              <select value={editor.timeframe} onChange={event => setEditor(current => ({ ...current, timeframe: event.target.value }))}>
                <option value="*">通用</option>
                {TIMEFRAME_OPTIONS.map(item => <option key={item}>{item}</option>)}
              </select>
            </Field>
            <Field label="规则链名称"><input value={editor.chain_name} onChange={event => setEditor(current => ({ ...current, chain_name: event.target.value }))} /></Field>
            <Field label="优先级"><input type="number" value={editor.priority} onChange={event => setEditor(current => ({ ...current, priority: Number(event.target.value) }))} /></Field>
            <Field label="说明"><input value={editor.description} onChange={event => setEditor(current => ({ ...current, description: event.target.value }))} /></Field>
            <label className="check"><input type="checkbox" checked={editor.enabled} onChange={event => setEditor(current => ({ ...current, enabled: event.target.checked }))} /> 启用为默认候选</label>
          </div>
          <div className="rule-json-editor">
            <div className="json-editor-heading">
              <div>
                <strong>expression_json</strong>
                <span>使用 JSON DSL 组合原子规则，点击下方原子规则可复制 ref 节点。</span>
              </div>
              <span className={`json-status ${jsonStatus.ok ? 'valid' : 'invalid'}`}>{jsonStatus.message}</span>
            </div>
            <div className="json-editor-shell">
              <CodeMirror
                value={editor.expression_text}
                height="360px"
                basicSetup={{
                  lineNumbers: true,
                  foldGutter: true,
                  highlightActiveLine: true,
                  autocompletion: true,
                  bracketMatching: true,
                  closeBrackets: true,
                }}
                extensions={jsonEditorExtensions}
                onChange={(value) => setEditor(current => ({ ...current, expression_text: value }))}
              />
            </div>
            <div className="json-editor-footer">
              <span>常用节点：ref、and、any、all_enabled、any_enabled</span>
              <div className="toolbar-row json-editor-actions">
                <button type="button" className="secondary-button" onClick={formatExpressionJson}>格式化 JSON</button>
                <button type="button" className="primary" disabled={saving || !jsonStatus.ok} onClick={saveChain}>{saving ? '保存中...' : '保存'}</button>
                <button type="button" disabled={saving || !editor.chain_key} onClick={deleteChain}>删除</button>
              </div>
            </div>
          </div>
        </div>
      </Panel>
      <Panel title="原子规则">
        <div className="rule-list-toolbar">
          <Field label="搜索规则">
            <input value={ruleSearch} onChange={event => setRuleSearch(event.target.value)} placeholder="规则名、Key、实现类" />
          </Field>
          <Field label="规则类型">
            <select value={ruleTypeFilter} onChange={event => setRuleTypeFilter(event.target.value)}>
              <option value="all">全部类型</option>
              <option value="filter">硬筛选</option>
              <option value="strategy">策略规则</option>
            </select>
          </Field>
          <Field label="方向">
            <select value={ruleDirectionFilter} onChange={event => setRuleDirectionFilter(event.target.value)}>
              <option value="all">全部方向</option>
              <option value="bullish">看涨</option>
              <option value="bearish">看跌</option>
              <option value="neutral">中性</option>
              <option value="unknown">未标明</option>
            </select>
          </Field>
          <div className="rule-list-summary">共 {ruleRows.length} 条</div>
        </div>
        <div className="pager">
          <button disabled={rulePage <= 0} onClick={() => setRulePage(page => Math.max(0, page - 1))}>上一页</button>
          <span>{rulePage + 1} / {rulePageCount}</span>
          <button disabled={rulePage >= rulePageCount - 1} onClick={() => setRulePage(page => Math.min(rulePageCount - 1, page + 1))}>下一页</button>
        </div>
        {visibleRuleRows.length === 0 ? (
          <div className="empty">暂无匹配规则</div>
        ) : Object.entries(groupedVisibleRuleRows).map(([group, rows]) => (
          <div className="rule-group" key={group}>
            <div className="rule-group-title"><span>{group}</span><small>{rows.length} 条</small></div>
            <Table rows={rows} columns={['rule_key', 'rule_name', 'signal_direction_label', 'rule_type', 'strategy_category', 'implementation', 'enabled', 'display_order']} onRowClick={(row) => insertRuleRef(row.rule_key)} />
          </div>
        ))}
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
          <Metric label="规则链" value={task.chain_name || task.chain_key || '默认链/历史任务'} />
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

function Field({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return <label className={['field', className].filter(Boolean).join(' ')}><span>{label}</span>{children}</label>
}

function FactorList({ factors, citations }: { factors: string[]; citations: FactorCitations }) {
  if (!factors.length) return <>无</>
  return (
    <ul className="factor-list">
      {factors.map(factor => (
        <li key={factor} className="factor-item">
          <span>{factor}</span>
          <CitationTags links={citations[factor] || []} />
        </li>
      ))}
    </ul>
  )
}

function CitationTags({ links }: { links: EvidenceLink[] }) {
  if (!links.length) return null
  return (
    <span className="citation-tags">
      {links.map((link, index) => {
        const url = link.url || ''
        const label = link.label || link.domain || `来源${index + 1}`
        const title = link.title || url || label
        if (!url) return <span key={`${label}-${index}`} className="citation-tag">{label}</span>
        return (
          <a key={`${url}-${index}`} className="citation-tag" href={url} target="_blank" rel="noreferrer" title={title}>
            {label}
          </a>
        )
      })}
    </span>
  )
}

function Table({ rows, columns, onRowClick }: { rows: any[]; columns: string[]; onRowClick?: (row: any) => void }) {
  if (!rows || rows.length === 0) return <div className="empty">暂无数据</div>
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{columns.map(column => <th key={column}>{columnLabel(column)}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} onClick={() => onRowClick?.(row)} className={onRowClick ? 'clickable-row' : ''}>
              {columns.map(column => <td key={column}>{formatCell(row[column], column)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TaskTable({ rows, openTask }: { rows: Task[]; openTask: (taskId: string) => void }) {
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
            <th>规则链</th>
            <th>状态</th>
            <th>进度</th>
            <th>当前股票</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const isWebJob = row.job_type === 'web_job'
            const id = row.task_id
            const stockText = row.current_stock_code || '未补齐'
            const progressText = isWebJob
                ? (row.task_ids && row.task_ids.length > 0 ? `${row.task_ids.length} 个市场任务` : statusLabel(row.status))
              : `${row.completed_count}/${row.total_count}`
            const firstTaskId = row.task_ids && row.task_ids.length > 0 ? row.task_ids[0] : ''
            return (
              <tr key={`${row.job_type || 'screening'}-${id}`}>
                <td>{isWebJob ? '任务组' : '全市场'}</td>
                <td>
                  {isWebJob && !firstTaskId ? (
                    <span title={id}>{id.slice(0, 8)}</span>
                  ) : (
                    <button
                      className="link-button"
                      title={id}
                      onClick={() => openTask(isWebJob ? firstTaskId : id)}
                    >
                      {id.slice(0, 8)}
                    </button>
                  )}
                </td>
                <td>{row.market}</td>
                <td>{row.timeframe}</td>
                <td>{row.chain_name || row.chain_key || '默认链/历史任务'}</td>
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

function parseOptionalNumber(value: string) {
  const trimmed = value.trim()
  if (!trimmed) return undefined
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : undefined
}

function defaultDateTimeInput() {
  const now = new Date()
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset())
  return now.toISOString().slice(0, 16)
}

function splitCodes(value: string) {
  return value
    .split(/[,，\s]+/)
    .map(item => item.trim())
    .filter(Boolean)
}

function normalizeOptionBatchRows(data: any): OptionBatchRow[] {
  if (Array.isArray(data?.items)) return data.items
  if (Array.isArray(data?.results)) return data.results
  if (Array.isArray(data?.evaluations)) return data.evaluations
  if (Array.isArray(data)) return data
  return []
}

function firstBatchCandidate(rows: OptionBatchRow[]) {
  return rows.find(row => row.candidates && row.candidates.length > 0)?.candidates?.[0] || null
}

function hasOptionMacroFields(candidate: OptionCandidate) {
  return candidate.宏观分析评分 !== undefined && candidate.宏观分析评分 !== null
}

function optionMarketLabel(value?: string) {
  return OPTION_MARKET_OPTIONS.find(item => item.value === value)?.label || displayMissing(value)
}

function displayWarnings(value: any) {
  if (Array.isArray(value)) return value.length > 0 ? value.join('；') : '无'
  return displayMissing(value)
}

function asStringList(value: any): string[] {
  if (!value) return []
  if (Array.isArray(value)) return value.map(item => String(item)).filter(Boolean)
  return [String(value)].filter(Boolean)
}

function asEvidenceLinks(value: any, fallbackUrls: string[] = []): EvidenceLink[] {
  const links: EvidenceLink[] = []
  if (typeof value === 'string' && value.trim().startsWith('[')) {
    try {
      value = JSON.parse(value)
    } catch {
      value = []
    }
  }
  if (Array.isArray(value)) {
    value.forEach(item => {
      if (typeof item === 'string') {
        links.push({ label: sourceLabelFromUrl(item), url: item, title: item })
      } else if (item && typeof item === 'object') {
        const link = item as EvidenceLink
        if (link.url || link.label) links.push(link)
      }
    })
  }
  fallbackUrls.forEach(url => {
    if (!links.some(link => link.url === url)) {
      links.push({ label: sourceLabelFromUrl(url), url, title: url })
    }
  })
  return links
}

function asFactorCitations(value: any): FactorCitations {
  if (typeof value === 'string' && value.trim().startsWith('{')) {
    try {
      value = JSON.parse(value)
    } catch {
      value = {}
    }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const result: FactorCitations = {}
  Object.entries(value).forEach(([factor, links]) => {
    result[factor] = asEvidenceLinks(links)
  })
  return result
}

function sourceLabelFromUrl(url: string) {
  try {
    const host = new URL(url).hostname.replace(/^www\./, '')
    if (host.includes('ir.mi.com')) return '小米IR'
    if (host.includes('xiaomi.gcs-web.com')) return '小米公告'
    if (host.includes('hkexnews.hk')) return 'HKEX公告'
    if (host.includes('sec.gov')) return 'SEC'
    if (host.includes('cninfo.com.cn')) return '巨潮资讯'
    if (host.includes('sse.com.cn')) return '上交所公告'
    if (host.includes('szse.cn')) return '深交所公告'
    if (host.includes('arxiv.org')) return 'arXiv'
    if (host.includes('reuters.com')) return 'Reuters'
    if (host.includes('cnbc.com')) return 'CNBC'
    return host.split('.')[0] || '来源'
  } catch {
    return '来源'
  }
}

function pickOptionValue(sources: Record<string, unknown>[], keys: string[]) {
  for (const key of keys) {
    for (const source of sources) {
      const value = source?.[key]
      if (value !== undefined && value !== null && value !== '') return value
    }
  }
  return undefined
}

function optionDetailSummary(candidate: OptionCandidate) {
  const order = candidate.order_suggestion || {}
  const risk = candidate.risk_metrics || {}
  const row: Record<string, unknown> = {
    策略名称: candidate.策略名称,
    期权评分: candidate.期权评分 ?? candidate.评分,
    建议限价: pickOptionValue([order, risk], ['建议限价', 'limit_price', 'suggested_limit_price', 'planned_limit_price']),
    允许滑点: pickOptionValue([order, risk], ['允许滑点', 'allowed_slippage', 'slippage']),
    建议数量: pickOptionValue([order, risk], ['建议数量', 'quantity', 'suggested_quantity']),
    最大亏损: pickOptionValue([risk, order], ['最大亏损', 'max_loss', 'maximum_loss']),
    目标收益: pickOptionValue([risk, order], ['目标收益', 'target_profit', 'target_return']),
    盈亏平衡点: pickOptionValue([risk, order], ['盈亏平衡点', 'breakeven', 'break_even']),
    止损价: pickOptionValue([risk, order], ['止损价', 'stop_loss', 'stop_loss_price']),
    止盈价: pickOptionValue([risk, order], ['止盈价', 'take_profit', 'take_profit_price']),
    计划持有期: pickOptionValue([order, risk], ['计划持有期', 'planned_holding_days', 'max_holding_days']),
    退出条件: pickOptionValue([order, risk], ['退出条件', 'exit_conditions', 'exit_condition'])
  }
  if (hasOptionMacroFields(candidate)) {
    row.宏观分析评分 = candidate.宏观分析评分
    row.综合评分 = candidate.综合评分
    row.宏观方向 = candidate.宏观方向
    row.新闻影响 = candidate.新闻影响
    row.热点匹配 = candidate.热点匹配
    row.主力资金风险 = candidate.主力资金风险
  }
  return row
}

function columnLabel(column: string) {
  return COLUMN_LABELS[column] || column
}

function formatCell(value: any, column?: string): React.ReactNode {
  if (column === 'status' || column === 'is_passed' || column === 'result') return <StatusBadge value={value} />
  if (column === 'market') return optionMarketLabel(value)
  if (column === 'enabled') return typeof value === 'string' ? value : (value ? '启用' : '停用')
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
