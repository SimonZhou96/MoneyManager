import { Handle, Position } from '@xyflow/react'
import type { OutputNodeData } from '../types'
import { NODE_COLORS } from '../types'

interface Props {
  data: OutputNodeData
  selected?: boolean
}

export function OutputNode({ data, selected }: Props) {
  const color = NODE_COLORS.output
  return (
    <div
      className={`output-node-card ${selected ? 'selected' : ''}`}
      style={{ borderColor: color }}
    >
      <Handle id="target" type="target" position={Position.Top} style={{ background: color, borderColor: color }} />
      <div className="output-node-body">
        <span className="output-icon" style={{ color }}>✓</span>
        <span style={{ color }}>{data.label || '输出'}</span>
      </div>
      <Handle id="source" type="source" position={Position.Bottom} style={{ background: color, borderColor: color }} />
    </div>
  )
}
