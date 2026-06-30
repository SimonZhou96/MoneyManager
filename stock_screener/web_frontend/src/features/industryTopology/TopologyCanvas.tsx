import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, forwardRef } from 'react'
import { Circle } from '@antv/g'
import { Graph } from '@antv/g6'
import type { TopologyEdgeData, TopologyNodeColorMetric, TopologyNodeData, TopologyNodePatch, TopologyQuoteItem, TopologyRenderEdgeData, TopologyRenderNodeData } from './types'
import { displaySector, isAggregateEdge, isAggregateNode } from './topologyAggregation'

interface Props {
  rawNodes: TopologyRenderNodeData[]
  rawEdges: TopologyRenderEdgeData[]
  onExpand: (code: string, market: string) => void
  onExpandAggregate?: (sector: string) => void
  onCollapseAggregate?: (sector: string) => void
  onStartTopology: () => void
  onRefreshTopology: () => void
  canStartTopology: boolean
  canRefreshTopology: boolean
  isTopologyBusy: boolean
  startTopologyLabel?: string
  nodeColorMetric: TopologyNodeColorMetric
  selectedNodeId?: string | null
  selectedEdgeKey?: string | null
  focusNodeId?: string | null
  matchedNodeIds?: Set<string> | string[]
  normalNodeIds?: Set<string> | string[]
  normalEdgeKeys?: Set<string> | string[]
  flowEdgeKeys?: Set<string> | string[]
  highlightCycles?: boolean
  onSelectNode?: (node: TopologyRenderNodeData | null) => void
  onSelectEdge?: (edge: TopologyRenderEdgeData | null, edgeKey?: string) => void
}

export interface TopologyCanvasHandle {
  applyQuotes: (items: TopologyQuoteItem[]) => void
  applyNodePatches: (items: TopologyNodePatch[]) => void
}

type G6Graph = InstanceType<typeof Graph>
type GraphDatum = { id: string; data?: Record<string, unknown>; style?: Record<string, unknown> }
type GraphPluginOption = Record<string, unknown> & { type: string; key?: string }
type RenderedEdgeData = TopologyRenderEdgeData & { edgeKey?: string; visualSource?: string; visualTarget?: string }
type ToolbarAction = 'start-topology' | 'refresh-topology'
type NodeDegreeStats = { out: number; in: number; total: number }
type SectorHullGroup = { sector: string; memberIds: string[]; zone?: string }

interface ToolbarState {
  canStartTopology: boolean
  canRefreshTopology: boolean
  isTopologyBusy: boolean
  startTopologyLabel: string
}

interface ViewState {
  zoom: number
  hoveredNodeId?: string | null
  hoveredEdgeKey?: string | null
  selectedNodeId?: string | null
  selectedEdgeKey?: string | null
  focusNodeId?: string | null
  relatedNodeIds: Set<string>
  relatedEdgeKeys: Set<string>
  matchedNodeIds: Set<string>
  normalNodeIds: Set<string>
  normalEdgeKeys: Set<string>
  cycleEdgeKeys: Set<string>
  highlightCycles: boolean
  pinnedEdgeKeys: Set<string>
}

const STATUS_RING: Record<string, string> = {
  pending: '#64748b',
  queued: '#64748b',
  cached: '#22c55e',
  fresh: '#22c55e',
  stale: '#eab308',
  failed: '#ef4444',
  error: '#ef4444',
  skipped: '#94a3b8',
}

const EDGE_COLOR: Record<string, string> = {
  upstream: '#60a5fa',
  downstream: '#f59e0b',
  peer: '#8b5cf6',
}

const CENTER_OUT_EDGE = '#60a5fa'
const CENTER_IN_EDGE = '#fbbf24'
const DEFAULT_EDGE = '#64748b'
const TOPOLOGY_ACTION_TOOLBAR_KEY = 'topology-action-toolbar'
const TOPOLOGY_TOOLTIP_KEY = 'topo-tooltip'
const SECTOR_HULL_KEY_PREFIX = 'topo-sector-hull:'
const CENTER_NODE_FILL = '#fbbf24'
const DEGREE_GOLD_MIN = { r: 254, g: 243, b: 199 }
const DEGREE_GOLD_MAX = { r: 180, g: 83, b: 9 }

const NODE_COLOR_METRIC_LABEL: Record<TopologyNodeColorMetric, string> = {
  out_degree: '出度',
  in_degree: '入度',
  total_degree: '总度数',
}

export function sizeLevelFromMarketCap(marketCap: number | null | undefined): 1 | 2 | 3 | 4 | 5 | 6 {
  if (marketCap === null || marketCap === undefined || !Number.isFinite(marketCap)) return 1
  if (marketCap < 50 * 1e8) return 1
  if (marketCap < 200 * 1e8) return 2
  if (marketCap < 1000 * 1e8) return 3
  if (marketCap < 3000 * 1e8) return 4
  if (marketCap < 1e12) return 5
  return 6
}

function nodeRadiusFromMarketCap(marketCap: number | null | undefined): number {
  if (marketCap === null || marketCap === undefined || !Number.isFinite(marketCap) || marketCap <= 0) return 11
  const MIN_CAP = 5e9
  const MAX_CAP = 1e13
  const R_MIN = 13
  const R_MAX = 48
  const cap = Math.min(MAX_CAP, Math.max(MIN_CAP, marketCap))
  const t = (Math.log10(cap) - Math.log10(MIN_CAP)) / (Math.log10(MAX_CAP) - Math.log10(MIN_CAP))
  return R_MIN + t * (R_MAX - R_MIN)
}

function nodeSize(node: TopologyRenderNodeData) {
  if (isAggregateNode(node)) return Math.min(54, 28 + node.hidden_node_ids.length * 1.2)
  return node.is_center ? 42 : nodeRadiusFromMarketCap(node.market_cap)
}

function shortText(text: string, maxLength: number) {
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text
}

function sectorHullKey(sector: string) {
  return `${SECTOR_HULL_KEY_PREFIX}${encodeURIComponent(sector)}`
}

function sectorHullFill(index: number) {
  const fills = [
    'rgba(96, 165, 250, 0.09)',
    'rgba(45, 212, 191, 0.08)',
    'rgba(251, 191, 36, 0.08)',
    'rgba(167, 139, 250, 0.08)',
    'rgba(248, 113, 113, 0.07)',
  ]
  return fills[index % fills.length]
}

function sectorHullStroke(index: number) {
  const strokes = ['#60a5fa', '#2dd4bf', '#fbbf24', '#a78bfa', '#f87171']
  return strokes[index % strokes.length]
}

function sectorHullGroups(nodes: TopologyRenderNodeData[]): SectorHullGroup[] {
  const visibleNodeIds = new Set(nodes.map((node) => node.id))
  const sectorMembers = new Map<string, Set<string>>()
  const sectorZone = new Map<string, string>()

  nodes.forEach((node) => {
    if (node.is_center) return
    const sector = isAggregateNode(node) ? node.aggregate_sector : displaySector(node)
    const members = sectorMembers.get(sector) || new Set<string>()
    members.add(node.id)
    if (isAggregateNode(node)) {
      node.visible_representative_ids.forEach((id) => {
        if (visibleNodeIds.has(id)) members.add(id)
      })
      sectorZone.set(sector, node.zone)
    } else if (!sectorZone.has(sector)) {
      sectorZone.set(sector, node.zone)
    }
    sectorMembers.set(sector, members)
  })

  return [...sectorMembers.entries()]
    .map(([sector, members]) => ({
      sector,
      memberIds: [...members],
      zone: sectorZone.get(sector),
    }))
    .filter((group) => group.memberIds.length >= 2)
}

function sectorHullPlugins(nodes: TopologyRenderNodeData[]): GraphPluginOption[] {
  const plugins: GraphPluginOption[] = []
  sectorHullGroups(nodes).forEach((group, index) => {
    plugins.push({
      type: 'hull',
      key: sectorHullKey(group.sector),
      members: group.memberIds,
      padding: 38,
      concavity: 120,
      corner: 'rounded',
      fill: sectorHullFill(index),
      stroke: sectorHullStroke(index),
      lineWidth: 1.6,
      lineDash: [8, 6],
      label: true,
      labelText: group.sector,
      labelPlacement: group.zone === 'downstream' ? 'bottom' : 'top',
      labelCloseToPath: false,
      labelFill: '#dbeafe',
      labelFontSize: 11,
      labelFontWeight: 800,
      labelBackground: true,
      labelBackgroundFill: 'rgba(15, 23, 42, 0.84)',
      labelBackgroundRadius: 5,
      labelPadding: [3, 7, 3, 7],
    })
  })
  return plugins
}

function refreshSectorHulls(graph: G6Graph, nodes: TopologyRenderNodeData[]) {
  sectorHullGroups(nodes).forEach((group) => {
    const hull = graph.getPluginInstance(sectorHullKey(group.sector)) as { updateMember?: (members: string[]) => void } | undefined
    hull?.updateMember?.(group.memberIds)
  })
}

