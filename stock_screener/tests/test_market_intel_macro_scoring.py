import json
import unittest
from datetime import datetime, timedelta, timezone

try:
    from market_intel.macro_scoring import (
        DEFAULT_MACRO_SUB_WEIGHTS,
        MacroEvidencePreprocessor,
        MacroScoreParser,
        aggregate_rule_scores,
    )
    from market_intel.models import EvidencePack, IntelItem
except ModuleNotFoundError:
    from stock_screener.market_intel.macro_scoring import (
        DEFAULT_MACRO_SUB_WEIGHTS,
        MacroEvidencePreprocessor,
        MacroScoreParser,
        aggregate_rule_scores,
    )
    from stock_screener.market_intel.models import EvidencePack, IntelItem


_DEFAULT_FETCHED_AT = object()


def make_item(
    *,
    title,
    item_type,
    event_time=None,
    published_at=None,
    fetched_at=_DEFAULT_FETCHED_AT,
    summary="",
    url="https://example.com/intel",
):
    if fetched_at is _DEFAULT_FETCHED_AT:
        fetched_at = datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc)
    return IntelItem(
        scope_type="stock",
        market="A",
        code="SZ.000001",
        source="test",
        provider="test",
        item_type=item_type,
        title=title,
        summary=summary,
        url=url,
        event_time=event_time,
        published_at=published_at,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(hours=3) if fetched_at is not None else None,
        dedupe_key=title,
    )


