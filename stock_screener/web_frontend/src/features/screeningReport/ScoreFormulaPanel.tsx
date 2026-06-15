import React from 'react'
import type { DimensionBreakdown } from './types'
import { computeContribution } from './utils'

interface Props {
  ruleDetails: Array<{
    rule_key?: string
    rule_name?: string
    result?: string
    strategy_category?: string
    details?: Record<string, unknown>
  }>
  dimensions: DimensionBreakdown[]
  finalScore: number | null | undefined
}

const CATEGORY_LABELS: Record<string, string> = {
  technical: '技术面', macro: '宏观面', event_hot: '事件热度',
  capital_risk: '资金风险', llm: 'AI分析',
}

export function ScoreFormulaPanel({ ruleDetails, dimensions, finalScore }: Props) {
  // 提取各规则的贡献分，按维度分组
  const grouped = new Map<string, Array<{ name: string; score: number }>>()

  for (const r of ruleDetails) {
    if (r.result !== 'pass') continue
    const contrib = computeContribution('pass', r.details)
    if (contrib.score <= 0) continue
    const cat = r.strategy_category || 'technical'
    const label = CATEGORY_LABELS[cat] || cat
    if (!grouped.has(label)) grouped.set(label, [])
    grouped.get(label)!.push({
      name: r.rule_name || r.rule_key || '?',
      score: contrib.score,
    })
  }

  const allContribs = [...grouped.values()].flat()
  const totalContrib = allContribs.reduce((s, c) => s + c.score, 0)
  const base = 50
  const computed = Math.min(100, base + totalContrib)

  return (
    <div className="score-formula-panel">
      {/* 权重说明行 */}
      <div className="sfp-weights">
        {dimensions.map(d => (
          <span
            key={d.key}
            className={`sfp-weight-tag${d.contributionType === 'not_included' ? ' sfp-weight-excluded' : ''}`}
          >
            {d.label} × {d.weight}%{d.weight === 0 ? '（不纳入）' : ''}
          </span>
        ))}
      </div>

      {/* 公式展开 */}
      <div className="sfp-formula-body">
        <div className="sfp-formula-line">
          <span className="sfp-base">基础分 50.0</span>
          {allContribs.length > 0 && <span className="sfp-op"> + </span>}
        </div>

        {[...grouped.entries()].map(([cat, items]) => (
          <div key={cat} className="sfp-group">
            <span className="sfp-cat-label">{cat}</span>
            <div className="sfp-items">
              {items.map((c, i) => (
                <span key={i} className="sfp-item">
                  <span className="sfp-item-score">+{c.score.toFixed(1)}</span>
                  <span className="sfp-item-name">{c.name}</span>
                </span>
              ))}
            </div>
          </div>
        ))}

        <div className="sfp-result-line">
          <span className="sfp-equals">= </span>
          <span className="sfp-total">{computed.toFixed(1)}</span>
          <span className="sfp-outof"> / 100</span>
          {finalScore != null && Math.abs(Number(finalScore) - computed) > 0.5 && (
            <span className="sfp-note">（后端综合评分 {Number(finalScore).toFixed(1)}）</span>
          )}
        </div>
      </div>

      {allContribs.length === 0 && (
        <p className="sfp-empty">当前无通过规则贡献额外加分，评分仅含基础分。</p>
      )}
    </div>
  )
}
