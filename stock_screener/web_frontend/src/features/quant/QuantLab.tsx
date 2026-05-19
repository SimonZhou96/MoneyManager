import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../../api'
import type { QuantBacktestRequest, QuantBacktestStatus, QuantBacktestSubmitResponse, QuantStrategyOverlay, QuantSymbolChart } from './types'

const TERMINAL_STATUSES = new Set(['completed', 'failed'])

export function QuantLab() {
  const [market, setMarket] = useState('US')
  const [symbols, setSymbols] = useState('US.AAPL')
  const [entryChainKey, setEntryChainKey] = useState('default')
  const [exitPolicy, setExitPolicy] = useState('fixed_holding_days')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [runId, setRunId] = useState('')
  const [run, setRun] = useState<QuantBacktestStatus | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const isActiveRun = Boolean(runId && run && !TERMINAL_STATUSES.has(run.status))
  const metricValues = useMemo(() => run?.metrics || {}, [run])
  const selectedChart = run?.chart?.symbols?.[0]

  async function refreshRun(nextRunId = runId) {
    if (!nextRunId) return
    const response = await api<QuantBacktestStatus>(`/api/quant/backtests/${nextRunId}`)
    setRun(response)
    setStatus(statusText(response))
    if (response.status === 'failed') {
      setError(response.error_message || '回测执行失败')
    }
  }

  useEffect(() => {
    if (!runId || (run && TERMINAL_STATUSES.has(run.status))) {
      return
    }
    const timer = window.setInterval(() => {
      refreshRun(runId).catch((err) => {
        setError(err instanceof Error ? err.message : '刷新回测进度失败')
      })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [runId, run?.status])

  async function submit() {
    setError('')
    setStatus('提交中')
    setRun(null)
    setRunId('')
    setSubmitting(true)
    const payload: QuantBacktestRequest = {
      market,
      symbols: symbols.split(/[,\n]/).map((item) => item.trim()).filter(Boolean),
      strategy_source: 'rule_chain',
      entry_chain_key: entryChainKey,
      exit_policy: exitPolicy === 'fixed_holding_days' ? { type: 'fixed_holding_days', days: 5 } : { type: 'stop_loss', pct: 0.08 },
      start: '2026-01-01',
      end: '2026-05-18',
      initial_cash: 100000,
      quantity: 100,
      commission_rate: 0.001,
      slippage_rate: 0.001,
      max_position_weight: 0.2
    }
    try {
      const response = await api<QuantBacktestSubmitResponse>('/api/quant/backtests', {
        method: 'POST',
        body: JSON.stringify(payload)
      })
      setRunId(response.run_id)
      setStatus(`已创建回测: ${response.run_id}`)
      await refreshRun(response.run_id)
    } catch (err) {
      setStatus('')
      setError(err instanceof Error ? err.message : '创建回测失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="quant-lab">
      <header className="page-header">
        <h1>量化实验室</h1>
        <p>使用现有规则链生成模拟交易点，回测胜率、收益、回撤和纸面交易表现。</p>
      </header>
      <div className="quant-grid">
        <aside className="panel quant-config">
          <label>市场</label>
          <select value={market} onChange={(event) => setMarket(event.target.value)}>
            <option value="US">美股</option>
            <option value="HK">港股</option>
            <option value="A">A股</option>
          </select>
          <label>标的</label>
          <div className="symbol-input">
            <textarea
              value={symbols}
              onChange={(event) => setSymbols(event.target.value)}
              placeholder="每行一个标的，例如：US.AAPL"
              spellCheck={false}
            />
            <span>{symbols.split(/[,\n]/).map((item) => item.trim()).filter(Boolean).length} 个标的，支持逗号或换行分隔</span>
          </div>
          <label>入口规则链</label>
          <input value={entryChainKey} onChange={(event) => setEntryChainKey(event.target.value)} />
          <label>退出策略</label>
          <select value={exitPolicy} onChange={(event) => setExitPolicy(event.target.value)}>
            <option value="fixed_holding_days">固定持有 5 天</option>
            <option value="stop_loss">止损 8%</option>
          </select>
          <button onClick={submit} disabled={submitting || isActiveRun}>{submitting || isActiveRun ? '回测处理中' : '开始回测'}</button>
          {status && <div className="notice">{status}</div>}
          {error && <div className="error">{error}</div>}
        </aside>
        <main className="quant-results">
          <div className="metric-row">
            <div className="metric-card">总收益<span>{formatPercent(metricValues.total_return)}</span></div>
            <div className="metric-card">最大回撤<span>{formatPercent(metricValues.max_drawdown)}</span></div>
            <div className="metric-card">胜率<span>{formatPercent(metricValues.win_rate)}</span></div>
            <div className="metric-card">盈亏比<span>{formatNumber(metricValues.win_loss_ratio)}</span></div>
          </div>
          <div className="panel quant-progress-panel">
            <div className="progress-header">
              <div>
                <h2>服务端进度</h2>
                <p>{run ? statusText(run) : '尚未创建回测任务'}</p>
              </div>
              <strong>{run ? `${run.progress_pct}%` : '-'}</strong>
            </div>
            <div className={`progress-track ${run?.status === 'failed' ? 'failed' : ''} ${run?.status === 'completed' ? 'completed' : ''}`} aria-label="回测进度">
              <span style={{ width: `${run?.progress_pct || 0}%` }} />
            </div>
            {run?.error_message && <div className="error compact-error">{run.error_message}</div>}
          </div>
          <CandlestickChart data={selectedChart} />
          <div className="panel quant-log-panel">
            <h2>服务端日志</h2>
            {run?.progress_logs?.length ? (
              <ol>
                {run.progress_logs.map((item, index) => (
                  <li key={`${item.time || 'log'}-${index}`}>
                    {item.time && <time>{item.time}</time>}
                    <span>{item.message}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">提交回测后会显示服务端处理过程。</p>
            )}
          </div>
        </main>
      </div>
    </section>
  )
}

function statusText(run: QuantBacktestStatus) {
  const statusMap: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    completed: '已完成',
    failed: '失败'
  }
  const label = statusMap[run.status] || run.status
  return `${label}${run.current_stage ? `：${run.current_stage}` : ''}`
}

function formatPercent(value: number | undefined) {
  if (value === undefined || Number.isNaN(value)) return '-'
  return `${(value * 100).toFixed(2)}%`
}

function formatNumber(value: number | undefined) {
  if (value === undefined || Number.isNaN(value)) return '-'
  return value.toFixed(2)
}

function CandlestickChart({ data }: { data?: QuantSymbolChart }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)

  if (!data || !data.bars.length) {
    return <div className="panel chart-placeholder">K 线图 / 买入卖出信号。若服务端提示 missing_bars，请先同步该标的 K 线或使用可用的数据源。</div>
  }

  const width = 720
  const height = 470
  const padding = { top: 24, right: 42, bottom: 34, left: 48 }
  const pricePanelHeight = 230
  const rsiPanel = { top: 286, height: 64 }
  const volumePanel = { top: 382, height: 50 }
  const plotWidth = width - padding.left - padding.right
  const plotHeight = pricePanelHeight
  const high = Math.max(...data.bars.map((bar) => bar.high))
  const low = Math.min(...data.bars.map((bar) => bar.low))
  const priceRange = Math.max(high - low, 1)
  const y = (price: number) => padding.top + ((high - price) / priceRange) * plotHeight
  const x = (index: number) => padding.left + (data.bars.length === 1 ? plotWidth / 2 : (index / (data.bars.length - 1)) * plotWidth)
  const candleWidth = Math.max(4, Math.min(18, plotWidth / Math.max(data.bars.length, 1) * 0.56))
  const indexByDate = new Map(data.bars.map((bar, index) => [bar.date, index]))
  const markerByDate = data.signals.filter((signal) => signal.direction === 'buy' || signal.direction === 'sell')
  const overlays = data.overlays || []
  const zuoyiOverlay = overlays.find((item) => item.type === 'zuoyi')
  const emaOverlay = overlays.find((item) => item.type === 'ema')
  const rsiOverlay = overlays.find((item) => item.type === 'rsi')
  const volumeOverlay = overlays.find((item) => item.type === 'volume')
  const pctOverlay = overlays.find((item) => item.type === 'pct_change')
  const hoveredBar = hoveredIndex === null ? null : data.bars[hoveredIndex]
  const tooltipWidth = 150
  const tooltipHeight = 116
  const tooltipX = hoveredIndex === null ? 0 : Math.min(x(hoveredIndex) + 14, width - padding.right - tooltipWidth)
  const tooltipY = hoveredBar ? Math.max(padding.top, y(hoveredBar.high) - tooltipHeight - 10) : 0

  return (
    <div className="panel kline-panel">
      <div className="kline-heading">
        <h2>{data.symbol} K 线与买卖信号</h2>
        <div className="kline-legend">
          <span className="buy-dot">买入</span>
          <span className="sell-dot">卖出</span>
          {zuoyiOverlay && <span className="zuoyi-dot">左一</span>}
          {emaOverlay && <span className="ema-dot">EMA</span>}
        </div>
      </div>
      <svg className="kline-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.symbol} K线图`}>
        <line className="axis" x1={padding.left} y1={padding.top + plotHeight} x2={width - padding.right} y2={padding.top + plotHeight} />
        <line className="axis" x1={padding.left} y1={padding.top} x2={padding.left} y2={padding.top + plotHeight} />
        {[high, low].map((price) => (
          <g key={price}>
            <text className="price-label" x={width - padding.right + 8} y={y(price) + 4}>{price.toFixed(2)}</text>
          </g>
        ))}
        {renderEmaOverlay(emaOverlay, indexByDate, x, y)}
        {renderZuoYiOverlay(zuoyiOverlay, indexByDate, x, y)}
        {renderPctOverlay(pctOverlay, indexByDate, x, y, data)}
        {data.bars.map((bar, index) => {
          const cx = x(index)
          const openY = y(bar.open)
          const closeY = y(bar.close)
          const bullish = bar.close >= bar.open
          return (
            <g
              key={bar.date}
              className={bullish ? 'candle up' : 'candle down'}
              onMouseEnter={() => setHoveredIndex(index)}
              onMouseLeave={() => setHoveredIndex(null)}
              onFocus={() => setHoveredIndex(index)}
              onBlur={() => setHoveredIndex(null)}
              tabIndex={0}
            >
              <line x1={cx} y1={y(bar.high)} x2={cx} y2={y(bar.low)} />
              <rect x={cx - candleWidth / 2} y={Math.min(openY, closeY)} width={candleWidth} height={Math.max(Math.abs(openY - closeY), 2)} rx="2" />
              <rect className="candle-hitbox" x={cx - Math.max(candleWidth, 12) / 2} y={padding.top} width={Math.max(candleWidth, 12)} height={plotHeight} />
              {(index === 0 || index === data.bars.length - 1) && <text className="date-label" x={cx} y={height - 14}>{bar.date.slice(5)}</text>}
            </g>
          )
        })}
        {hoveredBar && (
          <g className="kline-tooltip" transform={`translate(${tooltipX} ${tooltipY})`} pointerEvents="none">
            <rect width={tooltipWidth} height={tooltipHeight} rx="8" />
            <text x="12" y="22" className="tooltip-title">{hoveredBar.date}</text>
            <text x="12" y="44">开盘 <tspan>{formatPrice(hoveredBar.open)}</tspan></text>
            <text x="12" y="62">最高 <tspan>{formatPrice(hoveredBar.high)}</tspan></text>
            <text x="12" y="80">最低 <tspan>{formatPrice(hoveredBar.low)}</tspan></text>
            <text x="12" y="98">收盘 <tspan>{formatPrice(hoveredBar.close)}</tspan></text>
          </g>
        )}
        {markerByDate.map((signal, markerIndex) => {
          const index = indexByDate.get(signal.date)
          if (index === undefined) return null
          const bar = data.bars[index]
          const cx = x(index)
          const baseY = signal.direction === 'buy' ? y(bar.low) + 18 : y(bar.high) - 18
          const markerClass = signal.direction === 'buy' ? 'marker buy' : 'marker sell'
          const label = signal.direction === 'buy' ? 'B' : 'S'
          return (
            <g key={`${signal.signal_id}-${markerIndex}`} className={markerClass}>
              <circle cx={cx} cy={baseY} r="10" />
              <text x={cx} y={baseY + 4}>{label}</text>
            <title>{`${label} ${signal.date} ${signal.reason}`}</title>
          </g>
          )
        })}
        {renderRsiOverlay(rsiOverlay, indexByDate, x, padding.left, width - padding.right, rsiPanel)}
        {renderVolumeOverlay(volumeOverlay, indexByDate, x, padding.left, width - padding.right, volumePanel)}
      </svg>
      {overlays.length > 0 && (
        <div className="strategy-overlay-list">
          {overlays.map((overlay) => (
            <span key={`${overlay.type}-${overlay.rule_key}`}>{overlay.rule_name}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function renderEmaOverlay(overlay: QuantStrategyOverlay | undefined, indexByDate: Map<string, number>, x: (index: number) => number, y: (price: number) => number) {
  if (!overlay?.lines?.length) return null
  return (
    <g className="ema-overlay">
      {overlay.lines.map((line, lineIndex) => (
        <polyline key={line.name} className={lineIndex === 0 ? 'ema-fast' : 'ema-slow'} points={line.points.map((point) => {
          const index = indexByDate.get(point.date)
          return index === undefined ? '' : `${x(index)},${y(point.value)}`
        }).filter(Boolean).join(' ')} />
      ))}
      {overlay.signals?.map((signal, index) => {
        const pointIndex = indexByDate.get(signal.date)
        if (pointIndex === undefined) return null
        return <circle key={`${signal.date}-${index}`} className="ema-signal" cx={x(pointIndex)} cy={18} r="5"><title>{signal.label || overlay.rule_name}</title></circle>
      })}
    </g>
  )
}

function renderZuoYiOverlay(overlay: QuantStrategyOverlay | undefined, indexByDate: Map<string, number>, x: (index: number) => number, y: (price: number) => number) {
  if (!overlay?.items?.length) return null
  return (
    <g className="zuoyi-overlay">
      {overlay.items.map((item, index) => {
        const leftIndex = indexByDate.get(item.left_one_date)
        const medianIndex = indexByDate.get(item.median_date)
        const breakoutIndex = indexByDate.get(item.breakout_date)
        if (leftIndex === undefined || medianIndex === undefined || breakoutIndex === undefined) return null
        const highY = y(item.left_one_high)
        const lowY = y(item.left_one_low)
        return (
          <g key={`${item.direction}-${item.left_one_date}-${item.breakout_date}-${index}`}>
            <line x1={x(leftIndex)} y1={highY} x2={x(breakoutIndex)} y2={highY} />
            <line x1={x(leftIndex)} y1={lowY} x2={x(breakoutIndex)} y2={lowY} />
            <circle className="median-point" cx={x(medianIndex)} cy={y(item.direction === 'bullish' ? item.median_low || item.left_one_low : item.median_high || item.left_one_high)} r="5" />
            <rect className="left-one-point" x={x(leftIndex) - 5} y={(highY + lowY) / 2 - 5} width="10" height="10" rx="2" />
            <text x={x(breakoutIndex)} y={item.direction === 'bullish' ? highY - 8 : lowY + 16}>{item.direction === 'bullish' ? '左一突破' : '左一跌破'}</text>
            <title>{`左一战法 ${item.direction}：左一 ${item.left_one_date}，中位 ${item.median_date}，突破 ${item.breakout_date}`}</title>
          </g>
        )
      })}
    </g>
  )
}

function renderPctOverlay(overlay: QuantStrategyOverlay | undefined, indexByDate: Map<string, number>, x: (index: number) => number, y: (price: number) => number, data: QuantSymbolChart) {
  if (!overlay?.signals?.length) return null
  return (
    <g className="pct-overlay">
      {overlay.signals.map((signal, index) => {
        const pointIndex = indexByDate.get(signal.date)
        if (pointIndex === undefined) return null
        const bar = data.bars[pointIndex]
        return (
          <g key={`${signal.rule_key}-${signal.date}-${index}`}>
            <path d={`M ${x(pointIndex)} ${y(bar.high) - 26} l 7 7 l -7 7 l -7 -7 z`} />
            <text x={x(pointIndex)} y={y(bar.high) - 32}>{formatSignedPercent(signal.pct_change)}</text>
            <title>{`${signal.rule_name || '单日涨跌幅'} ${formatSignedPercent(signal.pct_change)}`}</title>
          </g>
        )
      })}
    </g>
  )
}

function renderRsiOverlay(overlay: QuantStrategyOverlay | undefined, indexByDate: Map<string, number>, x: (index: number) => number, left: number, right: number, panel: { top: number; height: number }) {
  if (!overlay?.lines?.length) return null
  const yRsi = (value: number) => panel.top + panel.height - (Math.max(0, Math.min(100, value)) / 100) * panel.height
  return (
    <g className="indicator-panel rsi-panel">
      <text x={left} y={panel.top - 8}>{overlay.rule_name}</text>
      <line x1={left} y1={panel.top + panel.height} x2={right} y2={panel.top + panel.height} />
      {overlay.thresholds?.map((threshold) => (
        <line key={threshold} className="threshold" x1={left} y1={yRsi(threshold)} x2={right} y2={yRsi(threshold)} />
      ))}
      {overlay.lines.map((line) => (
        <polyline key={line.name} points={line.points.map((point) => {
          const index = indexByDate.get(point.date)
          return index === undefined ? '' : `${x(index)},${yRsi(point.value)}`
        }).filter(Boolean).join(' ')} />
      ))}
      {overlay.signals?.map((signal, index) => {
        const pointIndex = indexByDate.get(signal.date)
        if (pointIndex === undefined || signal.value == null) return null
        return <circle key={`${signal.date}-${index}`} cx={x(pointIndex)} cy={yRsi(signal.value)} r="4"><title>{signal.rule_name}</title></circle>
      })}
    </g>
  )
}

function renderVolumeOverlay(overlay: QuantStrategyOverlay | undefined, indexByDate: Map<string, number>, x: (index: number) => number, left: number, right: number, panel: { top: number; height: number }) {
  if (!overlay?.bars?.length) return null
  const maxVolume = Math.max(...overlay.bars.map((bar) => bar.value), 1)
  const barWidth = 4
  return (
    <g className="indicator-panel volume-panel">
      <text x={left} y={panel.top - 8}>{overlay.rule_name}</text>
      <line x1={left} y1={panel.top + panel.height} x2={right} y2={panel.top + panel.height} />
      {overlay.bars.map((bar) => {
        const index = indexByDate.get(bar.date)
        if (index === undefined) return null
        const h = Math.max(1, (bar.value / maxVolume) * panel.height)
        return <rect key={bar.date} x={x(index) - barWidth / 2} y={panel.top + panel.height - h} width={barWidth} height={h} />
      })}
      {overlay.signals?.map((signal, index) => {
        const pointIndex = indexByDate.get(signal.date)
        if (pointIndex === undefined) return null
        return <circle key={`${signal.date}-${index}`} cx={x(pointIndex)} cy={panel.top + 8} r="4"><title>{`${overlay.rule_name}: ${signal.today_volume || '-'}`}</title></circle>
      })}
    </g>
  )
}

function formatPrice(value: number) {
  return Number.isFinite(value) ? value.toFixed(2) : '-'
}

function formatSignedPercent(value?: number | null) {
  if (value === undefined || value === null || Number.isNaN(value)) return '-'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}
