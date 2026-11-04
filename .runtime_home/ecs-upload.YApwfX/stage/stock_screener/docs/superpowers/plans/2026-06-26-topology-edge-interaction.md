# Topology Canvas Edge Interaction Rework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the global "显示关系" checkbox with hover-to-inspect, click-to-pin edge labels that adapt their length to the pixel distance between connected nodes.

**Architecture:** All state lives in `TopologyCanvas` (pinnedEdgeKeys Set + ref, helper functions, adaptive label logic). `IndustryTopologyPanel` only loses code (checkbox + prop). No new dependencies — G6's `getElementPosition()` + `updateEdgeData()` handle dynamic label updates without React re-renders.

**Tech Stack:** React 18, TypeScript, @antv/g6 v5, existing `TopologyEdgeData`/`TopologyNodeData` types.

## Global Constraints

- Edge labels hidden by default — no global toggle
- Hover tooltip shows full edge details (already works via G6 tooltip plugin)
- Click toggles pin on edge label; sidebar DetailPanel behavior preserved
- Pinned labels adapt character length to source↔target pixel distance on node drag
- Multiple edges can be pinned simultaneously
- Pin state survives filter changes, resets on new topology

---

### Task 1: Add helper functions + extend ViewState

**Files:**
- Modify: `stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx`

**Interfaces:**
- Produces: `distToMaxLen(dist: number): number` — maps pixel distance to max character count
- Produces: `adaptiveEdgeLabel(edge: TopologyEdgeData, dist: number): string` — returns truncated label for given distance
- Produces: `ViewState.pinnedEdgeKeys: Set<string>` — new field on existing interface

- [ ] **Step 1: Add `distToMaxLen` and `adaptiveEdgeLabel` helper functions**

Add these two functions after the existing `edgeLabel()` function (around line 167), before `edgeKey()`:

```typescript
function distToMaxLen(dist: number): number {
  if (dist < 120) return 6
  if (dist < 200) return 12
  if (dist < 350) return 22
  if (dist < 550) return 40
  return 0  // no truncation — show full text
}

function adaptiveEdgeLabel(edge: TopologyEdgeData, dist: number): string {
  const fullText = edge.label || edge.evidence || edge.relation || ''
  const maxLen = distToMaxLen(dist)
  if (maxLen === 0) return fullText
  return shortText(fullText, maxLen)
}
```

- [ ] **Step 2: Add `pinnedEdgeKeys` to `ViewState` interface**

In the `ViewState` interface (lines 40-52), add the new field:

```typescript
interface ViewState {
  zoom: number
  hoveredNodeId?: string | null
  hoveredEdgeKey?: string | null
  selectedNodeId?: string | null
  selectedEdgeKey?: string | null
  focusNodeId?: string | null
  relatedNodeIds: Set<string>
  relatedEdgeKeys: Set<string>
  cycleEdgeKeys: Set<string>
  showEdgeLabels: boolean
  highlightCycles: boolean
  pinnedEdgeKeys: Set<string>  // <-- NEW
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd stock_screener/web_frontend && npx tsc --noEmit 2>&1 | head -20`

