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
        self.text = payload if isinstance(payload, str) else ""

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
        self.assertEqual(provider.provider_name, "news")
        self.assertEqual(provider.name, "news")

    def test_cailianpress_fetch_market_accepts_nested_roll_data(self):
        from market_intel.providers.cailianpress import CailianpressIntelProvider

        session = FakeSession([
            {
                "data": {
                    "roll_data": [
                        {
                            "title": "政策利好落地",
                            "content": "科技板块走强",
                            "ctime": 1779685200000,
                            "id": "cls-nested-1",
                        }
                    ]
                }
            }
        ])
        provider = CailianpressIntelProvider(session=session)

        items = provider.fetch_market("A")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].provider, "cailianpress")
        self.assertEqual(items[0].source, "财联社")
        self.assertEqual(items[0].item_type, "market_news")
        self.assertEqual(items[0].title, "政策利好落地")

    def test_cailianpress_fetch_market_accepts_top_level_roll_data(self):
        from market_intel.providers.cailianpress import CailianpressIntelProvider

        session = FakeSession([
            {
                "roll_data": [
                    {
                        "title": "市场快讯",
                        "content": "指数集体走强",
                        "ctime": 1779685200000,
                        "id": "cls-top-1",
                    }
                ]
            }
        ])
        provider = CailianpressIntelProvider(session=session)

        items = provider.fetch_market("A")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].dedupe_key, "cailianpress:market_news:cls-top-1")

    def test_cailianpress_html_parser_extracts_telegraph_items(self):
        from market_intel.providers.cailianpress import parse_cailianpress_html

        html = """
        <html><body>
          <div class="telegraph-content-box">
            <span class="telegraph-time">09:31</span>
            <div class="telegraph-content">半导体板块快速拉升</div>
          </div>
        </body></html>
        """

        rows = parse_cailianpress_html(html)

        self.assertEqual(rows, [{"title": "半导体板块快速拉升", "content": "半导体板块快速拉升", "time": "09:31"}])

    def test_legacy_news_provider_alias_still_works(self):
        session = FakeSession([
            {
                "roll_data": [
                    {
                        "title": "兼容快讯",
                        "content": "沿用财联社解析逻辑",
                        "ctime": 1779685200000,
                        "id": "legacy-news-1",
                    }
                ]
            }
        ])
        provider = NewsIntelProvider(session=session)

        items = provider.fetch_market("A")

        self.assertEqual(NewsIntelProvider.provider_name, "news")
        self.assertEqual(NewsIntelProvider.name, "news")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].provider, "news")
        self.assertEqual(items[0].dedupe_key, "news:market_news:legacy-news-1")

    def test_sina_jsonp_parser_returns_market_news_rows(self):
        from market_intel.providers.sina import parse_sina_live_feed

        text = 'callback({"result":{"data":{"feed":{"list":[{"title":"美联储释放信号","content":"全球市场波动","created_at":"2026-05-25 09:32:00","url":"https://finance.sina.com.cn/news"}]}}}})'

        rows = parse_sina_live_feed(text)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "美联储释放信号")
        self.assertEqual(rows[0]["content"], "全球市场波动")

    def test_sina_provider_fetches_market_news(self):
        from market_intel.providers.sina import SinaNewsIntelProvider

        session = FakeSession([
            'callback({"result":{"data":{"feed":{"list":[{"title":"盘中快讯","content":"港股科技走强","created_at":"2026-05-25 09:32:00"}]}}}})'
        ])
        provider = SinaNewsIntelProvider(session=session)

        items = provider.fetch_market("HK")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].provider, "sina")
        self.assertEqual(items[0].source, "新浪财经")
        self.assertEqual(items[0].item_type, "market_news")

    def test_tradingview_news_list_parser_accepts_supported_shapes(self):
        from market_intel.providers.tradingview import parse_tradingview_news_list

        self.assertEqual(parse_tradingview_news_list([{"id": "a"}]), [{"id": "a"}])
        self.assertEqual(parse_tradingview_news_list({"items": [{"id": "b"}]}), [{"id": "b"}])
        self.assertEqual(parse_tradingview_news_list({"data": [{"id": "c"}]}), [{"id": "c"}])
        self.assertEqual(parse_tradingview_news_list({"news": [{"id": "d"}]}), [{"id": "d"}])

    def test_tradingview_provider_fetches_list_and_bounded_details(self):
        from market_intel.providers.tradingview import TradingViewNewsIntelProvider

        session = FakeSession([
            {
                "items": [
                    {"id": "tv-1", "title": "AI chip stocks rise", "published": 1779685200},
                    {"id": "tv-2", "title": "Oil prices fall", "published": 1779685300},
                ]
            },
            {"story": {"body": "Detailed AI chip context", "link": "https://www.tradingview.com/news/tv-1"}},
        ])
        provider = TradingViewNewsIntelProvider(session=session, detail_limit=1)

        items = provider.fetch_market("US")

        self.assertEqual(len(items), 2)
        self.assertEqual(session.calls[0][0], "https://news-mediator.tradingview.com/news-flow/v2/news")
        self.assertEqual(session.calls[1][0], "https://news-headlines.tradingview.com/v3/story")
        self.assertEqual(items[0].provider, "tradingview")
        self.assertIn("Detailed AI chip context", items[0].summary)

    def test_tradingview_provider_keeps_list_items_when_detail_fails(self):
        from market_intel.providers.tradingview import TradingViewNewsIntelProvider

        session = FakeSession([
            {
                "items": [
                    {"id": "tv-1", "title": "AI chip stocks rise", "published": 1779685200},
                ]
            },
            ({"error": "bad detail"}, RuntimeError("HTTP 500")),
        ])
        provider = TradingViewNewsIntelProvider(session=session, detail_limit=1)

        items = provider.fetch_market("US")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].provider, "tradingview")
        self.assertEqual(items[0].title, "AI chip stocks rise")
        self.assertEqual(items[0].summary, "")

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


