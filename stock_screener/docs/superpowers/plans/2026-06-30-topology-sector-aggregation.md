# Topology Sector Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a frontend-only sector summary mode for the industry topology graph, showing the center stock, up to three representative companies per sector, foldable aggregate buckets, and reversible summary/full views.

**Architecture:** Keep raw topology data as the source of truth in `IndustryTopologyPanel`. Add a pure aggregation module that transforms filtered raw nodes/edges into render nodes/edges, then make `TopologyCanvas` style and interact with aggregate nodes/edges without changing backend APIs.

**Tech Stack:** React 18, TypeScript, Vite, AntV G6 5.1.1, existing CSS in `web_frontend/src/styles.css`.

---

## File Structure

- Create `web_frontend/src/features/industryTopology/topologyAggregation.ts`: Pure helper module for canonical sector display, representative ranking, aggregate nodes, aggregate summary edges, and render metadata guards.
- Modify `web_frontend/src/features/industryTopology/types.ts`: Add frontend render-only aggregate node and aggregate edge types while preserving existing API types.
- Modify `web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx`: Add summary/full mode state, expanded sector state, canonical sector filtering, aggregate detail actions, and pass render data to the canvas.
- Modify `web_frontend/src/features/industryTopology/TopologyCanvas.tsx`: Detect aggregate nodes/edges, style them differently, route double-click on aggregate nodes to frontend expansion, and keep real-node backend expansion unchanged.
- Modify `web_frontend/src/styles.css`: Add compact segmented controls, aggregate bucket styles, and aggregate detail panel styles.

Implementation notes:

- Do not modify backend files.
- Do not delete the `industry` API field. Only stop treating it as a first-class UI grouping dimension.
- Preserve existing dirty user changes in `TopologyCanvas.tsx` and `styles.css`; read current files before editing.
- Use `npm run build` from `web_frontend` as the verification command.

---

### Task 1: Add Render-Only Aggregate Types

**Files:**
- Modify: `web_frontend/src/features/industryTopology/types.ts`

- [ ] **Step 1: Add aggregate render types**

Append these definitions after `TopologyEdgeData`:

```ts
export interface TopologyAggregateNodeData extends TopologyNodeData {
  is_aggregate: true
  aggregate_sector: string
  hidden_node_ids: string[]
  visible_representative_ids: string[]
  relation_counts: Partial<Record<Direction, number>>
  source_edges: TopologyEdgeData[]
}

export interface TopologyAggregateEdgeData extends TopologyEdgeData {
  is_aggregate: true
  aggregate_sector: string
  hidden_node_ids: string[]
  source_edges: TopologyEdgeData[]
  relation_count: number
}

export type TopologyRenderNodeData = TopologyNodeData | TopologyAggregateNodeData
export type TopologyRenderEdgeData = TopologyEdgeData | TopologyAggregateEdgeData
```

- [ ] **Step 2: Run TypeScript build to expose type consumers**

Run:

```bash
cd web_frontend && npm run build
```

Expected: It may fail because existing components still use `TopologyNodeData[]`; capture the errors for Task 2 and Task 3.

- [ ] **Step 3: Commit**

```bash
git add web_frontend/src/features/industryTopology/types.ts
git commit -m "feat: add topology aggregate render types"
```

---

### Task 2: Implement Pure Sector Aggregation Helpers

**Files:**
- Create: `web_frontend/src/features/industryTopology/topologyAggregation.ts`

- [ ] **Step 1: Create the helper module**

Create `topologyAggregation.ts` with this complete implementation:

```ts
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
```

- [ ] **Step 2: Run build and fix only local compile errors in this new file**

Run:

```bash
cd web_frontend && npm run build
```

Expected: Type errors may still appear in existing components because they do not accept render types yet. Errors inside `topologyAggregation.ts` must be fixed before continuing.

- [ ] **Step 3: Commit**

```bash
git add web_frontend/src/features/industryTopology/topologyAggregation.ts
git commit -m "feat: add topology sector aggregation helpers"
```

---

### Task 3: Wire Summary Mode Into IndustryTopologyPanel

**Files:**
- Modify: `web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx`

- [ ] **Step 1: Update imports**

Change the type import to include render types:

```ts
import type {
  SearchResult,
  TopologyAggregateEdgeData,
  TopologyAggregateNodeData,
  TopologyEdgeData,
  TopologyGraph,
  TopologyNodeColorMetric,
  TopologyNodeData,
  TopologyNodePatch,
  TopologyQuoteItem,
  TopologyRenderEdgeData,
  TopologyRenderNodeData,
  TopologyStats,
  TopologyTaskStage,
} from './types'
```

