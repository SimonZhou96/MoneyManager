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
  timeframe: Timeframe
  symbol: string
  markers?: TradeMarker[]
}

export function KlineChart({ rows, loading, error, timeframe, symbol, markers }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<{ chart: any; candleSeries: any; volumeSeries: any } | null>(null)

  // Init / dispose chart
  useEffect(() => {
    return () => {
      if (chartRef.current) {
        try { chartRef.current.chart.remove() } catch (_) { /* ignore */ }
        chartRef.current = null
      }
    }
  }, [])

  // Sync data
  useEffect(() => {
    if (!containerRef.current) return
    const container = containerRef.current

    // Lazy import to ensure lightweight-charts is loaded
    const initChart = async () => {
      const { createChart, CandlestickSeries, HistogramSeries } = await import('lightweight-charts')

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

      // Fit on data
      chart.timeScale().fitContent()
    }

    initChart().catch(console.error)
  }, [timeframe])

  // Update data
  useEffect(() => {
    const ref = chartRef.current
    if (!ref || rows.length === 0) return

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
        return {
          time: time.replace(' ', 'T').slice(0, 19) as any,
          open, high, low, close,
        }
      })
      .filter(Boolean) as any[]

    if (candleData.length === 0 && rows.length > 0) {
      console.warn('KlineChart: all rows filtered! First raw row:', rows[0],
        'Parsed time:', String(rows[0].at || rows[0].date || rows[0].time || rows[0].t || ''),
        'Parsed open:', Number((rows[0] as any).o ?? rows[0].open ?? 0))
    }

    const volumeData = rows
      .map(row => {
        const time = String(row.at || row.date || row.time || row.t || '')
        const volume = Number((row as any).v ?? row.volume ?? 0)
        if (!time) return null
        const closeVal = Number((row as any).c ?? row.close ?? 0)
        const openVal = Number((row as any).o ?? row.open ?? 0)
        return {
          time: time.replace(' ', 'T').slice(0, 19) as any,
          value: volume,
          color: closeVal >= openVal
            ? 'rgba(34,197,94,0.25)'
            : 'rgba(239,68,68,0.25)',
        }
      })
      .filter(Boolean) as any[]

    try {
      if (candleData.length > 0) {
        ref.candleSeries.setData(candleData.slice(-200))
        ref.volumeSeries.setData(volumeData.slice(-200))
        ref.chart.timeScale().fitContent()
      }
    } catch (_) { /* ignore setData errors */ }
  }, [rows])

  // Overlay trade markers (buy/sell arrows)
  useEffect(() => {
    const ref = chartRef.current
    if (!ref?.candleSeries) return
    if (!markers || markers.length === 0) {
      try { ref.candleSeries.setMarkers([]) } catch (_) { /* ignore */ }
      return
    }
    const formatted = markers
      .map(m => ({
        time: String(m.time || '').replace(' ', 'T').slice(0, 19) as any,
        position: m.side === 'buy' ? 'belowBar' as const : 'aboveBar' as const,
        shape: m.side === 'buy' ? 'arrowUp' as const : 'arrowDown' as const,
        color: m.side === 'buy' ? '#22c55e' : '#ef4444',
        text: m.side === 'buy' ? '买入' : '卖出',
        size: 2,
      }))
      .filter(m => !!m.time)
    try { ref.candleSeries.setMarkers(formatted) } catch (_) { /* ignore */ }
  }, [markers])

  return (
    <div className="kline-chart-panel">
      <div className="kline-chart-heading">
        <span className="symbol-name">{symbol}</span>
        <span className="timeframe-badge">{timeframe}</span>
      </div>
      <div className="kline-chart-wrap">
        {loading && (
          <div className="chart-loading-overlay">
            <div className="chart-loading-spinner" />
            <span>加载K线数据...</span>
          </div>
        )}
        {error && (
          <div className="chart-error-overlay">
            <span>{error}</span>
          </div>
        )}
        {!loading && !error && rows.length === 0 && (
          <div className="chart-empty-overlay">
            <span>暂无K线数据</span>
          </div>
        )}
        <div ref={containerRef} className="kline-chart-container" />
      </div>
    </div>
  )
}
