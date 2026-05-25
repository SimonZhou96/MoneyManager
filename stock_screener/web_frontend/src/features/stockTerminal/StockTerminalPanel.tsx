import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { getMarketDigest, getProviderRuns, getStockIntel, previewEvidencePack } from '../marketIntel/api'
import type { EvidencePackPreview, IntelBundle, IntelItem, MarketCode, ProviderRun, SourceStatus } from '../marketIntel/types'
import { getStockTerminalFundFlow, getStockTerminalKlines, getStockTerminalMinute, getStockTerminalSummary } from './api'
import type { DataBlockStatus, SeriesPoint, StockTerminalSeriesResponse, StockTerminalSummary } from './types'

type TerminalTab = 'quote' | 'money' | 'intel' | 'evidence'

export type StockTerminalRow = {
  market?: string
  code?: string
  name?: string
  close_price?: number
  filter_summary?: string
  is_passed?: boolean
}

type LoadState = {
  summary: boolean
  quote: boolean
  money: boolean
  intel: boolean
  evidence: boolean
}

const TABS: Array<{ key: TerminalTab; label: string }> = [
  { key: 'quote', label: '行情' },
  { key: 'money', label: '资金' },
  { key: 'intel', label: '情报' },
  { key: 'evidence', label: '证据' }
]

const INTEL_TYPES: Record<string, string> = {
  announcement: '公告',
  research_report: '研报',
  financial: '资金面',
  market_news: '市场新闻',
  index_snapshot: '指数快照',
  news: '新闻'
}

const EMPTY_LOAD_STATE: LoadState = {
  summary: false,
  quote: false,
  money: false,
  intel: false,
  evidence: false
}

