import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  type Connection,
  MarkerType,
  BackgroundVariant,
  type Node,
  type Edge,
  type OnConnect,
  type ReactFlowInstance,
  type IsValidConnection,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { ExpressionNode, AtomicRuleMeta, RuleNodeData, GateNodeData } from './types'
import { NODE_COLORS } from './types'
import { expressionToFlow, flowToExpression, setRuleMetaRegistry } from './converter'
import { RuleNode } from './nodes/RuleNode'
import { GateNode } from './nodes/GateNode'
import { OutputNode } from './nodes/OutputNode'
import { RuleSidebar } from './RuleSidebar'
import { RuleInspector } from './RuleInspector'

// ── Validation helpers (module-level, no component dependency) ──

type ValidationStatus = 'valid' | 'warning' | 'error'

interface ExpressionValidation {
  status: ValidationStatus
  message: string
}

/** Check if adding edge source→target would create a cycle (walk up from target). */
function wouldCreateCycle(sourceId: string, targetId: string, edges: Edge[]): boolean {
  const visited = new Set<string>()
  const stack = [targetId]
  while (stack.length > 0) {
    const current = stack.pop()!
    if (current === sourceId) return true
    if (visited.has(current)) continue
    visited.add(current)
    for (const e of edges) {
      if (e.target === current) stack.push(e.source)
    }
  }
  return false
}

/** Validate the current graph structure and compute the expression. */
function validateCanvas(
  nodes: Node[],
  edges: Edge[],
  outputNodeId: string,
): ExpressionValidation {
  // No output node
  if (!nodes.find(n => n.id === outputNodeId)) {
    return { status: 'error', message: '输出节点缺失' }
  }

  // Orphaned nodes with no incoming edges (not the output itself)
  const nodesWithIncoming = new Set(edges.map(e => e.target))
  nodesWithIncoming.add(outputNodeId)
  const orphans = nodes.filter(n => n.type !== 'outputNode' && !nodesWithIncoming.has(n.id))
  if (orphans.length > 0) {
    return { status: 'warning', message: `${orphans.length} 个游离节点` }
  }

  // Rule nodes must not have outgoing edges
  const ruleSources = edges.filter(e => {
    const src = nodes.find(n => n.id === e.source)
    return src?.type === 'ruleNode'
  })
  if (ruleSources.length > 0) {
    return { status: 'error', message: '规则节点不能有出边' }
  }

  // Try full expression conversion
  try {
    flowToExpression(nodes, edges, outputNodeId)
  } catch {
    return { status: 'error', message: '表达式转换失败' }
  }

  // Warn on empty gate children
  const gateNodes = nodes.filter(n => n.type === 'gateNode')
  for (const g of gateNodes) {
    const childEdges = edges.filter(e => e.source === g.id)
    if (childEdges.length === 0) {
      const d = g.data as unknown as GateNodeData
      return { status: 'warning', message: `门节点 "${d.label}" 下无子节点` }
    }
  }

  return { status: 'valid', message: '表达式有效' }
}

// Register custom node types (stable ref — defined outside component)
const nodeTypes = {
  ruleNode: RuleNode as any,
  gateNode: GateNode as any,
  outputNode: OutputNode as any,
}

interface Props {
  rules: AtomicRuleMeta[]
  expression: ExpressionNode | null
  onExpressionChange: (expr: ExpressionNode) => void
}