Add:

```ts
import { aggregateTopology, displaySector, isAggregateEdge, isAggregateNode, type TopologyViewMode } from './topologyAggregation'
```

- [ ] **Step 2: Replace sector validation with canonical display sector**

Replace:

```ts
function isValidSector(sector: string | null | undefined) {
  const value = (sector || '').trim()
  return value !== '' && value !== '--' && value !== '板块未知'
}
```

with:

```ts
function isValidSector(sector: string | null | undefined) {
  const value = (sector || '').trim()
  return value !== '' && value !== '--'
}
```

Update every sector option computation to use `displaySector(node)`:

```ts
const sectorOptions = useMemo(() => {
  const counts = new Map<string, number>()
  nodes.forEach((node) => {
    if (node.is_center) return
    const sector = displaySector(node)
    if (!isValidSector(sector)) return
    counts.set(sector, (counts.get(sector) || 0) + 1)
  })
  return Array.from(counts.entries())
    .map(([sector, count]) => ({ sector, count }))
    .sort((a, b) => b.count - a.count || a.sector.localeCompare(b.sector))
}, [nodes])
```

- [ ] **Step 3: Add view state**

After `onlyImportant` state, add:

```ts
const [viewMode, setViewMode] = useState<TopologyViewMode>('summary')
const [expandedSectors, setExpandedSectors] = useState<Set<string>>(() => new Set())
```

Add helpers near `toggleSector`:

```ts
const expandSector = useCallback((sector: string) => {
  setExpandedSectors((current) => new Set(current).add(sector))
}, [])

const collapseSector = useCallback((sector: string) => {
  setExpandedSectors((current) => {
    const next = new Set(current)
    next.delete(sector)
    return next
  })
}, [])

const collapseAllSectors = useCallback(() => {
  setExpandedSectors(new Set())
}, [])
```

- [ ] **Step 4: Clear expanded sectors on new graph**

Inside `applyGraph`, after `if (resetSelection) { ... }`, add:

```ts
if (replaceAll) {
  setExpandedSectors(new Set())
}
```

- [ ] **Step 5: Split filtering and aggregation in visibleGraph**

Replace the final return of `visibleGraph` with a two-phase result. Keep the existing path/filter calculations, but after `filteredNodes`, `visibleIds`, and `visibleEdges` are built, add:

```ts
    const searchMatchNodeIds = queryActive ? visibleMatchedNodeIds : new Set<string>()
    const aggregated = viewMode === 'summary'
      ? aggregateTopology(filteredNodes, visibleEdges, {
        centerId,
        expandedSectors,
        searchMatchNodeIds,
        representativeLimit: 3,
      })
      : {
        nodes: filteredNodes as TopologyRenderNodeData[],
        edges: visibleEdges as TopologyRenderEdgeData[],
        aggregateCount: 0,
        foldedNodeCount: 0,
        sectorCount: sectorOptions.length,
        aggregateNodeIds: new Set<string>(),
        autoExpandedSectors: new Set<string>(),
      }
    const renderedNodeIds = new Set(aggregated.nodes.map((node) => node.id))
    const renderedEdges = aggregated.edges.filter((edge) => renderedNodeIds.has(edge.source) && renderedNodeIds.has(edge.target))
    return {
      nodes: aggregated.nodes,
      edges: renderedEdges,
      focusNodeId: clickActive ? clickedNodeId || null : queryActive ? Array.from(visibleMatchedNodeIds)[0] || null : null,
      matchedNodeIds: visibleMatchedNodeIds,
      normalNodeIds: clickActive || queryActive || sectorActive ? visiblePathNodeIds : new Set<string>(),
      directMatchedCount: visibleMatchedNodeIds.size,
      highlightedNodeCount: clickActive || queryActive || sectorActive
        ? new Set([...visiblePathNodeIds, ...visibleMatchedNodeIds]).size
        : visibleMatchedNodeIds.size,
      flowEdgeKeys: clickActive || queryActive || sectorActive
        ? new Set(Array.from(pathEdgeKeys || []).filter((key) => renderedEdges.some((edge) => edgeKey(edge) === key)))
        : new Set<string>(),
      aggregateCount: aggregated.aggregateCount,
      foldedNodeCount: aggregated.foldedNodeCount,
      sectorCount: aggregated.sectorCount,
    }
```

Update the `useMemo` dependency array to include `expandedSectors`, `sectorOptions.length`, and `viewMode`.

- [ ] **Step 6: Add toolbar view controls**

In the filter bar before the node color select, add:

```tsx
<div className="topo-view-mode" aria-label="拓扑视图模式">
  <button type="button" className={viewMode === 'summary' ? 'active' : ''} onClick={() => setViewMode('summary')}>摘要</button>
  <button type="button" className={viewMode === 'full' ? 'active' : ''} onClick={() => setViewMode('full')}>完整</button>
</div>
{expandedSectors.size > 0 ? (
  <button type="button" className="topo-collapse-all" onClick={collapseAllSectors}>全部收起</button>
) : null}
```

Update summary text:

```tsx
<span className="topo-filter-summary">
  显示 {visibleGraph.nodes.length}/{nodes.length} 节点 · {visibleGraph.edges.length}/{edges.length} 关系
  {viewMode === 'summary' ? ` · 聚合 ${visibleGraph.aggregateCount} 板块 · 折叠 ${visibleGraph.foldedNodeCount}` : ''}
  {visibleGraph.directMatchedCount > 0 ? ` · 命中 ${visibleGraph.directMatchedCount}` : ''}
  {visibleGraph.highlightedNodeCount > visibleGraph.directMatchedCount ? ` · 路径 ${visibleGraph.highlightedNodeCount}` : ''}
</span>
```

- [ ] **Step 7: Route aggregate node selection and double-click**

Pass two new callbacks to `TopologyCanvas`:

```tsx
onExpandAggregate={(sector) => expandSector(sector)}
onCollapseAggregate={(sector) => collapseSector(sector)}
```

In `onSelectNode`, accept aggregate render nodes:

```ts
onSelectNode={(node) => {
  if (node) {
    if (isAggregateNode(node)) {
      setSelectedNode(node as unknown as TopologyNodeData)
    } else {
      const enriched = nodesRef.current.find((item) => item.id === node.id)
      setSelectedNode(enriched || node)
    }
  } else {
    setSelectedNode(null)
  }
  if (node) onSelectEdge(null)
}}
```

- [ ] **Step 8: Enhance DetailPanel for aggregate nodes and edges**

At the top of `DetailPanel`, before the real edge branch, add:

```tsx
  if (node && isAggregateNode(node as TopologyAggregateNodeData)) {
    const aggregate = node as TopologyAggregateNodeData
    return (
      <aside className="topo-detail-panel topo-detail-panel--aggregate">
        <button className="topo-detail-close" onClick={onClose}>×</button>
        <div className="topo-detail-kicker">板块聚合</div>
        <h3>{aggregate.aggregate_sector}</h3>
        <div className="topo-detail-row"><span>折叠公司</span><strong>{aggregate.hidden_node_ids.length}</strong></div>
        <div className="topo-detail-row"><span>代表公司</span><strong>{aggregate.visible_representative_ids.length}</strong></div>
        <div className="topo-detail-row"><span>上游</span><strong>{aggregate.relation_counts.upstream || 0}</strong></div>
        <div className="topo-detail-row"><span>下游</span><strong>{aggregate.relation_counts.downstream || 0}</strong></div>
        <div className="topo-detail-row"><span>同业</span><strong>{aggregate.relation_counts.peer || 0}</strong></div>
        <div className="topo-detail-actions">
          <button type="button" onClick={() => expandSector(aggregate.aggregate_sector)}>展开板块</button>
          <button type="button" onClick={() => setSelectedSectors(new Set([aggregate.aggregate_sector]))}>只看该板块</button>
        </div>
      </aside>
    )
  }
```

Because `DetailPanel` currently does not receive `expandSector` or `setSelectedSectors`, extend its props:

```ts
  onExpandSector: (sector: string) => void
  onFilterSector: (sector: string) => void
```

and pass:

```tsx
<DetailPanel
  node={selectedNode}
  edge={selectedEdge}
  edgeNodes={edgeNodes}
  onClose={() => { setSelectedNode(null); onSelectEdge(null) }}
  onExpandSector={expandSector}
  onFilterSector={(sector) => setSelectedSectors(new Set([sector]))}
/>
```

For aggregate edges, add this before the real edge detail branch:

```tsx
  if (edge && isAggregateEdge(edge as TopologyAggregateEdgeData)) {
    const aggregate = edge as TopologyAggregateEdgeData
    return (
      <aside className="topo-detail-panel topo-detail-panel--aggregate">
        <button className="topo-detail-close" onClick={onClose}>×</button>
        <div className="topo-detail-kicker">聚合关系</div>
        <h3>{aggregate.aggregate_sector}</h3>
        <div className="topo-detail-row"><span>方向</span><strong>{aggregate.direction}</strong></div>
        <div className="topo-detail-row"><span>关系数</span><strong>{aggregate.relation_count}</strong></div>
        <p className="topo-detail-evidence">{aggregate.evidence || '展开板块查看完整关系证据。'}</p>
        <div className="topo-detail-actions">
          <button type="button" onClick={() => onExpandSector(aggregate.aggregate_sector)}>展开板块</button>
          <button type="button" onClick={() => onFilterSector(aggregate.aggregate_sector)}>只看该板块</button>
        </div>
      </aside>
    )
  }
```

- [ ] **Step 9: Build**

Run:

```bash
cd web_frontend && npm run build
```

Expected: Type errors may remain in `TopologyCanvas.tsx` until Task 4. No errors should remain in `IndustryTopologyPanel.tsx` except prop/type mismatches with `TopologyCanvas`.

- [ ] **Step 10: Commit**

```bash
git add web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx
git commit -m "feat: wire topology sector summary mode"
```

---

### Task 4: Teach TopologyCanvas About Aggregate Nodes And Edges

**Files:**
- Modify: `web_frontend/src/features/industryTopology/TopologyCanvas.tsx`

- [ ] **Step 1: Update imports and props**

Change the type import:

```ts
import type {
  TopologyEdgeData,
  TopologyNodeColorMetric,
  TopologyNodeData,
  TopologyNodePatch,
  TopologyQuoteItem,
  TopologyRenderEdgeData,
  TopologyRenderNodeData,
} from './types'
import { isAggregateEdge, isAggregateNode } from './topologyAggregation'
```

Update props:

```ts
  rawNodes: TopologyRenderNodeData[]
  rawEdges: TopologyRenderEdgeData[]
  onExpand: (code: string, market: string) => void
  onExpandAggregate?: (sector: string) => void
  onCollapseAggregate?: (sector: string) => void
  onSelectNode?: (node: TopologyRenderNodeData | null) => void
  onSelectEdge?: (edge: TopologyRenderEdgeData | null, edgeKey?: string) => void
```

Update internal refs for `rawNodesRef` and `rawEdgesRef` to use render types.

- [ ] **Step 2: Style aggregate nodes**

In `nodeSize`, add:

```ts
  if (isAggregateNode(node)) return Math.min(54, 28 + node.hidden_node_ids.length * 1.2)
```

In `nodeLabel`, add before normal node logic:

```ts
  if (isAggregateNode(node)) return `${node.aggregate_sector}\n+${node.hidden_node_ids.length}`
```

In `nodeStyle`, add:

```ts
  const aggregate = isAggregateNode(node)
```

Change return fields:

```ts
    fill: aggregate ? 'rgba(96, 165, 250, 0.16)' : fill,
    stroke: aggregate ? '#60a5fa' : matched ? '#f8fafc' : stroke,
    lineWidth: aggregate ? 2.5 : matched ? node.is_center ? 5 : 4 : active ? node.is_center ? 4 : 3 : 1.5,
    lineDash: aggregate ? [6, 4] : undefined,
    labelFill: aggregate ? '#dbeafe' : matched ? '#f8fafc' : node.is_center ? '#f8fafc' : '#cbd5e1',
```

- [ ] **Step 3: Style aggregate edges**

In `edgeStyle`, add:

```ts
  const aggregate = isAggregateEdge(edge)
```

Change style fields:

```ts
    lineWidth: aggregate ? 2 : cyclic ? 3 : (active || isPinned) ? 2.5 : normal ? 1.4 : 1,
    strokeOpacity: aggregate ? 0.55 : hasFocus && !active && !cyclic && !isPinned && !normal ? 0.03 : (active || cyclic || isPinned) ? 0.9 : normal ? 0.32 : 0.15,
    lineDash: aggregate ? [8, 5] : cyclic ? [6, 4] : undefined,
    labelText: aggregate ? `${edge.direction} ${isAggregateEdge(edge) ? edge.relation_count : ''}` : showLabel ? (cyclic ? `环路 · ${edgeLabel(edge)}` : edgeLabel(edge)) : '',
```

- [ ] **Step 4: Update tooltips**

At the top of `quoteTooltip`, add:

```ts
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
```

At the top of `edgeTooltip`, add:

```ts
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
```

- [ ] **Step 5: Route double-click**

Replace the `node:dblclick` handler body with:

```ts
      const node = rawNodesRef.current.find((item) => item.id === id)
      if (!node) return
      if (isAggregateNode(node)) {
        onExpandAggregateRef.current?.(node.aggregate_sector)
        return
      }
      if (!node.expanded) onExpandRef.current(node.code, node.market)
```

