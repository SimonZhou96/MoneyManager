import unittest

from db import hash_password, verify_password
from kline_fetcher import DatabaseKlineFetcher, KlineFetcherFactory
from web.single_stock import _load_stock_info, normalize_stock_code
from web.validation import validate_markets, validate_timeframe


class WebPlatformTests(unittest.TestCase):
    def test_password_hash_round_trip(self):
        hashed = hash_password("secret-password")
        self.assertTrue(verify_password("secret-password", hashed))
        self.assertFalse(verify_password("wrong", hashed))
        self.assertFalse(verify_password("secret-password", "invalid"))

    def test_normalize_stock_code_requires_market_context(self):
        self.assertEqual(normalize_stock_code("HK", "700"), "HK.00700")
        self.assertEqual(normalize_stock_code("HK", "HK.00700"), "HK.00700")
        self.assertEqual(normalize_stock_code("US", "AAPL"), "US.AAPL")
        self.assertEqual(normalize_stock_code("US", "US.MSFT"), "US.MSFT")
        self.assertEqual(normalize_stock_code("A", "600519"), "SH.600519")
        self.assertEqual(normalize_stock_code("A", "000001"), "SZ.000001")
        self.assertEqual(normalize_stock_code("A", "688001"), "SH.688001")

    def test_fetcher_chain_puts_database_cache_first_when_db_is_provided(self):
        class FakeDB:
            pass

        fetchers = KlineFetcherFactory.create_fetcher_chain(db=FakeDB())
        self.assertGreater(len(fetchers), 0)
        self.assertIsInstance(fetchers[0], DatabaseKlineFetcher)

    def test_validate_screening_task_write_inputs(self):
        self.assertEqual(validate_markets(["hk", "US", "HK"]), ["HK", "US"])
        self.assertEqual(validate_timeframe("1d"), "1d")
        with self.assertRaisesRegex(ValueError, "至少选择一个市场"):
            validate_markets([])
        with self.assertRaisesRegex(ValueError, "不支持的市场"):
            validate_markets(["CN"])
        with self.assertRaisesRegex(ValueError, "不支持的周期"):
            validate_timeframe("2d")

    def test_single_stock_info_enriches_from_pool_and_sector_membership(self):
        class FakeDB:
            def get_stocks_by_codes(self, market, codes, include_fundamentals=True):
                return []

            def get_stock_pool_records_by_codes(self, market, codes):
                return [{
                    "pool_type": "best",
                    "code": codes[0],
                    "name": "Pool Name",
                    "market_cap": 123.4,
                    "pe_ratio": 18.5,
                    "industry_name": "Biotechnology",
                }]

            def get_sector_memberships_by_codes(self, market, codes):
                return {
                    codes[0]: [
                        {"sector_type": "sector", "sector_name": "Health Care"},
                        {"sector_type": "industry", "sector_name": "Pharmaceuticals"},
                    ]
                }

        stock = _load_stock_info(FakeDB(), "US", "US.TEST")

        self.assertEqual(stock.name, "Pool Name")
        self.assertEqual(stock.market_cap, 123.4)
        self.assertEqual(stock.pe_ratio, 18.5)
        self.assertEqual(stock.sector, "Biotechnology")
        self.assertEqual(stock.industry, "Biotechnology")


if __name__ == "__main__":
    unittest.main()
