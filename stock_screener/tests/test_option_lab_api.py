import unittest

from option_lab.macro_analysis import OptionMacroAnalysis
from option_lab.models import RiskProfile
from option_lab.market_data import FakeOptionMarketDataProvider
from option_lab.service import InMemoryOptionLabRepository, OptionLabService
from web.auth import CurrentUser
from web.business import BusinessError
from web.options import (
    OptionBatchEvaluateRequest,
    OptionEvaluateRequest,
    assert_plan_owner,
    evaluate_options,
    evaluate_options_batch,
    evaluation_item_to_response,
    position_belongs_to_user,
    risk_profile_from_request,
)


class RecordingMacroProvider:
    def __init__(self):
        self.calls = []

    def analyze(self, *, market, code, snapshot, ttl_minutes):
        self.calls.append((market, code, ttl_minutes))
        return OptionMacroAnalysis(
            macro_score=80,
            macro_direction="偏多",
            news_impact="利好",
            hot_sector_mark="重点",
            main_force_risk_level="低",
            summary="宏观层面支持跟踪",
            positive_factors=["热点资金关注"],
            risk_factors=["估值波动"],
            macro_factors=["政策环境中性偏多"],
            source_urls=["https://example.com/news"],
            warnings=[],
            cached=False,
            provider="recording",
            analyzed_at="2026-05-16T10:00:00",
            expires_at="2026-05-16T11:00:00",
        )


class OptionLabApiTests(unittest.TestCase):
    def test_evaluate_request_accepts_chinese_risk_profile(self):
        payload = OptionEvaluateRequest(market="US", code="AAPL", risk_profile="保守")

        self.assertEqual(risk_profile_from_request(payload), RiskProfile.CONSERVATIVE)

    def test_evaluate_request_rejects_bad_risk_profile(self):
        payload = OptionEvaluateRequest(market="US", code="AAPL", risk_profile="极端")

        with self.assertRaisesRegex(ValueError, "不支持的风险偏好"):
            risk_profile_from_request(payload)

    def test_evaluate_request_macro_flags_default_off(self):
        payload = OptionEvaluateRequest(market="US", code="AAPL")

        self.assertFalse(payload.enable_macro_analysis)
        self.assertFalse(payload.force_macro_refresh)
        self.assertEqual(payload.macro_cache_ttl_minutes, 60)

    def test_evaluate_options_without_macro_keeps_candidate_plain(self):
        provider = RecordingMacroProvider()
        service = OptionLabService(
            repository=InMemoryOptionLabRepository(),
            market_data_provider=FakeOptionMarketDataProvider(),
            macro_analysis_provider=provider,
        )
        payload = OptionEvaluateRequest(market="US", code="AAPL", risk_profile="均衡")

        response = evaluate_options(payload, CurrentUser(id=1, username="u", role="admin"), service)

        self.assertEqual(provider.calls, [])
        self.assertNotIn("macro_analysis", response)
        self.assertNotIn("宏观分析评分", response["candidates"][0])

    def test_evaluate_options_with_macro_returns_macro_and_composite_scores(self):
        service = OptionLabService(
            repository=InMemoryOptionLabRepository(),
            market_data_provider=FakeOptionMarketDataProvider(),
            macro_analysis_provider=RecordingMacroProvider(),
        )
        payload = OptionEvaluateRequest(
            market="US",
            code="AAPL",
            risk_profile="均衡",
            enable_macro_analysis=True,
            force_macro_refresh=True,
            macro_cache_ttl_minutes=30,
        )

        response = evaluate_options(payload, CurrentUser(id=1, username="u", role="admin"), service)

        candidate = response["candidates"][0]
        self.assertEqual(response["macro_analysis"]["宏观分析评分"], 80)
        self.assertEqual(candidate["评分"], candidate["期权评分"])
        self.assertEqual(candidate["宏观分析评分"], 80)
        self.assertEqual(candidate["综合评分"], round(candidate["期权评分"] * 0.7 + 80 * 0.3, 2))

    def test_batch_response_contains_run_id_and_candidates_for_frontend(self):
        service = OptionLabService(
            repository=InMemoryOptionLabRepository(),
            market_data_provider=FakeOptionMarketDataProvider(),
        )
        payload = OptionBatchEvaluateRequest(market="US", codes=["AAPL"], risk_profile="均衡")

        response = evaluate_options_batch(payload, CurrentUser(id=1, username="u", role="admin"), service)

        self.assertEqual(response["status"], "completed")
        self.assertTrue(response["run_id"])
        self.assertTrue(response["items"][0]["run_id"])
        self.assertGreater(len(response["items"][0]["candidates"]), 0)

    def test_batch_response_passes_macro_flags(self):
        service = OptionLabService(
            repository=InMemoryOptionLabRepository(),
            market_data_provider=FakeOptionMarketDataProvider(),
            macro_analysis_provider=RecordingMacroProvider(),
        )
        payload = OptionBatchEvaluateRequest(market="US", codes=["AAPL"], risk_profile="均衡", enable_macro_analysis=True)

        response = evaluate_options_batch(payload, CurrentUser(id=1, username="u", role="admin"), service)

        self.assertEqual(response["items"][0]["macro_analysis"]["宏观分析评分"], 80)
        self.assertEqual(response["items"][0]["candidates"][0]["宏观分析评分"], 80)

    def test_batch_history_item_restores_child_run_and_candidates(self):
        row = evaluation_item_to_response({
            "run_id": "parent-run",
            "best_strategy": {
                "child_run_id": "child-run",
                "策略名称": "买入看涨期权",
                "评分": 83,
                "candidates": [{"candidate_id": "candidate-1"}],
            },
        })

        self.assertEqual(row["run_id"], "child-run")
        self.assertEqual(row["candidates"][0]["candidate_id"], "candidate-1")

    def test_null_owner_records_are_not_accessible(self):
        class Repo:
            def get_order_plan(self, plan_id):
                return {"plan_id": plan_id, "user_id": None}

        with self.assertRaises(BusinessError):
            assert_plan_owner(Repo(), "plan-1", 1)
        self.assertFalse(position_belongs_to_user({"position_id": "p", "user_id": None}, 1))


if __name__ == "__main__":
    unittest.main()
