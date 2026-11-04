import React, { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import './reports.css'

export type ReportAnalysis = {
  analysis_status?: string
  summary?: string
  reliability_score?: number | null
  confidence_score?: number | null
  signal_bias?: string
  positive_factors?: string[]
  risk_factors?: string[]
  macro_factors?: string[]
  company_events?: string[]
  market_hot_news?: string[]
  company_hot_news?: string[]
  news_impact?: string
  source_urls?: string[]
  data_gaps?: string[]
  factor_citations?: Record<string, { label?: string; url?: string }[]>
}
export type ReportStock = {
  code: string
  name: string
  instrument_type?: string
  screening?: { sector?: string; conditions_met?: string; pe_ratio?: string; market_cap?: string }
  score?: { final_score?: number; formula?: string }
  analysis?: ReportAnalysis
}
export type ReportMarket = {
  market: string
  market_label?: string
  task_date?: string
  timeframe?: string
  hot_sectors?: string[]
  stocks: ReportStock[]
}
export type ReportContent = {
  schema_version: number
  overview: { task_id: string; task_type: string; task_date: string; timeframe: string; passed_count: number }
  markets: ReportMarket[]
  disclaimer?: string
}

export type ReportDocument = {
  report_id: string
  task_id: string
  task_type: string
  title: string
  summary: string
  markets: string[]
  codes: string[]
  names: string[]
  passed_count: number
  generated_at: string
  task_date?: string
  timeframe?: string
  chain_key?: string
  content?: ReportContent
  highlights?: Record<string, { text: string; matched: boolean }[][]>
}
export type ReportList = { reports: ReportDocument[]; total: number; page: number; page_size: number }

const TASK_TYPES: Record<string, string> = {
  market: '全市场筛选', single_stock: '个股筛选', custom_list: '自定义名单', scheduled: '定时筛选',
}
const MARKETS: Record<string, string> = { HK: '港股', US: '美股', A: 'A股' }

function ReportHighlight({ text = '', parts }: { text?: string; parts?: { text: string; matched: boolean }[] }) {
  if (!parts?.length) return <>{text}</>
  return <>{parts.map((part, index) => part.matched
    ? <mark className="report-highlight" key={index}>{part.text}</mark>
    : <React.Fragment key={index}>{part.text}</React.Fragment>)}</>
}

function reportTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}

