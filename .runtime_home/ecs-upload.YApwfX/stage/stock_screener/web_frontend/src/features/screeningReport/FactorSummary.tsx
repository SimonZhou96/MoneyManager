import React from 'react'
import type { FactorItem } from './types'

interface Props {
  positiveFactors: FactorItem[]
  negativeFactors: FactorItem[]
}

export function FactorSummary({ positiveFactors, negativeFactors }: Props) {
  if (positiveFactors.length === 0 && negativeFactors.length === 0) return null

  return (
    <div className="screening-factors">
      {/* 主要加分项 */}
      <div className="factor-card factor-positive">
        <h4 className="factor-title">
          <span className="factor-icon">▲</span> 主要加分项
        </h4>
        {positiveFactors.length === 0 ? (
          <p className="factor-empty">暂无加分项</p>
        ) : (
          <ul className="factor-list">
            {positiveFactors.map((f, i) => (
              <li key={i} className="factor-item">
                <span className="factor-rule-name">{f.ruleName}</span>
                <span className="factor-score" style={{ color: '#22c55e' }}>+{f.score.toFixed(1)}</span>
                {f.reason && <p className="factor-reason">{f.reason}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 主要扣分项 */}
      <div className="factor-card factor-negative">
        <h4 className="factor-title">
          <span className="factor-icon">▼</span> 主要扣分项
        </h4>
        {negativeFactors.length === 0 ? (
          <p className="factor-empty">暂无扣分项</p>
        ) : (
          <ul className="factor-list">
            {negativeFactors.map((f, i) => (
              <li key={i} className="factor-item">
                <span className="factor-rule-name">{f.ruleName}</span>
                <span className="factor-score" style={{ color: '#ef4444' }}>-{f.score.toFixed(1)}</span>
                {f.reason && <p className="factor-reason">{f.reason}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
