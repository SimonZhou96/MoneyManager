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
            if task.expires_at <= now and task.stage not in {"initial_graph_running", "depth_expanding"}
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
            graph = task.graph
            for graph in service.iter_depth_graphs(task.code, task.market, depth=task.depth, center_name=task.center_name):
                task = self.registry.get(task_id)
                if task is None or task.stage == "cancelled":
                    return
                stats = graph.get("stats") or {}
                reached_depth = max_graph_depth(graph)
                progress_pct = depth_progress_pct(reached_depth, task.depth)
                self.registry.update(
                    task_id,
                    stage="depth_expanding",
                    progress_pct=progress_pct,
                    message=f"第 {stats.get('expanding_depth') or reached_depth} 度拓扑生成中",
                    graph=graph,
                    warnings=list(graph.get("warnings") or []),
                )
            task = self.registry.get(task_id)
            if task is None or task.stage == "cancelled":
                return
            requested_depth = max(int(task.depth or 0), 0)
            reached_depth = max_graph_depth(graph)
            if requested_depth > reached_depth:
                self.registry.update(
                    task_id,
                    stage="partial",
                    progress_pct=95,
                    message=f"Topology graph partial: reached depth {reached_depth}, requested depth {requested_depth}",
                    warnings=["topology_depth_incomplete"],
                )
                return
            self.registry.update(task_id, stage="enriching", progress_pct=95, message="Enriching graph", graph=graph)
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
            self.registry.update(task_id, stage="enriching", progress_pct=95, message="Search enrichment unavailable", warnings=["search_unavailable"])
            return
        items = enriched.get("items") or []
        warnings = list(enriched.get("warnings") or [])
        if items:
            graph = merge_node_patches(graph, items)
        self.registry.update(task_id, stage="source_polling", progress_pct=95, message="Checking source relations", graph=graph, warnings=warnings)


def build_skeleton_graph(*, code: str, market: str, center_name: str, depth: int) -> Dict[str, Any]:
    center = {
        "id": symbol_id(market, code),
        "code": code,
        "market": market,
        "name": center_name or code,
        "sector": "板块未知",
        "industry": "",
        "price": None,
        "pct_chg": None,
        "market_cap": None,
        "market_cap_str": "--",
        "size_level": 6,
        "quote_status": "pending",
        "quote_updated_at": None,
        "quote_error": "",
        "field_sources": {},
        "field_confidence": {},
        "data_gaps": ["sector", "industry", "price", "market_cap", "pct_chg"],
        "field_errors": {
            "price": "quote_pending",
            "pct_chg": "quote_pending",
            "market_cap": "quote_pending",
        },
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


def max_graph_depth(graph: Dict[str, Any]) -> int:
    depths = [
        int(node.get("depth") or 0)
        for node in graph.get("nodes") or []
        if isinstance(node, dict)
    ]
    return max(depths, default=0)


def depth_progress_pct(reached_depth: int, requested_depth: int) -> int:
    if requested_depth <= 0:
        return 90
    return min(90, max(5, round(5 + (max(reached_depth, 0) / requested_depth) * 85)))


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