class MacroEvidencePreprocessorTests(unittest.TestCase):
    def test_preprocessor_groups_dimensions_and_sorts_effective_time(self):
        pack = EvidencePack(
            market="A",
            code="SZ.000001",
            structured_items=[
                make_item(
                    title="较早利好订单",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc),
                    published_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc),
                    summary="获得订单",
                ),
                make_item(
                    title="较新风险提示",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
                    published_at=datetime(2026, 5, 26, 14, 10, tzinfo=timezone.utc),
                    summary="订单延期风险",
                ),
                make_item(
                    title="AI 板块走强",
                    item_type="hot_sector",
                    event_time=datetime(2026, 5, 26, 13, 0, tzinfo=timezone.utc),
                    published_at=datetime(2026, 5, 26, 13, 10, tzinfo=timezone.utc),
                    summary="板块热度上升",
                ),
                make_item(
                    title="大盘新闻",
                    item_type="market_news",
                    event_time=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
                    published_at=datetime(2026, 5, 26, 12, 10, tzinfo=timezone.utc),
                    summary="市场热点新闻",
                ),
                make_item(
                    title="未知信息",
                    item_type="custom_item",
                    event_time=datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc),
                ),
            ],
        )

        package = MacroEvidencePreprocessor().build(
            pack,
            as_of=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(package.market, "A")
        self.assertEqual(package.code, "SZ.000001")
        self.assertEqual([row.title for row in package.company_events], ["较新风险提示", "较早利好订单"])
        self.assertEqual([row.title for row in package.hot_sectors], ["AI 板块走强"])
        self.assertEqual([row.title for row in package.market_hot_news], ["大盘新闻"])
        self.assertEqual([row.title for row in package.other_items], ["未知信息"])
        self.assertTrue(package.has_scoreable_evidence)

    def test_detects_newer_negative_event_reversing_older_positive_event(self):
        pack = EvidencePack(
            market="A",
            code="SZ.000001",
            structured_items=[
                make_item(
                    title="公司中标新订单",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc),
                    summary="订单增长形成利好",
                ),
                make_item(
                    title="公司提示交付风险",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
                    summary="订单延期风险上升",
                ),
            ],
        )

        package = MacroEvidencePreprocessor().build(
            pack,
            as_of=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(package.temporal_findings), 1)
        finding = package.temporal_findings[0]
        self.assertEqual(finding.type, "newer_event_reverses_older_signal")
        self.assertEqual(finding.older_evidence_title, "公司中标新订单")
        self.assertEqual(finding.newer_evidence_title, "公司提示交付风险")

    def test_detects_reversal_when_newest_event_is_neutral(self):
        pack = EvidencePack(
            market="A",
            code="SZ.000001",
            structured_items=[
                make_item(
                    title="公司发布例行说明",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc),
                    summary="管理层说明经营计划",
                ),
                make_item(
                    title="公司提示履约风险",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
                    summary="订单延期风险增加",
                ),
                make_item(
                    title="公司获得增长订单",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc),
                    summary="订单增长形成利好",
                ),
            ],
        )

        package = MacroEvidencePreprocessor().build(
            pack,
            as_of=datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
        )

        reversals = [
            finding for finding in package.temporal_findings
            if finding.type == "newer_event_reverses_older_signal"
        ]
        self.assertEqual(len(reversals), 1)
        self.assertEqual(reversals[0].older_evidence_title, "公司获得增长订单")
        self.assertEqual(reversals[0].newer_evidence_title, "公司提示履约风险")

    def test_detects_confirmation_when_newest_event_is_neutral(self):
        pack = EvidencePack(
            market="A",
            code="SZ.000001",
            structured_items=[
                make_item(
                    title="公司发布例行说明",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc),
                    summary="管理层说明经营计划",
                ),
                make_item(
                    title="公司上调盈利预期",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 14, 0, tzinfo=timezone.utc),
                    summary="盈利增长",
                ),
                make_item(
                    title="公司获得新订单",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc),
                    summary="订单增长形成利好",
                ),
            ],
        )

        package = MacroEvidencePreprocessor().build(
            pack,
            as_of=datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
        )

        confirmations = [
            finding for finding in package.temporal_findings
            if finding.type == "newer_event_confirms_older_signal"
        ]
        self.assertEqual(len(confirmations), 1)
        self.assertEqual(confirmations[0].older_evidence_title, "公司获得新订单")
        self.assertEqual(confirmations[0].newer_evidence_title, "公司上调盈利预期")

    def test_computes_age_hours_with_timezone_normalization(self):
        pack = EvidencePack(
            market="US",
            code="AAPL",
            structured_items=[
                make_item(
                    title="Naive published item",
                    item_type="financial",
                    event_time=None,
                    published_at=datetime(2026, 5, 26, 12, 30),
                ),
                make_item(
                    title="Aware event item",
                    item_type="announcement",
                    event_time=datetime(2026, 5, 26, 18, 0, tzinfo=timezone(timedelta(hours=8))),
                    published_at=datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc),
                ),
            ],
        )

        package = MacroEvidencePreprocessor().build(
            pack,
            as_of=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc),
        )

        ages_by_title = {row.title: row.age_hours for row in package.company_events}
        self.assertEqual(ages_by_title["Naive published item"], 2.5)
        self.assertEqual(ages_by_title["Aware event item"], 5.0)

    def test_missing_time_creates_data_gap(self):
        no_time = make_item(
            title="无时间新闻",
            item_type="market_news",
            event_time=None,
            published_at=None,
        )
        no_fetch = make_item(
            title="无抓取时间",
            item_type="announcement",
            event_time=datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc),
            published_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc),
            fetched_at=None,
        )
        pack = EvidencePack(
            market="A",
            code="SZ.000001",
            structured_items=[no_time, no_fetch],
            data_gaps=["upstream gap"],
        )

        package = MacroEvidencePreprocessor().build(
            pack,
            as_of=datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc),
        )

        self.assertIn("upstream gap", package.data_gaps)
        self.assertIn("无时间新闻 缺少 event_time/published_at", package.data_gaps)
        self.assertIn("无抓取时间 缺少 fetched_at", package.data_gaps)


