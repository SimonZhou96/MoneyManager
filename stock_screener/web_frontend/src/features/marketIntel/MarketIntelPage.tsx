import { useMemo, useState, type ReactNode } from 'react'
import { getMarketDigest, getProviderRuns, getStockIntel, previewEvidencePack } from './api'
import type { EvidencePackPreview, IntelBundle, IntelItem, MarketCode, ProviderRun, SourceStatus } from './types'

const MARKET_OPTIONS: Array<{ value: MarketCode; label: string; defaultCode: string }> = [
  { value: 'A', label: 'A股', defaultCode: '600519' },
  { value: 'HK', label: '港股', defaultCode: '00700' },
  { value: 'US', label: '美股', defaultCode: 'AAPL' }
]

const ITEM_TYPE_LABELS: Record<string, string> = {
  announcement: '公告',
  research_report: '研报',
  financial: '资金面',
  market_news: '市场新闻',
  index_snapshot: '指数快照',
  news: '新闻'
}

type MarketIntelState = {
  stock: IntelBundle | null
  market: IntelBundle | null
  runs: ProviderRun[]
  pack: EvidencePackPreview | null
}

export function MarketIntelPage() {
  const [market, setMarket] = useState<MarketCode>('A')
  const [code, setCode] = useState('600519')
  const [data, setData] = useState<MarketIntelState>({
    stock: null,
    market: null,
    runs: [],
    pack: null
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const sourceStatuses = useMemo(
    () => mergeSourceStatus(data.stock, data.market, data.pack),
    [data.stock, data.market, data.pack]
  )

  async function load(forceRefresh = false) {
    const normalizedCode = code.trim()
    if (!normalizedCode) {
      setError('请输入代码')
      return
    }
    setLoading(true)
    setError('')
    try {
      const [stock, marketDigest, providerRuns, pack] = await Promise.all([
        getStockIntel(market, normalizedCode, forceRefresh),
        getMarketDigest(market, forceRefresh),
        getProviderRuns(market, normalizedCode, 20),
        previewEvidencePack(market, normalizedCode, forceRefresh)
      ])
      setData({
        stock,
        market: marketDigest,
        runs: providerRuns.runs || [],
        pack
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载市场情报失败')
    } finally {
      setLoading(false)
    }
  }

  function changeMarket(value: MarketCode) {
    setMarket(value)
    setCode(MARKET_OPTIONS.find(item => item.value === value)?.defaultCode || '')
  }

  return (
    <section>
      <header className="page-header">
        <h1>市场情报</h1>
        <p>按市场和代码查看结构化公告、研报、资金面、市场摘要与证据包覆盖情况。</p>
      </header>

      <form className="form-grid market-intel-controls" onSubmit={event => { event.preventDefault(); load(false).catch(console.error) }}>
        <label className="field">
          <span>市场</span>
          <select value={market} onChange={event => changeMarket(event.target.value as MarketCode)}>
            {MARKET_OPTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label className="field">
          <span>代码</span>
          <input value={code} onChange={event => setCode(event.target.value)} placeholder="例如 600519 / AAPL" />
        </label>
        <button className="primary" disabled={loading}>{loading ? '查询中...' : '查询'}</button>
        <button type="button" className="secondary-button" disabled={loading} onClick={() => load(true).catch(console.error)}>刷新缓存</button>
      </form>

      {error && <div className="error market-intel-error">{error}</div>}

      <div className="metric-grid market-intel-summary">
        <Metric label="结构化条目" value={countPackItems(data.pack?.structured_items)} />
        <Metric label="引用数量" value={data.pack?.citations?.length || 0} />
        <Metric label="数据缺口" value={data.pack?.data_gaps?.length || 0} />
        <Metric label="Provider 运行" value={data.runs.length} />
      </div>

      <div className="market-intel-layout">
        <div>
          <Panel title="来源状态">
            <SourceStatusList items={sourceStatuses} />
          </Panel>
          <Panel title="Evidence Pack">
            <dl className="market-intel-kv">
              <dt>结构化</dt>
              <dd>{countPackItems(data.pack?.structured_items)}</dd>
              <dt>搜索文档</dt>
              <dd>{countPackItems(data.pack?.search_documents)}</dd>
              <dt>人工补充</dt>
              <dd>{countPackItems(data.pack?.manual_items)}</dd>
              <dt>Data gaps</dt>
              <dd>{data.pack?.data_gaps?.length ? data.pack.data_gaps.join('；') : '无'}</dd>
            </dl>
          </Panel>
          <Panel title="最近 Provider 运行">
            <ProviderRunsTable rows={data.runs} />
          </Panel>
        </div>

        <div>
          <Panel title="个股数据">
            <GroupedItems groups={data.stock?.groups} preferredTypes={['announcement', 'research_report', 'financial', 'news']} />
          </Panel>
          <Panel title="市场数据">
            <GroupedItems groups={data.market?.groups} preferredTypes={['market_news', 'index_snapshot']} />
          </Panel>
        </div>
      </div>
    </section>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="panel"><h2>{title}</h2>{children}</section>
}

function SourceStatusList({ items }: { items: SourceStatus[] }) {
  if (items.length === 0) return <div className="empty">暂无来源状态，先执行查询。</div>
  return (
    <div className="market-intel-source-list">
      {items.map(item => (
        <article key={`${item.provider}-${item.status}-${item.fetched_at || ''}`} className="market-intel-source">
          <div>
            <strong>{item.provider}</strong>
            <span>{item.item_count || 0} 条{item.stale ? ' · stale' : ''}</span>
          </div>
          <span className={`status ${statusClass(item.status)}`}>{statusLabel(item.status)}</span>
          {item.error_message && <p>{item.error_message}</p>}
        </article>
      ))}
    </div>
  )
}

function GroupedItems({ groups, preferredTypes }: { groups?: Record<string, IntelItem[]>; preferredTypes: string[] }) {
  const entries = orderedGroups(groups, preferredTypes)
  if (entries.length === 0) return <div className="empty">暂无数据</div>
  return (
    <div className="market-intel-groups">
      {entries.map(([type, items]) => (
        <section key={type} className="market-intel-group">
          <h3>{ITEM_TYPE_LABELS[type] || type}</h3>
          {items.length === 0 ? <div className="empty">暂无{ITEM_TYPE_LABELS[type] || type}</div> : (
            <div className="market-intel-item-list">
              {items.slice(0, 6).map(item => <IntelCard key={item.dedupe_key || `${type}-${item.title}`} item={item} />)}
            </div>
          )}
        </section>
      ))}
    </div>
  )
}

function IntelCard({ item }: { item: IntelItem }) {
  const title = item.title || '未命名情报'
  return (
    <article className="market-intel-item">
      <header>
        {item.url ? <a href={item.url} target="_blank" rel="noreferrer">{title}</a> : <strong>{title}</strong>}
        <span>{item.provider || item.source || '-'}</span>
      </header>
      {item.summary && <p>{item.summary}</p>}
      <footer>{formatTime(item.published_at || item.fetched_at)}{item.is_stale ? ' · stale' : ''}</footer>
    </article>
  )
}

function ProviderRunsTable({ rows }: { rows: ProviderRun[] }) {
  if (rows.length === 0) return <div className="empty">暂无运行记录</div>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>市场</th>
            <th>代码</th>
            <th>状态</th>
            <th>数量</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.provider}-${row.market}-${row.code}-${index}`}>
              <td>{row.provider || '-'}</td>
              <td>{row.market || '-'}</td>
              <td>{row.code || '-'}</td>
              <td><span className={`status ${statusClass(row.status || '')}`}>{statusLabel(row.status || '')}</span></td>
              <td>{row.item_count ?? '-'}</td>
              <td>{formatTime(row.finished_at || row.created_at || row.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function mergeSourceStatus(...sources: Array<IntelBundle | EvidencePackPreview | null>): SourceStatus[] {
  const map = new Map<string, SourceStatus>()
  sources.forEach(source => {
    Object.values(source?.source_status || {}).forEach(item => {
      map.set(item.provider || JSON.stringify(item), item)
    })
  })
  return Array.from(map.values())
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

function countPackItems(items?: unknown[]) {
  return Array.isArray(items) ? items.length : 0
}

function statusClass(value: string) {
  const key = value.toLowerCase()
  if (key === 'success' || key === 'completed') return 'pass'
  if (key === 'failed' || key === 'error') return 'fail'
  if (key === 'running') return 'running'
  if (key === 'queued') return 'queued'
  return 'neutral'
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    success: '成功',
    completed: '已完成',
    failed: '失败',
    error: '错误',
    running: '运行中',
    queued: '排队中'
  }
  return labels[value.toLowerCase()] || value || '未知'
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}
