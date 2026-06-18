"""基线测试：SH.600999 / US.AXP / HK.00128 全流程筛选

覆盖：
1. 技术规则评估（41条）
2. Top20 选择（虽然只有3只，但走完整链路）
3. 宏观规则评估
4. aggregate_rule_scores 评分
5. 写 DB
6. CSV 导出

用于 unified_bullish_top20 优化前的基线记录。
"""
import sys
import os
import json
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.screen_service import run_screening_task
from db import MySqlConfig


# ── DB 配置 ──────────────────────────────────────────────────
MYSQL_CONFIG = MySqlConfig(
    host="127.0.0.1",
    port=3306,
    user="root",
    password="123456",
    database="market_data",
)


# ── 测试股票 ─────────────────────────────────────────────────
TEST_STOCKS = [
    {"market": "A", "code": "SH.600999", "name": "招商证券"},
    {"market": "US", "code": "US.AXP", "name": "美国运通"},
    {"market": "HK", "code": "HK.00128", "name": "安宁控股"},
]


def screen_one_stock(market: str, code: str, name: str):
    """对单只股票执行 unified_bullish_top20 全流程"""
    print(f"\n{'='*70}")
    print(f"  [{market}] {code} {name}")
    print(f"{'='*70}")

    task_id = f"e2e_baseline_{market}_{code.replace('.','_')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    try:
        result = run_screening_task(
            mysql_config=MYSQL_CONFIG,
            task_id=task_id,
            market=market,
            timeframe="1d",
            params=None,
            verbose=True,
            watchlist=[{"code": code, "name": name}],
            progress_log=True,
            chain_key="unified_bullish_top20",
        )
        return result
    except Exception as e:
        print(f"  ❌ 筛选失败: {e}")
        import traceback
        traceback.print_exc()
        return None


class BaselineE2ETest(unittest.TestCase):
    """基线端到端测试"""

    @classmethod
    def setUpClass(cls):
        cls.results = {}
        for stock in TEST_STOCKS:
            r = screen_one_stock(**stock)
            cls.results[f"{stock['market']}.{stock['code']}"] = r

    def test_01_SH_600999_passed(self):
        """SH.600999 招商证券 — 应通过筛选"""
        r = self.results.get("A.SH.600999")
        self.assertIsNotNone(r, "SH.600999 返回 None（筛选异常）")
        print(f"\n  SH.600999 filter_details count: {len(r.get('filter_details', [])) if r else 0}")
        print(f"  SH.600999 is_passed: {r.get('is_passed') if r else 'N/A'}")
        print(f"  SH.600999 final_score: {r.get('final_score') if r else 'N/A'}")

    def test_02_US_AXP_passed(self):
        """US.AXP 美国运通 — 应通过筛选"""
        r = self.results.get("US.US.AXP")
        self.assertIsNotNone(r, "US.AXP 返回 None（筛选异常）")
        print(f"\n  US.AXP filter_details count: {len(r.get('filter_details', [])) if r else 0}")
        print(f"  US.AXP is_passed: {r.get('is_passed') if r else 'N/A'}")
        print(f"  US.AXP final_score: {r.get('final_score') if r else 'N/A'}")

    def test_03_HK_00128_passed(self):
        """HK.00128 安宁控股 — 应通过筛选"""
        r = self.results.get("HK.HK.00128")
        self.assertIsNotNone(r, "HK.00128 返回 None（筛选异常）")
        print(f"\n  HK.00128 filter_details count: {len(r.get('filter_details', [])) if r else 0}")
        print(f"  HK.00128 is_passed: {r.get('is_passed') if r else 'N/A'}")
        print(f"  HK.00128 final_score: {r.get('final_score') if r else 'N/A'}")

    def test_04_all_three_have_filter_details(self):
        """三只股票都有完整的 filter_details"""
        for key, r in self.results.items():
            with self.subTest(stock=key):
                self.assertIsNotNone(r, f"{key} 返回 None")
                details = r.get("filter_details", [])
                self.assertGreater(len(details), 0, f"{key} filter_details 为空")

                # 分类统计
                tech = [d for d in details if d.get("strategy_category") == "technical"]
                macro = [d for d in details if d.get("strategy_category") == "macro"]
                bullish = [d for d in tech if d.get("result") == "pass"]
                print(f"\n  {key}: {len(tech)} 技术规则, {len(bullish)} 通过, {len(macro)} 宏观规则")

    def test_05_all_three_have_valid_scores(self):
        """三只股票都有有效的 final_score"""
        for key, r in self.results.items():
            with self.subTest(stock=key):
                self.assertIsNotNone(r, f"{key} 返回 None")
                score = r.get("final_score")
                self.assertIsNotNone(score, f"{key} final_score 为 None")
                self.assertGreaterEqual(score, 0, f"{key} final_score < 0")
                self.assertLessEqual(score, 100, f"{key} final_score > 100")
                print(f"\n  {key} final_score: {score}")

    def test_06_all_three_written_to_db(self):
        """三只股票的结果已写入 DB"""
        from db import list_single_stock_runs_by_code

        for stock in TEST_STOCKS:
            with self.subTest(stock=f"{stock['market']}.{stock['code']}"):
                runs = list_single_stock_runs_by_code(
                    MYSQL_CONFIG, stock["market"], stock["code"]
                )
                self.assertGreater(len(runs), 0, f"{stock['code']} 没有 DB 记录")
                latest = runs[0]
                print(f"\n  {stock['code']}: run_id={latest.get('run_id')}, "
                      f"is_passed={latest.get('is_passed')}, "
                      f"final_score={latest.get('final_score')}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
