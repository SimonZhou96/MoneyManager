/**
 * K 线图组件 — lightweight-charts 封装。
 *
 * ⚠️  **铁律：任何 series.setData() 调用前必须确保数据按 time 去重且升序。**
 *
 * lightweight-charts 内部将时间字符串转为 Unix timestamp，重复时间戳会抛：
 *   "Assertion failed: data must be asc ordered by time, index=N, time=T, prev time=T"
 *
 * 去重必须在以下 **每一个** setData 调用点执行：
 *   1. candleSeries.setData()   — buildCandleData() 内已去重
 *   2. volumeSeries.setData()   — _applyAll() 内 volSeen Set 去重
 *   3. MA LineSeries.setData()  — _syncMA() 内尾行去重
 *   4. MACD series.setData()   — _syncMACD() 内尾行去重
 *
 * 已犯多次 → 参见 CLAUDE.md fix log: 2026-06-13 / 2026-06-15 / 2026-06-15(2)
 */
import React, { useEffect, useRef, useState } from 'react'
import type { Timeframe } from '../types'

export interface TradeMarker {
  time: string
  side: 'buy' | 'sell'
  price?: number
  /** 自定义标签文本（规则标记时使用） */
  label?: string
  /** 自定义颜色（规则标记时使用），不传则根据 side 自动选择 */
  color?: string
  /** 自定义形状（规则标记时使用），不传则根据 side 自动选择 */
  shape?: 'arrowUp' | 'arrowDown' | 'circle' | 'square'
}

/** 规则标记：将 rule_details 中有日期的规则命中映射为图表标记 */
export interface RuleChartMarker {
  time: string
  ruleName: string
  result: 'pass' | 'fail'
  ruleKey: string
  color?: string
}

interface Props {
  rows: Array<Record<string, unknown>>
  loading: boolean
  loadingMore?: boolean
  error: string
  diagnostics?: { status: string; source: string; error_message?: string } | null
  timeframe: Timeframe
  symbol: string
  markers?: TradeMarker[]
  showMA?: boolean
  showVolume?: boolean
  showMACD?: boolean
  /** 用户向左滚动到最早数据时触发，用于分页加载更早的 K 线 */
  onNeedOlderData?: () => void
}

/** 将原始行数据转换为 lightweight-charts 格式 */
function toChartTime(raw: string, timeframe: Timeframe): string {
  const iso = raw.replace(' ', 'T')
  const isDaily = timeframe === '1d' || timeframe === '1wk' || timeframe === '1mo'
  return isDaily ? iso.slice(0, 10) : iso.slice(0, 19)
}

// ── 指标计算 ──

function sma(values: number[], period: number): (number | null)[] {
  const result: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= period) sum -= values[i - period]
    result.push(i >= period - 1 ? sum / period : null)
  }
  return result
}

function ema(values: number[], period: number): (number | null)[] {
  const result: (number | null)[] = []
  const k = 2 / (period + 1)
  let prev: number | null = null
  for (let i = 0; i < values.length; i++) {
    if (i < period - 1) {
      result.push(null)
      continue
    }
    if (prev === null) {
      // 首个值用 SMA 初始化
      let sum = 0
      for (let j = i - period + 1; j <= i; j++) sum += values[j]
      prev = sum / period
    } else {
      prev = values[i] * k + prev * (1 - k)
    }
    result.push(prev)
  }
  return result
}

interface CandleLike { time: string; close: number }
interface MACDData { time: string; dif: number | null; dea: number | null; macd: number | null }

function computeMACD(candles: CandleLike[], fast = 12, slow = 26, signal = 9): MACDData[] {
  const closes = candles.map(c => c.close)
  const fastEMA = ema(closes, fast)
  const slowEMA = ema(closes, slow)
  const dif: (number | null)[] = []
  for (let i = 0; i < closes.length; i++) {
    if (fastEMA[i] != null && slowEMA[i] != null) {
      dif.push(fastEMA[i]! - slowEMA[i]!)
    } else {
      dif.push(null)
    }
  }
  const difVals = dif.map(v => v ?? 0)
  const dea = ema(difVals, signal)
  // DEA should be null where DIF is null
  for (let i = 0; i < dea.length; i++) {
    if (dif[i] == null) dea[i] = null
  }
  const result: MACDData[] = []
  for (let i = 0; i < candles.length; i++) {
    result.push({
      time: candles[i].time,
      dif: dif[i] ?? null,
      dea: dea[i] ?? null,
      macd: (dif[i] != null && dea[i] != null) ? (dif[i]! - dea[i]!) * 2 : null,
    })
  }
  return result
}

