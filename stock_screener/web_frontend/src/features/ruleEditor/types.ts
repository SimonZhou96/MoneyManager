import type { Node, Edge } from '@xyflow/react'

// ---- DSL expression types (mirrors rule_engine.py RuleExpressionEvaluator) ----
// Discriminated union: each variant has exactly one operator key.
export type ExpressionNode =
  | { ref: string }
  | { and: ExpressionNode[] }
  | { any: ExpressionNode[] }
  | { all_enabled: string[] }
  | { any_enabled: string[] }

// ---- React Flow node data types ----
// Index signatures required for React Flow v12 generic Node<D,T> compatibility.
export interface RuleNodeData {
  [key: string]: unknown
  ruleKey: string
  ruleName: string
  ruleType: 'filter' | 'strategy'
  strategyCategory: 'technical' | 'macro' | null
  implementation: string
  params?: Record<string, unknown>
  enabled: boolean
  description?: string
}

export interface GateNodeData {
  [key: string]: unknown
  gateType: 'and' | 'any' | 'all_enabled' | 'any_enabled'
  label: string
}

export interface OutputNodeData {
  [key: string]: unknown
  label: string
}

// ---- App node union (React Flow v12 pattern) ----
export type AppNode =
  | Node<RuleNodeData, 'ruleNode'>
  | Node<GateNodeData, 'gateNode'>
  | Node<OutputNodeData, 'outputNode'>

export type AppEdge = Edge

// ---- Atomic rule metadata (from API) ----
export interface AtomicRuleMeta {
  market: string
  rule_key: string
  rule_name: string
  rule_type: string                      // 'filter' | 'strategy'
  strategy_category?: string             // 'technical' | 'macro'
  implementation: string
  params?: Record<string, unknown>
  enabled: boolean
  display_order: number
  description?: string
}

// ---- Rule chain config (from API) ----
export interface RuleChainConfig {
  market: string
  timeframe: string
  chain_key: string
  chain_name: string
  expression: ExpressionNode
  expression_json?: ExpressionNode
  enabled: boolean
  priority: number
  description?: string
}

// ---- Node category colors ----
export const NODE_COLORS: Record<string, string> = {
  filter: '#3b82f6',      // blue — hard filters
  strategy: '#22c55e',    // green — technical signals
  macro: '#c9a84c',       // gold — macro analysis
  andGate: '#8b5cf6',     // purple — AND gate
  orGate: '#f59e0b',      // orange — OR gate
  output: '#e8c560',      // bright gold — output
}

export const NODE_LABELS: Record<string, string> = {
  filter: '硬筛选',
  strategy: '技术信号',
  macro: '宏观评估',
  andGate: 'AND (全部通过)',
  orGate: 'OR (任一通过)',
  output: '输出判定',
}

/** Determine node display category from rule metadata */
export function ruleNodeCategory(rule: AtomicRuleMeta): 'filter' | 'strategy' | 'macro' {
  if (rule.rule_type === 'filter') return 'filter'
  if (rule.strategy_category === 'macro') return 'macro'
  return 'strategy'
}
