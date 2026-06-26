#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM + search orchestration for topology first paint."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from .relation_engine import RelationEngineError
from .symbols import symbol_id
from signal_analysis.models import SearchDocument
from signal_analysis.search_providers import NullSearchProvider, SearchProvider

logger = logging.getLogger(__name__)


class TopologyEnrichmentService:
    """Build a first-paint topology snapshot from LLM + search."""

    def __init__(self, relation_engine: Any, search_provider: SearchProvider | None = None):
        self.relation_engine = relation_engine
        self.search_provider = search_provider or NullSearchProvider()

    def build_initial_snapshot(
        self,
        *,
        market: str,
        code: str,
        name: str = "",
        sector: str = "",
    ) -> Dict[str, Any]:
        if self.relation_engine is None:
            return {}
        resolved = self.relation_engine.resolve_center(
            request_market=market,
            request_code=code,
            request_name=name,
            request_sector=sector,
            search_documents=[],
        )
        if not resolved or not resolved.get("resolved"):
            raise RelationEngineError("LLM 无法高置信确认中心股票身份")

        canonical_market = str(resolved.get("market") or market).strip().upper() or market
        canonical_code = str(resolved.get("code") or code).strip() or code
        canonical_name = str(resolved.get("name") or name or code).strip() or code
        canonical_sector = str(resolved.get("sector") or sector).strip()

        graph = self.relation_engine.infer_graph(
            code=canonical_code,
            name=canonical_name,
            market=canonical_market,
            sector=canonical_sector,
            search_documents=[],
            center_payload=resolved,
        )
        if not graph:
            return {}
        graph["center"] = graph.get("center") or resolved
        graph["warnings"] = list(graph.get("warnings") or [])
        return graph

    def enrich_graph_fields(
        self,
        *,
        center: Dict[str, Any],
        symbols: List[str],
    ) -> Dict[str, Any]:
        if self.relation_engine is None:
            return {"items": [], "warnings": []}
        canonical_market = str(center.get("market") or "").strip().upper()
        canonical_code = str(center.get("code") or "").strip()
        canonical_name = str(center.get("name") or canonical_code).strip() or canonical_code
        canonical_sector = str(center.get("sector") or "").strip()
        if not canonical_market or not canonical_code:
            return {"items": [], "warnings": ["search_enrich_invalid_center"]}

        started_at = time.perf_counter()
        relation_docs, relation_warnings = self._search_relation_documents(
            market=canonical_market,
            code=canonical_code,
            name=canonical_name,
            sector=canonical_sector,
        )
        if not relation_docs:
            logger.info(
                "industry_topology_search_enrich stage=search_enrich market=%s code=%s name=%s duration_ms=%d success=%s item_count=%d warning_count=%d provider=%s search_used=%s",
                canonical_market,
                canonical_code,
                canonical_name,
                int((time.perf_counter() - started_at) * 1000),
                False,
                0,
                len(relation_warnings),
                getattr(self.search_provider, "name", "none"),
                False,
            )
            return {"items": [], "warnings": relation_warnings}

        graph = self.relation_engine.infer_graph(
            code=canonical_code,
            name=canonical_name,
            market=canonical_market,
            sector=canonical_sector,
            search_documents=relation_docs,
            center_payload=center,
        )
        items = self._filter_existing_patches(
            center=graph.get("center") if isinstance(graph.get("center"), dict) else center,
            graph_items=list(graph.get("items") or []),
            symbols=symbols,
        )
        warnings = [*relation_warnings, *list(graph.get("warnings") or [])]
        logger.info(
            "industry_topology_search_enrich stage=search_enrich market=%s code=%s name=%s duration_ms=%d success=%s item_count=%d warning_count=%d provider=%s search_used=%s",
            canonical_market,
            canonical_code,
            canonical_name,
            int((time.perf_counter() - started_at) * 1000),
            True,
            len(items),
            len(warnings),
            getattr(self.search_provider, "name", "none"),
            True,
        )
        return {"items": items, "warnings": warnings, "data_stage": "llm_initial"}

    def _search_center_documents(self, *, market: str, code: str, name: str) -> tuple[List[SearchDocument], List[str]]:
        queries = []
        if name:
            queries.append(f"{market} {code} {name} stock listed company")
        queries.append(f"{market} {code} listed company ticker official name")
        return self._search_queries(queries, max_results=4)

    def _search_relation_documents(self, *, market: str, code: str, name: str, sector: str) -> tuple[List[SearchDocument], List[str]]:
        queries = [f"{name or code} {market} {code} industry supply chain suppliers customers"]
        if sector:
            queries.append(f"{name or code} {sector} listed company market cap change percent")
        return self._search_queries(queries, max_results=6)

    def _search_queries(self, queries: List[str], *, max_results: int) -> tuple[List[SearchDocument], List[str]]:
        provider = self.search_provider
        if provider is None or not getattr(provider, "is_available", False):
            return [], []
        docs: List[SearchDocument] = []
        warnings: List[str] = []
        seen = set()
        for query in queries:
            if not query:
                continue
            try:
                result = provider.search(query, max_results)
            except Exception as exc:
                if "search_unavailable" not in warnings:
                    warnings.append("search_unavailable")
                continue
            for doc in result:
                url = str(getattr(doc, "url", "") or "")
                key = (url, str(getattr(doc, "title", "") or ""))
                if key in seen:
                    continue
                seen.add(key)
                docs.append(doc)
                if len(docs) >= max_results:
                    return docs, warnings
        return docs, warnings

    @staticmethod
    def _filter_existing_patches(
        *,
        center: Dict[str, Any],
        graph_items: List[Dict[str, Any]],
        symbols: List[str],
    ) -> List[Dict[str, Any]]:
        allowed = {str(symbol or "").strip() for symbol in symbols if str(symbol or "").strip()}
        items: List[Dict[str, Any]] = []
        center_symbol = symbol_id(str(center.get("market") or "").strip().upper(), str(center.get("code") or "").strip())
        if center_symbol in allowed:
            items.append(TopologyEnrichmentService._patch_payload(center, center_symbol))
        for item in graph_items:
            symbol = symbol_id(str(item.get("market") or "").strip().upper(), str(item.get("code") or "").strip())
            if symbol not in allowed or not str(item.get("code") or "").strip():
                continue
            items.append(TopologyEnrichmentService._patch_payload(item, symbol))
        return items

    @staticmethod
    def _patch_payload(item: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        field_sources = {}
        for key, value in dict(item.get("field_sources") or {}).items():
            if str(key or "").strip() and str(value or "").strip():
                field_sources[str(key).strip()] = "search_enrich"
        return {
            "id": symbol,
            "symbol": symbol,
            "market": str(item.get("market") or "").strip().upper(),
            "code": str(item.get("code") or "").strip(),
            "name": str(item.get("name") or "").strip(),
            "sector": str(item.get("sector") or "").strip(),
            "industry": str(item.get("industry") or "").strip(),
            "market_cap": item.get("market_cap"),
            "market_cap_str": str(item.get("market_cap_str") or "").strip(),
            "pct_chg": item.get("pct_chg"),
            "field_sources": field_sources,
            "field_confidence": {
                field: confidence
                for field in ("name", "sector", "industry", "market_cap", "pct_chg")
                for confidence in [item.get("confidence")]
                if confidence is not None and field in field_sources
            },
            "data_gaps": [
                field for field in ("name", "sector", "industry", "market_cap", "pct_chg")
                if item.get(field) in (None, "", "--")
            ],
            "data_stage": "llm_initial",
        }
