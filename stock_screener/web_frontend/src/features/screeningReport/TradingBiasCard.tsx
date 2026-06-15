import React from 'react'
import type { DimensionBreakdown } from './types'
import { scoreToBias, computeConfidence, generateExplanation } from './utils'

interface Props {
  finalScore: number | null | undefined
  aiAnalysis: Record<string, unknown> | null | undefined
  ruleDetails: Array<{ result?: string; details?: Record<string, unknown> }>
  dimensions: DimensionBreakdown[]
}

const ZONE_LABELS = ['强规避', '观望偏弱', '中性观望', '偏买入', '强买入']
const ZONE_COLORS = ['#ef4444', '#f59e0b', '#6b7280', '#22c55e', '#16a34a']
const ZONE_PCT = [20, 20, 20, 20, 20] // each zone is 20%

function baseConfidence(aiAnalysis: Record<string, unknown> | null | undefined, ruleDetails: Array<{ result?: string }>) {
  return computeConfidence(aiAnalysis, ruleDetails)
}

export function TradingBiasCard({ finalScore, aiAnalysis, ruleDetails, dimensions }: Props) {
  const bias = scoreToBias(finalScore)
  const confidence = baseConfidence(aiAnalysis, ruleDetails)
  const hasScore = finalScore != null && Number.isFinite(finalScore)

  const passCount = ruleDetails.filter(d => d.result === 'pass').length
  const totalRules = ruleDetails.length
  const explanation = generateExplanation(finalScore, bias, passCount, totalRules, dimensions)

  // 检查是否有未计算维度
  const notIncludedDims = dimensions.filter(d => d.contributionType === 'not_included' && d.weight > 0)

  return (
    <div className="trading-bias-card">
      {/* ── 左侧：偏置 + 分数 + 信号 + 可信度 ── */}
      <div className="tbc-main">
        <div className="tbc-bias-row">
          <span
            className="tbc-bias-badge"
            style={{ color: bias.color, background: `${bias.color}18`, borderColor: `${bias.color}40` }}
          >
            {bias.label}
          </span>
          <span className="tbc-signal" style={{ color: bias.color }}>
            {bias.signalLabel}
          </span>
          <span className={`tbc-confidence tbc-conf-${confidence.level}`}>
            可信度: {confidence.label}
          </span>
        </div>

        <div className="tbc-score-row">
          <span className="tbc-score-value" style={{ color: hasScore ? bias.color : '#6b7280' }}>
            {hasScore ? finalScore!.toFixed(1) : '—'}
          </span>
          <span className="tbc-score-max">/ 100</span>
          <span className="tbc-score-label">买入评分</span>
        </div>

        <p className="tbc-explanation">{explanation}</p>

        {/* 未计算维度提示 */}
        {notIncludedDims.length > 0 && (
          <p className="tbc-dim-warning">
            ⚠ {notIncludedDims.map(d => d.label).join('、')}未计算，本次评分未纳入该维度。
          </p>
        )}
      </div>

      {/* ── 右侧/下方：评分可视化条（三层结构）── */}
      <div className="tbc-bar-section">
        <div
          className="tbc-bar-container"
          style={{ '--score-percent': hasScore ? `${Math.max(0, Math.min(100, finalScore!))}%` : '50%' } as React.CSSProperties}
        >
          {/* 第一层：当前分数 bubble（评分条上方） */}
          {hasScore && (
            <div className="tbc-score-bubble" style={{ color: bias.color }}>
              <span className="tbc-bubble-value">{finalScore!.toFixed(1)}</span>
              <div className="tbc-bubble-arrow" style={{ borderTopColor: bias.color }} />
            </div>
          )}

          {/* 第二层：评分条本体 */}
          <div className="tbc-bar-track">
            {ZONE_LABELS.map((label, i) => (
              <div
                key={label}
                className="tbc-bar-zone"
                style={{ width: `${ZONE_PCT[i]}%`, background: ZONE_COLORS[i] }}
                title={label}
              />
            ))}
          </div>

          {/* 第三层：区间标签 */}
          <div className="tbc-bar-labels">
            {ZONE_LABELS.map((label, i) => (
              <span key={label} className="tbc-bar-label" style={{ color: ZONE_COLORS[i] }}>
                {label}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
