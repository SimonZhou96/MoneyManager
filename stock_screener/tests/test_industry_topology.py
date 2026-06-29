#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产业拓扑单元测试。"""
import unittest
import sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from industry_topology.models import (
    RelationType, Direction, TopologyNode, TopologyEdge, CachedRelation,
)
from industry_topology.resolver import compute_size_level, format_market_cap, NodeResolver
import industry_topology.service as topology_service_module
from industry_topology.service import TopologyService
from industry_topology.symbols import parse_topology_symbol, symbol_id
from stock_terminal.models import BlockStatus, QuoteSnapshot
from web.topology import (
    GraphRequest,
    QuoteRefreshRequest,
    _pct_chg_from_kline_rows,
    _quote_from_cache,
    _quote_status_item,
    _schedule_relation_generation,
    _TOPOLOGY_GENERATION_IN_FLIGHT,
    existing_graph as topology_existing_graph_route,
    graph as topology_graph_route,
    quotes as topology_quotes_route,
    refresh_quotes as topology_refresh_quotes_route,
    search_enrich as topology_search_enrich_route,
)
from web.topology_tasks import TopologyTaskRegistry, TopologyTaskRunner


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


class TestTopologyQuoteSymbols(unittest.TestCase):
    def test_parse_a_share_exchange_symbols(self):
        cases = {
            "SH:603290.SH": ("SH:603290.SH", "A", "SH.603290", "SH", "603290.SS"),
            "SZ:002600.SZ": ("SZ:002600.SZ", "A", "SZ.002600", "SZ", "002600.SZ"),
            "A:600745.SH": ("A:600745.SH", "A", "SH.600745", "SH", "600745.SS"),
        }
        for raw, expected in cases.items():
            parsed = parse_topology_symbol(raw)
            self.assertFalse(parsed["skipped"])
            self.assertEqual(parsed["symbol"], expected[0])
            self.assertEqual(parsed["market"], expected[1])
            self.assertEqual(parsed["code"], expected[2])
            self.assertEqual(parsed["exchange"], expected[3])
            self.assertEqual(parsed["provider_symbols"]["yfinance"], expected[4])

    def test_parse_global_exchange_symbols(self):
        cases = {
            "US:QCOM.US": ("US:QCOM.US", "US", "US.QCOM", "QCOM"),
            "NYSE:SONY.NYSE": ("NYSE:SONY.NYSE", "US", "US.SONY", "SONY"),
            "TY:6758.TY": ("TY:6758.TY", "JP", "JP.6758", "6758.T"),
            "TW:2454.TW": ("TW:2454.TW", "TW", "TW.2454", "2454.TW"),
            "KS:005930.KS": ("KS:005930.KS", "KR", "KR.005930", "005930.KS"),
        }
        for raw, expected in cases.items():
            parsed = parse_topology_symbol(raw)
            self.assertFalse(parsed["skipped"])
            self.assertEqual(parsed["symbol"], expected[0])
            self.assertEqual(parsed["market"], expected[1])
            self.assertEqual(parsed["code"], expected[2])
            self.assertEqual(parsed["provider_symbols"]["yfinance"], expected[3])

    def test_parse_unsupported_market_is_skipped(self):
        parsed = parse_topology_symbol("LSE:VOD.L")

        self.assertTrue(parsed["skipped"])
        self.assertEqual(parsed["status"], "skipped")
        self.assertIn("unsupported", parsed["error"])


class TestTopologyQuotePayload(unittest.TestCase):
    def test_quote_from_cache_returns_name_and_formatted_market_cap(self):
        parsed = parse_topology_symbol("US:TSM.US")
        quote = QuoteSnapshot(
            market="US",
            code="US.TSM",
            name="台积电",
            price=439.215,
            change_percent=1.2,
            market_cap=1.23e12,
            fetched_at=datetime(2026, 6, 23, 15, 16, tzinfo=timezone.utc),
            source="fake",
        )
        status = BlockStatus(status="cached", source="fake", fetched_at=quote.fetched_at)

        item = _quote_from_cache(parsed, quote, status)

        self.assertEqual(item["name"], "台积电")
        self.assertEqual(item["price"], 439.215)
        self.assertEqual(item["pct_chg"], 1.2)
        self.assertEqual(item["market_cap"], 1.23e12)
        self.assertEqual(item["market_cap_str"], "1.2万亿")
        self.assertEqual(item["data_gaps"], [])
        self.assertEqual(item["field_errors"], {})

    def test_quote_from_cache_explains_missing_quote_fields(self):
        parsed = parse_topology_symbol("US:NVDA.US")
        quote = QuoteSnapshot(
            market="US",
            code="US.NVDA",
            name="NVIDIA",
            price=439.215,
            change_percent=None,
            market_cap=None,
            fetched_at=datetime(2026, 6, 23, 15, 16, tzinfo=timezone.utc),
            source="fake",
        )
        status = BlockStatus(status="cached", source="fake", fetched_at=quote.fetched_at)

        item = _quote_from_cache(parsed, quote, status)

        self.assertEqual(item["price"], 439.215)
        self.assertIn("pct_chg", item["data_gaps"])
        self.assertIn("market_cap", item["data_gaps"])
        self.assertEqual(item["field_errors"]["pct_chg"], "provider_missing_field")
        self.assertEqual(item["field_errors"]["market_cap"], "provider_missing_field")

    def test_quote_status_item_uses_stable_display_fallbacks(self):
        parsed = parse_topology_symbol("SZ:300207.SZ")

        item = _quote_status_item(parsed, "pending")

        self.assertEqual(item["name"], "SZ.300207")
        self.assertIsNone(item["market_cap"])
        self.assertEqual(item["market_cap_str"], "未知")
        self.assertEqual(item["field_errors"]["price"], "quote_pending")
        self.assertEqual(item["field_errors"]["pct_chg"], "quote_pending")
        self.assertEqual(item["field_errors"]["market_cap"], "quote_pending")

    def test_quotes_fills_missing_pct_chg_from_kline_change_rate(self):
        fetched_at = datetime(2026, 6, 23, 15, 16, tzinfo=timezone.utc)
        quote = QuoteSnapshot(
            market="US",
            code="US.NVDA",
            name="NVIDIA",
            price=439.215,
            change_percent=None,
            market_cap=1.23e12,
            fetched_at=fetched_at,
            source="fake",
        )
        service = FakeQuoteKlineService(
            quote=quote,
            status=BlockStatus(status="cached", source="fake", fetched_at=fetched_at),
            kline_rows=[{"close": 430.0, "change_rate": None}, {"close": 439.215, "change_rate": 2.14}],
        )

        result = topology_quotes_route("US:NVDA.US", service)
        item = result["data"]["items"][0]

        self.assertEqual(item["pct_chg"], 2.14)
        self.assertEqual(item["field_sources"]["pct_chg"], "kline")
        self.assertNotIn("pct_chg", item["data_gaps"])
        self.assertNotIn("pct_chg", item["field_errors"])

    def test_pct_chg_from_kline_rows_computes_from_two_closes(self):
        pct_chg = _pct_chg_from_kline_rows({"rows": [{"close": 100}, {"close": 103.456}]})

        self.assertEqual(pct_chg, 3.46)

    def test_quotes_keeps_provider_missing_field_when_kline_is_insufficient(self):
        fetched_at = datetime(2026, 6, 23, 15, 16, tzinfo=timezone.utc)
        quote = QuoteSnapshot(
            market="US",
            code="US.NVDA",
            name="NVIDIA",
            price=439.215,
            change_percent=None,
            market_cap=1.23e12,
            fetched_at=fetched_at,
            source="fake",
        )
        service = FakeQuoteKlineService(
            quote=quote,
            status=BlockStatus(status="cached", source="fake", fetched_at=fetched_at),
            kline_rows=[{"close": None}],
        )

        result = topology_quotes_route("US:NVDA.US", service)
        item = result["data"]["items"][0]

        self.assertIn("pct_chg", item["data_gaps"])
        self.assertEqual(item["field_errors"]["pct_chg"], "provider_missing_field")

    def test_quotes_recomputes_gaps_after_fundamentals_fill_market_cap(self):
        fetched_at = datetime(2026, 6, 23, 15, 16, tzinfo=timezone.utc)
        quote = QuoteSnapshot(
            market="US",
            code="US.AMZN",
            name="Amazon",
            price=185.0,
            change_percent=None,
            market_cap=None,
            fetched_at=fetched_at,
            source="fake",
        )
        service = FakeQuoteKlineService(
            quote=quote,
            status=BlockStatus(status="cached", source="fake", fetched_at=fetched_at),
            kline_rows=[{"close": 180.0}, {"close": 185.0}],
            fundamentals=[{"code": "US.AMZN", "name": "Amazon", "sector": "Consumer", "industry": "Retail", "market_cap": 2.1e12}],
        )

        result = topology_quotes_route("US:AMZN.US", service)
        item = result["data"]["items"][0]

        self.assertEqual(item["market_cap"], 2.1e12)
        self.assertEqual(item["pct_chg"], 2.78)
        self.assertEqual(item["data_gaps"], [])
        self.assertEqual(item["field_errors"], {})


