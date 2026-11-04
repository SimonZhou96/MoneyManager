#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编排：搜索 → 首次拓扑 → 按需展开 → 刷新。故障隔离 + LLM 硬上限。"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import os
from typing import Any, Dict, List, Optional, Tuple

from market import normalize_market

from .cache import GraphCache
from .enrichment import TopologyEnrichmentService
from .relation_engine import RelationEngine, RelationEngineError
from .resolver import NodeResolver, compute_size_level, format_market_cap
from .symbols import bare_code, parse_topology_symbol, symbol_id

_LLM_HARD_LIMIT = 20
_DEFAULT_DEPTH_BATCH_SIZE = 5


class TopologyService:
    def __init__(self, db: Any, llm_provider: Any = None):
        self.db = db
        self.resolver = NodeResolver(db)
        self.cache = GraphCache(db)
        if llm_provider is None:
            llm_provider = self._default_llm()
        self.engine = RelationEngine(self.resolver, llm_provider) if llm_provider else None
        self.search_provider = None if getattr(llm_provider, "name", "") == "fake" else self._default_search()
        self.enricher = TopologyEnrichmentService(self.engine, self.search_provider) if self.engine else None
        self._llm_calls = 0  # 会话累计

    @staticmethod
    def _default_llm():
        try:
            from signal_analysis.factories import LLMProviderFactory
            from signal_analysis.models import AnalysisSettings
            return LLMProviderFactory.from_env(AnalysisSettings())
        except Exception:
            return None

    @staticmethod
    def _default_search():
        try:
            from signal_analysis.browser_search_providers import BingBaiduSearchProvider
            from signal_analysis.factories import SearchProviderFactory
            from signal_analysis.models import AnalysisSettings
            from signal_analysis.search_providers import FallbackSearchProvider, NullSearchProvider

            settings = AnalysisSettings(search_max_results=4)
            providers = []
            for provider_name in SearchProviderFactory._provider_order_from_env():
                if provider_name == "bing_baidu":
                    headless = os.getenv("BING_BAIDU_BROWSER_HEADLESS", "true").strip().lower() != "false"
                    timeout = int(os.getenv("TOPOLOGY_BING_BAIDU_TIMEOUT_SEC", "5") or "5")
                    provider = BingBaiduSearchProvider(headless=headless, timeout_sec=timeout)
                    if not getattr(provider, "is_available", False):
                        continue
                    providers.append(provider)
                    continue
                provider = SearchProviderFactory._provider_from_env(provider_name, settings)
                if getattr(provider, "is_available", False):
                    providers.append(provider)
            if not providers:
                return NullSearchProvider()
            if len(providers) == 1:
                return providers[0]
            return FallbackSearchProvider(providers)
        except Exception:
            return None

    def _provider_meta(self) -> tuple[str, str]:
        """Return (provider_name, model_name) from the injected LLM provider.

        Guards for engine/llm being None and for providers that lack a
        ``model_name`` property (e.g. FakeLLMProvider in tests).
        """
        llm = getattr(self.engine, "llm", None) if self.engine else None
        if llm is None:
            return "none", ""
        provider_name = getattr(llm, "name", "unknown") or "unknown"
        model_name = getattr(llm, "model_name", "") or ""
        return provider_name, model_name

    # -- 搜索 --
    def search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for market in ("A", "HK", "US"):
            try:
                rows = self.db.search_stocks_by_name(market, keyword, limit) if hasattr(self.db, "search_stocks_by_name") else []
            except Exception:
                rows = []
            for r in rows:
                results.append({"code": r["code"], "name": r.get("name", ""), "market": market,
                                "sector": r.get("sector") or r.get("industry") or ""})
                if len(results) >= limit:
                    return results
        return results

    # -- 首次拓扑 --
    def build_graph(self, code: str, market: str, depth: int = 3, quote_mode: str = "defer", center_name: str = "") -> Dict[str, Any]:
        market = normalize_market(market)
        return self._traverse(code, market, depth, existing_codes=None, is_center=True, quote_mode=quote_mode, center_name=center_name)

    def build_initial_graph_only(self, code: str, market: str, depth: int = 3, center_name: str = "") -> Dict[str, Any]:
        market = normalize_market(market)
        center_info = self._resolver_resolve(code, market, include_quote=False, fallback_name=center_name)
        resolved_code = self._storage_code(center_info.get("code") or code, market)
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
        initial_relations = self._normalize_relation_storage_codes(list(initial.get("relations") or []))
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

        self._expand_cached_map_to_depth(resolved_code, market, depth, cached_map)
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
            "reached_depth": self._max_cached_depth(resolved_code, cached_map, graph),
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

    def iter_depth_graphs(self, code: str, market: str, depth: int = 3, center_name: str = ""):
        market = normalize_market(market)
        requested_depth = max(int(depth or 0), 0)
        center_info = self._resolver_resolve(code, market, include_quote=False, fallback_name=center_name)
        resolved_code = self._storage_code(center_info.get("code") or code, market)
        cached_map, stale_sources = self._load_cached_map_to_depth(resolved_code, market, requested_depth)
        batch_size = self._depth_batch_size()
        initial_graph = self.cache.build_graph(cached_map)
        reached_depth = self._completed_depth(resolved_code, requested_depth, cached_map, stale_sources, initial_graph)

        if cached_map and reached_depth >= requested_depth:
            yield self._graph_from_cached_map(
                resolved_code,
                market,
                requested_depth,
                cached_map,
                center_info=center_info,
                expanding_depth=reached_depth,
                data_stage="cached",
                reached_depth=reached_depth,
            )
            return

        for expanding_depth in range(max(1, reached_depth + 1), requested_depth + 1):
            frontier = self._depth_frontier(resolved_code, market, cached_map, expanding_depth)
            if not frontier:
                return
            progressed = False
            for i in range(0, len(frontier), batch_size):
                if self._llm_calls >= _LLM_HARD_LIMIT:
                    return
                result = self._infer_batch_with_limit(frontier[i:i + batch_size])
                if not result:
                    continue
                for (_, source_code), rels in result.items():
                    cached_map[source_code] = rels
                    progressed = True
            graph = self._graph_from_cached_map(
                resolved_code,
                market,
                requested_depth,
                cached_map,
                center_info=center_info,
                expanding_depth=expanding_depth,
            )
            yield graph
            if not progressed or int(graph.get("stats", {}).get("reached_depth") or 0) < expanding_depth:
                return

    # -- 按需展开 --
    def expand(
        self,
        code: str,
        market: str,
        depth: int = 2,
        existing_codes: Optional[List[str]] = None,
        quote_mode: str = "defer",
    ) -> Dict[str, Any]:
        market = normalize_market(market)
        result = self._traverse(code, market, depth, existing_codes=existing_codes or [], is_center=False, quote_mode=quote_mode)
        result.pop("center", None)
        return result

    # -- 刷新 --
    def refresh(self, code: str, market: str) -> Dict[str, Any]:
        market = normalize_market(market)
        # 强制重算：先取行情（中心节点），再重算关系
        center_info = self.resolver.resolve(code, market, include_quote=True)
        relations = self._infer_with_limit(code, market, center_info.get("name", ""), center_info.get("sector", ""))
        cached_map: Dict[str, List] = {}
        if relations is not None:
            cached_map[code] = relations
            try:
                p_name, m_name = self._provider_meta()
                self.cache.save_relations(code, market, relations, provider=p_name, model=m_name)
            except Exception:
                pass
        graph = self.cache.build_graph(cached_map) if cached_map else None
        nodes, edges = self._assemble(code, market, [code], cached_map, existing_set=set(), center_info=center_info, graph=graph)
        return {"nodes": nodes, "edges": edges, "stats": {"llm_calls": self._llm_calls}}

    def search_enrich(self, *, center: Dict[str, Any], symbols: List[str]) -> Dict[str, Any]:
        if self.enricher is None:
            return {"items": [], "warnings": []}
        return self.enricher.enrich_graph_fields(center=center, symbols=symbols)

    # -- 内部遍历 --
    def _traverse(
        self,
        code: str,
        market: str,
        depth: int,
        existing_codes: Optional[List[str]],
        is_center: bool,
        quote_mode: str = "defer",
        center_name: str = "",
    ) -> Dict[str, Any]:
        existing_set = set(existing_codes) if existing_codes else set()
        include_quote = quote_mode == "sync"
        initial_stage = quote_mode == "llm_initial"
        center_info = self._resolver_resolve(code, market, include_quote=include_quote, fallback_name=center_name)
        code = self._storage_code(center_info.get("code") or code, market)
        center_name = center_info.get("name", "") or code
        center_sector = center_info.get("sector", "") or "--"
        llm_node_data: Dict[str, Dict[str, Any]] = {}
        warnings: List[str] = []
        initial_relations: Optional[List[Any]] = None

        if initial_stage:
            initial = self._build_initial_snapshot(code, market, center_name=center_name, center_sector=center_sector)
            if not initial:
                raise RuntimeError("LLM 首屏拓扑生成失败")
            if initial:
                center_payload = initial.get("center") if isinstance(initial.get("center"), dict) else {}
                initial_relations = self._normalize_relation_storage_codes(list(initial.get("relations") or []))
                if not initial_relations:
                    raise RuntimeError("LLM 首屏拓扑未返回任何关系节点")
                llm_node_data = self._llm_node_data_from_relations(initial_relations)
                llm_node_data[symbol_id(market, code)] = center_payload
                center_info = self._merge_node_info(center_info, center_payload, prefer_llm=True)
                center_name = center_info.get("name", "") or center_name
                center_sector = center_info.get("sector", "") or center_sector
                if initial_relations is not None:
                    self.cache.save_relations(code, market, initial_relations, provider=self._provider_meta()[0], model=self._provider_meta()[1])
            warnings = list(initial.get("warnings") or []) if initial_stage and initial else []

        # 收集缓存；auto/defer 只读已有关系，sync/llm_initial 再补缺失。
        if initial_relations is not None:
            cached_map, stale_sources = self._load_cached_map_to_depth(code, market, depth, initial_relations=initial_relations)
        else:
            cached_map, stale_sources = self._load_cached_map_to_depth(code, market, depth)

        relation_status = "cached" if cached_map else "pending"
        if quote_mode in {"sync", "llm_initial"}:
            # LLM 补算 stale_sources（受硬上限）
            for src in list(stale_sources):
                rels = self._infer_with_limit(src, market, center_name if src == code else "", center_sector if src == code else "--")
                if rels is not None:
                    rels = self._normalize_relation_storage_codes(rels)
                    cached_map[src] = rels
                    # 写缓存
                    try:
                        p_name, m_name = self._provider_meta()
                        self.cache.save_relations(src, market, rels, provider=p_name, model=m_name)
                    except Exception:
                        pass
            relation_status = "fresh" if cached_map else "failed"
        elif quote_mode == "auto" and stale_sources:
            relation_status = "generating"

        graph = self.cache.build_graph(cached_map)
        reachable = {code}
        if code in graph:
            reachable.update(self.cache.reachable_within(graph, code, depth))
        # Also include nodes directly from cached_map relations (handles fake/cache
        # implementations where reachable_within doesn't traverse the graph)
        for src, rels in cached_map.items():
            for r in rels:
                if r.is_empty:
                    continue
                reachable.add(src)
                reachable.add(r.peer_code)

        nodes, edges = self._assemble(
            code,
            market,
            list(reachable),
            cached_map,
            existing_set,
            is_center=is_center,
            center_info=center_info,
            graph=graph,
            include_quote=include_quote,
            llm_node_data=llm_node_data,
            prefer_llm=initial_stage,
            data_stage="llm_initial" if initial_stage else None,
        )
        reached_depth = self._completed_depth(code, depth, cached_map, stale_sources, graph)
        stats = {
            "llm_calls": self._llm_calls,
            "cached_nodes": len(cached_map),
            "stale_nodes": len(stale_sources),
            "stale_sources": self._stale_source_payload(stale_sources, market, code, cached_map),
            "depth": depth,
            "requested_depth": depth,
            "reached_depth": reached_depth,
            "can_continue": bool(stale_sources) or reached_depth < depth,
            "relation_status": relation_status,
            "data_stage": "llm_initial" if initial_stage else "source_verified" if include_quote else "cached",
        }
        if not cached_map and self._llm_calls == 0 and quote_mode == "sync":
            stats["error"] = "llm_failed"
        result = {"center": self._to_node(center_info, code, market, expanded=True, is_center=True, stale=False, depth=0, zone="center"),
                "nodes": nodes, "edges": edges, "stats": stats}
        if warnings:
            result["warnings"] = warnings
        return result

    def _infer_with_limit(self, code: str, market: str, name: str, sector: str):
        if self._llm_calls >= _LLM_HARD_LIMIT or self.engine is None:
            return None
        try:
            rels = self.engine.infer(code, name, market, sector)
            self._llm_calls += 1
            return rels
        except RelationEngineError:
            return None
        except Exception:
            return None

    def _load_cached_map_to_depth(
        self,
        center_code: str,
        market: str,
        depth: int,
        *,
        initial_relations: Optional[List[Any]] = None,
    ) -> Tuple[Dict[str, List], List[str]]:
        cached_map: Dict[str, List] = {}
        stale_sources: List[str] = []
        requested_depth = max(int(depth or 0), 0)

        if initial_relations is not None:
            cached_map[center_code] = initial_relations
        else:
            center_relations = self.cache.get_relations(center_code, market)
            if center_relations is None:
                return cached_map, [center_code]
            cached_map[center_code] = center_relations

        for source_depth in range(0, requested_depth):
            graph = self.cache.build_graph(cached_map)
            node_meta = self._build_node_meta(center_code, cached_map, graph)
            node_markets = self._build_node_markets(center_code, market, cached_map)
            frontier = [
                code
                for code, meta in node_meta.items()
                if meta["depth"] == source_depth and code in cached_map
            ]
            for source_code in frontier:
                for rel in cached_map.get(source_code) or []:
                    if rel.is_empty:
                        continue
                    if rel.peer_code in cached_map or rel.peer_code in stale_sources:
                        continue
                    peer_depth = node_meta.get(rel.peer_code, {}).get("depth")
                    if peer_depth is None or peer_depth >= requested_depth:
                        continue
                    peer_market = node_markets.get(rel.peer_code, rel.peer_market or market)
                    peer_relations = self.cache.get_relations(rel.peer_code, peer_market)
                    if peer_relations is None:
                        stale_sources.append(rel.peer_code)
                    else:
                        cached_map[rel.peer_code] = peer_relations
        return cached_map, stale_sources

    def infer_and_cache_batch(self, sources: List[Dict[str, str]]) -> bool:
        """批量推理多个 source 并写缓存。一次 LLM 调用覆盖整批。

        sources: [{code, market, name?, sector?}] —— name/sector 缺失时用 resolver 补齐。
        未返回 group 的 source 写入空关系缓存，避免反复 generating。
        """
        return bool(self._infer_batch_with_limit(sources))

    def _infer_batch_with_limit(self, sources: List[Dict[str, str]]) -> Dict[Tuple[str, str], List[Any]]:
        if not sources or self.engine is None or self._llm_calls >= _LLM_HARD_LIMIT:
            return {}

        enriched: List[Dict[str, str]] = []
        for s in sources:
            market = normalize_market(str(s.get("market", "")))
            code = self._storage_code(str(s.get("code", "")).strip(), market)
            if not code:
                continue
            name = str(s.get("name", "") or "")
            sector = str(s.get("sector", "") or "")
            if not name or not sector or sector == "--":
                info = self.resolver.resolve(code, market, include_quote=False)
                name = name or info.get("name", "") or code
                sector = sector if (sector and sector != "--") else (info.get("sector", "") or "--")
            enriched.append({"code": code, "market": market, "name": name, "sector": sector})
        if not enriched:
            return {}

        try:
            result = self.engine.infer_batch(enriched)
            self._llm_calls += 1
        except Exception:
            return {}

        p_name, m_name = self._provider_meta()
        normalized_result: Dict[Tuple[str, str], List[Any]] = {}
        for (market, code), rels in result.items():
            storage_code = self._storage_code(code, market)
            rels = self._normalize_relation_storage_codes(rels)
            normalized_result[(market, storage_code)] = rels
            try:
                self.cache.save_relations(storage_code, market, rels, provider=p_name, model=m_name)
            except Exception:
                pass
        return normalized_result

    @staticmethod
    def _storage_code(code: str, market: str) -> str:
        normalized_market = normalize_market(market)
        value = str(code or "").strip().upper()
        if not value:
            return ""
        if normalized_market == "US":
            return value if value.startswith("US.") else f"US.{value}"
        if normalized_market == "HK":
            if value.startswith("HK."):
                return f"HK.{value[3:].zfill(5)}" if value[3:].isdigit() else value
            return f"HK.{value.zfill(5)}" if value.isdigit() else value
        if normalized_market in {"JP", "TW", "KR"}:
            return value if value.startswith(f"{normalized_market}.") else f"{normalized_market}.{value}"
        return value

    @classmethod
    def _normalize_relation_storage_codes(cls, relations: List[Any]) -> List[Any]:
        normalized: List[Any] = []
        for rel in relations:
            peer_market = str(getattr(rel, "peer_market", "") or "")
            peer_code = str(getattr(rel, "peer_code", "") or "")
            next_peer_code = cls._storage_code(peer_code, peer_market)
            if next_peer_code and next_peer_code != peer_code:
                rel = replace(rel, peer_code=next_peer_code)
            normalized.append(rel)
        return normalized

    def _expand_cached_map_to_depth(self, center_code: str, market: str, depth: int, cached_map: Dict[str, List]) -> None:
        if depth <= 1 or not cached_map:
            return
        batch_size = self._depth_batch_size()
        graph = self.cache.build_graph(cached_map)
        while self._llm_calls < _LLM_HARD_LIMIT:
            node_meta = self._build_node_meta(center_code, cached_map, graph)
            frontier = [
                {"code": code, "market": self._build_node_markets(center_code, market, cached_map).get(code, market)}
                for code, meta in node_meta.items()
                if 0 < meta["depth"] < depth and code not in cached_map
            ]
            if not frontier:
                return
            progressed = False
            for i in range(0, len(frontier), batch_size):
                if self._llm_calls >= _LLM_HARD_LIMIT:
                    return
                result = self._infer_batch_with_limit(frontier[i:i + batch_size])
                if not result:
                    continue
                for (_, code), rels in result.items():
                    cached_map[code] = rels
                    progressed = True
            if not progressed:
                return
            graph = self.cache.build_graph(cached_map)

    def _depth_frontier(self, center_code: str, market: str, cached_map: Dict[str, List], expanding_depth: int) -> List[Dict[str, str]]:
        if expanding_depth <= 1:
            return [] if center_code in cached_map else [{"code": center_code, "market": market}]
        graph = self.cache.build_graph(cached_map)
        node_meta = self._build_node_meta(center_code, cached_map, graph)
        node_markets = self._build_node_markets(center_code, market, cached_map)
        return [
            {"code": code, "market": node_markets.get(code, market)}
            for code, meta in node_meta.items()
            if meta["depth"] == expanding_depth - 1 and code not in cached_map
        ]

    def _graph_from_cached_map(
        self,
        center_code: str,
        market: str,
        requested_depth: int,
        cached_map: Dict[str, List],
        *,
        center_info: Dict[str, Any],
        expanding_depth: int,
        data_stage: str = "llm_initial",
        reached_depth: Optional[int] = None,
    ) -> Dict[str, Any]:
        graph = self.cache.build_graph(cached_map)
        reachable = {center_code}
        if center_code in graph:
            reachable.update(self.cache.reachable_within(graph, center_code, requested_depth))
        for src, rels in cached_map.items():
            for rel in rels:
                if rel.is_empty:
                    continue
                reachable.add(src)
                reachable.add(rel.peer_code)
        nodes, edges = self._assemble(
            center_code,
            market,
            list(reachable),
            cached_map,
            existing_set=set(),
            is_center=True,
            center_info=center_info,
            graph=graph,
            include_quote=False,
            prefer_llm=True,
            data_stage=data_stage,
        )
        reached_depth = reached_depth if reached_depth is not None else self._max_cached_depth(center_code, cached_map, graph)
        return {
            "center": self._to_node(
                center_info,
                center_code,
                market,
                expanded=True,
                is_center=True,
                stale=False,
                depth=0,
                zone="center",
                data_stage=data_stage,
            ),
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "llm_calls": self._llm_calls,
                "cached_nodes": len(cached_map),
                "stale_nodes": 0,
                "stale_sources": [],
                "depth": requested_depth,
                "requested_depth": requested_depth,
                "reached_depth": reached_depth,
                "expanding_depth": expanding_depth,
                "can_continue": reached_depth < requested_depth,
                "relation_status": "initial_ready" if reached_depth >= requested_depth else "generating",
                "data_stage": data_stage,
            },
            "warnings": [],
        }

    @staticmethod
    def _max_cached_depth(center_code: str, cached_map: Dict[str, List], graph: Any = None) -> int:
        meta = TopologyService._build_node_meta(center_code, cached_map, graph)
        return max((item["depth"] for item in meta.values()), default=0)

    @staticmethod
    def _completed_depth(center_code: str, requested_depth: int, cached_map: Dict[str, List], stale_sources: List[str], graph: Any = None) -> int:
        has_non_empty_relation = any(
            not getattr(rel, "is_empty", False)
            for rels in cached_map.values()
            for rel in (rels or [])
        )
        if cached_map and not stale_sources and has_non_empty_relation:
            return max(int(requested_depth or 0), 0)
        return TopologyService._max_cached_depth(center_code, cached_map, graph)

    @staticmethod
    def _depth_batch_size() -> int:
        try:
            value = int(os.getenv("TOPOLOGY_DEPTH_BATCH_SIZE", str(_DEFAULT_DEPTH_BATCH_SIZE)) or _DEFAULT_DEPTH_BATCH_SIZE)
        except ValueError:
            value = _DEFAULT_DEPTH_BATCH_SIZE
        return max(1, value)

    @staticmethod
    def _stale_source_payload(stale_sources: List[str], market: str, center_code: str, cached_map: Dict[str, List]) -> List[Dict[str, str]]:
        node_markets = TopologyService._build_node_markets(center_code, market, cached_map)
        return [{"code": source, "market": node_markets.get(source, market)} for source in stale_sources]

    def _assemble(self, center_code, market, codes, cached_map, existing_set, is_center=False, center_info=None, graph=None, include_quote=False, llm_node_data=None, prefer_llm=False, data_stage=None):
        node_markets = self._build_node_markets(center_code, market, cached_map)
        # 从 LLM 缓存中提取 peer_name 和 peer_market_cap 作为回退
        peer_name_map: Dict[str, str] = {}
        peer_node_data: Dict[str, Dict[str, Any]] = {}
        for src, rels in cached_map.items():
            for r in rels:
                if r.is_empty:
                    continue
                if r.peer_name and r.peer_code not in peer_name_map:
                    peer_name_map[r.peer_code] = r.peer_name
                peer_node_data.setdefault(symbol_id(r.peer_market or market, r.peer_code), {
                    "name": r.peer_name,
                    "market_cap": r.peer_market_cap,
                    "market_cap_str": r.peer_market_cap_str,
                    "sector": r.peer_sector,
                    "industry": r.peer_industry,
                    "pct_chg": r.peer_pct_chg,
                    "confidence": r.confidence,
                    "field_sources": dict(r.field_sources or {}),
                })
        llm_node_data = {**peer_node_data, **(llm_node_data or {})}
        resolve_lookup = {
            c: self._resolve_identity(node_markets.get(c, market), c)
            for c in codes
            if c != center_code
        }
        resolve_targets = [(identity[0], identity[1]) for identity in resolve_lookup.values()]
        infos = self.resolver.resolve_many(resolve_targets, include_quote=include_quote) if resolve_targets else {}
        if center_info is None:
            center_info = self._resolver_resolve(center_code, market, include_quote=include_quote)
        infos[symbol_id(market, center_info.get("code") or center_code)] = center_info
        node_meta = self._build_node_meta(center_code, cached_map, graph)
        nodes = []
        for c in codes:
            item_market = node_markets.get(c, market)
            symbol = symbol_id(item_market, c)
            resolve_identity = resolve_lookup.get(c)
            resolve_symbol = symbol_id(resolve_identity[0], resolve_identity[1]) if resolve_identity else symbol
            if (c in existing_set or symbol in existing_set) and not is_center:
                continue
            info = infos.get(resolve_symbol, {"code": c, "name": "", "market": item_market, "sector": "--", "market_cap": None, "pct_chg": None, "quote_status": "pending"})
            llm_info = llm_node_data.get(symbol, {})
            if c == center_code:
                llm_info = llm_node_data.get(symbol_id(item_market, c), llm_info)
            info = self._merge_node_info(info, llm_info, prefer_llm=prefer_llm)
            meta = node_meta.get(c, {"depth": 1, "zone": "peer"})
            nodes.append(
                self._to_node(
                    info,
                    c,
                    item_market,
                    display_code=c,
                    expanded=c in cached_map,
                    is_center=(c == center_code and is_center),
                    stale=False,
                    depth=meta["depth"],
                    zone=meta["zone"],
                    data_stage=data_stage or ("source_verified" if include_quote else "cached"),
                )
            )
        edges = []
        for src, rels in cached_map.items():
            for r in rels:
                if r.is_empty:
                    continue
                if r.peer_code not in codes and src not in codes:
                    continue
                source_market = r.source_market or node_markets.get(src, market)
                target_market = r.peer_market or node_markets.get(r.peer_code, market)
                rel_cn = _RELATION_CN.get(r.relation.value, r.relation.value)
                label = f"{rel_cn}·{r.evidence}" if r.evidence else rel_cn
                edges.append({
                    "source": symbol_id(source_market, src), "target": symbol_id(target_market, r.peer_code),
                    "direction": r.direction.value, "relation": r.relation.value,
                    "label": label[:40], "evidence": r.evidence,
                })
        return nodes, edges

    @staticmethod
    def _to_node(info, code, market, expanded, is_center, stale, depth, zone, display_code=None, data_stage=None):
        market_cap = info.get("market_cap")
        node_market = market
        node_code = display_code or info.get("code") or code
        return {
            "id": symbol_id(node_market, node_code),
            "code": node_code, "name": info.get("name", "") or code,
            "market": node_market,
            "sector": info.get("sector", "--") or "板块未知",
            "industry": info.get("industry", "") or "",
            "price": info.get("price"),
            "pct_chg": info.get("pct_chg"),
            "market_cap": market_cap,
            "market_cap_str": format_market_cap(market_cap),
            "size_level": 6 if is_center else compute_size_level(market_cap),
            "quote_status": info.get("quote_status", "pending"),
            "quote_updated_at": info.get("quote_updated_at"),
            "quote_error": info.get("quote_error", ""),
            "field_sources": info.get("field_sources", {}),
            "field_confidence": info.get("field_confidence", {}),
            "data_gaps": info.get("data_gaps", []),
            "field_errors": info.get("field_errors", {}),
            "data_stage": data_stage or info.get("data_stage", "cached"),
            "expanded": expanded, "stale": stale, "is_center": is_center,
            "depth": depth, "zone": zone,
        }

    def _build_initial_snapshot(self, code: str, market: str, center_name: str, center_sector: str) -> Dict[str, Any]:
        if self.enricher is None or self._llm_calls >= _LLM_HARD_LIMIT:
            return {}
        snapshot = self.enricher.build_initial_snapshot(
            market=market,
            code=code,
            name=center_name,
            sector=center_sector,
        )
        if snapshot:
            self._llm_calls += 1
            snapshot["relations"] = self.engine._parse_items(snapshot.get("items") or [], code, market) if self.engine else []
        return snapshot

    @staticmethod
    def _llm_node_data_from_relations(relations: List[Any]) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for rel in relations:
            symbol = symbol_id(rel.peer_market, rel.peer_code)
            result[symbol] = {
                "name": rel.peer_name,
                "sector": getattr(rel, "peer_sector", ""),
                "industry": getattr(rel, "peer_industry", ""),
                "market_cap": rel.peer_market_cap,
                "market_cap_str": rel.peer_market_cap_str,
                "pct_chg": getattr(rel, "peer_pct_chg", None),
                "confidence": getattr(rel, "confidence", None),
                "field_sources": dict(getattr(rel, "field_sources", None) or {}),
            }
        return result

    @staticmethod
    def _merge_node_info(source_info: Dict[str, Any], llm_info: Dict[str, Any], *, prefer_llm: bool) -> Dict[str, Any]:
        result = dict(source_info or {})
        field_sources = dict(result.get("field_sources") or {})
        field_confidence = dict(result.get("field_confidence") or {})
        llm_sources = dict(llm_info.get("field_sources") or {})
        llm_confidence = llm_info.get("confidence")

        for field in ("name", "sector", "industry", "market_cap", "pct_chg"):
            source_value = source_info.get(field) if source_info else None
            llm_value = llm_info.get(field) if llm_info else None
            use_llm = prefer_llm and TopologyService._has_value(llm_value, field)
            if field == "name":
                source_has_chinese = TopologyService._contains_cjk(source_value)
                llm_has_chinese = TopologyService._contains_cjk(llm_value)
                if source_has_chinese and not llm_has_chinese:
                    use_llm = False
                elif llm_has_chinese and not source_has_chinese:
                    use_llm = True
            if not use_llm and TopologyService._has_value(source_value, field):
                result[field] = source_value
                if field == "name" and TopologyService._contains_cjk(source_value) and not TopologyService._contains_cjk(llm_value):
                    field_sources[field] = field_sources.get(field, "llm_search")
                else:
                    field_sources[field] = "resolver_override" if TopologyService._has_value(llm_value, field) else "resolver"
                field_confidence.pop(field, None)
            elif TopologyService._has_value(llm_value, field):
                result[field] = llm_value
                field_sources[field] = llm_sources.get(field, "llm_search")
                if llm_confidence is not None:
                    field_confidence[field] = llm_confidence
            elif TopologyService._has_value(source_value, field):
                result[field] = source_value
                field_sources[field] = "resolver"
                field_confidence.pop(field, None)
        result["field_sources"] = field_sources
        result["field_confidence"] = field_confidence
        result["data_gaps"] = [field for field in ("name", "sector", "industry", "price", "market_cap", "pct_chg") if not TopologyService._has_value(result.get(field), field)]
        return result

    @staticmethod
    def _contains_cjk(value: Any) -> bool:
        return any("\u3400" <= char <= "\u9fff" for char in str(value or ""))

    @staticmethod
    def _has_value(value: Any, field: str) -> bool:
        if value is None:
            return False
        if field in {"name", "sector", "industry"}:
            return str(value).strip() not in {"", "--", "板块未知"}
        return True

    def _resolver_resolve(self, code: str, market: str, include_quote: bool = False, fallback_name: str = "") -> Dict[str, Any]:
        try:
            return self.resolver.resolve(code, market, include_quote=include_quote, fallback_name=fallback_name)
        except TypeError:
            return self.resolver.resolve(code, market, include_quote=include_quote)

    @staticmethod
    def _resolve_identity(market: str, code: str) -> tuple[str, str]:
        parsed = parse_topology_symbol(symbol_id(market, code))
        if parsed.get("skipped"):
            return market, code
        return parsed["market"], parsed["code"]

    @staticmethod
    def _build_node_markets(center_code: str, market: str, cached_map: Dict[str, List]) -> Dict[str, str]:
        node_markets: Dict[str, str] = {center_code: market}
        for src, rels in cached_map.items():
            for rel in rels:
                if rel.is_empty:
                    continue
                node_markets.setdefault(src, rel.source_market or market)
                node_markets[rel.peer_code] = rel.peer_market or market
        return node_markets

    @staticmethod
    def _build_node_meta(center_code: str, cached_map: Dict[str, List], graph: Any = None) -> Dict[str, Dict[str, Any]]:
        meta: Dict[str, Dict[str, Any]] = {center_code: {"depth": 0, "zone": "center"}}
        queue: deque[tuple[str, int, str]] = deque([(center_code, 0, "center")])

        while queue:
            src, depth, inherited_zone = queue.popleft()
            for rel in cached_map.get(src, []):
                if rel.is_empty:
                    continue
                next_zone = rel.direction.value if depth == 0 else inherited_zone
                current = meta.get(rel.peer_code)
                should_update = (
                    current is None
                    or depth + 1 < current["depth"]
                    or (depth + 1 == current["depth"] and current["zone"] == "peer" and next_zone != "peer")
                )
                if should_update:
                    meta[rel.peer_code] = {"depth": depth + 1, "zone": next_zone}
                    queue.append((rel.peer_code, depth + 1, next_zone))

        if graph is not None:
            for node in getattr(graph, "nodes", lambda: [])():
                meta.setdefault(node, {"depth": 1, "zone": "peer"})

        return meta


_RELATION_CN = {
    "supplier": "供应商", "raw_material": "原材料", "equipment": "设备", "foundry_packaging": "代工封测",
    "component": "零部件", "customer": "客户", "odm": "代工", "distributor": "分销",
    "application": "应用场景", "service": "服务", "competitor": "竞品", "substitute": "替代品",
    "other": "其他",
}
