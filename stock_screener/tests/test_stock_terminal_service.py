import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from stock_terminal.models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot
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