function nodeLabel(node: TopologyRenderNodeData, view: ViewState) {
  if (isAggregateNode(node)) return `${node.aggregate_sector}\n+${node.hidden_node_ids.length}`
  const text = node.name || node.code
  if (node.is_center) return `${text}\n${node.id}`
  const isActive = node.id === view.hoveredNodeId || node.id === view.selectedNodeId || node.id === view.focusNodeId
  const isImportant = node.depth <= 1 && node.size_level >= 3
  if (!isActive && view.zoom < 0.7) return ''
  if (!isActive && !isImportant && view.zoom < 1.1) return node.depth <= 1 ? shortText(text, 8) : ''
  const name = shortText(text, isActive ? 12 : 8)
  const sector = node.sector && node.sector !== '--' ? node.sector : '板块未知'
  if (!isActive && view.zoom < 1.15) return name
  return `${name}\n${shortText(sector, 8)}`
}

function hasMarketCap(node: TopologyRenderNodeData) {
  return node.market_cap !== null && node.market_cap !== undefined && node.market_cap_str !== '未知' && node.market_cap_str !== '--'
}

function buildNodeDegreeMap(nodes: TopologyRenderNodeData[], edges: TopologyRenderEdgeData[]): Map<string, NodeDegreeStats> {
  const degreeMap = new Map<string, NodeDegreeStats>()
  nodes.forEach((node) => degreeMap.set(node.id, { out: 0, in: 0, total: 0 }))
  edges.forEach((edge) => {
    const sourceStats = degreeMap.get(edge.source)
    if (sourceStats) {
      sourceStats.out += 1
      sourceStats.total += 1
    }

    const targetStats = degreeMap.get(edge.target)
    if (targetStats) {
      targetStats.in += 1
      targetStats.total += 1
    }
  })
  return degreeMap
}

function getDegreeMetricValue(stats: NodeDegreeStats | undefined, metric: TopologyNodeColorMetric): number {
  if (!stats) return 0
  if (metric === 'out_degree') return stats.out
  if (metric === 'in_degree') return stats.in
  return stats.total
}

function interpolateGoldColor(value: number, maxValue: number): string {
  const ratio = maxValue > 0 ? Math.min(Math.max(value / maxValue, 0), 1) : 0
  const r = Math.round(DEGREE_GOLD_MIN.r + (DEGREE_GOLD_MAX.r - DEGREE_GOLD_MIN.r) * ratio)
  const g = Math.round(DEGREE_GOLD_MIN.g + (DEGREE_GOLD_MAX.g - DEGREE_GOLD_MIN.g) * ratio)
  const b = Math.round(DEGREE_GOLD_MIN.b + (DEGREE_GOLD_MAX.b - DEGREE_GOLD_MIN.b) * ratio)
  return `rgb(${r}, ${g}, ${b})`
}

function buildNodeFillMap(nodes: TopologyRenderNodeData[], edges: TopologyRenderEdgeData[], metric: TopologyNodeColorMetric): Map<string, string> {
  const degreeMap = buildNodeDegreeMap(nodes, edges)
  const maxMetricValue = Math.max(0, ...nodes.map((node) => getDegreeMetricValue(degreeMap.get(node.id), metric)))
  const fillMap = new Map<string, string>()
  nodes.forEach((node) => {
    const metricValue = getDegreeMetricValue(degreeMap.get(node.id), metric)
    fillMap.set(node.id, node.is_center ? CENTER_NODE_FILL : interpolateGoldColor(metricValue, maxMetricValue))
  })
  return fillMap
}

function nodeStyle(node: TopologyRenderNodeData, view: ViewState, degreeFill: string) {
  const aggregate = isAggregateNode(node)
  const missingMarketCap = !hasMarketCap(node)
  const matched = view.matchedNodeIds.has(node.id)
  const fill = node.is_center ? CENTER_NODE_FILL : degreeFill
  const stroke = STATUS_RING[node.quote_status || 'pending'] || '#64748b'
  const hasFocus = Boolean(view.focusNodeId || view.selectedNodeId || view.hoveredNodeId || view.selectedEdgeKey || view.hoveredEdgeKey)
  const active = matched || view.normalNodeIds.has(node.id) || node.is_center || node.id === view.selectedNodeId || node.id === view.hoveredNodeId || node.id === view.focusNodeId || view.relatedNodeIds.has(node.id)
  const baseOpacity = missingMarketCap && !node.is_center ? 0.42 : node.quote_status === 'failed' || node.quote_status === 'error' ? 0.68 : 0.95
  return {
    size: matched && !node.is_center ? nodeSize(node) + 6 : nodeSize(node),
    fill: aggregate ? 'rgba(96, 165, 250, 0.16)' : fill,
    stroke: aggregate ? '#60a5fa' : matched ? '#f8fafc' : stroke,
    lineWidth: aggregate ? 2.5 : matched ? node.is_center ? 5 : 4 : active ? node.is_center ? 4 : 3 : 1.5,
    lineDash: aggregate ? [6, 4] : undefined,
    opacity: hasFocus && !active ? 0.16 : baseOpacity,
    labelText: nodeLabel(node, view),
    labelFill: aggregate ? '#dbeafe' : matched ? '#f8fafc' : node.is_center ? '#f8fafc' : '#cbd5e1',
    labelFontSize: matched ? node.is_center ? 14 : 11 : node.is_center ? 13 : 10,
    labelFontWeight: matched || node.is_center ? 800 : 500,
    labelPlacement: 'bottom',
    labelBackground: true,
    labelBackgroundFill: matched ? 'rgba(251, 191, 36, 0.2)' : 'rgba(15, 23, 42, 0.78)',
    labelBackgroundRadius: 4,
    labelPadding: [2, 4, 2, 4],
    labelOpacity: hasFocus && !active ? 0.18 : node.is_center ? 1 : 0.82,
    halo: node.is_center || matched,
    haloStroke: matched ? '#f8fafc' : '#fbbf24',
    haloLineWidth: matched ? 12 : 8,
    haloStrokeOpacity: matched ? 0.28 : 0.18,
  }
}

function edgeLabel(edge: TopologyRenderEdgeData) {
  const text = edge.label || edge.evidence || edge.relation || ''
  return shortText(text, 22)
}

function distToMaxLen(dist: number): number {
  if (dist < 120) return 6
  if (dist < 200) return 12
  if (dist < 350) return 22
  if (dist < 550) return 40
  return 0  // no truncation — show full text
}

function adaptiveEdgeLabel(edge: TopologyRenderEdgeData, dist: number): string {
  const fullText = edge.label || edge.evidence || edge.relation || ''
  const maxLen = distToMaxLen(dist)
  if (maxLen === 0) return fullText
  return shortText(fullText, maxLen)
}

function edgeKey(edge: Pick<TopologyRenderEdgeData, 'source' | 'target' | 'relation'>) {
  return `${edge.source}->${edge.target}:${edge.relation}`
}

function visualEdge(edge: TopologyRenderEdgeData) {
  if (edge.direction === 'upstream') {
    return { ...edge, source: edge.target, target: edge.source }
  }
  return edge
}

function edgeStroke(edge: TopologyRenderEdgeData, centerId: string | undefined) {
  if (centerId && edge.source === centerId) return CENTER_OUT_EDGE
  if (centerId && edge.target === centerId) return CENTER_IN_EDGE
  return EDGE_COLOR[edge.direction] || DEFAULT_EDGE
}

function edgeStyle(edge: TopologyRenderEdgeData, centerId: string | undefined, view: ViewState, key: string) {
  const aggregate = isAggregateEdge(edge)
  const stroke = edgeStroke(edge, centerId)
  const touchesCenter = Boolean(centerId && (edge.source === centerId || edge.target === centerId))
  const active = key === view.selectedEdgeKey || key === view.hoveredEdgeKey || view.relatedEdgeKeys.has(key)
  const normal = view.normalEdgeKeys.has(key)
  const cyclic = view.highlightCycles && view.cycleEdgeKeys.has(key)
  const hasFocus = Boolean(view.focusNodeId || view.selectedNodeId || view.hoveredNodeId || view.selectedEdgeKey || view.hoveredEdgeKey)
  const isPinned = view.pinnedEdgeKeys.has(key)
  const showLabel = active || (view.zoom > 1.35 && touchesCenter)
  return {
    stroke: cyclic ? '#f8fafc' : stroke,
    lineWidth: aggregate ? 2 : cyclic ? 3 : (active || isPinned) ? 2.5 : normal ? 1.4 : 1,
    strokeOpacity: aggregate ? 0.55 : hasFocus && !active && !cyclic && !isPinned && !normal ? 0.03 : (active || cyclic || isPinned) ? 0.9 : normal ? 0.32 : 0.15,
    lineDash: aggregate ? [8, 5] : cyclic ? [6, 4] : undefined,
    endArrow: false,
    shadowBlur: cyclic ? 16 : (active || isPinned) ? 10 : 0,
    shadowColor: cyclic ? '#fbbf24' : (active || isPinned) ? stroke : 'transparent',
    shadowOffsetX: 0,
    shadowOffsetY: 0,
    labelText: aggregate ? `${edge.direction} ${isAggregateEdge(edge) ? edge.relation_count : ''}` : showLabel ? (cyclic ? `环路 · ${edgeLabel(edge)}` : edgeLabel(edge)) : '',
    labelFill: '#dbeafe',
    labelFontSize: 9,
    labelBackground: true,
    labelBackgroundFill: 'rgba(15, 23, 42, 0.78)',
    labelBackgroundRadius: 4,
    labelPadding: [1, 4, 1, 4],
  }
}

