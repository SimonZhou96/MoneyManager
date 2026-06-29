import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { StockSearchInput } from './StockSearchInput'
import { TopologyCanvas, sizeLevelFromMarketCap } from './TopologyCanvas'
import type { TopologyCanvasHandle } from './TopologyCanvas'
import { topologyApi } from './topologyApi'
import type {
  SearchResult,
  TopologyEdgeData,
  TopologyGraph,
  TopologyNodeData,
  TopologyNodePatch,
  TopologyQuoteItem,
  TopologyStats,
  TopologyTaskStage,
} from './types'

const POLL_INTERVAL_MS = 2500
const MAX_POLL_ATTEMPTS = 24
const RELATION_POLL_INTERVAL_MS = 3500
const MAX_RELATION_POLL_ATTEMPTS = 24
const GRAPH_TASK_POLL_INTERVAL_MS = 1500
const GRAPH_TASK_TERMINAL_STAGES = new Set(['done', 'partial', 'failed', 'cancelled', 'expired'])
const TERMINAL_QUOTE_STATUSES = new Set(['cached', 'fresh', 'stale', 'failed', 'error', 'skipped'])
const NON_RETRY_QUOTE_STATUSES = new Set(['failed', 'error', 'skipped'])
const RELATION_FILTERS = [
  { key: 'upstream', label: '上游' },
  { key: 'downstream', label: '下游' },
  { key: 'peer', label: '同业' },
] as const
const FIELD_NAMES = ['name', 'sector', 'industry', 'market_cap', 'pct_chg'] as const

type RelationFilter = typeof RELATION_FILTERS[number]['key']
type QuotePollState = 'idle' | 'polling' | 'done' | 'timeout'
type RelationPollState = 'idle' | 'polling' | 'done' | 'timeout'
type RankedField = typeof FIELD_NAMES[number]

type RenderMeta = {
  hasRenderedSize: boolean
  fieldRanks: Partial<Record<RankedField, number>>
}

type PathScope = {
  nodeIds: Set<string>
  edgeKeys: Set<string>
}

function edgeKey(edge: TopologyEdgeData) {
  return `${edge.source}->${edge.target}:${edge.relation}`
}

function normalizeText(value: string | null | undefined) {
  return (value || '').trim().toLowerCase()
}

function nodeMatchesCompanyQuery(node: TopologyNodeData, query: string) {
  const keyword = normalizeText(query)
  if (!keyword) return true
  return [node.name, node.code, node.id].some((field) => normalizeText(field).includes(keyword))
}

function isValidSector(sector: string | null | undefined) {
  const value = (sector || '').trim()
  return value !== '' && value !== '--' && value !== '板块未知'
}

function addAdjacencyEdge(map: Map<string, TopologyEdgeData[]>, id: string, edge: TopologyEdgeData) {
  const list = map.get(id) || []
  list.push(edge)
  map.set(id, list)
}

function traversalEndpoints(edge: TopologyEdgeData) {
  if (edge.direction === 'upstream') {
    return [{ source: edge.target, target: edge.source }]
  }
  return [{ source: edge.source, target: edge.target }]
}

function buildAdjacency(edges: TopologyEdgeData[], undirected = false) {
  const map = new Map<string, TopologyEdgeData[]>()
  edges.forEach((edge) => {
    traversalEndpoints(edge).forEach(({ source, target }) => {
      addAdjacencyEdge(map, source, edge)
      if (undirected) addAdjacencyEdge(map, target, edge)
    })
  })
  return map
}

function nextNodeForEdge(edge: TopologyEdgeData, current: string, undirected: boolean) {
  for (const { source, target } of traversalEndpoints(edge)) {
    if (source === current) return target
    if (undirected && target === current) return source
  }
  return null
}

function findPath(startId: string, endId: string, adjacency: Map<string, TopologyEdgeData[]>, undirected = false) {
  if (startId === endId) return { nodes: [startId], edges: [] as TopologyEdgeData[] }
  const queue: string[] = [startId]
  const seen = new Set([startId])
  const previous = new Map<string, { nodeId: string; edge: TopologyEdgeData }>()

  while (queue.length > 0) {
    const current = queue.shift()!
    for (const edge of adjacency.get(current) || []) {
      const next = nextNodeForEdge(edge, current, undirected)
      if (!next || seen.has(next)) continue
      seen.add(next)
      previous.set(next, { nodeId: current, edge })
      if (next === endId) {
        const pathNodes = [endId]
        const pathEdges: TopologyEdgeData[] = []
        let cursor = endId
        while (cursor !== startId) {
          const step = previous.get(cursor)
          if (!step) break
          pathEdges.unshift(step.edge)
          pathNodes.unshift(step.nodeId)
          cursor = step.nodeId
        }
        return { nodes: pathNodes, edges: pathEdges }
      }
      queue.push(next)
    }
  }

  return null
}

function addPathToScope(scope: PathScope, path: { nodes: string[]; edges: TopologyEdgeData[] } | null) {
  if (!path) return
  path.nodes.forEach((id) => scope.nodeIds.add(id))
  path.edges.forEach((edge) => scope.edgeKeys.add(edgeKey(edge)))
}

function buildIncomingAdjacency(edges: TopologyEdgeData[]) {
  const map = new Map<string, TopologyEdgeData[]>()
  edges.forEach((edge) => {
    traversalEndpoints(edge).forEach(({ target }) => addAdjacencyEdge(map, target, edge))
  })
  return map
}

function previousNodeForEdge(edge: TopologyEdgeData, current: string) {
  for (const { source, target } of traversalEndpoints(edge)) {
    if (target === current) return source
  }
  return null
}

function centerPathScopeForMatches(matchIds: Set<string>, centerId: string | undefined, edges: TopologyEdgeData[]) {
  const directedAdjacency = buildAdjacency(edges)
  const undirectedAdjacency = buildAdjacency(edges, true)
  const scope: PathScope = { nodeIds: new Set(), edgeKeys: new Set() }

  matchIds.forEach((id) => {
    scope.nodeIds.add(id)
    if (centerId) addPathToScope(scope, findPath(id, centerId, directedAdjacency) || findPath(id, centerId, undirectedAdjacency, true))
  })

  return scope
}