export function RuleChainEditor({ rules, expression, onExpressionChange }: Props) {
  const reactFlowInstance = useRef<ReactFlowInstance | null>(null)

  // React Flow state — use base Node/Edge types for compatibility, cast data accesses
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [outputNodeId, setOutputNodeId] = useState('')

  // Register rule metadata for converter lookups
  useEffect(() => {
    setRuleMetaRegistry(rules)
  }, [rules])

  // Load expression into canvas (re-trigger on expression text change)
  const exprKey = useMemo(() => JSON.stringify(expression), [expression])
  useEffect(() => {
    if (!expression) return
    try {
      const result = expressionToFlow(expression)
      setNodes(result.nodes)
      setEdges(result.edges)
      setOutputNodeId(result.outputNodeId)
    } catch {
      resetCanvas()
    }
  }, [exprKey])

  // Sync canvas changes back to DSL
  const syncToExpression = useCallback(() => {
    if (!outputNodeId || nodes.length === 0) return
    try {
      const expr = flowToExpression(nodes, edges, outputNodeId)
      onExpressionChange(expr)
    } catch { /* ignore */ }
  }, [nodes, edges, outputNodeId, onExpressionChange])

  // Auto-sync on node/edge changes (debounced via effect timing)
  useEffect(() => {
    const timer = setTimeout(syncToExpression, 300)
    return () => clearTimeout(timer)
  }, [nodes, edges])

  // Expression validation state (computed after every node/edge change)
  const validation = useMemo<ExpressionValidation>(
    () => validateCanvas(nodes, edges, outputNodeId),
    [nodes, edges, outputNodeId],
  )

  // Connection validation: prevent invalid edges
  const isValidConnection = useCallback<IsValidConnection<Edge>>(
    (connection) => {
      if (!connection.source || !connection.target || connection.source === connection.target) return false
      const sourceNode = nodes.find(n => n.id === connection.source)
      const targetNode = nodes.find(n => n.id === connection.target)
      if (!sourceNode || !targetNode) return false
      // Rule nodes are leaves — no outgoing edges
      if (sourceNode.type === 'ruleNode') return false
      // Output node is root — nothing connects TO it
      if (targetNode.type === 'outputNode') return false
      // Cycle detection
      if (wouldCreateCycle(connection.source, connection.target, edges)) return false
      return true
    },
    [nodes, edges],
  )

  // ---- handlers ----
  const onConnect: OnConnect = useCallback(
    (connection: Connection) => {
      setEdges(eds => addEdge({ ...connection, markerEnd: { type: MarkerType.ArrowClosed, color: '#c9a84c' } }, eds))
    },
    [setEdges],
  )

  const onDragStart = useCallback((event: React.DragEvent, rule: AtomicRuleMeta) => {
    event.dataTransfer.setData('application/json', JSON.stringify(rule))
    event.dataTransfer.effectAllowed = 'move'
  }, [])

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      const raw = event.dataTransfer.getData('application/json')
      if (!raw || !reactFlowInstance.current) return
      const rule: AtomicRuleMeta = JSON.parse(raw)
      const position = reactFlowInstance.current.screenToFlowPosition({ x: event.clientX, y: event.clientY })

      const id = `n${Date.now()}`
      const newNode: Node = {
        id,
        type: 'ruleNode',
        position,
        data: {
          ruleKey: rule.rule_key,
          ruleName: rule.rule_name,
          ruleType: rule.rule_type as 'filter' | 'strategy',
          strategyCategory: (rule.strategy_category as 'technical' | 'macro') || null,
          implementation: rule.implementation,
          params: rule.params,
          enabled: rule.enabled,
          description: rule.description,
        },
      }
      setNodes(nds => [...nds, newNode])
    },
    [setNodes],
  )

  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setSelectedNode(node)
  }, [])

  const onPaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [])

  const deleteNode = useCallback((nodeId: string) => {
    setNodes(nds => nds.filter(n => n.id !== nodeId))
    setEdges(eds => eds.filter(e => e.source !== nodeId && e.target !== nodeId))
    setSelectedNode(null)
  }, [setNodes, setEdges])

  const changeGateType = useCallback((nodeId: string, gateType: 'and' | 'any' | 'all_enabled' | 'any_enabled') => {
    setNodes(nds => nds.map(n => {
      if (n.id === nodeId && n.type === 'gateNode') {
        return { ...n, data: { ...n.data, gateType, label: gateType === 'and' || gateType === 'all_enabled' ? 'AND' : 'OR' } }
      }
      return n
    }))
  }, [setNodes])

  // Add a gate node at canvas center
  const addGateNode = useCallback((gateType: 'and' | 'any') => {
    if (!reactFlowInstance.current) return
    const center = reactFlowInstance.current.screenToFlowPosition({
      x: window.innerWidth / 2,
      y: window.innerHeight / 3,
    })
    const id = `n${Date.now()}`
    const newNode: Node = {
      id,
      type: 'gateNode',
      position: center,
      data: { gateType, label: gateType === 'and' ? 'AND' : 'OR' },
    }
    setNodes(nds => [...nds, newNode])
  }, [setNodes])

  // Reset to empty canvas with output node
  const resetCanvas = useCallback(() => {
    const outId = `n${Date.now()}`
    const outNode: Node = {
      id: outId,
      type: 'outputNode',
      position: { x: 400, y: 0 },
      data: { label: '输出' },
    }
    setNodes([outNode])
    setEdges([])
    setOutputNodeId(outId)
    setSelectedNode(null)
  }, [setNodes, setEdges])

  // Fit view after nodes loaded
  useEffect(() => {
    if (nodes.length > 0 && reactFlowInstance.current) {
      setTimeout(() => reactFlowInstance.current?.fitView({ padding: 0.2, duration: 300 }), 100)
    }
  }, [nodes.length === 0]) // only on initial load

  return (
    <div className="rule-chain-editor">
      {/* Left — Sidebar */}
      <RuleSidebar rules={rules} onDragStart={onDragStart} />

      {/* Center — Canvas */}
      <div className="rule-canvas-wrap">
        <div className="rule-canvas-toolbar">
          <button className="secondary-button" onClick={() => addGateNode('and')}>+ AND 门</button>
          <button className="secondary-button" onClick={() => addGateNode('any')}>+ OR 门</button>
          <button className="secondary-button" onClick={resetCanvas}>清空画布</button>
          <button className="secondary-button" onClick={syncToExpression}>刷新表达式</button>
          <span className={`expr-status expr-status-${validation.status}`}>
            {validation.status === 'error' ? '❌' : validation.status === 'warning' ? '⚠️' : '✅'} {validation.message}
          </span>
        </div>
        <div className="rule-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            onDragOver={onDragOver}
            onDrop={onDrop}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            onInit={instance => { reactFlowInstance.current = instance }}
            nodeTypes={nodeTypes}
            colorMode="dark"
            fitView
            deleteKeyCode={['Backspace', 'Delete']}
            multiSelectionKeyCode="Shift"
            snapToGrid
            snapGrid={[20, 20]}
            minZoom={0.5}
            defaultEdgeOptions={{
              style: { stroke: '#c9a84c', strokeWidth: 2 },
              markerEnd: { type: MarkerType.ArrowClosed, color: '#c9a84c' },
            }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#2a3040" />
            <Controls className="rule-flow-controls" />
            <MiniMap
              className="rule-flow-minimap"
              nodeColor={n => {
                if (n.type === 'ruleNode') {
                  const d = n.data as unknown as RuleNodeData
                  const cat = d.ruleType === 'filter' ? 'filter' : d.strategyCategory === 'macro' ? 'macro' : 'strategy'
                  return NODE_COLORS[cat] || '#8a8070'
                }
                if (n.type === 'gateNode') {
                  const d = n.data as unknown as { gateType: string }
                  return d.gateType === 'and' || d.gateType === 'all_enabled' ? NODE_COLORS.andGate : NODE_COLORS.orGate
                }
                return NODE_COLORS.output
              }}
            />
          </ReactFlow>
        </div>
      </div>

      {/* Right — Inspector */}
      <RuleInspector
        node={selectedNode}
        allNodes={nodes}
        onDeleteNode={deleteNode}
        onChangeGateType={changeGateType}
      />
    </div>
  )
}