// ─── Phase 1: Ring-Sectored Deterministic Layout ───
// Replaces initialPosition() + d3-force with a deterministic placement where
// position encodes zone (upstream=top, downstream=bottom, peers=sides).
// No force simulation; positions are stable, predictable, and zone-loyal.

const R_BASE = 240
const MIN_RING_GAP = 132
const NODE_ARC_GAP = 24
const SECTOR_ANGLE_GAP = Math.PI * 0.028
const SECTOR_PADDING_RADIUS = 42
const HUB_BUFFER_BASE = 190
const HUB_BUFFER_FACTOR = 18
const PEER_SPLIT_WEIGHT_BALANCE = 0.92
const POLAR_COLLISION_PASSES = 2

type LayoutDirection = 'upstream' | 'downstream' | 'peerLeft' | 'peerRight'

interface Sector {
  start: number  // radians
  end: number    // radians
}

interface AngularInterval {
  start: number
  end: number
  center: number
}

interface RadialInterval {
  inner: number
  outer: number
}

interface SectorCollisionBox {
  minAngle: number
  maxAngle: number
  minRadius: number
  maxRadius: number
}

interface LayoutNode {
  node: TopologyRenderNodeData
  id: string
  sector: string
  baseDirection: 'upstream' | 'downstream' | 'peer'
  direction: LayoutDirection
  depth: number
  degree: number
  marketCap: number
  labelWidth: number
  nodeRadius: number
}

interface RingInfo {
  depth: number
  nodes: LayoutNode[]
  requiredRadius: number
  actualRadius: number
}

interface SectorInfo {
  sector: string
  direction: LayoutDirection
  weight: number
  nodeCount: number
  edgeCount: number
  maxRingLoad: number
  avgLabelWidth: number
  rings: RingInfo[]
  angle: AngularInterval
  radius: RadialInterval
  collisionBox: SectorCollisionBox
}

interface DirectionGroup {
  direction: LayoutDirection
  angularDomain: AngularInterval
  sectors: SectorInfo[]
}

const DIRECTION_DOMAINS: Record<LayoutDirection, AngularInterval> = {
  upstream: makeInterval(Math.PI * 0.14, Math.PI * 0.86),
  downstream: makeInterval(Math.PI * 1.14, Math.PI * 1.86),
  peerLeft: makeInterval(Math.PI * 0.86, Math.PI * 1.26),
  peerRight: makeInterval(-Math.PI * 0.26, Math.PI * 0.14),
}

function makeInterval(start: number, end: number): AngularInterval {
  return { start, end, center: (start + end) / 2 }
}

function angleSize(interval: AngularInterval) {
  return Math.max(0.001, interval.end - interval.start)
}

function sectorKeyForLayout(node: TopologyRenderNodeData) {
  return isAggregateNode(node) ? node.aggregate_sector : displaySector(node)
}

function stableNodeCompare(a: LayoutNode, b: LayoutNode) {
  const degreeDiff = b.degree - a.degree
  if (degreeDiff !== 0) return degreeDiff
  const capDiff = b.marketCap - a.marketCap
  if (capDiff !== 0) return capDiff
  return (a.node.name || a.id).localeCompare(b.node.name || b.id) || a.id.localeCompare(b.id)
}

function estimateLabelWidth(node: TopologyRenderNodeData) {
  const text = node.name || node.code || node.id
  return Math.min(132, Math.max(36, text.length * 7.2))
}

function buildLayoutNodes(nodes: TopologyRenderNodeData[], edges: TopologyRenderEdgeData[]): LayoutNode[] {
  const degreeMap = buildNodeDegreeMap(nodes, edges)
  return nodes
    .filter((node) => !node.is_center)
    .map((node): LayoutNode => {
      const baseDirection = node.zone === 'upstream' || node.zone === 'downstream' ? node.zone : 'peer'
      return {
        node,
        id: node.id,
        sector: sectorKeyForLayout(node),
        baseDirection,
        direction: baseDirection === 'peer' ? 'peerLeft' : baseDirection,
        depth: Math.max(1, node.depth || 1),
        degree: degreeMap.get(node.id)?.total || 0,
        marketCap: node.market_cap || 0,
        labelWidth: estimateLabelWidth(node),
        nodeRadius: nodeSize(node),
      }
    })
}

function groupLayoutNodesBySector(nodes: LayoutNode[], direction: LayoutDirection, edges: TopologyRenderEdgeData[]): SectorInfo[] {
  const groups = new Map<string, LayoutNode[]>()
  nodes.forEach((node) => {
    const key = node.sector
    const group = groups.get(key) || []
    group.push(node)
    groups.set(key, group)
  })
  return [...groups.entries()]
    .map(([sector, sectorNodes]) => buildSectorInfo(sector, direction, sectorNodes, edges))
    .sort(stableSectorCompare)
}

function stableSectorCompare(a: SectorInfo, b: SectorInfo) {
  const weightDiff = b.weight - a.weight
  if (Math.abs(weightDiff) > 0.0001) return weightDiff
  const countDiff = b.nodeCount - a.nodeCount
  if (countDiff !== 0) return countDiff
  return a.sector.localeCompare(b.sector)
}

function buildSectorInfo(sector: string, direction: LayoutDirection, nodes: LayoutNode[], edges: TopologyRenderEdgeData[]): SectorInfo {
  const nodeIds = new Set(nodes.map((node) => node.id))
  const edgeCount = edges.filter((edge) => nodeIds.has(edge.source) || nodeIds.has(edge.target)).length
  const rings = buildRings(nodes)
  const maxRingLoad = Math.max(1, ...rings.map((ring) => ring.nodes.length))
  const avgLabelWidth = nodes.reduce((sum, node) => sum + node.labelWidth, 0) / Math.max(1, nodes.length)
  const weight = computeSectorWeight(nodes.length, edgeCount, maxRingLoad, avgLabelWidth)
  return {
    sector,
    direction,
    weight,
    nodeCount: nodes.length,
    edgeCount,
    maxRingLoad,
    avgLabelWidth,
    rings,
    angle: makeInterval(0, 0),
    radius: { inner: R_BASE, outer: R_BASE },
    collisionBox: { minAngle: 0, maxAngle: 0, minRadius: R_BASE, maxRadius: R_BASE },
  }
}

function buildRings(nodes: LayoutNode[]): RingInfo[] {
  const byDepth = new Map<number, LayoutNode[]>()
  nodes.forEach((node) => {
    const group = byDepth.get(node.depth) || []
    group.push(node)
    byDepth.set(node.depth, group)
  })
  return [...byDepth.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([depth, ringNodes]) => ({
      depth,
      nodes: [...ringNodes].sort(stableNodeCompare),
      requiredRadius: R_BASE,
      actualRadius: R_BASE,
    }))
}

function computeSectorWeight(nodeCount: number, edgeCount: number, maxRingLoad: number, avgLabelWidth: number) {
  return Math.sqrt(nodeCount) + Math.log2(edgeCount + 1) * 0.6 + maxRingLoad * 0.35 + avgLabelWidth * 0.01
}

