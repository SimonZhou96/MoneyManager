import unittest
from datetime import datetime, timedelta

from market_intel.models import (
    DataSourceStatus,
    EvidencePack,
    IntelItem,
    MarketIntelBundle,
    StockIntelBundle,
)
from market_intel.providers.base import dedupe_items, group_items, ttl_for_item_type


class MarketIntelModelTests(unittest.TestCase):
    def test_intel_item_round_trip_preserves_metadata(self):
        item = IntelItem(
            scope_type="stock",
            market="US",
            code="AAPL",
            source="sec",
            provider="fixture",
            item_type="financial",
            title="Apple annual report",
            summary="Revenue grew year over year.",
            url="https://example.com/aapl",
            published_at=datetime(2026, 5, 24, 9, 0, 0),
            fetched_at=datetime(2026, 5, 25, 8, 0, 0),
            expires_at=datetime(2026, 5, 26, 8, 0, 0),
            dedupe_key="aapl-financial-2026",
            raw_json={"source_id": "10-k", "score": 9},
            is_stale=True,
        )

        restored = IntelItem.from_dict(item.to_dict())

        self.assertEqual(restored.source, "sec")
        self.assertEqual(restored.raw_json, {"source_id": "10-k", "score": 9})
        self.assertTrue(restored.is_stale)
        self.assertEqual(restored.published_at, datetime(2026, 5, 24, 9, 0, 0))

    def test_stock_intel_bundle_groups_items_and_serializes_source_status(self):
        newer = IntelItem(
            scope_type="stock",
            market="US",
            code="AAPL",
            source="news",
            provider="provider-a",
            item_type="market_news",
            title="Newer",
            fetched_at=datetime(2026, 5, 25, 10, 0, 0),
            expires_at=datetime(2026, 5, 25, 10, 15, 0),
            dedupe_key="newer",
        )
        older = IntelItem(
            scope_type="stock",
            market="US",
            code="AAPL",
            source="news",
            provider="provider-a",
            item_type="market_news",
            title="Older",
            fetched_at=datetime(2026, 5, 25, 9, 0, 0),
            expires_at=datetime(2026, 5, 25, 9, 15, 0),
            dedupe_key="older",
        )
        status = DataSourceStatus(
            provider="provider-a",
            status="fresh",
            item_count=2,
            error_message="",
            fetched_at=datetime(2026, 5, 25, 10, 1, 0),
            stale=False,
        )

        payload = StockIntelBundle(
            market="US",
            code="AAPL",
            items=[older, newer],
            freshness_status="fresh",
            source_status={"provider-a": status},
        ).to_dict()

        self.assertEqual(payload["scope_type"], "stock")
        self.assertEqual(payload["market"], "US")
        self.assertEqual(payload["code"], "AAPL")
        self.assertEqual(
            [item["title"] for item in payload["groups"]["market_news"]],
            ["Newer", "Older"],
        )
        self.assertEqual(payload["source_status"]["provider-a"]["item_count"], 2)
        self.assertFalse(payload["source_status"]["provider-a"]["stale"])

    def test_evidence_pack_carries_data_gaps_and_citations(self):
        pack = EvidencePack(
            market="HK",
            code="01810",
            data_gaps=["missing long tiger data"],
            citations=[{"label": "annual report", "url": "https://example.com/report"}],
        )

        payload = pack.to_dict()

        self.assertEqual(payload["data_gaps"], ["missing long tiger data"])
        self.assertEqual(
            payload["citations"],
            [{"label": "annual report", "url": "https://example.com/report"}],
        )

    def test_dedupe_items_keeps_first_item_for_same_identity(self):
        first = IntelItem(
            scope_type="stock",
            market="US",
            code="AAPL",
            source="news",
            provider="provider-a",
            item_type="market_news",
            title="First",
            fetched_at=datetime(2026, 5, 25, 8, 0, 0),
            expires_at=datetime(2026, 5, 25, 8, 15, 0),
            dedupe_key="same",
        )
        duplicate = IntelItem(
            scope_type="stock",
            market="US",
            code="AAPL",
            source="news",
            provider="provider-a",
            item_type="market_news",
            title="Duplicate",
            fetched_at=datetime(2026, 5, 25, 9, 0, 0),
            expires_at=datetime(2026, 5, 25, 9, 15, 0),
            dedupe_key="same",
        )

        deduped = dedupe_items([first, duplicate])

        self.assertEqual(deduped, [first])

    def test_ttl_policy_is_concrete(self):
        self.assertEqual(ttl_for_item_type("financial"), timedelta(days=1))
        self.assertEqual(ttl_for_item_type("announcement"), timedelta(hours=12))
        self.assertEqual(ttl_for_item_type("research_report"), timedelta(hours=12))
        self.assertEqual(ttl_for_item_type("money_flow"), timedelta(minutes=30))
        self.assertEqual(ttl_for_item_type("long_tiger"), timedelta(minutes=30))
        self.assertEqual(ttl_for_item_type("hot_sector"), timedelta(minutes=30))
        self.assertEqual(ttl_for_item_type("market_news"), timedelta(minutes=15))
        self.assertEqual(ttl_for_item_type("index_snapshot"), timedelta(minutes=15))
        self.assertEqual(ttl_for_item_type("search_document"), timedelta(hours=2))
        self.assertEqual(ttl_for_item_type("unknown"), timedelta(hours=6))

    def test_group_sort_handles_mixed_timezone_datetimes(self):
        aware = IntelItem.from_dict({
            "scope_type": "market",
            "market": "US",
            "code": "",
            "source": "news",
            "provider": "provider-a",
            "item_type": "market_news",
            "title": "Aware timestamp",
            "fetched_at": "2026-05-25T10:00:00Z",
            "expires_at": "2026-05-25T10:15:00Z",
            "dedupe_key": "aware",
        })
        naive = IntelItem(
            scope_type="market",
            market="US",
            code="",
            source="news",
            provider="provider-a",
            item_type="market_news",
            title="Naive timestamp",
            fetched_at=datetime(2026, 5, 25, 11, 0, 0),
            expires_at=datetime(2026, 5, 25, 11, 15, 0),
            dedupe_key="naive",
        )

        bundle_payload = MarketIntelBundle(market="US", items=[aware, naive]).to_dict()
        provider_groups = group_items([aware, naive])

        self.assertEqual(
            [item["title"] for item in bundle_payload["groups"]["market_news"]],
            ["Naive timestamp", "Aware timestamp"],
        )
        self.assertEqual(
            [item.title for item in provider_groups["market_news"]],
            ["Naive timestamp", "Aware timestamp"],
        )


if __name__ == "__main__":
    unittest.main()