class MarketIntelSourceRegistryTests(unittest.TestCase):
    def test_source_registry_exposes_default_enabled_and_disabled_sources(self):
        from market_intel.providers.source_registry import list_source_configs

        configs = {item.provider: item for item in list_source_configs()}

        self.assertTrue(configs["cailianpress"].default_enabled)
        self.assertTrue(configs["sina"].default_enabled)
        self.assertTrue(configs["tradingview"].default_enabled)
        self.assertTrue(configs["eastmoney"].default_enabled)
        self.assertTrue(configs["global_index"].default_enabled)
        self.assertFalse(configs["iwencai"].default_enabled)
        self.assertFalse(configs["eastmoney_search"].default_enabled)
        self.assertFalse(configs["xueqiu"].default_enabled)
        self.assertFalse(configs["xueqiu"].automated_safe)
        self.assertTrue(configs["xueqiu"].requires_browser)

    def test_source_registry_payload_is_frontend_safe(self):
        from market_intel.providers.source_registry import source_status_payload

        rows = source_status_payload()
        cailianpress = next(item for item in rows if item["provider"] == "cailianpress")
        xueqiu = next(item for item in rows if item["provider"] == "xueqiu")

        self.assertEqual(cailianpress["source"], "财联社")
        self.assertEqual(cailianpress["status"], "enabled")
        self.assertEqual(xueqiu["status"], "disabled")
        self.assertEqual(xueqiu["disabled_reason"], "requires browser/cookie support")

    def test_factory_uses_env_order_and_enabled_flags(self):
        import os
        from unittest.mock import patch

        env = {
            "MARKET_INTEL_PROVIDER_ORDER": "tradingview,cailianpress,sina,xueqiu,unknown",
            "MARKET_INTEL_ENABLE_TRADINGVIEW": "1",
            "MARKET_INTEL_ENABLE_CAILIANPRESS": "1",
            "MARKET_INTEL_ENABLE_SINA": "0",
            "MARKET_INTEL_ENABLE_XUEQIU": "1",
            "MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            providers = build_market_intel_providers(enable_live=True)

        self.assertEqual([provider.provider_name for provider in providers], ["tradingview", "cailianpress"])

    def test_xueqiu_requires_browser_provider_allow_flag(self):
        import os
        from unittest.mock import patch

        from market_intel.providers.source_registry import config_by_provider

        with patch.dict(os.environ, {
            "MARKET_INTEL_ENABLE_XUEQIU": "1",
            "MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED": "0",
        }, clear=False):
            self.assertFalse(config_by_provider()["xueqiu"].enabled())

        with patch.dict(os.environ, {
            "MARKET_INTEL_ENABLE_XUEQIU": "1",
            "MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED": "1",
        }, clear=False):
            self.assertTrue(config_by_provider()["xueqiu"].enabled())


if __name__ == "__main__":
    unittest.main()
