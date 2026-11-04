import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kline_fetcher


class _FallbackFetcher:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def get_name(self):
        return self.name

    def close(self):
        self.closed = True


class OpenDFetcherChainTests(unittest.TestCase):
    def _fallbacks(self):
        return (
            mock.patch.object(kline_fetcher, "YFinanceKlineFetcher", lambda: _FallbackFetcher("YFinance")),
            mock.patch.object(kline_fetcher, "AKShareKlineFetcher", lambda: _FallbackFetcher("AKShare")),
        )

    def test_disabled_opend_never_constructs_connection(self):
        opener = mock.Mock()
        futu = types.SimpleNamespace(OpenQuoteContext=opener, RET_OK=0)
        yf_patch, ak_patch = self._fallbacks()
        with yf_patch, ak_patch, mock.patch.dict(sys.modules, {"futu": futu}), mock.patch.dict(
            os.environ, {"KLINE_USE_FUTU_OPEND": "0"}, clear=False
        ):
            with kline_fetcher.managed_fetcher_chain() as fetchers:
                self.assertEqual([item.get_name() for item in fetchers], ["YFinance", "AKShare"])

        opener.assert_not_called()

    def test_ready_opend_is_first_source_and_is_closed(self):
        class QuoteContext:
            instance = None

            def __init__(self, host, port):
                self.host = host
                self.port = port
                self.closed = False
                QuoteContext.instance = self

            def get_global_state(self):
                return 0, {"program_status_type": "READY", "qot_logined": True}

            def close(self):
                self.closed = True

        futu = types.SimpleNamespace(OpenQuoteContext=QuoteContext, RET_OK=0)
        yf_patch, ak_patch = self._fallbacks()
        with yf_patch, ak_patch, mock.patch.dict(sys.modules, {"futu": futu}), mock.patch.dict(
            os.environ,
            {"KLINE_USE_FUTU_OPEND": "1", "FUTU_HOST": "10.0.0.8", "FUTU_PORT": "12345"},
            clear=False,
        ):
            with kline_fetcher.managed_fetcher_chain() as fetchers:
                self.assertEqual([item.get_name() for item in fetchers], ["FutuOpenAPI", "YFinance", "AKShare"])
                self.assertEqual((QuoteContext.instance.host, QuoteContext.instance.port), ("10.0.0.8", 12345))

        self.assertTrue(QuoteContext.instance.closed)

    def test_unready_opend_is_closed_and_falls_back(self):
        class QuoteContext:
            instance = None

            def __init__(self, host, port):
                self.closed = False
                QuoteContext.instance = self

            def get_global_state(self):
                return 0, {"program_status_type": "READY", "qot_logined": False}

            def close(self):
                self.closed = True

        futu = types.SimpleNamespace(OpenQuoteContext=QuoteContext, RET_OK=0)
        yf_patch, ak_patch = self._fallbacks()
        with yf_patch, ak_patch, mock.patch.dict(sys.modules, {"futu": futu}), mock.patch.dict(
            os.environ, {"KLINE_USE_FUTU_OPEND": "1"}, clear=False
        ):
            with kline_fetcher.managed_fetcher_chain() as fetchers:
                self.assertEqual([item.get_name() for item in fetchers], ["YFinance", "AKShare"])

        self.assertTrue(QuoteContext.instance.closed)

    def test_factory_excludes_opend_without_a_verified_context(self):
        fetchers = kline_fetcher.KlineFetcherFactory.create_fetcher_chain()

        names = [fetcher.get_name() for fetcher in fetchers]
        self.assertNotIn("DatabaseKlineCache", names)
        self.assertFalse(any("FutuOpenD" in name for name in names))


if __name__ == "__main__":
    unittest.main()
