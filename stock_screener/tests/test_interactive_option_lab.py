import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from interactive_option_lab import InteractiveOptionLabApp


class InteractiveOptionLabTests(unittest.TestCase):
    def test_single_mode_collects_chinese_options(self):
        answers = iter(["1", "US", "AAPL", "2", "100000", "", "1000", "30", "n", "n"])
        output = []

        app = InteractiveOptionLabApp(
            input_func=lambda prompt: next(answers),
            print_func=lambda *args, **kwargs: output.append(" ".join(str(a) for a in args)),
        )
        options = app.prompt_evaluation_options()

        self.assertEqual(options.mode, "single")
        self.assertEqual(options.market, "US")
        self.assertEqual(options.codes, ["US.AAPL"])
        self.assertEqual(options.risk_profile, "均衡")
        self.assertFalse(options.enable_macro_analysis)
        self.assertTrue(any("MoneyManager 期权实验室" in line for line in output))

    def test_batch_mode_parses_codes(self):
        answers = iter(["2", "HK", "700,9988", "1", "", "", "", "", "n", "n"])
        app = InteractiveOptionLabApp(input_func=lambda prompt: next(answers), print_func=lambda *args, **kwargs: None)
        options = app.prompt_evaluation_options()

        self.assertEqual(options.mode, "batch")
        self.assertEqual(options.codes, ["HK.00700", "HK.09988"])
        self.assertEqual(options.risk_profile, "保守")

    def test_macro_options_can_be_enabled(self):
        answers = iter(["1", "US", "AAPL", "3", "", "", "", "30", "y", "y", "n"])
        app = InteractiveOptionLabApp(input_func=lambda prompt: next(answers), print_func=lambda *args, **kwargs: None)

        options = app.prompt_evaluation_options()

        self.assertTrue(options.enable_macro_analysis)
        self.assertTrue(options.force_macro_refresh)
        self.assertEqual(options.macro_cache_ttl_minutes, 60)

    def test_shell_runs_multiple_commands_in_one_session(self):
        answers = iter([
            "eval",
            "US",
            "AAPL",
            "2",
            "100000",
            "",
            "1000",
            "30",
            "n",
            "n",
            "1",
            "plans",
            "exit",
        ])
        output = []

        with patch.dict("os.environ", {"OPTION_LAB_MARKET_DATA_MODE": "fake"}):
            app = InteractiveOptionLabApp(
                input_func=lambda prompt: next(answers),
                print_func=lambda *args, **kwargs: output.append(" ".join(str(a) for a in args)),
            )
            code = app.run_shell()

        self.assertEqual(code, 0)
        self.assertTrue(any("option-lab> 可用命令" in line for line in output))
        self.assertTrue(any("订单建议列表" in line for line in output))
        self.assertGreaterEqual(len(app.repository.order_plans), 1)

    def test_shell_script_starts_persistent_cli(self):
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_option_lab_shell.sh"
        env = os.environ.copy()
        env["OPTION_LAB_MARKET_DATA_MODE"] = "fake"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHON_BIN"] = sys.executable

        completed = subprocess.run(
            [str(script_path)],
            input="help\nexit\n",
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("option-lab> 可用命令", completed.stdout)
        self.assertIn("已退出期权实验室", completed.stdout)


if __name__ == "__main__":
    unittest.main()
