import React, { useEffect, useState, useCallback, useMemo } from 'react'
import type { MarketCode, Timeframe, HotSector, SectorStock, SingleStockResult, BacktestResult } from './types'
import {
  getHotSectors,
  getSectorStocks,
  getKlines,
  submitSingleStock,
  getSingleStockResult,
  submitBacktest,
  getBacktestResult,
} from './api'
import { SectorTreemap } from './components/SectorTreemap'
import { SectorStockTable } from './components/SectorStockTable'
import { KlineChart, type TradeMarker } from './components/KlineChart'
import { TimeframeSelector } from './components/TimeframeSelector'
import { SingleStockAnalysis } from './components/SingleStockAnalysis'
import { BacktestPanel } from './components/BacktestPanel'

const MARKET_OPTIONS: { value: MarketCode; label: string }[] = [
  { value: 'HK', label: '港股' },
  { value: 'US', label: '美股' },
  { value: 'A', label: 'A股' },
]

const TIMEFRAMES: Timeframe[] = ['1d', '60m', '30m', '15m', '5m', '1m', '1wk']

export function MarketAnalysisPage() {
  // ── Market state ──
  const [market, setMarket] = useState<MarketCode>('HK')

  // ── Sector state ──
  const [sectors, setSectors] = useState<HotSector[]>([])
  const [sectorsLoading, setSectorsLoading] = useState(false)
  const [sectorsError, setSectorsError] = useState('')
  const [sectorsDate, setSectorsDate] = useState('')
  const [selectedSector, setSelectedSector] = useState<HotSector | null>(null)

  // ── Sector stocks state ──
  const [sectorStocks, setSectorStocks] = useState<SectorStock[]>([])
  const [stocksLoading, setStocksLoading] = useState(false)

  // ── Stock detail state ──
  const [selectedCode, setSelectedCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [klineRows, setKlineRows] = useState<Array<Record<string, unknown>>>([])
  const [klineLoading, setKlineLoading] = useState(false)
  const [klineError, setKlineError] = useState('')
  const [timeframe, setTimeframe] = useState<Timeframe>('1d')

  // ── Analysis state ──
  const [analysisResult, setAnalysisResult] = useState<SingleStockResult | null>(null)
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const [analysisRunId, setAnalysisRunId] = useState('')

  // ── Backtest state ──
  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null)
  const [backtestLoading, setBacktestLoading] = useState(false)
  const [backtestError, setBacktestError] = useState('')
  const [backtestRunId, setBacktestRunId] = useState('')

  // ── Compute trade markers from backtest result ──
  const backtestMarkers = useMemo<TradeMarker[]>(() => {
    if (!backtestResult?.trades || backtestResult.trades.length === 0) return []
    return backtestResult.trades
      .filter(t => t.date && t.side)
      .map(t => ({ time: t.date, side: t.side as 'buy' | 'sell', price: t.price }))
  }, [backtestResult?.trades])

  // ── Load sectors on market change ──
  const loadSectors = useCallback(async () => {
    setSectorsLoading(true)
    setSectorsError('')
    setSelectedSector(null)
    setSectorStocks([])
    try {
      const data = await getHotSectors(market, 15)
      setSectors(data.sectors || [])
      setSectorsDate((data as any).data_date || '')
    } catch (err) {
      setSectorsError(err instanceof Error ? err.message : '加载热点板块失败')
    } finally {
      setSectorsLoading(false)
    }
  }, [market])

  useEffect(() => { loadSectors() }, [loadSectors])

  // ── Load sector stocks on sector click ──
  const handleSectorClick = useCallback(async (sector: HotSector) => {
    setSelectedSector(sector)
    setStocksLoading(true)
    try {
      const data = await getSectorStocks(sector.name, market, 30)
      setSectorStocks(data.stocks || [])
    } catch (_err) {
      setSectorStocks([])
    } finally {
      setStocksLoading(false)
    }
  }, [market])

  // ── Load klines + submit analysis on stock click ──
  const handleStockClick = useCallback(async (stock: SectorStock) => {
    const code = stock.code
    setSelectedCode(code)
    setStockName(stock.name || code)
    setAnalysisResult(null)
    setAnalysisError('')
    setBacktestResult(null)

    // Load klines
    setKlineLoading(true)
    setKlineError('')
    console.log('[Kline] fetching', market, code, timeframe)
    try {
      const data = await getKlines(market, code, timeframe, 120)
      console.log('[Kline] got', data.rows?.length, 'rows')
      setKlineRows(data.rows || [])
    } catch (err) {
      setKlineError(err instanceof Error ? err.message : '加载K线失败')
    } finally {
      setKlineLoading(false)
    }

    // Submit single stock analysis
    setAnalysisLoading(true)
    try {
      const { run_id } = await submitSingleStock(market, code, timeframe)
      setAnalysisRunId(run_id)
      // Start polling
      const poll = async () => {
        try {
          const result = await getSingleStockResult(run_id)
          if (result.status === 'completed' || result.status === 'failed') {
            setAnalysisResult(result)
            setAnalysisLoading(false)
            setAnalysisRunId('')
            return
          }
          // Keep polling
          setTimeout(poll, 3000)
        } catch (_err) {
          setAnalysisError('获取分析结果失败')
          setAnalysisLoading(false)
          setAnalysisRunId('')
        }
      }
      setTimeout(poll, 2000)
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : '提交单股分析失败')
      setAnalysisLoading(false)
    }
  }, [market, timeframe])

  // ── Reload klines on timeframe change ──
  useEffect(() => {
    if (!selectedCode) return
    let cancelled = false
    setKlineLoading(true)
    setKlineError('')
    getKlines(market, selectedCode, timeframe, 120)
      .then(data => { if (!cancelled) setKlineRows(data.rows || []) })
      .catch(err => { if (!cancelled) setKlineError(err instanceof Error ? err.message : '加载K线失败') })
      .finally(() => { if (!cancelled) setKlineLoading(false) })
    return () => { cancelled = true }
  }, [timeframe, selectedCode, market])

  // ── Backtest handlers ──
  const handleBacktestSubmit = useCallback(async (config: {
    entry_chain_key: string
    exit_policy: { type: string; days?: number; pct?: number }
  }) => {
    if (!selectedCode) return
    setBacktestLoading(true)
    setBacktestError('')
    setBacktestResult(null)
    try {
      const today = new Date().toISOString().slice(0, 10)
      const yearAgo = new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10)
      const { run_id } = await submitBacktest({
        market,
        symbols: [selectedCode],
        strategy_source: 'rule_chain',
        entry_chain_key: config.entry_chain_key,
        exit_policy: config.exit_policy,
        start: yearAgo,
        end: today,
        initial_cash: 100000,
        quantity: 100,
        commission_rate: 0.001,
        slippage_rate: 0.001,
        max_position_weight: 1.0,
      })
      setBacktestRunId(run_id)
      const poll = async () => {
        try {
          const result = await getBacktestResult(run_id)
          if (result.status === 'completed' || result.status === 'failed') {
            setBacktestResult(result)
            setBacktestLoading(false)
            setBacktestRunId('')
            return
          }
          setBacktestResult(result) // Show progress
          setTimeout(poll, 1500)
        } catch (_err) {
          setBacktestError('获取回测结果失败')
          setBacktestLoading(false)
          setBacktestRunId('')
        }
      }
      setTimeout(poll, 1000)
    } catch (err) {
      setBacktestError(err instanceof Error ? err.message : '提交回测失败')
      setBacktestLoading(false)
    }
  }, [market, selectedCode])

  return (
    <div className="market-analysis-page">
      <header className="market-analysis-header">
        <div>
          <h1>大盘分析</h1>
          <p>热点板块、个股技术面与回测评估</p>
        </div>
        <div className="market-analysis-controls">
          <div className="market-select-wrapper">
            {MARKET_OPTIONS.map(opt => (
              <button
                key={opt.value}
                className={`market-chip${market === opt.value ? ' active' : ''}`}
                onClick={() => setMarket(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <button className="gold-btn secondary" onClick={loadSectors} disabled={sectorsLoading}>
            {sectorsLoading ? '加载中...' : '刷新'}
          </button>
        </div>
      </header>

      {sectorsError && <div className="error-banner">{sectorsError}</div>}

      {/* ── Section 1: Hot Sector Treemap ── */}
      <section className="sector-section">
        <div className="section-label">
          <span className="gold-dot" />
          热点板块
          {sectorsDate && (
            <span className="section-meta" style={{ marginLeft: 12, color: '#8a8070', fontSize: 12 }}>
              数据日期: {sectorsDate}
            </span>
          )}
          {selectedSector && (
            <span className="section-breadcrumb">
              <span className="breadcrumb-sep">›</span>
              {selectedSector.name}
            </span>
          )}
        </div>
        <SectorTreemap
          sectors={sectors}
          loading={sectorsLoading}
          selectedName={selectedSector?.name || null}
          onSectorClick={handleSectorClick}
        />
      </section>

      {/* ── Section 2: Sector Stock Table ── */}
      {selectedSector && (
        <section className="sector-section">
          <div className="section-label">
            <span className="gold-dot" />
            板块成分股
            <span className="section-meta">
              {selectedSector.name} · {sectorStocks.length} 只
              {selectedSector.change_pct !== undefined && selectedSector.change_pct !== 0 && (
                <span className={selectedSector.change_pct >= 0 ? 'up' : 'down'}>
                  {selectedSector.change_pct >= 0 ? '+' : ''}{selectedSector.change_pct.toFixed(2)}%
                </span>
              )}
            </span>
          </div>
          <SectorStockTable
            stocks={sectorStocks}
            loading={stocksLoading}
            selectedCode={selectedCode}
            onStockClick={handleStockClick}
          />
        </section>
      )}

      {/* ── Section 3: K-line Chart + Single Stock Analysis ── */}
      {selectedCode && (
        <section className="analysis-section">
          <div className="section-label">
            <span className="gold-dot" />
            个股分析
            <span className="section-meta">{stockName || selectedCode}</span>
          </div>

          <div className="timeframe-bar">
            <TimeframeSelector
              timeframes={TIMEFRAMES}
              value={timeframe}
              onChange={setTimeframe}
            />
          </div>

          <div className="analysis-grid">
            <div className="analysis-grid-kline">
              <KlineChart
                rows={klineRows}
                loading={klineLoading}
                error={klineError}
                timeframe={timeframe}
                symbol={stockName || selectedCode}
                markers={backtestMarkers}
              />
            </div>
            <div className="analysis-grid-detail">
              <SingleStockAnalysis
                result={analysisResult}
                loading={analysisLoading}
                error={analysisError}
              />
            </div>
          </div>
        </section>
      )}

      {/* ── Section 4: Backtest ── */}
      {selectedCode && (
        <section className="backtest-section">
          <div className="section-label">
            <span className="gold-dot" />
            策略回测
          </div>
          <BacktestPanel
            market={market}
            stockCode={selectedCode}
            result={backtestResult}
            loading={backtestLoading}
            error={backtestError}
            onSubmit={handleBacktestSubmit}
          />
        </section>
      )}
    </div>
  )
}