class FakeQuoteRepository:
    def __init__(self, quote, status, fundamentals=None):
        self.quote = quote
        self.status = status
        self.db = self
        self.fundamentals = fundamentals or []

    def get_quote(self, market, code, now=None):
        return self.quote, self.status

    def get_stocks_by_codes(self, market, codes, include_fundamentals=False):
        code_set = {str(code).upper() for code in codes}
        return [row for row in self.fundamentals if str(row.get("code") or "").upper() in code_set]


class FakeQuoteKlineService:
    def __init__(self, quote, status, kline_rows, fundamentals=None):
        self.repository = FakeQuoteRepository(quote, status, fundamentals=fundamentals)
        self.kline_rows = kline_rows

    def now(self):
        return datetime(2026, 6, 23, 15, 16, tzinfo=timezone.utc)

    def get_klines(self, market, code, timeframe, limit=500, before=None):
        return {"rows": self.kline_rows[-limit:]}


class TestTopologyServiceMerge(unittest.TestCase):
    def test_merge_node_info_preserves_existing_chinese_name(self):
        merged = TopologyService._merge_node_info(
            {"name": "英伟达", "field_sources": {"name": "llm_search"}},
            {"name": "NVIDIA", "field_sources": {"name": "resolver"}},
            prefer_llm=False,
        )

        self.assertEqual(merged["name"], "英伟达")
        self.assertEqual(merged["field_sources"]["name"], "llm_search")

    def test_merge_node_info_allows_llm_chinese_name_over_resolver_english(self):
        merged = TopologyService._merge_node_info(
            {"name": "NVIDIA", "field_sources": {"name": "resolver"}},
            {"name": "英伟达", "field_sources": {"name": "llm_search"}, "confidence": 0.9},
            prefer_llm=False,
        )

        self.assertEqual(merged["name"], "英伟达")
        self.assertEqual(merged["field_sources"]["name"], "llm_search")


class FakeTaskService:
    def __init__(self, graph=None, enrich=None, error=None):
        self.layer_calls = []
        self.graph = graph or {
            "center": {"id": "US:NVDA", "code": "NVDA", "market": "US", "name": "NVIDIA", "data_stage": "llm_initial"},
            "nodes": [
                {"id": "US:NVDA", "code": "NVDA", "market": "US", "name": "NVIDIA", "is_center": True, "data_stage": "llm_initial", "depth": 0},
                {"id": "US:TSM", "code": "TSM", "market": "US", "name": "TSMC", "is_center": False, "data_stage": "llm_initial", "depth": 1},
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

    def iter_depth_graphs(self, code, market, depth=3, center_name=""):
        if self.error:
            raise self.error
        center = {
            "id": "US:NVDA", "code": "NVDA", "market": "US", "name": "NVIDIA",
            "is_center": True, "data_stage": "llm_initial", "depth": 0,
        }
        tsm = {
            "id": "US:TSM", "code": "TSM", "market": "US", "name": "TSM",
            "is_center": False, "data_stage": "llm_initial", "depth": 1,
        }
        asml = {
            "id": "US:ASML", "code": "ASML", "market": "US", "name": "ASML",
            "is_center": False, "data_stage": "llm_initial", "depth": 2,
        }
        zeiss = {
            "id": "US:ZEISS", "code": "ZEISS", "market": "US", "name": "ZEISS",
            "is_center": False, "data_stage": "llm_initial", "depth": 3,
        }
        layers = [
            ([center, tsm], [{"source": "US:NVDA", "target": "US:TSM", "direction": "upstream", "relation": "foundry_packaging", "label": "代工"}]),
            ([center, tsm, asml], [
                {"source": "US:NVDA", "target": "US:TSM", "direction": "upstream", "relation": "foundry_packaging", "label": "代工"},
                {"source": "US:TSM", "target": "US:ASML", "direction": "upstream", "relation": "equipment", "label": "设备"},
            ]),
            ([center, tsm, asml, zeiss], [
                {"source": "US:NVDA", "target": "US:TSM", "direction": "upstream", "relation": "foundry_packaging", "label": "代工"},
                {"source": "US:TSM", "target": "US:ASML", "direction": "upstream", "relation": "equipment", "label": "设备"},
                {"source": "US:ASML", "target": "US:ZEISS", "direction": "upstream", "relation": "component", "label": "组件"},
            ]),
        ]
        for layer, (nodes, edges) in enumerate(layers[:depth], 1):
            self.layer_calls.append([node["code"] for node in nodes if node["depth"] == layer - 1])
            yield {
                "center": center,
                "nodes": nodes,
                "edges": edges,
                "stats": {
                    "requested_depth": depth,
                    "depth": depth,
                    "reached_depth": layer,
                    "expanding_depth": layer,
                    "llm_calls": layer,
                    "relation_status": "generating" if layer < depth else "initial_ready",
                    "data_stage": "llm_initial",
                },
                "warnings": [],
            }

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
        task = registry.create_task(code="NVDA", market="US", depth=1, center_name="NVIDIA", quote_mode="llm_initial")
        service = FakeTaskService()
        runner = TopologyTaskRunner(registry, lambda: service)

        runner.run(task.task_id)
        snapshot = registry.get_snapshot(task.task_id)

        self.assertEqual(snapshot["stage"], "done")
        self.assertEqual(snapshot["progress_pct"], 100)
        self.assertEqual(len(snapshot["graph"]["nodes"]), 2)
        self.assertEqual(snapshot["graph"]["stats"]["reached_depth"], 1)
        self.assertEqual(service.layer_calls, [["NVDA"]])

    def test_runner_updates_snapshot_after_each_depth_layer(self):
        registry = TopologyTaskRegistry(ttl_seconds=60)
        task = registry.create_task(code="NVDA", market="US", depth=3, center_name="NVIDIA", quote_mode="llm_initial")
        snapshots = []
        original_update = registry.update

        def recording_update(*args, **kwargs):
            updated = original_update(*args, **kwargs)
            if kwargs.get("stage") == "depth_expanding" and kwargs.get("graph"):
                snapshots.append(registry.get_snapshot(task.task_id))
            return updated

        registry.update = recording_update
        service = FakeTaskService()
        runner = TopologyTaskRunner(registry, lambda: service)

        runner.run(task.task_id)
        final = registry.get_snapshot(task.task_id)

        self.assertEqual([snap["graph"]["stats"]["reached_depth"] for snap in snapshots], [1, 2, 3])
        self.assertEqual([len(snap["graph"]["nodes"]) for snap in snapshots], [2, 3, 4])
        self.assertEqual(service.layer_calls, [["NVDA"], ["TSM"], ["ASML"]])
        self.assertEqual(final["stage"], "done")

    def test_runner_marks_partial_when_requested_depth_is_not_reached(self):
        registry = TopologyTaskRegistry(ttl_seconds=60)
        task = registry.create_task(code="NVDA", market="US", depth=3, center_name="NVIDIA", quote_mode="llm_initial")
        service = FakeTaskService(graph={
            "center": {"id": "US:NVDA", "code": "NVDA", "market": "US", "name": "NVIDIA", "data_stage": "llm_initial", "depth": 0},
            "nodes": [
                {"id": "US:NVDA", "code": "NVDA", "market": "US", "name": "NVIDIA", "is_center": True, "data_stage": "llm_initial", "depth": 0},
                {"id": "US:TSM", "code": "TSM", "market": "US", "name": "TSM", "is_center": False, "data_stage": "llm_initial", "depth": 1},
            ],
            "edges": [{"source": "US:NVDA", "target": "US:TSM", "direction": "upstream", "relation": "foundry_packaging", "label": "代工"}],
            "stats": {"requested_depth": 3, "depth": 3, "reached_depth": 1, "relation_status": "initial_ready", "data_stage": "llm_initial"},
            "warnings": [],
        })

        def one_layer(*args, **kwargs):
            yield service.graph

        service.iter_depth_graphs = one_layer
        runner = TopologyTaskRunner(registry, lambda: service)

        runner.run(task.task_id)
        snapshot = registry.get_snapshot(task.task_id)

        self.assertEqual(snapshot["stage"], "partial")
        self.assertLess(snapshot["progress_pct"], 100)
        self.assertIn("requested depth 3", snapshot["message"])
        self.assertIn("topology_depth_incomplete", snapshot["warnings"])

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
        self.assertTrue(any("MODIFY COLUMN direction VARCHAR(16) NOT NULL" in s for s in executed))


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


import networkx as nx
from datetime import datetime, timedelta
from industry_topology.cache import GraphCache


def _make_relation(peer_code, direction=Direction.UPSTREAM, relation=RelationType.SUPPLIER,
                   expires=None, is_empty=False, peer_market="A"):
    return CachedRelation(
        source_code="US.NVDA", source_market="US",
        peer_code=peer_code, peer_market=peer_market, peer_name=f"公司{peer_code}",
        relation=relation, direction=direction, evidence="测试",
        expires_at=expires, is_empty=is_empty,
    )


def _make_db_mock():
    """Create a real MarketDatabase instance (bypass __init__) with mocked conn."""
    db = MarketDatabase.__new__(MarketDatabase)
    db.conn = MagicMock()
    cursor = MagicMock()
    db.conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    db.conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return db, cursor


class TestGraphCache(unittest.TestCase):
    def test_get_relations_hit(self):
        db, cursor = _make_db_mock()
        cursor.fetchall.return_value = [
            ("US.NVDA", "US", "300308", "A", "中际旭创", "supplier", "upstream", "提供光模块", 0,
             datetime.now(), datetime.now(), datetime.now() + timedelta(days=7), "deepseek", "v4"),
        ]
        gc = GraphCache(db)
        rels = gc.get_relations("US.NVDA", "US")
        self.assertIsNotNone(rels)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0].peer_code, "300308")

    def test_get_relations_expired_returns_none(self):
        db, cursor = _make_db_mock()
        cursor.fetchall.return_value = []  # 过期被 WHERE 过滤
        gc = GraphCache(db)
        self.assertIsNone(gc.get_relations("US.NVDA", "US"))

    def test_save_relations_replaces_group(self):
        db, cursor = _make_db_mock()
        gc = GraphCache(db)
        rels = [_make_relation("300308"), _make_relation("300394", direction=Direction.DOWNSTREAM, relation=RelationType.CUSTOMER)]
        gc.save_relations("US.NVDA", "US", rels, provider="deepseek", model="v4", ttl_days=7)
        # 应先 DELETE WHERE source_code 再 INSERT
        executed = [c.args[0] for c in cursor.execute.call_args_list if c.args]
        self.assertTrue(any("DELETE FROM industry_relations" in s and "source_code=%s" in s for s in executed))
        self.assertTrue(any("INSERT INTO industry_relations" in s for s in executed))

    def test_reachable_within_depth(self):
        db, _ = _make_db_mock()
        gc = GraphCache(db)
        g = nx.DiGraph()
        g.add_edge("A", "B"); g.add_edge("B", "C"); g.add_edge("C", "D")
        self.assertEqual(set(gc.reachable_within(g, "A", 1)), {"A", "B"})
        self.assertEqual(set(gc.reachable_within(g, "A", 3)), {"A", "B", "C", "D"})

    def test_build_graph_carries_attrs(self):
        db, _ = _make_db_mock()
        gc = GraphCache(db)
        cached = {
            "US.NVDA": [_make_relation("300308"), _make_relation("300394", Direction.DOWNSTREAM, RelationType.CUSTOMER)],
            "300308": [_make_relation("002156", direction=Direction.UPSTREAM, relation=RelationType.FOUNDRY_PACKAGING)],
        }
        g = gc.build_graph(cached)
        self.assertTrue(g.has_edge("US.NVDA", "300308"))
        self.assertEqual(g.nodes["US.NVDA"]["source_code"], "US.NVDA")


