import unittest
from pathlib import Path

from db import MarketDatabase, _decode_json_field, _json_or_none


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.executemany_calls = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, values):
        self.executemany_calls.append((sql, list(values)))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj


class OptionLabDbTests(unittest.TestCase):
    def test_json_helpers_preserve_chinese_keys(self):
        encoded = _json_or_none({"策略名称": "买入看涨期权", "warnings": ["风险提示"]})
        self.assertIn("买入看涨期权", encoded)
        decoded = _decode_json_field(encoded, {})
        self.assertEqual(decoded["策略名称"], "买入看涨期权")

    def test_option_lab_schema_file_mentions_all_tables(self):
        sql_path = Path(__file__).resolve().parents[1] / "sql" / "013_option_lab.sql"
        with sql_path.open("r", encoding="utf-8") as f:
            sql = f.read()

        for table in [
            "option_evaluation_runs",
            "option_evaluation_items",
            "option_strategy_candidates",
            "option_macro_analysis_cache",
            "option_order_plans",
            "option_tracked_positions",
            "option_monitor_events",
            "option_market_snapshots",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)

    def test_init_option_lab_schema_executes_deployment_sql(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.init_option_lab_schema()

        executed_sql = "\n".join(sql for sql, _ in conn.cursor_obj.executed)
        self.assertIn("CREATE TABLE IF NOT EXISTS option_evaluation_runs", executed_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS option_market_snapshots", executed_sql)

    def test_create_run_and_insert_candidates_encode_json_fields(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.create_option_evaluation_run({
            "run_id": "run-1",
            "mode": "single",
            "user_id": 7,
            "market": "US",
            "code": "AAPL",
            "risk_profile": "balanced",
            "request": {"策略名称": "买入看涨期权"},
            "warnings": ["风险提示"],
        })
        db.insert_option_strategy_candidates([
            {
                "candidate_id": "candidate-1",
                "run_id": "run-1",
                "market": "US",
                "code": "AAPL",
                "strategy_key": "long_call",
                "strategy_name": "买入看涨期权",
                "score": 82.5,
                "recommendation_status": "recommended",
                "fit_reason": "正股趋势偏强",
                "contract_details": [{"合约代码": "US.AAPL260619C00200000"}],
                "risk_metrics": {"最大亏损": 525.0},
                "order_suggestion": {"建议限价": 5.25},
                "warnings": ["注意流动性"],
                "data_quality": {"status": "ok"},
                "snapshot_id": "snap-1",
                "宏观分析评分": 80,
                "综合评分": 81.75,
                "宏观方向": "偏多",
                "新闻影响": "利好",
            }
        ])

        run_params = conn.cursor_obj.executed[-1][1]
        self.assertIn("买入看涨期权", run_params[6])
        self.assertIn("风险提示", run_params[8])
        candidate_values = conn.cursor_obj.executemany_calls[-1][1][0]
        self.assertIn("US.AAPL260619C00200000", candidate_values[9])
        self.assertIn("最大亏损", candidate_values[10])
        self.assertIn("宏观分析评分", candidate_values[14])
        self.assertIn("综合评分", candidate_values[14])

    def test_list_candidates_decodes_json_fields(self):
        conn = FakeConnection()
        conn.cursor_obj.rows = [
            (
                "candidate-1", "run-1", "US", "AAPL", "long_call", "买入看涨期权",
                82.5, "recommended", "正股趋势偏强",
                '[{"合约代码":"US.AAPL260619C00200000"}]',
                '{"最大亏损":525.0}',
                '{"建议限价":5.25}',
                '["注意流动性"]',
                '{"status":"ok"}',
                '{"宏观分析评分":80,"综合评分":81.75,"宏观方向":"偏多"}',
                "snap-1",
                "2026-05-16 09:30:00",
            )
        ]
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        rows = db.list_option_strategy_candidates("run-1")

        self.assertEqual(rows[0]["strategy_name"], "买入看涨期权")
        self.assertEqual(rows[0]["contract_details"][0]["合约代码"], "US.AAPL260619C00200000")
        self.assertEqual(rows[0]["risk_metrics"]["最大亏损"], 525.0)
        self.assertEqual(rows[0]["宏观分析评分"], 80)
        self.assertEqual(rows[0]["综合评分"], 81.75)

    def test_macro_analysis_cache_round_trips_json_fields(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.upsert_option_macro_analysis_cache({
            "cache_key": "US:US.AAPL:2026-05-16:default",
            "market": "US",
            "code": "US.AAPL",
            "analysis_profile": "default",
            "macro_score": 80,
            "macro_direction": "偏多",
            "news_impact": "利好",
            "hot_sector_mark": "重点",
            "main_force_risk_level": "低",
            "summary": "宏观层面支持跟踪",
            "positive_factors": ["热点资金关注"],
            "risk_factors": ["估值波动"],
            "macro_factors": ["政策环境中性偏多"],
            "source_urls": ["https://example.com/news"],
            "warnings": ["缓存提示"],
            "provider": "recording",
            "expires_at": "2026-05-16T11:00:00",
            "raw_payload": {"宏观分析评分": 80},
        })

        values = conn.cursor_obj.executed[-1][1]
        self.assertIn("热点资金关注", values[10])
        self.assertIn("缓存提示", values[14])
        self.assertIn("宏观分析评分", values[17])

        conn.cursor_obj.rows = [(
            "US:US.AAPL:2026-05-16:default", "US", "US.AAPL", "default", 80,
            "偏多", "利好", "重点", "低", "宏观层面支持跟踪",
            '["热点资金关注"]',
            '["估值波动"]',
            '["政策环境中性偏多"]',
            '["https://example.com/news"]',
            '["缓存提示"]',
            "recording", "2026-05-16 10:00:00", "2026-05-16T11:00:00",
            '{"宏观分析评分":80}',
        )]

        row = db.get_option_macro_analysis_cache("US:US.AAPL:2026-05-16:default")

        self.assertEqual(row["macro_score"], 80.0)
        self.assertEqual(row["positive_factors"], ["热点资金关注"])
        self.assertEqual(row["raw_payload"]["宏观分析评分"], 80)

    def test_order_position_and_event_methods_use_repository_contract(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.create_option_order_plan({
            "plan_id": "plan-1",
            "candidate_id": "candidate-1",
            "user_id": 7,
            "contract_details": [{"合约代码": "US.AAPL260619C00200000"}],
            "order_suggestion": {"建议限价": 5.25},
        })
        db.create_option_tracked_position({
            "position_id": "position-1",
            "plan_id": "plan-1",
            "user_id": 7,
            "market": "US",
            "code": "AAPL",
            "strategy_name": "买入看涨期权",
            "contract_details": [{"合约代码": "US.AAPL260619C00200000"}],
            "filled_price": 5.2,
            "quantity": 1,
        })
        db.insert_option_monitor_events([
            {
                "event_id": "event-1",
                "position_id": "position-1",
                "severity": "important",
                "event_type": "take_profit_near",
                "message": "接近止盈价",
            }
        ])

        self.assertEqual(len(conn.cursor_obj.executed), 2)
        self.assertEqual(len(conn.cursor_obj.executemany_calls), 1)
        self.assertIn("接近止盈价", conn.cursor_obj.executemany_calls[-1][1][0])

    def test_evaluation_items_preserve_child_run_and_candidates(self):
        conn = FakeConnection()
        db = MarketDatabase.__new__(MarketDatabase)
        db.conn = conn

        db.insert_option_evaluation_items([
            {
                "run_id": "parent-run",
                "child_run_id": "child-run",
                "market": "US",
                "code": "US.AAPL",
                "status": "completed",
                "best_strategy": {"策略名称": "买入看涨期权"},
                "candidates": [{"candidate_id": "candidate-1"}],
            }
        ])

        values = conn.cursor_obj.executemany_calls[-1][1][0]
        self.assertIn("child-run", values[5])
        self.assertIn("candidate-1", values[5])


if __name__ == "__main__":
    unittest.main()
