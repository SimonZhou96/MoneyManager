import React from 'react'
import type { DimensionBreakdown } from './types'

interface Props {
  ruleDetails: Array<{
    rule_key?: string
    rule_name?: string
    rule_type?: string
    result?: string
    strategy_category?: string
    implementation?: string
    details?: Record<string, unknown>
    reason?: string
  }>
  dimensions: DimensionBreakdown[]
  finalScore: number | null | undefined
  /** 后端 result_json，包含 score_details / technical_score / macro_score 等 */
  resultJson: Record<string, unknown>
}

const ENTERPRISE_MODULE_WEIGHTS: Record<string, number> = {
  macro_score: 0.30,
  industry_score: 0.25,
  company_score: 0.25,
  valuation_score: 0.10,
  trading_score: 0.10,
}

const ENTERPRISE_MODULE_LABELS: Record<string, string> = {
  macro_score: '宏观', industry_score: '行业', company_score: '公司',
  valuation_score: '估值', trading_score: '交易',
}

/** 从 ruleDetails 找出 EnterprisePotentialAnalysis 的五模块详情 */
function findEnterpriseDetails(
  ruleDetails: Props['ruleDetails'],
): Record<string, unknown> | null {
  for (const r of ruleDetails) {
    const imp = r.implementation || ''
    if (imp === 'EnterprisePotentialAnalysisStrategizer' && r.details) {
      return r.details
    }
  }
  return null
}

function _safeNum(v: unknown): number | null {
  if (v == null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

export function ScoreFormulaPanel({ ruleDetails, dimensions: _dims, finalScore, resultJson: _rj }: Props) {
  const enterprise = findEnterpriseDetails(ruleDetails)

  // 扣分项
  const negative: Array<{ name: string; score: number; reason?: string }> = []
  for (const r of ruleDetails) {
    if (r.result !== 'fail') continue
    const name = r.rule_name || r.rule_key || '?'
    const ds = r.details || {}
    const s = _safeNum(ds.score ?? ds.total_score) ?? 0
    negative.push({ name, score: s, reason: r.reason })
  }

  return (
    <div className="score-formula-panel">
      <h3 className="screening-subtitle">买入评分计算链路</h3>

      {/* ── 一、五模块宏观分解 ── */}
      {enterprise && (
        <div className="sfp-section">
          <div className="sfp-section-title">一、五模块宏观分解（EnterprisePotential）</div>
          <table className="sfp-table">
            <thead>
              <tr>
                <th>模块</th>
                <th>评分</th>
                <th>权重</th>
                <th>贡献</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(ENTERPRISE_MODULE_WEIGHTS).map(([field, weight]) => {
                const raw = _safeNum((enterprise as any)[field])
                const contrib = raw != null ? raw * weight : null
                return (
                  <tr key={field}>
                    <td>{ENTERPRISE_MODULE_LABELS[field] || field}</td>
                    <td>
                      {raw != null ? raw.toFixed(1) : <span className="sfp-na">N/A（用50.0补齐）</span>}
                    </td>
                    <td>{(weight * 100).toFixed(0)}%</td>
                    <td>
                      {contrib != null ? (
                        <span className={contrib > 0 ? 'sfp-positive' : 'sfp-zero'}>{contrib.toFixed(1)}</span>
                      ) : <span className="sfp-na">—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {enterprise.total_score != null && (
            <div className="sfp-enterprise-total">
              加权总分：{Number(enterprise.total_score).toFixed(1)}
              {Number(enterprise.total_score) < 70 && (
                <span className="sfp-na"> → 门限70分未通过</span>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── 二、扣分项 ── */}
      <div className="sfp-section">
        <div className="sfp-section-title">二、扣分项</div>
        {negative.length > 0 ? (
          <ul className="sfp-factor-list">
            {negative.map((f, i) => (
              <li key={i} className="sfp-factor-negative">
                <span className="sfp-factor-name">{f.name}</span>
                {f.score > 0 && <span className="sfp-factor-score">-{f.score.toFixed(1)}</span>}
                {f.reason && <span className="sfp-factor-note">（{f.reason}）</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="sfp-empty">无扣分项</p>
        )}
      </div>

      {/* ── 三、最终买入评分 ── */}
      <div className="sfp-section sfp-final-section">
        <div className="sfp-section-title">三、最终买入评分</div>
        <div className="sfp-formula-final">
          <code className="sfp-code sfp-code-result">
            {finalScore != null ? finalScore.toFixed(1) : '—'} / 100
          </code>
        </div>
      </div>
    </div>
  )
}