// ── 数据应用 ──

function buildCandleData(rows: Array<Record<string, unknown>>, timeframe: Timeframe) {
  const candles = rows
    .map((row) => {
      const time = String(row.at || row.date || row.time || row.t || '')
      const open = Number((row as any).o ?? row.open ?? 0)
      const high = Number((row as any).h ?? row.high ?? 0)
      const low = Number((row as any).l ?? row.low ?? 0)
      const close = Number((row as any).c ?? row.close ?? 0)
      if (!time || !Number.isFinite(open)) return null
      const chartTime = toChartTime(time, timeframe)
      return { time: chartTime, open, high, low, close }
    })
    .filter(Boolean) as { time: string; open: number; high: number; low: number; close: number }[]

  // 去重
  const seen = new Set<string>()
  return candles.filter(c => {
    if (seen.has(c.time)) return false
    seen.add(c.time)
    return true
  })
}

interface ChartRef {
  chart: any
  candleSeries: any
  volumeSeries: any
  maSeries: any[]
  macdDIF: any
  macdDEA: any
  macdHistogram: any
  indicatorData: CandleLike[] | null
  LineSeries: any
  HistogramSeries: any
}

function _applyMarkers(chartRef: ChartRef | null, markers: TradeMarker[] | undefined, timeframe: Timeframe) {
  if (!chartRef?.candleSeries) return
  if (!markers || markers.length === 0) {
    try { chartRef.candleSeries.setMarkers([]) } catch (_) { /* ignore */ }
    return
  }
  const formatted = markers
    .map(m => {
      const side: string = m.side || 'buy'
      const isBuy = side === 'buy'
      return {
        time: toChartTime(String(m.time || ''), timeframe) as any,
        position: (isBuy ? 'belowBar' : 'aboveBar') as 'belowBar' | 'aboveBar',
        shape: (m.shape || (isBuy ? 'arrowUp' : 'arrowDown')) as 'arrowUp' | 'arrowDown' | 'circle' | 'square',
        color: m.color || (isBuy ? '#22c55e' : '#ef4444'),
        text: m.label || (isBuy ? 'B' : 'S'),
        size: 3,
      }
    })
    .filter(m => !!m.time)
  console.warn(`[KlineChart] applying ${formatted.length} markers:`, formatted.map(m => `${m.time}:${m.text}`).join(', '))
  try { chartRef.candleSeries.setMarkers(formatted) } catch (e) { console.error('[KlineChart] setMarkers failed:', e) }
}

