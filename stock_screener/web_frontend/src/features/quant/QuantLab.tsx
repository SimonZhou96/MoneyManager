import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { api } from '../../api'
import { KlineChart } from '../marketAnalysis/components/KlineChart'
import type { TradeMarker } from '../marketAnalysis/components/KlineChart'
import type {
  StrategyMeta, StrategyBacktestRequest, OptimizationRequest,
  QuantBacktestStatus, OptimizationResultItem, QuantSymbolChart,
} from './types'

const TERMINAL = new Set(['completed', 'failed'])
const MARKETS = { HK: '港股', US: '美股', A: 'A股' } as const
const OBJECTIVES = { sharpe: 'Sharpe', total_return: '总收益', calmar: 'Calmar', win_rate: '胜率' } as const

export function QuantLab() {
  // ── 表单状态 ──
  const [market, setMarket] = useState('HK')
  const [symbol, setSymbol] = useState('HK.00700')
  const [strategies, setStrategies] = useState<StrategyMeta[]>([])
  const [strategyType, setStrategyType] = useState('ma_cross')
  const [params, setParams] = useState<Record<string, number>>({})
  const [entrySide, setEntrySide] = useState('long')
  const [dateRange, setDateRange] = useState({ start: '2025-01-01', end: '2026-01-01' })
  const [initialCash, setInitialCash] = useState(100000)
  const [quantity, setQuantity] = useState(10)

  // ── 风控 ──
  const [useRisk, setUseRisk] = useState(false)
  const [stopLoss, setStopLoss] = useState(0.08)
  const [takeProfit, setTakeProfit] = useState(0.20)
  const [trailingStop, setTrailingStop] = useState(0.05)

  // ── 优化 ──
  const [showOptimize, setShowOptimize] = useState(false)
  const [objective, setObjective] = useState('sharpe')
  const [paramSpaceText, setParamSpaceText] = useState('{"fast": [3,5,10], "slow": [10,20,30]}')

  // ── 运行状态 ──
  const [runId, setRunId] = useState('')
  const [run, setRun] = useState<QuantBacktestStatus | null>(null)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const isActive = Boolean(runId && run && !TERMINAL.has(run.status))

  // ── 加载策略列表 ──
  useEffect(() => {
    api<StrategyMeta[]>('/api/quant/strategies').then(list => {
      setStrategies(list)
      if (list.length > 0) {
        setStrategyType(list[0].type)
        const defaults: Record<string, number> = {}
        Object.entries(list[0].params).forEach(([k, v]) => { defaults[k] = v.default })
        setParams(defaults)
      }
    }).catch(() => {})
  }, [])

  // ── 切换策略时重置参数 ──
  const onStrategyChange = useCallback((type: string) => {
    setStrategyType(type)
    const meta = strategies.find(s => s.type === type)
    if (meta) {
      const defaults: Record<string, number> = {}
      Object.entries(meta.params).forEach(([k, v]) => { defaults[k] = v.default })
      setParams(defaults)
      if (showOptimize) {
        const space: Record<string, number[]> = {}
        Object.entries(meta.params).forEach(([k, v]) => {
          const step = v.type === 'int' ? Math.max(1, Math.round((v.max - v.min) / 5)) : (v.max - v.min) / 5
          space[k] = [v.default, Math.round(v.default + step), Math.round(v.default + step * 2)]
        })
        setParamSpaceText(JSON.stringify(space))
      }
    }
  }, [strategies, showOptimize])

  // ── 轮询 ──
  const refresh = useCallback(async (id: string) => {
    const r = await api<QuantBacktestStatus>(`/api/quant/backtests/${id}`)
    setRun(r)
    if (r.status === 'failed') setError(r.error_message || '执行失败')
  }, [])

  useEffect(() => {
    if (!runId || (run && TERMINAL.has(run.status))) return
    const t = setInterval(() => { refresh(runId).catch(() => {}) }, 1500)
    return () => clearInterval(t)
  }, [runId, run?.status, refresh])

  // ── 提交回测 ──
  const submit = async () => {
    setError(''); setRun(null); setRunId(''); setSubmitting(true)
    const payload: StrategyBacktestRequest = {
      market, symbols: [symbol],
      strategy: { type: strategyType, params, entry_side: entrySide },
      start: dateRange.start, end: dateRange.end,
      initial_cash: initialCash, quantity,
      commission_rate: 0.001, slippage_rate: 0.001, max_position_weight: 1.0,
    }
    if (useRisk) {
      payload.risk = {}
      if (stopLoss) payload.risk.stop_loss_pct = -Math.abs(stopLoss)
      if (takeProfit) payload.risk.take_profit_pct = takeProfit
      if (trailingStop) payload.risk.trailing_stop_pct = trailingStop
    }
    try {
      const r = await api<{ run_id: string }>('/api/quant/backtests/strategy', {
        method: 'POST', body: JSON.stringify(payload),
      })
      setRunId(r.run_id)
      await refresh(r.run_id)
    } catch (e: any) {
      setError(e?.message || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  // ── 提交优化 ──
  const submitOptimize = async () => {
    setError(''); setRun(null); setRunId(''); setSubmitting(true)
    let paramSpace: Record<string, number[]>
    try { paramSpace = JSON.parse(paramSpaceText) } catch {
      setError('参数空间 JSON 格式错误'); setSubmitting(false); return
    }
    const payload: OptimizationRequest = {
      market, symbol, strategy_type: strategyType,
      param_space: paramSpace, objective,
      start: dateRange.start, end: dateRange.end,
      initial_cash: initialCash, quantity,
    }
    try {
      const r = await api<{ run_id: string }>('/api/quant/backtests/optimize', {
        method: 'POST', body: JSON.stringify(payload),
      })
      setRunId(r.run_id)
      await refresh(r.run_id)
    } catch (e: any) {
      setError(e?.message || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  // ── 派生数据 ──
  const metrics = useMemo(() => run?.metrics || {}, [run])
  const selectedChart = useMemo(() => run?.chart?.symbols?.[0] as QuantSymbolChart | undefined, [run])
  const chartMarkers = useMemo<TradeMarker[]>(() => {
    if (!selectedChart?.trades) { console.warn('[QuantLab] no trades in selectedChart'); return [] }
    const result = selectedChart.trades
      .filter(t => t.date)
      .map(t => ({
        time: t.date!,
        side: t.side as 'buy' | 'sell',
        price: t.price,
        label: `${t.side === 'buy' ? 'B' : 'S'}@${t.price?.toFixed(1)}`,
      }))
    console.warn(`[QuantLab] chartMarkers: ${result.length} markers from ${selectedChart.trades.length} trades`)
    return result
  }, [selectedChart?.trades])
  const optimizationResults = useMemo(() => run?.chart?.optimization_results || [], [run])
  const currentStrategy = useMemo(() => strategies.find(s => s.type === strategyType), [strategies, strategyType])

  return (
    <section className="quant-lab">
      <header className="page-header">
        <h1>量化实验室</h1>
        <p>经典量化策略回测 — 均线交叉、MACD、RSI 等，支持参数网格搜索优化</p>
      </header>

      <div className="quant-lab-layout">
        {/* ── 左侧：配置面板 ── */}
        <aside className="panel quant-config">
          <label>市场</label>
          <select value={market} onChange={e => setMarket(e.target.value)}>
            {Object.entries(MARKETS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>

          <label>标的代码</label>
          <input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="HK.00700" />

          <label>策略类型</label>
          <select value={strategyType} onChange={e => onStrategyChange(e.target.value)}>
            {strategies.map(s => <option key={s.type} value={s.type}>{s.name}</option>)}
          </select>

          {/* 动态参数表单 */}
          {currentStrategy && Object.entries(currentStrategy.params).map(([key, def]) => (
            <div key={key} className="param-field">
              <label>{def.label || key}</label>
              <input
                type="number"
                value={params[key] ?? def.default}
                min={def.min} max={def.max}
                step={def.type === 'int' ? 1 : 0.01}
                onChange={e => setParams(prev => ({ ...prev, [key]: parseFloat(e.target.value) || 0 }))}
              />
            </div>
          ))}

          <label>方向</label>
          <select value={entrySide} onChange={e => setEntrySide(e.target.value)}>
            <option value="long">仅做多</option>
            <option value="both">多空双向</option>
          </select>

          <label>回测区间</label>
          <div className="date-range">
            <input type="date" value={dateRange.start} onChange={e => setDateRange(p => ({ ...p, start: e.target.value }))} />
            <span>—</span>
            <input type="date" value={dateRange.end} onChange={e => setDateRange(p => ({ ...p, end: e.target.value }))} />
          </div>

          <div className="param-row">
            <label>初始资金</label>
            <input type="number" value={initialCash} min={1000} step={10000} onChange={e => setInitialCash(Number(e.target.value))} />
          </div>

          <div className="param-row">
            <label>每笔数量</label>
            <input type="number" value={quantity} min={1} onChange={e => setQuantity(Number(e.target.value))} />
          </div>

          {/* 风控开关 */}
          <label className="checkbox-label">
            <input type="checkbox" checked={useRisk} onChange={e => setUseRisk(e.target.checked)} />
            启用风控
          </label>
          {useRisk && (
            <div className="risk-config">
              <div className="param-row">
                <label>止损</label>
                <input type="number" value={stopLoss} min={0.01} max={0.5} step={0.01} onChange={e => setStopLoss(Number(e.target.value))} />
                <span className="unit">{-stopLoss * 100}%</span>
              </div>
              <div className="param-row">
                <label>止盈</label>
                <input type="number" value={takeProfit} min={0.01} max={1.0} step={0.01} onChange={e => setTakeProfit(Number(e.target.value))} />
                <span className="unit">+{takeProfit * 100}%</span>
              </div>
              <div className="param-row">
                <label>移动止损</label>
                <input type="number" value={trailingStop} min={0.01} max={0.3} step={0.01} onChange={e => setTrailingStop(Number(e.target.value))} />
                <span className="unit">{trailingStop * 100}%</span>
              </div>
            </div>
          )}

          <div className="config-actions">
            <button onClick={submit} disabled={submitting || isActive} className="btn-primary">
              {submitting && !showOptimize ? '提交中...' : isActive ? '回测中...' : '开始回测'}
            </button>
          </div>

          {/* 参数优化 */}
          <details open={showOptimize} onToggle={e => setShowOptimize((e.target as HTMLDetailsElement).open)}>
            <summary>参数优化（网格搜索）</summary>
            <div className="optimize-config">
              <label>优化目标</label>
              <select value={objective} onChange={e => setObjective(e.target.value)}>
                {Object.entries(OBJECTIVES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <label>参数空间 (JSON)</label>
              <textarea
                value={paramSpaceText}
                onChange={e => setParamSpaceText(e.target.value)}
                rows={3}
                spellCheck={false}
              />
              <button onClick={submitOptimize} disabled={submitting || isActive} className="btn-secondary">
                {submitting && showOptimize ? '优化中...' : '开始优化'}
              </button>
            </div>
          </details>

          {error && <div className="error">{error}</div>}
        </aside>

        {/* ── 中间：图表区 ── */}
        <main className="quant-chart-area">
          {selectedChart && selectedChart.bars.length > 0 ? (
            <>
              <KlineChart
                rows={selectedChart.bars as any[]}
                loading={false}
                error={''}
                timeframe={'1d'}
                symbol={selectedChart.symbol || ''}
                markers={chartMarkers}
                showMA={true}
                showVolume={true}
                showMACD={false}
              />
              <TradeList data={selectedChart} />
            </>
          ) : (
            <div className="panel chart-placeholder">
              {run && run.status !== 'completed'
                ? <p>回测运行中... {run.current_stage} ({run.progress_pct}%)</p>
                : <p>配置策略参数后点击「开始回测」查看 K 线图与买卖信号</p>
              }
            </div>
          )}
          {/* 进度条 */}
          {run && !TERMINAL.has(run.status) && (
            <div className="panel quant-progress-panel">
              <div className="progress-header">
                <strong>{run.current_stage || run.status}</strong>
                <strong>{run.progress_pct}%</strong>
              </div>
              <div className={`progress-track ${run.status === 'failed' ? 'failed' : ''} ${run.status === 'completed' ? 'completed' : ''}`}>
                <span style={{ width: `${run.progress_pct}%` }} />
              </div>
              {run.error_message && <div className="error">{run.error_message}</div>}
            </div>
          )}
        </main>

        {/* ── 右侧：指标 + 结果 ── */}
        <aside className="quant-results">
          {/* 绩效指标卡 */}
          <div className="metrics-grid">
            <MetricCard label="Sharpe" value={metrics.sharpe} fmt="decimal" />
            <MetricCard label="年化收益" value={metrics.cagr} fmt="percent" />
            <MetricCard label="总收益" value={metrics.total_return} fmt="percent" />
            <MetricCard label="最大回撤" value={metrics.max_drawdown} fmt="percent" />
            <MetricCard label="胜率" value={metrics.win_rate} fmt="percent" />
            <MetricCard label="盈亏比" value={metrics.win_loss_ratio} fmt="decimal" />
            <MetricCard label="Calmar" value={metrics.calmar} fmt="decimal" />
            <MetricCard label="Sortino" value={metrics.sortino} fmt="decimal" />
            <MetricCard label="交易次数" value={metrics.trade_count} fmt="integer" />
            <MetricCard label="年化波动" value={metrics.annual_volatility} fmt="percent" />
          </div>

          {/* 优化结果排名表 */}
          {optimizationResults.length > 0 && (
            <div className="panel optimization-results">
              <h2>参数优化排名</h2>
              <table>
                <thead>
                  <tr>
                    <th>排名</th>
                    <th>参数</th>
                    <th>{OBJECTIVES[objective as keyof typeof OBJECTIVES] || objective}</th>
                    <th>Sharpe</th>
                    <th>收益</th>
                    <th>回撤</th>
                  </tr>
                </thead>
                <tbody>
                  {(optimizationResults as OptimizationResultItem[]).slice(0, 10).map(r => (
                    <tr key={r.rank} className={r.rank === 1 ? 'best' : ''}>
                      <td>{r.rank}</td>
                      <td>{JSON.stringify(r.params)}</td>
                      <td>{r.objective_value.toFixed(4)}</td>
                      <td>{r.sharpe.toFixed(2)}</td>
                      <td className={r.total_return >= 0 ? 'positive' : 'negative'}>{(r.total_return * 100).toFixed(2)}%</td>
                      <td className="negative">{(r.max_drawdown * 100).toFixed(2)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </aside>
      </div>
    </section>
  )
}

// ── 辅助组件 ──

function MetricCard({ label, value, fmt }: { label: string; value?: number; fmt: 'percent' | 'decimal' | 'integer' }) {
  const display = value == null || Number.isNaN(value) ? '-' :
    fmt === 'percent' ? `${(value * 100).toFixed(2)}%` :
    fmt === 'integer' ? String(Math.round(value)) :
    value.toFixed(4)
  return (
    <div className="metric-card">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{display}</span>
    </div>
  )
}

function TradeList({ data }: { data: QuantSymbolChart }) {
  if (!data.bars.length) return null
  const trades = data.trades || []
  return (
    <div className="panel trade-list">
      <h2>交易明细</h2>
      <div className="trade-table-wrap">
        <table>
          <thead>
            <tr><th>日期</th><th>方向</th><th>价格</th><th>数量</th></tr>
          </thead>
          <tbody>
            {trades.map((t, i) => (
              <tr key={t.trade_id || i}>
                <td>{t.date || '-'}</td>
                <td className={t.side === 'buy' ? 'positive' : 'negative'}>{t.side === 'buy' ? '买入' : '卖出'}</td>
                <td>{t.price.toFixed(2)}</td>
                <td>{t.quantity}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