from industry_topology.relation_engine import RelationEngine, RelationEngineError
from industry_topology.enrichment import TopologyEnrichmentService
from signal_analysis.models import SearchDocument


class FakeLLMProvider:
    name = "fake"
    is_available = True
    def __init__(self, payload): self.payload = payload
    def complete_json(self, *, system_prompt, user_prompt, json_schema):
        return self.payload


class SequenceLLMProvider:
    name = "fake"
    is_available = True

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def complete_json(self, *, system_prompt, user_prompt, json_schema):
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        if isinstance(payload, Exception):
            raise payload
        return payload


class FakeResolver:
    def resolve_many(self, codes, market):
        return {c: {"code": c, "name": f"公司{c}", "market": market, "sector": "板块", "market_cap": 1e10, "pct_chg": 1.0} for c in codes}


class FakeSearchProvider:
    name = "fake_search"
    is_available = True

    def __init__(self, docs=None, error=None):
        self.docs = list(docs or [])
        self.error = error
        self.calls = []

    def search(self, query: str, max_results: int):
        self.calls.append((query, max_results))
        if self.error:
            raise self.error
        return self.docs[:max_results]


class TestRelationEngine(unittest.TestCase):
    def test_user_prompt_includes_numeric_and_confidence_constraints(self):
        prompt = RelationEngine._user_prompt("英伟达", "US", "NVDA", "半导体")

        self.assertIn("pct_chg", prompt)
        self.assertIn("confidence", prompt)
        self.assertIn("不允许根据经验臆造实时数字", prompt)
        self.assertIn("若不确定 market_cap 或 pct_chg，必须返回 null", prompt)

    def test_user_prompts_require_chinese_display_names(self):
        graph_prompt = RelationEngine._user_prompt("NVIDIA", "US", "NVDA", "Semiconductors")
        center_prompt = RelationEngine._center_user_prompt(
            request_market="US",
            request_code="NVDA",
            request_name="NVIDIA",
            request_sector="Semiconductors",
            search_documents=[],
        )
        batch_prompt = RelationEngine._batch_user_prompt([
            {"code": "NVDA", "market": "US", "name": "NVIDIA", "sector": "Semiconductors"}
        ])

        for prompt in (graph_prompt, center_prompt, batch_prompt):
            self.assertIn("name 优先返回中文常用名", prompt)
            self.assertIn("aliases", prompt)
            self.assertIn("不得为了中文化而编造", prompt)

    def test_parse_valid_json(self):
        payload = {"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "提供光模块"},
            {"code": "601138", "name": "工业富联", "market": "A", "direction": "downstream", "relation": "odm", "evidence": "AI服务器代工"},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(len(rels), 2)
        self.assertEqual(rels[0].peer_code, "300308")
        self.assertEqual(rels[0].direction.value, "upstream")

    def test_invalid_json_raises(self):
        eng = RelationEngine(FakeResolver(), FakeLLMProvider({"not_items": []}))
        with self.assertRaises(RelationEngineError):
            eng.infer("US.NVDA", "英伟达", "US", "半导体")

    def test_out_of_vocab_relation_falls_back_to_other(self):
        payload = {"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "mystery_rel", "evidence": "未知"},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(rels[0].relation, RelationType.OTHER)

    def test_invalid_direction_falls_back_to_peer(self):
        payload = {"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "sideways", "relation": "supplier", "evidence": "提供光模块"},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(rels[0].direction, Direction.PEER)

    def test_dedup_same_source_peer_relation(self):
        payload = {"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "a"},
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "b"},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(len(rels), 1)

    def test_conflicting_direction_keeps_both(self):
        payload = {"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "a"},
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "downstream", "relation": "customer", "evidence": "b"},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(len(rels), 2)

    def test_empty_result_returns_empty_list(self):
        eng = RelationEngine(FakeResolver(), FakeLLMProvider({"items": []}))
        rels = eng.infer("US.NVDA", "英伟达", "US", "半导体")
        self.assertEqual(rels, [])

    def test_infer_batch_parses_multiple_source_groups(self):
        payload = {"groups": [
            {"source_code": "US.NVDA", "source_market": "US", "items": [
                {"code": "TSM", "name": "台积电", "market": "US", "direction": "upstream", "relation": "foundry_packaging", "evidence": "先进制程代工"},
            ]},
            {"source_code": "TSM", "source_market": "US", "items": [
                {"code": "ASML", "name": "ASML", "market": "US", "direction": "upstream", "relation": "equipment", "evidence": "光刻机设备"},
            ]},
        ]}
        eng = RelationEngine(FakeResolver(), FakeLLMProvider(payload))

        result = eng.infer_batch([
            {"code": "US.NVDA", "market": "US", "name": "NVIDIA", "sector": "Semiconductors"},
            {"code": "TSM", "market": "US", "name": "Taiwan Semiconductor", "sector": "Semiconductors"},
        ])

        self.assertEqual(set(result.keys()), {("US", "US.NVDA"), ("US", "TSM")})
        self.assertEqual(result[("US", "US.NVDA")][0].peer_code, "TSM")
        self.assertEqual(result[("US", "TSM")][0].peer_code, "ASML")


class TestTopologyEnrichmentService(unittest.TestCase):
    def test_unresolved_center_causes_initial_snapshot_failure(self):
        llm = SequenceLLMProvider([
            {
                "resolved": False,
                "market": "US",
                "code": "NVDA",
                "name": "",
                "aliases": [],
                "sector": "",
                "industry": "",
                "reason": "ambiguous",
                "confidence": 0.2,
            }
        ])
        engine = RelationEngine(FakeResolver(), llm)
        enricher = TopologyEnrichmentService(engine, FakeSearchProvider())

        with self.assertRaises(RelationEngineError):
            enricher.build_initial_snapshot(market="US", code="NVDA", name="NVIDIA", sector="半导体")

    def test_build_initial_snapshot_does_not_depend_on_search(self):
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
                    {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "提供光模块"},
                ],
                "warnings": [],
            },
        ])
        engine = RelationEngine(FakeResolver(), llm)
        enricher = TopologyEnrichmentService(engine, FakeSearchProvider(error=RuntimeError("search down")))

        snapshot = enricher.build_initial_snapshot(market="US", code="NVDA", name="NVIDIA", sector="半导体")

        self.assertEqual(snapshot["center"]["name"], "英伟达")
        self.assertEqual(snapshot["items"][0]["code"], "300308")
        self.assertEqual(snapshot["warnings"], [])

    def test_search_enrich_failure_only_records_warning(self):
        llm = SequenceLLMProvider([])
        engine = RelationEngine(FakeResolver(), llm)
        enricher = TopologyEnrichmentService(engine, FakeSearchProvider(error=RuntimeError("search down")))

        result = enricher.enrich_graph_fields(
            center={"market": "US", "code": "NVDA", "name": "英伟达", "sector": "AI芯片", "industry": "半导体"},
            symbols=["US:NVDA", "A:300308"],
        )

        self.assertEqual(result["items"], [])
        self.assertIn("search_unavailable", result["warnings"])

    def test_search_enrich_returns_existing_node_patches_only(self):
        llm = SequenceLLMProvider([
            {
                "center": {
                    "resolved": True,
                    "market": "US",
                    "code": "NVDA",
                    "name": "英伟达",
                    "aliases": ["NVIDIA"],
                    "sector": "AI芯片",
                    "industry": "半导体",
                    "market_cap": 2.9e12,
                    "pct_chg": 4.1,
                    "reason": "resolved",
                    "confidence": 0.95,
                    "field_sources": {"market_cap": "llm_search", "pct_chg": "llm_search"},
                },
                "items": [
                    {
                        "code": "300308",
                        "name": "中际旭创",
                        "market": "A",
                        "direction": "upstream",
                        "relation": "supplier",
                        "evidence": "提供高速光模块",
                        "sector": "光模块",
                        "industry": "CPO",
                        "market_cap": 9.5e10,
                        "pct_chg": 1.8,
                        "confidence": 0.81,
                        "field_sources": {"market_cap": "llm_search", "pct_chg": "llm_search"},
                    },
                    {
                        "code": "600000",
                        "name": "不在当前图中",
                        "market": "A",
                        "direction": "peer",
                        "relation": "competitor",
                        "evidence": "忽略",
                    },
                ],
                "warnings": [],
            },
        ])
        docs = [SearchDocument(title="doc", url="https://example.com", content="NVIDIA supply chain", query="nvda")]
        engine = RelationEngine(FakeResolver(), llm)
        enricher = TopologyEnrichmentService(engine, FakeSearchProvider(docs=docs))

        result = enricher.enrich_graph_fields(
            center={"market": "US", "code": "NVDA", "name": "英伟达", "sector": "AI芯片", "industry": "半导体"},
            symbols=["US:NVDA", "A:300308"],
        )

        self.assertEqual([item["id"] for item in result["items"]], ["US:NVDA", "A:300308"])
        self.assertEqual(result["items"][0]["field_sources"]["market_cap"], "search_enrich")
        self.assertEqual(result["items"][1]["field_sources"]["pct_chg"], "search_enrich")


