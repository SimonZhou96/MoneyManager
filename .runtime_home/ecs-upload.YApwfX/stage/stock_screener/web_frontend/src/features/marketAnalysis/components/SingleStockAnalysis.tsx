import React from 'react'
import type { SingleStockResult } from '../types'

interface Props {
  result: SingleStockResult | null
  loading: boolean
  error: string
}

function fmt(val: unknown, decimals = 1): string {
  if (val === null || val === undefined || val === '') return '-'
  const n = Number(val)
  if (!Number.isFinite(n)) return String(val)
  return n.toFixed(decimals)
}

function resultLabel(r: string): string {
  const map: Record<string, string> = { pass: '通过', fail: '未通过', skip: '跳过', error: '错误' }
  return map[r] || r || '-'
}

function resultClass(r: string): string {
  if (r === 'pass') return 'pass'
  if (r === 'fail') return 'fail'
  return ''
}

export function SingleStockAnalysis({ result, loading, error }: Props) {
  if (loading) {
    return (
      <div className="single-analysis-panel">
        <div className="analysis-metrics-grid">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="metric-card skeleton">
              <span className="skeleton-cell w-16" style={{ height: 11 }} />
              <span className="skeleton-cell w-20" style={{ height: 28, marginTop: 8 }} />
            </div>
          ))}
        </div>
        <div className="skeleton-rows">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton-row">
              <span className="skeleton-cell w-24" />
              <span className="skeleton-cell w-32" />
              <span className="skeleton-cell w-16" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="single-analysis-panel">
        <div className="error-banner">{error}</div>
      </div>
    )
  }

  if (!result) {
    return (
      <div className="single-analysis-panel">
        <div className="empty-state">
          <span className="muted">选择股票后自动加载分析结果</span>
        </div>
      </div>
    )
  }

  const { rule_chain, ai_analysis, warnings } = result
  const details = rule_chain?.details || []

  return (
    <div className="single-analysis-panel">
      {/* Score metrics */}
      <div className="analysis-metrics-grid">
        <div className="metric-card">
          <span className="metric-label">筛选结果</span>
          <span className={`metric-value ${result.passed ? 'gold' : 'red'}`}>
            {result.passed ? '通过' : '未通过'}
          </span>
        </div>
        <div className="metric-card">
          <span className="metric-label">数据源</span>
          <span className="metric-value-sm">{result.data_source || '-'}</span>
        </div>
        {result.market_cap != null && (
          <div className="metric-card">
            <span className="metric-label">市值</span>
            <span className="metric-value-sm">
              {result.market_cap >= 1e12 ? (result.market_cap / 1e12).toFixed(2) + 'T' :
               result.market_cap >= 1e8 ? (result.market_cap / 1e8).toFixed(1) + '亿' :
               result.market_cap >= 1e6 ? (result.market_cap / 1e6).toFixed(0) + 'M' : String(result.market_cap)}
            </span>
          </div>
        )}
        {result.pe_ratio != null && (
          <div className="metric-card">
            <span className="metric-label">PE</span>
            <span className="metric-value-sm">{fmt(result.pe_ratio)}</span>
          </div>
        )}
      </div>

      {/* Passed conditions */}
      {result.conditions_met && result.conditions_met.length > 0 && (
        <div className="analysis-conditions">
          <div className="subsection-title">满足条件</div>
          <div className="condition-tags">
            {result.conditions_met.map((c, i) => (
              <span key={i} className="condition-tag">{c}</span>
            ))}
          </div>
        </div>
      )}

      {/* Rule details */}
      {details.length > 0 && (
        <div className="analysis-rules">
          <div className="subsection-title">
            规则明细
            <span className="subsection-count">{details.filter(d => d.result === 'pass').length}/{details.length} 通过</span>
          </div>
          <div className="rule-list">
            {details.map((d, i) => (
              <div key={i} className={`rule-item ${resultClass(d.result)}`}>
                <div className="rule-item-head">
                  <span className={`rule-result-badge ${resultClass(d.result)}`}>
                    {resultLabel(d.result)}
                  </span>
                  <span className="rule-name">{d.rule_name || d.rule_key}</span>
                </div>
                {d.reason && <div className="rule-reason">{d.reason}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* AI analysis */}
      {ai_analysis && ai_analysis.analysis_status === 'success' && (
        <div className="analysis-ai">
          <div className="subsection-title">AI 分析</div>
          {ai_analysis.summary && (
            <p className="ai-summary">{ai_analysis.summary}</p>
          )}
          <div className="ai-meta">
            <span>信号偏向: <strong className={ai_analysis.signal_bias === 'bullish' ? 'up' : ai_analysis.signal_bias === 'bearish' ? 'down' : ''}>
              {ai_analysis.signal_bias || '-'}
            </strong></span>
            <span>可靠性: {fmt(ai_analysis.reliability_score)}</span>
            <span>置信度: {fmt(ai_analysis.confidence_score)}</span>
          </div>
          {ai_analysis.positive_factors && ai_analysis.positive_factors.length > 0 && (
            <div className="factor-group">
              <div className="factor-label up">利好因素</div>
              <ul className="factor-items">
                {ai_analysis.positive_factors.slice(0, 5).map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </div>
          )}
          {ai_analysis.risk_factors && ai_analysis.risk_factors.length > 0 && (
            <div className="factor-group">
              <div className="factor-label down">风险因素</div>
              <ul className="factor-items">
                {ai_analysis.risk_factors.slice(0, 5).map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Warnings */}
      {warnings && warnings.length > 0 && (
        <div className="analysis-warnings">
          {warnings.map((w, i) => (
            <div key={i} className="warning-item">{w}</div>
          ))}
        </div>
      )}
    </div>
  )
}
