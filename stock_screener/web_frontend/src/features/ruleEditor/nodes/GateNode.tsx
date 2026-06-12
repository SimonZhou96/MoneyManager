import { Handle, Position } from '@xyflow/react'
import type { GateNodeData } from '../types'
import { NODE_COLORS } from '../types'

interface Props {
  data: GateNodeData
  selected?: boolean
}

export function GateNode({ data, selected }: Props) {
  const isAnd = data.gateType === 'and' || data.gateType === 'all_enabled'
  const color = isAnd ? NODE_COLORS.andGate : NODE_COLORS.orGate
  const symbol = isAnd ? '∧' : '∨'
  const label = isAnd ? 'AND' : 'OR'

  return (
    <div
      className={`gate-node-card ${selected ? 'selected' : ''}`}
      style={{ borderColor: color }}
    >
      <Handle type="target" position={Position.Top} style={{ background: color, borderColor: color }} />
      <div className="gate-node-body">
        <span className="gate-symbol" style={{ color }}>{symbol}</span>
        <span className="gate-label" style={{ color }}>{label}</span>
      </div>
      <div className="gate-node-sub" style={{ color: `${color}99` }}>
        {isAnd ? '全部通过' : '任一通过'}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: color, borderColor: color }} />
    </div>
  )
}
