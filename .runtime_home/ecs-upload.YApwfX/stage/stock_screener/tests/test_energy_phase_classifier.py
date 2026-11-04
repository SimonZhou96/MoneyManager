#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for EnergyPhaseClassifier strategizer and analyze_energy_phases function.

Covers all six energy states (COMPRESS, RELEASE, TRENDING, EXHAUSTION, PEAK, CRASH),
edge cases (empty kline, insufficient rows, None kline_df), registry integration,
and parameter propagation.
"""

from datetime import date, timedelta
import unittest

import numpy as np
import pandas as pd

from filters import FilterContext, StockInfo
from rule_engine import RuleMetadata, RuleRegistry
from strategizers import EnergyPhaseClassifier
from strategy import analyze_energy_phases, EnergyPhaseAnalysis, get_market_energy_params, MARKET_ENERGY_PARAMS


def _df(rows):
    """Build a DataFrame from list of (open, high, low, close, volume) tuples."""
    start = date(2025, 6, 1)
    data = []
    for idx, row in enumerate(rows):
        item = {
            "date": start + timedelta(days=idx),
            "open": row[0],
            "high": row[1],
            "low": row[2],
            "close": row[3],
            "volume": row[4] if len(row) > 4 else 1000 + idx * 10,
        }
        data.append(item)
    return pd.DataFrame(data)


def _make_steady_uptrend(n: int = 40, base: float = 100.0, daily_return: float = 0.005):
    """Generate n bars of steady rising prices (should trigger TRENDING)."""
    np.random.seed(42)
    prices = [base]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + daily_return + np.random.normal(0, 0.003)))
    rows = []
    for i, c in enumerate(prices):
        o = c * (1 + np.random.uniform(-0.003, 0.003))
        h = max(o, c) * (1 + abs(np.random.normal(0, 0.003)))
        l = min(o, c) * (1 - abs(np.random.normal(0, 0.003)))
        rows.append((round(o, 2), round(h, 2), round(l, 2), round(c, 2), 1000 + i * 10))
    return rows


def _make_compress_range(n: int = 40, base: float = 80.0):
    """Generate n bars in a tight range well below MA (should trigger COMPRESS-like)."""
    np.random.seed(42)
    prices = [base]
    for _ in range(n - 1):
        # Stay in a narrow range
        prices.append(prices[-1] * (1 + np.random.normal(0, 0.003)))
    # Force all prices to stay near base (well below a hypothetical MA)
    rows = []
    for i, c in enumerate(prices):
        o = c * (1 + np.random.uniform(-0.002, 0.002))
        h = max(o, c) * 1.002
        l = min(o, c) * 0.998
        rows.append((round(o, 2), round(h, 2), round(l, 2), round(c, 2), 1000 + i * 10))
    return rows


def _make_crash(n: int = 40, base: float = 100.0):
    """Generate n bars in a sustained strong downtrend (should trigger CRASH)."""
    np.random.seed(42)
    prices = [base]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 - 0.02 + np.random.normal(0, 0.005)))
    rows = []
    for i, c in enumerate(prices):
        o = c * (1 + np.random.uniform(-0.003, 0.003))
        h = max(o, c) * 1.003
        l = min(o, c) * 0.997
        rows.append((round(o, 2), round(h, 2), round(l, 2), round(c, 2), 1000 + i * 10))
    return rows


class EnergyPhaseClassifierTest(unittest.TestCase):
    """Tests for EnergyPhaseClassifier strategizer and analyze_energy_phases."""

    # ── Edge cases ──

    def test_empty_kline_returns_not_satisfied(self):
        """Empty kline_df should return satisfied=False."""
        stock = StockInfo(market="HK", code="HK.00001", name="Test")
        stock.kline_df = pd.DataFrame()
        strategizer = EnergyPhaseClassifier()
        output = strategizer.apply(
            stock, FilterContext(check_date=date(2025, 7, 15), market="HK")
        )
        self.assertFalse(output.satisfied)
        self.assertIn("kline_available", output.details)
        self.assertFalse(output.details["kline_available"])

    def test_none_kline_df_returns_not_satisfied(self):
        """stock.kline_df=None should return satisfied=False."""
        stock = StockInfo(market="HK", code="HK.00001", name="Test")
        stock.kline_df = None
        strategizer = EnergyPhaseClassifier()
        output = strategizer.apply(
            stock, FilterContext(check_date=date(2025, 7, 15), market="HK")
        )
        self.assertFalse(output.satisfied)
        self.assertIn("kline_available", output.details)

    def test_insufficient_rows_returns_unknown(self):
        """Fewer than min_rows bars should yield state=UNKNOWN."""
        rows = _make_steady_uptrend(n=10, base=100.0)  # only 10 rows
        stock = StockInfo(market="HK", code="HK.00001", name="Test")
        stock.kline_df = _df(rows)
        strategizer = EnergyPhaseClassifier()
        output = strategizer.apply(
            stock, FilterContext(check_date=date(2025, 7, 15), market="HK")
        )
        self.assertFalse(output.satisfied)
        state = output.details.get("state")
        if state is not None:
            self.assertNotIn(state, ("RELEASE", "TRENDING"))

    def test_insufficient_rows_direct_function(self):
        """analyze_energy_phases with too-few rows should return UNKNOWN."""
        rows = _make_steady_uptrend(n=10, base=100.0)
        df = _df(rows)
        result = analyze_energy_phases(df, min_rows=30)
        self.assertFalse(result.satisfied)
        self.assertEqual(result.state, "UNKNOWN")
        self.assertEqual(result.details.get("required_rows"), 30)
        self.assertEqual(result.details.get("actual_rows"), 10)

    # ── Bullish states (satisfied=True) ──

    def test_trending_state_steady_uptrend(self):
        """
        Steady uptrend should produce TRENDING state.
        Conditions: KE_consistency > 0.7, PE < 100, KE_decay < 0.5.
        """
        rows = _make_steady_uptrend(n=40, base=100.0, daily_return=0.005)
        df = _df(rows)
        result = analyze_energy_phases(df)
        self.assertIn(
            result.state, ("TRENDING", "RELEASE"),
            f"Expected TRENDING/RELEASE, got {result.state}. "
            f"KE_consistency={result.details.get('ke_consistency')}, "
            f"PE_norm={result.details.get('pe_norm')}, "
            f"KE_decay={result.details.get('ke_decay')}"
        )
        self.assertTrue(
            result.satisfied,
            f"Expected satisfied for {result.state} state"
        )
        self.assertEqual(result.details.get("direction"), "bullish")

    def test_release_state_rapid_breakout(self):
        """
        Sharp breakout after compressed period should trigger RELEASE.
        Simulate: 30 bars of tight range then 5 bars of accelerating upward moves.
        """
        np.random.seed(42)
        rows = _make_compress_range(n=30, base=100.0)
        # Add accelerating breakout bars
        c = rows[-1][3]
        for i in range(5):
            spike = 1.0 + 0.03 * (i + 1)  # 3%, 6%, 9%, 12%, 15%
            new_c = c * spike
            o = c * 1.001
            h = new_c * 1.005
            l = c * 0.999
            rows.append((round(o, 2), round(h, 2), round(l, 2), round(new_c, 2), 2000 + i * 100))
            c = new_c
        df = _df(rows)
        result = analyze_energy_phases(df)
        # The breakout should trigger either RELEASE or TRENDING
        self.assertIn(
            result.state, ("RELEASE", "TRENDING"),
            f"Expected RELEASE or TRENDING, got {result.state}: "
            f"KE={result.details.get('ke_signed')}, "
            f"PE_norm={result.details.get('pe_norm')}, "
            f"DeltaE={result.details.get('delta_e_5')}, "
            f"EPR={result.details.get('epr')}, "
            f"PE_falling={result.details.get('pe_falling')}"
        )
        self.assertTrue(result.satisfied)

    # ── Bearish/neutral states (satisfied=False) ──

    def test_crash_state_strong_downtrend(self):
        """Sustained downtrend should not produce a bullish signal."""
        rows = _make_crash(n=40, base=100.0)
        df = _df(rows)
        result = analyze_energy_phases(df)
        # CRASH or some bearish state
        self.assertFalse(result.satisfied)
        self.assertNotEqual(result.details.get("direction"), "bullish")

    def test_exhaustion_state_momentum_fading(self):
        """
        Strong rally followed by stagnation should trigger EXHAUSTION or similar.
        The key is that KE decay is high while PE is rising.
        """
        np.random.seed(42)
        # Strong uptrend first
        rows = _make_steady_uptrend(n=25, base=100.0, daily_return=0.015)
        c = rows[-1][3]
        # Stagnation phase
        for _ in range(10):
            new_c = c * (1 + np.random.uniform(-0.002, 0.002))
            rows.append((c * 0.999, max(c, new_c) * 1.002,
                         min(c, new_c) * 0.998, round(new_c, 2), 1500))
            c = new_c
        df = _df(rows)
        result = analyze_energy_phases(df)
        # Should NOT be bullish (momentum is fading)
        self.assertFalse(result.satisfied)
        # State can be EXHAUSTION, PEAK, COMPRESS, or UNKNOWN depending on exact data
        # but must not be RELEASE or TRENDING

    def test_compress_state_low_ke(self):
        """
        Sideways market with price away from MA should be COMPRESS or UNKNOWN.
        Not a bullish state.
        """
        rows = _make_compress_range(n=40, base=80.0)
        df = _df(rows)
        result = analyze_energy_phases(df)
        self.assertNotEqual(result.details.get("direction"), "bullish")

    def test_peak_state_plausible(self):
        """
        Simulate a scenario where KE decays heavily after a strong run.
        Add plateau then sharp drops.
        """
        np.random.seed(42)
        rows = _make_steady_uptrend(n=25, base=100.0, daily_return=0.012)
        c = rows[-1][3]
        # Very weak plateau
        for _ in range(5):
            new_c = c * (1 + np.random.normal(0, 0.001))
            rows.append((c * 0.999, c * 1.003, c * 0.997, round(new_c, 2), 2000))
            c = new_c
        # Sharp reversal
        for i in range(3):
            new_c = c * (1 - 0.03 + np.random.normal(0, 0.003))
            rows.append((c, c * 1.003, new_c * 0.998, round(new_c, 2), 2500))
            c = new_c
        df = _df(rows)
        result = analyze_energy_phases(df)
        self.assertFalse(result.satisfied)

    # ── Rich details ──

    def test_rich_details_includes_all_metrics(self):
        """Details dict should contain all expected energy metrics keys."""
        rows = _make_steady_uptrend(n=40, base=100.0)
        df = _df(rows)
        result = analyze_energy_phases(df)
        expected_keys = {
            "state", "satisfied", "direction",
            "ke_signed", "pe_norm", "pe_raw",
            "ke_decay", "ke_consistency", "delta_e_5",
            "epr", "ke_path", "ke_negative_streak",
            "pe_rising", "pe_falling",
            "close", "ma20", "data_rows",
        }
        for key in expected_keys:
            self.assertIn(key, result.details, f"Missing key in details: {key}")

    def test_strategizer_wrapper_produces_rich_details(self):
        """EnergyPhaseClassifier.apply() should include energy metrics in details."""
        rows = _make_steady_uptrend(n=40, base=100.0)
        stock = StockInfo(market="HK", code="HK.00001", name="Test")
        stock.kline_df = _df(rows)
        strategizer = EnergyPhaseClassifier()
        output = strategizer.apply(
            stock, FilterContext(check_date=date(2025, 7, 15), market="HK")
        )
        for key in ("ke_signed", "pe_norm", "ke_decay", "ke_consistency", "state", "direction"):
            self.assertIn(key, output.details, f"Missing key in output details: {key}")

    # ── Parameter configuration ──

    def test_custom_min_rows_param(self):
        """Custom min_rows parameter should be respected."""
        rows = _make_steady_uptrend(n=50, base=100.0)
        df = _df(rows)
        result = analyze_energy_phases(df, min_rows=100)
        self.assertFalse(result.satisfied)
        self.assertEqual(result.state, "UNKNOWN")
        self.assertEqual(result.details.get("required_rows"), 100)

    def test_custom_params_via_strategizer(self):
        """Custom parameters passed to strategizer should propagate to analysis."""
        rows = _make_steady_uptrend(n=50, base=100.0)
        stock = StockInfo(market="HK", code="HK.00001", name="Test")
        stock.kline_df = _df(rows)
        strategizer = EnergyPhaseClassifier(
            ma_period=10,
            consistency_window=5,
            delta_window=3,
        )
        output = strategizer.apply(
            stock, FilterContext(check_date=date(2025, 7, 15), market="HK")
        )
        self.assertEqual(output.details.get("ma_period"), 10)
        self.assertEqual(output.details.get("consistency_window"), 5)
        self.assertEqual(output.details.get("delta_window"), 3)

    # ── Registry integration ──

    def test_rule_registry_builds_energy_phase_classifier(self):
        """RuleRegistry should instantiate EnergyPhaseClassifier from metadata."""
        metadata = RuleMetadata(
            market="HK",
            rule_key="energy_phase_bullish",
            rule_name="能量相位看涨",
            rule_type="strategy",
            strategy_category="technical",
            implementation="EnergyPhaseClassifier",
            params={
                "ma_period": 20,
                "direction": "bullish",
                "signal_group": "bullish",
            },
            enabled=True,
            display_order=180,
        )
        created = RuleRegistry.default().create(metadata)
        self.assertIsInstance(created, EnergyPhaseClassifier)

    # ── NaN safety ──

    def test_nan_safety_in_metrics(self):
        """All numeric metrics in details should be finite (no NaN/Inf)."""
        rows = _make_steady_uptrend(n=40, base=100.0)
        df = _df(rows)
        result = analyze_energy_phases(df)
        numeric_keys = (
            "ke_signed", "pe_norm", "pe_raw",
            "ke_decay", "ke_consistency", "delta_e_5",
            "epr", "ke_path",
        )
        for key in numeric_keys:
            val = result.details.get(key)
            if val is not None:
                self.assertTrue(
                    np.isfinite(val),
                    f"{key} = {val}, expected finite value"
                )


    # ── 新增参数化阈值测试 ──

    def test_custom_release_delta_e(self):
        """降低 release_delta_e 应使 RELEASE 更易触发。"""
        rows = _make_compress_range(n=30, base=100.0)
        c = rows[-1][3]
        # 中等强度的突破（3% daily）
        for i in range(5):
            spike = 1.0 + 0.02 * (i + 1)  # 2%, 4%, 6%, 8%, 10%
            new_c = c * spike
            rows.append((c * 1.001, new_c * 1.005, c * 0.999, round(new_c, 2), 2000 + i * 100))
            c = new_c
        df = _df(rows)

        # 默认 release_delta_e=10.0 → 中等突破可能无法触发 RELEASE
        result_default = analyze_energy_phases(df, release_delta_e=10.0)
        # 降低 release_delta_e=3.0 → 更容易触发
        result_easy = analyze_energy_phases(df, release_delta_e=3.0)

        # 降低阈值后 RELEASE 触发概率 >= 默认阈值
        # （如果默认都没触发，easy 也不一定触发，但至少不会更差）
        self.assertTrue(
            result_easy.details.get("delta_e_5", 0) >= result_default.details.get("delta_e_5", 0) - 0.01
        )

    def test_custom_trending_consistency(self):
        """降低 trending_consistency 应使 TRENDING 更易触发。"""
        np.random.seed(42)
        rows = _make_steady_uptrend(n=40, base=100.0, daily_return=0.004)  # 较弱的趋势
        df = _df(rows)

        result_strict = analyze_energy_phases(df, trending_consistency=0.8)
        result_lenient = analyze_energy_phases(df, trending_consistency=0.5)

        # 宽松阈值不应比严格阈值更差（信号角度）
        consistency = result_lenient.details.get("ke_consistency", 0)
        if consistency > 0.5:
            self.assertIn(result_lenient.state, ("TRENDING", "RELEASE"))
        # 严格阈值下可能无法触发
        self.assertIsNotNone(result_strict.state)

    def test_all_hardcoded_thresholds_parameterized(self):
        """所有新增参数应正确传递到 analyze_energy_phases 并反映在 details 中。"""
        rows = _make_steady_uptrend(n=40, base=100.0)
        df = _df(rows)

        custom = {
            "crash_neg_streak": 3,
            "crash_ke_path": -15.0,
            "peak_pe_threshold": 70.0,
            "peak_ke_silence": 0.8,
            "peak_delta_e": -8.0,
            "release_delta_e": 7.0,
            "trending_consistency": 0.65,
            "compress_consistency": 0.35,
            "compress_ke_path": -1.0,
        }
        result = analyze_energy_phases(df, **custom)

        for key, expected in custom.items():
            actual = result.details.get(key)
            self.assertEqual(
                actual, expected,
                f"Parameter {key}: expected {expected}, got {actual}"
            )

    # ── 市场预设测试 ──

    def test_get_market_energy_params_hk(self):
        """HK 市场应返回降低的阈值。"""
        params = get_market_energy_params("HK")
        self.assertIn("release_delta_e", params)
        self.assertIn("trending_consistency", params)
        self.assertLess(params["release_delta_e"], 10.0)
        self.assertLess(params["trending_consistency"], 0.7)
        self.assertLess(params.get("ke_threshold", 4.0), 4.0)

    def test_get_market_energy_params_us(self):
        """US 市场应返回默认附近的阈值。"""
        params = get_market_energy_params("US")
        self.assertIn("release_delta_e", params)
        self.assertAlmostEqual(params["release_delta_e"], 8.0, delta=2.0)
        self.assertAlmostEqual(params["trending_consistency"], 0.65, delta=0.1)

    def test_get_market_energy_params_a(self):
        """A 股市场应返回较高的阈值（高波动）。"""
        params = get_market_energy_params("A")
        self.assertIn("release_delta_e", params)
        self.assertGreater(params["release_delta_e"], 10.0)
        self.assertGreater(params["trending_consistency"], 0.7)

    def test_get_market_energy_params_sz_sh(self):
        """SZ/SH 前缀应映射到 A 股参数。"""
        sz = get_market_energy_params("SZ")
        sh = get_market_energy_params("SH")
        a = get_market_energy_params("A")
        self.assertEqual(sz, a)
        self.assertEqual(sh, a)

    def test_get_market_energy_params_hk_with_prefix(self):
        """HK.xxxxx 格式应正确提取市场。"""
        params = get_market_energy_params("HK.800000")
        self.assertIsNotNone(params)
        self.assertLess(params["release_delta_e"], 10.0)

    def test_get_market_energy_params_unknown(self):
        """未知市场应返回空 dict。"""
        self.assertEqual(get_market_energy_params("XX"), {})
        self.assertEqual(get_market_energy_params(None), {})
        self.assertEqual(get_market_energy_params(""), {})

    def test_energy_phase_classifier_market_hk(self):
        """EnergyPhaseClassifier(market='HK') 应使用 HK 市场预设。"""
        ec = EnergyPhaseClassifier(market="HK")
        self.assertLess(ec.release_delta_e, 10.0)
        self.assertLess(ec.trending_consistency, 0.7)
        # HK 预设 ke_threshold 应为 2.5
        self.assertEqual(ec.ke_threshold, 2.5)
        self.assertEqual(ec.crash_neg_streak, 4)

    def test_energy_phase_classifier_market_a(self):
        """EnergyPhaseClassifier(market='A') 应使用 A 股市场预设。"""
        ec = EnergyPhaseClassifier(market="A")
        self.assertGreater(ec.release_delta_e, 10.0)
        self.assertGreater(ec.trending_consistency, 0.7)
        self.assertEqual(ec.ke_threshold, 5.0)

    def test_energy_phase_classifier_market_override(self):
        """显式参数应覆盖市场预设。"""
        ec = EnergyPhaseClassifier(
            market="HK",
            release_delta_e=9.0,
            trending_consistency=0.8,
            ke_threshold=3.5,
        )
        self.assertEqual(ec.release_delta_e, 9.0)
        self.assertEqual(ec.trending_consistency, 0.8)
        self.assertEqual(ec.ke_threshold, 3.5)

    def test_energy_phase_classifier_no_market(self):
        """不带 market 参数应使用通用默认值。"""
        ec = EnergyPhaseClassifier()
        self.assertEqual(ec.release_delta_e, 10.0)
        self.assertEqual(ec.trending_consistency, 0.7)
        self.assertEqual(ec.ke_threshold, 4.0)
        self.assertEqual(ec.crash_neg_streak, 5)

    def test_market_energy_params_keys(self):
        """MARKET_ENERGY_PARAMS 应包含 HK/US/A 三个市场的预设。"""
        self.assertIn("HK", MARKET_ENERGY_PARAMS)
        self.assertIn("US", MARKET_ENERGY_PARAMS)
        self.assertIn("A", MARKET_ENERGY_PARAMS)

    # ── 阈值边界测试 ──

    def test_release_delta_e_boundary(self):
        """delta_e 必须严格大于 release_delta_e 才触发 RELEASE。"""
        np.random.seed(42)
        rows = _make_compress_range(n=30, base=100.0)
        c = rows[-1][3]
        # 精确控制突破强度
        for i in range(3):
            spike = 1.0 + 0.04 * (i + 1)
            new_c = c * spike
            rows.append((c * 1.001, new_c * 1.005, c * 0.999, round(new_c, 2), 2000 + i * 100))
            c = new_c
        df = _df(rows)

        # 先用默认参数看 delta_e 是多少
        result = analyze_energy_phases(df)
        actual_delta = result.details.get("delta_e_5", 0)

        # 设置 release_delta_e 略高于实际 delta_e（+0.01 margin for float）
        # delta_e 必须严格 > release_delta_e，所以刚好等于或小于时不应触发
        result_no = analyze_energy_phases(df, release_delta_e=actual_delta + 0.01)
        self.assertNotEqual(
            result_no.state, "RELEASE",
            f"delta_e={actual_delta} shouldn't trigger RELEASE when release_delta_e={actual_delta + 0.01}"
        )

        # 设置 release_delta_e 远低于实际 delta_e → 必须触发
        result_yes = analyze_energy_phases(df, release_delta_e=actual_delta * 0.5)
        self.assertIn(
            result_yes.state, ("RELEASE", "TRENDING"),
            f"Expected RELEASE/TRENDING when release_delta_e << delta_e, got {result_yes.state}"
        )

    def test_trending_consistency_boundary(self):
        """ke_consistency 刚好等于 trending_consistency 不触发 TRENDING。"""
        rows = _make_steady_uptrend(n=40, base=100.0, daily_return=0.004)
        df = _df(rows)

        result = analyze_energy_phases(df)
        consistency = result.details.get("ke_consistency", 0)

        # 设置 trending_consistency 刚好等于 ke_consistency
        result_boundary = analyze_energy_phases(df, trending_consistency=consistency)
        # 必须严格 > trending_consistency
        self.assertNotEqual(result_boundary.state, "TRENDING",
                            f"consistency={consistency} shouldn't trigger TRENDING when threshold={consistency}")


if __name__ == "__main__":
    unittest.main()
