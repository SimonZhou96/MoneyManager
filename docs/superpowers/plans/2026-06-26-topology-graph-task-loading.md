# Topology Graph Task Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a task-based topology graph loading flow so the frontend renders a center-node skeleton immediately and polls for progressively richer graph snapshots.

**Architecture:** Add a focused initial-only graph path in `TopologyService`, then add an in-memory task registry and task endpoints in the topology web layer. The React panel creates a task, renders the returned skeleton graph, and polls the task snapshot endpoint until a terminal stage.

**Tech Stack:** Python 3, FastAPI, unittest, React 18, TypeScript, Vite.

---

## File Structure

- Modify `stock_screener/industry_topology/service.py`: add a public `build_initial_graph_only(...)` method that reuses existing resolver, LLM initial snapshot, cache save, and `_assemble` behavior without synchronously inferring stale sources.
- Create `stock_screener/web/topology_tasks.py`: define task stages, task record storage, skeleton graph construction, registry operations, and background runner.
- Modify `stock_screener/web/topology.py`: add task request/response models and `/graph/tasks` routes; keep existing `/graph` behavior intact.
- Modify `stock_screener/tests/test_industry_topology.py`: add service tests for initial-only graph behavior and task registry/route tests.
- Modify `stock_screener/web_frontend/src/features/industryTopology/types.ts`: add topology task response types and stage union.
- Modify `stock_screener/web_frontend/src/features/industryTopology/topologyApi.ts`: add create/read/cancel task API methods.
- Modify `stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx`: replace initial graph request with task creation and task polling while preserving existing stale task guards and quote refresh behavior.

## Task 1: Initial-Only Service Path

**Files:**
- Modify: `stock_screener/industry_topology/service.py`
- Test: `stock_screener/tests/test_industry_topology.py`

- [ ] **Step 1: Add a failing service test that proves initial-only graph avoids stale-source inference**

Add this test method inside `class TestTopologyService(unittest.TestCase):` in `stock_screener/tests/test_industry_topology.py`.

