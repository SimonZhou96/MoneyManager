import subprocess
import unittest
from pathlib import Path


class RunMoneyManagerScriptTest(unittest.TestCase):
    def test_list_prints_supported_scenarios(self):
        script_path = Path(__file__).resolve().parents[1] / "run_moneymanager.sh"

        completed = subprocess.run(
            [str(script_path), "--list"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        for command in ["screening", "option", "backend", "frontend", "web"]:
            self.assertIn(command, completed.stdout)

    def test_help_explains_stock_pool_selection(self):
        script_path = Path(__file__).resolve().parents[1] / "run_moneymanager.sh"

        completed = subprocess.run(
            [str(script_path), "--help"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("股票池类型", completed.stdout)
        self.assertIn("POOLS", completed.stdout)

    def test_interactive_menu_mentions_screening_pool_selection(self):
        script_path = Path(__file__).resolve().parents[1] / "run_moneymanager.sh"

        completed = subprocess.run(
            [str(script_path)],
            input="0\n",
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("选股器（全市场可选择股票池类型）", completed.stdout)


if __name__ == "__main__":
    unittest.main()