export function StockTerminalPanel({ row }: { row: StockTerminalRow | null }) {
  const [tab, setTab] = useState<TerminalTab>('quote')
  const [summary, setSummary] = useState<StockTerminalSummary | null>(null)
  const [klines, setKlines] = useState<StockTerminalSeriesResponse | null>(null)
  const [minute, setMinute] = useState<StockTerminalSeriesResponse | null>(null)
  const [fundFlow, setFundFlow] = useState<StockTerminalSeriesResponse | null>(null)
  const [stockIntel, setStockIntel] = useState<IntelBundle | null>(null)
  const [marketDigest, setMarketDigest] = useState<IntelBundle | null>(null)
  const [evidence, setEvidence] = useState<EvidencePackPreview | null>(null)
  const [runs, setRuns] = useState<ProviderRun[]>([])
  const [loading, setLoading] = useState<LoadState>(EMPTY_LOAD_STATE)
  const [error, setError] = useState('')

  const target = useMemo(() => normalizeTarget(row), [row])
  const targetKey = target ? `${target.market}:${target.code}` : ''

  useEffect(() => {
    setTab('quote')
    setSummary(null)
    setKlines(null)
    setMinute(null)
    setFundFlow(null)
    setStockIntel(null)
    setMarketDigest(null)
    setEvidence(null)
    setRuns([])
    setLoading(EMPTY_LOAD_STATE)
    setError('')
    if (!target) return

    let cancelled = false
    setLoading(current => ({ ...current, summary: true }))
    getStockTerminalSummary(target.market, target.code)
      .then(data => {
        if (!cancelled) setSummary(data)
      })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载个股摘要失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(current => ({ ...current, summary: false }))
      })
    return () => { cancelled = true }
  }, [targetKey])

  useEffect(() => {
    if (!target || tab !== 'quote' || (klines && minute)) return
    let cancelled = false
    setLoading(current => ({ ...current, quote: true }))
    Promise.all([
      klines ? Promise.resolve(klines) : getStockTerminalKlines(target.market, target.code, '1d', 80),
      minute ? Promise.resolve(minute) : getStockTerminalMinute(target.market, target.code)
    ])
      .then(([nextKlines, nextMinute]) => {
        if (cancelled) return
        setKlines(nextKlines)
        setMinute(nextMinute)
      })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载行情序列失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(current => ({ ...current, quote: false }))
      })
    return () => { cancelled = true }
  }, [targetKey, tab, klines, minute])

  useEffect(() => {
    if (!target || tab !== 'money' || fundFlow) return
    let cancelled = false
    setLoading(current => ({ ...current, money: true }))
    getStockTerminalFundFlow(target.market, target.code)
      .then(data => {
        if (!cancelled) setFundFlow(data)
      })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载资金流失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(current => ({ ...current, money: false }))
      })
    return () => { cancelled = true }
  }, [targetKey, tab, fundFlow])

  useEffect(() => {
    if (!target || tab !== 'intel' || stockIntel) return
    const marketCode = asMarketCode(target.market)
    if (!marketCode) {
      setError(`市场 ${target.market} 暂不支持情报查询`)
      return
    }
    let cancelled = false
    setLoading(current => ({ ...current, intel: true }))
    getStockIntel(marketCode, target.code, false)
      .then(data => {
        if (!cancelled) setStockIntel(data)
      })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载个股情报失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(current => ({ ...current, intel: false }))
      })
    return () => { cancelled = true }
  }, [targetKey, tab, stockIntel])

  useEffect(() => {
    if (!target || tab !== 'evidence' || evidence) return
    const marketCode = asMarketCode(target.market)
    if (!marketCode) {
      setError(`市场 ${target.market} 暂不支持证据包查询`)
      return
    }
    let cancelled = false
    setLoading(current => ({ ...current, evidence: true }))
    Promise.all([
      previewEvidencePack(marketCode, target.code, false),
      getProviderRuns(marketCode, target.code, 20),
      getMarketDigest(marketCode, false).catch(() => null)
    ])
      .then(([nextEvidence, providerRuns, digest]) => {
        if (cancelled) return
        setEvidence(nextEvidence)
        setRuns(providerRuns.runs || [])
        setMarketDigest(digest)
      })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载证据包失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(current => ({ ...current, evidence: false }))
      })
    return () => { cancelled = true }
  }, [targetKey, tab, evidence])

  const sourceStatuses = useMemo(
    () => collectSourceStatus(summary, klines, minute, fundFlow, stockIntel, evidence, marketDigest),
    [summary, klines, minute, fundFlow, stockIntel, evidence, marketDigest]
  )

  if (!target) {
    return (
      <aside className="stock-terminal-panel">
        <div className="terminal-empty">
          <strong>选择一行查看个股终端</strong>
          <span>终端数据只会在选中筛选结果后按标签页懒加载。</span>
        </div>
      </aside>
    )
  }

  const quote = summary?.quote
  const title = summary?.name || row?.name || target.code
  const quoteRows = [
    ['最新价', quote?.price ?? row?.close_price],
    ['涨跌', quote?.change],
    ['涨跌幅', formatPercent(quote?.change_percent)],
    ['开盘', quote?.open_price],
    ['最高', quote?.high],
    ['最低', quote?.low],
    ['昨收', quote?.previous_close],
    ['成交量', quote?.volume]
  ]

  return (
    <aside className="stock-terminal-panel">
      <header className="terminal-heading">
        <div>
          <span>{target.market}</span>
          <h2>{title}</h2>
          <p>{target.code}</p>
        </div>
        <StatusPill value={row?.is_passed} />
      </header>

      {error && <div className="inline-error">{error}</div>}

      <div className="terminal-tabs" role="tablist" aria-label="个股终端">
        {TABS.map(item => (
          <button
            key={item.key}
            type="button"
            className={tab === item.key ? 'active' : ''}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="terminal-section">
        {loading.summary && <div className="terminal-loading">摘要加载中...</div>}
        {tab === 'quote' && (
          <>
            <div className="quote-grid">
              {quoteRows.map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <strong>{displayValue(value)}</strong>
                </div>
              ))}
            </div>
            <MiniBars title="日 K 收盘" rows={klines?.rows || []} valueKey="close" loading={loading.quote} />
            <MiniBars title="分钟成交" rows={minute?.rows || []} valueKey="volume" loading={loading.quote} />
          </>
        )}
        {tab === 'money' && (
          <MiniBars title="资金净流入" rows={fundFlow?.rows || []} valueKey="net_inflow" loading={loading.money} signed />
        )}
        {tab === 'intel' && (
          <IntelGroups bundle={stockIntel} loading={loading.intel} />
        )}
        {tab === 'evidence' && (
          <EvidenceView evidence={evidence} runs={runs} marketDigest={marketDigest} loading={loading.evidence} />
        )}
      </div>

      <section className="terminal-source-list">
        <h3>来源状态</h3>
        {sourceStatuses.length === 0 ? <div className="empty compact-empty">暂无来源状态</div> : sourceStatuses.slice(0, 6).map(item => (
          <div key={`${item.provider}-${item.status}-${item.fetched_at || ''}`}>
            <strong>{item.provider}</strong>
            <span>{statusLabel(item.status)}{item.stale ? ' / stale' : ''}</span>
          </div>
        ))}
      </section>
    </aside>
  )
}

function MiniBars({
  title,
  rows,
  valueKey,
  loading,
  signed = false
}: {
  title: string
  rows: SeriesPoint[]
  valueKey: keyof SeriesPoint
  loading: boolean
  signed?: boolean
}) {
  const values = rows
    .slice(-28)
    .map(row => Number(row[valueKey] ?? 0))
    .filter(value => Number.isFinite(value))
  const max = Math.max(...values.map(value => Math.abs(value)), 0)

  return (
    <section className="mini-series">
      <header>
        <h3>{title}</h3>
        <span>{loading ? '加载中...' : `${values.length} 点`}</span>
      </header>
      {values.length === 0 ? <div className="empty compact-empty">{loading ? '加载中...' : '暂无数据'}</div> : (
        <div className="bar-series" aria-label={title}>
          {values.map((value, index) => {
            const height = max > 0 ? Math.max(4, Math.round((Math.abs(value) / max) * 58)) : 4
            const className = signed && value < 0 ? 'negative' : 'positive'
            return <span key={`${value}-${index}`} className={className} style={{ height }} title={String(value)} />
          })}
        </div>
      )}
    </section>
  )
}