```python
    def test_build_initial_graph_only_does_not_infer_stale_sources(self):
        cache = FakeCache({})
        llm = SequenceLLMProvider([
            {
                "resolved": True,
                "market": "US",
                "code": "NVDA",
                "name": "英伟达",
                "aliases": ["NVIDIA"],
                "sector": "AI芯片",
                "industry": "半导体",
                "reason": "resolved",
                "confidence": 0.9,
            },
            {
                "center": {
                    "resolved": True,
                    "market": "US",
                    "code": "NVDA",
                    "name": "英伟达",
                    "aliases": ["NVIDIA"],
                    "sector": "AI芯片",
                    "industry": "半导体",
                    "reason": "resolved",
                    "confidence": 0.9,
                },
                "items": [
                    {
                        "code": "TSM",
                        "name": "台积电",
                        "market": "US",
                        "direction": "upstream",
                        "relation": "foundry_packaging",
                        "evidence": "先进制程代工",
                        "sector": "晶圆代工",
                        "industry": "半导体",
                    }
                ],
                "warnings": [],
            },
        ])
        svc = TopologyService(FakeDB(), llm_provider=llm)
        svc.cache = cache
        svc.resolver = FakeResolver2()

        result = svc.build_initial_graph_only("NVDA", "US", depth=3, center_name="NVIDIA")

        self.assertEqual(result["stats"]["data_stage"], "llm_initial")
        self.assertEqual(result["stats"]["relation_status"], "initial_ready")
        self.assertEqual(result["stats"]["llm_calls"], 1)
        self.assertEqual(llm.calls, 2)
        self.assertEqual([item[0] for item in cache.saved], ["NVDA"])
        self.assertGreaterEqual(len(result["nodes"]), 2)
        self.assertTrue(any(node["id"] == "US:TSM" for node in result["nodes"]))
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```bash
rtk python -m unittest stock_screener.tests.test_industry_topology.TestTopologyService.test_build_initial_graph_only_does_not_infer_stale_sources -v
```

Expected: FAIL with `AttributeError: 'TopologyService' object has no attribute 'build_initial_graph_only'`.

- [ ] **Step 3: Add `build_initial_graph_only` to `TopologyService`**

In `stock_screener/industry_topology/service.py`, add this method after `build_graph`.

```python
    def build_initial_graph_only(self, code: str, market: str, depth: int = 3, center_name: str = "") -> Dict[str, Any]:
        market = normalize_market(market)
        center_info = self._resolver_resolve(code, market, include_quote=False, fallback_name=center_name)
        resolved_code = center_info.get("code") or code
        center_name_value = center_info.get("name", "") or center_name or resolved_code
        center_sector = center_info.get("sector", "") or "--"
        initial = self._build_initial_snapshot(
            resolved_code,
            market,
            center_name=center_name_value,
            center_sector=center_sector,
        )
        if not initial:
            raise RuntimeError("LLM 首屏拓扑生成失败")
        center_payload = initial.get("center") if isinstance(initial.get("center"), dict) else {}
        initial_relations = list(initial.get("relations") or [])
        if not initial_relations:
            raise RuntimeError("LLM 首屏拓扑未返回任何关系节点")

        llm_node_data = self._llm_node_data_from_relations(initial_relations)
        llm_node_data[symbol_id(market, resolved_code)] = center_payload
        center_info = self._merge_node_info(center_info, center_payload, prefer_llm=True)
        cached_map = {resolved_code: initial_relations}
        try:
            p_name, m_name = self._provider_meta()
            self.cache.save_relations(resolved_code, market, initial_relations, provider=p_name, model=m_name)
        except Exception:
            pass

        graph = self.cache.build_graph(cached_map)
        reachable = {resolved_code}
        if resolved_code in graph:
            reachable.update(self.cache.reachable_within(graph, resolved_code, depth))
        for src, rels in cached_map.items():
            for rel in rels:
                if rel.is_empty:
                    continue
                reachable.add(src)
                reachable.add(rel.peer_code)

        nodes, edges = self._assemble(
            resolved_code,
            market,
            list(reachable),
            cached_map,
            existing_set=set(),
            is_center=True,
            center_info=center_info,
            graph=graph,
            include_quote=False,
            llm_node_data=llm_node_data,
            prefer_llm=True,
            data_stage="llm_initial",
        )
        stats = {
            "llm_calls": self._llm_calls,
            "cached_nodes": len(cached_map),
            "stale_nodes": 0,
            "stale_sources": [],
            "depth": depth,
            "relation_status": "initial_ready",
            "data_stage": "llm_initial",
        }
        return {
            "center": self._to_node(
                center_info,
                resolved_code,
                market,
                expanded=True,
                is_center=True,
                stale=False,
                depth=0,
                zone="center",
                data_stage="llm_initial",
            ),
            "nodes": nodes,
            "edges": edges,
            "stats": stats,
            "warnings": list(initial.get("warnings") or []),
        }
```

- [ ] **Step 4: Run the service test and verify it passes**

Run:

```bash
rtk python -m unittest stock_screener.tests.test_industry_topology.TestTopologyService.test_build_initial_graph_only_does_not_infer_stale_sources -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add stock_screener/industry_topology/service.py stock_screener/tests/test_industry_topology.py
rtk git commit -m "feat: add initial-only topology graph builder"
```

## Task 2: Backend Task Registry And Routes

**Files:**
- Create: `stock_screener/web/topology_tasks.py`
- Modify: `stock_screener/web/topology.py`
- Test: `stock_screener/tests/test_industry_topology.py`

- [ ] **Step 1: Add failing tests for skeleton creation, task success, cancellation, and failure**

Add these imports near the existing `from web.topology import (...)` block in `stock_screener/tests/test_industry_topology.py`.

```python
from web.topology_tasks import TopologyTaskRegistry, TopologyTaskRunner
```

Add this test class after `TestTopologyQuotePayload`.

```python
class FakeTaskService:
    def __init__(self, graph=None, enrich=None, error=None):
        self.graph = graph or {
            "center": {"id": "US:NVDA", "code": "NVDA", "market": "US", "name": "NVIDIA", "data_stage": "llm_initial"},
            "nodes": [
                {"id": "US:NVDA", "code": "NVDA", "market": "US", "name": "NVIDIA", "is_center": True, "data_stage": "llm_initial"},
                {"id": "US:TSM", "code": "TSM", "market": "US", "name": "TSMC", "is_center": False, "data_stage": "llm_initial"},
            ],
            "edges": [{"source": "US:NVDA", "target": "US:TSM", "direction": "upstream", "relation": "foundry_packaging", "label": "代工"}],
            "stats": {"relation_status": "initial_ready", "data_stage": "llm_initial"},
            "warnings": [],
        }
        self.enrich = enrich or {"items": [], "warnings": []}
        self.error = error

    def build_initial_graph_only(self, code, market, depth=3, center_name=""):
        if self.error:
            raise self.error
        return self.graph

    def search_enrich(self, *, center, symbols):
        return self.enrich


