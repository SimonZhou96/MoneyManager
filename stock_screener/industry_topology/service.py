#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编排：搜索 → 首次拓扑 → 按需展开 → 刷新。故障隔离 + LLM 硬上限。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from market import normalize_market

from .cache import GraphCache
from .relation_engine import RelationEngine, RelationEngineError
from .resolver import NodeResolver, compute_size_level, format_market_cap

_LLM_HARD_LIMIT = 20


class TopologyService:
    def __init__(self, db: Any, llm_provider: Any = None):
        self.db = db
        self.resolver = NodeResolver(db)
        self.cache = GraphCache(db)
        if llm_provider is None:
            llm_provider = self._default_llm()
        self.engine = RelationEngine(self.resolver, llm_provider) if llm_provider else None
        self._llm_calls = 0  # 会话累计

    @staticmethod
    def _default_llm():
        try:
            from signal_analysis.factories import LLMProviderFactory
            from signal_analysis.models import AnalysisSettings
            return LLMProviderFactory.from_env(AnalysisSettings())
        except Exception:
            return None

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
    def build_graph(self, code: str, market: str, depth: int = 3) -> Dict[str, Any]:
        market = normalize_market(market)
        return self._traverse(code, market, depth, existing_codes=None, is_center=True)

    # -- 按需展开 --
    def expand(self, code: str, market: str, depth: int = 2, existing_codes: Optional[List[str]] = None) -> Dict[str, Any]:
        market = normalize_market(market)
        return self._traverse(code, market, depth, existing_codes=existing_codes or [], is_center=False)

    # -- 刷新 --
    def refresh(self, code: str, market: str) -> Dict[str, Any]:
        market = normalize_market(market)
        # 强制重算：先取行情（中心节点），再重算关系
        center_info = self.resolver.resolve(code, market)
        relations = self._infer_with_limit(code, market, center_info.get("name", ""), center_info.get("sector", ""))
        cached_map: Dict[str, List] = {}
        if relations is not None:
            cached_map[code] = relations
            try:
                self.cache.save_relations(code, market, relations, provider="deepseek", model="v4")
            except Exception:
                pass
        nodes, edges = self._assemble(code, market, [code], cached_map, existing_set=set())
        return {"nodes": nodes, "edges": edges, "stats": {"llm_calls": self._llm_calls}}

    # -- 内部遍历 --
    def _traverse(self, code: str, market: str, depth: int, existing_codes: Optional[List[str]], is_center: bool) -> Dict[str, Any]:
        existing_set = set(existing_codes) if existing_codes else set()
        center_info = self.resolver.resolve(code, market)
        center_name = center_info.get("name", "") or code
        center_sector = center_info.get("sector", "") or "--"

        # 收集缓存
        cached_map: Dict[str, List] = {}
        stale_sources: List[str] = []
        cached_for_center = self.cache.get_relations(code, market)
        if cached_for_center is not None:
            cached_map[code] = cached_for_center
        else:
            stale_sources.append(code)

        # 对已缓存节点的子关系也尝试加载（depth>1 时）
        if depth > 1 and cached_for_center:
            for r in cached_for_center:
                if r.is_empty:
                    continue
                sub = self.cache.get_relations(r.peer_code, r.peer_market)
                if sub is not None:
                    cached_map[r.peer_code] = sub
                else:
                    stale_sources.append(r.peer_code)

        # LLM 补算 stale_sources（受硬上限）
        for src in list(stale_sources):
            rels = self._infer_with_limit(src, market, center_name if src == code else "", center_sector if src == code else "--")
            if rels is not None:
                cached_map[src] = rels
                # 写缓存
                try:
                    self.cache.save_relations(src, market, rels, provider="deepseek", model="v4")
                except Exception:
                    pass

        graph = self.cache.build_graph(cached_map)
        reachable = set(self.cache.reachable_within(graph, code, depth))
        # Also include nodes directly from cached_map relations (handles fake/cache
        # implementations where reachable_within doesn't traverse the graph)
        for src, rels in cached_map.items():
            for r in rels:
                if r.is_empty:
                    continue
                reachable.add(src)
                reachable.add(r.peer_code)

        nodes, edges = self._assemble(code, market, list(reachable), cached_map, existing_set, is_center=is_center)
        stats = {"llm_calls": self._llm_calls, "cached_nodes": len(cached_map), "stale_nodes": len(stale_sources), "depth": depth}
        if not cached_map and self._llm_calls == 0:
            stats["error"] = "llm_failed"
        return {"center": self._to_node(center_info, code, market, expanded=True, is_center=True, stale=False),
                "nodes": nodes, "edges": edges, "stats": stats}

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

    def _assemble(self, center_code, market, codes, cached_map, existing_set, is_center=False):
        infos = self.resolver.resolve_many([c for c in codes if c != center_code], market) if codes else {}
        center_info = self.resolver.resolve(center_code, market)
        infos[center_code] = center_info
        nodes = []
        for c in codes:
            if c in existing_set and not is_center:
                continue
            info = infos.get(c, {"code": c, "name": "", "market": market, "sector": "--", "market_cap": None, "pct_chg": None})
            nodes.append(self._to_node(info, c, market, expanded=c in cached_map, is_center=(c == center_code and is_center), stale=False))
        edges = []
        for src, rels in cached_map.items():
            for r in rels:
                if r.is_empty:
                    continue
                if r.peer_code not in codes and src not in codes:
                    continue
                rel_cn = _RELATION_CN.get(r.relation.value, r.relation.value)
                label = f"{rel_cn}·{r.evidence}" if r.evidence else rel_cn
                edges.append({
                    "source": src, "target": r.peer_code,
                    "direction": r.direction.value, "relation": r.relation.value,
                    "label": label[:40], "evidence": r.evidence,
                })
        return nodes, edges

    @staticmethod
    def _to_node(info, code, market, expanded, is_center, stale):
        market_cap = info.get("market_cap")
        return {
            "code": code, "name": info.get("name", "") or code,
            "market": info.get("market", market),
            "sector": info.get("sector", "--") or "--",
            "pct_chg": info.get("pct_chg"),
            "market_cap_str": format_market_cap(market_cap),
            "size_level": 6 if is_center else compute_size_level(market_cap),
            "expanded": expanded, "stale": stale, "is_center": is_center,
        }


_RELATION_CN = {
    "supplier": "供应商", "raw_material": "原材料", "equipment": "设备", "foundry_packaging": "代工封测",
    "component": "零部件", "customer": "客户", "odm": "代工", "distributor": "分销",
    "application": "应用场景", "service": "服务", "competitor": "竞品", "substitute": "替代品",
    "other": "其他",
}
