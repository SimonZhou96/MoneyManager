import unittest
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from stock_terminal.models import FundFlowPoint, KlinePoint, MinutePoint, QuoteSnapshot
from stock_terminal.repository import InMemoryStockTerminalRepository, MySqlStockTerminalRepository


class FakeStockTerminalDb:
    def __init__(self):
        self.kline_frame = pd.DataFrame()
        self.minute_rows = {}
        self.lookups = []

    def get_kline_cache(self, market, code, timeframe, max_count=500):
        self.lookups.append(("stock_kline_cache", market, code, timeframe, max_count))
        return self.kline_frame

    def get_stock_terminal_json_cache(self, table, market, code, trade_date=None):
        self.lookups.append((table, market, code, trade_date))
        return self.minute_rows.get((table, market, code, trade_date))


class StockTerminalRepositoryTest(unittest.TestCase):
    def test_quote_cache_returns_cached_status_before_expiry(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        quote = QuoteSnapshot(market="US", code="US.AAPL", name="Apple", price=190.0, fetched_at=now, source="fake")

        repo.save_quote(quote, expires_at=now + timedelta(minutes=5))
        cached, status = repo.get_quote("US", "US.AAPL", now=now + timedelta(minutes=1))

        self.assertEqual(cached.price, 190.0)
        self.assertEqual(status.status, "cached")
        self.assertFalse(status.stale)

    def test_quote_cache_returns_stale_status_after_expiry(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        quote = QuoteSnapshot(market="US", code="US.AAPL", price=190.0, fetched_at=now, source="fake")

        repo.save_quote(quote, expires_at=now + timedelta(minutes=1))
        cached, status = repo.get_quote("US", "US.AAPL", now=now + timedelta(minutes=3))

        self.assertEqual(cached.price, 190.0)
        self.assertEqual(status.status, "stale")
        self.assertTrue(status.stale)

    def test_quote_error_cache_round_trip(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)

        repo.save_quote_error("US", "US.AAPL", "provider down", source="fallback", fetched_at=now, expires_at=now + timedelta(minutes=1))
        cached, status = repo.get_quote("US", "US.AAPL", now=now)

        self.assertIsNone(cached)
        self.assertEqual(status.status, "error")
        self.assertEqual(status.error_message, "provider down")
        self.assertEqual(status.source, "fallback")

    def test_kline_minute_and_fund_flow_cache_round_trip(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc)
        kline_rows = [KlinePoint(at=now, open=9.0, high=11.0, low=8.0, close=10.0, volume=1000.0)]
        minute_rows = [MinutePoint(at=now, price=10.0, average_price=9.9, volume=100.0)]
        flow_rows = [FundFlowPoint(at=now, inflow=10.0, outflow=4.0, net_inflow=6.0)]

        repo.save_klines("A", "SH.600519", "1d", kline_rows, source="fake", expires_at=now + timedelta(minutes=30))
        repo.save_minute("A", "SH.600519", minute_rows, source="fake", expires_at=now + timedelta(minutes=1))
        repo.save_fund_flow("A", "SH.600519", flow_rows, source="fake", expires_at=now + timedelta(minutes=30))

        kline_cached, kline_status = repo.get_klines("A", "SH.600519", "1d", limit=20, now=now)
        minute_cached, minute_status = repo.get_minute("A", "SH.600519", now=now)
        flow_cached, flow_status = repo.get_fund_flow("A", "SH.600519", now=now)

        self.assertEqual(kline_cached[0].close, 10.0)
        self.assertEqual(kline_status.status, "cached")
        self.assertEqual(minute_cached[0].price, 10.0)
        self.assertEqual(minute_status.status, "cached")
        self.assertEqual(flow_cached[0].net_inflow, 6.0)
        self.assertEqual(flow_status.status, "cached")

    def test_us_minute_cache_uses_market_local_trade_date(self):
        repo = InMemoryStockTerminalRepository()
        saved_trade_date = date(2026, 5, 25)
        minute_rows = [
            MinutePoint(
                at=datetime(2026, 5, 25, 15, 30, tzinfo=timezone.utc),
                price=190.0,
            )
        ]

        repo.save_minute(
            "US",
            "US.AAPL",
            minute_rows,
            source="fake",
            expires_at=datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc),
            trade_date=saved_trade_date,
        )
        cached, status = repo.get_minute(
            "US",
            "US.AAPL",
            now=datetime(2026, 5, 26, 0, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(cached[0].price, 190.0)
        self.assertEqual(status.status, "cached")

    def test_explicit_minute_trade_date_wins(self):
        repo = InMemoryStockTerminalRepository()
        now = datetime(2026, 5, 26, 0, 30, tzinfo=timezone.utc)

        repo.save_minute(
            "US",
            "US.AAPL",
            [MinutePoint(at=now, price=190.0)],
            source="fake",
            expires_at=now + timedelta(minutes=30),
            trade_date=date(2026, 5, 25),
        )
        repo.save_minute(
            "US",
            "US.AAPL",
            [MinutePoint(at=now, price=191.0)],
            source="fake",
            expires_at=now + timedelta(minutes=30),
            trade_date=date(2026, 5, 26),
        )

        cached, _ = repo.get_minute("US", "US.AAPL", now=now, trade_date=date(2026, 5, 26))

        self.assertEqual(cached[0].price, 191.0)

    def test_mysql_minute_lookup_uses_market_local_trade_date(self):
        db = FakeStockTerminalDb()
        db.minute_rows[("stock_minute_cache", "US", "US.AAPL", date(2026, 5, 25))] = {
            "payload_json": [
                {
                    "at": "2026-05-25T15:30:00+00:00",
                    "price": 190.0,
                    "average_price": 189.5,
                    "volume": 100.0,
                }
            ],
            "source": "fake-db",
            "fetched_at": datetime(2026, 5, 25, 15, 31, tzinfo=timezone.utc),
            "expires_at": datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc),
        }
        repo = MySqlStockTerminalRepository(db)

        cached, status = repo.get_minute(
            "US",
            "US.AAPL",
            now=datetime(2026, 5, 26, 0, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(db.lookups[-1], ("stock_minute_cache", "US", "US.AAPL", date(2026, 5, 25)))
        self.assertEqual(cached[0].price, 190.0)
        self.assertEqual(status.status, "cached")

    def test_mysql_kline_cache_uses_updated_at_for_freshness(self):
        db = FakeStockTerminalDb()
        db.kline_frame = pd.DataFrame(
            [
                {
                    "date": datetime(2026, 5, 25, tzinfo=timezone.utc),
                    "open": 9.0,
                    "high": 11.0,
                    "low": 8.0,
                    "close": 10.0,
                    "volume": 100.0,
                    "turnover": None,
                    "source": "mysql-cache",
                    "updated_at": datetime(2026, 5, 25, 9, 20, tzinfo=timezone.utc),
                }
            ]
        )
        repo = MySqlStockTerminalRepository(db, kline_ttl=timedelta(minutes=30))

        rows, status = repo.get_klines(
            "US",
            "US.AAPL",
            "1d",
            now=datetime(2026, 5, 25, 9, 40, tzinfo=timezone.utc),
        )

        self.assertEqual(rows[0].close, 10.0)
        self.assertEqual(status.status, "cached")
        self.assertFalse(status.stale)

    def test_mysql_kline_cache_without_updated_at_forces_refresh(self):
        db = FakeStockTerminalDb()
        db.kline_frame = pd.DataFrame(
            [
                {
                    "date": datetime(2026, 5, 25, tzinfo=timezone.utc),
                    "open": 9.0,
                    "high": 11.0,
                    "low": 8.0,
                    "close": 10.0,
                    "volume": 100.0,
                    "source": "mysql-cache",
                }
            ]
        )
        repo = MySqlStockTerminalRepository(db, kline_ttl=timedelta(minutes=30))

        _, status = repo.get_klines(
            "US",
            "US.AAPL",
            "1d",
            now=datetime(2026, 5, 25, 9, 40, tzinfo=timezone.utc),
        )

        self.assertEqual(status.status, "stale")
        self.assertTrue(status.stale)


if __name__ == "__main__":
    unittest.main()
