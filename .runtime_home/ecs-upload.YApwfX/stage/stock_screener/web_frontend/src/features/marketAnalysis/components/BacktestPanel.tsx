import React, { useState } from 'react'
import type { BacktestResult, BacktestMetrics } from '../types'
import { EquityCurve } from './EquityCurve'

interface Props {
  market: string
  stockCode: string
  result: BacktestResult | null
  loading: boolean
  error: string
  onSubmit: (config: {
    entry_chain_key: string
    exit_policy: { type: string; days?: number; pct?: number }
  }) => void
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '-'
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%'
}

function fmtNum(v: number | null | undefined, d = 2): string {
  if (v == null) return '-'
  return Number(v).toFixed(d)
}

export function BacktestPanel({ market, stockCode, result, loading, error, onSubmit }: Props) {
  const [entryChainKey, setEntryChainKey] = useState('default')
  const [exitType, setExitType] = useState('fixed_holding_days')
  const [exitDays, setExitDays] = useState(5)
  const [exitPct, setExitPct] = useState(8)

  const handleSubmit = () => {
    onSubmit({
      entry_chain_key: entryChainKey,
      exit_policy: exitType === 'fixed_holding_days'
        ? { type: exitType, days: exitDays }
        : { type: exitType, pct: exitPct / 100 },
    })
  }

  const isRunning = loading && result?.status === 'running'

  return (
    <div className="backtest-panel">
      {/* Form */}
      <div className="backtest-form">
        <div className="bf-field">
          <label>标的</label>
          <input type="text" value={stockCode} disabled className="bf-input muted" />
        </div>
        <div className="bf-field">
          <label>入口规则链</label>
          <input
            type="text"
            value={entryChainKey}
            onChange={e => setEntryChainKey(e.target.value)}
            className="bf-input"
            placeholder="default"
          />
        </div>
        <div className="bf-field">
          <label>退出策略</label>
          <select value={exitType} onChange={e => setExitType(e.target.value)} className="bf-select">
            <option value="fixed_holding_days">固定持有天数</option>
            <option value="stop_loss">固定止损比例</option>
          </select>
        </div>
        <div className="bf-field">
          <label>{exitType === 'fixed_holding_days' ? '持有天数' : '止损比例(%)'}</label>
          {exitType === 'fixed_holding_days' ? (
            <input
              type="number"
              value={exitDays}
              onChange={e => setExitDays(Number(e.target.value))}
              min={1}
              max={60}
              className="bf-input"
            />
          ) : (
            <input
              type="number"
              value={exitPct}
              onChange={e => setExitPct(Number(e.target.value))}
              min={1}
              max={50}
              className="bf-input"
            />
          )}
        </div>
        <button
          className="gold-btn primary"
          onClick={handleSubmit}
          disabled={loading}
        >
          {loading ? '回测中...' : '开始回测'}
        </button>
      </div>

      {/* Progress */}
      {isRunning && (
        <div className="backtest-progress">
          <div className="progress-track">
            <span
              className="progress-fill running"
              style={{ width: `${result?.progress_pct || 5}%` }}
            />
          </div>
          <span className="progress-text">{result?.current_stage || '计算中...'}</span>
        </div>
      )}

      {/* Error */}
      {error && <div className="error-banner">{error}</div>}
      {result?.status === 'failed' && (
        <div className="error-banner">
          {result.error_message || '回测执行失败'}
        </div>
      )}

      {/* Results */}
      {result?.status === 'completed' && result.metrics && (
        <div className="backtest-results">
          <div className="analysis-metrics-grid">
            <MetricCard label="总收益" value={fmtPct(result.metrics.total_return)} accent />
            <MetricCard label="最大回撤" value={fmtPct(result.metrics.max_drawdown)} />
            <MetricCard label="胜率" value={fmtNum(result.metrics.win_rate ? result.metrics.win_rate * 100 : null, 1) + '%'} />
            <MetricCard label="盈亏比" value={fmtNum(result.metrics.win_loss_ratio)} />
          </div>

          {result.equity_curve && result.equity_curve.length > 0 && (
            <EquityCurve data={result.equity_curve} />
          )}

          {result.trades && result.trades.length > 0 && (
            <div className="trade-list">
              <div className="subsection-title">
                交易明细
                <span className="subsection-count">{result.trades.length} 笔</span>
              </div>
              <div className="table-wrap" style={{ maxHeight: 200, overflowY: 'auto' }}>
                <table>
                  <thead>
                    <tr>
                      <th>日期</th>
                      <th>方向</th>
                      <th>价格</th>
                      <th>数量</th>
                      <th>盈亏</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.slice(0, 50).map((t, i) => (
                      <tr key={i}>
                        <td className="mono">{String(t.date || '').slice(0, 10)}</td>
                        <td className={t.side === 'buy' ? 'up' : 'down'}>
                          {t.side === 'buy' ? '买入' : '卖出'}
                        </td>
                        <td className="mono">{fmtNum(t.price)}</td>
                        <td className="mono">{t.quantity}</td>
                        <td className={`mono ${(t.pnl || 0) >= 0 ? 'up' : 'down'}`}>
                          {t.pnl != null ? fmtPct(t.pnl) : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Empty prompt */}
      {!result && !loading && !error && (
        <div className="empty-state" style={{ marginTop: 16 }}>
          <span className="muted">配置回测参数并开始评估策略表现</span>
        </div>
      )}
    </div>
  )
}

function MetricCard({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className={`metric-card${accent ? ' accent' : ''}`}>
      <span className="metric-label">{label}</span>
      <span className={`metric-value${accent ? ' gold' : ''}`}>{value}</span>
    </div>
  )
}