class MacroScoreParserTests(unittest.TestCase):
    def test_parser_clamps_values_and_recomputes_passed(self):
        result = MacroScoreParser.parse(
            {
                "macro_score": 120,
                "passed": False,
                "sub_scores": {
                    "company_event_strength": 130,
                    "sector_heat": -150,
                },
                "weighted_contribution": {
                    "company_event_strength": "12.5",
                },
                "summary": "strong setup",
                "temporal_summary": "newer evidence confirms",
                "risks": ["crowded", 123],
                "evidence_refs": [{"title": "公告"}, "bad-ref"],
            },
            threshold=70,
        )

        self.assertEqual(result.macro_score, 100.0)
        self.assertTrue(result.passed)
        self.assertEqual(result.sub_scores["company_event_strength"], 100.0)
        self.assertEqual(result.sub_scores["sector_heat"], -100.0)
        self.assertEqual(result.weighted_contribution["company_event_strength"], 12.5)
        self.assertEqual(result.risks, ["crowded", "123"])
        self.assertEqual(result.evidence_refs, [{"title": "公告"}])
        self.assertEqual(result.to_details()["macro_score"], 100.0)

    def test_parser_defaults_missing_subscore_keys_to_zero(self):
        result = MacroScoreParser.parse(
            {
                "macro_score": 10,
                "sub_scores": {
                    "company_event_strength": 20,
                },
            },
            threshold=30,
        )

        self.assertFalse(result.passed)
        self.assertEqual(set(DEFAULT_MACRO_SUB_WEIGHTS).difference(result.sub_scores), set())
        self.assertEqual(result.sub_scores["company_event_strength"], 20.0)
        self.assertEqual(result.sub_scores["news_validation"], 0.0)
        self.assertEqual(result.sub_scores["freshness"], 0.0)

    def test_parser_treats_non_finite_macro_score_as_zero(self):
        result = MacroScoreParser.parse({"macro_score": "nan"}, threshold=70)

        self.assertEqual(result.macro_score, 0.0)
        self.assertFalse(result.passed)

    def test_parser_invalid_nan_macro_score_does_not_pass_zero_threshold(self):
        result = MacroScoreParser.parse({"macro_score": "nan"}, threshold=0)

        self.assertEqual(result.macro_score, 0.0)
        self.assertFalse(result.passed)

    def test_parser_invalid_infinite_macro_score_does_not_pass_zero_threshold(self):
        result = MacroScoreParser.parse({"macro_score": float("inf")}, threshold=0)

        self.assertEqual(result.macro_score, 0.0)
        self.assertFalse(result.passed)
        json.dumps(result.to_details(), ensure_ascii=False, allow_nan=False)

    def test_parser_missing_macro_score_does_not_pass_zero_threshold(self):
        result = MacroScoreParser.parse({}, threshold=0)

        self.assertEqual(result.macro_score, 0.0)
        self.assertFalse(result.passed)

    def test_parser_boolean_false_macro_score_does_not_pass_zero_threshold(self):
        result = MacroScoreParser.parse({"macro_score": False}, threshold=0)

        self.assertEqual(result.macro_score, 0.0)
        self.assertFalse(result.passed)

    def test_parser_boolean_true_macro_score_is_sanitized_and_invalid(self):
        result = MacroScoreParser.parse({"macro_score": True}, threshold=0)

        self.assertEqual(result.macro_score, 0.0)
        self.assertFalse(result.passed)

    def test_parser_uses_default_threshold_for_malformed_threshold(self):
        result = MacroScoreParser.parse({"macro_score": 10}, threshold="bad-threshold")

        self.assertEqual(result.threshold, 60.0)
        self.assertFalse(result.passed)

    def test_parser_boolean_threshold_uses_default_threshold(self):
        result = MacroScoreParser.parse({"macro_score": 10}, threshold=False)

        self.assertEqual(result.threshold, 60.0)
        self.assertFalse(result.passed)

    def test_parser_preserves_valid_zero_threshold(self):
        result = MacroScoreParser.parse({"macro_score": 0}, threshold=0)

        self.assertEqual(result.threshold, 0.0)
        self.assertTrue(result.passed)

    def test_parser_treats_non_finite_subscores_as_zero(self):
        result = MacroScoreParser.parse(
            {
                "macro_score": 10,
                "sub_scores": {
                    "company_event_strength": float("inf"),
                    "sector_heat": float("nan"),
                },
            },
            threshold=70,
        )

        self.assertEqual(result.sub_scores["company_event_strength"], 0.0)
        self.assertEqual(result.sub_scores["sector_heat"], 0.0)

    def test_parser_treats_boolean_subscores_as_zero(self):
        result = MacroScoreParser.parse(
            {
                "macro_score": 10,
                "sub_scores": {
                    "company_event_strength": True,
                    "sector_heat": False,
                },
            },
            threshold=70,
        )

        self.assertEqual(result.sub_scores["company_event_strength"], 0.0)
        self.assertEqual(result.sub_scores["sector_heat"], 0.0)

    def test_parser_rejects_non_dict_payload(self):
        with self.assertRaises(ValueError):
            MacroScoreParser.parse(["not", "a", "dict"], threshold=60)

    def test_to_details_is_json_safe_for_nested_evidence_datetimes(self):
        published_at = datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc)
        result = MacroScoreParser.parse(
            {
                "macro_score": 80,
                "evidence_refs": [
                    {
                        "title": "公告",
                        "published_at": published_at,
                        "tags": ("订单", "增长"),
                        "quality": float("nan"),
                        "weight": float("inf"),
                    }
                ],
            },
            threshold=70,
        )

        details = result.to_details()
        json.dumps(details, ensure_ascii=False, allow_nan=False)

        self.assertEqual(
            details["evidence_refs"][0]["published_at"],
            "2026-05-26T09:30:00+00:00",
        )
        self.assertEqual(details["evidence_refs"][0]["tags"], ["订单", "增长"])
        self.assertIsNone(details["evidence_refs"][0]["quality"])
        self.assertIsNone(details["evidence_refs"][0]["weight"])

    def test_to_details_includes_json_safe_raw_payload(self):
        published_at = datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc)
        result = MacroScoreParser.parse(
            {
                "macro_score": 80,
                "raw_event": {
                    "published_at": published_at,
                    "quality": float("nan"),
                },
            },
            threshold=70,
        )

        details = result.to_details()
        json.dumps(details, ensure_ascii=False, allow_nan=False)

        self.assertEqual(
            details["raw"]["raw_event"]["published_at"],
            "2026-05-26T09:30:00+00:00",
        )
        self.assertIsNone(details["raw"]["raw_event"]["quality"])

    def test_to_details_handles_cyclic_evidence_refs(self):
        cyclic_ref = {"title": "循环证据"}
        cyclic_ref["self"] = cyclic_ref
        result = MacroScoreParser.parse(
            {
                "macro_score": 80,
                "evidence_refs": [cyclic_ref],
            },
            threshold=70,
        )

        details = result.to_details()
        json.dumps(details, ensure_ascii=False, allow_nan=False)

        self.assertEqual(details["evidence_refs"][0]["self"]["self"], "<cycle>")

    def test_to_details_handles_deeply_nested_raw_payload(self):
        nested = "leaf"
        for _ in range(300):
            nested = {"child": nested}
        result = MacroScoreParser.parse(
            {
                "macro_score": 80,
                "raw_event": nested,
            },
            threshold=70,
        )

        details = result.to_details()
        json.dumps(details, ensure_ascii=False, allow_nan=False)

        cursor = details["raw"]["raw_event"]
        for _ in range(100):
            cursor = cursor["child"]
        self.assertEqual(cursor, "<max_depth>")


