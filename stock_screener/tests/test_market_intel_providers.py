import unittest

from market_intel.providers.eastmoney import (
    EastmoneyMarketIntelProvider,
    eastmoney_secu_code,
)
from market_intel.providers.factory import build_market_intel_providers
from market_intel.providers.global_index import GlobalIndexProvider
from market_intel.providers.news import NewsIntelProvider


class FakeResponse:
    def __init__(self, payload, status_error=None):
        self.payload = payload
        self.status_error = status_error

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_error is not None:
            raise self.status_error
        return None


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.payloads:
            raise AssertionError(f"Unexpected request: {url}")
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, tuple):
            return FakeResponse(payload[0], status_error=payload[1])
        return FakeResponse(payload)


class MarketIntelProviderTests(unittest.TestCase):
    def test_eastmoney_secu_code_normalizes_market_codes(self):
        self.assertEqual(eastmoney_secu_code("A", "600519"), "600519.SH")
        self.assertEqual(eastmoney_secu_code("A", "601398"), "601398.SH")
        self.assertEqual(eastmoney_secu_code("A", "603288"), "603288.SH")
        self.assertEqual(eastmoney_secu_code("A", "688981"), "688981.SH")
        self.assertEqual(eastmoney_secu_code("A", "000001"), "000001.SZ")
        self.assertEqual(eastmoney_secu_code("HK", "700"), "00700.HK")
        self.assertEqual(eastmoney_secu_code("US", "AAPL.US"), "AAPL")

    def test_eastmoney_fetch_stock_returns_all_item_types(self):
        session = FakeSession([
            {
                "data": [
                    {
                        "title": "年度分红公告",
                        "notice_date": "2026-05-24 09:00:00",
                        "url": "https://example.com/notice",
                        "art_code": "notice-1",
                    }
                ]
            },
            {
                "data": [
                    {
                        "title": "买入评级报告",
                        "publish_date": "2026-05-23",
                        "summary": "维持买入评级",
                        "url": "https://example.com/report",
                        "info_code": "report-1",
                    }
                ]
            },
            {
                "data": {
                    "security_name_abbr": "贵州茅台",
                    "total_operate_income": 100,
                    "net_profit": 20,
                    "roe": 18.5,
                }
            },
        ])
        provider = EastmoneyMarketIntelProvider(session=session)

        items = provider.fetch_stock("A", "600519")

        self.assertEqual(
            [item.item_type for item in items],
            ["announcement", "research_report", "financial"],
        )
        self.assertEqual({item.provider for item in items}, {"eastmoney"})
        self.assertEqual(provider.name, "eastmoney")
        self.assertEqual(provider.provider_name, "eastmoney")
        self.assertTrue(all(item.raw_json for item in items))

    def test_eastmoney_fetch_stock_keeps_other_sub_sources_when_one_fails(self):
        session = FakeSession([
            ({"error": "bad request"}, RuntimeError("HTTP 400")),
            {
                "data": [
                    {
                        "title": "买入评级报告",
                        "publish_date": "2026-05-23",
                        "summary": "维持买入评级",
                        "info_code": "report-1",
                    }
                ]
            },
            {
                "data": {
                    "security_name_abbr": "贵州茅台",
                    "total_operate_income": 100,
                    "net_profit": 20,
                }
            },
        ])
        provider = EastmoneyMarketIntelProvider(session=session)

        items = provider.fetch_stock("A", "600519")

        self.assertEqual(
            [item.item_type for item in items],
            ["research_report", "financial"],
        )
        self.assertEqual({item.provider for item in items}, {"eastmoney"})

    def test_news_fetch_market_returns_market_news(self):
        session = FakeSession([
            {
                "roll_data": [
                    {
                        "title": "市场快讯",
                        "content": "指数集体走强",
                        "ctime": 1779685200000,
                        "id": "cls-1",
                    }
                ]
            }
        ])
        provider = NewsIntelProvider(session=session)

        items = provider.fetch_market("A")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_type, "market_news")
        self.assertEqual(items[0].title, "市场快讯")
        self.assertEqual(items[0].provider, "news")
        self.assertEqual(provider.name, "news")

    def test_global_index_fetch_market_returns_index_snapshot(self):
        session = FakeSession([
            {
                "data": {
                    "diff": [
                        {
                            "f14": "纳斯达克",
                            "f2": 19200.5,
                            "f3": 1.25,
                            "f12": "IXIC",
                        }
                    ]
                }
            }
        ])
        provider = GlobalIndexProvider(session=session)

        items = provider.fetch_market("global")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_type, "index_snapshot")
        self.assertEqual(items[0].title, "纳斯达克")
        self.assertIn("19200.5", items[0].summary)
        self.assertEqual(items[0].provider, "global_index")

    def test_factory_disabled_returns_empty_provider_list(self):
        self.assertEqual(build_market_intel_providers(enable_live=False), [])


if __name__ == "__main__":
    unittest.main()
