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

    def test_parser_rejects_non_dict_payload(self):
        with self.assertRaises(ValueError):
            MacroScoreParser.parse(["not", "a", "dict"], threshold=60)


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


if __name__ == "__main__":
    unittest.main()
