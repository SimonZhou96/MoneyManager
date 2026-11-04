# Topology Canvas Edge Interaction Rework

**Date**: 2026-06-26
**Status**: approved

## Overview

Rework how edge (relation) labels are displayed on the industry topology canvas. Replace the global "显示关系" checkbox with a hover-to-inspect, click-to-pin interaction model, where pinned labels adapt their length to the available space between nodes.

## Current State

- A "显示关系" checkbox globally toggles all edge labels
- Edge labels are abbreviated to 22 chars via `edgeLabel()`
- Hover tooltip (`edgeTooltip()`) shows full edge details (source, target, direction, relation, evidence)
- Clicking an edge selects it and opens a `DetailPanel` sidebar

## Target State

1. **No global edge label toggle** — edges are hidden by default, no "显示关系" checkbox
2. **Hover** → tooltip with full edge description, upstream/downstream node names
3. **Click** → pin the edge label on the canvas (persistent), + sidebar DetailPanel
4. **Click again** → unpin, label disappears from canvas
5. **Drag nodes** → pinned labels adapt: longer edges show more text, shorter edges truncate

## Design

### 1. Remove Global Edge Label Toggle

**Files**: `IndustryTopologyPanel.tsx`, `TopologyCanvas.tsx`

- Delete `showEdgeLabels` state and the associated checkbox `<label>` in `IndustryTopologyPanel.tsx`
- Remove `showEdgeLabels` prop from `TopologyCanvas` interface
- In `edgeStyle()`, replace `view.showEdgeLabels` with a check against `view.pinnedEdgeKeys`

### 2. Hover Tooltip (existing, polish)

Already functional via G6's `tooltip` plugin → `edgeTooltip()`.

Minor change: remove the hint "点击边固定查看关系详情" from `edgeTooltip()` since the actual click behavior now does pin.

### 3. Click to Pin / Unpin

**New state in `TopologyCanvas`:**

```ts
const [pinnedEdgeKeys, setPinnedEdgeKeys] = useState<Set<string>>(new Set())
const pinnedEdgeKeysRef = useRef<Set<string>>(new Set())  // mirror for G6 callbacks
```

**Modified `edge:click` handler:**

```ts
graph.on('edge:click', (event) => {
  const data = event.target?.data?.data
  if (!data) return
  const key = data.edgeKey
  // Toggle pin
  setPinnedEdgeKeys(prev => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })
  // Existing sidebar behavior preserved
  onSelectNodeRef.current?.(null)
  onSelectEdgeRef.current?.(data, key)
})
```

**Modified `edgeStyle()`:**

```ts
const isPinned = view.pinnedEdgeKeys.has(key)
const showLabel = isPinned || active || (view.zoom > 1.35 && touchesCenter)
```

When `isPinned` is true, `labelText` uses the adaptive label (see section 4) instead of the default truncated one.

**Pinned edge visual treatment:**
- Slightly thicker line (lineWidth: 2.5, same as hovered)
- Higher opacity (0.9)
- Label background: a distinct tint (e.g., `rgba(251, 191, 36, 0.15)` with amber border) to differentiate from hover tooltip

**ViewState extension:**

```ts
interface ViewState {
  // ... existing fields ...
  pinnedEdgeKeys: Set<string>
}
```

### 4. Adaptive Label Length on Node Drag

**Distance → character limit mapping:**

| Pixel Distance | Max Chars | Behavior |
|:---|:---|:---|
| < 120px | 6 | e.g. "上游供…" |
| 120–200px | 12 | e.g. "上游供应关系…" |
| 200–350px | 22 | e.g. "上游供应关系 · 芯片代工" |
| 350–550px | 40 | Near-full label |
| > 550px | 0 (no limit) | Full text |

**Helper functions:**

```ts
function distToMaxLen(dist: number): number {
  if (dist < 120) return 6
  if (dist < 200) return 12
  if (dist < 350) return 22
  if (dist < 550) return 40
  return 0  // no truncation
}

function adaptiveEdgeLabel(edge: TopologyEdgeData, dist: number): string {
  const fullText = edge.label || edge.evidence || edge.relation || ''
  const maxLen = distToMaxLen(dist)
  if (maxLen === 0) return fullText
  return shortText(fullText, maxLen)
}
```

