#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公司维度关系缓存 + networkx 图遍历。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import networkx as nx

from .models import CachedRelation, Direction, RelationType

_TTL_DAYS_DEFAULT = 7


class GraphCache:
    def __init__(self, db: Any):
        self.db = db

    def get_relations(self, source_code: str, market: str) -> Optional[List[CachedRelation]]:
        """命中返回列表（0 LLM）；过期/无返回 None。"""
        sql = (
            "SELECT source_code, source_market, peer_code, peer_market, peer_name, "
            "relation, direction, evidence, is_empty, created_at, updated_at, expires_at "
            "FROM industry_relations WHERE source_code=%s AND source_market=%s AND expires_at > NOW()"
        )
        with self.db.conn.cursor() as cursor:
            cursor.execute(sql, [source_code, market])
            rows = cursor.fetchall() or []
        if not rows:
            return None
        return [self._row_to_relation(r) for r in rows]

    def save_relations(self, source_code: str, market: str, relations: List[CachedRelation],
                       provider: str, model: str, ttl_days: int = _TTL_DAYS_DEFAULT) -> None:
        """整组替换：DELETE 旧组 + INSERT 新组。"""
        expires = datetime.now() + timedelta(days=ttl_days)
        with self.db.conn.cursor() as cursor:
            cursor.execute("DELETE FROM industry_relations WHERE source_code=%s", [source_code])
            if not relations:
                # 空结果也写一条 is_empty=1，防反复空推理浪费额度
                cursor.execute(
                    "INSERT INTO industry_relations "
                    "(source_code, source_market, peer_code, peer_market, peer_name, "
                    "relation, direction, evidence, is_empty, expires_at, llm_provider, llm_model) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s)",
                    [source_code, market, "", "", "", "other", "peer", "", expires, provider, model],
                )
                return
            for r in relations:
                cursor.execute(
                    "INSERT INTO industry_relations "
                    "(source_code, source_market, peer_code, peer_market, peer_name, "
                    "relation, direction, evidence, is_empty, expires_at, llm_provider, llm_model) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [source_code, market, r.peer_code, r.peer_market, r.peer_name,
                     r.relation.value, r.direction.value, r.evidence, 0, expires, provider, model],
                )

    def build_graph(self, cached_map: Dict[str, List[CachedRelation]]) -> nx.DiGraph:
        """把多 source 的缓存关系组装成有向图。节点属性带 source_code/expires_at。"""
        g = nx.DiGraph()
        for source_code, rels in cached_map.items():
            g.add_node(source_code, source_code=source_code, stale=False)
            for r in rels:
                if r.is_empty:
                    continue
                g.add_node(r.peer_code, source_code=r.peer_code, stale=False)
                g.add_edge(source_code, r.peer_code, relation=r.relation, direction=r.direction, evidence=r.evidence)
        return g

    def reachable_within(self, graph: nx.DiGraph, source_code: str, depth: int) -> List[str]:
        """N 度内可达节点（含 source 自身）。"""
        lengths = nx.single_source_shortest_path_length(graph, source_code, cutoff=depth)
        return list(lengths.keys())

    def stale_nodes(self, graph: nx.DiGraph, codes: List[str], now: Optional[datetime] = None) -> List[str]:
        """返回未在 graph 中出现（无缓存）的节点。过期判定在 get_relations 返回 None 时已体现。"""
        now = now or datetime.now()
        return [c for c in codes if c not in graph.nodes]

    @staticmethod
    def _row_to_relation(row) -> CachedRelation:
        return CachedRelation(
            source_code=row[0], source_market=row[1],
            peer_code=row[2], peer_market=row[3], peer_name=row[4],
            relation=RelationType(row[5]) if row[5] in RelationType._value2member_map_ else RelationType.OTHER,
            direction=Direction(row[6]) if row[6] in Direction._value2member_map_ else Direction.PEER,
            evidence=row[7] or "", is_empty=bool(row[8]),
            expires_at=row[11],
        )
