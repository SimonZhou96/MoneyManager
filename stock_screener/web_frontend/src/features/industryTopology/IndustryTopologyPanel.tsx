import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { StockSearchInput } from './StockSearchInput'
import { TopologyCanvas, sizeLevelFromMarketCap } from './TopologyCanvas'
import { topologyApi } from './topologyApi'
import type { SearchResult, TopologyEdgeData, TopologyGraph, TopologyNodeData, TopologyQuoteItem, TopologyStats } from './types'

const POLL_INTERVAL_MS = 2500
const MAX_POLL_ATTEMPTS = 24
const RELATION_POLL_INTERVAL_MS = 3500
const MAX_RELATION_POLL_ATTEMPTS = 24
const TERMINAL_QUOTE_STATUSES = new Set(['cached', 'fresh', 'stale', 'failed', 'error', 'skipped'])
const RELATION_FILTERS = [
  { key: 'upstream', label: '上游' },
  { key: 'downstream', label: '下游' },
  { key: 'peer', label: '同业' },
] as const

type RelationFilter = typeof RELATION_FILTERS[number]['key']

function edgeKey(edge: TopologyEdgeData) {
  return `${edge.source}->${edge.target}:${edge.relation}`
}

function normalizeText(value: string | null | undefined) {
  return (value || '').trim().toLowerCase()
}

function nodeMatchesQuery(node: TopologyNodeData, query: string) {
  const keyword = normalizeText(query)
  if (!keyword) return true
  return [node.name, node.code, node.id, node.market, node.sector].some((field) => normalizeText(field).includes(keyword))
}