Expected: no new errors from these additions (the new field is not yet consumed, so it may warn about unused — that's fine).

- [ ] **Step 4: Commit**

```bash
git add stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx
git commit -m "feat: add adaptive edge label helpers + extend ViewState

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Add pinnedEdgeKeys state + integrate into edge:click and edgeStyle

**Files:**
- Modify: `stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx`

**Interfaces:**
- Consumes: `ViewState.pinnedEdgeKeys` (from Task 1)
- Consumes: `adaptiveEdgeLabel()` (from Task 1)
- Produces: `pinnedEdgeKeys` React state + `pinnedEdgeKeysRef` ref

- [ ] **Step 1: Add `pinnedEdgeKeys` state and ref**

In the component body (after line 615, alongside other `useState` declarations), add:

```typescript
const [pinnedEdgeKeys, setPinnedEdgeKeys] = useState<Set<string>>(new Set())
const pinnedEdgeKeysRef = useRef<Set<string>>(new Set())
```

- [ ] **Step 2: Sync ref with state**

After the existing ref sync block (around line 622, after `onSelectEdgeRef.current = onSelectEdge`), add:

```typescript
pinnedEdgeKeysRef.current = pinnedEdgeKeys
```

- [ ] **Step 3: Update `useMemo` for `view` to include `pinnedEdgeKeys`**

In the `useMemo` that builds `view` (lines 625-641), add `pinnedEdgeKeys` to both the returned object and the dependency array:

```typescript
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
    highlightCycles,
    pinnedEdgeKeys,           // <-- NEW
    cycleEdgeKeys: detectCycleEdges(rawEdges),
    ...related,
  }
}, [focusNodeId, highlightCycles, hoveredEdgeKey, hoveredNodeId, rawEdges, rawNodes, selectedEdgeKey, selectedNodeId, showEdgeLabels, zoom, pinnedEdgeKeys])  // <-- pinnedEdgeKeys added
```

- [ ] **Step 4: Update `edgeStyle()` to check pinned state**

In `edgeStyle()` (lines 184-209), replace the `showLabel` line. Change:

```typescript
const showLabel = view.showEdgeLabels || active || (view.zoom > 1.35 && touchesCenter)
```

To:

```typescript
const isPinned = view.pinnedEdgeKeys.has(key)
const showLabel = isPinned || active || (view.zoom > 1.35 && touchesCenter)
```

Then update the pinned label visual treatment. Replace the `labelText` line:

```typescript
labelText: showLabel ? (cyclic ? `环路 · ${edgeLabel(edge)}` : edgeLabel(edge)) : '',
```

With a version that uses adaptive label for pinned edges and amber tinting:

```typescript
labelText: showLabel
  ? (cyclic
    ? `环路 · ${isPinned ? adaptiveEdgeLabel(edge, 999) : edgeLabel(edge)}`
    : isPinned
      ? adaptiveEdgeLabel(edge, 999)  // placeholder; real distance computed on drag
      : edgeLabel(edge))
  : '',
labelFill: isPinned ? '#fbbf24' : '#dbeafe',
labelBackgroundFill: isPinned ? 'rgba(251, 191, 36, 0.15)' : 'rgba(15, 23, 42, 0.78)',
```

And update lineWidth/strokeOpacity to treat pinned like active:

```typescript
lineWidth: cyclic ? 3 : (active || isPinned) ? 2.5 : 1,
strokeOpacity: hasFocus && !active && !cyclic && !isPinned ? 0.03 : (active || cyclic || isPinned) ? 0.9 : 0.15,
```

- [ ] **Step 5: Rewrite `edge:click` handler to toggle pin**

Replace the existing `edge:click` handler (lines 830-835):

```typescript
graph.on('edge:click', (event: unknown) => {
  const data = (event as { target?: { data?: { data?: TopologyEdgeData & { edgeKey?: string } } } }).target?.data?.data
  if (!data) return
  const key = data.edgeKey
  if (!key) return
  // Toggle pin
  setPinnedEdgeKeys((prev) => {
    const next = new Set(prev)
    if (next.has(key)) {
      next.delete(key)
    } else {
      next.add(key)
    }
    return next
  })
  // Existing sidebar behavior preserved
  onSelectNodeRef.current?.(null)
  onSelectEdgeRef.current?.(data, key)
})
```

- [ ] **Step 6: Verify TypeScript and build**

Run: `cd stock_screener/web_frontend && npx tsc --noEmit 2>&1 | head -30`

Expected: no new type errors.

- [ ] **Step 7: Commit**

```bash
git add stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx
git commit -m "feat: add pinnedEdgeKeys state, toggle on edge click, pinned label styling

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Add adaptive label update on node drag

**Files:**
- Modify: `stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx`

**Interfaces:**
- Consumes: `pinnedEdgeKeysRef` (from Task 2)
- Consumes: `adaptiveEdgeLabel()`, `distToMaxLen()` (from Task 1)
- Consumes: `visualEdge()`, `edgeKey()` (existing)

- [ ] **Step 1: Extend `node:dragend` handler with adaptive label logic**

Replace the existing `node:dragend` handler (lines 819-828):