Add refs for aggregate callbacks near existing callback refs:

```ts
const onExpandAggregateRef = useRef<Props['onExpandAggregate']>(onExpandAggregate)
const onCollapseAggregateRef = useRef<Props['onCollapseAggregate']>(onCollapseAggregate)
```

and update them in the callback-ref effect:

```ts
onExpandAggregateRef.current = onExpandAggregate
onCollapseAggregateRef.current = onCollapseAggregate
```

- [ ] **Step 6: Build**

Run:

```bash
cd web_frontend && npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web_frontend/src/features/industryTopology/TopologyCanvas.tsx
git commit -m "feat: render topology aggregate buckets"
```

---

### Task 5: Add Styles For Summary Controls And Aggregate Detail

**Files:**
- Modify: `web_frontend/src/styles.css`

- [ ] **Step 1: Add filter bar controls**

Near existing `.topo-filter-group` styles, add:

```css
.topo-view-mode {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border: 1px solid #2a3040;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.92);
}
.topo-view-mode button,
.topo-collapse-all {
  padding: 5px 10px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: #8a8070;
  cursor: pointer;
  font-size: 12px;
  white-space: nowrap;
}
.topo-view-mode button.active {
  background: rgba(96, 165, 250, 0.18);
  color: #dbeafe;
}
.topo-collapse-all {
  border: 1px solid rgba(96, 165, 250, 0.26);
  background: rgba(96, 165, 250, 0.1);
  color: #dbeafe;
}
```

- [ ] **Step 2: Add aggregate detail actions**

Near `.topo-detail-*` styles, add:

```css
.topo-detail-panel--aggregate {
  border-color: rgba(96, 165, 250, 0.36);
}
.topo-detail-actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.topo-detail-actions button {
  flex: 1;
  padding: 7px 10px;
  border: 1px solid rgba(96, 165, 250, 0.32);
  border-radius: 8px;
  background: rgba(96, 165, 250, 0.12);
  color: #dbeafe;
  cursor: pointer;
  font-size: 12px;
}
.topo-detail-actions button:hover {
  border-color: rgba(96, 165, 250, 0.58);
  background: rgba(96, 165, 250, 0.18);
}
.topo-g6-tip--aggregate .topo-g6-tip-title {
  color: #dbeafe;
}
```

- [ ] **Step 3: Build**

Run:

```bash
cd web_frontend && npm run build
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web_frontend/src/styles.css
git commit -m "style: add topology aggregation controls"
```

---

### Task 6: Manual Verification With US.NVDA

**Files:**
- No source changes expected.

- [ ] **Step 1: Start the dev server**

Run:

```bash
cd web_frontend && npm run dev
```

Expected: Vite prints a local URL, usually `http://localhost:5173/`.

- [ ] **Step 2: Open the topology page**

Use the browser at the local URL and navigate to `产业拓扑`.

Expected:

- The page loads without console errors.
- Search/select `US.NVDA`.
- Existing graph loads or can be started from the toolbar.

- [ ] **Step 3: Verify default summary mode**

Expected:

- `摘要` is active by default.
- The summary line includes `聚合`.
- The graph is visibly less dense than full mode.
- Buckets such as `Semiconductors`, `Auto Manufacturers`, or `板块未知` appear when enough nodes exist.
- Top 3 representative nodes remain as real company nodes.

- [ ] **Step 4: Verify edge preservation**

Expected:

- Representative company edges retain existing colors and tooltips.
- Aggregate edges appear dashed and show direction counts.
- Clicking an aggregate edge opens a detail panel with relation count and evidence preview.

- [ ] **Step 5: Verify expand/collapse**

Expected:

- Double-clicking an aggregate bucket expands that sector.
- `全部收起` appears after expansion.
- Clicking `全部收起` restores buckets.
- Switching `完整` shows full graph; switching back to `摘要` restores summary behavior.

- [ ] **Step 6: Verify search and filters**

Expected:

- Searching a hidden company reveals or highlights the matching sector.
- Sector filter uses only the canonical sector label.
- `只看该板块` filters to that sector.
- Relation filters update aggregate counts.

- [ ] **Step 7: Final build**

Run:

```bash
cd web_frontend && npm run build
```

Expected: PASS.

- [ ] **Step 8: Commit verification notes if any docs changed**

If no files changed, do not commit. If implementation notes were added to docs, run:

```bash
git add docs/superpowers/plans/2026-06-30-topology-sector-aggregation.md
git commit -m "docs: record topology aggregation verification"
```

