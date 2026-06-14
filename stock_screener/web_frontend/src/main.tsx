import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import CodeMirror from '@uiw/react-codemirror'
import { json } from '@codemirror/lang-json'
import { api } from './api'
import { QuantLab } from './features/quant/QuantLab'
import { MarketAnalysisPage } from './features/marketAnalysis/MarketAnalysisPage'
import { StockTerminalPanel, type StockTerminalRow } from './features/stockTerminal/StockTerminalPanel'
import { KlineChart } from './features/marketAnalysis/components/KlineChart'
import { RuleChainEditor } from './features/ruleEditor/RuleChainEditor'
import type { ExpressionNode } from './features/ruleEditor/types'
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
  summary?: Record<string, unknown>
}
type ReportSectionItem = {
  label?: string
  title?: string
  value?: unknown
  status?: string
  reason?: string
  url?: string
  source_type?: string
  rule_type?: string
  strategy_category?: string
  details?: Record<string, unknown>
}
type ReportSection = {
  section_key: string
  title: string
  summary?: string
  items?: ReportSectionItem[]
}
type ScreeningResult = {
  market: string
  code: string
  name?: string
  is_passed: boolean
  sector?: string
  industry?: string
  filter_summary?: string
  filter_details?: FilterDetailRow[]
  close_price?: number
  technical_score?: number
  macro_score?: number
  final_score?: number
  score_details?: Record<string, unknown>
  report_sections?: ReportSection[]
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
type MacroScoreDetails = {
  macro_score?: number
  threshold?: number
  summary?: string
  temporal_summary?: string
  sub_scores?: Record<string, number>
  risks?: string[]
  evidence_refs?: EvidenceLink[]
  temporal_findings?: Array<Record<string, string>>
}
type FilterDetailRow = {
  rule_key?: string
  rule_type?: string
  strategy_category?: string
  filter_name?: string
  result?: string
  reason?: string
  details?: Record<string, unknown>
}
type StockSearchResult = {
  code: string
  name: string
  sector: string
  industry: string
  market_cap: number | null
}
type SingleStockHistoryItem = {
  run_id: string
  market: string
  code: string
  timeframe: string
  passed: boolean | null
  chain_key: string
  chain_name?: string
  status: string
  final_score?: number
  technical_score?: number
  macro_score?: number
  created_at: string | null
  name?: string
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
  final_score: '综合分',
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
  score_details: '评分明细',
  status: '状态',
  macro_score: '宏观分',
  task_id: '任务 ID',
  technical_score: '技术分',
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

const PROGRESS_STEP_ORDER = ['init', 'stock_lookup', 'kline_fetch', 'rule_eval', 'read_result', 'save_result', 'done']
function _progressStepClass(currentStep: string, stepKey: string, status: string): string {
  const curIdx = PROGRESS_STEP_ORDER.indexOf(currentStep)
  const stepIdx = PROGRESS_STEP_ORDER.indexOf(stepKey)
  if (status === 'completed' || (status === 'failed' && stepIdx <= curIdx)) return 'done'
  if (curIdx >= 0 && stepIdx < curIdx) return 'done'
  if (stepKey === currentStep) return 'active'
  return ''
}

function commonRuleChains(markets: string[], rulesByMarket: Record<string, RulesResponse | undefined>): RuleChain[] {
  if (markets.length === 0) return []
  const firstRules = rulesByMarket[markets[0]]
  if (!firstRules?.chains) return []
  return firstRules.chains
    .filter(chain => markets.every(market => (rulesByMarket[market]?.chains || []).some(item => item.chain_key === chain.chain_key && item.timeframe === chain.timeframe)))
    .sort((a, b) => Number(b.enabled) - Number(a.enabled) || a.priority - b.priority || a.chain_key.localeCompare(b.chain_key))
}

function App() {
  const [page, setPage] = useState('marketAnalysis')
  const [selectedTaskId, setSelectedTaskId] = useState('')

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <strong>MoneyManager</strong>
          <span>Stock Screener</span>
        </div>
        <nav>
          <button className={page === 'dashboard' ? 'active' : ''} onClick={() => setPage('dashboard')}>总览</button>
          <button className={page === 'codeScreening' ? 'active' : ''} onClick={() => setPage('codeScreening')}>个股筛选器</button>
          <button className={page === 'options' ? 'active' : ''} onClick={() => setPage('options')}>期权实验室</button>
          <button className={page === 'quant' ? 'active' : ''} onClick={() => setPage('quant')}>量化实验室</button>
          <button className={page === 'marketAnalysis' ? 'active' : ''} onClick={() => setPage('marketAnalysis')}>大盘分析</button>
          <button className={page === 'rules' ? 'active' : ''} onClick={() => setPage('rules')}>规则链</button>
        </nav>
        <div className="sidebar-footer">
          <span>MoneyManager</span>
        </div>
      </aside>
      <main className="content">
        {page === 'dashboard' && <Dashboard
          openTask={(taskId) => { setSelectedTaskId(taskId); setPage('task') }}
        />}
        {page === 'codeScreening' && <CodeScreening openTask={(taskId) => { setSelectedTaskId(taskId); setPage('task') }} />}
        {page === 'options' && <OptionLab />}
        {page === 'quant' && <QuantLab />}
        {page === 'marketAnalysis' && <MarketAnalysisPage />}
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
    <section className="option-lab-scope">
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
    current_stock_code: webJobCurrentText(item),
    chain_key: item.chain_key,
    chain_name: item.chain_name
  }))
  return [...webJobs, ...marketTasks]
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
    .slice(0, 50)
}

function webJobStageLabel(summary?: Record<string, unknown>) {
  const stage = String(summary?.stage || '').trim()
  if (stage === 'screening') return '筛选中'
  if (stage === 'custom_list_screening') return '代码筛选中'
  if (stage === 'single_stock') return '单股筛选中'
  return ''
}

function webJobCurrentText(job: Pick<Job, 'status' | 'summary' | 'task_ids'>) {
  if (job.task_ids && job.task_ids.length > 0) return '已生成筛选任务'
  const currentMarket = String(job.summary?.current_market || '').trim()
  const stageLabel = webJobStageLabel(job.summary)
  if (currentMarket && stageLabel) return `${currentMarket} / ${stageLabel}`
  if (currentMarket) return `执行 ${currentMarket}`
  if (String(job.status).toLowerCase() === 'queued') return '等待任务创建'
  if (String(job.status).toLowerCase() === 'running') return stageLabel || '执行中'
  return '未补齐'
}

function webJobProgressText(row: Task) {
  if (row.task_ids && row.task_ids.length > 0) return `${row.task_ids.length} 个筛选任务`
  return webJobStageLabel(row.summary) || statusLabel(row.status)
}

const MARKET_LABELS: Record<string, string> = { HK: '港股', US: '美股', A: 'A股' }