from industry_topology.service import TopologyService


class FakeDB:
    def __init__(self): self.conn = MagicMock()
    def search_stocks_by_name(self, market, keyword, limit=10):
        return [{"code": "US.NVDA", "name": "英伟达", "sector": "半导体", "industry": "半导体"}]
    def get_stocks_by_codes(self, market, codes, include_fundamentals=True):
        return [{"code": c, "name": f"公司{c}", "sector": "板块", "industry": "板块", "market_cap": 1e10} for c in codes]


class FakeResolver2:
    def resolve(self, code, market, include_quote=False):
        return {"code": code, "name": f"公司{code}", "market": market, "sector": "板块", "market_cap": 1e10, "pct_chg": None, "quote_status": "pending"}
    def resolve_many(self, stocks, market=None, include_quote=False):
        if market is not None:
            stocks = [(market, c) for c in stocks]
        return {
            (f"{m}:{c}" if market is None else c): {
                "code": c, "name": f"公司{c}", "market": m, "sector": "板块", "market_cap": 1e10,
                "pct_chg": None, "quote_status": "pending",
            }
            for m, c in stocks
        }


class RecordingResolver(FakeResolver2):
    def __init__(self):
        self.resolve_many_inputs = []

    def resolve_many(self, stocks, market=None, include_quote=False):
        self.resolve_many_inputs.append((stocks, market, include_quote))
        return super().resolve_many(stocks, market=market, include_quote=include_quote)


class AliasAwareResolver(FakeResolver2):
    def __init__(self):
        self.resolve_many_inputs = []

    def resolve_many(self, stocks, market=None, include_quote=False):
        self.resolve_many_inputs.append((stocks, market, include_quote))
        result = {}
        for item_market, code in stocks:
            symbol = symbol_id(item_market, code)
            result[symbol] = {
                "code": code,
                "name": "中芯国际" if code == "SH.688981" else f"公司{code}",
                "market": item_market,
                "sector": "半导体",
                "market_cap": 500 * 1e8,
                "pct_chg": None,
                "quote_status": "pending",
            }
        return result