```typescript
graph.on('node:dragend', (event: unknown) => {
  const id = (event as { target?: { id?: string } }).target?.id
  if (!id || graph.destroyed) return
  try {
    const [x, y] = graph.getElementPosition(id) as [number, number]
    positionsRef.current.set(id, { x, y })
  } catch {
    /* position is best-effort only */
  }

  // Adaptive labels for pinned edges after drag
  const pinnedKeys = pinnedEdgeKeysRef.current
  if (pinnedKeys.size === 0) return

  const edgeUpdates: Array<{ id: string; data: Record<string, unknown>; style: Record<string, unknown> }> = []
  for (const edge of rawEdgesRef.current) {
    const key = edgeKey(edge)
    if (!pinnedKeys.has(key)) continue
    try {
      const srcPos = graph.getElementPosition(edge.source) as [number, number] | null
      const tgtPos = graph.getElementPosition(edge.target) as [number, number] | null
      if (!srcPos || !tgtPos) continue
      const dist = Math.hypot(tgtPos[0] - srcPos[0], tgtPos[1] - srcPos[1])
      const visual = visualEdge(edge)
      edgeUpdates.push({
        id: `${visual.source}->${visual.target}:${edge.relation}`,
        data: { ...edge, edgeKey: key } as unknown as Record<string, unknown>,
        style: {
          labelText: adaptiveEdgeLabel(edge, dist),
          labelFill: '#fbbf24',
          labelBackgroundFill: 'rgba(251, 191, 36, 0.15)',
        },
      })
    } catch {
      /* position query best-effort */
    }
  }
  if (edgeUpdates.length > 0) {
    graph.updateEdgeData(edgeUpdates as never)
    void graph.draw()
  }
})
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd stock_screener/web_frontend && npx tsc --noEmit 2>&1 | head -30`

Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx
git commit -m "feat: adaptive pinned edge labels on node drag via distance-to-chars mapping

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Clear pinnedEdgeKeys on topology reset

**Files:**
- Modify: `stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx`

**Interfaces:**
- Consumes: `setPinnedEdgeKeys` (from Task 2)
- Consumes: `rawNodes` prop — structural change detection

- [ ] **Step 1: Add useEffect to clear pins when topology resets**

Add a new `useEffect` after the existing toolbar plugin update effect (after line 875, before the main data-sync effect at line 877):

```typescript
// Clear pinned edge labels when topology fundamentally changes (new center / reset)
useEffect(() => {
  const centerId = rawNodes.find((node) => node.is_center)?.id || null
  // If center changed or nodes went empty, reset pins
  if (rawNodes.length === 0 || (centerId !== null && centerId !== centerIdRef.current)) {
    setPinnedEdgeKeys(new Set())
  }
}, [rawNodes])
```

> **Why center ID check:** `centerIdRef.current` is set in the `layoutPositions` useMemo (line 708). When a new topology starts, the center node ID changes → pins clear. Node expansion (same center) preserves pins. Filter changes don't affect `rawNodes` at all — they only affect `visibleGraph` in the parent.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd stock_screener/web_frontend && npx tsc --noEmit 2>&1 | head -30`

Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx
git commit -m "feat: clear pinnedEdgeKeys on topology reset (center node change)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Remove showEdgeLabels from IndustryTopologyPanel

**Files:**
- Modify: `stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx`
- Modify: `stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx` (remove prop)

**Interfaces:**
- Consumes: `Props.showEdgeLabels` (to be removed from TopologyCanvas)
- Removes: `showEdgeLabels` state, checkbox UI, prop passthrough

- [ ] **Step 1: Remove `showEdgeLabels` state from IndustryTopologyPanel**

In `IndustryTopologyPanel` (line 233), delete:

```typescript
const [showEdgeLabels, setShowEdgeLabels] = useState(false)
```

- [ ] **Step 2: Remove the checkbox UI**

In the filter bar JSX (lines 739-741), delete the checkbox line:

```html
<label className="topo-check"><input type="checkbox" checked={showEdgeLabels} onChange={(event) => setShowEdgeLabels(event.target.checked)} />显示关系</label>
```

*(Delete only the "显示关系" checkbox; keep the other two checkboxes "只看重点" and "高亮环路".)*

- [ ] **Step 3: Remove `showEdgeLabels` prop from TopologyCanvas usage**

In the `<TopologyCanvas` JSX (lines 745-770), remove the line:

```typescript
showEdgeLabels={showEdgeLabels}
```

- [ ] **Step 4: Remove `showEdgeLabels` from TopologyCanvas Props interface**

In `TopologyCanvas.tsx`, remove from the `Props` interface (line 18):

```typescript
// Delete this line:
showEdgeLabels?: boolean
```

- [ ] **Step 5: Remove `showEdgeLabels` from TopologyCanvas destructuring**

In the component function signature (line 591), remove the parameter:

```typescript
// Change:
showEdgeLabels = false,
// To: (delete the line entirely)
```

- [ ] **Step 6: Remove `showEdgeLabels` from ViewState and its usage**

In the `ViewState` interface, remove the field:

```typescript
// Delete this line:
showEdgeLabels: boolean
```

