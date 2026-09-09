import unittest
import time
from unittest.mock import patch
from pathlib import Path

import pandas as pd

import akshare as ak
from stock_pool import CANONICAL_POOL_TYPES, StockPoolFetcher


STOCK_SCREENER_DIR = Path(__file__).resolve().parents[1]


class FakeIndexConstituentDb:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot or []
        self.saved = []

    def upsert_stock_index_constituents(self, market, rows, source="online"):
        self.saved.append((market, rows, source))

    def get_stock_index_constituents(self, market, index_codes=None):
        return list(self.snapshot)


class FakeUSIndexProvider:
    def __init__(self, source, rows):
        self.source = source
        self.rows = rows
        self.called = False

    def fetch(self, definitions):
        self.called = True
        return list(self.rows)


class FakeIndustryQuoteCtx:
    def __init__(self):
        self.plate_stock_calls = 0

    def get_plate_list(self, market, plate):
        return 0, pd.DataFrame([{
            "code": "US.LIST20077",
            "plate_name": "Semiconductors",
            "plate_id": "LIST20077",
        }])

    def get_plate_stock(self, plate_code):
        self.plate_stock_calls += 1
        if self.plate_stock_calls == 1:
            return -1, "Get Stock List within a Sector is too frequent，request failed, no more than 10 times every 30 seconds."
        return 0, pd.DataFrame([
            {"code": "US.NVDA", "stock_name": "NVIDIA"},
            {"code": "US.AMD", "stock_name": "AMD"},
        ])

    def get_market_snapshot(self, codes):
        return 0, pd.DataFrame([
            {"code": "US.AMD", "name": "AMD", "total_market_val": 200.0, "last_price": 10.0},
            {"code": "US.NVDA", "name": "NVIDIA", "total_market_val": 500.0, "last_price": 20.0},
        ])


