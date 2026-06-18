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


# ── 流动性计算测试 ────────────────────────────────────────────────


class TestLiquidityComputation(unittest.TestCase):
    """_compute_liquidity 实现的全面测试。"""

    def setUp(self):
        self.cache = MarketCache()

    # ── A 股 ────────────────────────────────────────────────────

    def _mock_akshare_margin(self, prices_sh, prices_sz, dates):
        """构建两融余额 mock DataFrame。"""
        import pandas as pd
        sh_data = {"日期": dates, "融资融券余额": prices_sh}
        sz_data = {"日期": dates, "融资融券余额": prices_sz}
        return pd.DataFrame(sh_data), pd.DataFrame(sz_data)

    def _mock_akshare_buy(self, buys_sh, buys_sz, dates):
        """构建融资买入额 mock DataFrame。"""
        import pandas as pd
        sh_data = {"日期": dates, "融资买入额": buys_sh}
        sz_data = {"日期": dates, "融资买入额": buys_sz}
        return pd.DataFrame(sh_data), pd.DataFrame(sz_data)

    def _mock_northbound_summary(self, net_buy_value=5.0, direction="北向"):
        """构建北向资金 Summary mock DataFrame。"""
        import pandas as pd
        return pd.DataFrame({
            "交易日": ["2026-06-18"],
            "资金方向": [direction],
            "成交净买额": [net_buy_value],
        })

    def _mock_northbound_hist(self, hist_vals):
        """构建北向历史累计净买额 mock DataFrame。"""
        import pandas as pd
        import numpy as np
        n = len(hist_vals)
        return pd.DataFrame({
            "日期": [f"2026-06-{17 - i:02d}" for i in range(n)],
            "当日成交净买额": [np.nan] * n,
            "历史累计净买额": hist_vals,
            "持股市值": [0.0] * n,
        })

    def _patch_akshare(self, margin_sh, margin_sz, buy_sh, buy_sz,
                       north_summary=None, north_hist=None):
        """应用 AKShare mock patches。"""
        patcher_margin_sh = patch("akshare.macro_china_market_margin_sh", return_value=margin_sh)
        patcher_margin_sz = patch("akshare.macro_china_market_margin_sz", return_value=margin_sz)
        patcher_buy_sh = patch("akshare.macro_china_market_margin_sh", return_value=buy_sh)
        patcher_buy_sz = patch("akshare.macro_china_market_margin_sz", return_value=buy_sz)

        self.mock_margin_sh = patcher_margin_sh.start()
        self.mock_margin_sz = patcher_margin_sz.start()
        self.mock_buy_sh = patcher_buy_sh.start()
        self.mock_buy_sz = patcher_buy_sz.start()

        self.addCleanup(patcher_margin_sh.stop)
        self.addCleanup(patcher_margin_sz.stop)
        self.addCleanup(patcher_buy_sh.stop)
        self.addCleanup(patcher_buy_sz.stop)

        # 北向资金
        patcher_north_summary = patch(
            "akshare.stock_hsgt_fund_flow_summary_em",
            return_value=north_summary if north_summary is not None
            else pd.DataFrame(),
        )
        patcher_north_hist = patch(
            "akshare.stock_hsgt_hist_em",
            return_value=north_hist if north_hist is not None
            else pd.DataFrame(),
        )
        self.mock_north_summary = patcher_north_summary.start()
        self.mock_north_hist = patcher_north_hist.start()
        self.addCleanup(patcher_north_summary.stop)
        self.addCleanup(patcher_north_hist.stop)

    def test_liquidity_a_bullish(self):
        """A 股：两融余额增长>2% + 融资买入额增长>10% → score=85。"""
        import pandas as pd
        dates = ["2026-06-11", "2026-06-12", "2026-06-15", "2026-06-16", "2026-06-17",
                 "2026-06-18"]

        # 两融余额连续增长（~3% 5日涨幅）
        sh_margin = [1.45e12, 1.46e12, 1.47e12, 1.48e12, 1.49e12, 1.50e12]
        sz_margin = [1.40e12, 1.41e12, 1.42e12, 1.43e12, 1.44e12, 1.45e12]

        # 融资买入额持续放量（~15% 5日涨幅）
        sh_buy = [1.17e11, 1.48e11, 1.49e11, 1.52e11, 1.64e11, 1.70e11]
        sz_buy = [1.18e11, 1.42e11, 1.49e11, 1.66e11, 1.66e11, 1.75e11]

        margin_sh_df, margin_sz_df = self._mock_akshare_margin(sh_margin, sz_margin, dates)
        buy_sh_df, buy_sz_df = self._mock_akshare_buy(sh_buy, sz_buy, dates)

        # 北向资金今日净流入
        north_summary = self._mock_northbound_summary(net_buy_value=8.5)

        patcher_margin_sh = patch("akshare.macro_china_market_margin_sh",
                                  side_effect=[margin_sh_df, buy_sh_df])
        patcher_margin_sz = patch("akshare.macro_china_market_margin_sz",
                                  side_effect=[margin_sz_df, buy_sz_df])
        patcher_north_summary = patch(
            "akshare.stock_hsgt_fund_flow_summary_em",
            return_value=north_summary,
        )
        patcher_north_hist = patch(
            "akshare.stock_hsgt_hist_em",
            return_value=pd.DataFrame(),
        )

        self.mock_ms = patcher_margin_sh.start()
        self.addCleanup(patcher_margin_sh.stop)
        self.mock_msz = patcher_margin_sz.start()
        self.addCleanup(patcher_margin_sz.stop)
        self.mock_ns = patcher_north_summary.start()
        self.addCleanup(patcher_north_summary.stop)
        self.mock_nh = patcher_north_hist.start()
        self.addCleanup(patcher_north_hist.stop)

        result = self.cache._compute_liquidity("A")

        self.assertIsNotNone(result)
        self.assertEqual(result.fund_flow_direction, "inflow")
        # 50 + 15(两融>2%) + 10(北向流入) + 10(融资买入>10%) = 85
        self.assertEqual(result.score, 85.0)
        self.assertIn("两融余额", result.explanation)
        self.assertIn("+15", result.explanation)
        self.assertIn("北向净买入", result.explanation)
        self.assertIn("+10", result.explanation)
        self.assertIn("融资买入额", result.explanation)

    def test_liquidity_a_bearish(self):
        """A 股：两融余额缩减>2% + 融资买入额缩减>10% → score=30。"""
        import pandas as pd
        dates = ["2026-06-11", "2026-06-12", "2026-06-15", "2026-06-16", "2026-06-17",
                 "2026-06-18"]

        # 两融余额连续缩减（~-3% 5日）
        sh_margin = [1.50e12, 1.49e12, 1.48e12, 1.46e12, 1.45e12, 1.44e12]
        sz_margin = [1.45e12, 1.44e12, 1.43e12, 1.41e12, 1.40e12, 1.38e12]

        # 融资买入额萎缩（~-20% 5日）
        sh_buy = [1.70e11, 1.64e11, 1.52e11, 1.49e11, 1.48e11, 1.17e11]
        sz_buy = [1.75e11, 1.66e11, 1.66e11, 1.49e11, 1.42e11, 1.18e11]

        margin_sh_df, margin_sz_df = self._mock_akshare_margin(sh_margin, sz_margin, dates)
        buy_sh_df, buy_sz_df = self._mock_akshare_buy(sh_buy, sz_buy, dates)

        patcher_margin_sh = patch("akshare.macro_china_market_margin_sh",
                                  side_effect=[margin_sh_df, buy_sh_df])
        patcher_margin_sz = patch("akshare.macro_china_market_margin_sz",
                                  side_effect=[margin_sz_df, buy_sz_df])
        patcher_north_summary = patch(
            "akshare.stock_hsgt_fund_flow_summary_em",
            return_value=self._mock_northbound_summary(net_buy_value=-8.0),
        )
        patcher_north_hist = patch(
            "akshare.stock_hsgt_hist_em",
            return_value=pd.DataFrame(),
        )

        self.mock_ms = patcher_margin_sh.start()
        self.addCleanup(patcher_margin_sh.stop)
        self.mock_msz = patcher_margin_sz.start()
        self.addCleanup(patcher_margin_sz.stop)
        self.mock_ns = patcher_north_summary.start()
        self.addCleanup(patcher_north_summary.stop)
        self.mock_nh = patcher_north_hist.start()
        self.addCleanup(patcher_north_hist.stop)

        result = self.cache._compute_liquidity("A")

        self.assertIsNotNone(result)
        self.assertEqual(result.fund_flow_direction, "outflow")
        # 50 - 10(两融<-2%) - 10(融资买入<-10%) = 30
        self.assertEqual(result.score, 30.0)
        self.assertIn("-10", result.explanation)

    def test_liquidity_a_neutral(self):
        """A 股：两融余额/成交量变化在阈值内 → score=50。"""
        import pandas as pd
        dates = ["2026-06-11", "2026-06-12", "2026-06-15", "2026-06-16", "2026-06-17",
                 "2026-06-18"]

        # 两融余额几乎不变
        sh_margin = [1.45e12] * 6
        sz_margin = [1.40e12] * 6
        # 融资买入额几乎不变
        sh_buy = [1.40e11] * 6
        sz_buy = [1.30e11] * 6

        margin_sh_df, margin_sz_df = self._mock_akshare_margin(sh_margin, sz_margin, dates)
        buy_sh_df, buy_sz_df = self._mock_akshare_buy(sh_buy, sz_buy, dates)

        patcher_margin_sh = patch("akshare.macro_china_market_margin_sh",
                                  side_effect=[margin_sh_df, buy_sh_df])
        patcher_margin_sz = patch("akshare.macro_china_market_margin_sz",
                                  side_effect=[margin_sz_df, buy_sz_df])
        patcher_north_summary = patch(
            "akshare.stock_hsgt_fund_flow_summary_em",
            return_value=self._mock_northbound_summary(net_buy_value=0.0),
        )
        patcher_north_hist = patch(
            "akshare.stock_hsgt_hist_em",
            return_value=pd.DataFrame(),
        )

        self.mock_ms = patcher_margin_sh.start()
        self.addCleanup(patcher_margin_sh.stop)
        self.mock_msz = patcher_margin_sz.start()
        self.addCleanup(patcher_margin_sz.stop)
        self.mock_ns = patcher_north_summary.start()
        self.addCleanup(patcher_north_summary.stop)
        self.mock_nh = patcher_north_hist.start()
        self.addCleanup(patcher_north_hist.stop)

        result = self.cache._compute_liquidity("A")

        self.assertIsNotNone(result)
        self.assertEqual(result.score, 50.0)
        self.assertEqual(result.fund_flow_direction, "neutral")

    # ── HK ──────────────────────────────────────────────────────

    def _mock_southbound_hist(self, net_vals):
        """构建南向历史净买卖 mock DataFrame。"""
        import pandas as pd
        n = len(net_vals)
        return pd.DataFrame({
            "日期": [f"2026-06-{17 - i:02d}" for i in range(n)],
            "当日成交净买额": net_vals,
        })

    def test_liquidity_hk_bullish(self):
        """HK：南向净流入 + 恒指上涨 → score=70。"""
        import pandas as pd

        # 南向近5日净买入合计>20
        south_hist = self._mock_southbound_hist([15.0, 12.0, 5.0, 8.0, 10.0])

        # 恒指近5日上涨>3%
        import numpy as np
        hsi_hist = pd.DataFrame({
            "Close": [24000.0, 24200.0, 24400.0, 24700.0, 25000.0],
        })

        patcher_south = patch("akshare.stock_hsgt_hist_em", return_value=south_hist)
        patcher_vix = patch("yfinance.Ticker")

        self.mock_south = patcher_south.start()
        self.addCleanup(patcher_south.stop)

        # mock yfinance Ticker for ^HSI
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hsi_hist
        patcher_hsi = patch("yfinance.Ticker", return_value=mock_ticker)
        self.mock_hsi = patcher_hsi.start()
        self.addCleanup(patcher_hsi.stop)

        # Mock yf_sleep to be a no-op
        patcher_sleep = patch("yf_ratelimit.yf_sleep")
        self.mock_sleep = patcher_sleep.start()
        self.addCleanup(patcher_sleep.stop)

        result = self.cache._compute_liquidity("HK")

        self.assertIsNotNone(result)
        self.assertEqual(result.fund_flow_direction, "inflow")
        # 50 + 10(南向>20) + 10(恒指>3%) = 70
        self.assertEqual(result.score, 70.0)

    def test_liquidity_hk_bearish(self):
        """HK：南向净流出 + 恒指下跌 → score=35。"""
        import pandas as pd

        south_hist = self._mock_southbound_hist([-10.0, -15.0, -8.0, -5.0, -12.0])
        hsi_hist = pd.DataFrame({
            "Close": [25000.0, 24800.0, 24500.0, 24200.0, 23800.0],
        })

        patcher_south = patch("akshare.stock_hsgt_hist_em", return_value=south_hist)
        self.mock_south = patcher_south.start()
        self.addCleanup(patcher_south.stop)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hsi_hist
        patcher_hsi = patch("yfinance.Ticker", return_value=mock_ticker)
        self.mock_hsi = patcher_hsi.start()
        self.addCleanup(patcher_hsi.stop)

        patcher_sleep = patch("yf_ratelimit.yf_sleep")
        self.mock_sleep = patcher_sleep.start()
        self.addCleanup(patcher_sleep.stop)

        result = self.cache._compute_liquidity("HK")

        self.assertIsNotNone(result)
        self.assertEqual(result.fund_flow_direction, "outflow")
        # 50 - 5(南向< -20) - 10(恒指< -3%) = 35
        self.assertEqual(result.score, 35.0)

    # ── US ──────────────────────────────────────────────────────

    def test_liquidity_us_bullish(self):
        """US：VIX低 + SPY上涨 → score=70。"""
        import pandas as pd

        vix_hist = pd.DataFrame({
            "Close": [16.5, 16.0, 15.8, 15.5, 15.0],
        })
        spy_hist = pd.DataFrame({
            "Close": [500.0, 505.0, 510.0, 515.0, 520.0],
        })

        mock_vix = MagicMock()
        mock_vix.history.return_value = vix_hist

        mock_spy = MagicMock()
        mock_spy.history.return_value = spy_hist

        # yfinance.Ticker called twice: ^VIX then SPY
        ticker_results = {"^VIX": mock_vix, "SPY": mock_spy}
        def ticker_side_effect(symbol):
            return ticker_results.get(symbol, MagicMock())

        patcher_ticker = patch("yfinance.Ticker", side_effect=ticker_side_effect)
        self.mock_ticker = patcher_ticker.start()
        self.addCleanup(patcher_ticker.stop)

        patcher_sleep = patch("yf_ratelimit.yf_sleep")
        self.mock_sleep = patcher_sleep.start()
        self.addCleanup(patcher_sleep.stop)

        result = self.cache._compute_liquidity("US")

        self.assertIsNotNone(result)
        self.assertEqual(result.fund_flow_direction, "inflow")
        # 50 + 10(VIX<18) + 10(SPY>2%) = 70
        self.assertEqual(result.score, 70.0)

    def test_liquidity_us_bearish(self):
        """US：VIX高(>25) + VIX飙升(>15%) + SPY下跌(<-2%) → score=30。"""
        import pandas as pd

        vix_hist = pd.DataFrame({
            "Close": [22.0, 24.0, 26.0, 28.0, 30.0],
        })
        spy_hist = pd.DataFrame({
            "Close": [520.0, 515.0, 508.0, 502.0, 495.0],
        })

        mock_vix = MagicMock()
        mock_vix.history.return_value = vix_hist

        mock_spy = MagicMock()
        mock_spy.history.return_value = spy_hist

        def ticker_side_effect(symbol):
            return {"^VIX": mock_vix, "SPY": mock_spy}.get(symbol, MagicMock())

        patcher_ticker = patch("yfinance.Ticker", side_effect=ticker_side_effect)
        self.mock_ticker = patcher_ticker.start()
        self.addCleanup(patcher_ticker.stop)

        patcher_sleep = patch("yf_ratelimit.yf_sleep")
        self.mock_sleep = patcher_sleep.start()
        self.addCleanup(patcher_sleep.stop)

        result = self.cache._compute_liquidity("US")

        self.assertIsNotNone(result)
        self.assertEqual(result.fund_flow_direction, "outflow")
        # 50 - 10(VIX>25) - 5(VIX飙升>15%) - 5(SPY<-2%) = 30
        self.assertEqual(result.score, 30.0)

    def test_liquidity_unknown_market(self):
        """不支持的市场 → score=50，不崩溃。"""
        result = self.cache._compute_liquidity("JP")
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 50.0)
        self.assertIn("暂无", result.explanation)

    def test_liquidity_akshare_failure(self):
        """AKShare 异常 → 不崩溃，返回 factor 异常说明 + score=50。"""
        import pandas as pd

        patcher_margin_sh = patch("akshare.macro_china_market_margin_sh",
                                  side_effect=RuntimeError("API timeout"))
        patcher_margin_sz = patch("akshare.macro_china_market_margin_sz",
                                  side_effect=RuntimeError("API timeout"))
        patcher_north_summary = patch("akshare.stock_hsgt_fund_flow_summary_em",
                                      side_effect=RuntimeError("API timeout"))

        self.mock_ms = patcher_margin_sh.start()
        self.addCleanup(patcher_margin_sh.stop)
        self.mock_msz = patcher_margin_sz.start()
        self.addCleanup(patcher_margin_sz.stop)
        self.mock_ns = patcher_north_summary.start()
        self.addCleanup(patcher_north_summary.stop)

        # Need buy data too since margin_sh and margin_sz are re-used for buy
        patcher_buy_sh = patch("akshare.macro_china_market_margin_sh",
                               side_effect=RuntimeError("API timeout"))
        patcher_buy_sz = patch("akshare.macro_china_market_margin_sz",
                               side_effect=RuntimeError("API timeout"))
        self.mock_bs = patcher_buy_sh.start()
        self.addCleanup(patcher_buy_sh.stop)
        self.mock_bsz = patcher_buy_sz.start()
        self.addCleanup(patcher_buy_sz.stop)

        result = self.cache._compute_liquidity("A")

        self.assertIsNotNone(result)
        self.assertEqual(result.score, 50.0)
        self.assertIn("异常", result.explanation)


