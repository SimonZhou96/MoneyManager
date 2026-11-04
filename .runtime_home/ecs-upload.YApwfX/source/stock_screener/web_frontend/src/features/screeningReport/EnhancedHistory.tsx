import React from 'react'
import type { HistoryTrendPoint } from './types'
import { scoreChangeLabel } from './utils'

interface HistoryRunItem {
  run_id: string
  created_at: string | null
  final_score?: number | null
  passed?: boolean | null
  status?: string
  chain_name?: string
}

interface Props {
  runs: HistoryRunItem[]
  loading: boolean
  trend: HistoryTrendPoint[]
  onSelectRun: (runId: string) => void
}

function Sparkline({ points, width, height }: { points: (number | null)[]; width: number; height: number }) {
  const valid = points.filter((p): p is number => p != null)
  if (valid.length < 2) {
    return (
      <svg width={width} height={height} style={{ display: 'block' }}>
        <text x={width / 2} y={height / 2} fill="#6b7280" fontSize="11" textAnchor="middle" dominantBaseline="middle">
          {valid.length === 0 ? '无数据' : '需至少2个数据点'}
        </text>
      </svg>
    )
  }

  const min = Math.min(...valid)
  const max = Math.max(...valid)
  const range = max - min || 1
  const padding = 4

  const xs = valid.map((_, i) => padding + (i / (valid.length - 1)) * (width - 2 * padding))
  const ys = valid.map(v => height - padding - ((v - min) / range) * (height - 2 * padding))

  const pathD = ys.map((y, i) => `${i === 0 ? 'M' : 'L'} ${xs[i]} ${y}`).join(' ')
  const fillD = `${pathD} L ${xs[xs.length - 1]} ${height - padding} L ${xs[0]} ${height - padding} Z`

  const isUp = valid[valid.length - 1] >= valid[0]
  const color = isUp ? '#22c55e' : '#ef4444'

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <path d={fillD} fill={`${color}15`} />
      <path d={pathD} fill="none" stroke={color} strokeWidth="1.5" />
      {valid.length <= 10 &&
        ys.map((y, i) => (
          <circle key={i} cx={xs[i]} cy={y} r="2.5" fill={color} />
        ))}
    </svg>
  )
}

export function EnhancedHistory({ runs, loading, trend, onSelectRun }: Props) {
  const sortedRuns = [...runs].sort(
    (a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime(),
  )

  const trendScores = trend.map(t => t.score)

  return (
    <div className="enhanced-history">
      {loading && <p className="history-loading">加载中...</p>}

      {/* 评分趋势 sparkline */}
      {trendScores.length >= 2 && (
        <div className="history-trend">
          <h4 className="history-trend-title">评分趋势</h4>
          <Sparkline points={trendScores} width={280} height={60} />
          <div className="history-trend-labels">
            <span style={{ color: '#6b7280' }}>{trend[0]?.date}</span>
            <span style={{ color: '#6b7280' }}>{trend[trend.length - 1]?.date}</span>
          </div>
        </div>
      )}

      {!loading && sortedRuns.length === 0 && (
        <p className="history-empty">暂无历史筛选记录</p>
      )}

      {sortedRuns.length > 0 && (
        <div className="history-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>规则链</th>
                <th>状态</th>
                <th>买入评分</th>
                <th>较上次</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {sortedRuns.map((run, i) => {
                const prevRun = sortedRuns[i + 1] // next in sorted = previous chronologically
                const change = scoreChangeLabel(
                  prevRun?.final_score ?? null,
                  run.final_score ?? null,
                )
                const passed = run.passed
                return (
                  <tr
                    key={run.run_id}
                    className="history-row-clickable"
                    onClick={() => onSelectRun(run.run_id)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td className="cell-time">
                      {(run.created_at || '').replace('T', ' ').slice(0, 19)}
                    </td>
                    <td>{run.chain_name || '—'}</td>
                    <td>
                      <span className={`status ${passed ? 'pass' : 'fail'}`}>
                        {passed ? '通过' : '未通过'}
                      </span>
                    </td>
                    <td style={{ fontWeight: 700, color: '#D8B84E' }}>
                      {run.final_score != null ? run.final_score.toFixed(1) : '—'}
                    </td>
                    <td style={{ color: change.color, fontSize: 12 }}>
                      {change.icon} {change.label}
                    </td>
                    <td>
                      <button
                        className="btn-ghost-xs"
                        onClick={(e) => { e.stopPropagation(); onSelectRun(run.run_id) }}
                      >
                        查看
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
