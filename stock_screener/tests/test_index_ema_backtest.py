import base64
import json
import sys
import tempfile
import unittest
import zlib
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import run_index_ema_backtest as backtest


class OpeningRangeBacktestTests(unittest.TestCase):
    def test_bullish_opening_bar_enters_next_bar_and_flattens_at_close(self):
        bars = _session(
            date(2025, 11, 3),
            first=(100.0, 102.0, 99.0, 101.0),
            second=(101.5, 101.8, 101.2, 101.6),
            base=101.5,
        )

        fills = backtest.run_backtest({"US.QQQ": bars}, initial_cash=10_000)

        self.assertEqual([fill["action"] for fill in fills], ["buy", "sell"])
        self.assertEqual(fills[0]["timestamp"], _utc_text(date(2025, 11, 3), 9, 35))
        self.assertAlmostEqual(fills[0]["price"], 101.50)
        self.assertEqual(fills[0]["position_side"], "long")
        self.assertEqual(fills[1]["reason"], "收盘平仓")
        self.assertEqual(fills[1]["timestamp"], _utc_text(date(2025, 11, 3), 16, 0))

    def test_bearish_opening_bar_opens_short_and_covers_by_close(self):
        bars = _session(
            date(2025, 11, 3),
            first=(100.0, 101.0, 98.0, 99.0),
            second=(98.5, 98.8, 98.2, 98.4),
            base=98.5,
        )

        fills = backtest.run_backtest({"US.TQQQ": bars}, initial_cash=10_000)

        self.assertEqual([fill["action"] for fill in fills], ["sell_short", "buy_to_cover"])
        self.assertEqual([fill["position_side"] for fill in fills], ["short", "short"])
        self.assertAlmostEqual(fills[0]["price"], 98.50)
        self.assertEqual(fills[1]["reason"], "收盘平仓")

    def test_bearish_opening_range_high_is_short_stop(self):
        bars = _session(
            date(2025, 11, 3),
            first=(100.0, 101.0, 98.0, 99.0),
            second=(98.5, 98.8, 98.2, 98.4),
            base=98.5,
            overrides={10: (102.0, 103.0, 100.5, 102.0)},
        )

        fills = backtest.run_backtest({"US.TQQQ": bars}, initial_cash=10_000)

        self.assertEqual(fills[-1]["action"], "buy_to_cover")
        self.assertIn("首根 5 分钟 K 线高点止损", fills[-1]["reason"])
        self.assertGreater(fills[-1]["price"], 101.0)

    def test_bearish_opening_range_sets_ten_r_short_target(self):
        bars = _session(
            date(2025, 11, 3),
            first=(100.0, 101.0, 98.0, 99.0),
            second=(98.5, 98.8, 98.2, 98.4),
            base=98.5,
            overrides={10: (98.0, 98.2, 70.0, 74.0)},
        )

        fills = backtest.run_backtest({"US.TQQQ": bars}, initial_cash=10_000)

        self.assertEqual(fills[-1]["action"], "buy_to_cover")
        self.assertEqual(fills[-1]["reason"], "10R 止盈")
        self.assertLess(fills[-1]["price"], fills[0]["price"])

    def test_doji_opening_bar_skips_the_whole_session(self):
        bars = _session(
            date(2025, 11, 3),
            first=(100.0, 101.0, 99.0, 100.0),
            second=(100.5, 100.8, 100.2, 100.6),
            base=100.5,
        )

        fills = backtest.run_backtest({"US.QQQ": bars}, initial_cash=10_000)

        self.assertEqual(fills, [])

    def test_only_complete_us_sessions_are_traded_and_dst_keeps_nine_thirty_open(self):
        before_dst = _session(
            date(2025, 3, 7),
            first=(100.0, 102.0, 99.0, 101.0),
            second=(101.5, 101.8, 101.2, 101.6),
            base=101.5,
        )
        after_dst = _session(
            date(2025, 3, 10),
            first=(100.0, 102.0, 99.0, 101.0),
            second=(101.5, 101.8, 101.2, 101.6),
            base=101.5,
        )
        incomplete = _session(
            date(2025, 3, 11),
            first=(100.0, 102.0, 99.0, 101.0),
            second=(101.5, 101.8, 101.2, 101.6),
            base=101.5,
        )[1:]

        fills = backtest.run_backtest(
            {"US.QQQ": before_dst + after_dst + incomplete}, initial_cash=10_000
        )

        entries = [fill for fill in fills if fill["role"] == "entry"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["timestamp"], _utc_text(date(2025, 3, 7), 9, 35))
        self.assertEqual(entries[1]["timestamp"], _utc_text(date(2025, 3, 10), 9, 35))

    def test_futu_closing_time_key_is_normalized_to_candle_open(self):
        import pandas as pd

        frame = pd.DataFrame([{
            "time_key": "2025-11-03 09:35:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000,
        }])

        bars = backtest._normalise_frame(frame, "US.QQQ")

        self.assertEqual(bars[0]["timestamp"], _utc_text(date(2025, 11, 3), 9, 30))

    def test_share_size_respects_one_percent_risk_and_four_x_exposure_cap(self):
        sizing = getattr(backtest, "size_position", None)
        self.assertTrue(callable(sizing), "size_position must enforce the paper risk and leverage limits")

        shares = sizing("US.QQQ", 10_000.0, 100.0, 99.99, "long")

        self.assertLessEqual(shares * 100.0, 40_000.0)
        self.assertGreater(shares, 0)
        self.assertLessEqual(shares * 0.03 + 10.0, 100.0)

    def test_short_share_size_respects_risk_budget_and_gross_exposure_cap(self):
        shares = backtest.size_position("US.QQQ", 10_000.0, 100.0, 101.0, "short")
        estimated_risk = shares * 1.0
        estimated_risk += backtest._fee_usd("US.QQQ", "sell_short", shares, 100.0)
        estimated_risk += backtest._fee_usd("US.QQQ", "buy_to_cover", shares, 101.0)

        self.assertGreater(shares, 0)
        self.assertLessEqual(shares * 100.0, 40_000.0)
        self.assertLessEqual(estimated_risk, 100.0)

    def test_stop_wins_when_stop_and_ten_r_target_are_touched_in_one_bar(self):
        bars = _session(
            date(2025, 11, 3),
            first=(100.0, 102.0, 99.0, 101.0),
            second=(101.5, 101.8, 101.2, 101.6),
            base=101.5,
            overrides={10: (101.5, 130.0, 98.0, 110.0)},
        )

        fills = backtest.run_backtest({"US.QQQ": bars}, initial_cash=10_000)

        self.assertEqual([fill["action"] for fill in fills], ["buy", "sell"])
        self.assertIn("止损", fills[1]["reason"])
        self.assertLessEqual(fills[1]["price"], 99.0)

    def test_ten_r_target_closes_long_when_only_target_is_touched(self):
        bars = _session(
            date(2025, 11, 3),
            first=(100.0, 102.0, 99.0, 101.0),
            second=(101.5, 101.8, 101.2, 101.6),
            base=101.5,
            overrides={10: (101.5, 127.0, 101.0, 125.0)},
        )

        fills = backtest.run_backtest({"US.QQQ": bars}, initial_cash=10_000)

        self.assertEqual(fills[-1]["reason"], "10R 止盈")
        self.assertGreater(fills[-1]["price"], fills[0]["price"])

    def test_gap_through_stop_fills_at_open_without_extra_slippage(self):
        bars = _session(
            date(2025, 11, 3),
            first=(100.0, 102.0, 99.0, 101.0),
            second=(101.5, 101.8, 101.2, 101.6),
            base=101.5,
            overrides={10: (98.5, 99.0, 98.2, 98.7)},
        )

        fills = backtest.run_backtest({"US.QQQ": bars}, initial_cash=10_000)

        self.assertIn("止损", fills[-1]["reason"])
        self.assertAlmostEqual(fills[-1]["price"], 98.50)

    def test_noise_area_uses_prior_fourteen_intraday_moves_and_adjusts_for_gap(self):
        bars = []
        sessions = _business_dates(date(2025, 1, 2), 15)
        for session_date in sessions[:14]:
            bars.extend(_session(
                session_date,
                first=(100.0, 101.0, 99.0, 100.0),
                second=(100.0, 101.0, 99.0, 100.0),
                base=100.0,
                overrides={5: (100.0, 101.0, 100.0, 101.0)},
            ))
        bars.extend(_session(
            sessions[14],
            first=(102.0, 102.0, 102.0, 102.0),
            second=(102.0, 102.0, 102.0, 102.0),
            base=102.0,
        ))

        point = backtest.noise_area_by_session(bars, "US.QQQ")[sessions[14]][5]

        self.assertAlmostEqual(point["sigma"], 0.01)
        self.assertAlmostEqual(point["upper"], 103.02)
        self.assertAlmostEqual(point["lower"], 99.0)

    def test_noise_area_for_a_session_does_not_change_when_future_data_is_appended(self):
        sessions = _business_dates(date(2025, 2, 3), 16)
        bars = []
        for session_date in sessions[:15]:
            bars.extend(_session(
                session_date,
                first=(100.0, 101.0, 99.0, 100.0),
                second=(100.0, 101.0, 99.0, 100.0),
                base=100.0,
            ))
        before = backtest.noise_area_by_session(bars, "US.QQQ")[sessions[14]][5]
        bars.extend(_session(
            sessions[15],
            first=(100.0, 150.0, 50.0, 140.0),
            second=(140.0, 150.0, 50.0, 140.0),
            base=140.0,
        ))
        after = backtest.noise_area_by_session(bars, "US.QQQ")[sessions[14]][5]

        self.assertEqual(before, after)

    def test_daily_volatility_uses_only_fourteen_prior_completed_returns(self):
        sessions = _business_dates(date(2025, 3, 3), 17)
        bars = []
        closes = []
        for index, session_date in enumerate(sessions[:16]):
            base = 100.0 + (0.2 if index % 2 else -0.2)
            closes.append(base)
            bars.extend(_session(
                session_date,
                first=(base, base + 0.1, base - 0.1, base),
                second=(base, base + 0.1, base - 0.1, base),
                base=base,
            ))
        volatility = backtest.daily_volatility_by_session(bars, "US.QQQ")
        expected = backtest.stdev([closes[i] / closes[i - 1] - 1.0 for i in range(1, 15)])
        before = volatility[sessions[15]]
        bars.extend(_session(
            sessions[16],
            first=(100.0, 150.0, 50.0, 140.0),
            second=(140.0, 150.0, 50.0, 140.0),
            base=140.0,
        ))

        self.assertAlmostEqual(before, expected)
        self.assertEqual(volatility[sessions[15]], backtest.daily_volatility_by_session(bars, "US.QQQ")[sessions[15]])

    def test_noise_signal_waits_for_half_hour_close_and_enters_next_bar_open(self):
        data = _noise_data()
        fills = _run_variant(data, "noise_area_paper")
        entries = [fill for fill in fills if fill["role"] == "entry"]

        self.assertTrue(entries)
        entry = entries[0]
        session_date = _business_dates(date(2025, 4, 1), 16)[-1]
        self.assertEqual(entry["timestamp"], _utc_text(session_date, 10, 0))
        self.assertEqual(entry["action"], "buy")
        self.assertIn("噪声区上边界", entry["reason"])
        self.assertEqual(fills[-1]["reason"], "收盘平仓")
        self.assertEqual(fills[-1]["timestamp"], _utc_text(session_date, 16, 0))

    def test_noise_area_can_open_and_close_a_short_position(self):
        session_date = _business_dates(date(2025, 4, 1), 16)[-1]
        fills = _run_variant(_noise_data(short_breakout=True), "noise_area_paper")

        self.assertEqual([fill["action"] for fill in fills[-2:]], ["sell_short", "buy_to_cover"])
        self.assertEqual([fill["position_side"] for fill in fills[-2:]], ["short", "short"])
        self.assertEqual(fills[-1]["reason"], "收盘平仓")
        self.assertEqual(fills[-1]["timestamp"], _utc_text(session_date, 16, 0))

    def test_paper_style_reverses_when_opposite_noise_boundary_breaks(self):
        fills = _run_variant(_noise_data(opposite_breakout=True), "noise_area_paper")
        short_entry_index = next(index for index, fill in enumerate(fills) if fill["action"] == "sell_short")

        self.assertEqual(fills[short_entry_index - 1]["action"], "sell")
        self.assertEqual(fills[short_entry_index]["timestamp"], fills[short_entry_index - 1]["timestamp"])
        self.assertIn("反手", fills[short_entry_index - 1]["reason"])

    def test_low_turnover_variant_does_not_reenter_after_stop(self):
        data = _noise_data(reentry_breakout=True)
        paper_fills = _run_variant(data, "noise_area_paper")
        low_turnover_fills = _run_variant(data, "noise_area_low_turnover")
        paper_entries = [fill for fill in paper_fills if fill["role"] == "entry"]
        low_entries = [fill for fill in low_turnover_fills if fill["role"] == "entry"]

        self.assertGreaterEqual(len(paper_entries), 2)
        self.assertEqual(len(low_entries), 1)
        self.assertLessEqual(len(low_turnover_fills), 2)
        self.assertIn("动态止损", low_turnover_fills[-1]["reason"])
        self.assertEqual(low_turnover_fills[-1]["timestamp"], _utc_text(
            _business_dates(date(2025, 4, 1), 16)[-1], 10, 30
        ))

    def test_noise_position_size_targets_two_percent_volatility_and_caps_at_four_x(self):
        size = getattr(backtest, "size_position_by_volatility", None)
        self.assertTrue(callable(size), "volatility targeting must size the noise-area strategy")

        self.assertEqual(size("US.QQQ", 10_000, 100.0, 0.01), 200)
        self.assertEqual(size("US.QQQ", 10_000, 100.0, 0.001), 400)

    def test_all_fills_use_zero_slippage(self):
        self.assertEqual(backtest._adverse_fill_price(100.0, "buy", 0.01), 100.0)
        self.assertEqual(backtest._adverse_fill_price(100.0, "sell_short", 0.01), 100.0)

    def test_scenarios_include_all_strategies_periods_and_isolated_books(self):
        data = _scenario_data()
        runner = getattr(backtest, "run_scenarios", None)
        self.assertTrue(callable(runner), "run_scenarios must create the four independent books")

        result = runner(data, initial_cash=10_000, predictions={}, folds=[], evaluation_start=_business_dates(date(2025, 10, 1), 16)[-1])

        self.assertEqual(len(result["scenarios"]), 96)
        self.assertEqual({scenario["initial_cash_usd"] for scenario in result["scenarios"]}, {10_000})
        self.assertEqual({scenario["symbol"] for scenario in result["scenarios"]}, set(backtest.SYMBOLS))
        self.assertEqual(len(result["benchmarks"]), 6)
        self.assertEqual(
            {"full", "recent_year"},
            {s["evaluation_period"] for s in result["scenarios"]},
        )

    def test_missing_futu_history_fails_clearly_instead_of_using_short_yahoo_fallback(self):
        from unittest.mock import patch

        with patch.object(backtest, "_fetch_from_futu", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Futu OpenD"):
                backtest.load_market_data()

    def test_short_round_trip_performance_counts_profit_and_both_fees(self):
        fills = [
            _fill("US.TQQQ", "sell_short", 100.0, 10, "short", "entry", 10.0),
            _fill("US.TQQQ", "buy_to_cover", 90.0, 10, "short", "exit", 10.0),
        ]

        summary = backtest.calculate_performance(fills, initial_cash=10_000)

        self.assertEqual(summary["closed_trades"], 1)
        self.assertEqual(summary["winning_trades"], 1)
        self.assertGreater(summary["gross_profit_usd"], 0)
        self.assertGreater(summary["total_fees_usd"], 0)
        self.assertAlmostEqual(
            summary["net_profit_usd"], summary["gross_profit_usd"] - summary["total_fees_usd"]
        )

    def test_chart_and_json_keep_scenario_aware_fills(self):
        data = _scenario_data()
        runner = getattr(backtest, "run_scenarios", None)
        self.assertTrue(callable(runner), "run_scenarios must produce chartable scenario results")
        scenarios = runner(data, initial_cash=10_000, predictions={}, folds=[], evaluation_start=_business_dates(date(2025, 10, 1), 16)[-1])

        with tempfile.TemporaryDirectory() as tmp:
            html_path, json_path = backtest.write_outputs(data, scenarios, Path(tmp))
            encoded = html_path.read_text(encoding="utf-8").split(
                '<script id="chart-data" type="application/octet-stream">', 1
            )[1].split("</script>", 1)[0]
            chart_data = json.loads(zlib.decompress(base64.b64decode(encoded)))
            stored_fills = json.loads(json_path.read_text(encoding="utf-8"))
            html = html_path.read_text(encoding="utf-8")
            output_names = {path.name for path in Path(tmp).iterdir()}

        self.assertEqual(output_names, {"index_ema_backtest.html", "trades.json"})
        self.assertEqual(chart_data["fills"], stored_fills)
        self.assertEqual(len(chart_data["panels"]), 96)
        expected_ids = {
            f"{symbol}:{variant}:{period}"
            for symbol in backtest.SYMBOLS for variant in backtest.ALL_VARIANTS
            for period in ("full", "recent_year")
        }
        self.assertEqual({panel["scenario_id"] for panel in chart_data["panels"]}, expected_ids)
        self.assertTrue({fill["scenario_id"] for fill in stored_fills}.issubset(expected_ids))
        self.assertTrue(any(
            bar[5] is not None and bar[6] is not None and bar[7] is not None
            for bar in chart_data["symbols"]["US.QQQ"]["bars"]
        ))
        self.assertTrue(all(fill["strategy_version"] == backtest.STRATEGY_VERSION for fill in stored_fills))
        self.assertIn("Sharpe", html)
        self.assertIn("胜率", html)
        self.assertIn("平均 R", html)
        self.assertIn("买入持有", html)
        self.assertIn("噪声区", html)
        self.assertIn("VWAP", html)
        self.assertIn("近一年", html)
        self.assertIn("样本外", html)


def _run_variant(data, variant):
    try:
        return backtest.run_backtest(data, initial_cash=10_000, strategy_variant=variant)
    except TypeError as error:
        raise AssertionError("run_backtest must accept a strategy_variant") from error


def _fill(symbol, action, price, quantity, side, role, initial_risk_usd):
    return {
        "timestamp": "2025-11-03T15:00:00+00:00",
        "symbol": symbol,
        "strategy_variant": "orb_range_10r",
        "scenario_id": f"{symbol}:orb_range_10r",
        "action": action,
        "position_side": side,
        "role": role,
        "price": price,
        "quantity": quantity,
        "fee_usd": 1.0,
        "initial_risk_usd": initial_risk_usd,
        "reason": "test fixture",
    }


def _session(session_date, *, first, second, base, overrides=None):
    overrides = overrides or {}
    timezone_ny = ZoneInfo("America/New_York")
    bars = []
    for index in range(78):
        local_time = datetime.combine(session_date, time(9, 30), timezone_ny) + timedelta(minutes=5 * index)
        price = base
        bar = (price, price + 0.05, price - 0.05, price)
        if index == 0:
            bar = first
        elif index == 1:
            bar = second
        if index in overrides:
            bar = overrides[index]
        bars.append({
            "timestamp": local_time.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "open": bar[0],
            "high": bar[1],
            "low": bar[2],
            "close": bar[3],
            "volume": 1000,
        })
    return bars


def _scenario_data():
    sessions = _business_dates(date(2025, 10, 1), 16)
    data = {}
    for symbol in backtest.SYMBOLS:
        bars = []
        for index, session_date in enumerate(sessions[:-1]):
            base = 99.8 if index % 2 == 0 else 100.2
            bars.extend(_session(
                session_date,
                first=(base, base + 1, base - 1, base),
                second=(base, base + 1, base - 1, base),
                base=base,
            ))
        bars.extend(_session(
            sessions[-1],
            first=(100.0, 102.0, 99.0, 101.0),
            second=(101.0, 101.8, 100.8, 101.6),
            base=100.0,
            overrides={
                5: (100.0, 101.0, 100.0, 101.0),
                6: (101.0, 102.0, 100.9, 101.5),
            },
        ))
        data[symbol] = bars
    return data


def _noise_data(*, reentry_breakout=False, short_breakout=False, opposite_breakout=False):
    sessions = _business_dates(date(2025, 4, 1), 16)
    bars = []
    for index, session_date in enumerate(sessions[:15]):
        base = 99.8 if index % 2 == 0 else 100.2
        bars.extend(_session(
            session_date,
            first=(base, base + 0.1, base - 0.1, base),
            second=(base, base + 0.1, base - 0.1, base),
            base=base,
        ))
    overrides = {
        5: (100.0, 101.0, 100.0, 101.0),
        6: (101.0, 101.2, 100.9, 101.0),
    }
    current_base = 101.0
    if reentry_breakout:
        overrides.update({
            11: (100.0, 100.1, 99.9, 100.0),
            12: (100.0, 100.1, 99.9, 100.0),
            17: (100.0, 102.0, 100.0, 102.0),
            18: (102.0, 102.2, 101.9, 102.0),
        })
    if short_breakout:
        current_base = 99.0
        overrides.update({
            5: (100.0, 100.0, 99.0, 99.0),
            6: (99.0, 99.1, 98.9, 99.0),
        })
    if opposite_breakout:
        overrides.update({
            17: (100.0, 100.0, 99.0, 99.0),
            18: (99.0, 99.1, 98.9, 99.0),
        })
    bars.extend(_session(
        sessions[-1],
        first=(100.0, 100.1, 99.9, 100.0),
        second=(100.0, 100.1, 99.9, 100.0),
        base=current_base,
        overrides=overrides,
    ))
    return {"US.QQQ": bars}


def _business_dates(start, count):
    days = []
    day = start
    while len(days) < count:
        if backtest._session_close(day) is not None:
            days.append(day)
        day += timedelta(days=1)
    return days


def _utc_text(session_date, hour, minute):
    local = datetime.combine(session_date, time(hour, minute), ZoneInfo("America/New_York"))
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    unittest.main()