class StockPoolTypesTest(unittest.TestCase):
    def test_canonical_pool_types_replace_legacy_pool_names(self):
        self.assertEqual(
            CANONICAL_POOL_TYPES,
            ("best", "major_index", "industry_top5", "recent_ipo_2y", "all_etf"),
        )
        for legacy in ("index", "industry", "ipo", "etf"):
            self.assertNotIn(legacy, CANONICAL_POOL_TYPES)

    def test_a_share_major_index_constituents_are_normalized_from_akshare(self):
        def fake_cons(symbol):
            if symbol != "000300":
                return pd.DataFrame()
            return pd.DataFrame([
                {"成分券代码": "600519", "成分券名称": "贵州茅台"},
                {"成分券代码": "000001", "成分券名称": "平安银行"},
            ])

        with patch.object(ak, "index_stock_cons_csindex", side_effect=fake_cons), \
                patch.object(ak, "index_stock_cons_sina", return_value=pd.DataFrame()), \
                patch.object(ak, "index_stock_cons", return_value=pd.DataFrame()):
            rows = StockPoolFetcher().fetch_major_index_constituents("A", index_codes=[])

        by_code = {item["code"]: item for item in rows}
        self.assertEqual(by_code["SH.600519"]["name"], "贵州茅台")
        self.assertEqual(by_code["SZ.000001"]["name"], "平安银行")
        self.assertEqual(by_code["SH.600519"]["index_name"], "沪深300")

    def test_a_share_major_index_constituents_fallback_to_db_snapshot_when_akshare_times_out(self):
        def slow_cons(symbol):
            time.sleep(0.2)
            return pd.DataFrame()

        db = FakeIndexConstituentDb(snapshot=[{
            "code": "SH.600519",
            "name": "贵州茅台",
            "index_code": "000300",
            "index_name": "沪深300",
        }])
        with patch.dict("os.environ", {"STOCK_POOL_A_INDEX_FETCH_TIMEOUT_SEC": "0.1"}), \
                patch.object(ak, "index_stock_cons_csindex", side_effect=slow_cons), \
                patch.object(ak, "index_stock_cons_sina", return_value=pd.DataFrame()), \
                patch.object(ak, "index_stock_cons", return_value=pd.DataFrame()):
            rows = StockPoolFetcher(db=db).fetch_major_index_constituents("A", index_codes=["000300"])

        self.assertEqual(rows[0]["code"], "SH.600519")
        self.assertEqual(rows[0]["index_name"], "沪深300")

    def test_us_major_index_constituents_use_funda_provider_first(self):
        funda = FakeUSIndexProvider("funda", [
            {
                "code": "US.BRK-B",
                "name": "Berkshire Hathaway",
                "index_code": "sp500",
                "index_name": "标普500",
                "extra_data": {"source": "funda"},
            },
            {
                "code": "US.AAPL",
                "name": "Apple",
                "index_code": "sp500",
                "index_name": "标普500",
                "extra_data": {"source": "funda"},
            },
        ])
        yfinance = FakeUSIndexProvider("yfinance", [
            {"code": "US.MSFT", "name": "Microsoft", "index_code": "sp500", "index_name": "标普500"},
        ])
        with patch.object(StockPoolFetcher, "_us_major_index_providers", return_value=[funda, yfinance]):
            rows = StockPoolFetcher().fetch_major_index_constituents("US", index_codes=["sp500"])

        by_code = {item["code"]: item for item in rows}
        self.assertEqual(by_code["US.BRK-B"]["name"], "Berkshire Hathaway")
        self.assertEqual(by_code["US.AAPL"]["index_name"], "标普500")
        self.assertTrue(funda.called)
        self.assertFalse(yfinance.called)

    def test_us_major_index_constituents_save_provider_snapshot_to_db(self):
        provider = FakeUSIndexProvider("funda", [
            {
                "code": "US.AAPL",
                "name": "Apple",
                "index_code": "sp500",
                "index_name": "标普500",
                "extra_data": {"source": "funda"},
            },
        ])
        db = FakeIndexConstituentDb()
        with patch.object(StockPoolFetcher, "_us_major_index_providers", return_value=[provider]):
            rows = StockPoolFetcher(db=db).fetch_major_index_constituents("US", index_codes=[])

        self.assertEqual(rows[0]["code"], "US.AAPL")
        self.assertEqual(db.saved[0][0], "US")
        self.assertEqual(db.saved[0][2], "funda")

    def test_us_major_index_constituents_fallback_to_db_snapshot_when_online_fails(self):
        db_provider = FakeUSIndexProvider("db_snapshot", [
            {
                "code": "US.MSFT",
                "name": "Microsoft",
                "index_code": "sp500",
                "index_name": "标普500",
                "extra_data": {"source": "db_snapshot"},
            },
        ])
        with patch.object(StockPoolFetcher, "_us_major_index_providers", return_value=[
            FakeUSIndexProvider("funda", []),
            FakeUSIndexProvider("yfinance", []),
            db_provider,
        ]):
            rows = StockPoolFetcher().fetch_major_index_constituents("US", index_codes=["sp500"])

        self.assertEqual(rows, [
            {
                "code": "US.MSFT",
                "name": "Microsoft",
                "index_code": "sp500",
                "index_name": "标普500",
                "extra_data": {"source": "db_snapshot"},
            },
        ])

    def test_industry_leaders_retry_futu_plate_stock_rate_limit(self):
        quote_ctx = FakeIndustryQuoteCtx()
        with patch.dict("os.environ", {"STOCK_POOL_PLATE_STOCK_RETRY_SEC": "0"}), \
                patch("time.sleep") as sleep_mock:
            rows = StockPoolFetcher(quote_ctx=quote_ctx).fetch_industry_leaders("US", top_n=1)

        self.assertEqual(quote_ctx.plate_stock_calls, 2)
        sleep_mock.assert_not_called()
        self.assertEqual(rows, [{
            "code": "US.NVDA",
            "name": "NVIDIA",
            "industry_code": "US.LIST20077",
            "industry_name": "Semiconductors",
            "rank": 1,
            "market_cap": 500.0,
            "price": 20.0,
        }])

    def test_frontend_screening_form_exposes_pool_type_selection(self):
        source = (STOCK_SCREENER_DIR / "web_frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

        self.assertIn("POOL_OPTIONS", source)
        for pool_type in CANONICAL_POOL_TYPES:
            self.assertIn(pool_type, source)
        self.assertIn("pool_types", source)

    def test_non_interactive_screening_shell_passes_pool_selection(self):
        source = (STOCK_SCREENER_DIR / "scripts" / "run_screening.sh").read_text(encoding="utf-8")

        self.assertIn("POOLS=", source)
        self.assertIn('"--pools" "$POOLS"', source)


if __name__ == "__main__":
    unittest.main()