function splitDirectionGroups(layoutNodes: LayoutNode[], edges: TopologyRenderEdgeData[]): DirectionGroup[] {
  const upstreamNodes = layoutNodes.filter((node) => node.baseDirection === 'upstream')
  const downstreamNodes = layoutNodes.filter((node) => node.baseDirection === 'downstream')
  const peerSectors = groupLayoutNodesBySector(layoutNodes.filter((node) => node.baseDirection === 'peer'), 'peerLeft', edges)
  const peerLeft: SectorInfo[] = []
  const peerRight: SectorInfo[] = []
  let leftWeight = 0
  let rightWeight = 0

  peerSectors.forEach((sector, index) => {
    const targetLeft = leftWeight <= rightWeight * PEER_SPLIT_WEIGHT_BALANCE || (Math.abs(leftWeight - rightWeight) < 0.0001 && index % 2 === 0)
    const target = targetLeft ? peerLeft : peerRight
    sector.direction = targetLeft ? 'peerLeft' : 'peerRight'
    sector.rings.forEach((ring) => ring.nodes.forEach((node) => { node.direction = sector.direction }))
    target.push(sector)
    if (targetLeft) leftWeight += sector.weight
    else rightWeight += sector.weight
  })

  const groups: DirectionGroup[] = [
    { direction: 'upstream', angularDomain: DIRECTION_DOMAINS.upstream, sectors: groupLayoutNodesBySector(upstreamNodes, 'upstream', edges) },
    { direction: 'peerLeft', angularDomain: DIRECTION_DOMAINS.peerLeft, sectors: peerLeft.sort(stableSectorCompare) },
    { direction: 'peerRight', angularDomain: DIRECTION_DOMAINS.peerRight, sectors: peerRight.sort(stableSectorCompare) },
    { direction: 'downstream', angularDomain: DIRECTION_DOMAINS.downstream, sectors: groupLayoutNodesBySector(downstreamNodes, 'downstream', edges) },
  ]
  return groups.filter((group) => group.sectors.length > 0)
}

function ringSectoredLayout(
  nodes: TopologyRenderNodeData[],
  edges: TopologyRenderEdgeData[],
  existingPositions?: Map<string, { x: number; y: number }>,
  preservedNodeIds?: Set<string>,
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>()

  if (nodes.length === 1) {
    positions.set(nodes[0].id, { x: 0, y: 0 })
    return positions
  }

  // 1. Preserve existing positions (expand: keep stable layout)
  if (existingPositions) {
    for (const node of nodes) {
      if (!preservedNodeIds?.has(node.id)) continue
      const existing = existingPositions.get(node.id)
      if (existing) positions.set(node.id, { ...existing })
    }
  }

  // 2. Center node → fixed at origin
  const center = nodes.find((node) => node.is_center)
  if (center) positions.set(center.id, { x: 0, y: 0 })

  const layoutNodes = buildLayoutNodes(nodes, edges).filter((node) => !positions.has(node.id))
  const hubBuffer = computeHubBuffer(center?.id, edges)
  const directionGroups = splitDirectionGroups(layoutNodes, edges)
  directionGroups.forEach((group) => {
    allocateSectorAngles(group)
    group.sectors.forEach((sector) => computeRingRadii(sector, hubBuffer))
    resolveSectorPolarCollisions(group)
  })
  placeNodesInPolarSpace(directionGroups, edges, positions)

  placeAggregateRepresentatives(nodes, positions)

  return positions
}

function placeAggregateRepresentatives(
  nodes: TopologyRenderNodeData[],
  positions: Map<string, { x: number; y: number }>,
) {
  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const aggregateNodes = nodes.filter(isAggregateNode)
  for (const aggregate of aggregateNodes) {
    const anchor = positions.get(aggregate.id)
    if (!anchor) continue
    const representativeIds = aggregate.visible_representative_ids
      .filter((id) => {
        const node = nodeById.get(id)
        return Boolean(node && !node.is_center && positions.has(id))
      })
      .slice(0, 3)
    if (representativeIds.length === 0) continue

    const baseAngle = aggregate.zone === 'upstream'
      ? Math.PI / 2
      : aggregate.zone === 'downstream'
        ? -Math.PI / 2
        : anchor.x < 0 ? Math.PI : 0
    const offsets = representativeIds.length === 1 ? [0] : representativeIds.length === 2 ? [-0.42, 0.42] : [-0.56, 0, 0.56]
    const radius = 72

    representativeIds.forEach((id, index) => {
      const angle = baseAngle + offsets[index]
      positions.set(id, {
        x: anchor.x + Math.cos(angle) * radius,
        y: anchor.y - Math.sin(angle) * radius,
      })
    })
  }
}

function computeHubBuffer(centerId: string | undefined, edges: TopologyRenderEdgeData[]) {
  if (!centerId) return R_BASE
  const hubDegree = edges.filter((edge) => edge.source === centerId || edge.target === centerId).length
  return Math.max(R_BASE, HUB_BUFFER_BASE + Math.sqrt(hubDegree) * HUB_BUFFER_FACTOR)
}

function allocateSectorAngles(group: DirectionGroup) {
  const sectors = group.sectors.sort(stableSectorCompare)
  const domainSize = angleSize(group.angularDomain)
  const gap = Math.min(SECTOR_ANGLE_GAP, domainSize / Math.max(8, sectors.length * 3))
  const available = Math.max(domainSize * 0.72, domainSize - gap * Math.max(0, sectors.length - 1))
  const minAngle = Math.min(Math.PI * 0.075, Math.max(Math.PI * 0.035, available * 0.06))
  const compressedMinAngle = Math.min(minAngle, available / Math.max(1, sectors.length) * 0.82)
  const base = compressedMinAngle * sectors.length
  const remaining = Math.max(0, available - base)
  const totalWeight = sectors.reduce((sum, sector) => sum + sector.weight, 0) || 1
  let cursor = group.angularDomain.center - available / 2

  sectors.forEach((sector) => {
    const size = compressedMinAngle + remaining * (sector.weight / totalWeight)
    sector.angle = makeInterval(cursor, cursor + size)
    cursor += size + gap
  })
}

function computeRingRadii(sector: SectorInfo, hubBuffer: number) {
  const sectorAngle = angleSize(sector.angle)
  let previousRadius = hubBuffer
  sector.rings.forEach((ring, index) => {
    const nodeArc = ring.nodes.reduce((sum, node) => sum + node.nodeRadius * 2, 0)
    const labelReserve = ring.nodes.reduce((sum, node) => sum + node.labelWidth, 0) / Math.max(1, ring.nodes.length) * 0.48
    const gapArc = Math.max(0, ring.nodes.length - 1) * NODE_ARC_GAP
    ring.requiredRadius = (nodeArc + labelReserve + gapArc) / Math.max(sectorAngle, Math.PI * 0.04)
    ring.actualRadius = Math.max(hubBuffer + (ring.depth - 1) * MIN_RING_GAP, previousRadius + (index === 0 ? 0 : MIN_RING_GAP), ring.requiredRadius)
    previousRadius = ring.actualRadius
  })

  const radii = sector.rings.map((ring) => ring.actualRadius)
  const inner = Math.max(80, Math.min(...radii, hubBuffer) - SECTOR_PADDING_RADIUS)
  const outer = Math.max(...radii, hubBuffer) + SECTOR_PADDING_RADIUS
  sector.radius = { inner, outer }
  sector.collisionBox = computeSectorCollisionBox(sector)
}

function computeSectorCollisionBox(sector: SectorInfo): SectorCollisionBox {
  return {
    minAngle: sector.angle.start - Math.PI * 0.012,
    maxAngle: sector.angle.end + Math.PI * 0.012,
    minRadius: sector.radius.inner - SECTOR_PADDING_RADIUS,
    maxRadius: sector.radius.outer + SECTOR_PADDING_RADIUS,
  }
}

function polarBoxesOverlap(a: SectorCollisionBox, b: SectorCollisionBox) {
  const angleOverlap = Math.max(a.minAngle, b.minAngle) < Math.min(a.maxAngle, b.maxAngle)
  const radiusOverlap = Math.max(a.minRadius, b.minRadius) < Math.min(a.maxRadius, b.maxRadius)
  return angleOverlap && radiusOverlap
}

function resolveSectorPolarCollisions(group: DirectionGroup) {
  const sectors = group.sectors.sort((a, b) => a.angle.center - b.angle.center || stableSectorCompare(a, b))
  for (let pass = 0; pass < POLAR_COLLISION_PASSES; pass += 1) {
    for (let index = 1; index < sectors.length; index += 1) {
      const prev = sectors[index - 1]
      const current = sectors[index]
      if (!polarBoxesOverlap(prev.collisionBox, current.collisionBox)) continue
      const shift = Math.min(MIN_RING_GAP * 1.5, Math.max(0, prev.collisionBox.maxRadius - current.collisionBox.minRadius) + SECTOR_PADDING_RADIUS)
      current.rings.forEach((ring) => {
        ring.actualRadius += shift
      })
      current.radius = { inner: current.radius.inner + shift, outer: current.radius.outer + shift }
      current.collisionBox = computeSectorCollisionBox(current)
    }
  }
}

function placeNodesInPolarSpace(
  groups: DirectionGroup[],
  edges: TopologyRenderEdgeData[],
  positions: Map<string, { x: number; y: number }>,
) {
  const placedAngles = new Map<string, number>()
  groups.forEach((group) => {
    group.sectors
      .sort((a, b) => a.angle.center - b.angle.center || stableSectorCompare(a, b))
      .forEach((sector) => {
        sector.rings.forEach((ring) => {
          const ordered = orderRingNodesByBarycenter(ring, edges, placedAngles, sector.angle.center)
          const angles = distributeAnglesEvenly(ordered.length, sector.angle)
          ordered.forEach((layoutNode, index) => {
            const angle = angles[index] ?? sector.angle.center
            positions.set(layoutNode.id, polarToCartesian(angle, ring.actualRadius))
            placedAngles.set(layoutNode.id, angle)
          })
        })
      })
  })
}