export function KlineChart({ rows, loading, loadingMore, error, diagnostics, timeframe, symbol, markers, showMA, showVolume, showMACD, onNeedOlderData }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ChartRef | null>(null)
  const rowsRef = useRef(rows)
  rowsRef.current = rows
  const showMARef = useRef(showMA)
  showMARef.current = showMA
  const showMACDRef = useRef(showMACD)
  showMACDRef.current = showMACD
  const onNeedOlderDataRef = useRef(onNeedOlderData)
  onNeedOlderDataRef.current = onNeedOlderData
  const isUpdatingRef = useRef(false)
  const isInitialRef = useRef(true)
  const [chartReady, setChartReady] = useState(false)

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
      const { createChart, CandlestickSeries, HistogramSeries, LineSeries } = await import('lightweight-charts')
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

      chartRef.current = {
        chart,
        candleSeries,
        volumeSeries,
        maSeries: [],
        macdDIF: null,
        macdDEA: null,
        macdHistogram: null,
        indicatorData: null,
        LineSeries,
        HistogramSeries,
      }

      // 监听用户滚动到左边界 → 触发分页加载
      chart.timeScale().subscribeVisibleTimeRangeChange((newRange: { from: any; to: any } | null) => {
        if (isUpdatingRef.current || !newRange || newRange.from == null) return
        const currentRows = rowsRef.current
        if (currentRows.length === 0) return
        // 找到当前数据中最旧的 bar 时间
        const oldest = currentRows.reduce((a, b) => {
          const ta = String((a as any).at || (a as any).date || (a as any).time || (a as any).t || '')
          const tb = String((b as any).at || (b as any).date || (b as any).time || (b as any).t || '')
          return ta < tb ? a : b
        })
        const oldestTime = String((oldest as any).at || (oldest as any).date || (oldest as any).time || (oldest as any).t || '')
        if (!oldestTime) return
        // 当可见范围左边界到达或超过最旧 bar 时触发
        if (String(newRange.from) <= oldestTime) {
          onNeedOlderDataRef.current?.()
        }
      })

      // 异步初始化完成后立即应用已到达的数据
      if (!cancelled && rowsRef.current.length > 0) {
        _applyAll(chartRef.current, rowsRef.current, timeframe, { showMA: showMARef.current, showMACD: showMACDRef.current })
        isInitialRef.current = false
      } else {
        chart.timeScale().fitContent()
      }
      if (!cancelled) setChartReady(true)
    }

    initChart().catch(console.error)
    return () => { cancelled = true; isInitialRef.current = true }
  }, [timeframe])

  // Update candle/volume when rows change — 保存/恢复可视范围避免跳动
  useEffect(() => {
    const ref = chartRef.current
    if (!ref || rows.length === 0) return

    isUpdatingRef.current = true
    const savedRange = !isInitialRef.current
      ? ref.chart.timeScale().getVisibleRange()
      : null

    _applyAll(ref, rows, timeframe, { showMA: showMA, showMACD: showMACD })

    if (savedRange && (savedRange as any).from != null && (savedRange as any).to != null) {
      try { ref.chart.timeScale().setVisibleRange(savedRange as any) } catch (_) { /* ignore */ }
    } else {
      ref.chart.timeScale().fitContent()
    }

    isInitialRef.current = false
    isUpdatingRef.current = false
  }, [rows, timeframe])

  // ── MA 均线 ──
  useEffect(() => {
    const ref = chartRef.current
    if (!ref) return
    _syncMA(ref, showMA ?? false)
  }, [showMA])

  // ── Volume 可见性 ──
  useEffect(() => {
    const ref = chartRef.current
    if (!ref) return
    const vis = showVolume ?? true
    try { ref.volumeSeries.applyOptions({ visible: vis }) } catch (_) { /* ignore */ }
    try {
      const volScale = ref.chart.priceScale('volume')
      if (volScale) volScale.applyOptions({ visible: vis })
    } catch (_) { /* ignore */ }
  }, [showVolume])

  // ── MACD ──
  useEffect(() => {
    const ref = chartRef.current
    if (!ref) return
    _syncMACD(ref, showMACD ?? false)
  }, [showMACD, rows])

  // Overlay trade markers
  useEffect(() => {
    _applyMarkers(chartRef.current, markers, timeframe)
  }, [markers, timeframe, chartReady])

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
        {loadingMore && (
          <div className="chart-loading-more-overlay">
            <span>加载更多K线数据...</span>
          </div>
        )}
        <div ref={containerRef} className="kline-chart-container" />
      </div>
    </div>
  )
}

// ── 内部辅助 ──

const MA_PERIODS = [5, 10, 20, 60]
const MA_COLORS = ['#fbbf24', '#f97316', '#e879f9', '#38bdf8'] // 黄、橙、紫、蓝

function _applyAll(ref: ChartRef, rows: Array<Record<string, unknown>>, timeframe: Timeframe, opts?: { showMA?: boolean; showMACD?: boolean }) {
  const candles = buildCandleData(rows, timeframe)
  if (candles.length === 0) return

  ref.candleSeries.setData(candles)

  // Volume — 注意：rows 可能有同日期多条记录，必须去重后再 setData，
  // 否则 lightweight-charts 抛 "data must be asc ordered by time"
  const candleTimeSet = new Set(candles.map(c => c.time))
  const volSeen = new Set<string>()
  const volumeData = rows
    .map(row => {
      const time = String(row.at || row.date || row.time || row.t || '')
      const volume = Number((row as any).v ?? row.volume ?? 0)
      if (!time) return null
      const chartTime = toChartTime(time, timeframe)
      if (!candleTimeSet.has(chartTime) || volSeen.has(chartTime)) return null
      volSeen.add(chartTime)
      const closeVal = Number((row as any).c ?? row.close ?? 0)
      const openVal = Number((row as any).o ?? row.open ?? 0)
      return {
        time: chartTime as any,
        value: volume,
        color: closeVal >= openVal
          ? 'rgba(34,197,94,0.25)'
          : 'rgba(239,68,68,0.25)',
      }
    })
    .filter(Boolean) as any[]
  ref.volumeSeries.setData(volumeData)

  // Store candle data for indicator computation
  ref.indicatorData = candles

  // 数据更新后同步指标（处理先开指标后加载数据的竞态）
  if (opts?.showMA) _syncMA(ref, true)
  if (opts?.showMACD) _syncMACD(ref, true)
}

