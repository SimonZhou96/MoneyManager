import { useEffect, useMemo, useRef, useState } from 'react'
import { Circle } from '@antv/g'
import { Graph } from '@antv/g6'
import type { TopologyEdgeData, TopologyNodeData, TopologyZone } from './types'

interface Props {
  rawNodes: TopologyNodeData[]
  rawEdges: TopologyEdgeData[]
  onExpand: (code: string, market: string) => void
  selectedNodeId?: string | null
  selectedEdgeKey?: string | null
  focusNodeId?: string | null
  showEdgeLabels?: boolean
  onSelectNode?: (node: TopologyNodeData | null) => void
  onSelectEdge?: (edge: TopologyEdgeData | null, edgeKey?: string) => void
}

type G6Graph = InstanceType<typeof Graph>
type GraphDatum = { id: string; data?: Record<string, unknown>; style?: Record<string, unknown> }
type RenderedEdgeData = TopologyEdgeData & { edgeKey?: string; visualSource?: string; visualTarget?: string }

interface ViewState {
  zoom: number
  hoveredNodeId?: string | null
  hoveredEdgeKey?: string | null
  selectedNodeId?: string | null
  selectedEdgeKey?: string | null
  focusNodeId?: string | null
  showEdgeLabels?: boolean
  relatedNodeIds: Set<string>
  relatedEdgeKeys: Set<string>
}

