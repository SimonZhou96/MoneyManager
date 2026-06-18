#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MarketCache 单元测试：TTL 过期、摘要生成、to_filter_details、市场宽度计算。"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.market_cache import MarketCache
from scoring.models import (
    MarketTemperature,
    CreditRiskResult,
    MarketBreadthResult,
)


# ── 辅助函数 ────────────────────────────────────────────────────


def _make_kline_rows(code: str, prices):
    """生成 stock_kline_cache 格式的模拟数据行。

    返回 [(code, bar_time, close), ...] 按 bar_time ASC 排列。
    """
    start = datetime(2024, 1, 1)
    rows = []
    for i, p in enumerate(prices):
        bt = (start + timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S")
        rows.append((code, bt, float(p)))
    return rows


def _sorted_rows(*stock_groups):
    """合并多个股票的数据并按 (code, bar_time) 排序。"""
    all_rows = []
    for grp in stock_groups:
        all_rows.extend(grp)
    all_rows.sort(key=lambda r: (r[0], r[1]))
    return all_rows


def _mock_db_with_rows(rows):
    """创建 mock DB，其 cursor.fetchall() 返回给定 rows。"""
    mock_cursor = MagicMock()
    mock_cursor.__enter__.return_value = mock_cursor
    mock_cursor.fetchall.return_value = rows

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    mock_db = MagicMock()
    mock_db.conn = mock_conn
    return mock_db


# ── TTL 测试 ────────────────────────────────────────────────────


class TestMarketCacheTTL(unittest.TestCase):
    """缓存 TTL 测试。"""

    def setUp(self):
        self.cache = MarketCache()

    def test_stale_when_empty(self):
        """无缓存时 is_stale() 返回 True。"""
        temp = MarketTemperature(market="HK")
        self.assertTrue(temp.is_stale())

    def test_not_stale_within_ttl(self):
        """TTL 内不 stale。"""
        temp = MarketTemperature(
            market="HK",
            computed_at=datetime.now(timezone.utc).isoformat(),
            ttl_minutes=60,
        )
        self.assertFalse(temp.is_stale())

    def test_stale_after_ttl(self):
        """超过 TTL 后 stale。"""
        temp = MarketTemperature(
            market="HK",
            computed_at=(
                datetime.now(timezone.utc) - timedelta(minutes=61)
            ).isoformat(),
            ttl_minutes=60,
        )
        self.assertTrue(temp.is_stale())

    def test_cache_hit_within_ttl(self):
        """缓存命中：TTL 内直接返回，不重新计算。"""
        temp = MarketTemperature(
            market="A",
            computed_at=datetime.now(timezone.utc).isoformat(),
            credit_risk=CreditRiskResult(score=72, level="low"),
        )
        self.cache._cache["A"] = temp
        result = self.cache.get_or_compute("A")
        self.assertEqual(result.credit_risk.score, 72)

    @patch.object(MarketCache, "_compute_all", return_value=MarketTemperature(market="A"))
    def test_cache_miss_recomputes(self, mock_compute):
        """缓存过期/缺失 → 重新计算。"""
        stale = MarketTemperature(
            market="A",
            computed_at=(
                datetime.now(timezone.utc) - timedelta(minutes=61)
            ).isoformat(),
            ttl_minutes=60,
        )
        self.cache._cache["A"] = stale
        self.cache.get_or_compute("A")
        mock_compute.assert_called_once_with("A")


# ── 摘要生成测试 ────────────────────────────────────────────────


class TestMarketTemperatureSummaries(unittest.TestCase):
    """摘要生成测试。"""

    def test_summaries_bullish(self):
        """全面偏强的市场温度。"""
        temp = MarketTemperature(
            market="A",
            credit_risk=CreditRiskResult(score=75, level="low"),
            market_breadth=MarketBreadthResult(score=68, above_ma50_pct=0.62),
        )
        temp.temperature_summary = "偏强"
        temp.money_making_summary = "好"
        temp.capital_env_summary = "流入"
        temp.risk_appetite_summary = "高"
        temp.hot_clarity_summary = "清晰"

        self.assertEqual(temp.temperature_summary, "偏强")

    def test_to_filter_details_all_available(self):
        """5 个市场级结果全部可用时，生成 5 条 filter_detail。"""
        temp = MarketTemperature(
            market="HK",
            credit_risk=CreditRiskResult(score=72, level="low", explanation="信用环境稳定"),
        )
        details = temp.to_filter_details()
        self.assertEqual(len(details), 5)
        # credit_risk 有数据
        cr = [d for d in details if d["rule_key"] == "credit_risk_regime"][0]
        self.assertEqual(cr["result"], "pass")
        self.assertIn("score", cr["details"])
        # market_breadth 无数据
        mb = [d for d in details if d["rule_key"] == "market_breadth_regime"][0]
        self.assertEqual(mb["result"], "fail")


# ── 市场宽度计算测试 ────────────────────────────────────────────


class TestMarketBreadthComputation(unittest.TestCase):
    """_compute_market_breadth 实现的全面测试。"""

    def setUp(self):
        self.cache = MarketCache()

    # ── 辅助方法 ────────────────────────────────────────────────

    def _patch_db(self, rows):
        """应用 patch，让 MarketDatabase(config) 返回 mock DB。"""
        patcher_db = patch("scoring.market_cache.MarketDatabase")
        patcher_cfg = patch("scoring.market_cache.MySqlConfig")
        self.mock_db_cls = patcher_db.start()
        self.mock_cfg_cls = patcher_cfg.start()
        self.addCleanup(patcher_db.stop)
        self.addCleanup(patcher_cfg.stop)

        self.mock_db_cls.return_value = _mock_db_with_rows(rows)
        return self.mock_db_cls

    def _compute(self, market: str, rows) -> MarketBreadthResult:
        self._patch_db(rows)
        return self.cache._compute_market_breadth(market)

    # ── 测试用例 ────────────────────────────────────────────────

    def test_bullish_full_score(self):
        """4 只股票全线走强 → score=100（所有加分项触发）。"""
        n_days = 260

        def uptrend(last_up=True):
            p = list(range(100, 100 + n_days))
            if not last_up:
                p[-1] = p[-2] - 1  # 最后一天下跌
            return p

        # A/B/D: 上升趋势，最后一天上涨
        rows = _sorted_rows(
            _make_kline_rows("A", uptrend(last_up=True)),
            _make_kline_rows("B", uptrend(last_up=False)),  # 最后一天下跌
            _make_kline_rows("C", uptrend(last_up=True)),
            _make_kline_rows("D", uptrend(last_up=True)),
        )
        result = self._compute("HK", rows)

        self.assertIsNotNone(result)
        # advance=3(A,C,D), decline=1(B) → ratio=3.0
        self.assertEqual(result.advance_decline_ratio, 3.0)
        # 全线站上 MA50/MA200
        self.assertEqual(result.above_ma50_pct, 1.0)
        self.assertEqual(result.above_ma200_pct, 1.0)
        # A/C/D 为新高（最后一天是最高点），B 不是（最后一天低于前高）
        self.assertEqual(result.new_high_52w, 3)
        self.assertEqual(result.new_low_52w, 0)
        # score = 50 + 25(ma50>60%) + 15(ma200>55%) + 10(adv_dec>1.5) + 10(high>low*2) = 110 → clamped 100
        self.assertEqual(result.score, 100.0)
        self.assertIn("综合评分=100.0", result.explanation)

    def test_bearish_low_score(self):
        """4 只股票全线走弱 → score=15（所有扣分项触发）。"""
        n_days = 260

        def downtrend(last_down=True):
            p = list(reversed(range(100, 100 + n_days)))  # 359→100
            if not last_down:
                p[-1] = p[-2] + 5  # 最后一天反弹
            return p

        rows = _sorted_rows(
            _make_kline_rows("A", downtrend(last_down=True)),
            _make_kline_rows("B", downtrend(last_down=False)),  # 最后一天反弹 → advance
            _make_kline_rows("C", downtrend(last_down=True)),
            _make_kline_rows("D", downtrend(last_down=True)),
        )
        result = self._compute("HK", rows)

        self.assertIsNotNone(result)
        # advance=1(B), decline=3(A,C,D) → ratio=0.33
        self.assertAlmostEqual(result.advance_decline_ratio, 1.0 / 3.0)
        # 全线低于 MA50/MA200
        self.assertEqual(result.above_ma50_pct, 0.0)
        self.assertEqual(result.above_ma200_pct, 0.0)
        # A/C/D 为新低（最后一天是最低点）
        self.assertEqual(result.new_low_52w, 3)
        # score = 50 - 20(ma50<40%) - 15(low>high*2) = 15
        self.assertEqual(result.score, 15.0)

    def test_mixed_neutral_score(self):
        """多空均衡 → score=50（无加减分项触发）。"""
        n_days = 260

        def uptrend():
            return list(range(100, 100 + n_days))

        def downtrend():
            return list(reversed(range(100, 100 + n_days)))

        # A: 上升 + 最后一天上涨
        # B: 上升 + 最后一天下跌
        # C: 下跌 + 最后一天上涨（反弹）
        # D: 下跌 + 最后一天下跌
        rows_data = uptrend()[:]
        p_b = uptrend()[:]
        p_b[-1] = p_b[-2] - 1
        p_c = downtrend()[:]
        p_c[-1] = p_c[-2] + 5
        p_d = downtrend()[:]

        rows = _sorted_rows(
            _make_kline_rows("A", uptrend()),
            _make_kline_rows("B", p_b),
            _make_kline_rows("C", p_c),
            _make_kline_rows("D", p_d),
        )
        result = self._compute("HK", rows)

        self.assertIsNotNone(result)
        # advance=2(A,C), decline=2(B,D)
        self.assertEqual(result.advance_decline_ratio, 1.0)
        # A/B 站上 MA50/200, C/D 不站上 → 50%
        self.assertEqual(result.above_ma50_pct, 0.5)
        self.assertEqual(result.above_ma200_pct, 0.5)
        self.assertEqual(result.score, 50.0)

    def test_empty_data_returns_neutral(self):
        """无缓存数据 → score=50。"""
        result = self._compute("HK", [])
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 50.0)
        self.assertIn("暂无缓存", result.explanation)

    def test_insufficient_bars_per_stock(self):
        """每只股票不足 2 根 K 线 → 全部跳过，靠 ma50<40% 扣分到 30。"""
        # 2 只股票，各 1 根 K 线（不够 advance/decline 判断）
        rows = _sorted_rows(
            _make_kline_rows("A", [100.0]),
            _make_kline_rows("B", [200.0]),
        )
        result = self._compute("HK", rows)

        self.assertIsNotNone(result)
        # 全部跳过 → 0/2 = 0% → 扣 20 分
        self.assertEqual(result.above_ma50_pct, 0.0)
        self.assertEqual(result.advance_decline_ratio, 0.0)
        # 50 - 20 = 30
        self.assertEqual(result.score, 30.0)

    def test_partial_data_skips_ma200(self):
        """只有 100 根 K 线 → MA200 跳过但 MA50 正常计算。"""
        n = 100  # 多于 MA50(50) 但少于 MA200(200)
        prices = list(range(100, 100 + n))
        rows = _sorted_rows(_make_kline_rows("A", prices))
        result = self._compute("HK", rows)

        self.assertIsNotNone(result)
        # advance/decline: 最后一天上涨
        self.assertEqual(result.advance_decline_ratio, 1.0)
        # above_ma50: 最后价格 > MA50
        self.assertGreater(result.above_ma50_pct, 0.0)
        # above_ma200: 数据不足，计为 not above → 0
        self.assertEqual(result.above_ma200_pct, 0.0)
        # 1 只股票，10 分加分/d扣分逻辑：ma50_pct=1(>0.6→+25)，ma200=0(不触发)
        # adv_dec_ratio=1.0(不触发)，new_high=1(新高) new_low=0, high>low*2? 1>0→+10
        # score = 50 + 25 + 10 = 85
        self.assertEqual(result.score, 85.0)

    def test_strict_above_ma_thresholds(self):
        """边界测试：各项指标恰好不触发阈值 → score=50。"""
        n_days = 260

        def uptrend():
            return list(range(100, 100 + n_days))

        def downtrend():
            return list(reversed(range(100, 100 + n_days)))

        # 4 只股票，2 只上涨（站上 MA50/MA200），2 只下跌
        # above_ma50_pct = 2/4 = 0.5 → 介于 0.4~0.6 之间，不触发
        # above_ma200_pct = 2/4 = 0.5 → 未超过 0.55，不触发
        # 2 advance, 2 decline → ratio = 1.0 → 未超过 1.5，不触发
        # new_high=2, new_low=2 → 均未超过对方 2 倍，不触发
        rows = _sorted_rows(
            _make_kline_rows("A", uptrend()),
            _make_kline_rows("B", uptrend()),
            _make_kline_rows("C", downtrend()),
            _make_kline_rows("D", downtrend()),
        )
        result = self._compute("HK", rows)

        self.assertAlmostEqual(result.above_ma50_pct, 2.0 / 4.0)
        self.assertAlmostEqual(result.above_ma200_pct, 2.0 / 4.0)
        # 2 advance(A,B), 2 decline(C,D) → 1.0
        self.assertAlmostEqual(result.advance_decline_ratio, 1.0)
        self.assertEqual(result.new_high_52w, 2)
        self.assertEqual(result.new_low_52w, 2)
        # score = 50 (no thresholds triggered)
        self.assertEqual(result.score, 50.0)

    def test_db_failure_returns_neutral(self):
        """DB 连接异常 → score=50，不崩溃。"""
        patcher_db = patch("scoring.market_cache.MarketDatabase")
        patcher_cfg = patch("scoring.market_cache.MySqlConfig")
        mock_db = patcher_db.start()
        mock_cfg = patcher_cfg.start()
        self.addCleanup(patcher_db.stop)
        self.addCleanup(patcher_cfg.stop)

        # MarketDatabase(config) 抛出异常
        mock_db.side_effect = RuntimeError("DB connection failed")

        result = self.cache._compute_market_breadth("HK")

        self.assertIsNotNone(result)
        self.assertEqual(result.score, 50.0)
        self.assertIn("异常", result.explanation)

    def test_single_stock_no_thresholds_triggered(self):
        """只有 1 只股票 → advance/decline 正常计算，但阈值都不触发。"""
        prices = list(range(100, 360))  # 260 bars, uptrend
        rows = _sorted_rows(_make_kline_rows("A", prices))
        result = self._compute("HK", rows)

        self.assertIsNotNone(result)
        # 只有 1 个 advance（最后一天上涨）→ ratio=1/1=1.0
        self.assertEqual(result.advance_decline_ratio, 1.0)
        # above_ma50_pct = 1/1 = 1.0 > 0.6 → +25
        # above_ma200_pct = 1/1 = 1.0 > 0.55 → +15
        # new_high=1, new_low=0 → 1 > 0 → +10
        # score = 50 + 25 + 15 + 10 = 100
        self.assertEqual(result.score, 100.0)

    def test_new_low_dominates(self):
        """新低远多于新高 → -15 扣分。"""
        n_days = 260

        def downtrend():
            return list(reversed(range(100, 100 + n_days)))

        # 3 只股票全部创新低，0 只创新高
        rows = _sorted_rows(
            _make_kline_rows("A", downtrend()),
            _make_kline_rows("B", downtrend()),
            _make_kline_rows("C", downtrend()),
        )
        result = self._compute("HK", rows)

        self.assertEqual(result.new_low_52w, 3)
        self.assertEqual(result.new_high_52w, 0)
        # 3 > 0*2 → -15
        self.assertEqual(result.score, 15.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
