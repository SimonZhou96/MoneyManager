"""Opt-in MySQL regressions; only an isolated temporary database is modified.

STOCK_CATALOG_TEST_MYSQL=1 .venv/bin/python -m unittest discover \
  -s tests -p test_stock_catalog_mysql.py -v
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from decimal import Decimal
import uuid
import json
import statistics
import time
from datetime import date

from db import MarketDatabase, STOCK_CATALOG_FIELDS, STOCK_POOL_CATALOG_COLUMNS
from scripts.migrate_stock_catalog import migrate
from web.config import load_dotenv, mysql_config_from_env


@unittest.skipUnless(os.getenv("STOCK_CATALOG_TEST_MYSQL") == "1", "requires opt-in local MySQL")
class StockCatalogMySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv()
        cls.admin = MarketDatabase(mysql_config_from_env())
        cls.database = "mm_catalog_test_" + uuid.uuid4().hex[:12]
        with cls.admin.conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{cls.database}` CHARACTER SET utf8mb4")
        try:
            cls.db = MarketDatabase(replace(mysql_config_from_env(), database=cls.database))
            cls.db.init_stock_pool_schema()
        except Exception:
            with cls.admin.conn.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{cls.database}`")
            cls.admin.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        with cls.admin.conn.cursor() as cursor:
            cursor.execute(f"DROP DATABASE `{cls.database}`")
        cls.admin.close()

    def setUp(self):
        with self.db.conn.cursor() as cursor:
            cursor.execute("TRUNCATE stock_pools")
            cursor.execute("DROP TABLE IF EXISTS stocks")
            cursor.execute("SHOW TABLES LIKE 'stock_catalog_sync_runs'")
            if cursor.fetchone():
                cursor.execute("TRUNCATE stock_catalog_sync_runs")

    def repository(self, limit=200):
        from worker.repository import Repository
        repo = Repository(self.db, date(2026, 11, 4), limit)
        self.assertTrue(repo.start())
        self.addCleanup(repo.close)
        return repo

    def test_daily_sync_preserves_memberships_and_fundamentals_and_searches_aliases(self):
        self.seed_memberships()
        repo = self.repository()
        records = [{"market": "HK", "code": "HK.00700", "name_zh": "騰訊控股", "name_en": "TENCENT",
                    "instrument_type": "股票", "exchange": "HKEX"}]
        first = repo.apply("HK", "hk", records)
        self.assertEqual(first["inserted"], 1)
        second = repo.apply("HK", "hk", records)
        self.assertEqual((second["inserted"], second["updated"]), (0, 0))
        self.assertEqual(len(self.db.get_stock_pool("HK", "best")), 1)
        self.assertEqual(self.db.get_stocks_by_codes("HK", ["HK.00700"])[0]["market_cap"], 100)
        for query in ("腾讯", "騰訊", "TENCENT", "HK.00700"):
            self.assertEqual(self.db.search_stocks_by_name("HK", query)[0]["name"], "腾讯控股")
        self.db.upsert_stocks("HK", [{"code": "HK.00700", "name": "TENCENT"}])
        self.db.upsert_stock_pool("HK", "recent_ipo_2y", [{"code": "HK.00700", "name": "TENCENT"}])
        self.assertEqual(self.db.get_stock_pool("HK", "recent_ipo_2y")[0]["name"], "腾讯控股")
        self.assertEqual(self.db.search_stocks_by_name("HK", "腾讯")[0]["name"], "腾讯控股")

    def test_empty_and_shrunken_sources_do_not_destroy_existing_rows(self):
        repo = self.repository()
        with self.db.conn.cursor() as cursor:
            cursor.execute("INSERT INTO stock_catalog_sync_runs (run_date,status,sources_json) VALUES ('2026-11-03','success',%s)",
                           (json.dumps({"hk": {"status": "success", "seen": 100}}),))
        record = {"market": "HK", "code": "HK.00700", "name_en": "TENCENT", "instrument_type": "股票"}
        with self.assertRaisesRegex(ValueError, "Empty"):
            repo.apply("HK", "hk", [])
        with self.assertRaisesRegex(ValueError, ">20%"):
            repo.apply("HK", "hk", [record])
        self.assertEqual(self.db.stock_count("HK"), 0)

    def test_daily_budget_is_durable_and_other_connections_cannot_run(self):
        from worker.repository import Repository
        from worker.transport import BudgetExceeded
        repo = self.repository(limit=2)
        repo.phase = "hk"
        repo.bump_attempt()
        repo.bump_attempt()
        repo.finish()
        repo.close()
        resumed = Repository(self.db, date(2026, 11, 4), 2)
        self.assertFalse(resumed.start())
        resumed.close()
        self.assertTrue(resumed.start(force=True))
        self.addCleanup(resumed.close)
        with self.assertRaises(BudgetExceeded):
            resumed.bump_attempt()
        other = MarketDatabase(replace(mysql_config_from_env(), database=self.database))
        self.addCleanup(other.close)
        concurrent = Repository(other, date(2026, 11, 4))
        self.assertFalse(concurrent.start())

    def test_failure_of_one_source_does_not_block_the_next(self):
        from worker.providers import Snapshot
        from worker.stock_catalog import sync, Settings
        class FakeProviders:
            def __init__(self, transport):
                pass
            def fetch(self, phase):
                if phase == "hk":
                    raise TimeoutError("provider unavailable")
                return Snapshot([{"market": "US", "code": "US.AAPL", "name_en": "Apple Inc.", "instrument_type": "股票"}])
        with tempfile.TemporaryDirectory() as temp, patch("worker.stock_catalog.PHASES", (("hk", "HK"), ("us", "US"))):
            result = sync(self.db, Settings(cache_dir=Path(temp)), run_date=date(2026, 11, 4), providers_factory=FakeProviders)
        self.assertEqual(result, "partial")
        self.assertEqual(self.db.search_stocks_by_name("US", "Apple")[0]["code"], "US.AAPL")

    def test_catalog_types_override_legacy_etf_pool(self):
        from scheduled_daily_job import get_etf_codes
        self.db.upsert_stock_pool("US", "all_etf", [{"code": "US.AAPL", "name": "Apple"}])
        repo = self.repository()
        repo.apply("US", "us", [
            {"market": "US", "code": "US.AAPL", "name_en": "Apple", "instrument_type": "股票"},
            {"market": "US", "code": "US.SPY", "name_en": "SPDR S&P 500 ETF", "instrument_type": "ETF"}])
        self.assertEqual(get_etf_codes(self.db, "US"), {"US.SPY"})

    def test_force_refetches_invalid_source_instead_of_reusing_a_bad_cache(self):
        from worker.providers import Snapshot
        from worker.stock_catalog import sync, Settings
        class FakeProviders:
            calls = 0
            def __init__(self, transport):
                pass
            def fetch(self, phase):
                FakeProviders.calls += 1
                if FakeProviders.calls == 1:
                    return Snapshot([])
                return Snapshot([{"market": "US", "code": "US.AAPL", "name_en": "Apple Inc.", "instrument_type": "股票"}])
        with tempfile.TemporaryDirectory() as temp, patch("worker.stock_catalog.PHASES", (("us", "US"),)):
            settings = Settings(cache_dir=Path(temp))
            self.assertEqual(sync(self.db, settings, run_date=date(2026, 11, 4), providers_factory=FakeProviders), "failed")
            self.assertEqual(sync(self.db, settings, force=True, run_date=date(2026, 11, 4), providers_factory=FakeProviders), "success")
        self.assertEqual(FakeProviders.calls, 2)

    def test_30000_row_catalog_batch_merge_and_search_latency(self):
        repo = self.repository()
        records = [{"market": "US", "code": f"US.T{i:05d}", "name_en": f"Company {i:05d}", "instrument_type": "股票"} for i in range(30000)]
        started = time.perf_counter()
        result = repo.apply("US", "benchmark", records)
        write_seconds = time.perf_counter() - started
        self.assertEqual(result["seen"], 30000)
        self.assertEqual(self.db.stock_count("US"), 30000)
        timings = []
        for i in range(30):
            started = time.perf_counter()
            rows = self.db.search_stocks_by_name("US", f"Company {i:05d}")
            timings.append(time.perf_counter() - started)
            self.assertEqual(rows[0]["code"], f"US.T{i:05d}")
        p95 = statistics.quantiles(timings, n=100)[94]
        print(f"Catalog benchmark: 30000 rows, write={write_seconds:.3f}s, search p95={p95:.3f}s")
        self.assertLess(p95, 1.0)

    def seed_memberships(self):
        self.db.upsert_stock_pool("HK", "best", [{"code": "HK.00700", "name": "TENCENT", "market_cap": 100, "industry_name": "Internet"}])
        self.db.upsert_stock_pool("HK", "major_index", [{"code": "HK.00700", "name": "TENCENT", "index_code": "HSI"}])

    def test_duplicates_do_not_consume_search_limit_and_gaps_are_merged(self):
        self.seed_memberships()
        self.db.upsert_stock_pool("HK", "best", [{"code": "HK.00001", "name": "TENCENT Supplier", "market_cap": 10}])
        self.assertEqual(self.db.stock_count("HK"), 2)
        rows = self.db.search_stocks_by_name("HK", "TENCENT", limit=2)
        self.assertEqual([row["code"] for row in rows], ["HK.00700", "HK.00001"])
        rows = self.db.get_stocks_by_codes("HK", ["HK.00700"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["industry"], "Internet")
        self.assertEqual(rows[0]["market_cap"], 100)
        self.assertEqual(self.db.search_stocks_by_name("US", "TENCENT"), [])

    def test_enrichment_updates_memberships_without_losing_pool_fields(self):
        self.seed_memberships()
        self.db.upsert_stocks("HK", [{"code": "HK.00700", "name": "腾讯控股", "pe_ratio": 0, "pb_ratio": 0}])
        self.db.update_stock_sector("HK", "HK.00700", sector="Technology", industry="Internet")
        self.db.upsert_stocks("HK", [{"code": "HK.00700", "name": "", "sector": None}])
        result = self.db.get_stocks_by_codes("HK", ["HK.00700"])[0]
        self.assertEqual((result["name"], result["sector"], result["pe_ratio"], result["pb_ratio"]), ("腾讯控股", "Technology", 0, 0))
        with self.db.conn.cursor() as cursor:
            cursor.execute("SELECT pool_type, name, index_code FROM stock_pools ORDER BY pool_type")
            self.assertEqual(cursor.fetchall(), (("best", "腾讯控股", None), ("major_index", "腾讯控股", "HSI")))

    def test_unpooled_stock_is_searchable_without_becoming_a_screening_member(self):
        self.db.upsert_stocks("US", [{"code": "US.NEW", "name": "New Company", "market_cap": 0}])
        self.db.upsert_stocks("US", [{"code": "US.NEW", "currency": "USD"}])
        self.assertEqual(self.db.stock_count("US"), 1)
        self.assertEqual(self.db.search_stocks_by_name("US", "New")[0]["market_cap"], 0)
        self.assertEqual(self.db.get_stock_pool("US", "best"), [])
        self.assertEqual(len(self.db.get_stock_pool("US", "catalog")), 1)

    def test_initialization_is_idempotent_and_never_creates_stocks(self):
        self.db.init_schema()
        self.db.init_schema()
        self.db.migrate_add_sector_columns()
        with self.db.conn.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'stocks'")
            self.assertIsNone(cursor.fetchone())

    def test_existing_pool_schema_is_upgraded_without_losing_memberships(self):
        self.seed_memberships()
        with self.db.conn.cursor() as cursor:
            changes = [f"DROP COLUMN `{field}`" for field in STOCK_POOL_CATALOG_COLUMNS]
            cursor.execute("ALTER TABLE stock_pools " + ", ".join(changes) + ", DROP INDEX idx_pool_market_code")
        self.db.init_stock_pool_schema()
        self.assertEqual(self.db.stock_count("HK"), 1)
        self.assertEqual(self.db.get_stocks("HK")[0]["industry"], "Internet")

    def test_sector_join_returns_one_security_with_multiple_memberships(self):
        from web.sectors import _query_sector_stocks
        self.seed_memberships()
        self.db.init_sector_schema()
        self.db.upsert_stock_sector_memberships([{"market": "HK", "code": "HK.00700",
                                                  "sector_name": "Internet"}])
        with patch("web.sectors._fetch_daily_bars", return_value={}) as fetch:
            rows = _query_sector_stocks(self.db, "HK", "Internet", 10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "TENCENT")
        fetch.assert_called_once_with("HK", {"HK.00700"}, max_count=2)

    def create_legacy_stock(self):
        # Minimal legacy schema with every field used by the migration.
        definitions = {"name": "VARCHAR(255)", "listing_date": "DATE", "industry_code": "VARCHAR(64)",
                       "market_cap": "DECIMAL(28,2)", "pe_ratio": "DECIMAL(20,6)", **STOCK_POOL_CATALOG_COLUMNS}
        columns = ", ".join(f"`{field}` {definitions[field]}" for field in STOCK_CATALOG_FIELDS)
        with self.db.conn.cursor() as cursor:
            cursor.execute(f"CREATE TABLE stocks (market VARCHAR(8), code VARCHAR(32), {columns})")
            cursor.execute("""INSERT INTO stocks (market, code, name, exchange, currency, lot_size, status,
                           listing_date, delisting_date, sector, sector_code, industry, industry_code,
                           market_cap, pe_ratio, pb_ratio, source)
                           VALUES ('HK','HK.00700','腾讯控股','HKEX','HKD',100,'ACTIVE',
                           '2004-06-16',NULL,'Technology','T','Internet','I',123.45,0,0,'manual')""")

    def test_migration_preserves_all_fields_before_drop_and_can_be_rerun(self):
        self.seed_memberships()
        self.create_legacy_stock()
        with tempfile.TemporaryDirectory() as temp:
            backup = Path(temp) / "stocks.sql"
            self.assertEqual(migrate(self.db, backup, drop_legacy=True), 1)
            self.assertIn("腾讯控股", backup.read_text())
            self.assertIn("CREATE TABLE", backup.read_text())
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(migrate(self.db, backup, drop_legacy=True), 0)
        with self.db.conn.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'stocks'")
            self.assertIsNone(cursor.fetchone())
            cursor.execute("SELECT exchange,currency,lot_size,pb_ratio FROM stock_pools")
            self.assertEqual(cursor.fetchall(), (("HKEX", "HKD", 100, Decimal(0)),) * 2)

    def test_failed_verification_retains_legacy_and_backup(self):
        self.create_legacy_stock()
        with tempfile.TemporaryDirectory() as temp, patch.object(self.db, "upsert_stocks"):
            backup = Path(temp) / "stocks.sql"
            with self.assertRaisesRegex(RuntimeError, "Missing migrated security"):
                migrate(self.db, backup, drop_legacy=True)
            self.assertTrue(backup.exists())
        with self.db.conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM stocks")
            self.assertEqual(cursor.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
