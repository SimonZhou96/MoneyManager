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
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { ExpressionNode, AtomicRuleMeta, RuleNodeData } from './types'
import { NODE_COLORS } from './types'
import { expressionToFlow, flowToExpression, setRuleMetaRegistry } from './converter'
import { RuleNode } from './nodes/RuleNode'
import { GateNode } from './nodes/GateNode'
import { OutputNode } from './nodes/OutputNode'
import { RuleSidebar } from './RuleSidebar'
import { RuleInspector } from './RuleInspector'

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
        </div>
        <div className="rule-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
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
