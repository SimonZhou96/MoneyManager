import os
import unittest
from unittest import mock

from screening_defaults import BUILTIN_DEFAULTS, env_defaults, resolve_defaults


class ScreeningDefaultsTests(unittest.TestCase):
    def test_builtin_defaults_contains_expected_keys(self) -> None:
        expected_keys = {
            "timeframe",
            "markets",
            "pools",
            "csv_path",
            "market_workers",
            "futu_host",
            "futu_port",
            "no_fetch",
            "no_feishu",
            "require_fresh_pools",
            "enable_llm_analysis",
            "main_force_enable_external_data",
        }
        self.assertEqual(expected_keys, set(BUILTIN_DEFAULTS.keys()))

    def test_env_defaults_parses_values(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "TIMEFRAME": "1wk",
                "MARKETS": "HK,US",
                "POOLS": "best,major_index",
                "CSV_PATH": "tmp/result.csv",
                "MARKET_WORKERS": "5",
                "FUTU_HOST": "10.0.0.2",
                "FUTU_PORT": "22222",
                "NO_FETCH": "1",
                "NO_FEISHU": "1",
                "REQUIRE_FRESH_POOLS": "1",
                "ENABLE_LLM_ANALYSIS": "0",
                "MAIN_FORCE_ENABLE_EXTERNAL_DATA": "0",
            },
            clear=False,
        ):
            defaults = env_defaults()

        self.assertEqual("1wk", defaults["timeframe"])
        self.assertEqual("HK,US", defaults["markets"])
        self.assertEqual("best,major_index", defaults["pools"])
        self.assertEqual("tmp/result.csv", defaults["csv_path"])
        self.assertEqual(5, defaults["market_workers"])
        self.assertEqual("10.0.0.2", defaults["futu_host"])
        self.assertEqual(22222, defaults["futu_port"])
        self.assertTrue(defaults["no_fetch"])
        self.assertTrue(defaults["no_feishu"])
        self.assertTrue(defaults["require_fresh_pools"])
        self.assertFalse(defaults["enable_llm_analysis"])
        self.assertFalse(defaults["main_force_enable_external_data"])

    def test_resolve_defaults_uses_last_then_env_then_builtin(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "TIMEFRAME": "1wk",
                "MARKET_WORKERS": "5",
                "FUTU_PORT": "22222",
                "NO_FETCH": "1",
            },
            clear=False,
        ):
            resolved = resolve_defaults(
                {
                    "timeframe": "3mo",
                    "market_workers": 7,
                }
            )

        self.assertEqual("3mo", resolved["timeframe"])
        self.assertEqual(7, resolved["market_workers"])
        self.assertEqual(22222, resolved["futu_port"])
        self.assertTrue(resolved["no_fetch"])
        self.assertEqual("HK,US,A", resolved["markets"])

    def test_resolve_defaults_ignores_unknown_keys(self) -> None:
        resolved = resolve_defaults(
            {
                "timeframe": "1d",
                "unknown_key": "value",
            }
        )
        self.assertEqual("1d", resolved["timeframe"])
        self.assertNotIn("unknown_key", resolved)


if __name__ == "__main__":
    unittest.main()
