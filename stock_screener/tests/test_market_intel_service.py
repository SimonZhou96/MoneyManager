import unittest
from datetime import datetime, timedelta

from market_intel.models import IntelItem
from market_intel.repository import InMemoryMarketIntelRepository
from market_intel.service import MarketIntelService


def make_item(
    *,
    scope_type="stock",
    market="US",
    code="AAPL",
    provider="fixture",
    item_type="market_news",
    title="Fixture news",
    dedupe_key="fixture-news",
):
    fetched_at = datetime(2026, 5, 25, 10, 0, 0)
    return IntelItem(
        scope_type=scope_type,
        market=market,
        code=code,
        source=provider,
        provider=provider,
        item_type=item_type,
        title=title,
        summary=f"{title} summary",
        url=f"https://example.com/{dedupe_key}",
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(minutes=15),
        dedupe_key=dedupe_key,
    )


class CountingProvider:
    name = "fixture"

    def __init__(self):
        self.stock_calls = 0
        self.market_calls = 0

    @property
    def is_available(self):
        return True

    def fetch_stock(self, market, code):
        self.stock_calls += 1
        return [
            make_item(
                market=market,
                code=code,
                provider=self.name,
                title="Fresh stock news",
                dedupe_key=f"{market}-{code}-fresh",
            )
        ]

    def fetch_market(self, market):
        self.market_calls += 1
        return [
            make_item(
                scope_type="market",
                market=market,
                code="",
                provider=self.name,
                item_type="market_news",
                title="Macro headline",
                dedupe_key=f"{market}-macro",
            ),
            make_item(
                scope_type="market",
                market=market,
                code="",
                provider=self.name,
                item_type="index_snapshot",
                title="Index snapshot",
                dedupe_key=f"{market}-index",
            ),
        ]


class ProviderNameOnlyProvider(CountingProvider):
    provider_name = "provider-name-only"

    @property
    def name(self):
        return None

    def fetch_stock(self, market, code):
        self.stock_calls += 1
        return [
            make_item(
                market=market,
                code=code,
                provider=self.provider_name,
                title="Provider-name-only news",
                dedupe_key=f"{market}-{code}-provider-name-only",
            )
        ]


class FailingProvider:
    name = "broken"

    @property
    def is_available(self):
        return True

    def fetch_stock(self, market, code):
        raise RuntimeError("provider unavailable")

    def fetch_market(self, market):
        raise RuntimeError("provider unavailable")


class MarketIntelServiceTests(unittest.TestCase):
    def test_force_refresh_then_cached_get_does_not_call_provider_again(self):
        repo = InMemoryMarketIntelRepository()
        provider = CountingProvider()
        service = MarketIntelService(repo, [provider])

        fresh = service.get_stock_intel("US", "AAPL", force_refresh=True)
        cached = service.get_stock_intel("US", "AAPL")

        self.assertEqual(provider.stock_calls, 1)
        self.assertEqual(fresh, cached)
        self.assertEqual(cached["freshness_status"], "fresh")
        self.assertEqual(cached["groups"]["market_news"][0]["title"], "Fresh stock news")

    def test_provider_failure_returns_stale_cache_and_source_status_failed(self):
        repo = InMemoryMarketIntelRepository()
        repo.upsert_items([
            make_item(
                market="US",
                code="AAPL",
                provider="fixture",
                title="Cached stock news",
                dedupe_key="cached-stock-news",
            ).to_dict()
        ])
        service = MarketIntelService(repo, [FailingProvider()])

        payload = service.refresh_stock_intel("US", "AAPL")

        self.assertEqual(payload["freshness_status"], "stale")
        self.assertTrue(payload["groups"]["market_news"][0]["is_stale"])
        self.assertEqual(payload["source_status"]["broken"]["status"], "failed")
        self.assertIn("provider unavailable", payload["source_status"]["broken"]["error_message"])
        runs = service.list_provider_runs(provider="broken", market="US", code="AAPL", status="failed")
        self.assertEqual(len(runs), 1)
        self.assertIn("provider unavailable", runs[0]["error_message"])

    def test_market_digest_groups_market_items(self):
        repo = InMemoryMarketIntelRepository()
        provider = CountingProvider()
        service = MarketIntelService(repo, [provider])

        payload = service.refresh_market_digest("US")

        self.assertEqual(payload["scope_type"], "market")
        self.assertEqual(payload["code"], "")
        self.assertEqual(set(payload["groups"].keys()), {"market_news", "index_snapshot"})
        self.assertEqual(payload["groups"]["market_news"][0]["title"], "Macro headline")
        self.assertEqual(payload["groups"]["index_snapshot"][0]["title"], "Index snapshot")

    def test_provider_name_fallback_is_used_when_name_is_missing(self):
        repo = InMemoryMarketIntelRepository()
        provider = ProviderNameOnlyProvider()
        service = MarketIntelService(repo, [provider])

        payload = service.refresh_stock_intel("US", "MSFT")

        self.assertIn("provider-name-only", payload["source_status"])
        runs = service.list_provider_runs(provider="provider-name-only", market="US", code="MSFT", status="success")
        self.assertEqual(len(runs), 1)


if __name__ == "__main__":
    unittest.main()