class TestTopologyTaskRegistry(unittest.TestCase):
    def test_create_task_returns_center_skeleton(self):
        registry = TopologyTaskRegistry(ttl_seconds=60)

        task = registry.create_task(code="NVDA", market="US", depth=3, center_name="NVIDIA", quote_mode="llm_initial")

        self.assertTrue(task.task_id.startswith("topo_"))
        self.assertEqual(task.stage, "queued")
        self.assertEqual(task.progress_pct, 5)
        self.assertEqual(task.graph["center"]["id"], "US:NVDA")
        self.assertEqual(task.graph["nodes"][0]["data_stage"], "skeleton")

    def test_runner_reaches_done_with_initial_graph(self):
        registry = TopologyTaskRegistry(ttl_seconds=60)
        task = registry.create_task(code="NVDA", market="US", depth=3, center_name="NVIDIA", quote_mode="llm_initial")
        runner = TopologyTaskRunner(registry, lambda: FakeTaskService())

        runner.run(task.task_id)
        snapshot = registry.get_snapshot(task.task_id)

        self.assertEqual(snapshot["stage"], "done")
        self.assertEqual(snapshot["progress_pct"], 100)
        self.assertEqual(len(snapshot["graph"]["nodes"]), 2)
        self.assertEqual(snapshot["graph"]["stats"]["relation_status"], "initial_ready")

    def test_runner_failure_preserves_skeleton(self):
        registry = TopologyTaskRegistry(ttl_seconds=60)
        task = registry.create_task(code="NVDA", market="US", depth=3, center_name="NVIDIA", quote_mode="llm_initial")
        runner = TopologyTaskRunner(registry, lambda: FakeTaskService(error=RuntimeError("boom")))

        runner.run(task.task_id)
        snapshot = registry.get_snapshot(task.task_id)

        self.assertEqual(snapshot["stage"], "failed")
        self.assertEqual(snapshot["error"]["code"], "initial_graph_failed")
        self.assertEqual(snapshot["graph"]["nodes"][0]["data_stage"], "skeleton")

    def test_cancel_prevents_runner_updates(self):
        registry = TopologyTaskRegistry(ttl_seconds=60)
        task = registry.create_task(code="NVDA", market="US", depth=3, center_name="NVIDIA", quote_mode="llm_initial")
        registry.cancel(task.task_id)
        runner = TopologyTaskRunner(registry, lambda: FakeTaskService())

        runner.run(task.task_id)
        snapshot = registry.get_snapshot(task.task_id)

        self.assertEqual(snapshot["stage"], "cancelled")
        self.assertEqual(snapshot["progress_pct"], 100)
        self.assertEqual(snapshot["graph"]["nodes"][0]["data_stage"], "skeleton")