function formatTopologyStats(stats: TopologyStats) {
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
  return [`关系 ${statusText}`, `LLM调用 ${stats.llm_calls}`, `缓存 ${stats.cached_nodes ?? 0}`, `stale ${stats.stale_nodes ?? 0}`, backgroundText]
    .filter(Boolean)
    .join(' | ')
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
  return (
    <aside className="topo-detail-panel">
      <button className="topo-detail-close" onClick={onClose}>×</button>
      <div className="topo-detail-kicker">企业详情</div>
      <h3>{node?.name || node?.code}</h3>
      <div className="topo-detail-row"><span>代码</span><strong>{node?.id}</strong></div>
      <div className="topo-detail-row"><span>市场</span><strong>{node?.market}</strong></div>
      <div className="topo-detail-row"><span>板块</span><strong>{sector}</strong></div>
      <div className="topo-detail-row"><span>市值</span><strong>{node?.market_cap_str || '未知'}</strong></div>
      <div className="topo-detail-row"><span>涨跌幅</span><strong>{node?.pct_chg ?? '--'}</strong></div>
      <div className="topo-detail-row"><span>行情状态</span><strong>{node?.quote_status || 'pending'}</strong></div>
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
  const [relationStatus, setRelationStatus] = useState<TopologyStats['relation_status']>()
  const [relationFilters, setRelationFilters] = useState<Set<RelationFilter>>(() => new Set(['upstream', 'downstream', 'peer']))
  const [graphQuery, setGraphQuery] = useState('')
  const [onlyImportant, setOnlyImportant] = useState(false)
  const [showEdgeLabels, setShowEdgeLabels] = useState(false)
  const [selectedNode, setSelectedNode] = useState<TopologyNodeData | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<TopologyEdgeData | null>(null)
  const [selectedEdgeKey, setSelectedEdgeKey] = useState<string | null>(null)
  const pollAttemptsRef = useRef(0)
  const relationPollAttemptsRef = useRef(0)

  const symbols = useMemo(() => nodes.map((node) => node.id), [nodes])
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])

  const visibleGraph = useMemo(() => {
    const center = nodes.find((node) => node.is_center)
    const matchedNodeIds = new Set(nodes.filter((node) => nodeMatchesQuery(node, graphQuery)).map((node) => node.id))
    const allowedEdges = edges.filter((edge) => relationFilters.has(edge.direction) && (!graphQuery || matchedNodeIds.has(edge.source) || matchedNodeIds.has(edge.target)))
    const edgeNodeIds = new Set<string>()
    allowedEdges.forEach((edge) => {
      edgeNodeIds.add(edge.source)
      edgeNodeIds.add(edge.target)
    })
    const filteredNodes = nodes.filter((node) => {
      if (node.is_center) return true
      if (onlyImportant && node.depth > 1 && node.size_level < 3) return false
      if (graphQuery && !matchedNodeIds.has(node.id) && !edgeNodeIds.has(node.id)) return false
      return edgeNodeIds.has(node.id) || node.depth <= 1 || node.id === center?.id
    })
    const visibleIds = new Set(filteredNodes.map((node) => node.id))
    return {
      nodes: filteredNodes,
      edges: allowedEdges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
      focusNodeId: graphQuery ? Array.from(matchedNodeIds).find((id) => visibleIds.has(id)) || null : null,
    }
  }, [edges, graphQuery, nodes, onlyImportant, relationFilters])

  const edgeNodes = useMemo(() => selectedEdge ? {
    source: nodeById.get(selectedEdge.source),
    target: nodeById.get(selectedEdge.target),
  } : {}, [nodeById, selectedEdge])

  const applyQuoteItems = useCallback((items: TopologyQuoteItem[]) => {
    if (items.length === 0) return
    const bySymbol = new Map(items.map((item) => [item.symbol, item]))
    setNodes((prev) => prev.map((node) => {
      const quote = bySymbol.get(node.id)
      if (!quote) return node
      return {
        ...node,
        name: quote.name || node.name,
        pct_chg: quote.pct_chg,
        market_cap: quote.market_cap,
        market_cap_str: quote.market_cap_str || node.market_cap_str,
        size_level: quote.market_cap === null || quote.market_cap === undefined ? node.size_level : sizeLevelFromMarketCap(quote.market_cap),
        quote_status: quote.status,
        quote_updated_at: quote.updated_at,
        quote_error: quote.error,
      }
    }))
  }, [])

  const startQuoteRefresh = useCallback(async (nextSymbols: string[]) => {
    if (nextSymbols.length === 0) return
    pollAttemptsRef.current = 0
    try {
      await topologyApi.refreshQuotes(nextSymbols)
    } catch {
      // 行情刷新失败不影响拓扑首屏
    }
  }, [])

  const applyGraph = useCallback((graph: TopologyGraph, resetSelection = false) => {
    setNodes(graph.nodes)
    setEdges(graph.edges)
    if (resetSelection) {
      setSelectedNode(null)
      setSelectedEdge(null)
      setSelectedEdgeKey(null)
    }
    setStats(formatTopologyStats(graph.stats))
    setRelationStatus(graph.stats.relation_status)
    void startQuoteRefresh(graph.nodes.map((node) => node.id))
  }, [startQuoteRefresh])

  const startTopology = useCallback(async () => {
    if (!selected) return
    try {
      relationPollAttemptsRef.current = 0
      const res = await topologyApi.graph(selected.code, selected.market, depth)
      applyGraph(res.data, true)
    } catch (e) {
      setStats(`拓扑失败: ${(e as Error).message}`)
    }
  }, [selected, depth, applyGraph])

  const onExpand = useCallback(async (code: string, market: string) => {
    if (!selected) return
    const existing = nodes.map((node) => node.id)
    try {
      const res = await topologyApi.expand(code, market, depth, existing)
      const data = res.data
      const addedSymbols: string[] = []
      setNodes((prev) => {
        const map = new Map(prev.map((node) => [node.id, node]))
        data.nodes.forEach((node) => {
          if (!map.has(node.id)) addedSymbols.push(node.id)
          map.set(node.id, node)
        })
        return Array.from(map.values()).map((node) => node.id === `${market}:${code}` || (node.code === code && node.market === market)
          ? { ...node, expanded: true }
          : node)
      })
      setEdges((prev) => {
        const seen = new Set(prev.map((edge) => `${edge.source}->${edge.target}:${edge.relation}`))
        const merged = [...prev]
        data.edges.forEach((edge) => {
          const id = `${edge.source}->${edge.target}:${edge.relation}`
          if (!seen.has(id)) merged.push(edge)
        })
        return merged
      })
      setStats(formatTopologyStats(data.stats))
      setRelationStatus(data.stats.relation_status)
      if (data.stats.relation_status === 'generating') relationPollAttemptsRef.current = 0
      void startQuoteRefresh(addedSymbols)
    } catch { /* toast 可后续加 */ }
  }, [selected, nodes, depth, startQuoteRefresh])

  const onRefresh = useCallback(async () => {
    if (!selected) return
    try {
      await topologyApi.refresh(selected.code, selected.market)
      await startTopology()
    } catch { /* */ }
  }, [selected, startTopology])

  const toggleRelation = useCallback((key: RelationFilter) => {
    setRelationFilters((prev) => {
      const next = new Set(prev)
      if (next.has(key) && next.size > 1) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const onSelectEdge = useCallback((edge: TopologyEdgeData | null, key?: string) => {
    setSelectedEdge(edge)
    setSelectedEdgeKey(edge ? key || edgeKey(edge) : null)
  }, [])

  useEffect(() => {
    if (symbols.length === 0) return
    const activeSymbols = () => nodes
      .filter((node) => !TERMINAL_QUOTE_STATUSES.has(node.quote_status || 'pending'))
      .map((node) => node.id)

    const timer = window.setInterval(async () => {
      const pending = activeSymbols()
      if (pending.length === 0 || pollAttemptsRef.current >= MAX_POLL_ATTEMPTS) {
        window.clearInterval(timer)
        return
      }
      pollAttemptsRef.current += 1
      try {
        const res = await topologyApi.quotes(pending)
        applyQuoteItems(res.data.items)
      } catch {
        // 轮询失败时等待下一轮，不重置拓扑图
      }
    }, POLL_INTERVAL_MS)

    return () => window.clearInterval(timer)
  }, [applyQuoteItems, nodes, symbols.length])

  useEffect(() => {
    if (!selected || nodes.length === 0 || relationStatus !== 'generating') return
    const timer = window.setInterval(async () => {
      if (relationPollAttemptsRef.current >= MAX_RELATION_POLL_ATTEMPTS) {
        window.clearInterval(timer)
        return
      }
      relationPollAttemptsRef.current += 1
      try {
        const res = await topologyApi.graph(selected.code, selected.market, depth)
        applyGraph(res.data)
        if (res.data.stats.relation_status !== 'generating') {
          window.clearInterval(timer)
        }
      } catch {
        // 关系轮询失败时等待下一轮，不清空已有缓存图
      }
    }, RELATION_POLL_INTERVAL_MS)

    return () => window.clearInterval(timer)
  }, [applyGraph, depth, nodes.length, relationStatus, selected])

  return (
    <div className="industry-topology">
      <div className="topo-toolbar">
        <StockSearchInput onSelect={setSelected} selected={selected} onClearSelected={() => setSelected(null)} />
        <label>深度
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((item) => <option key={item} value={item}>{item}度</option>)}
          </select>
        </label>
        <button onClick={startTopology} disabled={!selected}>开始拓扑</button>
        <button onClick={onRefresh} disabled={!selected}>刷新关系</button>
        <span className="topo-stats">{stats}</span>
      </div>
      {nodes.length > 0 ? (
        <div className="topo-filterbar">
          <input
            className="topo-graph-search"
            value={graphQuery}
            onChange={(event) => setGraphQuery(event.target.value)}
            placeholder="搜索公司 / 代码 / 板块"
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
          <label className="topo-check"><input type="checkbox" checked={onlyImportant} onChange={(event) => setOnlyImportant(event.target.checked)} />只看重点</label>
          <label className="topo-check"><input type="checkbox" checked={showEdgeLabels} onChange={(event) => setShowEdgeLabels(event.target.checked)} />显示边标签</label>
          <span className="topo-filter-summary">显示 {visibleGraph.nodes.length}/{nodes.length} 节点 · {visibleGraph.edges.length}/{edges.length} 关系</span>
        </div>
      ) : null}
      {nodes.length > 0 ? (
        <TopologyCanvas
          rawNodes={visibleGraph.nodes}
          rawEdges={visibleGraph.edges}
          onExpand={onExpand}
          selectedNodeId={selectedNode?.id || null}
          selectedEdgeKey={selectedEdgeKey}
          focusNodeId={visibleGraph.focusNodeId}
          showEdgeLabels={showEdgeLabels}
          onSelectNode={(node) => {
            setSelectedNode(node)
            if (node) onSelectEdge(null)
          }}
          onSelectEdge={onSelectEdge}
        />
      ) : <div className="topo-empty">选择股票后点击&quot;开始拓扑&quot;</div>}
      <DetailPanel node={selectedNode} edge={selectedEdge} edgeNodes={edgeNodes} onClose={() => { setSelectedNode(null); onSelectEdge(null) }} />
    </div>
  )
}
