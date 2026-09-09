import unittest
from unittest.mock import patch
from custom_list import (
    CustomListCodeParser,
    CustomListJobService,
    STATUS_TEXT_DUPLICATE,
    STATUS_TEXT_FAILED,
    STATUS_TEXT_INVALID,
    STATUS_TEXT_PASSED,
)


class CustomListTests(unittest.TestCase):
    def test_code_parser_normalizes_and_classifies_inputs(self):
        parsed = CustomListCodeParser().parse("HK", ["700", "HK.00700", "US.AAPL", ""])

        self.assertEqual(parsed.valid_codes, ["HK.00700"])
        self.assertEqual(parsed.duplicate_inputs[0]["状态"], STATUS_TEXT_DUPLICATE)
        self.assertEqual(parsed.invalid_inputs[0]["状态"], STATUS_TEXT_INVALID)
        self.assertEqual(parsed.invalid_inputs[0]["reason"], "代码市场不匹配")
        self.assertEqual(parsed.input_summary["输入数量"], 4)
        self.assertEqual(parsed.input_summary["有效代码数"], 1)
        self.assertEqual(parsed.input_summary["重复代码数"], 1)
        self.assertEqual(parsed.input_summary["无效代码数"], 2)

    def test_code_parser_supports_a_and_us_market_formats(self):
        a_parsed = CustomListCodeParser().parse("A", ["600519", "000001.SZ", "BJ.430047"])
        us_parsed = CustomListCodeParser().parse("US", ["AAPL", "US.MSFT", "TSLA.US"])

        self.assertEqual(a_parsed.valid_codes, ["SH.600519", "SZ.000001", "BJ.430047"])
        self.assertEqual(us_parsed.valid_codes, ["US.AAPL", "US.MSFT", "US.TSLA"])

    def test_job_service_creates_backend_custom_job_with_task_id(self):
        class FakeDB:
            def __init__(self):
                self.created = None
                self.updated_job = None
                self.created_task = None

            def init_schema(self, timeframe="1d"):
                self.schema_timeframe = timeframe

            def create_web_screening_job(self, **kwargs):
                self.created = kwargs

            def create_screening_task(self, **kwargs):
                self.created_task = kwargs

            def update_web_screening_job(self, *args, **kwargs):
                self.updated_job = (args, kwargs)

        db = FakeDB()
        parsed = CustomListCodeParser().parse("HK", ["700", "9988"])
        response = CustomListJobService(db).create_job(
            user_id=7,
            market="HK",
            timeframe="1d",
            chain={"chain_key": "default", "chain_name": "默认链"},
            parse_result=parsed,
            enable_ai_analysis=True,
            send_feishu=False,
        )

        self.assertEqual(response["status"], "running")
        self.assertEqual(response["runner"], "web_backend")
        self.assertEqual(response["market"], "HK")
        self.assertTrue(response["task_id"])
        self.assertEqual(db.created["markets"], ["HK"])
        self.assertEqual(db.created["execution_mode"], "web_backend")
        options = db.created["options"]
        self.assertEqual(options["job_kind"], "custom_list")
        self.assertEqual(options["result_upload_scope"], "all")
        self.assertEqual(options["task_id"], response["task_id"])
        self.assertEqual(options["normalized_codes"], ["HK.00700", "HK.09988"])
        self.assertEqual(options["watchlist_by_market"]["HK"][0]["code"], "HK.00700")
        self.assertEqual(db.created_task["task_id"], response["task_id"])
        self.assertEqual(db.created_task["total_count"], 2)
        self.assertEqual(db.updated_job[0][1], "running")
        self.assertEqual(db.updated_job[1]["task_ids"], [response["task_id"]])

    def test_custom_list_runner_reuses_backend_task_id(self):
        from custom_list import CustomListScreeningRunner

        class FakeDB:
            instances = []

            def __init__(self, mysql_config):
                self.created_tasks = []
                FakeDB.instances.append(self)

            def init_schema(self, timeframe="1d"):
                pass

            def create_screening_task(self, **kwargs):
                self.created_tasks.append(kwargs)

            def close(self):
                pass

        class FakeProcessor:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def process(self, **kwargs):
                return []

        job = {
            "job_id": "job-1",
            "markets": ["HK"],
            "timeframe": "1d",
            "options": {
                "task_id": "task-existing",
                "chain_key": "default",
                "watchlist_by_market": {"HK": [{"code": "HK.00700", "name": "腾讯控股"}]},
            },
        }

        with patch("custom_list.MarketDatabase", FakeDB), \
                patch("custom_list.run_screening_task") as run_task, \
                patch("custom_list.load_passed_screening_records", return_value=[]), \
                patch("custom_list.ScreeningPostProcessor", FakeProcessor):
            result = CustomListScreeningRunner(object()).run(
                job=job,
                csv_base="logs/test",
                today_str="2026-05-25",
                enable_ai_analysis=False,
            )

        self.assertEqual(result.task_id, "task-existing")
        self.assertEqual(FakeDB.instances, [])
        run_task.assert_called_once()
        self.assertEqual(run_task.call_args.kwargs["task_id"], "task-existing")
        self.assertEqual(run_task.call_args.kwargs["watchlist"][0]["code"], "HK.00700")

    def test_job_service_builds_ordered_chinese_status_results(self):
        class FakeDB:
            def get_screening_results_by_task(self, task_id, limit=500, offset=0, passed_only=False):
                if offset:
                    return []
                return [
                    {"code": "HK.00700", "is_passed": True, "name": "腾讯控股"},
                    {"code": "HK.09988", "is_passed": False, "name": "阿里巴巴-W"},
                ]

        job = {
            "job_id": "job-1",
            "status": "completed",
            "markets": ["HK"],
            "timeframe": "1d",
            "task_ids": ["task-1"],
            "options": {
                "job_kind": "custom_list",
                "result_upload_scope": "all",
                "chain_key": "default",
                "chain_name": "默认链",
                "input_summary": {"输入数量": 4, "有效代码数": 2, "无效代码数": 1, "重复代码数": 1},
                "input_status_rows": [
                    {"index": 0, "input": "700", "code": "HK.00700", "status": "valid", "状态": "等待筛选"},
                    {"index": 1, "input": "9988", "code": "HK.09988", "status": "valid", "状态": "等待筛选"},
                    {"index": 2, "input": "US.AAPL", "code": None, "status": "invalid", "状态": STATUS_TEXT_INVALID},
                    {"index": 3, "input": "00700", "code": "HK.00700", "status": "duplicate", "状态": STATUS_TEXT_DUPLICATE},
                ],
            },
        }

        result = CustomListJobService(FakeDB()).build_results(job)

        self.assertEqual([row["状态"] for row in result["rows"]], [
            STATUS_TEXT_PASSED,
            STATUS_TEXT_FAILED,
            STATUS_TEXT_INVALID,
            STATUS_TEXT_DUPLICATE,
        ])
        self.assertEqual(result["input_summary"]["通过数量"], 1)
        self.assertEqual(result["input_summary"]["未通过数量"], 1)

    def test_job_service_adds_report_sections_for_failed_result(self):
        class FakeDB:
            def get_screening_results_by_task(self, task_id, limit=500, offset=0, passed_only=False):
                if offset:
                    return []
                return [{
                    "code": "US.MSFT",
                    "name": "Microsoft",
                    "is_passed": False,
                    "filter_summary": "未通过 2 条规则",
                    "technical_score": 0.0,
                    "macro_score": 58.0,
                    "final_score": 23.2,
                    "score_details": {
                        "macro_details": {
                            "macro_score": 58,
                            "threshold": 60,
                            "summary": "宏观分数未达阈值",
                            "temporal_summary": "近期证据偏旧，可信度下调",
                            "sub_scores": {"news_validation": 45, "freshness": 35},
                            "risks": ["新闻验证不足"],
                            "evidence_refs": [{"label": "Reuters", "url": "https://example.com/news"}],
                        }
                    },
                    "filter_details": [
                        {
                            "rule_key": "zuoyi_signal",
                            "filter_name": "ZuoYiStrategizer",
                            "result": "fail",
                            "reason": "左一战法形态未出现",
                            "details": {"latest_close": 418.57, "left_one_count": 0},
                        },
                        {
                            "rule_key": "market_intel_macro_score",
                            "filter_name": "MarketIntelMacroScoreRule",
                            "result": "fail",
                            "reason": "宏观评分未达标",
                            "details": {
                                "macro_score": 58,
                                "threshold": 60,
                                "summary": "宏观分数未达阈值",
                                "temporal_summary": "近期证据偏旧，可信度下调",
                            },
                        },
                    ],
                }]

        job = {
            "job_id": "job-1",
            "status": "completed",
            "markets": ["US"],
            "timeframe": "1d",
            "task_ids": ["task-1"],
            "options": {
                "job_kind": "custom_list",
                "result_upload_scope": "all",
                "chain_key": "default",
                "chain_name": "默认链",
                "input_summary": {"输入数量": 1, "有效代码数": 1, "无效代码数": 0, "重复代码数": 0},
                "input_status_rows": [
                    {"index": 0, "input": "MSFT", "code": "US.MSFT", "status": "valid", "状态": "等待筛选"},
                ],
            },
        }

        row = CustomListJobService(FakeDB()).build_results(job)["rows"][0]
        sections = {section["section_key"]: section for section in row["report_sections"]}

        self.assertIn("decision", sections)
        self.assertIn("failure_reasons", sections)
        self.assertIn("strategy_process", sections)
        self.assertIn("score_breakdown", sections)
        self.assertIn("macro_evidence", sections)
        self.assertEqual(sections["decision"]["items"][0]["value"], "未通过")
        self.assertEqual(sections["failure_reasons"]["items"][0]["reason"], "左一战法形态未出现")
        self.assertEqual(sections["strategy_process"]["items"][1]["title"], "market_intel_macro_score")
        self.assertIn("宏观分数未达阈值", sections["score_breakdown"]["summary"])
        self.assertEqual(sections["macro_evidence"]["items"][0]["label"], "证据时效")


if __name__ == "__main__":
    unittest.main()