function IntelGroups({ bundle, loading }: { bundle: IntelBundle | null; loading: boolean }) {
  const groups = orderedGroups(bundle?.groups, ['announcement', 'research_report', 'financial', 'news'])
  if (loading) return <div className="terminal-loading">情报加载中...</div>
  if (groups.length === 0) return <div className="empty compact-empty">暂无情报数据</div>
  return (
    <div className="terminal-intel-list">
      {groups.map(([type, items]) => (
        <section key={type}>
          <h3>{INTEL_TYPES[type] || type}</h3>
          {items.slice(0, 4).map(item => <IntelItemCard item={item} key={item.dedupe_key || item.title || `${type}-${item.url}`} />)}
        </section>
      ))}
    </div>
  )
}

function EvidenceView({
  evidence,
  runs,
  marketDigest,
  loading
}: {
  evidence: EvidencePackPreview | null
  runs: ProviderRun[]
  marketDigest: IntelBundle | null
  loading: boolean
}) {
  if (loading) return <div className="terminal-loading">证据包加载中...</div>
  return (
    <div className="terminal-evidence">
      <div className="quote-grid tight">
        <div><span>结构化</span><strong>{evidence?.structured_items?.length || 0}</strong></div>
        <div><span>搜索文档</span><strong>{evidence?.search_documents?.length || 0}</strong></div>
        <div><span>人工补充</span><strong>{evidence?.manual_items?.length || 0}</strong></div>
        <div><span>Provider</span><strong>{runs.length}</strong></div>
      </div>
      {evidence?.data_gaps?.length ? <p className="terminal-note">{evidence.data_gaps.join('；')}</p> : null}
      <section>
        <h3>市场摘要</h3>
        <IntelGroups bundle={marketDigest} loading={false} />
      </section>
      <section>
        <h3>最近运行</h3>
        {runs.length === 0 ? <div className="empty compact-empty">暂无运行记录</div> : runs.slice(0, 8).map(run => (
          <div className="provider-run-row" key={`${run.provider}-${run.started_at || run.created_at}`}>
            <strong>{run.provider || '-'}</strong>
            <span>{statusLabel(run.status || '')}</span>
            <small>{displayValue(run.item_count)} 条</small>
          </div>
        ))}
      </section>
    </div>
  )
}

function IntelItemCard({ item }: { item: IntelItem }) {
  const title = item.title || '未命名情报'
  return (
    <article className="terminal-intel-item">
      <header>
        {item.url ? <a href={item.url} target="_blank" rel="noreferrer">{title}</a> : <strong>{title}</strong>}
        <span>{item.provider || item.source || '-'}</span>
      </header>
      {item.summary && <p>{item.summary}</p>}
    </article>
  )
}

function StatusPill({ value }: { value?: boolean }) {
  if (value === undefined) return null
  return <span className={`status-badge ${value ? 'pass' : 'fail'}`}>{value ? '通过' : '未通过'}</span>
}

function collectSourceStatus(...sources: Array<SourceCarrier | null>): Array<SourceStatus & { stale?: boolean }> {
  const map = new Map<string, SourceStatus & { stale?: boolean }>()
  sources.forEach(source => {
    Object.entries(source?.source_status || {}).forEach(([key, item]) => {
      const normalized = normalizeSourceStatus(key, item)
      map.set(normalized.provider || key, normalized)
    })
  })
  return Array.from(map.values())
}

type SourceCarrier = {
  source_status?: Record<string, SourceStatus | DataBlockStatus>
}

function normalizeSourceStatus(key: string, item: SourceStatus | DataBlockStatus): SourceStatus & { stale?: boolean } {
  if ('provider' in item) return item
  return {
    provider: item.source || key,
    status: item.status,
    item_count: 0,
    error_message: item.error_message,
    fetched_at: item.fetched_at,
    stale: item.stale
  }
}

function normalizeTarget(row: StockTerminalRow | null) {
  const code = row?.code?.trim()
  const market = row?.market?.trim()
  if (!code || !market) return null
  return { code, market }
}

function asMarketCode(value: string): MarketCode | null {
  return value === 'A' || value === 'HK' || value === 'US' ? value : null
}

function orderedGroups(groups: Record<string, IntelItem[]> | undefined, preferredTypes: string[]) {
  if (!groups) return []
  const preferred = preferredTypes
    .map(type => [type, groups[type] || []] as [string, IntelItem[]])
    .filter(([, items]) => items.length > 0)
  const rest = Object.entries(groups)
    .filter(([type, items]) => !preferredTypes.includes(type) && Array.isArray(items) && items.length > 0)
  return [...preferred, ...rest]
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    success: '成功',
    completed: '已完成',
    failed: '失败',
    error: '错误',
    running: '运行中',
    queued: '排队中',
    fresh: '新鲜',
    stale: '过期'
  }
  return labels[value.toLowerCase()] || value || '未知'
}

function formatPercent(value?: number | null) {
  if (value === undefined || value === null) return undefined
  return `${value.toFixed(2)}%`
}

function displayValue(value: ReactNode) {
  if (value === undefined || value === null || value === '') return '-'
  return value
}
