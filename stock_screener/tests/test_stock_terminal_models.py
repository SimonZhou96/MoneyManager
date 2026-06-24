import importlib
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from math import inf, nan
from pathlib import Path

from stock_terminal.models import (
    BlockStatus,
    FundFlowPoint,
    KlinePoint,
    MinutePoint,
    QuoteSnapshot,
    StockTerminalSummary,
    data_status,
)


@contextmanager
def repo_root_import_mode():
    stock_screener_root = Path(__file__).resolve().parents[1]
    old_path = list(sys.path)
    old_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "stock_terminal" or name.startswith("stock_terminal.")
    }
    old_package = sys.modules.pop("stock_screener.stock_terminal", None)
    for name in list(old_modules):
        sys.modules.pop(name, None)
    try:
        sys.path = [
            path
            for path in sys.path
            if Path(path or ".").resolve() != stock_screener_root
        ]
        yield
    finally:
        sys.path = old_path
        sys.modules.update(old_modules)
        if old_package is not None:
            sys.modules["stock_screener.stock_terminal"] = old_package
        else:
            sys.modules.pop("stock_screener.stock_terminal", None)


class StockTerminalModelsTest(unittest.TestCase):
    def test_package_level_import_exports_models(self):
        with repo_root_import_mode():
            package = importlib.import_module("stock_screener.stock_terminal")

        self.assertEqual(package.BlockStatus(status="ok").to_dict()["status"], "ok")

    def test_data_status_serializes_source_and_stale_flag(self):
        now = datetime.now(timezone.utc)
        fetched_at = now.replace(microsecond=0)
        expires_at = fetched_at + timedelta(minutes=5)
        status = data_status(
            status="cached",
            source="test-cache",
            fetched_at=fetched_at,
            expires_at=expires_at,
        )

        payload = status.to_dict()

        self.assertEqual(payload["status"], "cached")
        self.assertEqual(payload["source"], "test-cache")
        self.assertEqual(payload["fetched_at"], fetched_at.isoformat())
        self.assertEqual(payload["expires_at"], expires_at.isoformat())
        self.assertFalse(payload["stale"])
        self.assertEqual(payload["error_message"], "")

    def test_data_status_marks_same_day_expired_data_stale(self):
        now = datetime.now(timezone.utc)
        status = data_status(
            status="cached",
            source="test-cache",
            fetched_at=now - timedelta(minutes=10),
            expires_at=now - timedelta(minutes=1),
        )

        self.assertTrue(status.to_dict()["stale"])

    def test_naive_datetime_serializes_as_utc(self):
        status = BlockStatus(
            status="fresh",
            source="fake",
            fetched_at=datetime(2026, 5, 25, 9, 30),
            expires_at=datetime(2026, 5, 25, 9, 35),
        )

        payload = status.to_dict()

        self.assertEqual(payload["fetched_at"], "2026-05-25T09:30:00+00:00")
        self.assertEqual(payload["expires_at"], "2026-05-25T09:35:00+00:00")

    def test_summary_contains_block_statuses(self):
        summary = StockTerminalSummary(
            market="A",
            code="SH.600519",
            name="贵州茅台",
            quote=QuoteSnapshot(
                market="A",
                code="SH.600519",
                name="贵州茅台",
                price=1688.0,
                change_percent=1.2,
                fetched_at=datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc),
                source="fake",
            ),
            statuses={
                "quote": BlockStatus(status="fresh", source="fake"),
                "kline": BlockStatus(status="empty", source="cache"),
            },
        )

        payload = summary.to_dict()

        self.assertEqual(payload["market"], "A")
        self.assertEqual(payload["code"], "SH.600519")
        self.assertEqual(payload["quote"]["price"], 1688.0)
        self.assertEqual(payload["source_status"]["quote"]["status"], "fresh")
        self.assertEqual(payload["source_status"]["kline"]["status"], "empty")

    def test_quote_snapshot_serializes_market_cap(self):
        quote = QuoteSnapshot(
            market="US",
            code="US.TSM",
            name="台积电",
            price=439.215,
            market_cap=1.23e12,
            fetched_at=datetime(2026, 6, 23, 15, 16, tzinfo=timezone.utc),
            source="fake",
        )

        payload = quote.to_dict()

        self.assertEqual(payload["market_cap"], 1.23e12)

    def test_series_points_serialize_numbers_and_time(self):
        at = datetime(2026, 5, 25, 9, 31, tzinfo=timezone.utc)

        kline = KlinePoint(at=at, open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0)
        minute = MinutePoint(at=at, price=1.5, average_price=1.4, volume=80.0)
        flow = FundFlowPoint(
            at=at,
            inflow=10.0,
            outflow=6.0,
            net_inflow=4.0,
            main_net_inflow=3.0,
            retail_net_inflow=1.0,
        )

        self.assertEqual(kline.to_dict()["close"], 1.5)
        self.assertEqual(minute.to_dict()["average_price"], 1.4)
        self.assertEqual(flow.to_dict()["net_inflow"], 4.0)

    def test_empty_and_non_numeric_values_serialize_as_none(self):
        at = datetime(2026, 5, 25, 9, 31, tzinfo=timezone.utc)

        kline = KlinePoint(at=at, open="", high="bad", low=None, close="1.5", volume="100")
        minute = MinutePoint(at=at, price="", average_price="bad", volume=None)
        flow = FundFlowPoint(
            at=at,
            inflow="",
            outflow="bad",
            net_inflow=None,
            main_net_inflow="3",
            retail_net_inflow="1",
        )

        self.assertIsNone(kline.to_dict()["open"])
        self.assertIsNone(kline.to_dict()["high"])
        self.assertIsNone(kline.to_dict()["low"])
        self.assertEqual(kline.to_dict()["close"], 1.5)
        self.assertIsNone(minute.to_dict()["price"])
        self.assertIsNone(minute.to_dict()["average_price"])
        self.assertIsNone(minute.to_dict()["volume"])
        self.assertIsNone(flow.to_dict()["inflow"])
        self.assertIsNone(flow.to_dict()["outflow"])
        self.assertIsNone(flow.to_dict()["net_inflow"])
        self.assertEqual(flow.to_dict()["main_net_inflow"], 3.0)

    def test_non_finite_numbers_serialize_as_none(self):
        at = datetime(2026, 5, 25, 9, 31, tzinfo=timezone.utc)

        kline = KlinePoint(
            at=at,
            open=nan,
            high=inf,
            low=-inf,
            close="NaN",
            volume="Infinity",
            turnover="-Infinity",
        )
        minute = MinutePoint(
            at=at,
            price="nan",
            average_price="inf",
            volume="-inf",
            turnover=1.0,
        )

        kline_payload = kline.to_dict()
        minute_payload = minute.to_dict()

        self.assertIsNone(kline_payload["open"])
        self.assertIsNone(kline_payload["high"])
        self.assertIsNone(kline_payload["low"])
        self.assertIsNone(kline_payload["close"])
        self.assertIsNone(kline_payload["volume"])
        self.assertIsNone(kline_payload["turnover"])
        self.assertIsNone(minute_payload["price"])
        self.assertIsNone(minute_payload["average_price"])
        self.assertIsNone(minute_payload["volume"])
        self.assertEqual(minute_payload["turnover"], 1.0)

    def test_point_models_allow_only_at_constructor(self):
        at = datetime(2026, 5, 25, 9, 31, tzinfo=timezone.utc)

        kline = KlinePoint(at=at)
        minute = MinutePoint(at=at)
        flow = FundFlowPoint(at=at)

        self.assertIsNone(kline.to_dict()["close"])
        self.assertIsNone(minute.to_dict()["average_price"])
        self.assertIsNone(flow.to_dict()["net_inflow"])


if __name__ == "__main__":
    unittest.main()
