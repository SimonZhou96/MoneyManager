# Topology Sector Aggregation View

**Date**: 2026-06-30
**Status**: approved

## Overview

Add a frontend-only summary view for the industry topology graph. The default graph should show the center stock, core one-hop companies, and sector-level aggregate buckets instead of rendering every multi-hop node at once.

The implementation must not change topology generation, backend APIs, database schemas, or screening logic. It is a presentation-layer transformation over the existing `TopologyNodeData[]` and `TopologyEdgeData[]`.

## Current State

- The topology page renders raw nodes and edges through `IndustryTopologyPanel` and `TopologyCanvas`.
- The graph already supports relation filters, sector filtering, company search, selected-path highlighting, cycle highlighting, and deterministic zone layout.
- Nodes contain both `sector` and `industry`, but the UI exposes sector/industry inconsistently.
- Multi-hop graphs can become visually dense, especially around high-degree stocks such as `US.NVDA`.

## Decisions

1. Use `sector` as the frontend canonical grouping dimension.
2. Use `industry` only as a fallback when `sector` is missing.
3. Default to summary mode.
4. In summary mode, show up to three representative companies per sector and fold the rest into a sector aggregate bucket.
5. Preserve original edge information by rendering aggregate summary edges in folded state and restoring real edges when a sector is expanded.

Canonical display sector:

```ts
function displaySector(node: TopologyNodeData): string {
  return normalizeText(node.sector) || normalizeText(node.industry) || '板块未知'
}
```

## Target UX

### Default Summary Mode

The default canvas displays:

- Center node, always visible.
- Up to three representative company nodes per sector.
- One aggregate bucket per sector when hidden nodes remain, such as `Semiconductors +3` or `板块未知 +11`.
- Summary edges from aggregate buckets to the center, grouped by relation direction.

Top toolbar additions:

- Segmented control: `摘要` / `完整`.
- Optional compact action: `全部收起`, visible when one or more sectors are expanded.
- Summary text: `显示 23/45 节点 · 聚合 6 板块`.

Right detail panel for a selected aggregate bucket:

- Sector name.
- Total node count and hidden node count.
- Top representative companies.
- Relation summary counts: upstream, downstream, peer.
- Actions: `展开板块`, `只看该板块`.

### Expanded Sector

When a user expands a sector:

- The aggregate bucket for that sector disappears.
- All real nodes in that sector become visible.
- Real edges for those nodes are restored.
- Other sectors remain folded unless already expanded.

When a user collapses a sector:

- Non-representative nodes in that sector are hidden again.
- The aggregate bucket and summary edges return.
- If the selected node becomes hidden, selection moves to the aggregate bucket.

### Full Mode

`完整` mode renders the existing full graph behavior with real nodes and real edges after the active filters are applied. It does not permanently mutate expanded sector state.

Switching back to `摘要` returns to folded summary behavior, keeping search text and relation filters.

## View Model

Introduce a frontend view model layer before data reaches `TopologyCanvas`.

```ts
type TopologyRenderNode = TopologyNodeData | TopologyAggregateNode

interface TopologyAggregateNode {
  id: string
  code: string
  name: string
  market: string
  sector: string
  industry?: string
  isAggregate: true
  aggregateSector: string
  hiddenNodeIds: string[]
  visibleRepresentativeIds: string[]
  relationCounts: Record<Direction, number>
  sourceEdges: TopologyEdgeData[]
  expanded: false
  stale: false
  is_center: false
  depth: number
  zone: TopologyZone
}
```

Aggregate node IDs should be deterministic:

```ts
`aggregate:${sector}:${zone}`
```

Use stable escaping for sector text so IDs do not collide with real stock symbols.

## Representative Selection

For each sector, select up to three visible representatives.

Ranking priority:

1. Depth 1 before deeper nodes.
2. Nodes directly connected to the center before indirect nodes.
3. Higher total degree in the current relation-filtered graph.
4. Higher `market_cap`.
5. Higher `size_level`.
6. Stable lexical fallback by `name` then `id`.

The center node is never counted inside a sector bucket.

## Edge Preservation

### Real Edges

Representative company nodes keep their original real edges. Existing edge color, hover tooltip, click selection, pinned labels, and detail behavior continue to work.

### Aggregate Summary Edges

Hidden nodes do not draw every real edge in folded state. Instead, their real edges are grouped into summary edges by direction:

- `upstream`
- `downstream`
- `peer`

Each aggregate summary edge stores the original source edges:

```ts
interface AggregateEdgeData extends TopologyEdgeData {
  isAggregate: true
  aggregateSector: string
  hiddenNodeIds: string[]
  sourceEdges: TopologyEdgeData[]
  relationCount: number
}
```

Tooltip example:

```text
Semiconductors · 同业关系 3 条
AMD, AVGO, INTC
```

Clicking a summary edge opens the detail panel with the full source-edge list and evidence snippets.

### Internal Hidden Edges

Edges between two hidden nodes in the same folded sector should not be drawn on the canvas by default. They should be counted in the aggregate detail panel as internal relations and restored when the sector expands.

## Search, Filters, And Drilldown

Search:

- Searching by company name or code should match hidden nodes.
- If a hidden node matches, the matching sector should be temporarily expanded or the aggregate bucket should be highlighted with a clear match count.
- The preferred behavior is temporary expansion for matched sectors, because it lets users see the exact company and center path.

Sector filter:

- Uses only `displaySector`.
- Filtering a sector shows that sector's representatives plus aggregate bucket in summary mode.
- `只看该板块` sets the sector filter to one sector and closes the aggregate detail action loop.

Relation filters:

- Apply before aggregation.
- Aggregate counts and representative ranking must reflect the active relation filter.

Only-important:

- Remains available, but summary mode already reduces clutter.
- If enabled, it affects representative eligibility after search/path requirements are satisfied.

## Canvas Rendering

`TopologyCanvas` should receive a compatible node and edge array. Minimal changes are preferred:

- Extend node/edge style helpers to detect `isAggregate`.
- Aggregate nodes should be visually distinct: larger bucket node, count badge in label, dashed or thicker outline.
- Aggregate summary edges should use existing direction colors with a dashed style and label count.
- Double-clicking an aggregate node expands its sector instead of calling the backend expand endpoint.
- Double-clicking a real node preserves the existing backend expansion behavior.

## State

New state in `IndustryTopologyPanel`:

```ts
const [viewMode, setViewMode] = useState<'summary' | 'full'>('summary')
const [expandedSectors, setExpandedSectors] = useState<Set<string>>(() => new Set())
```

State transitions:

- `展开板块`: add sector to `expandedSectors`.
- `收起板块`: remove sector from `expandedSectors`.
- `全部收起`: clear `expandedSectors`.
- `完整`: render full graph view.
- `摘要`: render summary graph view using current `expandedSectors`.
- New center stock or new topology task: clear `expandedSectors`.

## Data Flow

```text
API graph
  -> raw nodes / raw edges in IndustryTopologyPanel
  -> relation/search/sector filters
  -> summary aggregation view model
  -> TopologyCanvas render nodes / render edges
  -> node/edge click detail panel
```

The raw graph remains the source of truth. The aggregation layer is reversible.

## Error Handling

- Missing `sector` and `industry`: group under `板块未知`.
- Empty sector after filtering: show the existing empty canvas state.
- Aggregate source edges missing because of stale data: render the bucket with zero counts and omit summary edge labels.
- Duplicate aggregate IDs: use escaped sector plus zone and add a numeric suffix only if a collision is detected.

## Testing

Unit-level tests should cover pure aggregation helpers:

- `displaySector` uses `sector`, then `industry`, then `板块未知`.
- Representative ranking chooses at most three per sector.
- Folded sectors produce aggregate nodes with correct hidden IDs.
- Summary edges preserve `sourceEdges` and direction counts.
- Expanded sectors restore real nodes and real edges.
- Relation filters affect aggregation counts.
- Search against a hidden node reveals or highlights the correct sector.

Frontend verification:

- Run the existing frontend typecheck/build.
- Manually validate with `US.NVDA`:
  - Default summary mode is readable.
  - `Semiconductors`, `Auto Manufacturers`, and `板块未知` aggregate buckets appear when applicable.
  - Expanding and collapsing a sector restores and folds nodes correctly.
  - Edge tooltip/detail still exposes original relation evidence.
  - Switching `完整` / `摘要` is reversible.

## Out Of Scope

- Backend topology generation changes.
- Database schema changes.
- Rewriting `sector` or `industry` storage.
- New market data enrichment.
- Making aggregate buckets screening signals.