```

- [ ] **Step 2: Run tests and verify they fail because the module does not exist**

Run:

```bash
rtk python -m unittest stock_screener.tests.test_industry_topology.TestTopologyTaskRegistry -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'web.topology_tasks'`.

- [ ] **Step 3: Create the task registry and runner**

Create `stock_screener/web/topology_tasks.py` with this content.

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import threading
import uuid
from typing import Any, Callable, Dict, Optional

from industry_topology.symbols import symbol_id

TERMINAL_STAGES = {"done", "partial", "failed", "cancelled", "expired"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TopologyTask:
    task_id: str
    code: str
    market: str
    depth: int
    center_name: str
    quote_mode: str
    stage: str
    progress_pct: int
    graph: Dict[str, Any]
    message: str
    warnings: list[str] = field(default_factory=list)
    error: Optional[Dict[str, str]] = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    expires_at: datetime = field(default_factory=lambda: utc_now() + timedelta(minutes=20))


class TopologyTaskRegistry:
    def __init__(self, ttl_seconds: int = 1200):
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._tasks: Dict[str, TopologyTask] = {}

    def create_task(self, *, code: str, market: str, depth: int, center_name: str, quote_mode: str) -> TopologyTask:
        now = utc_now()
        task_id = f"topo_{uuid.uuid4().hex}"
        task = TopologyTask(
            task_id=task_id,
            code=code,
            market=market,
            depth=depth,
            center_name=center_name,
            quote_mode=quote_mode,
            stage="queued",
            progress_pct=5,
            graph=build_skeleton_graph(code=code, market=market, center_name=center_name, depth=depth),
            message="Task created",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        with self._lock:
            self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> Optional[TopologyTask]:
        with self._lock:
            self.cleanup()
            return self._tasks.get(task_id)

    def get_snapshot(self, task_id: str) -> Optional[Dict[str, Any]]:
        task = self.get(task_id)
        if task is None:
            return None
        return task_to_payload(task)

    def update(
        self,
        task_id: str,
        *,
        stage: str,
        progress_pct: int,
        message: str,
        graph: Optional[Dict[str, Any]] = None,
        warnings: Optional[list[str]] = None,
        error: Optional[Dict[str, str]] = None,
    ) -> Optional[TopologyTask]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.stage in TERMINAL_STAGES:
                return task
            task.stage = stage
            task.progress_pct = progress_pct
            task.message = message
            task.updated_at = utc_now()
            task.expires_at = task.updated_at + timedelta(seconds=self.ttl_seconds)
            if graph is not None:
                task.graph = graph
            if warnings:
                task.warnings = sorted(set([*task.warnings, *warnings]))
            task.error = error
            return task

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.stage = "cancelled"
            task.progress_pct = 100
            task.message = "Task cancelled"
            task.updated_at = utc_now()
            return True

    def cleanup(self) -> None:
        now = utc_now()
        expired_ids = [
            task_id
            for task_id, task in self._tasks.items()
            if task.expires_at <= now and task.stage != "initial_graph_running"
        ]
        for task_id in expired_ids:
            self._tasks.pop(task_id, None)


class TopologyTaskRunner:
    def __init__(self, registry: TopologyTaskRegistry, service_factory: Callable[[], Any]):
        self.registry = registry
        self.service_factory = service_factory

    def run(self, task_id: str) -> None:
        task = self.registry.get(task_id)
        if task is None or task.stage == "cancelled":
            return
        self.registry.update(task_id, stage="initial_graph_running", progress_pct=20, message="Building initial graph")
        task = self.registry.get(task_id)
        if task is None or task.stage == "cancelled":
            return
        try:
            service = self.service_factory()
            graph = service.build_initial_graph_only(task.code, task.market, depth=task.depth, center_name=task.center_name)
            warnings = list(graph.get("warnings") or [])
            self.registry.update(
                task_id,
                stage="initial_graph_ready",
                progress_pct=55,
                message="Initial graph ready",
                graph=graph,
                warnings=warnings,
            )
            task = self.registry.get(task_id)
            if task is None or task.stage == "cancelled":
                return
            self.registry.update(task_id, stage="enriching", progress_pct=70, message="Enriching graph")
            self._run_search_enrich(task_id, service)
            task = self.registry.get(task_id)
            if task is None or task.stage == "cancelled":
                return
            self.registry.update(task_id, stage="done", progress_pct=100, message="Topology graph ready")
        except Exception as exc:
            self.registry.update(
                task_id,
                stage="failed",
                progress_pct=100,
                message="Initial graph failed",
                error={"code": "initial_graph_failed", "message": str(exc) or "LLM initial graph generation failed"},
            )

    def _run_search_enrich(self, task_id: str, service: Any) -> None:
        task = self.registry.get(task_id)
        if task is None:
            return
        graph = task.graph
        try:
            center = graph.get("center") or {}
            symbols = [node.get("id") for node in graph.get("nodes") or [] if node.get("id")]
            enriched = service.search_enrich(center=center, symbols=symbols)
        except Exception:
            self.registry.update(task_id, stage="enriching", progress_pct=75, message="Search enrichment unavailable", warnings=["search_unavailable"])
            return
        items = enriched.get("items") or []
        warnings = list(enriched.get("warnings") or [])
        if items:
            graph = merge_node_patches(graph, items)
        self.registry.update(task_id, stage="source_polling", progress_pct=85, message="Checking source relations", graph=graph, warnings=warnings)


def build_skeleton_graph(*, code: str, market: str, center_name: str, depth: int) -> Dict[str, Any]:
    center = {
        "id": symbol_id(market, code),
        "code": code,
        "market": market,
        "name": center_name or code,
        "sector": "板块未知",
        "industry": "",
        "pct_chg": None,
        "market_cap": None,
        "market_cap_str": "--",
        "size_level": 6,
        "quote_status": "pending",
        "quote_updated_at": None,
        "quote_error": "",
        "field_sources": {},
        "field_confidence": {},
        "data_gaps": ["sector", "industry", "market_cap", "pct_chg"],
        "data_stage": "skeleton",
        "expanded": False,
        "stale": False,
        "is_center": True,
        "depth": 0,
        "zone": "center",
    }
    return {
        "center": center,
        "nodes": [center],
        "edges": [],
        "stats": {
            "llm_calls": 0,
            "cached_nodes": 0,
            "stale_nodes": 0,
            "stale_sources": [],
            "depth": depth,
            "relation_status": "pending",
            "data_stage": "skeleton",
        },
        "warnings": [],
    }


def merge_node_patches(graph: Dict[str, Any], patches: list[Dict[str, Any]]) -> Dict[str, Any]:
    patch_by_id = {patch.get("id"): patch for patch in patches if patch.get("id")}
    nodes = []
    for node in graph.get("nodes") or []:
        patch = patch_by_id.get(node.get("id"))
        nodes.append({**node, **patch} if patch else node)
    center = graph.get("center") or {}
    center_patch = patch_by_id.get(center.get("id"))
    return {**graph, "center": {**center, **center_patch} if center_patch else center, "nodes": nodes}


def task_to_payload(task: TopologyTask) -> Dict[str, Any]:
    return {
        "task_id": task.task_id,
        "stage": task.stage,
        "progress_pct": task.progress_pct,
        "graph": task.graph,
        "message": task.message,
        "warnings": task.warnings,
        "error": task.error,
        "updated_at": task.updated_at.isoformat(),
    }
```

