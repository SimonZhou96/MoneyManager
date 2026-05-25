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
            dedupe_key="stock-headline",
        )
        market_item = make_item(
            scope_type="market",
            code="",
            title="Market headline",
            summary="Macro development.",
            dedupe_key="market-headline",
        )
        manual_item = make_item(
            source="manual",
            provider="manual",
            item_type="manual_note",
            title="Manual context",
            summary="Analyst supplied context.",
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
        self.assertEqual(
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
        self.assertEqual(payload["citations"], [{"label": "source", "url": "https://example.com/source"}])


if __name__ == "__main__":
    unittest.main()