function oneHopUpstreamAndCenterPathScopeForMatches(
  matchIds: Set<string>,
  centerId: string | undefined,
  edges: TopologyEdgeData[],
) {
  const directedAdjacency = buildAdjacency(edges)
  const undirectedAdjacency = buildAdjacency(edges, true)
  const incomingAdjacency = buildIncomingAdjacency(edges)
  const scope: PathScope = { nodeIds: new Set(), edgeKeys: new Set() }

  matchIds.forEach((id) => {
    scope.nodeIds.add(id)
    for (const edge of incomingAdjacency.get(id) || []) {
      const previous = previousNodeForEdge(edge, id)
      if (!previous) continue
      scope.nodeIds.add(previous)
      scope.edgeKeys.add(edgeKey(edge))
    }

    if (centerId) addPathToScope(scope, findPath(id, centerId, directedAdjacency) || findPath(id, centerId, undirectedAdjacency, true))
  })

  return scope
}

function hasFieldValue(field: RankedField, value: unknown) {
  if (value === null || value === undefined) return false
  if (field === 'name' || field === 'sector' || field === 'industry') {
    return String(value).trim() !== '' && String(value).trim() !== '--' && String(value).trim() !== '板块未知'
  }
  return true
}

function sourcePriority(source?: string) {
  if (!source) return 0
  if (source === 'resolver' || source === 'resolver_override') return 3
  if (source === 'search_enrich') return 2
  return 1
}

function containsCjk(value: unknown) {
  return /[\u3400-\u9fff]/.test(String(value || ''))
}

function hasCompleteQuoteFields(node: Pick<TopologyNodeData, 'price' | 'pct_chg' | 'market_cap'>) {
  return node.price !== null && node.price !== undefined
    && node.pct_chg !== null && node.pct_chg !== undefined
    && node.market_cap !== null && node.market_cap !== undefined
}

function stagePriority(stage?: string) {
  if (stage === 'source_verified') return 3
  if (stage === 'source_partial') return 2
  if (stage === 'llm_initial') return 1
  return 0
}

function formatTopologyStats(stats: TopologyStats, warnings: string[]) {
  const statusText = stats.relation_status === 'generating'
    ? '生成中'
    : stats.relation_status === 'cached'
      ? '缓存'
      : stats.relation_status === 'fresh'
        ? '已刷新'
        : stats.relation_status === 'failed'
          ? '失败'
          : stats.relation_status || 'pending'
  const backgroundText = stats.relation_status === 'generating'
    ? stats.background_started ? '后台已启动' : '后台生成中'
    : ''
  const stageText = stats.data_stage === 'llm_initial'
    ? '首屏 AI'
    : stats.data_stage === 'source_verified'
      ? '数据源已校正'
      : stats.data_stage === 'source_partial'
        ? '数据源补全中'
        : ''
  const warningText = warnings.length > 0 ? `告警 ${warnings.join(',')}` : ''
  return [`关系 ${statusText}`, stageText, `LLM调用 ${stats.llm_calls}`, `缓存 ${stats.cached_nodes ?? 0}`, `stale ${stats.stale_nodes ?? 0}`, backgroundText, warningText]
    .filter(Boolean)
    .join(' | ')
}

function buildRenderMeta(node: TopologyNodeData): RenderMeta {
  const fieldRanks: Partial<Record<RankedField, number>> = {}
  FIELD_NAMES.forEach((field) => {
    if (hasFieldValue(field, node[field])) {
      fieldRanks[field] = sourcePriority(node.field_sources?.[field])
    }
  })
  return {
    hasRenderedSize: node.market_cap !== null && node.market_cap !== undefined,
    fieldRanks,
  }
}

function buildProgress(stage: TopologyTaskStage, nodes: TopologyNodeData[], relationPollState: RelationPollState) {
  if (stage === 'graph_loading') return 10
  if (stage === 'graph_depth_expanding') return 35
  if (stage === 'graph_ready_search_enriching') return 65
  if (stage === 'graph_ready_source_polling') {
    const total = nodes.length || 1
    const ready = nodes.filter((node) => hasCompleteQuoteFields(node) || NON_RETRY_QUOTE_STATUSES.has(node.quote_status || 'pending')).length
    const quoteRatio = ready / total
    const relationRatio = relationPollState === 'done' || relationPollState === 'timeout' ? 1 : 0
    return Math.min(99, Math.round(75 + quoteRatio * 15 + relationRatio * 10))
  }
  if (stage === 'done' || stage === 'partial' || stage === 'failed') return 100
  return 0
}

function progressLabel(stage: TopologyTaskStage, quotePollState: QuotePollState, relationPollState: RelationPollState, expandingDepth: number | null) {
  if (stage === 'graph_loading') return 'LLM 首屏拓扑生成中'
  if (stage === 'graph_depth_expanding') return expandingDepth ? `第 ${expandingDepth} 度拓扑生成中` : '拓扑逐层生成中'
  if (stage === 'graph_ready_search_enriching') return 'Search 补强中'
  if (stage === 'graph_ready_source_polling') {
    if (quotePollState === 'polling' && relationPollState === 'polling') return '数据源行情与关系校正中'
    if (quotePollState === 'polling') return '数据源行情校正中'
    if (relationPollState === 'polling') return '关系状态校正中'
    return '数据源校正收尾中'
  }
  if (stage === 'partial') return '数据源部分完成，保留首屏结果'
  if (stage === 'failed') return '首屏拓扑生成失败'
  if (stage === 'done') return '数据源已校正'
  return '等待开始拓扑'
}

function isGraphTaskTerminal(stage: string) {
  return GRAPH_TASK_TERMINAL_STAGES.has(stage)
}

