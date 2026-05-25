import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from market import normalize_market
from web.auth import CurrentUser, require_user
from web.business import BusinessError
from web.errors import business_error_handler
from web.market_intel import get_market_intel_service, router
from web.single_stock import normalize_stock_code


class FakeMarketIntelService:
    def __init__(self):
        self.stock_calls = []
        self.digest_calls = []
        self.run_calls = []

    def get_stock_intel(self, market, code, force_refresh=False):
        self.stock_calls.append((market, code, force_refresh))
        return {
            "scope_type": "stock",
            "market": market,
            "code": code,
            "freshness_status": "fresh",
            "source_status": {
                "fake": {
                    "provider": "fake",
                    "status": "success",
                    "item_count": 2,
                }
            },
            "groups": {
                "news": [
                    {
                        "scope_type": "stock",
                        "market": market,
                        "code": code,
                        "source": "fake",
                        "provider": "fake",
                        "item_type": "news",
                        "title": "贵州茅台跟踪",
                        "summary": "测试新闻",
                        "url": "https://example.com/maotai",
                        "fetched_at": "2026-05-25T00:00:00+00:00",
                        "expires_at": "2026-05-25T02:00:00+00:00",
                        "dedupe_key": "news-1",
                    },
                    {
                        "scope_type": "stock",
                        "market": market,
                        "code": code,
                        "source": "fake",
                        "provider": "fake",
                        "item_type": "news",
                        "title": "贵州茅台二次跟踪",
                        "summary": "第二条测试新闻",
                        "url": "https://example.com/maotai-2",
                        "fetched_at": "2026-05-25T00:00:00+00:00",
                        "expires_at": "2026-05-25T02:00:00+00:00",
                        "dedupe_key": "news-2",
                    },
                ]
            },
            "data_gaps": [],
        }

    def get_market_digest(self, market, force_refresh=False):
        self.digest_calls.append((market, force_refresh))
        return {
            "scope_type": "market",
            "market": market,
            "freshness_status": "fresh",
            "source_status": {},
            "groups": {},
        }

    def list_provider_runs(self, provider=None, market=None, code=None, status=None, limit=50):
        self.run_calls.append((provider, market, code, status, limit))
        return [
            {
                "provider": "fake",
                "market": market or "A",
                "code": code or "SH.600519",
                "status": status or "success",
            }
        ]


class MarketIntelApiTest(unittest.TestCase):
    def setUp(self):
        self.service = FakeMarketIntelService()
        app = FastAPI()
        app.add_exception_handler(BusinessError, business_error_handler)
        app.include_router(router)
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=1, username="tester", role="admin")
        app.dependency_overrides[get_market_intel_service] = lambda: self.service
        self.client = TestClient(app)

    def test_stock_intel_returns_normalized_identity_and_source_status(self):
        response = self.client.get("/api/market-intel/stocks/A/600519?refresh=true&max_items_per_group=1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        market = normalize_market("A")
        code = normalize_stock_code(market, "600519")
        self.assertEqual(payload["market"], market)
        self.assertEqual(payload["code"], code)
        self.assertEqual(self.service.stock_calls, [(market, code, True)])
        self.assertIn("fake", payload["source_status"])
        self.assertEqual(payload["source_status"]["fake"]["status"], "success")
        self.assertEqual(len(payload["groups"]["news"]), 1)

    def test_provider_runs_returns_runs_shape(self):
        response = self.client.get("/api/market-intel/provider-runs?market=A&code=600519&limit=3")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        market = normalize_market("A")
        code = normalize_stock_code(market, "600519")
        self.assertIn("runs", payload)
        self.assertIsInstance(payload["runs"], list)
        self.assertEqual(self.service.run_calls, [(None, market, code, None, 3)])

    def test_sources_endpoint_returns_registry_rows(self):
        response = self.client.get("/api/market-intel/sources")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("sources", payload)
        providers = {item["provider"]: item for item in payload["sources"]}
        self.assertEqual(providers["cailianpress"]["source"], "财联社")
        self.assertEqual(providers["xueqiu"]["status"], "disabled")
        self.assertTrue(providers["xueqiu"]["requires_browser"])

    def test_evidence_pack_preview_uses_market_intel_without_llm(self):
        response = self.client.post(
            "/api/market-intel/evidence-pack/preview",
            json={
                "market": "A",
                "code": "600519",
                "include_search": False,
                "force_refresh": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        market = normalize_market("A")
        code = normalize_stock_code(market, "600519")
        self.assertEqual(payload["market"], market)
        self.assertEqual(payload["code"], code)
        self.assertIn("data_gaps", payload)
        self.assertEqual(self.service.stock_calls, [(market, code, True)])
        self.assertEqual(self.service.digest_calls, [(market, True)])

    def test_stock_intel_rejects_include_search_true(self):
        response = self.client.get("/api/market-intel/stocks/A/600519?include_search=true")

        self.assertUnsupportedSearch(response)
        self.assertEqual(self.service.stock_calls, [])

    def test_refresh_stock_intel_rejects_include_search_true(self):
        response = self.client.post(
            "/api/market-intel/stocks/A/600519/refresh",
            json={"include_search": True},
        )

        self.assertUnsupportedSearch(response)
        self.assertEqual(self.service.stock_calls, [])

    def test_evidence_pack_preview_rejects_include_search_true(self):
        response = self.client.post(
            "/api/market-intel/evidence-pack/preview",
            json={"market": "A", "code": "600519", "include_search": True},
        )

        self.assertUnsupportedSearch(response)
        self.assertEqual(self.service.stock_calls, [])
        self.assertEqual(self.service.digest_calls, [])

    def test_market_intel_service_dependency_does_not_initialize_schema_per_request(self):
        class FakeDb:
            def __init__(self):
                self.init_calls = 0

            def init_market_intel_schema(self):
                self.init_calls += 1

        db = FakeDb()

        service = get_market_intel_service(db)

        self.assertIsNotNone(service)
        self.assertEqual(db.init_calls, 0)

    def test_provider_runs_requires_market_when_code_is_provided(self):
        response = self.client.get("/api/market-intel/provider-runs?code=600519")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload.get("ok", True))
        self.assertEqual(payload.get("error_code"), "MARKET_INTEL_INVALID_REQUEST")
        self.assertIn("market", payload.get("message", ""))
        self.assertEqual(self.service.run_calls, [])

    def assertUnsupportedSearch(self, response):
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload.get("ok", True))
        self.assertEqual(payload.get("error_code"), "MARKET_INTEL_INVALID_REQUEST")
        self.assertIn("include_search", payload.get("message", ""))


if __name__ == "__main__":
    unittest.main()