class AggregateRuleScoresTests(unittest.TestCase):
    def test_aggregate_combines_technical_and_macro_scores(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
                {"rule_type": "strategy", "strategy_category": "technical", "result": "fail", "details": {}},
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 80},
                },
            ]
        )

        self.assertEqual(result["technical_score"], 50.0)
        self.assertEqual(result["macro_score"], 80.0)
        self.assertEqual(result["final_score"], 62.0)
        self.assertEqual(result["technical_weight"], 0.6)
        self.assertEqual(result["macro_weight"], 0.4)

    def test_aggregate_negative_macro_score_reduces_final_score(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "fail",
                    "details": {"macro_score": -50},
                },
            ]
        )

        self.assertEqual(result["technical_score"], 100.0)
        self.assertEqual(result["macro_score"], -50.0)
        self.assertEqual(result["final_score"], 40.0)

    def test_aggregate_uses_default_weights_for_malformed_weights(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 80},
                },
            ],
            technical_weight="bad",
            macro_weight="bad",
        )

        self.assertEqual(result["technical_weight"], 0.6)
        self.assertEqual(result["macro_weight"], 0.4)
        self.assertEqual(result["final_score"], 92.0)

    def test_aggregate_uses_default_weights_for_non_finite_weights(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 80},
                },
            ],
            technical_weight=float("nan"),
            macro_weight=float("inf"),
        )

        self.assertEqual(result["technical_weight"], 0.6)
        self.assertEqual(result["macro_weight"], 0.4)
        self.assertEqual(result["final_score"], 92.0)

    def test_aggregate_uses_default_weights_for_boolean_weights(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 80},
                },
            ],
            technical_weight=False,
            macro_weight=True,
        )

        self.assertEqual(result["technical_weight"], 0.6)
        self.assertEqual(result["macro_weight"], 0.4)
        self.assertEqual(result["final_score"], 92.0)

    def test_aggregate_preserves_valid_zero_weights(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 80},
                },
            ],
            technical_weight=0,
            macro_weight=0,
        )

        self.assertEqual(result["technical_weight"], 0.0)
        self.assertEqual(result["macro_weight"], 0.0)
        self.assertEqual(result["final_score"], 0.0)

    def test_aggregate_rounds_scores_to_two_decimals(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
                {"rule_type": "strategy", "strategy_category": "technical", "result": "fail", "details": {}},
                {"rule_type": "strategy", "strategy_category": "technical", "result": "fail", "details": {}},
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 100},
                },
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "fail",
                    "details": {"macro_score": 0},
                },
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 100},
                },
            ]
        )

        self.assertEqual(result["technical_score"], 33.33)
        self.assertEqual(result["macro_score"], 66.67)
        self.assertEqual(result["final_score"], 46.67)

    def test_aggregate_clamps_final_score_above_upper_bound(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 100},
                },
            ],
            technical_weight=2,
            macro_weight=2,
        )

        self.assertEqual(result["technical_score"], 100.0)
        self.assertEqual(result["macro_score"], 100.0)
        self.assertEqual(result["final_score"], 100.0)

    def test_aggregate_clamps_final_score_below_lower_bound(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "fail", "details": {}},
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "fail",
                    "details": {"macro_score": -100},
                },
            ],
            technical_weight=1,
            macro_weight=2,
        )

        self.assertEqual(result["technical_score"], 0.0)
        self.assertEqual(result["macro_score"], -100.0)
        self.assertEqual(result["final_score"], -100.0)

    def test_aggregate_ignores_macro_skip_and_error_rows(self):
        result = aggregate_rule_scores(
            [
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "skip",
                    "details": {"macro_score": 90},
                },
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "error",
                    "details": {"macro_score": -80},
                },
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 60},
                },
            ]
        )

        self.assertEqual(result["macro_score"], 60.0)
        self.assertEqual(result["final_score"], 60.0)

    def test_aggregate_ignores_boolean_macro_scores(self):
        result = aggregate_rule_scores(
            [
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": True},
                },
                {
                    "rule_type": "strategy",
                    "strategy_category": "macro",
                    "result": "fail",
                    "details": {"macro_score": False},
                },
            ]
        )

        self.assertIsNone(result["macro_score"])
        self.assertIsNone(result["final_score"])

    def test_aggregate_ignores_macro_rows_that_are_not_strategies(self):
        result = aggregate_rule_scores(
            [
                {
                    "rule_type": "filter",
                    "strategy_category": "macro",
                    "result": "pass",
                    "details": {"macro_score": 80},
                }
            ]
        )

        self.assertIsNone(result["macro_score"])
        self.assertIsNone(result["final_score"])

    def test_aggregate_ignores_technical_skip_and_error_rows(self):
        result = aggregate_rule_scores(
            [
                {"rule_type": "strategy", "strategy_category": "technical", "result": "skip", "details": {}},
                {"rule_type": "strategy", "strategy_category": "technical", "result": "error", "details": {}},
                {"rule_type": "strategy", "strategy_category": "technical", "result": "pass", "details": {}},
            ]
        )

        self.assertEqual(result["technical_score"], 100.0)
        self.assertEqual(result["final_score"], 100.0)


if __name__ == "__main__":
    unittest.main()
