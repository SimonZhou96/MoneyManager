import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from stock_terminal.models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot
from stock_terminal.providers.eastmoney import (
    EastmoneyStockTerminalProvider,
    parse_eastmoney_fund_flow_rows,
    parse_eastmoney_minute_rows,
    parse_eastmoney_quote,
)
from stock_terminal.repository import InMemoryStockTerminalRepository, MySqlStockTerminalRepository
from stock_terminal.service import StockTerminalService


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.quote_calls = []
        self.kline_calls = []
        self.minute_calls = []
        self.fund_flow_calls = []

    def fetch_quote(self, market, code):
        self.quote_calls.append((market, code))
        return QuoteSnapshot(market=market, code=code, name=code, price=10.0, source=self.name)

    def fetch_klines(self, market, code, timeframe, limit):
        self.kline_calls.append((market, code, timeframe, limit))
        at = datetime(2026, 5, 25, tzinfo=timezone.utc)
        return [KlinePoint(at=at, open=9, high=11, low=8, close=10, volume=100)]

    def fetch_minute(self, market, code):
        self.minute_calls.append((market, code))
        at = datetime(2026, 5, 25, 9, 31, tzinfo=timezone.utc)
        return [MinutePoint(at=at, price=10, average_price=9.8, volume=100)]

    def fetch_fund_flow(self, market, code):
        self.fund_flow_calls.append((market, code))
        at = datetime(2026, 5, 25, tzinfo=timezone.utc)
        return [FundFlowPoint(at=at, inflow=10, outflow=4, net_inflow=6)]


class FailingProvider(FakeProvider):
    name = "failing"

    def fetch_quote(self, market, code):
        raise RuntimeError("quote down")


class NoneQuoteProvider(FakeProvider):
    name = "none_quote"

    def fetch_quote(self, market, code):
        self.quote_calls.append((market, code))
        return None


class EastmoneyLikeProvider(FakeProvider):
    name = "eastmoney"
    last_source = "DatabaseKlineCache"


class FakeKlineDb:
    def __init__(self, frame):
        self.frame = frame
        self.saved_kline_rows = []

    def get_kline_cache(self, market, code, timeframe, max_count=500):
        return self.frame

    def upsert_kline_cache(self, rows):
        row_list = list(rows)
        self.saved_kline_rows.extend(row_list)
        return len(row_list)


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload
        self.raise_calls = 0

    def raise_for_status(self):
        self.raise_calls += 1

    def json(self):
        return self.payload


class RecordingSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}, "timeout": timeout})
        return FakeHttpResponse(self.payloads.pop(0))


