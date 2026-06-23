#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产业拓扑单元测试。"""
import unittest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from industry_topology.models import (
    RelationType, Direction, TopologyNode, TopologyEdge, CachedRelation,
)


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


if __name__ == "__main__":
    unittest.main()
