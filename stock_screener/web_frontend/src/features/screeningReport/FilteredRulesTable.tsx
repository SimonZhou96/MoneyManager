import React, { useMemo, useState } from 'react'
import { computeContribution } from './utils'

interface RuleDetailRow {
  rule_key?: string
  rule_name?: string
  rule_type?: string
  strategy_category?: string
  result?: string
  reason?: string
  details?: Record<string, unknown>
}

interface Props {
  ruleDetails: RuleDetailRow[]
}

type FilterTab = 'all' | 'pass' | 'fail' | 'skip'

const TAB_LABELS: Record<FilterTab, string> = {
  all: '全部',
  pass: '通过',
  fail: '失败',
  skip: '未计算',
}

const RESULT_LABELS: Record<string, string> = {
  pass: '通过',
  fail: '失败',
  skip: '—',
  error: '错误',
}

export function FilteredRulesTable({ ruleDetails }: Props) {
  const [filter, setFilter] = useState<FilterTab>('all')
  const [expanded, setExpanded] = useState(false)

  const counts = useMemo(() => {
    const all = ruleDetails.length
    const pass = ruleDetails.filter(d => d.result === 'pass').length
    const fail = ruleDetails.filter(d => d.result === 'fail').length
    const skip = ruleDetails.filter(d => d.result !== 'pass' && d.result !== 'fail').length
    return { all, pass, fail, skip }
  }, [ruleDetails])

  const filtered = useMemo(() => {
    let list = ruleDetails
    if (filter === 'pass') list = ruleDetails.filter(d => d.result === 'pass')
    else if (filter === 'fail') list = ruleDetails.filter(d => d.result === 'fail')
    else if (filter === 'skip') list = ruleDetails.filter(d => d.result !== 'pass' && d.result !== 'fail')

    // 失败规则按贡献排序（扣分多的排前面）
    if (filter === 'fail' || filter === 'all') {
      return [...list].sort((a, b) => {
        const ca = computeContribution(a.result || '', a.details).score
        const cb = computeContribution(b.result || '', b.details).score
        if (a.result === 'fail' && b.result === 'fail') return cb - ca
        if (a.result === 'fail') return -1
        if (b.result === 'fail') return 1
        return cb - ca
      })
    }
    return list
  }, [ruleDetails, filter])

  const displayed = expanded ? filtered : filtered.slice(0, 5)

  if (ruleDetails.length === 0) {
    return (
      <div className="filtered-rules-empty">
        <p>暂无规则明细数据</p>
      </div>
    )
  }

  return (
    <div className="filtered-rules-section">
      <h3 className="screening-subtitle">规则明细</h3>

      {/* 筛选标签 */}
      <div className="screening-filter-tabs">
        {(Object.keys(TAB_LABELS) as FilterTab[]).map(tab => (
          <button
            key={tab}
            className={`screening-filter-tab${filter === tab ? ' active' : ''}`}
            onClick={() => setFilter(tab)}
          >
            {TAB_LABELS[tab]}
            <span className="screening-filter-tab-count">{counts[tab]}</span>
          </button>
        ))}
      </div>

      {/* 规则表格 */}
      <div className="rules-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: '40%' }}>规则</th>
              <th style={{ width: '15%' }}>类型</th>
              <th style={{ width: '12%' }}>结果</th>
              <th style={{ width: '33%' }}>原因</th>
            </tr>
          </thead>
          <tbody>
            {displayed.map((row, i) => (
              <tr key={row.rule_key || i}>
                <td className="cell-rule-name">{row.rule_name || row.rule_key || '—'}</td>
                <td className="cell-muted">{row.rule_type || row.strategy_category || '—'}</td>
                <td>
                  <span className={`status ${row.result === 'pass' ? 'pass' : row.result === 'fail' ? 'fail' : 'neutral'}`}>
                    {RESULT_LABELS[row.result || ''] || row.result || '—'}
                  </span>
                </td>
                <td className="cell-muted">{row.reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 展开/收起 */}
      {filtered.length > 5 && (
        <button className="btn-expand" onClick={() => setExpanded(!expanded)}>
          {expanded ? '收起' : `展开全部（共 ${filtered.length} 条）`}
        </button>
      )}

      {/* 评分公式（仅在有数据时展示） */}
      <details className="score-formula-box" style={{ marginTop: 14 }}>
        <summary className="score-formula-summary">评分公式</summary>
        <ScoreFormulaBreakdown ruleDetails={ruleDetails} />
      </details>
    </div>
  )
}

/** 评分公式明细 */
function ScoreFormulaBreakdown({ ruleDetails }: { ruleDetails: RuleDetailRow[] }) {
  const contributions = ruleDetails
    .filter(d => d.result === 'pass')
    .map(d => {
      const contrib = computeContribution('pass', d.details)
      return { name: d.rule_name || d.rule_key || '?', score: contrib.score }
    })
    .filter(c => c.score > 0)

  const total = contributions.reduce((sum, c) => sum + c.score, 0)
  const base = 50

  if (contributions.length === 0) {
    return <p className="score-formula-text">无额外加分项 — 仅基础分 {base.toFixed(1)}</p>
  }

  return (
    <div>
      <p className="score-formula-text">
        {base} {contributions.map(c => ` +${c.score.toFixed(1)}(${c.name})`).join('')}
      </p>
      <p className="score-formula-result">= {((base + total) > 100 ? 100 : base + total).toFixed(1)}</p>
    </div>
  )
}