function DetailPanel({
  node,
  edge,
  edgeNodes,
  onClose,
}: {
  node: TopologyNodeData | null
  edge: TopologyEdgeData | null
  edgeNodes: { source?: TopologyNodeData; target?: TopologyNodeData }
  onClose: () => void
}) {
  if (!node && !edge) return null
  if (edge) {
    return (
      <aside className="topo-detail-panel">
        <button className="topo-detail-close" onClick={onClose}>×</button>
        <div className="topo-detail-kicker">关系详情</div>
        <h3>{edge.label || edge.relation}</h3>
        <div className="topo-detail-row"><span>方向</span><strong>{edge.direction}</strong></div>
        <div className="topo-detail-row"><span>来源</span><strong>{edgeNodes.source?.name || edge.source}</strong></div>
        <div className="topo-detail-row"><span>目标</span><strong>{edgeNodes.target?.name || edge.target}</strong></div>
        <div className="topo-detail-row"><span>类型</span><strong>{edge.relation || '--'}</strong></div>
        <p className="topo-detail-evidence">{edge.evidence || edge.label || '暂无关系解释'}</p>
      </aside>
    )
  }
  const sector = node?.sector && node.sector !== '--' ? node.sector : '板块未知'
  const industry = node?.industry?.trim() || '行业未知'
  const dataStage = node?.data_stage === 'llm_initial'
    ? 'AI 初始结果'
    : node?.data_stage === 'source_verified'
      ? '数据源已校正'
      : node?.data_stage === 'source_partial'
        ? '数据源补全中'
        : '未知'
  const fieldErrors = node?.field_errors
    ? Object.entries(node.field_errors).map(([field, reason]) => `${field}: ${reason}`).join('，')
    : ''
  return (
    <aside className="topo-detail-panel">
      <button className="topo-detail-close" onClick={onClose}>×</button>
      <div className="topo-detail-kicker">企业详情</div>
      <h3>{node?.name || node?.code}</h3>
      <div className="topo-detail-row"><span>代码</span><strong>{node?.id}</strong></div>
      <div className="topo-detail-row"><span>市场</span><strong>{node?.market}</strong></div>
      <div className="topo-detail-row"><span>板块</span><strong>{sector}</strong></div>
      <div className="topo-detail-row"><span>行业</span><strong>{industry}</strong></div>
      <div className="topo-detail-row"><span>价格</span><strong>{node?.price ?? '--'}</strong></div>
      <div className="topo-detail-row"><span>市值</span><strong>{node?.market_cap_str || '未知'}</strong></div>
      <div className="topo-detail-row"><span>涨跌幅</span><strong>{node?.pct_chg ?? '--'}</strong></div>
      <div className="topo-detail-row"><span>行情状态</span><strong>{node?.quote_status || 'pending'}</strong></div>
      {fieldErrors ? <div className="topo-detail-row"><span>缺失原因</span><strong>{fieldErrors}</strong></div> : null}
      <div className="topo-detail-row"><span>数据阶段</span><strong>{dataStage}</strong></div>
      <div className="topo-detail-hint">双击画布节点可继续展开该企业关系。</div>
    </aside>
  )
}

