#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from stock_name_resolver import StockNameInfo, StockNameProvider, StockNameResolver


class FakeNameProvider(StockNameProvider):
    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, market, codes):
        return {
            code: StockNameInfo(code=code, name=name, source="fake")
            for code, name in self.mapping.items()
        }


class StockNameResolverTest(unittest.TestCase):
    def test_a_share_prefers_chinese_name_over_english(self):
        records = [{"code": "SH.600436", "name": "Zhangzhou Pientzehuang Pharmaceutical"}]
        resolver = StockNameResolver([FakeNameProvider({"SH.600436": "片仔癀"})])

        resolver.enrich_records("A", records)

        self.assertEqual(records[0]["name"], "片仔癀")
        self.assertEqual(records[0]["name_source"], "fake")

    def test_a_share_alias_code_can_match_chinese_name(self):
        records = [{"code": "600436.SS", "name": "Zhangzhou Pientzehuang Pharmaceutical"}]
        resolver = StockNameResolver([FakeNameProvider({"SH.600436": "片仔癀"})])

        resolver.enrich_records("A", records)

        self.assertEqual(records[0]["name"], "片仔癀")

    def test_hk_share_prefers_chinese_name_over_english(self):
        records = [{"code": "HK.00700", "name": "Tencent Holdings"}]
        resolver = StockNameResolver([FakeNameProvider({"HK.00700": "腾讯控股"})])

        resolver.enrich_records("HK", records)

        self.assertEqual(records[0]["name"], "腾讯控股")

    def test_existing_chinese_name_is_not_overwritten(self):
        records = [{"code": "HK.00700", "name": "腾讯控股"}]
        resolver = StockNameResolver([FakeNameProvider({"HK.00700": "Tencent Holdings"})])

        resolver.enrich_records("HK", records)

        self.assertEqual(records[0]["name"], "腾讯控股")
        self.assertNotIn("name_source", records[0])

    def test_english_name_is_kept_when_no_chinese_name_exists(self):
        records = [{"code": "HK.00001", "name": "CK Hutchison"}]
        resolver = StockNameResolver([FakeNameProvider({"HK.00001": "CK Hutchison Holdings"})])

        resolver.enrich_records("HK", records)

        self.assertEqual(records[0]["name"], "CK Hutchison")

    def test_us_market_is_not_changed(self):
        records = [{"code": "US.AAPL", "name": "Apple Inc"}]
        resolver = StockNameResolver([FakeNameProvider({"US.AAPL": "苹果"})])

        resolver.enrich_records("US", records)

        self.assertEqual(records[0]["name"], "Apple Inc")


if __name__ == "__main__":
    unittest.main()
