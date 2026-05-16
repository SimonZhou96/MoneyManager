import unittest

from option_lab.market_data import FakeOptionMarketDataProvider, OptionMarketDataProviderChain


class OptionLabMarketDataTests(unittest.TestCase):
    def test_fake_provider_returns_snapshot_with_contracts(self):
        provider = FakeOptionMarketDataProvider()
        snapshot = provider.fetch_snapshot("US", "US.AAPL")

        self.assertEqual(snapshot.underlying.code, "US.AAPL")
        self.assertEqual(snapshot.data_quality.status, "ok")
        self.assertGreaterEqual(len(snapshot.option_quotes), 2)
        self.assertEqual(snapshot.option_quotes[0].currency, "USD")

    def test_provider_chain_falls_back_to_second_provider(self):
        class EmptyProvider:
            name = "empty"

            def fetch_snapshot(self, market, code):
                raise RuntimeError("empty source")

        chain = OptionMarketDataProviderChain([EmptyProvider(), FakeOptionMarketDataProvider()])
        snapshot = chain.fetch_snapshot("HK", "HK.00700")

        self.assertEqual(snapshot.provider, "fake")
        self.assertEqual(snapshot.underlying.market, "HK")


if __name__ == "__main__":
    unittest.main()
