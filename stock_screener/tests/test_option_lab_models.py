import unittest
from datetime import datetime

from option_lab.models import (
    ContractDetail,
    DataQuality,
    MonitorEventSeverity,
    OptionContractType,
    OptionSide,
    RiskProfile,
    StrategyCandidate,
)


class OptionLabModelTests(unittest.TestCase):
    def test_risk_profile_chinese_labels(self):
        self.assertEqual(RiskProfile.CONSERVATIVE.label, "保守")
        self.assertEqual(RiskProfile.BALANCED.label, "均衡")
        self.assertEqual(RiskProfile.AGGRESSIVE.label, "进取")
        self.assertEqual(RiskProfile.from_input("保守"), RiskProfile.CONSERVATIVE)
        self.assertEqual(RiskProfile.from_input("balanced"), RiskProfile.BALANCED)

    def test_contract_detail_uses_chinese_display_fields(self):
        detail = ContractDetail(
            side=OptionSide.BUY,
            contract_type=OptionContractType.CALL,
            option_code="US.AAPL260619C00200000",
            provider_code="AAPL260619C00200000",
            expiration_date="2026-06-19",
            strike=200.0,
            suggested_price=5.25,
            quantity=1,
            currency="USD",
        )

        self.assertEqual(detail.to_display_row()["买卖方向"], "买入")
        self.assertEqual(detail.to_display_row()["期权类型"], "Call")
        self.assertEqual(detail.to_display_row()["合约代码"], "US.AAPL260619C00200000")
        self.assertEqual(detail.to_display_row()["建议价格"], 5.25)

    def test_candidate_serializes_for_frontend(self):
        candidate = StrategyCandidate(
            candidate_id="candidate-1",
            run_id="run-1",
            market="US",
            code="US.AAPL",
            strategy_key="long_call",
            strategy_name="买入看涨期权",
            score=82.5,
            recommendation_status="recommended",
            fit_reason="正股趋势偏强，期权流动性达标",
            contract_details=[],
            risk_metrics={"最大亏损": 525.0},
            order_suggestion={"建议限价": 5.25},
            warnings=[],
            data_quality=DataQuality(status="ok", warnings=[]),
            created_at=datetime(2026, 5, 16, 9, 30, 0),
        )

        payload = candidate.to_dict()

        self.assertEqual(payload["策略名称"], "买入看涨期权")
        self.assertEqual(payload["评分"], 82.5)
        self.assertEqual(payload["数据质量"]["status"], "ok")

    def test_candidate_serializes_macro_evidence_for_frontend(self):
        candidate = StrategyCandidate(
            candidate_id="candidate-1",
            run_id="run-1",
            market="HK",
            code="HK.01810",
            strategy_key="long_call",
            strategy_name="买入看涨期权",
            score=82.5,
            recommendation_status="recommended",
            fit_reason="正股趋势偏强",
            contract_details=[],
            risk_metrics={},
            order_suggestion={},
            warnings=[],
            data_quality=DataQuality(status="ok", warnings=[]),
            created_at=datetime(2026, 5, 16, 9, 30, 0),
            option_score=82.5,
            macro_score=70,
            composite_score=78.75,
            macro_data_gaps=["主力资金/盘口数据缺失或不足"],
            macro_evidence_links=[{"label": "小米年报", "url": "https://ir.mi.com/annual-report"}],
            macro_factor_citations={
                "小米在AI、机器人领域有业务布局": [
                    {"label": "小米年报", "url": "https://ir.mi.com/annual-report"}
                ]
            },
        )

        payload = candidate.to_dict()

        self.assertEqual(payload["数据缺失原因"], ["主力资金/盘口数据缺失或不足"])
        self.assertEqual(payload["引用来源"][0]["label"], "小米年报")
        self.assertEqual(
            payload["因素引用"]["小米在AI、机器人领域有业务布局"][0]["url"],
            "https://ir.mi.com/annual-report",
        )

    def test_monitor_event_severity_labels(self):
        self.assertEqual(MonitorEventSeverity.URGENT.label, "紧急")
        self.assertEqual(MonitorEventSeverity.IMPORTANT.label, "重要")
        self.assertEqual(MonitorEventSeverity.INFO.label, "提示")


if __name__ == "__main__":
    unittest.main()