- [ ] **Step 4: Run registry tests and verify they pass**

Run:

```bash
rtk python -m unittest stock_screener.tests.test_industry_topology.TestTopologyTaskRegistry -v
```

Expected: PASS.

- [ ] **Step 5: Add task routes to `web/topology.py`**

Modify the import section in `stock_screener/web/topology.py`.

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
```

Add this import after the existing local imports.

```python
from .topology_tasks import TopologyTaskRegistry, TopologyTaskRunner, task_to_payload
```

Add this module-level registry after `_TOPOLOGY_BATCH_SIZE`.

```python
_TOPOLOGY_TASKS = TopologyTaskRegistry()
```

Add these routes after the existing `/graph` route.

```python
@router.post("/graph/tasks")
def create_graph_task(req: GraphRequest, background_tasks: BackgroundTasks):
    task = _TOPOLOGY_TASKS.create_task(
        code=req.code,
        market=req.market,
        depth=req.depth,
        center_name=req.center_name,
        quote_mode=_graph_quote_mode(req.quote_mode),
    )

    def service_factory() -> TopologyService:
        db = MarketDatabase(mysql_config_from_env())
        return TopologyService(db)

    background_tasks.add_task(TopologyTaskRunner(_TOPOLOGY_TASKS, service_factory).run, task.task_id)
    return {"ok": True, "data": task_to_payload(task)}


@router.get("/graph/tasks/{task_id}")
def get_graph_task(task_id: str):
    data = _TOPOLOGY_TASKS.get_snapshot(task_id)
    if data is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "task_not_found"})
    return {"ok": True, "data": data}


@router.delete("/graph/tasks/{task_id}")
def cancel_graph_task(task_id: str):
    cancelled = _TOPOLOGY_TASKS.cancel(task_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "task_not_found"})
    data = _TOPOLOGY_TASKS.get_snapshot(task_id)
    return {"ok": True, "data": data}
