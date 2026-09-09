import unittest
from pathlib import Path
from unittest import mock

from db import hash_password, verify_password
from kline_fetcher import KlineFetcherFactory
from web.business import BusinessError
from web.auth import CurrentUser
from web.main import BulkStockPoolRequest, ScreeningTaskRequest, bulk_stock_pool, create_screening_task, get_screening_job
from web.rate_limit import InMemorySlidingWindowRateLimiter, RateLimitRule, rate_limiter
from web.rule_chains import parse_rule_expression, resolve_rule_chain, validate_rule_expression_against_market
from signal_analysis.models import SignalAnalysisResult
from web.single_stock import _analysis_to_response_dict, _load_stock_info, normalize_stock_code
from web.validation import (
    validate_agent_artifact_size,
    validate_agent_bulk_size,
    validate_agent_json_payload_size,
    validate_markets,
    validate_rule_chain_key,
    validate_rule_chain_timeframe,
    validate_timeframe,
)


class WebPlatformTests(unittest.TestCase):
    def test_single_stock_wires_signal_and_market_intel_runtime_gates(self):
        content = (Path(__file__).resolve().parents[1] / "web" / "single_stock.py").read_text(encoding="utf-8")

        self.assertIn("requires_signal_analysis()", content)
        self.assertIn("requires_market_intel_macro_score()", content)
        self.assertIn("build_market_intel_service(mysql_config, enabled=True)", content)
        self.assertIn("market_intel_service", content)
        self.assertIn("macro_score_scorer", content)

    def test_screening_results_api_exposes_score_fields(self):
        db_source = (Path(__file__).resolve().parents[1] / "db.py").read_text(encoding="utf-8")
        main_source = (Path(__file__).resolve().parents[1] / "web" / "main.py").read_text(encoding="utf-8")

        for field in ("technical_score", "macro_score", "final_score", "score_details"):
            self.assertIn(field, db_source)
            self.assertIn(field, main_source)

    def test_password_hash_round_trip(self):
        hashed = hash_password("secret-password")
        self.assertTrue(verify_password("secret-password", hashed))
        self.assertFalse(verify_password("wrong", hashed))
        self.assertFalse(verify_password("secret-password", "invalid"))

    def test_normalize_stock_code_requires_market_context(self):
        self.assertEqual(normalize_stock_code("HK", "700"), "HK.00700")
        self.assertEqual(normalize_stock_code("HK", "HK.00700"), "HK.00700")
        self.assertEqual(normalize_stock_code("US", "AAPL"), "US.AAPL")
        self.assertEqual(normalize_stock_code("US", "US.MSFT"), "US.MSFT")
        self.assertEqual(normalize_stock_code("A", "600519"), "SH.600519")
        self.assertEqual(normalize_stock_code("A", "000001"), "SZ.000001")
        self.assertEqual(normalize_stock_code("A", "688001"), "SH.688001")

    def test_fetcher_chain_never_includes_database_cache(self):
        fetchers = KlineFetcherFactory.create_fetcher_chain()
        self.assertGreater(len(fetchers), 0)
        self.assertNotIn("DatabaseKlineCache", [fetcher.get_name() for fetcher in fetchers])

    def test_validate_screening_task_write_inputs(self):
        self.assertEqual(validate_markets(["hk", "US", "HK"]), ["HK", "US"])
        self.assertEqual(validate_timeframe("1d"), "1d")
        self.assertEqual(validate_rule_chain_timeframe("*"), "*")
        self.assertEqual(validate_rule_chain_key("macro_only_chain"), "macro_only_chain")
        with self.assertRaisesRegex(ValueError, "至少选择一个市场"):
            validate_markets([])
        with self.assertRaisesRegex(ValueError, "不支持的市场"):
            validate_markets(["CN"])
        with self.assertRaisesRegex(ValueError, "不支持的周期"):
            validate_timeframe("2d")
        with self.assertRaisesRegex(ValueError, "仅支持小写字母"):
            validate_rule_chain_key("Bad-Key")

    def test_create_screening_task_stores_selected_pool_types(self):
        class FakeBackgroundTasks:
            def __init__(self):
                self.tasks = []

            def add_task(self, fn, *args, **kwargs):
                self.tasks.append((fn, args, kwargs))

        class FakeDB:
            def __init__(self):
                self.created_job = None
                self.created_locks = None

            def get_screening_run_locks(self, run_date, markets, timeframe, chain_key=None, pool_scope=None):
                return []

            def create_web_screening_job(self, job_id, user_id, markets, timeframe, options, execution_mode="web_backend"):
                self.created_job = {
                    "job_id": job_id,
                    "user_id": user_id,
                    "markets": markets,
                    "timeframe": timeframe,
                    "options": options,
                    "execution_mode": execution_mode,
                }

            def create_screening_run_locks(self, **kwargs):
                self.created_locks = kwargs

        db = FakeDB()
        background = FakeBackgroundTasks()
        payload = ScreeningTaskRequest(
            markets=["HK"],
            timeframe="1d",
            pool_types=["major_index", "all_etf"],
            enable_ai_analysis=False,
        )

        with mock.patch("web.main.resolve_rule_chain", return_value={
            "chain_key": "default_zuoyi_and_other",
            "chain_timeframe": "*",
            "chain_name": "默认链",
        }):
            result = create_screening_task(
                payload,
                background,
                CurrentUser(id=7, username="tester", role="admin"),
                db,
            )

        self.assertEqual(result["runner"], "web_backend")
        self.assertEqual(result["pool_types"], ["major_index", "all_etf"])
        self.assertEqual(db.created_job["execution_mode"], "web_backend")
        self.assertEqual(db.created_job["options"]["pool_types"], ["major_index", "all_etf"])
        self.assertEqual(db.created_locks["pool_scope"], "major_index,all_etf")
        self.assertEqual(len(background.tasks), 1)
        self.assertEqual(background.tasks[0][1][2], ["HK"])

    def test_create_screening_task_checks_artifact_directory_before_persisting_job(self):
        class FakeBackgroundTasks:
            def add_task(self, *args, **kwargs):
                raise AssertionError("background task must not be scheduled")

        class FakeDB:
            def __init__(self):
                self.created_job = False
                self.created_locks = False

            def get_screening_run_locks(self, *args, **kwargs):
                return []

            def create_web_screening_job(self, *args, **kwargs):
                self.created_job = True

            def create_screening_run_locks(self, **kwargs):
                self.created_locks = True

        db = FakeDB()
        with mock.patch("web.main.resolve_rule_chain", return_value={
            "chain_key": "default_zuoyi_and_other",
            "chain_timeframe": "*",
            "chain_name": "默认链",
        }), mock.patch("web.main.artifact_dir", side_effect=OSError("只读目录")):
            with self.assertRaisesRegex(OSError, "只读目录"):
                create_screening_task(
                    ScreeningTaskRequest(markets=["HK"], pool_types=["best"]),
                    FakeBackgroundTasks(),
                    CurrentUser(id=801, username="artifact-test", role="admin"),
                    db,
                )

        self.assertFalse(db.created_job)
        self.assertFalse(db.created_locks)

    def test_web_job_records_market_task_id_before_market_screening_finishes(self):
        from types import SimpleNamespace
        from web.jobs import run_web_screening_job

        events = []

        class FakeDB:
            def __init__(self, _config):
                pass

            def init_web_schema(self):
                pass

            def update_web_screening_job(self, _job_id, status, task_ids=None, **_kwargs):
                events.append(("job_updated", status, task_ids))

            def close(self):
                pass

        def run_market(**kwargs):
            events.append(("market_started",))
            kwargs["on_task_created"]("task-hk-1")
            events.append(("market_finishing",))
            return SimpleNamespace(task_id="task-hk-1", csv_paths=[], market="HK", passed=[])

        with mock.patch("web.jobs.MarketDatabase", FakeDB), \
             mock.patch("web.jobs.get_default_screening_params", return_value={}), \
             mock.patch("web.jobs.run_market_screening_worker", side_effect=run_market), \
             mock.patch("web.jobs._record_artifacts"):
            run_web_screening_job(
                mysql_config=object(), job_id="job-1", markets=["HK"], timeframe="1d",
                artifact_root="/tmp/moneymanager-test-artifacts", enable_ai_analysis=False,
            )

        callback_update = ("job_updated", "running", ["task-hk-1"])
        self.assertIn(callback_update, events)
        self.assertLess(events.index(callback_update), events.index(("market_finishing",)))

    def test_screening_job_uses_task_item_summary_when_task_counter_is_missing(self):
        class FakeDB:
            def get_web_screening_job(self, job_id):
                return {"job_id": job_id, "task_ids": ["task-hk-1"]}

            def get_task_by_id(self, task_id):
                return {
                    "task_id": task_id,
                    "market": "HK",
                    "total_count": 401,
                    "completed_count": 401,
                    "passed_count": None,
                }

            def summarize_screening_task_items(self, task_ids):
                if task_ids != ["task-hk-1"]:
                    raise AssertionError("expected the linked market task")
                return {"total_count": 401, "completed_count": 401, "passed_count": 24, "error_count": 0}

        result = get_screening_job("job-hk-1", None, FakeDB())

        self.assertEqual(result["total_count"], 401)
        self.assertEqual(result["completed_count"], 401)
        self.assertEqual(result["passed_count"], 24)
        self.assertEqual(result["error_count"], 0)

    def test_rule_chain_expression_validation_checks_shape_and_refs(self):
        class FakeDB:
            def get_screening_rule_metadata(self, market):
                return [
                    {
                        "market": market,
                        "rule_key": "company_event_hot_news_link",
                        "rule_name": "公司时事与热点新闻关联",
                        "rule_type": "strategy",
                        "strategy_category": "macro",
                        "implementation": "CompanyEventHotNewsStrategizer",
                        "params_json": {},
                        "enabled": True,
                        "display_order": 220,
                        "description": "",
                    }
                ]

        expression = parse_rule_expression('{"ref":"company_event_hot_news_link"}')
        self.assertEqual(
            validate_rule_expression_against_market(FakeDB(), "HK", expression),
            {"ref": "company_event_hot_news_link"},
        )
        with self.assertRaises(BusinessError) as missing:
            validate_rule_expression_against_market(FakeDB(), "HK", {"ref": "missing_rule"})
        self.assertEqual(missing.exception.error_code, "INVALID_RULE_CHAIN_EXPRESSION")
        with self.assertRaises(BusinessError):
            parse_rule_expression('{"ref": ""}')

    def test_single_stock_info_enriches_from_pool_and_sector_membership(self):
        class FakeDB:
            def get_stocks_by_codes(self, market, codes, include_fundamentals=True):
                return []

            def get_stock_pool_records_by_codes(self, market, codes):
                return [{
                    "pool_type": "best",
                    "code": codes[0],
                    "name": "Pool Name",
                    "market_cap": 123.4,
                    "pe_ratio": 18.5,
                    "industry_name": "Biotechnology",
                }]

            def get_sector_memberships_by_codes(self, market, codes):
                return {
                    codes[0]: [
                        {"sector_type": "sector", "sector_name": "Health Care"},
                        {"sector_type": "industry", "sector_name": "Pharmaceuticals"},
                    ]
                }

        stock = _load_stock_info(FakeDB(), "US", "US.TEST")

        self.assertEqual(stock.name, "Pool Name")
        self.assertEqual(stock.market_cap, 123.4)
        self.assertEqual(stock.pe_ratio, 18.5)
        self.assertEqual(stock.sector, "Biotechnology")
        self.assertEqual(stock.industry, "Biotechnology")

    def test_agent_stock_pool_bulk_upsert_rejects_legacy_pool_type(self):
        class FakeDB:
            def __init__(self):
                self.calls = []

            def upsert_stock_pool(self, market, pool_type, rows):
                self.calls.append((market, pool_type, rows))

        db = FakeDB()
        payload = BulkStockPoolRequest(
            sync_run_id="sync-1",
            market="HK",
            pool_type="index",
            rows=[{"code": "HK.00700"}],
        )

        with self.assertRaisesRegex(Exception, "无效股票池类型"):
            bulk_stock_pool(payload, db)
        self.assertEqual(db.calls, [])

        valid_payload = BulkStockPoolRequest(
            sync_run_id="sync-1",
            market="HK",
            pool_type="major_index",
            rows=[{"code": "HK.00700"}],
        )
        self.assertEqual(bulk_stock_pool(valid_payload, db), {"written": 1})
        self.assertEqual(db.calls[0][1], "major_index")

    def test_single_stock_ai_response_includes_evidence_fields(self):
        response = _analysis_to_response_dict(SignalAnalysisResult(
            code="US.AAPL",
            name="Apple",
            reliability_score=82.0,
            confidence_score=76.0,
            signal_bias="bullish",
            positive_factors=["AI strategy"],
            data_gaps=["主力资金/盘口数据不足"],
            evidence_links=[{
                "label": "SEC",
                "url": "https://www.sec.gov/example",
                "title": "10-K",
                "domain": "www.sec.gov",
                "source_type": "公告",
            }],
            factor_citations={
                "AI strategy": [{
                    "label": "SEC",
                    "url": "https://www.sec.gov/example",
                    "title": "10-K",
                    "domain": "www.sec.gov",
                    "source_type": "公告",
                }]
            },
        ))

        self.assertEqual(response["data_gaps"], ["主力资金/盘口数据不足"])
        self.assertEqual(response["数据缺失原因"], ["主力资金/盘口数据不足"])
        self.assertEqual(response["evidence_links"][0]["label"], "SEC")
        self.assertEqual(response["因素引用"]["AI strategy"][0]["source_type"], "公告")

    def test_business_error_carries_chinese_message_and_retry_after(self):
        error = BusinessError(
            "RATE_LIMIT_CREATE_TASK",
            "筛选任务创建太频繁，请 28 分钟后再试",
            retry_after_seconds=1680,
        )

        self.assertEqual(error.error_code, "RATE_LIMIT_CREATE_TASK")
        self.assertEqual(error.message, "筛选任务创建太频繁，请 28 分钟后再试")
        self.assertEqual(error.retry_after_seconds, 1680)

    def test_in_memory_sliding_window_rate_limiter_blocks_until_window_expires(self):
        now = [1000.0]
        limiter = InMemorySlidingWindowRateLimiter(clock=lambda: now[0])
        rule = RateLimitRule(
            limit=2,
            window_seconds=60,
            error_code="RATE_LIMIT_TEST",
            message_template="测试太频繁，请 {retry_after_text} 后再试",
        )

        self.assertTrue(limiter.check("user:1:test", rule).allowed)
        self.assertTrue(limiter.check("user:1:test", rule).allowed)
        blocked = limiter.check("user:1:test", rule)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.retry_after_seconds, 60)

        now[0] += 61
        self.assertTrue(limiter.check("user:1:test", rule).allowed)

    def test_agent_bulk_size_fails_with_business_error(self):
        rate_limiter.reset()
        try:
            with self.assertRaises(BusinessError) as too_large:
                validate_agent_bulk_size([{} for _ in range(2001)])
            self.assertEqual(too_large.exception.error_code, "AGENT_BULK_TOO_LARGE")
            self.assertIn("2000 行以内", too_large.exception.message)
        finally:
            rate_limiter.reset()

    def test_agent_payload_and_artifact_size_fail_with_business_error(self):
        with self.assertRaises(BusinessError) as json_too_large:
            validate_agent_json_payload_size({"rows": [{"text": "x" * 128}]}, max_bytes=64)
        self.assertEqual(json_too_large.exception.error_code, "AGENT_PAYLOAD_TOO_LARGE")
        self.assertIn("单次推送内容过大", json_too_large.exception.message)

        with self.assertRaises(BusinessError) as artifact_too_large:
            validate_agent_artifact_size(128, max_bytes=64)
        self.assertEqual(artifact_too_large.exception.error_code, "AGENT_ARTIFACT_TOO_LARGE")
        self.assertIn("单个导出文件过大", artifact_too_large.exception.message)

    def test_resolve_rule_chain_allows_disabled_explicit_trial_chain(self):
        class FakeDB:
            def get_active_screening_rule_chain(self, market, timeframe="*"):
                return {
                    "market": market,
                    "timeframe": timeframe,
                    "chain_key": "default_zuoyi_and_other",
                    "chain_name": "默认链",
                    "expression_json": {"ref": "zuoyi_signal"},
                    "enabled": True,
                    "priority": 100,
                    "description": "",
                }

            def get_screening_rule_chain(self, market, chain_key, timeframe="*"):
                if chain_key == "trend_capital_accumulation_watch":
                    return {
                        "market": market,
                        "timeframe": timeframe,
                        "chain_key": chain_key,
                        "chain_name": "趋势主力缩量试跑链",
                        "expression_json": {"ref": "zuoyi_signal"},
                        "enabled": False,
                        "priority": 300,
                        "description": "trial",
                    }
                return None

        chain = resolve_rule_chain(FakeDB(), ["HK", "US"], timeframe="5m", chain_key="trend_capital_accumulation_watch")

        self.assertEqual(chain["chain_key"], "trend_capital_accumulation_watch")
        self.assertEqual(chain["chain_timeframe"], "5m")
        self.assertFalse(chain["enabled"])

    def test_resolve_rule_chain_returns_business_error_for_missing_market_chain(self):
        class FakeDB:
            def get_active_screening_rule_chain(self, market, timeframe="*"):
                return None

            def get_screening_rule_chain(self, market, chain_key, timeframe="*"):
                return None

        with self.assertRaises(BusinessError) as ctx:
            resolve_rule_chain(FakeDB(), ["HK"], timeframe="1d", chain_key="missing_chain")

        self.assertEqual(ctx.exception.error_code, "RULE_CHAIN_NOT_FOUND")
        self.assertIn("不适用于所选市场", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
