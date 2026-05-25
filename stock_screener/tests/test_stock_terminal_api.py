import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.auth import CurrentUser, require_user
from web.business import BusinessError
from web.errors import business_error_handler
from web.stock_terminal import get_stock_terminal_service, router


class FakeStockTerminalService:
    def __init__(self):
        self.calls = []

    def get_summary(self, market, code):
        self.calls.append(("summary", market, code))
        return {"market": market, "code": code, "quote": None, "source_status": {"quote": {"status": "empty"}}}

    def get_klines(self, market, code, timeframe, limit):
        self.calls.append(("klines", market, code, timeframe, limit))
        return {
            "market": market,
            "code": code,
            "timeframe": timeframe,
            "rows": [],
            "source_status": {"kline": {"status": "empty"}},
        }

    def get_minute(self, market, code):
        self.calls.append(("minute", market, code))
        return {"market": market, "code": code, "rows": [], "source_status": {"minute": {"status": "empty"}}}

    def get_fund_flow(self, market, code):
        self.calls.append(("fund_flow", market, code))
        return {"market": market, "code": code, "rows": [], "source_status": {"fund_flow": {"status": "empty"}}}


class StockTerminalApiTest(unittest.TestCase):
    def setUp(self):
        self.service = FakeStockTerminalService()
        app = FastAPI()
        app.add_exception_handler(BusinessError, business_error_handler)
        app.include_router(router)
        app.dependency_overrides[require_user] = lambda: CurrentUser(id=1, username="tester", role="admin")
        app.dependency_overrides[get_stock_terminal_service] = lambda: self.service
        self.client = TestClient(app)

    def test_summary_normalizes_a_share_code(self):
        response = self.client.get("/api/stock-terminal/A/600519/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], "SH.600519")
        self.assertEqual(self.service.calls, [("summary", "A", "SH.600519")])

    def test_klines_validates_timeframe_and_limit(self):
        response = self.client.get("/api/stock-terminal/US/AAPL/klines?timeframe=1d&limit=20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.calls, [("klines", "US", "US.AAPL", "1d", 20)])

    def test_klines_accepts_limit_500(self):
        response = self.client.get("/api/stock-terminal/US/AAPL/klines?timeframe=1d&limit=500")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.calls, [("klines", "US", "US.AAPL", "1d", 500)])

    def test_klines_rejects_limit_above_500_before_service_call(self):
        response = self.client.get("/api/stock-terminal/US/AAPL/klines?timeframe=1d&limit=501")
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(self.service.calls, [])

    def test_minute_and_fund_flow_routes_use_selected_stock_only(self):
        minute = self.client.get("/api/stock-terminal/HK/00700/minute")
        flow = self.client.get("/api/stock-terminal/HK/00700/fund-flow")
        self.assertEqual(minute.status_code, 200)
        self.assertEqual(flow.status_code, 200)
        self.assertEqual(self.service.calls, [("minute", "HK", "HK.00700"), ("fund_flow", "HK", "HK.00700")])

    def test_invalid_timeframe_returns_business_error(self):
        response = self.client.get("/api/stock-terminal/US/AAPL/klines?timeframe=bad&limit=20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error_code"], "STOCK_TERMINAL_INVALID_REQUEST")
        self.assertEqual(self.service.calls, [])

    def test_service_dependency_does_not_initialize_schema_per_request(self):
        class FakeDb:
            def __init__(self):
                self.init_stock_terminal_schema_called = False

            def init_stock_terminal_schema(self):
                self.init_stock_terminal_schema_called = True

        db = FakeDb()
        with patch("web.stock_terminal.build_stock_terminal_providers", return_value=[]):
            service = get_stock_terminal_service(db)

        self.assertFalse(db.init_stock_terminal_schema_called)
        self.assertIs(service.repository.db, db)


if __name__ == "__main__":
    unittest.main()