function orderRingNodesByBarycenter(
  ring: RingInfo,
  edges: TopologyRenderEdgeData[],
  placedAngles: Map<string, number>,
  fallbackAngle: number,
) {
  const scored = ring.nodes.map((node) => {
    const neighborAngles: number[] = []
    edges.forEach((edge) => {
      const neighborId = edge.source === node.id ? edge.target : edge.target === node.id ? edge.source : null
      if (!neighborId) return
      const angle = placedAngles.get(neighborId)
      if (angle !== undefined) neighborAngles.push(angle)
    })
    const barycenter = neighborAngles.length > 0
      ? neighborAngles.reduce((sum, angle) => sum + angle, 0) / neighborAngles.length
      : fallbackAngle
    return { node, barycenter }
  })
  return scored
    .sort((a, b) => a.barycenter - b.barycenter || stableNodeCompare(a.node, b.node))
    .map((item) => item.node)
}

function distributeAnglesEvenly(count: number, interval: AngularInterval) {
  if (count <= 0) return []
  if (count === 1) return [interval.center]
  const size = angleSize(interval)
  const inset = Math.min(size * 0.18, Math.PI * 0.045)
  const start = interval.start + inset
  const end = interval.end - inset
  const usable = Math.max(0.001, end - start)
  return Array.from({ length: count }, (_, index) => start + usable * (index / Math.max(1, count - 1)))
}

function polarToCartesian(angle: number, radius: number) {
  return {
    x: Math.cos(angle) * radius,
    y: -Math.sin(angle) * radius,
  }
}

function detectCycleEdges(edges: TopologyRenderEdgeData[]) {
  const adjacency = new Map<string, string[]>()
  for (const edge of edges) {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, [])
    if (!adjacency.has(edge.target)) adjacency.set(edge.target, [])
    adjacency.get(edge.source)!.push(edge.target)
  }

  const indexByNode = new Map<string, number>()
  const lowlink = new Map<string, number>()
  const stack: string[] = []
  const onStack = new Set<string>()
  const cyclicNodes = new Set<string>()
  let index = 0

  const visit = (node: string) => {
    indexByNode.set(node, index)
    lowlink.set(node, index)
    index += 1
    stack.push(node)
    onStack.add(node)

    for (const next of adjacency.get(node) || []) {
      if (!indexByNode.has(next)) {
        visit(next)
        lowlink.set(node, Math.min(lowlink.get(node)!, lowlink.get(next)!))
      } else if (onStack.has(next)) {
        lowlink.set(node, Math.min(lowlink.get(node)!, indexByNode.get(next)!))
      }
    }

    if (lowlink.get(node) !== indexByNode.get(node)) return
    const component: string[] = []
    let cur: string | undefined
    do {
      cur = stack.pop()
      if (!cur) break
      onStack.delete(cur)
      component.push(cur)
    } while (cur !== node)

    if (component.length > 1) component.forEach((item) => cyclicNodes.add(item))
  }

  for (const node of adjacency.keys()) {
    if (!indexByNode.has(node)) visit(node)
  }

  return new Set(edges
    .filter((edge) => edge.source === edge.target || (cyclicNodes.has(edge.source) && cyclicNodes.has(edge.target)))
    .map(edgeKey))
}

function toG6Data(
  nodes: TopologyRenderNodeData[],
  edges: TopologyRenderEdgeData[],
  view: ViewState,
  positions: Map<string, { x: number; y: number }>,
  nodeColorMetric: TopologyNodeColorMetric,
) {
  const centerId = nodes.find((node) => node.is_center)?.id
  const nodeFillMap = buildNodeFillMap(nodes, edges, nodeColorMetric)

  return {
    nodes: nodes.map((node) => {
      const pos = positions.get(node.id) || { x: 0, y: 0 }
      return {
        id: node.id,
        data: node as unknown as Record<string, unknown>,
        style: {
          x: pos.x,
          y: pos.y,
          ...nodeStyle(node, view, nodeFillMap.get(node.id) || CENTER_NODE_FILL),
        },
      }
    }),
    edges: edges.map((edge) => {
      const rendered = visualEdge(edge)
      const key = edgeKey(edge)
      const stroke = edgeStroke(rendered, centerId)
      return {
        id: `${rendered.source}->${rendered.target}:${edge.relation}`,
        source: rendered.source,
        target: rendered.target,
        data: { ...edge, edgeKey: key, visualSource: rendered.source, visualTarget: rendered.target, stroke } as unknown as Record<string, unknown>,
        style: edgeStyle(rendered, centerId, view, key),
      }
    }),
  }
}

function quoteTooltip(node: TopologyRenderNodeData, edge?: TopologyRenderEdgeData) {
  if (isAggregateNode(node)) {
    return `
      <div class="topo-g6-tip topo-g6-tip--aggregate">
        <div class="topo-g6-tip-title">${node.aggregate_sector}</div>
        <div class="topo-g6-tip-row"><span>折叠公司</span><strong>${node.hidden_node_ids.length}</strong></div>
        <div class="topo-g6-tip-row"><span>上游</span><strong>${node.relation_counts.upstream || 0}</strong></div>
        <div class="topo-g6-tip-row"><span>下游</span><strong>${node.relation_counts.downstream || 0}</strong></div>
        <div class="topo-g6-tip-row"><span>同业</span><strong>${node.relation_counts.peer || 0}</strong></div>
        <div class="topo-g6-tip-hint">双击展开板块</div>
      </div>
    `
  }
  const pct = node.pct_chg === null || node.pct_chg === undefined ? '--' : `${node.pct_chg > 0 ? '+' : ''}${node.pct_chg}%`
  const sector = node.sector && node.sector !== '--' ? node.sector : '板块未知'
  const relation = edge ? `<div class="topo-g6-tip-row"><span>关系</span><strong>${edge.label}</strong></div>` : ''
  const evidence = edge?.evidence ? `<div class="topo-g6-tip-evidence">${edge.evidence}</div>` : ''
  return `
    <div class="topo-g6-tip">
      <div class="topo-g6-tip-title">${node.name || node.code}</div>
      <div class="topo-g6-tip-row"><span>代码</span><strong>${node.id}</strong></div>
      <div class="topo-g6-tip-row"><span>市场</span><strong>${node.market}</strong></div>
      <div class="topo-g6-tip-row"><span>板块</span><strong>${sector}</strong></div>
      <div class="topo-g6-tip-row"><span>涨跌幅</span><strong>${pct}</strong></div>
      <div class="topo-g6-tip-row"><span>市值</span><strong>${node.market_cap_str || '--'}</strong></div>
      <div class="topo-g6-tip-row"><span>行情</span><strong>${node.quote_status || 'pending'}</strong></div>
      ${relation}${evidence}
      <div class="topo-g6-tip-hint">双击节点展开产业关系</div>
    </div>
  `
}

function edgeTooltip(edge: RenderedEdgeData, nodes: TopologyRenderNodeData[]) {
  if (isAggregateEdge(edge)) {
    const examples = edge.source_edges.slice(0, 5).map((item) => item.label || item.evidence || item.relation).filter(Boolean).join('；')
    return `
      <div class="topo-g6-tip topo-g6-tip--aggregate">
        <div class="topo-g6-tip-title">${edge.aggregate_sector} · 聚合关系</div>
        <div class="topo-g6-tip-row"><span>方向</span><strong>${edge.direction}</strong></div>
        <div class="topo-g6-tip-row"><span>关系数</span><strong>${edge.relation_count}</strong></div>
        <div class="topo-g6-tip-evidence">${examples || '展开板块查看完整关系证据。'}</div>
      </div>
    `
  }
  const sourceNode = nodes.find((node) => node.id === edge.source)
  const targetNode = nodes.find((node) => node.id === edge.target)
  const sourceName = sourceNode ? `${sourceNode.name || sourceNode.code}（${sourceNode.id}）` : edge.source
  const targetName = targetNode ? `${targetNode.name || targetNode.code}（${targetNode.id}）` : edge.target
  const evidence = edge.evidence ? `<div class="topo-g6-tip-evidence">${edge.evidence}</div>` : ''
  return `
    <div class="topo-g6-tip topo-g6-tip--edge">
      <div class="topo-g6-tip-title">${edge.label || edge.relation || '产业关系'}</div>
      <div class="topo-g6-tip-row"><span>来源</span><strong>${sourceName}</strong></div>
      <div class="topo-g6-tip-row"><span>目标</span><strong>${targetName}</strong></div>
      <div class="topo-g6-tip-row"><span>方向</span><strong>${edge.direction}</strong></div>
      <div class="topo-g6-tip-row"><span>关系</span><strong>${edge.relation}</strong></div>
      ${evidence}
    </div>
  `
}

