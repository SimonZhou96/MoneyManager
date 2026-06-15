import React from 'react'
import type { DimensionBreakdown } from './types'

interface Props {
  dimensions: DimensionBreakdown[]
}

const CONTRIBUTION_COLORS: Record<string, string> = {
  strong_positive: '#22c55e',
  weak_positive: '#4ade80',
  neutral: '#6b7280',
  negative: '#ef4444',
  not_included: '#374151',
}

export function ScoreBreakdownCards({ dimensions }: Props) {
  return (
    <div className="score-breakdown-section">
      <h3 className="screening-subtitle">买入评分构成</h3>
      <div className="score-overview-grid">
        {dimensions.map(dim => (
          <div
            key={dim.key}
            className={`score-card${dim.contributionType === 'negative' ? ' score-card-negative' : ''}${dim.contributionType === 'strong_positive' ? ' score-card-positive' : ''}`}
          >
            <div className="score-card-head">
              <span className="score-card-title">{dim.label}</span>
              <span className="score-card-weight">权重 {dim.weight}%</span>
            </div>
            <div className="score-card-value">
              <span
                className="score-number"
                style={{ color: dim.score != null ? CONTRIBUTION_COLORS[dim.contributionType] || '#e5e7eb' : '#6b7280' }}
              >
                {dim.displayScore}
              </span>
            </div>
            <div className="score-card-contribution">
              <span
                className="contrib-badge"
                style={{
                  color: CONTRIBUTION_COLORS[dim.contributionType] || '#6b7280',
                  background: `${CONTRIBUTION_COLORS[dim.contributionType] || '#6b7280'}18`,
                }}
              >
                {dim.contributionLabel}
              </span>
            </div>
            <div className="score-card-counts">
              {dim.passed > 0 && <span className="count-pass">{dim.passed} 通过</span>}
              {dim.failed > 0 && <span className="count-fail">{dim.failed} 未通过</span>}
              {dim.notCalculated > 0 && <span className="count-unknown">{dim.notCalculated} 未计算</span>}
              {dim.passed === 0 && dim.failed === 0 && dim.notCalculated === 0 && (
                <span className="count-unknown">无数据</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
