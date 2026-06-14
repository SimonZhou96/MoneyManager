import React, { useEffect, useRef } from 'react'
import type { Timeframe } from '../types'

export interface TradeMarker {
  time: string
  side: 'buy' | 'sell'
  price?: number
}

interface Props {
  rows: Array<Record<string, unknown>>
  loading: boolean
  error: string
  diagnostics?: { status: string; source: string; error_message?: string } | null
  timeframe: Timeframe
  symbol: string
  markers?: TradeMarker[]
}

/** 将原始行数据转换为 lightweight-charts 格式 */
function toChartTime(raw: string, timeframe: Timeframe): string {
  const iso = raw.replace(' ', 'T')
  // 日线/周线/月线 → YYYY-MM-DD（10字符）；分钟线 → ISO datetime（19字符）
  const isDaily = timeframe === '1d' || timeframe === '1wk' || timeframe === '1mo'
  return isDaily ? iso.slice(0, 10) : iso.slice(0, 19)
}

function applyData(
  chart: any, candleSeries: any, volumeSeries: any,
  rows: Array<Record<string, unknown>>,
  timeframe: Timeframe,
) {
  const candleData = rows
    .map((row, idx) => {
      const time = String(row.at || row.date || row.time || row.t || '')
      const open = Number((row as any).o ?? row.open ?? 0)
      const high = Number((row as any).h ?? row.high ?? 0)
      const low = Number((row as any).l ?? row.low ?? 0)
      const close = Number((row as any).c ?? row.close ?? 0)
      if (!time || !Number.isFinite(open)) {
        if (idx === 0) console.warn('KlineChart: first row has invalid fields', row)
        return null
      }
      const chartTime = toChartTime(time, timeframe)
      return { time: chartTime as any, open, high, low, close }
    })
    .filter(Boolean) as any[]

  if (candleData.length === 0) {
    console.warn('KlineChart: all rows filtered! first row:', rows[0])
    return
  }

  const volumeData = rows
    .map(row => {
      const time = String(row.at || row.date || row.time || row.t || '')
      const volume = Number((row as any).v ?? row.volume ?? 0)
      if (!time) return null
      const closeVal = Number((row as any).c ?? row.close ?? 0)
      const openVal = Number((row as any).o ?? row.open ?? 0)
      return {
        time: toChartTime(time, timeframe) as any,
        value: volume,
        color: closeVal >= openVal
          ? 'rgba(34,197,94,0.25)'
          : 'rgba(239,68,68,0.25)',
      }
    })
    .filter(Boolean) as any[]

  try {
    candleSeries.setData(candleData.slice(-200))
    volumeSeries.setData(volumeData.slice(-200))
    chart.timeScale().fitContent()
  } catch (e) {
    console.error('KlineChart setData error:', e, 'first candle:', candleData[0])
  }
}

