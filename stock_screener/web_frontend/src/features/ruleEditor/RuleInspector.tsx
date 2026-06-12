import type { Node } from '@xyflow/react'
import type { RuleNodeData, GateNodeData } from './types'
import { NODE_COLORS, NODE_LABELS } from './types'

interface Props {
  node: Node | null
  allNodes: Node[]
  onDeleteNode: (nodeId: string) => void
  onChangeGateType: (nodeId: string, gateType: 'and' | 'any' | 'all_enabled' | 'any_enabled') => void
}

export function RuleInspector({ node, allNodes, onDeleteNode, onChangeGateType }: Props) {
  if (!node) {
    return (
      <div className="rule-inspector">
        <div className="rule-inspector-empty">选中一个节点查看属性</div>
      </div>
    )
  }

  return (
    <div className="rule-inspector">
      <div className="rule-inspector-header">
        <h3>节点属性</h3>
        <button className="secondary-button" onClick={() => onDeleteNode(node.id)}>删除节点</button>
      </div>

      {node.type === 'ruleNode' && <RuleNodeInspector data={node.data as unknown as RuleNodeData} />}
      {node.type === 'gateNode' && <GateNodeInspector data={node.data as unknown as GateNodeData} nodeId={node.id} onChangeGateType={onChangeGateType} />}
      {node.type === 'outputNode' && <OutputNodeInspector />}
    </div>
  )
}

function RuleNodeInspector({ data }: { data: RuleNodeData }) {
  const cat = data.ruleType === 'filter' ? 'filter' : data.strategyCategory === 'macro' ? 'macro' : 'strategy'
  const color = NODE_COLORS[cat]
  return (
    <div className="inspector-fields">
      <Field label="类型" value={<span style={{ color }}>{NODE_LABELS[cat]}</span>} />
      <Field label="规则 Key" value={data.ruleKey} />
      <Field label="规则名称" value={data.ruleName} />
      <Field label="实现类" value={data.implementation || '-'} />
      <Field label="启用" value={data.enabled ? '✅ 是' : '⛔ 否'} />
      {data.params && Object.keys(data.params).length > 0 && (
        <div className="inspector-params">
          <div className="inspector-params-title">参数</div>
          {Object.entries(data.params).map(([k, v]) => (
            <Field key={k} label={k} value={String(v)} />
          ))}
        </div>
      )}
    </div>
  )
}

function GateNodeInspector({
  data,
  nodeId,
  onChangeGateType,
}: { data: GateNodeData; nodeId: string; onChangeGateType: (id: string, type: 'and' | 'any' | 'all_enabled' | 'any_enabled') => void }) {
  const isAnd = data.gateType === 'and' || data.gateType === 'all_enabled'
  const color = isAnd ? NODE_COLORS.andGate : NODE_COLORS.orGate
  return (
    <div className="inspector-fields">
      <Field label="类型" value={<span style={{ color }}>逻辑门</span>} />
      <div className="inspector-gate-type">
        <label className="inspector-label">逻辑</label>
        <div className="segmented" style={{ display: 'flex', gap: 6 }}>
          <GateChip label="AND" active={data.gateType === 'and'} onClick={() => onChangeGateType(nodeId, 'and')} />
          <GateChip label="OR" active={data.gateType === 'any'} onClick={() => onChangeGateType(nodeId, 'any')} />
          <GateChip label="AND (启用)" active={data.gateType === 'all_enabled'} onClick={() => onChangeGateType(nodeId, 'all_enabled')} />
          <GateChip label="OR (启用)" active={data.gateType === 'any_enabled'} onClick={() => onChangeGateType(nodeId, 'any_enabled')} />
        </div>
      </div>
      <Field label="语义" value={isAnd ? '所有子规则必须通过' : '任一子规则通过即可'} />
    </div>
  )
}

function OutputNodeInspector() {
  return (
    <div className="inspector-fields">
      <Field label="类型" value={<span style={{ color: NODE_COLORS.output }}>输出判定</span>} />
      <Field label="说明" value="规则链的终点，链中所有规则在此汇聚判定" />
    </div>
  )
}

function GateChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className={`gate-chip ${active ? 'active' : ''}`}
      onClick={onClick}
    >
      {label}
    </button>
  )
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="inspector-field">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
