from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from signal_analysis.models import ScreeningSignalRow, SignalAnalysisResult, compute_unified_score
from signal_analysis.evidence import build_data_gaps


class NoMainForceSelectionTest(unittest.TestCase):
    def test_main_force_values_do_not_change_final_score_or_ranking(self):
        result = SignalAnalysisResult(code="000001", name="样本", reliability_score=80, confidence_score=80)
        row = ScreeningSignalRow.from_csv_row({"股票代码": "000001", "市场": "A", "主力风险分": "0", "主力流出风险": "低"}, 0, "A")
        high_risk = replace(row, main_force_risk_score="100", main_force_risk_level="高")
        missing = replace(row, main_force_risk_score="", main_force_risk_level="")
        scores = [compute_unified_score(result, item) for item in (row, high_risk, missing)]
        self.assertEqual(len({score.final_score for score in scores}), 1)
        self.assertNotIn("资金风险", scores[0].formula)
        self.assertNotIn("资金风险分", scores[0].to_csv_columns())

    def test_main_force_fields_do_not_enter_ai_payload(self):
        row = ScreeningSignalRow.from_csv_row({"股票代码": "000001", "市场": "A", "主力风险分": "100", "主力风险说明": "严重流出"}, 0, "A")
        self.assertFalse(any(key.startswith("main_force") for key in row.to_prompt_dict()))

    def test_missing_main_force_is_not_an_evidence_gap(self):
        gaps = build_data_gaps(None, SignalAnalysisResult(code="000001", name="样本"), [], {})
        self.assertFalse(any("主力" in item or "盘口" in item for item in gaps))

    def test_postprocessor_never_fetches_main_force_data(self):
        import scheduled_daily_job as job
        processor = job.ScreeningPostProcessor(mysql_config=object(), csv_base="/tmp/no-main-force.csv",
            today_str="2026-10-19", enable_ai_analysis=False)
        with patch.object(job, "enrich_records_with_names"), patch.object(job, "enrich_records_with_sectors"), \
             patch.object(job, "get_market_etf_codes", return_value=set()), \
             patch.object(job, "write_screening_csv") as write, \
             patch.object(job, "enrich_records_with_main_force_risks", side_effect=AssertionError("主力数据被调用")):
            processor.process("US", "1d", "test", [{"code": "US.TEST"}])
        write.assert_called_once()

    def test_old_ai_cache_is_not_reused_and_news_cache_stays_reusable(self):
        from signal_analysis.service import MySqlSignalAnalysisRepository, selection_cache_profile
        db = Mock()
        row = {"analysis_profile": "default", "code": "000001"}
        with patch("signal_analysis.service.MarketDatabase", return_value=db):
            repo = MySqlSignalAnalysisRepository(object())
            repo.save_results([row])
            repo.get_signal_analysis_cache("A", "000001", "1d", "default", None)
        saved = db.upsert_signal_analysis_cache.call_args.args[0][0]
        self.assertEqual(saved["analysis_profile"], selection_cache_profile("default"))
        self.assertNotEqual(saved["analysis_profile"], "default")
        self.assertEqual(db.get_signal_analysis_cache.call_args.kwargs["analysis_profile"], saved["analysis_profile"])
        self.assertEqual(db.upsert_company_news_cache.call_args.args[0], [row])


if __name__ == "__main__":
    unittest.main()