```

- [ ] **Step 6: Run backend topology tests**

Run:

```bash
rtk python -m unittest stock_screener.tests.test_industry_topology -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
rtk git add stock_screener/web/topology_tasks.py stock_screener/web/topology.py stock_screener/tests/test_industry_topology.py
rtk git commit -m "feat: add topology graph task endpoints"
```

## Task 3: Frontend API Types

**Files:**
- Modify: `stock_screener/web_frontend/src/features/industryTopology/types.ts`
- Modify: `stock_screener/web_frontend/src/features/industryTopology/topologyApi.ts`

- [ ] **Step 1: Add topology task types**

In `stock_screener/web_frontend/src/features/industryTopology/types.ts`, add these exports near the existing topology stage types.

```ts
export type TopologyGraphTaskStage =
  | 'queued'
  | 'initial_graph_running'
  | 'initial_graph_ready'
  | 'enriching'
  | 'source_polling'
  | 'done'
  | 'partial'
  | 'failed'
  | 'cancelled'
  | 'expired'

export interface TopologyGraphTaskError {
  code: string
  message: string
}

export interface TopologyGraphTask {
  task_id: string
  stage: TopologyGraphTaskStage
  progress_pct: number
  graph: TopologyGraph
  message: string
  warnings: string[]
  error?: TopologyGraphTaskError | null
  updated_at: string
}
```

- [ ] **Step 2: Add task API methods**

In `stock_screener/web_frontend/src/features/industryTopology/topologyApi.ts`, update the type import.

```ts
import type {
  ExpandResult,
  SearchResult,
  TopologyGraph,
  TopologyGraphTask,
  TopologyQuoteBatch,
  TopologySearchEnrichResult,
} from './types'
```

Add these methods inside `topologyApi` after `graph`.

```ts
  createGraphTask: (code: string, market: string, depth: number, centerName = '', quoteMode = 'llm_initial') =>
    api<{ ok: true; data: TopologyGraphTask }>('/api/topology/graph/tasks', {
      method: 'POST',
      body: JSON.stringify({ code, market, depth, center_name: centerName, quote_mode: quoteMode }),
    }),
  getGraphTask: (taskId: string) =>
    api<{ ok: true; data: TopologyGraphTask }>(`/api/topology/graph/tasks/${encodeURIComponent(taskId)}`),
  cancelGraphTask: (taskId: string) =>
    api<{ ok: true; data: TopologyGraphTask }>(`/api/topology/graph/tasks/${encodeURIComponent(taskId)}`, {
      method: 'DELETE',
    }),
```

- [ ] **Step 3: Run TypeScript build to catch type errors**

Run:

```bash
rtk npm run build
```

Working directory: `stock_screener/web_frontend`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
rtk git add stock_screener/web_frontend/src/features/industryTopology/types.ts stock_screener/web_frontend/src/features/industryTopology/topologyApi.ts
rtk git commit -m "feat: add topology graph task api client"
```

## Task 4: Frontend Task Polling Flow

**Files:**
- Modify: `stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx`

- [ ] **Step 1: Add task polling constants and terminal-stage helper**

In `stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx`, near existing poll constants, add:

```ts
const GRAPH_TASK_POLL_INTERVAL_MS = 1500
const GRAPH_TASK_TERMINAL_STAGES = new Set(['done', 'partial', 'failed', 'cancelled', 'expired'])

function isGraphTaskTerminal(stage: string) {
  return GRAPH_TASK_TERMINAL_STAGES.has(stage)
}
```

- [ ] **Step 2: Add a backend task id ref**

Inside the component, near existing refs such as `taskIdRef`, add:

```ts
  const graphTaskIdRef = useRef<string | null>(null)
```

- [ ] **Step 3: Replace `startTopology` with task creation and polling**

Replace the current `startTopology` callback with this implementation.

```tsx
  const startTopology = useCallback(async () => {
    if (!selected) return
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
    if (previousGraphTaskId) {
      void topologyApi.cancelGraphTask(previousGraphTaskId).catch(() => undefined)
    }
    try {
      const created = await topologyApi.createGraphTask(selected.code, selected.market, depth, selected.name, 'llm_initial')
      if (taskId !== taskIdRef.current) return
      graphTaskIdRef.current = created.data.task_id
      applyGraph(created.data.graph, true, true)
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
            applyGraph(task.graph, task.stage === 'initial_graph_ready', task.stage === 'initial_graph_ready')
          }
          setWarnings(Array.from(new Set(task.warnings || [])))
          setStats(task.error?.message || task.message || formatTopologyStats(task.graph.stats, task.warnings || []))
          setProgressPct(task.progress_pct)
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
```

