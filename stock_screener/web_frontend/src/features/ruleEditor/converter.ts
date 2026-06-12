/**
 * Bidirectional converter: JSON DSL expression tree ↔ React Flow nodes + edges.
 *
 * DSL operators (from rule_engine.py RuleExpressionEvaluator):
 *   {"ref": "rule_key"}              → single rule leaf node
 *   {"and": [...]}                   → AND gate with nested children
 *   {"any": [...]}                   → OR gate with nested children
 *   {"all_enabled": ["a","b"]}       → AND gate with rule-key children
 *   {"any_enabled": ["a","b"]}       → OR gate with rule-key children
 *
 * React Flow format:
 *   nodes: { id, type, position, data }
 *   edges:  { id, source, target }
 */

import type { Node, Edge } from '@xyflow/react'
import type { ExpressionNode, RuleNodeData, GateNodeData, OutputNodeData, AtomicRuleMeta } from './types'

let _nodeCounter = 0
let _edgeCounter = 0

function nextNodeId(): string {
  _nodeCounter++
  return `n${_nodeCounter}`
}

function nextEdgeId(): string {
  _edgeCounter++
  return `e${_edgeCounter}`
}

function resetCounters(): void {
  _nodeCounter = 0
  _edgeCounter = 0
}

// ---- rule metadata registry for lookup during conversion ----
let _ruleMetaByKey: Record<string, AtomicRuleMeta> = {}

export function setRuleMetaRegistry(meta: AtomicRuleMeta[]): void {
  _ruleMetaByKey = {}
  for (const m of meta) {
    _ruleMetaByKey[m.rule_key] = m
  }
}

// ================================================================
//  DSL → React Flow  (deserialization: load from DB → render canvas)
// ================================================================

export interface ConvertResult {
  nodes: Node[]
  edges: Edge[]
  outputNodeId: string
}

export function expressionToFlow(expression: ExpressionNode): ConvertResult {
  resetCounters()

  // Create the output (root) node
  const outputId = nextNodeId()
  const outputNode: Node<OutputNodeData, 'outputNode'> = {
    id: outputId,
    type: 'outputNode',
    position: { x: 400, y: 0 },
    data: { label: '输出' },
  }

  const { nodes, edges } = convertSubExpr(expression, outputId, 400, 120)

  return {
    nodes: [outputNode as Node, ...nodes],
    edges,
    outputNodeId: outputId,
  }
}

