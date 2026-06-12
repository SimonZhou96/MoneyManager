import React from 'react'
import type { Timeframe } from '../types'

const LABELS: Record<Timeframe, string> = {
  '1m': '1分',
  '5m': '5分',
  '15m': '15分',
  '30m': '30分',
  '60m': '60分',
  '1d': '日线',
  '1wk': '周线',
}

interface Props {
  timeframes: Timeframe[]
  value: Timeframe
  onChange: (tf: Timeframe) => void
}

export function TimeframeSelector({ timeframes, value, onChange }: Props) {
  return (
    <div className="timeframe-selector">
      {timeframes.map(tf => (
        <button
          key={tf}
          className={`tf-chip${value === tf ? ' active' : ''}`}
          onClick={() => onChange(tf)}
        >
          {LABELS[tf] || tf}
        </button>
      ))}
    </div>
  )
}