export function IndustryTopologyPanel() {
  const [selected, setSelected] = useState<SearchResult | null>(null)
  const [depth, setDepth] = useState(3)
  const [nodes, setNodes] = useState<TopologyNodeData[]>([])
  const [edges, setEdges] = useState<TopologyEdgeData[]>([])
  const [stats, setStats] = useState<string>('')
  const [warnings, setWarnings] = useState<string[]>([])
  const [relationStatus, setRelationStatus] = useState<TopologyStats['relation_status']>()
  const [taskStage, setTaskStage] = useState<TopologyTaskStage>('idle')
  const [progressPct, setProgressPct] = useState(0)
  const [quotePollState, setQuotePollState] = useState<QuotePollState>('idle')
  const [relationPollState, setRelationPollState] = useState<RelationPollState>('idle')
  const [expandingDepth, setExpandingDepth] = useState<number | null>(null)
  const [canContinueTopology, setCanContinueTopology] = useState(false)
  const [relationFilters, setRelationFilters] = useState<Set<RelationFilter>>(() => new Set(['upstream', 'downstream', 'peer']))
  const [graphQuery, setGraphQuery] = useState('')
  const [selectedSectors, setSelectedSectors] = useState<Set<string>>(() => new Set())
  const [sectorMenuOpen, setSectorMenuOpen] = useState(false)
  const [onlyImportant, setOnlyImportant] = useState(false)
  const [highlightCycles, setHighlightCycles] = useState(false)
  const [selectedNode, setSelectedNode] = useState<TopologyNodeData | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<TopologyEdgeData | null>(null)
  const [selectedEdgeKey, setSelectedEdgeKey] = useState<string | null>(null)
  const pollAttemptsRef = useRef(0)
  const relationPollAttemptsRef = useRef(0)
  const canvasRef = useRef<TopologyCanvasHandle>(null)
  const nodesRef = useRef<TopologyNodeData[]>([])
  const renderMetaRef = useRef<Map<string, RenderMeta>>(new Map())
  const taskIdRef = useRef(0)
  const graphTaskIdRef = useRef<string | null>(null)

  const symbols = useMemo(() => nodes.map((node) => node.id), [nodes])
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])
  const isBusy = taskStage === 'graph_loading' || taskStage === 'graph_depth_expanding' || taskStage === 'graph_ready_search_enriching' || taskStage === 'graph_ready_source_polling'
  const sectorOptions = useMemo(() => {
    const counts = new Map<string, number>()
    nodes.forEach((node) => {
      if (!isValidSector(node.sector)) return
      counts.set(node.sector, (counts.get(node.sector) || 0) + 1)
    })
    return Array.from(counts.entries())
      .map(([sector, count]) => ({ sector, count }))
      .sort((a, b) => b.count - a.count || a.sector.localeCompare(b.sector))
  }, [nodes])

  const visibleGraph = useMemo(() => {
    const center = nodes.find((node) => node.is_center)
    const centerId = center?.id
    const queryActive = normalizeText(graphQuery) !== ''
    const clickedNodeId = selectedNode?.id
    const clickActive = Boolean(clickedNodeId)
    const sectorActive = selectedSectors.size > 0
    const allowedEdges = edges.filter((edge) => relationFilters.has(edge.direction))
    const directMatchedNodeIds = new Set<string>()
    let pathNodeIds: Set<string> | null = null
    let pathEdgeKeys: Set<string> | null = null

    if (clickActive && clickedNodeId) {
      directMatchedNodeIds.add(clickedNodeId)
      const clickScope = oneHopUpstreamAndCenterPathScopeForMatches(new Set([clickedNodeId]), centerId, allowedEdges)
      pathNodeIds = clickScope.nodeIds
      pathEdgeKeys = clickScope.edgeKeys
    } else if (queryActive) {
      const queryMatchIds = new Set(nodes.filter((node) => nodeMatchesCompanyQuery(node, graphQuery)).map((node) => node.id))
      queryMatchIds.forEach((id) => directMatchedNodeIds.add(id))
      const queryScope = centerPathScopeForMatches(queryMatchIds, centerId, allowedEdges)
      pathNodeIds = queryScope.nodeIds
      pathEdgeKeys = queryScope.edgeKeys
    }

    if (sectorActive) {
      const sectorMatchIds = new Set(nodes
        .filter((node) => selectedSectors.has(node.sector))
        .map((node) => node.id))
      sectorMatchIds.forEach((id) => directMatchedNodeIds.add(id))
      const sectorScope = centerPathScopeForMatches(sectorMatchIds, centerId, allowedEdges)
      if (pathNodeIds && pathEdgeKeys) {
        pathNodeIds = new Set(Array.from(pathNodeIds).filter((id) => sectorScope.nodeIds.has(id)))
        pathEdgeKeys = new Set(Array.from(pathEdgeKeys).filter((id) => sectorScope.edgeKeys.has(id)))
        if (centerId) pathNodeIds.add(centerId)
        directMatchedNodeIds.forEach((id) => {
          if (sectorScope.nodeIds.has(id) && (!queryActive || pathNodeIds?.has(id))) pathNodeIds?.add(id)
        })
      } else {
        pathNodeIds = sectorScope.nodeIds
        pathEdgeKeys = sectorScope.edgeKeys
      }
    }

    const edgeNodeIds = new Set<string>()
    const scopedEdges = pathEdgeKeys
      ? allowedEdges.filter((edge) => pathEdgeKeys?.has(edgeKey(edge)))
      : allowedEdges
    scopedEdges.forEach((edge) => {
      edgeNodeIds.add(edge.source)
      edgeNodeIds.add(edge.target)
    })
    const filteredNodes = nodes.filter((node) => {
      if (node.is_center) return !pathNodeIds || pathNodeIds.has(node.id)
      if (pathNodeIds && !pathNodeIds.has(node.id)) return false
      if (onlyImportant && !pathNodeIds?.has(node.id) && node.depth > 1 && node.size_level < 3) return false
      return edgeNodeIds.has(node.id) || directMatchedNodeIds.has(node.id) || node.id === centerId
    })
    const visibleIds = new Set(filteredNodes.map((node) => node.id))
    const visibleMatchedNodeIds = new Set(Array.from(directMatchedNodeIds).filter((id) => visibleIds.has(id)))
    const visiblePathNodeIds = new Set(Array.from(pathNodeIds || []).filter((id) => visibleIds.has(id)))
    const visibleEdges = scopedEdges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target))
    return {
      nodes: filteredNodes,
      edges: visibleEdges,
      focusNodeId: clickActive ? clickedNodeId || null : queryActive ? Array.from(visibleMatchedNodeIds)[0] || null : null,
      matchedNodeIds: visibleMatchedNodeIds,
      normalNodeIds: clickActive || queryActive || sectorActive ? visiblePathNodeIds : new Set<string>(),
      directMatchedCount: visibleMatchedNodeIds.size,
      highlightedNodeCount: clickActive || queryActive || sectorActive
        ? new Set([...visiblePathNodeIds, ...visibleMatchedNodeIds]).size
        : visibleMatchedNodeIds.size,
      flowEdgeKeys: clickActive || queryActive || sectorActive
        ? new Set(Array.from(pathEdgeKeys || []).filter((key) => visibleEdges.some((edge) => edgeKey(edge) === key)))
        : new Set<string>(),
    }
  }, [edges, graphQuery, nodes, onlyImportant, relationFilters, selectedNode?.id, selectedSectors])

  const edgeNodes = useMemo(() => selectedEdge ? {
    source: nodeById.get(selectedEdge.source),
    target: nodeById.get(selectedEdge.target),
  } : {}, [nodeById, selectedEdge])

  const commitNodeState = useCallback((nextNodes: TopologyNodeData[], changedNodes: TopologyNodeData[]) => {
    nodesRef.current = nextNodes
    setNodes(nextNodes)
    if (changedNodes.length > 0) {
      canvasRef.current?.applyNodePatches(changedNodes.map((node) => ({ ...node })))
    }
    setSelectedNode((current) => current ? nextNodes.find((node) => node.id === current.id) || current : current)
  }, [])

  const mergeIntoExistingNodes = useCallback((patches: TopologyNodePatch[], fallbackSource: string) => {
    if (patches.length === 0 || nodesRef.current.length === 0) return
    const patchMap = new Map(patches.map((patch) => [patch.id, patch]))
    const changedNodes: TopologyNodeData[] = []
    const mergedNodes = nodesRef.current.map((node) => {
      const patch = patchMap.get(node.id)
      if (!patch) return node
      const meta = renderMetaRef.current.get(node.id) || buildRenderMeta(node)
      const nextNode: TopologyNodeData = {
        ...node,
        quote_status: patch.quote_status ?? node.quote_status,
        quote_updated_at: patch.quote_updated_at ?? node.quote_updated_at,
        quote_error: patch.quote_error ?? node.quote_error,
        data_gaps: patch.data_gaps ?? node.data_gaps,
        field_errors: patch.field_errors ?? node.field_errors,
      }
      let changed = false
      FIELD_NAMES.forEach((field) => {
        const incoming = patch[field]
        if (!hasFieldValue(field, incoming)) return
        const incomingSource = patch.field_sources?.[field] || fallbackSource
        const incomingRank = sourcePriority(incomingSource)
        const currentRank = meta.fieldRanks[field] ?? sourcePriority(node.field_sources?.[field])
        if (field === 'name') {
          const currentHasChinese = containsCjk(nextNode.name)
          const incomingHasChinese = containsCjk(incoming)
          if (currentHasChinese && !incomingHasChinese) return
          if (!incomingHasChinese && hasFieldValue(field, nextNode[field]) && incomingRank < currentRank) return
        } else if (hasFieldValue(field, nextNode[field]) && incomingRank < currentRank) {
          return
        }
        if ((nextNode as unknown as Record<string, unknown>)[field] === incoming) return
        ;(nextNode as unknown as Record<string, unknown>)[field] = incoming
        nextNode.field_sources = { ...(nextNode.field_sources || {}), [field]: incomingSource }
        if (patch.field_confidence?.[field] !== undefined) {
          nextNode.field_confidence = { ...(nextNode.field_confidence || {}), [field]: patch.field_confidence[field] }
        }
        meta.fieldRanks[field] = incomingRank
        changed = true
      })
      if (patch.price !== undefined && patch.price !== node.price) {
        nextNode.price = patch.price
        if (patch.field_sources?.price) {
          nextNode.field_sources = { ...(nextNode.field_sources || {}), price: patch.field_sources.price }
        }
        changed = true
      }
      if (hasFieldValue('market_cap', patch.market_cap)) {
        nextNode.market_cap_str = patch.market_cap_str || nextNode.market_cap_str
        if (!meta.hasRenderedSize) {
          nextNode.size_level = sizeLevelFromMarketCap(patch.market_cap)
          meta.hasRenderedSize = true
        }
      }
      if (patch.data_stage && stagePriority(patch.data_stage) >= stagePriority(nextNode.data_stage)) {
        nextNode.data_stage = patch.data_stage
        changed = true
      }
      renderMetaRef.current.set(node.id, meta)
      const metaChanged = nextNode.quote_status !== node.quote_status
        || nextNode.quote_updated_at !== node.quote_updated_at
        || nextNode.quote_error !== node.quote_error
        || nextNode.data_gaps !== node.data_gaps
        || nextNode.field_errors !== node.field_errors
      if (!changed && !metaChanged) {
        return node
      }
      changedNodes.push(nextNode)
      return nextNode
    })
    commitNodeState(mergedNodes, changedNodes)
  }, [commitNodeState])

  const applyQuoteItems = useCallback((items: TopologyQuoteItem[]) => {
    if (items.length === 0) return
    mergeIntoExistingNodes(items.map((item) => ({
      id: item.symbol,
      symbol: item.symbol,
      market: item.market,
      code: item.code,
      name: item.name,
      sector: item.sector,
      industry: item.industry,
      price: item.price,
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
      field_errors: item.field_errors,
    })), 'resolver')
  }, [mergeIntoExistingNodes])

  const startQuoteRefresh = useCallback(async (nextSymbols: string[]) => {
    if (nextSymbols.length === 0) {
      setQuotePollState('done')
      return
    }
    pollAttemptsRef.current = 0
    setQuotePollState('polling')
    try {
      const res = await topologyApi.refreshQuotes(nextSymbols)
      applyQuoteItems(res.data.items as TopologyQuoteItem[])
    } catch {
      // 行情刷新失败不影响拓扑首屏
    }
  }, [applyQuoteItems])

  const applyGraph = useCallback((graph: TopologyGraph, resetSelection = false, replaceAll = false) => {
    const previousNodes = replaceAll ? [] : nodesRef.current
    const previousIds = new Set(previousNodes.map((node) => node.id))
    if (replaceAll) {
      renderMetaRef.current.clear()
    }
    const nextNodes = graph.nodes.map((incoming) => {
      const prev = previousNodes.find((node) => node.id === incoming.id)
      if (!prev) {
        renderMetaRef.current.set(incoming.id, buildRenderMeta(incoming))
        return incoming
      }
      const patch: TopologyNodePatch = {
        id: incoming.id,
        symbol: incoming.id,
        market: incoming.market,
        code: incoming.code,
        name: incoming.name,
        sector: incoming.sector,
        industry: incoming.industry,
        price: incoming.price,
        pct_chg: incoming.pct_chg,
        market_cap: incoming.market_cap,
        market_cap_str: incoming.market_cap_str,
        quote_status: incoming.quote_status,
        quote_updated_at: incoming.quote_updated_at,
        quote_error: incoming.quote_error,
        field_sources: incoming.field_sources,
        field_confidence: incoming.field_confidence,
        data_stage: incoming.data_stage,
        data_gaps: incoming.data_gaps,
        field_errors: incoming.field_errors,
      }
      const meta = renderMetaRef.current.get(prev.id) || buildRenderMeta(prev)
      renderMetaRef.current.set(prev.id, meta)
      const merged = {
        ...prev,
        expanded: incoming.expanded,
        stale: incoming.stale,
        depth: incoming.depth,
        zone: incoming.zone,
        is_center: incoming.is_center,
      }
      return merged
    })
    nodesRef.current = nextNodes
    setNodes(nextNodes)
    setEdges(graph.edges)
    if (resetSelection) {
      setSelectedNode(null)
      setSelectedEdge(null)
      setSelectedEdgeKey(null)
    }
    setWarnings((current) => Array.from(new Set([...(graph.warnings || []), ...current])))
    setStats(formatTopologyStats(graph.stats, graph.warnings || []))
    setRelationStatus(graph.stats.relation_status)
    setCanContinueTopology(Boolean(graph.stats.can_continue || graph.stats.relation_status === 'generating'))
    setRelationPollState(graph.stats.relation_status === 'generating' ? 'polling' : 'done')
    const refreshSymbols = nextNodes
      .filter((node) => replaceAll || !previousIds.has(node.id))
      .map((node) => node.id)
    if (replaceAll) {
      nextNodes.forEach((node) => renderMetaRef.current.set(node.id, buildRenderMeta(node)))
    }
    mergeIntoExistingNodes(graph.nodes.map((node) => ({
      id: node.id,
      symbol: node.id,
      market: node.market,
      code: node.code,
      name: node.name,
      sector: node.sector,
      industry: node.industry,
      price: node.price,
      pct_chg: node.pct_chg,
      market_cap: node.market_cap,
      market_cap_str: node.market_cap_str,
      quote_status: node.quote_status,
      quote_updated_at: node.quote_updated_at,
      quote_error: node.quote_error,
      field_sources: node.field_sources,
      field_confidence: node.field_confidence,
      data_stage: node.data_stage,
      data_gaps: node.data_gaps,
      field_errors: node.field_errors,
    })), 'llm_graph')
    void startQuoteRefresh(refreshSymbols)
  }, [mergeIntoExistingNodes, startQuoteRefresh])

  const startTopology = useCallback(async (mode: 'start' | 'continue' = 'start') => {
    if (!selected) return
    const isContinue = mode === 'continue'
    const taskId = taskIdRef.current + 1
    taskIdRef.current = taskId
    const previousGraphTaskId = graphTaskIdRef.current
    graphTaskIdRef.current = null
    relationPollAttemptsRef.current = 0
    pollAttemptsRef.current = 0
    setWarnings([])
    setTaskStage('graph_loading')
    setProgressPct(5)
    setQuotePollState('idle')
    setRelationPollState('idle')
    setExpandingDepth(null)
    if (previousGraphTaskId) {
      void topologyApi.cancelGraphTask(previousGraphTaskId).catch(() => undefined)
    }
    try {
      const created = await topologyApi.createGraphTask(selected.code, selected.market, depth, selected.name, 'llm_initial')
      if (taskId !== taskIdRef.current) return
      graphTaskIdRef.current = created.data.task_id
      if (!isContinue || nodesRef.current.length === 0) {
        applyGraph(created.data.graph, true, true)
      }
      setWarnings(Array.from(new Set(created.data.warnings || [])))
      setStats(created.data.message || formatTopologyStats(created.data.graph.stats, created.data.warnings || []))
      setProgressPct(created.data.progress_pct)

      const pollGraphTask = async () => {
        const activeGraphTaskId = graphTaskIdRef.current
        if (!activeGraphTaskId || taskId !== taskIdRef.current) return
        try {
          const res = await topologyApi.getGraphTask(activeGraphTaskId)
          if (taskId !== taskIdRef.current || graphTaskIdRef.current !== activeGraphTaskId) return
          const task = res.data
          if (task.graph) {
            const replaceGraph = !isContinue && task.stage === 'initial_graph_ready'
            applyGraph(task.graph, replaceGraph, replaceGraph)
          }
          setWarnings(Array.from(new Set(task.warnings || [])))
          setStats(task.error?.message || task.message || formatTopologyStats(task.graph.stats, task.warnings || []))
          setProgressPct(task.progress_pct)
          setExpandingDepth(task.graph?.stats?.expanding_depth ?? null)
          if (task.stage === 'depth_expanding') {
            setTaskStage('graph_depth_expanding')
          }
          if (task.stage === 'initial_graph_ready' || task.stage === 'enriching' || task.stage === 'source_polling') {
            setTaskStage('graph_ready_source_polling')
          }
          if (task.stage === 'done') {
            setTaskStage('done')
            setRelationPollState('done')
            setProgressPct(100)
            return
          }
          if (task.stage === 'partial') {
            setTaskStage('partial')
            setRelationPollState('timeout')
            setProgressPct(100)
            return
          }
          if (task.stage === 'failed' || task.stage === 'expired' || task.stage === 'cancelled') {
            setTaskStage(task.stage === 'cancelled' ? 'partial' : 'failed')
            setRelationPollState('done')
            setProgressPct(100)
            return
          }
          if (!isGraphTaskTerminal(task.stage)) {
            window.setTimeout(pollGraphTask, GRAPH_TASK_POLL_INTERVAL_MS)
          }
        } catch (e) {
          if (taskId !== taskIdRef.current) return
          setTaskStage('failed')
          setProgressPct(100)
          setStats(`拓扑任务失败: ${(e as Error).message}`)
        }
      }

      window.setTimeout(pollGraphTask, GRAPH_TASK_POLL_INTERVAL_MS)
    } catch (e) {
      if (taskId !== taskIdRef.current) return
      setTaskStage('failed')
      setProgressPct(100)
      setStats(`拓扑失败: ${(e as Error).message}`)
    }
  }, [selected, depth, applyGraph])

  const onExpand = useCallback(async (code: string, market: string) => {
    if (!selected) return
    const existing = nodesRef.current.map((node) => node.id)
    try {
      const res = await topologyApi.expand(code, market, depth, existing)
      const data = res.data
      const existingIds = new Set(nodesRef.current.map((node) => node.id))
      const mergedNodes = [...nodesRef.current]
      data.nodes.forEach((node) => {
        if (!existingIds.has(node.id)) {
          mergedNodes.push(node)
          renderMetaRef.current.set(node.id, buildRenderMeta(node))
        }
      })
      const normalizedNodes = mergedNodes.map((node) => node.id === `${market}:${code}` || (node.code === code && node.market === market)
        ? { ...node, expanded: true }
        : node)
      nodesRef.current = normalizedNodes
      setNodes(normalizedNodes)
      setEdges((prev) => {
        const seen = new Set(prev.map((edge) => `${edge.source}->${edge.target}:${edge.relation}`))
        const merged = [...prev]
        data.edges.forEach((edge) => {
          const id = `${edge.source}->${edge.target}:${edge.relation}`
          if (!seen.has(id)) merged.push(edge)
        })
        return merged
      })
      setStats(formatTopologyStats(data.stats, warnings))
      setRelationStatus(data.stats.relation_status)
      setRelationPollState(data.stats.relation_status === 'generating' ? 'polling' : relationPollState)
      const addedSymbols = data.nodes.filter((node) => !existingIds.has(node.id)).map((node) => node.id)
      void startQuoteRefresh(addedSymbols)
    } catch {
      // 后续可以补 toast
    }
  }, [selected, depth, startQuoteRefresh, warnings, relationPollState])

  const onRefresh = useCallback(async () => {
    if (!selected || isBusy) return
    try {
      await topologyApi.refresh(selected.code, selected.market)
      await startTopology()
    } catch {
      // noop
    }
  }, [selected, isBusy, startTopology])

  const toggleRelation = useCallback((key: RelationFilter) => {
    setRelationFilters((prev) => {
      const next = new Set(prev)
      if (next.has(key) && next.size > 1) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const toggleSector = useCallback((sector: string) => {
    setSelectedSectors((prev) => {
      const next = new Set(prev)
      if (next.has(sector)) next.delete(sector)
      else next.add(sector)
      return next
    })
  }, [])

  const onSelectEdge = useCallback((edge: TopologyEdgeData | null, key?: string) => {
    setSelectedEdge(edge)
    setSelectedEdgeKey(edge ? key || edgeKey(edge) : null)
  }, [])

  useEffect(() => {
    const loadId = taskIdRef.current + 1
    taskIdRef.current = loadId
    graphTaskIdRef.current = null
    relationPollAttemptsRef.current = 0
    pollAttemptsRef.current = 0
    setWarnings([])
    setProgressPct(0)
    setQuotePollState('idle')
    setRelationPollState('idle')
    setExpandingDepth(null)
    setCanContinueTopology(false)
    setSelectedNode(null)
    setSelectedEdge(null)
    setSelectedEdgeKey(null)
    setSelectedSectors(new Set())
    setSectorMenuOpen(false)

    if (!selected) {
      nodesRef.current = []
      setNodes([])
      setEdges([])
      setStats('')
      setRelationStatus(undefined)
      setTaskStage('idle')
      return
    }

    setTaskStage('idle')
    setStats('读取已有拓扑...')
    topologyApi.existingGraph(selected.code, selected.market, depth, selected.name)
      .then((res) => {
        if (loadId !== taskIdRef.current) return
        if (res.data.nodes.length > 1 || res.data.edges.length > 0) {
          applyGraph(res.data, true, true)
          setTaskStage(res.data.stats.can_continue || res.data.stats.relation_status === 'generating' ? 'partial' : 'done')
          setProgressPct(res.data.stats.can_continue || res.data.stats.relation_status === 'generating' ? 100 : 100)
        } else {
          nodesRef.current = []
          setNodes([])
          setEdges([])
          setRelationStatus(res.data.stats.relation_status)
          setCanContinueTopology(false)
          setStats('暂无已有拓扑，可开始生成')
          setTaskStage('idle')
        }
      })
      .catch((e) => {
        if (loadId !== taskIdRef.current) return
        nodesRef.current = []
        setNodes([])
        setEdges([])
        setStats(`读取已有拓扑失败: ${(e as Error).message}`)
        setTaskStage('failed')
      })
  }, [applyGraph, depth, selected])

  useEffect(() => {
    if (quotePollState !== 'polling' || symbols.length === 0) return
    const activeSymbols = () => nodesRef.current
      .filter((node) => !hasCompleteQuoteFields(node) && !NON_RETRY_QUOTE_STATUSES.has(node.quote_status || 'pending'))
      .map((node) => node.id)

    const timer = window.setInterval(async () => {
      const pending = activeSymbols()
      if (pending.length === 0) {
        setQuotePollState('done')
        window.clearInterval(timer)
        return
      }
      if (pollAttemptsRef.current >= MAX_POLL_ATTEMPTS) {
        setQuotePollState('timeout')
        window.clearInterval(timer)
        return
      }
      pollAttemptsRef.current += 1
      try {
        const res = await topologyApi.quotes(pending)
        applyQuoteItems(res.data.items)
      } catch {
        // 轮询失败时等待下一轮
      }
    }, POLL_INTERVAL_MS)

    return () => window.clearInterval(timer)
  }, [applyQuoteItems, quotePollState, symbols.length])

  useEffect(() => {
    if (!selected || nodes.length === 0 || relationPollState !== 'polling' || relationStatus !== 'generating') return
    if (graphTaskIdRef.current) return
    const timer = window.setInterval(async () => {
      if (relationPollAttemptsRef.current >= MAX_RELATION_POLL_ATTEMPTS) {
        setRelationPollState('timeout')
        window.clearInterval(timer)
        return
      }
      relationPollAttemptsRef.current += 1
      try {
        const res = await topologyApi.graph(selected.code, selected.market, depth, selected.name, 'auto')
        applyGraph(res.data)
        if (res.data.stats.relation_status !== 'generating') {
          setRelationPollState('done')
          window.clearInterval(timer)
        }
      } catch {
        // 关系轮询失败时等待下一轮
      }
    }, RELATION_POLL_INTERVAL_MS)

    return () => window.clearInterval(timer)
  }, [applyGraph, depth, nodes.length, relationPollState, relationStatus, selected])

  useEffect(() => {
    if (taskStage === 'graph_ready_search_enriching' || taskStage === 'graph_ready_source_polling') {
      setProgressPct(buildProgress(taskStage, nodes, relationPollState))
    }
  }, [taskStage, nodes, relationPollState])

  useEffect(() => {
    if (taskStage !== 'graph_ready_source_polling') return
    const quotesDone = quotePollState === 'done' || quotePollState === 'timeout'
    const relationsDone = relationPollState === 'done' || relationPollState === 'timeout'
    if (!quotesDone || !relationsDone) return
    const isPartial = quotePollState === 'timeout' || relationPollState === 'timeout'
    setTaskStage(isPartial ? 'partial' : 'done')
    setProgressPct(100)
  }, [taskStage, quotePollState, relationPollState])

  useEffect(() => {
    if (selectedSectors.size === 0) return
    const valid = new Set(sectorOptions.map((item) => item.sector))
    setSelectedSectors((current) => {
      const next = new Set(Array.from(current).filter((sector) => valid.has(sector)))
      return next.size === current.size ? current : next
    })
  }, [sectorOptions, selectedSectors.size])

  return (
    <div className="industry-topology">
      <div className="topo-toolbar">
        <StockSearchInput onSelect={setSelected} selected={selected} onClearSelected={() => setSelected(null)} />
        <label>深度
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))} disabled={isBusy}>
            {[1, 2, 3, 4, 5].map((item) => <option key={item} value={item}>{item}度</option>)}
          </select>
        </label>
        <span className="topo-stats">{stats}</span>
      </div>
      {taskStage !== 'idle' ? (
        <div className="topo-progress">
          <div className={`progress-track ${taskStage === 'failed' ? 'failed' : taskStage === 'done' ? 'completed' : ''}`}>
            <span style={{ width: `${progressPct}%` }} />
          </div>
          <div className="topo-progress-meta">
            <strong>{progressLabel(taskStage, quotePollState, relationPollState, expandingDepth)}</strong>
            <span>{progressPct}%</span>
          </div>
          {warnings.length > 0 ? <div className="topo-progress-warning">warnings: {warnings.join(', ')}</div> : null}
        </div>
      ) : null}
      {nodes.length > 0 ? (
        <div className="topo-filterbar">
          <input
            className="topo-graph-search"
            value={graphQuery}
            onChange={(event) => {
              setGraphQuery(event.target.value)
              setSelectedNode(null)
              setSelectedEdge(null)
              setSelectedEdgeKey(null)
            }}
            placeholder="搜索公司 / 代码"
          />
          <div className="topo-filter-group" aria-label="关系类型过滤">
            {RELATION_FILTERS.map((item) => (
              <button
                key={item.key}
                className={relationFilters.has(item.key) ? 'active' : ''}
                onClick={() => toggleRelation(item.key)}
              >{item.label}</button>
            ))}
          </div>
          <div className="topo-sector-select">
            <button
              type="button"
              className={`topo-sector-select-trigger${selectedSectors.size > 0 ? ' active' : ''}`}
              onClick={() => setSectorMenuOpen((open) => !open)}
              aria-expanded={sectorMenuOpen}
            >
              {selectedSectors.size > 0 ? `板块 ${selectedSectors.size}` : '筛选板块'}
            </button>
            {sectorMenuOpen ? (
              <div className="topo-sector-menu">
                <div className="topo-sector-menu-head">
                  <span>当前图谱板块</span>
                  {selectedSectors.size > 0 ? <button type="button" onClick={() => setSelectedSectors(new Set())}>清空</button> : null}
                </div>
                {sectorOptions.length > 0 ? sectorOptions.map((item) => (
                  <label key={item.sector} className="topo-sector-option">
                    <input
                      type="checkbox"
                      checked={selectedSectors.has(item.sector)}
                      onChange={() => toggleSector(item.sector)}
                    />
                    <span>{item.sector}</span>
                    <strong>{item.count}</strong>
                  </label>
                )) : <div className="topo-sector-empty">暂无可筛选板块</div>}
              </div>
            ) : null}
          </div>
          <label className="topo-check"><input type="checkbox" checked={onlyImportant} onChange={(event) => setOnlyImportant(event.target.checked)} />只看重点</label>
          <label className="topo-check"><input type="checkbox" checked={highlightCycles} onChange={(event) => setHighlightCycles(event.target.checked)} />高亮环路</label>
          <span className="topo-filter-summary">
            显示 {visibleGraph.nodes.length}/{nodes.length} 节点 · {visibleGraph.edges.length}/{edges.length} 关系
            {visibleGraph.directMatchedCount > 0 ? ` · 命中 ${visibleGraph.directMatchedCount}` : ''}
            {visibleGraph.highlightedNodeCount > visibleGraph.directMatchedCount ? ` · 路径 ${visibleGraph.highlightedNodeCount}` : ''}
          </span>
        </div>
      ) : null}
      <TopologyCanvas
        ref={canvasRef}
        rawNodes={visibleGraph.nodes}
        rawEdges={visibleGraph.edges}
        onExpand={onExpand}
        onStartTopology={() => startTopology(canContinueTopology ? 'continue' : 'start')}
        onRefreshTopology={onRefresh}
        canStartTopology={Boolean(selected) && !isBusy}
        canRefreshTopology={Boolean(selected) && !isBusy}
        isTopologyBusy={isBusy}
        startTopologyLabel={canContinueTopology ? '继续拓扑' : '开始拓扑'}
        selectedNodeId={selectedNode?.id || null}
        selectedEdgeKey={selectedEdgeKey}
        focusNodeId={visibleGraph.focusNodeId}
        matchedNodeIds={visibleGraph.matchedNodeIds}
        normalNodeIds={visibleGraph.normalNodeIds}
        normalEdgeKeys={visibleGraph.flowEdgeKeys}
        flowEdgeKeys={visibleGraph.flowEdgeKeys}
        highlightCycles={highlightCycles}
        onSelectNode={(node) => {
          if (node) {
            const enriched = nodesRef.current.find((item) => item.id === node.id)
            setSelectedNode(enriched || node)
          } else {
            setSelectedNode(null)
          }
          if (node) onSelectEdge(null)
        }}
        onSelectEdge={onSelectEdge}
      />
      <DetailPanel node={selectedNode} edge={selectedEdge} edgeNodes={edgeNodes} onClose={() => { setSelectedNode(null); onSelectEdge(null) }} />
    </div>
  )
}