/** Check if value is an ExpressionNode (not a bare string). */
function isExprObj(v: unknown): v is ExpressionNode {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** Recursively convert an expression sub-tree attached to a parent node. */
function convertSubExpr(
  expr: ExpressionNode,
  parentId: string,
  baseX: number,
  y: number,
): { nodes: Node[]; edges: Edge[] } {
  const e = expr as Record<string, unknown>

  // {"ref": "rule_key"}
  if (typeof e.ref === 'string') {
    return makeRuleNode(e.ref, parentId, baseX, y)
  }

  // {"and": [...]}
  if (Array.isArray(e.and)) {
    return makeGateNode('and', e.and as ExpressionNode[], parentId, baseX, y)
  }

  // {"any": [...]}
  if (Array.isArray(e.any)) {
    return makeGateNode('any', e.any as ExpressionNode[], parentId, baseX, y)
  }

  // {"all_enabled": ["a","b"]}
  if (Array.isArray(e.all_enabled)) {
    return makeEnabledGateNode('all_enabled', e.all_enabled as string[], parentId, baseX, y)
  }

  // {"any_enabled": ["a","b"]}
  if (Array.isArray(e.any_enabled)) {
    return makeEnabledGateNode('any_enabled', e.any_enabled as string[], parentId, baseX, y)
  }

  return { nodes: [], edges: [] }
}

function makeRuleNode(ruleKey: string, parentId: string, x: number, y: number): { nodes: Node[]; edges: Edge[] } {
  const id = nextNodeId()
  const meta = _ruleMetaByKey[ruleKey]
  const data: RuleNodeData = {
    ruleKey,
    ruleName: meta?.rule_name || ruleKey,
    ruleType: (meta?.rule_type as 'filter' | 'strategy') || 'strategy',
    strategyCategory: (meta?.strategy_category as 'technical' | 'macro') || null,
    implementation: meta?.implementation || '',
    params: meta?.params,
    enabled: meta?.enabled ?? true,
    description: meta?.description,
  }
  const node: Node<RuleNodeData, 'ruleNode'> = { id, type: 'ruleNode', position: { x, y }, data }
  const edge: Edge = { id: nextEdgeId(), source: parentId, target: id }
  return { nodes: [node as Node], edges: [edge] }
}

function makeGateNode(
  gateType: 'and' | 'any',
  children: ExpressionNode[],
  parentId: string,
  x: number,
  y: number,
): { nodes: Node[]; edges: Edge[] } {
  const gateId = nextNodeId()
  const data: GateNodeData = {
    gateType,
    label: gateType === 'and' ? 'AND' : 'OR',
  }
  const gateNode: Node<GateNodeData, 'gateNode'> = { id: gateId, type: 'gateNode', position: { x, y }, data }
  const parentEdge: Edge = { id: nextEdgeId(), source: parentId, target: gateId }

  const allNodes: Node[] = [gateNode as Node]
  const allEdges: Edge[] = [parentEdge]

  const childCount = children.length
  const totalWidth = childCount * 180
  const startX = x - totalWidth / 2 + 90

  for (let i = 0; i < children.length; i++) {
    const childX = startX + i * 180
    const childY = y + 100
    const result = convertSubExpr(children[i], gateId, childX, childY)
    allNodes.push(...result.nodes)
    allEdges.push(...result.edges)
  }

  return { nodes: allNodes, edges: allEdges }
}

function makeEnabledGateNode(
  gateType: 'all_enabled' | 'any_enabled',
  ruleKeys: string[],
  parentId: string,
  x: number,
  y: number,
): { nodes: Node[]; edges: Edge[] } {
  const gateId = nextNodeId()
  const data: GateNodeData = {
    gateType,
    label: gateType === 'all_enabled' ? 'AND (启用)' : 'OR (启用)',
  }
  const gateNode: Node<GateNodeData, 'gateNode'> = { id: gateId, type: 'gateNode', position: { x, y }, data }
  const parentEdge: Edge = { id: nextEdgeId(), source: parentId, target: gateId }

  const allNodes: Node[] = [gateNode as Node]
  const allEdges: Edge[] = [parentEdge]

  const count = ruleKeys.length
  const totalWidth = count * 180
  const startX = x - totalWidth / 2 + 90

  for (let i = 0; i < ruleKeys.length; i++) {
    const childX = startX + i * 180
    const childY = y + 100
    const result = makeRuleNode(ruleKeys[i], gateId, childX, childY)
    allNodes.push(...result.nodes)
    allEdges.push(...result.edges)
  }

  return { nodes: allNodes, edges: allEdges }
}

// ================================================================
//  React Flow → DSL  (serialization: canvas edits → save to DB)
// ================================================================

export function flowToExpression(nodes: Node[], edges: Edge[], outputNodeId: string): ExpressionNode {
  const outputNode = nodes.find(n => n.id === outputNodeId)
  if (!outputNode) return { ref: '' }

  // Follow the first child edge from output to get the actual expression root
  const firstChildEdge = edges.find(e => e.source === outputNodeId)
  if (!firstChildEdge) return { ref: '' }

  return nodeToExpression(firstChildEdge.target, nodes, edges)
}

function nodeToExpression(nodeId: string, nodes: Node[], edges: Edge[]): ExpressionNode {
  const node = nodes.find(n => n.id === nodeId)
  if (!node) return { ref: '' }

  // Rule node → {"ref": "rule_key"}
  if (node.type === 'ruleNode') {
    const data = node.data as unknown as RuleNodeData
    return { ref: data.ruleKey }
  }

  // Gate node → {"and"/"any"/"all_enabled"/"any_enabled": [...]}
  if (node.type === 'gateNode') {
    const data = node.data as unknown as GateNodeData
    const childEdges = edges.filter(e => e.source === nodeId)
    const childIds = childEdges.map(e => e.target)

    // all_enabled / any_enabled: children are flat rule keys
    if (data.gateType === 'all_enabled' || data.gateType === 'any_enabled') {
      const keys = childIds
        .map(id => {
          const childNode = nodes.find(n => n.id === id)
          if (childNode?.type === 'ruleNode') return (childNode.data as unknown as RuleNodeData).ruleKey
          return null
        })
        .filter(Boolean) as string[]
      return { [data.gateType]: keys } as ExpressionNode
    }

    // and / any: children can be nested sub-expressions
    const children = childIds
      .map(id => nodeToExpression(id, nodes, edges))
      .filter(c => !(isRefNode(c) && c.ref === ''))
    return { [data.gateType]: children } as ExpressionNode
  }

  return { ref: '' }
}

function isRefNode(e: ExpressionNode): e is { ref: string } {
  return 'ref' in e
}