const ZONE_COLOR: Record<TopologyZone, string> = {
  center: '#fbbf24',
  upstream: '#60a5fa',
  downstream: '#f59e0b',
  peer: '#8b5cf6',
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

export function sizeLevelFromMarketCap(marketCap: number | null | undefined): 1 | 2 | 3 | 4 | 5 | 6 {
  if (marketCap === null || marketCap === undefined || !Number.isFinite(marketCap)) return 1
  if (marketCap < 50 * 1e8) return 1
  if (marketCap < 200 * 1e8) return 2
  if (marketCap < 1000 * 1e8) return 3
  if (marketCap < 3000 * 1e8) return 4
  if (marketCap < 1e12) return 5
  return 6
}

function nodeSize(node: TopologyNodeData) {
  return node.is_center ? 42 : 10 + Math.max(1, node.size_level) * 4
}

function shortText(text: string, maxLength: number) {
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text
}

function nodeLabel(node: TopologyNodeData, view: ViewState) {
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

function hasMarketCap(node: TopologyNodeData) {
  return node.market_cap !== null && node.market_cap !== undefined && node.market_cap_str !== '未知' && node.market_cap_str !== '--'
}

function nodeStyle(node: TopologyNodeData, view: ViewState) {
  const missingMarketCap = !hasMarketCap(node)
  const fill = missingMarketCap && !node.is_center ? '#64748b' : ZONE_COLOR[node.zone] || ZONE_COLOR.peer
  const stroke = STATUS_RING[node.quote_status || 'pending'] || '#64748b'
  const hasFocus = Boolean(view.focusNodeId || view.selectedNodeId || view.hoveredNodeId || view.selectedEdgeKey || view.hoveredEdgeKey)
  const active = node.is_center || node.id === view.selectedNodeId || node.id === view.hoveredNodeId || node.id === view.focusNodeId || view.relatedNodeIds.has(node.id)
  const baseOpacity = missingMarketCap && !node.is_center ? 0.42 : node.quote_status === 'failed' || node.quote_status === 'error' ? 0.68 : 0.95
  return {
    size: nodeSize(node),
    fill,
    stroke,
    lineWidth: active ? node.is_center ? 4 : 3 : 1.5,
    opacity: hasFocus && !active ? 0.16 : baseOpacity,
    labelText: nodeLabel(node, view),
    labelFill: node.is_center ? '#f8fafc' : '#cbd5e1',
    labelFontSize: node.is_center ? 13 : 10,
    labelFontWeight: node.is_center ? 700 : 500,
    labelPlacement: 'bottom',
    labelBackground: true,
    labelBackgroundFill: 'rgba(15, 23, 42, 0.78)',
    labelBackgroundRadius: 4,
    labelPadding: [2, 4, 2, 4],
    labelOpacity: hasFocus && !active ? 0.18 : node.is_center ? 1 : 0.82,
    halo: node.is_center,
    haloStroke: '#fbbf24',
    haloLineWidth: 8,
    haloStrokeOpacity: 0.18,
  }
}

function edgeLabel(edge: TopologyEdgeData) {
  const text = edge.label || edge.evidence || edge.relation || ''
  return shortText(text, 22)
}

function edgeKey(edge: Pick<TopologyEdgeData, 'source' | 'target' | 'relation'>) {
  return `${edge.source}->${edge.target}:${edge.relation}`
}

function visualEdge(edge: TopologyEdgeData) {
  if (edge.direction === 'upstream') {
    return { ...edge, source: edge.target, target: edge.source }
  }
  return edge
}

function edgeStroke(edge: TopologyEdgeData, centerId: string | undefined) {
  if (centerId && edge.source === centerId) return CENTER_OUT_EDGE
  if (centerId && edge.target === centerId) return CENTER_IN_EDGE
  return EDGE_COLOR[edge.direction] || DEFAULT_EDGE
}

function edgeStyle(edge: TopologyEdgeData, centerId: string | undefined, view: ViewState, key: string) {
  const stroke = edgeStroke(edge, centerId)
  const touchesCenter = Boolean(centerId && (edge.source === centerId || edge.target === centerId))
  const active = key === view.selectedEdgeKey || key === view.hoveredEdgeKey || view.relatedEdgeKeys.has(key)
  const hasFocus = Boolean(view.focusNodeId || view.selectedNodeId || view.hoveredNodeId || view.selectedEdgeKey || view.hoveredEdgeKey)
  const showLabel = view.showEdgeLabels || key === view.selectedEdgeKey || key === view.hoveredEdgeKey || (view.zoom > 1.35 && touchesCenter)
  return {
    stroke,
    lineWidth: active ? 2.5 : 1,
    strokeOpacity: hasFocus && !active ? 0.03 : active ? 0.9 : 0.15,
    endArrow: false,
    shadowBlur: active ? 10 : 0,
    shadowColor: active ? stroke : 'transparent',
    shadowOffsetX: 0,
    shadowOffsetY: 0,
    labelText: showLabel ? edgeLabel(edge) : '',
    labelFill: '#dbeafe',
    labelFontSize: 9,
    labelBackground: true,
    labelBackgroundFill: 'rgba(15, 23, 42, 0.78)',
    labelBackgroundRadius: 4,
    labelPadding: [1, 4, 1, 4],
  }
}

function initialPosition(node: TopologyNodeData, index: number, total: number) {
  if (node.is_center) return { x: 0, y: 0 }
  const angleBase = node.zone === 'upstream'
    ? Math.PI
    : node.zone === 'downstream'
      ? 0
      : Math.PI / 2
  const spread = node.zone === 'peer' ? Math.PI * 1.4 : Math.PI * 0.65
  const ratio = total <= 1 ? 0.5 : index / Math.max(1, total - 1)
  const angle = angleBase - spread / 2 + spread * ratio
  const radius = 180 + node.depth * 110 + (index % 5) * 22
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius }
}

function toG6Data(nodes: TopologyNodeData[], edges: TopologyEdgeData[], view: ViewState) {
  const zoneCounts = new Map<TopologyZone, number>()
  nodes.forEach((node) => zoneCounts.set(node.zone, (zoneCounts.get(node.zone) || 0) + 1))
  const zoneIndex = new Map<TopologyZone, number>()
  const centerId = nodes.find((node) => node.is_center)?.id

  return {
    nodes: nodes.map((node) => {
      const index = zoneIndex.get(node.zone) || 0
      zoneIndex.set(node.zone, index + 1)
      return {
        id: node.id,
        data: node as unknown as Record<string, unknown>,
        style: {
          ...initialPosition(node, index, zoneCounts.get(node.zone) || nodes.length),
          ...nodeStyle(node, view),
        },
      }
    }),
    edges: edges.map((edge, index) => {
      const rendered = visualEdge(edge)
      const key = edgeKey(edge)
      const stroke = edgeStroke(rendered, centerId)
      return {
        id: `${rendered.source}->${rendered.target}:${edge.relation}:${index}`,
        source: rendered.source,
        target: rendered.target,
        data: { ...edge, edgeKey: key, visualSource: rendered.source, visualTarget: rendered.target, stroke } as unknown as Record<string, unknown>,
        style: edgeStyle(rendered, centerId, view, key),
      }
    }),
  }
}

function quoteTooltip(node: TopologyNodeData, edge?: TopologyEdgeData) {
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

function edgeTooltip(edge: RenderedEdgeData, nodes: TopologyNodeData[]) {
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
      <div class="topo-g6-tip-hint">点击边固定查看关系详情</div>
    </div>
  `
}

function relatedSets(nodes: TopologyNodeData[], edges: TopologyEdgeData[], anchorId?: string | null, edgeAnchor?: string | null) {
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

export function TopologyCanvas({
  rawNodes,
  rawEdges,
  onExpand,
  selectedNodeId,
  selectedEdgeKey,
  focusNodeId,
  showEdgeLabels,
  onSelectNode,
  onSelectEdge,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const graphRef = useRef<G6Graph | null>(null)
  const initializedRef = useRef(false)
  const previousNodeIdsRef = useRef<Set<string>>(new Set())
  const particleMapRef = useRef<Map<string, ParticleState>>(new Map())
  const nodeParticleKeysRef = useRef<Set<string>>(new Set())
  const onExpandRef = useRef(onExpand)
  const rawNodesRef = useRef(rawNodes)
  const onSelectNodeRef = useRef(onSelectNode)
  const onSelectEdgeRef = useRef(onSelectEdge)
  const [zoom, setZoom] = useState(1)
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null)
  const [hoveredEdgeKey, setHoveredEdgeKey] = useState<string | null>(null)
  onExpandRef.current = onExpand
  rawNodesRef.current = rawNodes
  onSelectNodeRef.current = onSelectNode
  onSelectEdgeRef.current = onSelectEdge

  const view = useMemo<ViewState>(() => {
    const activeNodeId = focusNodeId || selectedNodeId || hoveredNodeId
    const activeEdgeKey = selectedEdgeKey || hoveredEdgeKey
    const related = relatedSets(rawNodes, rawEdges, activeNodeId, activeEdgeKey)
    return {
      zoom,
      hoveredNodeId,
      hoveredEdgeKey,
      selectedNodeId,
      selectedEdgeKey,
      focusNodeId,
      showEdgeLabels,
      ...related,
    }
  }, [focusNodeId, hoveredEdgeKey, hoveredNodeId, rawEdges, rawNodes, selectedEdgeKey, selectedNodeId, showEdgeLabels, zoom])

  const graphData = useMemo(() => toG6Data(rawNodes, rawEdges, view), [rawEdges, rawNodes, view])

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
        style: ((datum: GraphDatum) => datum.style || nodeStyle(datum.data as unknown as TopologyNodeData, view)) as never,
      },
      edge: {
        type: 'line',
        style: ((datum: GraphDatum) => datum.style || {}) as never,
      },
      layout: {
        type: 'd3-force',
        link: { distance: 150, strength: 0.28 },
        manyBody: { strength: -360 },
        collide: { radius: 42, strength: 0.92 },
        x: { strength: 0.08 },
        y: { strength: 0.08 },
      },
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'hover-activate', 'focus-element'],
      plugins: [
        { type: 'minimap', size: [180, 120], position: 'right-bottom' },
        {
          type: 'tooltip',
          trigger: 'hover',
          getContent: (event: unknown, items: Array<{ data?: { data?: TopologyNodeData | TopologyEdgeData } }>) => {
            const target = (event as { target?: { data?: { data?: TopologyNodeData | RenderedEdgeData } } }).target?.data?.data
            const item = target || items?.[0]?.data?.data
            if (!item) return ''
            if ('source' in item && 'target' in item) {
              return edgeTooltip(item as RenderedEdgeData, rawNodesRef.current)
            }
            return quoteTooltip(item as TopologyNodeData)
          },
        },
      ],
    })

    graph.on('node:dblclick', (event: unknown) => {
      const id = (event as { target?: { id?: string } }).target?.id
      const node = rawNodesRef.current.find((item) => item.id === id)
      if (node && !node.expanded) onExpandRef.current(node.code, node.market)
    })

    graph.on('node:pointerenter', (event: unknown) => {
      const id = (event as { target?: { id?: string } }).target?.id
      if (id) setHoveredNodeId(id)
    })

    graph.on('node:pointerleave', () => setHoveredNodeId(null))

    graph.on('edge:pointerenter', (event: unknown) => {
      const data = (event as { target?: { data?: { data?: TopologyEdgeData & { edgeKey?: string; visualSource?: string; visualTarget?: string; stroke?: string } } } }).target?.data?.data
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
      const data = (event as { target?: { data?: { data?: TopologyEdgeData & { edgeKey?: string } } } }).target?.data?.data
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

    graph.on('edge:click', (event: unknown) => {
      const data = (event as { target?: { data?: { data?: TopologyEdgeData & { edgeKey?: string } } } }).target?.data?.data
      if (!data) return
      onSelectNodeRef.current?.(null)
      onSelectEdgeRef.current?.(data, data.edgeKey)
    })

    graph.on('canvas:click', () => {
      onSelectNodeRef.current?.(null)
      onSelectEdgeRef.current?.(null)
    })

    graph.on('aftertransform', () => {
      const nextZoom = graph.getZoom()
      setZoom((prev) => Math.abs(prev - nextZoom) > 0.08 ? nextZoom : prev)
    })

    graphRef.current = graph

    return () => {
      destroyParticle(graph, particleMapRef.current)
      graph.destroy()
      graphRef.current = null
      initializedRef.current = false
      previousNodeIdsRef.current = new Set()
    }
  }, [])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return

    const nextIds = new Set(rawNodes.map((node) => node.id))
    const previousIds = previousNodeIdsRef.current
    const hasStructuralChange = rawNodes.length !== previousIds.size || rawNodes.some((node) => !previousIds.has(node.id))

    if (!initializedRef.current || hasStructuralChange) {
      destroyParticle(graph, particleMapRef.current)
      graph.setData(graphData as never)
      void graph.render()
      initializedRef.current = true
      previousNodeIdsRef.current = nextIds
      return
    }

    graph.updateNodeData(rawNodes.map((node) => ({
      id: node.id,
      data: node as unknown as Record<string, unknown>,
      style: nodeStyle(node, view),
    })) as never)
    graph.updateEdgeData(graphData.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      data: edge.data,
      style: edge.style,
    })) as never)
    void graph.draw()
    previousNodeIdsRef.current = nextIds
  }, [graphData, rawNodes])

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

  return (
    <div className="topo-canvas-shell">
      <div className="topo-legend" aria-hidden="true">
        <span className="topo-legend-item topo-legend-item--upstream">上游</span>
        <span className="topo-legend-item topo-legend-item--downstream">下游</span>
        <span className="topo-legend-item topo-legend-item--peer">同业</span>
        <span className="topo-legend-item topo-legend-item--center">中心</span>
      </div>
      <div className="topo-g6-help">滚轮缩放 · 拖拽画布 · 双击节点展开 · 悬浮查看详情</div>
      <div className="topo-canvas topo-canvas--g6" ref={containerRef} />
    </div>
  )
}
