import unittest
from datetime import datetime, timedelta

from market_intel.evidence import EvidencePackBuilder, intel_items_to_search_documents
from market_intel.models import IntelItem, MarketIntelBundle, StockIntelBundle
from signal_analysis.models import SearchDocument


def make_item(
    *,
    scope_type="stock",
    market="US",
    code="AAPL",
    source="news",
    provider="fixture",
    item_type="market_news",
    title="Market intel title",
    summary="Market intel summary",
    url="https://example.com/intel",
    dedupe_key="intel-key",
):
    fetched_at = datetime(2026, 5, 25, 9, 0, 0)
    return IntelItem(
        scope_type=scope_type,
        market=market,
        code=code,
        source=source,
        provider=provider,
        item_type=item_type,
        title=title,
        summary=summary,
        url=url,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(hours=1),
        dedupe_key=dedupe_key,
    )


class MarketIntelEvidenceTests(unittest.TestCase):
    def test_intel_items_to_search_documents_includes_source_type_and_summary(self):
        item = make_item(
            source="eastmoney",
            item_type="announcement",
            title="AAPL announces buyback",
            summary="Board approved a new repurchase plan.",
            url="https://example.com/aapl-buyback",
        )

        documents = intel_items_to_search_documents([item])

        self.assertEqual(len(documents), 1)
        self.assertIsInstance(documents[0], SearchDocument)
        self.assertEqual(documents[0].title, "AAPL announces buyback")
        self.assertEqual(documents[0].url, "https://example.com/aapl-buyback")
        self.assertIn("source=eastmoney", documents[0].content)
        self.assertIn("type=announcement", documents[0].content)
        self.assertIn("Board approved a new repurchase plan.", documents[0].content)

    def test_evidence_pack_builder_merges_context_status_gaps_and_citations(self):
        stock_item = make_item(
            scope_type="stock",
            code="AAPL",
            title="Stock headline",
            summary="Company-specific development.",
            url="https://example.com/stock-headline",
            dedupe_key="stock-headline",
        )
        market_item = make_item(
            scope_type="market",
            code="",
            title="Market headline",
            summary="Macro development.",
            url="https://example.com/market-headline",
            dedupe_key="market-headline",
        )
        manual_item = make_item(
            source="manual",
            provider="manual",
            item_type="manual_note",
            title="Manual context",
            summary="Analyst supplied context.",
            url="https://example.com/manual-context",
            dedupe_key="manual-context",
        )
        search_document = SearchDocument(
            title="Search context",
            url="https://example.com/search",
            content="External search context.",
            score=0.8,
            query="AAPL news",
        )

        pack = EvidencePackBuilder().build(
            market="US",
            code="AAPL",
            stock_bundle=StockIntelBundle(
                market="US",
                code="AAPL",
                items=[stock_item],
                freshness_status="fresh",
                source_status={"stock-provider": {"status": "success", "item_count": 1}},
            ).to_dict(),
            market_bundle=MarketIntelBundle(
                market="US",
                items=[market_item],
                freshness_status="fresh",
                source_status={"market-provider": {"status": "success", "item_count": 1}},
            ).to_dict(),
            search_documents=[search_document],
            manual_items=[manual_item],
            source_status={"manual": {"status": "success", "item_count": 1}},
            data_gaps=["missing long tiger data"],
            citations=[{"label": "source", "url": "https://example.com/source"}],
        )

        payload = pack.to_dict()

        self.assertEqual(payload["market"], "US")
        self.assertEqual(payload["code"], "AAPL")
        self.assertCountEqual(
            [item["title"] for item in payload["structured_items"]],
            ["Stock headline", "Market headline"],
        )
        self.assertEqual(payload["search_documents"][0]["title"], "Search context")
        self.assertEqual(payload["manual_items"][0]["title"], "Manual context")
        self.assertEqual(payload["stock_context"]["items"][0]["title"], "Stock headline")
        self.assertEqual(payload["market_context"]["items"][0]["title"], "Market headline")
        self.assertEqual(payload["source_status"]["stock-provider"]["item_count"], 1)
        self.assertEqual(payload["source_status"]["market-provider"]["item_count"], 1)
        self.assertEqual(payload["source_status"]["manual"]["item_count"], 1)
        self.assertEqual(payload["data_gaps"], ["missing long tiger data"])
        self.assertIn({"label": "source", "url": "https://example.com/source"}, payload["citations"])

    def test_service_backed_builder_fetches_bundles_with_force_refresh_and_derives_gaps_and_citations(self):
        class FakeService:
            def __init__(self):
                self.stock_calls = []
                self.market_calls = []

            def get_stock_intel(self, market, code, force_refresh=False):
                self.stock_calls.append((market, code, force_refresh))
                return {
                    "scope_type": "stock",
                    "market": market,
                    "code": code,
                    "groups": {},
                    "freshness_status": "stale",
                    "source_status": {"stock-provider": {"status": "failed", "item_count": 0}},
                }

            def get_market_digest(self, market, force_refresh=False):
                self.market_calls.append((market, force_refresh))
                return MarketIntelBundle(
                    market=market,
                    items=[
                        make_item(
                            scope_type="market",
                            market=market,
                            code="",
                            title="Macro policy shift",
                            url="https://example.com/macro",
                            dedupe_key="macro-policy-shift",
                        )
                    ],
                    freshness_status="fresh",
                    source_status={"market-provider": {"status": "success", "item_count": 1}},
                ).to_dict()

        search_document = SearchDocument(
            title="Search sourced catalyst",
            url="https://example.com/search-catalyst",
            content="Search document context.",
            query="AAPL catalyst",
        )

        service = FakeService()
        pack = EvidencePackBuilder(service).build(
            market="US",
            code="AAPL",
            search_documents=[search_document],
            force_refresh=True,
        )
        payload = pack.to_dict()

        self.assertEqual(pack.market, "US")
        self.assertEqual(pack.code, "AAPL")
        self.assertTrue(any("stock intel" in gap and "stale" in gap for gap in payload["data_gaps"]))
        self.assertTrue(any(item["title"] == "Macro policy shift" for item in payload["structured_items"]))
        self.assertIn(
            {"label": "Macro policy shift", "url": "https://example.com/macro"},
            payload["citations"],
        )
        self.assertIn(
            {"label": "Search sourced catalyst", "url": "https://example.com/search-catalyst"},
            payload["citations"],
        )
        self.assertEqual(service.stock_calls, [("US", "AAPL", True)])
        self.assertEqual(service.market_calls, [("US", True)])

    def test_service_backed_builder_passes_force_refresh_to_service(self):
        class RecordingService:
            def __init__(self):
                self.stock_calls = []
                self.market_calls = []

            def get_stock_intel(self, market, code, force_refresh=False):
                self.stock_calls.append((market, code, force_refresh))
                return StockIntelBundle(market=market, code=code).to_dict()

            def get_market_digest(self, market, force_refresh=False):
                self.market_calls.append((market, force_refresh))
                return MarketIntelBundle(market=market).to_dict()

        service = RecordingService()

        EvidencePackBuilder(service).build(market="US", code="AAPL", force_refresh=True)

        self.assertEqual(service.stock_calls, [("US", "AAPL", True)])
        self.assertEqual(service.market_calls, [("US", True)])

    def test_evidence_pack_dedupes_by_url_and_prefers_high_reliability_source(self):
        cailian = make_item(
            scope_type="market",
            market="A",
            code="",
            source="财联社",
            provider="cailianpress",
            title="AI算力板块走强",
            summary="财联社快讯",
            url="https://example.com/same-event/",
            dedupe_key="cls-1",
        )
        search = SearchDocument(
            title="AI算力板块走强",
            url="https://example.com/same-event",
            content="搜索重复结果",
            query="AI 算力",
        )

        pack = EvidencePackBuilder().build(
            market="A",
            market_bundle=MarketIntelBundle(
                market="A",
                items=[cailian],
                freshness_status="fresh",
            ).to_dict(),
            search_documents=[search],
        )
        payload = pack.to_dict()

        self.assertEqual([item["provider"] for item in payload["structured_items"]], ["cailianpress"])
        self.assertEqual(payload["search_documents"], [])
        self.assertEqual(payload["structured_items"][0]["title"], "AI算力板块走强")

    def test_evidence_pack_ranks_manual_then_official_then_news_then_search(self):
        manual = make_item(
            source="manual",
            provider="manual",
            title="人工重点",
            dedupe_key="manual",
        )
        announcement = make_item(
            source="东方财富",
            provider="eastmoney",
            item_type="announcement",
            title="公司公告",
            url="https://example.com/announcement",
            dedupe_key="ann",
        )
        sina = make_item(
            source="新浪财经",
            provider="sina",
            item_type="market_news",
            title="新浪快讯",
            url="https://example.com/sina",
            dedupe_key="sina",
        )
        search = SearchDocument(
            title="搜索补充",
            url="https://example.com/search-only",
            content="搜索内容",
            query="query",
        )

        pack = EvidencePackBuilder().build(
            market="A",
            stock_bundle=StockIntelBundle(
                market="A",
                code="600519",
                items=[sina, announcement],
                freshness_status="fresh",
            ).to_dict(),
            manual_items=[manual],
            search_documents=[search],
        )
        payload = pack.to_dict()

        self.assertEqual([item["title"] for item in payload["manual_items"]], ["人工重点"])
        self.assertEqual([item["title"] for item in payload["structured_items"]], ["公司公告", "新浪快讯"])
        self.assertEqual([item["title"] for item in payload["search_documents"]], ["搜索补充"])


if __name__ == "__main__":
    unittest.main()
