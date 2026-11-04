import type {
  Direction,
  TopologyAggregateEdgeData,
  TopologyAggregateNodeData,
  TopologyEdgeData,
  TopologyNodeData,
  TopologyRenderEdgeData,
  TopologyRenderNodeData,
  TopologyZone,
} from './types'

export type TopologyViewMode = 'summary' | 'full'

export interface AggregationOptions {
  centerId?: string
  expandedSectors: Set<string>
  searchMatchNodeIds?: Set<string>
  representativeLimit?: number
}

export interface AggregatedTopology {
  nodes: TopologyRenderNodeData[]
  edges: TopologyRenderEdgeData[]
  aggregateCount: number
  foldedNodeCount: number
  sectorCount: number
  aggregateNodeIds: Set<string>
  autoExpandedSectors: Set<string>
}

type DegreeStats = { total: number }

const DEFAULT_REPRESENTATIVE_LIMIT = 3

function cleanText(value: string | null | undefined) {
  const text = (value || '').trim()
  return text === '--' ? '' : text
}

export function displaySector(node: Pick<TopologyNodeData, 'sector' | 'industry'>): string {
  return cleanText(node.sector) || cleanText(node.industry) || '板块未知'
}

export function aggregateNodeId(sector: string, zone: TopologyZone) {
  return `aggregate:${encodeURIComponent(sector)}:${zone}`
}

function edgeKey(edge: Pick<TopologyEdgeData, 'source' | 'target' | 'relation'>) {
  return `${edge.source}->${edge.target}:${edge.relation}`
}

function buildDegreeMap(nodes: TopologyNodeData[], edges: TopologyEdgeData[]) {
  const ids = new Set(nodes.map((node) => node.id))
  const degree = new Map<string, DegreeStats>()
  ids.forEach((id) => degree.set(id, { total: 0 }))
  edges.forEach((edge) => {
    if (ids.has(edge.source)) degree.get(edge.source)!.total += 1
    if (ids.has(edge.target)) degree.get(edge.target)!.total += 1
  })
  return degree
}

function touchesCenter(node: TopologyNodeData, centerId: string | undefined, edges: TopologyEdgeData[]) {
  return Boolean(centerId && edges.some((edge) => (
    (edge.source === centerId && edge.target === node.id)
    || (edge.target === centerId && edge.source === node.id)
  )))
}

function rankNodes(nodes: TopologyNodeData[], centerId: string | undefined, edges: TopologyEdgeData[]) {
  const degree = buildDegreeMap(nodes, edges)
  return [...nodes].sort((a, b) => {
    const depthDiff = (a.depth || 99) - (b.depth || 99)
    if (depthDiff !== 0) return depthDiff
    const centerDiff = Number(touchesCenter(b, centerId, edges)) - Number(touchesCenter(a, centerId, edges))
    if (centerDiff !== 0) return centerDiff
    const degreeDiff = (degree.get(b.id)?.total || 0) - (degree.get(a.id)?.total || 0)
    if (degreeDiff !== 0) return degreeDiff
    const capDiff = (b.market_cap || 0) - (a.market_cap || 0)
    if (capDiff !== 0) return capDiff
    const sizeDiff = (b.size_level || 0) - (a.size_level || 0)
    if (sizeDiff !== 0) return sizeDiff
    return (a.name || a.id).localeCompare(b.name || b.id) || a.id.localeCompare(b.id)
  })
}

function dominantZone(nodes: TopologyNodeData[]): TopologyZone {
  const counts = new Map<TopologyZone, number>()
  nodes.forEach((node) => counts.set(node.zone, (counts.get(node.zone) || 0) + 1))
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || 'peer'
}

function aggregateLabel(sector: string, hiddenCount: number) {
  return `${sector} +${hiddenCount}`
}

function buildAggregateNode(sector: string, visibleIds: string[], hiddenNodes: TopologyNodeData[], sourceEdges: TopologyEdgeData[]): TopologyAggregateNodeData {
  const zone = dominantZone(hiddenNodes)
  const relationCounts: Partial<Record<Direction, number>> = {}
  sourceEdges.forEach((edge) => {
    relationCounts[edge.direction] = (relationCounts[edge.direction] || 0) + 1
  })
  return {
    id: aggregateNodeId(sector, zone),
    code: aggregateNodeId(sector, zone),
    name: aggregateLabel(sector, hiddenNodes.length),
    market: hiddenNodes[0]?.market || '',
    sector,
    industry: undefined,
    price: null,
    pct_chg: null,
    market_cap: null,
    market_cap_str: `${hiddenNodes.length}家公司`,
    size_level: Math.min(6, Math.max(2, Math.ceil(Math.log2(hiddenNodes.length + 1)))) as 1 | 2 | 3 | 4 | 5 | 6,
    quote_status: 'cached',
    expanded: false,
    stale: false,
    is_center: false,
    depth: Math.min(...hiddenNodes.map((node) => node.depth || 1)),
    zone,
    is_aggregate: true,
    aggregate_sector: sector,
    hidden_node_ids: hiddenNodes.map((node) => node.id),
    visible_representative_ids: visibleIds,
    relation_counts: relationCounts,
    source_edges: sourceEdges,
  }
}