export function KlineChart({ rows, loading, error, diagnostics, timeframe, symbol, markers }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<{ chart: any; candleSeries: any; volumeSeries: any } | null>(null)
  const rowsRef = useRef(rows)
  rowsRef.current = rows  // 始终保持最新 rows 引用，供异步回调读取

  // Dispose chart on unmount
  useEffect(() => {
    return () => {
      if (chartRef.current) {
        try { chartRef.current.chart.remove() } catch (_) { /* ignore */ }
        chartRef.current = null
      }
    }
  }, [])

  // Init chart (re-create when timeframe changes)
  useEffect(() => {
    if (!containerRef.current) return
    const container = containerRef.current

    let cancelled = false

    const initChart = async () => {
      const { createChart, CandlestickSeries, HistogramSeries } = await import('lightweight-charts')
      if (cancelled) return

      // Clean up previous
      if (chartRef.current) {
        try { chartRef.current.chart.remove() } catch (_) { /* ignore */ }
        chartRef.current = null
      }

      const chart = createChart(container, {
        layout: {
          background: { color: '#141820' },
          textColor: '#8a8070',
        },
        grid: {
          vertLines: { color: 'rgba(255,255,255,0.04)' },
          horzLines: { color: 'rgba(255,255,255,0.04)' },
        },
        crosshair: {
          vertLine: { color: 'rgba(201,168,76,0.3)', width: 1, style: 2 },
          horzLine: { color: 'rgba(201,168,76,0.3)', width: 1, style: 2 },
        },
        rightPriceScale: {
          borderColor: 'rgba(255,255,255,0.06)',
          scaleMargins: { top: 0.1, bottom: 0.25 },
        },
        timeScale: {
          borderColor: 'rgba(255,255,255,0.06)',
          timeVisible: true,
          secondsVisible: timeframe === '1m',
        },
        handleScroll: { vertTouchDrag: false },
      })

      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: '#22c55e',
        downColor: '#ef4444',
        borderUpColor: '#22c55e',
        borderDownColor: '#ef4444',
        wickUpColor: '#22c55e',
        wickDownColor: '#ef4444',
      })

      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: 'volume',
        priceFormat: { type: 'volume' },
      })

      chart.priceScale('volume').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      })

      volumeSeries.applyOptions({
        color: 'rgba(201,168,76,0.15)',
      })

      chartRef.current = { chart, candleSeries, volumeSeries }

      // 异步初始化完成后立即应用已到达的数据（解决竞态）
      if (!cancelled && rowsRef.current.length > 0) {
        applyData(chart, candleSeries, volumeSeries, rowsRef.current, timeframe)
      } else {
        chart.timeScale().fitContent()
      }
    }

    initChart().catch(console.error)
    return () => { cancelled = true }
  }, [timeframe])

  // Update data when rows change
  useEffect(() => {
    const ref = chartRef.current
    if (!ref || rows.length === 0) return
    applyData(ref.chart, ref.candleSeries, ref.volumeSeries, rows, timeframe)
  }, [rows, timeframe])

  // Overlay trade markers
  useEffect(() => {
    const ref = chartRef.current
    if (!ref?.candleSeries) return
    if (!markers || markers.length === 0) {
      try { ref.candleSeries.setMarkers([]) } catch (_) { /* ignore */ }
      return
    }
    const formatted = markers
      .map(m => ({
        time: toChartTime(String(m.time || ''), timeframe) as any,
        position: m.side === 'buy' ? 'belowBar' as const : 'aboveBar' as const,
        shape: m.side === 'buy' ? 'arrowUp' as const : 'arrowDown' as const,
        color: m.side === 'buy' ? '#22c55e' : '#ef4444',
        text: m.side === 'buy' ? '买入' : '卖出',
        size: 2,
      }))
      .filter(m => !!m.time)
    try { ref.candleSeries.setMarkers(formatted) } catch (_) { /* ignore */ }
  }, [markers, timeframe])

  return (
    <div className="kline-chart-panel">
      <div className="kline-chart-heading">
        <span className="symbol-name">{symbol}</span>
        <span className="timeframe-badge">{timeframe}</span>
      </div>
      <div className="kline-chart-wrap">
        {loading && (
          <div className="chart-loading-overlay">
            <div className="kline-skeleton-bars">
              {Array.from({length: 36}).map((_, i) => (
                <div key={i} className={`skeleton-cell sk-bar${i % 3}`}
                  style={{
                    flex: 1, minWidth: 4, maxWidth: 14,
                    height: `${22 + Math.sin(i * 0.25) * 14 + (i % 5) * 7}%`,
                    animationDelay: `${i * 0.04}s`,
                  }}
                />
              ))}
            </div>
            <div className="chart-loading-spinner" />
            <span>加载K线数据...</span>
            <span className="chart-loading-hint">正在从多个数据源获取，请稍候</span>
          </div>
        )}
        {error && (
          <div className="chart-error-overlay">
            <span className="chart-error-icon">⚠️</span>
            <span className="chart-error-title">K线数据加载失败</span>
            <span className="chart-error-detail">{error}</span>
            {diagnostics?.source && (
              <span className="chart-error-source">数据源: {diagnostics.source}</span>
            )}
            <div className="chart-error-hints">
              <span>建议：切换至更长周期（如周线/月线），或检查股票代码是否正确</span>
            </div>
          </div>
        )}
        {!loading && !error && rows.length === 0 && (
          <div className="chart-empty-overlay">
            <span className="chart-empty-icon">📊</span>
            <span className="chart-empty-title">暂无K线数据</span>
            {diagnostics?.error_message ? (
              <span className="chart-empty-detail text-danger">{diagnostics.error_message}</span>
            ) : diagnostics?.source ? (
              <span className="chart-empty-detail">数据源 {diagnostics.source} 无数据返回</span>
            ) : (
              <span className="chart-empty-detail">该股票在当前周期下暂无可用K线数据</span>
            )}
            <span className="chart-empty-hint">建议：切换至更长周期（周线/月线），或检查股票代码是否正确</span>
          </div>
        )}
        <div ref={containerRef} className="kline-chart-container" />
      </div>
    </div>
  )
}