function relatedSets(nodes: TopologyRenderNodeData[], edges: TopologyRenderEdgeData[], anchorId?: string | null, edgeAnchor?: string | null) {
  const relatedNodeIds = new Set<string>()
  const relatedEdgeKeys = new Set<string>()
  if (anchorId) relatedNodeIds.add(anchorId)
  edges.forEach((edge) => {
    const key = edgeKey(edge)
    if (edgeAnchor && key === edgeAnchor) {
      relatedEdgeKeys.add(key)
      relatedNodeIds.add(edge.source)
      relatedNodeIds.add(edge.target)
    }
    if (anchorId && (edge.source === anchorId || edge.target === anchorId)) {
      relatedEdgeKeys.add(key)
      relatedNodeIds.add(edge.source)
      relatedNodeIds.add(edge.target)
    }
  })
  nodes.filter((node) => node.is_center).forEach((node) => relatedNodeIds.add(node.id))
  return { relatedNodeIds, relatedEdgeKeys }
}

function topologyToolbarItems(state: ToolbarState) {
  return [
    {
      id: 'edit',
      value: `start-topology ${state.canStartTopology ? 'is-enabled' : 'is-disabled'} ${state.isTopologyBusy ? 'is-busy' : ''}`,
      title: state.isTopologyBusy ? '生成中...' : state.startTopologyLabel,
    },
    {
      id: 'reset',
      value: `refresh-topology ${state.canRefreshTopology ? 'is-enabled' : 'is-disabled'}`,
      title: '刷新关系',
    },
  ]
}

function toolbarActionFromValue(value: string): ToolbarAction | null {
  if (value.startsWith('start-topology')) return 'start-topology'
  if (value.startsWith('refresh-topology')) return 'refresh-topology'
  return null
}

function fitGraphView(graph: G6Graph, nodeCount: number) {
  if (graph.destroyed) return
  const g = graph as unknown as { fitView?: (options?: unknown) => unknown; fitCenter?: () => unknown }
  try {
    if (nodeCount <= 1) {
      if (typeof g.fitCenter === 'function') void g.fitCenter()
      return
    }
    if (typeof g.fitView === 'function') {
      void g.fitView({ padding: 80 })
      return
    }
    if (typeof g.fitCenter === 'function') {
      void g.fitCenter()
    }
  } catch {
    /* viewport fitting is best-effort */
  }
}

interface ParticleState {
  circle: Circle
  raf: number
}

function destroyParticle(graph: G6Graph, map: Map<string, ParticleState>, key?: string) {
  if (key !== undefined) {
    const p = map.get(key)
    if (!p) return
    cancelAnimationFrame(p.raf)
    if (!graph.destroyed) {
      try {
        p.circle.remove()
      } catch {
        /* canvas already destroyed */
      }
    }
    map.delete(key)
  } else {
    for (const p of map.values()) {
      cancelAnimationFrame(p.raf)
      if (!graph.destroyed) {
        try {
          p.circle.remove()
        } catch {
          /* canvas already destroyed */
        }
      }
    }
    map.clear()
  }
}

function startParticle(
  graph: G6Graph,
  map: Map<string, ParticleState>,
  key: string,
  edge: { visualSource?: string; visualTarget?: string; stroke: string },
) {
  destroyParticle(graph, map, key)
  if (graph.destroyed) return
  const source = edge.visualSource
  const target = edge.visualTarget
  if (!source || !target) return
  const circle = new Circle({ style: { r: 3, fill: edge.stroke, opacity: 0.95 } })
  try {
    graph.getCanvas().appendChild(circle)
  } catch {
    return
  }
  const duration = 2500
  const startTs = performance.now()
  const particle: ParticleState = { circle, raf: 0 }
  map.set(key, particle)
  const tick = () => {
    if (graph.destroyed || map.get(key) !== particle) return
    let p1: [number, number] | null = null
    let p2: [number, number] | null = null
    try {
      p1 = graph.getElementPosition(source) as [number, number]
      p2 = graph.getElementPosition(target) as [number, number]
    } catch {
      destroyParticle(graph, map, key)
      return
    }
    if (!p1 || !p2) {
      destroyParticle(graph, map, key)
      return
    }
    const t = ((performance.now() - startTs) % duration) / duration
    circle.style.cx = p1[0] + (p2[0] - p1[0]) * t
    circle.style.cy = p1[1] + (p2[1] - p1[1]) * t
    particle.raf = requestAnimationFrame(tick)
  }
  particle.raf = requestAnimationFrame(tick)
}