// ── MA 同步 ──

function _syncMA(ref: ChartRef, show: boolean) {
  // 清理旧 MA 线
  for (const s of ref.maSeries) {
    try { ref.chart.removeSeries(s) } catch (_) { /* ignore */ }
  }
  ref.maSeries = []

  if (!show || !ref.indicatorData || ref.indicatorData.length === 0) return

  const closes = ref.indicatorData.map(c => c.close)
  const times = ref.indicatorData.map(c => c.time)
  // 只取尾部 200 根
  const tailCloses = closes.slice(-200)
  const tailTimes = times.slice(-200)

  MA_PERIODS.forEach((period, idx) => {
    if (tailCloses.length < period) return
    const maVals = sma(tailCloses, period)
    const data = tailTimes.map((t, i) => ({
      time: t as any,
      value: maVals[i],
    })).filter(d => d.value != null)

    // 去重：indicatorData 已去重，此处为安全兜底
    const maSeen = new Set<string>()
    const deduped = data.filter(d => {
      const k = String(d.time)
      if (maSeen.has(k)) return false
      maSeen.add(k)
      return true
    })

    if (deduped.length === 0) return

    const lineSeries = ref.chart.addSeries(ref.LineSeries, {
      color: MA_COLORS[idx],
      lineWidth: 1,
      priceScaleId: 'right',
    })
    lineSeries.setData(deduped)
    ref.maSeries.push(lineSeries)
  })
}

// ── MACD 同步 ──

function _syncMACD(ref: ChartRef, show: boolean) {
  // 清理旧 MACD 系列
  if (ref.macdDIF) { try { ref.chart.removeSeries(ref.macdDIF) } catch (_) { /* ignore */ } ref.macdDIF = null }
  if (ref.macdDEA) { try { ref.chart.removeSeries(ref.macdDEA) } catch (_) { /* ignore */ } ref.macdDEA = null }
  if (ref.macdHistogram) { try { ref.chart.removeSeries(ref.macdHistogram) } catch (_) { /* ignore */ } ref.macdHistogram = null }

  if (!show || !ref.indicatorData || ref.indicatorData.length === 0) {
    // 还原价格比例
    try {
      ref.candleSeries.applyOptions({ priceScaleId: 'right' })
      ref.chart.priceScale('right').applyOptions({
        scaleMargins: { top: 0.1, bottom: 0.25 },
      })
    } catch (_) { /* ignore */ }
    return
  }

  const candles = ref.indicatorData.slice(-200)
  if (candles.length < 26) return

  const macdData = computeMACD(candles)

  // 为 MACD 创建独立价格轴
  const macdScaleId = 'macd'

  // DIF 快线
  ref.macdDIF = ref.chart.addSeries(ref.LineSeries, {
    color: '#fbbf24',
    lineWidth: 1,
    priceScaleId: macdScaleId,
  })
  ref.macdDIF.setData(
    macdData.filter(d => d.dif != null).map(d => ({ time: d.time as any, value: d.dif }))
  )

  // DEA 慢线
  ref.macdDEA = ref.chart.addSeries(ref.LineSeries, {
    color: '#38bdf8',
    lineWidth: 1,
    priceScaleId: macdScaleId,
  })
  ref.macdDEA.setData(
    macdData.filter(d => d.dea != null).map(d => ({ time: d.time as any, value: d.dea }))
  )

  // 柱状图
  ref.macdHistogram = ref.chart.addSeries(ref.HistogramSeries, {
    priceScaleId: macdScaleId,
    priceFormat: { type: 'volume' },
  })
  ref.macdHistogram.setData(
    macdData.filter(d => d.macd != null).map(d => ({
      time: d.time as any,
      value: d.macd,
      color: d.macd! >= 0 ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.4)',
    }))
  )

  // 调整价格比例
  ref.chart.priceScale(macdScaleId).applyOptions({
    scaleMargins: { top: 0.8, bottom: 0.05 },
  })

  // 蜡烛图区域让出空间给 MACD
  ref.candleSeries.applyOptions({
    priceScaleId: 'right',
  })
  ref.chart.priceScale('right').applyOptions({
    scaleMargins: { top: 0.05, bottom: 0.35 },
  })
}
