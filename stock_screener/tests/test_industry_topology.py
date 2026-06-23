#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产业拓扑单元测试。"""
import unittest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from industry_topology.models import (
    RelationType, Direction, TopologyNode, TopologyEdge, CachedRelation,
)
from industry_topology.resolver import compute_size_level, format_market_cap, NodeResolver


class TestModels(unittest.TestCase):
    def test_relation_type_members(self):
        self.assertIn("supplier", [r.value for r in RelationType])
        self.assertIn("customer", [r.value for r in RelationType])
        self.assertIn("competitor", [r.value for r in RelationType])
        self.assertIn("other", [r.value for r in RelationType])
        self.assertEqual(len(list(RelationType)), 13)  # 12 标签 + other

    def test_direction_members(self):
        self.assertEqual({d.value for d in Direction}, {"upstream", "downstream", "peer"})

    def test_topology_node_fields(self):
        n = TopologyNode(
            code="US.NVDA", name="英伟达", market="US", sector="半导体",
            pct_chg=2.13, market_cap_str="2.8万亿", size_level=6,
            expanded=False, stale=False, is_center=True,
        )
        self.assertEqual(n.size_level, 6)
        self.assertTrue(n.is_center)

    def test_topology_edge_fields(self):
        e = TopologyEdge(
            source="US.NVDA", target="300308", direction=Direction.UPSTREAM,
            relation=RelationType.SUPPLIER, label="供应商·提供800G光模块", evidence="提供800G光模块",
        )
        self.assertEqual(e.direction, Direction.UPSTREAM)

    def test_cached_relation_fields(self):
        r = CachedRelation(
            source_code="US.NVDA", source_market="US",
            peer_code="300308", peer_market="A", peer_name="中际旭创",
            relation=RelationType.SUPPLIER, direction=Direction.UPSTREAM,
            evidence="提供800G光模块", expires_at=None, is_empty=False,
        )
        self.assertEqual(r.peer_name, "中际旭创")


from unittest.mock import MagicMock
from db import MarketDatabase


class TestSchema(unittest.TestCase):
    def test_init_industry_topology_schema_exists(self):
        self.assertTrue(hasattr(MarketDatabase, "init_industry_topology_schema"))

    def test_init_industry_topology_schema_executes_create(self):
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = MagicMock()
        cursor = MagicMock()
        db.conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        db.conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        db.init_industry_topology_schema()
        executed = [c.args[0] for c in cursor.execute.call_args_list if c.args]
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS industry_relations" in s for s in executed))
        self.assertTrue(any("expires_at" in s for s in executed))
        self.assertTrue(any("is_empty" in s for s in executed))
        self.assertTrue(any("UNIQUE KEY" in s and "source_code" in s for s in executed))


class TestSizeLevel(unittest.TestCase):
    # 市值单位：元
    def test_level1_under_50yi(self):
        self.assertEqual(compute_size_level(30 * 1e8), 1)   # 30亿
    def test_level2_50_200yi(self):
        self.assertEqual(compute_size_level(100 * 1e8), 2)  # 100亿
    def test_level3_200_1000yi(self):
        self.assertEqual(compute_size_level(500 * 1e8), 3)  # 500亿
    def test_level4_1000_3000yi(self):
        self.assertEqual(compute_size_level(2000 * 1e8), 4) # 2000亿
    def test_level5_3000yi_1wan(self):
        self.assertEqual(compute_size_level(5000 * 1e8), 5) # 5000亿
    def test_level6_over_1wan(self):
        self.assertEqual(compute_size_level(2 * 1e12), 6)   # 2万亿
    def test_none_returns_min(self):
        self.assertEqual(compute_size_level(None), 1)


class TestFormatMarketCap(unittest.TestCase):
    def test_wan_yi(self):
        self.assertEqual(format_market_cap(2 * 1e12), "2.0万亿")
    def test_yi(self):
        self.assertEqual(format_market_cap(850 * 1e8), "850亿")
    def test_none(self):
        self.assertEqual(format_market_cap(None), "--")


if __name__ == "__main__":
    unittest.main()