**Integration in `node:dragend`:**

After updating `positionsRef`, iterate pinned edges and update their labels via G6's imperative API (no React re-render):

```ts
graph.on('node:dragend', (event) => {
  // ... existing position update (lines 819-828) ...

  // Adaptive labels for pinned edges
  const pinnedKeys = pinnedEdgeKeysRef.current
  if (pinnedKeys.size === 0 || graph.destroyed) return

  const edgeUpdates: Array<{ id: string; style: Record<string, unknown> }> = []
  for (const edge of rawEdgesRef.current) {
    const key = edgeKey(edge)
    if (!pinnedKeys.has(key)) continue
    try {
      const srcPos = graph.getElementPosition(edge.source)
      const tgtPos = graph.getElementPosition(edge.target)
      if (!srcPos || !tgtPos) continue
      const dist = Math.hypot(tgtPos[0] - srcPos[0], tgtPos[1] - srcPos[1])
      const visual = visualEdge(edge)
      edgeUpdates.push({
        id: `${visual.source}->${visual.target}:${edge.relation}`,
        data: { ...edge, edgeKey: key },
        style: {
          labelText: adaptiveEdgeLabel(edge, dist),
          labelFill: '#fbbf24',
        },
      })
    } catch {
      // position query best-effort
    }
  }
  if (edgeUpdates.length > 0 && !graph.destroyed) {
    graph.updateEdgeData(edgeUpdates as never)
    void graph.draw()
  }
})
```

### 5. Pin State Lifecycle

- **New topology started** → clear `pinnedEdgeKeys` (reset in the `applyGraph` flow, or via a `useEffect` watching `rawNodes` structural change)
- **Edge filtered out** → pin state preserved in Set; label naturally hidden because edge isn't rendered; reappears when filter clears
- **Node expand adds edges** → new edges start unpinned (not in Set)

### State Diagram

```
         hover
    ┌──────────────┐
    │  tooltip     │  (transient, G6 tooltip plugin)
    │  shows near  │
    │  cursor      │
    └──────┬───────┘
           │ mouseleave → tooltip hides
           
         click (edge not pinned)
    ┌──────────────┐
    │  label pins  │──→ sidebar opens
    │  on edge     │──→ label adapts on drag
    │  (persistent)│
    └──────┬───────┘
           │ click same edge again
           ▼
    ┌──────────────┐
    │  label hides │──→ sidebar closes
    │  (unpinned)  │
    └──────────────┘
```

## Files Changed

| File | Changes |
|:---|:---|
| `TopologyCanvas.tsx` | Add `pinnedEdgeKeys` state + ref; extend `ViewState`; modify `edgeStyle()` pinned check; add `edge:click` toggle logic; add adaptive label update in `node:dragend`; add helper `adaptiveEdgeLabel()` + `distToMaxLen()` |
| `IndustryTopologyPanel.tsx` | Remove `showEdgeLabels` state; remove checkbox UI; remove prop passthrough; clear pin state on new topology |
| `styles.css` | Add `.topo-g6-tip--edge` pinned state styling (amber-tinted label background for pinned edges) |

## Edge Cases

- **Node overlap (dist < 30px)**: Show minimal label (1–2 chars or empty) — edge is nearly invisible anyway
- **Graph reset / re-topology**: Clear `pinnedEdgeKeys` when `rawNodes` structural change is detected (new center node)
- **Canvas zoom**: Zoom changes don't affect pinned labels — they're always visible at their adaptive length; zoom only controls the non-pinned edge label threshold (existing `view.zoom > 1.35` rule unchanged)
- **Multiple pins**: All pinned edges get adaptive label updates on any node drag — O(pinned × edges) loop is fine for typical graph sizes (≤100 edges)
- **Performance**: `graph.updateEdgeData()` is a G6 imperative update — no React reconciliation. Only called when nodes are dragged (low frequency)
