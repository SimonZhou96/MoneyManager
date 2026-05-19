import React, { useState } from 'react'
import { api } from '../../api'
import type { QuantBacktestRequest, QuantBacktestSubmitResponse } from './types'

export function QuantLab() {
  const [market, setMarket] = useState('US')
  const [symbols, setSymbols] = useState('US.AAPL')
  const [entryChainKey, setEntryChainKey] = useState('default')
  const [exitPolicy, setExitPolicy] = useState('fixed_holding_days')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  async function submit() {
    setError('')
    setStatus('提交中')
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
      setStatus(`已创建回测: ${response.run_id}`)
    } catch (err) {
      setStatus('')
      setError(err instanceof Error ? err.message : '创建回测失败')
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
          <textarea value={symbols} onChange={(event) => setSymbols(event.target.value)} />
          <label>入口规则链</label>
          <input value={entryChainKey} onChange={(event) => setEntryChainKey(event.target.value)} />
          <label>退出策略</label>
          <select value={exitPolicy} onChange={(event) => setExitPolicy(event.target.value)}>
            <option value="fixed_holding_days">固定持有 5 天</option>
            <option value="stop_loss">止损 8%</option>
          </select>
          <button onClick={submit}>开始回测</button>
          {status && <div className="notice">{status}</div>}
          {error && <div className="error">{error}</div>}
        </aside>
        <main className="quant-results">
          <div className="metric-row">
            <div className="metric-card">总收益<span>-</span></div>
            <div className="metric-card">最大回撤<span>-</span></div>
            <div className="metric-card">胜率<span>-</span></div>
            <div className="metric-card">盈亏比<span>-</span></div>
          </div>
          <div className="panel chart-placeholder">净值曲线 / 回撤曲线</div>
          <div className="panel chart-placeholder">交易明细 / 规则链触发明细 / 纸面交易状态</div>
        </main>
      </div>
    </section>
  )
}
