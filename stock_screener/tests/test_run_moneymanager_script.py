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
        for command in ["full-screening", "code-screening", "screening", "option", "quant", "rules", "backend", "frontend", "web"]:
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
        self.assertIn("全市场筛选", completed.stdout)
        self.assertIn("个股筛选器", completed.stdout)
        self.assertIn("POOLS", completed.stdout)

    def test_interactive_menu_aligns_with_frontend_entries(self):
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
        self.assertIn("业务入口（与前端导航对齐）", completed.stdout)
        for label in ["总览（Web 控制台）", "全市场筛选", "个股筛选器", "期权实验室", "量化实验室（Web）", "规则链（Web）"]:
            self.assertIn(label, completed.stdout)
        self.assertIn("服务/工具", completed.stdout)
        self.assertNotIn("选股器（全市场可选择股票池类型）", completed.stdout)


if __name__ == "__main__":
    unittest.main()
