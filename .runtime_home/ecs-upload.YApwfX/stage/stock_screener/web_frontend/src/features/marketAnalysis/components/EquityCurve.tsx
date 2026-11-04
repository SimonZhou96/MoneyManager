import React, { useMemo } from 'react'

interface Props {
  data: { date: string; value: number }[]
}

export function EquityCurve({ data }: Props) {
  const pathD = useMemo(() => {
    if (data.length < 2) return ''
    const xScale = (i: number) => (i / (data.length - 1)) * 100
    const values = data.map(d => d.value)
    const min = Math.min(...values)
    const max = Math.max(...values)
    const range = max - min || 1
    const yScale = (v: number) => 100 - ((v - min) / range) * 90 - 5 // 5-95% height

    let d = `M 0,${yScale(data[0].value)}`
    for (let i = 1; i < data.length; i++) {
      d += ` L ${xScale(i)},${yScale(data[i].value)}`
    }
    // Area fill
    const lastX = xScale(data.length - 1)
    d += ` L ${lastX},100 L 0,100 Z`
    return d
  }, [data])

  if (data.length < 2) return null

  const values = data.map(d => d.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const startVal = values[0]
  const endVal = values[values.length - 1]
  const isUp = endVal >= startVal

  return (
    <div className="equity-curve-chart">
      <div className="equity-curve-header">
        <span>资金曲线</span>
        <span className="mono muted">
          {new Date(data[0]?.date || '').toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
          {' — '}
          {new Date(data[data.length - 1]?.date || '').toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
        </span>
      </div>
      <div className="equity-curve-svg">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none">
          {/* Grid lines */}
          {[25, 50, 75].map(y => (
            <line key={y} x1="0" y1={y} x2="100" y2={y} stroke="rgba(255,255,255,0.04)" strokeWidth="0.3" />
          ))}
          {/* Area fill */}
          <path
            d={pathD}
            fill={isUp ? 'rgba(34,197,94,0.06)' : 'rgba(239,68,68,0.06)'}
          />
          {/* Line */}
          <path
            d={pathD.replace(/L \d+,\d+ Z$/, '')}
            fill="none"
            stroke={isUp ? '#22c55e' : '#ef4444'}
            strokeWidth="0.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* Baseline */}
          <line
            x1="0"
            y1={95 - ((startVal - min) / (max - min || 1)) * 90}
            x2="100"
            y2={95 - ((startVal - min) / (max - min || 1)) * 90}
            stroke="rgba(201,168,76,0.3)"
            strokeWidth="0.2"
            strokeDasharray="1,1"
          />
        </svg>
      </div>
    </div>
  )
}
