import unittest

from db import hash_password, verify_password
from kline_fetcher import DatabaseKlineFetcher, KlineFetcherFactory
from web.business import BusinessError
from web.rate_limit import InMemorySlidingWindowRateLimiter, RateLimitRule, rate_limiter
from web.rule_chains import resolve_rule_chain
from signal_analysis.models import SignalAnalysisResult
from web.single_stock import _analysis_to_response_dict, _load_stock_info, normalize_stock_code
from web.validation import (
    validate_agent_artifact_size,
    validate_agent_bulk_size,
    validate_agent_json_payload_size,
    validate_markets,
    validate_timeframe,
)


class WebPlatformTests(unittest.TestCase):
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

    def test_fetcher_chain_puts_database_cache_first_when_db_is_provided(self):
        class FakeDB:
            pass

        fetchers = KlineFetcherFactory.create_fetcher_chain(db=FakeDB())
        self.assertGreater(len(fetchers), 0)
        self.assertIsInstance(fetchers[0], DatabaseKlineFetcher)

    def test_validate_screening_task_write_inputs(self):
        self.assertEqual(validate_markets(["hk", "US", "HK"]), ["HK", "US"])
        self.assertEqual(validate_timeframe("1d"), "1d")
        with self.assertRaisesRegex(ValueError, "至少选择一个市场"):
            validate_markets([])
        with self.assertRaisesRegex(ValueError, "不支持的市场"):
            validate_markets(["CN"])
        with self.assertRaisesRegex(ValueError, "不支持的周期"):
            validate_timeframe("2d")

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
