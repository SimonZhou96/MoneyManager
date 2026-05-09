#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import unittest
from unittest.mock import patch

import scheduled_daily_job as job
from sector_resolver import (
    SectorInfo,
    SectorProvider,
    SectorResolver,
    StockPoolSectorProvider,
    _normalize_a_code,
    _to_yahoo_symbol,
)


class FakePoolDb:
    def __init__(self):
        self.pools = {
            ("HK", "best"): [
                {"code": "HK.00001", "name": "Best Name", "market_cap": 100, "pe_ratio": 10},
                {"code": "HK.02800", "name": "ETF Name", "market_cap": 200, "pe_ratio": 20},
            ],
            ("HK", "industry"): [
                {
                    "code": "HK.00001",
                    "name": "Industry Name",
                    "industry_name": "Finance",
                    "industry_code": "BK001",
                }
            ],
            ("HK", "etf"): [
                {"code": "HK.02800", "name": "ETF Name"},
            ],
        }

    def get_stock_pool(self, market, pool_type, limit=None):
        return list(self.pools.get((market, pool_type), []))

    def get_stocks_by_codes(self, market, codes, include_fundamentals=True):
        return []

    def get_sector_memberships_by_codes(self, market, codes):
        return {}


class StaticProvider(SectorProvider):
    name = "static"

    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, market, codes):
        return {code: self.mapping[code] for code in codes if code in self.mapping}


class ExplodingProvider(SectorProvider):
    name = "boom"

    def resolve(self, market, codes):
        raise RuntimeError("boom")


class SectorResolverTest(unittest.TestCase):
    def test_stock_pool_provider_resolves_industry_and_etf(self):
        provider = StockPoolSectorProvider(FakePoolDb())

        result = provider.resolve("HK", ["HK.00001", "HK.02800"])

        self.assertEqual(result["HK.00001"].sector, "Finance")
        self.assertEqual(result["HK.00001"].industry, "Finance")
        self.assertEqual(result["HK.02800"].sector, "ETF")
        self.assertEqual(result["HK.02800"].industry, "ETF")

    def test_resolver_preserves_existing_fields_and_continues_after_provider_failure(self):
        resolver = SectorResolver([
            StaticProvider({"HK.00001": SectorInfo(code="HK.00001", sector="Finance", source="first")}),
            ExplodingProvider(),
            StaticProvider({"HK.00001": SectorInfo(code="HK.00001", sector="Tech", industry="Banking", source="second")}),
        ])

        result = resolver.resolve("HK", ["HK.00001"])

        self.assertEqual(result["HK.00001"].sector, "Finance")
        self.assertEqual(result["HK.00001"].industry, "Banking")
        self.assertIn("first", result["HK.00001"].source)
        self.assertIn("second", result["HK.00001"].source)

    def test_get_merged_pool_stocks_backfills_later_pool_industry(self):
        with patch.dict(os.environ, {}, clear=True):
            records = job.get_merged_pool_stocks(FakePoolDb(), "HK")

        by_code = {item["code"]: item for item in records}
        self.assertEqual(by_code["HK.00001"]["name"], "Best Name")
        self.assertEqual(by_code["HK.00001"]["sector"], "Finance")
        self.assertEqual(by_code["HK.00001"]["industry"], "Finance")
        self.assertEqual(by_code["HK.02800"]["sector"], "ETF")
        self.assertEqual(by_code["HK.02800"]["industry"], "ETF")

    def test_hk_yahoo_symbol_uses_four_digit_code(self):
        self.assertEqual(_to_yahoo_symbol("HK", "HK.00006"), "0006.HK")
        self.assertEqual(_to_yahoo_symbol("HK", "HK.02460"), "2460.HK")

    def test_a_share_symbol_normalization_supports_prefix_and_suffix(self):
        self.assertEqual(_normalize_a_code("SH.601339"), "601339")
        self.assertEqual(_normalize_a_code("SZ.300750"), "300750")
        self.assertEqual(_normalize_a_code("601339.SH"), "601339")
        self.assertEqual(_to_yahoo_symbol("A", "SH.601339"), "601339.SS")


if __name__ == "__main__":
    unittest.main()