export const TopologyCanvas = forwardRef<TopologyCanvasHandle, Props>(function TopologyCanvas({
  rawNodes,
  rawEdges,
  onExpand,
  onExpandAggregate,
  onCollapseAggregate,
  onStartTopology,
  onRefreshTopology,
  canStartTopology,
  canRefreshTopology,
  isTopologyBusy,
  startTopologyLabel = '开始拓扑',
  selectedNodeId,
  selectedEdgeKey,
  focusNodeId,
  matchedNodeIds = [],
  normalNodeIds = [],
  normalEdgeKeys = [],
  flowEdgeKeys = [],
  highlightCycles = false,
  nodeColorMetric,
  onSelectNode,
  onSelectEdge,
}, ref) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const graphRef = useRef<G6Graph | null>(null)
  const initializedRef = useRef(false)
  const previousNodeIdsRef = useRef<Set<string>>(new Set())
  const previousEdgeIdsRef = useRef<Set<string>>(new Set())
  const positionsRef = useRef<Map<string, { x: number; y: number }>>(new Map())
  const manuallyPositionedNodeIdsRef = useRef<Set<string>>(new Set())
  const centerIdRef = useRef<string | null>(null)
  const particleMapRef = useRef<Map<string, ParticleState>>(new Map())
  const nodeParticleKeysRef = useRef<Set<string>>(new Set())
  const flowParticleKeysRef = useRef<Set<string>>(new Set())
  const tooltipPatchedRef = useRef(false)
  const onExpandRef = useRef(onExpand)
  const onExpandAggregateRef = useRef<Props['onExpandAggregate']>(onExpandAggregate)
  const onCollapseAggregateRef = useRef<Props['onCollapseAggregate']>(onCollapseAggregate)
  const onStartTopologyRef = useRef(onStartTopology)
  const onRefreshTopologyRef = useRef(onRefreshTopology)
  const toolbarStateRef = useRef<ToolbarState>({ canStartTopology, canRefreshTopology, isTopologyBusy, startTopologyLabel })
  const rawNodesRef = useRef(rawNodes)
  const rawEdgesRef = useRef(rawEdges)
  const onSelectNodeRef = useRef(onSelectNode)
  const onSelectEdgeRef = useRef(onSelectEdge)
  const [zoom, setZoom] = useState(1)
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null)
  const [hoveredEdgeKey, setHoveredEdgeKey] = useState<string | null>(null)
  const [pinnedEdgeKeys, setPinnedEdgeKeys] = useState<Set<string>>(new Set())
  const pinnedEdgeKeysRef = useRef<Set<string>>(new Set())
  onExpandRef.current = onExpand
  onExpandAggregateRef.current = onExpandAggregate
  onCollapseAggregateRef.current = onCollapseAggregate
  onStartTopologyRef.current = onStartTopology
  onRefreshTopologyRef.current = onRefreshTopology
  toolbarStateRef.current = { canStartTopology, canRefreshTopology, isTopologyBusy, startTopologyLabel }
  rawNodesRef.current = rawNodes
  rawEdgesRef.current = rawEdges
  onSelectNodeRef.current = onSelectNode
  onSelectEdgeRef.current = onSelectEdge
  pinnedEdgeKeysRef.current = pinnedEdgeKeys

  const view = useMemo<ViewState>(() => {
    const activeNodeId = focusNodeId || selectedNodeId || hoveredNodeId
    const activeEdgeKey = selectedEdgeKey || hoveredEdgeKey
    const related = relatedSets(rawNodes, rawEdges, activeNodeId, activeEdgeKey)
    const matchSet = matchedNodeIds instanceof Set ? matchedNodeIds : new Set(matchedNodeIds)
    const normalSet = normalNodeIds instanceof Set ? normalNodeIds : new Set(normalNodeIds)
    const normalEdgeSet = normalEdgeKeys instanceof Set ? normalEdgeKeys : new Set(normalEdgeKeys)
    return {
      zoom,
      hoveredNodeId,
      hoveredEdgeKey,
      selectedNodeId,
      selectedEdgeKey,
      focusNodeId,
      matchedNodeIds: matchSet,
      normalNodeIds: normalSet,
      normalEdgeKeys: normalEdgeSet,
      highlightCycles,
      pinnedEdgeKeys,
      cycleEdgeKeys: detectCycleEdges(rawEdges),
      ...related,
    }
  }, [focusNodeId, highlightCycles, hoveredEdgeKey, hoveredNodeId, matchedNodeIds, normalEdgeKeys, normalNodeIds, rawEdges, rawNodes, selectedEdgeKey, selectedNodeId, zoom, pinnedEdgeKeys])

  const viewRef = useRef(view)
  viewRef.current = view

  const graphPlugins = useCallback((nodes: TopologyRenderNodeData[]): GraphPluginOption[] => [
    {
      type: 'toolbar',
      key: TOPOLOGY_ACTION_TOOLBAR_KEY,
      className: 'topo-g6-toolbar',
      position: 'top-right',
      style: { top: '14px', right: '14px' },
      getItems: () => topologyToolbarItems(toolbarStateRef.current),
      onClick: (value: string) => {
        const action = toolbarActionFromValue(value)
        const state = toolbarStateRef.current
        if (action === 'start-topology' && state.canStartTopology) {
          onStartTopologyRef.current()
        }
        if (action === 'refresh-topology' && state.canRefreshTopology) {
          onRefreshTopologyRef.current()
        }
      },
    },
    { type: 'minimap', size: [180, 120], position: 'right-bottom' },
    {
      type: 'tooltip',
      key: TOPOLOGY_TOOLTIP_KEY,
      trigger: 'hover',
      getContent: (event: unknown, items: Array<{ data?: Record<string, unknown>; source?: string; target?: string }>) => {
        // G6 5.1.1: items[0] = { id, source?, target?, data: actualElementData, style }
        // The actual node/edge data is at items[0].data (single nesting)
        const item = items?.[0]?.data as TopologyRenderNodeData | RenderedEdgeData | undefined
        if (!item) return ''
        if ('source' in item && 'target' in item) {
          return edgeTooltip(item as RenderedEdgeData, rawNodesRef.current)
        }
        return quoteTooltip(item as TopologyRenderNodeData)
      },
    },
    ...sectorHullPlugins(nodes),
  ], [])

  // ---- imperative node update (bypasses React re-render) ----
  const applyNodePatches = useCallback((items: TopologyNodePatch[]) => {
    const graph = graphRef.current
    if (!graph || !initializedRef.current) return

    const bySymbol = new Map(items.map((item) => [item.id, item]))
    const curView = viewRef.current
    const updates: Array<{ id: string; data: Record<string, unknown>; style: Record<string, unknown> }> = []

    rawNodesRef.current = rawNodesRef.current.map((node) => {
      const patch = bySymbol.get(node.id)
      if (!patch || isAggregateNode(node)) return node
      return {
        ...node,
        ...patch,
        size_level: patch.market_cap === null || patch.market_cap === undefined
          ? node.size_level
          : (patch as Partial<TopologyNodeData>).size_level || sizeLevelFromMarketCap(patch.market_cap),
      }
    })

    const nodeFillMap = buildNodeFillMap(rawNodesRef.current, rawEdgesRef.current, nodeColorMetric)
    for (const node of rawNodesRef.current) {
      const patch = bySymbol.get(node.id)
      if (!patch || isAggregateNode(node)) continue
      updates.push({
        id: node.id,
        data: node as unknown as Record<string, unknown>,
        style: nodeStyle(node, curView, nodeFillMap.get(node.id) || CENTER_NODE_FILL),
      })
    }

    if (updates.length > 0) {
      graph.updateNodeData(updates as never)
    }
  }, [nodeColorMetric])

  const applyQuotes = useCallback((items: TopologyQuoteItem[]) => {
    applyNodePatches(items.map((item) => ({
      id: item.symbol,
      symbol: item.symbol,
      market: item.market,
      code: item.code,
      name: item.name,
      sector: item.sector,
      industry: item.industry,
      pct_chg: item.pct_chg,
      market_cap: item.market_cap,
      market_cap_str: item.market_cap_str,
      quote_status: item.status,
      quote_updated_at: item.updated_at,
      quote_error: item.error,
      field_sources: item.field_sources,
      field_confidence: item.field_confidence,
      data_stage: item.data_stage,
      data_gaps: item.data_gaps,
    })))
  }, [applyNodePatches])

  useImperativeHandle(ref, () => ({ applyQuotes, applyNodePatches }), [applyNodePatches, applyQuotes])

  const layoutPositions = useMemo(() => {
    const centerId = rawNodes.find((node) => node.is_center)?.id || null
    if (centerId !== centerIdRef.current) {
      positionsRef.current = new Map()
      manuallyPositionedNodeIdsRef.current = new Set()
      centerIdRef.current = centerId
    }
    const next = ringSectoredLayout(rawNodes, rawEdges, positionsRef.current, manuallyPositionedNodeIdsRef.current)
    positionsRef.current = next
    return next
  }, [rawEdges, rawNodes])

  const graphData = useMemo(() => toG6Data(rawNodes, rawEdges, view, layoutPositions, nodeColorMetric), [layoutPositions, nodeColorMetric, rawEdges, rawNodes, view])

  useEffect(() => {
    const container = containerRef.current
    if (!container || graphRef.current) return

    const graph = new Graph({
      container,
      autoFit: 'view',
      background: '#080d14',
      data: { nodes: [], edges: [] },
      node: {
        type: 'circle',
        style: ((datum: GraphDatum) => datum.style || nodeStyle(datum.data as unknown as TopologyRenderNodeData, view, CENTER_NODE_FILL)) as never,
      },
      edge: {
        type: 'line',
        style: ((datum: GraphDatum) => datum.style || {}) as never,
      },
      behaviors: [
          'drag-canvas',
          'zoom-canvas',
          { type: 'drag-element', enable: (event: { targetType?: string }) => event.targetType === 'node' },
          'hover-activate',
        ],
      plugins: graphPlugins(rawNodes) as never,
    })

    graph.on('node:dblclick', (event: unknown) => {
      const id = (event as { target?: { id?: string } }).target?.id
      const node = rawNodesRef.current.find((item) => item.id === id)
      if (!node) return
      if (isAggregateNode(node)) {
        onExpandAggregateRef.current?.(node.aggregate_sector)
        return
      }
      if (node && !node.expanded) onExpandRef.current(node.code, node.market)
    })

    graph.on('node:pointerenter', (event: unknown) => {
      const id = (event as { target?: { id?: string } }).target?.id
      if (id) setHoveredNodeId(id)
    })

    graph.on('node:pointerleave', () => setHoveredNodeId(null))

    graph.on('edge:pointerenter', (event: unknown) => {
      const id = (event as { target?: { id?: string } }).target?.id
      if (!id || graph.destroyed) return
      const edgeDatum = graph.getEdgeData(id)
      const data = edgeDatum?.data as (TopologyRenderEdgeData & { edgeKey?: string; visualSource?: string; visualTarget?: string; stroke?: string }) | undefined
      const key = data?.edgeKey || null
      setHoveredEdgeKey(key)
      if (data && data.visualSource && data.visualTarget && data.stroke && key) {
        // Skip if already covered by node selection particles
        if (!nodeParticleKeysRef.current.has(key)) {
          startParticle(graph, particleMapRef.current, key, { visualSource: data.visualSource, visualTarget: data.visualTarget, stroke: data.stroke })
        }
      }
    })

    graph.on('edge:pointerleave', (event: unknown) => {
      setHoveredEdgeKey(null)
      const id = (event as { target?: { id?: string } }).target?.id
      if (!id || graph.destroyed) return
      const edgeDatum = graph.getEdgeData(id)
      const data = edgeDatum?.data as (TopologyRenderEdgeData & { edgeKey?: string }) | undefined
      const leavingKey = data?.edgeKey || null
      if (leavingKey && !nodeParticleKeysRef.current.has(leavingKey)) {
        destroyParticle(graph, particleMapRef.current, leavingKey)
      }
    })

    graph.on('node:click', (event: unknown) => {
      const id = (event as { target?: { id?: string } }).target?.id
      const node = rawNodesRef.current.find((item) => item.id === id) || null
      onSelectEdgeRef.current?.(null)
      onSelectNodeRef.current?.(node)
    })

    graph.on('node:drag', () => {
      if (graph.destroyed) return
      refreshSectorHulls(graph, rawNodesRef.current)
    })

    graph.on('node:dragend', (event: unknown) => {
      const id = (event as { target?: { id?: string } }).target?.id
      if (!id || graph.destroyed) return
      try {
        const [x, y] = graph.getElementPosition(id) as [number, number]
        positionsRef.current.set(id, { x, y })
        manuallyPositionedNodeIdsRef.current.add(id)
      } catch {
        /* position is best-effort only */
      }
      refreshSectorHulls(graph, rawNodesRef.current)

    })

    graph.on('edge:click', (event: unknown) => {
      const id = (event as { target?: { id?: string } }).target?.id
      if (!id || graph.destroyed) return
      const edgeDatum = graph.getEdgeData(id)
      const data = edgeDatum?.data as (TopologyRenderEdgeData & { edgeKey?: string }) | undefined
      if (!data) return
      const key = data.edgeKey
      if (!key) return
      // Toggle pin: pin makes the hover tooltip sticky; unpin hides it
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const tooltip = graphRef.current?.getPluginInstance(TOPOLOGY_TOOLTIP_KEY) as any
      setPinnedEdgeKeys((prev) => {
        const next = new Set(prev)
        if (next.has(key)) {
          next.delete(key)
          // Unpin: hide tooltip
          tooltip?.hide()
        } else {
          next.add(key)
          // Pin: tooltip already visible from hover, pin-aware canvas:pointermove keeps it alive
        }
        return next
      })
      onSelectNodeRef.current?.(null)
      onSelectEdgeRef.current?.(data, key)
    })

    graph.on('canvas:click', () => {
      onSelectNodeRef.current?.(null)
      onSelectEdgeRef.current?.(null)
      // Clear all pinned edges and hide tooltip on canvas click
      if (pinnedEdgeKeysRef.current.size > 0) {
        setPinnedEdgeKeys(new Set())
        const tooltip = graphRef.current?.getPluginInstance(TOPOLOGY_TOOLTIP_KEY) as any
        tooltip?.hide()
      }
    })

    graph.on('aftertransform', () => {
      if (graph.destroyed) return
      try {
        const nextZoom = graph.getZoom()
        setZoom((prev) => Math.abs(prev - nextZoom) > 0.08 ? nextZoom : prev)
      } catch {
        /* G6 may emit transform before the viewport controller is fully ready. */
      }
    })

    graphRef.current = graph

    return () => {
      destroyParticle(graph, particleMapRef.current)
      graph.destroy()
      graphRef.current = null
      initializedRef.current = false
      previousNodeIdsRef.current = new Set()
      previousEdgeIdsRef.current = new Set()
    }
  }, [graphPlugins])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph || graph.destroyed) return
    graph.updatePlugin({
      type: 'toolbar',
      key: TOPOLOGY_ACTION_TOOLBAR_KEY,
      className: 'topo-g6-toolbar',
      position: 'top-right',
      style: { top: '14px', right: '14px' },
      getItems: () => topologyToolbarItems(toolbarStateRef.current),
    } as never)
  }, [canRefreshTopology, canStartTopology, isTopologyBusy, startTopologyLabel])

  // Clear pinned edge labels when topology fundamentally changes (new center / reset)
  useEffect(() => {
    const centerId = rawNodes.find((node) => node.is_center)?.id || null
    if (rawNodes.length === 0 || (centerId !== null && centerId !== centerIdRef.current)) {
      setPinnedEdgeKeys(new Set())
    }
  }, [rawNodes])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return

    const nextNodeIds = new Set(rawNodes.map((node) => node.id))
    const prevNodeIds = previousNodeIdsRef.current
    const nextEdgeIds = new Set(rawEdges.map((edge) => `${edge.source}->${edge.target}:${edge.relation}`))
    const prevEdgeIds = previousEdgeIdsRef.current
    const nodeStructChange = rawNodes.length !== prevNodeIds.size || rawNodes.some((node) => !prevNodeIds.has(node.id))
    const edgeStructChange = rawEdges.length !== prevEdgeIds.size || rawEdges.some((edge) => !prevEdgeIds.has(`${edge.source}->${edge.target}:${edge.relation}`))
    const hasStructuralChange = nodeStructChange || edgeStructChange

    if (!initializedRef.current || hasStructuralChange) {
      destroyParticle(graph, particleMapRef.current)
      graph.setPlugins(graphPlugins(rawNodes) as never)
      tooltipPatchedRef.current = false
      graph.setData(graphData as never)
      void graph.render().then(() => {
        fitGraphView(graph, rawNodes.length)
        // Patch tooltip plugin for pin support (must run after async render completes)
        if (!tooltipPatchedRef.current) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const tp = graph.getPluginInstance(TOPOLOGY_TOOLTIP_KEY) as any
          if (tp) {
            graph.off('canvas:pointermove', tp.onCanvasMove)
            graph.off('node:drag', tp.onPointerLeave)
            graph.on('canvas:pointermove', (e: unknown) => {
              if (pinnedEdgeKeysRef.current.size > 0) return
              tp.onCanvasMove(e)
            })
            graph.on('node:drag', (e: unknown) => {
              if (pinnedEdgeKeysRef.current.size > 0) return
              tp.onPointerLeave(e)
            })
          }
          tooltipPatchedRef.current = true
        }
      })
      initializedRef.current = true
      previousNodeIdsRef.current = nextNodeIds
      previousEdgeIdsRef.current = nextEdgeIds
      return
    }

    const nodeFillMap = buildNodeFillMap(rawNodes, rawEdges, nodeColorMetric)
    graph.updateNodeData(rawNodes.map((node) => ({
      id: node.id,
      data: node as unknown as Record<string, unknown>,
      style: nodeStyle(node, view, nodeFillMap.get(node.id) || CENTER_NODE_FILL),
    })) as never)
    graph.updateEdgeData(graphData.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      data: edge.data,
      style: edge.style,
    })) as never)
    void graph.draw()
    previousNodeIdsRef.current = nextNodeIds
    previousEdgeIdsRef.current = nextEdgeIds
  }, [graphData, graphPlugins, nodeColorMetric, rawEdges, rawNodes, view])

  // --- 节点选中粒子动画 ---
  // 选中节点时，在所有关联边上显示方向性粒子流
  // 粒子方向由 visualEdge() 保证：上游边已 swap source↔target，箭头指向中心
  useEffect(() => {
    const graph = graphRef.current
    if (!graph || !initializedRef.current) return

    // 清除旧粒子
    for (const key of nodeParticleKeysRef.current) {
      destroyParticle(graph, particleMapRef.current, key)
    }
    nodeParticleKeysRef.current.clear()

    if (!selectedNodeId || !rawEdges.length) return

    const centerId = rawNodes.find((n) => n.is_center)?.id

    for (const edge of rawEdges) {
      const visual = visualEdge(edge)
      if (visual.source === selectedNodeId || visual.target === selectedNodeId) {
        const key = edgeKey(edge)
        const stroke = edgeStroke(visual, centerId)
        startParticle(graph, particleMapRef.current, key, {
          visualSource: visual.source,
          visualTarget: visual.target,
          stroke,
        })
        nodeParticleKeysRef.current.add(key)
      }
    }
  }, [selectedNodeId, rawEdges, rawNodes])

  // --- 筛选子图粒子动画 ---
  // 搜索/板块筛选命中后，在保留下来的路径边上显示流向。
  useEffect(() => {
    const graph = graphRef.current
    if (!graph || !initializedRef.current) return

    for (const key of flowParticleKeysRef.current) {
      destroyParticle(graph, particleMapRef.current, key)
    }
    flowParticleKeysRef.current.clear()

    const flowSet = flowEdgeKeys instanceof Set ? flowEdgeKeys : new Set(flowEdgeKeys)
    if (flowSet.size === 0 || rawEdges.length === 0) return

    const centerId = rawNodes.find((n) => n.is_center)?.id

    for (const edge of rawEdges) {
      const key = edgeKey(edge)
      if (!flowSet.has(key)) continue
      const visual = visualEdge(edge)
      const stroke = edgeStroke(visual, centerId)
      const particleKey = `flow:${key}`
      startParticle(graph, particleMapRef.current, particleKey, {
        visualSource: visual.source,
        visualTarget: visual.target,
        stroke,
      })
      flowParticleKeysRef.current.add(particleKey)
    }
  }, [flowEdgeKeys, rawEdges, rawNodes])

  return (
    <div className="topo-canvas-shell">
      <div className="topo-legend" aria-hidden="true">
        <span className="topo-legend-label">关系</span>
        <span className="topo-legend-item topo-legend-item--line topo-legend-item--upstream">上游边</span>
        <span className="topo-legend-item topo-legend-item--line topo-legend-item--downstream">下游边</span>
        <span className="topo-legend-item topo-legend-item--line topo-legend-item--peer">同业边</span>
        <span className="topo-legend-item topo-legend-item--center">中心节点</span>
        <span className="topo-legend-item topo-legend-item--degree">节点颜色：浅金 = 连接少，深金 = 连接多，当前指标 = {NODE_COLOR_METRIC_LABEL[nodeColorMetric]}</span>
      </div>
      <div className="topo-g6-help">滚轮缩放 · 拖拽画布 · 双击节点展开 · 悬浮查看详情</div>
      {rawNodes.length === 0 ? <div className="topo-empty topo-empty--canvas">选择股票后会优先加载上次拓扑，可开始或继续生成</div> : null}
      <div className="topo-canvas topo-canvas--g6" ref={containerRef} />
    </div>
  )
})
