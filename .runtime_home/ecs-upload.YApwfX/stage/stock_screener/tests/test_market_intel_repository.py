import json
import math
import unittest
from datetime import date, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import MarketDatabase


SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.executemany_calls = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, values):
        self.executemany_calls.append((sql, list(values)))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj


class FailedMarketIntelAlterCursor(FakeCursor):
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT `event_time` FROM market_intel_items" in sql:
            raise Exception("Unknown column 'event_time'")
        if "ALTER TABLE market_intel_items ADD COLUMN event_time" in sql:
            raise RuntimeError("alter failed")


class MarketIntelRepositoryTests(unittest.TestCase):
    def test_schema_file_mentions_all_three_tables(self):
        with open(SQL_DIR / "017_market_intel.sql", "r", encoding="utf-8") as f:
            sql = f.read()

        for table in [
            "market_intel_items",
            "market_intel_bundles",
            "market_intel_provider_runs",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)
        self.assertIn("raw_json JSON", sql)
        self.assertIn("bundle_json JSON", sql)
        self.assertIn("source_status_json JSON", sql)
        self.assertIn("event_time DATETIME(6) NULL", sql)

    def test_init_market_intel_schema_executes_deployment_sql(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.init_market_intel_schema()

        executed_sql = "\n".join(sql for sql, _ in conn.cursor_obj.executed)
        self.assertIn("CREATE TABLE IF NOT EXISTS market_intel_items", executed_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS market_intel_bundles", executed_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS market_intel_provider_runs", executed_sql)

    def test_market_intel_event_time_migration_raises_when_alter_fails(self):
        cursor = FailedMarketIntelAlterCursor()
        db = MarketDatabase.__new__(MarketDatabase)

        with self.assertRaises(RuntimeError):
            db._ensure_market_intel_schema_migrations(cursor)

    def test_in_memory_repository_stores_items_bundle_and_provider_runs(self):
        from market_intel.repository import InMemoryMarketIntelRepository

        repo = InMemoryMarketIntelRepository()
        item = {
            "market": "A",
            "code": "000001",
            "item_type": "news",
            "source": "fixture",
            "source_id": "news-1",
            "title": "政策利好",
            "raw_json": {"摘要": "中文内容"},
        }
        repo.upsert_items([item])
        item["raw_json"]["摘要"] = "mutated"

        rows = repo.list_items(market="A", code="000001")
        self.assertEqual(rows[0]["raw_json"]["摘要"], "中文内容")

        bundle = {
            "scope_type": "symbol",
            "market": "A",
            "code": "000001",
            "bundle_json": {"items": [{"title": "政策利好"}]},
            "source_status_json": {"fixture": "ok"},
        }
        repo.upsert_bundle(bundle)
        bundle["bundle_json"]["items"][0]["title"] = "mutated"

        stored_bundle = repo.get_bundle("symbol", "A", "000001")
        self.assertEqual(stored_bundle["bundle_json"]["items"][0]["title"], "政策利好")

        provider_run = {
            "run_id": "run-1",
            "provider": "fixture",
            "market": "A",
            "code": "000001",
            "status": "success",
            "raw_json": {"结果": "正常"},
        }
        repo.insert_provider_run(provider_run)
        provider_run["raw_json"]["结果"] = "mutated"

        runs = repo.list_provider_runs(provider="fixture", market="A", code="000001", status="success")
        self.assertEqual(runs[0]["raw_json"]["结果"], "正常")

    def test_upsert_market_intel_items_encodes_chinese_json_payload(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.upsert_market_intel_items([
            {
                "market": "A",
                "code": "000001",
                "item_type": "news",
                "source": "fixture",
                "source_id": "news-1",
                "title": "政策利好",
                "summary": "中文摘要",
                "url": "https://example.com/news/1",
                "published_at": "2026-05-25T09:30:00+08:00",
                "raw_json": {"标题": "政策利好", "摘要": "中文摘要"},
            }
        ])

        sql, values = conn.cursor_obj.executemany_calls[-1]
        self.assertIn("market_intel_items", sql)
        self.assertIn("event_time", sql)
        raw_json = values[0][-1]
        self.assertIn("政策利好", raw_json)
        self.assertEqual(json.loads(raw_json)["摘要"], "中文摘要")

    def test_upsert_market_intel_items_persists_event_time(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.upsert_market_intel_items([
            {
                "market": "US",
                "code": "AAPL",
                "item_type": "market_news",
                "source": "fixture",
                "title": "Apple event",
                "event_time": "2026-05-26T09:00:00+00:00",
                "fetched_at": "2026-05-26T10:00:00+00:00",
                "dedupe_key": "aapl-event",
            }
        ])

        sql, values = conn.cursor_obj.executemany_calls[-1]
        self.assertIn("event_time", sql)
        self.assertEqual(values[0][9], datetime(2026, 5, 26, 9, 0, 0))

    def test_upsert_screening_results_sanitizes_non_finite_values(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.upsert_screening_results(date(2026, 6, 3), [{
            "task_id": "task-1",
            "market": "HK",
            "code": "HK.00001",
            "name": "Test",
            "is_passed": True,
            "technical_score": float("inf"),
            "macro_score": float("-inf"),
            "final_score": math.nan,
            "score_details": {"raw": {"ratio": float("inf")}},
            "filter_details": [{"details": {"pe_ratio": float("inf"), "drawdown": math.nan}}],
            "market_cap": float("inf"),
            "pe_ratio": math.nan,
            "close_price": 12.3,
        }])

        _, values = conn.cursor_obj.executemany_calls[-1]
        row = values[0]
        self.assertIsNone(row[7])
        self.assertIsNone(row[8])
        self.assertIsNone(row[9])
        self.assertEqual(json.loads(row[10]), {"raw": {"ratio": None}})
        self.assertEqual(json.loads(row[11]), [{"details": {"pe_ratio": None, "drawdown": None}}])
        self.assertIsNone(row[14])
        self.assertIsNone(row[15])
        self.assertEqual(row[16], 12.3)

    def test_stock_pool_and_kline_cache_sanitize_non_finite_values(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.upsert_stock_pool("US", "industry_top5", [{
            "code": "US.TEST",
            "name": "Test",
            "market_cap": float("inf"),
            "price": math.nan,
            "pe_ratio": float("-inf"),
            "turnover": 100.0,
            "volume": float("inf"),
            "days_since_listing": math.nan,
            "rank": 1,
            "extra_data": {"raw": float("inf")},
        }])
        _, pool_values = conn.cursor_obj.executemany_calls[-1]
        pool_row = pool_values[0]
        self.assertIsNone(pool_row[4])
        self.assertIsNone(pool_row[5])
        self.assertIsNone(pool_row[6])
        self.assertEqual(pool_row[7], 100.0)
        self.assertIsNone(pool_row[8])
        self.assertIsNone(pool_row[10])
        self.assertEqual(pool_row[15], 1.0)
        self.assertEqual(json.loads(pool_row[16]), {"raw": None})

        db.upsert_kline_cache([{
            "market": "A",
            "code": "SH.600000",
            "timeframe": "1d",
            "bar_time": datetime(2026, 6, 3),
            "open": float("inf"),
            "high": 12.0,
            "low": math.nan,
            "close": float("-inf"),
            "volume": 1000,
            "turnover": math.nan,
        }])
        _, kline_values = conn.cursor_obj.executemany_calls[-1]
        kline_row = kline_values[0]
        self.assertIsNone(kline_row[4])
        self.assertEqual(kline_row[5], 12.0)
        self.assertIsNone(kline_row[6])
        self.assertIsNone(kline_row[7])
        self.assertEqual(kline_row[8], 1000.0)
        self.assertIsNone(kline_row[9])

    def test_analysis_and_main_force_scores_sanitize_non_finite_values(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        row = {
            "task_id": "task-1",
            "market": "US",
            "code": "US.TEST",
            "check_date": date(2026, 6, 3),
            "reliability_score": float("inf"),
            "confidence_score": math.nan,
            "hot_sector_relevance": float("-inf"),
            "raw_response": {"score": float("inf")},
        }
        db.upsert_signal_analysis_results([row])
        _, values = conn.cursor_obj.executemany_calls[-1]
        signal_row = values[0]
        self.assertIsNone(signal_row[7])
        self.assertIsNone(signal_row[8])
        self.assertIsNone(signal_row[22])
        self.assertEqual(json.loads(signal_row[30]), {"score": None})

        cache_row = dict(row)
        cache_row["timeframe"] = "1d"
        cache_row["analysis_profile"] = "default"
        db.upsert_signal_analysis_cache([cache_row])
        _, values = conn.cursor_obj.executemany_calls[-1]
        cache_values = values[0]
        self.assertIsNone(cache_values[8])
        self.assertIsNone(cache_values[9])
        self.assertIsNone(cache_values[23])
        self.assertEqual(json.loads(cache_values[31]), {"score": None})

        db.upsert_main_force_risk_results([{
            "task_id": "task-1",
            "market": "A",
            "code": "SH.600000",
            "check_date": date(2026, 6, 3),
            "risk_score": float("inf"),
            "metrics_json": {"net_inflow": math.nan},
        }])
        _, values = conn.cursor_obj.executemany_calls[-1]
        risk_row = values[0]
        self.assertIsNone(risk_row[8])
        self.assertEqual(json.loads(risk_row[13]), {"net_inflow": None})

    def test_list_market_intel_items_returns_and_orders_by_event_time(self):
        conn = FakeConnection()
        conn.cursor_obj.rows = [(
            "stock",
            "US",
            "AAPL",
            "fixture",
            "fixture",
            "market_news",
            "Apple event",
            "",
            "https://example.com/aapl",
            datetime(2026, 5, 26, 9, 0, 0),
            datetime(2026, 5, 25, 9, 0, 0),
            '{"source":"fixture"}',
            datetime(2026, 5, 26, 10, 0, 0),
            datetime(2026, 5, 26, 10, 15, 0),
            0,
            "aapl-event",
        )]
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        rows = db.list_market_intel_items(market="US", code="AAPL")

        sql, _ = conn.cursor_obj.executed[-1]
        self.assertIn("event_time", sql)
        self.assertIn("ORDER BY COALESCE(event_time, published_at, fetched_at) DESC", sql)
        self.assertEqual(rows[0]["event_time"], datetime(2026, 5, 26, 9, 0, 0))

    def test_in_memory_repository_sorts_by_event_time_first(self):
        from market_intel.repository import InMemoryMarketIntelRepository

        repo = InMemoryMarketIntelRepository()
        repo.upsert_items([
            {
                "market": "US",
                "code": "AAPL",
                "provider": "fixture",
                "dedupe_key": "published",
                "title": "Published fallback",
                "published_at": "2026-05-25T09:00:00+00:00",
                "fetched_at": "2026-05-25T10:00:00+00:00",
            },
            {
                "market": "US",
                "code": "AAPL",
                "provider": "fixture",
                "dedupe_key": "event",
                "title": "Event time wins",
                "event_time": "2026-05-26T09:00:00+00:00",
                "published_at": "2026-05-20T09:00:00+00:00",
                "fetched_at": "2026-05-20T10:00:00+00:00",
            },
        ])

        rows = repo.list_items(market="US", code="AAPL")

        self.assertEqual([row["title"] for row in rows], ["Event time wins", "Published fallback"])

    def test_in_memory_repository_sorts_offset_event_times_by_utc_instant(self):
        from market_intel.repository import InMemoryMarketIntelRepository

        repo = InMemoryMarketIntelRepository()
        repo.upsert_items([
            {
                "market": "US",
                "code": "AAPL",
                "provider": "fixture",
                "dedupe_key": "local-offset-earlier",
                "title": "Local offset earlier",
                "event_time": "2026-05-26T09:00:00+08:00",
            },
            {
                "market": "US",
                "code": "AAPL",
                "provider": "fixture",
                "dedupe_key": "utc-later",
                "title": "UTC later",
                "event_time": "2026-05-26T02:00:00+00:00",
            },
        ])

        rows = repo.list_items(market="US", code="AAPL")

        self.assertEqual([row["title"] for row in rows], ["UTC later", "Local offset earlier"])


if __name__ == "__main__":
    unittest.main()
