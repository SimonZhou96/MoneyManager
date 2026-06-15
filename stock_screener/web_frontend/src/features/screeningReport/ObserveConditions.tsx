import React from 'react'
import type { ObserveCondition } from './types'

interface Props {
  conditions: ObserveCondition[]
  biasLabel: string
}

export function ObserveConditions({ conditions, biasLabel }: Props) {
  // 只在偏买入以下展示观察条件
  if (conditions.length === 0 || biasLabel === '强买入' || biasLabel === '偏买入') {
    return null
  }

  return (
    <div className="observe-conditions-section">
      <h3 className="screening-subtitle">转为偏买入的观察条件</h3>
      <p className="observe-hint">
        以下条件基于未通过规则自动生成，不构成投资建议，仅供观察参考：
      </p>
      <ul className="observe-list">
        {conditions.map((c, i) => (
          <li key={i} className="observe-item">
            <span className="observe-dot" />
            <span>观察 {c.condition} 是否改善或转为通过。</span>
          </li>
        ))}
        <li className="observe-item observe-item-last">
          <span className="observe-dot" />
          <span>观察通过规则数量是否提升至 {Math.ceil(conditions.length * 2.5)} 条以上。</span>
        </li>
      </ul>
    </div>
  )
}
