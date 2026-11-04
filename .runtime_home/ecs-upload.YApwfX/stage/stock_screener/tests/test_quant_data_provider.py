#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

import pandas as pd

from quant_lab.data_provider import CachedKlineDataProvider


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
        provider = CachedKlineDataProvider(repository=None, timeframe="1d")

        with patch("quant_lab.data_provider.managed_fetcher_chain", _chain([EmptyFetcher(), WorkingFetcher()])):
            bars = provider.bars_for("HK", "HK.01810", date(2026, 5, 1), date(2026, 5, 18))

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1].ts, date(2026, 5, 18))
        self.assertEqual(bars[-1].close, 30.66)

    def test_explicit_futu_context_is_used_without_database_cache(self):
        quote_ctx = object()
        provider = CachedKlineDataProvider(repository=None, timeframe="1d", quote_ctx=quote_ctx)

        with patch.object(provider, "_futu_fetcher", return_value=FutuFetcher()) as futu:
            bars = provider.bars_for("HK", "HK.01810", date(2026, 5, 1), date(2026, 5, 18))

        futu.assert_called_once()
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 31.5)


def _chain(fetchers):
    @contextmanager
    def context():
        yield fetchers
    return context


if __name__ == "__main__":
    unittest.main()