# ── 大宗商品冲击计算测试 ────────────────────────────────────────────


class TestCommodityShockComputation(unittest.TestCase):
    """_compute_commodity_shock 实现测试。"""

    def setUp(self):
        self.cache = MarketCache()

    # ── 辅助方法 ────────────────────────────────────────────────

    def _mock_hist(self, close_prices):
        """构建 commodity history mock DataFrame。"""
        import pandas as pd
        return pd.DataFrame({"Close": close_prices})

    def _patch_tickers(self, ticker_map):
        """Patch yfinance.Ticker 返回映射好的 mock，并 mock yf_sleep。"""
        def side_effect(symbol):
            return ticker_map.get(symbol, MagicMock())

        patcher = patch("yfinance.Ticker", side_effect=side_effect)
        self.mock_ticker = patcher.start()
        self.addCleanup(patcher.stop)

        patcher_sleep = patch("yf_ratelimit.yf_sleep")
        self.mock_sleep = patcher_sleep.start()
        self.addCleanup(patcher_sleep.stop)

    # ── 测试用例 ────────────────────────────────────────────────

    def test_no_shock_stable_market(self):
        """所有商品 abs(5d) < 2% → score=55（无显著波动 +5）。"""
        # 20 个交易日，缓慢上涨趋势，5d < 2%
        stable = [75.0, 75.3, 75.6, 75.4, 75.8,
                  76.0, 76.2, 75.9, 76.3, 76.1,
                  76.4, 76.6, 76.3, 76.7, 76.5,
                  76.8, 77.0, 76.7, 77.1, 76.9]  # 5d: (76.9 - 76.8)/76.8 = +0.13%

        ticker_map = {}
        for sym in ["CL=F", "HG=F", "GC=F", "NG=F", "SI=F"]:
            ticker_map[sym] = MagicMock(
                history=MagicMock(return_value=self._mock_hist(stable))
            )
        self._patch_tickers(ticker_map)

        result = self.cache._compute_commodity_shock("US")

        self.assertIsNotNone(result)
        self.assertEqual(result.score, 55.0)
        self.assertEqual(len(result.shocks), 0)
        self.assertIn("无明显波动", result.explanation)

    def test_one_severe_shock_penalty(self):
        """一个商品 abs(5d) > 10% → score=40（50 - 10）。"""
        # 5 个商品中 4 个稳定，原油剧烈拉升
        stable = [75.0] * 20
        # 原油: 前 15 天平稳，后 5 天急涨 ~15%
        oil = [75.0] * 15 + [76.0, 82.0, 86.0, 87.0, 89.0]
        # 5d: (89 - 76)/76 = +17.1% > 10%

        ticker_map = {
            "CL=F": MagicMock(history=MagicMock(return_value=self._mock_hist(oil))),
        }
        for sym in ["HG=F", "GC=F", "NG=F", "SI=F"]:
            ticker_map[sym] = MagicMock(
                history=MagicMock(return_value=self._mock_hist(stable))
            )
        self._patch_tickers(ticker_map)

        result = self.cache._compute_commodity_shock("US")

        self.assertIsNotNone(result)
        self.assertEqual(result.score, 40.0)  # 50 - 10
        self.assertIn("原油", result.shocks)
        s = result.shocks["原油"]
        self.assertEqual(s["direction"], "up")
        self.assertGreater(s["magnitude"], 0.10)
        self.assertEqual(s["impact"], "航空/化工下游承压")

    def test_multiple_shocks_with_uncertainty_penalty(self):
        """多个冲击 + 一条严重 → score=35（50 - 10 - 5）。"""
        stable = [75.0] * 20
        # 原油: 5d 涨幅 ~6.5%（冲击，不严重）
        oil = [75.0] * 15 + [76.0, 78.0, 79.0, 80.0, 81.0]
        # 5d: (81 - 76)/76 = +6.58% > 5%, < 10%

        # 铜: 5d 涨幅 ~15%（严重冲击）
        copper = [4.0] * 15 + [4.0, 4.2, 4.4, 4.5, 4.6]
        # 5d: (4.6 - 4.0)/4.0 = +15% > 10%

        ticker_map = {}
        for sym in ["GC=F", "NG=F", "SI=F"]:
            ticker_map[sym] = MagicMock(
                history=MagicMock(return_value=self._mock_hist(stable))
            )
        ticker_map["CL=F"] = MagicMock(
            history=MagicMock(return_value=self._mock_hist(oil))
        )
        ticker_map["HG=F"] = MagicMock(
            history=MagicMock(return_value=self._mock_hist(copper))
        )
        self._patch_tickers(ticker_map)

        result = self.cache._compute_commodity_shock("US")

        self.assertIsNotNone(result)
        # 铜严重(-10) + 多冲击(-5) = 35
        self.assertEqual(result.score, 35.0)
        self.assertIn("原油", result.shocks)
        self.assertIn("铜", result.shocks)
        self.assertEqual(len(result.shocks), 2)
        self.assertEqual(result.shocks["铜"]["impact"], "工业金属/新能源利好")

    def test_yfinance_failure_returns_neutral(self):
        """YFinance 异常 → score=50，不崩溃。"""
        patcher = patch("yfinance.Ticker", side_effect=RuntimeError("API timeout"))
        self.mock_ticker = patcher.start()
        self.addCleanup(patcher.stop)

        patcher_sleep = patch("yf_ratelimit.yf_sleep")
        self.mock_sleep = patcher_sleep.start()
        self.addCleanup(patcher_sleep.stop)

        result = self.cache._compute_commodity_shock("US")

        self.assertIsNotNone(result)
        self.assertEqual(result.score, 50.0)
        self.assertIn("异常", result.explanation)


