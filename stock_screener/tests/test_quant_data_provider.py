#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from quant_lab.data_provider import CachedKlineDataProvider


class EmptyRepository:
    def get_kline_cache(self, market, code, timeframe, max_count=500):
        return pd.DataFrame()


class EmptyFetcher:
    def get_name(self):
        return "empty"

    def fetch(self, stock_code, market="HK", timeframe="1d", max_count=2000):
        return pd.DataFrame()


class WorkingFetcher:
    def get_name(self):
        return "working"

    def fetch(self, stock_code, market="HK", timeframe="1d", max_count=2000):
        return pd.DataFrame(
            [
                {"date": "2026-05-15", "open": 31.6, "high": 31.72, "low": 30.46, "close": 30.7, "volume": 152641980},
                {"date": "2026-05-18", "open": 31.0, "high": 31.06, "low": 30.14, "close": 30.66, "volume": 116608436},
            ]
        )


class CacheRepository:
    def get_kline_cache(self, market, code, timeframe, max_count=500):
        return pd.DataFrame(
            [
                {"date": "2026-05-18", "open": 20, "high": 21, "low": 19, "close": 20.5, "volume": 10},
            ]
        )


class FutuFetcher:
    def get_name(self):
        return "FutuOpenAPI"

    def fetch(self, stock_code, market="HK", timeframe="1d", max_count=2000):
        return pd.DataFrame(
            [
                {"date": "2026-05-18", "open": 31, "high": 32, "low": 30, "close": 31.5, "volume": 100},
            ]
        )


class CachedKlineDataProviderTest(unittest.TestCase):
    def test_network_fallback_uses_fetcher_chain_until_one_returns_data(self):
        provider = CachedKlineDataProvider(repository=EmptyRepository(), timeframe="1d")

        with patch("quant_lab.data_provider.KlineFetcherFactory.create_fetcher_chain", return_value=[EmptyFetcher(), WorkingFetcher()]):
            bars = provider.bars_for("HK", "HK.01810", date(2026, 5, 1), date(2026, 5, 18))

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1].ts, date(2026, 5, 18))
        self.assertEqual(bars[-1].close, 30.66)

    def test_database_cache_is_used_before_futu_quote_context(self):
        quote_ctx = object()
        provider = CachedKlineDataProvider(repository=CacheRepository(), timeframe="1d", quote_ctx=quote_ctx)

        with patch("quant_lab.data_provider.KlineFetcherFactory.create_fetcher_chain", return_value=[FutuFetcher()]) as factory:
            bars = provider.bars_for("HK", "HK.01810", date(2026, 5, 1), date(2026, 5, 18))

        factory.assert_not_called()
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 20.5)

    def test_empty_database_cache_falls_back_to_futu_before_public_sources(self):
        quote_ctx = object()
        provider = CachedKlineDataProvider(repository=EmptyRepository(), timeframe="1d", quote_ctx=quote_ctx)

        with patch("quant_lab.data_provider.KlineFetcherFactory.create_fetcher_chain", return_value=[FutuFetcher()]) as factory:
            bars = provider.bars_for("HK", "HK.01810", date(2026, 5, 1), date(2026, 5, 18))

        factory.assert_called_with(quote_ctx=quote_ctx)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 31.5)


if __name__ == "__main__":
    unittest.main()
