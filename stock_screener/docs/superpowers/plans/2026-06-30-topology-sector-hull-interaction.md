# Topology Sector Hull Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make sector hull regions follow dragged member nodes and reduce overlap between sector regions in the summary topology view.

**Architecture:** Keep all behavior frontend-only in `TopologyCanvas.tsx`. Use G6 Hull plugin for region rendering, actively refresh hull members during node dragging, and upgrade the deterministic layout so nodes are placed by zone first and sector group second.

**Tech Stack:** React, TypeScript, AntV G6 5.1.1, existing deterministic topology layout.

---

### Task 1: Add Hull Refresh Helpers

**Files:**
- Modify: `web_frontend/src/features/industryTopology/TopologyCanvas.tsx`

- [ ] **Step 1: Add a helper that refreshes current G6 hull plugins**

Add a helper near `sectorHullPlugins`:

```ts
function refreshSectorHulls(graph: G6Graph, nodes: TopologyRenderNodeData[]) {
  sectorHullGroups(nodes).forEach((group) => {
    const hull = graph.getPluginInstance(sectorHullKey(group.sector)) as { updateMember?: (members: string[]) => void } | undefined
    hull?.updateMember?.(group.memberIds)
  })
}
```

- [ ] **Step 2: Call the helper while dragging and after drag ends**

In `TopologyCanvas.tsx`, add `node:drag` handler that calls `refreshSectorHulls(graph, rawNodesRef.current)`. In existing `node:dragend`, call the helper after saving the new node position.

- [ ] **Step 3: Verify build**

Run: `npm run build` from `web_frontend`.
Expected: TypeScript and Vite build pass.

### Task 2: Sector-Aware Initial Layout

**Files:**
- Modify: `web_frontend/src/features/industryTopology/TopologyCanvas.tsx`

- [ ] **Step 1: Add sector group helpers for layout**

Add helpers that group unplaced nodes by `displaySector(node)` and preserve current zone behavior.

- [ ] **Step 2: Replace per-zone flat placement with sector bands**

For upstream/downstream, split the angular sector into sub-sectors per display sector and place each sector's nodes inside its own band with spacing. For peer nodes, split into left/right columns by sector with vertical offsets.

- [ ] **Step 3: Keep aggregate representatives near their aggregate bucket**

Keep the existing `placeAggregateRepresentatives(nodes, positions)` post-pass so representative nodes stay visually tied to the bucket.

- [ ] **Step 4: Verify build**

Run: `npm run build` from `web_frontend`.
Expected: TypeScript and Vite build pass.

### Task 3: Project Graph and Commit

**Files:**
- Modify: `graphify-out/*`

- [ ] **Step 1: Update graphify**

Run: `graphify update .` from `stock_screener`.
Expected: `graph.json` and report refresh; HTML visualization may be skipped due graph size.

- [ ] **Step 2: Commit**

Run:

```bash
git add web_frontend/src/features/industryTopology/TopologyCanvas.tsx graphify-out/.graphify_labels.json graphify-out/GRAPH_REPORT.md graphify-out/graph.json graphify-out/manifest.json docs/superpowers/plans/2026-06-30-topology-sector-hull-interaction.md
git commit -m "fix: improve topology sector hull interactions"
```

Expected: commit succeeds on `main`.

---

Self-review:
- Covers drag-follow behavior and overlap reduction.
- Keeps changes frontend-only.
- Uses G6 Hull plugin rather than custom drawing.
- No backend/API/schema change.
