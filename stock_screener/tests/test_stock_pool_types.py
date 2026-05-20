import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

import akshare as ak
import local_agent
from stock_pool import CANONICAL_POOL_TYPES, StockPoolFetcher


STOCK_SCREENER_DIR = Path(__file__).resolve().parents[1]


class StockPoolTypesTest(unittest.TestCase):
    def test_canonical_pool_types_replace_legacy_pool_names(self):
        self.assertEqual(
            CANONICAL_POOL_TYPES,
            ("best", "major_index", "industry_top5", "recent_ipo_2y", "all_etf"),
        )
        for legacy in ("index", "industry", "ipo", "etf"):
            self.assertNotIn(legacy, CANONICAL_POOL_TYPES)

    def test_local_agent_uploads_and_collects_new_pool_keys(self):
        self.assertEqual(
            local_agent.POOL_MAP,
            {
                "best": "best_stocks",
                "major_index": "major_index_constituents",
                "industry_top5": "industry_top5",
                "recent_ipo_2y": "recent_ipo_2y",
                "all_etf": "all_etf",
            },
        )

        codes = local_agent.collect_codes_from_pools({
            "best_stocks": [{"code": "HK.00001"}],
            "major_index_constituents": [{"code": "HK.00700"}],
            "industry_top5": [{"code": "HK.00941"}],
            "recent_ipo_2y": [{"code": "HK.09880"}],
            "all_etf": [{"code": "HK.02800"}],
            "index_constituents": [{"code": "HK.LEGACY"}],
        })

        self.assertEqual(codes, ["HK.00001", "HK.00700", "HK.00941", "HK.09880", "HK.02800"])

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

    def test_us_major_index_constituents_are_normalized_from_public_tables(self):
        table = pd.DataFrame([
            {"Symbol": "BRK.B", "Security": "Berkshire Hathaway"},
            {"Symbol": "AAPL", "Security": "Apple"},
        ])
        with patch("stock_pool.pd.read_html", return_value=[table]):
            rows = StockPoolFetcher().fetch_major_index_constituents("US", index_codes=[])

        by_code = {item["code"]: item for item in rows}
        self.assertEqual(by_code["US.BRK-B"]["name"], "Berkshire Hathaway")
        self.assertEqual(by_code["US.AAPL"]["index_name"], "标普500")

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