- [ ] **Step 4: Disable the old relation polling loop for task-owned topology starts**

In the `useEffect` that polls `topologyApi.graph(..., 'auto')`, add this guard before creating the timer:

```ts
    if (graphTaskIdRef.current) return
```

This prevents the old graph route from racing the new task snapshot flow.

- [ ] **Step 5: Run frontend build**

Run:

```bash
rtk npm run build
```

Working directory: `stock_screener/web_frontend`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
rtk git add stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx
rtk git commit -m "feat: poll topology graph tasks in frontend"
```

## Task 5: End-To-End Verification And Cleanup

**Files:**
- Verify: `stock_screener/web/topology.py`
- Verify: `stock_screener/web/topology_tasks.py`
- Verify: `stock_screener/industry_topology/service.py`
- Verify: `stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx`

- [ ] **Step 1: Run backend topology test suite**

Run:

```bash
rtk python -m unittest stock_screener.tests.test_industry_topology -v
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run:

```bash
rtk npm run build
```

Working directory: `stock_screener/web_frontend`

Expected: PASS.

- [ ] **Step 3: Start the local app if not already running**

If no dev server is already running, start the backend/frontend exactly as the project normally does. If the project has a combined launcher script, use that. If it does not, use the existing backend command and:

```bash
rtk npm run dev
```

Working directory: `stock_screener/web_frontend`

Expected: Vite prints a localhost URL.

- [ ] **Step 4: Manually verify task API timing**

Run:

```bash
rtk curl -s -X POST 'http://127.0.0.1:5173/api/topology/graph/tasks' \
  -H 'Content-Type: application/json' \
  --data-raw '{"code":"NVDA","market":"US","depth":3,"center_name":"NVIDIA","quote_mode":"llm_initial"}'
```

Expected: response returns quickly with `ok: true`, a `task_id`, `stage: "queued"` or a later stage, and one skeleton node.

- [ ] **Step 5: Poll the returned task**

Run, replacing `<task_id>`:

```bash
rtk curl -s 'http://127.0.0.1:5173/api/topology/graph/tasks/<task_id>'
```

Expected: stage advances to `initial_graph_running`, `initial_graph_ready`, or `done`; when initial graph is ready, nodes and edges are present.

- [ ] **Step 6: Verify the frontend interaction**

Open the topology panel, search/select NVIDIA, and start topology. Expected:

- The center node appears immediately.
- Progress starts around 5%.
- The graph expands when the task reaches `initial_graph_ready`.
- The UI stops polling at `done`, `partial`, or `failed`.
- Starting a second topology request does not let the first task overwrite the second graph.

- [ ] **Step 7: Check git status only contains intended files**

Run:

```bash
rtk git status --short
```

Expected: only files touched by this plan are modified or untracked.

- [ ] **Step 8: Final commit if verification required small fixes**

If Step 1-7 required any small fixes, commit them:

```bash
rtk git add stock_screener/industry_topology/service.py stock_screener/web/topology.py stock_screener/web/topology_tasks.py stock_screener/tests/test_industry_topology.py stock_screener/web_frontend/src/features/industryTopology/types.ts stock_screener/web_frontend/src/features/industryTopology/topologyApi.ts stock_screener/web_frontend/src/features/industryTopology/IndustryTopologyPanel.tsx
rtk git commit -m "fix: stabilize topology graph task loading"
```

Expected: commit succeeds, or no commit is needed because the working tree already matches earlier task commits.

## Self-Review

- Spec coverage: The plan covers task creation, task polling, cancellation, skeleton first render, initial-only graph generation, enrichment warning behavior, terminal states, and compatibility with the existing `/api/topology/graph` route.
- Placeholder scan: The plan contains no unresolved placeholder markers and gives concrete code, commands, and expected outcomes.
- Type consistency: Backend stage strings match frontend `TopologyGraphTaskStage`; API method names match the frontend plan; task payload fields match the spec.