class EastmoneyProviderParserTest(unittest.TestCase):
    def test_parse_quote_shape(self):
        payload = {
            "data": {
                "f43": 168800,
                "f44": 169900,
                "f45": 167000,
                "f46": 168000,
                "f47": 100000,
                "f48": 2000000,
                "f57": "600519",
                "f58": "贵州茅台",
                "f60": 166800,
                "f169": 2000,
                "f170": 120,
            }
        }

        quote = parse_eastmoney_quote("A", "SH.600519", payload)

        self.assertEqual(quote.name, "贵州茅台")
        self.assertEqual(quote.price, 1688.0)
        self.assertEqual(quote.change_percent, 1.2)
        self.assertEqual(quote.source, "eastmoney")

    def test_parse_quote_handles_empty_markers(self):
        payload = {"data": {"f43": "-", "f57": "AAPL", "f58": "Apple"}}

        quote = parse_eastmoney_quote("US", "US.AAPL", payload)

        self.assertIsNone(quote.price)
        self.assertEqual(quote.name, "Apple")

    def test_parse_minute_shape(self):
        payload = {
            "data": {
                "trends": [
                    "2026-05-25 09:30,10.0,10.1,100,1000",
                    "2026-05-25 09:31,10.2,10.15,120,1300",
                ]
            }
        }

        rows = parse_eastmoney_minute_rows(payload)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].price, 10.2)
        self.assertEqual(rows[1].average_price, 10.15)
        self.assertEqual(rows[1].turnover, 1300.0)

    def test_parse_fund_flow_shape(self):
        payload = {"data": {"klines": ["2026-05-25,10,4,6,5,1"]}}

        rows = parse_eastmoney_fund_flow_rows(payload)

        self.assertEqual(rows[0].inflow, 10.0)
        self.assertEqual(rows[0].net_inflow, 6.0)
        self.assertEqual(rows[0].main_net_inflow, 5.0)

    def test_live_provider_uses_market_specific_secid(self):
        self.assertEqual(EastmoneyStockTerminalProvider()._secid("A", "SH.600519"), "1.600519")
        self.assertEqual(EastmoneyStockTerminalProvider()._secid("A", "SZ.000001"), "0.000001")
        self.assertEqual(EastmoneyStockTerminalProvider()._secid("HK", "HK.700"), "116.00700")
        self.assertEqual(EastmoneyStockTerminalProvider()._secid("US", "US.AAPL"), "105.AAPL")

    def test_live_provider_fetches_quote_minute_and_fund_flow(self):
        session = RecordingSession(
            [
                {"data": {"f43": 168800, "f58": "贵州茅台"}},
                {"data": {"trends": ["2026-05-25 09:30,10.0,10.1,100,1000"]}},
                {"data": {"klines": ["2026-05-25,10,4,6,5,1"]}},
            ]
        )
        provider = EastmoneyStockTerminalProvider(session=session, timeout_sec=1.5)

        quote = provider.fetch_quote("A", "SH.600519")
        minute = provider.fetch_minute("HK", "HK.700")
        fund_flow = provider.fetch_fund_flow("US", "US.AAPL")

        self.assertEqual(quote.price, 1688.0)
        self.assertEqual(minute[0].price, 10.0)
        self.assertEqual(fund_flow[0].net_inflow, 6.0)
        self.assertEqual(session.calls[0]["params"]["secid"], "1.600519")
        self.assertEqual(session.calls[1]["params"]["secid"], "116.00700")
        self.assertEqual(session.calls[2]["params"]["secid"], "105.AAPL")
        self.assertTrue(session.calls[0]["url"].endswith("/api/qt/stock/get"))
        self.assertTrue(session.calls[1]["url"].endswith("/api/qt/stock/trends2/get"))
        self.assertTrue(session.calls[2]["url"].endswith("/api/qt/stock/fflow/daykline/get"))
        self.assertTrue(all(call["timeout"] == 1.5 for call in session.calls))


