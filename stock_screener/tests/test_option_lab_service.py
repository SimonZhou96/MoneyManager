import unittest

from option_lab.market_data import FakeOptionMarketDataProvider
from option_lab.models import RiskProfile
from option_lab.service import InMemoryOptionLabRepository, OptionLabService


class OptionLabServiceTests(unittest.TestCase):
    def test_evaluate_single_persists_run_and_candidates(self):
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(repository=repo, market_data_provider=FakeOptionMarketDataProvider())

        result = service.evaluate_single(market="US", code="US.AAPL", risk_profile=RiskProfile.CONSERVATIVE)

        self.assertEqual(result.status, "completed")
        self.assertGreater(len(result.candidates), 0)
        self.assertEqual(repo.runs[result.run_id]["status"], "completed")
        self.assertEqual(len(repo.candidates[result.run_id]), len(result.candidates))

    def test_save_order_plan_and_record_fill_creates_position(self):
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(repository=repo, market_data_provider=FakeOptionMarketDataProvider())
        result = service.evaluate_single(market="US", code="US.AAPL", risk_profile=RiskProfile.BALANCED)

        plan = service.save_order_plan(result.candidates[0].candidate_id, user_id=1)
        position = service.record_fill(
            plan_id=plan.plan_id,
            filled_price=5.1,
            quantity=1,
            filled_at="2026-05-16 10:00:00",
            fee=1.0,
        )

        self.assertEqual(position["status"], "active")
        self.assertEqual(position["策略名称"], result.candidates[0].strategy_name)

    def test_refresh_position_generates_chinese_events(self):
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(repository=repo, market_data_provider=FakeOptionMarketDataProvider())
        result = service.evaluate_single(market="US", code="US.AAPL", risk_profile=RiskProfile.BALANCED)
        plan = service.save_order_plan(result.candidates[0].candidate_id, user_id=1)
        position = service.record_fill(plan.plan_id, filled_price=5.1, quantity=1, filled_at="2026-05-16 10:00:00")

        events = service.refresh_position(position["position_id"])

        self.assertGreaterEqual(len(events), 1)
        self.assertIn(events[0]["提醒级别"], {"紧急", "重要", "提示"})

    def test_evaluate_single_uses_scope_capital_and_holding_days(self):
        repo = InMemoryOptionLabRepository()
        service = OptionLabService(repository=repo, market_data_provider=FakeOptionMarketDataProvider())

        result = service.evaluate_single(
            market="US",
            code="US.AAPL",
            risk_profile=RiskProfile.BALANCED,
            capital=100000,
            planned_holding_days=20,
            strategy_scope=["long_call"],
        )

        self.assertEqual([item.strategy_key for item in result.candidates], ["long_call"])
        self.assertGreaterEqual(result.candidates[0].order_suggestion["建议数量"], 1)
        self.assertEqual(result.candidates[0].order_suggestion["计划持有期"], "20天")


if __name__ == "__main__":
    unittest.main()