function TextList({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null
  return <div className="report-content-group"><h4>{title}</h4><ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>
}

function ReportContentView({ content }: { content?: ReportContent }) {
  if (!content) return <div className="report-content-empty">该报告暂无结构化正文。</div>
  return <div className="report-structured">
    <section className="report-content-section"><h3>任务概况</h3>
      <dl className="report-facts"><div><dt>任务日期</dt><dd>{content.overview?.task_date || '—'}</dd></div>
        <div><dt>分析周期</dt><dd>{content.overview?.timeframe || '—'}</dd></div>
        <div><dt>入选数量</dt><dd>{content.overview?.passed_count ?? 0} 只</dd></div></dl>
    </section>
    {(content.markets || []).map(market => <section className="report-content-section" key={market.market}>
      <h3>{market.market_label || market.market} · 入选分析</h3>
      {!!market.hot_sectors?.length && <p className="report-market-hot">热点方向：{market.hot_sectors.join('、')}</p>}
      <div className="report-stock-list">{market.stocks.map(stock => <article className="report-stock-card" key={stock.code}>
        <header><div><h4>{stock.name}</h4><span className="report-stock-code">{stock.code}</span></div>
          <div className="report-stock-score">综合评分 {stock.score?.final_score ?? '—'}</div></header>
        <p className="report-stock-meta">{stock.instrument_type || '股票'}{stock.screening?.sector ? ` · ${stock.screening.sector}` : ''}
          {stock.screening?.conditions_met ? ` · 命中：${stock.screening.conditions_met}` : ''}</p>
        {stock.analysis?.summary && <p className="report-stock-summary">{stock.analysis.summary}</p>}
        <div className="report-stock-tags"><span>信号可靠性 {stock.analysis?.reliability_score ?? '—'}</span>
          <span>模型置信度 {stock.analysis?.confidence_score ?? '—'}</span><span>方向 {stock.analysis?.signal_bias || '—'}</span></div>
        <TextList title="支撑因素" items={stock.analysis?.positive_factors} />
        <TextList title="风险因素" items={stock.analysis?.risk_factors} />
        <TextList title="宏观与政策" items={stock.analysis?.macro_factors} />
        <TextList title="公司事件" items={stock.analysis?.company_events} />
        <TextList title="市场热点新闻" items={stock.analysis?.market_hot_news} />
        <TextList title="公司热点新闻" items={stock.analysis?.company_hot_news} />
        <TextList title="数据缺失" items={stock.analysis?.data_gaps} />
        {!!stock.analysis?.source_urls?.length && <div className="report-content-group"><h4>信息来源</h4><ul>
          {stock.analysis.source_urls.map((url, index) => <li key={`${url}-${index}`}><a href={/^https?:\/\//i.test(url) ? url : undefined} target="_blank" rel="noopener noreferrer">{url}</a></li>)}
        </ul></div>}
      </article>)}</div>
    </section>)}
    {content.disclaimer && <p className="report-content-disclaimer">{content.disclaimer}</p>}
  </div>
}

export function ReportsPage() {
  const pageRef = useRef<HTMLElement>(null)
  const [location, setLocation] = useState(() => {
    const params = new URLSearchParams(window.location.search)
    return { q: params.get('q') || '', page: Math.max(1, parseInt(params.get('report_page') || '1') || 1), reportId: params.get('report_id') || '' }
  })
  const [keyword, setKeyword] = useState(location.q)
  const [list, setList] = useState<ReportList | null>(null)
  const [report, setReport] = useState<ReportDocument | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const onBack = () => {
      const params = new URLSearchParams(window.location.search)
      setLocation({ q: params.get('q') || '', page: Math.max(1, parseInt(params.get('report_page') || '1') || 1), reportId: params.get('report_id') || '' })
    }
    window.addEventListener('popstate', onBack)
    return () => window.removeEventListener('popstate', onBack)
  }, [])

  useEffect(() => { setKeyword(location.q) }, [location.q])

  useEffect(() => { pageRef.current?.scrollIntoView({ block: 'start', behavior: 'auto' }) }, [location.q, location.page, location.reportId])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setReport(null)
    async function load() {
      try {
        const params = new URLSearchParams({ q: location.q, page: String(location.page), page_size: '20' })
        if (location.reportId) {
          const detail = await api<ReportDocument>(`/api/reports/${encodeURIComponent(location.reportId)}`, { signal: controller.signal })
          if (!controller.signal.aborted) setReport(detail)
        } else {
          const result = await api<ReportList>(`/api/reports?${params}`, { signal: controller.signal })
          if (!controller.signal.aborted) setList(result)
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '报告加载失败，请重试')
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [location.q, location.page, location.reportId, revision])

  function navigateReports(next: typeof location) {
    const url = new URL(window.location.href)
    url.searchParams.set('page', 'reports')
    if (next.q) url.searchParams.set('q', next.q)
    else url.searchParams.delete('q')
    if (next.page > 1) url.searchParams.set('report_page', String(next.page))
    else url.searchParams.delete('report_page')
    if (next.reportId) url.searchParams.set('report_id', next.reportId)
    else url.searchParams.delete('report_id')
    window.history.pushState(null, '', url)
    setLocation(next)
    if (next.q === location.q && next.page === location.page && next.reportId === location.reportId) setRevision(value => value + 1)
  }

  function reportLink(id: string) {
    const url = new URL(window.location.href)
    url.searchParams.set('page', 'reports')
    url.searchParams.set('report_id', id)
    return url.pathname + url.search
  }

  const totalPages = Math.max(1, Math.ceil((list?.total || 0) / 20))

  return <section className="reports-page" ref={pageRef}>
    <header className="page-header reports-header">
      <div><p className="eyebrow">RESEARCH LIBRARY</p><h1>报告查询</h1><p>汇集每次任务的 AI 分析，回看宏观背景、公司信息与技术依据。</p></div>
      <span className="reports-mode">MySQL · 任务报告</span>
    </header>

    {location.reportId ? <button className="secondary-button reports-back" aria-label="返回报告列表" onClick={() => navigateReports({ ...location, reportId: '' })}>← 返回报告列表</button>
      : <form className="panel reports-search" onSubmit={event => { event.preventDefault(); navigateReports({ q: keyword.trim(), page: 1, reportId: '' }) }}>
        <label htmlFor="report-keyword">报告关键词</label>
        <div className="reports-search-row"><input id="report-keyword" value={keyword} maxLength={500} onChange={event => setKeyword(event.target.value)} placeholder="搜索公司、股票代码、任务 ID 或报告内容" />
          <button className="primary" type="submit" disabled={loading}>搜索报告</button>
          <button className="secondary-button" type="button" disabled={loading} onClick={() => setRevision(value => value + 1)} aria-label="刷新报告">{loading ? '刷新中…' : '↻ 刷新'}</button>
        </div>
      </form>}

    {loading ? <div className="panel reports-state" role="status" aria-live="polite"><span className="reports-spinner" /><h2>正在加载报告</h2><p>正在读取{location.reportId ? '完整报告' : '归档列表'}，请稍候…</p></div>
      : error ? <div className="panel reports-state" role="alert"><span className="reports-state-icon">!</span><h2>报告暂时无法加载</h2><p>{error}</p><button className="secondary-button" onClick={() => setRevision(value => value + 1)}>重试</button></div>
      : location.reportId && report ? <article className="panel report-document">
        <header className="report-document-header"><div className="report-tags"><span className="report-type">{TASK_TYPES[report.task_type] || report.task_type}</span>{report.markets.map(market => <span key={market} className="report-market">{MARKETS[market] || market}</span>)}</div>
          <h2>{report.title}</h2><p className="report-document-summary">{report.summary}</p>
          <dl className="report-facts"><div><dt>生成时间 · 上海</dt><dd>{reportTime(report.generated_at)}</dd></div><div><dt>入选数量</dt><dd>{report.passed_count} 只</dd></div><div><dt>任务 ID</dt><dd className="report-task-id">{report.task_id}</dd></div><div><dt>报告 ID</dt><dd className="report-task-id">{report.report_id}</dd></div></dl>
          <p className="report-symbols">{report.names.join(' · ')}{report.codes.length > 0 && <span>{report.codes.join(' / ')}</span>}</p>
        </header>
        <ReportContentView content={report.content} />
      </article>
      : !list?.reports.length ? <div className="panel reports-state"><span className="reports-state-icon">⌕</span><h2>{location.q ? '没有找到相关报告' : '还没有归档报告'}</h2><p>{location.q ? '试试公司名称、完整股票代码，或减少关键词。' : '有股票入选且 AI 报告成功入库后，会自动出现在这里。'}</p>{location.q && <button className="secondary-button" onClick={() => navigateReports({ q: '', page: 1, reportId: '' })}>查看全部报告</button>}</div>
      : <><div className="reports-list-heading"><h2>报告列表 <span>{list.total} 篇</span></h2><span>{location.q ? `“${location.q}”的搜索结果 · 相关性优先` : '最近生成优先'}</span></div>
        <div className="reports-grid">{list.reports.map(item => <article className="panel report-card" key={item.report_id}>
          <div className="report-card-top"><span className="report-type">{TASK_TYPES[item.task_type] || item.task_type}</span><time dateTime={item.generated_at}>{reportTime(item.generated_at)}</time></div>
          <h3><a href={reportLink(item.report_id)} onClick={event => { event.preventDefault(); navigateReports({ ...location, reportId: item.report_id }) }}><ReportHighlight text={item.title} parts={item.highlights?.title?.[0]} /></a></h3>
          <p className="report-card-summary"><ReportHighlight text={item.summary} parts={item.highlights?.summary?.[0]} /></p>
          {!!item.highlights?.content_text?.length && <div className="report-content-matches"><span>正文命中</span>{item.highlights.content_text.map((parts, index) => <p className="report-match-snippet" key={index}>…<ReportHighlight parts={parts} />…</p>)}</div>}
          {(['company_names', 'codes'] as const).map(field => item.highlights?.[field]?.map((parts, index) => <p className="report-match-metadata" key={`${field}-${index}`}><span>{field === 'codes' ? '代码命中' : '公司命中'}</span><ReportHighlight parts={parts} /></p>))}
          <div className="report-tags">{item.markets.map(market => <span key={market} className="report-market">{MARKETS[market] || market}</span>)}<span className="report-count">入选 {item.passed_count} 只</span></div>
          <div className="report-card-bottom"><span className="report-task-id" title={item.task_id}>任务 <ReportHighlight text={item.task_id} parts={item.highlights?.task_id?.[0]} /></span><a href={reportLink(item.report_id)} aria-label={`查看报告：${item.title}`} onClick={event => { event.preventDefault(); navigateReports({ ...location, reportId: item.report_id }) }}>阅读全文 →</a></div>
        </article>)}</div>
        <nav className="reports-pagination" aria-label="报告分页"><span>第 {location.page} / {totalPages} 页 · 共 {list.total} 篇</span><div><button className="secondary-button" disabled={location.page <= 1} onClick={() => navigateReports({ ...location, page: location.page - 1 })}>上一页</button><button className="secondary-button" disabled={location.page >= totalPages} onClick={() => navigateReports({ ...location, page: location.page + 1 })}>下一页</button></div></nav>
      </>}
  </section>
}