class StockTerminalServiceTest(unittest.TestCase):
    def test_summary_uses_cached_quote_without_provider_call(self):
        repo = InMemoryStockTerminalRepository()
        provider = FakeProvider()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        repo.save_quote(
            QuoteSnapshot(market="US", code="US.AAPL", name="Apple", price=190, fetched_at=now, source="cache"),
            expires_at=now + timedelta(minutes=5),
        )
        service = StockTerminalService(repo, [provider], now=lambda: now)

        payload = service.get_summary("US", "US.AAPL")

        self.assertEqual(payload["quote"]["price"], 190.0)
        self.assertEqual(payload["source_status"]["quote"]["status"], "cached")
        self.assertEqual(provider.quote_calls, [])

    def test_summary_fetches_provider_when_cache_empty(self):
        repo = InMemoryStockTerminalRepository()
        provider = FakeProvider()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        service = StockTerminalService(repo, [provider], now=lambda: now)

        payload = service.get_summary("A", "SH.600519")

        self.assertEqual(payload["quote"]["price"], 10.0)
        self.assertEqual(payload["source_status"]["quote"]["status"], "fresh")
        self.assertEqual(provider.quote_calls, [("A", "SH.600519")])

    def test_summary_skips_none_quote_provider_and_uses_next_provider(self):
        repo = InMemoryStockTerminalRepository()
        none_provider = NoneQuoteProvider()
        provider = FakeProvider()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        service = StockTerminalService(repo, [none_provider, provider], now=lambda: now)

        payload = service.get_summary("A", "SH.600519")

        self.assertEqual(payload["quote"]["price"], 10.0)
        self.assertEqual(payload["source_status"]["quote"]["status"], "fresh")
        self.assertEqual(payload["source_status"]["quote"]["source"], "fake")
        self.assertEqual(none_provider.quote_calls, [("A", "SH.600519")])
        self.assertEqual(provider.quote_calls, [("A", "SH.600519")])

    def test_summary_returns_error_when_all_providers_return_none(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        service = StockTerminalService(repo, [NoneQuoteProvider(), NoneQuoteProvider()], now=lambda: now)

        payload = service.get_summary("US", "US.AAPL")

        self.assertIsNone(payload["quote"])
        self.assertEqual(payload["source_status"]["quote"]["status"], "error")
        self.assertIn("none_quote", payload["source_status"]["quote"]["error_message"])
        self.assertIn("returned no data", payload["source_status"]["quote"]["error_message"])

    def test_summary_returns_error_block_when_provider_fails(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        service = StockTerminalService(repo, [FailingProvider()], now=lambda: now)

        payload = service.get_summary("US", "US.AAPL")

        self.assertIsNone(payload["quote"])
        self.assertEqual(payload["source_status"]["quote"]["status"], "error")
        self.assertIn("quote down", payload["source_status"]["quote"]["error_message"])

    def test_summary_returns_stale_quote_when_refresh_fails(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        repo.save_quote(
            QuoteSnapshot(market="US", code="US.AAPL", name="Apple", price=190, fetched_at=now, source="cache"),
            expires_at=now - timedelta(minutes=1),
        )
        service = StockTerminalService(repo, [FailingProvider()], now=lambda: now)

        payload = service.get_summary("US", "US.AAPL")

        self.assertEqual(payload["quote"]["price"], 190.0)
        self.assertEqual(payload["source_status"]["quote"]["status"], "stale")
        self.assertIn("quote down", payload["source_status"]["quote"]["error_message"])
        self.assertEqual(payload["data_gaps"], ["quote"])

    def test_lazy_blocks_fetch_on_demand(self):
        repo = InMemoryStockTerminalRepository()
        provider = FakeProvider()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        service = StockTerminalService(repo, [provider], now=lambda: now)

        kline = service.get_klines("US", "US.AAPL", "1d", 50)
        minute = service.get_minute("US", "US.AAPL")
        flow = service.get_fund_flow("US", "US.AAPL")

        self.assertEqual(kline["market"], "US")
        self.assertEqual(kline["code"], "US.AAPL")
        self.assertEqual(kline["timeframe"], "1d")
        self.assertEqual(kline["rows"][0]["close"], 10.0)
        self.assertEqual(minute["market"], "US")
        self.assertEqual(minute["code"], "US.AAPL")
        self.assertEqual(minute["rows"][0]["price"], 10.0)
        self.assertEqual(flow["market"], "US")
        self.assertEqual(flow["code"], "US.AAPL")
        self.assertEqual(flow["rows"][0]["net_inflow"], 6.0)
        self.assertEqual(kline["source_status"]["kline"]["status"], "fresh")
        self.assertEqual(minute["source_status"]["minute"]["status"], "fresh")
        self.assertEqual(flow["source_status"]["fund_flow"]["status"], "fresh")
        self.assertEqual(kline["source_status"]["kline"]["fetched_at"], now.isoformat())
        self.assertEqual(minute["source_status"]["minute"]["fetched_at"], now.isoformat())
        self.assertEqual(flow["source_status"]["fund_flow"]["fetched_at"], now.isoformat())
        self.assertEqual(provider.kline_calls, [("US", "US.AAPL", "1d", 50)])
        self.assertEqual(provider.minute_calls, [("US", "US.AAPL")])
        self.assertEqual(provider.fund_flow_calls, [("US", "US.AAPL")])

    def test_service_status_uses_stock_terminal_provider_name_not_last_source(self):
        repo = InMemoryStockTerminalRepository()
        provider = EastmoneyLikeProvider()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        service = StockTerminalService(repo, [provider], now=lambda: now)

        kline = service.get_klines("US", "US.AAPL", "1d", 50)

        self.assertEqual(kline["source_status"]["kline"]["status"], "fresh")
        self.assertEqual(kline["source_status"]["kline"]["source"], "eastmoney")

    def test_stale_mysql_kline_cache_fetches_provider_and_saves_fresh_rows(self):
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        db = FakeKlineDb(
            pd.DataFrame(
                [
                    {
                        "date": now - timedelta(days=1),
                        "open": 1.0,
                        "high": 2.0,
                        "low": 1.0,
                        "close": 1.5,
                        "volume": 100.0,
                        "source": "mysql-cache",
                        "updated_at": now - timedelta(hours=1),
                    }
                ]
            )
        )
        repo = MySqlStockTerminalRepository(db, kline_ttl=timedelta(minutes=30))
        provider = FakeProvider()
        service = StockTerminalService(repo, [provider], now=lambda: now)

        payload = service.get_klines("US", "US.AAPL", "1d", 50)

        self.assertEqual(payload["rows"][0]["close"], 10.0)
        self.assertEqual(payload["source_status"]["kline"]["status"], "fresh")
        self.assertEqual(provider.kline_calls, [("US", "US.AAPL", "1d", 50)])
        self.assertEqual(len(db.saved_kline_rows), 1)

    def test_fresh_mysql_kline_cache_skips_provider(self):
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        db = FakeKlineDb(
            pd.DataFrame(
                [
                    {
                        "date": now - timedelta(days=1),
                        "open": 1.0,
                        "high": 2.0,
                        "low": 1.0,
                        "close": 1.5,
                        "volume": 100.0,
                        "source": "mysql-cache",
                        "updated_at": now - timedelta(minutes=10),
                    }
                ]
            )
        )
        repo = MySqlStockTerminalRepository(db, kline_ttl=timedelta(minutes=30))
        provider = FakeProvider()
        service = StockTerminalService(repo, [provider], now=lambda: now)

        payload = service.get_klines("US", "US.AAPL", "1d", 50)

        self.assertEqual(payload["rows"][0]["close"], 1.5)
        self.assertEqual(payload["source_status"]["kline"]["status"], "cached")
        self.assertEqual(provider.kline_calls, [])
        self.assertEqual(db.saved_kline_rows, [])

    def test_lazy_blocks_use_cache_without_provider_call(self):
        repo = InMemoryStockTerminalRepository()
        provider = FakeProvider()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        repo.save_klines(
            "US",
            "US.AAPL",
            "1d",
            [KlinePoint(at=now, open=9, high=11, low=8, close=10, volume=100)],
            source="cache",
            expires_at=now + timedelta(minutes=30),
        )
        repo.save_minute(
            "US",
            "US.AAPL",
            [MinutePoint(at=now, price=10, average_price=9.8, volume=100)],
            source="cache",
            expires_at=now + timedelta(minutes=1),
        )
        repo.save_fund_flow(
            "US",
            "US.AAPL",
            [FundFlowPoint(at=now, inflow=10, outflow=4, net_inflow=6)],
            source="cache",
            expires_at=now + timedelta(minutes=30),
        )
        service = StockTerminalService(repo, [provider], now=lambda: now)

        kline = service.get_klines("US", "US.AAPL", "1d", 50)
        minute = service.get_minute("US", "US.AAPL")
        flow = service.get_fund_flow("US", "US.AAPL")

        self.assertIn("rows", kline)
        self.assertIn("rows", minute)
        self.assertIn("rows", flow)
        self.assertNotIn("klines", kline)
        self.assertNotIn("minute", minute)
        self.assertNotIn("fund_flow", flow)
        self.assertEqual(kline["source_status"]["kline"]["status"], "cached")
        self.assertEqual(minute["source_status"]["minute"]["status"], "cached")
        self.assertEqual(flow["source_status"]["fund_flow"]["status"], "cached")
        self.assertEqual(provider.kline_calls, [])
        self.assertEqual(provider.minute_calls, [])
        self.assertEqual(provider.fund_flow_calls, [])


if __name__ == "__main__":
    unittest.main()
