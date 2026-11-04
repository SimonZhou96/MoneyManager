import unittest
from unittest.mock import patch

from interactive_screening import InteractiveScreeningOptions, ScreeningInteractiveApp
from screening_prompt_flow import PromptBack, PromptQuit


class InteractiveScreeningTests(unittest.TestCase):
    def test_split_csv_values_accepts_chinese_commas(self):
        values = ScreeningInteractiveApp._split_csv_values("HK， US,A,,")
        self.assertEqual(values, ["HK", "US", "A"])

    def test_prompt_timeframe_lists_options_and_retries_invalid_value(self):
        answers = iter(["bad", "1h"])
        output = []
        app = ScreeningInteractiveApp(
            input_func=lambda _: next(answers),
            print_func=lambda *args, **kwargs: output.append(" ".join(str(arg) for arg in args)),
        )
        timeframe = app._prompt_timeframe("K 线周期", "1d")
        self.assertEqual(timeframe, "1h")
        text = "\n".join(output)
        self.assertIn("K 线周期无效", text)

    def test_prompt_pools_rejects_removed_legacy_pool_types(self):
        answers = iter([
            "index,industry,ipo,etf",
            "best,major_index,industry_top5,recent_ipo_2y,all_etf",
        ])
        output = []
        app = ScreeningInteractiveApp(
            input_func=lambda _: next(answers),
            print_func=lambda *args, **kwargs: output.append(" ".join(str(arg) for arg in args)),
        )
        pools = app._prompt_pools()
        self.assertEqual(
            pools, ["best", "major_index", "industry_top5", "recent_ipo_2y", "all_etf"])
        self.assertIn("无效股票池类型", "\n".join(output))

    def test_apply_main_force_external_data_env(self):
        options = InteractiveScreeningOptions(
            mode="full",
            enable_main_force_external_data=True,
            futu_host="127.0.0.1",
            futu_port=11111,
        )
        with patch.dict("os.environ", {}, clear=True):
            ScreeningInteractiveApp._apply_main_force_external_data_env(options)
            import os
            self.assertEqual(os.environ["ENABLE_MAIN_FORCE_RISK_ANALYSIS"], "1")
            self.assertEqual(os.environ["MAIN_FORCE_ENABLE_EXTERNAL_DATA"], "1")
            self.assertEqual(os.environ["FUTU_HOST"], "127.0.0.1")


class NavInputTests(unittest.TestCase):
    def _app(self, value):
        return ScreeningInteractiveApp(
            input_func=lambda _prompt="": value,
            print_func=lambda *a, **k: None,
        )

    def test_nav_input_back(self):
        app = self._app("b")
        with self.assertRaises(PromptBack):
            app._nav_input("x: ")

    def test_nav_input_quit(self):
        app = self._app("q")
        with self.assertRaises(PromptQuit):
            app._nav_input("x: ")

    def test_prompt_bool_back_propagates(self):
        app = self._app("b")
        with self.assertRaises(PromptBack):
            app._prompt_bool("启用?", True)


class PromptOptionsFlowTests(unittest.TestCase):
    def _app(self, answers):
        it = iter(answers)
        return ScreeningInteractiveApp(
            input_func=lambda _prompt="": next(it),
            print_func=lambda *a, **k: None,
        )

    @patch("interactive_screening.load_last_config", return_value=None)
    @patch("interactive_screening.save_last_config", return_value=True)
    def test_custom_flow_minimal(self, _save, _load):
        app = self._app(["2", "", "n", "y", "n", "US", "AAPL,MSFT", "", ""])
        with patch.object(
            ScreeningInteractiveApp, "_prompt_chain_for_markets", return_value={"US": None},
        ):
            options = app.prompt_options()
        self.assertEqual(options.mode, "custom")
        self.assertEqual(options.market, "US")
        self.assertEqual(options.codes, ["AAPL", "MSFT"])

    @patch("interactive_screening.load_last_config", return_value=None)
    @patch("interactive_screening.save_last_config", return_value=True)
    def test_full_flow_no_fetch(self, _save, _load):
        app = self._app(["1", "1d", "y", "n", "n", "HK,A", "best", "n", "2", "n", ""])
        with patch.object(
            ScreeningInteractiveApp,
            "_prompt_chain_for_markets",
            return_value={"HK": None, "A": "a_trial"},
        ):
            options = app.prompt_options()
        self.assertEqual(options.mode, "full")
        self.assertEqual(options.markets, ["HK", "A"])
        self.assertEqual(options.pools, ["best"])
        self.assertFalse(options.fetch_pools)
        self.assertEqual(options.market_workers, 2)
        self.assertEqual(options.chain_by_market, {"HK": None, "A": "a_trial"})

    def test_codes_required_no_example_default(self):
        app = self._app(["", "AAPL"])
        codes = app._prompt_codes("US")
        self.assertEqual(codes, ["AAPL"])


class RunCancelTests(unittest.TestCase):
    def test_run_returns_130_on_cancel(self):
        from screening_prompt_flow import FlowCancelled

        app = ScreeningInteractiveApp(
            input_func=lambda _prompt="": "q",
            print_func=lambda *a, **k: None,
        )

        def boom(*_a, **_k):
            raise FlowCancelled()

        app.prompt_options = boom  # type: ignore[method-assign]
        with patch("interactive_screening._load_dotenv"):
            self.assertEqual(app.run(), 130)


if __name__ == "__main__":
    unittest.main()