function CodeScreening({ openTask }: { openTask: (taskId: string) => void }) {
  // ---- form state ----
  const [market, setMarket] = useState('HK')
  const [timeframe, setTimeframe] = useState('1d')
  const [rules, setRules] = useState<RulesResponse | null>(null)
  const [chainKey, setChainKey] = useState('')

  // ---- stock search ----
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<StockSearchResult[]>([])
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchLoading, setSearchLoading] = useState(false)
  const [selectedStock, setSelectedStock] = useState<StockSearchResult | null>(null)

  // ---- screening ----
  const [runId, setRunId] = useState('')
  const [screening, setScreening] = useState(false)
  const [screeningResult, setScreeningResult] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState({ pct: 0, step: '', detail: '', status: 'running' })

  // ---- K-line ----
  const [klineRows, setKlineRows] = useState<Record<string, unknown>[]>([])
  const [klineLoading, setKlineLoading] = useState(false)
  const [klineError, setKlineError] = useState('')
  const [klineDiagnostics, setKlineDiagnostics] = useState<{
    status: string; source: string; error_message?: string
  } | null>(null)

  // ---- report tab ----
  const [reportTab, setReportTab] = useState<'current' | 'history'>('current')
  const [historyRuns, setHistoryRuns] = useState<SingleStockHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)

  // ---- load rules on market / timeframe change ----
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

  // ---- stock name search with debounce ----
  useEffect(() => {
    if (searchQuery.trim().length < 1) {
      setSearchResults([])
      setSearchOpen(false)
      return
    }
    const timer = setTimeout(async () => {
      setSearchLoading(true)
      try {
        const data = await api<{ results: StockSearchResult[] }>(
          `/api/stocks/search?q=${encodeURIComponent(searchQuery.trim())}&market=${market}&limit=10`
        )
        setSearchResults(data.results || [])
        setSearchOpen(true)
      } catch { /* ignore search errors */ }
      finally { setSearchLoading(false) }
    }, 300)
    return () => clearTimeout(timer)
  }, [searchQuery, market])

  // ---- select stock from autocomplete ----
  function selectStock(stock: StockSearchResult) {
    setSelectedStock(stock)
    setSearchQuery(stock.name)
    setSearchOpen(false)
    // reset previous results
    setScreeningResult(null)
    setRunId('')
    setKlineRows([])
    setKlineError('')
    setError('')
    setHistoryRuns([])
  }

  // ---- fetch K-line data ----
  async function fetchKline(code: string) {
    setKlineLoading(true)
    setKlineError('')
    setKlineDiagnostics(null)
    try {
      const data = await api<{
        rows: Array<Record<string, unknown>>
        source_status?: { kline?: { status: string; source: string; error_message?: string; stale?: boolean } }
        data_gaps?: string[]
      }>(
        `/api/stock-terminal/${encodeURIComponent(market)}/${encodeURIComponent(code)}/klines?timeframe=${encodeURIComponent(timeframe)}&limit=200`
      )
      setKlineRows(Array.isArray(data.rows) ? data.rows : [])

      // 保存 K 线数据源诊断信息
      const klineStat = data.source_status?.kline
      if (klineStat) {
        setKlineDiagnostics({
          status: klineStat.status || 'unknown',
          source: klineStat.source || '',
          error_message: klineStat.error_message || (klineStat.stale ? '数据可能已过期' : undefined),
        })
        if (klineStat.status === 'error' || klineStat.status === 'empty') {
          setKlineError(klineStat.error_message || 'K线数据获取失败 — 所有数据源均无返回')
        }
        if ((data.data_gaps || []).includes('kline') && !klineStat.error_message) {
          setKlineError('K线数据缺失 — 当前股票在该周期下无可用数据')
        }
      }
    } catch (err) {
      setKlineError(err instanceof Error ? err.message : '加载K线失败')
      setKlineRows([])
    } finally { setKlineLoading(false) }
  }

  // ---- SSE progress tracking ----
  useEffect(() => {
    if (!runId || !screening) return
    let cancelled = false
    const es = new EventSource(`/api/screening/single-stock/${runId}/progress`)

    es.onmessage = (event) => {
      if (cancelled) return
      try {
        const data = JSON.parse(event.data)
        setProgress({ pct: data.pct || 0, step: data.step || '', detail: data.detail || '', status: data.status || 'running' })

        if (data.status === 'completed' || data.status === 'failed') {
          es.close()
          // Fetch the full result
          api<Record<string, unknown>>(`/api/screening/single-stock/${runId}`)
            .then(resultData => {
              if (!cancelled) {
                setScreening(false)
                setScreeningResult(resultData)
                if (resultData.rule_details) {
                  setScreeningResult(prev => ({ ...prev, rule_details: resultData.rule_details }))
                }
              }
            })
            .catch(() => { if (!cancelled) setScreening(false) })
        }
      } catch { /* ignore parse errors */ }
    }

    es.onerror = () => {
      // Fallback: try polling if SSE fails
      es.close()
      if (cancelled) return
      let pollCount = 0
      const pollTimer = window.setInterval(async () => {
        if (cancelled) { window.clearInterval(pollTimer); return }
        pollCount++
        try {
          const data = await api<Record<string, unknown>>(`/api/screening/single-stock/${runId}`)
          if (cancelled) return
          const status = String(data.status || '')
          if (status === 'completed' || status === 'failed') {
            window.clearInterval(pollTimer)
            setScreening(false)
            setScreeningResult(data)
            if (data.rule_details) {
              setScreeningResult(prev => ({ ...prev, rule_details: data.rule_details }))
            }
          } else if (pollCount > 30) {
            window.clearInterval(pollTimer)
            setScreening(false)
            setError('筛选任务超时，请稍后查看历史记录')
          }
        } catch { /* ignore */ }
      }, 2000)
    }

    return () => { cancelled = true; es.close() }
  }, [runId, screening])

  // ---- submit screening ----
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setError('')
    setScreeningResult(null)
    setRunId('')
    setKlineRows([])
    setKlineError('')
    setProgress({ pct: 0, step: '', detail: '', status: 'running' })

    if (!selectedStock) {
      setError('请先搜索并选择一只股票')
      return
    }
    setScreening(true)

    // 1. fetch K-line immediately
    fetchKline(selectedStock.code)

    // 2. create single-stock screening run (web mode)
    try {
      const data = await api<{ run_id: string; status: string }>('/api/screening/single-stock', {
        method: 'POST',
        body: JSON.stringify({
          market,
          code: selectedStock.code,
          timeframe,
          chain_key: chainKey || undefined,
          mode: 'web',  // run immediately in web backend thread
        })
      })
      setRunId(data.run_id || '')
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建筛选任务失败')
      setScreening(false)
    }
  }

  // ---- load history ----
  async function loadHistory() {
    if (!selectedStock) return
    setHistoryLoading(true)
    try {
      const data = await api<{ history: SingleStockHistoryItem[] }>(
        `/api/screening/single-stock/history/${encodeURIComponent(selectedStock.code)}?market=${market}&limit=20`
      )
      setHistoryRuns(data.history || [])
    } catch { /* ignore */ }
    finally { setHistoryLoading(false) }
  }

  useEffect(() => {
    if (reportTab === 'history' && selectedStock && historyRuns.length === 0) {
      loadHistory()
    }
  }, [reportTab, selectedStock?.code])

  const selectedChain = (rules?.chains || []).find(item => item.chain_key === chainKey) || rules?.chain
  const ruleDetails: FilterDetailRow[] = (screeningResult?.rule_details as FilterDetailRow[]) || []
  const resultJson = (screeningResult?.result_json || screeningResult || {}) as Record<string, unknown>

  return (
    <section className="code-screening-layout">
      <Header title="个股筛选器" subtitle="输入股票名称，一键筛选并查看K线与分析报告" />

      {/* ---- search form ---- */}
      <form className="compact-form-grid" onSubmit={submit}>
        <Field label="市场">
          <select value={market} onChange={event => { setMarket(event.target.value); setSelectedStock(null); setSearchQuery('') }}>
            {Object.entries(MARKET_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
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
                {item.chain_name} {item.enabled ? '(默认)' : ''}
              </option>
            ))}
          </select>
        </Field>
        <Field label="股票名称" className="field-wide">
          <div className="stock-search">
            <input
              value={searchQuery}
              onChange={event => setSearchQuery(event.target.value)}
              onFocus={() => { if (searchResults.length > 0) setSearchOpen(true) }}
              onBlur={() => setTimeout(() => setSearchOpen(false), 200)}
              placeholder="输入股票名称，如：腾讯 / Apple / 茅台"
              autoComplete="off"
            />
            {searchLoading && <span className="search-spinner" />}
            {searchOpen && searchResults.length > 0 && (
              <div className="search-dropdown">
                {searchResults.map(r => (
                  <div className="search-item" key={r.code} onMouseDown={() => selectStock(r)}>
                    <span className="stock-name">{r.name}</span>
                    <span className="stock-code">{r.code}</span>
                    <span className="stock-sector">{r.sector || r.industry || ''}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Field>
        <div className="code-preview-actions">
          <button className="primary" disabled={screening || !selectedStock || (rules?.chains || []).length === 0}>
            {screening ? '筛选中...' : '开始筛选'}
          </button>
        </div>
      </form>

      {/* ---- selected stock info ---- */}
      {selectedStock && (
        <div className="metric-grid">
          <Metric label="股票名称" value={selectedStock.name} />
          <Metric label="股票代码" value={
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontFamily: 'ui-monospace, monospace' }}>{selectedStock.code}</span>
              <button className="link-button" style={{ fontSize: 11, padding: 0, minHeight: 20 }}
                onClick={() => { navigator.clipboard?.writeText(selectedStock.code).catch(() => undefined) }}
                title="复制代码">📋</button>
            </span>
          } />
          <Metric label="市场" value={MARKET_LABELS[market] || market} />
          <Metric label="板块" value={
            <span
              title={(selectedStock.sector || selectedStock.industry) || undefined}
              style={{ overflow: 'hidden', textOverflow: 'ellipsis', display: 'block' }}
            >{selectedStock.sector || selectedStock.industry || '-'}</span>
          } />
          <Metric label="规则链" value={
            <span
              title={chainDisplay(selectedChain)}
              style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' }}
            >{chainDisplay(selectedChain)}</span>
          } />
          <Metric label="周期" value={timeframe} />
        </div>
      )}

      {error && (
        <ErrorRecoveryCard
          error={error}
          failedStep={progress.step}
          screeningResult={screeningResult}
          onRetry={() => { submit(new Event('retry') as any) }}
          onTechOnly={() => { setChainKey(''); submit(new Event('techonly') as any) }}
          onSwitchTimeframe={(tf) => { setTimeframe(tf); }}
          currentTimeframe={timeframe}
        />
      )}

      {/* ---- K-line chart ---- */}
      {selectedStock && (
        <div className="kline-panel-wrap">
          <KlineChart
            rows={klineRows}
            loading={klineLoading}
            error={klineError}
            diagnostics={klineDiagnostics}
            timeframe={timeframe as any}
            symbol={`${selectedStock.name} (${selectedStock.code})`}
          />
        </div>
      )}

      {/* ---- progress bar ---- */}
      {screening && (
        <div className="screening-progress">
          <div className="screening-progress-bar">
            <div
              className={`screening-progress-fill ${progress.status === 'completed' ? 'done' : progress.status === 'failed' ? 'failed' : ''}`}
              style={{ width: `${progress.pct > 0 ? progress.pct : 5}%` }}
            />
          </div>
          <span className="screening-progress-label">
            {progress.detail || '正在初始化...'}
            {progress.pct > 0 && ` (${progress.pct}%)`}
          </span>
          <div className="screening-progress-steps">
            {[
              { key: 'init', label: '初始化' },
              { key: 'stock_lookup', label: '查询股票' },
              { key: 'kline_fetch', label: '获取K线' },
              { key: 'rule_eval', label: '执行规则' },
              { key: 'read_result', label: '读取结果' },
              { key: 'save_result', label: '保存报告' },
            ].map(s => (
              <span key={s.key} className={`progress-step ${_progressStepClass(progress.step, s.key, progress.status)}`}>
                {s.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ---- data diagnostic panel ---- */}
      {screeningResult && (
        <DataDiagnosticPanel
          selectedStock={selectedStock}
          klineDiagnostics={klineDiagnostics}
          klineRows={klineRows}
          screeningResult={screeningResult}
          progress={progress}
        />
      )}

      {/* ---- report panel ---- */}
      {screeningResult && (
        <Panel title="筛选报告">
          {/* tabs */}
          <div className="report-tabs">
            <button className={`report-tab ${reportTab === 'current' ? 'active' : ''}`} onClick={() => setReportTab('current')}>
              📊 本次结果
            </button>
            <button className={`report-tab ${reportTab === 'history' ? 'active' : ''}`} onClick={() => setReportTab('history')}>
              📋 历史记录
            </button>
          </div>

          {reportTab === 'current' && (
            <>
              {/* P0-2: 评分概览卡片 */}
              <ScoreOverviewCards ruleDetails={ruleDetails} resultJson={resultJson} screeningResult={screeningResult} />

              <div className="metric-grid" style={{ marginTop: 16 }}>
                <Metric label="筛选状态" value={<StatusBadge value={screeningResult.passed ? 'passed' : 'failed'} />} />
                <Metric label="数据来源" value={dataSourceLabel(screeningResult.data_source)} />
                <Metric label="完成时间" value={displayMissing(screeningResult.finished_at)} />
                <Metric label="规则链" value={chainDisplay(selectedChain)} />
              </div>

              {/* P1-5: score formula visualization */}
              {ruleDetails.length > 0 && resultJson.final_score != null && (
                <ScoreFormulaDisplay ruleDetails={ruleDetails} resultJson={resultJson} />
              )}

              {/* scoring detail */}
              {(screeningResult.score_details || screeningResult.filter_details) && (
                <div style={{ marginTop: 16 }}>
                  <h3 style={{ color: '#e8e0d0', margin: '0 0 12px' }}>规则明细</h3>
                  {ruleDetails.length > 0 ? (
                    <table className="data-table" style={{ width: '100%' }}>
                      <thead>
                        <tr>
                          <th>规则</th>
                          <th>类型</th>
                          <th>结果</th>
                          <th>原因</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ruleDetails.map((d, i) => (
                          <tr key={i}>
                            <td style={{ color: '#e8e0d0' }}>{d.rule_key || d.filter_name || '-'}</td>
                            <td>{d.rule_type || d.strategy_category || '-'}</td>
                            <td><StatusBadge value={d.result || ''} /></td>
                            <td style={{ color: '#c0b8a0', maxWidth: 320 }}>{d.reason || '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="empty">暂无规则明细数据</div>
                  )}
                </div>
              )}

              {/* filter_details array */}
              {!ruleDetails.length && Array.isArray(screeningResult.filter_details) && (screeningResult.filter_details as FilterDetailRow[]).length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <h3 style={{ color: '#e8e0d0', margin: '0 0 12px' }}>筛选详情</h3>
                  <table className="data-table" style={{ width: '100%' }}>
                    <thead>
                      <tr><th>规则</th><th>结果</th><th>原因</th></tr>
                    </thead>
                    <tbody>
                      {(screeningResult.filter_details as FilterDetailRow[]).map((d, i) => (
                        <tr key={i}>
                          <td style={{ color: '#e8e0d0' }}>{d.rule_key || d.filter_name || '-'}</td>
                          <td><StatusBadge value={d.result || ''} /></td>
                          <td style={{ color: '#c0b8a0' }}>{d.reason || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* ai analysis summary */}
              {(screeningResult.ai_analysis as Record<string, unknown>) && (
                <div style={{ marginTop: 16 }}>
                  <h3 style={{ color: '#e8e0d0', margin: '0 0 12px' }}>AI 分析摘要</h3>
                  <div className="ai-analysis-box">
                    {Object.entries(screeningResult.ai_analysis as Record<string, unknown>).map(([k, v]) => (
                      <div key={k} style={{ marginBottom: 8 }}>
                        <strong style={{ color: '#c9a84c' }}>{k}：</strong>
                        <span style={{ color: '#c0b8a0' }}>{typeof v === 'string' ? v : JSON.stringify(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {reportTab === 'history' && (
            <div style={{ marginTop: 16 }}>
              {historyLoading ? (
                <div className="empty">加载中...</div>
              ) : historyRuns.length === 0 ? (
                <div className="empty">
                  <div style={{ marginBottom: 8 }}>暂无历史筛选记录</div>
                  <small style={{ color: '#605850' }}>选择股票并点击"开始筛选"后，结果将自动保存在这里</small>
                </div>
              ) : (
                <table className="data-table" style={{ width: '100%' }}>
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>规则链</th>
                      <th>结果</th>
                      <th>综合分</th>
                      <th>失败原因</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {historyRuns.map(r => (
                      <tr key={r.run_id} style={{ cursor: 'pointer' }} onClick={async () => {
                        try {
                          const data = await api<Record<string, unknown>>(`/api/screening/single-stock/${r.run_id}`)
                          setScreeningResult(data)
                          setReportTab('current')
                        } catch { /* ignore */ }
                      }}>
                        <td style={{ color: '#e8e0d0', whiteSpace: 'nowrap' }}>{String(r.created_at || '').replace('T', ' ').slice(0, 16)}</td>
                        <td style={{ color: '#c9a84c', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }} title={r.chain_name || r.chain_key}>{r.chain_name || r.chain_key || '-'}</td>
                        <td><StatusBadge value={r.passed ? 'passed' : 'failed'} /></td>
                        <td style={{ color: r.final_score != null ? '#e8c560' : '#8a8070', fontWeight: r.final_score != null ? 700 : 400 }}>
                          {r.final_score != null ? Number(r.final_score).toFixed(1) : '—'}
                        </td>
                        <td style={{ color: '#ef4444', fontSize: 12, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {r.status === 'failed' || r.passed === false ? (r.status === 'failed' ? '执行失败' : '未通过筛选') : '-'}
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          <button
                            className="link-button"
                            style={{ fontSize: 12, marginRight: 8 }}
                            onClick={(e) => {
                              e.stopPropagation()
                              setMarket(r.market)
                              setTimeframe(r.timeframe)
                              setChainKey(r.chain_key)
                              if (selectedStock?.code === r.code) {
                                setTimeout(() => submit(new Event('history_retry') as any), 100)
                              }
                            }}
                            title="使用相同参数重新筛选"
                          >🔄 重筛</button>
                          <button
                            className="link-button"
                            style={{ fontSize: 12 }}
                            onClick={(e) => {
                              e.stopPropagation()
                              const params = JSON.stringify({
                                market: r.market, code: r.code, timeframe: r.timeframe,
                                chain_key: r.chain_key, chain_name: r.chain_name || '',
                              }, null, 2)
                              navigator.clipboard?.writeText(params).catch(() => undefined)
                            }}
                            title="复制筛选参数"
                          >📋</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </Panel>
      )}
    </section>
  )
}

function CodeScreeningResultTable({
  rows,
  terminalMarket,
  taskId,
  openTask,
  selectedCode,
  onSelectRow
}: {
  rows: CustomListResultRow[]
  terminalMarket: string
  taskId?: string
  openTask: (taskId: string) => void
  selectedCode?: string
  onSelectRow: (row: CustomListResultRow) => void
}) {
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
            <th>技术分</th>
            <th>宏观分</th>
            <th>综合分</th>
            <th>筛选摘要</th>
            <th>原因</th>
            <th>任务详情</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const rowTaskId = row.task_id || taskId || ''
            const terminalRow = normalizeCodeScreeningTerminalRow(row, terminalMarket)
            const isSelected = Boolean(terminalRow.code && terminalRow.code === selectedCode)
            return (
              <tr
                key={`${row.input || row.code || 'row'}-${index}`}
                className={`${isSelected ? 'selected-row ' : ''}clickable-row`}
                onClick={() => onSelectRow(row)}
              >
                <td>{displayMissing(row.input || row.code)}</td>
                <td>{displayMissing(row.code)}</td>
                <td>{displayMissing(row.name)}</td>
                <td><span className={`status-badge ${codeScreeningStatusClass(row)}`}>{codeScreeningStatusLabel(row)}</span></td>
                <td><StatusBadge value={codeScreeningResultValue(row)} /></td>
                <td>{displayMissing(row.sector)}</td>
                <td>{displayMissing(row.industry)}</td>
                <td>{displayMissing(row.close_price)}</td>
                <td>{formatScore(row.technical_score)}</td>
                <td>{formatScore(row.macro_score)}</td>
                <td>{formatScore(row.final_score)}</td>
                <td>{displayMissing(row.filter_summary)}</td>
                <td>{displayMissing(row.reason)}</td>
                <td>{rowTaskId ? <button className="link-button" onClick={event => { event.stopPropagation(); openTask(rowTaskId) }}>{rowTaskId.slice(0, 8)}</button> : '等待创建'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const REPORT_SECTION_TITLES: Record<string, string> = {
  decision: '筛选结论',
  failure_reasons: '关键未通过原因',
  strategy_process: '策略过程',
  score_breakdown: '评分拆解',
  macro_evidence: '宏观证据',
}

function CodeScreeningReportPanel({ row }: { row?: CustomListResultRow | null }) {
  if (!row) {
    return (
      <Panel title="分析报告">
        <div className="empty">选择结果行查看未通过原因、分析思路和每条策略过程</div>
      </Panel>
    )
  }
  const sections = row.report_sections || []
  return (
    <Panel title={`分析报告 ${displayMissing(row.code || row.input)}`}>
      {sections.length === 0 ? (
        <div className="empty">暂无报告明细</div>
      ) : (
        <div className="code-report">
          {sections.map(section => (
            <ReportSectionBlock section={section} key={section.section_key || section.title} />
          ))}
        </div>
      )}
    </Panel>
  )
}

function ReportSectionBlock({ section }: { section: ReportSection }) {
  const items = section.items || []
  return (
    <section className={`code-report-section section-${section.section_key || 'default'}`}>
      <header>
        <h3>{section.title || REPORT_SECTION_TITLES[section.section_key] || '报告明细'}</h3>
      </header>
      {section.summary && <p className="code-report-summary">{section.summary}</p>}
      {items.length > 0 && (
        <div className="code-report-items">
          {items.map((item, index) => (
            <article className="code-report-item" key={`${item.title || item.label || 'item'}-${index}`}>
              <div className="code-report-item-main">
                <strong>{item.title || item.label || `明细 ${index + 1}`}</strong>
                {item.status && <StatusBadge value={item.status} />}
              </div>
              {item.label && item.title && <span className="code-report-label">{item.label}</span>}
              {item.value !== undefined && (
                item.url
                  ? <a href={String(item.url)} target="_blank" rel="noreferrer">{formatReportValue(item.value)}</a>
                  : <span>{formatReportValue(item.value)}</span>
              )}
              {item.reason && <p>{item.reason}</p>}
              {item.details && Object.keys(item.details).length > 0 && (
                <dl className="code-report-details">
                  {Object.entries(item.details).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{formatReportValue(value)}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

function normalizeCodeScreeningTerminalRow(row: CustomListResultRow, fallbackMarket: string): StockTerminalRow {
  const code = (row.code || row.input || '').trim()
  return {
    ...row,
    code,
    market: row.market || inferMarketFromCode(code) || fallbackMarket
  }
}

function inferMarketFromCode(code?: string) {
  const value = String(code || '').trim().toUpperCase()
  if (value.startsWith('US.')) return 'US'
  if (value.startsWith('HK.')) return 'HK'
  if (value.startsWith('SH.') || value.startsWith('SZ.') || value.startsWith('BJ.')) return 'A'
  return ''
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

function isTerminalTaskStatus(status?: string | null) {
  return ['completed', 'failed', 'error', 'cancelled'].includes(String(status || '').toLowerCase())
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
  const [editMode, setEditMode] = useState<'visual' | 'json'>('visual')
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

  // Parse current expression_text into typed object for visual editor
  const visualExpression = useMemo<ExpressionNode | null>(() => {
    try {
      return JSON.parse(editor.expression_text) as ExpressionNode
    } catch {
      return null
    }
  }, [editor.expression_text])

  // Called when visual editor modifies the expression
  function handleVisualExpressionChange(expr: ExpressionNode) {
    setEditor(current => ({
      ...current,
      expression_text: JSON.stringify(expr, null, 2),
    }))
  }

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
      <Header title="规则链" subtitle="拖拽节点编排规则链，或切换到 JSON 模式直接编辑表达式" />
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
        <button type="button" className="secondary-button" onClick={startNewChain}>新建</button>
      </div>
      <div className="table-note">
        {editMode === 'visual'
          ? '拖拽左侧规则到画布，用 AND/OR 逻辑门连接。改动自动同步到 JSON 表达式。'
          : '使用 JSON DSL 组合原子规则，可从下方原子规则复制 ref 节点。'}
      </div>
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
        {/* Mode toggle */}
        <div className="rule-editor-mode-bar">
          <div className="segmented">
            <button type="button" className={editMode === 'visual' ? 'selected' : ''} onClick={() => setEditMode('visual')}>
              🎨 可视化编排
            </button>
            <button type="button" className={editMode === 'json' ? 'selected' : ''} onClick={() => setEditMode('json')}>
              📝 JSON 编辑
            </button>
          </div>
        </div>

        {/* Metadata fields — shared between modes */}
        <div className="rule-editor-fields" style={{ marginTop: 14 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, alignItems: 'end' }}>
            <Field label="规则链 Key"><input value={editor.chain_key} onChange={event => setEditor(current => ({ ...current, chain_key: event.target.value }))} /></Field>
            <Field label="适用周期">
              <select value={editor.timeframe} onChange={event => setEditor(current => ({ ...current, timeframe: event.target.value }))}>
                <option value="*">通用</option>
                {TIMEFRAME_OPTIONS.map(item => <option key={item}>{item}</option>)}
              </select>
            </Field>
            <Field label="规则链名称"><input value={editor.chain_name} onChange={event => setEditor(current => ({ ...current, chain_name: event.target.value }))} /></Field>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 12, alignItems: 'end', marginTop: 10 }}>
            <Field label="优先级"><input type="number" value={editor.priority} onChange={event => setEditor(current => ({ ...current, priority: Number(event.target.value) }))} /></Field>
            <Field label="说明"><input value={editor.description} onChange={event => setEditor(current => ({ ...current, description: event.target.value }))} /></Field>
            <label className="check"><input type="checkbox" checked={editor.enabled} onChange={event => setEditor(current => ({ ...current, enabled: event.target.checked }))} /> 启用为默认候选</label>
          </div>
        </div>

        {/* Visual editor */}
        {editMode === 'visual' && (
          <div className="rule-visual-editor-wrap" style={{ marginTop: 14 }}>
            <RuleChainEditor
              rules={(rules?.metadata || []) as any[]}
              expression={visualExpression}
              onExpressionChange={handleVisualExpressionChange}
            />
          </div>
        )}

        {/* JSON editor */}
        {editMode === 'json' && (
          <div className="rule-json-editor" style={{ marginTop: 14 }}>
            <div className="json-editor-heading">
              <div>
                <strong>expression_json</strong>
                <span>使用 JSON DSL 组合原子规则。节点：ref、and、any、all_enabled、any_enabled</span>
              </div>
              <span className={`json-status ${jsonStatus.ok ? 'valid' : 'invalid'}`}>{jsonStatus.message}</span>
            </div>
            <div className="json-editor-shell">
              <CodeMirror
                value={editor.expression_text}
                height="300px"
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
              </div>
            </div>
          </div>
        )}

        {/* Save / Delete bar — shared */}
        <div className="toolbar-row" style={{ marginTop: 14, justifyContent: 'flex-end', gap: 8 }}>
          <span className={`json-status ${jsonStatus.ok ? 'valid' : 'invalid'}`} style={{ marginRight: 'auto' }}>
            {jsonStatus.message}
          </span>
          <button type="button" className="primary" disabled={saving || !jsonStatus.ok} onClick={saveChain}>
            {saving ? '保存中...' : '保存'}
          </button>
          <button type="button" className="secondary-button" disabled={saving || !editor.chain_key} onClick={deleteChain}>删除</button>
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
  const [selectedResult, setSelectedResult] = useState<ScreeningResult | null>(null)
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
  }, [taskId, limit, offset, passedOnly])

  useEffect(() => {
    if (!taskId) return
    if (isTerminalTaskStatus(task?.status)) return
    const timer = window.setInterval(() => refresh().catch(console.error), 8000)
    return () => window.clearInterval(timer)
  }, [taskId, limit, offset, passedOnly, task?.status])

  useEffect(() => {
    if (results.length === 0) {
      setSelectedResult(null)
      return
    }
    if (selectedResult && !results.some(row => row.code === selectedResult.code && row.market === selectedResult.market)) {
      setSelectedResult(null)
    }
  }, [results, selectedResult])

  if (!taskId) {
    return <section><Header title="任务详情" subtitle="请选择一个筛选任务" /></section>
  }

  const filteredTotal = passedOnly ? passedCount : totalCount
  const currentStart = filteredTotal === 0 ? 0 : offset + 1
  const currentEnd = Math.min(offset + results.length, filteredTotal)
  const canPrev = offset > 0
  const canNext = offset + limit < filteredTotal
  const selectedMacroDetails = selectedResult ? macroScoreDetailsFromResult(selectedResult) : null
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
        <div className="metric-grid task-summary-grid">
          <Metric label="市场" value={task.market} />
          <Metric label="规则链" value={task.chain_name || task.chain_key || '默认链/历史任务'} className="task-summary-rule" />
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
          <div className="table-note">该任务为结果轻量存储模式：云端只保存通过股票明细，失败股票只计入统计。</div>
        )}
        <div className="pager">
          <button type="button" disabled={!canPrev} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</button>
          <span>第 {filteredTotal === 0 ? 0 : Math.floor(offset / limit) + 1} 页</span>
          <button type="button" disabled={!canNext} onClick={() => setOffset(offset + limit)}>下一页</button>
        </div>
        <Table
          rows={results}
          columns={['is_passed', 'code', 'name', 'sector', 'industry', 'close_price', 'technical_score', 'macro_score', 'final_score', 'filter_summary']}
          onRowClick={row => setSelectedResult(row)}
        />
      </Panel>
      <Panel title={selectedResult ? `评分明细 ${selectedResult.code}` : '评分明细'}>
        {selectedResult ? (
          <>
            <div className="metric-grid">
              <Metric label="技术分" value={formatScore(selectedResult.technical_score)} />
              <Metric label="宏观分" value={formatScore(selectedResult.macro_score)} />
              <Metric label="综合分" value={formatScore(selectedResult.final_score)} />
            </div>
            <MacroScoreDetails details={selectedMacroDetails} />
          </>
        ) : (
          <div className="empty">选择结果行查看评分明细</div>
        )}
      </Panel>
    </section>
  )
}

function Header({ title, subtitle }: { title: string; subtitle: string }) {
  return <header className="page-header"><h1>{title}</h1><p>{subtitle}</p></header>
}

function Metric({ label, value, className = '' }: { label: string; value: React.ReactNode; className?: string }) {
  return <div className={['metric', className].filter(Boolean).join(' ')}><span>{label}</span><strong>{value}</strong></div>
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

function MacroScoreDetails({ details }: { details?: MacroScoreDetails | null }) {
  if (!details || details.macro_score === undefined || details.macro_score === null) return <div className="empty">暂无宏观评分明细</div>
  const subScores = Object.entries(details.sub_scores || {})
  const risks = details.risks || []
  const temporalFindings = details.temporal_findings || []
  return (
    <div className="macro-score-card">
      <div className="macro-score-heading">
        <strong>宏观评分 {formatScore(details.macro_score)}</strong>
        <span>阈值 {formatScore(details.threshold)}</span>
      </div>
      {details.summary && <p>{details.summary}</p>}
      {details.temporal_summary && <p className="temporal-summary">{details.temporal_summary}</p>}
      {subScores.length > 0 && (
        <div className="subscore-grid">
          {subScores.map(([key, value]) => (
            <div className="subscore-row" key={key}>
              <span>{macroDimensionLabel(key)}</span>
              <meter min={-100} max={100} low={0} high={60} optimum={80} value={Number(value)} />
              <strong>{formatScore(value)}</strong>
            </div>
          ))}
        </div>
      )}
      {risks.length > 0 && (
        <ul className="risk-list">
          {risks.map(item => <li key={item}>{item}</li>)}
        </ul>
      )}
      {temporalFindings.length > 0 && (
        <div className="temporal-finding-list">
          {temporalFindings.map((item, index) => (
            <div className="temporal-finding-row" key={`${item.type || 'finding'}-${index}`}>
              <strong>{displayMissing(item.type)}</strong>
              <span>{displayMissing(item.description)}</span>
            </div>
          ))}
        </div>
      )}
      <CitationTags links={details.evidence_refs || []} />
    </div>
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
              ? webJobProgressText(row)
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

type DataStatus = 'computed' | 'not_computed' | 'missing' | 'error'

const DATA_STATUS_LABELS: Record<DataStatus, string> = {
  computed: '已计算',
  not_computed: '未计算',
  missing: '数据缺失',
  error: '接口失败',
}

function DataStatusBadge({ status }: { status: DataStatus }) {
  return <span className={`data-status ${status}`}>{DATA_STATUS_LABELS[status]}</span>
}

/** 根据值和后端状态推断数据完整性 */
function inferDataStatus(value: unknown, meta?: { status?: string; error?: string }): DataStatus {
  if (meta?.status === 'error' || meta?.error) return 'error'
  if (meta?.status === 'missing' || value === undefined || value === null || value === '') return 'not_computed'
  return 'computed'
}

/** 错误恢复建议 */
interface ErrorSuggestion {
  label: string
  action: string
}
function analyzeErrorSuggestions(
  error: string,
  failedStep: string,
  screeningResult: Record<string, unknown> | null,
  currentTimeframe: string,
): ErrorSuggestion[] {
  const suggestions: ErrorSuggestion[] = []

  // 重试总是可用
  suggestions.push({ label: '🔄 重新筛选', action: 'retry' })

  // K线相关错误 → 建议切换周期
  if (
    error.includes('K-line') || error.includes('K线') || error.includes('kline') ||
    error.includes('kline') || error.includes('数据不足') || error.includes('returned empty') ||
    failedStep === 'kline_fetch'
  ) {
    const altTimeframes = ['1d', '1wk', '1mo'].filter(tf => tf !== currentTimeframe)
    for (const tf of altTimeframes) {
      const label = tf === '1d' ? '日线(1d)' : tf === '1wk' ? '周线(1wk)' : '月线(1mo)'
      suggestions.push({ label: `📅 切换至${label}`, action: `switch_${tf}` })
    }
  }

  // 规则执行失败 → 建议仅技术面
  if (error.includes('rule') || error.includes('规则') || failedStep === 'rule_eval') {
    suggestions.push({ label: '⚙️ 仅执行技术面分析', action: 'tech_only' })
  }

  // 宏观/外部API 错误 → 建议跳过宏观
  if (error.includes('macro') || error.includes('宏观') || error.includes('Tavily') || error.includes('timeout')) {
    if (!suggestions.some(s => s.action === 'tech_only')) {
      suggestions.push({ label: '⚙️ 跳过宏观数据', action: 'tech_only' })
    }
  }

  suggestions.push({ label: '📋 复制错误详情', action: 'copy' })

  return suggestions
}

/** ── Score overview cards (P0-2) ── */
interface ScoreCardProps {
  title: string
  score: string | number
  scoreStatus: DataStatus
  weightLabel: string
  passCount: number
  failCount: number
  unknownCount: number
  missingCount: number
  highlight?: boolean
  direction?: string
}

function ScoreCard({ title, score, scoreStatus, weightLabel, passCount, failCount, unknownCount, missingCount, highlight, direction }: ScoreCardProps) {
  const dirLabel = direction === 'bullish' ? '🟢 看涨' : direction === 'bearish' ? '🔴 看跌' : ''
  return (
    <div className={`score-card${highlight ? ' score-card-highlight' : ''}`}>
      <div className="score-card-head">
        <span className="score-card-title">{title}</span>
        <span className="score-card-weight">{weightLabel}</span>
      </div>
      <div className="score-card-value">
        <span className="score-number">{score}</span>
        <DataStatusBadge status={scoreStatus} />
      </div>
      {dirLabel && <div className="score-card-direction">{dirLabel}</div>}
      <div className="score-card-counts">
        <span className="sc-pass">{passCount} 通过</span>
        <span className="sc-fail">{failCount} 未通过</span>
        {unknownCount > 0 && <span className="sc-unknown">{unknownCount} 无法计算</span>}
        {missingCount > 0 && <span className="sc-missing">{missingCount} 缺失</span>}
      </div>
    </div>
  )
}

function ScoreOverviewCards({ ruleDetails, resultJson, screeningResult }: {
  ruleDetails: FilterDetailRow[]
  resultJson: Record<string, unknown>
  screeningResult: Record<string, unknown> | null
}) {
  // 按 strategy_category 分组统计
  const techRules = ruleDetails.filter(d => d.strategy_category === 'technical' || !d.strategy_category)
  const macroRules = ruleDetails.filter(d => d.strategy_category === 'macro')

  function counts(rules: FilterDetailRow[]) {
    return {
      pass: rules.filter(d => d.result === 'pass').length,
      fail: rules.filter(d => d.result === 'fail').length,
      unknown: rules.filter(d => d.result !== 'pass' && d.result !== 'fail').length,
      missing: 0,
    }
  }
  const techCounts = counts(techRules)
  const macroCounts = counts(macroRules)

  const finalScore = formatScoreWithStatus(resultJson.final_score)
  const techScore = formatScoreWithStatus(resultJson.technical_score)
  const macroScore = formatScoreWithStatus(resultJson.macro_score)

  // 推断方向（从final_score或ai_analysis）
  const aiAnalysis = screeningResult?.ai_analysis as Record<string, unknown> | undefined
  const direction = (aiAnalysis?.signal_bias as string) || ''

  return (
    <div className="score-overview-grid" style={{ marginTop: 16 }}>
      <ScoreCard
        title="综合评分" score={finalScore.display} scoreStatus={finalScore.status}
        weightLabel="100%" passCount={techCounts.pass + macroCounts.pass}
        failCount={techCounts.fail + macroCounts.fail}
        unknownCount={techCounts.unknown + macroCounts.unknown}
        missingCount={techCounts.missing + macroCounts.missing}
        highlight direction={direction}
      />
      <ScoreCard
        title="技术维度" score={techScore.display} scoreStatus={techScore.status}
        weightLabel="权重 40%"
        passCount={techCounts.pass} failCount={techCounts.fail}
        unknownCount={techCounts.unknown} missingCount={techCounts.missing}
      />
      <ScoreCard
        title="宏观维度" score={macroScore.display} scoreStatus={macroScore.status}
        weightLabel="权重 30%"
        passCount={macroCounts.pass} failCount={macroCounts.fail}
        unknownCount={macroCounts.unknown} missingCount={macroCounts.missing}
      />
      <ScoreCard
        title="资金风险" score="—" scoreStatus="not_computed"
        weightLabel="权重 20%" passCount={0} failCount={0}
        unknownCount={0} missingCount={1}
      />
    </div>
  )
}

/** ── Score formula visualization (P1-5) ── */
const STRATEGY_CATEGORY_LABELS: Record<string, string> = {
  technical: '技术面', macro: '宏观面', event_hot: '事件热度', capital_risk: '资金风险',
}

function ScoreFormulaDisplay({ ruleDetails, resultJson }: {
  ruleDetails: FilterDetailRow[]
  resultJson: Record<string, unknown>
}) {
  // 找出贡献非零的规则
  const contributing = ruleDetails
    .filter(d => d.result === 'pass' && d.details)
    .map(d => {
      const score = (d.details as any)?.score ?? (d.details as any)?.total_score ?? 0
      return { name: d.rule_key || d.filter_name || '', score: Number(score) || 0, category: d.strategy_category || '' }
    })
    .filter(d => d.score !== 0)

  const baseScore = 50
  const additions = contributing.map(d => `+${d.score.toFixed(1)}(${d.name})`)
  const formula = baseScore + (contributing.length ? ' ' + additions.join(' ') : '')
  const final = Number(resultJson.final_score) || baseScore

  return (
    <div className="score-formula-box" style={{ marginTop: 16 }}>
      <div className="score-formula-title">📐 评分公式</div>
      <div className="score-formula-text">{formula}</div>
      <div className="score-formula-result">= {final.toFixed(1)}</div>
      {contributing.length === 0 && (
        <div className="score-formula-note">无额外加分项 — 仅基础分 50.0</div>
      )}
    </div>
  )
}

function ErrorRecoveryCard({
  error,
  failedStep,
  screeningResult,
  onRetry,
  onTechOnly,
  onSwitchTimeframe,
  currentTimeframe,
}: {
  error: string
  failedStep: string
  screeningResult: Record<string, unknown> | null
  onRetry: () => void
  onTechOnly: () => void
  onSwitchTimeframe: (tf: string) => void
  currentTimeframe: string
}) {
  const suggestions = analyzeErrorSuggestions(error, failedStep, screeningResult, currentTimeframe)
  const warnings = (screeningResult?.warnings as string[]) || []

  function handleSuggestion(suggestion: ErrorSuggestion) {
    switch (suggestion.action) {
      case 'retry': onRetry(); break
      case 'tech_only': onTechOnly(); break
      case 'copy':
        navigator.clipboard?.writeText(`错误: ${error}\n${warnings.length ? '警告: ' + warnings.join('; ') : ''}`)
          .catch(() => undefined)
        break
      default:
        if (suggestion.action.startsWith('switch_')) {
          onSwitchTimeframe(suggestion.action.replace('switch_', ''))
        }
    }
  }

  const stepLabel = (() => {
    if (!failedStep) return ''
    const labels: Record<string, string> = {
      init: '初始化', stock_lookup: '查询股票', kline_fetch: '获取K线',
      rule_eval: '执行规则', read_result: '读取结果', save_result: '保存报告',
    }
    return labels[failedStep] || failedStep
  })()

  return (
    <div className="error-recovery-card">
      <div className="error-recovery-header">
        <span className="error-recovery-icon">⚠️</span>
        <div>
          <h3>筛选未完成</h3>
          {stepLabel && <span className="error-recovery-step">失败阶段: {stepLabel}</span>}
        </div>
      </div>
      <p className="error-recovery-detail">{error}</p>
      {warnings.length > 0 && (
        <ul className="error-recovery-warnings">
          {warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
      <div className="error-recovery-actions">
        {suggestions.map(s => (
          <button key={s.action} className="secondary-button" onClick={() => handleSuggestion(s)}>
            {s.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/** ── Data diagnostic panel (P0-3) ── */
interface DiagItem {
  key: string
  label: string
  status: DataStatus
  detail: string
}

function DataDiagnosticPanel({
  selectedStock, klineDiagnostics, klineRows, screeningResult, progress,
}: {
  selectedStock: StockSearchResult | null
  klineDiagnostics: { status: string; source: string; error_message?: string } | null
  klineRows: Record<string, unknown>[]
  screeningResult: Record<string, unknown> | null
  progress: { pct: number; step: string; detail: string; status: string }
}) {
  const ruleDetails = (screeningResult?.rule_details as FilterDetailRow[]) || []
  const resultJson = (screeningResult?.result_json || screeningResult || {}) as Record<string, unknown>

  const items: DiagItem[] = [
    {
      key: 'stock', label: '股票识别',
      status: selectedStock ? 'computed' : 'error',
      detail: selectedStock
        ? `已识别 — ${selectedStock.name} (${selectedStock.code})`
        : '未选择股票',
    },
    {
      key: 'kline', label: 'K线数据',
      status: klineDiagnostics
        ? (klineDiagnostics.status === 'cached' || klineDiagnostics.status === 'fresh' ? 'computed'
          : klineDiagnostics.status === 'error' ? 'error' : 'missing')
        : klineRows.length > 0 ? 'computed' : 'not_computed',
      detail: klineRows.length > 0
        ? `已获取 — ${klineRows.length} 条 ${progress.step === 'kline_fetch' ? '(加载中...)' : ''}`
        : klineDiagnostics?.error_message
          ? `获取失败 — ${klineDiagnostics.error_message}`
          : klineDiagnostics?.source
            ? `数据源 ${klineDiagnostics.source}: 无数据返回` : '未获取',
    },
    {
      key: 'technical', label: '技术指标',
      status: ruleDetails.length > 0 ? 'computed'
        : screeningResult ? 'not_computed' : 'not_computed',
      detail: ruleDetails.length > 0
        ? `已计算 — ${ruleDetails.filter(d => d.result === 'pass').length}/${ruleDetails.length} 条规则通过`
        : screeningResult ? '规则评估未产生明细' : '尚未执行',
    },
    {
      key: 'macro', label: '宏观数据',
      status: resultJson.macro_score !== undefined && resultJson.macro_score !== null ? 'computed'
        : screeningResult ? 'missing' : 'not_computed',
      detail: resultJson.macro_score !== undefined && resultJson.macro_score !== null
        ? `已补齐 — 宏观分 ${Number(resultJson.macro_score).toFixed(1)}`
        : screeningResult ? '宏观数据未补齐 — 外部API可能超时或数据不足' : '尚未执行',
    },
    {
      key: 'industry', label: '行业数据',
      status: selectedStock?.industry || selectedStock?.sector ? 'computed' : 'missing',
      detail: selectedStock?.industry || selectedStock?.sector
        ? `已获取 — ${selectedStock.industry || selectedStock.sector}`
        : '行业数据不可用',
    },
    {
      key: 'backend', label: '后端状态',
      status: screeningResult
        ? (screeningResult.status === 'completed' ? 'computed'
          : screeningResult.status === 'failed' ? 'error' : 'not_computed')
        : 'not_computed',
      detail: screeningResult
        ? (screeningResult.status === 'completed' ? '已完成 — 无异常'
          : `异常 — ${screeningResult.status || 'unknown'}`)
        : '尚未连接',
    },
  ]

  const hasError = items.some(i => i.status === 'error')

  return (
    <details open={hasError} className="diagnostic-panel">
      <summary className="diagnostic-summary">
        <span className="diagnostic-title">📡 数据诊断</span>
        <span className="diagnostic-counts">
          <span className="diag-ok">{items.filter(i => i.status === 'computed').length} 正常</span>
          {items.filter(i => i.status === 'error').length > 0 && (
            <span className="diag-err">{items.filter(i => i.status === 'error').length} 异常</span>
          )}
          {items.filter(i => i.status === 'missing').length > 0 && (
            <span className="diag-warn">{items.filter(i => i.status === 'missing').length} 缺失</span>
          )}
        </span>
      </summary>
      <div className="diagnostic-items">
        {items.map(item => (
          <div key={item.key} className="diagnostic-item">
            <DataStatusBadge status={item.status} />
            <span className="diagnostic-label">{item.label}</span>
            <span className="diagnostic-detail">{item.detail}</span>
          </div>
        ))}
      </div>
    </details>
  )
}

/** 格式化分数，返回 {display, status} 以支持不同展示 */
function formatScoreWithStatus(value?: number | string | null, meta?: { status?: string; error?: string }): { display: string; status: DataStatus } {
  if (value === undefined || value === null || value === '' || (typeof value === 'string' && value.trim() === '')) {
    return { display: '—', status: meta?.status === 'error' ? 'error' : 'not_computed' }
  }
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return { display: String(value), status: meta?.status === 'error' ? 'error' : 'missing' }
  }
  return { display: numeric.toFixed(1), status: 'computed' }
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

function macroScoreDetailsFromResult(result: ScreeningResult): MacroScoreDetails | null {
  const macroRow = (result.filter_details || []).find(item => {
    const details = item.details || {}
    return details.macro_score !== undefined && details.macro_score !== null
  })
  const scoreDetails = result.score_details || {}
  const nested = scoreDetails.macro_details
  const source = (isRecord(nested) ? nested : macroRow?.details || scoreDetails) as Record<string, unknown>
  if (source.macro_score === undefined || source.macro_score === null) return null
  return source as MacroScoreDetails
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function formatScore(value?: number | string | null) {
  if (value === undefined || value === null || value === '') return '-'
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric.toFixed(1) : String(value)
}

/** data_source 值 → 用户可读的中文标签 */
const DATA_SOURCE_LABELS: Record<string, string> = {
  web_backend: 'Web 后端直连',
  eastmoney: '东方财富',
  yfinance: 'Yahoo Finance',
  akshare: 'AKShare',
  futu: '富途 OpenD',
  opend_cache: '富途 OpenD (缓存)',
  DatabaseKlineCache: '数据库缓存',
  failed: '获取失败',
}

function dataSourceLabel(value: unknown): string {
  const key = String(value || '').trim()
  if (!key) return '未补齐'
  // 尝试精确匹配，然后前缀匹配
  if (DATA_SOURCE_LABELS[key]) return DATA_SOURCE_LABELS[key]
  for (const [prefix, label] of Object.entries(DATA_SOURCE_LABELS)) {
    if (key.toLowerCase().startsWith(prefix.toLowerCase())) return label
  }
  return key
}

function formatReportValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return '未补齐'
  if (Array.isArray(value)) {
    if (value.length === 0) return '无'
    return value.map(item => formatReportValue(item)).join('；')
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function macroDimensionLabel(key: string) {
  const labels: Record<string, string> = {
    company_event_strength: '公司事件强度',
    sector_heat: '板块热度',
    news_validation: '新闻验证',
    impact_direction: '影响方向',
    source_credibility: '来源可信度',
    freshness: '时效性',
  }
  return labels[key] || key
}

function formatCell(value: any, column?: string): React.ReactNode {
  if (column === 'status' || column === 'is_passed' || column === 'result') return <StatusBadge value={value} />
  if (column === 'market') return optionMarketLabel(value)
  if (column === 'technical_score' || column === 'macro_score' || column === 'final_score') return formatScore(value)
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
