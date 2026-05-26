import json
import unittest
from datetime import datetime
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