function buildAggregateEdges(aggregateNode: TopologyAggregateNodeData, centerId: string | undefined, hiddenIds: Set<string>, edges: TopologyEdgeData[]) {
  if (!centerId) return []
  const byDirection = new Map<Direction, TopologyEdgeData[]>()
  edges.forEach((edge) => {
    if (!hiddenIds.has(edge.source) && !hiddenIds.has(edge.target)) return
    const current = byDirection.get(edge.direction) || []
    current.push(edge)
    byDirection.set(edge.direction, current)
  })
  return [...byDirection.entries()].map(([direction, sourceEdges]): TopologyAggregateEdgeData => ({
    source: direction === 'upstream' ? aggregateNode.id : centerId,
    target: direction === 'upstream' ? centerId : aggregateNode.id,
    direction,
    relation: `aggregate_${direction}`,
    label: `${aggregateNode.aggregate_sector} · ${direction} ${sourceEdges.length}`,
    evidence: sourceEdges.slice(0, 5).map((edge) => edge.label || edge.evidence || edge.relation).filter(Boolean).join('；'),
    is_aggregate: true,
    aggregate_sector: aggregateNode.aggregate_sector,
    hidden_node_ids: aggregateNode.hidden_node_ids,
    source_edges: sourceEdges,
    relation_count: sourceEdges.length,
  }))
}

export function aggregateTopology(nodes: TopologyNodeData[], edges: TopologyEdgeData[], options: AggregationOptions): AggregatedTopology {
  const representativeLimit = options.representativeLimit ?? DEFAULT_REPRESENTATIVE_LIMIT
  const center = nodes.find((node) => node.is_center)
  const centerId = options.centerId || center?.id
  const searchMatchNodeIds = options.searchMatchNodeIds || new Set<string>()
  const autoExpandedSectors = new Set<string>()
  const grouped = new Map<string, TopologyNodeData[]>()

  nodes.forEach((node) => {
    if (node.is_center) return
    const sector = displaySector(node)
    const list = grouped.get(sector) || []
    list.push(node)
    grouped.set(sector, list)
  })

  searchMatchNodeIds.forEach((id) => {
    const node = nodes.find((item) => item.id === id)
    if (node && !node.is_center) autoExpandedSectors.add(displaySector(node))
  })

  const renderNodes: TopologyRenderNodeData[] = center ? [center] : []
  const renderEdges: TopologyRenderEdgeData[] = []
  const aggregateNodeIds = new Set<string>()
  const visibleIds = new Set<string>(center ? [center.id] : [])
  const hiddenIds = new Set<string>()
  const aggregateNodes: TopologyAggregateNodeData[] = []

  grouped.forEach((sectorNodes, sector) => {
    const expanded = options.expandedSectors.has(sector) || autoExpandedSectors.has(sector)
    const ranked = rankNodes(sectorNodes, centerId, edges)
    const representatives = expanded ? ranked : ranked.slice(0, representativeLimit)
    representatives.forEach((node) => {
      renderNodes.push(node)
      visibleIds.add(node.id)
    })
    const hidden = expanded ? [] : ranked.slice(representativeLimit)
    hidden.forEach((node) => hiddenIds.add(node.id))
    if (hidden.length > 0) {
      const hiddenSet = new Set(hidden.map((node) => node.id))
      const sourceEdges = edges.filter((edge) => hiddenSet.has(edge.source) || hiddenSet.has(edge.target))
      const aggregateNode = buildAggregateNode(sector, representatives.map((node) => node.id), hidden, sourceEdges)
      renderNodes.push(aggregateNode)
      visibleIds.add(aggregateNode.id)
      aggregateNodeIds.add(aggregateNode.id)
      aggregateNodes.push(aggregateNode)
    }
  })

  edges.forEach((edge) => {
    if (hiddenIds.has(edge.source) || hiddenIds.has(edge.target)) return
    if (visibleIds.has(edge.source) && visibleIds.has(edge.target)) renderEdges.push(edge)
  })

  aggregateNodes.forEach((aggregateNode) => {
    const hiddenSet = new Set(aggregateNode.hidden_node_ids)
    buildAggregateEdges(aggregateNode, centerId, hiddenSet, edges).forEach((edge) => {
      if (!renderEdges.some((existing) => edgeKey(existing) === edgeKey(edge))) renderEdges.push(edge)
    })
  })

  return {
    nodes: renderNodes,
    edges: renderEdges,
    aggregateCount: aggregateNodes.length,
    foldedNodeCount: hiddenIds.size,
    sectorCount: grouped.size,
    aggregateNodeIds,
    autoExpandedSectors,
  }
}

export function isAggregateNode(node: TopologyRenderNodeData | null | undefined): node is TopologyAggregateNodeData {
  return Boolean(node && 'is_aggregate' in node && node.is_aggregate)
}

export function isAggregateEdge(edge: TopologyRenderEdgeData | null | undefined): edge is TopologyAggregateEdgeData {
  return Boolean(edge && 'is_aggregate' in edge && edge.is_aggregate)
}