In the `useMemo` that builds view, remove `showEdgeLabels` from the returned object:

```typescript
// Delete this line from the return block:
showEdgeLabels,
```

And remove `showEdgeLabels` from the dependency array.

- [ ] **Step 7: Verify TypeScript and build**

Run: `cd stock_screener/web_frontend && npx tsc --noEmit 2>&1 | head -30`

Expected: no errors. If there's a reference to `showEdgeLabels` anywhere else, fix it (the `edgeStyle()` function now uses `view.pinnedEdgeKeys.has(key)` instead).

- [ ] **Step 8: Commit**

```bash
git add stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx
git commit -m "refactor: remove global showEdgeLabels checkbox, edges hidden by default

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Update edgeTooltip hint + add pinned label CSS

**Files:**
- Modify: `stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx` (tooltip hint)
- Modify: `stock_screener/web_frontend/src/styles.css` (pinned label styles)

- [ ] **Step 1: Remove the obsolete hint from `edgeTooltip()`**

In `edgeTooltip()` (lines 441-458), find and remove the hint line:

```html
<div class="topo-g6-tip-hint">点击边固定查看关系详情</div>
```

The updated `edgeTooltip()` return:

```typescript
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
    </div>
  `
}
```

- [ ] **Step 2: Add pinned edge label CSS class**

In `styles.css`, after the existing `.topo-g6-tip-hint` block (around line 4864), add a style block for pinned edge labels. G6 renders edge labels as SVG `<text>` elements inside a `<g>` — the `labelBackgroundFill` we set in `edgeStyle()` handles the inline styling. Add a CSS comment block for reference:

```css
/* Pinned edge labels — amber-tinted via inline style in edgeStyle().
   .topo-g6-tip--edge is used in the hover tooltip, not the edge label itself. */
```

*(The amber styling is already applied via `labelFill` + `labelBackgroundFill` in `edgeStyle()` from Task 2, Step 4. No additional CSS selectors needed for G6 edge labels since G6 renders them as canvas/SVG elements with inline styles.)*

- [ ] **Step 3: Verify build**

Run: `cd stock_screener/web_frontend && npm run build 2>&1 | tail -10`

Expected: `vite vX.X.X building for production...` completes without errors.

- [ ] **Step 4: Commit**

```bash
git add stock_screener/web_frontend/src/features/industryTopology/TopologyCanvas.tsx stock_screener/web_frontend/src/styles.css
git commit -m "chore: remove obsolete tooltip hint, add pinned label CSS note

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Build verification

**Files:**
- None (verification only)

- [ ] **Step 1: Full production build**

```bash
cd stock_screener/web_frontend && npm run build 2>&1
```

Expected: build succeeds with no errors, no warnings about unused imports/variables related to `showEdgeLabels`.

- [ ] **Step 2: TypeScript check**

```bash
cd stock_screener/web_frontend && npx tsc --noEmit 2>&1
```

Expected: zero errors.

- [ ] **Step 3: Python compile check (backend unchanged, but verify no regressions)**

```bash
cd stock_screener && python3 -m py_compile web/topology.py db.py 2>&1
```

Expected: no output (success).

- [ ] **Step 4: Manual E2E smoke test checklist**

Start the dev server and verify:

1. **No checkbox** — "显示关系" checkbox absent from filter bar
2. **Edges hidden** — No edge labels visible on canvas by default
3. **Hover tooltip** — Hovering an edge shows tooltip with: relation label, source node name+code, target node name+code, direction, relation type, evidence description
4. **Click to pin** — Clicking an edge: label appears on the edge line itself (amber colored), sidebar DetailPanel opens with edge details
5. **Multiple pins** — Click three different edges → all three show pinned labels
6. **Click to unpin** — Click a pinned edge → label disappears, sidebar closes
7. **Drag adaptation** — Pin an edge, drag one of its endpoint nodes far away → label expands to show more text; drag nodes close together → label truncates
8. **Filter persistence** — Pin edges, then toggle relation filters (上游/下游/同业) → pins survive; edges hidden by filter naturally don't show labels; re-enable filter → labels reappear
9. **Topology reset** — Click "开始拓扑" with a new stock → all pins cleared, edges hidden
10. **Node expand** — Double-click a node to expand → new edges appear unpinned; existing pinned edges keep their pins

- [ ] **Step 5: Commit (if any final fixes needed)**

```bash
git add -A
git commit -m "chore: final verification fixes for edge interaction rework

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