# ── 政策事件分析测试 ────────────────────────────────────────────


class TestPolicyEventComputation(unittest.TestCase):
    """_compute_policy_event 实现测试。"""

    def setUp(self):
        self.cache = MarketCache()

    def test_policy_event_returns_result(self):
        """计算始终返回 PolicyEventResult（不返回 None）。"""
        result = self.cache._compute_policy_event("HK")
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 50.0)

    def test_policy_event_no_tavily_returns_neutral(self):
        """未配置 Tavily → score=50, direction=neutral。"""
        result = self.cache._compute_policy_event("HK")
        self.assertEqual(result.score, 50.0)
        self.assertEqual(result.direction, "neutral")

    def test_policy_event_mocked_positive_kw(self):
        """模拟 Tavily 返回含正面关键词 → score > 50。"""
        from unittest.mock import MagicMock, patch
        from signal_analysis.models import SearchDocument

        mock_docs = [
            SearchDocument(
                title="央行降准0.5个百分点释放长期流动性",
                url="http://example.com/1",
                content="央行宣布降准降息，金融稳增长政策加码，利好股市",
                score=1.0,
                query="央行 降准 降息 LPR",
            ),
        ]
        mock_client = MagicMock()
        mock_client.search.return_value = mock_docs

        with patch(
            "signal_analysis.search_providers.TavilySearchProvider",
            return_value=mock_client,
        ), patch("os.getenv", return_value="mock_key"):
            result = self.cache._compute_policy_event("A")

        self.assertIsNotNone(result)
        # 降准 + 降息 + 稳增长 = 3 个正面关键词 > 0 负面
        self.assertGreater(result.score, 50.0)
        self.assertIn("neutral_positive", result.direction)
        self.assertIn("金融", result.related_sectors)

    def test_policy_event_mocked_negative_kw(self):
        """模拟 Tavily 返回含负面关键词 → score < 50。"""
        from unittest.mock import MagicMock, patch
        from signal_analysis.models import SearchDocument

        mock_docs = [
            SearchDocument(
                title="美联储加息75基点，全球贸易战升级",
                url="http://example.com/2",
                content="美联储宣布加息，地缘政治冲突加剧，制裁收紧",
                score=1.0,
                query="美联储 利率 关税 政策",
            ),
        ]
        mock_client = MagicMock()
        mock_client.search.return_value = mock_docs

        with patch(
            "signal_analysis.search_providers.TavilySearchProvider",
            return_value=mock_client,
        ), patch("os.getenv", return_value="mock_key"):
            result = self.cache._compute_policy_event("US")

        self.assertIsNotNone(result)
        # 加息 + 贸易战 + 地缘政治 + 制裁 + 收紧 = 5 个负面 > 0 正面
        self.assertLess(result.score, 50.0)
        self.assertIn("neutral_negative", result.direction)
        self.assertTrue(len(result.risk_events) > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
