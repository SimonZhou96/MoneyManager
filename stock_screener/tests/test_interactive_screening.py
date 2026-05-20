import unittest
from unittest.mock import patch

from interactive_screening import InteractiveScreeningOptions, ScreeningInteractiveApp


class InteractiveScreeningTests(unittest.TestCase):
    def test_split_csv_values_accepts_chinese_commas(self):
        values = ScreeningInteractiveApp._split_csv_values("HK， US,A,,")

        self.assertEqual(values, ["HK", "US", "A"])

    def test_prompt_custom_code_options(self):
        answers = iter([
            "2",        # 自选股票代码筛选
            "",         # timeframe
            "n",        # AI 分析
            "y",        # 主力资金外部数据
            "n",        # 飞书
            "",         # CSV
            "",         # chain_key
            "US",       # market
            "AAPL,MSFT",
        ])
        app = ScreeningInteractiveApp(input_func=lambda _: next(answers), print_func=lambda *args, **kwargs: None)

        options = app.prompt_options()

        self.assertEqual(options.mode, "custom")
        self.assertEqual(options.market, "US")
        self.assertEqual(options.codes, ["AAPL", "MSFT"])
        self.assertFalse(options.enable_ai_analysis)
        self.assertTrue(options.enable_main_force_external_data)
        self.assertFalse(options.send_feishu)

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
        self.assertIn("分钟级: 1m, 2m, 5m, 15m, 30m, 60m, 90m", text)
        self.assertIn("小时级: 1h", text)
        self.assertIn("日线及以上: 1d, 5d, 1wk, 1mo, 3mo", text)
        self.assertIn("K 线周期无效", text)

    def test_prompt_full_market_options_without_fetch(self):
        answers = iter([
            "",          # 默认全市场筛选
            "1d",
            "y",         # AI 分析
            "n",         # 主力资金外部数据
            "n",         # 飞书
            "logs/x.csv",
            "trial_chain",
            "HK,A",
            "best,major_index,all_etf",
            "n",         # 不刷新股票池
            "2",         # market workers
            "",          # futu host
            "",          # futu port
        ])
        app = ScreeningInteractiveApp(input_func=lambda _: next(answers), print_func=lambda *args, **kwargs: None)

        options = app.prompt_options()

        self.assertEqual(options.mode, "full")
        self.assertEqual(options.markets, ["HK", "A"])
        self.assertEqual(options.pools, ["best", "major_index", "all_etf"])
        self.assertFalse(options.fetch_pools)
        self.assertFalse(options.enable_main_force_external_data)
        self.assertEqual(options.market_workers, 2)
        self.assertEqual(options.chain_key, "trial_chain")

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

        self.assertEqual(pools, ["best", "major_index", "industry_top5", "recent_ipo_2y", "all_etf"])
        text = "\n".join(output)
        self.assertIn("major_index", text)
        self.assertIn("industry_top5", text)
        self.assertIn("recent_ipo_2y", text)
        self.assertIn("all_etf", text)
        self.assertIn("无效股票池类型", text)

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
            self.assertEqual(os.environ["MAIN_FORCE_ENABLE_FUTU_OPEND"], "1")
            self.assertEqual(os.environ["FUTU_HOST"], "127.0.0.1")
            self.assertEqual(os.environ["FUTU_PORT"], "11111")


if __name__ == "__main__":
    unittest.main()