class FakeCache:
    def __init__(self, hit_map): self.hit_map = hit_map; self.saved = []
    def get_relations(self, code, market):
        return self.hit_map.get(code)
    def save_relations(self, code, market, rels, provider, model, ttl_days=7):
        self.saved.append((code, rels))
    def build_graph(self, cached_map): import networkx as nx; g = nx.DiGraph(); [g.add_edge(s, r.peer_code) for s, rels in cached_map.items() for r in rels if not r.is_empty]; return g
    def reachable_within(self, g, s, d): import networkx as nx; return list(nx.single_source_shortest_path_length(g, s, cutoff=d).keys())


class TestTopologyService(unittest.TestCase):
    def test_search(self):
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        res = svc.search("英伟达")
        self.assertEqual(res[0]["code"], "US.NVDA")

    def test_build_graph_first_time_triggers_llm(self):
        cache = FakeCache({})
        eng = RelationEngine(FakeResolver2(), FakeLLMProvider({"items": [
            {"code": "300308", "name": "中际旭创", "market": "A", "direction": "upstream", "relation": "supplier", "evidence": "光模块"}
        ]}))
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache; svc.engine = eng; svc.resolver = FakeResolver2()
        result = svc.build_graph("US.NVDA", "US", depth=1, quote_mode="sync")
        self.assertEqual(result["stats"]["llm_calls"], 1)
        self.assertGreater(len(result["nodes"]), 1)

    def test_build_graph_llm_initial_prefers_llm_fields_before_source_refresh(self):
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
                "market_cap": 2.8e12,
                "pct_chg": 3.2,
                "reason": "resolved",
                "confidence": 0.93,
                "field_sources": {"name": "llm_search", "sector": "llm_search", "industry": "llm_search", "market_cap": "llm_search", "pct_chg": "llm_search"},
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
                    "market_cap": 2.8e12,
                    "pct_chg": 3.2,
                    "reason": "resolved",
                    "confidence": 0.93,
                    "field_sources": {"name": "llm_search", "sector": "llm_search", "industry": "llm_search", "market_cap": "llm_search", "pct_chg": "llm_search"},
                },
                "items": [
                    {
                        "code": "300308",
                        "name": "中际旭创",
                        "market": "A",
                        "direction": "upstream",
                        "relation": "supplier",
                        "evidence": "提供高速光模块",
                        "sector": "光模块",
                        "industry": "CPO",
                        "market_cap": 9.5e10,
                        "pct_chg": 1.8,
                        "confidence": 0.81,
                        "field_sources": {"name": "llm_search", "sector": "llm_search", "industry": "llm_search", "market_cap": "llm_search", "pct_chg": "llm_search"},
                    }
                ],
                "warnings": [],
            },
        ])
        svc = TopologyService(FakeDB(), llm_provider=llm)
        svc.cache = cache
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), llm)
        svc.enricher = TopologyEnrichmentService(svc.engine, FakeSearchProvider())

        result = svc.build_graph("US.NVDA", "US", depth=1, quote_mode="llm_initial", center_name="NVIDIA")
        node_map = {node["id"]: node for node in result["nodes"]}

        self.assertEqual(result["stats"]["llm_calls"], 1)
        self.assertEqual(result["stats"]["data_stage"], "llm_initial")
        self.assertEqual(node_map["US:NVDA"]["name"], "英伟达")
        self.assertEqual(node_map["US:NVDA"]["sector"], "AI芯片")
        self.assertEqual(node_map["US:NVDA"]["industry"], "半导体")
        self.assertEqual(node_map["US:NVDA"]["pct_chg"], 3.2)
        self.assertEqual(node_map["US:NVDA"]["field_sources"]["name"], "llm_search")
        self.assertAlmostEqual(node_map["US:NVDA"]["field_confidence"]["name"], 0.93)
        self.assertEqual(node_map["A:300308"]["sector"], "光模块")
        self.assertEqual(node_map["A:300308"]["industry"], "CPO")
        self.assertEqual(node_map["A:300308"]["pct_chg"], 1.8)
        self.assertEqual(node_map["A:300308"]["field_sources"]["market_cap"], "llm_search")

    def test_build_initial_graph_only_depth_one_does_not_infer_stale_sources(self):
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
        svc.engine = RelationEngine(FakeResolver2(), llm)
        svc.enricher = TopologyEnrichmentService(svc.engine, FakeSearchProvider())

        result = svc.build_initial_graph_only("NVDA", "US", depth=1, center_name="NVIDIA")

        self.assertEqual(result["stats"]["data_stage"], "llm_initial")
        self.assertEqual(result["stats"]["relation_status"], "initial_ready")
        self.assertEqual(result["stats"]["llm_calls"], 1)
        self.assertEqual(llm.calls, 2)
        self.assertEqual([item[0] for item in cache.saved], ["US.NVDA"])
        self.assertGreaterEqual(len(result["nodes"]), 2)
        self.assertTrue(any(node["id"] == "US:TSM" for node in result["nodes"]))

    def test_build_initial_graph_only_expands_to_requested_depth_with_batch_inference(self):
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
                    }
                ],
                "warnings": [],
            },
            {"groups": [
                {"source_code": "TSM", "source_market": "US", "items": [
                    {
                        "code": "ASML",
                        "name": "阿斯麦",
                        "market": "US",
                        "direction": "upstream",
                        "relation": "equipment",
                        "evidence": "光刻设备",
                    }
                ]},
            ]},
            {"groups": [
                {"source_code": "ASML", "source_market": "US", "items": [
                    {
                        "code": "ZEISS",
                        "name": "蔡司",
                        "market": "US",
                        "direction": "upstream",
                        "relation": "component",
                        "evidence": "光学组件",
                    }
                ]},
            ]},
        ])
        svc = TopologyService(FakeDB(), llm_provider=llm)
        svc.cache = cache
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), llm)
        svc.enricher = TopologyEnrichmentService(svc.engine, FakeSearchProvider())

        result = svc.build_initial_graph_only("NVDA", "US", depth=3, center_name="NVIDIA")
        node_map = {node["id"]: node for node in result["nodes"]}

        self.assertEqual(result["stats"]["relation_status"], "initial_ready")
        self.assertEqual(result["stats"]["reached_depth"], 3)
        self.assertEqual(result["stats"]["llm_calls"], 3)
        self.assertEqual(node_map["US:TSM"]["depth"], 1)
        self.assertEqual(node_map["US:ASML"]["depth"], 2)
        self.assertEqual(node_map["US:ZEISS"]["depth"], 3)
        self.assertEqual([item[0] for item in cache.saved], ["US.NVDA", "US.TSM", "US.ASML"])

    def test_iter_depth_graphs_uses_infer_batch_from_center_and_yields_each_layer(self):
        cache = FakeCache({})
        llm = SequenceLLMProvider([
            {"groups": [
                {"source_code": "NVDA", "source_market": "US", "items": [
                    {"code": "TSM", "name": "台积电", "market": "US", "direction": "upstream", "relation": "foundry_packaging", "evidence": "先进制程代工"},
                ]},
            ]},
            {"groups": [
                {"source_code": "TSM", "source_market": "US", "items": [
                    {"code": "ASML", "name": "阿斯麦", "market": "US", "direction": "upstream", "relation": "equipment", "evidence": "光刻设备"},
                ]},
            ]},
            {"groups": [
                {"source_code": "ASML", "source_market": "US", "items": [
                    {"code": "ZEISS", "name": "蔡司", "market": "US", "direction": "upstream", "relation": "component", "evidence": "光学组件"},
                ]},
            ]},
        ])
        svc = TopologyService(FakeDB(), llm_provider=llm)
        svc.cache = cache
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), llm)

        graphs = list(svc.iter_depth_graphs("NVDA", "US", depth=3, center_name="NVIDIA"))

        self.assertEqual([graph["stats"]["reached_depth"] for graph in graphs], [1, 2, 3])
        self.assertEqual([len(graph["nodes"]) for graph in graphs], [2, 3, 4])
        self.assertEqual(llm.calls, 3)
        self.assertEqual([item[0] for item in cache.saved], ["US.NVDA", "US.TSM", "US.ASML"])

    def test_iter_depth_graphs_reuses_cached_center_and_continues_from_missing_layer(self):
        cache = FakeCache({
            "US.NVDA": [_make_relation("US.TSM", peer_market="US")],
        })
        llm = SequenceLLMProvider([
            {"groups": [
                {"source_code": "TSM", "source_market": "US", "items": [
                    {"code": "ASML", "name": "阿斯麦", "market": "US", "direction": "upstream", "relation": "equipment", "evidence": "光刻设备"},
                ]},
            ]},
        ])
        svc = TopologyService(FakeDB(), llm_provider=llm)
        svc.cache = cache
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), llm)

        graphs = list(svc.iter_depth_graphs("NVDA", "US", depth=2, center_name="NVIDIA"))

        self.assertEqual(len(graphs), 1)
        self.assertEqual(graphs[0]["stats"]["reached_depth"], 2)
        self.assertEqual(llm.calls, 1)
        self.assertEqual([item[0] for item in cache.saved], ["US.TSM"])

    def test_iter_depth_graphs_accepts_bare_source_code_for_prefixed_center_symbol(self):
        cache = FakeCache({})
        llm = SequenceLLMProvider([
            {"groups": [
                {"source_code": "NVDA", "source_market": "US", "items": [
                    {"code": "TSM", "name": "台积电", "market": "US", "direction": "upstream", "relation": "foundry_packaging", "evidence": "先进制程代工"},
                ]},
            ]},
        ])
        svc = TopologyService(FakeDB(), llm_provider=llm)
        svc.cache = cache
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), llm)

        graphs = list(svc.iter_depth_graphs("US.NVDA", "US", depth=1, center_name="NVIDIA"))

        self.assertEqual(len(graphs), 1)
        self.assertEqual(graphs[0]["stats"]["reached_depth"], 1)
        self.assertTrue(any(node["id"] == "US:TSM" for node in graphs[0]["nodes"]))
        self.assertEqual([item[0] for item in cache.saved], ["US.NVDA"])

    def test_iter_depth_graphs_stops_when_next_layer_returns_empty_group(self):
        cache = FakeCache({})
        llm = SequenceLLMProvider([
            {"groups": [
                {"source_code": "NVDA", "source_market": "US", "items": [
                    {"code": "TSM", "name": "台积电", "market": "US", "direction": "upstream", "relation": "foundry_packaging", "evidence": "先进制程代工"},
                ]},
            ]},
            {"groups": [
                {"source_code": "TSM", "source_market": "US", "items": []},
            ]},
        ])
        svc = TopologyService(FakeDB(), llm_provider=llm)
        svc.cache = cache
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), llm)

        graphs = list(svc.iter_depth_graphs("NVDA", "US", depth=3, center_name="NVIDIA"))

        self.assertEqual([graph["stats"]["reached_depth"] for graph in graphs], [1, 1])
        self.assertEqual(graphs[-1]["stats"]["expanding_depth"], 2)
        self.assertEqual([item[0] for item in cache.saved], ["US.NVDA", "US.TSM"])

    def test_iter_depth_graphs_stops_at_llm_hard_limit_and_keeps_existing_graph(self):
        original_limit = topology_service_module._LLM_HARD_LIMIT
        topology_service_module._LLM_HARD_LIMIT = 1
        try:
            cache = FakeCache({})
            llm = SequenceLLMProvider([
                {"groups": [
                    {"source_code": "NVDA", "source_market": "US", "items": [
                        {"code": "TSM", "name": "台积电", "market": "US", "direction": "upstream", "relation": "foundry_packaging", "evidence": "先进制程代工"},
                    ]},
                ]},
                {"groups": [
                    {"source_code": "TSM", "source_market": "US", "items": [
                        {"code": "ASML", "name": "阿斯麦", "market": "US", "direction": "upstream", "relation": "equipment", "evidence": "光刻设备"},
                    ]},
                ]},
            ])
            svc = TopologyService(FakeDB(), llm_provider=llm)
            svc.cache = cache
            svc.resolver = FakeResolver2()
            svc.engine = RelationEngine(FakeResolver2(), llm)

            graphs = list(svc.iter_depth_graphs("NVDA", "US", depth=3, center_name="NVIDIA"))
        finally:
            topology_service_module._LLM_HARD_LIMIT = original_limit

        self.assertEqual(len(graphs), 1)
        self.assertEqual(graphs[0]["stats"]["reached_depth"], 1)
        self.assertEqual(len(graphs[0]["nodes"]), 2)
        self.assertEqual(llm.calls, 1)

    def test_build_graph_llm_initial_fails_when_initial_snapshot_missing(self):
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = FakeCache({})
        svc.resolver = FakeResolver2()
        svc.enricher = type("BrokenEnricher", (), {"build_initial_snapshot": lambda *args, **kwargs: {}})()

        with self.assertRaises(RuntimeError):
            svc.build_graph("US.NVDA", "US", depth=1, quote_mode="llm_initial", center_name="NVIDIA")

    def test_search_enrich_uses_existing_symbols_and_returns_patches(self):
        llm = SequenceLLMProvider([
            {
                "center": {
                    "resolved": True,
                    "market": "US",
                    "code": "NVDA",
                    "name": "英伟达",
                    "aliases": ["NVIDIA"],
                    "sector": "AI芯片",
                    "industry": "半导体",
                    "market_cap": 2.9e12,
                    "pct_chg": 4.1,
                    "reason": "resolved",
                    "confidence": 0.95,
                    "field_sources": {"market_cap": "llm_search", "pct_chg": "llm_search"},
                },
                "items": [
                    {
                        "code": "300308",
                        "name": "中际旭创",
                        "market": "A",
                        "direction": "upstream",
                        "relation": "supplier",
                        "evidence": "提供高速光模块",
                        "sector": "光模块",
                        "industry": "CPO",
                        "market_cap": 9.5e10,
                        "pct_chg": 1.8,
                        "confidence": 0.81,
                        "field_sources": {"market_cap": "llm_search", "pct_chg": "llm_search"},
                    },
                ],
                "warnings": [],
            },
        ])
        docs = [SearchDocument(title="doc", url="https://example.com", content="NVIDIA supply chain", query="nvda")]
        svc = TopologyService(FakeDB(), llm_provider=llm)
        svc.cache = FakeCache({})
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), llm)
        svc.enricher = TopologyEnrichmentService(svc.engine, FakeSearchProvider(docs=docs))

        result = svc.search_enrich(
            center={"market": "US", "code": "NVDA", "name": "英伟达", "sector": "AI芯片", "industry": "半导体"},
            symbols=["US:NVDA", "A:300308"],
        )

        self.assertEqual(result["items"][0]["id"], "US:NVDA")
        self.assertEqual(result["items"][1]["id"], "A:300308")
        self.assertEqual(result["items"][1]["field_sources"]["market_cap"], "search_enrich")

    def test_build_graph_defer_does_not_call_llm(self):
        calls = {"count": 0}
        def fail_if_called(self, **kwargs):
            calls["count"] += 1
            raise Exception("不该同步调 LLM")

        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = FakeCache({})
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), type("Boom",(),{"complete_json": fail_if_called})())

        result = svc.build_graph("US.NVDA", "US", depth=1, quote_mode="defer")

        self.assertEqual(calls["count"], 0)
        self.assertEqual(result["stats"]["llm_calls"], 0)
        self.assertEqual(result["stats"].get("relation_status"), "pending")
        self.assertEqual(len(result["nodes"]), 1)

    def test_build_graph_defer_empty_cache_returns_center_node(self):
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = FakeCache({})
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), FakeLLMProvider({"items": []}))

        result = svc.build_graph("US.NVDA", "US", depth=1, quote_mode="defer")

        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["nodes"][0]["id"], "US:NVDA")
        self.assertEqual(result["edges"], [])

    def test_build_graph_auto_empty_cache_marks_generating_without_llm(self):
        calls = {"count": 0}
        def fail_if_called(self, **kwargs):
            calls["count"] += 1
            raise Exception("自动模式不应同步调 LLM")

        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = FakeCache({})
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), type("Boom",(),{"complete_json": fail_if_called})())

        result = svc.build_graph("US.NVDA", "US", depth=1, quote_mode="auto")

        self.assertEqual(calls["count"], 0)
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["stats"]["relation_status"], "generating")
        self.assertEqual(result["stats"]["stale_nodes"], 1)
        self.assertEqual(result["stats"]["stale_sources"], [{"code": "US.NVDA", "market": "US"}])

    def test_build_graph_auto_uses_prefixed_us_cache_key_without_bare_fallback(self):
        cache = FakeCache({
            "NVDA": [_make_relation("TSM", peer_market="US")],
        })
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache
        svc.resolver = FakeResolver2()

        result = svc.build_graph("US.NVDA", "US", depth=1, quote_mode="auto")

        self.assertEqual(result["stats"]["relation_status"], "generating")
        self.assertEqual(result["stats"]["cached_nodes"], 0)
        self.assertEqual(result["stats"]["stale_nodes"], 1)
        self.assertEqual(result["stats"]["stale_sources"], [{"code": "US.NVDA", "market": "US"}])

    def test_build_graph_auto_empty_relation_placeholder_can_continue(self):
        cache = FakeCache({
            "US.NVDA": [_make_relation("", peer_market="", is_empty=True)],
        })
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache
        svc.resolver = FakeResolver2()

        result = svc.build_graph("US.NVDA", "US", depth=3, quote_mode="auto")

        self.assertEqual(result["stats"]["relation_status"], "cached")
        self.assertEqual(result["stats"]["reached_depth"], 0)
        self.assertTrue(result["stats"]["can_continue"])

    def test_build_graph_auto_partial_cache_returns_cached_edges_and_stale_sources(self):
        cache = FakeCache({
            "US.NVDA": [_make_relation("300308")],
        })
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache
        svc.resolver = FakeResolver2()

        result = svc.build_graph("US.NVDA", "US", depth=2, quote_mode="auto")

        self.assertGreaterEqual(len(result["nodes"]), 2)
        self.assertEqual(len(result["edges"]), 1)
        self.assertEqual(result["stats"]["relation_status"], "generating")
        self.assertEqual(result["stats"]["cached_nodes"], 1)
        self.assertEqual(result["stats"]["stale_nodes"], 1)
        self.assertEqual(result["stats"]["stale_sources"], [{"code": "300308", "market": "A"}])
        self.assertEqual(result["stats"]["requested_depth"], 2)
        self.assertEqual(result["stats"]["reached_depth"], 1)
        self.assertTrue(result["stats"]["can_continue"])

    def test_build_graph_auto_full_cache_does_not_mark_generating(self):
        cache = FakeCache({
            "US.NVDA": [_make_relation("300308")],
            "300308": [],
        })
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache
        svc.resolver = FakeResolver2()

        result = svc.build_graph("US.NVDA", "US", depth=2, quote_mode="auto")

        self.assertEqual(result["stats"]["relation_status"], "cached")
        self.assertEqual(result["stats"]["stale_nodes"], 0)
        self.assertEqual(result["stats"]["stale_sources"], [])
        self.assertEqual(result["stats"]["requested_depth"], 2)
        self.assertEqual(result["stats"]["reached_depth"], 2)
        self.assertFalse(result["stats"]["can_continue"])

    def test_llm_failure_empty_relations_returns_center_node(self):
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = FakeCache({})
        svc.resolver = FakeResolver2()
        svc.engine = RelationEngine(FakeResolver2(), FakeLLMProvider({"items": []}))

        result = svc.build_graph("US.NVDA", "US", depth=1, quote_mode="sync")

        self.assertEqual(result["stats"]["llm_calls"], 1)
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["nodes"][0]["id"], "US:NVDA")
        self.assertEqual(result["edges"], [])

    def test_infer_and_cache_batch_saves_empty_relations_for_missing_group(self):
        payload = {"groups": [
            {"source_code": "US.NVDA", "source_market": "US", "items": [
                {"code": "TSM", "name": "台积电", "market": "US", "direction": "upstream", "relation": "foundry_packaging", "evidence": "先进制程代工"},
            ]},
        ]}
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider(payload))
        svc.cache = FakeCache({})
        svc.resolver = FakeResolver2()

        result = svc.infer_and_cache_batch([
            {"code": "US.NVDA", "market": "US"},
            {"code": "TSM", "market": "US"},
        ])

        self.assertTrue(result)
        self.assertEqual([item[0] for item in svc.cache.saved], ["US.NVDA", "US.TSM"])
        self.assertEqual(len(svc.cache.saved[0][1]), 1)
        self.assertEqual(svc.cache.saved[1][1], [])

    def test_build_graph_cached_zero_llm(self):
        cache = FakeCache({"US.NVDA": [_make_relation("300308")]})
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache; svc.resolver = FakeResolver2()
        # engine 不该被调用：给一个会抛的 fake
        svc.engine = RelationEngine(FakeResolver2(), type("Boom",(),{"complete_json": lambda self,**k: (_ for _ in ()).throw(Exception("不该调"))})())
        result = svc.build_graph("US.NVDA", "US", depth=1, quote_mode="sync")
        self.assertEqual(result["stats"]["llm_calls"], 0)

    def test_llm_failure_isolated(self):
        cache = FakeCache({})
        boom_engine = RelationEngine(FakeResolver2(), type("Boom",(),{"complete_json": lambda self,**k: (_ for _ in ()).throw(Exception("LLM挂"))})())
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache; svc.engine = boom_engine; svc.resolver = FakeResolver2()
        result = svc.build_graph("US.NVDA", "US", depth=1, quote_mode="sync")
        # 中心节点仍返回，edges 空
        self.assertEqual(result["stats"].get("error"), "llm_failed")
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["nodes"][0]["code"], "US.NVDA")

    def test_build_graph_includes_depth_and_zone(self):
        cache = FakeCache({
            "US.NVDA": [
                _make_relation("300308", direction=Direction.UPSTREAM, relation=RelationType.SUPPLIER),
                _make_relation("600001", direction=Direction.DOWNSTREAM, relation=RelationType.CUSTOMER),
            ],
        })
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache
        svc.resolver = FakeResolver2()

        result = svc.build_graph("US.NVDA", "US", depth=1)
        node_map = {node["code"]: node for node in result["nodes"]}

        self.assertEqual(node_map["US.NVDA"]["depth"], 0)
        self.assertEqual(node_map["US.NVDA"]["zone"], "center")
        self.assertEqual(node_map["300308"]["depth"], 1)
        self.assertEqual(node_map["300308"]["zone"], "upstream")
        self.assertEqual(node_map["600001"]["depth"], 1)
        self.assertEqual(node_map["600001"]["zone"], "downstream")

    def test_build_graph_uses_peer_market_and_symbol_ids(self):
        cache = FakeCache({
            "HK.01810": [
                _make_relation("QCOM", peer_market="US"),
                _make_relation("002600", peer_market="A"),
                _make_relation("6981", peer_market="JP"),
            ],
        })
        resolver = RecordingResolver()
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache
        svc.resolver = resolver

        result = svc.build_graph("01810", "HK", depth=1)
        node_map = {node["id"]: node for node in result["nodes"]}

        self.assertIn("HK:01810", node_map)
        self.assertIn("US:QCOM", node_map)
        self.assertIn("A:002600", node_map)
        self.assertIn("JP:6981", node_map)
        self.assertEqual(node_map["US:QCOM"]["market"], "US")
        self.assertEqual(node_map["A:002600"]["market"], "A")
        self.assertEqual(node_map["JP:6981"]["market"], "JP")
        self.assertEqual(resolver.resolve_many_inputs[0][1], None)
        self.assertFalse(resolver.resolve_many_inputs[0][2])
        self.assertCountEqual(
            resolver.resolve_many_inputs[0][0],
            [("US", "US.QCOM"), ("A", "SZ.002600"), ("JP", "JP.6981")],
        )

    def test_exchange_alias_nodes_resolve_with_standard_market_but_keep_display_id(self):
        cache = FakeCache({
            "HK.01810": [
                _make_relation("688981.SH", peer_market="SH"),
            ],
        })
        resolver = AliasAwareResolver()
        svc = TopologyService(FakeDB(), llm_provider=FakeLLMProvider({"items": []}))
        svc.cache = cache
        svc.resolver = resolver

        result = svc.build_graph("HK.01810", "HK", depth=1)
        node_map = {node["id"]: node for node in result["nodes"]}

        self.assertIn("SH:688981.SH", node_map)
        self.assertEqual(node_map["SH:688981.SH"]["name"], "中芯国际")
        self.assertEqual(node_map["SH:688981.SH"]["sector"], "半导体")
        self.assertEqual(node_map["SH:688981.SH"]["market_cap_str"], "500亿")
        self.assertEqual(node_map["SH:688981.SH"]["size_level"], 3)
        self.assertEqual(resolver.resolve_many_inputs[0][0], [("A", "SH.688981")])

    def test_parse_topology_symbol_normalizes_supported_markets(self):
        from industry_topology.symbols import parse_topology_symbol

        parsed = parse_topology_symbol("A:002600")

        self.assertEqual(parsed["symbol"], "A:002600")
        self.assertEqual(parsed["market"], "A")
        self.assertEqual(parsed["code"], "SZ.002600")
        self.assertFalse(parsed["skipped"])

    def test_parse_topology_symbol_supports_japan_market(self):
        from industry_topology.symbols import parse_topology_symbol

        parsed = parse_topology_symbol("JP:6981")

        self.assertEqual(parsed["symbol"], "JP:6981")
        self.assertFalse(parsed["skipped"])
        self.assertEqual(parsed["market"], "JP")
        self.assertEqual(parsed["code"], "JP.6981")
        self.assertEqual(parsed["provider_symbols"]["yfinance"], "6981.T")


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


