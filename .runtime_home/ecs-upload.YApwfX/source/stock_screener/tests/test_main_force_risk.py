import unittest
from pathlib import Path

import pandas as pd

from kline_fetcher import KlineFetcherBase
from main_force_risk import (
    ExternalRiskSnapshot,
    FutuOpenDMainForceDataProvider,
    MainForceRiskAnalyzer,
    MainForceRiskService,
    NullMainForceDataProvider,
)


class StaticKlineFetcher(KlineFetcherBase):
    def __init__(self, df):
        self.df = df

    def fetch(self, stock_code, market="HK", timeframe="1d", max_count=2000):
        return self.df.tail(max_count).copy()

    def get_name(self):
        return "static"


class ClosableStaticKlineFetcher(StaticKlineFetcher):
    def __init__(self, df):
        super().__init__(df)
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def make_drop_kline():
    dates = pd.date_range("2026-01-01", periods=80, freq="D")
    close = [100.0 + (i % 5) for i in range(79)] + [90.0]
    open_ = [value + 1.0 for value in close]
    high = [value + 2.0 for value in close]
    low = [value - 2.0 for value in close]
    volume = [1000.0 for _ in range(79)] + [3500.0]
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


class MainForceRiskAnalyzerTest(unittest.TestCase):
    def test_kline_analyzer_outputs_chinese_risk_signals(self):
        analyzer = MainForceRiskAnalyzer()
        result = analyzer.analyze(
            record={"code": "HK.00001", "name": "测试股份"},
            market="HK",
            kline=make_drop_kline(),
            external=ExternalRiskSnapshot.disabled("HK"),
        )

        labels = [signal.label for signal in result.triggered_signals]
        self.assertIn("放量下跌", labels)
        self.assertIn("跌破重要支撑", labels)
        self.assertGreaterEqual(result.risk_score, 30)
        self.assertIn(result.risk_level_text, {"中", "高"})
        self.assertNotIn("volume_profile", result.data_status_text("chip"))
        self.assertEqual(result.data_status_text("chip"), "可用:K线成交量分布")

    def test_service_updates_record_with_csv_ready_fields(self):
        service = MainForceRiskService(
            kline_fetchers=[StaticKlineFetcher(make_drop_kline())],
            data_provider=NullMainForceDataProvider(),
        )
        records = [{"code": "HK.00001", "name": "测试股份"}]

        results = service.analyze_records(market="HK", timeframe="1d", records=records)

        self.assertEqual(len(results), 1)
        self.assertIn(records[0]["main_force_risk_level_text"], {"中", "高"})
        self.assertIn("放量下跌", records[0]["main_force_risk_signals_text"])
        self.assertEqual(records[0]["main_force_chip_data_text"], "可用:K线成交量分布")
        self.assertIn("资金流向: 暂无明细", records[0]["main_force_market_data_observation_text"])
        self.assertIn("成交量分布: 现价", records[0]["main_force_market_data_observation_text"])
        self.assertIn("近120日成交量加权价", records[0]["main_force_market_data_observation_text"])
        self.assertIn("最大成交量区间", records[0]["main_force_market_data_observation_text"])
        self.assertIn("样本80日", records[0]["main_force_market_data_observation_text"])
        self.assertNotIn("筹码观察", records[0]["main_force_market_data_observation_text"])
        self.assertNotIn("筹码分布使用成交量分布近似", records[0]["main_force_missing_data_text"])

    def test_service_close_releases_owned_kline_fetchers(self):
        """Would fail if a job leaves a stateful K-line fetcher alive after completion."""
        fetcher = ClosableStaticKlineFetcher(make_drop_kline())
        service = MainForceRiskService(
            kline_fetchers=[fetcher],
            data_provider=NullMainForceDataProvider(),
        )

        service.close()

        self.assertEqual(fetcher.close_calls, 1)

    def test_futu_provider_subscribes_before_order_book_and_broker_queue_for_minute_timeframe(self):
        class FakeSubType:
            ORDER_BOOK = "ORDER_BOOK"
            BROKER = "BROKER"

        class FakeFutu:
            RET_OK = 0
            SubType = FakeSubType

        class FakeQuoteContext:
            def __init__(self):
                self.subscribe_calls = []
                self.unsubscribe_calls = []

            def get_capital_flow(self, code):
                return 0, {"net_inflow": -1000}

            def subscribe(self, codes, subtypes, subscribe_push=False):
                self.subscribe_calls.append((list(codes), list(subtypes), subscribe_push))
                return 0, None

            def get_order_book(self, code, num=10):
                return 0, {
                    "Bid": [(100.0, 100, 1, {})],
                    "Ask": [(100.1, 350, 1, {})],
                }

            def get_broker_queue(self, code):
                return 0, pd.DataFrame([{"broker": "A"}]), pd.DataFrame([
                    {"broker": "B"},
                    {"broker": "C"},
                    {"broker": "D"},
                    {"broker": "E"},
                    {"broker": "F"},
                ])

            def unsubscribe(self, codes, subtypes):
                self.unsubscribe_calls.append((list(codes), list(subtypes)))
                return 0, None

        quote_ctx = FakeQuoteContext()
        provider = FutuOpenDMainForceDataProvider(quote_ctx=quote_ctx)
        provider.ft = FakeFutu

        snapshot = provider.fetch("HK", "HK.00700", timeframe="5m")

        self.assertIn((["HK.00700"], ["ORDER_BOOK"], False), quote_ctx.subscribe_calls)
        self.assertIn((["HK.00700"], ["BROKER"], False), quote_ctx.subscribe_calls)
        self.assertEqual(snapshot.statuses["order_book"].status, "available")
        self.assertTrue(any(signal.label == "盘口卖盘压制" for signal in snapshot.signals))
        result = MainForceRiskAnalyzer().analyze(
            record={"code": "HK.00700", "name": "腾讯控股"},
            market="HK",
            kline=make_drop_kline(),
            external=snapshot,
        )
        self.assertIn("资金流向: 整体资金净流出1000", result.market_data_observation_text())
        self.assertIn("盘口: 卖一量350股，买一量100股，卖一约为买一3.5倍", result.market_data_observation_text())
        self.assertIn("经纪队列: 卖盘经纪5家，买盘经纪1家", result.market_data_observation_text())

    def test_futu_provider_skips_order_book_for_daily_timeframe(self):
        class FakeSubType:
            ORDER_BOOK = "ORDER_BOOK"
            BROKER = "BROKER"

        class FakeFutu:
            RET_OK = 0
            SubType = FakeSubType

        class FakeQuoteContext:
            def __init__(self):
                self.subscribe_calls = []

            def get_capital_flow(self, code):
                return 0, {"net_inflow": -1000}

            def subscribe(self, codes, subtypes, subscribe_push=False):
                self.subscribe_calls.append((list(codes), list(subtypes), subscribe_push))
                return 0, None

            def get_order_book(self, code, num=10):
                raise AssertionError("daily timeframe must not fetch order book")

            def get_broker_queue(self, code):
                raise AssertionError("daily timeframe must not fetch broker queue")

        quote_ctx = FakeQuoteContext()
        provider = FutuOpenDMainForceDataProvider(quote_ctx=quote_ctx)
        provider.ft = FakeFutu

        snapshot = provider.fetch("HK", "HK.00700", timeframe="1d")

        self.assertEqual(quote_ctx.subscribe_calls, [])
        self.assertEqual(snapshot.statuses["order_book"].status, "not_applicable")
        self.assertFalse(any(signal.source in {"盘口", "经纪队列"} for signal in snapshot.signals))
        result = MainForceRiskAnalyzer().analyze(
            record={"code": "HK.00700", "name": "腾讯控股"},
            market="HK",
            kline=make_drop_kline(),
            external=snapshot,
        )
        self.assertIn("资金流向: 整体资金净流出1000", result.market_data_observation_text())
        self.assertNotIn("盘口:", result.market_data_observation_text())
        self.assertNotIn("经纪队列:", result.market_data_observation_text())

    def test_main_force_sql_exists(self):
        sql_path = Path(__file__).resolve().parents[1] / "sql" / "010_main_force_risk_analysis.sql"
        content = sql_path.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS screening_main_force_risks", content)
        self.assertIn("main_force_risk_level", content)
        self.assertIn("uk_main_force_task_market_code", content)


if __name__ == "__main__":
    unittest.main()
