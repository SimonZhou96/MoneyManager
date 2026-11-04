import { Handle, Position, NodeResizer } from '@xyflow/react'
import type { RuleNodeData } from '../types'
import { NODE_COLORS, NODE_LABELS } from '../types'

interface Props {
  data: RuleNodeData
  selected?: boolean
}

export function RuleNode({ data, selected }: Props) {
  const cat = data.ruleType === 'filter' ? 'filter' : data.strategyCategory === 'macro' ? 'macro' : 'strategy'
  const color = NODE_COLORS[cat] || '#8a8070'
  const catLabel = NODE_LABELS[cat] || cat

  return (
    <>
      <NodeResizer
        minWidth={160}
        maxWidth={400}
        isVisible={selected}
        lineStyle={{ borderColor: color }}
        handleStyle={{ borderColor: color, background: '#141920' }}
      />
      <div
        className={`rule-node-card ${selected ? 'selected' : ''}`}
        style={{ borderColor: color, width: '100%', height: '100%' }}
      >
        <Handle id="target" type="target" position={Position.Top} style={{ background: color, borderColor: color }} />
        <div className="rule-node-header" style={{ background: `${color}18` }}>
          <span className="rule-node-cat" style={{ color }}>{catLabel}</span>
          <span className="rule-node-key" style={{ color }}>{data.ruleKey}</span>
        </div>
        <div className="rule-node-body">
          <strong>{data.ruleName}</strong>
          {data.enabled === false && <span className="rule-node-disabled">已禁用</span>}
        </div>
        <Handle id="source" type="source" position={Position.Bottom} style={{ background: color, borderColor: color }} />
      </div>
    </>
  )
}