class TestTopologyBackgroundScheduler(unittest.TestCase):
    def setUp(self):
        _TOPOLOGY_GENERATION_IN_FLIGHT.clear()

    def tearDown(self):
        _TOPOLOGY_GENERATION_IN_FLIGHT.clear()

    def test_schedule_relation_generation_deduplicates_in_flight_sources(self):
        background = FakeBackgroundTasks()
        stats = {"stale_sources": [{"code": "US.NVDA", "market": "US"}, {"code": "300308", "market": "A"}]}

        first = _schedule_relation_generation(background, stats)
        second = _schedule_relation_generation(background, stats)

        self.assertTrue(first)


class TestTopologyRoutes(unittest.TestCase):
    def setUp(self):
        _TOPOLOGY_GENERATION_IN_FLIGHT.clear()

    def tearDown(self):
        _TOPOLOGY_GENERATION_IN_FLIGHT.clear()

    def test_graph_route_uses_llm_initial_mode(self):
        service = MagicMock()
        service.build_graph.return_value = {"nodes": [], "edges": [], "stats": {"relation_status": "cached"}}
        background = FakeBackgroundTasks()

        topology_graph_route(
            GraphRequest(code="US.NVDA", market="US", depth=1, center_name="NVIDIA"),
            background,
            service,
        )

        self.assertEqual(service.build_graph.call_args.kwargs["quote_mode"], "llm_initial")

    def test_existing_graph_route_reads_auto_without_background_scheduler(self):
        service = MagicMock()
        service.build_graph.return_value = {"nodes": [], "edges": [], "stats": {"relation_status": "generating"}}

        response = topology_existing_graph_route(
            GraphRequest(code="US.NVDA", market="US", depth=2, center_name="NVIDIA"),
            service,
        )

        self.assertEqual(response["data"]["stats"]["relation_status"], "generating")
        self.assertNotIn("background_started", response["data"]["stats"])
        self.assertEqual(service.build_graph.call_args.kwargs["quote_mode"], "auto")

    def test_search_enrich_route_passes_center_and_symbols(self):
        service = MagicMock()
        service.search_enrich.return_value = {"items": [], "warnings": []}

        class Request:
            center_market = "US"
            center_code = "NVDA"
            center_name = "英伟达"
            center_sector = "AI芯片"
            center_industry = "半导体"
            symbols = ["US:NVDA", "A:300308"]

        topology_search_enrich_route(Request(), service)

        self.assertEqual(
            service.search_enrich.call_args.kwargs,
            {
                "center": {
                    "market": "US",
                    "code": "NVDA",
                    "name": "英伟达",
                    "sector": "AI芯片",
                    "industry": "半导体",
                },
                "symbols": ["US:NVDA", "A:300308"],
            },
        )

    def test_refresh_quotes_skips_cached_complete_quote(self):
        service = MagicMock()
        fetched_at = datetime(2026, 6, 23, 15, 16, tzinfo=timezone.utc)
        quote = QuoteSnapshot(
            market="US",
            code="US.NVDA",
            name="NVIDIA",
            price=439.215,
            change_percent=1.2,
            market_cap=1.23e12,
            fetched_at=fetched_at,
            source="fake",
        )
        service.repository.get_quote.return_value = (quote, BlockStatus(status="cached", source="fake", fetched_at=fetched_at))
        service.now.return_value = fetched_at
        background = FakeBackgroundTasks()

        result = topology_refresh_quotes_route(QuoteRefreshRequest(symbols=["US:NVDA"], force=False), background, service)

        self.assertEqual(background.tasks, [])
        self.assertEqual(result["data"]["items"][0]["status"], "cached")
        self.assertEqual(result["data"]["items"][0]["data_gaps"], [])

    def test_refresh_quotes_requeues_cached_quote_with_missing_key_field(self):
        service = MagicMock()
        fetched_at = datetime(2026, 6, 23, 15, 16, tzinfo=timezone.utc)
        quote = QuoteSnapshot(
            market="US",
            code="US.NVDA",
            name="NVIDIA",
            price=439.215,
            change_percent=None,
            market_cap=1.23e12,
            fetched_at=fetched_at,
            source="fake",
        )
        service.repository.get_quote.return_value = (quote, BlockStatus(status="cached", source="fake", fetched_at=fetched_at))
        service.now.return_value = fetched_at
        background = FakeBackgroundTasks()

        result = topology_refresh_quotes_route(QuoteRefreshRequest(symbols=["US:NVDA"], force=False), background, service)

        self.assertEqual(len(background.tasks), 1)
        self.assertEqual(result["data"]["items"][0]["status"], "queued")
        self.assertEqual(result["data"]["items"][0]["field_errors"]["pct_chg"], "quote_pending")

    def test_schedule_relation_generation_batches_stale_sources_by_five(self):
        background = FakeBackgroundTasks()
        stats = {"stale_sources": [{"code": f"S{i}", "market": "US"} for i in range(9)]}

        started = _schedule_relation_generation(background, stats)

        self.assertTrue(started)
        self.assertEqual(len(background.tasks), 2)
        self.assertEqual([len(task[1][0]) for task in background.tasks], [5, 4])
        self.assertEqual(len(_TOPOLOGY_GENERATION_IN_FLIGHT), 9)

    def test_graph_route_marks_background_started_for_generating_response(self):
        class FakeSvc:
            def __init__(self):
                self.quote_mode = None
                self.center_name = None

            def build_graph(self, code, market, depth, quote_mode="auto", center_name=""):
                self.quote_mode = quote_mode
                self.center_name = center_name
                return {
                    "nodes": [],
                    "edges": [],
                    "stats": {
                        "llm_calls": 0,
                        "cached_nodes": 0,
                        "stale_nodes": 1,
                        "stale_sources": [{"code": code, "market": market}],
                        "relation_status": "generating",
                    },
                }

        background = FakeBackgroundTasks()
        svc = FakeSvc()
        response = topology_graph_route(GraphRequest(code="US.NVDA", market="US", depth=1, quote_mode="defer", center_name="英伟达"), background, svc)

        self.assertEqual(svc.quote_mode, "llm_initial")
        self.assertEqual(svc.center_name, "英伟达")
        self.assertTrue(response["data"]["stats"]["background_started"])
        self.assertEqual(len(background.tasks), 1)


if __name__ == "__main__":
    unittest.main()
