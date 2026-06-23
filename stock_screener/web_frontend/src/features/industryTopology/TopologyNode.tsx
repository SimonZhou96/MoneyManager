import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { TopologyNodeData } from './types'

const SIZE_MAP: Record<number, { w: number; h: number }> = {
  1: { w: 120, h: 72 }, 2: { w: 150, h: 88 }, 3: { w: 180, h: 104 },
  4: { w: 210, h: 120 }, 5: { w: 240, h: 136 }, 6: { w: 270, h: 152 },
}

function colorFor(pct: number | null): { border: string; bg: string; text: string } {
  if (pct === null) return { border: '#6b7280', bg: '#f3f4f6', text: '#6b7280' }
  if (pct > 0) return { border: '#16a34a', bg: '#dcfce7', text: '#16a34a' }
  if (pct < 0) return { border: '#dc2626', bg: '#fee2e2', text: '#dc2626' }
  return { border: '#6b7280', bg: '#f3f4f6', text: '#6b7280' }
}

export const TopologyNode = memo(function TopologyNode({ data, id }: NodeProps) {
  const d = data as unknown as TopologyNodeData
  const sz = SIZE_MAP[d.size_level] || SIZE_MAP[1]
  const c = colorFor(d.pct_chg)
  const pctStr = d.pct_chg === null ? '--' : `${d.pct_chg > 0 ? '+' : ''}${d.pct_chg}%`
  return (
    <div
      className="topo-node"
      style={{ width: sz.w, height: sz.h, borderColor: c.border, backgroundColor: c.bg,
               boxShadow: d.is_center ? '0 0 0 3px #fbbf24' : undefined }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="topo-node-top">
        <span className="topo-node-sector">{d.sector}</span>
        <span className="topo-node-pct" style={{ color: c.text }}>{pctStr}</span>
      </div>
      <div className="topo-node-name">{d.name || d.code}</div>
      <div className="topo-node-bottom">
        <span className="topo-node-code">{d.code}</span>
        <span className="topo-node-cap">{d.market_cap_str}</span>
      </div>
      {!d.is_center && (
        <button className="topo-node-expand" disabled={d.expanded} data-node-id={id}>
          {d.expanded ? '已展开' : '展开↗'}
        </button>
      )}
      {d.stale && <span className="topo-node-stale">数据较旧</span>}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  )
})
